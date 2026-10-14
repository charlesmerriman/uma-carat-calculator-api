"""The gallery routes: posting a PvP team, the feed, one post, votes, views,
copies, and the account preferences the gallery adds.

The rules behind them are tested in test_gallery.py; this module checks the
routes apply them and send exactly the fields they should.
"""

from rest_framework.test import APIClient

from calculatorapi.models import (
    BlockedTerm,
    GalleryHiddenReason,
    GalleryPost,
    GalleryVote,
)
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
