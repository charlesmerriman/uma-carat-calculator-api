"""The condition and effect tags the Skills page filters on (skill_tags.py)."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from calculatorapi.skill_tags import condition_tags, effect_tags, skill_tags


def skill(*slots):
    return SimpleNamespace(effects=list(slots), precondition="", condition="")


def slot(condition, abilities=(), precondition=""):
    return {"precondition": precondition, "condition": condition, "abilities": list(abilities)}


def ability(kind, value, target_type=1):
    return {"type": kind, "value": value, "target_type": target_type}


class ConditionTagTests(SimpleTestCase):

    def test_phases_places_and_the_race(self):
        cases = {
            "phase==1": {"mid"},
            "phase>=2": {"late", "spurt"},
            "phase_random==0": {"early"},
            "phase_corner_random==1": {"mid", "corner"},
            "is_lastspurt==1": {"spurt"},
            "corner==0": {"straight"},
            "corner!=0": {"corner"},
            "corner==3": {"corner"},
            "is_finalcorner==1&corner==0": {"final-straight"},
            "is_finalcorner==1&corner!=0": {"final-corner", "corner"},
            "is_finalcorner==1": {"final-corner"},
            "is_last_straight==1": {"final-straight"},
            "slope==1": {"uphill"},
            "down_slope_random==1": {"downhill"},
            "distance_type==2": {"mile"},
            "distance_type!=3": {"sprint", "mile", "long"},
            "ground_type==2": {"dirt"},
            "running_style==1@running_style==4": {"front-runner", "end-closer"},
        }
        for condition, expected in cases.items():
            with self.subTest(condition=condition):
                self.assertEqual(condition_tags(condition), expected)

    def test_what_isnt_a_restriction_or_isnt_confirmed_gives_nothing(self):
        # All four phases is no restriction; an unconfirmed variable is no tag.
        self.assertEqual(condition_tags("phase>=0"), set())
        self.assertEqual(condition_tags("behind_near_lane_time_set1>=1"), set())
        self.assertEqual(condition_tags(""), set())
        self.assertEqual(condition_tags("phase==1&order>>2"), set())  # not the game's grammar


class EffectTagTests(SimpleTestCase):

    def test_what_a_skill_does_for_herself(self):
        self.assertEqual(effect_tags(ability(27, 1500)), {"speed"})
        self.assertEqual(effect_tags(ability(31, 2000)), {"accel"})
        self.assertEqual(effect_tags(ability(9, 550)), {"recovery"})
        self.assertEqual(effect_tags(ability(3, 400000)), {"stats"})

    def test_lowering_another_runner_is_a_debuff(self):
        self.assertEqual(effect_tags(ability(22, -1500, target_type=9)), {"debuff"})
        self.assertEqual(effect_tags(ability(9, -100, target_type=4)), {"debuff"})
        # Rushed runners take longer to calm down: a positive number, still a debuff.
        self.assertEqual(effect_tags(ability(13, 50000, target_type=21)), {"debuff"})
        # A boost aimed at a named runner is no debuff.
        self.assertEqual(effect_tags(ability(3, 400000, target_type=22)), set())
        # Draining herself is no recovery, and nothing else either.
        self.assertEqual(effect_tags(ability(9, -100)), set())


class SkillTagTests(SimpleTestCase):

    def test_every_slot_precondition_and_ability_counts(self):
        tags = skill_tags(skill(
            slot("phase==2&running_style==1", [ability(27, 1500)], precondition="ground_type==1"),
            slot("is_last_straight==1", [ability(31, 2000)]),
        ))
        self.assertEqual(tags, ["accel", "final-straight", "front-runner", "late", "speed", "turf"])

    def test_a_skill_without_effects_still_reads_its_condition(self):
        self.assertEqual(skill_tags(SimpleNamespace(effects=[], precondition="", condition="phase==1")), ["mid"])
