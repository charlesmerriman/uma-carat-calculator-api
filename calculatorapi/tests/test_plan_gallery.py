"""Community plans: posting a plan, the feed, copying (plan_gallery.py,
views/plan_gallery.py; notes/pull-plan-database-plan.md, PR 2).

A plan post on the gallery's SHARED routes (reading, votes, comments, reports,
take-down, the admin) is pinned in test_gallery_plan_posts.py.
"""

# Every class here sits on RouteFixture's chain plus this module's own fixture.
# pylint: disable=too-many-ancestors

import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command

from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi import gallery, gallery_moderation, moderation_cards, plan_gallery, plans
from calculatorapi.models import (
    PLAN_CAP,
    GalleryPost,
    GalleryVote,
    Plan,
    UserPlannedBanner,
    VoteValue,
)
from calculatorapi.tests.factories import make_scenario, make_support_banner, make_timeline, make_uma_banner
from calculatorapi.tests.test_gallery_plan_posts import make_plan_post
from calculatorapi.tests.test_pvp_gallery import FORBIDDEN, RouteFixture, all_keys
from calculatorapi.views.plan_public import SharedPlannedBannerSerializer
from calculatorapi.views.user_planned_banner import NOTE_MAX_LENGTH
from calculatorapi.views.user import GameStatsSerializer

# Every stat that may NOT go on a post, whatever is ticked: balances (what a
# person holds) and choices about spending. With POSTED_INCOME_FIELDS this
# must be the whole stats block, so a new stat has to pick a side here.
NEVER_POSTED = {
    "current_carat", "current_paid_carat", "uma_ticket", "support_ticket",
    "uma_selector_ticket", "support_selector_ticket",
    "sr_shards", "sr_crystals", "ssr_shards", "ssr_crystals",
    "spend_tickets_on_banners", "discounted_paid_pulls", "full_price_paid_pulls",
    "daily_pulls_other_uma_banners", "daily_pulls_other_support_banners",
    "include_purchases_in_projection", "webstore_bonus", "skipped_race_events",
}
ROW_FIELDS = set(SharedPlannedBannerSerializer.Meta.fields)


def days(count):
    return timezone.now() + datetime.timedelta(days=count)


class PlanGalleryFixture(RouteFixture):

    def setUp(self):
        super().setUp()
        # Six uma banners ten days apart, the first ending in two days.
        self.banners = [
            make_uma_banner(
                make_timeline(
                    f"T{index}", global_start_date=days(10 * index - 5), global_end_date=days(10 * index + 2)
                ),
                name=f"Uma {index}",
            )
            for index in range(6)
        ]

    def member(self, **kwargs):
        """An account with early access, which Community plans needs."""
        user = self.person(**kwargs)
        user.early_access = True
        user.save()
        return user

    def plan(self, user, banners=None, notes=None, name="Main"):
        plan = plans.get_active_plan(user) if name == "Main" else Plan.objects.create(user=user, name=name)
        for index, banner in enumerate(banners if banners is not None else self.banners[:3]):
            UserPlannedBanner.objects.create(
                plan=plan, user=user, banner_uma=banner, number_of_pulls=200, note=(notes or {}).get(index, "")
            )
        return plan

    def feed(self, client=None, **params):  # pylint: disable=arguments-differ
        return (client or self.client_for(self.member())).get("/plan-gallery", params)

    def vote_at(self, post, count, when, value=VoteValue.UP):
        for _ in range(count):
            vote = GalleryVote.objects.create(post=post, user=self.person(), value=value)
            GalleryVote.objects.filter(pk=vote.pk).update(created_at=when)

    def publish(self, user, plan=None, **kwargs):
        fields = {"caption": "F2P road to the anniversary", "confirm_public_identity": True, **kwargs}
        post, _ = plan_gallery.publish_plan(user, plan if plan is not None else self.plan(user), **fields)
        return post


class SnapshotTests(PlanGalleryFixture):

    def test_rows_are_the_share_links_whitelist_and_nothing_about_the_author(self):
        post = self.publish(self.member())
        snapshot = post.plan_snapshot
        self.assertEqual(set(snapshot), {"rows", "ends"})
        self.assertEqual(len(snapshot["rows"]), 3)
        self.assertEqual(set(snapshot["rows"][0]), ROW_FIELDS - {"note"})
        self.assertEqual(len(snapshot["ends"]), 3)

    def test_notes_only_when_ticked_never_from_the_share_link(self):
        user = self.member()
        plan = self.plan(user, notes={0: "Stop at one copy"})
        plan.share_notes = plan.share_stats = plan.is_shared = True
        plan.save()
        post = self.publish(user, plan)
        self.assertNotIn("income", post.plan_snapshot)
        self.assertFalse(any("note" in row for row in post.plan_snapshot["rows"]))
        post = self.publish(user, plan, include_notes=True)
        self.assertEqual(post.plan_snapshot["rows"][0]["note"], "Stop at one copy")
        # Only a literal True counts.
        post = self.publish(user, plan, include_notes="yes", include_income=1)
        self.assertFalse(any("note" in row for row in post.plan_snapshot["rows"]))
        self.assertNotIn("income", post.plan_snapshot)

    def test_income_is_the_whitelist_and_every_stat_picks_a_side(self):
        self.assertEqual(set(GameStatsSerializer.Meta.fields), set(plan_gallery.POSTED_INCOME_FIELDS) | NEVER_POSTED)
        self.assertFalse(set(plan_gallery.POSTED_INCOME_FIELDS) & NEVER_POSTED)
        user = self.member()
        user.current_carat = 126500
        user.daily_carat = True
        user.save()
        post = self.publish(user, include_income=True)
        self.assertEqual(set(post.plan_snapshot["income"]), set(plan_gallery.POSTED_INCOME_FIELDS))
        self.assertTrue(post.plan_snapshot["income"]["daily_carat"])
        self.assertNotIn(126500, post.plan_snapshot["income"].values())

    def test_a_plan_with_its_own_resources_posts_that_blocks_income(self):
        user = self.member()
        plan = self.plan(user)
        plans.attach_income_profile(plan)
        plan.refresh_from_db()
        plan.income_profile.training_pass = True
        plan.income_profile.save()
        post = self.publish(user, plan, include_income=True)
        self.assertTrue(post.plan_snapshot["income"]["training_pass"])

    def test_income_is_frozen_at_posting(self):
        user = self.member()
        post = self.publish(user, include_income=True)
        user.daily_carat = not post.plan_snapshot["income"]["daily_carat"]
        user.save()
        post.refresh_from_db()
        self.assertNotEqual(post.plan_snapshot["income"]["daily_carat"], user.daily_carat)

    def test_a_note_as_long_as_the_calculator_allows_can_be_posted(self):
        """The posting limit is the Calculator's: a note you could save, you
        can post (both 1,000 since 2026-11-23)."""
        self.assertEqual(plan_gallery.PLAN_NOTE_MAX_LENGTH, NOTE_MAX_LENGTH)
        user = self.member()
        post = self.publish(user, self.plan(user, notes={0: "x" * NOTE_MAX_LENGTH}), include_notes=True)
        self.assertEqual(post.plan_snapshot["rows"][0]["note"], "x" * NOTE_MAX_LENGTH)

    def test_a_note_that_may_not_be_posted_names_its_banner(self):
        user = self.member()
        for note, words in (("see https://example.com", "Links"), ("x" * 1001, "1,000 characters")):
            with self.subTest(words=words):
                plan = self.plan(user, notes={1: note}, name=words)
                with self.assertRaises(gallery.GalleryRefused) as refused:
                    self.publish(user, plan, include_notes=True)
                self.assertIn("Uma 1", refused.exception.message)
                self.assertIn(words, refused.exception.message)
                self.assertEqual(refused.exception.field, "notes")
                # Unticked, the same plan posts.
                self.publish(user, plan)

    def test_nothing_to_post(self):
        user = self.member()
        with self.assertRaises(gallery.GalleryRefused):
            self.publish(user, self.plan(user, banners=[]))


class RangeTests(PlanGalleryFixture):

    def scenario(self, name, start):
        return make_scenario(name, banner_timeline=make_timeline(name, global_start_date=start, global_end_date=start))

    def test_the_newest_scenario_started_by_the_last_banner(self):
        self.scenario("Old", days(-100))
        middle = self.scenario("Middle", days(15))
        self.scenario("Later", days(200))
        user = self.member()
        # The plan's last banner (Uma 2) ends in 22 days.
        self.assertEqual(self.publish(user).plan_range, middle)

    def test_none_when_no_scenario_has_started_by_then(self):
        self.scenario("Later", days(200))
        self.assertIsNone(self.publish(self.member()).plan_range)

    def test_a_repost_works_it_out_again(self):
        user = self.member()
        plan = self.plan(user)
        self.assertIsNone(self.publish(user, plan).plan_range)
        later = self.scenario("New", days(1))
        self.assertEqual(self.publish(user, plan).plan_range, later)


class PublishTests(PlanGalleryFixture):

    def test_needs_early_access_the_agree_box_a_name_and_a_title(self):
        outsider = self.person()
        with self.assertRaises(gallery.GalleryRefused) as refused:
            self.publish(outsider)
        self.assertEqual(refused.exception.status, 403)
        user = self.member()
        for fields, field in (({"confirm_public_identity": False}, None), ({"caption": "  "}, "caption")):
            with self.subTest(fields=fields):
                with self.assertRaises(gallery.GalleryRefused) as refused:
                    self.publish(user, **fields)
                self.assertEqual(refused.exception.field, field)
        nameless = self.member()
        nameless.display_name = ""
        nameless.save()
        with self.assertRaises(gallery.GalleryRefused):
            self.publish(nameless)

    def test_the_title_and_words_go_through_the_filters(self):
        with self.assertRaises(gallery.GalleryRefused) as refused:
            self.publish(self.member(), caption="visit https://spam.example")
        self.assertEqual(refused.exception.message, gallery_moderation.LINKS_REFUSED)

    def test_the_three_picks(self):
        user = self.member()
        post = self.publish(user, spending="dolphin", sweat="no_life", strategies=["end", "front"])
        self.assertEqual(
            (post.plan_spending, post.plan_sweat, post.plan_strategies), ("dolphin", "no_life", "front,end")
        )
        for fields, field in (({"spending": "rich"}, "spending"), ({"sweat": "x"}, "sweat"),
                              ({"strategies": ["runaway"]}, "strategies"), ({"strategies": "front"}, "strategies")):
            with self.subTest(fields=fields):
                with self.assertRaises(gallery.GalleryRefused) as refused:
                    self.publish(user, **fields)
                self.assertEqual(refused.exception.field, field)

    def test_five_posts_at_once(self):
        user = self.member()
        for _ in range(plan_gallery.PLAN_POST_CAP):
            make_plan_post(user)
        with self.assertRaises(gallery.GalleryRefused) as refused:
            self.publish(user)
        self.assertIn("up to 5 plans posted", refused.exception.message)

    def test_a_repost_keeps_the_link_and_votes_and_marks_edits_only_when_the_plan_changed(self):
        user = self.member()
        plan = self.plan(user)
        post = self.publish(user, plan)
        gallery.vote(self.person(), post, VoteValue.UP)
        first_edit = post.content_updated_at
        again = self.publish(user, plan, caption="New words only")
        self.assertEqual((again.pk, again.public_id), (post.pk, post.public_id))
        self.assertEqual(again.content_updated_at, first_edit)
        self.assertEqual(again.votes.count(), 1)
        UserPlannedBanner.objects.create(plan=plan, user=user, banner_uma=self.banners[4], number_of_pulls=133)
        changed = self.publish(user, plan)
        self.assertGreater(changed.content_updated_at, first_edit)
        self.assertEqual(GalleryPost.objects.filter(author=user).count(), 1)

    def test_deleting_the_plan_leaves_the_post(self):
        user = self.member()
        plan = self.plan(user, name="Spare")
        post = self.publish(user, plan)
        plans.delete_plan(plan)
        post.refresh_from_db()
        self.assertIsNone(post.source_plan)

    def test_the_route(self):
        user = self.member()
        plan = self.plan(user)
        client = self.client_for(user)
        created = client.post(f"/plans/{plan.id}/publish",
                              {"caption": "Hi", "confirm_public_identity": True, "strategies": ["pace"]}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["kind"], "plan")
        self.assertEqual(created.data["strategies"], ["pace"])
        again = client.post(
            f"/plans/{plan.id}/publish", {"caption": "Hi", "confirm_public_identity": True}, format="json"
        )
        self.assertEqual(again.status_code, 200)
        other = self.member()
        self.assertEqual(self.client_for(other).post(f"/plans/{plan.id}/publish", {}, format="json").status_code, 404)

    def test_the_plan_list_says_which_plan_is_posted(self):
        user = self.member()
        plan = self.plan(user)
        post = self.publish(user, plan)
        listed = {row["id"]: row for row in self.client_for(user).get("/plans").data}
        self.assertEqual(listed[plan.id]["post_public_id"], post.public_id)


class FeedTests(PlanGalleryFixture):

    def test_early_access_first(self):
        self.assertEqual(APIClient().get("/plan-gallery").status_code, 403)
        self.assertEqual(self.client_for(self.person()).get("/plan-gallery").status_code, 403)
        self.assertEqual(self.feed().status_code, 200)

    def test_a_row_carries_the_plan_and_nothing_about_the_author(self):
        user = self.member(display_name="CaratSaver")
        user.current_carat = 126500
        user.save()
        self.publish(user, spending="f2p", include_income=True, include_notes=True)
        response = self.feed()
        self.assertEqual(set(response.data), {"count", "sort", "window", "limit", "has_more", "results", "filters"})
        row = response.data["results"][0]
        self.assertEqual(row["kind"], "plan")
        self.assertEqual(row["plan"]["row_count"], 3)
        self.assertEqual(row["plan"]["pull_count"], 600)
        self.assertTrue(row["plan"]["has_income"])
        self.assertEqual(len(row["plan"]["lead"]), 3)
        self.assertEqual(row["spending"], "f2p")
        # The list's Copies column: a plan row carries the count.
        self.assertEqual(row["stats"]["copies"], 0)
        self.assertNotIn("rows", row)
        self.assertNotIn("income", row)
        self.assertFalse(all_keys(response.data) & (FORBIDDEN | NEVER_POSTED | {"source_plan", "plan_snapshot"}))

    def test_the_open_post_adds_rows_and_income_never_a_balance(self):
        user = self.member()
        user.current_carat = 126500
        user.save()
        post = self.publish(user, include_income=True)
        response = self.client_for(self.member()).get(f"/gallery/posts/{post.public_id}")
        self.assertEqual(len(response.data["rows"]), 3)
        self.assertEqual(set(response.data["income"]), set(plan_gallery.POSTED_INCOME_FIELDS))
        self.assertFalse(all_keys(response.data) & NEVER_POSTED)
        self.assertNotIn("126500", response.content.decode())

    def test_lead_banners_are_the_ones_still_ahead(self):
        user = self.member()
        ended = make_uma_banner(
            make_timeline("Gone", global_start_date=days(-20), global_end_date=days(-10)), name="Gone"
        )
        self.publish(user, self.plan(user, banners=[ended, *self.banners[:5]]))
        lead = self.feed().data["results"][0]["plan"]["lead"]
        self.assertEqual([row["banner_uma"] for row in lead], [banner.id for banner in self.banners[:4]])

    def test_top_by_window_and_new(self):
        a, b, c = (self.publish(self.member(), caption=name) for name in "ABC")
        for post, age in ((a, 40), (b, 10), (c, 0)):
            GalleryPost.objects.filter(pk=post.pk).update(published_at=days(-age))
        self.vote_at(a, 3, days(-20))
        self.vote_at(b, 2, days(-3))
        self.vote_at(c, 1, days(0) - datetime.timedelta(hours=2))

        def order(**params):
            return [row["caption"] for row in self.feed(**params).data["results"]]

        self.assertEqual(order(sort="top", window="day"), ["C", "B", "A"])
        self.assertEqual(order(sort="top", window="week"), ["B", "C", "A"])
        self.assertEqual(order(sort="top", window="month"), ["A", "B", "C"])
        self.assertEqual(order(sort="top", window="all"), ["A", "B", "C"])
        self.assertEqual(order(sort="new"), ["C", "B", "A"])
        # Unknown values fall back, as the gallery's do.
        self.assertEqual(self.feed(sort="hot", window="year").data["window"], "week")

    def test_downvotes_count_and_the_score_never_reads_below_zero(self):
        post = self.publish(self.member())
        self.vote_at(post, 2, days(0), VoteValue.DOWN)
        self.assertEqual(self.feed().data["results"][0]["stats"]["score"], 0)

    def test_filters(self):
        early = make_scenario("Early", banner_timeline=make_timeline("E", global_start_date=days(-100)))
        late = make_scenario("Late", banner_timeline=make_timeline("L", global_start_date=days(30)))
        short = self.publish(self.member(), caption="Short", spending="f2p", sweat="casual", strategies=["front"])
        user = self.member()
        long_plan = self.publish(user, self.plan(user, banners=self.banners), caption="Long", spending="whale",
                                 strategies=["late", "end"])
        self.assertEqual((short.plan_range, long_plan.plan_range), (early, late))

        def captions(**params):
            return [row["caption"] for row in self.feed(sort="new", **params).data["results"]]

        self.assertEqual(captions(spending="f2p"), ["Short"])
        self.assertEqual(captions(sweat="casual"), ["Short"])
        self.assertEqual(captions(sweat="comp"), [])
        self.assertEqual(captions(strategy="end,front"), ["Long", "Short"])
        self.assertEqual(captions(strategy="pace"), [])
        self.assertEqual(captions(range=early.id), ["Short"])
        self.assertEqual(captions(range=late.id), ["Long", "Short"])
        self.assertEqual(captions(spending="whale", strategy="end", range=late.id), ["Long"])
        # An unknown value is no filter at all.
        self.assertEqual(captions(spending="rich", strategy="runaway"), ["Long", "Short"])
        filters = self.feed().data["filters"]
        self.assertEqual([choice["label"] for choice in filters["ranges"]], ["Early", "Late"])
        self.assertEqual(
            filters["spending"][1], {"value": "minnow", "label": "Minnow", "meaning": "Daily Carat Pack only"}
        )
        self.assertEqual([choice["value"] for choice in filters["strategies"]], ["front", "pace", "late", "end"])


class RememberedOrderTests(PlanGalleryFixture):

    def test_ranked_once_a_minute_and_again_after_a_post(self):
        self.publish(self.member())
        with mock.patch.object(plan_gallery, "_rank", wraps=plan_gallery._rank) as rank:  # pylint: disable=protected-access
            self.feed()
            self.feed(spending="f2p")
            self.assertEqual(rank.call_count, 1)
            self.publish(self.member())
            self.assertEqual(self.feed().data["count"], 2)
            self.assertEqual(rank.call_count, 2)

    def test_a_post_gone_since_the_order_was_made_drops_out_at_once(self):
        hidden, blocked, erased, kept = (self.publish(self.member(), caption=name)
                                         for name in ("Hidden", "Blocked", "Erased", "Kept"))
        self.assertEqual(self.feed().data["count"], 4)
        gallery_moderation.remove(hidden, None)
        blocked.author.publishing_blocked = True
        blocked.author.save()
        gallery.erase_users([erased.author])
        self.assertEqual([row["caption"] for row in self.feed().data["results"]], [kept.caption])


class CopyTests(PlanGalleryFixture):

    def setUp(self):
        super().setUp()
        self.author = self.member()
        self.post_row = self.publish(self.author, self.plan(self.author, notes={0: "Stop at one"}),
                                     include_notes=True, include_income=True)
        self.url = f"/plan-gallery/posts/{self.post_row.public_id}/copy"

    def test_an_account_copy_is_a_plain_plan_with_the_posted_rows(self):
        copier = self.member()
        response = self.client_for(copier).post(self.url, {"name": "Borrowed"}, format="json")
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(id=response.data["plan"]["id"])
        self.assertEqual(plan.user, copier)
        self.assertFalse(plan.is_active)
        self.assertIsNone(plan.income_profile)
        rows = list(plan.banners.order_by("id"))
        self.assertEqual([row.banner_uma_id for row in rows], [banner.id for banner in self.banners[:3]])
        self.assertEqual(rows[0].note, "Stop at one")
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.copy_count, 1)

    def test_the_authors_own_copy_is_not_counted(self):
        self.client_for(self.author).post(self.url, {"name": "Again"}, format="json")
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.copy_count, 0)

    def test_the_plan_cap(self):
        copier = self.member()
        for index in range(PLAN_CAP):
            Plan.objects.create(user=copier, name=f"P{index}")
        response = self.client_for(copier).post(self.url, {"name": "One more"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"error": "You can keep up to 5 plans."})

    def test_a_guest_and_an_outsider_are_refused_and_a_get_makes_nothing(self):
        self.assertEqual(APIClient().post(self.url).status_code, 403)
        self.assertEqual(self.client_for(self.person()).post(self.url, {"name": "x"}, format="json").status_code, 403)
        self.assertEqual(self.client_for(self.member()).get(self.url).status_code, 405)
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.copy_count, 0)

    def test_a_guest_copy_once_open_is_the_rows_and_never_the_income(self):
        with mock.patch.object(plan_gallery, "can_use", return_value=True):
            response = APIClient().post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["user_planned_banner_data"]), 3)
        self.assertIsNone(response.data["user_stats_data"])

    def test_a_team_post_is_not_copied_here(self):
        team_post = self.post(self.person())
        self.assertEqual(
            self.client_for(self.member()).post(f"/plan-gallery/posts/{team_post.public_id}/copy").status_code, 404
        )


class PlanPostAroundTheSiteTests(PlanGalleryFixture):

    def test_the_moderation_card_lists_posted_notes_never_income(self):
        user = self.member()
        plan = self.plan(user, notes={0: "Stop at one copy"})
        post = self.publish(user, plan, include_notes=True, include_income=True)
        text = moderation_cards.item_text(post)
        self.assertIn("Notes: Stop at one copy", text)
        self.assertNotIn("daily_carat", text)

    def test_the_link_preview_draws_the_first_banners(self):
        post = self.publish(self.member())
        page = APIClient().get(f"/post/{post.public_id}").content.decode()
        self.assertIn("Community plan: 600 pulls on 3 banners", page)
        self.assertIn("Uma 0, Uma 1 and Uma 2", page)
        self.assertNotIn(post.caption, page)


class DemoCommandTests(PlanGalleryFixture):

    def test_posts_the_cast_and_replaces_itself(self):
        for index in range(4):
            make_support_banner(make_timeline(f"S{index}", global_start_date=days(3 * index),
                                              global_end_date=days(3 * index + 5)), name=f"Card {index}")
        def demo():
            # A fresh queryset each time: an iterated one keeps its rows.
            return GalleryPost.objects.filter(author__username__startswith="demoplan_")

        call_command("seed_plan_gallery_demo", stdout=StringIO())
        self.assertEqual(demo().count(), 10)
        self.assertTrue(all(post.is_plan for post in demo()))
        call_command("seed_plan_gallery_demo", stdout=StringIO())
        self.assertEqual(demo().count(), 10)
        call_command("seed_plan_gallery_demo", "--clear", stdout=StringIO())
        self.assertEqual(demo().count(), 0)
