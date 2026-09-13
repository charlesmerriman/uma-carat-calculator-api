"""Signing in and accounts: staff login, OAuth redirect URIs, GET /account, Patreon sign-in and linking."""

# Builders and request helpers take one parameter per field a test can vary.
# pylint: disable=too-many-arguments,too-many-positional-arguments

import datetime
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

from django.core import signing
from django.test import override_settings
from django.urls import NoReverseMatch, reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from calculatorapi.views.social_auth import STATE_SALT
from calculatorapi.views.account_linking import LINK_STATE_SALT
from calculatorapi import oauth
from calculatorapi.models import (
    BannerUma, CustomUser, Feedback, PatreonSupporter, SocialAccount, UserPlannedBanner,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_user, make_timeline, auth_client, FakeResponse


class AuthTests(CalculatorTestCase):
    def setUp(self):
        self.client = APIClient()

    # register ─────────────────────────────────────────────────────────────────

    def test_register_endpoint_no_longer_exists(self):
        """Public sign-up was removed when accounts moved to Google/Discord."""
        res = self.client.post('/register', {
            'username': 'newuser', 'password': 'StrongPass123!',
            'email': 'new@test.com', 'first_name': 'New', 'last_name': 'User',
        }, format='json')
        self.assertEqual(res.status_code, 404)
        self.assertFalse(CustomUser.objects.filter(username='newuser').exists())

    def test_register_route_is_not_reversible(self):
        with self.assertRaises(NoReverseMatch):
            reverse('register')

    # login (staff only) ───────────────────────────────────────────────────────

    def test_staff_login_returns_200_and_token(self):
        make_user('staffuser', 'correctpass', is_staff=True)
        res = self.client.post('/login', {'username': 'staffuser', 'password': 'correctpass'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('token', res.data)

    def test_non_staff_login_rejected_despite_correct_password(self):
        """Ordinary accounts must go through a provider, even if a password
        somehow remains set on the row."""
        make_user('loginuser', 'correctpass')
        res = self.client.post('/login', {'username': 'loginuser', 'password': 'correctpass'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertNotIn('token', res.data)

    def test_non_staff_rejection_is_indistinguishable_from_wrong_password(self):
        """Same status AND same body, so /login can't be used to discover which
        usernames exist."""
        make_user('loginuser', 'correctpass')
        valid_pw = self.client.post('/login', {'username': 'loginuser', 'password': 'correctpass'}, format='json')
        wrong_pw = self.client.post('/login', {'username': 'loginuser', 'password': 'wrongpass'}, format='json')
        no_such_user = self.client.post('/login', {'username': 'nobody', 'password': 'whatever'}, format='json')
        self.assertEqual(valid_pw.status_code, wrong_pw.status_code, no_such_user.status_code)
        self.assertEqual(valid_pw.data, wrong_pw.data)
        self.assertEqual(valid_pw.data, no_such_user.data)

    def test_login_wrong_password_returns_400(self):
        make_user('staffuser', 'correctpass', is_staff=True)
        res = self.client.post('/login', {'username': 'staffuser', 'password': 'wrongpass'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_login_nonexistent_user_returns_400(self):
        res = self.client.post('/login', {'username': 'nobody', 'password': 'whatever'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_social_user_with_unusable_password_cannot_login(self):
        """A social account has no usable password; an empty/blank attempt must
        not slip through Django's authenticate()."""
        user = make_user('socialuser')
        user.set_unusable_password()
        user.save()
        for attempt in ('', '!', 'testpass123'):
            res = self.client.post('/login', {'username': 'socialuser', 'password': attempt}, format='json')
            self.assertEqual(res.status_code, 400, f'password {attempt!r} was accepted')

    # logout ───────────────────────────────────────────────────────────────────

    def test_logout_returns_200_and_deletes_token(self):
        user = make_user()
        client, _token = auth_client(user)
        res = client.post('/logout')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Token.objects.filter(user=user).exists())

    def test_logout_unauthenticated_returns_401(self):
        res = self.client.post('/logout')
        self.assertEqual(res.status_code, 401)


CANONICAL_REDIRECT = "https://app.example.com/auth/callback"
DEV_REDIRECT = "http://localhost:5173/auth/callback"
UNLISTED_REDIRECT = "https://attacker.example.net/auth/callback"


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="test-google-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-google-secret",
    DISCORD_OAUTH_CLIENT_ID="test-discord-client",
    DISCORD_OAUTH_CLIENT_SECRET="test-discord-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT, DEV_REDIRECT]),
)
class SocialAuthRedirectUriTests(CalculatorTestCase):
    """The allowlisted `redirect_uri` parameter on /auth/<provider>/start.

    It exists so `npm run dev:live` -- a local Vite server talking to a deployed
    backend -- can complete a sign-in on localhost instead of being bounced to
    the deployed site. The allowlist is the security boundary: without it the
    endpoint would mail authorization codes to any address a caller named.
    """

    def _start(self, provider="google", redirect_uri=None):
        url = f"/auth/{provider}/start"
        if redirect_uri is not None:
            url += "?" + urlencode({"redirect_uri": redirect_uri})
        return self.client.get(url)

    @staticmethod
    def _redirect_param(response):
        """The redirect_uri the provider consent URL actually carries."""
        authorize_url = response.json()["authorize_url"]
        return parse_qs(urlparse(authorize_url).query)["redirect_uri"][0]

    def test_start_without_the_parameter_uses_the_canonical_uri(self):
        """The deployed SPA sends no parameter; its behaviour must not change."""
        response = self._start()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._redirect_param(response), CANONICAL_REDIRECT)

    def test_start_accepts_an_allowlisted_uri(self):
        response = self._start(redirect_uri=DEV_REDIRECT)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._redirect_param(response), DEV_REDIRECT)

    def test_start_rejects_an_unlisted_uri(self):
        """The whole point: an arbitrary address must not be honoured."""
        response = self._start(redirect_uri=UNLISTED_REDIRECT)

        self.assertEqual(response.status_code, 400)

    def test_an_unlisted_uri_is_refused_rather_than_silently_defaulted(self):
        """Falling back to the canonical URI would look like a working login
        that mysteriously lands somewhere else. Fail loudly instead."""
        response = self._start(redirect_uri=UNLISTED_REDIRECT)

        self.assertNotIn("authorize_url", response.json())

    def test_rejected_uri_is_not_echoed_back_to_the_caller(self):
        response = self._start(redirect_uri=UNLISTED_REDIRECT)

        self.assertNotIn(UNLISTED_REDIRECT, json.dumps(response.json()))

    def test_allowlist_is_enforced_for_discord_too(self):
        self.assertEqual(
            self._start(provider="discord", redirect_uri=UNLISTED_REDIRECT).status_code,
            400,
        )
        self.assertEqual(
            self._start(provider="discord", redirect_uri=DEV_REDIRECT).status_code,
            200,
        )

    def test_completion_exchanges_with_the_uri_the_login_started_with(self):
        """Providers bind the code to the redirect_uri, so the token request has
        to repeat the one used at the start -- not the canonical default."""
        state = self._start(redirect_uri=DEV_REDIRECT).json()["state"]

        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity("sub-1", "")) as mocked:
            response = self.client.post(
                "/auth/social",
                {"provider": "google", "code": "CODE", "state": state},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 201)
        mocked.assert_called_once_with("google", "CODE", DEV_REDIRECT)

    def test_completion_uses_the_canonical_uri_when_none_was_requested(self):
        state = self._start().json()["state"]

        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity("sub-2", "")) as mocked:
            self.client.post(
                "/auth/social",
                {"provider": "google", "code": "CODE", "state": state},
                content_type="application/json",
            )

        mocked.assert_called_once_with("google", "CODE", CANONICAL_REDIRECT)

    def test_a_state_predating_the_redirect_field_still_completes(self):
        """A login in flight while this release deploys carries a state with no
        "r". It was started against the canonical URI, so it must finish there
        rather than 400 on a field that did not exist when it was minted."""
        legacy_state = signing.dumps({"p": "google", "n": "nonce"}, salt=STATE_SALT)

        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity("sub-3", "")) as mocked:
            response = self.client.post(
                "/auth/social",
                {"provider": "google", "code": "CODE", "state": legacy_state},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 201)
        mocked.assert_called_once_with("google", "CODE", CANONICAL_REDIRECT)

    def test_the_redirect_uri_cannot_be_swapped_by_tampering_with_the_state(self):
        """The signature is what makes the sealed URI trustworthy on return."""
        forged = signing.dumps(
            {"p": "google", "n": "nonce", "r": UNLISTED_REDIRECT},
            salt="some-other-salt",
        )

        with patch("calculatorapi.oauth.exchange_code") as mocked:
            response = self.client.post(
                "/auth/social",
                {"provider": "google", "code": "CODE", "state": forged},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()

    def test_a_state_minted_for_one_provider_is_not_replayable_against_another(self):
        """Pre-existing guarantee; the payload refactor must not have lost it."""
        state = self._start(provider="google", redirect_uri=DEV_REDIRECT).json()["state"]

        with patch("calculatorapi.oauth.exchange_code") as mocked:
            response = self.client.post(
                "/auth/social",
                {"provider": "discord", "code": "CODE", "state": state},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="test-google-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-google-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT]),
)
class SocialAuthDefaultAllowlistTests(CalculatorTestCase):
    """With no extra URIs configured -- the default for any deployment that has
    not opted in -- the endpoint behaves exactly as it did before."""

    def test_localhost_is_not_allowed_by_default(self):
        response = self.client.get(
            "/auth/google/start?" + urlencode({"redirect_uri": DEV_REDIRECT})
        )

        self.assertEqual(response.status_code, 400)

    def test_the_canonical_uri_is_always_allowed_even_if_named_explicitly(self):
        response = self.client.get(
            "/auth/google/start?" + urlencode({"redirect_uri": CANONICAL_REDIRECT})
        )

        self.assertEqual(response.status_code, 200)


class AccountEndpointTests(CalculatorTestCase):
    """GET /account — the SPA's source of truth for "who am I signed in as?".

    This route exists so the client can stop inferring identity from the mere
    presence of a token string in localStorage. What it must get right is
    therefore narrow but load-bearing: it answers only for the caller, it
    refuses anonymous callers outright, and it never emits the one column on
    SocialAccount that the sign-in design exists to keep private.
    """

    def setUp(self):
        self.user = make_user('accountuser')
        self.client, _ = auth_client(self.user)

    def test_anonymous_request_is_rejected(self):
        """401 rather than an empty account body.

        This is what lets the client treat a token as untrustworthy: a revoked
        or expired token gets a 401 here, so the SPA learns the string it is
        holding has stopped meaning anything instead of rendering a signed-in
        shell around nothing.
        """
        res = APIClient().get('/account')
        self.assertEqual(res.status_code, 401)

    def test_returns_the_expected_top_level_shape(self):
        res = self.client.get('/account')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            set(res.data),
            {'username', 'display_name', 'avatar_url', 'avatar_uma', 'linked_providers', 'supporter'},
        )
        self.assertEqual(res.data['username'], 'accountuser')

    def test_lists_linked_providers_oldest_first(self):
        SocialAccount.objects.create(
            user=self.user, provider='discord', subject_id='discord-999')
        SocialAccount.objects.create(
            user=self.user, provider='google', subject_id='google-111')
        # Force a deterministic order: created_at is auto_now_add, so both rows
        # can land in the same microsecond on a fast machine.
        SocialAccount.objects.filter(provider='discord').update(
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        SocialAccount.objects.filter(provider='google').update(
            created_at=datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc))

        res = self.client.get('/account')

        providers = [row['provider'] for row in res.data['linked_providers']]
        self.assertEqual(providers, ['discord', 'google'])

    def test_a_linked_provider_carries_provider_date_and_avatar(self):
        SocialAccount.objects.create(
            user=self.user, provider='google', subject_id='google-111')

        res = self.client.get('/account')

        row = res.data['linked_providers'][0]
        self.assertEqual(set(row), {'provider', 'linked_at', 'avatar_url'})

    def test_subject_id_never_reaches_the_response(self):
        """The one that matters.

        subject_id is the provider's permanent opaque id for a person. The
        serializer's explicit field list is the only thing keeping it off the
        wire, so it is asserted on its own rather than left to the field-set
        check above — the same treatment the supporter email gets.
        """
        SocialAccount.objects.create(
            user=self.user, provider='google', subject_id='super-secret-subject')

        res = self.client.get('/account')

        self.assertNotIn('super-secret-subject', json.dumps(res.data, default=str))

    def test_only_the_callers_own_providers_are_listed(self):
        """An account summary that leaked another user's linked identities would
        be a cross-account disclosure, so it gets its own case."""
        stranger = make_user('stranger')
        SocialAccount.objects.create(
            user=stranger, provider='discord', subject_id='not-mine')
        SocialAccount.objects.create(
            user=self.user, provider='google', subject_id='google-111')

        res = self.client.get('/account')

        providers = [row['provider'] for row in res.data['linked_providers']]
        self.assertEqual(providers, ['google'])

    def test_staff_accounts_answer_with_no_linked_providers(self):
        """Staff sign in with a password and hold no SocialAccount rows at all.

        An empty list is the correct answer for them, not an error — asserted so
        a future change can't start treating "no providers" as a broken account.
        """
        staff = make_user('staffaccount', is_staff=True)
        staff_client, _ = auth_client(staff)

        res = staff_client.get('/account')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['linked_providers'], [])

    def test_supporter_block_is_present_and_false_for_everyone(self):
        """Phase 0 has no entitlement path, so nobody can be a supporter yet.

        The block still ships, because the client contract must not change when
        Phase 2 makes it real.
        """
        res = self.client.get('/account')

        self.assertEqual(res.data['supporter'], {'is_supporter': False})

    def test_supporter_block_carries_no_null_tier_fields(self):
        """A null tier_name sitting beside is_supporter: false invites a client
        to render an empty badge, or to read the absence of a tier as a tier.
        Absent means absent."""
        res = self.client.get('/account')

        self.assertEqual(set(res.data['supporter']), {'is_supporter'})


@override_settings(
    PATREON_OAUTH_CLIENT_ID="test-patreon-oauth-client",
    PATREON_OAUTH_CLIENT_SECRET="test-patreon-oauth-secret",
)
class PatreonOAuthProviderTests(CalculatorTestCase):
    """Patreon as a third SIGN-IN provider.

    The privacy posture is the whole point of these: Patreon will happily send
    an email address and a display name if asked, and the only thing stopping it
    is what this module asks for.
    """

    def test_patreon_is_a_supported_provider(self):
        self.assertTrue(oauth.is_supported("patreon"))

    def test_it_uses_the_sign_in_credentials_not_the_creator_ones(self):
        """The trap this project is most likely to fall into.

        PATREON_CLIENT_ID is the CREATOR app used by the supporters sync;
        PATREON_OAUTH_CLIENT_ID is the identity app used here. Wiring the wrong
        pair fails in a way that reads like an outage.
        """
        with override_settings(
            PATREON_CLIENT_ID="creator-app-id",
            PATREON_CLIENT_SECRET="creator-app-secret",
        ):
            config = oauth.get_config("patreon")

        self.assertEqual(config["client_id"], "test-patreon-oauth-client")
        self.assertEqual(config["client_secret"], "test-patreon-oauth-secret")

    def test_the_scope_never_asks_for_an_email(self):
        """Patreon's email sits behind a separate `identity[email]` scope.

        Asking for it would put an address in the response for every person who
        signs in — the exact thing the whole social-auth design avoids.
        """
        scope = oauth.get_config("patreon")["scope"]

        self.assertEqual(scope, "identity")
        self.assertNotIn("email", scope)

    def test_subject_id_is_read_from_the_json_api_resource(self):
        payload = {"data": {"type": "user", "id": "1234567", "attributes": {}}}

        with patch("calculatorapi.oauth.requests.get", return_value=FakeResponse(payload)):
            result = oauth._patreon_identity(  # pylint: disable=protected-access
                {}, {"access_token": "at-1"}
            )

        self.assertEqual(result.subject_id, "1234567")

    def test_it_requests_exactly_one_user_attribute_the_avatar(self):
        """A minimal sparse fieldset, and both halves of it matter.

        Absent, Patreon returns its default attribute set — full name, vanity
        URL, social handles — none of which we want to receive, let alone
        store. EMPTY, Patreon returns HTTP 400 and no one can sign in, which is
        exactly what shipped on 2026-09-09. So it names exactly one attribute:
        `thumb_url`, the avatar, which is the one this project actually wants
        (it replaced a throwaway boolean on 2026-09-12).
        """
        payload = {"data": {"id": "1234567"}}

        with patch(
            "calculatorapi.oauth.requests.get", return_value=FakeResponse(payload)
        ) as mocked:
            oauth._patreon_identity({}, {"access_token": "at-1"})  # pylint: disable=protected-access

        params = mocked.call_args.kwargs["params"]
        self.assertEqual(params, {"fields[user]": "thumb_url"})
        # The point of the assertion above, spelled out so a future edit that
        # "tidies" the value has to argue with it.
        self.assertNotEqual(params["fields[user]"], "")

    def test_a_response_without_a_user_resource_is_refused(self):
        """Guessing at an unexpected shape is how a wrong id gets bound to an
        account, so anything but a single resource object is an error."""
        for payload in ({"data": []}, {"data": None}, {}, {"data": {"type": "user"}}):
            with self.subTest(payload=payload):
                with patch(
                    "calculatorapi.oauth.requests.get", return_value=FakeResponse(payload)
                ):
                    with self.assertRaises(oauth.OAuthError):
                        oauth._patreon_identity(  # pylint: disable=protected-access
                            {}, {"access_token": "at-1"}
                        )

    def test_a_non_200_from_patreon_is_an_oauth_error(self):
        with patch(
            "calculatorapi.oauth.requests.get", return_value=FakeResponse({}, status_code=401)
        ):
            with self.assertRaises(oauth.OAuthError):
                oauth._patreon_identity({}, {"access_token": "at-1"})  # pylint: disable=protected-access


@override_settings(
    PATREON_OAUTH_CLIENT_ID="test-patreon-oauth-client",
    PATREON_OAUTH_CLIENT_SECRET="test-patreon-oauth-secret",
    GOOGLE_OAUTH_CLIENT_ID="test-google-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-google-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT, DEV_REDIRECT]),
)
class AccountLinkStartTests(CalculatorTestCase):
    """GET /account/link/<provider>/start."""

    def setUp(self):
        self.user = make_user('linkuser')
        self.client, _ = auth_client(self.user)

    def test_anonymous_callers_are_rejected(self):
        """Linking attaches an identity to an account. With no account in hand
        there is nothing to attach it to, and this must never fall back to
        creating one — that is sign-in's job."""
        response = APIClient().get("/account/link/patreon/start")

        self.assertEqual(response.status_code, 401)

    def test_unknown_provider_is_a_404(self):
        response = self.client.get("/account/link/myspace/start")

        self.assertEqual(response.status_code, 404)

    def test_returns_a_consent_url_and_state(self):
        response = self.client.get("/account/link/patreon/start")

        self.assertEqual(response.status_code, 200)
        self.assertIn("patreon.com", response.json()["authorize_url"])
        self.assertTrue(response.json()["state"])

    def test_the_allowlist_still_governs_the_redirect_uri(self):
        """Same security boundary as sign-in, and deliberately the same code:
        a second copy could drift, and a drifted copy is an open redirector."""
        allowed = self.client.get(
            "/account/link/patreon/start?" + urlencode({"redirect_uri": DEV_REDIRECT})
        )
        refused = self.client.get(
            "/account/link/patreon/start?" + urlencode({"redirect_uri": UNLISTED_REDIRECT})
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(refused.status_code, 400)

    def test_the_state_carries_the_user_it_was_minted_for(self):
        state = self.client.get("/account/link/patreon/start").json()["state"]

        payload = signing.loads(state, salt=LINK_STATE_SALT)
        self.assertEqual(payload["u"], self.user.pk)

    def test_starting_a_link_is_rate_limited(self):
        """Each accepted call sends someone to a provider, so the cap is on
        outbound work rather than on a public write. Asserted because a throttle
        that silently does not throttle looks exactly like one that does."""
        for _ in range(20):
            self.assertEqual(
                self.client.get("/account/link/patreon/start").status_code, 200
            )

        self.assertEqual(
            self.client.get("/account/link/patreon/start").status_code, 429
        )


@override_settings(
    PATREON_OAUTH_CLIENT_ID="test-patreon-oauth-client",
    PATREON_OAUTH_CLIENT_SECRET="test-patreon-oauth-secret",
    GOOGLE_OAUTH_CLIENT_ID="test-google-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-google-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT, DEV_REDIRECT]),
)
class AccountLinkCompleteTests(CalculatorTestCase):
    """POST /account/link/<provider>/complete — the account-takeover surface.

    Every test here is about something that must NOT happen.
    """

    def setUp(self):
        self.user = make_user('linkuser')
        self.client, _ = auth_client(self.user)

    def _state(self, provider="patreon", client=None):
        return (client or self.client).get(
            f"/account/link/{provider}/start"
        ).json()["state"]

    def _complete(self, state, code="CODE", provider="patreon", subject_id="patreon-1",
                  client=None):
        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity(subject_id, "")) as mocked:
            response = (client or self.client).post(
                f"/account/link/{provider}/complete",
                {"code": code, "state": state},
                format="json",
            )
        return response, mocked

    def test_links_the_identity_to_the_signed_in_account(self):
        response, _ = self._complete(self._state())

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            SocialAccount.objects.filter(
                user=self.user, provider="patreon", subject_id="patreon-1"
            ).exists()
        )

    def test_linking_never_creates_an_account(self):
        """The invariant that separates this endpoint from sign-in. If this ever
        fails, the endpoint has become a second account-creation path — one that
        runs while someone else is authenticated."""
        before = CustomUser.objects.count()

        self._complete(self._state())

        self.assertEqual(CustomUser.objects.count(), before)

    def test_completing_twice_is_idempotent(self):
        state_one = self._state()
        state_two = self._state()
        self._complete(state_one)

        response, _ = self._complete(state_two)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SocialAccount.objects.filter(user=self.user).count(), 1)

    def test_an_identity_owned_by_someone_else_is_refused(self):
        """The takeover case. Reassigning the row would strip another account of
        its sign-in method — and hand it to the caller."""
        stranger = make_user('stranger')
        SocialAccount.objects.create(
            user=stranger, provider="patreon", subject_id="patreon-1")

        response, _ = self._complete(self._state())

        self.assertEqual(response.status_code, 409)
        link = SocialAccount.objects.get(provider="patreon", subject_id="patreon-1")
        self.assertEqual(link.user, stranger)

    def test_a_second_identity_for_the_same_provider_is_refused(self):
        """One identity per provider per account, so DELETE by provider stays
        unambiguous. Unlink the first one to swap."""
        self._complete(self._state())

        response, _ = self._complete(self._state(), subject_id="patreon-2")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(SocialAccount.objects.filter(user=self.user).count(), 1)

    def test_a_state_minted_for_another_user_is_refused(self):
        """Login-CSRF, aimed at linking. Without the user binding, an attacker
        starts a link on their own account, gets the victim's browser to
        complete it, and their Patreon identity lands on the victim's account —
        after which they can sign in as the victim at will.
        """
        attacker = make_user('attacker')
        attacker_client, _ = auth_client(attacker)
        attacker_state = self._state(client=attacker_client)

        response, mocked = self._complete(attacker_state)

        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()
        self.assertFalse(SocialAccount.objects.filter(user=self.user).exists())

    def test_a_sign_in_state_cannot_be_used_to_link(self):
        """Why the two salts differ.

        A sign-in state carries no `u` at all, so if this endpoint accepted them
        the user-binding check above would pass vacuously and every protection
        it provides would be gone.
        """
        sign_in_state = self.client.get("/auth/patreon/start").json()["state"]

        response, mocked = self._complete(sign_in_state)

        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()

    def test_a_forged_state_is_refused(self):
        forged = signing.dumps(
            {"p": "patreon", "n": "nonce", "r": CANONICAL_REDIRECT, "u": self.user.pk},
            salt="not-the-real-salt",
        )

        response, mocked = self._complete(forged)

        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()

    def test_a_state_for_one_provider_is_not_replayable_against_another(self):
        state = self._state(provider="google")

        response, mocked = self._complete(state, provider="patreon")

        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()

    def test_the_exchange_repeats_the_uri_the_flow_started_with(self):
        state = self.client.get(
            "/account/link/patreon/start?" + urlencode({"redirect_uri": DEV_REDIRECT})
        ).json()["state"]

        _, mocked = self._complete(state)

        mocked.assert_called_once_with("patreon", "CODE", DEV_REDIRECT)

    def test_anonymous_callers_are_rejected(self):
        response = APIClient().post(
            "/account/link/patreon/complete", {"code": "C", "state": "S"}, format="json"
        )

        self.assertEqual(response.status_code, 401)


class AccountLinkDeleteTests(CalculatorTestCase):
    """DELETE /account/link/<provider> — and the refusal that prevents lockouts."""

    def setUp(self):
        self.user = make_user('linkuser')
        # Ordinary accounts have no usable password: a provider is the ONLY way in.
        self.user.set_unusable_password()
        self.user.save()
        self.client, _ = auth_client(self.user)

    def test_unlinks_a_provider(self):
        SocialAccount.objects.create(user=self.user, provider="google", subject_id="g-1")
        SocialAccount.objects.create(user=self.user, provider="patreon", subject_id="p-1")

        response = self.client.delete("/account/link/patreon")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            SocialAccount.objects.filter(user=self.user, provider="patreon").exists()
        )

    def test_unlinking_something_that_is_not_linked_is_a_404(self):
        response = self.client.delete("/account/link/patreon")

        self.assertEqual(response.status_code, 404)

    def test_the_last_sign_in_method_cannot_be_removed(self):
        """There is no password and no email to send a reset to — by design. So
        removing the only provider would lock someone out of their own saved
        plan permanently, with nothing to recover through."""
        SocialAccount.objects.create(user=self.user, provider="google", subject_id="g-1")

        response = self.client.delete("/account/link/google")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(SocialAccount.objects.filter(user=self.user).exists())

    def test_staff_may_remove_their_last_provider(self):
        """Staff sign in with a password that actually works, so they are not
        locking themselves out."""
        staff = make_user('staffuser', is_staff=True)
        SocialAccount.objects.create(user=staff, provider="google", subject_id="g-staff")
        staff_client, _ = auth_client(staff)

        response = staff_client.delete("/account/link/google")

        self.assertEqual(response.status_code, 204)

    def test_one_user_cannot_unlink_another_users_provider(self):
        stranger = make_user('stranger')
        SocialAccount.objects.create(
            user=stranger, provider="patreon", subject_id="p-stranger")

        response = self.client.delete("/account/link/patreon")

        self.assertEqual(response.status_code, 404)
        self.assertTrue(SocialAccount.objects.filter(user=stranger).exists())

    def test_anonymous_callers_are_rejected(self):
        response = APIClient().delete("/account/link/patreon")

        self.assertEqual(response.status_code, 401)


@override_settings(
    PATREON_OAUTH_CLIENT_ID="test-patreon-oauth-client",
    PATREON_OAUTH_CLIENT_SECRET="test-patreon-oauth-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT]),
)
class PatreonSignInTests(CalculatorTestCase):
    """Signing in WITH Patreon, as opposed to linking it."""

    def test_sign_in_resolves_to_the_account_that_linked_it(self):
        """The payoff for linking: after attaching Patreon to an existing
        account, signing in with Patreon must land in THAT account rather than
        minting a second empty one."""
        user = make_user('linkuser')
        SocialAccount.objects.create(
            user=user, provider="patreon", subject_id="patreon-1")
        state = self.client.get("/auth/patreon/start").json()["state"]

        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity("patreon-1", "")):
            response = self.client.post(
                "/auth/social",
                {"provider": "patreon", "code": "CODE", "state": state},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token"], Token.objects.get(user=user).key)

    def test_an_unknown_patreon_identity_still_creates_an_account(self):
        """Patreon is a first-class sign-in provider, not link-only."""
        before = CustomUser.objects.count()
        state = self.client.get("/auth/patreon/start").json()["state"]

        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity("patreon-new", "")):
            response = self.client.post(
                "/auth/social",
                {"provider": "patreon", "code": "CODE", "state": state},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(CustomUser.objects.count(), before + 1)


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="test-google-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-google-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT]),
)
class AccountDeleteTests(CalculatorTestCase):
    """DELETE /account — the self-serve way out, and what it must leave behind.

    An account holds no email, so there is no support desk to write to; this
    route is the only way a person can remove their own data. The cases pin the
    OUTCOME — what is gone and what remains — rather than the implementation,
    because the models' on_delete rules do the work and a future change to one
    of them has to show up here.
    """

    def setUp(self):
        self.user = make_user('deleteme')
        # An ordinary account: no usable password, a provider is the way in.
        self.user.set_unusable_password()
        self.user.save()
        self.client, self.token = auth_client(self.user)
        SocialAccount.objects.create(
            user=self.user, provider='google', subject_id='g-del',
            avatar_url='https://lh3.googleusercontent.com/a/x')

    def test_anonymous_callers_are_rejected(self):
        self.assertEqual(APIClient().delete('/account').status_code, 401)

    def test_deletes_the_account_and_answers_204(self):
        response = self.client.delete('/account')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.user.pk).exists())

    def test_the_token_stops_working_immediately(self):
        self.client.delete('/account')

        self.assertEqual(self.client.get('/account').status_code, 401)
        self.assertFalse(Token.objects.filter(key=self.token.key).exists())

    def test_everything_the_person_entered_goes_with_them(self):
        banner = BannerUma.objects.create(name='B', banner_timeline=make_timeline())
        UserPlannedBanner.objects.create(user=self.user, banner_uma=banner, number_of_pulls=10)

        self.client.delete('/account')

        self.assertFalse(SocialAccount.objects.filter(subject_id='g-del').exists())
        self.assertFalse(UserPlannedBanner.objects.filter(banner_uma=banner).exists())
        # The catalogue itself is untouched — only the plan that referenced it.
        self.assertTrue(BannerUma.objects.filter(pk=banner.pk).exists())

    def test_signing_in_again_creates_a_fresh_account(self):
        """The (provider, subject_id) pair went with the account, so the same
        Google login now lands in a new, empty account rather than a ghost."""
        self.client.delete('/account')
        state = APIClient().get('/auth/google/start').json()['state']

        with patch('calculatorapi.oauth.exchange_code', return_value=oauth.Identity('g-del', '')):
            response = APIClient().post(
                '/auth/social',
                {'provider': 'google', 'code': 'CODE', 'state': state},
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(
            SocialAccount.objects.get(subject_id='g-del').user.username, 'deleteme')

    def test_the_supporter_row_survives_with_its_link_cleared(self):
        """A pledge is a fact about Patreon, not about this account. Same
        treatment as an unlink, a lapse and a purge: the row stays, the pointer
        goes, and the publication consent it carries is untouched."""
        supporter = PatreonSupporter.objects.create(
            display_name='Rhondal', linked_user=self.user, is_public=True)

        self.client.delete('/account')

        supporter.refresh_from_db()
        self.assertIsNone(supporter.linked_user)
        self.assertTrue(supporter.is_public)

    def test_feedback_survives_without_its_author(self):
        report = Feedback.objects.create(
            category='bug', message='The timeline is upside down.', user=self.user)

        self.client.delete('/account')

        report.refresh_from_db()
        self.assertIsNone(report.user)

    def test_staff_are_refused_and_untouched(self):
        """Admin accounts are deleted in the admin, deliberately and logged —
        not by a route anyone holding their token can call."""
        staff = make_user('staffuser', is_staff=True)
        staff_client, _ = auth_client(staff)

        response = staff_client.delete('/account')

        self.assertEqual(response.status_code, 403)
        self.assertTrue(CustomUser.objects.filter(pk=staff.pk).exists())

    def test_only_the_caller_is_deleted(self):
        """Implied by the view reading nothing but request.user, but the whole
        point of the route is worth one line that says so."""
        stranger = make_user('stranger')

        self.client.delete('/account')

        self.assertTrue(CustomUser.objects.filter(pk=stranger.pk).exists())
