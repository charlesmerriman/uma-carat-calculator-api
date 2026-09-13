"""purge_user_pii, which retires personal data from the old password-based accounts."""

import datetime
from io import StringIO

from django.core.management import call_command
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from calculatorapi.models import (
    CustomUser,
    BannerUma, UserPlannedBanner,
    SocialAccount,
    PatreonSupporter,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_user, make_timeline, auth_client


class PurgeUserPiiTests(CalculatorTestCase):
    """`purge_user_pii` retires personal data from the old password-based
    sign-up while leaving staff logins working."""

    def setUp(self):
        self.user = make_user('olduser', 'oldpassword')
        self.staff = make_user('adminuser', 'adminpass', is_staff=True)

    def _run(self, **opts):
        out = StringIO()
        call_command('purge_user_pii', no_input=True, stdout=out, **opts)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        output = self._run(dry_run=True)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'olduser@test.com')
        self.assertTrue(self.user.has_usable_password())
        self.assertIn('Dry run', output)

    def test_purge_blanks_non_staff_pii(self):
        self._run()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')
        self.assertEqual(self.user.first_name, '')
        self.assertEqual(self.user.last_name, '')

    def test_purge_makes_password_unusable(self):
        self._run()
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
        self.assertFalse(self.user.check_password('oldpassword'))

    def test_purged_user_cannot_login(self):
        self._run()
        res = APIClient().post(
            '/login', {'username': 'olduser', 'password': 'oldpassword'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_purge_deletes_non_staff_tokens(self):
        _client, token = auth_client(self.user)
        self._run()
        self.assertFalse(Token.objects.filter(key=token.key).exists())

    def test_staff_account_is_untouched(self):
        _client, staff_token = auth_client(self.staff)
        self._run()
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.email, 'adminuser@test.com')
        self.assertTrue(self.staff.has_usable_password())
        self.assertTrue(Token.objects.filter(key=staff_token.key).exists())
        res = APIClient().post(
            '/login', {'username': 'adminuser', 'password': 'adminpass'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_purge_preserves_saved_plans(self):
        """Plans stay in the database — the accounts just become unreachable."""
        timeline = make_timeline()
        banner = BannerUma.objects.create(name='B', banner_timeline=timeline)
        UserPlannedBanner.objects.create(user=self.user, banner_uma=banner, number_of_pulls=10)
        self._run()
        self.assertEqual(UserPlannedBanner.objects.filter(user=self.user).count(), 1)

    def test_purge_is_idempotent(self):
        self._run()
        self._run()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, '')
        self.assertFalse(self.user.has_usable_password())

    def test_patreon_emails_survive_an_ordinary_purge(self):
        """Supporters are not accounts. A routine run must not wipe the field
        the admin uses to tell them apart — that needs asking for."""
        supporter = PatreonSupporter.objects.create(
            display_name='Rhondal', email='rtibplays@gmail.com')
        output = self._run()
        supporter.refresh_from_db()
        self.assertEqual(supporter.email, 'rtibplays@gmail.com')
        self.assertIn('--include-patreon', output)

    def test_include_patreon_blanks_supporter_emails(self):
        supporter = PatreonSupporter.objects.create(
            display_name='Rhondal', email='rtibplays@gmail.com',
            is_public=True, patron_since=datetime.date(2025, 1, 1))
        self._run(include_patreon=True)
        supporter.refresh_from_db()
        self.assertEqual(supporter.email, '')
        # Blanked, not deleted — the thank-you list and its consent survive.
        self.assertTrue(supporter.is_public)
        self.assertEqual(supporter.patron_since, datetime.date(2025, 1, 1))

    def test_include_patreon_dry_run_changes_nothing(self):
        supporter = PatreonSupporter.objects.create(
            display_name='Rhondal', email='rtibplays@gmail.com')
        self._run(dry_run=True, include_patreon=True)
        supporter.refresh_from_db()
        self.assertEqual(supporter.email, 'rtibplays@gmail.com')

    def test_include_patreon_runs_with_no_accounts_left_to_purge(self):
        """The account purge is a one-shot; the supporter lever must still work
        long after every account has already been scrubbed."""
        CustomUser.objects.filter(is_staff=False).delete()
        supporter = PatreonSupporter.objects.create(
            display_name='Rhondal', email='rtibplays@gmail.com')
        self._run(include_patreon=True)
        supporter.refresh_from_db()
        self.assertEqual(supporter.email, '')

    def test_social_users_survive_purge(self):
        """A social account has no PII to begin with; the purge must not break
        its ability to sign in (its token is deleted, but the link remains)."""
        social_user = CustomUser.objects.create_user(username='user_abc123')
        social_user.set_unusable_password()
        social_user.save()
        link = SocialAccount.objects.create(
            user=social_user, provider='google', subject_id='SUB-XYZ')
        self._run()
        self.assertTrue(SocialAccount.objects.filter(pk=link.pk).exists())
        self.assertEqual(SocialAccount.objects.get(pk=link.pk).user_id, social_user.pk)
