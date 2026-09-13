"""Account preferences: display name and uma avatar via PATCH /account, and the /umas picker catalogue."""

from io import StringIO

from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi.models import CustomUser, SocialAccount, Uma
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client, make_user

PICTURE = "https://lh3.googleusercontent.com/a/ACg8ocJ-avatar=s96-c"


# FileSystemStorage rather than the Spaces backend: `uma.image.url` has to
# resolve without credentials for the precedence assertions below. The tests
# compare against `uma.image.url` itself rather than a literal, so they hold
# whatever the storage's URL shape is.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class AccountPreferencesTests(CalculatorTestCase):  # pylint: disable=too-many-public-methods
    """PATCH /account: the two fields, their validation, and what GET says after."""

    def setUp(self):
        self.user = make_user("user_a3f9c1")
        self.client, _ = auth_client(self.user)
        # Assigning a name to the ImageField is enough: nothing here opens the
        # file, and the storage is never asked to save one.
        self.uma = Uma.objects.create(name="Special Week", image="umas/special-week.png")
        self.bare_uma = Uma.objects.create(name="No Picture")

    def _get(self):
        return self.client.get("/account")

    def _patch(self, body):
        return self.client.patch("/account", body, format="json")

    def _google(self, user=None, avatar_url=PICTURE):
        return SocialAccount.objects.create(
            user=user or self.user, provider="google", subject_id="g-1", avatar_url=avatar_url
        )

    # the shape ───────────────────────────────────────────────────────────────

    def test_get_reports_empty_preferences_by_default(self):
        body = self._get().json()
        self.assertEqual(body["display_name"], "")
        self.assertIsNone(body["avatar_uma"])
        self.assertIsNone(body["avatar_url"])

    def test_patch_answers_with_the_full_account_summary(self):
        response = self._patch({"display_name": "Rhondal"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {"username", "display_name", "avatar_url", "avatar_uma", "linked_providers", "supporter"},
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

    def test_display_name_is_not_unique(self):
        other = make_user("user_b7e2d0")
        other_client, _ = auth_client(other)
        self.assertEqual(self._patch({"display_name": "Rhondal"}).status_code, 200)
        response = other_client.patch("/account", {"display_name": "Rhondal"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            CustomUser.objects.filter(display_name="Rhondal").count(), 2
        )

    # avatar uma ──────────────────────────────────────────────────────────────

    def test_avatar_uma_wins_over_the_provider_picture(self):
        self._google()
        response = self._patch({"avatar_uma": self.uma.id})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["avatar_uma"], self.uma.id)
        self.assertEqual(body["avatar_url"], self.uma.image.url)
        # The per-provider picture is still reported on its row: the
        # sign-in-methods list shows which picture came from where.
        self.assertEqual(body["linked_providers"][0]["avatar_url"], PICTURE)

    def test_avatar_uma_null_returns_to_the_provider_picture(self):
        self._google()
        self._patch({"avatar_uma": self.uma.id})
        response = self._patch({"avatar_uma": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["avatar_uma"])
        self.assertEqual(response.json()["avatar_url"], PICTURE)

    def test_avatar_url_is_null_with_neither_uma_nor_provider_picture(self):
        self._google(avatar_url="")
        self.assertIsNone(self._get().json()["avatar_url"])

    def test_avatar_uma_without_a_picture_is_refused(self):
        response = self._patch({"avatar_uma": self.bare_uma.id})
        self.assertEqual(response.status_code, 400)
        self.assertIn("avatar_uma", response.json())
        self.user.refresh_from_db()
        self.assertIsNone(self.user.avatar_uma_id)

    def test_unknown_avatar_uma_is_refused(self):
        response = self._patch({"avatar_uma": 999_999})
        self.assertEqual(response.status_code, 400)
        self.assertIn("avatar_uma", response.json())

    def test_deleting_the_chosen_uma_falls_back_to_the_provider(self):
        self._google()
        self._patch({"avatar_uma": self.uma.id})
        self.uma.delete()
        body = self._get().json()
        self.assertIsNone(body["avatar_uma"])
        self.assertEqual(body["avatar_url"], PICTURE)

    def test_uma_whose_picture_was_removed_falls_back_to_the_provider(self):
        # An editor can clear the image after the pick was made. The FK stays
        # (the pick is still theirs), but the URL must not be a broken tile.
        self._google()
        self._patch({"avatar_uma": self.uma.id})
        self.uma.image = None
        self.uma.save()
        body = self._get().json()
        self.assertEqual(body["avatar_uma"], self.uma.id)
        self.assertEqual(body["avatar_url"], PICTURE)

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
        self._patch({"display_name": "Rhondal", "avatar_uma": self.uma.id})
        response = self._patch({"display_name": "Rho"})
        self.assertEqual(response.json()["display_name"], "Rho")
        self.assertEqual(response.json()["avatar_uma"], self.uma.id)

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

    def test_purge_blanks_the_display_name_and_keeps_the_uma(self):
        self._patch({"display_name": "Rhondal", "avatar_uma": self.uma.id})
        out = StringIO()
        call_command("purge_user_pii", "--no-input", stdout=out)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "")
        self.assertEqual(self.user.avatar_uma_id, self.uma.id)
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
