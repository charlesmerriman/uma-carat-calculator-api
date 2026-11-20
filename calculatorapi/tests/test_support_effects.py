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


UNIQUE_AMOUNTS = [{"type": 6, "name": "Guts Bonus", "value": 1, "format": "number", "condition": None,
                   "line": "Guts Bonus 1", "scope": "card"},
                  {"type": 12, "name": "Initial Guts", "value": 20, "format": "number", "condition": None,
                   "line": "Initial Guts 20", "scope": "card"}]


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
                         {"level": 30, "text": "Guts Bonus and Initial Guts", "translated": False,
                          "from_amounts": False, "amounts": UNIQUE_AMOUNTS, "multiplies": []})

    def test_a_jp_only_card_names_its_plain_typed_effects(self):
        jp_only = {"level": 30, "effects": [{"type": 6, "value": 1}, {"type": 12, "value": 20}]}
        self.assertEqual(support_effects.unique_effect(jp_only),
                         {"level": 30, "text": "Guts Bonus 1 and Initial Guts 20", "translated": False,
                          "from_amounts": True, "amounts": UNIQUE_AMOUNTS, "multiplies": []})

    def test_a_friendship_gauge_effect_is_decoded(self):
        """Type 101: `value` is the gauge threshold, `params` up to two (type, amount) pairs.
        Card 30053, whose game sentence is "Speed Bonus and Skill Point Bonus (Friendship Gauge 80+)"."""
        jp_style = {"level": 1, "effects": [{"type": 101, "value": 80, "params": [3, 1, 30, 1]}]}
        self.assertEqual(support_effects.unique_effect(jp_style), {
            "level": 1, "text": "Speed Bonus 1 and Skill Point Bonus 1 (Friendship Gauge 80+)", "translated": False,
            "from_amounts": True,
            "amounts": [
                {"type": 3, "name": "Speed Bonus", "value": 1, "format": "number", "condition": "Friendship Gauge 80+",
                 "line": "Speed Bonus 1", "scope": "card"},
                {"type": 30, "name": "Skill Point Bonus", "value": 1, "format": "number",
                 "condition": "Friendship Gauge 80+", "line": "Skill Point Bonus 1", "scope": "card"},
            ],
            "multiplies": [],
        })
        one_pair = {"level": 1, "effects": [{"type": 101, "value": 100, "params": [7, 3, 0, 0]}]}
        self.assertEqual(support_effects.unique_effect(one_pair)["text"], "Wit Bonus 3 (Friendship Gauge 100+)")

    def test_umatls_sentence_comes_after_the_games_and_before_the_named_line(self):
        """PR 3: the import puts UmaTL's sentence in `translated_text`; it shows only when the game
        has none of its own, and the page is told so (`translated`) to credit UmaTL."""
        gauge = [{"type": 101, "value": 80, "params": [3, 1, 0, 0]}]
        jp_only = {"level": 1, "effects": gauge, "translated_text": "Speed Bonus (Friendship 80+)"}
        page = support_effects.unique_effect(jp_only)
        self.assertEqual((page["text"], page["translated"]), ("Speed Bonus (Friendship 80+)", True))
        self.assertEqual([part["value"] for part in page["amounts"]], [1])
        both = {**jp_only, "text": "Speed Bonus (Friendship Gauge 80+)"}
        self.assertEqual(support_effects.unique_effect(both)["translated"], False)
        # An undecoded part (a type no reader knows) with UmaTL's sentence: the words show, no numbers.
        worded = {"level": 1, "effects": [{"type": 199, "value": 1, "params": [8, 4, 5, 0]}],
                  "translated_text": "Training bonus scaling with learnt speed skills, up to 5"}
        self.assertEqual(support_effects.unique_effect(worded)["amounts"], None)

    def test_a_plain_part_and_a_gauge_part_are_named_in_order(self):
        mixed = {"level": 1, "effects": [{"type": 15, "value": 5}, {"type": 101, "value": 80, "params": [8, 10, 0, 0]}]}
        self.assertEqual(support_effects.unique_effect(mixed)["text"],
                         "Race Bonus 5%, Training Effectiveness 10% (Friendship Gauge 80+)")

    def test_a_decoded_friendship_bonus_multiplies_by_its_number_not_its_words(self):
        gauge = {"level": 1, "effects": [{"type": 101, "value": 80, "params": [1, 10, 0, 0]}],
                 "text": "Bonus during friendship training"}
        self.assertEqual(support_effects.unique_effect(gauge, [{"type": 1, "levels": {"1": 25}}])["multiplies"],
                         ["Friendship Bonus"])

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
        conditional = {"level": 30, "effects": [{"type": 199, "value": 28, "params": [30, 0, 0, 0]}]}
        self.assertIsNone(support_effects.unique_effect(conditional))
        self.assertIsNone(support_effects.unique_effect(None))
        # A gauge pair naming an effect type the names file lacks is not decoded either.
        unknown = {"level": 1, "effects": [{"type": 101, "value": 80, "params": [99, 1, 0, 0]}]}
        self.assertIsNone(support_effects.unique_effect(unknown))

    def test_a_sentence_with_an_undecoded_part_keeps_its_words_and_sends_no_amounts(self):
        worded = {"level": 30, "effects": [{"type": 199, "value": 28, "params": [30, 0, 0, 0]}],
                  "text": "Training Energy Cost Reduction when Friendship Training occurs"}
        self.assertEqual(support_effects.unique_effect(worded)["amounts"], None)


class FriendshipGaugeCheckTests(SimpleTestCase):
    """The decoding of type 101, held against the game's own words: every effect it names, and
    the threshold, appear in the global client's sentence for that card, on every type-101 row
    in the committed snapshot (33 on 2026-11-14)."""

    def test_every_global_type_101_row_matches_the_games_sentence(self):
        rows = json.loads((SNAPSHOT / "support_effects.json").read_text(encoding="utf-8"))
        checked = 0
        for card in rows:
            unique = card["unique"]
            if not unique or not any(effect["type"] == 101 for effect in unique["effects"]):
                continue
            with self.subTest(card=card["id"]):
                page = support_effects.unique_effect(unique)
                self.assertIsNotNone(page["amounts"])
                for part in page["amounts"]:
                    self.assertIn(part["name"].lower(), unique["text"].lower())
                    self.assertIn(part["condition"].split()[-1].rstrip("+"), unique["text"])
            checked += 1
        self.assertGreaterEqual(checked, 33)


def slot(kind, value, params=()):
    """One stored unique-effect slot, as the import writes it."""
    return {"level": 1, "effects": [{"type": kind, "value": value, "params": list(params)}]}


class UniqueTypeTests(SimpleTestCase):
    """Types 102 to 122 and the effect types 33 and 41 (notes/unique-effects-plan.md): each reads
    as the plan's table says, from Daptrius's list and answers on 2026-11-16, and the card list's
    sort counts each at its most. Card ids are the game's."""

    # (card, stored slot, the line the page shows, what the sort counts)
    READINGS = [
        (30083, slot(102, 80, [20, 0, 0, 0]),
         "Training Effectiveness 20% on a training that isn't its type (Friendship Gauge 80+)", {8: 20}),
        (30085, slot(103, 5, [15, 0, 0, 0]), "Training Effectiveness 15% with 5 or more card types in the deck",
         {8: 15}),
        (30086, slot(104, 10000, [20, 0, 0, 0]), "Training Effectiveness 1% per 10,000 fans, up to 20%", {8: 20}),
        (30090, slot(105, 10, [2, 0, 0, 0]),
         "Initial stats +10 for each card in the deck, in that card's stat (Friend and Group cards: +2 to every "
         "stat)", {}),
        (30112, slot(106, 5, [1, 3, 0, 0]),
         "Friendship Bonus 3% per Friendship Training with this card, up to 15%", {1: 15}),
        (30094, slot(107, 1, [10, 30, 15, 5]),
         "Friendship Bonus 5% to 15%, more the lower your Energy (15% at 30 Energy or less)", {1: 15}),
        (30095, slot(108, 8, [100, 75, 5, 20]),
         "Training Effectiveness 5%, plus 0.75% per point of max Energy over 100, up to 20%", {8: 20}),
        (30099, slot(109, 8, [30, 0, 0, 0]),
         "Training Effectiveness 1% per 30 total Friendship Gauge in the deck, up to 20%", {8: 20}),
        (30102, slot(110, 8, [5, 0, 0, 0]),
         "Training Effectiveness 5% per support card on the training, itself included (5% to 25%)", {8: 25}),
        (30107, slot(111, 8, [5, 0, 0, 0]),
         "Training Effectiveness 5% per level of the training facility, up to 25% at level 5", {8: 25}),
        (30108, slot(112, 20), "20% chance a training's failure rate drops to 0%", {}),
        (30127, slot(113, 2, [60, 0, 0, 0]), "Mood Effect 60% on Friendship Training", {2: 60}),
        (30052, slot(113, 28, [30, 0, 0, 0]), "Energy Cost Reduction 30% on Friendship Training", {28: 30}),
        (30115, slot(114, 8, [5, 20, 0, 0]), "Training Effectiveness 5% to 20%, more the higher your Energy",
         {8: 20}),
        (30146, slot(115, 14, [5, 0, 0, 0]), "Initial Friendship Gauge 5 for every card in the deck", {}),
        (30154, slot(116, 3, [3, 1, 3, 0]), "Speed Bonus 1 per recovery skill learned, up to 3 skills", {3: 3}),
        (30175, slot(116, 2, [5, 1, 3, 0]), "Power Bonus 1 per acceleration skill learned, up to 3 skills", {5: 3}),
        (30223, slot(116, 1, [8, 4, 5, 0]),
         "Training Effectiveness 4% per speed skill learned, up to 5 skills", {8: 20}),
        (30148, slot(117, 8, [25, 20, 0, 0]),
         "Training Effectiveness 1% per level across all five facilities, up to 20%", {8: 20}),
        (30160, slot(118, 1, [60, 0, 0, 0]), "Can appear in 2 trainings at once (Friendship Gauge 60+)", {}),
        (30188, slot(119, 50, [1, 60, 0, 0]),
         "Specialty Priority 50 for every card in the deck (Friendship Gauge 60+)", {}),
        (30264, slot(120, 1, [80, 1, 2, 0]),
         "Stat bonus 1 for each card in the deck, in that card's stat, up to 2 per stat; Friend and Group cards "
         "give Skill Point Bonus (Friendship Gauge 80+)", {}),
        (30207, slot(121, 1, [2, 0, 0, 0]),
         "Every support gains 1 more Friendship Gauge from training, 2 when training with this card", {}),
        (30257, slot(122, 19, [60, 0, 0, 0]),
         "Specialty Priority 60 on the next turn for supports that train with this card", {}),
        (30298, slot(101, 80, [5, 1, 33, 1]), "Power Bonus 1 and Hint Levels Lv 1 (Friendship Gauge 80+)",
         {5: 1, 17: 1}),
        (30161, slot(101, 100, [30, 1, 41, 1]),
         "Skill Point Bonus 1 and Speed, Stamina, Power, Guts and Wit Bonus 1 (Friendship Gauge 100+)",
         {30: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1}),
    ]

    def test_each_type_reads_as_the_plan_says_and_sorts_at_its_most(self):
        for card, unique, line, sorted_values in self.READINGS:
            with self.subTest(card=card):
                page = support_effects.unique_effect(unique)
                self.assertEqual((page["text"], page["from_amounts"]), (line, True))
                self.assertEqual(support_effects.unique_parts(unique),
                                 {"level": 1, "values": sorted_values, "conditional": False})

    def test_a_deck_effect_is_listed_but_never_sorted_or_multiplied(self):
        """Daptrius, 2026-11-16, on Ryoka Tsurugi: list the amount, never say how it affects
        priority. The card's own Specialty Priority doesn't gain it, and no multiply line."""
        ryoka = slot(119, 50, [1, 60, 0, 0])
        page = support_effects.unique_effect(ryoka, [{"type": 19, "levels": {"1": 20}}])
        self.assertEqual(page["multiplies"], [])
        self.assertEqual(page["amounts"][0]["scope"], "deck")
        self.assertEqual(support_effects.unique_parts(ryoka)["values"], {})

    def test_a_stacking_friendship_bonus_multiplies_with_the_cards_own(self):
        twin_turbo = slot(106, 5, [1, 3, 0, 0])
        self.assertEqual(support_effects.unique_effect(twin_turbo, [{"type": 1, "levels": {"1": 25}}])["multiplies"],
                         ["Friendship Bonus"])

    def test_a_slot_naming_an_unknown_effect_or_skill_kind_is_not_read(self):
        self.assertIsNone(support_effects.unique_effect(slot(113, 99, [60, 0, 0, 0])))
        self.assertIsNone(support_effects.unique_effect(slot(116, 9, [3, 1, 3, 0])))
        self.assertEqual(support_effects.unique_parts(slot(116, 9, [3, 1, 3, 0]))["conditional"], True)

    def test_every_unique_in_both_snapshots_now_reads(self):
        """No card the game files hold keeps the sort's "+?" mark (2026-11-16: 563 JP cards and
        271 Global). A type the game adds later fails here first: give it a reader or list it."""
        for snapshot in (SNAPSHOT, SNAPSHOT.parent / "jp_snapshot"):
            for card in json.loads((snapshot / "support_effects.json").read_text(encoding="utf-8")):
                unique = card["unique"]
                if not unique or not unique["effects"]:
                    continue
                with self.subTest(snapshot=snapshot.name, card=card["id"]):
                    self.assertIsNotNone(support_effects.unique_amounts(unique))
                    self.assertFalse(support_effects.unique_parts(unique)["conditional"])

    def test_every_global_card_of_the_new_types_names_its_effect_in_the_games_sentence(self):
        """Like the type-101 check: what each reader says the card gives, and its gauge
        threshold, appear in the global client's own sentence for that card."""
        rows = json.loads((SNAPSHOT / "support_effects.json").read_text(encoding="utf-8"))
        checked = 0
        for card in rows:
            unique = card["unique"]
            if not unique or not any(102 <= effect["type"] <= 122 for effect in unique["effects"]):
                continue
            with self.subTest(card=card["id"]):
                amounts = support_effects.unique_amounts(unique)
                self.assertIsNotNone(amounts)
                for found in amounts or ():
                    self.assertIn(found["name"].lower(), unique["text"].lower())
                    if found["condition"]:
                        self.assertIn(found["condition"].split()[-1].rstrip("+"), unique["text"])
            checked += 1
        self.assertGreaterEqual(checked, 17)


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
                         {"level": 30, "text": "Guts Bonus and Initial Guts", "translated": False,
                          "from_amounts": False, "amounts": UNIQUE_AMOUNTS, "multiplies": []})
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
                                                {"type": 199, "value": 28, "params": [30, 0, 0, 0]}]}
        self.assertEqual(support_effects.unique_parts(conditional),
                         {"level": 25, "values": {15: 5}, "conditional": True})

    def test_a_decoded_gauge_part_counts_in_the_sort(self):
        """Owner, 2026-11-14: counted as if the gauge is reached, and no "+?" mark."""
        gauge = {"level": 1, "effects": [{"type": 101, "value": 80, "params": [8, 10, 30, 2]}]}
        self.assertEqual(support_effects.unique_parts(gauge),
                         {"level": 1, "values": {8: 10, 30: 2}, "conditional": False})
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

    def test_a_guest_reads_it_like_the_pages(self):
        # Open to everyone since 2026-11-18; self.client is signed out.
        self.assertEqual(self.client.get("/encyclopedia/card-effects").status_code, 200)
