"""Story events: the extractor's reward sum, the import, dates, the
/calculator-data payload, the admin, and the two things they must never feed
(income and selector eligibility). notes/story-events-plan.md."""

import datetime
import json
import sqlite3
import tempfile
from pathlib import Path

from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.eligibility import build_first_jp_date_maps
from calculatorapi.models import (
    BannerTimeline, CustomUser, StoryEvent, StoryEventCard, SupportCard,
)
from calculatorapi.predictions import (
    STORY_EVENT_CLAIM_DAYS,
    build_effective_date_maps,
    build_story_event_date_map,
)

from .base import PLAIN_TEST_STORAGES, CalculatorTestCase
from .factories import _dt, _iso, make_timeline
from .test_skills import import_game_data, write_snapshot
from .test_snapshot_scripts import extractor

DAY = datetime.timedelta(days=1)


def story_event_db():
    """An in-memory mdb holding one story event laid out the way the game lays
    out Sweet Memories Teatime (event 1022), which pays 2,160 carats, 2 + 2
    tickets, 3 + 3 shards and five copies of its card."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE text_data (category INTEGER, "index" INTEGER, text TEXT);
        CREATE TABLE story_event_data (story_event_id INTEGER, start_date INTEGER,
            ending_date INTEGER, end_date INTEGER);
        CREATE TABLE story_event_point_reward (story_event_id INTEGER, item_category INTEGER,
            item_id INTEGER, item_num INTEGER);
        CREATE TABLE story_event_roulette_bingo (story_event_id INTEGER, sheet_num INTEGER,
            can_loop INTEGER, reward_set_id INTEGER);
        CREATE TABLE story_event_bingo_reward (reward_set_id INTEGER, item_category INTEGER,
            item_id INTEGER, item_num INTEGER);
        CREATE TABLE story_event_story_data (story_event_id INTEGER,
            add_reward_category_1 INTEGER, add_reward_id_1 INTEGER, add_reward_num_1 INTEGER,
            add_reward_category_2 INTEGER, add_reward_id_2 INTEGER, add_reward_num_2 INTEGER);
        CREATE TABLE story_event_mission (story_event_id INTEGER, item_category INTEGER,
            item_id INTEGER, item_num INTEGER);

        INSERT INTO text_data VALUES (189, 1022, 'Sweet Memories Teatime');
        INSERT INTO story_event_data VALUES (1022, 1794261600, 1795471199, 1795730399);

        -- Points: 1,500 carats in steps, the tickets, 3 rainbow shards, 1 copy.
        INSERT INTO story_event_point_reward VALUES (1022, 90, 43, 1000), (1022, 90, 43, 500),
            (1022, 40, 41, 2), (1022, 40, 111, 2), (1022, 164, 149, 3), (1022, 51, 30133, 1),
            (1022, 91, 59, 232000);
        -- Roulette: sheets 1 and 2 SHARE set 44 and each pays it; sheet 3 pays
        -- set 45; the looping sheet 4 pays nothing that counts.
        INSERT INTO story_event_roulette_bingo VALUES (1022, 1, 0, 44), (1022, 2, 0, 44),
            (1022, 3, 0, 45), (1022, 4, 1, 7);
        INSERT INTO story_event_bingo_reward VALUES (44, 90, 43, 150), (44, 164, 150, 1),
            (44, 51, 30133, 1), (45, 90, 43, 150), (45, 164, 150, 1), (45, 51, 30133, 1),
            (7, 90, 43, 9999);
        -- Story: seven chapters of 30 carats, and the fifth copy on the last.
        INSERT INTO story_event_story_data VALUES (1022, 90, 43, 30, 0, 0, 0),
            (1022, 90, 43, 30, 0, 0, 0), (1022, 90, 43, 30, 0, 0, 0), (1022, 90, 43, 30, 0, 0, 0),
            (1022, 90, 43, 30, 0, 0, 0), (1022, 90, 43, 30, 0, 0, 0), (1022, 90, 43, 30, 0, 0, 0),
            (1022, 51, 30133, 1, 0, 0, 0);
        -- A mission paying event points only.
        INSERT INTO story_event_mission VALUES (1022, 100, 58, 90000);
    """)
    return conn


class StoryEventExtractTests(CalculatorTestCase):

    def test_the_reward_sum_reads_all_four_tables(self):
        [event] = extractor.extract_story_events(story_event_db())
        self.assertEqual(event, {
            "id": 1022,
            "name": "Sweet Memories Teatime",
            "start": "2026-11-09T22:00:00+00:00",
            "end": "2026-11-23T21:59:59+00:00",
            "claim_end": "2026-11-26T21:59:59+00:00",
            "rewards": {"carats": 2160, "uma_tickets": 2, "support_tickets": 2,
                        "ssr_shards": 3, "sr_shards": 3},
            "cards": [30133],
        })

    def test_a_database_without_story_events_gives_none(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.assertIsNone(extractor.extract_story_events(conn))


def snapshot_event(event_id=1022, carats=2160, **overrides):
    """One event as story_events.json carries it: Sweet Memories Teatime by default."""
    return {
        "id": event_id, "name": "Sweet Memories Teatime", "start": "2026-11-09T22:00:00+00:00",
        "end": "2026-11-23T21:59:59+00:00", "claim_end": "2026-11-26T21:59:59+00:00",
        "rewards": {"carats": carats, "uma_tickets": 2, "support_tickets": 2,
                    "ssr_shards": 3, "sr_shards": 3},
        "cards": [30133],
        **overrides,
    }


class StoryEventImportTests(CalculatorTestCase):
    """The import's story event pass: what it creates, what it leaves to editors."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.teatime_banner = make_timeline(
            name="Ines Fujin (Valentine)", global_start_date=_dt(2026, 11, 9, 22),
            global_end_date=_dt(2026, 11, 23, 21, 59, 59), jp_start_date=_dt(2023, 1, 30, 22),
        )
        # An upcoming banner: no Global dates, its JP day is 2023-03-29.
        self.upcoming_banner = make_timeline(
            name="Mejiro Ardan (Ballroom)", jp_start_date=_dt(2023, 3, 29, 22),
            jp_end_date=_dt(2023, 4, 9, 21, 59, 59),
        )
        self.hishi = _card("Hishi Akebono", 30133)
        _card("Tokai Teio", 30140)
        self.global_events = [snapshot_event()]
        self.jp_events = [
            snapshot_event(name="スイートメモリー", start="2023-01-31T03:00:00+00:00",
                           end="2023-02-10T02:59:59+00:00", claim_end="2023-02-13T02:59:59+00:00"),
            # 12:00 on 2023-03-29 in Japan: the upcoming banner's JP day.
            snapshot_event(1024, name="されば君、かなし", start="2023-03-29T03:00:00+00:00",
                           end="2023-04-10T02:59:59+00:00", claim_end="2023-04-13T02:59:59+00:00",
                           cards=[30140]),
        ]

    def run_import(self, *flags):
        global_dir = self.root / "master_snapshot"
        global_dir.mkdir(exist_ok=True)
        write_snapshot(global_dir, [])
        (global_dir / "story_events.json").write_text(json.dumps(self.global_events), encoding="utf-8")
        jp_dir = self.root / "jp_snapshot"
        jp_dir.mkdir(exist_ok=True)
        for name in ("skills", "cards", "card_skills", "support_cards", "support_hints"):
            (jp_dir / f"{name}.json").write_text("[]", encoding="utf-8")
        (jp_dir / "evolutions.json").write_text(
            json.dumps({"outfit": [], "scenario": [], "succession": []}), encoding="utf-8")
        (jp_dir / "story_events.json").write_text(json.dumps(self.jp_events), encoding="utf-8")
        return import_game_data(global_dir, "--jp-snapshot", str(jp_dir), *flags)

    def test_creates_every_event_with_its_banner_and_cards(self):
        out = self.run_import()
        self.assertIn("story events: updating 0, creating 2, adding 2 free card(s)", out)
        teatime = StoryEvent.objects.get(game_id=1022)
        self.assertEqual(teatime.name, "Sweet Memories Teatime")
        self.assertEqual(teatime.jp_name, "スイートメモリー")
        self.assertEqual(teatime.global_claim_end_at, _dt(2026, 11, 26, 21, 59, 59))
        self.assertEqual(teatime.jp_start_at, _dt(2023, 1, 31, 3))
        self.assertEqual(teatime.carat_amount, 2160)
        # Global's start, to the minute.
        self.assertEqual(teatime.banner_timeline, self.teatime_banner)
        self.assertEqual([c.support_card for c in teatime.cards.all()], [self.hishi])
        upcoming = StoryEvent.objects.get(game_id=1024)
        # No Global name, and Global's dates stay empty.
        self.assertEqual((upcoming.name, upcoming.global_start_at), ("", None))
        # JP's start, on the banner's JP day.
        self.assertEqual(upcoming.banner_timeline, self.upcoming_banner)

    def test_a_second_run_changes_nothing(self):
        self.run_import()
        self.assertIn("Nothing to write.", self.run_import())

    def test_the_game_columns_follow_the_snapshot(self):
        self.run_import()
        self.global_events = [snapshot_event(name="Sweet Memories Tea Time")]
        self.run_import()
        self.assertEqual(StoryEvent.objects.get(game_id=1022).name, "Sweet Memories Tea Time")

    def test_rewards_follow_the_files_until_an_editor_changes_one(self):
        self.run_import()
        self.global_events = [snapshot_event(carats=2000)]
        self.run_import()
        self.assertEqual(StoryEvent.objects.get(game_id=1022).carat_amount, 2000)

        StoryEvent.objects.filter(game_id=1022).update(carat_amount=2910, rewards_edited=True)
        self.global_events = [snapshot_event(carats=1800)]
        self.run_import()
        self.assertEqual(StoryEvent.objects.get(game_id=1022).carat_amount, 2910)

    def test_what_editors_own_survives(self):
        self.run_import()
        other = make_timeline(name="The right banner")
        teatime = StoryEvent.objects.get(game_id=1022)
        StoryEvent.objects.filter(pk=teatime.pk).update(banner_timeline=other)
        StoryEvent.objects.filter(game_id=1024).update(working_name="You Are Sareba, I Feel Sad")
        StoryEventCard.objects.filter(story_event=teatime).update(must_get=True, note="Grab it")
        self.assertIn("Nothing to write.", self.run_import())
        teatime.refresh_from_db()
        self.assertEqual(teatime.banner_timeline, other)
        self.assertEqual(StoryEvent.objects.get(game_id=1024).working_name,
                         "You Are Sareba, I Feel Sad")
        self.assertEqual(teatime.cards.get().note, "Grab it")

    def test_a_card_with_no_row_is_reported_and_skipped(self):
        self.jp_events.append(snapshot_event(1030, name="", start="2023-08-01T03:00:00+00:00",
                                             cards=[30999]))
        out = self.run_import()
        self.assertIn("event 1030: support card 30999 has no row; add it and re-run", out)
        self.assertIn("no banner found for 1: 1030", out)
        self.assertFalse(StoryEvent.objects.get(game_id=1030).cards.exists())

    def test_global_only_imports_globals_events(self):
        out = self.run_import("--global-only")
        self.assertIn("creating 1, adding 1 free card(s)", out)
        self.assertEqual(StoryEvent.objects.get().jp_name, "")


def _card(name, game_id, rarity=3, **fields):
    return SupportCard.objects.create(name=name, game_id=game_id, rarity=rarity, **fields)


def _event(game_id=1022, banner=None, **fields):
    return StoryEvent.objects.create(game_id=game_id, banner_timeline=banner, **fields)


def _resolve(event):
    emap = build_effective_date_maps()[BannerTimeline]
    return build_story_event_date_map([event], emap)[event.id]


def _payload():
    return APIClient().get("/calculator-data").json()["story_event_data"]


class StoryEventDateTests(CalculatorTestCase):
    """The game's own instants once Global has run the event, the banner's before."""

    def setUp(self):
        # Sweet Memories Teatime and its banner, as the game has them.
        self.start = _dt(2026, 11, 9, 22)
        self.end = _dt(2026, 11, 23, 21, 59, 59)
        self.banner = make_timeline(
            name="Ines Fujin (Valentine)", global_start_date=self.start, global_end_date=self.end,
        )

    def test_the_game_dates_win_when_global_has_them(self):
        event = _event(
            banner=self.banner,
            global_start_at=self.start, global_end_at=self.end,
            global_claim_end_at=_dt(2026, 11, 26, 21, 59, 59),
        )
        resolved = _resolve(event)
        self.assertEqual(resolved["start_date"], self.start)
        self.assertEqual(resolved["end_date"], self.end)
        self.assertEqual(resolved["claim_end_date"], _dt(2026, 11, 26, 21, 59, 59))
        self.assertFalse(resolved["is_predicted"])

    def test_an_event_global_has_not_run_borrows_its_banner(self):
        resolved = _resolve(_event(banner=self.banner))
        self.assertEqual(resolved["start_date"], self.start)
        self.assertEqual(resolved["end_date"], self.end)
        # Every Global event so far closes its claims three days after it ends.
        self.assertEqual(STORY_EVENT_CLAIM_DAYS, 3 * DAY)
        self.assertEqual(resolved["claim_end_date"], self.end + 3 * DAY)

    def test_a_predicted_banner_makes_a_predicted_event(self):
        # The anchor (confirmed) and an unconfirmed banner a month later on JP.
        make_timeline(name="Anchor", jp_start_date=_dt(2023, 1, 1),
                      jp_end_date=_dt(2023, 1, 10), global_start_date=self.start,
                      global_end_date=self.end)
        upcoming = make_timeline(name="Upcoming", jp_start_date=_dt(2023, 2, 1),
                                 jp_end_date=_dt(2023, 2, 10))
        resolved = _resolve(_event(game_id=1024, banner=upcoming))
        self.assertTrue(resolved["is_predicted"])
        self.assertEqual(resolved["claim_end_date"], resolved["end_date"] + 3 * DAY)

    def test_a_missing_claim_deadline_is_filled_the_same_way(self):
        event = _event(banner=self.banner, global_start_at=self.start, global_end_at=self.end)
        self.assertEqual(_resolve(event)["claim_end_date"], self.end + 3 * DAY)

    def test_no_banner_and_no_dates_resolves_to_nothing(self):
        resolved = _resolve(_event())
        self.assertIsNone(resolved["start_date"])
        self.assertIsNone(resolved["claim_end_date"])


class StoryEventPayloadTests(CalculatorTestCase):
    """What the Timeline reads: `story_event_data` on /calculator-data."""

    def setUp(self):
        self.banner = make_timeline(name="Rice Shower (Halloween)")
        self.bourbon = _card("Mihono Bourbon", 30066, title="[The Ghost Finds Halloween Magic]",
                             purpose="A wit card for long races.", card_type="wit")
        self.event = _event(
            game_id=1006, banner=self.banner, name="Halloween Makeover!", jp_name="ハロウィン",
            carat_amount=2160, uma_ticket_amount=2, support_ticket_amount=2,
            ssr_shard_amount=3, sr_shard_amount=3,
        )
        StoryEventCard.objects.create(
            story_event=self.event, support_card=self.bourbon, must_get=True, note="Worth the grind",
        )

    def test_an_event_carries_its_rewards_dates_and_cards(self):
        [entry] = _payload()
        self.assertEqual(entry["game_id"], 1006)
        self.assertEqual(entry["name"], "Halloween Makeover!")
        self.assertEqual(entry["banner_timeline"], self.banner.id)
        self.assertEqual(
            (entry["carat_amount"], entry["uma_ticket_amount"], entry["support_ticket_amount"],
             entry["ssr_shard_amount"], entry["sr_shard_amount"]),
            (2160, 2, 2, 3, 3),
        )
        self.assertEqual(entry["start_date"], _iso(self.banner.global_start_date))
        self.assertEqual(entry["claim_end_date"], _iso(self.banner.global_end_date + 3 * DAY))
        self.assertEqual(entry["cards"], [{
            "id": self.bourbon.id, "name": "Mihono Bourbon",
            "title": "[The Ghost Finds Halloween Magic]", "image": None,
            # What FramedCard draws a card from; no borderless art uploaded here.
            "image_borderless": None, "card_type": "wit", "rarity": 3,
            "purpose": "A wit card for long races.", "game_id": 30066,
            "must_get": True, "note": "Worth the grind",
        }])

    def test_the_japanese_name_is_never_sent(self):
        self.event.name = ""
        self.event.save()
        [entry] = _payload()
        self.assertEqual(entry["name"], "")
        self.assertNotIn("ハロウィン", str(entry))
        self.assertNotIn("jp_name", entry)

    def test_a_working_name_shows_until_global_names_the_event(self):
        self.event.name = ""
        self.event.working_name = "Make up in Halloween"
        self.event.save()
        self.assertEqual(_payload()[0]["name"], "Make up in Halloween")
        self.event.name = "Halloween Makeover!"
        self.event.save()
        self.assertEqual(_payload()[0]["name"], "Halloween Makeover!")

    def test_a_translated_card_title_is_not_sent(self):
        # UmaTL's titles must be credited where they show, and this row has no
        # credit line: a card Global hasn't titled shows its name alone.
        self.bourbon.title = ""
        self.bourbon.translated_title = "[Halloween Ghost]"
        self.bourbon.save()
        self.assertEqual(_payload()[0]["cards"][0]["title"], "")

    def test_an_event_with_no_banner_is_not_sent(self):
        _event(game_id=1050, name="Somewhere later")
        self.assertEqual([e["game_id"] for e in _payload()], [1006])

    def test_cards_come_in_their_order(self):
        sr = _card("Nice Nature", 20026, rarity=2)
        StoryEventCard.objects.create(story_event=self.event, support_card=sr, order=0)
        StoryEventCard.objects.filter(support_card=self.bourbon).update(order=1)
        public_payload_cache.invalidate()  # a queryset update() skips the signal
        self.assertEqual([c["name"] for c in _payload()[0]["cards"]],
                         ["Nice Nature", "Mihono Bourbon"])

    def test_an_edit_reaches_the_cached_payload(self):
        _payload()
        self.event.carat_amount = 2910
        self.event.save()
        self.assertEqual(_payload()[0]["carat_amount"], 2910)

    def test_more_events_ask_no_more_queries(self):
        def count():
            public_payload_cache.invalidate()
            with CaptureQueriesContext(connection) as queries:
                APIClient().get("/calculator-data")
            return len(queries)

        count()  # warm the per-process caches first
        one = count()
        for number in range(5):
            event = _event(game_id=1100 + number, banner=make_timeline(name=f"B{number}"))
            for card_number in range(2):
                StoryEventCard.objects.create(
                    story_event=event, support_card=_card(f"C{number}{card_number}",
                                                          40000 + number * 10 + card_number))
        self.assertEqual(count(), one)

    def test_one_card_once_per_event(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryEventCard.objects.create(story_event=self.event, support_card=self.bourbon)


class StoryEventsFeedNothingTests(CalculatorTestCase):
    """Display only: an event's card never becomes a dated catalogue card, and
    the event never reaches the income ledger."""

    def setUp(self):
        self.welfare = _card("Hishi Akebono", 30133)
        event = _event(banner=make_timeline(), carat_amount=2160)
        StoryEventCard.objects.create(story_event=event, support_card=self.welfare)

    def test_a_card_on_an_event_has_no_jp_date_for_the_selectors(self):
        self.assertNotIn(self.welfare.id, build_first_jp_date_maps()[1])

    def test_the_card_on_the_wire_carries_no_selector_key(self):
        card = _payload()[0]["cards"][0]
        self.assertFalse({"first_jp_date", "global_release_at", "released_at"} & set(card))

    def test_the_income_ledger_does_not_read_it(self):
        ledger = APIClient().get("/calculator-data").json()["income_ledger"]
        self.assertFalse([row for row in ledger if row.get("carats") == 2160])


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class StoryEventAdminTests(CalculatorTestCase):

    def setUp(self):
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))
        self.event = _event(banner=make_timeline(), name="Sweet Memories Teatime",
                            carat_amount=1800)

    def _post(self, **changes):
        data = {
            "working_name": "", "banner_timeline": self.event.banner_timeline_id,
            "carat_amount": 1800, "uma_ticket_amount": 0, "support_ticket_amount": 0,
            "ssr_shard_amount": 0, "sr_shard_amount": 0,
            "cards-TOTAL_FORMS": 0, "cards-INITIAL_FORMS": 0,
            "cards-MIN_NUM_FORMS": 0, "cards-MAX_NUM_FORMS": 1000,
            **changes,
        }
        url = reverse("admin:calculatorapi_storyevent_change", args=(self.event.pk,))
        return self.client.post(url, data)

    def test_the_pages_render(self):
        self.assertEqual(
            self.client.get(reverse("admin:calculatorapi_storyevent_changelist")).status_code, 200)
        self.assertEqual(self.client.get(
            reverse("admin:calculatorapi_storyevent_change", args=(self.event.pk,))).status_code, 200)

    def test_changing_a_reward_marks_the_rewards_as_set_by_hand(self):
        self.assertEqual(self._post(carat_amount=2160).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.carat_amount, 2160)
        self.assertTrue(self.event.rewards_edited)

    def test_an_edit_elsewhere_leaves_the_rewards_to_the_import(self):
        self.assertEqual(self._post(working_name="Sweet Memory Tea Time").status_code, 302)
        self.event.refresh_from_db()
        self.assertFalse(self.event.rewards_edited)

    def test_the_game_fields_cannot_be_changed_here(self):
        self._post(name="Something else", game_id=9999)
        self.event.refresh_from_db()
        self.assertEqual((self.event.name, self.event.game_id), ("Sweet Memories Teatime", 1022))

    def test_the_card_rows_ask_nothing_per_row(self):
        url = reverse("admin:calculatorapi_storyevent_change", args=(self.event.pk,))
        StoryEventCard.objects.create(story_event=self.event, support_card=_card("A", 30001))
        self.client.get(url)  # warm the per-process caches (content types) first
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(self.client.get(url).status_code, 200)
        for number in range(2, 8):
            StoryEventCard.objects.create(
                story_event=self.event, support_card=_card(f"C{number}", 30000 + number))
        with CaptureQueriesContext(connection) as seven:
            self.client.get(url)
        self.assertEqual(len(one), len(seven))
