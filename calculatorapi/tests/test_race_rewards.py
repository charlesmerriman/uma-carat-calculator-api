"""CM and LoH payouts that change from a later event on (models/race_rank_reward.py).

The server only stores and serves the rows; which row an event pays is the
client's job (frontend `raceRewardAmount`), like the rest of the projection.
"""
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.models import (
    ChampionsMeetingRank,
    ChampionsMeetingRankReward,
    CustomUser,
    LeagueOfHeroesRank,
    LeagueOfHeroesRankReward,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES


def _calculator_data(client):
    res = client.get("/calculator-data")
    assert res.status_code == 200, res.status_code
    return res.json()


class RaceRankRewardApiTests(CalculatorTestCase):
    def setUp(self):
        self.client = APIClient()
        self.champion = ChampionsMeetingRank.objects.create(name="Champion", income_amount=3300)
        self.plat4 = LeagueOfHeroesRank.objects.create(name="Platinum 4", income_amount=3450)

    def test_calculator_data_carries_each_rank_s_rewards_in_event_order(self):
        # Entered out of order, served ascending: the client walks them in order.
        LeagueOfHeroesRankReward.objects.create(rank=self.plat4, from_event_number=6, income_amount=3900)
        LeagueOfHeroesRankReward.objects.create(rank=self.plat4, from_event_number=1, income_amount=2450)
        LeagueOfHeroesRankReward.objects.create(rank=self.plat4, from_event_number=2, income_amount=3450)
        ChampionsMeetingRankReward.objects.create(rank=self.champion, from_event_number=30, income_amount=3800)

        data = _calculator_data(self.client)
        loh = next(r for r in data["league_of_heroes_rank_data"] if r["name"] == "Platinum 4")
        cm = next(r for r in data["champions_meeting_rank_data"] if r["name"] == "Champion")
        self.assertEqual(
            loh["rewards"],
            [
                {"from_event_number": 1, "income_amount": 2450},
                {"from_event_number": 2, "income_amount": 3450},
                {"from_event_number": 6, "income_amount": 3900},
            ],
        )
        self.assertEqual(cm["rewards"], [{"from_event_number": 30, "income_amount": 3800}])
        # The rank's own amount is still sent: it pays every event before the first row.
        self.assertEqual(cm["income_amount"], 3300)

    def test_a_rank_with_no_rows_sends_an_empty_list(self):
        data = _calculator_data(self.client)
        cm = next(r for r in data["champions_meeting_rank_data"] if r["name"] == "Champion")
        self.assertEqual(cm["rewards"], [])

    def test_rewards_cost_one_query_per_kind_however_many_ranks(self):
        # prefetch_related, not a query per rank: count the payload's queries
        # with one rank of each kind, then with three more, and compare.
        def count():
            # A rebuild every time, never the cached payload.
            public_payload_cache.invalidate()
            with CaptureQueriesContext(connection) as ctx:
                _calculator_data(self.client)
            return len(ctx.captured_queries)

        count()  # Warm up: the first build does one-time work of its own.
        before = count()
        for n in range(3):
            rank = ChampionsMeetingRank.objects.create(name=f"Extra {n}", income_amount=n)
            ChampionsMeetingRankReward.objects.create(rank=rank, from_event_number=30, income_amount=n)
            rank = LeagueOfHeroesRank.objects.create(name=f"Extra {n}", income_amount=n)
            LeagueOfHeroesRankReward.objects.create(rank=rank, from_event_number=6, income_amount=n)
        self.assertEqual(count(), before)

    def test_public_rank_endpoints_carry_the_rewards_too(self):
        ChampionsMeetingRankReward.objects.create(rank=self.champion, from_event_number=30, income_amount=3800)
        res = self.client.get("/championsmeetingranks")
        champion = next(r for r in res.json() if r["name"] == "Champion")
        self.assertEqual(champion["rewards"], [{"from_event_number": 30, "income_amount": 3800}])

    def test_one_row_per_rank_and_event_number(self):
        ChampionsMeetingRankReward.objects.create(rank=self.champion, from_event_number=30, income_amount=3800)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ChampionsMeetingRankReward.objects.create(
                rank=self.champion, from_event_number=30, income_amount=4000
            )

    def test_rows_go_with_their_rank(self):
        LeagueOfHeroesRankReward.objects.create(rank=self.plat4, from_event_number=6, income_amount=3900)
        self.plat4.delete()
        self.assertFalse(LeagueOfHeroesRankReward.objects.exists())


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class RaceRankRewardAdminTests(CalculatorTestCase):
    """The rows are edited inline on the rank's own page."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = CustomUser.objects.create_superuser(username="boss", password="x")

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_each_rank_page_shows_its_rewards_inline(self):
        cm = ChampionsMeetingRank.objects.create(name="Champion", income_amount=3300)
        loh = LeagueOfHeroesRank.objects.create(name="Platinum 4", income_amount=3450)
        for name, rank, related in (
            ("championsmeetingrank", cm, "from a later meeting"),
            ("leagueofheroesrank", loh, "from a later league"),
        ):
            with self.subTest(model=name):
                res = self.client.get(reverse(f"admin:calculatorapi_{name}_change", args=[rank.pk]))
                self.assertEqual(res.status_code, 200)
                self.assertContains(res, related, msg_prefix=name, status_code=200)
