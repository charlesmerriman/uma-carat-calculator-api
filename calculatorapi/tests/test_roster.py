"""What a player owns: the /roster routes, the hand-edit rule and the import."""

import gzip
import json

from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.models import OwnedSupportCard, OwnedUma
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client, make_user
from calculatorapi.tests.test_pvp_teams import make_card, make_uma


class RosterFixture(CalculatorTestCase):

    def setUp(self):
        self.user = make_user("trainer")
        self.client, _ = auth_client(self.user)
        self.oguri = make_uma("Oguri Cap", 100601)
        self.maruzensky = make_uma("Maruzensky", 100401)
        self.kitasan = make_card("Kitasan Black", 30028)
        self.tachyon = make_card("Agnes Tachyon", 30034)

    def patch(self, **body):
        return self.client.patch("/roster", body, format="json")

    def run_import(self, client=None, **body):
        return (client or self.client).post("/roster/import", body, format="json")


class ModelTests(RosterFixture):

    def test_one_row_per_uma_and_per_card(self):
        OwnedUma.objects.create(user=self.user, uma=self.oguri, source="manual")
        with self.assertRaises(IntegrityError), transaction.atomic():
            OwnedUma.objects.create(user=self.user, uma=self.oguri, source="manual")
        OwnedSupportCard.objects.create(user=self.user, support_card=self.kitasan, source="manual")
        with self.assertRaises(IntegrityError), transaction.atomic():
            OwnedSupportCard.objects.create(user=self.user, support_card=self.kitasan, source="manual")

    def test_ranges(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OwnedUma.objects.create(user=self.user, uma=self.oguri, source="manual", stars=6)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OwnedSupportCard.objects.create(user=self.user, support_card=self.kitasan, source="manual", limit_break=5)


class EditTests(RosterFixture):

    def test_grid_edits_add_change_and_remove(self):
        body = self.patch(
            umas=[{"uma": self.oguri.id, "owned": True, "stars": 3}],
            support_cards=[{"support_card": self.kitasan.id, "owned": True, "limit_break": 2}],
        ).json()
        self.assertEqual(
            body["umas"],
            [{"uma": self.oguri.id, "stars": 3, "talent_level": None, "source": "manual", "obtained_at": None}],
        )
        self.assertEqual(body["support_cards"][0]["limit_break"], 2)

        self.patch(support_cards=[{"support_card": self.kitasan.id, "owned": True, "limit_break": 4}])
        self.assertEqual(OwnedSupportCard.objects.get().limit_break, 4)
        self.patch(umas=[{"uma": self.oguri.id, "owned": False}])
        self.assertFalse(OwnedUma.objects.exists())

    def test_marking_many_at_once_is_one_request(self):
        changes = [{"uma": uma.id, "owned": True} for uma in (self.oguri, self.maruzensky)]
        self.assertEqual(len(self.patch(umas=changes).json()["umas"]), 2)

    def test_a_refused_change_writes_nothing(self):
        response = self.patch(umas=[{"uma": self.oguri.id, "owned": True}, {"uma": 9999, "owned": True}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OwnedUma.objects.exists())

    def test_refusals(self):
        for body in [
            {"umas": [{"uma": self.oguri.id, "owned": True, "stars": 6}]},
            {"support_cards": [{"support_card": self.kitasan.id, "owned": True, "limit_break": 5}]},
            {"umas": [{"uma": self.oguri.id, "owned": True}, {"uma": self.oguri.id, "owned": False}]},
        ]:
            with self.subTest(body=body):
                self.assertEqual(self.patch(**body).status_code, 400)

    def test_guests_and_strangers(self):
        self.assertEqual(APIClient().get("/roster").status_code, 401)
        self.patch(umas=[{"uma": self.oguri.id, "owned": True}])
        rival, _ = auth_client(make_user("rival"))
        self.assertEqual(rival.get("/roster").json(), {"umas": [], "support_cards": []})


class ImportTests(RosterFixture):

    def items(self, lb=2, stars=3):
        return {
            "umas": [{"uma": self.oguri.id, "stars": stars, "talent_level": 5,
                      "game_data": {"card_id": 100601, "rarity": stars,
                                    "create_time": "2025-09-01 08:00:00", "viewer_id": 7}}],
            "support_cards": [{"support_card": self.kitasan.id, "limit_break": lb, "spare_copies": 1,
                               "game_data": {"support_card_id": 30028, "exp": 1200, "viewer_id": 7}}],
        }

    def test_an_import_adds_everything_and_keeps_the_raw_object(self):
        body = self.run_import(**self.items()).json()
        self.assertEqual(body["umas"], {"added": 1, "updated": 0, "removed": 0})
        uma = OwnedUma.objects.get()
        self.assertEqual((uma.stars, uma.talent_level, uma.source), (3, 5, "import"))
        self.assertEqual(uma.game_data["viewer_id"], 7)
        self.assertEqual(uma.obtained_at.isoformat(), "2025-09-01T08:00:00+00:00")
        card = OwnedSupportCard.objects.get()
        self.assertEqual((card.limit_break, card.spare_copies, card.game_data["exp"]), (2, 1, 1200))
        self.assertNotIn("game_data", json.dumps(body["roster"]))

    def test_a_re_import_updates_what_it_wrote(self):
        self.run_import(**self.items(lb=2))
        body = self.run_import(**self.items(lb=4)).json()
        self.assertEqual(body["support_cards"], {"added": 0, "updated": 1, "removed": 0})
        self.assertEqual(OwnedSupportCard.objects.get().limit_break, 4)

    def test_the_file_overwrites_hand_edits(self):
        self.run_import(**self.items(lb=2))
        self.patch(support_cards=[{"support_card": self.kitasan.id, "owned": True, "limit_break": 3}])
        self.run_import(**self.items(lb=4))
        card = OwnedSupportCard.objects.get()
        self.assertEqual((card.limit_break, card.source), (4, "import"))

    def test_what_the_file_lacks_is_removed(self):
        """Owner, 2026-10-26: if it doesn't exist in the file, it doesn't exist."""
        self.run_import(**self.items())
        self.patch(umas=[{"uma": self.maruzensky.id, "owned": True}])
        body = self.run_import(umas=self.items()["umas"], support_cards=[]).json()
        self.assertEqual(body["umas"]["removed"], 1)
        self.assertEqual(list(OwnedUma.objects.values_list("uma_id", flat=True)), [self.oguri.id])
        self.assertFalse(OwnedSupportCard.objects.exists())

    def test_a_list_not_sent_is_left_alone(self):
        self.run_import(**self.items())
        body = self.run_import(support_cards=[{"support_card": self.tachyon.id, "limit_break": 1}]).json()
        self.assertNotIn("umas", body)
        self.assertEqual(OwnedUma.objects.count(), 1)
        self.assertEqual(list(OwnedSupportCard.objects.values_list("support_card_id", flat=True)), [self.tachyon.id])

    def test_start_over_clears_one_list(self):
        self.run_import(**self.items())
        response = self.client.delete("/roster/umas")
        self.assertEqual(response.json()["umas"], [])
        self.assertEqual(OwnedSupportCard.objects.count(), 1)
        self.client.delete("/roster/support-cards")
        self.assertFalse(OwnedSupportCard.objects.exists())
        self.assertEqual(self.client.delete("/roster/everything").status_code, 404)

    def test_another_players_id_is_stripped(self):
        items = self.items()
        items["support_cards"][0]["game_data"]["owner_viewer_id"] = 99
        self.run_import(**items)
        self.assertNotIn("owner_viewer_id", OwnedSupportCard.objects.get().game_data)

    def test_a_gzipped_import_is_read(self):
        response = self.client.generic(
            "POST", "/roster/import", gzip.compress(json.dumps(self.items()).encode()),
            content_type="application/json", HTTP_CONTENT_ENCODING="gzip",
        )
        self.assertEqual(response.status_code, 200)  # pylint: disable=no-member
        self.assertEqual(OwnedUma.objects.count(), 1)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class LifecycleTests(RosterFixture):

    def test_roster_writes_keep_the_public_payload(self):
        APIClient().get("/calculator-data")
        self.patch(umas=[{"uma": self.oguri.id, "owned": True}])
        self.run_import(support_cards=[{"support_card": self.tachyon.id, "limit_break": 4}])
        self.assertIsNotNone(public_payload_cache.read(), "a roster save dropped /calculator-data")

    def test_deleting_the_account_deletes_the_roster(self):
        self.patch(
            umas=[{"uma": self.oguri.id, "owned": True}],
            support_cards=[{"support_card": self.kitasan.id, "owned": True}],
        )
        self.assertEqual(self.client.delete("/account").status_code, 204)
        self.assertFalse(OwnedUma.objects.exists())
        self.assertFalse(OwnedSupportCard.objects.exists())
