"""Date prediction: global dates from JP ones, schedule offsets, events, scenarios and campaigns."""

import datetime

from django.utils import timezone

from calculatorapi.predictions import (
    PREDICTION_FACTOR,
    snap_to_reset,
    GAME_EVENT_END_DATE_BUFFER,
    apply_schedule_offsets,
    compute_effective_dates,
    game_event_effective_dates,
    game_event_confirmed_dates,
    build_effective_date_map,
    build_effective_date_maps,
    build_anniversary_event_date_map,
    build_scenario_date_map,
)
from calculatorapi.models import (
    BannerTimeline,
    ChampionsMeeting, LeagueOfHeroes, GameEvent,
    AnniversaryEventBanner,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_timeline,
    make_champions_meeting,
    make_league_of_heroes,
    make_game_event,
    make_scenario,
    make_anniversary_event,
    _UTC,
    _dt,
    _predicted,
    _predicted_end,
)


class PredictionUnitTests(CalculatorTestCase):
    """Directly exercises compute_effective_dates on plain dicts (no DB)."""

    def test_confirmed_banner_passes_through(self):
        rows = [{
            "id": 1, "jp_start_date": _dt(2025, 1, 1), "jp_end_date": _dt(2025, 1, 8),
            "global_start_date": _dt(2025, 6, 1), "global_end_date": _dt(2025, 6, 8),
        }]
        out = compute_effective_dates(rows)[1]
        self.assertEqual(out["start_date"], _dt(2025, 6, 1))
        self.assertEqual(out["end_date"], _dt(2025, 6, 8))
        self.assertFalse(out["is_predicted"])

    def test_anchor_is_latest_jp_among_confirmed_with_jp(self):
        rows = [
            # confirmed + jp, earlier jp
            {"id": 1, "jp_start_date": _dt(2024, 6, 1), "jp_end_date": _dt(2024, 6, 8),
             "global_start_date": _dt(2025, 5, 1), "global_end_date": _dt(2025, 5, 8)},
            # confirmed + jp, latest jp -> should be the anchor
            {"id": 2, "jp_start_date": _dt(2025, 1, 1), "jp_end_date": _dt(2025, 1, 8),
             "global_start_date": _dt(2025, 6, 1), "global_end_date": _dt(2025, 6, 8)},
            # confirmed but NO jp -> ineligible as anchor
            {"id": 3, "jp_start_date": None, "jp_end_date": None,
             "global_start_date": _dt(2025, 7, 1), "global_end_date": _dt(2025, 7, 8)},
            # target awaiting confirmation
            {"id": 4, "jp_start_date": _dt(2025, 1, 31), "jp_end_date": _dt(2025, 2, 7),
             "global_start_date": None, "global_end_date": None},
        ]
        target = compute_effective_dates(rows)[4]
        # Anchored to id=2 (jp 2025-01-01 / global 2025-06-01). Δjp = 30 days.
        self.assertTrue(target["is_predicted"])
        self.assertEqual(target["start_date"], _predicted(_dt(2025, 6, 1), 30))

    def test_fixed_anchor_worked_example(self):
        rows = [
            {"id": 1, "jp_start_date": _dt(2025, 1, 1), "jp_end_date": _dt(2025, 1, 8),
             "global_start_date": _dt(2025, 6, 1), "global_end_date": _dt(2025, 6, 8)},
            {"id": 2, "jp_start_date": _dt(2025, 1, 31), "jp_end_date": _dt(2025, 2, 7),
             "global_start_date": None, "global_end_date": None},
        ]
        out = compute_effective_dates(rows)[2]
        # Δjp = 30d × 0.664 = 19.92d -> raw 2025-06-20 22:04:48, snapped to the
        # reset of that calendar day, 22:00:00; the banner runs 7d, so the raw
        # end is 06-27 22:04:48, snapped to 21:59:59 on its own day.
        # Deliberately hardcoded: this is the one test that pins the arithmetic,
        # so retuning PREDICTION_FACTOR should fail here and nowhere else.
        self.assertEqual(out["start_date"], datetime.datetime(2025, 6, 20, 22, 0, 0, tzinfo=_UTC))
        self.assertEqual(out["end_date"], datetime.datetime(2025, 6, 27, 21, 59, 59, tzinfo=_UTC))
        self.assertTrue(out["is_predicted"])

    def test_no_anchor_leaves_jp_only_rows_unresolved(self):
        rows = [{
            "id": 1, "jp_start_date": _dt(2025, 1, 1), "jp_end_date": _dt(2025, 1, 8),
            "global_start_date": None, "global_end_date": None,
        }]
        out = compute_effective_dates(rows)[1]
        self.assertIsNone(out["start_date"])
        self.assertIsNone(out["end_date"])
        self.assertFalse(out["is_predicted"])

    def test_target_with_no_jp_and_no_global_is_unresolved(self):
        rows = [
            {"id": 1, "jp_start_date": _dt(2025, 1, 1), "jp_end_date": _dt(2025, 1, 8),
             "global_start_date": _dt(2025, 6, 1), "global_end_date": _dt(2025, 6, 8)},
            {"id": 2, "jp_start_date": None, "jp_end_date": None,
             "global_start_date": None, "global_end_date": None},
        ]
        out = compute_effective_dates(rows)[2]
        self.assertIsNone(out["start_date"])
        self.assertFalse(out["is_predicted"])

    def test_snap_to_reset_takes_the_calendar_day_then_a_fixed_time(self):
        # The sheet's rule: datetime -> date -> fixed time. NOT nearest reset:
        # 07:36 on the 10th is closer to the 9th's 22:00, and still snaps to
        # the 10th's, because that is the calendar day it fell on.
        early = datetime.datetime(2026, 11, 10, 7, 36, 0, tzinfo=_UTC)
        late = datetime.datetime(2026, 12, 21, 23, 55, 12, tzinfo=_UTC)
        self.assertEqual(snap_to_reset(early), _dt(2026, 11, 10, 22, 0, 0))
        self.assertEqual(snap_to_reset(late), _dt(2026, 12, 21, 22, 0, 0))
        self.assertEqual(snap_to_reset(early, end=True), _dt(2026, 11, 10, 21, 59, 59))
        self.assertEqual(snap_to_reset(late, end=True), _dt(2026, 12, 21, 21, 59, 59))
        # Already on the reset: unchanged.
        self.assertEqual(snap_to_reset(_dt(2026, 11, 10, 22, 0, 0)), _dt(2026, 11, 10, 22, 0, 0))
        self.assertEqual(snap_to_reset(_dt(2026, 11, 10, 21, 59, 59), end=True),
                         _dt(2026, 11, 10, 21, 59, 59))

    def test_predicted_window_is_reset_aligned_and_keeps_the_jp_run_length(self):
        # Live-shaped rows: JP and global windows both run 22:00 -> 21:59:59.
        # Whatever fraction the factor produces, the predicted window must come
        # out 22:00:00 -> 21:59:59 and span the same number of game days as
        # the JP window (10 here).
        rows = [
            {"id": 1, "jp_start_date": _dt(2023, 1, 20, 22), "jp_end_date": _dt(2023, 1, 30, 21, 59, 59),
             "global_start_date": _dt(2026, 11, 4, 22), "global_end_date": _dt(2026, 11, 14, 21, 59, 59)},
            {"id": 2, "jp_start_date": _dt(2023, 3, 20, 22), "jp_end_date": _dt(2023, 3, 30, 21, 59, 59),
             "global_start_date": None, "global_end_date": None},
        ]
        out = compute_effective_dates(rows, prediction_factor=0.64)[2]
        # Δjp = 59d × 0.64 = 37.76d -> raw 2026-12-12 16:14:24.
        self.assertEqual(out["start_date"], _dt(2026, 12, 12, 22, 0, 0))
        self.assertEqual(out["end_date"], _dt(2026, 12, 22, 21, 59, 59))
        # A confirmed row is a fact and is never touched, whatever its time.
        self.assertEqual(compute_effective_dates(rows)[1]["start_date"], _dt(2026, 11, 4, 22))

    def test_negative_delta_predicts_before_anchor(self):
        rows = [
            {"id": 1, "jp_start_date": _dt(2025, 3, 1), "jp_end_date": _dt(2025, 3, 8),
             "global_start_date": _dt(2025, 8, 1), "global_end_date": _dt(2025, 8, 8)},
            # target's jp is BEFORE the anchor's jp -> predicted start before anchor global
            {"id": 2, "jp_start_date": _dt(2025, 1, 30), "jp_end_date": _dt(2025, 2, 6),
             "global_start_date": None, "global_end_date": None},
        ]
        out = compute_effective_dates(rows)[2]
        # Δjp = -30d × 0.664 = -19.92d -> raw 2025-07-12 01:55:12, snapped to
        # 2025-07-12 22:00:00.
        self.assertEqual(out["start_date"], _predicted(_dt(2025, 8, 1), -30))
        self.assertTrue(out["is_predicted"])

    def test_predicted_start_but_null_jp_end_gives_null_end(self):
        rows = [
            {"id": 1, "jp_start_date": _dt(2025, 1, 1), "jp_end_date": _dt(2025, 1, 8),
             "global_start_date": _dt(2025, 6, 1), "global_end_date": _dt(2025, 6, 8)},
            {"id": 2, "jp_start_date": _dt(2025, 1, 31), "jp_end_date": None,
             "global_start_date": None, "global_end_date": None},
        ]
        out = compute_effective_dates(rows)[2]
        self.assertEqual(out["start_date"], _predicted(_dt(2025, 6, 1), 30))
        self.assertIsNone(out["end_date"])
        self.assertTrue(out["is_predicted"])


def _entry(start, end=None, is_predicted=True, offset_days=0, anchor_start=None):
    """Build one effective-date map entry by hand, matching the shape
    compute_effective_dates produces.

    `anchor_start` defaults to None — i.e. "this map records no anchor", which
    is how a hand-built map behaves, so the tests below that don't care about
    anchors read exactly as they did before it existed.
    """
    return {
        "start_date": start,
        "end_date": end,
        "is_predicted": is_predicted,
        "offset_days": offset_days,
        "applied_offset_days": 0,
        "anchor_start": anchor_start,
    }


class ScheduleOffsetUnitTests(CalculatorTestCase):
    """Directly exercises apply_schedule_offsets on hand-built maps (no DB).

    An offset pushes its own row AND every dated row after it, across every
    content type at once, and offsets stack.
    """

    def test_offset_shifts_the_row_carrying_it(self):
        emap = {1: _entry(_dt(2025, 8, 24), _dt(2025, 8, 31), offset_days=7)}
        apply_schedule_offsets([emap])
        self.assertEqual(emap[1]['start_date'], _dt(2025, 8, 31))
        self.assertEqual(emap[1]['applied_offset_days'], 7)

    def test_rows_before_the_offset_are_unchanged(self):
        emap = {
            1: _entry(_dt(2025, 8, 10)),
            2: _entry(_dt(2025, 8, 24), offset_days=7),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[1]['start_date'], _dt(2025, 8, 10))
        self.assertEqual(emap[1]['applied_offset_days'], 0)

    def test_rows_after_the_offset_shift_by_the_same_amount(self):
        emap = {
            1: _entry(_dt(2025, 8, 24), offset_days=7),
            2: _entry(_dt(2025, 9, 7)),
            3: _entry(_dt(2025, 9, 21)),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[2]['start_date'], _dt(2025, 9, 14))
        self.assertEqual(emap[3]['start_date'], _dt(2025, 9, 28))

    def test_offsets_stack(self):
        emap = {
            1: _entry(_dt(2025, 8, 24), offset_days=7),
            2: _entry(_dt(2025, 9, 12), offset_days=3),
            3: _entry(_dt(2025, 9, 21)),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[1]['applied_offset_days'], 7)
        # Row 2 gets row 1's +7 as well as its own +3.
        self.assertEqual(emap[2]['applied_offset_days'], 10)
        self.assertEqual(emap[2]['start_date'], _dt(2025, 9, 22))
        self.assertEqual(emap[3]['applied_offset_days'], 10)
        self.assertEqual(emap[3]['start_date'], _dt(2025, 10, 1))

    def test_end_date_shifts_by_the_same_amount(self):
        emap = {1: _entry(_dt(2025, 8, 24), _dt(2025, 8, 31), offset_days=7)}
        apply_schedule_offsets([emap])
        # Both ends move together, so the run length is preserved.
        self.assertEqual(emap[1]['start_date'], _dt(2025, 8, 31))
        self.assertEqual(emap[1]['end_date'], _dt(2025, 9, 7))

    def test_null_end_date_stays_null(self):
        emap = {1: _entry(_dt(2025, 8, 24), None, offset_days=7)}
        apply_schedule_offsets([emap])
        self.assertEqual(emap[1]['start_date'], _dt(2025, 8, 31))
        self.assertIsNone(emap[1]['end_date'])

    def test_confirmed_row_after_an_offset_is_never_shifted(self):
        emap = {
            1: _entry(_dt(2025, 8, 24), offset_days=7),
            2: _entry(_dt(2025, 9, 7), is_predicted=False),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[2]['start_date'], _dt(2025, 9, 7))
        self.assertEqual(emap[2]['applied_offset_days'], 0)

    def test_confirmed_rows_own_offset_does_not_cascade(self):
        """The self-healing property. Once a slipped row is confirmed it becomes
        the anchor, so its real date already carries the slip — a still-live
        offset would count the same delay twice."""
        emap = {
            1: _entry(_dt(2025, 8, 24), is_predicted=False, offset_days=7),
            2: _entry(_dt(2025, 9, 7)),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[2]['start_date'], _dt(2025, 9, 7))
        self.assertEqual(emap[2]['applied_offset_days'], 0)

    def test_offset_crosses_content_types(self):
        """One shared calendar: a banner offset moves later Champions Meetings
        and League of Heroes events too, unlike anchors which stay per-model."""
        banners = {1: _entry(_dt(2025, 8, 24), offset_days=7)}
        meetings = {1: _entry(_dt(2025, 9, 2))}
        leagues = {1: _entry(_dt(2025, 9, 5))}
        apply_schedule_offsets([banners, meetings, leagues])
        self.assertEqual(meetings[1]['start_date'], _dt(2025, 9, 9))
        self.assertEqual(leagues[1]['start_date'], _dt(2025, 9, 12))

    def test_offset_from_another_content_type_stacks(self):
        banners = {1: _entry(_dt(2025, 8, 24), offset_days=7), 2: _entry(_dt(2025, 9, 21))}
        leagues = {1: _entry(_dt(2025, 9, 12), offset_days=3)}
        apply_schedule_offsets([banners, leagues])
        # The banner at 2025-09-21 is behind both the banner +7 and the LoH +3.
        self.assertEqual(banners[2]['applied_offset_days'], 10)
        self.assertEqual(banners[2]['start_date'], _dt(2025, 10, 1))

    def test_unresolved_rows_are_untouched(self):
        emap = {
            1: _entry(_dt(2025, 8, 24), offset_days=7),
            2: _entry(None, None, is_predicted=False),
        }
        apply_schedule_offsets([emap])
        self.assertIsNone(emap[2]['start_date'])
        self.assertEqual(emap[2]['applied_offset_days'], 0)

    def test_offset_on_a_row_with_no_resolved_date_is_ignored(self):
        """Nothing to order it against, so it can't say what comes 'after' it."""
        emap = {
            1: _entry(None, None, is_predicted=False, offset_days=7),
            2: _entry(_dt(2025, 9, 7)),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[2]['start_date'], _dt(2025, 9, 7))

    def test_negative_offset_pulls_dates_earlier(self):
        emap = {
            1: _entry(_dt(2025, 8, 24), offset_days=-7),
            2: _entry(_dt(2025, 9, 7)),
        }
        apply_schedule_offsets([emap])
        self.assertEqual(emap[1]['start_date'], _dt(2025, 8, 17))
        self.assertEqual(emap[2]['start_date'], _dt(2025, 8, 31))

    def test_no_offsets_leaves_everything_alone(self):
        emap = {1: _entry(_dt(2025, 8, 24), _dt(2025, 8, 31))}
        apply_schedule_offsets([emap])
        self.assertEqual(emap[1]['start_date'], _dt(2025, 8, 24))
        self.assertEqual(emap[1]['end_date'], _dt(2025, 8, 31))
        self.assertEqual(emap[1]['applied_offset_days'], 0)

    def test_offset_at_or_before_the_targets_anchor_is_not_applied(self):
        """The anchor's global date is a fact, and the prediction was measured
        forward from it — so a slip that happened before it is already inside
        the number being shifted here. Applying it again double-counts."""
        banners = {1: _entry(_dt(2025, 8, 24), offset_days=7)}
        # Predicted from an anchor that sits AFTER the banner slip.
        leagues = {1: _entry(_dt(2025, 10, 1), anchor_start=_dt(2025, 9, 1))}
        apply_schedule_offsets([banners, leagues])
        self.assertEqual(leagues[1]['start_date'], _dt(2025, 10, 1))
        self.assertEqual(leagues[1]['applied_offset_days'], 0)

    def test_offset_after_the_targets_anchor_still_applies(self):
        """The other half of the rule: a slip the anchor could not have known
        about is genuinely new, so it must still cascade."""
        banners = {1: _entry(_dt(2025, 9, 15), offset_days=7)}
        leagues = {1: _entry(_dt(2025, 10, 1), anchor_start=_dt(2025, 9, 1))}
        apply_schedule_offsets([banners, leagues])
        self.assertEqual(leagues[1]['start_date'], _dt(2025, 10, 8))
        self.assertEqual(leagues[1]['applied_offset_days'], 7)

    def test_anchor_filter_is_per_map_not_global(self):
        """Each map is filtered by its OWN anchor. A banner predicted off an
        early anchor still takes the slip that a later-anchored LoH ignores."""
        banners = {
            1: _entry(_dt(2025, 8, 24), offset_days=7, anchor_start=_dt(2025, 6, 1)),
            2: _entry(_dt(2025, 10, 1), anchor_start=_dt(2025, 6, 1)),
        }
        leagues = {1: _entry(_dt(2025, 10, 1), anchor_start=_dt(2025, 9, 1))}
        apply_schedule_offsets([banners, leagues])
        self.assertEqual(banners[2]['applied_offset_days'], 7)
        self.assertEqual(leagues[1]['applied_offset_days'], 0)

    def test_build_effective_date_maps_does_not_double_count_a_late_anchor(self):
        """End-to-end regression for the League of Heroes shape: a model whose
        only globally-dated row sits AFTER a banner slip, with that row's date
        taken from the already-slip-corrected calendar. Every later row of that
        model used to inherit the slip through the anchor and then be shifted by
        it again — a constant ~7-day lateness against the source spreadsheet."""
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_timeline(
            name='Slipped',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
            schedule_offset_days=7,
        )
        # The LoH anchor's global date is late enough to already contain the
        # banner's +7 (the banner resolves to ~2025-06-28 once shifted).
        make_league_of_heroes(
            name='LoH Anchor',
            jp_start_date=_dt(2025, 2, 1), jp_end_date=_dt(2025, 2, 8),
            global_start_date=_dt(2025, 8, 1), global_end_date=_dt(2025, 8, 8),
        )
        make_league_of_heroes(
            name='LoH Later',
            jp_start_date=_dt(2025, 3, 3), jp_end_date=_dt(2025, 3, 10),
        )

        maps = build_effective_date_maps()
        later = maps[LeagueOfHeroes][LeagueOfHeroes.objects.get(name='LoH Later').id]

        # Pure anchor math, with NO offset on top.
        self.assertEqual(later['start_date'], _predicted(_dt(2025, 8, 1), 30))
        self.assertEqual(later['applied_offset_days'], 0)

    def test_build_effective_date_maps_applies_offsets_across_models(self):
        """The ORM wrapper: per-model anchors, one shared offset pass."""
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        # Predicted off the banner anchor, then pushed 7 days by its own offset.
        make_timeline(
            name='Slipped',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
            schedule_offset_days=7,
        )
        # A Champions Meeting predicted off its OWN anchor to a date after the
        # banner slip, so it inherits the +7 on top.
        make_champions_meeting(
            name='CM Anchor', cm_number=1,
            jp_start_date=_dt(2025, 2, 1), jp_end_date=_dt(2025, 2, 8),
            global_start_date=_dt(2025, 6, 10), global_end_date=_dt(2025, 6, 17),
        )
        make_champions_meeting(
            name='CM Later', cm_number=2,
            jp_start_date=_dt(2025, 3, 3), jp_end_date=_dt(2025, 3, 10),
        )

        maps = build_effective_date_maps()
        banner = maps[BannerTimeline][BannerTimeline.objects.get(name='Slipped').id]
        meeting = maps[ChampionsMeeting][ChampionsMeeting.objects.get(name='CM Later').id]

        self.assertEqual(banner['start_date'], _predicted(_dt(2025, 6, 1), 30, offset_days=7))
        self.assertEqual(banner['applied_offset_days'], 7)
        self.assertEqual(meeting['start_date'], _predicted(_dt(2025, 6, 10), 30, offset_days=7))
        self.assertEqual(meeting['applied_offset_days'], 7)


class GameEventPredictionTests(CalculatorTestCase):
    """game_event_effective_dates/game_event_confirmed_dates: GameEvent has no
    date fields of its own, so these resolve purely via the linked
    BannerTimeline's own (already-built) effective-date map."""

    def test_confirmed_banner_gives_end_date_plus_buffer(self):
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        event = make_game_event(banner_timeline=timeline)
        emap = build_effective_date_map()
        out = game_event_effective_dates(event, emap)
        self.assertEqual(out['start_date'], _dt(2025, 6, 1))
        self.assertEqual(out['end_date'], _dt(2025, 6, 8) + GAME_EVENT_END_DATE_BUFFER)
        self.assertFalse(out['is_predicted'])

    def test_predicted_banner_propagates_is_predicted(self):
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted_tl = make_timeline(
            name='Predicted',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        event = make_game_event(banner_timeline=predicted_tl)
        emap = build_effective_date_map()
        out = game_event_effective_dates(event, emap)
        # Banner runs 7d from its predicted start; the event's end trails by the buffer.
        predicted_start = _predicted(_dt(2025, 6, 1), 30)
        self.assertTrue(out['is_predicted'])
        self.assertEqual(out['start_date'], predicted_start)
        self.assertEqual(out['end_date'],
                         _predicted_end(_dt(2025, 6, 1), 30, 7) + GAME_EVENT_END_DATE_BUFFER)

    def test_unlinked_event_resolves_to_null(self):
        event = make_game_event(banner_timeline=None)
        emap = build_effective_date_map()
        out = game_event_effective_dates(event, emap)
        self.assertIsNone(out['start_date'])
        self.assertIsNone(out['end_date'])
        self.assertFalse(out['is_predicted'])

    def test_linked_but_unresolvable_banner_resolves_to_null(self):
        # A banner with neither JP nor global dates has no resolvable entry.
        timeline = make_timeline(name='No dates at all')
        timeline.global_start_date = None
        timeline.global_end_date = None
        timeline.save()
        event = make_game_event(banner_timeline=timeline)
        emap = build_effective_date_map()
        out = game_event_effective_dates(event, emap)
        self.assertIsNone(out['start_date'])
        self.assertFalse(out['is_predicted'])

    def test_confirmed_dates_never_predicts(self):
        # game_event_confirmed_dates (used by the standalone /events route)
        # must show null rather than a prediction for a JP-only banner.
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted_tl = make_timeline(
            name='Predicted',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        event = make_game_event(banner_timeline=predicted_tl)
        event = GameEvent.objects.select_related('banner_timeline').get(pk=event.pk)
        out = game_event_confirmed_dates(event)
        self.assertIsNone(out['start_date'])
        self.assertFalse(out['is_predicted'])

    def test_confirmed_dates_reads_raw_banner_dates(self):
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        event = make_game_event(banner_timeline=timeline)
        event = GameEvent.objects.select_related('banner_timeline').get(pk=event.pk)
        out = game_event_confirmed_dates(event)
        self.assertEqual(out['start_date'], _dt(2025, 6, 1))
        self.assertEqual(out['end_date'], _dt(2025, 6, 8) + GAME_EVENT_END_DATE_BUFFER)
        self.assertFalse(out['is_predicted'])

    def test_confirmed_dates_unlinked_is_null(self):
        event = make_game_event(banner_timeline=None)
        out = game_event_confirmed_dates(event)
        self.assertIsNone(out['start_date'])
        self.assertFalse(out['is_predicted'])


class GameEventBannerTimelineDeletionTests(CalculatorTestCase):
    """GameEvent.banner_timeline is SET_NULL, not CASCADE -- an event's own
    content (image, reward amounts) outlives its linked banner."""

    def test_deleting_banner_timeline_sets_game_event_banner_timeline_null(self):
        timeline = make_timeline(name='Doomed Banner')
        event = make_game_event(name='Survives', banner_timeline=timeline, carat_amount=100)

        timeline.delete()

        event.refresh_from_db()
        self.assertIsNone(event.banner_timeline_id)
        self.assertTrue(GameEvent.objects.filter(pk=event.pk).exists())
        self.assertEqual(event.carat_amount, 100)


class ScenarioDateTests(CalculatorTestCase):
    """A scenario borrows its launch banner's START, and has no end at all."""

    def test_start_comes_from_the_launch_banner_and_there_is_no_end(self):
        now = timezone.now()
        banner = make_timeline(
            name='Launch banner', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=14),
        )
        scenario = make_scenario(banner_timeline=banner)

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_scenario_date_map([scenario], emap)[scenario.id]

        self.assertEqual(resolved['start_date'], banner.global_start_date)
        # The banner ends; the scenario does not. Borrowing the banner's end
        # would invent an expiry the scenario has never had.
        self.assertIsNone(resolved['end_date'])
        self.assertFalse(resolved['is_predicted'])

    def test_prediction_and_offset_propagate_from_the_banner(self):
        now = timezone.now()
        # Anchor: a confirmed JP+global pair the predictor can measure from.
        make_timeline(
            name='Anchor',
            jp_start_date=timezone.make_aware(datetime.datetime(2022, 1, 1)),
            jp_end_date=timezone.make_aware(datetime.datetime(2022, 1, 14)),
            global_start_date=now, global_end_date=now + datetime.timedelta(days=14),
        )
        predicted = make_timeline(
            name='Predicted launch banner',
            jp_start_date=timezone.make_aware(datetime.datetime(2022, 6, 1)),
            jp_end_date=timezone.make_aware(datetime.datetime(2022, 6, 14)),
            global_start_date=None, global_end_date=None,
        )
        scenario = make_scenario(banner_timeline=predicted)

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_scenario_date_map([scenario], emap)[scenario.id]

        self.assertTrue(resolved['is_predicted'])
        self.assertEqual(resolved['start_date'], emap[predicted.id]['start_date'])
        self.assertEqual(
            resolved['applied_offset_days'], emap[predicted.id]['applied_offset_days']
        )
        self.assertIsNone(resolved['end_date'])

    def test_unlinked_scenario_resolves_to_a_null_start(self):
        scenario = make_scenario(banner_timeline=None)
        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_scenario_date_map([scenario], emap)[scenario.id]

        self.assertIsNone(resolved['start_date'])
        self.assertIsNone(resolved['end_date'])
        self.assertFalse(resolved['is_predicted'])


class AnniversaryEventDateTests(CalculatorTestCase):
    """A campaign spans its banner parts rather than owning dates."""

    def test_dates_span_earliest_start_to_latest_end(self):
        now = timezone.now()
        part1 = make_timeline(
            name='Part 1', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=10),
        )
        part2 = make_timeline(
            name='Part 2', global_start_date=now + datetime.timedelta(days=5),
            global_end_date=now + datetime.timedelta(days=30),
        )
        event = make_anniversary_event(parts=[part1, part2])

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertEqual(resolved['start_date'], part1.global_start_date)
        self.assertEqual(resolved['end_date'], part2.global_end_date)
        self.assertFalse(resolved['is_predicted'])

    def test_one_predicted_part_makes_the_whole_campaign_predicted(self):
        now = timezone.now()
        # Anchor: a confirmed JP+global pair the predictor can measure from.
        make_timeline(
            name='Anchor',
            jp_start_date=timezone.make_aware(datetime.datetime(2022, 1, 1)),
            jp_end_date=timezone.make_aware(datetime.datetime(2022, 1, 14)),
            global_start_date=now, global_end_date=now + datetime.timedelta(days=14),
        )
        confirmed = make_timeline(
            name='Confirmed part', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=10),
        )
        predicted = make_timeline(
            name='Predicted part',
            jp_start_date=timezone.make_aware(datetime.datetime(2022, 6, 1)),
            jp_end_date=timezone.make_aware(datetime.datetime(2022, 6, 14)),
            global_start_date=None, global_end_date=None,
        )
        event = make_anniversary_event(parts=[confirmed, predicted])

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertTrue(resolved['is_predicted'])

    def test_campaign_with_no_parts_resolves_to_null_dates(self):
        event = make_anniversary_event(parts=[])
        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertIsNone(resolved['start_date'])
        self.assertIsNone(resolved['main_start_date'])
        self.assertIsNone(resolved['end_date'])
        self.assertFalse(resolved['is_predicted'])

    def test_main_start_date_is_part_2_for_an_anniversary(self):
        """Part 1 is the run-up; the anniversary itself is Part 2."""
        now = timezone.now()
        part1 = make_timeline(
            name='Run-up', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=10),
        )
        part2 = make_timeline(
            name='The anniversary',
            global_start_date=now + datetime.timedelta(days=10),
            global_end_date=now + datetime.timedelta(days=30),
        )
        event = make_anniversary_event(parts=[part1, part2])

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        # The campaign still OPENS at Part 1 -- packs and the run-up rewards are
        # real -- but the event it is named after starts at Part 2.
        self.assertEqual(resolved['start_date'], part1.global_start_date)
        self.assertEqual(resolved['main_start_date'], part2.global_start_date)
        self.assertEqual(resolved['end_date'], part2.global_end_date)

    def test_main_start_date_picks_part_2_by_number_not_by_date(self):
        """The 5th Anniversary's Part 4 opens before its Part 3.

        Concurrent banners, which is how the source sheet records them. Ordering
        the parts by date would therefore not put Part 2 second, so the selection
        has to key on part_number.
        """
        now = timezone.now()
        timelines = [
            make_timeline(
                name=f'Part {number}',
                global_start_date=now + datetime.timedelta(days=offset),
                global_end_date=now + datetime.timedelta(days=offset + 10),
            )
            for number, offset in [(1, 0), (2, 10), (3, 30), (4, 20)]
        ]
        event = make_anniversary_event(parts=[])
        for number, timeline in enumerate(timelines, start=1):
            AnniversaryEventBanner.objects.create(
                anniversary_event=event, banner_timeline=timeline,
                part_number=number,
            )

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertEqual(resolved['main_start_date'], timelines[1].global_start_date)

    def test_main_start_date_falls_back_when_there_is_no_part_2(self):
        """The 0.5th Anniversary's shape: its Part 2 banner has no timeline row.

        Only the Part 3 link resolves, and that one part is the whole campaign as
        far as the app can see -- so it supplies both dates rather than leaving
        the campaign unplaceable.
        """
        now = timezone.now()
        part3 = make_timeline(
            name='Only surviving part', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=11),
        )
        event = make_anniversary_event(parts=[])
        AnniversaryEventBanner.objects.create(
            anniversary_event=event, banner_timeline=part3, part_number=3,
        )

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertEqual(resolved['main_start_date'], part3.global_start_date)
        self.assertEqual(resolved['start_date'], part3.global_start_date)

    def test_main_start_date_falls_back_when_only_a_part_1_is_linked(self):
        now = timezone.now()
        part1 = make_timeline(
            name='Run-up only', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=10),
        )
        event = make_anniversary_event(parts=[part1])

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertEqual(resolved['main_start_date'], part1.global_start_date)

    def test_a_new_year_campaign_keeps_its_opening_as_its_main_start(self):
        """Only anniversaries run a Part 1 run-up.

        A New Year campaign's Part 1 IS the New Year banner (New Years 2025 =
        Katsuragi Ace + Mr. C.B.), so moving it to Part 2 would place it on the
        follow-up banner instead of the event.
        """
        now = timezone.now()
        part1 = make_timeline(
            name='New Year banner', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=14),
        )
        part2 = make_timeline(
            name='Follow-up',
            global_start_date=now + datetime.timedelta(days=9),
            global_end_date=now + datetime.timedelta(days=20),
        )
        event = make_anniversary_event(
            name='New Years 2025', event_type='new_year', parts=[part1, part2],
        )

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_anniversary_event_date_map([event], emap)[event.id]

        self.assertEqual(resolved['main_start_date'], part1.global_start_date)
        self.assertEqual(resolved['start_date'], part1.global_start_date)
