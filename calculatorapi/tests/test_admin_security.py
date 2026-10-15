"""The admin's front door: the second factor (django-otp) and the lockout (django-axes).

The login tests sign in through the real form with a PLAIN Client, not the
VerifiedClient the rest of the suite inherits, because here the form is the
thing under test. -> calculatorapi/admin_site.py, docs/admin.md
"""

from base64 import b32encode
from io import StringIO

from axes.models import AccessAttempt, AccessLog
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import NoReverseMatch, reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from calculatorapi import admin_site
from calculatorapi.models import CustomUser
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import make_user

PASSWORD = "correct-horse-battery"

# The second factor ON whatever the local .env says, and django-otp's own
# per-device throttle off: a test that types a wrong code and then a right one
# would otherwise be told to wait a second between them.
SECURITY_SETTINGS = {
    "STORAGES": PLAIN_TEST_STORAGES,
    "ADMIN_REQUIRE_OTP": True,
    "OTP_TOTP_THROTTLE_FACTOR": 0,
    "OTP_STATIC_THROTTLE_FACTOR": 0,
}


class FastHasherTests(CalculatorTestCase):
    """The suite runs on the fast password hasher (calculatorproject/test_runner.py).

    Here, beside the password tests, because a silent return to PBKDF2 would
    not fail anything else: the suite would only get slow again, by minutes.
    Checked in the process the test runs in, which under --parallel is a
    worker, so this covers the forkserver path too.
    """

    def test_the_suite_runs_on_the_fast_hasher(self):
        from calculatorproject.test_runner import FAST_PASSWORD_HASHERS  # pylint: disable=import-outside-toplevel
        self.assertEqual(settings.PASSWORD_HASHERS, FAST_PASSWORD_HASHERS)
        user = CustomUser.objects.create_user(username="hashcheck", password=PASSWORD)
        self.assertTrue(user.password.startswith("md5$"))


def app_code(device):
    """The six digits an authenticator app would show for `device` right now."""
    return f"{totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits):06d}"


class FrontDoorFixture(CalculatorTestCase):
    """A superuser, a plain client, and the urls every class below hits."""

    def setUp(self):
        self.boss = CustomUser.objects.create_superuser(username="boss", password=PASSWORD)
        self.client = Client()
        self.login_url = reverse("admin:login")
        self.index_url = reverse("admin:index")
        self.setup_url = reverse("admin:two_factor_setup")
        self.backup_url = reverse("admin:two_factor_backup_codes")
        self.status_url = reverse("admin:two_factor")

    def enrol(self, user=None):
        user = user or self.boss
        device = TOTPDevice.objects.create(user=user, name="Authenticator app", confirmed=True)
        return device, admin_site.issue_backup_codes(user)

    def login(self, username="boss", password=PASSWORD, code="", **extra):
        return self.client.post(
            self.login_url,
            {"username": username, "password": password, "otp_token": code, "next": self.index_url},
            **extra,
        )

    def assert_signed_in(self, verified=True):
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.boss.pk))
        self.assertEqual(DEVICE_ID_SESSION_KEY in self.client.session, verified)

    def assert_not_signed_in(self):
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(**SECURITY_SETTINGS)
class TwoFactorLoginTests(FrontDoorFixture):
    """Username, password and a code, for an account that has an authenticator."""

    def test_the_login_page_asks_for_a_code(self):
        self.assertContains(self.client.get(self.login_url), 'name="otp_token"')

    @override_settings(ADMIN_REQUIRE_OTP=False)
    def test_with_the_second_factor_off_the_password_alone_signs_in(self):
        self.assertNotContains(self.client.get(self.login_url), 'name="otp_token"')
        self.assertRedirects(self.login(), self.index_url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.index_url).status_code, 200)

    def test_the_password_alone_does_not_sign_in_an_enrolled_account(self):
        self.enrol()
        res = self.login()
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Enter the code from your authenticator app")
        self.assert_not_signed_in()

    def test_a_wrong_code_is_refused(self):
        self.enrol()
        res = self.login(code="000000")
        self.assertContains(res, "That code is not right")
        self.assert_not_signed_in()

    def test_the_app_code_signs_in_verified(self):
        device, _ = self.enrol()
        self.assertRedirects(self.login(code=app_code(device)), self.index_url,
                             fetch_redirect_response=False)
        self.assert_signed_in()
        self.assertEqual(self.client.get(self.index_url).status_code, 200)
        # The admin_view()-wrapped pages in urls.py sit behind the same gate.
        self.assertEqual(self.client.get(reverse("admin-analytics")).status_code, 200)

    def test_a_code_typed_with_a_space_still_works(self):
        device, _ = self.enrol()
        code = app_code(device)
        self.assertRedirects(self.login(code=f"{code[:3]} {code[3:]}"), self.index_url,
                             fetch_redirect_response=False)

    def test_an_app_code_is_not_accepted_twice(self):
        device, _ = self.enrol()
        code = app_code(device)
        self.login(code=code)
        self.client.logout()
        self.assertContains(self.login(code=code), "That code is not right")
        self.assert_not_signed_in()

    def test_a_backup_code_signs_in_once(self):
        _, codes = self.enrol()
        # Typed in capitals, as a person copying from a printout might.
        self.assertRedirects(self.login(code=codes[0].upper()), self.index_url,
                             fetch_redirect_response=False)
        self.assert_signed_in()
        self.assertFalse(StaticToken.objects.filter(token=codes[0]).exists())
        self.client.logout()
        self.assertContains(self.login(code=codes[0]), "That code is not right")
        self.assert_not_signed_in()

    def test_a_signed_in_but_unverified_session_is_kept_out(self):
        # The form cannot produce one; force_login can, which proves the gate
        # is has_permission and not the form.
        self.enrol()
        self.client.force_login(self.boss)
        for url in (self.index_url, reverse("admin-analytics"), self.status_url):
            with self.subTest(url=url):
                res = self.client.get(url)
                self.assertEqual(res.status_code, 302)
                self.assertIn(self.login_url, res["Location"])

    def test_a_non_staff_account_is_refused_before_any_second_step(self):
        make_user("user_a3f9c1", password=PASSWORD)
        res = self.login(username="user_a3f9c1")
        self.assertEqual(res.status_code, 200)
        self.assert_not_signed_in()
        self.assertNotIn(admin_site.ENROL_KEY, self.client.session)
        self.assertFalse(TOTPDevice.objects.exists())


@override_settings(**SECURITY_SETTINGS)
class EnrolmentTests(FrontDoorFixture):
    """A staff account's first login: the setup page, the first code, the backup codes."""

    def start(self):
        return self.login()

    def test_a_first_login_goes_to_setup_without_signing_in(self):
        self.assertRedirects(self.start(), self.setup_url, fetch_redirect_response=False)
        self.assert_not_signed_in()
        self.assertEqual(self.client.session[admin_site.ENROL_KEY]["user"], self.boss.pk)

    def test_the_setup_page_shows_the_qr_code_and_the_key(self):
        self.start()
        res = self.client.get(self.setup_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "<svg")
        device = TOTPDevice.objects.get(user=self.boss, confirmed=False)
        self.assertContains(res, b32encode(device.bin_key).decode()[:4])

    def test_the_setup_page_is_not_reachable_without_the_note(self):
        self.assertRedirects(self.client.get(self.setup_url), self.login_url,
                             fetch_redirect_response=False)
        self.assertFalse(TOTPDevice.objects.filter(user=self.boss).exists())

    def test_an_expired_note_is_ignored(self):
        self.start()
        session = self.client.session
        note = session[admin_site.ENROL_KEY]
        note["at"] -= admin_site.ENROL_WINDOW_SECONDS + 1
        session[admin_site.ENROL_KEY] = note
        session.save()
        self.assertRedirects(self.client.get(self.setup_url), self.login_url,
                             fetch_redirect_response=False)
        self.assertNotIn(admin_site.ENROL_KEY, self.client.session)

    def test_confirming_the_first_code_signs_in_and_shows_the_backup_codes_once(self):
        self.start()
        self.client.get(self.setup_url)
        device = TOTPDevice.objects.get(user=self.boss, confirmed=False)
        res = self.client.post(self.setup_url, {"code": app_code(device)})
        self.assertRedirects(res, self.backup_url, fetch_redirect_response=False)
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assert_signed_in()
        self.assertEqual(self.client.session[DEVICE_ID_SESSION_KEY], device.persistent_id)
        self.assertNotIn(admin_site.ENROL_KEY, self.client.session)

        res = self.client.get(self.backup_url)
        self.assertEqual(res.status_code, 200)
        codes = list(StaticToken.objects.filter(device__user=self.boss)
                     .values_list("token", flat=True))
        self.assertEqual(len(codes), admin_site.BACKUP_CODE_COUNT)
        for code in codes:
            self.assertContains(res, code)
        # Once: a reload lands on the settings page, and the admin is open.
        self.assertRedirects(self.client.get(self.backup_url), self.status_url,
                             fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.index_url).status_code, 200)

    def test_a_wrong_first_code_leaves_the_device_unconfirmed(self):
        self.start()
        res = self.client.post(self.setup_url, {"code": "000000"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "That code is not right")
        self.assertFalse(TOTPDevice.objects.filter(user=self.boss, confirmed=True).exists())
        self.assert_not_signed_in()

    def test_a_reload_keeps_the_same_secret(self):
        self.start()
        self.client.get(self.setup_url)
        self.client.get(self.setup_url)
        self.assertEqual(TOTPDevice.objects.filter(user=self.boss).count(), 1)

    def test_an_account_enrolled_meanwhile_is_sent_back_to_the_login(self):
        self.start()
        TOTPDevice.objects.create(user=self.boss, name="other tab", confirmed=True)
        self.assertRedirects(self.client.get(self.setup_url), self.login_url,
                             fetch_redirect_response=False)

    def test_deleting_the_authenticator_sends_the_next_login_back_to_setup(self):
        device, _ = self.enrol()
        device.delete()
        self.assertRedirects(self.start(), self.setup_url, fetch_redirect_response=False)


@override_settings(**SECURITY_SETTINGS)
class TwoFactorSettingsTests(CalculatorTestCase):
    """The settings page and the user page, reached with the suite's VerifiedClient."""

    def setUp(self):
        self.boss = CustomUser.objects.create_superuser(username="boss", password=PASSWORD)
        TOTPDevice.objects.create(user=self.boss, name="Authenticator app", confirmed=True)
        self.old_codes = admin_site.issue_backup_codes(self.boss)
        self.client.force_login(self.boss)

    def test_the_page_shows_how_many_codes_are_left(self):
        res = self.client.get(reverse("admin:two_factor"))
        self.assertContains(res, "10 unused codes left")
        self.assertContains(res, "Issue new backup codes")

    def test_issuing_new_codes_cancels_the_old_set(self):
        res = self.client.post(reverse("admin:two_factor"), {"action": "regenerate"})
        self.assertRedirects(res, reverse("admin:two_factor_backup_codes"),
                             fetch_redirect_response=False)
        res = self.client.get(reverse("admin:two_factor_backup_codes"))
        new_codes = set(StaticToken.objects.filter(device__user=self.boss)
                        .values_list("token", flat=True))
        self.assertEqual(len(new_codes), admin_site.BACKUP_CODE_COUNT)
        self.assertFalse(new_codes & set(self.old_codes))
        for code in new_codes:
            self.assertContains(res, code)

    def test_the_user_page_lists_the_devices_of_staff_only(self):
        res = self.client.get(reverse("admin:calculatorapi_customuser_change", args=[self.boss.pk]))
        self.assertContains(res, "Authenticator app (two-factor)")
        self.assertContains(res, "Backup codes (two-factor)")
        plain = make_user("user_plain")
        res = self.client.get(reverse("admin:calculatorapi_customuser_change", args=[plain.pk]))
        self.assertNotContains(res, "(two-factor)")

    def test_the_stock_device_admins_are_off_the_site(self):
        for name in ("otp_totp_totpdevice", "otp_static_staticdevice"):
            with self.subTest(name=name), self.assertRaises(NoReverseMatch):
                reverse(f"admin:{name}_changelist")


@override_settings(STORAGES=PLAIN_TEST_STORAGES, ADMIN_REQUIRE_OTP=False)
class LockoutTests(CalculatorTestCase):
    """django-axes on the admin form. The second factor is OFF here so a right
    password shows as a redirect; a lockout happens before either factor."""

    def setUp(self):
        CustomUser.objects.create_superuser(username="boss", password=PASSWORD)
        self.client = Client()
        self.login_url = reverse("admin:login")

    def attempt(self, password, ip="203.0.113.5", username="boss"):
        return self.client.post(
            self.login_url,
            {"username": username, "password": password},
            HTTP_X_FORWARDED_FOR=ip,
        )

    def test_the_fifth_wrong_password_locks_the_pair(self):
        for _ in range(4):
            self.assertEqual(self.attempt("nope").status_code, 200)
        res = self.attempt("nope")
        self.assertEqual(res.status_code, 429)
        self.assertContains(res, "Too many sign-in attempts", status_code=429)
        self.assertContains(res, "15 minutes", status_code=429)
        # The right password is refused too while the pair is locked.
        self.assertEqual(self.attempt(PASSWORD).status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_different_address_is_not_locked(self):
        for _ in range(5):
            self.attempt("nope", ip="203.0.113.5")
        self.assertEqual(self.attempt(PASSWORD, ip="198.51.100.9").status_code, 302)

    def test_a_different_username_from_the_same_address_is_not_locked(self):
        CustomUser.objects.create_superuser(username="mario", password=PASSWORD)
        for _ in range(5):
            self.attempt("nope", username="boss")
        self.assertEqual(self.attempt(PASSWORD, username="mario").status_code, 302)

    def test_the_forwarded_address_is_the_one_counted(self):
        # REMOTE_ADDR is the proxy in production: counting it would put every
        # visitor in one bucket. The test client's REMOTE_ADDR is 127.0.0.1.
        self.attempt("nope", ip="203.0.113.5")
        self.assertEqual(AccessAttempt.objects.get().ip_address, "203.0.113.5")

    def test_a_right_password_resets_the_count(self):
        for _ in range(4):
            self.attempt("nope")
        self.assertEqual(self.attempt(PASSWORD).status_code, 302)
        self.client.logout()
        for _ in range(4):
            self.assertEqual(self.attempt("nope").status_code, 200)

    def test_a_sign_in_is_written_to_the_log(self):
        self.attempt(PASSWORD)
        row = AccessLog.objects.get()
        self.assertEqual((row.username, row.ip_address), ("boss", "203.0.113.5"))


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class SecurityAdminTests(CalculatorTestCase):
    """The Security pages: the owner reads them, a content editor cannot."""

    def test_the_owner_sees_the_sign_in_log_and_the_lockouts(self):
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))
        for name in ("accesslog", "accessattempt"):
            with self.subTest(name=name):
                res = self.client.get(reverse(f"admin:axes_{name}_changelist"))
                self.assertEqual(res.status_code, 200)

    def test_content_editors_do_not(self):
        call_command("create_content_editor_group", stdout=StringIO())
        editor = CustomUser.objects.create_user(username="editor", password="x", is_staff=True)
        editor.groups.add(Group.objects.get(name="Content editors"))
        self.client.force_login(editor)
        for name in ("accesslog", "accessattempt"):
            with self.subTest(name=name):
                res = self.client.get(reverse(f"admin:axes_{name}_changelist"))
                self.assertEqual(res.status_code, 403)
