"""Career objectives: the extractor's file, the import, the wording, and the uma page."""

import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from calculatorapi.career import goal_text, need_label, ordinal, shown_goals, turn_label
from calculatorapi.game_data_values import objective_values
from calculatorapi.models import Uma
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.test_skills_api import editor_client

SNAPSHOT = Path(__file__).resolve().parents[2] / "scripts" / "data" / "master_snapshot"

SPECIAL_WEEK_GOALS = [
    {"turn": 12, "kind": 1, "race": "Junior Make Debut", "grade": 900, "top": 0},
    {"turn": 27, "kind": 1, "race": "Kisaragi Sho", "grade": 300, "top": 5},
    {"turn": 34, "kind": 1, "race": "Tokyo Yushun (Japanese Derby)", "grade": 100, "top": 5},
    {"turn": 44, "kind": 1, "race": "Kikuka Sho", "grade": 100, "top": 3},
    {"turn": 56, "kind": 1, "race": "Tenno Sho (Spring)", "grade": 100, "top": 3},
    {"turn": 70, "kind": 1, "race": "Japan Cup", "grade": 100, "top": 2},
    {"turn": 72, "kind": 1, "race": "Arima Kinen", "grade": 100, "top": 1},
]


class TurnLabelTests(CalculatorTestCase):
    def test_every_boundary_of_the_three_years(self):
        cases = {
            1: "Junior Year, Early Jan", 2: "Junior Year, Late Jan", 3: "Junior Year, Early Feb",
            12: "Junior Year, Late Jun", 24: "Junior Year, Late Dec",
            25: "Classic Year, Early Jan", 27: "Classic Year, Early Feb", 48: "Classic Year, Late Dec",
            49: "Senior Year, Early Jan", 72: "Senior Year, Late Dec",
        }
        for turn, label in cases.items():
            self.assertEqual(turn_label(turn), label, turn)
        # Past the third year: the scenario's finals, never a goal shown.
        self.assertIsNone(turn_label(73))
        self.assertIsNone(turn_label(0))

    def test_the_placing_asked_for(self):
        self.assertEqual([need_label(top) for top in (0, 1, 2, 5)], ["Run it", "Win", "Top 2", "Top 5"])

    def test_a_goal_it_cannot_word_is_counted_not_guessed(self):
        goals = SPECIAL_WEEK_GOALS[:2] + [
            # A race goal with no English name for its race.
            {"turn": 69, "kind": 1, "condition_id": 20002, "value_1": 1, "value_2": 0},
            # Placings in a grade below G1: "that grade" or "that grade or better" isn't confirmed.
            {"turn": 49, "kind": 2, "condition_id": 300, "value_1": 3, "value_2": 4},
            {"turn": 25, "kind": 2, "condition_id": 700, "value_1": 1, "value_2": 2},
        ]
        rows, hidden = shown_goals(goals)
        self.assertEqual(rows, [
            {"turn": 12, "when": "Junior Year, Late Jun", "kind": "race", "race": "Junior Make Debut", "grade": None,
             "need": "Run it"},
            {"turn": 27, "when": "Classic Year, Early Feb", "kind": "race", "race": "Kisaragi Sho", "grade": "G3",
             "need": "Top 5"},
        ])
        self.assertEqual(hidden, 3)

    def test_oguri_caps_two_goals_that_are_not_one_race(self):
        """As Daptrius read them off the game (board, 2026-11-12): 3,000 fans by
        Junior Year's late December, and 3rd or better in 2 G1s by Senior Year's late June."""
        rows, hidden = shown_goals([
            {"turn": 24, "kind": 3, "condition_id": 0, "value_1": 3000, "value_2": 0},
            {"turn": 60, "kind": 2, "condition_id": 100, "value_1": 3, "value_2": 2},
        ])
        self.assertEqual(rows, [
            {"turn": 24, "when": "Junior Year, Late Dec", "kind": "fans", "race": "Get 3,000 fans", "grade": None,
             "need": ""},
            {"turn": 60, "when": "Senior Year, Late Jun", "kind": "placings",
             "race": "Place 3rd or better in 2 G1 races", "grade": None, "need": ""},
        ])
        self.assertEqual(hidden, 0)

    def test_the_wording_of_a_count_and_a_placing(self):
        self.assertEqual(goal_text({"kind": 3, "value_1": 10000}), "Get 10,000 fans")
        self.assertEqual(goal_text({"kind": 2, "condition_id": 100, "value_1": 5, "value_2": 1}),
                         "Place 5th or better in 1 G1 race")
        self.assertIsNone(goal_text({"kind": 2, "condition_id": 100, "value_1": 1, "value_2": 2}))
        self.assertIsNone(goal_text({"kind": 3, "value_1": 0}))
        self.assertEqual([ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)],
                         ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"])


class ObjectiveImportTests(CalculatorTestCase):
    def test_every_outfit_of_the_character_gets_her_goals(self):
        objectives = [{"chara_id": 1001, "goals": SPECIAL_WEEK_GOALS}]
        self.assertEqual(
            objective_values(objectives, {100101, 100102, 100201}),
            {100101: {"career_objectives": SPECIAL_WEEK_GOALS}, 100102: {"career_objectives": SPECIAL_WEEK_GOALS}},
        )

    def test_the_committed_file_has_special_weeks_seven_races(self):
        objectives = json.loads((SNAPSHOT / "objectives.json").read_text(encoding="utf-8"))
        special_week = next(row for row in objectives if row["chara_id"] == 1001)
        self.assertEqual(special_week["goals"], SPECIAL_WEEK_GOALS)

    def test_the_import_writes_them_and_a_rerun_writes_nothing(self):
        uma = Uma.objects.create(name="Special Week", game_id=100101)
        call_command("import_game_data", "--no-input", "--snapshot", str(SNAPSHOT), stdout=StringIO())
        uma.refresh_from_db()
        self.assertEqual(uma.career_objectives, SPECIAL_WEEK_GOALS)
        out = StringIO()
        call_command("import_game_data", "--no-input", "--snapshot", str(SNAPSHOT), stdout=out)
        self.assertIn("Nothing to write.", out.getvalue())


class UmaPageObjectivesTests(CalculatorTestCase):
    def test_the_page_sends_them_worded(self):
        Uma.objects.create(name="Special Week", game_id=100101, image="umas/100101.png",
                           career_objectives=SPECIAL_WEEK_GOALS + [{"turn": 30, "kind": 3, "value_1": 5000}])
        body = editor_client().get("/encyclopedia/umas/100101").json()
        self.assertEqual([(row["when"], row["race"], row["grade"], row["need"]) for row in body["objectives"]][1:3], [
            ("Classic Year, Early Feb", "Kisaragi Sho", "G3", "Top 5"),
            ("Classic Year, Late May", "Tokyo Yushun (Japanese Derby)", "G1", "Top 5"),
        ])
        # Her seven races and the fan count, nothing left out.
        self.assertEqual((len(body["objectives"]), body["objectives_hidden"]), (8, 0))
        self.assertEqual([row["race"] for row in body["objectives"] if row["kind"] == "fans"], ["Get 5,000 fans"])
