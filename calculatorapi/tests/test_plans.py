"""Pull plans: the /plans routes, plan-scoped saving, copying, and the 0067 backfill."""

# A setUp that builds a scenario keeps one attribute per object under test.
# pylint: disable=too-many-instance-attributes

from importlib import import_module

from django.apps import apps
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from calculatorapi import plans
from calculatorapi.models import (
    DEFAULT_PLAN_NAME,
    PLAN_CAP,
    Plan,
    UserPlannedBanner,
    UserPlannedPurchase,
    AnniversaryEventProduct,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client,
    make_anniversary_event,
    make_support_banner,
    make_uma_banner,
    make_user,
)


def _row(plan, banner, pulls=10, reserved=0):
    """A planned uma-banner row in `plan`, carrying the transitional `user` too."""
    return UserPlannedBanner.objects.create(
        user=plan.user, plan=plan, banner_uma=banner,
        number_of_pulls=pulls, reserved_copies=reserved,
    )


class ActivePlanTests(CalculatorTestCase):
    """plans.get_active_plan: every account has one plan to open on."""

    def setUp(self):
        self.user = make_user()

    def test_first_call_creates_the_main_plan(self):
        self.assertFalse(Plan.objects.filter(user=self.user).exists())

        plan = plans.get_active_plan(self.user)

        self.assertEqual(plan.name, DEFAULT_PLAN_NAME)
        self.assertTrue(plan.is_active)
        # And a second call finds it rather than making another.
        self.assertEqual(plans.get_active_plan(self.user).id, plan.id)
        self.assertEqual(Plan.objects.filter(user=self.user).count(), 1)

    def test_plans_with_none_active_promotes_the_oldest(self):
        oldest = Plan.objects.create(user=self.user, name="First")
        Plan.objects.create(user=self.user, name="Second")

        plan = plans.get_active_plan(self.user)

        self.assertEqual(plan.id, oldest.id)
        oldest.refresh_from_db()
        self.assertTrue(oldest.is_active)

    def test_a_second_active_plan_is_refused_by_the_database(self):
        Plan.objects.create(user=self.user, name="A", is_active=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Plan.objects.create(user=self.user, name="B", is_active=True)

    def test_two_users_may_each_have_an_active_plan(self):
        other = make_user(username="other")
        Plan.objects.create(user=self.user, name="A", is_active=True)
        Plan.objects.create(user=other, name="A", is_active=True)  # no raise

    def test_rows_written_without_a_plan_are_adopted(self):
        """The deploy-window case: old code saved a row with a user and no plan."""
        banner = make_uma_banner()
        orphan = UserPlannedBanner.objects.create(
            user=self.user, banner_uma=banner, number_of_pulls=7,
        )

        plan = plans.get_active_plan(self.user)

        orphan.refresh_from_db()
        self.assertEqual(orphan.plan_id, plan.id)

    def test_set_active_moves_the_flag(self):
        first = plans.get_active_plan(self.user)
        second = Plan.objects.create(user=self.user, name="Second")

        plans.set_active_plan(second)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)


class CopyPlanTests(CalculatorTestCase):
    """plans.copy_plan: what publishing and taking a plan will both be built on."""

    def setUp(self):
        self.user = make_user()
        self.source = plans.get_active_plan(self.user)
        self.uma_banner = make_uma_banner()
        self.support_banner = make_support_banner()
        _row(self.source, self.uma_banner, pulls=120, reserved=1)
        UserPlannedBanner.objects.create(
            user=self.user, plan=self.source,
            banner_support=self.support_banner, number_of_pulls=60,
        )

    def test_copy_carries_every_row_and_leaves_the_source_alone(self):
        copy = plans.copy_plan(self.source, owner=self.user, name="Copy")

        self.assertNotEqual(copy.id, self.source.id)
        self.assertFalse(copy.is_active)
        self.assertEqual(
            sorted(copy.banners.values_list(
                "banner_uma_id", "banner_support_id",
                "number_of_pulls", "reserved_copies",
            ), key=str),
            sorted(self.source.banners.values_list(
                "banner_uma_id", "banner_support_id",
                "number_of_pulls", "reserved_copies",
            ), key=str),
        )
        # New rows, not the same rows moved.
        self.assertEqual(self.source.banners.count(), 2)
        self.assertFalse(
            set(copy.banners.values_list("id", flat=True))
            & set(self.source.banners.values_list("id", flat=True))
        )

    def test_copy_into_another_account_belongs_to_that_account(self):
        """The shape "take a published plan" will have."""
        taker = make_user(username="taker")

        copy = plans.copy_plan(self.source, owner=taker, name="Taken")

        self.assertEqual(copy.user_id, taker.id)
        self.assertEqual(copy.banners.count(), 2)
        self.assertEqual(
            set(copy.banners.values_list("user_id", flat=True)), {taker.id}
        )

    def test_editing_the_copy_does_not_touch_the_source(self):
        copy = plans.copy_plan(self.source, owner=self.user, name="Copy")
        copy.banners.update(number_of_pulls=1)
        self.assertEqual(
            sorted(self.source.banners.values_list("number_of_pulls", flat=True)),
            [60, 120],
        )


class PlanRouteTests(CalculatorTestCase):
    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.banner = make_uma_banner()

    def test_routes_require_sign_in(self):
        guest = APIClient()
        self.assertEqual(guest.get("/plans").status_code, 401)
        self.assertEqual(guest.post("/plans", {"name": "x"}, format="json").status_code, 401)
        self.assertEqual(guest.get("/plans/1").status_code, 401)

    def test_list_starts_with_the_main_plan(self):
        res = self.client.get("/plans")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["name"], DEFAULT_PLAN_NAME)
        self.assertTrue(res.data[0]["is_active"])
        # The whitelist: nothing about the owner rides along.
        self.assertEqual(
            set(res.data[0].keys()), {"id", "name", "is_active", "updated_at"}
        )

    def test_create_blank(self):
        res = self.client.post("/plans", {"name": "  Whale   route "}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["plan"]["name"], "Whale route")
        self.assertFalse(res.data["plan"]["is_active"])
        self.assertEqual(res.data["user_planned_banner_data"], [])
        # The Main plan exists beside it and is still the active one.
        self.assertEqual(Plan.objects.filter(user=self.user).count(), 2)

    def test_create_refuses_a_blank_name(self):
        res = self.client.post("/plans", {"name": "   "}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_create_as_a_copy(self):
        main = plans.get_active_plan(self.user)
        _row(main, self.banner, pulls=200)

        res = self.client.post(
            "/plans", {"name": "Variant", "copy_from": main.id}, format="json"
        )

        self.assertEqual(res.status_code, 201)
        rows = res.data["user_planned_banner_data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number_of_pulls"], 200)
        self.assertEqual(rows[0]["plan"], res.data["plan"]["id"])

    def test_cannot_copy_someone_elses_plan(self):
        other = make_user(username="other")
        theirs = plans.get_active_plan(other)
        _row(theirs, self.banner)

        res = self.client.post(
            "/plans", {"name": "Stolen", "copy_from": theirs.id}, format="json"
        )

        self.assertEqual(res.status_code, 404)
        self.assertFalse(Plan.objects.filter(user=self.user, name="Stolen").exists())

    def test_the_cap_refuses_one_more(self):
        plans.get_active_plan(self.user)
        for index in range(PLAN_CAP - 1):
            res = self.client.post("/plans", {"name": f"Plan {index}"}, format="json")
            self.assertEqual(res.status_code, 201)

        res = self.client.post("/plans", {"name": "One too many"}, format="json")

        self.assertEqual(res.status_code, 400)
        self.assertEqual(Plan.objects.filter(user=self.user).count(), PLAN_CAP)

    def test_detail_returns_that_plans_rows_only(self):
        main = plans.get_active_plan(self.user)
        other = Plan.objects.create(user=self.user, name="Other")
        _row(main, self.banner, pulls=11)
        _row(other, self.banner, pulls=22)

        res = self.client.get(f"/plans/{other.id}")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            [row["number_of_pulls"] for row in res.data["user_planned_banner_data"]],
            [22],
        )

    def test_someone_elses_plan_is_a_404_on_every_method(self):
        other = make_user(username="other")
        theirs = plans.get_active_plan(other)
        _row(theirs, self.banner)

        self.assertEqual(self.client.get(f"/plans/{theirs.id}").status_code, 404)
        self.assertEqual(
            self.client.patch(
                f"/plans/{theirs.id}", {"name": "Mine now"}, format="json"
            ).status_code, 404)
        self.assertEqual(self.client.delete(f"/plans/{theirs.id}").status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, DEFAULT_PLAN_NAME)
        self.assertEqual(theirs.banners.count(), 1)

    def test_rename(self):
        main = plans.get_active_plan(self.user)
        res = self.client.patch(f"/plans/{main.id}", {"name": "F2P"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["name"], "F2P")

    def test_activate_switches_the_open_plan(self):
        main = plans.get_active_plan(self.user)
        other = Plan.objects.create(user=self.user, name="Other")

        res = self.client.patch(f"/plans/{other.id}", {"is_active": True}, format="json")

        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["is_active"])
        main.refresh_from_db()
        self.assertFalse(main.is_active)
        # And /calculator-data now opens on it.
        self.assertEqual(self.client.get("/calculator-data").data["active_plan_id"], other.id)

    def test_is_active_false_is_ignored(self):
        main = plans.get_active_plan(self.user)
        self.client.patch(f"/plans/{main.id}", {"is_active": False}, format="json")
        main.refresh_from_db()
        self.assertTrue(main.is_active)

    def test_delete_takes_the_rows_and_lands_on_a_survivor(self):
        main = plans.get_active_plan(self.user)
        other = Plan.objects.create(user=self.user, name="Other")
        _row(other, self.banner)
        plans.set_active_plan(other)

        res = self.client.delete(f"/plans/{other.id}")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["active_plan_id"], main.id)
        self.assertFalse(UserPlannedBanner.objects.filter(user=self.user).exists())

    def test_the_last_plan_cannot_be_deleted(self):
        main = plans.get_active_plan(self.user)
        res = self.client.delete(f"/plans/{main.id}")
        self.assertEqual(res.status_code, 400)
        self.assertTrue(Plan.objects.filter(id=main.id).exists())


class PlanScopedSaveTests(CalculatorTestCase):
    """PATCH /calculator-data writes banner rows into the plan the body names."""

    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.banner = make_uma_banner()
        self.other_banner = make_uma_banner(name="Second Uma Banner")
        self.plan_a = plans.get_active_plan(self.user)
        self.plan_b = Plan.objects.create(user=self.user, name="B")
        self.row_a = _row(self.plan_a, self.banner, pulls=100)
        self.row_b = _row(self.plan_b, self.other_banner, pulls=200)

    def _patch(self, body):
        return self.client.patch("/calculator-data", body, format="json")

    def test_get_serves_the_active_plans_rows_and_the_plan_list(self):
        res = self.client.get("/calculator-data")
        self.assertEqual(res.data["active_plan_id"], self.plan_a.id)
        self.assertEqual([p["name"] for p in res.data["user_plans"]],
                         [DEFAULT_PLAN_NAME, "B"])
        self.assertEqual(
            [row["id"] for row in res.data["user_planned_banner_data"]],
            [self.row_a.id],
        )

    def test_a_late_save_for_plan_a_cannot_touch_plan_b(self):
        """The auto-save race: edit A, switch to B, THEN the 5 s timer fires.

        The body is A's rows and names A. B is active by the time it arrives.
        It must land in A, and B's rows must survive the delete-what-was-not-
        named step.
        """
        plans.set_active_plan(self.plan_b)

        res = self._patch({
            "plan_id": self.plan_a.id,
            "user_planned_banner_data": [
                {"id": self.row_a.id, "number_of_pulls": 150},
            ],
        })

        self.assertEqual(res.status_code, 200)
        self.row_a.refresh_from_db()
        self.row_b.refresh_from_db()  # still exists
        self.assertEqual(self.row_a.number_of_pulls, 150)
        self.assertEqual(self.row_b.number_of_pulls, 200)

    def test_an_empty_list_clears_only_the_named_plan(self):
        res = self._patch({"plan_id": self.plan_b.id, "user_planned_banner_data": []})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(self.plan_b.banners.exists())
        self.assertTrue(self.plan_a.banners.exists())

    def test_new_rows_land_in_the_named_plan(self):
        res = self._patch({
            "plan_id": self.plan_b.id,
            "user_planned_banner_data": [
                {"id": self.row_b.id},
                {"banner_uma": self.banner.id, "number_of_pulls": 30},
            ],
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.plan_b.banners.count(), 2)
        self.assertEqual(self.plan_a.banners.count(), 1)

    def test_a_row_id_from_another_plan_is_not_found_and_nothing_is_saved(self):
        res = self._patch({
            "plan_id": self.plan_b.id,
            "user_stats_data": {"current_carat": 4242},
            "user_planned_banner_data": [
                {"id": self.row_a.id, "number_of_pulls": 1},
            ],
        })
        self.assertEqual(res.status_code, 404)
        # Rolled back whole: B kept the row the delete step had removed, A's
        # row is unchanged, and the stats written first did not persist.
        self.assertTrue(UserPlannedBanner.objects.filter(id=self.row_b.id).exists())
        self.row_a.refresh_from_db()
        self.assertEqual(self.row_a.number_of_pulls, 100)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.current_carat, 4242)

    def test_a_row_cannot_name_its_own_plan(self):
        """`plan` inside a row is read-only; only the top-level plan_id counts."""
        res = self._patch({
            "plan_id": self.plan_a.id,
            "user_planned_banner_data": [
                {"id": self.row_a.id},
                {"banner_uma": self.other_banner.id, "number_of_pulls": 5,
                 "plan": self.plan_b.id},
            ],
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.plan_a.banners.count(), 2)
        self.assertEqual(self.plan_b.banners.count(), 1)

    def test_someone_elses_plan_id_is_a_404_and_writes_nothing(self):
        other = make_user(username="other")
        theirs = plans.get_active_plan(other)
        their_row = _row(theirs, self.banner, pulls=77)

        res = self._patch({"plan_id": theirs.id, "user_planned_banner_data": []})

        self.assertEqual(res.status_code, 404)
        self.assertTrue(UserPlannedBanner.objects.filter(id=their_row.id).exists())

    def test_a_non_numeric_plan_id_is_a_404_not_a_500(self):
        res = self._patch({"plan_id": "abc", "user_planned_banner_data": []})
        self.assertEqual(res.status_code, 404)

    def test_no_plan_id_means_the_active_plan(self):
        """A tab left open across the deploy: its bundle has never heard of plans."""
        res = self._patch({"user_planned_banner_data": []})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(self.plan_a.banners.exists())
        self.assertTrue(self.plan_b.banners.exists())

    def test_purchases_belong_to_the_account_not_the_plan(self):
        event = make_anniversary_event()
        product = AnniversaryEventProduct.objects.create(
            anniversary_event=event, name="Pack", product_type="carat_pack",
            usd_cost=10, paid_carat_amount=1500,
        )
        self._patch({
            "plan_id": self.plan_a.id,
            "user_planned_purchase_data": [{"product": product.id, "quantity": 1}],
        })
        plans.set_active_plan(self.plan_b)

        res = self.client.get("/calculator-data")

        self.assertEqual(res.data["active_plan_id"], self.plan_b.id)
        self.assertEqual(len(res.data["user_planned_purchase_data"]), 1)
        self.assertEqual(UserPlannedPurchase.objects.filter(user=self.user).count(), 1)

    def test_saving_rows_stamps_the_plan(self):
        before = Plan.objects.get(id=self.plan_b.id).updated_at
        self._patch({"plan_id": self.plan_b.id, "user_planned_banner_data": []})
        self.assertGreater(Plan.objects.get(id=self.plan_b.id).updated_at, before)


class MainPlanBackfillTests(CalculatorTestCase):
    """
    0067's RunPython, exercised against the live registry. The historical
    models it asks for have the same fields it reads, so it runs unchanged.
    """

    @staticmethod
    def _backfill():
        migration = import_module("calculatorapi.migrations.0067_backfill_main_plans")
        migration.backfill_main_plans(apps, None)

    def setUp(self):
        self.banner = make_uma_banner()
        self.other_banner = make_uma_banner(name="Second Uma Banner")

    def _orphan(self, user, banner, pulls=10):
        return UserPlannedBanner.objects.create(
            user=user, banner_uma=banner, number_of_pulls=pulls,
        )

    def test_each_user_with_rows_gets_one_active_main_plan_holding_them(self):
        alice = make_user(username="alice")
        bob = make_user(username="bob")
        self._orphan(alice, self.banner)
        self._orphan(alice, self.other_banner)
        self._orphan(bob, self.banner)

        self._backfill()

        for user, expected_rows in ((alice, 2), (bob, 1)):
            plan = Plan.objects.get(user=user)
            self.assertEqual(plan.name, "Main plan")
            self.assertTrue(plan.is_active)
            self.assertEqual(plan.banners.count(), expected_rows)
        self.assertFalse(UserPlannedBanner.objects.filter(plan__isnull=True).exists())

    def test_a_user_with_no_rows_gets_no_plan(self):
        idle = make_user(username="idle")
        self._backfill()
        self.assertFalse(Plan.objects.filter(user=idle).exists())

    def test_running_twice_changes_nothing(self):
        alice = make_user(username="alice")
        self._orphan(alice, self.banner)

        self._backfill()
        self._backfill()

        self.assertEqual(Plan.objects.filter(user=alice).count(), 1)

    def test_stray_rows_join_an_existing_active_plan(self):
        alice = make_user(username="alice")
        existing = Plan.objects.create(user=alice, name="Already here", is_active=True)
        self._orphan(alice, self.banner)

        self._backfill()

        self.assertEqual(Plan.objects.filter(user=alice).count(), 1)
        self.assertEqual(existing.banners.count(), 1)

    def test_reverse_detaches_rows_before_deleting_plans(self):
        """CASCADE is enforced in Python: delete the plans first and every
        planned banner goes with them."""
        alice = make_user(username="alice")
        row = self._orphan(alice, self.banner)
        self._backfill()

        migration = import_module("calculatorapi.migrations.0067_backfill_main_plans")
        migration.remove_plans(apps, None)

        self.assertFalse(Plan.objects.exists())
        row.refresh_from_db()
        self.assertIsNone(row.plan_id)
