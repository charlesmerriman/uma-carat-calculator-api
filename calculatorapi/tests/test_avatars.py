"""Provider avatars: the one profile attribute an account holds, read at sign-in and shown only to its owner."""

import base64
import datetime
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
class ProviderAvatarTests(CalculatorTestCase):
    """The avatar: the ONE profile attribute an account holds (since 2026-09-12).

    Two things have to stay true at once. Each provider's extractor must hand
    the picture back, and it must hand back NOTHING ELSE. Google's "profile"
    scope in particular puts a real name in the same token, and the only thing
    keeping that name out of this database is what these extractors return.
    """

    GOOGLE_CONFIG = {"client_id": "test-google-client"}
    PICTURE = "https://lh3.googleusercontent.com/a/ACg8ocJ-avatar=s96-c"
    OTHER_PICTURE = "https://c10.patreonusercontent.com/4/patreon-media/p/user/1/thumb.png"

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

    def test_identity_carries_exactly_two_fields(self):
        """Adding a third field is the moment the privacy boundary moves, and it
        should have to argue with this test to do it."""
        self.assertEqual(oauth.Identity._fields, ("subject_id", "avatar_url"))

    # Google ──────────────────────────────────────────────────────────────────

    def test_google_scope_adds_profile_for_the_picture_and_still_no_email(self):
        """`picture` only exists in the id_token under "profile". "email" is a
        third, separate scope and must stay out."""
        scope = oauth.get_config("google")["scope"]

        self.assertEqual(scope, "openid profile")
        self.assertNotIn("email", scope)

    def test_google_reads_the_picture_and_drops_the_name(self):
        """The one that matters for Google. "profile" delivers the name in the
        same token; it must die here."""
        result = self._google(
            picture=self.PICTURE, name="Ada Lovelace", given_name="Ada",
            family_name="Lovelace", locale="en",
        )

        self.assertEqual(result, oauth.Identity("google-sub-1", self.PICTURE))
        self.assertNotIn("Lovelace", repr(result))

    def test_google_without_a_picture_yields_an_empty_avatar(self):
        self.assertEqual(self._google().avatar_url, "")

    # Discord ─────────────────────────────────────────────────────────────────

    def test_discord_builds_the_cdn_url_from_the_avatar_hash(self):
        result = self._discord({"id": "123456", "username": "ada", "avatar": "0a1b2c3d"})

        self.assertEqual(result.subject_id, "123456")
        self.assertEqual(
            result.avatar_url, "https://cdn.discordapp.com/avatars/123456/0a1b2c3d.png?size=128"
        )
        self.assertNotIn("ada", repr(result))

    def test_discord_without_a_custom_avatar_yields_an_empty_avatar(self):
        """A null hash means no custom picture. Discord's default silhouette is
        deliberately not substituted -- "" lets the client draw its own."""
        self.assertEqual(self._discord({"id": "123456", "avatar": None}).avatar_url, "")

    def test_discord_refuses_an_avatar_hash_that_is_not_hex(self):
        """The hash is interpolated into a URL, so it is validated first."""
        self.assertEqual(self._discord({"id": "1", "avatar": "../../evil"}).avatar_url, "")
        self.assertEqual(self._discord({"id": "1", "avatar": "a_0f0f"}).avatar_url,
                         "https://cdn.discordapp.com/avatars/1/a_0f0f.png?size=128")

    # Patreon ─────────────────────────────────────────────────────────────────

    def test_patreon_reads_the_thumbnail_and_drops_everything_else(self):
        result = self._patreon({
            "data": {
                "type": "user", "id": "777",
                "attributes": {"thumb_url": self.OTHER_PICTURE, "full_name": "Ada"},
            }
        })

        self.assertEqual(result, oauth.Identity("777", self.OTHER_PICTURE))
        self.assertNotIn("Ada", repr(result))

    def test_patreon_without_attributes_yields_an_empty_avatar(self):
        self.assertEqual(self._patreon({"data": {"id": "777"}}).avatar_url, "")

    # the URL filter ──────────────────────────────────────────────────────────

    def test_values_that_are_not_an_https_url_are_dropped(self):
        """The value lands in an <img src> and a 500-char column."""
        for value in (
            "http://insecure.example/a.png",
            "javascript:alert(1)",
            "",
            None,
            42,
            "https://" + "x" * 600,
        ):
            with self.subTest(value=value):
                self.assertEqual(self._google(picture=value).avatar_url, "")

    # persistence: sign-in ────────────────────────────────────────────────────

    def _sign_in(self, identity):
        state = self.client.get("/auth/google/start").json()["state"]
        with patch("calculatorapi.oauth.exchange_code", return_value=identity):
            return self.client.post(
                "/auth/social",
                {"provider": "google", "code": "CODE", "state": state},
                content_type="application/json",
            )

    def test_sign_in_stores_the_avatar_on_the_provider_row(self):
        self._sign_in(oauth.Identity("g-1", self.PICTURE))

        row = SocialAccount.objects.get(provider="google", subject_id="g-1")
        self.assertEqual(row.avatar_url, self.PICTURE)

    def test_sign_in_refreshes_the_avatar_including_to_blank(self):
        """The picture follows the provider. Someone who removed theirs must not
        keep seeing the old one here."""
        self._sign_in(oauth.Identity("g-1", self.PICTURE))

        self._sign_in(oauth.Identity("g-1", ""))

        row = SocialAccount.objects.get(provider="google", subject_id="g-1")
        self.assertEqual(row.avatar_url, "")

    # persistence: linking ────────────────────────────────────────────────────

    def _link(self, client, identity):
        state = client.get("/account/link/patreon/start").json()["state"]
        with patch("calculatorapi.oauth.exchange_code", return_value=identity):
            return client.post(
                "/account/link/patreon/complete",
                {"code": "CODE", "state": state},
                format="json",
            )

    def test_linking_stores_the_avatar(self):
        user = make_user("linkuser")
        client, _ = auth_client(user)

        response = self._link(client, oauth.Identity("p-1", self.OTHER_PICTURE))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["avatar_url"], self.OTHER_PICTURE)
        self.assertEqual(
            SocialAccount.objects.get(user=user, provider="patreon").avatar_url,
            self.OTHER_PICTURE,
        )

    def test_relinking_refreshes_the_avatar(self):
        """The idempotent 200 path consulted the provider too, so it follows the
        same rule as sign-in rather than freezing the first picture."""
        user = make_user("linkuser")
        client, _ = auth_client(user)
        self._link(client, oauth.Identity("p-1", self.OTHER_PICTURE))

        response = self._link(client, oauth.Identity("p-1", self.PICTURE))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SocialAccount.objects.get(user=user, provider="patreon").avatar_url, self.PICTURE
        )

    # GET /account ────────────────────────────────────────────────────────────

    @staticmethod
    def _row(user, provider, avatar_url, last_login=None, created=None):
        row = SocialAccount.objects.create(
            user=user, provider=provider, subject_id=f"{provider}-id", avatar_url=avatar_url
        )
        updates = {}
        if last_login is not None:
            updates["last_login_at"] = datetime.datetime(2026, last_login, 1, tzinfo=datetime.timezone.utc)
        if created is not None:
            updates["created_at"] = datetime.datetime(2026, created, 1, tzinfo=datetime.timezone.utc)
        if updates:
            SocialAccount.objects.filter(pk=row.pk).update(**updates)
        return row

    def test_account_lists_each_providers_own_avatar(self):
        user = make_user("avataruser")
        client, _ = auth_client(user)
        self._row(user, "google", self.PICTURE, created=1)
        self._row(user, "patreon", self.OTHER_PICTURE, created=2)

        rows = client.get("/account").data["linked_providers"]

        self.assertEqual(
            [(row["provider"], row["avatar_url"]) for row in rows],
            [("google", self.PICTURE), ("patreon", self.OTHER_PICTURE)],
        )

    def test_account_avatar_is_the_most_recently_signed_in_providers(self):
        """Follows the login the person actually uses, not the one they joined with."""
        user = make_user("avataruser")
        client, _ = auth_client(user)
        self._row(user, "google", self.PICTURE, last_login=1, created=1)
        self._row(user, "discord", "https://cdn.discordapp.com/avatars/1/ab.png", last_login=2, created=1)

        self.assertEqual(
            client.get("/account").data["avatar_url"],
            "https://cdn.discordapp.com/avatars/1/ab.png",
        )

    def test_account_avatar_skips_providers_with_no_picture(self):
        """An empty string must not win the tie just for being newest."""
        user = make_user("avataruser")
        client, _ = auth_client(user)
        self._row(user, "google", self.PICTURE, last_login=1)
        self._row(user, "discord", "", last_login=2)

        self.assertEqual(client.get("/account").data["avatar_url"], self.PICTURE)

    def test_account_avatar_is_null_when_no_provider_has_one(self):
        """null, not "": the client draws a fallback on null and would try to
        load "" as an image."""
        user = make_user("avataruser")
        client, _ = auth_client(user)
        self._row(user, "google", "")

        self.assertIsNone(client.get("/account").data["avatar_url"])

    def test_a_link_only_provider_counts_by_the_date_it_was_linked(self):
        """A provider that was linked but never signed in through has no
        last_login_at; its link date stands in rather than disqualifying it."""
        user = make_user("avataruser")
        client, _ = auth_client(user)
        self._row(user, "google", self.PICTURE, last_login=2, created=1)
        self._row(user, "patreon", self.OTHER_PICTURE, created=3)

        self.assertEqual(client.get("/account").data["avatar_url"], self.OTHER_PICTURE)
