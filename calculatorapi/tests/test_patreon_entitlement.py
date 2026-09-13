"""
Supporter entitlement: joining a Patreon pledge to an account on the site.

The daily sync always knew WHO was pledging, and the sign-in tables always knew
who was signed in, but nothing joined the two. These tests cover the join.
"""

import datetime
import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from calculatorapi.views.social_auth import STATE_SALT
from calculatorapi.views.account_linking import LINK_STATE_SALT
from calculatorapi.admin_patreon_import import apply_patreon_import
from calculatorapi import patreon_api
from calculatorapi import oauth
from calculatorapi import benefits
from calculatorapi.models import (
    CustomUser,
    SocialAccount,
    PatreonTier, PatreonSupporter, PatreonCredentials,
)
from calculatorapi.tests.base import CalculatorTestCase


class PatreonEntitlementTests(CalculatorTestCase):
    """`benefits`: the derivation, and the two directions a link is made from."""

    def setUp(self):
        self.tier = PatreonTier.objects.create(name="Junior Class", order=10)
        self.top_tier = PatreonTier.objects.create(name="Classic Class", order=0)
        self.user = CustomUser.objects.create(username="user_abc123")

    def _supporter(self, **kwargs):
        return PatreonSupporter.objects.create(**{
            "display_name": "Rhondal",
            "patreon_user_id": "7",
            "tier": self.tier,
            "is_active": True,
            **kwargs,
        })

    def test_an_unlinked_patron_entitles_nobody(self):
        self._supporter()
        self.assertFalse(benefits.is_supporter(self.user))

    def test_a_linked_active_tiered_patron_is_a_supporter(self):
        self._supporter(linked_user=self.user)
        self.assertTrue(benefits.is_supporter(self.user))

    def test_a_lapse_drops_entitlement_and_keeps_the_row(self):
        """Nothing is deleted and no consent decision is touched — a lapsed
        patron who resumes must not have to opt in to being thanked again."""
        supporter = self._supporter(linked_user=self.user, is_public=True)

        supporter.is_active = False
        supporter.save(update_fields=["is_active"])

        self.assertFalse(benefits.is_supporter(self.user))
        supporter.refresh_from_db()
        self.assertTrue(supporter.is_public)
        self.assertEqual(supporter.linked_user_id, self.user.pk)

    def test_a_linked_patron_on_no_tier_is_not_a_supporter(self):
        self._supporter(linked_user=self.user, tier=None)
        self.assertFalse(benefits.is_supporter(self.user))

    def test_an_anonymous_caller_is_never_a_supporter(self):
        self.assertFalse(benefits.is_supporter(AnonymousUser()))
        self.assertFalse(benefits.is_supporter(None))

    def test_ad_free_needs_only_a_paid_tier(self):
        self._supporter(linked_user=self.user, tier=self.tier)
        self.assertTrue(benefits.has_benefit(self.user, benefits.AD_FREE))

    def test_a_tier_gated_benefit_reads_order_as_a_threshold(self):
        """Lower order = higher tier, so the check is `<=`. Asserted through a
        temporary entry rather than a real one, so this keeps testing the
        mechanism after the benefit map changes."""
        supporter = self._supporter(linked_user=self.user, tier=self.tier)
        with patch.dict(benefits.BENEFITS, {"top_only": 0}, clear=False):
            self.assertFalse(benefits.has_benefit(self.user, "top_only"))

            supporter.tier = self.top_tier
            supporter.save(update_fields=["tier"])
            self.assertTrue(benefits.has_benefit(self.user, "top_only"))

    def test_an_unknown_benefit_key_raises(self):
        """A typo in a gate must fail loudly at the first request, not quietly
        refuse everyone forever."""
        with self.assertRaises(KeyError):
            benefits.has_benefit(self.user, "no_such_benefit")

    def test_alice_pledges_then_links(self):
        """The sync got there first; linking finds the row by id."""
        self._supporter()

        benefits.link_supporter_to_user(self.user, "7")

        self.assertTrue(benefits.is_supporter(self.user))

    def test_bob_links_then_pledges(self):
        """No row exists at link time. The next sync creates it, sees the
        SocialAccount already there, and attaches — with no second action
        from him."""
        SocialAccount.objects.create(
            user=self.user, provider=SocialAccount.PROVIDER_PATREON, subject_id="7")

        summary = apply_patreon_import([{
            "display_name": "Rhondal", "patreon_user_id": "7", "email": "",
            "tier_name": "Junior Class", "is_active": True,
        }])

        self.assertEqual(summary["linked"], ["Rhondal"])
        self.assertTrue(benefits.is_supporter(self.user))

    def test_a_patron_is_never_moved_onto_a_second_account(self):
        other = CustomUser.objects.create(username="user_def456")
        self._supporter(linked_user=other)

        self.assertIsNone(benefits.link_supporter_to_user(self.user, "7"))
        self.assertFalse(benefits.is_supporter(self.user))

    def test_unlinking_keeps_the_row_and_its_consent(self):
        supporter = self._supporter(linked_user=self.user, is_public=True,
                                    patron_since=datetime.date(2025, 1, 1))

        self.assertTrue(benefits.unlink_supporter(self.user))

        supporter.refresh_from_db()
        self.assertIsNone(supporter.linked_user_id)
        self.assertTrue(supporter.is_public)
        self.assertEqual(supporter.patron_since, datetime.date(2025, 1, 1))
        self.assertFalse(benefits.is_supporter(self.user))

    def test_deleting_an_account_keeps_the_supporter_row(self):
        """SET_NULL, not CASCADE. They are still a patron — they just have no
        account here any more."""
        self._supporter(linked_user=self.user, is_public=True)

        self.user.delete()

        supporter = PatreonSupporter.objects.get(patreon_user_id="7")
        self.assertIsNone(supporter.linked_user_id)
        self.assertTrue(supporter.is_public)


class SupporterAccountEndpointTests(CalculatorTestCase):
    """What GET /account says about entitlement, and what it refuses to say."""

    def setUp(self):
        self.client = APIClient()
        self.tier = PatreonTier.objects.create(name="Junior Class", order=10)
        self.user = CustomUser.objects.create(username="user_abc123")
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_a_non_supporter_gets_the_bare_block(self):
        """No null tier and no empty benefits list — either would invite a
        client to render an empty badge, or to read the absence of a tier as
        a tier."""
        response = self.client.get("/account")

        self.assertEqual(response.data["supporter"], {"is_supporter": False})

    def test_a_supporter_gets_their_tier_and_benefit_keys(self):
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier,
            is_active=True, linked_user=self.user, email="r@example.com")

        block = self.client.get("/account").data["supporter"]

        self.assertTrue(block["is_supporter"])
        self.assertEqual(block["tier"], "Junior Class")
        self.assertIn(benefits.AD_FREE, block["benefits"])

    def test_the_block_never_carries_supporter_identifiers(self):
        """The same field-list discipline the linked providers are under: the
        account owner has no use for the row id, the Patreon id, the display
        name or the admin-only email, so none of them are on the wire."""
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier,
            is_active=True, linked_user=self.user, email="r@example.com")

        body = json.dumps(self.client.get("/account").data)

        self.assertNotIn("r@example.com", body)
        self.assertNotIn("Rhondal", body)
        self.assertNotIn("patreon_user_id", body)

    def test_a_lapsed_supporter_reads_as_not_a_supporter(self):
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier,
            is_active=False, linked_user=self.user)

        self.assertEqual(
            self.client.get("/account").data["supporter"], {"is_supporter": False})


@override_settings(
    PATREON_OAUTH_CLIENT_ID="pid",
    PATREON_OAUTH_CLIENT_SECRET="psecret",
    OAUTH_REDIRECT_URI="http://localhost:5173/auth/callback",
)
class PatreonLinkEntitlementTests(CalculatorTestCase):
    """Linking Patreon to an account resolves entitlement there and then."""

    def setUp(self):
        self.client = APIClient()
        self.tier = PatreonTier.objects.create(name="Junior Class", order=10)
        self.user = CustomUser.objects.create(username="user_abc123")
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        # The inline refresh is skipped outright when there is no token to use,
        # so a configured deployment is what these tests have to look like.
        credentials = PatreonCredentials.load()
        credentials.access_token = "access-1"
        credentials.refresh_token = "refresh-1"
        credentials.expires_at = timezone.now() + datetime.timedelta(days=30)
        credentials.campaign_id = "camp-1"
        credentials.save()

    def _state(self):
        return signing.dumps(
            {
                "p": "patreon",
                "n": "nonce",
                "r": "http://localhost:5173/auth/callback",
                "u": self.user.pk,
            },
            salt=LINK_STATE_SALT,
        )

    def _complete(self, subject_id="7"):
        with patch.object(oauth, "exchange_code", return_value=oauth.Identity(subject_id)):
            return self.client.post(
                "/account/link/patreon/complete",
                {"code": "abc", "state": self._state()},
                format="json",
            )

    def test_linking_a_known_patron_makes_them_a_supporter_immediately(self):
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier, is_active=True)

        response = self._complete()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(benefits.is_supporter(self.user))

    def test_a_known_patron_costs_no_request_to_patreon(self):
        """The common case must not reach the network at all: anyone who
        pledged before today is already in the table."""
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier, is_active=True)

        with patch("calculatorapi.patreon_api.fetch_members") as fetch:
            self._complete()

        fetch.assert_not_called()

    def test_carol_pledges_and_links_in_the_same_minute(self):
        """Nobody knows about her yet, so the link triggers the real sync —
        the same producer and the same reconcile as every other path, not a
        second route into entitlement reading her own token."""
        with patch("calculatorapi.patreon_api.fetch_members", return_value=[{
            "display_name": "Carol", "patreon_user_id": "7", "email": "",
            "tier_name": "Junior Class", "is_active": True,
        }]) as fetch:
            response = self._complete()

        self.assertEqual(response.status_code, 201)
        fetch.assert_called_once()
        self.assertTrue(benefits.is_supporter(self.user))

    def test_the_inline_sync_never_deactivates_anyone(self):
        """Retiring lapsed patrons is the daily job's business. A user action
        may only ever ADD what it came for."""
        stale = PatreonSupporter.objects.create(
            display_name="Someone Else", patreon_user_id="99",
            tier=self.tier, is_active=True)

        with patch("calculatorapi.patreon_api.fetch_members", return_value=[]):
            self._complete()

        stale.refresh_from_db()
        self.assertTrue(stale.is_active)

    def test_patreon_being_down_does_not_fail_the_link(self):
        """The link has already succeeded by the time entitlement is resolved.
        Turning that into an error would lose the SocialAccount row too."""
        with patch("calculatorapi.patreon_api.fetch_members",
                   side_effect=patreon_api.PatreonApiError("boom")):
            response = self._complete()

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            SocialAccount.objects.filter(user=self.user, provider="patreon").exists())
        self.assertFalse(benefits.is_supporter(self.user))

    def test_a_non_patron_who_links_is_simply_not_a_supporter(self):
        with patch("calculatorapi.patreon_api.fetch_members", return_value=[]):
            response = self._complete()

        self.assertEqual(response.status_code, 201)
        self.assertFalse(benefits.is_supporter(self.user))

    def test_a_conflicting_link_resolves_no_entitlement(self):
        """A 409 means the identity is someone else's, so reading a pledge off
        it would be reading a pledge that is not this user's."""
        other = CustomUser.objects.create(username="user_def456")
        SocialAccount.objects.create(
            user=other, provider=SocialAccount.PROVIDER_PATREON, subject_id="7")
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier, is_active=True)

        with patch("calculatorapi.patreon_api.fetch_members") as fetch:
            response = self._complete()

        self.assertEqual(response.status_code, 409)
        fetch.assert_not_called()
        self.assertFalse(benefits.is_supporter(self.user))

    def test_linking_google_never_touches_patreon(self):
        with patch("calculatorapi.patreon_api.fetch_members") as fetch:
            state = signing.dumps(
                {"p": "google", "n": "n", "r": "http://localhost:5173/auth/callback",
                 "u": self.user.pk},
                salt=LINK_STATE_SALT,
            )
            with patch.object(oauth, "exchange_code", return_value=oauth.Identity("g-1")):
                response = self.client.post(
                    "/account/link/google/complete",
                    {"code": "abc", "state": state}, format="json")

        self.assertEqual(response.status_code, 201)
        fetch.assert_not_called()

    def test_unlinking_patreon_drops_entitlement_but_keeps_the_row(self):
        SocialAccount.objects.create(
            user=self.user, provider=SocialAccount.PROVIDER_GOOGLE, subject_id="g-1")
        with patch("calculatorapi.patreon_api.fetch_members", return_value=[]):
            self._complete()
        supporter = PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier,
            is_active=True, is_public=True)
        benefits.link_supporter_to_user(self.user, "7")

        response = self.client.delete("/account/link/patreon")

        self.assertEqual(response.status_code, 204)
        supporter.refresh_from_db()
        self.assertIsNone(supporter.linked_user_id)
        self.assertTrue(supporter.is_public)


@override_settings(
    PATREON_OAUTH_CLIENT_ID="pid",
    PATREON_OAUTH_CLIENT_SECRET="psecret",
    OAUTH_REDIRECT_URI="http://localhost:5173/auth/callback",
)
class PatreonSignInEntitlementTests(CalculatorTestCase):
    """Signing in with Patreon attaches a known patron — locally only."""

    def setUp(self):
        self.client = APIClient()
        self.tier = PatreonTier.objects.create(name="Junior Class", order=10)

    def _sign_in(self, subject_id="7"):
        state = signing.dumps(
            {"p": "patreon", "n": "n", "r": "http://localhost:5173/auth/callback"},
            salt=STATE_SALT,
        )
        with patch.object(oauth, "exchange_code", return_value=oauth.Identity(subject_id)):
            return self.client.post(
                "/auth/social",
                {"provider": "patreon", "code": "abc", "state": state},
                format="json",
            )

    def test_a_known_patron_signing_in_becomes_a_supporter(self):
        PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=self.tier, is_active=True)

        response = self._sign_in()

        self.assertEqual(response.status_code, 201)
        supporter = PatreonSupporter.objects.get(patreon_user_id="7")
        self.assertIsNotNone(supporter.linked_user_id)
        self.assertTrue(benefits.is_supporter(supporter.linked_user))

    def test_sign_in_never_calls_patreon(self):
        """Sign-in is the hot path and most people signing in are not patrons.
        Asking Patreon on every one would spend a request per login to answer
        'no' — the link endpoint is where that question is worth asking."""
        with patch("calculatorapi.patreon_api.fetch_members") as fetch:
            self._sign_in()

        fetch.assert_not_called()


class PurgeClearsSupporterLinkTests(CalculatorTestCase):
    """A purged account must not leave a live entitlement pointing at it."""

    def test_the_link_is_cleared_and_the_row_is_not(self):
        tier = PatreonTier.objects.create(name="Junior Class", order=10)
        user = CustomUser.objects.create(username="user_abc123")
        supporter = PatreonSupporter.objects.create(
            display_name="Rhondal", patreon_user_id="7", tier=tier,
            is_active=True, is_public=True, email="r@example.com",
            linked_user=user)

        call_command("purge_user_pii", "--no-input", stdout=StringIO())

        supporter.refresh_from_db()
        self.assertIsNone(supporter.linked_user_id)
        # Not behind --include-patreon: severing a link is not touching data.
        self.assertEqual(supporter.email, "r@example.com")
        self.assertTrue(supporter.is_public)
        self.assertFalse(benefits.is_supporter(user))
