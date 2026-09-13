"""Account preferences: the display name via PATCH /account, the whitelist, and the /umas picker catalogue.

The other preference, `oshis`, has its own module (test_oshi)."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi.models import CustomUser, Uma
from calculatorapi.views.account import AccountPreferencesSerializer
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client, make_user


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class AccountPreferencesTests(CalculatorTestCase):  # pylint: disable=too-many-public-methods
    """PATCH /account: the display name, its validation, and what GET says after."""

    def setUp(self):
        self.user = make_user("user_a3f9c1")
        self.client, _ = auth_client(self.user)

    def _get(self):
        return self.client.get("/account")

    def _patch(self, body):
        return self.client.patch("/account", body, format="json")

    # the shape ───────────────────────────────────────────────────────────────

    def test_get_reports_empty_preferences_by_default(self):
        body = self._get().json()
        self.assertEqual(body["display_name"], "")
        self.assertEqual(body["oshis"], [])
        self.assertEqual(body["oshi_slots"], 0)
        self.assertIsNone(body["avatar_url"])

    def test_patch_answers_with_the_full_account_summary(self):
        response = self._patch({"display_name": "Rhondal"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"username", "display_name", "avatar_url", "oshis", "oshi_slots",
             "linked_providers", "supporter"},
        )

    # display name ────────────────────────────────────────────────────────────

    def test_display_name_is_saved_and_returned(self):
        response = self._patch({"display_name": "Rhondal"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "Rhondal")
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "Rhondal")
        self.assertEqual(self._get().json()["display_name"], "Rhondal")

    def test_display_name_is_stripped(self):
        response = self._patch({"display_name": "   Rhondal \t "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "Rhondal")

    def test_display_name_longer_than_32_is_refused(self):
        response = self._patch({"display_name": "x" * 33})
        self.assertEqual(response.status_code, 400)
        self.assertIn("display_name", response.json())
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "")

    def test_display_name_of_exactly_32_is_accepted(self):
        response = self._patch({"display_name": "x" * 32})
        self.assertEqual(response.status_code, 200)

    def test_display_name_refuses_control_and_invisible_characters(self):
        for bad in (
            "Rhon\x07dal",        # a control character (Cc)
            "Rhon\u200bdal",     # a zero-width space (Cf): looks like "Rhondal"
            "\u202eRhondal",     # a right-to-left override (Cf)
            "\u200b\u200b",     # looks blank, is not
        ):
            with self.subTest(bad=bad):
                response = self._patch({"display_name": bad})
                self.assertEqual(response.status_code, 400)
                self.assertIn("display_name", response.json())

    def test_display_name_allows_the_zero_width_joiner(self):
        # Family emoji are several people joined by U+200D; a name with one
        # is not an attack, it is a name with an emoji in it.
        response = self._patch({"display_name": "\U0001F468\u200d\U0001F469\u200d\U0001F467 Fam"})
        self.assertEqual(response.status_code, 200)

    def test_blank_display_name_clears_it(self):
        self._patch({"display_name": "Rhondal"})
        response = self._patch({"display_name": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "")

    # uniqueness: names will be visible to other users, so nobody takes another's ─

    def test_display_name_taken_by_another_account_is_refused(self):
        other = make_user("user_b7e2d0")
        other_client, _ = auth_client(other)
        self.assertEqual(self._patch({"display_name": "Rhondal"}).status_code, 200)

        response = other_client.patch("/account", {"display_name": "Rhondal"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["display_name"], ["That name is taken."])
        other.refresh_from_db()
        self.assertEqual(other.display_name, "")

    def test_display_name_uniqueness_ignores_case(self):
        other = make_user("user_b7e2d0")
        other_client, _ = auth_client(other)
        self._patch({"display_name": "Rhondal"})

        response = other_client.patch("/account", {"display_name": "rHONDAL"}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("display_name", response.json())

    def test_resaving_your_own_name_is_not_a_collision(self):
        self._patch({"display_name": "Rhondal"})
        self.assertEqual(self._patch({"display_name": "Rhondal"}).status_code, 200)
        self.assertEqual(self._patch({"display_name": "rhondal"}).status_code, 200)

    def test_a_name_freed_by_its_owner_can_be_taken(self):
        other = make_user("user_b7e2d0")
        other_client, _ = auth_client(other)
        self._patch({"display_name": "Rhondal"})
        self._patch({"display_name": ""})

        response = other_client.patch("/account", {"display_name": "Rhondal"}, format="json")

        self.assertEqual(response.status_code, 200)

    def test_many_accounts_may_have_no_name(self):
        other = make_user("user_b7e2d0")
        other_client, _ = auth_client(other)
        self.assertEqual(self._patch({"display_name": ""}).status_code, 200)
        self.assertEqual(other_client.patch("/account", {"display_name": ""}, format="json").status_code, 200)
        self.assertEqual(CustomUser.objects.filter(display_name="").count(), 2)

    def test_another_accounts_handle_is_refused_as_a_name(self):
        """A chosen "user_b7e2d0" would impersonate whoever holds that handle."""
        make_user("user_b7e2d0")

        response = self._patch({"display_name": "USER_B7E2D0"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["display_name"], ["That name is taken."])

    def test_your_own_handle_is_allowed_as_your_name(self):
        self.assertEqual(self._patch({"display_name": "user_a3f9c1"}).status_code, 200)

    def test_the_database_refuses_a_duplicate_the_view_did_not_see(self):
        """The constraint is the backstop against two people claiming a name in
        the same instant. Ignoring case, and only among non-blank names."""
        CustomUser.objects.filter(pk=self.user.pk).update(display_name="Rhondal")
        other = make_user("user_b7e2d0")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CustomUser.objects.filter(pk=other.pk).update(display_name="rhondal")

    def test_a_race_on_the_same_name_answers_400_not_500(self):
        with patch.object(AccountPreferencesSerializer, "save", side_effect=IntegrityError("dup")):
            response = self._patch({"display_name": "Rhondal"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["display_name"], ["That name is taken."])

    # the whitelist ───────────────────────────────────────────────────────────

    def test_patch_writes_only_the_two_preferences(self):
        response = self._patch({
            "display_name": "Rhondal",
            "username": "admin",
            "is_staff": True,
            "is_superuser": True,
            "current_carat": 999_999,
            "email": "someone@example.com",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "Rhondal")
        self.assertEqual(self.user.username, "user_a3f9c1")
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.current_carat, 0)
        self.assertEqual(self.user.email, "user_a3f9c1@test.com")

    def test_patching_one_preference_leaves_the_other_alone(self):
        # A supporter with one slot, so the oshi half of the body is accepted.
        # The interplay itself is test_oshi's business; this pins only that
        # a later name-only PATCH does not clear the list.
        from calculatorapi.models import PatreonSupporter, PatreonTier  # pylint: disable=import-outside-toplevel
        tier = PatreonTier.objects.create(name="Junior Class", order=3)
        PatreonSupporter.objects.create(display_name="R", patreon_user_id="7", tier=tier,
                                        is_active=True, linked_user=self.user)
        uma = Uma.objects.create(name="Special Week", image="umas/special-week.png")
        self._patch({"display_name": "Rhondal", "oshis": [uma.id]})
        response = self._patch({"display_name": "Rho"})
        self.assertEqual(response.json()["display_name"], "Rho")
        self.assertEqual([row["id"] for row in response.json()["oshis"]], [uma.id])

    def test_empty_patch_changes_nothing(self):
        self._patch({"display_name": "Rhondal"})
        response = self._patch({})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "Rhondal")

    # who may call it ─────────────────────────────────────────────────────────

    def test_anonymous_patch_is_401(self):
        response = APIClient().patch("/account", {"display_name": "x"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_staff_can_set_preferences(self):
        staff = make_user("admin", is_staff=True)
        client, _ = auth_client(staff)
        response = client.patch("/account", {"display_name": "The Admin"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "The Admin")

    # the purge ───────────────────────────────────────────────────────────────

    def test_purge_blanks_the_display_name(self):
        self._patch({"display_name": "Rhondal"})
        out = StringIO()
        call_command("purge_user_pii", "--no-input", stdout=out)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "")
        self.assertIn("holding a display name:  1", out.getvalue())


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class UmaCatalogueTests(CalculatorTestCase):
    """GET /umas: the picker's options — public, pictured umas only, by name."""

    def setUp(self):
        Uma.objects.create(name="Zeta Uma", image="umas/zeta.png")
        Uma.objects.create(name="Alpha Uma", image="umas/alpha.png", admin_comments="editor note")
        Uma.objects.create(name="Bare Uma")

    def test_is_public(self):
        self.assertEqual(APIClient().get("/umas").status_code, 200)

    def test_lists_only_umas_with_a_picture_sorted_by_name(self):
        body = APIClient().get("/umas").json()
        self.assertEqual([row["name"] for row in body], ["Alpha Uma", "Zeta Uma"])

    def test_rows_carry_only_id_name_and_image(self):
        # No admin_comments, no selector gates, no purpose: the picker has no
        # use for them, and admin_comments is an editors' field that a new
        # public route should not carry.
        row = APIClient().get("/umas").json()[0]
        self.assertEqual(set(row), {"id", "name", "image"})
        self.assertTrue(row["image"])

    def test_image_is_the_storage_url(self):
        uma = Uma.objects.get(name="Alpha Uma")
        row = APIClient().get("/umas").json()[0]
        self.assertEqual(row["image"], uma.image.url)
