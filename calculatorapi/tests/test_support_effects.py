"""A support card's effects at any level (calculatorapi/support_effects.py), and their import."""

import json
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from calculatorapi import support_effects
from calculatorapi.game_data_values import effect_values
from calculatorapi.models import SupportCard
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.test_skills_api import editor_client

SNAPSHOT = Path(__file__).resolve().parents[2] / "scripts" / "data" / "master_snapshot"

# Special Week's SSR [The Brightest Star in Japan!] (30001), as the game's table states it.
BRIGHTEST_STAR = [
    {"type": 1, "levels": {"1": 10, "25": 15, "30": 15, "45": 20}},
    {"type": 2, "levels": {"1": 10, "30": 40, "50": 60}},
    {"type": 5, "levels": {"35": 1}},
    {"type": 8, "levels": {"45": 5, "50": 10}},
    {"type": 14, "levels": {"10": 10, "25": 20, "30": 20, "45": 25}},
    {"type": 17, "levels": {"10": 1, "25": 2, "30": 2, "45": 2}},
    {"type": 18, "levels": {"10": 5, "25": 30, "30": 30, "45": 40}},
    {"type": 19, "levels": {"15": 5, "25": 20, "30": 20, "45": 35}},
]
UNIQUE = {"level": 30, "effects": [{"type": 6, "value": 1}, {"type": 12, "value": 20}],
          "text": "Guts Bonus and Initial Guts"}


def levels_of(effect_type):
    return next(effect["levels"] for effect in BRIGHTEST_STAR if effect["type"] == effect_type)


class ValueAtTests(SimpleTestCase):
    def test_the_card_daptrius_checked_cell_for_cell(self):
        """His five levels, every one between named ones (board, 2026-11-12)."""
        checked = {
            1: (16, 17, 18, 18, 19),    # Friendship Bonus
            2: (45, 46, 50, 51, 52),    # Mood Effect
            14: (21, 22, 23, 23, 24),   # Initial Friendship Gauge
            18: (33, 34, 36, 37, 38),   # Hint Frequency
            19: (25, 26, 30, 31, 32),   # Specialty Priority
            17: (2, 2, 2, 2, 2),        # Hint Levels
            5: (1, 1, 1, 1, 1),         # Power Bonus, first named at 35
            8: (None,) * 5,             # Training Effectiveness: "Unlocked at level 45"
        }
        for effect_type, expected in checked.items():
            got = tuple(support_effects.value_at(levels_of(effect_type), level) for level in (35, 36, 40, 41, 42))
            self.assertEqual(got, expected, effect_type)

    def test_the_plans_worked_example_at_every_limit_break(self):
        at_caps = {
            1: (15, 16, 18, 20, 20),
            2: (40, 45, 50, 55, 60),
            5: (None, 1, 1, 1, 1),
            8: (None, None, None, 5, 10),
            14: (20, 21, 23, 25, 25),
            17: (2, 2, 2, 2, 2),
            18: (30, 33, 36, 40, 40),
            19: (20, 25, 30, 35, 35),
        }
        for effect_type, expected in at_caps.items():
            got = tuple(support_effects.value_at(levels_of(effect_type), level) for level in support_effects.caps(3))
            self.assertEqual(got, expected, effect_type)

    def test_nothing_before_the_first_named_level_and_flat_after_the_last(self):
        gauge = levels_of(14)
        self.assertIsNone(support_effects.value_at(gauge, 9))
        self.assertEqual(support_effects.value_at(gauge, 10), 10)
        self.assertEqual(support_effects.value_at(gauge, 50), 25)
        self.assertIsNone(support_effects.value_at({}, 30))

    def test_a_named_level_reads_exactly(self):
        for level, value in (("1", 10), ("25", 15), ("30", 15), ("45", 20)):
            self.assertEqual(support_effects.value_at(levels_of(1), int(level)), value)

    def test_the_caps_by_rarity(self):
        self.assertEqual(support_effects.caps(1), (20, 25, 30, 35, 40))
        self.assertEqual(support_effects.caps(2), (25, 30, 35, 40, 45))
        self.assertEqual(support_effects.caps(3), (30, 35, 40, 45, 50))
        self.assertEqual(support_effects.caps(None), ())


class TableTests(SimpleTestCase):
    def test_rows_in_the_order_the_card_gains_them_with_a_value_per_level(self):
        rows = support_effects.table(BRIGHTEST_STAR, 3)
        self.assertEqual([row["name"] for row in rows], [
            "Friendship Bonus", "Mood Effect", "Initial Friendship Gauge", "Hint Levels", "Hint Frequency",
            "Specialty Priority", "Power Bonus", "Training Effectiveness",
        ])
        friendship = rows[0]
        self.assertEqual((friendship["format"], friendship["unlock"], len(friendship["values"])), ("percent", 1, 50))
        self.assertEqual(friendship["help"], "Increases the effectiveness of Friendship Training")
        self.assertEqual(friendship["values"][34], 16)   # level 35
        power = rows[6]
        self.assertEqual((power["unlock"], power["values"][33], power["values"][34]), (35, None, 1))

    def test_an_sr_stops_at_its_own_top_level(self):
        rows = support_effects.table([{"type": 1, "levels": {"1": 5, "45": 20, "50": 30}}], 2)
        self.assertEqual(len(rows[0]["values"]), 45)

    def test_a_type_the_file_does_not_name_is_left_out_never_shown_bare(self):
        self.assertEqual(support_effects.table([{"type": 99, "levels": {"1": 5}}], 3), [])
        self.assertEqual(support_effects.table(BRIGHTEST_STAR, None), [])

    def test_only_confirmed_formats_carry_a_unit(self):
        """Seven formats are confirmed from Daptrius's shots and nine from his list
        (2026-11-13); every other type is a plain number."""
        types = support_effects.load_types()
        self.assertEqual(
            {key: row["format"] for key, row in types.items() if row["format_confirmed"]},
            {
                # His shots of Special Week's SSR.
                1: "percent", 2: "percent", 18: "percent", 17: "level", 5: "number", 14: "number", 19: "number",
                # His list: Training Effectiveness, Race Bonus, Fan Bonus, Event Recovery, Event
                # Effectiveness, Failure Protection, Energy Cost Reduction; then Skill Point Bonus
                # and Wit Friendship Recovery.
                8: "percent", 15: "percent", 16: "percent", 25: "percent", 26: "percent", 27: "percent",
                28: "percent", 30: "number", 31: "number",
            },
        )
        self.assertTrue(all(row["format"] == "number" for row in types.values() if not row["format_confirmed"]))
        # Minigame Effectiveness: he has never seen it, so it stays a bare number.
        self.assertEqual((types[29]["name"], types[29]["format"]), ("Minigame Effectiveness", "number"))

    def test_the_committed_names_are_the_snapshots(self):
        """The API's file is the game's vocabulary: a client update that adds a type fails here until it is added."""
        extracted = json.loads((SNAPSHOT / "support_effect_types.json").read_text(encoding="utf-8"))
        types = support_effects.load_types()
        self.assertEqual(
            [(row["type"], row["name"], row["help"]) for row in extracted],
            [(key, row["name"], row["help"]) for key, row in sorted(types.items())],
        )


class UniqueEffectTests(SimpleTestCase):
    def test_the_games_own_sentence_where_global_has_one(self):
        self.assertEqual(support_effects.unique_effect(UNIQUE),
                         {"level": 30, "text": "Guts Bonus and Initial Guts", "multiplies": []})

    def test_a_jp_only_card_names_its_plain_typed_effects(self):
        jp_only = {"level": 30, "effects": [{"type": 6, "value": 1}, {"type": 12, "value": 20}]}
        self.assertEqual(support_effects.unique_effect(jp_only),
                         {"level": 30, "text": "Guts Bonus 1, Initial Guts 20", "multiplies": []})

    def test_a_unique_friendship_bonus_or_specialty_priority_multiplies_with_the_cards_own(self):
        """Daptrius, 2026-11-13: Specialty Priority 35 with a unique 40 acts like (100 + 35) x 1.4,
        not 35 + 40. The page is told which of the two the unique effect names, and only when the
        card has its own to multiply with."""
        unique = {"level": 30, "effects": [], "text": "Gain Specialty Priority (40) when the bond gauge is at least 80"}
        own = [{"type": 19, "levels": {"1": 35}}, {"type": 1, "levels": {"1": 10}}]
        self.assertEqual(support_effects.unique_effect(unique, own)["multiplies"], ["Specialty Priority"])
        self.assertEqual(support_effects.unique_effect(unique, [{"type": 1, "levels": {"1": 10}}])["multiplies"], [])
        typed = {"level": 30, "effects": [{"type": 1, "value": 10}, {"type": 19, "value": 20}]}
        self.assertEqual(support_effects.unique_effect(typed, own)["multiplies"],
                         ["Friendship Bonus", "Specialty Priority"])

    def test_a_conditional_effect_nobody_decoded_is_not_worded(self):
        conditional = {"level": 30, "effects": [{"type": 101, "value": 80, "params": [3, 1, 30, 1]}]}
        self.assertIsNone(support_effects.unique_effect(conditional))
        self.assertIsNone(support_effects.unique_effect(None))


class EffectImportTests(SimpleTestCase):
    def test_global_wins_and_jp_fills_the_rest(self):
        global_rows = [{"id": 30001, "effects": BRIGHTEST_STAR, "unique": UNIQUE}]
        jp_rows = [
            {"id": 30001, "effects": [{"type": 1, "levels": {"1": 1}}], "unique": {"level": 30, "effects": []}},
            {"id": 30300, "effects": [{"type": 3, "levels": {"1": 1}}], "unique": None},
        ]
        values = effect_values(global_rows, jp_rows, {30001, 30300, 30999})
        self.assertEqual(values[30001], {"effects": BRIGHTEST_STAR, "unique_effect": UNIQUE})
        self.assertEqual(values[30300], {"effects": [{"type": 3, "levels": {"1": 1}}], "unique_effect": None})
        # A card neither snapshot has effects for is left alone.
        self.assertNotIn(30999, values)


# The pages build image URLs from stored names. CI has no bucket configured, so
# the S3 storage raises there; plain file storage answers anywhere.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class CardPageEffectsTests(CalculatorTestCase):
    def test_the_page_sends_the_table_the_caps_and_the_unique_effect(self):
        SupportCard.objects.create(
            name="Special Week", title="[The Brightest Star in Japan!]", game_id=30001, rarity=3, card_type="guts",
            image="support_cards/30001.png", effects=BRIGHTEST_STAR, unique_effect=UNIQUE,
        )
        body = editor_client().get("/encyclopedia/cards/30001").json()
        self.assertEqual(body["caps"], [30, 35, 40, 45, 50])
        self.assertEqual(body["unique_effect"],
                         {"level": 30, "text": "Guts Bonus and Initial Guts", "multiplies": []})
        self.assertEqual(len(body["effects"]), 8)
        hint = next(row for row in body["effects"] if row["name"] == "Hint Frequency")
        self.assertEqual([hint["values"][level - 1] for level in (30, 35, 40, 45, 50)], [30, 33, 36, 40, 40])

    def test_a_card_with_no_effects_sends_empty_lists(self):
        SupportCard.objects.create(name="Bare", game_id=30777, rarity=3, card_type="speed", image="support_cards/x.png")
        body = editor_client().get("/encyclopedia/cards/30777").json()
        self.assertEqual((body["effects"], body["caps"], body["unique_effect"]), ([], [], None))


class EffectSortTests(SimpleTestCase):
    """The card list's effect sort reads these (notes/card-effect-sort-plan.md)."""

    def test_values_at_each_limit_break_cap(self):
        found = support_effects.at_caps(BRIGHTEST_STAR, 3)
        self.assertEqual(found[18], [30, 33, 36, 40, 40])  # Hint Frequency
        self.assertEqual(found[8], [None, None, None, 5, 10])  # Training Effectiveness unlocks at 45
        self.assertEqual(support_effects.at_caps(BRIGHTEST_STAR, None), {})

    def test_the_uniques_plain_parts_and_whether_any_part_was_left_out(self):
        self.assertEqual(support_effects.unique_parts(UNIQUE),
                         {"level": 30, "values": {6: 1, 12: 20}, "conditional": False})
        conditional = {"level": 25, "effects": [{"type": 15, "value": 5},
                                                {"type": 101, "value": 3, "params": [1, 2]}]}
        self.assertEqual(support_effects.unique_parts(conditional),
                         {"level": 25, "values": {15: 5}, "conditional": True})
        self.assertIsNone(support_effects.unique_parts(None))


class CardEffectsRouteTests(CalculatorTestCase):
    def test_every_card_keyed_by_pk_with_the_types_any_card_has(self):
        card = SupportCard.objects.create(
            name="Special Week", title="[The Brightest Star in Japan!]", game_id=30001, rarity=3, card_type="guts",
            image="support_cards/30001.png", effects=BRIGHTEST_STAR, unique_effect=UNIQUE,
        )
        SupportCard.objects.create(name="Bare", game_id=30777, rarity=3, card_type="speed", image="support_cards/x.png")
        body = editor_client().get("/encyclopedia/card-effects").json()
        self.assertEqual(list(body["cards"]), [str(card.id)])  # a card with no effects isn't sent
        row = body["cards"][str(card.id)]
        self.assertEqual(row["caps"], [30, 35, 40, 45, 50])
        self.assertEqual(row["effects"]["19"], [20, 25, 30, 35, 35])  # Specialty Priority
        self.assertEqual(row["unique"], {"level": 30, "values": {"6": 1, "12": 20}, "conditional": False})
        self.assertEqual([entry["type"] for entry in body["types"]], [1, 2, 5, 8, 14, 17, 18, 19])

    def test_supporters_first_like_the_pages(self):
        self.assertEqual(self.client.get("/encyclopedia/card-effects").status_code, 401)
