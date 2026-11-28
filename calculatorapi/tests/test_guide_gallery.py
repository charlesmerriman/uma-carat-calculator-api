"""Community guides: a player's own PvP guide as the gallery's third kind of
post (guide_gallery.py, gallery_access.py, models/pvp_community_guide.py;
notes/community-guides-plan.md).

The groundwork (PR 1) is pinned first: the constraints, the one gate every
route to a post asks, and that no team read ever counts a guide post.
"""

# Every class here sits on RouteFixture's chain plus this module's own fixture.
# pylint: disable=too-many-ancestors

from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from calculatorapi import community_guide_document, gallery, gallery_access, moderation_cards
from calculatorapi.models import (
    BlockedTerm,
    GalleryComment,
    GalleryPost,
    GalleryPostKind,
    PvpCommunityGuide,
    PvpGuide,
    PvpTeam,
    PvpUmaRecommendation,
)
from calculatorapi.models.skill import SkillAvailability
from calculatorapi.tests.factories import make_league_of_heroes
from calculatorapi.tests.test_gallery import SENIOR
from calculatorapi.tests.test_pvp_gallery import RouteFixture, all_keys
from calculatorapi.tests.test_pvp_guides import make_skill


def make_working_copy(user, event, **fields):
    """A working copy for `event` (a CM or an LoH), made straight in the database."""
    field = "champions_meeting" if event.__class__.__name__ == "ChampionsMeeting" else "league_of_heroes"
    return PvpCommunityGuide.objects.create(author=user, **{field: event}, **fields)


def make_guide_post(user, event, *, source=None, **fields):
    """A guide post by `user` for `event`, from a working copy (made if not given)."""
    source = source or make_working_copy(user, event)
    field = "champions_meeting" if source.champions_meeting_id else "league_of_heroes"
    return GalleryPost.objects.create(
        public_id=gallery.generate_unique_post_id(),
        author=user,
        kind=GalleryPostKind.PVP_GUIDE,
        caption=fields.pop("caption", "Front runner line"),
        guide_snapshot=fields.pop("guide_snapshot", {}),
        source_guide=source,
        **{field: source.event},
        **fields,
    )


class GuideFixture(RouteFixture):

    def member(self, **kwargs):
        """An account with early access, which community guides need."""
        user = self.person(**kwargs)
        user.early_access = True
        user.save()
        return user

    def team_post(self, user):
        team = self.team(user)
        post, _ = gallery.publish_team(user, team, confirm_public_identity=True)
        return post


class ConstraintTests(GuideFixture):

    def assert_refused(self, **fields):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryPost.objects.create(public_id=gallery.generate_unique_post_id(), **fields)

    def test_a_guide_post_has_exactly_one_event(self):
        user = self.member()
        self.assert_refused(author=user, kind=GalleryPostKind.PVP_GUIDE)
        self.assert_refused(
            author=user, kind=GalleryPostKind.PVP_GUIDE,
            champions_meeting=self.cm, league_of_heroes=make_league_of_heroes(),
        )

    def test_only_a_guide_post_names_a_working_copy(self):
        user = self.member()
        copy = make_working_copy(user, self.cm)
        self.assert_refused(author=user, kind=GalleryPostKind.PLAN, source_guide=copy)
        self.assert_refused(
            author=user, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm, source_guide=copy,
        )

    def test_a_working_copy_has_one_post(self):
        user = self.member()
        copy = make_working_copy(user, self.cm)
        make_guide_post(user, self.cm, source=copy)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_guide_post(user, self.cm, source=copy)

    def test_a_team_and_several_guides_share_one_event(self):
        user = self.member()
        self.team_post(user)
        make_guide_post(user, self.cm)
        make_guide_post(user, self.cm)
        self.assertEqual(GalleryPost.objects.filter(author=user, champions_meeting=self.cm).count(), 3)
        # Still one TEAM post per person per event.
        self.assert_refused(author=user, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm)

    def test_a_working_copy_has_exactly_one_event(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpCommunityGuide.objects.create(author=self.member())

    def test_posting_the_team_again_leaves_the_guide_alone(self):
        user = self.member()
        guide = make_guide_post(user, self.cm, caption="My guide")
        first = self.team_post(user)
        again, created = gallery.publish_team(
            user, PvpTeam.objects.get(pk=first.source_team_id), caption="New words", confirm_public_identity=True,
        )
        self.assertFalse(created)
        self.assertEqual(again.pk, first.pk)
        guide.refresh_from_db()
        self.assertEqual((guide.kind, guide.caption), (GalleryPostKind.PVP_GUIDE, "My guide"))


class TeamReadsTests(GuideFixture):
    """A guide post belongs to its event, but nothing that reads TEAMS sees it."""

    def test_the_team_feed_holds_teams_only(self):
        user = self.member()
        team = self.team_post(user)
        make_guide_post(self.member(), self.cm)
        response = self.feed()
        self.assertEqual([row["public_id"] for row in response.data["results"]], [team.public_id])
        self.assertEqual(response.data["count"], 1)

    def test_rankings_count_teams_only(self):
        team = self.team_post(self.member())
        make_guide_post(self.member(), self.cm)
        self.assertEqual(list(gallery.visible_teams().values_list("pk", flat=True)), [team.pk])

    def test_my_posts_lists_a_guide_with_its_event(self):
        user = self.member()
        make_guide_post(user, self.cm, caption="Mine")
        response = self.client_for(user).get("/gallery/my-posts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["kind"], "pvp_guide")
        self.assertEqual(response.data[0]["event"], {"kind": "cm", "id": self.cm.id, "label": self.cm.name})

    def test_a_guide_is_never_copied_as_a_team(self):
        # The copy route counts a guide's "Use this deck" and hands back no team.
        post = make_guide_post(self.member(), self.cm)
        response = self.client_for(self.member()).post(f"/gallery/posts/{post.public_id}/copy")
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(response.data)


class GateTests(GuideFixture):
    """gallery_access.can_open: a guide post is early access first on every route."""

    def setUp(self):
        super().setUp()
        self.author = self.member()
        self.post = make_guide_post(self.author, self.cm)
        self.url = f"/gallery/posts/{self.post.public_id}"

    def test_the_rule(self):
        plain, member = self.person(), self.member()
        self.assertFalse(gallery_access.can_open(self.post, plain))
        self.assertTrue(gallery_access.can_open(self.post, member))
        self.assertTrue(gallery_access.can_open(self.post, self.person(SENIOR)))
        self.assertTrue(gallery_access.can_open(self.post, self.author))

    def test_a_reader_without_early_access_is_refused_everywhere(self):
        plain = self.client_for(self.person())
        detail = plain.get(self.url)
        self.assertEqual(detail.status_code, 403)
        self.assertEqual(detail.data, {"error": "Community guides are open to supporters first."})
        self.assertEqual(APIClient().get(self.url).status_code, 403)
        self.assertEqual(plain.put(f"{self.url}/vote", {"value": "up"}, format="json").status_code, 404)
        self.assertEqual(plain.post(f"{self.url}/comments", {"body": "Hi"}, format="json").status_code, 404)
        self.assertEqual(plain.get(f"{self.url}/comments").status_code, 404)
        report = plain.post("/gallery/reports", {"post": self.post.public_id, "reason": "spam"}, format="json")
        self.assertEqual(report.status_code, 404)
        self.assertEqual(plain.get(f"/profile-cards/post/{self.post.public_id}").status_code, 404)
        plain.post(f"{self.url}/view")
        self.post.refresh_from_db()
        self.assertEqual(self.post.view_count, 0)

    def test_a_member_reads_votes_and_comments(self):
        member = self.client_for(self.member())
        detail = member.get(self.url)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["kind"], "pvp_guide")
        self.assertEqual(detail.data["event"]["id"], self.cm.id)
        self.assertEqual(member.put(f"{self.url}/vote", {"value": "up"}, format="json").status_code, 200)
        self.assertEqual(member.post(f"{self.url}/comments", {"body": "Nice"}, format="json").status_code, 201)
        self.assertEqual(GalleryComment.objects.filter(post=self.post).count(), 1)

    def test_the_author_always_reaches_their_own(self):
        self.author.early_access = False
        self.author.save()
        self.assertEqual(self.client_for(self.author).get(self.url).status_code, 200)

    def test_a_profile_card_lists_guides_only_for_readers_who_may_open_them(self):
        comment_author = self.member()
        make_guide_post(comment_author, self.cm, caption="Theirs")
        team = self.team_post(comment_author)
        url = f"/profile-cards/post/{team.public_id}"
        kinds = [row["kind"] for row in self.client_for(self.person()).get(url).data["posts"]]
        self.assertEqual(kinds, ["pvp_team"])
        kinds = sorted(row["kind"] for row in self.client_for(self.member()).get(url).data["posts"])
        self.assertEqual(kinds, ["pvp_guide", "pvp_team"])


class EraseTests(GuideFixture):

    def test_erasing_an_account_removes_its_guides_and_drafts(self):
        user = self.member()
        make_guide_post(user, self.cm)
        make_working_copy(user, self.cm, title="Draft")
        gallery.erase_users([user])
        self.assertFalse(GalleryPost.objects.filter(author=user).exists())
        self.assertFalse(PvpCommunityGuide.objects.filter(author=user).exists())


# ── PR 2: writing, posting and reading ───────────────────────────────────────


class WritingFixture(GuideFixture):
    """A writer with early access, a skill on global and a JP-only skill that
    reads in UmaTL's translation (the one a reader must see credited)."""

    def setUp(self):
        super().setUp()
        self.writer = self.member()
        self.client_w = self.client_for(self.writer)
        self.skill = make_skill("Lead the Pack", 100011)
        self.jp_skill = make_skill(
            "", 200011, translated_name="Rising Dragon", availability=SkillAvailability.JP, jp_name="昇竜",
        )

    def start(self, client=None, **body):
        body = {"kind": "cm", "event": self.cm.id, **body}
        return (client or self.client_w).post("/community-guides", body, format="json")

    def content(self, **changes):
        """A document with one row in most sections."""
        body = {
            "scenario": None,
            "style_ranking": "Front >> End",
            "short_description": "Mile on turf.",
            "meta_analysis": "Go early. [[Lead the Pack]] wins it.",
            "stat_configs": [{"style": "everyone", "label": "Minimum", "speed": "1100"}],
            "team_comps": [{"text": "2 Front + 1 Pace", "note": ""}],
            "uma_recommendations": [
                {"style": "front", "uma": self.umas[0].id, "stars": 5, "role": "Runaway", "note": ""},
                {"style": "end", "uma": self.umas[1].id, "stars": 3, "role": "", "note": ""},
            ],
            "skill_recommendations": [
                {"style": "front", "category": "acceleration", "tier": 0, "skill": self.skill.id, "note": ""},
                {"style": "front", "category": "acceleration", "tier": 1, "skill": self.jp_skill.id, "note": ""},
            ],
            "style_notes": [{"style": "front", "body": "**Go early.**"}],
            "decks": [{
                "id": None, "style": "front", "label": "Budget", "note": "",
                "slots": [{"slot": 1, "support_card": self.cards[0].id, "note": ""},
                          {"slot": 6, "is_borrow": True, "support_card": self.cards[1].id, "note": ""}],
            }],
        }
        body.update(changes)
        return body

    def put(self, guide_id, version, client=None, **body):
        body = {"version": version, **body}
        return (client or self.client_w).put(f"/community-guides/{guide_id}/document", body, format="json")

    def written(self, title="Front runner line", client=None, **changes):
        """A working copy with the full content saved, as its API answer."""
        created = self.start(client, title=title).data
        saved = self.put(created["id"], created["version"], client, **self.content(**changes))
        self.assertEqual(saved.status_code, 200, saved.data)
        return saved.data["document"]

    def publish(self, guide_id, client=None, **body):
        body = {"confirm_public_identity": True, **body}
        return (client or self.client_w).post(f"/community-guides/{guide_id}/publish", body, format="json")


class StartTests(WritingFixture):

    def test_who_may_start_one(self):
        self.assertEqual(APIClient().post("/community-guides", {}, format="json").status_code, 401)
        plain = self.start(self.client_for(self.person()))
        self.assertEqual(plain.status_code, 403)
        self.assertEqual(plain.data["error"], "Community guides are open to supporters first.")
        blocked = self.member()
        blocked.publishing_blocked = True
        blocked.save()
        self.assertEqual(self.start(self.client_for(blocked)).status_code, 403)

    def test_an_empty_start(self):
        response = self.start(title="  My guide  ")
        self.assertEqual(response.status_code, 201)
        document = response.data
        self.assertEqual(document["title"], "My guide")
        self.assertEqual((document["kind"], document["event"], document["post"]), ("cm", self.cm.id, None))
        self.assertEqual(document["decks"], [])
        self.assertEqual(document["admin_comments"], "")

    def test_five_per_event(self):
        for _ in range(5):
            self.assertEqual(self.start().status_code, 201)
        sixth = self.start()
        self.assertEqual(sixth.status_code, 400)
        self.assertEqual(sixth.data["error"], "You can have 5 guides for one event. Delete one to write another.")
        loh = make_league_of_heroes()
        self.assertEqual(self.start(kind="loh", event=loh.id).status_code, 201)
        first = PvpCommunityGuide.objects.filter(author=self.writer, champions_meeting=self.cm).first()
        self.assertEqual(self.client_w.delete(f"/community-guides/{first.id}").status_code, 204)
        self.assertEqual(self.start().status_code, 201)

    def test_my_guides_lists_one_event(self):
        self.start(title="One")
        self.start(title="Two")
        self.start(kind="loh", event=make_league_of_heroes().id, title="Elsewhere")
        response = self.client_w.get("/community-guides", {"kind": "cm", "event": self.cm.id})
        self.assertEqual([row["title"] for row in response.data["guides"]], ["One", "Two"])
        self.assertEqual((response.data["cap"], response.data["can_write"]), (5, True))

    def test_starting_from_the_site_guide(self):
        site = PvpGuide.objects.create(
            champions_meeting=self.cm, visibility="supporters", style_ranking="Pace > End",
            admin_comments="Henry's private note",
        )
        PvpUmaRecommendation.objects.create(guide=site, style="pace", uma=self.umas[2], stars=4)
        document = self.start(start="official").data
        self.assertEqual(document["style_ranking"], "Pace > End")
        self.assertEqual(document["uma_recommendations"][0]["uma"], self.umas[2].id)
        self.assertEqual(document["admin_comments"], "")
        self.assertNotIn("Henry's private note", str(document))

    def test_no_site_guide_to_start_from_when_it_is_private(self):
        PvpGuide.objects.create(champions_meeting=self.cm, visibility="private")
        response = self.start(start="official")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "This event has no site guide to start from.")

    def test_starting_from_one_of_mine_and_not_from_someone_elses(self):
        mine = self.written()
        copy = self.start(start=mine["id"]).data
        self.assertEqual(copy["meta_analysis"], mine["meta_analysis"])
        theirs = make_working_copy(self.member(), self.cm)
        self.assertEqual(self.start(start=theirs.id).status_code, 400)


class SaveTests(WritingFixture):

    def test_a_save_round_trips_and_answers_the_readers_shape(self):
        created = self.start().data
        response = self.put(created["id"], created["version"], title="Renamed", **self.content(admin_comments="secret"))
        self.assertEqual(response.status_code, 200)
        document, guide = response.data["document"], response.data["guide"]
        self.assertEqual(document["version"], created["version"] + 1)
        self.assertEqual(document["title"], "Renamed")
        self.assertEqual(document["admin_comments"], "")
        self.assertEqual(document["decks"][0]["id"], 1)
        self.assertEqual([row["uma"]["id"] for row in guide["uma_recommendations"]],
                         [self.umas[0].id, self.umas[1].id])
        skills = {row["skill"]["id"]: row["skill"] for row in guide["skill_recommendations"]}
        self.assertTrue(skills[self.jp_skill.id]["uses_translation"])
        self.assertEqual(skills[self.jp_skill.id]["name"], "Rising Dragon")
        self.assertIn("lead the pack", guide["skill_mentions"])
        self.assertEqual(guide["comment_counts"], {})
        self.assertNotIn("secret", str(response.data))

    def test_a_stale_save_is_a_409_with_the_current_document(self):
        created = self.start().data
        self.put(created["id"], created["version"], **self.content())
        stale = self.put(created["id"], created["version"], **self.content(style_ranking="Mine"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["current"]["style_ranking"], "Front >> End")

    def test_refusals_name_their_path(self):
        created = self.start().data
        cases = (
            ({"team_comps": [{"text": f"Comp {n}", "note": ""} for n in range(16)]}, "team_comps"),
            ({"meta_analysis": "x" * 6001}, "meta_analysis"),
            ({"style_notes": [{"style": "front", "body": "x" * 4001}]}, "style_notes[0].body"),
            ({"uma_recommendations": [{"style": "front", "uma": 99999, "stars": 3}]}, "uma_recommendations[0].uma"),
        )
        for changes, path in cases:
            with self.subTest(path=path):
                response = self.put(created["id"], created["version"], **self.content(**changes))
                self.assertEqual(response.status_code, 400)
                self.assertIn(path, [error["path"] for error in response.data["errors"]])

    def test_a_too_long_guide(self):
        created = self.start().data
        long_decks = [{
            "id": None, "style": "front", "label": f"Deck {n}", "note": "n" * 300,
            "slots": [
                {"slot": 1 + (m % 6), "order": m // 6, "support_card": None, "note": "x" * 100} for m in range(24)
            ],
        } for n in range(30)]
        response = self.put(created["id"], created["version"], **self.content(decks=long_decks))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "This guide is too long to save. Take something out.")

    def test_someone_elses_guide_is_a_404(self):
        theirs = make_working_copy(self.member(), self.cm)
        self.assertEqual(self.client_w.get(f"/community-guides/{theirs.id}/document").status_code, 404)
        self.assertEqual(self.put(theirs.id, 0, **self.content()).status_code, 404)
        self.assertEqual(self.client_w.delete(f"/community-guides/{theirs.id}").status_code, 404)
        self.assertEqual(self.publish(theirs.id).status_code, 404)

    def test_starting_over(self):
        PvpGuide.objects.create(champions_meeting=self.cm, visibility="public", style_ranking="Site order")
        document = self.written()
        response = self.client_w.post(
            f"/community-guides/{document['id']}/start-over",
            {"version": document["version"], "start": "official"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["document"]["style_ranking"], "Site order")


class PublishTests(WritingFixture):

    def test_posting_needs_the_tick_and_a_title(self):
        document = self.written(title="")
        self.assertEqual(self.publish(document["id"], confirm_public_identity="true").status_code, 400)
        refused = self.publish(document["id"])
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data["errors"], [{"path": "title", "message": "Give your guide a title first."}])

    def test_every_word_goes_through_the_filters(self):
        BlockedTerm.objects.create(term="rude")
        cases = (
            ({"decks": [{**self.content()["decks"][0], "label": "www.spam.example"}]}, "decks[0].label"),
            ({"stat_configs": [{"style": "everyone", "label": "rude", "speed": ""}]}, "stat_configs[0].label"),
            ({"uma_recommendations": [{"style": "front", "uma": self.umas[0].id, "stars": 3, "role": "rude"}]},
             "uma_recommendations[0].role"),
        )
        for changes, path in cases:
            with self.subTest(path=path):
                document = self.written(**changes)
                response = self.publish(document["id"])
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["errors"][0]["path"], path)
                PvpCommunityGuide.objects.filter(pk=document["id"]).delete()

    def test_a_post_and_its_update(self):
        document = self.written()
        created = self.publish(document["id"])
        self.assertEqual(created.status_code, 201)
        self.assertEqual((created.data["kind"], created.data["caption"]), ("pvp_guide", "Front runner line"))
        post = GalleryPost.objects.get(public_id=created.data["public_id"])
        self.assertNotIn("admin_comments", post.guide_snapshot)
        first_update = post.content_updated_at

        same = self.publish(document["id"])
        self.assertEqual(same.status_code, 200)
        self.assertEqual(same.data["public_id"], created.data["public_id"])
        post.refresh_from_db()
        self.assertEqual(post.content_updated_at, first_update)

        current = self.client_w.get(f"/community-guides/{document['id']}/document").data["document"]
        self.assertEqual(current["post"], created.data["public_id"])
        self.assertFalse(current["changed_since_posted"])
        self.put(document["id"], current["version"], **self.content(style_ranking="End >> Front"))
        changed = self.client_w.get(f"/community-guides/{document['id']}/document").data["document"]
        self.assertTrue(changed["changed_since_posted"])
        self.publish(document["id"])
        post.refresh_from_db()
        self.assertGreater(post.content_updated_at, first_update)
        self.assertEqual(post.guide_snapshot["style_ranking"], "End >> Front")

    def test_a_hidden_post_stays_hidden(self):
        document = self.written()
        public_id = self.publish(document["id"]).data["public_id"]
        GalleryPost.objects.filter(public_id=public_id).update(is_hidden=True, hidden_reason="moderator")
        self.publish(document["id"])
        self.assertTrue(GalleryPost.objects.get(public_id=public_id).is_hidden)

    def test_a_posted_guide_is_deleted_only_once_taken_down(self):
        document = self.written()
        public_id = self.publish(document["id"]).data["public_id"]
        refused = self.client_w.delete(f"/community-guides/{document['id']}")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data["error"], "Take the post down first.")
        self.assertEqual(self.client_w.delete(f"/gallery/posts/{public_id}").status_code, 204)
        self.assertEqual(self.client_w.delete(f"/community-guides/{document['id']}").status_code, 204)


class ReadingTests(WritingFixture):

    def posted(self, title="Front runner line", **changes):
        document = self.written(title=title, **changes)
        return self.publish(document["id"]).data["public_id"]

    def test_the_post_carries_the_guide_in_the_readers_shape(self):
        public_id = self.posted()
        reader = self.client_for(self.member())
        response = reader.get(f"/gallery/posts/{public_id}")
        self.assertEqual(response.status_code, 200)
        guide = response.data["guide"]
        self.assertEqual(guide["visibility"], "public")
        self.assertEqual(guide["event"], self.cm.id)
        self.assertEqual(guide["decks"][0]["slots"][0]["support_card"]["id"], self.cards[0].id)
        jp = next(row["skill"] for row in guide["skill_recommendations"] if row["skill"]["id"] == self.jp_skill.id)
        self.assertTrue(jp["uses_translation"])
        self.assertNotIn("admin_comments", all_keys(response.data))
        self.assertNotIn("source_guide", all_keys(response.data))

    def test_an_uma_that_left_the_catalogue_drops_out(self):
        public_id = self.posted()
        self.umas[1].delete()
        guide = self.client_w.get(f"/gallery/posts/{public_id}").data["guide"]
        self.assertEqual([row["uma"]["id"] for row in guide["uma_recommendations"]], [self.umas[0].id])

    def test_the_list(self):
        top = self.posted(title="Top one")
        other_client = self.client_for(self.member())
        other_doc = self.written(title="Second", client=other_client)
        second = self.publish(other_doc["id"], other_client).data["public_id"]
        other_client.put(f"/gallery/posts/{top}/vote", {"value": "up"}, format="json")
        url = f"/pvp-guide-gallery/cm/{self.cm.id}"
        self.assertEqual(self.client_for(self.person()).get(url).status_code, 403)
        self.assertEqual(APIClient().get(url).status_code, 403)
        listed = other_client.get(url)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["count"], 2)
        self.assertEqual(listed.data["results"][0]["public_id"], top)
        self.assertEqual(listed.data["results"][0]["umas"], [{"uma": self.umas[0].id}, {"uma": self.umas[1].id}])
        self.assertNotIn("guide", listed.data["results"][0])
        newest = other_client.get(url, {"sort": "new"}).data["results"]
        self.assertEqual(newest[0]["public_id"], second)
        GalleryPost.objects.filter(public_id=second).update(is_hidden=True, hidden_reason="moderator")
        self.assertEqual(other_client.get(url).data["count"], 1)

    def test_the_list_costs_no_query_per_row(self):
        reader = self.client_for(self.member())
        url = f"/pvp-guide-gallery/cm/{self.cm.id}"
        self.posted(title="One")
        reader.get(url)  # warm the per-process lookups
        with CaptureQueriesContext(connection) as one:
            reader.get(url)
        for index in range(3):
            client = self.client_for(self.member())
            document = self.written(title=f"More {index}", client=client)
            self.publish(document["id"], client)
        with CaptureQueriesContext(connection) as four:
            self.assertEqual(reader.get(url).data["count"], 4)
        self.assertEqual(len(four), len(one))

    def test_use_this_deck_counts_as_a_copy(self):
        public_id = self.posted()
        response = self.client_for(self.member()).post(f"/gallery/posts/{public_id}/copy")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(GalleryPost.objects.get(public_id=public_id).copy_count, 1)

    def test_the_share_page_names_the_event_and_nothing_else(self):
        public_id = self.posted(title="Secret sauce")
        page = APIClient().get(f"/post/{public_id}").content.decode()
        self.assertIn(f"A community guide for {self.cm.name}", page)
        self.assertNotIn("Secret sauce", page)
        self.assertNotIn(self.writer.display_name, page)
        self.assertIn("guides=community", page)
        self.assertEqual(APIClient().get(f"/post/{public_id}/preview.jpg").status_code, 404)

    def test_the_account_says_who_gets_community_guides(self):
        self.assertTrue(self.client_w.get("/account").data["community_guides"])
        self.assertFalse(self.client_for(self.person()).get("/account").data["community_guides"])

    def test_a_moderation_card_carries_the_guides_words(self):
        public_id = self.posted()
        post = GalleryPost.objects.get(public_id=public_id)
        text = moderation_cards.item_text(post)
        self.assertIn("Title: Front runner line", text)
        self.assertIn("Go early.", text)
        post.guide_snapshot = {**post.guide_snapshot, "meta_analysis": "y" * 3000}
        self.assertIn("more characters)", community_guide_document.card_text(post))
