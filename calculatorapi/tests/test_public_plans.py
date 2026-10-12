"""Share links: Plan.public_id, the sharing flags, and the two public routes.

A shared plan is copied, never viewed. These tests pin what may leave an
account through a share link and when, because a field that slips into one of
these payloads reaches anyone holding the link.
"""

# A setUp that builds a scenario keeps one attribute per object under test.
# pylint: disable=too-many-instance-attributes

import datetime
from unittest.mock import patch

from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi import plans
from calculatorapi.models import (
    AnniversaryEventProduct,
    DEFAULT_PLAN_NAME,
    IncomeProfile,
    PLAN_CAP,
    Plan,
    UserPlannedBanner,
    UserPlannedPurchase,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client,
    make_anniversary_event,
    make_support_banner,
    make_timeline,
    make_uma_banner,
    make_user,
)

PUBLIC_ID = r"^[A-Za-z0-9]{8}$"
SUMMARY_KEYS = {
    "public_id", "updated_at", "copy_count", "share_stats", "share_notes",
    "row_count", "rows",
}


def _row(plan, pulls=10, *, banner=None, note=""):
    """A planned uma-banner row in `plan`, carrying the transitional `user` too."""
    return UserPlannedBanner.objects.create(
        user=plan.user, plan=plan, banner_uma=banner or make_uma_banner(),
        number_of_pulls=pulls, reserved_copies=1, note=note,
    )


def _purchase(owner, quantity=2, income_profile=None):
    """One planned pack purchase in `owner`'s account (or one of its profiles)."""
    product = AnniversaryEventProduct.objects.create(
        anniversary_event=make_anniversary_event(), name="Pack",
        product_type="carat_pack", usd_cost=10, paid_carat_amount=1500,
    )
    return UserPlannedPurchase.objects.create(
        user=owner, income_profile=income_profile, product=product, quantity=quantity,
    )


def _share(plan, **flags):
    """Switch sharing on, plus whichever share options the test names."""
    for key, value in {"is_shared": True, **flags}.items():
        setattr(plan, key, value)
    plan.save()
    return plan


class PublicIdTests(CalculatorTestCase):
    """Every plan has its own share id from the moment it exists."""

    def setUp(self):
        self.user = make_user()

    def test_the_first_plan_gets_a_public_id(self):
        plan = plans.get_active_plan(self.user)

        self.assertRegex(plan.public_id, PUBLIC_ID)

    def test_created_plans_have_unique_public_ids(self):
        first = plans.get_active_plan(self.user)
        second = plans.create_plan(self.user, "Second")
        copy = plans.copy_plan(first, owner=self.user, name="Copy")

        self.assertEqual(len({first.public_id, second.public_id, copy.public_id}), 3)

    def test_public_id_retries_a_collision(self):
        with patch(
            "calculatorapi.plans.generate_public_id",
            side_effect=["AAAAAAAA", "AAAAAAAA", "BBBBBBBB"],
        ):
            first = plans.get_active_plan(self.user)
            second = plans.create_plan(self.user, "Second")

        self.assertEqual(first.public_id, "AAAAAAAA")
        self.assertEqual(second.public_id, "BBBBBBBB")

    def test_a_copy_starts_private_whatever_its_source(self):
        source = _share(plans.get_active_plan(self.user),
                        share_stats=True, share_notes=True)
        Plan.objects.filter(pk=source.pk).update(copy_count=9)
        source.refresh_from_db()

        copy = plans.copy_plan(source, owner=make_user(username="other"), name="Copy")

        self.assertNotEqual(copy.public_id, source.public_id)
        self.assertFalse(copy.is_shared)
        self.assertFalse(copy.share_stats)
        self.assertFalse(copy.share_notes)
        self.assertEqual(copy.copy_count, 0)

    def test_reset_changes_the_id_and_nothing_else(self):
        plan = _share(plans.get_active_plan(self.user), share_notes=True)
        Plan.objects.filter(pk=plan.pk).update(copy_count=3)
        plan.refresh_from_db()
        old_id = plan.public_id

        plans.reset_public_id(plan)
        plan.refresh_from_db()

        self.assertNotEqual(plan.public_id, old_id)
        self.assertRegex(plan.public_id, PUBLIC_ID)
        self.assertTrue(plan.is_shared)
        self.assertTrue(plan.share_notes)
        self.assertEqual(plan.copy_count, 3)


class SummaryRouteTests(CalculatorTestCase):
    """GET /plans/public/<id>: what the landing card shows, and nothing more."""

    def setUp(self):
        self.sharer = make_user(username="sharer")
        self.plan = plans.get_active_plan(self.sharer)
        self.guest = APIClient()

    def _get(self, public_id=None):
        return self.guest.get(f"/plans/public/{public_id or self.plan.public_id}")

    def test_unknown_public_id_is_not_found(self):
        self.assertEqual(self._get("unknown1").status_code, 404)

    def test_a_plan_that_is_not_shared_looks_exactly_like_no_plan(self):
        """A probe must not be able to tell a private plan's id from a made-up
        one, or the id space could be walked for plans that exist."""
        private = self._get()
        unknown = self._get("unknown1")

        self.assertEqual(private.status_code, 404)
        self.assertEqual(private.json(), unknown.json())

    def test_turning_sharing_off_takes_the_link_down(self):
        _share(self.plan)
        self.assertEqual(self._get().status_code, 200)

        self.plan.is_shared = False
        self.plan.save()

        self.assertEqual(self._get().status_code, 404)

    def test_the_summary_carries_ids_and_counts_only(self):
        uma_banner = make_uma_banner()
        support_banner = make_support_banner()
        _row(self.plan, pulls=42, banner=uma_banner, note="my secret")
        UserPlannedBanner.objects.create(
            user=self.sharer, plan=self.plan, banner_support=support_banner,
            number_of_pulls=7, reserved_copies=0,
        )
        _share(self.plan, share_stats=True, share_notes=True)

        res = self._get()

        self.assertEqual(res.status_code, 200)
        # The whitelist, both ways: nothing about the owner, no plan id, no
        # name, no pull counts, no notes, no stats, even with both options on.
        self.assertEqual(set(res.data), SUMMARY_KEYS)
        self.assertEqual(res.data["public_id"], self.plan.public_id)
        self.assertEqual(res.data["row_count"], 2)
        self.assertCountEqual(
            res.data["rows"],
            [
                {"banner_uma": uma_banner.id, "banner_support": None, "banner_step_up": None},
                {"banner_uma": None, "banner_support": support_banner.id, "banner_step_up": None},
            ],
        )
        self.assertTrue(res.data["share_stats"])
        self.assertTrue(res.data["share_notes"])
        self.assertEqual(res.data["copy_count"], 0)
        self.assertNotIn("my secret", str(res.content))
        self.assertNotIn(DEFAULT_PLAN_NAME, str(res.content))

    def test_rows_come_in_the_order_the_owner_sees_them(self):
        now = timezone.now()
        later = make_uma_banner(make_timeline(
            name="Later", global_start_date=now + datetime.timedelta(days=40),
            global_end_date=now + datetime.timedelta(days=50),
        ))
        sooner = make_uma_banner(make_timeline(
            name="Sooner", global_start_date=now + datetime.timedelta(days=10),
            global_end_date=now + datetime.timedelta(days=20),
        ))
        _row(self.plan, banner=later)  # added first, dated last
        _row(self.plan, banner=sooner)
        _share(self.plan)

        rows = self._get().data["rows"]

        self.assertEqual([r["banner_uma"] for r in rows], [sooner.id, later.id])

    def test_reading_the_summary_changes_nothing(self):
        """Opening a link must be harmless: link previews and crawlers fetch it."""
        _share(self.plan)
        before = Plan.objects.count()

        self._get()
        self._get()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 0)
        self.assertEqual(Plan.objects.count(), before)

    def test_the_summary_cannot_be_written_to(self):
        _share(self.plan)
        url = f"/plans/public/{self.plan.public_id}"

        self.assertEqual(self.guest.patch(url, {"name": "X"}, format="json").status_code, 405)
        self.assertEqual(self.guest.delete(url).status_code, 405)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.name, DEFAULT_PLAN_NAME)


class GuestCopyTests(CalculatorTestCase):
    """POST /plans/public/<id>/copy with no account: the rows go to the device."""

    def setUp(self):
        self.sharer = make_user(username="sharer")
        self.sharer.current_carat = 4321
        self.sharer.current_paid_carat = 765
        self.sharer.uma_ticket = 12
        self.sharer.daily_carat = True
        self.sharer.save()
        self.plan = plans.get_active_plan(self.sharer)
        self.row = _row(self.plan, pulls=42, note="my secret")
        self.guest = APIClient()

    def _copy(self, public_id=None, **body):
        return self.guest.post(
            f"/plans/public/{public_id or self.plan.public_id}/copy", body, format="json"
        )

    def test_not_shared_is_a_404_and_is_not_counted(self):
        res = self._copy()

        self.assertEqual(res.status_code, 404)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 0)

    def test_the_rows_arrive_in_the_device_store_shape_without_owner_ids(self):
        _share(self.plan)

        res = self._copy()

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            set(res.data),
            {"user_planned_banner_data", "user_stats_data", "user_planned_purchase_data"},
        )
        [row] = res.data["user_planned_banner_data"]
        # No row id, no owner, no plan, and no note while share_notes is off.
        self.assertEqual(
            set(row),
            {"number_of_pulls", "reserved_copies", "primary_card", "second_card",
             "primary_target", "banner_uma", "banner_support", "banner_step_up"},
        )
        self.assertEqual(row["number_of_pulls"], 42)
        self.assertEqual(row["banner_uma"], self.row.banner_uma_id)
        # Ids only, never a nested banner.
        self.assertIsInstance(row["banner_uma"], int)
        self.assertIsNone(res.data["user_stats_data"])
        self.assertIsNone(res.data["user_planned_purchase_data"])

    def test_notes_travel_only_when_the_sharer_shares_them(self):
        _share(self.plan, share_notes=True)

        [row] = self._copy().data["user_planned_banner_data"]

        self.assertEqual(row["note"], "my secret")

    def test_shared_stats_travel_only_when_the_guest_asks(self):
        """A guest's device has one set of stats, so taking the owner's
        replaces their own. The owner sharing them is not the guest asking."""
        _purchase(self.sharer)
        _share(self.plan, share_stats=True)

        for res in (self._copy(), self._copy(include_stats=False)):
            self.assertIsNone(res.data["user_stats_data"])
            self.assertIsNone(res.data["user_planned_purchase_data"])

    def test_asking_for_stats_the_sharer_kept_gets_none(self):
        _share(self.plan)

        self.assertIsNone(self._copy(include_stats=True).data["user_stats_data"])

    def test_a_bad_include_stats_is_refused_and_not_counted(self):
        _share(self.plan, share_stats=True)

        res = self._copy(include_stats="yes")

        self.assertEqual(res.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 0)

    def test_resources_travel_when_shared_and_asked_for(self):
        purchase = _purchase(self.sharer, quantity=3)
        _share(self.plan, share_stats=True)

        res = self._copy(include_stats=True)

        # The purchases come with the stats they fund, in the device store's
        # shape: catalogue ids and a count, nothing pointing at the owner.
        self.assertEqual(
            res.data["user_planned_purchase_data"],
            [{"product": purchase.product_id, "quantity": 3,
              "target_uma": None, "target_support": None}],
        )
        stats = res.data["user_stats_data"]

        self.assertEqual(stats["current_carat"], 4321)
        self.assertEqual(stats["current_paid_carat"], 765)
        self.assertEqual(stats["uma_ticket"], 12)
        self.assertTrue(stats["daily_carat"])
        # The stats block and nothing else: no id, no username, no user.
        self.assertNotIn("id", stats)
        self.assertNotIn("username", stats)
        self.assertNotIn("user", stats)

    def test_shared_resources_come_from_the_plans_own_income_profile(self):
        _purchase(self.sharer, quantity=9)  # the account's, not this plan's
        plans.attach_income_profile(self.plan)
        self.plan.refresh_from_db()
        profile = self.plan.income_profile
        profile.current_carat = 2345
        profile.save()
        UserPlannedPurchase.objects.filter(income_profile=profile).delete()
        _purchase(self.sharer, quantity=4, income_profile=profile)
        _share(self.plan, share_stats=True)

        res = self._copy(include_stats=True)

        self.assertEqual(res.data["user_stats_data"]["current_carat"], 2345)
        self.assertEqual(
            [row["quantity"] for row in res.data["user_planned_purchase_data"]], [4]
        )

    def test_a_guest_copy_is_counted_without_touching_the_plan(self):
        _share(self.plan)
        # Pin updated_at in the past so a bump would be visible.
        Plan.objects.filter(pk=self.plan.pk).update(
            updated_at=timezone.now() - datetime.timedelta(days=3)
        )
        self.plan.refresh_from_db()
        updated_at = self.plan.updated_at

        self._copy()
        self._copy()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 2)
        # Being copied is not an edit: the card's "Updated" date stays put.
        self.assertEqual(self.plan.updated_at, updated_at)
        self.assertEqual(Plan.objects.count(), 1)


class AccountCopyTests(CalculatorTestCase):
    """POST /plans/public/<id>/copy signed in: a new plan in the caller's account."""

    def setUp(self):
        self.sharer = make_user(username="sharer")
        self.sharer.current_carat = 4321
        self.sharer.save()
        self.plan = plans.get_active_plan(self.sharer)
        self.row = _row(self.plan, pulls=42, note="my secret")
        event = make_anniversary_event()
        product = AnniversaryEventProduct.objects.create(
            anniversary_event=event, name="Pack", product_type="carat_pack",
            usd_cost=10, paid_carat_amount=1500,
        )
        UserPlannedPurchase.objects.create(user=self.sharer, product=product, quantity=2)

        self.copier = make_user(username="copier")
        self.copier.current_carat = 100
        self.copier.save()
        self.client, _ = auth_client(self.copier)

    def _copy(self, name="Their plan", client=None):
        return (client or self.client).post(
            f"/plans/public/{self.plan.public_id}/copy", {"name": name}, format="json"
        )

    def _copied_plan(self, res):
        return Plan.objects.get(id=res.data["plan"]["id"])

    def test_not_shared_is_a_404(self):
        self.assertEqual(self._copy().status_code, 404)
        self.assertEqual(Plan.objects.filter(user=self.copier).count(), 0)

    def test_the_copy_lands_in_the_callers_account_inactive_and_private(self):
        _share(self.plan, share_stats=True, share_notes=True)

        res = self._copy()

        self.assertEqual(res.status_code, 201)
        copy = self._copied_plan(res)
        self.assertEqual(copy.user, self.copier)
        self.assertEqual(copy.name, "Their plan")
        # Inactive like any new plan: the client switches to it. The copier's
        # Main plan was created first, so the copy is never their only plan.
        self.assertFalse(copy.is_active)
        self.assertTrue(Plan.objects.filter(user=self.copier, is_active=True).exists())
        self.assertFalse(copy.is_shared)
        self.assertEqual(copy.copy_count, 0)
        self.assertNotEqual(copy.public_id, self.plan.public_id)
        # Shaped like POST /plans, so the client stores it with the same code.
        self.assertEqual(
            set(res.data),
            {"plan", "user_stats_data", "user_planned_banner_data", "user_planned_purchase_data"},
        )
        [row] = res.data["user_planned_banner_data"]
        self.assertEqual(row["number_of_pulls"], 42)
        self.assertEqual(row["user"], self.copier.id)

    def test_the_source_is_counted_and_never_changed(self):
        _share(self.plan)

        self._copy()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 1)
        self.assertEqual(self.plan.user, self.sharer)
        self.assertEqual(self.plan.banners.count(), 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.number_of_pulls, 42)

    def test_notes_are_blanked_unless_the_sharer_shares_them(self):
        _share(self.plan)
        copy = self._copied_plan(self._copy())
        self.assertEqual(copy.banners.get().note, "")

        _share(self.plan, share_notes=True)
        copy = self._copied_plan(self._copy(name="Again"))
        self.assertEqual(copy.banners.get().note, "my secret")

    def test_without_shared_stats_the_copy_reads_the_copiers_own(self):
        _share(self.plan)

        res = self._copy()

        self.assertIsNone(self._copied_plan(res).income_profile)
        self.assertEqual(res.data["user_stats_data"]["current_carat"], 100)
        # The copier's own (empty) purchases, not the sharer's.
        self.assertEqual(res.data["user_planned_purchase_data"], [])

    def test_shared_resources_seed_a_profile_the_copier_owns(self):
        _share(self.plan, share_stats=True)

        res = self._copy()

        copy = self._copied_plan(res)
        profile = copy.income_profile
        self.assertIsNotNone(profile)
        self.assertEqual(profile.user, self.copier)
        self.assertEqual(profile.current_carat, 4321)
        self.assertEqual(res.data["user_stats_data"]["current_carat"], 4321)
        # The sharer's purchases come with their stats, as the copier's own rows.
        [copied] = UserPlannedPurchase.objects.filter(income_profile=profile)
        self.assertEqual(copied.user, self.copier)
        self.assertEqual(copied.quantity, 2)
        self.assertEqual(
            [row["quantity"] for row in res.data["user_planned_purchase_data"]], [2]
        )
        # The copier's account-level purchases are untouched (still none), and
        # so are the sharer's: one row, still on their account.
        self.assertFalse(
            UserPlannedPurchase.objects.filter(user=self.copier, income_profile=None).exists()
        )
        self.assertEqual(UserPlannedPurchase.objects.filter(user=self.sharer).count(), 1)
        # And the sharer's own block is untouched: not pointed at, not moved.
        self.assertFalse(IncomeProfile.objects.filter(user=self.sharer).exists())
        self.copier.refresh_from_db()
        self.assertEqual(self.copier.current_carat, 100)

    def test_the_cap_refuses_the_copy_and_it_is_not_counted(self):
        _share(self.plan)
        plans.get_active_plan(self.copier)
        for i in range(PLAN_CAP - 1):
            plans.create_plan(self.copier, f"Plan {i}")

        res = self._copy()

        self.assertEqual(res.status_code, 400)
        self.assertIn(str(PLAN_CAP), res.data["error"])
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 0)

    def test_the_name_is_validated_like_a_new_plan(self):
        _share(self.plan)

        self.assertEqual(self._copy(name="   ").status_code, 400)
        res = self._copy(name="  My   copy ")
        self.assertEqual(self._copied_plan(res).name, "My copy")

    def test_the_owner_copying_their_own_link_gets_a_duplicate_and_no_count(self):
        _share(self.plan)
        owner_client, _ = auth_client(self.sharer)

        res = self._copy(name="Dupe", client=owner_client)

        self.assertEqual(res.status_code, 201)
        copy = self._copied_plan(res)
        self.assertEqual(copy.user, self.sharer)
        # A Duplicate keeps the owner's own notes, as it always has.
        self.assertEqual(copy.banners.get().note, "my secret")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 0)


class PatchSharingTests(CalculatorTestCase):
    """PATCH /plans/<id>: the owner's share face."""

    def setUp(self):
        self.user = make_user()
        self.plan = plans.get_active_plan(self.user)
        self.client, _ = auth_client(self.user)
        self.url = f"/plans/{self.plan.id}"

    def test_the_flags_are_off_by_default_and_on_the_plan_list(self):
        [listed] = self.client.get("/plans").data

        self.assertEqual(listed["public_id"], self.plan.public_id)
        self.assertFalse(listed["is_shared"])
        self.assertFalse(listed["share_stats"])
        self.assertFalse(listed["share_notes"])
        self.assertEqual(listed["copy_count"], 0)

    def test_the_owner_sets_the_flags(self):
        res = self.client.patch(
            self.url, {"is_shared": True, "share_notes": True}, format="json"
        )

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_shared"])
        self.assertTrue(res.data["share_notes"])
        self.assertFalse(res.data["share_stats"])
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.is_shared)

    def test_the_id_and_the_count_cannot_be_written(self):
        old_id = self.plan.public_id

        self.client.patch(
            self.url, {"public_id": "AAAAAAAA", "copy_count": 999}, format="json"
        )

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.public_id, old_id)
        self.assertEqual(self.plan.copy_count, 0)

    def test_reset_link_kills_the_old_one(self):
        _share(self.plan)
        old_id = self.plan.public_id

        res = self.client.patch(self.url, {"regenerate_public_id": True}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res.data["public_id"], old_id)
        self.assertEqual(APIClient().get(f"/plans/public/{old_id}").status_code, 404)
        self.assertEqual(
            APIClient().get(f"/plans/public/{res.data['public_id']}").status_code, 200
        )

    def test_a_bad_reset_value_writes_nothing(self):
        old_id = self.plan.public_id

        res = self.client.patch(
            self.url, {"name": "Renamed", "regenerate_public_id": "yes"}, format="json"
        )

        self.assertEqual(res.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.public_id, old_id)
        self.assertEqual(self.plan.name, DEFAULT_PLAN_NAME)

    def test_a_new_plan_is_never_born_shared(self):
        res = self.client.post("/plans", {"name": "New", "is_shared": True}, format="json")

        self.assertEqual(res.status_code, 201)
        self.assertFalse(Plan.objects.get(id=res.data["plan"]["id"]).is_shared)

    def test_someone_else_cannot_share_my_plan(self):
        other_client, _ = auth_client(make_user(username="other"))

        res = other_client.patch(self.url, {"is_shared": True}, format="json")

        self.assertEqual(res.status_code, 404)
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_shared)


class ThrottleTests(CalculatorTestCase):
    """Both public routes are rate-limited. Asserted because a throttle that
    silently does not throttle looks exactly like one that does. Against an
    unknown id, so each request is cheap: the throttle runs before the view."""

    def test_both_scopes_have_a_rate(self):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertEqual(rates["public_plan"], "120/hour")
        self.assertEqual(rates["public_plan_copy"], "30/hour")

    def test_the_summary_is_throttled(self):
        client = APIClient()
        for _ in range(120):
            client.get("/plans/public/unknown1")
        self.assertEqual(client.get("/plans/public/unknown1").status_code, 429)

    def test_the_copy_is_throttled_for_guests_and_accounts(self):
        guest = APIClient()
        signed_in, _ = auth_client(make_user())
        for client in (guest, signed_in):
            for _ in range(30):
                client.post("/plans/public/unknown1/copy")
            self.assertEqual(
                client.post("/plans/public/unknown1/copy").status_code, 429
            )
