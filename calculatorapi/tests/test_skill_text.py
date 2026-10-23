"""
The readable conditions and effects (skill_text.py): each phrase family, the
raw fallback for what isn't verified, and two guards over every committed skill.
"""

import json
from types import SimpleNamespace

from django.test import override_settings
from django.urls import reverse

from calculatorapi import skill_text
from calculatorapi.models import CustomUser, Skill, Uma, UmaSkill
from calculatorapi.skill_conditions import racecourse_names
from calculatorapi.skill_text import TextContext, describe, describe_condition
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.tests.test_skills import SNAPSHOT_DIR

CONTEXT = TextContext(racecourses={10006: "Tokyo"}, skill_names={202051: "Professor of Curvature"},
                      characters={1002: "Silence Suzuka"})


def when(condition):
    return describe_condition(condition, CONTEXT)


def texts(phrases):
    return [item["text"] for item in phrases]


def skill(effects=None, condition="", precondition="", activate_lot=None):
    return SimpleNamespace(effects=effects or [], condition=condition, precondition=precondition,
                           activate_lot=activate_lot)


def slot(abilities, condition="always==1", duration=50000, cooldown=5000000):
    return {"precondition": "", "condition": condition, "duration": duration,
            "cooldown": cooldown, "abilities": abilities}


def ability(kind, value, target_type=1, target_value=0, value_usage=1):
    return {"type": kind, "value": value, "value_usage": value_usage, "value_level_usage": 1,
            "additional_activate_type": 0, "target_type": target_type,
            "target_value": target_value}


class ConditionTextTests(CalculatorTestCase):
    def test_shared_clauses_are_pulled_out_of_the_alternatives(self):
        # Xceleration's condition, verbatim.
        described = when(
            "order>=3&order_rate<=50&remain_distance<=200&bashin_diff_infront<=1"
            "@order>=3&order_rate<=50&remain_distance<=200&bashin_diff_behind<=1"
        )
        self.assertEqual(texts(described["all"]), [
            "In 3rd place or lower", "In the front 50% of the field", "200 m or less to go",
        ])
        self.assertEqual([texts(alt) for alt in described["any"]], [
            ["Within 1 length of the runner ahead"], ["The runner behind within 1 length"],
        ])

    def test_an_alternative_that_only_narrows_collapses(self):
        # (A and B) or A is A.
        self.assertEqual(when("phase==1&order==1@phase==1"), {"all": when("phase==1")["all"],
                                                              "any": []})

    def test_the_final_corner_and_straight_read_as_places(self):
        self.assertEqual(texts(when("is_finalcorner==1&corner==0")["all"]), ["On the final straight"])
        self.assertEqual(texts(when("is_finalcorner==1&corner!=0")["all"]), ["On the final corner"])
        self.assertEqual(texts(when("is_finalcorner==1")["all"]), ["On the final corner or later"])

    def test_phases(self):
        cases = {
            "phase==0": "Early-race", "phase==1": "Mid-race", "phase>=2": "Late-race or later",
            "phase==3": "In the last spurt phase", "phase<=1": "Early-race or mid-race",
            "phase_random==1": "At a random moment mid-race",
            "phase_laterhalf_random==1": "At a random moment in the second half of mid-race",
            "distance_rate_after_random==50": "At a random moment in the second half of the race",
        }
        for condition, expected in cases.items():
            self.assertEqual(texts(when(condition)["all"]), [expected], condition)

    def test_positions_and_distances(self):
        cases = {
            "order==1": "In the lead", "order>=1": None, "order<=5": "In 5th place or better",
            "order>=2&order<=4": "Between 2nd and 4th place",
            "order_rate>50": "Behind the front 50% of the field",
            "order_rate>=40&order_rate<=70": "Between 40% and 70% back in the field",
            "remain_distance<=201&remain_distance>=199": "At about 200 m to go",
            "remain_distance>=600": "600 m or more to go",
            "distance_rate>=50": "In the second half of the race",
            "distance_rate>=50&distance_rate<=60": "Between 50% and 60% of the race",
            "course_distance==2400": "On a 2,400 m course",
        }
        for condition, expected in cases.items():
            self.assertEqual(texts(when(condition)["all"]), [expected] if expected else [], condition)

    def test_the_race_itself(self):
        cases = {
            "distance_type==3": "In Medium races", "distance_type!=3": "Not in Medium races",
            "running_style==1": "As a Front Runner", "ground_type==2": "On dirt",
            "ground_condition==4": "On a heavy track", "season==5": "In spring",
            "weather==3": "In rainy weather", "rotation==1": "On a right-handed track",
            "slope==2": "On a downhill", "track_id==10006": "At Tokyo Racecourse",
            "time==4": "At night", "is_exist_chara_id==1002": "When Silence Suzuka is in the race",
            "is_used_skill_id==202051": "After using Professor of Curvature",
            "running_style_count_nige_otherself>=1": "With another Front Runner in the race",
        }
        for condition, expected in cases.items():
            self.assertEqual(texts(when(condition)["all"]), [expected], condition)

    def test_what_is_not_verified_stays_the_games_code(self):
        described = when("is_overtake==1&order==2")
        self.assertEqual(described["all"], [
            {"text": "is_overtake==1", "raw": True}, {"text": "In 2nd place", "raw": False},
        ])
        # A racecourse global has no skill for, so no name: raw too.
        self.assertTrue(when("track_id==10999")["all"][0]["raw"])
        # Not the game's grammar at all: the whole string, raw.
        self.assertEqual(when("phase==1&order>>2"), {
            "all": [{"text": "phase==1&order>>2", "raw": True}], "any": [],
        })

    def test_a_precondition_reads_as_only_after(self):
        # V Is for Victory!
        described = describe(skill(effects=[{
            "precondition": "is_finalcorner==1&blocked_side_continuetime>=2",
            "condition": "is_finalcorner==1&corner==0&order<=5",
            "duration": 50000, "cooldown": 5000000, "abilities": [ability(27, 2500)],
        }]), CONTEXT)
        [part] = described["slots"]
        self.assertEqual(texts(part["after"]["all"]),
                         ["On the final corner or later", "Blocked at the side for 2 s or more"])
        self.assertEqual(texts(part["when"]["all"]), ["On the final straight", "In 5th place or better"])


class EffectTextTests(CalculatorTestCase):
    def effects(self, *abilities):
        return texts(describe(skill(effects=[slot(list(abilities))]), CONTEXT)["slots"][0]["effects"])

    def test_the_verified_kinds_with_their_units(self):
        self.assertEqual(self.effects(
            ability(27, 3500), ability(31, 2000), ability(1, 600000), ability(9, 550),
            ability(21, -1500, target_type=18, target_value=1), ability(8, 100000),
        ), [
            "Velocity +0.35 m/s", "Acceleration +0.2 m/s²", "Speed +60", "Recovers 5.5% endurance",
            "Current speed -0.15 m/s (Front Runners)", "Field of view +10",
        ])

    def test_targets(self):
        self.assertEqual(self.effects(
            ability(9, -50, target_type=9, target_value=18),
            ability(21, -500, target_type=10, target_value=2),
            ability(21, -500, target_type=9, target_value=1),
            ability(1, 400000, target_type=22, target_value=1002),
        ), [
            "Drains 0.5% endurance (runners ahead)", "Current speed -0.05 m/s (up to 2 runners behind)",
            "Current speed -0.05 m/s (the runner ahead)", "Speed +40 (Silence Suzuka)",
        ])

    def test_unconfirmed_units_give_the_direction_only(self):
        self.assertEqual(self.effects(ability(10, 4000)), ["Less time lost to a slow start"])
        # The other direction was never seen, so it isn't worded: raw.
        effect = describe(skill(effects=[slot([ability(10, -4000)])]), CONTEXT)["slots"][0]["effects"][0]
        self.assertTrue(effect["raw"])

    def test_unknown_kinds_and_targets_stay_raw(self):
        described = describe(skill(effects=[slot([ability(42, 10000), ability(27, 1000, target_type=2)])]),
                             CONTEXT)
        self.assertTrue(all(item["raw"] for item in described["slots"][0]["effects"]))
        self.assertFalse(described["complete"])

    def test_a_scaling_value_says_so(self):
        self.assertEqual(self.effects(ability(27, 3500, value_usage=3)), ["Velocity +0.35 m/s (varies)"])

    def test_duration_cooldown_and_wit_check(self):
        passive = describe(skill(effects=[slot([ability(1, 400000)], duration=-1, cooldown=0)],
                                 activate_lot=0), CONTEXT)
        self.assertEqual((passive["slots"][0]["duration"], passive["slots"][0]["cooldown"]),
                         ("Whole race", None))
        self.assertIs(passive["wit_check"], False)
        timed = describe(skill(effects=[slot([ability(27, 3500)], duration=24000, cooldown=300000)],
                               activate_lot=1), CONTEXT)
        self.assertEqual((timed["slots"][0]["duration"], timed["slots"][0]["cooldown"]),
                         ("2.4 s", "30 s"))
        self.assertIs(timed["wit_check"], True)
        self.assertIsNone(describe(skill(), CONTEXT)["wit_check"])

    def test_a_skill_without_effects_still_reads_its_condition(self):
        described = describe(skill(condition="phase==1"), CONTEXT)
        self.assertEqual(texts(described["slots"][0]["when"]["all"]), ["Mid-race"])
        self.assertEqual(described["slots"][0]["effects"], [])


class CommittedSkillTextTests(CalculatorTestCase):
    """Every skill global has, read through the tables."""

    @classmethod
    def setUpTestData(cls):
        rows = json.loads((SNAPSHOT_DIR / "skills.json").read_text(encoding="utf-8"))
        # Racecourse names as the site finds them: off the racecourse skills.
        context = TextContext(racecourses=racecourse_names(
            SimpleNamespace(name=row["name"], condition=row["condition_1"]) for row in rows
        ))
        cls.described = [
            describe(skill(
                effects=[{"precondition": row.get(f"precondition_{n}", ""),
                          "condition": row.get(f"condition_{n}", ""), **part}
                         for n, part in enumerate(row["effects"], start=1)],
                activate_lot=row["activate_lot"],
            ), context)
            for row in rows
        ]

    def phrases(self):
        for described in self.described:
            for part in described["slots"]:
                for condition in (part["after"], part["when"]):
                    yield from condition["all"]
                    for alternative in condition["any"]:
                        yield from alternative
                yield from part["effects"]

    def test_no_phrase_has_an_em_dash(self):
        # The copy rule (.claude/rules/copy.md) holds for generated text too.
        self.assertFalse([item["text"] for item in self.phrases() if "—" in item["text"]])

    def test_most_skills_read_in_full(self):
        # A floor, not a target: 79% on 2026-10-23. A table edit that breaks a
        # common variable drops far below it.
        complete = sum(described["complete"] for described in self.described)
        self.assertGreaterEqual(complete / len(self.described), 0.75)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class SkillAdminTextTests(CalculatorTestCase):
    def test_the_change_page_shows_the_text_and_who_gets_it(self):
        base = Skill.objects.create(game_id=201351, name="Gourmand", rarity=2, group_id=20135, tier=2)
        evolved = Skill.objects.create(
            game_id=100101111, name="Gluttonous Ruler", rarity=6, group_id=10010111, tier=1,
            availability="announced", evolves_from=base, activate_lot=1,
            effects=[slot([ability(9, 550), ability(27, 1500)], condition="phase_random==1")],
        )
        week = Uma.objects.create(name="Special Week", game_id=100101)
        UmaSkill.objects.create(uma=week, skill=evolved, source="evolved", level=3)
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))

        page = self.client.get(reverse("admin:calculatorapi_skill_change", args=[evolved.pk]))

        self.assertContains(page, "At a random moment mid-race")
        self.assertContains(page, "Recovers 5.5% endurance; Velocity +0.15 m/s")
        self.assertContains(page, "Rolls against Wit before it fires.")
        self.assertContains(page, "Special Week</a> (evolved, level 3)")
        self.assertContains(page, "Evolves from Gourmand")

    def test_context_from_the_database_names_characters_and_racecourses(self):
        Skill.objects.create(game_id=200191, name="Tokyo Racecourse ◎", rarity=1, group_id=20019,
                             tier=2, condition="track_id==10006")
        Uma.objects.create(name="Silence Suzuka (Summer)", game_id=100202)
        context = skill_text.context_from_database()
        self.assertEqual(context.racecourses, {10006: "Tokyo"})
        self.assertEqual(context.characters[1002], "Silence Suzuka")
        self.assertEqual(context.skill_names[200191], "Tokyo Racecourse ◎")
