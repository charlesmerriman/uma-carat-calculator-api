"""Community guides: a player's own PvP guide as the gallery's third kind of
post (guide_gallery.py, gallery_access.py, models/pvp_community_guide.py;
notes/community-guides-plan.md).

The groundwork (PR 1) is pinned first: the constraints, the one gate every
route to a post asks, and that no team read ever counts a guide post.
"""

# Every class here sits on RouteFixture's chain plus this module's own fixture.
# pylint: disable=too-many-ancestors

from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from calculatorapi import gallery, gallery_access
from calculatorapi.models import (
    GalleryComment,
    GalleryPost,
    GalleryPostKind,
    PvpCommunityGuide,
    PvpTeam,
)
from calculatorapi.tests.factories import make_league_of_heroes
from calculatorapi.tests.test_gallery import SENIOR
from calculatorapi.tests.test_pvp_gallery import RouteFixture


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

    def test_a_guide_is_not_copied_as_a_team(self):
        post = make_guide_post(self.member(), self.cm)
        response = self.client_for(self.member()).post(f"/gallery/posts/{post.public_id}/copy")
        self.assertEqual(response.status_code, 404)


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
