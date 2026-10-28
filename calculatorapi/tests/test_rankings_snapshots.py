"""Rank movement (rankings_snapshots.py): the start-of-week snapshot each
payload compares with, what `previous` carries, and the Monday movers post."""

import datetime
from unittest import mock

from django.test import override_settings

from calculatorapi.models import RankingsSnapshot, SupportCard
from calculatorapi.public_payload_cache import affects_public_payload
from calculatorapi.rankings_snapshots import attach_previous, climbers, ranked_lists, week_start
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import auth_client, make_champions_meeting, make_user

UTC = datetime.timezone.utc
# A Wednesday, then the next Monday after midnight, then that Friday.
WEDNESDAY = datetime.datetime(2026, 10, 21, 12, tzinfo=UTC)
NEXT_MONDAY = datetime.datetime(2026, 10, 26, 0, 30, tzinfo=UTC)
NEXT_FRIDAY = datetime.datetime(2026, 10, 30, 9, tzinfo=UTC)


def guides(*decks):
    """A guides payload: one guide whose decks hold the given cards (main role)."""
    cards = [{"card": card, "deck": deck, "style": "pace", "tier": "standard", "role": "main"}
             for deck, held in enumerate(decks) for card in held]
    guide = {"kind": "cm", "event": 1, "scenario": None, "cards": cards, "umas": []}
    return {"generated_at": "x", "scenarios": [], "guides": [guide]}


def favourites(stamp, *umas):
    """A players payload whose favourites rank `umas` in order."""
    return {"generated_at": stamp, "favourites": [{"uma": uma, "people": 10 - index} for index, uma in enumerate(umas)]}


class SnapshotTests(CalculatorTestCase):
    """The snapshot is written on the first build, kept within the week, and
    replaced by the first build after Monday 00:00 UTC."""

    def test_written_on_the_first_build_with_nothing_to_compare(self):
        payload = attach_previous("players", favourites("a", 1), WEDNESDAY)
        self.assertIsNone(payload["previous"])
        self.assertEqual(RankingsSnapshot.objects.get(name="players").generated_at, WEDNESDAY)

    def test_kept_within_the_week_and_replaced_after_monday(self):
        attach_previous("players", favourites("a", 1, 2), WEDNESDAY)
        later = favourites("b", 2, 1)
        # Thursday: the Wednesday snapshot stands, in rank order.
        same_week = attach_previous("players", later, WEDNESDAY + datetime.timedelta(days=1))
        self.assertEqual(same_week["previous"], {"generated_at": "a", "favourites": [1, 2]})
        # The next Monday: replaced, so the week starts again from here.
        attach_previous("players", later, NEXT_MONDAY)
        self.assertEqual(RankingsSnapshot.objects.get(name="players").generated_at, NEXT_MONDAY)
        friday = attach_previous("players", later, NEXT_FRIDAY)
        self.assertEqual(friday["previous"]["favourites"], [2, 1])

    def test_guides_send_their_rows_so_the_client_counts_them_under_its_filters(self):
        old = guides([10, 11], [10])
        attach_previous("guides", old, WEDNESDAY)
        payload = attach_previous("guides", guides([11], [11]), WEDNESDAY + datetime.timedelta(hours=2))
        self.assertEqual(payload["previous"]["guides"], old["guides"])

    def test_a_snapshot_write_leaves_the_calculator_data_cache_alone(self):
        self.assertFalse(affects_public_payload(RankingsSnapshot))

    def test_week_start_is_monday_midnight_utc(self):
        self.assertEqual(week_start(WEDNESDAY), datetime.datetime(2026, 10, 19, tzinfo=UTC))
        self.assertEqual(week_start(NEXT_MONDAY), datetime.datetime(2026, 10, 26, tzinfo=UTC))


class RankedListsTests(CalculatorTestCase):
    """`previous` ranks as the client does."""

    def test_guides_overall_counts_decks_alternatives_out(self):
        payload = guides([10, 11], [11], [12])
        payload["guides"][0]["cards"].append(
            {"card": 12, "deck": 0, "style": "pace", "tier": "standard", "role": "alt"}
        )
        self.assertEqual(ranked_lists("guides", payload), {"cards": [11, 10, 12]})

    def test_pvp_ranks_win_rates_only_from_a_hundred_races(self):
        payload = {
            "built": {"umas": [{"uma": 1}, {"uma": 2}], "cards": [{"card": 7}]},
            "runs": {"rows": [
                {"uma": 1, "style": "pace", "races": 400, "wins": 100},
                {"uma": 2, "style": "end", "races": 120, "wins": 60},
                {"uma": 3, "style": "front", "races": 50, "wins": 40},
            ]},
        }
        self.assertEqual(ranked_lists("pvp:cm:1", payload), {"umas": [1, 2], "cards": [7], "runs": ["2-end", "1-pace"]})

    def test_climbers_into_the_top_ten_biggest_first(self):
        before = ["a", "b", "c", "d", "e"]
        after = ["d", "a", "e", "b", "c"]
        self.assertEqual(climbers(before, after), [("d", 4, 1), ("e", 5, 3)])


@override_settings(DISCORD_RANKINGS_CHANNEL_ID="555")
class MoversPostTests(CalculatorTestCase):
    """The week's climbers go to the team's channel once per list and week."""

    def setUp(self):
        self.cards = [
            SupportCard.objects.create(name=name, card_type="speed", rarity=3) for name in ("Kitasan", "Fine", "Light")
        ]

    def rotate(self, send):
        kitasan, fine, light = (card.id for card in self.cards)
        attach_previous("guides", guides([kitasan, fine, light], [kitasan, fine], [kitasan]), WEDNESDAY)
        attach_previous("guides", guides([light, fine, kitasan], [light, fine], [light]), NEXT_MONDAY)
        return send

    @mock.patch("calculatorapi.rankings_snapshots.discord_messages.send")
    def test_sends_the_climbers_under_last_weeks_key(self, send):
        self.rotate(send)
        send.assert_called_once()
        key, channel = send.call_args.args
        self.assertEqual((key, channel), ("rankings-movers:2026-W43:guides", "555"))
        self.assertIn("**Light**: #3 to #1", send.call_args.kwargs["embed"]["description"])

    @mock.patch("calculatorapi.rankings_snapshots.discord_messages.send")
    def test_nothing_within_the_week_and_nothing_for_a_list_not_worth_posting(self, send):
        attach_previous("guides", guides([1]), WEDNESDAY)
        attach_previous("guides", guides([2], [2]), WEDNESDAY + datetime.timedelta(days=2))
        attach_previous("players", favourites("a", 1, 2), WEDNESDAY)
        attach_previous("players", favourites("b", 2, 1), NEXT_MONDAY)
        send.assert_not_called()

    @override_settings(DISCORD_RANKINGS_CHANNEL_ID="")
    @mock.patch("calculatorapi.rankings_snapshots.discord_messages.send")
    def test_nothing_without_a_channel(self, send):
        self.rotate(send)
        send.assert_not_called()

    @override_settings(DISCORD_BOT_TOKEN="test-token")
    @mock.patch("calculatorapi.discord_messages._call", return_value={"id": "1"})
    def test_the_key_posts_once_even_if_the_week_rotates_again(self, call):
        with self.captureOnCommitCallbacks(execute=True):
            self.rotate(None)
        # A second rotation for the same week (a snapshot written back by hand, say) posts nothing.
        RankingsSnapshot.objects.filter(name="guides").update(generated_at=WEDNESDAY)
        with self.captureOnCommitCallbacks(execute=True):
            attach_previous("guides", guides([self.cards[2].id], [self.cards[1].id, self.cards[2].id]), NEXT_MONDAY)
        self.assertEqual(call.call_count, 1)


class PayloadPreviousTests(CalculatorTestCase):
    """Every Rankings route carries `previous` (null on the first build)."""

    def test_the_pvp_route_sends_previous(self):
        cm = make_champions_meeting(name="Libra Cup", cm_number=21)
        client, _ = auth_client(make_user("staff", is_staff=True))
        body = client.get(f"/rankings/pvp/cm/{cm.id}").json()
        self.assertIn("previous", body)
        self.assertIsNone(body["previous"])
        self.assertTrue(RankingsSnapshot.objects.filter(name=f"pvp:cm:{cm.id}").exists())
