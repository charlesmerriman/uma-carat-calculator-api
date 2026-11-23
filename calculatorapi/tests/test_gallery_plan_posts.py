"""A pull plan post on the gallery's shared layer (notes/pull-plan-database-plan.md, PR 1).

Nothing in the app makes a plan post yet: these build one straight in the
database and send it through everything that works on ANY gallery post (the
post routes, votes, views, comments, reports, the share page, the profile
card, moderation cards, the downvote alert, erasing an account, the admin),
so the plan kind is safe everywhere before the route that creates it exists.
The team gallery's own behaviour is pinned in test_gallery.py and
test_pvp_gallery.py; here only what a plan post changes.
"""

from unittest import mock

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import gallery, moderation_cards
from calculatorapi.models import (
    CustomUser,
    GalleryPost,
    GalleryPostKind,
    GalleryReportReason,
    Plan,
    PlanSpending,
    PlanSweat,
    VoteValue,
)
from calculatorapi.tests.base import PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import make_scenario
from calculatorapi.tests.test_moderation_cards import CHANNEL, ok
from calculatorapi.tests.test_pvp_gallery import FORBIDDEN, ROW_KEYS, RouteFixture, all_keys
from calculatorapi.views.gallery_share import SITE_NAME

# What a plan post's open payload carries in PR 1: the common fields only.
# The plan's own fields arrive with the plan feed (PR 2).
PLAN_FULL_KEYS = (ROW_KEYS - {"umas"}) | {"strategy", "hidden"}


def make_plan_post(user, **fields):
    """A plan post by `user`, made straight in the database."""
    return GalleryPost.objects.create(
        public_id=gallery.generate_unique_post_id(),
        author=user,
        kind=GalleryPostKind.PLAN,
        caption=fields.pop("caption", "F2P road to the anniversary"),
        strategy=fields.pop("strategy", "Skip everything until the anniversary."),
        plan_snapshot=fields.pop("plan_snapshot", {"rows": []}),
        **fields,
    )


class PlanPostConstraintTests(RouteFixture):

    def test_a_team_post_carries_no_plan_field(self):
        user = self.person()
        plan = Plan.objects.create(user=user, name="Main")
        for field, value in (
            ("plan_spending", PlanSpending.F2P),
            ("plan_sweat", PlanSweat.COMP),
            ("plan_strategies", "front,end"),
            ("plan_range", make_scenario()),
            ("source_plan", plan),
        ):
            with self.subTest(field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    GalleryPost.objects.create(
                        author=user, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm,
                        **{field: value},
                    )

    def test_a_plan_post_has_no_event(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_plan_post(self.person(), champions_meeting=self.cm)

    def test_a_plan_post_takes_every_plan_field(self):
        user = self.person()
        post = make_plan_post(
            user,
            plan_spending=PlanSpending.DOLPHIN,
            plan_sweat=PlanSweat.NO_LIFE,
            plan_strategies="pace,late",
            plan_range=make_scenario(),
            source_plan=Plan.objects.create(user=user, name="Main"),
        )
        self.assertTrue(post.is_plan)
        self.assertIsNone(post.event)

    def test_one_post_per_plan(self):
        user = self.person()
        plan = Plan.objects.create(user=user, name="Main")
        make_plan_post(user, source_plan=plan)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_plan_post(user, source_plan=plan)
        # Posts whose plans were deleted have none, and may be several.
        make_plan_post(user)
        make_plan_post(user)

    def test_deleting_the_plan_or_the_scenario_leaves_the_post(self):
        user = self.person()
        plan = Plan.objects.create(user=user, name="Main")
        scenario = make_scenario()
        post = make_plan_post(user, source_plan=plan, plan_range=scenario)
        plan.delete()
        scenario.delete()
        post.refresh_from_db()
        self.assertIsNone(post.source_plan)
        self.assertIsNone(post.plan_range)


class PlanPostRouteTests(RouteFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person()
        self.post_row = make_plan_post(self.author)
        self.url = f"/gallery/posts/{self.post_row.public_id}"

    def test_detail_sends_the_common_fields_and_says_its_kind(self):
        response = APIClient().get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), PLAN_FULL_KEYS)
        self.assertEqual(response.data["kind"], "plan")
        self.assertEqual(response.data["caption"], "F2P road to the anniversary")
        self.assertFalse(all_keys(response.data) & (FORBIDDEN | {"team", "event", "source_plan"}))

    def test_a_team_post_says_its_kind_too(self):
        team_post = self.post(self.person())
        response = APIClient().get(f"/gallery/posts/{team_post.public_id}")
        self.assertEqual(response.data["kind"], "pvp_team")

    def test_votes_both_ways_and_the_score_never_reads_below_zero(self):
        up, down, down_too = self.person(), self.person(), self.person()
        self.assertEqual(self.client_for(up).put(f"{self.url}/vote", {"value": "up"}, format="json").status_code, 200)
        self.client_for(down).put(f"{self.url}/vote", {"value": "down"}, format="json")
        response = self.client_for(down_too).put(f"{self.url}/vote", {"value": "down"}, format="json")
        self.assertEqual(response.data["score"], 0)
        self.assertEqual(gallery.tally(self.post_row.votes), (1, 2))

    def test_a_view_is_counted(self):
        self.assertEqual(APIClient().post(f"{self.url}/view").status_code, 204)
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.view_count, 1)

    def test_comments(self):
        reader = self.person()
        created = self.client_for(reader).post(f"{self.url}/comments", {"body": "Nice plan"}, format="json")
        self.assertEqual(created.status_code, 201)
        thread = APIClient().get(f"{self.url}/comments")
        self.assertEqual(thread.status_code, 200)
        self.assertIn("Nice plan", str(thread.data))

    def test_report(self):
        response = self.client_for(self.person()).post(
            "/gallery/reports", {"post": self.post_row.public_id, "reason": "spam"}, format="json"
        )
        self.assertEqual(response.status_code, 201)

    def test_the_author_takes_it_down(self):
        self.assertEqual(self.client_for(self.person()).delete(self.url).status_code, 404)
        self.assertEqual(self.client_for(self.author).delete(self.url).status_code, 204)
        self.assertFalse(GalleryPost.objects.filter(pk=self.post_row.pk).exists())

    def test_the_team_copy_route_refuses_a_plan_and_counts_nothing(self):
        response = APIClient().post(f"{self.url}/copy")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {"error": "This post isn't available any more."})
        with self.assertRaises(gallery.GalleryRefused):
            gallery.copy_snapshot(mock.Mock(user=self.person()), self.post_row)
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.copy_count, 0)

    def test_my_posts_lists_team_posts_only_for_now(self):
        team_post = self.post(self.author)
        response = self.client_for(self.author).get("/gallery/my-posts")
        self.assertEqual([row["public_id"] for row in response.data], [team_post.public_id])

    def test_never_in_an_event_feed(self):
        self.post(self.person())
        rows = self.feed().data["results"]
        self.assertNotIn(self.post_row.public_id, [row["public_id"] for row in rows])

    def test_share_page_uses_the_sites_tags_and_draws_no_picture(self):
        page = APIClient().get(f"/post/{self.post_row.public_id}")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn(f"<title>{SITE_NAME}</title>", html)
        self.assertNotIn(self.post_row.caption, html)
        picture = APIClient().get(f"/post/{self.post_row.public_id}/preview.jpg")
        self.assertEqual(picture.status_code, 404)

    def test_profile_card_opens_and_lists_team_posts_only(self):
        team_post = self.post(self.author)
        response = APIClient().get(f"/profile-cards/post/{self.post_row.public_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["public_id"] for row in response.data["posts"]], [team_post.public_id])

    def test_erasing_the_author_takes_it_away(self):
        gallery.erase_users([self.author])
        self.assertFalse(GalleryPost.objects.filter(pk=self.post_row.pk).exists())


class PlanPostAlertTests(RouteFixture):

    def test_the_downvote_alert_names_a_plan_not_an_event(self):
        post = make_plan_post(self.person())
        voters = [self.person() for _ in range(10)]
        with mock.patch.object(gallery.discord_messages, "send") as send:
            for voter in voters:
                gallery.vote(voter, post, VoteValue.DOWN)
        content = send.call_args.kwargs["content"]
        self.assertTrue(content.startswith("A pull plan is down to -10 (0 up, 10 down)"), content)
        self.assertNotIn(post.caption, content)


@override_settings(DISCORD_BOT_TOKEN="test-token", DISCORD_MOD_QUEUE_CHANNEL_ID=CHANNEL)
class PlanPostModerationCardTests(RouteFixture):

    def test_the_card_names_a_plan_and_its_two_texts(self):
        post = make_plan_post(self.person(display_name="CaratSaver"))
        with mock.patch("requests.request", return_value=ok()) as call:
            with self.captureOnCommitCallbacks(execute=True):
                gallery.report(self.person(), reason=GalleryReportReason.SPAM, post=post)
        card = call.call_args.kwargs["json"]["embeds"][0]
        self.assertEqual(card["title"], "Plan reported")
        self.assertIn("Title: F2P road to the anniversary", card["description"])
        self.assertIn("About this plan: Skip everything until the anniversary.", card["description"])

    def test_a_plan_with_no_words_says_so(self):
        post = make_plan_post(self.person(), caption="", strategy="")
        self.assertEqual(moderation_cards.item_text(post), "(a plan with no title or description)")


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class PlanPostAdminTests(RouteFixture):

    def setUp(self):
        super().setUp()
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))
        user = self.person()
        self.post_row = make_plan_post(
            user,
            plan_spending=PlanSpending.F2P,
            plan_range=make_scenario(),
            source_plan=Plan.objects.create(user=user, name="Main"),
        )

    def test_the_post_pages_render_with_a_plan_post(self):
        listing = self.client.get(reverse("admin:calculatorapi_gallerypost_changelist"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, self.post_row.public_id)
        page = self.client.get(reverse("admin:calculatorapi_gallerypost_change", args=[self.post_row.pk]))
        self.assertEqual(page.status_code, 200)
