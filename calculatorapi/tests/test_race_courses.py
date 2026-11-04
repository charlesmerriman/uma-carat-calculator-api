"""
The course map's record (notes/course-map-plan.md, PR 1): the model, the pure
clean-up and path helpers (course_shapes.py), the two admin buttons
(admin_course_import.py), the race links the extractor writes, and the course
each event sends.

Fixtures are small made-up records shaped like Daptrius's scraper output; the
real Longchamp 2400 and Nakayama 1800 boundaries are copied in where a rule
exists because of them.
"""

import json
import math
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import admin_course_import
from calculatorapi.admin_course_import import (
    apply_course_bundle,
    apply_course_links,
    parse_course_bundle,
    plan_course_links,
)
from calculatorapi.admin_race_course import RaceCourseForm
from calculatorapi.course_shapes import (
    course_fields,
    heading_change,
    pair_straights,
    pick_path,
    to_screen,
    trim_at_finish,
)
from calculatorapi.models import ChampionsMeeting, PvpGuide, RaceCourse
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.tests.factories import (
    make_champions_meeting,
    make_league_of_heroes,
    make_ranks,
    make_user,
)
from calculatorapi.tests.test_snapshot_scripts import extractor


def boundary(at, kind, front, *extra):
    return {"event_name": "straight_boundary", "distance": float(at), "values": [kind, front, *extra]}


def scraped(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            game_id=10701, track=10007, distance=1200, ground=1, inout=1, turn=2,
            corners=((300, 250, 3), (550, 238, 4)), straights=((0, 300, 2), (788, 1200, 1)),
            slopes=((100, 775, -10000), (875, 100, 20000)), events=None, warnings=()):
    """A record shaped like Daptrius's course_data.json entry (Chukyo turf 1200 by default)."""
    if events is None:
        events = []
        for start, end, front in straights:
            events += [boundary(start, 1, front), boundary(end, 2, front)]
    return {
        "course_id": str(game_id),
        "masterdata": {"id": game_id, "race_track_id": track, "distance": distance,
                       "ground": ground, "inout": inout, "turn": turn},
        "corners": [{"corner_number": n, "start": float(s), "length": length} for s, length, n in corners],
        "straights": [{"start": float(s), "end": float(e), "length": float(e - s), "front_type": f}
                      for s, e, f in straights],
        "slopes": [{"start": float(s), "length": length, "slope_raw": v} for s, length, v in slopes],
        "events": events,
        "warnings": list(warnings),
    }


NAMES = {10007: "Chukyo", 10006: "Tokyo", 10201: "Longchamp", 10005: "Nakayama"}


def oval(clockwise_with_z_up=True, points=1001):
    """A loop in the game's axes (z up), one lap, starting and finishing at the right."""
    sign = -1 if clockwise_with_z_up else 1
    xs, zs = [], []
    for i in range(points):
        angle = sign * 2 * math.pi * i / (points - 1)
        xs.append(300 * math.cos(angle))
        zs.append(120 * math.sin(angle))
    return xs, zs


def bundle_course(game_id=10701, **overrides):
    """One course as the bundle script writes it."""
    fields = course_fields(scraped(game_id=game_id), NAMES)
    xs, zs = oval(clockwise_with_z_up=False)  # Chukyo is left-handed
    fields.update({
        "region": "jp",
        "shape": to_screen(xs, zs, fields["distance"], 788),
        "path_source": "an_pos_race10007_00_1200_00_0_0",
        "path_candidates": {},
    })
    fields.update(overrides)
    return fields


def bundle_file(courses, **top):
    body = {"format": 1, "region": "jp", "courses": courses, **top}
    return SimpleUploadedFile("course_bundle_jp.json", json.dumps(body).encode(), "application/json")


def make_course(game_id=10701, **fields):
    course = bundle_course(game_id)
    course.update(fields)
    return RaceCourse.objects.create(
        region=course["region"], game_id=game_id,
        **{k: course[k] for k in admin_course_import.GAME_FIELDS},
        shape=course["shape"], path_source=course["path_source"],
        path_candidates=course["path_candidates"], show_map=course["show_map"],
    )


# ── course_shapes ────────────────────────────────────────────────────────────

class PairStraightsTests(CalculatorTestCase):
    def test_longchamp_2400_gets_the_straight_daptrius_described(self):
        # The real boundaries of course 11203. The scraper paired by front
        # type and lost 1617 to 1867 m.
        events = [boundary(0, 1, 2), boundary(1000, 2, 2), boundary(1617, 1, 3),
                  boundary(1867, 2, 1), boundary(1867, 1, 1, 1), boundary(2400, 2, 1)]
        straights, problems = pair_straights(events)
        self.assertEqual(straights, [
            {"start": 0, "length": 1000, "front": 2},
            {"start": 1617, "length": 250, "front": 3},
            {"start": 1867, "length": 533, "front": 1},
        ])
        self.assertEqual(problems, [])

    def test_nakayama_1800_no_longer_runs_through_its_corners(self):
        # Course 10509: corners 3 and 4 cover 1100 to 1492 m.
        events = [boundary(0, 1, 1), boundary(350, 2, 1), boundary(750, 1, 1),
                  boundary(1100, 2, 2), boundary(1492, 1, 1, 1), boundary(1800, 2, 1)]
        straights, _ = pair_straights(events)
        self.assertEqual([(s["start"], s["start"] + s["length"]) for s in straights],
                         [(0, 350), (750, 1100), (1492, 1800)])

    def test_a_start_with_no_end_is_a_problem_not_a_straight(self):
        # Longchamp 1000 (11201): two starts, no end.
        straights, problems = pair_straights([boundary(0, 1, 1), boundary(600, 1, 2)])
        self.assertEqual(straights, [])
        self.assertEqual(len(problems), 2)


class CourseFieldsTests(CalculatorTestCase):
    def test_chukyo_1200_keeps_the_games_numbers(self):
        fields = course_fields(scraped(), NAMES)
        self.assertEqual(fields["racecourse"], "Chukyo")
        self.assertEqual((fields["surface"], fields["direction"], fields["variant"]), ("turf", "left", ""))
        self.assertEqual(fields["corners"], [{"start": 300, "length": 250, "number": 3},
                                             {"start": 550, "length": 238, "number": 4}])
        self.assertEqual(fields["slopes"], [{"start": 100, "length": 775, "slope": -10000},
                                            {"start": 875, "length": 100, "slope": 20000}])
        self.assertEqual(fields["flags"], [])
        self.assertTrue(fields["show_map"])

    def test_corner_minus_one_stays_and_a_gap_stays_a_gap(self):
        # Santa Anita 2000 (11615): corner -1 at 100 m, nothing from 300 to 700.
        record = scraped(game_id=11615, track=10202, distance=2000,
                         corners=((100, 200, -1), (700, 150, 1), (850, 150, 2)),
                         straights=((0, 100, 1), (1000, 2000, 1)), slopes=())
        fields = course_fields(record, NAMES)
        self.assertEqual([c["number"] for c in fields["corners"]], [-1, 1, 2])
        self.assertEqual(fields["racecourse"], "Santa Anita Park")  # Global's text lacks it

    def test_corner_90_is_dropped_and_flagged(self):
        record = scraped(game_id=10613, track=10006, distance=2400,
                         corners=((500, 250, 1), (2348, 500, 90)), straights=((0, 500, 1),), slopes=())
        fields = course_fields(record, NAMES)
        self.assertEqual([c["number"] for c in fields["corners"]], [1])
        self.assertIn("corner 90", fields["flags"][0])

    def test_a_part_past_the_finish_is_cut_at_the_line(self):
        parts, flags = trim_at_finish([{"start": 1340, "length": 310, "front": 1}], 1600, "straight")
        self.assertEqual(parts, [{"start": 1340, "length": 260, "front": 1}])
        self.assertEqual(len(flags), 1)

    def test_a_dirt_course_starting_on_turf(self):
        # Tokyo dirt 1600 (10611, CM 22): turf for its first 100 m.
        events = [{"event_name": "ground_change", "distance": 0.0, "values": [1]},
                  {"event_name": "ground_change", "distance": 100.0, "values": [2]},
                  boundary(0, 1, 2), boundary(650, 2, 2)]
        record = scraped(game_id=10611, track=10006, distance=1600, ground=2,
                         straights=((0, 650, 2),), events=events, slopes=())
        fields = course_fields(record, NAMES)
        self.assertEqual(fields["surface_changes"],
                         [{"start": 0, "surface": "turf"}, {"start": 100, "surface": "dirt"}])

    def test_a_record_with_no_master_row_is_skipped(self):
        self.assertIsNone(course_fields({"course_id": "10614", "masterdata": None}, NAMES))

    def test_unpairable_straights_hide_the_map(self):
        record = scraped(game_id=11201, track=10201, distance=1000, turn=4,
                         events=[boundary(0, 1, 1), boundary(600, 1, 2)])
        self.assertFalse(course_fields(record, NAMES)["show_map"])


class PickPathTests(CalculatorTestCase):
    MASTER = {"race_track_id": 10006, "distance": 2400, "ground": 1, "inout": 1}

    def test_the_codes_pick_one_and_keep_the_other_for_the_swap(self):
        names = ["an_pos_race10006_00_2400_00_0_1", "an_pos_race10006_00_2400_00_0_0",
                 "an_pos_race10006_00_2400_01_0_0",   # dirt
                 "an_pos_race10006_01_0000_00_0_0",   # not a race path
                 "an_pos_race10006_00_1600_00_0_0"]   # another distance
        self.assertEqual(pick_path(self.MASTER, names),
                         ("an_pos_race10006_00_2400_00_0_0", ["an_pos_race10006_00_2400_00_0_1"]))

    def test_inner_and_outer_courses(self):
        kyoto = {"race_track_id": 10008, "distance": 1400, "ground": 1, "inout": 3}  # outer
        names = ["an_pos_race10008_00_1400_00_1_0", "an_pos_race10008_00_1400_00_2_0"]
        self.assertEqual(pick_path(kyoto, names), ("an_pos_race10008_00_1400_00_2_0", []))

    def test_no_file(self):
        self.assertEqual(pick_path(self.MASTER, []), (None, []))


class ToScreenTests(CalculatorTestCase):
    def test_a_right_handed_course_turns_clockwise_on_screen(self):
        # In the game's axes (z up) right-handed is clockwise; drawn without
        # flipping z it would come out anticlockwise, the mirror image.
        shape = to_screen(*oval(clockwise_with_z_up=True), 2000, 1800)
        self.assertGreater(heading_change(shape), 300)

    def test_a_left_handed_course_turns_anticlockwise(self):
        shape = to_screen(*oval(clockwise_with_z_up=False), 2000, 1800)
        self.assertLess(heading_change(shape), -300)

    def test_finish_at_the_bottom_and_the_box_at_zero(self):
        shape = to_screen(*oval(), 2000, 1800)
        mean_y = sum(y for _, y in shape) / len(shape)
        self.assertGreater(shape[-1][1], mean_y)
        self.assertEqual(min(x for x, _ in shape), 0)
        self.assertEqual(min(y for _, y in shape), 0)
        self.assertEqual(len(shape), 1001)

    def test_a_path_must_have_1001_points(self):
        with self.assertRaises(ValueError):
            to_screen([0.0] * 1000, [0.0] * 1000, 1200)


# ── The model and the payload ───────────────────────────────────────────────

class RaceCourseModelTests(CalculatorTestCase):
    def test_one_row_per_course_per_region(self):
        make_course(10701)
        RaceCourse.objects.create(region="global", game_id=10701, racecourse="Chukyo", track_id=10007,
                                  surface="turf", distance=1200, direction="left")
        with self.assertRaises(IntegrityError):
            make_course(10701)

    def test_str(self):
        self.assertEqual(str(make_course(10701)), "Chukyo turf 1200 m (JP)")


class EventCoursePayloadTests(CalculatorTestCase):
    def setUp(self):
        make_ranks()

    def payload(self):
        return APIClient().get("/calculator-data").json()

    def test_a_linked_event_sends_its_course_without_the_shape(self):
        cm = make_champions_meeting(cm_number=21)
        cm.course = make_course(10701)
        cm.save()
        [sent] = self.payload()["champions_meeting_data"]
        self.assertEqual(sent["course"]["game_id"], 10701)
        self.assertEqual(sent["course"]["corners"][1], {"start": 550, "length": 238, "number": 4})
        for private in ("shape", "flags", "source_data", "path_candidates"):
            self.assertNotIn(private, sent["course"])

    def test_no_course_or_a_hidden_map_sends_null(self):
        make_champions_meeting(cm_number=1)
        loh = make_league_of_heroes(loh_number=3)
        loh.course = make_course(10701, show_map=False)
        loh.save()
        data = self.payload()
        self.assertIsNone(data["champions_meeting_data"][0]["course"])
        self.assertIsNone(data["league_of_heroes_event_data"][0]["course"])


# ── Import courses ──────────────────────────────────────────────────────────

class ParseCourseBundleTests(CalculatorTestCase):
    def test_a_good_bundle(self):
        region, courses = parse_course_bundle(bundle_file([bundle_course()]))
        self.assertEqual((region, len(courses)), ("jp", 1))

    def test_refusals_an_editor_can_act_on(self):
        bad_shape = bundle_course(shape=[[0, 0]] * 10)
        for upload, message in (
            (SimpleUploadedFile("x.json", b"not json"), "doesn't read as JSON"),
            (bundle_file([bundle_course()], format=2), "format"),
            (bundle_file([bundle_course()], region="mars"), "region"),
            (bundle_file([bad_shape]), "course 10701: shape"),
            (bundle_file([bundle_course(), bundle_course()]), "twice"),
        ):
            with self.subTest(message=message), self.assertRaisesMessage(ValueError, message):
                parse_course_bundle(upload)


class ApplyCourseBundleTests(CalculatorTestCase):
    def test_creates_then_reports_unchanged(self):
        summary = apply_course_bundle("jp", [bundle_course()])
        self.assertEqual(len(summary["created"]), 1)
        self.assertEqual(apply_course_bundle("jp", [bundle_course()])["unchanged"], 1)

    def test_preview_writes_nothing(self):
        apply_course_bundle("jp", [bundle_course()], dry_run=True)
        self.assertEqual(RaceCourse.objects.count(), 0)

    def test_a_reimport_keeps_the_editors_choices(self):
        course = make_course(10701, path_source="b", path_candidates={"a": [[1, 1]] * 1001})
        course.later_laps = "outside"
        course.save()
        again = bundle_course(path_source="a", shape=[[1, 1]] * 1001,
                              path_candidates={"b": [[2, 2]] * 1001}, flags=["new note"])
        summary = apply_course_bundle("jp", [again])
        course.refresh_from_db()
        self.assertEqual(course.path_source, "b")
        self.assertEqual(course.shape[0], [2, 2])
        self.assertEqual(list(course.path_candidates), ["a"])
        self.assertEqual(course.later_laps, "outside")
        self.assertEqual(course.flags, ["new note"])
        self.assertEqual(len(summary["kept_choices"]), 1)

    def test_an_import_can_hide_a_map_but_never_shows_one(self):
        shown = make_course(10701)
        hidden = make_course(10702, show_map=False)
        apply_course_bundle("jp", [bundle_course(10701, show_map=False), bundle_course(10702)])
        shown.refresh_from_db()
        hidden.refresh_from_db()
        self.assertFalse(shown.show_map)
        self.assertFalse(hidden.show_map)

    def test_a_course_missing_from_the_bundle_is_listed_not_deleted(self):
        make_course(10799)
        summary = apply_course_bundle("jp", [bundle_course()])
        self.assertEqual(len(summary["not_in_bundle"]), 1)
        self.assertTrue(RaceCourse.objects.filter(game_id=10799).exists())


# ── Link courses ────────────────────────────────────────────────────────────

LINKS = {
    "champions": {
        "21": {"course": 10701, "season": 4, "weather": 1, "ground": 1},
        "22": {"course": 10611, "season": 4, "weather": 4, "ground": 3},  # not imported here
        "23": {"course": None, "courses": [10701, 10702], "season": 1, "weather": 1, "ground": 1},
    },
    "heroes": {"3": {"course": 10701, "season": 3, "weather": 0, "ground": 0}},
}


class LinkCoursesTests(CalculatorTestCase):
    def setUp(self):
        self.chukyo = make_course(10701)

    def cm(self, number, **typed):
        cm = make_champions_meeting(cm_number=number, name=f"CM {number}")
        ChampionsMeeting.objects.filter(pk=cm.pk).update(**{
            "track": "Chukyo", "surface_type": "Turf", "distance": "Sprint", "length": "1200",
            "direction": "Left", "track_condition": "Firm", "season": "Winter", "weather": "Sunny",
            **typed,
        })
        cm.refresh_from_db()
        return cm

    def test_links_each_event_the_snapshot_knows(self):
        cm = self.cm(21)
        loh = make_league_of_heroes(loh_number=3)
        summary = apply_course_links(plan_course_links(LINKS))
        cm.refresh_from_db()
        loh.refresh_from_db()
        self.assertEqual((cm.course, loh.course), (self.chukyo, self.chukyo))
        self.assertEqual(len(summary["linked"]), 2)

    def test_events_it_cannot_link_say_why(self):
        self.cm(22)
        self.cm(23)
        self.cm(99)
        unlinked = apply_course_links(plan_course_links(LINKS))["unlinked"]
        self.assertTrue(any("10611 isn't imported" in line for line in unlinked))
        self.assertTrue(any("several courses" in line for line in unlinked))
        self.assertTrue(any("not in the JP snapshot" in line for line in unlinked))

    def test_typed_details_that_disagree_are_listed_never_rewritten(self):
        # CM 50's real mistake: season and weather swapped.
        cm = self.cm(21, season="Sunny", weather="Winter", length="1200m")
        found = apply_course_links(plan_course_links(LINKS))["disagreements"]
        self.assertEqual(len(found), 1)
        self.assertEqual([field for field, _, _ in found[0]["fields"]], ["Season", "Weather"])
        cm.refresh_from_db()
        self.assertEqual(cm.season, "Sunny")

    def test_a_different_link_is_kept_unless_replace(self):
        other = make_course(10702)
        cm = self.cm(21)
        cm.course = other
        cm.save()
        summary = apply_course_links(plan_course_links(LINKS))
        cm.refresh_from_db()
        self.assertEqual(cm.course, other)
        self.assertEqual(len(summary["kept_different"]), 1)
        apply_course_links(plan_course_links(LINKS), replace_different=True)
        cm.refresh_from_db()
        self.assertEqual(cm.course, self.chukyo)

    def test_preview_writes_nothing(self):
        cm = self.cm(21)
        apply_course_links(plan_course_links(LINKS), dry_run=True)
        cm.refresh_from_db()
        self.assertIsNone(cm.course)


class RaceLinksExtractTests(CalculatorTestCase):
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE race (id INTEGER, course_set INTEGER);
            CREATE TABLE race_instance (id INTEGER, race_id INTEGER);
            CREATE TABLE race_condition (id INTEGER, season INTEGER, weather INTEGER, ground INTEGER);
            CREATE TABLE champions_race_condition (champions_id INTEGER, round_id INTEGER,
                                                   race_instance_id INTEGER, race_condition_id INTEGER);
            CREATE TABLE heroes_race_condition (id INTEGER, heroes_id INTEGER, stage INTEGER,
                                                race_instance_id INTEGER, season INTEGER,
                                                weather INTEGER, ground INTEGER);
            INSERT INTO race VALUES (1, 10701), (2, 10701), (3, 10611);
            INSERT INTO race_instance VALUES (600025, 1), (600037, 2), (600099, 3), (670005, 3);
            INSERT INTO race_condition VALUES (19, 4, 1, 1);
            INSERT INTO champions_race_condition VALUES (21, 0, 600025, 19), (21, 4, 600037, 19),
                                                        (30, 0, 600025, 19), (30, 4, 600099, 19);
            INSERT INTO heroes_race_condition VALUES (7, 3, 0, 670005, 3, 0, 0);
        """)
        return conn

    def test_each_event_by_number(self):
        links = extractor.extract_race_links(self.db())
        # CM 21's two race instances both run on 10701.
        self.assertEqual(links["champions"]["21"], {"course": 10701, "season": 4, "weather": 1, "ground": 1})
        self.assertEqual(links["heroes"]["3"], {"course": 10611, "season": 3, "weather": 0, "ground": 0})

    def test_an_event_on_two_courses_is_left_for_a_hand_pick(self):
        self.assertEqual(extractor.extract_race_links(self.db())["champions"]["30"]["courses"], [10611, 10701])
        self.assertIsNone(extractor.extract_race_links(self.db())["champions"]["30"]["course"])


# ── The admin pages ─────────────────────────────────────────────────────────

class PathSwapTests(CalculatorTestCase):
    def test_the_swap_trades_the_shapes(self):
        course = make_course(10606, path_source="a", shape=[[1, 1]] * 1001,
                             path_candidates={"b": [[2, 2]] * 1001})
        data = {f: getattr(course, f) for f in RaceCourseForm.Meta.fields}
        for key in ("corners", "straights", "slopes", "surface_changes"):
            data[key] = json.dumps(data[key])
        form = RaceCourseForm(data={**data, "path_choice": "b"}, instance=course)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        course.refresh_from_db()
        self.assertEqual((course.path_source, course.shape[0]), ("b", [2, 2]))
        self.assertEqual(course.path_candidates, {"a": [[1, 1]] * 1001})


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class RaceCourseAdminPageTests(CalculatorTestCase):
    def setUp(self):
        make_ranks()
        self.admin = make_user(username="courseadmin", is_staff=True)
        self.admin.is_superuser = True
        self.admin.save()
        self.client.force_login(self.admin)

    def test_the_changelist_and_a_course_page_render(self):
        course = make_course(10606, path_candidates={"b": [[2, 2]] * 1001})
        self.assertEqual(self.client.get(reverse("admin:calculatorapi_racecourse_changelist")).status_code, 200)
        page = self.client.get(reverse("admin:calculatorapi_racecourse_change", args=[course.pk]))
        self.assertContains(page, "Path file")

    def test_import_then_link_through_the_pages(self):
        url = reverse("admin:calculatorapi_racecourse_import_courses")
        self.client.post(url, {"bundle": bundle_file([bundle_course()]), "dry_run": ""})
        self.assertTrue(RaceCourse.objects.filter(game_id=10701).exists())

        cm = make_champions_meeting(cm_number=21)
        with tempfile.TemporaryDirectory() as tmp:
            links = Path(tmp) / "race_links.json"
            links.write_text(json.dumps(LINKS))
            with mock.patch.object(admin_course_import, "RACE_LINKS_PATH", links):
                page = self.client.post(reverse("admin:calculatorapi_racecourse_link_courses"), {"dry_run": ""})
        self.assertContains(page, "Typed details that disagree")
        cm.refresh_from_db()
        self.assertEqual(cm.course.game_id, 10701)

    def test_a_missing_snapshot_says_what_to_do(self):
        with mock.patch.object(admin_course_import, "RACE_LINKS_PATH", Path("/nonexistent/race_links.json")):
            page = self.client.post(reverse("admin:calculatorapi_racecourse_link_courses"), {"dry_run": "on"})
        self.assertContains(page, "refresh the JP snapshot")

    def test_a_bad_bundle_shows_the_reason(self):
        page = self.client.post(reverse("admin:calculatorapi_racecourse_import_courses"),
                                {"bundle": SimpleUploadedFile("x.json", b"nope"), "dry_run": "on"})
        self.assertContains(page, "doesn&#x27;t read as JSON")



class CourseShapeEndpointTests(CalculatorTestCase):
    """GET /race-courses/<id>/shape: the real shape, fetched when a map is drawn (PR 3)."""

    def shape(self, course_id):
        return APIClient().get(f"/race-courses/{course_id}/shape")

    def test_a_course_sends_its_1001_point_shape(self):
        shape = self.shape(make_course(10701).id).json()["shape"]
        self.assertEqual(len(shape), 1001)
        # Screen axes, finish at the bottom: the last point sits below the middle.
        self.assertGreater(shape[-1][1], sum(y for _, y in shape) / len(shape))

    def test_no_path_is_null_and_a_hidden_or_missing_course_is_a_404(self):
        course = make_course(10701)
        RaceCourse.objects.filter(pk=course.id).update(shape=None)
        self.assertIsNone(self.shape(course.id).json()["shape"])
        RaceCourse.objects.filter(pk=course.id).update(show_map=False)
        self.assertEqual(self.shape(course.id).status_code, 404)
        self.assertEqual(self.shape(99999).status_code, 404)

    def test_the_guide_payload_no_longer_carries_it(self):
        make_ranks()
        cm = make_champions_meeting(cm_number=21)
        cm.course = make_course(10701)
        cm.save()
        PvpGuide.objects.create(champions_meeting=cm, visibility="public")
        self.assertNotIn("course_shape", APIClient().get(f"/pvp-guides/cm/{cm.id}").json())
