"""The identity boundary: a sign-in yields one opaque id and nothing else about the person."""

import base64
import json
import time
from unittest.mock import patch

from django.test import override_settings

from calculatorapi import oauth
from calculatorapi.models import SocialAccount
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_user, auth_client, FakeResponse

CANONICAL_REDIRECT = "https://app.example.com/auth/callback"


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID="test-google-client",
    GOOGLE_OAUTH_CLIENT_SECRET="test-google-secret",
    DISCORD_OAUTH_CLIENT_ID="test-discord-client",
    DISCORD_OAUTH_CLIENT_SECRET="test-discord-secret",
    PATREON_OAUTH_CLIENT_ID="test-patreon-oauth-client",
    PATREON_OAUTH_CLIENT_SECRET="test-patreon-oauth-secret",
    OAUTH_REDIRECT_URI=CANONICAL_REDIRECT,
    OAUTH_ALLOWED_REDIRECT_URIS=frozenset([CANONICAL_REDIRECT]),
)
class IdentityBoundaryTests(CalculatorTestCase):
    """No profile attribute is held. Not a name, not a picture, not a locale.

    For one unshipped day (2026-09-12 to 2026-09-13) the provider picture was
    stored, and these tests are what stood between that and a second field
    coming back. Each provider's extractor must hand back the id and NOTHING
    ELSE, whatever the provider put next to it.
    """

    GOOGLE_CONFIG = {"client_id": "test-google-client"}
    PICTURE = "https://lh3.googleusercontent.com/a/ACg8ocJ-avatar=s96-c"

    @staticmethod
    def _id_token(**claims):
        """An unsigned JWT carrying `claims`. oauth.py never verifies the
        signature (see _decode_jwt_payload), so "sig" is as good as any."""
        payload = {
            "iss": "https://accounts.google.com",
            "aud": "test-google-client",
            "exp": int(time.time()) + 600,
            "sub": "google-sub-1",
            **claims,
        }

        def encode(part):
            return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

        return f"{encode({'alg': 'none'})}.{encode(payload)}.sig"

    def _google(self, **claims):
        return oauth._google_identity(  # pylint: disable=protected-access
            self.GOOGLE_CONFIG, {"id_token": self._id_token(**claims)}
        )

    @staticmethod
    def _discord(profile):
        with patch("calculatorapi.oauth.requests.get", return_value=FakeResponse(profile)):
            return oauth._discord_identity(  # pylint: disable=protected-access
                {}, {"access_token": "at-1"}
            )

    @staticmethod
    def _patreon(payload):
        with patch("calculatorapi.oauth.requests.get", return_value=FakeResponse(payload)):
            return oauth._patreon_identity(  # pylint: disable=protected-access
                {}, {"access_token": "at-1"}
            )

    # the Identity type ────────────────────────────────────────────────────────

    def test_identity_carries_exactly_one_field(self):
        """Adding a second field is the moment the privacy boundary moves, and it
        should have to argue with this test to do it."""
        self.assertEqual(oauth.Identity._fields, ("subject_id",))

    # Google ──────────────────────────────────────────────────────────────────

    def test_google_scope_is_openid_alone(self):
        """"profile" is what would add the name and picture; "email" the
        address. Neither is requested."""
        self.assertEqual(oauth.get_config("google")["scope"], "openid")

    def test_google_drops_everything_but_the_subject(self):
        """Even if a wider scope ever put them in the token, the picture, the
        name and the locale must die in the extractor."""
        result = self._google(
            picture=self.PICTURE, name="Ada Lovelace", given_name="Ada",
            family_name="Lovelace", locale="en", email="ada@example.com",
        )

        self.assertEqual(result, oauth.Identity("google-sub-1"))
        for leaked in ("Lovelace", "googleusercontent", "example.com"):
            self.assertNotIn(leaked, repr(result))

    # Discord ─────────────────────────────────────────────────────────────────

    def test_discord_drops_the_username_and_avatar_hash(self):
        result = self._discord({"id": "123456", "username": "ada", "avatar": "0a1b2c3d",
                                "global_name": "Ada L"})

        self.assertEqual(result, oauth.Identity("123456"))
        self.assertNotIn("ada", repr(result))
        self.assertNotIn("0a1b2c3d", repr(result))

    # Patreon ─────────────────────────────────────────────────────────────────

    def test_patreon_drops_every_attribute(self):
        result = self._patreon({
            "data": {
                "type": "user", "id": "777",
                "attributes": {"thumb_url": self.PICTURE, "full_name": "Ada", "hide_pledges": False},
            }
        })

        self.assertEqual(result, oauth.Identity("777"))
        self.assertNotIn("Ada", repr(result))
        self.assertNotIn("googleusercontent", repr(result))

    def test_patreon_fieldset_names_a_throwaway_attribute(self):
        """Not empty (Patreon 400s), not absent (Patreon sends the full default
        profile: name, picture, social handles), and nothing about the person."""
        with patch("calculatorapi.oauth.requests.get",
                   return_value=FakeResponse({"data": {"id": "1"}})) as mocked:
            oauth._patreon_identity({}, {"access_token": "at-1"})  # pylint: disable=protected-access

        params = mocked.call_args.kwargs["params"]
        self.assertEqual(params, {"fields[user]": "hide_pledges"})
        self.assertNotEqual(params["fields[user]"], "")

    # persistence ─────────────────────────────────────────────────────────────

    def test_sign_in_stores_only_the_provider_and_subject(self):
        state = self.client.get("/auth/google/start").json()["state"]
        with patch("calculatorapi.oauth.exchange_code", return_value=oauth.Identity("g-1")):
            self.client.post(
                "/auth/social",
                {"provider": "google", "code": "CODE", "state": state},
                content_type="application/json",
            )

        row = SocialAccount.objects.get(provider="google", subject_id="g-1")
        stored = {f.name for f in SocialAccount._meta.get_fields()}  # pylint: disable=protected-access
        self.assertEqual(
            stored, {"id", "user", "provider", "subject_id", "created_at", "last_login_at"}
        )
        self.assertIsNotNone(row.last_login_at)

    def test_a_linked_provider_row_carries_only_provider_and_date(self):
        user = make_user("rowuser")
        client, _ = auth_client(user)
        SocialAccount.objects.create(user=user, provider="google", subject_id="google-id")

        rows = client.get("/account").data["linked_providers"]

        self.assertEqual([set(row) for row in rows], [{"provider", "linked_at"}])
