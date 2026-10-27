"""GET /pvp-rankings: who may read it, and the rows it sends (views/pvp_rankings.py)."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from calculatorapi.models import (
    PvpDeck, PvpDeckSlot, PvpGuide, PvpUmaRecommendation, SupportCard, Uma,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client, make_champions_meeting, make_league_of_heroes, make_user,
)

URL = "/pvp-rankings"


class RankingsFixture(CalculatorTestCase):
    def setUp(self):
        self.cm = make_champions_meeting(name="Aquarius Cup", cm_number=22)
        self.loh = make_league_of_heroes(name="LoH 2")
        self.kitasan = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
        self.fine = SupportCard.objects.create(name="Fine Motion", card_type="wit", rarity=3)
        self.any_speed = SupportCard.objects.create(
            name="SSR Speed", card_type="speed", rarity=3, is_placeholder=True,
        )
        self.maru = Uma.objects.create(name="Maruzensky")

        self.guide = PvpGuide.objects.create(champions_meeting=self.cm, is_published=True)
        deck = PvpDeck.objects.create(guide=self.guide, style="front", label=" whale ")
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=self.kitasan)
        PvpDeckSlot.objects.create(deck=deck, slot=1, order=1, support_card=self.fine)
        PvpDeckSlot.objects.create(deck=deck, slot=2, note="Any Wit")
        PvpDeckSlot.objects.create(deck=deck, slot=3, support_card=self.any_speed)
        PvpDeckSlot.objects.create(deck=deck, slot=6, is_borrow=True, support_card=self.fine)
        mention = PvpDeck.objects.create(guide=self.guide, style="pace", label="Honorable mention", order=1)
        PvpDeckSlot.objects.create(deck=mention, slot=1, support_card=self.kitasan)
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=self.maru, stars=3)

        draft = PvpGuide.objects.create(league_of_heroes=self.loh, is_published=False)
        hidden = PvpDeck.objects.create(guide=draft, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=hidden, slot=1, support_card=self.kitasan)


class AccessTests(RankingsFixture):
    def test_a_guest_is_asked_to_sign_in(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_a_plain_account_is_refused(self):
        client, _ = auth_client(make_user("plain"))
        response = client.get(URL)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("guides", response.json())

    def test_staff_and_a_granted_account_may_read(self):
        granted = make_user("tester")
        granted.early_access = True
        granted.save(update_fields=["early_access"])
        for user in (make_user("staff", is_staff=True), granted):
            client, _ = auth_client(user)
            self.assertEqual(client.get(URL).status_code, 200, user.username)


class PayloadTests(RankingsFixture):
    def setUp(self):
        super().setUp()
        self.client, _ = auth_client(make_user("staff", is_staff=True))

    def test_published_guides_only_with_their_rows(self):
        body = self.client.get(URL).json()
        self.assertEqual(len(body["guides"]), 1)
        guide = body["guides"][0]
        self.assertEqual((guide["kind"], guide["event"]), ("cm", self.cm.id))
        self.assertEqual(guide["cards"], [
            {"card": self.kitasan.id, "style": "front", "deck": " whale ", "tier": "whale", "role": "main"},
            {"card": self.fine.id, "style": "front", "deck": " whale ", "tier": "whale", "role": "alt"},
            {"card": self.fine.id, "style": "front", "deck": " whale ", "tier": "whale", "role": "borrow"},
            {"card": self.kitasan.id, "style": "pace", "deck": "Honorable mention", "tier": "other", "role": "main"},
        ])
        self.assertEqual(guide["umas"], [{"uma": self.maru.id, "style": "front", "stars": 3}])

    def test_gzipped_when_asked(self):
        response = self.client.get(URL, HTTP_ACCEPT_ENCODING="gzip, deflate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Encoding"], "gzip")

    def test_no_placeholder_and_no_note_only_slot(self):
        cards = self.client.get(URL).json()["guides"][0]["cards"]
        self.assertNotIn(self.any_speed.id, [row["card"] for row in cards])
        self.assertTrue(all(row["card"] for row in cards))

    def test_three_queries_whatever_the_guide_count(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(URL).status_code, 200)
            return len(queries)

        before = count()
        cm = make_champions_meeting(name="Piscis Cup", cm_number=23)
        guide = PvpGuide.objects.create(champions_meeting=cm, is_published=True)
        deck = PvpDeck.objects.create(guide=guide, style="end", label="Beginner")
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=self.kitasan)
        PvpUmaRecommendation.objects.create(guide=guide, style="end", uma=self.maru, stars=1)
        self.assertEqual(count(), before)
