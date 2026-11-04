"""
A verdict per skill on a course (skill_verdicts.py; notes/course-map-plan-2.md,
section 2): what a skill does, where it fires against the late-race start, the
heal and carry-over shares, repeats and the words it needs, with Daptrius's
answers of 2026-11-03 in the numbers.
"""


from rest_framework.test import APIClient

from calculatorapi import skill_verdicts
from calculatorapi.models import RaceCourse, Skill
from calculatorapi.skill_conditions import parse_condition
from calculatorapi.skill_text import TextContext
from calculatorapi.skill_verdicts import (
    carry_window, chain_for, effect_kinds, mid_race_speed, needs_for, proc_window, repeats_for, verdict_for,
)
from calculatorapi.skill_zones import CourseFacts, zones_for
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.test_skill_zones import CHUKYO

# Kyoto turf 3200 m (CM 24): two laps of corners, the second 1 to 4.
KYOTO_3200 = RaceCourse(
    region="jp", game_id=10811, racecourse="Kyoto", track_id=10008, surface="turf", distance=3200,
    direction="right",
    corners=[
        {"start": 458, "length": 250, "number": 3}, {"start": 708, "length": 250, "number": 4},
        {"start": 1450, "length": 200, "number": 1}, {"start": 1650, "length": 200, "number": 2},
        {"start": 2300, "length": 250, "number": 3}, {"start": 2550, "length": 247, "number": 4},
    ],
    straights=[{"start": 0, "length": 458, "front": 1}, {"start": 958, "length": 492, "front": 2},
               {"start": 1850, "length": 450, "front": 2}, {"start": 2797, "length": 403, "front": 1}],
    slopes=[],
)


def slot(kind, value=2000, duration=30000, cooldown=5_000_000, target=1):
    return {"duration": duration, "cooldown": cooldown,
            "abilities": [{"type": kind, "value": value, "target_type": target}]}


ACCEL = [slot(31)]
SPEED = [slot(27, duration=24000)]
HEAL = [slot(9, value=550, duration=0)]
MAESTRO = "corner_random==1@corner_random==2@corner_random==3@corner_random==4"


def verdict(condition, effects, course=CHUKYO):
    once = skill_verdicts.is_once_per_race(effects)
    entry = zones_for(condition, course, once_per_race=once)
    return {**entry, **verdict_for(condition, effects, course, entry, TextContext())}


class EffectTests(CalculatorTestCase):
    def test_what_a_skill_does_and_to_whom(self):
        self.assertEqual(effect_kinds(ACCEL), ["accel"])
        self.assertEqual(effect_kinds(SPEED), ["speed"])
        self.assertEqual(effect_kinds(HEAL), ["heal"])
        self.assertEqual(effect_kinds([slot(9, value=-100, target=9)]), ["debuff"])
        self.assertEqual(effect_kinds([slot(31), slot(27)]), ["accel", "speed"])
        # A track green (a stat for the whole race) does none of these.
        self.assertEqual(effect_kinds([slot(1, value=400000, duration=-1)]), [])


class SpeedTests(CalculatorTestCase):
    def test_the_mid_race_speed(self):
        # 20.8 m/s base on 1200 m, times the style's coefficient plus the
        # average section randomness at 1200 Wit (about 0.13%).
        self.assertAlmostEqual(mid_race_speed(1200, ["end"]), 20.8 * 1.0012864, places=3)
        self.assertAlmostEqual(mid_race_speed(1200, ["front"]), 20.8 * 0.9812864, places=3)
        # Any style: the four styles' mean, 0.99225.
        self.assertAlmostEqual(mid_race_speed(1200), 20.8 * 0.9935364, places=3)


class SpurtVerdictTests(CalculatorTestCase):
    """The plan's CM 21 table: the late race starts at 800 m."""

    def test_the_window_a_good_accel_lands_in(self):
        # 3 s stretched x1.2 is 3.6 s; 0.5 s must be left in the late race, so
        # it may fire 3.1 s early, about 64 m at 20.67 m/s; and up to 50 m late.
        start, end = proc_window(ACCEL, 1200)
        self.assertEqual((round(start), end), (736, 850))
        # Without a duration there is no window.
        self.assertIsNone(proc_window([slot(31, duration=0)], 1200))

    def test_the_cm_21_table(self):
        # Final Push (front, 20.41 m/s): the window opens at 736.7 m, so 51.3
        # of the final corner's 238 m land well; `useful` is that share times
        # 0.2 m/s² for 3 s, 0.6 m/s.
        final_push = verdict("running_style==1&is_finalcorner_random==1&order==1", ACCEL)
        self.assertEqual(final_push["spread"], [550, 788])
        self.assertEqual(final_push["spurt"], {"from": -250, "to": -12, "odds": 0.22, "useful": 0.129})
        self.assertEqual(final_push["needs"], ["In the lead"])

        nsm = verdict("infront_near_lane_time>=1&is_lastspurt==1&hp_per>=1", ACCEL)
        self.assertEqual((nsm["fires_at"], nsm["spurt"]), (800, {"from": 0, "to": 0, "odds": 1.0, "useful": 0.6}))

        # 800 to 900 m, and only the first 50 m land well.
        neck = verdict("running_style==2&phase_firsthalf_random==2&order_rate<=50", ACCEL)
        self.assertEqual(neck["spurt"], {"from": 0, "to": 100, "odds": 0.5, "useful": 0.3})

        # Rule 1: a fixed zone fires at its start, 250 m early here: no use.
        red_shift = verdict("is_finalcorner==1&order<=5&order_rate<=50", ACCEL)
        spurt = red_shift["spurt"]
        self.assertEqual((red_shift["fires_at"], spurt["from"], spurt["odds"], spurt["useful"]), (550, -250, 0.0, 0))

    def test_usefulness_weighs_the_speed_added(self):
        gold = [slot(31, value=4000, duration=30000)]
        # 0.4 m/s² for 3 s at the spurt start beats 0.2 for 3 s.
        self.assertEqual(verdict("is_lastspurt==1", gold)["spurt"]["useful"], 1.2)
        # A slope first, then a point on it: Chukyo's one downhill runs 100 to
        # 875 m, and 114 of its 775 m land from 736 m on.
        downhill = verdict("down_slope_random==1", gold)["spurt"]
        self.assertEqual((downhill["odds"], downhill["useful"]), (0.15, 0.177))

        angling = verdict("phase>=2&corner!=0&order==1", ACCEL)
        self.assertEqual(angling["kind"], "never")
        self.assertNotIn("spurt", angling)

    def test_a_speed_skill_gets_no_odds(self):
        self.assertNotIn("odds", verdict("is_lastspurt==1", SPEED)["spurt"])

    def test_an_unconfirmed_clause_blocks_every_timing(self):
        cheer = verdict("phase==2&straight_front_type==2&order<=2", ACCEL)
        self.assertTrue(cheer["unreadable"])
        for field in ("fires_at", "spread", "spurt"):
            self.assertNotIn(field, cheer)


class FirstPointWinsTests(CalculatorTestCase):
    def test_a_once_a_race_skill_fires_on_its_first_point(self):
        self.assertEqual(zones_for(MAESTRO, CHUKYO)["zones"], [{"start": 300, "end": 788, "kind": "random"}])
        self.assertEqual(zones_for(MAESTRO, CHUKYO, once_per_race=True)["zones"],
                         [{"start": 300, "end": 550, "kind": "random"}])

    def test_on_two_laps_it_fires_on_the_last_lap_s_corner_1(self):
        # Each corner_random is the last lap's corner (Daptrius, 2026-11-03):
        # 1 at 1,450, 2 at 1,650, 3 at 2,300, 4 at 2,550 m. Corner 1 comes first.
        zones = zones_for(MAESTRO, KYOTO_3200, once_per_race=True)["zones"]
        self.assertEqual([(z["start"], z["end"]) for z in zones], [(1450, 1650)])

    def test_another_clause_keeps_every_alternative(self):
        zones = zones_for("corner_random==3&order==1@corner_random==4", CHUKYO, once_per_race=True)["zones"]
        self.assertEqual([(z["start"], z["end"]) for z in zones], [(300, 788)])


class HealTests(CalculatorTestCase):
    def test_the_share_landing_before_the_late_race(self):
        self.assertEqual(verdict(MAESTRO, HEAL)["before_spurt"], 1.0)
        self.assertEqual(verdict("running_style==2&phase_random==1", HEAL)["before_spurt"], 1.0)
        # A random point from 600 m to the finish: 200 of 600 m before 800 m.
        self.assertEqual(verdict("distance_rate_after_random==50", HEAL)["before_spurt"], 0.33)
        self.assertEqual(verdict("is_lastspurt==1", HEAL)["before_spurt"], 0.0)

    def test_straight_random_weighs_each_straight_the_same(self):
        # 0 to 300 m all before; 788 to 1200 m, 12 of 412 m before: (1 + 0.03) / 2.
        self.assertEqual(verdict("straight_random==1", HEAL)["before_spurt"], 0.51)

    def test_a_slope_first_then_a_point_on_it(self):
        # Chukyo's one downhill, 100 to 875 m: 700 of 775 m before 800 m.
        self.assertEqual(verdict("down_slope_random==1", HEAL)["before_spurt"], 0.9)

    def test_no_share_where_the_trigger_can_miss(self):
        # A random point that must also be on a straight can land where it can't fire.
        self.assertNotIn("before_spurt", verdict("phase_random==1&corner==0", HEAL))


class CarryOverTests(CalculatorTestCase):
    def test_a_target_speed_skill_must_fire_in_time_to_reach_its_speed(self):
        # 2.4 s stretched x1.2 at 20.67 m/s: from 59.5 m before 800 m. It needs
        # 0.2 / 0.465 m/s² = 0.43 s (8.9 m) to reach its new target speed, so
        # firing in the last 9 m before the late race does nothing.
        start, end = carry_window(SPEED, 1200)
        self.assertEqual((round(start), round(end)), (740, 791))
        carried = verdict("remain_distance<=455&remain_distance>=445", SPEED)
        self.assertEqual((carried["carry"], carried["carry_window"]), (1.0, [740, 791]))

    def test_a_trigger_in_the_late_race_has_nothing_to_carry(self):
        # Lights of Vaudeville fires with 300 m to go, 100 m into Chukyo's late
        # race: the band still draws, but there is no share to word.
        late = verdict("remain_distance<=300", SPEED)
        self.assertEqual((late["fires_at"], late["carry_window"]), (900, [740, 791]))
        self.assertNotIn("carry", late)
        # A random point wholly in the late race, the same.
        self.assertNotIn("carry", verdict("phase_random==2", SPEED))
        # One that straddles the start keeps its share, even a small one.
        self.assertIn("carry", verdict("distance_rate_after_random==50", SPEED))

    def test_one_that_also_adds_current_speed_helps_up_to_the_late_race(self):
        mixed = [{"duration": 24000, "cooldown": 5_000_000, "abilities": [
            {"type": 27, "value": 2000, "target_type": 1}, {"type": 22, "value": 1000, "target_type": 1},
        ]}]
        start, end = carry_window(mixed, 1200)
        self.assertEqual((round(start), end), (740, 800))


class RepeatsTests(CalculatorTestCase):
    def test_the_corner_family_spaced_a_stretched_cooldown_apart(self):
        corner_speed = [slot(27, duration=24000, cooldown=300000)]
        parsed, facts = parse_condition("all_corner_random==1"), CourseFacts(KYOTO_3200)
        # 30 s stretched x3.2 at 18.68 m/s is about 1,793 m: 458 and 2,300 m.
        self.assertEqual(repeats_for(parsed, corner_speed, facts), 2)
        self.assertEqual(verdict("all_corner_random==1", corner_speed, KYOTO_3200)["repeats"], 2)
        # A once-a-race skill never repeats.
        self.assertIsNone(repeats_for(parsed, SPEED, facts))

    def test_straights_wait_on_how_many_points_there_are(self):
        # Daptrius: they can fire again over several laps, but he knows nothing
        # beyond the description, so no count.
        straight_speed = [slot(27, duration=24000, cooldown=300000)]
        self.assertIsNone(repeats_for(parse_condition("straight_random==1"), straight_speed, CourseFacts(KYOTO_3200)))


class WordsTests(CalculatorTestCase):
    def test_alternatives_read_as_or(self):
        needs = needs_for(parse_condition(
            "running_style==1&phase<=1&change_order_onetime>0&accumulatetime>=5"
            "@running_style==1&phase<=1&blocked_side_continuetime>=2&accumulatetime>=5"
        ), TextContext())
        self.assertEqual(needs, [
            "After 5 s of racing", "Just after being passed, or blocked at the side for 2 s or more",
        ])

    def test_a_joined_alternative_keeps_one_capital(self):
        # Red Ace: in the lead with a runner close behind, or 2nd and trying to pass.
        needs = needs_for(parse_condition(
            "distance_rate>=50&order==1&bashin_diff_behind<=1@distance_rate>=50&order==2&overtake_target_time>=1"
        ), TextContext())
        self.assertEqual(needs, [
            "In the lead and the runner behind within 1 length, or in 2nd place and trying to pass a runner for 1 s or more",
        ])

    def test_a_clause_without_verified_words_is_a_plain_note(self):
        self.assertEqual(needs_for(parse_condition("phase==2&is_overtake==1"), TextContext()),
                         ["Plus a condition we can't read yet"])

    def test_an_alternative_needing_nothing_needs_nothing(self):
        self.assertEqual(needs_for(parse_condition("phase==2&order==1@phase==3"), TextContext()), [])

    def test_chains(self):
        self.assertEqual(chain_for(parse_condition("activate_count_heal>=3&distance_rate>=50")),
                         {"counts": "heal", "at_least": 3})
        self.assertEqual(chain_for(parse_condition("activate_count_middle>2")), {"counts": "middle", "at_least": 3})
        # Not verified (skill_text.SKILL_COUNTS): no chain.
        self.assertIsNone(chain_for(parse_condition("activate_count_later_half>=1")))


class EndpointTests(CalculatorTestCase):
    def test_the_map_carries_the_verdicts(self):
        course = RaceCourse.objects.create(**{
            f.attname: getattr(CHUKYO, f.attname) for f in RaceCourse._meta.concrete_fields if f.attname != "id"
        })
        values = {"description": "Does a thing.", "rarity": 1, "tier": 1, "icon_id": 10011, "availability": "global"}
        push = Skill.objects.create(game_id=200551, name="Final Push", group_id=20055,
                                    condition="running_style==1&is_finalcorner_random==1&order==1",
                                    effects=ACCEL, **values)
        body = APIClient().get(f"/race-courses/{course.id}/skill-zones").json()[str(push.id)]
        self.assertEqual((body["effects"], body["spread"], body["needs"]), (["accel"], [550, 788], ["In the lead"]))
