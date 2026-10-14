"""The gallery routes: posting a PvP team, the feed, one post, votes, views,
copies, and the account preferences the gallery adds.

The rules behind them are tested in test_gallery.py; this module checks the
routes apply them and send exactly the fields they should.
"""

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import gallery
from calculatorapi.models import (
    BlockedTerm,
    CustomUser,
    GalleryReport,
    GalleryHiddenReason,
    GalleryPost,
    GalleryVote,
)
from calculatorapi.tests.base import PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client, make_league_of_heroes
from calculatorapi.tests.test_gallery import CLASSIC, JUNIOR, SENIOR, GalleryFixture
from calculatorapi.views.gallery import GalleryCopyThrottle, GalleryReadThrottle, GalleryVoteThrottle
from calculatorapi.views.pvp_gallery import GALLERY_PAGE_SIZE, GalleryPublishThrottle

ROW_KEYS = {
    "public_id", "caption", "published_at", "content_updated_at", "is_mine", "umas",
    "stats", "my_vote", "poster",
}
FULL_KEYS = ROW_KEYS | {"team", "event", "hidden"}
POSTER_KEYS = {"display_name", "portrait", "tier_name", "gallery_style"}
# Keys that must never appear anywhere in a gallery payload.
FORBIDDEN = {"username", "user", "author", "author_id", "name", "notes", "is_focus",
             "weight", "points", "order", "email", "source_team", "voters"}


def all_keys(value):
    """Every dict key anywhere inside `value`."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in all_keys(item)}
    return set()


class RouteFixture(GalleryFixture):

    def client_for(self, user):
        client, _ = auth_client(user)
        return client

    def feed(self, client=None, event=None, **params):
        event = event or self.cm
        return (client or APIClient()).get(f"/pvp-gallery/cm/{event.id}", params)


class PublishRouteTests(RouteFixture):

    def publish(self, user, team, body=None):
        body = {"confirm_public_identity": True} if body is None else body
        return self.client_for(user).post(f"/pvp-teams/{team.id}/publish", body, format="json")

    def test_guests_cannot_post(self):
        user = self.person()
        response = APIClient().post(f"/pvp-teams/{self.team(user).id}/publish", {}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_someone_elses_team_is_a_404(self):
        response = self.publish(self.person(), self.team(self.person()))
        self.assertEqual(response.status_code, 404)

    def test_create_then_replace(self):
        user = self.person(SENIOR)
        team = self.team(user)
        created = self.publish(user, team, {"confirm_public_identity": True, "caption": "Mine"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(set(created.data), FULL_KEYS)
        self.assertEqual(created.data["caption"], "Mine")
        self.assertTrue(created.data["is_mine"])
        self.assertEqual(created.data["event"], {"kind": "cm", "id": self.cm.id})
        again = self.publish(user, team)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["public_id"], created.data["public_id"])

    def test_refusals_are_one_sentence(self):
        user = self.person()
        team = self.team(user)
        for body in ({"confirm_public_identity": "true"}, {"confirm_public_identity": True, "caption": 5},
                     {"confirm_public_identity": True, "caption": "www.spam.example"}):
            with self.subTest(body=body):
                response = self.publish(user, team, body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.data), {"error"})

    def test_a_blocked_account_gets_403(self):
        user = self.person()
        user.publishing_blocked = True
        user.save()
        self.assertEqual(self.publish(user, self.team(user)).status_code, 403)

    def test_the_team_list_says_which_team_is_posted(self):
        user = self.person()
        posted = self.team(user)
        other = self.team(user)
        post = self.publish(user, posted).data
        teams = {team["id"]: team["post"] for team in self.client_for(user).get("/pvp-teams").data}
        self.assertEqual(teams, {posted.id: post["public_id"], other.id: None})


class FeedTests(RouteFixture):

    def test_rows_carry_exactly_the_public_fields(self):
        self.post(self.person(CLASSIC, display_name="Turf"), )
        response = self.feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"count", "sort", "page", "has_more", "results"})
        row = response.data["results"][0]
        self.assertEqual(set(row), ROW_KEYS)
        self.assertEqual(set(row["poster"]), POSTER_KEYS)
        self.assertEqual(row["poster"]["gallery_style"], "sr")
        self.assertEqual(set(row["stats"]), {"views", "upvotes", "comments"})
        self.assertEqual(set(row["umas"][0]), {"uma", "style"})
        self.assertFalse(all_keys(response.data) & FORBIDDEN)

    def test_my_vote_is_null_for_a_guest(self):
        post = self.post(self.person())
        voter = self.person()
        GalleryVote.objects.create(post=post, user=voter)
        self.assertIsNone(self.feed().data["results"][0]["my_vote"])
        self.assertTrue(self.feed(self.client_for(voter)).data["results"][0]["my_vote"])
        self.assertFalse(self.feed(self.client_for(self.person())).data["results"][0]["my_vote"])

    def test_one_event_only_and_hidden_or_blocked_left_out(self):
        shown = self.post(self.person())
        self.post(self.person(), event={"league_of_heroes": make_league_of_heroes()})
        hidden = self.post(self.person())
        GalleryPost.objects.filter(pk=hidden.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS
        )
        blocked = self.person()
        self.post(blocked)
        blocked.publishing_blocked = True
        blocked.save()
        ids = [row["public_id"] for row in self.feed().data["results"]]
        self.assertEqual(ids, [shown.public_id])

    def test_sort_and_paging(self):
        authors = [self.person() for _ in range(GALLERY_PAGE_SIZE + 1)]
        for author in authors:
            self.post(author)
        first = self.feed(sort="new")
        self.assertEqual(first.data["count"], GALLERY_PAGE_SIZE + 1)
        self.assertTrue(first.data["has_more"])
        self.assertEqual(len(first.data["results"]), GALLERY_PAGE_SIZE)
        second = self.feed(sort="new", page=2)
        self.assertFalse(second.data["has_more"])
        self.assertEqual(len(second.data["results"]), 1)
        self.assertEqual(self.feed(sort="bogus").data["sort"], "hot")

    def test_an_unknown_event_is_a_404(self):
        self.assertEqual(APIClient().get("/pvp-gallery/cm/9999").status_code, 404)
        self.assertEqual(APIClient().get(f"/pvp-gallery/xx/{self.cm.id}").status_code, 404)


class PostRouteTests(RouteFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person(JUNIOR)
        self.post_row = self.post(self.author)
        self.url = f"/gallery/posts/{self.post_row.public_id}"

    def hide(self):
        GalleryPost.objects.filter(pk=self.post_row.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS
        )

    def test_detail(self):
        response = APIClient().get(self.url)
        self.assertEqual(set(response.data), FULL_KEYS)
        self.assertEqual(set(response.data["stats"]), {"views", "upvotes", "comments", "copies"})
        self.assertEqual(len(response.data["team"]), 3)
        self.assertFalse(all_keys(response.data) & FORBIDDEN)

    def test_a_hidden_post_is_a_404_except_to_its_author(self):
        self.hide()
        self.assertEqual(APIClient().get(self.url).status_code, 404)
        own = self.client_for(self.author).get(self.url)
        self.assertEqual(own.status_code, 200)
        self.assertTrue(own.data["hidden"])

    def test_only_the_author_takes_it_down(self):
        self.assertEqual(self.client_for(self.person()).delete(self.url).status_code, 404)
        self.assertEqual(APIClient().delete(self.url).status_code, 404)
        self.assertEqual(self.client_for(self.author).delete(self.url).status_code, 204)
        self.assertFalse(GalleryPost.objects.exists())

    def test_vote_and_take_it_back(self):
        voter = self.client_for(self.person())
        response = voter.put(f"{self.url}/vote")
        self.assertEqual(response.data, {"upvotes": 1, "my_vote": True})
        self.assertEqual(voter.put(f"{self.url}/vote").data["upvotes"], 1)
        self.assertEqual(voter.delete(f"{self.url}/vote").data, {"upvotes": 0, "my_vote": False})

    def test_vote_refusals(self):
        self.assertEqual(APIClient().put(f"{self.url}/vote").status_code, 401)
        own = self.client_for(self.author).put(f"{self.url}/vote")
        self.assertEqual(own.status_code, 403)
        self.hide()
        self.assertEqual(self.client_for(self.person()).put(f"{self.url}/vote").status_code, 404)

    def test_views_always_answer_204(self):
        client = APIClient(REMOTE_ADDR="203.0.113.7", HTTP_USER_AGENT="Mozilla/5.0")
        for _ in range(2):
            self.assertEqual(client.post(f"{self.url}/view").status_code, 204)
        self.assertEqual(client.post("/gallery/posts/nothere1/view").status_code, 204)
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.view_count, 1)

    def test_copy_hands_out_the_team_and_counts(self):
        response = APIClient().post(f"{self.url}/copy")
        self.assertEqual(set(response.data), {"kind", "event", "members"})
        self.assertEqual(response.data["kind"], "cm")
        self.assertEqual(len(response.data["members"]), 3)
        self.assertFalse(all_keys(response.data) & FORBIDDEN)
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.copy_count, 1)

    def test_my_posts_include_hidden_ones(self):
        self.hide()
        mine = self.client_for(self.author).get("/gallery/my-posts")
        self.assertEqual([row["public_id"] for row in mine.data], [self.post_row.public_id])
        self.assertTrue(mine.data[0]["hidden"])
        self.assertEqual(self.client_for(self.person()).get("/gallery/my-posts").data, [])

    def test_throttle_scopes(self):
        self.assertEqual(GalleryReadThrottle.scope, "gallery_read")
        self.assertEqual(GalleryVoteThrottle.scope, "gallery_vote")
        self.assertEqual(GalleryCopyThrottle.scope, "gallery_copy")
        self.assertEqual(GalleryPublishThrottle.scope, "gallery_publish")


class AccountPreferenceTests(RouteFixture):

    def test_the_badge_toggle(self):
        user = self.person()
        client = self.client_for(user)
        self.assertTrue(client.get("/account").data["show_supporter_badge"])
        response = client.patch("/account", {"show_supporter_badge": False}, format="json")
        self.assertFalse(response.data["show_supporter_badge"])
        user.refresh_from_db()
        self.assertFalse(user.show_supporter_badge)

    def test_blanking_the_name_is_refused_while_a_team_is_posted(self):
        user = self.person()
        self.post(user)
        response = self.client_for(user).patch("/account", {"display_name": ""}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["display_name"], ["Take your posted teams down first."])
        GalleryPost.objects.all().delete()
        response = self.client_for(user).patch("/account", {"display_name": ""}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_a_blocked_term_is_refused_in_a_display_name(self):
        BlockedTerm.objects.create(term="rude")
        user = self.person()
        response = self.client_for(user).patch("/account", {"display_name": "RudeOne Rude"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_publishing_blocked_cannot_be_set_by_the_person(self):
        user = self.person()
        self.client_for(user).patch("/account", {"publishing_blocked": True}, format="json")
        user.refresh_from_db()
        self.assertFalse(user.publishing_blocked)


class CommentRouteTests(RouteFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person(SENIOR)
        self.post_row = self.post(self.author)
        self.url = f"/gallery/posts/{self.post_row.public_id}/comments"
        self.commenter = self.person()

    def comment(self, user, body, parent=None):
        payload = {"body": body} if parent is None else {"body": body, "parent": parent}
        return self.client_for(user).post(self.url, payload, format="json")

    def test_thread_shape(self):
        top = self.comment(self.commenter, "Nice").data
        self.assertEqual(set(top), {"id", "state", "body", "created_at", "edited", "earlier_version",
                                    "is_author", "is_mine", "poster", "replies"})
        reply = self.comment(self.author, "Thanks", top["id"]).data
        self.assertTrue(reply["is_author"])
        thread = APIClient().get(self.url).data
        self.assertEqual(set(thread), {"count", "page", "has_more", "results"})
        self.assertEqual(thread["results"][0]["replies"][0]["body"], "Thanks")
        self.assertEqual(thread["results"][0]["replies"][0]["poster"]["gallery_style"], "ssr")
        self.assertFalse(all_keys(thread) & FORBIDDEN)

    def test_guests_read_but_cannot_write(self):
        self.assertEqual(APIClient().get(self.url).status_code, 200)
        self.assertEqual(APIClient().post(self.url, {"body": "Hi"}, format="json").status_code, 401)

    def test_feed_counts_visible_comments(self):
        self.comment(self.commenter, "One")
        self.comment(self.person(), "Two")
        self.assertEqual(self.feed().data["results"][0]["stats"]["comments"], 2)

    def test_edit_and_delete_only_your_own(self):
        mine = self.comment(self.commenter, "Mine").data
        url = f"/gallery/comments/{mine['id']}"
        self.assertEqual(self.client_for(self.person()).patch(url, {"body": "x"}, format="json").status_code, 404)
        edited = self.client_for(self.commenter).patch(url, {"body": "Edited"}, format="json")
        self.assertTrue(edited.data["edited"])
        self.assertEqual(self.client_for(self.commenter).delete(url).status_code, 204)

    def test_an_earlier_version_is_flagged(self):
        self.comment(self.commenter, "Before")
        gallery_repost = self.client_for(self.author).post(
            f"/pvp-teams/{self.post_row.source_team_id}/publish",
            {"confirm_public_identity": True}, format="json",
        )
        self.assertEqual(gallery_repost.status_code, 200)
        self.comment(self.person(), "After")
        flags = [row["earlier_version"] for row in APIClient().get(self.url).data["results"]]
        self.assertEqual(flags, [True, False])


class ReportRouteTests(RouteFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person()
        self.post_row = self.post(self.author)

    def report(self, user, body):
        return self.client_for(user).post("/gallery/reports", body, format="json")

    def test_report_a_post_then_a_repeat(self):
        reporter = self.person()
        body = {"post": self.post_row.public_id, "reason": "spam"}
        self.assertEqual(self.report(reporter, body).status_code, 201)
        self.assertEqual(self.report(reporter, body).status_code, 409)

    def test_report_a_comment(self):
        comment = gallery.add_comment(self.person(), self.post_row, "Bad words")
        response = self.report(self.person(), {"comment": comment.pk, "reason": "harassment", "note": "rude"})
        self.assertEqual(response.status_code, 201)

    def test_refusals(self):
        self.assertEqual(APIClient().post("/gallery/reports", {}, format="json").status_code, 401)
        self.assertEqual(self.report(self.person(), {"reason": "spam"}).status_code, 400)
        self.assertEqual(self.report(self.person(), {"post": "nothere1", "reason": "spam"}).status_code, 404)
        self.assertEqual(
            self.report(self.person(), {"post": self.post_row.public_id, "reason": "bogus"}).status_code, 400
        )
        self.assertEqual(
            self.report(self.author, {"post": self.post_row.public_id, "reason": "spam"}).status_code, 400
        )


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class ModerationAdminTests(RouteFixture):

    def setUp(self):
        super().setUp()
        self.boss = CustomUser.objects.create_superuser(username="boss", password="x")
        self.client.force_login(self.boss)
        self.post_row = self.post(self.person())
        comment = gallery.add_comment(self.person(), self.post_row, "Spam")
        for target in ({"post": self.post_row}, {"comment": comment}):
            for _ in range(3):
                gallery.report(self.person(), reason="spam", **target)
        BlockedTerm.objects.create(term="rude")

    def test_pages_render(self):
        for name in ("galleryreport", "gallerypost", "gallerycomment", "blockedterm"):
            with self.subTest(name=name):
                response = self.client.get(reverse(f"admin:calculatorapi_{name}_changelist"))
                self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("admin:calculatorapi_gallerypost_change", args=[self.post_row.pk]))
        self.assertEqual(response.status_code, 200)

    def test_keep_from_the_queue(self):
        reports = GalleryReport.objects.filter(post=self.post_row)
        self.client.post(reverse("admin:calculatorapi_galleryreport_changelist"), {
            "action": "keep_items", "_selected_action": [report.pk for report in reports],
        })
        self.post_row.refresh_from_db()
        self.assertFalse(self.post_row.is_hidden)
        self.assertEqual(set(reports.values_list("resolved_by", flat=True)), {self.boss.pk})

    def test_block_from_the_post_page(self):
        self.client.post(reverse("admin:calculatorapi_gallerypost_changelist"), {
            "action": "block_authors", "_selected_action": [self.post_row.pk],
        })
        self.post_row.refresh_from_db()
        self.assertTrue(self.post_row.author.publishing_blocked)
        self.assertEqual(self.post_row.hidden_reason, "moderator")
