"""The other free card events from the game files: the extractor's
free_card_events.json and the import that turns it into StoryEvent rows of
kind main_story, anniversary and special, placed under their banners.
notes/story-events-plan-2.md, PR 2."""

import json
import sqlite3
import tempfile
from pathlib import Path

from calculatorapi.models import StoryEvent, StoryEventCard, StoryEventKind, SupportCard, Uma

from .base import CalculatorTestCase
from .factories import _dt, make_timeline
from .test_skills import import_game_data, write_snapshot
from .test_snapshot_scripts import extractor


def free_events_db():
    """An in-memory mdb laid out the way the Global client lays out the events
    this pass reads: a launch-era main story part, the Act 1 finale with a card
    and an uma, the 1st anniversary's missions (and the story event replay
    missions, which must be skipped), and the April fan event."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE text_data (category INTEGER, "index" INTEGER, text TEXT);
        CREATE TABLE main_story_part (id INTEGER, start_date INTEGER, ui_color TEXT);
        CREATE TABLE main_story_data (id INTEGER, part_id INTEGER,
            add_reward_category_1 INTEGER, add_reward_id_1 INTEGER, add_reward_num_1 INTEGER);
        CREATE TABLE mission_data (id INTEGER, event_id INTEGER, item_category INTEGER,
            item_id INTEGER, item_num INTEGER, start_date TEXT, end_date TEXT);
        CREATE TABLE fan_raid_data (fan_raid_id INTEGER, start_date INTEGER, end_date INTEGER);
        CREATE TABLE fan_raid_individual_reward (id INTEGER, fan_raid_id INTEGER,
            item_category INTEGER, item_id INTEGER, item_num INTEGER);
        CREATE TABLE fan_raid_all_reward (id INTEGER, fan_raid_id INTEGER,
            item_category INTEGER, item_id INTEGER, item_num INTEGER);

        INSERT INTO text_data VALUES (93, 1, 'Chapter 1'), (93, 7, 'Finale Part 2'),
            (187, 138, '1st Anniversary Celebration Missions\\nPart 2'),
            (248, 1001, 'Top Umadol Project');
        -- Part 1 is dated 2017 by the client: in the game since launch.
        INSERT INTO main_story_part VALUES (1, 1483228800, ''), (7, 1782424800, ''), (8, 1800000000, '');
        INSERT INTO main_story_data VALUES (118, 1, 51, 30022, 1), (119, 1, 90, 43, 30),
            (713, 7, 50, 100103, 1), (714, 7, 51, 30105, 1),
            -- A part whose chapters give no card is not an event here.
            (801, 8, 90, 43, 30);
        INSERT INTO mission_data VALUES
            (1, 138, 51, 30088, 1, '2026/03/12 22:00:00', '2026/04/15 14:59:59'),
            (2, 138, 51, 30088, 1, '2026/03/12 22:00:00', '2026/04/15 14:59:59'),
            (3, 138, 90, 43, 100, '2026/03/12 22:00:00', '2026/04/15 14:59:59'),
            (4, 28, 51, 30025, 1, '2023/09/04 22:00:00', '2050/01/01 00:00:00');
        INSERT INTO fan_raid_data VALUES (1001, 1775512800, 1776117599);
        INSERT INTO fan_raid_individual_reward VALUES (4, 1001, 51, 20045, 1);
        INSERT INTO fan_raid_all_reward VALUES (1, 1001, 51, 20045, 1), (2, 1001, 90, 43, 50);
    """)
    return conn


class FreeCardEventExtractTests(CalculatorTestCase):

    def test_every_kind_comes_out_with_its_dates_and_rewards(self):
        events = extractor.extract_free_card_events(free_events_db(), "global")
        self.assertEqual(events, [
            {"kind": "main_story", "id": 1, "name": "Main Story Act 1, Chapter 1",
             "start": None, "end": None, "rewards": [{"type": "card", "id": 30022}]},
            {"kind": "main_story", "id": 7, "name": "Main Story Act 1, Finale Part 2",
             "start": "2026-06-25T22:00:00+00:00", "end": None,
             "rewards": [{"type": "card", "id": 30105}, {"type": "uma", "id": 100103}]},
            {"kind": "anniversary", "id": 138, "name": "1st Anniversary Celebration Missions Part 2",
             "start": "2026-03-12T22:00:00+00:00", "end": "2026-04-15T14:59:59+00:00",
             "rewards": [{"type": "card", "id": 30088}]},
            {"kind": "special", "id": 1001, "name": "Top Umadol Project",
             "start": "2026-04-06T22:00:00+00:00", "end": "2026-04-13T21:59:59+00:00",
             "rewards": [{"type": "card", "id": 20045}]},
        ])

    def test_jp_mission_dates_are_read_in_japan_time(self):
        conn = free_events_db()
        conn.execute("UPDATE mission_data SET start_date = '2022/2/24 12:00:00',"
                     " end_date = '2022/3/30 4:59:59' WHERE event_id = 138")
        [missions] = [e for e in extractor.extract_free_card_events(conn, "jp")
                      if e["kind"] == "anniversary"]
        self.assertEqual((missions["start"], missions["end"]),
                         ("2022-02-24T03:00:00+00:00", "2022-03-29T19:59:59+00:00"))

    def test_a_database_without_the_tables_gives_none(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.assertEqual(extractor.extract_free_card_events(conn, "global"), [])


def snapshot_free_event(kind, game_id, name="", **when_and_rewards):
    """One event as free_card_events.json carries it; `start`, `end` and
    `rewards` (as (type, id) pairs) by keyword."""
    return {"kind": kind, "id": game_id, "name": name,
            "start": when_and_rewards.get("start"), "end": when_and_rewards.get("end"),
            "rewards": [{"type": t, "id": i} for t, i in when_and_rewards.get("rewards", ())]}


class FreeCardEventImportTests(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """What the import creates and where it places each event."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        # The launch banner, then three Global banners, then one only JP has run.
        self.launch = make_timeline(name="T.M. Opera", global_start_date=_dt(2025, 6, 26, 22),
                                    global_end_date=_dt(2025, 7, 3, 21, 59, 59))
        self.eishin = make_timeline(name="Eishin Flash", global_start_date=_dt(2025, 10, 30, 22),
                                    global_end_date=_dt(2025, 11, 9, 21, 59, 59))
        self.finale = make_timeline(name="Seiun Sky (Rerun)", global_start_date=_dt(2026, 6, 25, 22),
                                    global_end_date=_dt(2026, 7, 4, 21, 59, 59))
        self.kitasan = make_timeline(name="Kitasan Black", global_start_date=_dt(2026, 3, 12, 22),
                                     global_end_date=_dt(2026, 3, 23, 21, 59, 59),
                                     jp_start_date=_dt(2022, 2, 24, 22))
        # A banner's JP date is its JP day (2023-02-24) at 22:00 UTC.
        self.cb = make_timeline(name="Mr. C.B.", jp_start_date=_dt(2023, 2, 24, 22),
                                jp_end_date=_dt(2023, 3, 20, 21, 59, 59))
        self.cards = {game_id: SupportCard.objects.create(name=name, game_id=game_id, rarity=3)
                      for game_id, name in ((30022, "Mejiro McQueen"), (30105, "Special Week"),
                                            (30088, "Satono Diamond"), (30136, "Symboli Kris S"),
                                            (30062, "Silence Suzuka"))}
        self.commander = Uma.objects.create(name="Special Week (Commander)", game_id=100103, rarity=3)
        self.global_events = [
            snapshot_free_event("main_story", 1, "Main Story Act 1, Chapter 1",
                                rewards=[("card", 30022)]),
            snapshot_free_event("main_story", 7, "Main Story Act 1, Finale Part 2",
                                start="2026-06-25T22:00:00+00:00",
                                rewards=[("card", 30105), ("uma", 100103)]),
            snapshot_free_event("anniversary", 101, "Half Anniversary Celebration Missions Part 2",
                                start="2025-11-03T22:00:00+00:00", end="2025-12-10T14:59:59+00:00",
                                rewards=[("card", 30062)]),
        ]
        self.jp_events = [
            snapshot_free_event("main_story", 7, "最終章後編", start="2022-07-20T03:00:00+00:00",
                                rewards=[("card", 30105), ("uma", 100103)]),
            snapshot_free_event("anniversary", 302, "2nd Anniversary 記念ミッション 第2弾",
                                start="2023-02-24T03:00:00+00:00", end="2023-03-29T19:59:59+00:00",
                                rewards=[("card", 30136)]),
        ]

    def run_import(self, *flags):
        global_dir = self.root / "master_snapshot"
        global_dir.mkdir(exist_ok=True)
        write_snapshot(global_dir, [])
        (global_dir / "free_card_events.json").write_text(
            json.dumps(self.global_events), encoding="utf-8")
        jp_dir = self.root / "jp_snapshot"
        jp_dir.mkdir(exist_ok=True)
        for name in ("skills", "cards", "card_skills", "support_cards", "support_hints"):
            (jp_dir / f"{name}.json").write_text("[]", encoding="utf-8")
        (jp_dir / "evolutions.json").write_text(
            json.dumps({"outfit": [], "scenario": [], "succession": []}), encoding="utf-8")
        (jp_dir / "free_card_events.json").write_text(json.dumps(self.jp_events), encoding="utf-8")
        return import_game_data(global_dir, "--jp-snapshot", str(jp_dir), *flags)

    def event(self, kind, game_id):
        return StoryEvent.objects.get(kind=kind, game_id=game_id)

    def test_creates_each_event_with_its_rewards(self):
        out = self.run_import()
        self.assertIn("creating 4, adding 5 free reward(s)", out)
        finale = self.event("main_story", 7)
        self.assertEqual((finale.name, finale.jp_name), ("Main Story Act 1, Finale Part 2", "最終章後編"))
        self.assertEqual(finale.global_start_at, _dt(2026, 6, 25, 22))
        self.assertEqual([c.support_card for c in finale.cards.exclude(support_card=None)],
                         [self.cards[30105]])
        self.assertEqual([c.uma for c in finale.cards.exclude(uma=None)], [self.commander])
        upcoming = self.event("anniversary", 302)
        self.assertEqual((upcoming.name, upcoming.global_start_at), ("", None))
        self.assertEqual(upcoming.jp_end_at, _dt(2023, 3, 29, 19, 59, 59))
        # No reward amounts: the files don't state them for these kinds.
        self.assertEqual(finale.carat_amount, 0)

    def test_each_event_is_placed_by_the_right_rule(self):
        out = self.run_import()
        self.assertEqual(self.event("main_story", 7).banner_timeline, self.finale)   # its minute
        self.assertEqual(self.event("anniversary", 101).banner_timeline, self.eishin)  # running
        self.assertEqual(self.event("anniversary", 302).banner_timeline, self.cb)      # JP day
        self.assertEqual(self.event("main_story", 1).banner_timeline, self.launch)     # launch
        self.assertIn("main_story 1 placed under banner", out)
        self.assertIn("(launch)", out)
        self.assertIn("anniversary 101 placed under banner", out)
        self.assertIn("(running on Global)", out)
        self.assertIn("anniversary 302 placed under banner", out)
        self.assertNotIn("(running on JP)", out)

    def test_a_banner_with_no_recorded_end_never_counts_as_running(self):
        # Kitasan Black has no JP end. JP's 2022-03-10 is inside no complete
        # window, so the event is left for an editor rather than guessed.
        self.jp_events.append(snapshot_free_event(
            "anniversary", 999, start="2022-03-10T03:00:00+00:00", rewards=[("card", 30088)]))
        out = self.run_import()
        self.assertIn("no banner found for 1: anniversary 999", out)

    def test_a_game_id_shared_with_a_story_event_is_a_different_row(self):
        story = StoryEvent.objects.create(game_id=7, name="Story event seven",
                                          banner_timeline=self.kitasan)
        self.run_import()
        story.refresh_from_db()
        self.assertEqual((story.kind, story.name), (StoryEventKind.STORY, "Story event seven"))
        self.assertEqual(self.event("main_story", 7).name, "Main Story Act 1, Finale Part 2")

    def test_a_second_run_changes_nothing(self):
        self.run_import()
        self.assertIn("Nothing to write.", self.run_import())

    def test_what_editors_own_survives(self):
        self.run_import()
        finale = self.event("main_story", 7)
        StoryEvent.objects.filter(pk=finale.pk).update(
            banner_timeline=self.kitasan, working_name="Kept", image="story_events/main-story-7.png")
        StoryEventCard.objects.filter(story_event=finale, uma=self.commander).update(
            must_get=True, note="A free 3 star")
        StoryEvent.objects.create(kind=StoryEventKind.SPECIAL, working_name="Welcome to Umayuru")
        self.assertIn("Nothing to write.", self.run_import())
        finale.refresh_from_db()
        self.assertEqual((finale.banner_timeline, finale.working_name, finale.image.name),
                         (self.kitasan, "Kept", "story_events/main-story-7.png"))
        self.assertEqual(finale.cards.get(uma=self.commander).note, "A free 3 star")
        self.assertTrue(StoryEvent.objects.filter(working_name="Welcome to Umayuru").exists())

    def test_a_reward_with_no_row_is_reported_and_skipped(self):
        self.jp_events.append(snapshot_free_event(
            "anniversary", 373, start="2023-08-24T03:00:00+00:00", rewards=[("card", 30162)]))
        out = self.run_import()
        self.assertIn("anniversary 373: support card 30162 has no row; add it and re-run", out)
        self.assertIn("no banner found for 1: anniversary 373", out)
        self.assertFalse(self.event("anniversary", 373).cards.exists())

    def test_global_only_imports_globals_events(self):
        out = self.run_import("--global-only")
        self.assertIn("creating 3, adding 4 free reward(s)", out)
        self.assertEqual(self.event("main_story", 7).jp_name, "")
