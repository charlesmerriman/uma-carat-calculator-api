"""
Where on a course a skill can fire (skill_zones.py; notes/course-map-plan.md,
"The zones module", PR 6): the table's every row on Chukyo turf 1200 m, the
merge of alternatives, the gates, the no-guessing rule, a test that every
variable the global skills use is classified, and the endpoint with its cache.
"""

import json
import re
from pathlib import Path

from rest_framework.test import APIClient

from calculatorapi.models import RaceCourse, Skill
from unittest import mock

from calculatorapi import skill_zones
from calculatorapi.skill_zones import classify, zones_for
from calculatorapi.tests.base import CalculatorTestCase

SNAPSHOT = Path(__file__).resolve().parents[2] / "scripts" / "data" / "master_snapshot" / "skills.json"

# Chukyo turf 1200 m (course 10701), the plan's worked example.
CHUKYO = RaceCourse(
    region="jp", game_id=10701, racecourse="Chukyo", track_id=10007, surface="turf", distance=1200,
    direction="left",
    corners=[{"start": 300, "length": 250, "number": 3}, {"start": 550, "length": 238, "number": 4}],
    straights=[{"start": 0, "length": 300, "front": 2}, {"start": 788, "length": 412, "front": 1}],
    slopes=[{"start": 100, "length": 775, "slope": -10000}, {"start": 875, "length": 100, "slope": 20000}],
)


def stretches(condition, course=CHUKYO):
    result = zones_for(condition, course)
    return [(zone["start"], zone["end"], zone["kind"]) for zone in result["zones"]]


class ZoneTableTests(CalculatorTestCase):
    def test_every_row_of_the_table_on_chukyo_1200(self):
        rows = {
            "phase==0": [(0, 200, "fixed")],
            "phase>=2": [(800, 1200, "fixed")],
            "phase_random==2": [(800, 1000, "random")],
            "phase_laterhalf_random==1": [(500, 800, "random")],
            "corner==4": [(550, 788, "fixed")],
            "corner==0": [(0, 300, "fixed"), (788, 1200, "fixed")],
            "corner!=0": [(300, 788, "fixed")],
            "all_corner_random==1": [(300, 788, "random")],
            # Corner skills: a random point on the last corner with that
            # number (Daptrius, 2026-11-03). Swinging Maestro lists all four.
            "corner_random==3": [(300, 550, "random")],
            "corner_random==1@corner_random==2@corner_random==3@corner_random==4": [(300, 788, "random")],
            "is_finalcorner==1": [(550, 1200, "fixed")],
            "is_finalcorner_random==1": [(550, 788, "random")],
            "is_finalcorner_laterhalf==1": [(669, 788, "fixed")],
            "straight_random==1": [(0, 300, "random"), (788, 1200, "random")],
            "is_last_straight==1": [(788, 1200, "fixed")],
            "last_straight_random==1": [(788, 1200, "random")],
            "is_lastspurt==1": [(800, 1200, "fixed")],
            "distance_rate>=50": [(600, 1200, "fixed")],
            "remain_distance<=200": [(1000, 1200, "fixed")],
            "slope==1": [(875, 975, "fixed")],
            "slope==2": [(100, 875, "fixed")],
            "up_slope_random==1": [(875, 975, "random")],
            "always==1": [(0, 0, "point")],
            # Triple 7s: the point with 777 m left.
            "remain_distance<=778&remain_distance>=776": [(422, 424, "fixed")],
            # A random point that must also be in a fixed stretch: random, in both.
            "phase>=2&all_corner_random==1": [],
        }
        for condition, expected in rows.items():
            with self.subTest(condition=condition):
                if condition == "phase>=2&all_corner_random==1":
                    # Late-race starts at 800, after the last corner ends (788): never.
                    self.assertEqual(zones_for(condition, CHUKYO)["kind"], "never")
                else:
                    self.assertEqual(stretches(condition), expected)

    def test_alternatives_merge(self):
        self.assertEqual(stretches("phase==0@phase==3"), [(0, 200, "fixed"), (1000, 1200, "fixed")])
        self.assertEqual(stretches("corner==3@corner==4"), [(300, 788, "fixed")])


class GateTests(CalculatorTestCase):
    def test_a_course_gate_that_fails_makes_the_skill_never(self):
        self.assertEqual(zones_for("track_id==10006", CHUKYO)["kind"], "never")
        self.assertEqual(zones_for("rotation==1&phase==1", CHUKYO)["kind"], "never")
        self.assertEqual(zones_for("track_id==10006@phase==3", CHUKYO)["kind"], "place")

    def test_a_course_gate_that_passes_narrows_nothing(self):
        self.assertEqual(zones_for("rotation==2", CHUKYO)["kind"], "passive")
        self.assertEqual(stretches("track_id==10007&phase==1"), [(200, 800, "fixed")])
        self.assertEqual(zones_for("distance_type==1&is_basis_distance==1", CHUKYO)["kind"], "passive")

    def test_the_runners_style_comes_back_as_styles(self):
        self.assertEqual(zones_for("running_style==1&phase==1", CHUKYO)["styles"], ["front"])
        self.assertEqual(zones_for("running_style==3@running_style==4", CHUKYO)["styles"], ["late", "end"])
        self.assertIsNone(zones_for("phase==1", CHUKYO)["styles"])

    def test_event_gates_narrow_nothing(self):
        self.assertEqual(zones_for("season==1", CHUKYO)["kind"], "passive")
        self.assertEqual(stretches("weather==3&phase==2"), [(800, 1000, "fixed")])

    def test_a_hill_skill_on_a_flat_course_is_never(self):
        fields = {f.attname: getattr(CHUKYO, f.attname) for f in RaceCourse._meta.concrete_fields}
        flat = RaceCourse(**{**fields, "slopes": []})
        self.assertEqual(zones_for("slope==1", flat)["kind"], "never")


class NoGuessingTests(CalculatorTestCase):
    def test_what_is_not_a_place_or_not_confirmed_reads_anywhere(self):
        for condition in ("order>=3", "straight_front_type==1", "hp_per<=50", "not a condition"):
            with self.subTest(condition=condition):
                self.assertEqual(zones_for(condition, CHUKYO)["kind"], "anywhere")

    def test_an_unconfirmed_clause_beside_a_place_narrows_nothing(self):
        with mock.patch.object(skill_zones, "UNCONFIRMED", frozenset({"mystery_place"})):
            self.assertEqual(stretches("phase==2&mystery_place==2"), [(800, 1000, "fixed")])

    def test_the_backstretch_is_a_place(self):
        # Victory Cheer! ("on the backstretch late-race"): Chukyo's opening
        # straight is its backstretch (`front` 2), which the late race never
        # reaches; its home straight is not one. ==1 has no rule (Global
        # never uses it), so it narrows nothing.
        self.assertEqual(stretches("straight_front_type==2"), [(0, 300, "fixed")])
        self.assertEqual(zones_for("phase==2&straight_front_type==2", CHUKYO)["kind"], "never")
        self.assertEqual(zones_for("straight_front_type==1", CHUKYO)["kind"], "anywhere")

    def test_an_announced_skill_s_variable_is_classified_too(self):
        # The snapshot above holds Global's skills only; an announced skill
        # (Magical☆Marvelous★World) brought `visiblehorse`, which the skill
        # pass found unclassified. It is race state, never a place.
        self.assertEqual(classify("visiblehorse"), "not-a-place")
        self.assertEqual(stretches("distance_rate>=40&distance_rate<=50&visiblehorse>=4"), [(480, 600, "fixed")])

    def test_every_variable_the_global_skills_use_is_classified(self):
        # A skills import that brings a new variable (JP's is_last_straight,
        # say) fails here before a player sees a zone built on a guess.
        skills = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        variables = set()
        for skill in skills:
            for key in ("condition_1", "condition_2", "precondition_1", "precondition_2"):
                variables |= set(re.findall(r"([a-z_0-9]+)\s*(?:==|!=|>=|<=|>|<)", skill.get(key) or ""))
        unknown = sorted(variable for variable in variables if classify(variable) is None)
        self.assertEqual(unknown, [])


class SkillZonesEndpointTests(CalculatorTestCase):
    def setUp(self):
        self.course = RaceCourse.objects.create(**{
            f.attname: getattr(CHUKYO, f.attname) for f in RaceCourse._meta.concrete_fields if f.attname != "id"
        })
        values = {"description": "Does a thing.", "rarity": 1, "tier": 1, "icon_id": 10011,
                  "availability": "global"}
        self.speed = Skill.objects.create(game_id=200011, name="Corner Speed", group_id=20001,
                                          condition="corner!=0", **values)

    def url(self, course_id=None):
        return f"/race-courses/{course_id or self.course.id}/skill-zones"

    def test_the_map_has_every_global_skill(self):
        body = APIClient().get(self.url()).json()
        self.assertEqual(body[str(self.speed.id)]["zones"], [{"start": 300, "end": 788, "kind": "fixed"}])

    def test_a_hidden_or_missing_course_is_a_404(self):
        RaceCourse.objects.filter(pk=self.course.id).update(show_map=False)
        self.assertEqual(APIClient().get(self.url()).status_code, 404)
        self.assertEqual(APIClient().get(self.url(99999)).status_code, 404)

    def test_a_skill_edit_drops_the_cached_map(self):
        APIClient().get(self.url())
        self.speed.condition = "phase==0"
        self.speed.save()
        body = APIClient().get(self.url()).json()
        self.assertEqual(body[str(self.speed.id)]["zones"], [{"start": 0, "end": 200, "kind": "fixed"}])


class CornerSkillsOverLapsTests(CalculatorTestCase):
    def test_a_corner_skill_uses_the_last_lap_s_corner(self):
        # A two-lap course numbers its corners 1 to 4 twice (Nakayama 3600):
        # corner_random==1 is a random point on the second corner 1, "the
        # last time a corner appears in the race" (Daptrius, 2026-11-03).
        two_laps = RaceCourse(
            region="jp", game_id=10507, racecourse="Nakayama", track_id=10005, surface="turf",
            distance=3600, direction="right",
            corners=[{"start": 290, "length": 250, "number": 1}, {"start": 1975, "length": 250, "number": 1}],
            straights=[], slopes=[],
        )
        self.assertEqual(stretches("corner_random==1", two_laps), [(1975, 2225, "random")])
