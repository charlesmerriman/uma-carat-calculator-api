"""
A verdict per skill on a course (skill_verdicts.py; notes/course-map-plan-2.md,
section 2): what a skill does, where it fires against the late-race start, the
heal and carry-over shares, repeats and the words it needs, with Daptrius's
answers of 2026-11-03 in the numbers.
"""


from unittest import mock

from rest_framework.test import APIClient

from calculatorapi import skill_verdicts, skill_zones
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
        self.assertEqual(
            final_push["spurt"], {"from": -250, "to": -12, "odds": 0.22, "useful": 0.129, "accel": 0.129, "carry": 0.0}
        )
        self.assertEqual(final_push["needs"], ["In the lead"])

        nsm = verdict("infront_near_lane_time>=1&is_lastspurt==1&hp_per>=1", ACCEL)
        self.assertEqual(nsm["fires_at"], 800)
        self.assertEqual(nsm["spurt"], {"from": 0, "to": 0, "odds": 1.0, "useful": 0.6, "accel": 0.6, "carry": 0.0})

        # 800 to 900 m, and only the first 50 m land well.
        neck = verdict("running_style==2&phase_firsthalf_random==2&order_rate<=50", ACCEL)
        self.assertEqual(neck["spurt"], {"from": 0, "to": 100, "odds": 0.5, "useful": 0.3, "accel": 0.3, "carry": 0.0})

        # Rule 1: a fixed zone fires at its start, 250 m early here: no use.
        red_shift = verdict("is_finalcorner==1&order<=5&order_rate<=50", ACCEL)
        spurt = red_shift["spurt"]
        self.assertEqual((red_shift["fires_at"], spurt["from"], spurt["odds"]), (550, -250, 0.0))
        self.assertAlmostEqual(spurt["useful"], 0.228, places=2)  # GatedZoneTests

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
        # Nothing is unconfirmed since the skill pass; the guard stays for the
        # next new variable, so a test stands one in.
        with mock.patch.object(skill_zones, "UNCONFIRMED", frozenset({"mystery_place"})):
            cheer = verdict("phase==2&mystery_place==2&order<=2", ACCEL)
        self.assertTrue(cheer["unreadable"])
        for field in ("fires_at", "spread", "spurt"):
            self.assertNotIn(field, cheer)

    def test_victory_cheer_fires_on_a_late_race_backstretch(self):
        # Kyoto 3200's second backstretch runs 1,850 to 2,300 m; the late race
        # starts at 2,133 m on it. Chukyo's only backstretch is its first 300 m.
        cheer = verdict("phase==2&straight_front_type==2&order<=2", ACCEL, KYOTO_3200)
        self.assertEqual((cheer["fires_at"], cheer["spurt"]["from"]), (2133, 0))
        self.assertEqual(cheer["needs"], ["In 2nd place or better"])
        self.assertEqual(verdict("phase==2&straight_front_type==2&order<=2", ACCEL)["kind"], "never")


class GatedZoneTests(CalculatorTestCase):
    """Rule 1 for a fixed zone with a clause the map can't predict (the
    owner, 2026-11-03): the words read from the zone's start, the rank counts
    the zone's metres before the window as chances to fire early."""

    def test_a_zone_opening_at_the_spurt_keeps_its_full_rank(self):
        # No Stopping Me!: a runner close ahead is unpredictable, but the zone
        # opens at the late-race start, so if it fires it fires perfectly.
        nsm = verdict("infront_near_lane_time>=1&is_lastspurt==1&hp_per>=1", ACCEL)
        self.assertEqual(nsm["spurt"], {"from": 0, "to": 0, "odds": 1.0, "useful": 0.6, "accel": 0.6, "carry": 0.0})

    def test_a_zone_opening_before_the_window_loses_the_early_metres(self):
        # Red Shift from the final corner at 550 m: 114 of the 300 m up to the
        # window's end land well; firing in the first 186 m uses it up.
        red_shift = verdict("is_finalcorner==1&order<=5&order_rate<=50", ACCEL)
        self.assertEqual(red_shift["fires_at"], 550)
        self.assertEqual(red_shift["spurt"]["odds"], 0.0)
        self.assertAlmostEqual(red_shift["spurt"]["useful"], 0.6 * 114.06 / 300, places=2)
        # A settled clause (a pre-race fact) leaves rule 1 whole: the rank is the odds.
        self.assertEqual(verdict("is_finalcorner==1&popularity>=4", ACCEL)["spurt"]["useful"], 0.0)

    def test_a_timer_counts_as_settled_once_its_seconds_have_passed(self):
        # 10 s at 20.7 m/s is 207 m: settled for a zone from 300 m, not from 100 m.
        self.assertTrue(skill_verdicts.is_settled("accumulatetime", ">=", 10, 300, 1200, None))
        self.assertFalse(skill_verdicts.is_settled("accumulatetime", ">=", 10, 100, 1200, None))
        self.assertTrue(skill_verdicts.is_settled("temptation_count", "==", 0, 0, 1200, None))
        self.assertFalse(skill_verdicts.is_settled("order_rate", "<=", 40, 800, 1200, None))

    def test_tamamo_s_earlier_straight_uses_her_unique_up(self):
        # Kyoto 3200: the straights after half-way run 1,850 to 2,300 m and
        # 2,797 m on. A 3 s accel stretches to 9.6 s there, so the window is
        # 1,963 to 2,183 m: 220 of the 333 m up to its end land well, and
        # firing in the first 113 m of the backstretch uses her unique up.
        tamamo = verdict("distance_rate>=50&corner==0&order_rate<=30&order_rate>=20", ACCEL, KYOTO_3200)
        self.assertEqual(tamamo["fires_at"], 1850)
        self.assertAlmostEqual(tamamo["spurt"]["useful"], 0.6 * 220 / 333.3, places=2)

    def test_speed_carried_into_the_late_race_counts_toward_the_rank(self):
        # Tamamo's unique adds 0.2 m/s² and 0.35 m/s for 3 s (the owner: carry-over
        # is prized). On Chukyo it opens 12 m before the late race: the accel
        # lands (0.6) and the speed carries (0.35), so 0.95; a 6 s one with its
        # speed only in the late race scores its acceleration alone.
        both = [{"duration": 30000, "cooldown": 5_000_000, "abilities": [
            {"type": 31, "value": 2000, "target_type": 1}, {"type": 27, "value": 3500, "target_type": 1},
        ]}]
        tamamo = verdict("distance_rate>=50&corner==0&order_rate<=30&order_rate>=20", both)
        self.assertEqual((tamamo["fires_at"], tamamo["carry"], tamamo["spurt"]["useful"]), (788, 1.0, 0.95))
        self.assertEqual(verdict("is_lastspurt==1", both)["spurt"]["useful"], 0.6)


class PathTests(CalculatorTestCase):
    """Rule 7: one verdict per way of firing, when the ways need different things."""

    TAMAMO = ("distance_rate>=50&corner==0&order_rate>=70&order_rate<=75&is_overtake==1"
              "@distance_rate>=50&corner==0&order_rate<=30&order_rate>=20")

    def test_each_way_words_its_own_needs(self):
        tamamo = verdict(self.TAMAMO, ACCEL)
        self.assertEqual([path["needs"] for path in tamamo["paths"]], [
            ["Between 70% and 75% back in the field", "While trying to pass another runner"],
            ["Between 20% and 30% back in the field"],
        ])
        self.assertEqual([path["fires_at"] for path in tamamo["paths"]], [788, 788])
        # The summary: the stretch the paths cover, the best path's rank, no odds.
        self.assertEqual(tamamo["spread"], [788, 788])
        self.assertEqual(tamamo["spurt"], {"from": -12, "to": -12, "useful": 0.6, "accel": 0.6, "carry": 0.0})
        self.assertNotIn("fires_at", tamamo)

    def test_ways_needing_the_same_things_stay_one_verdict(self):
        # Lightning Surge: a Sprint way and a Mile way, both the same otherwise.
        surge = verdict(
            "distance_type==1&phase>=2&order_rate>=50&is_overtake==1"
            "@distance_type==2&phase>=2&order_rate>=50&is_overtake==1",
            ACCEL,
        )
        self.assertNotIn("paths", surge)
        self.assertEqual(surge["fires_at"], 800)
        self.assertNotIn("paths", verdict(MAESTRO, HEAL))

    def test_a_way_that_can_t_fire_here_is_left_out(self):
        # Moving Past, and Beyond's second way needs the final corner in the
        # mid race: on Chukyo the final corner is in the late race, so only
        # the first way is live and the verdict is one.
        moving = verdict(
            "phase>=2&corner!=0&is_finalcorner==0&order_rate>=50"
            "@phase==1&corner!=0&is_finalcorner==1&order_rate>=50&temptation_count==0",
            ACCEL,
        )
        self.assertNotIn("paths", moving)


class ChainBoundTests(CalculatorTestCase):
    def test_a_chained_skill_with_no_place_fires_from_its_skills_phase(self):
        # Lightning Flare: two mid-race skills first, so from the mid-race start.
        flare = verdict("activate_count_middle>=2", SPEED)
        self.assertEqual((flare["kind"], flare["zones"]), ("place", [{"start": 200, "end": 1200, "kind": "fixed"}]))
        self.assertEqual(flare["fires_at"], 200)
        self.assertEqual(flare["chain"], {"counts": "middle", "at_least": 2})
        self.assertEqual(verdict("activate_count_later_half>=2", SPEED)["fires_at"], 600)
        # Counts without a phase stay unbound.
        self.assertEqual(verdict("activate_count_heal>=3", SPEED)["kind"], "anywhere")


class CornerPointTests(CalculatorTestCase):
    """Rule 8: all_corner_random's four points."""

    def test_a_short_cooldown_accel_lands_at_any_of_its_points(self):
        # Corner Connoisseur on Chukyo: one corner, 300 to 788 m; 52 of its 488 m
        # land in the window, so one point lands 10.7% of the time, four 36%.
        gold = [slot(31, value=4000, duration=30000, cooldown=300000)]
        connoisseur = verdict("all_corner_random==1", gold)
        self.assertEqual(connoisseur["spurt"]["odds"], 0.36)
        self.assertAlmostEqual(connoisseur["spurt"]["useful"], 0.363 * 1.2, places=2)

    def test_a_once_a_race_speed_skill_fires_at_its_first_point(self):
        # Sprint Corners: the first of four points is almost never in the last
        # 50 m of the corner, so the carry share is nil.
        corners = verdict("distance_type==1&all_corner_random==1", SPEED)
        self.assertLess(corners["carry"], 0.01)
        self.assertEqual(corners["carry_window"], [740, 791])


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
        # What it carries, for the accels column's words: 0.2 m/s for 2.4 s
        # stretched to Chukyo's 1200 m.
        self.assertEqual((carried["carry_speed"], carried["carry_seconds"]), (0.2, 2.9))

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
            "In the lead and the runner behind within 1 length, "
            "or in 2nd place and trying to pass a runner for 1 s or more",
        ])

    def test_a_clause_without_verified_words_is_a_plain_note(self):
        self.assertEqual(needs_for(parse_condition("phase==2&behind_near_lane_time_set1>=1"), TextContext()),
                         ["Plus a condition we can't read yet"])

    def test_an_alternative_needing_nothing_needs_nothing(self):
        self.assertEqual(needs_for(parse_condition("phase==2&order==1@phase==3"), TextContext()), [])

    def test_chains(self):
        self.assertEqual(chain_for(parse_condition("activate_count_heal>=3&distance_rate>=50")),
                         {"counts": "heal", "at_least": 3})
        self.assertEqual(chain_for(parse_condition("activate_count_middle>2")), {"counts": "middle", "at_least": 3})
        # Not verified (skill_text.SKILL_COUNTS): no chain.
        self.assertIsNone(chain_for(parse_condition("is_activate_any_skill==1")))


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
