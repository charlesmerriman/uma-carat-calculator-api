"""Career objectives: the extractor's file, the import, the wording, and the uma page."""

import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from calculatorapi.career import need_label, shown_goals, turn_label
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

    def test_only_race_goals_are_worded_and_the_rest_counted(self):
        goals = SPECIAL_WEEK_GOALS[:2] + [
            {"turn": 24, "kind": 3, "condition_id": 0, "value_1": 3000, "value_2": 0},
            {"turn": 69, "kind": 1, "condition_id": 20002, "value_1": 1, "value_2": 0},
        ]
        rows, hidden = shown_goals(goals)
        self.assertEqual(rows, [
            {"turn": 12, "when": "Junior Year, Late Jun", "race": "Junior Make Debut", "grade": None, "need": "Run it"},
            {"turn": 27, "when": "Classic Year, Early Feb", "race": "Kisaragi Sho", "grade": "G3", "need": "Top 5"},
        ])
        self.assertEqual(hidden, 2)


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
        self.assertEqual((len(body["objectives"]), body["objectives_hidden"]), (7, 1))
