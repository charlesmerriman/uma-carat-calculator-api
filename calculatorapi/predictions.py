"""
Global-server date prediction for JP-first content (banner timelines,
Champions Meetings, League of Heroes events).

The site plans pulls on the GLOBAL server, but global dates are only confirmed
~1 month out. Beyond that horizon we predict global dates from the
(always-known) JP schedule.

This module is pure query logic — the math lives in `compute_effective_dates`
(DB-free, unit-tested directly), with a thin ORM wrapper `build_effective_date_map`
that feeds it — mirroring the split in the `analytics` package. `compute_effective_dates`
is model-agnostic (duck-typed, id-keyed), so any content type with the same
jp_*/global_* date fields can reuse it; each model is resolved into its OWN map
(its own anchor set) — rows are never mixed across models.

Prediction model (fixed anchor):
- The "anchor" is the row with the greatest jp_start_date among rows that have
  BOTH a confirmed global_start_date AND a jp_start_date.
- For a row awaiting confirmation (global_start_date is null) with a jp_start_date:
      predicted_global_start = anchor.global_start_date
                               + (target.jp_start_date - anchor.jp_start_date) * FACTOR
      predicted_global_end   = predicted_global_start
                               + (target.jp_end_date - target.jp_start_date)
  The factor reflects global historically running content faster than JP. It is
  admin-editable (CalculationConstants.prediction_factor; 0.64 on the live site
  as of 2026-10-06), and PREDICTION_FACTOR below is only the fallback.
- Both predicted instants are then SNAPPED to the daily reset: the start to
  22:00:00 UTC and the end to 21:59:59 UTC on the UTC calendar day each raw
  instant falls on (`snap_to_reset`). Raw results carry a fractional time of
  day (0.64 x a whole number of days), which no real banner has, and which made
  the displayed day flip per viewer timezone on a random subset of rows. The
  source sheet does the same: datetime -> date -> fixed time.
- Confirmed rows pass through unchanged with is_predicted=False.
- Rows with no usable dates (or when no anchor exists) resolve to
  (None, None, False).

Schedule offsets (a second, separate layer on top of the above):
- The 0.664 factor assumes global keeps a steady pace. When it doesn't (a delayed
  banner, an inserted break week), every prediction after the slip is wrong by
  the same number of days. `schedule_offset_days` on a row is the manual fix.
- `apply_schedule_offsets` pushes each offset-carrying row AND every dated row
  after it forward by that many days; offsets stack. Unlike anchors, this pass
  spans ALL content types at once — one shared calendar — so an offset set on a
  banner also moves later Champions Meetings and League of Heroes events.
- Only predicted rows take part, as source or target. See that function for why
  that is what stops an offset double-counting once its row is confirmed.
- Offsets at or before a model's OWN anchor are skipped: the prediction was
  measured from that anchor's confirmed date, which already embodies every slip
  up to itself. Again, see that function.
"""

from datetime import datetime, timedelta

from .models import (
    BannerTimeline,
    CalculationConstants,
    ChampionsMeeting,
    LeagueOfHeroes,
)

# GameEvent's end date always trails its linked banner's end date by this much.
# This is the DEFAULT only — the live value is admin-editable (see
# CalculationConstants.game_event_end_buffer_days). It survives as the fallback
# for the pure, DB-free functions below, which take it as a parameter so they
# stay unit-testable without fixtures.
GAME_EVENT_END_DATE_BUFFER = timedelta(days=4)


def game_event_end_buffer():
    """The configured game-event end buffer, as a timedelta."""
    return timedelta(days=CalculationConstants.load().game_event_end_buffer_days)

# Sentinel used only to order rows that have no resolved start date; it never
# gets compared against a real datetime (the leading bool flag separates the
# two groups), so a naive value is safe here.
_NO_DATE_SENTINEL = datetime.max

# Global covers JP's back-catalogue faster than real time; each day of JP gap
# maps to ~0.664 days of global gap.
#
# DEFAULT only, same as the buffer above: the live value is admin-editable via
# CalculationConstants.prediction_factor, so retuning the cadence no longer needs
# a deploy. This constant is what the pure functions fall back to when called
# without one.
PREDICTION_FACTOR = 0.664


# The global server's daily reset, UTC. Every confirmed window runs
# 22:00:00 -> 21:59:59, so a predicted one is made to as well.
RESET_START_OF_DAY = timedelta(hours=22)
RESET_END_OF_DAY = timedelta(hours=21, minutes=59, seconds=59)


def snap_to_reset(instant, *, end=False):
    """Move a predicted instant onto the daily reset of the UTC calendar day
    it falls on: 22:00:00 for a start, 21:59:59 for an end.

    The rule is the source sheet's (take the date, add a fixed time), chosen
    over rounding to the nearest reset so the two schedules agree row for row.
    The rule is applied to each end of a window independently; because JP
    windows are themselves reset-aligned, the predicted window keeps the JP
    run length in game days.
    """
    day = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    return day + (RESET_END_OF_DAY if end else RESET_START_OF_DAY)


def _get(row, key):
    """Read `key` from either a .values() dict or a model instance (duck-typed
    so the pure function works in DB-free unit tests)."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key)


def compute_effective_dates(rows, *, prediction_factor=PREDICTION_FACTOR):
    """
    Resolve each timeline's effective global dates.

    rows: iterable of dicts or objects exposing `id`, `jp_start_date`,
          `jp_end_date`, `global_start_date`, `global_end_date` and
          (optionally) `schedule_offset_days`.

    `prediction_factor` defaults to the module constant so this stays pure and
    DB-free for direct unit tests; the ORM wrappers below pass the admin-editable
    value from CalculationConstants instead.

    Returns: { id: {"start_date": dt|None, "end_date": dt|None,
                    "is_predicted": bool, "offset_days": int,
                    "applied_offset_days": int, "anchor_start": dt|None} }

    These are the BASE dates — `offset_days` is carried through untouched and
    `applied_offset_days` starts at 0. Run `apply_schedule_offsets` over the
    finished maps to actually shift anything (see the module docstring).

    `anchor_start` is this model's anchor's confirmed global start, stamped on
    every entry so the (model-blind) offset pass can tell where each row's
    prediction was measured from. Diagnostic to callers; load-bearing to
    `apply_schedule_offsets`.
    """
    rows = list(rows)

    # Anchor = latest-JP banner that is BOTH confirmed on global AND has a JP date.
    anchor = None
    for row in rows:
        jp_start = _get(row, "jp_start_date")
        global_start = _get(row, "global_start_date")
        if jp_start is None or global_start is None:
            continue
        if anchor is None or jp_start > _get(anchor, "jp_start_date"):
            anchor = row

    # The anchor's global date is a fact, so it already reflects every schedule
    # slip that had happened by then — and every prediction below is measured
    # from it. Recorded on each entry so apply_schedule_offsets can skip those
    # slips instead of applying them a second time.
    anchor_start = _get(anchor, "global_start_date") if anchor is not None else None

    result = {}
    for row in rows:
        row_id = _get(row, "id")
        global_start = _get(row, "global_start_date")
        jp_start = _get(row, "jp_start_date")
        # `or 0` rather than a plain read: DB-free test rows may omit the key,
        # and the column itself is non-null so this only ever normalises None.
        offset_days = _get(row, "schedule_offset_days") or 0

        if global_start is not None:
            # Confirmed: use the real global dates as-is.
            result[row_id] = {
                "start_date": global_start,
                "end_date": _get(row, "global_end_date"),
                "is_predicted": False,
            }
        elif jp_start is not None and anchor is not None:
            # Predicted from the JP schedule, anchored to the last confirmed banner.
            jp_gap = jp_start - _get(anchor, "jp_start_date")
            pred_start = _get(anchor, "global_start_date") + jp_gap * float(prediction_factor)
            jp_end = _get(row, "jp_end_date")
            pred_end = pred_start + (jp_end - jp_start) if jp_end is not None else None
            # Snap AFTER the end is derived from the raw start, so the window
            # keeps its JP run length; snapping the start first and then
            # adding the run length would leave the end at 21:59:59 only by
            # luck of the JP row's own time of day.
            result[row_id] = {
                "start_date": snap_to_reset(pred_start),
                "end_date": snap_to_reset(pred_end, end=True) if pred_end is not None else None,
                "is_predicted": True,
            }
        else:
            # No confirmed dates and no way to predict (no JP date or no anchor).
            result[row_id] = {
                "start_date": None,
                "end_date": None,
                "is_predicted": False,
            }

        # Every entry carries the same keys regardless of branch, so callers
        # never have to guard on shape.
        result[row_id]["offset_days"] = offset_days
        result[row_id]["applied_offset_days"] = 0
        result[row_id]["anchor_start"] = anchor_start

    return result


def build_effective_date_map(model=BannerTimeline, *, prediction_factor=None):
    """Fetch every row's raw dates for `model` in one query and resolve them.
    Covers all ids so any serialization path can look up any row. `model` must
    expose the jp_*/global_* date fields; each model gets its own anchor set, so
    pass one model at a time (never merge rows across content types).

    `prediction_factor` is loaded from CalculationConstants when not given.
    Callers resolving several models should read it once and pass it down rather
    than paying a query per model."""
    if prediction_factor is None:
        prediction_factor = CalculationConstants.load().prediction_factor
    rows = model.objects.values(
        "id",
        "jp_start_date",
        "jp_end_date",
        "global_start_date",
        "global_end_date",
        "schedule_offset_days",
    )
    return compute_effective_dates(rows, prediction_factor=prediction_factor)


def apply_schedule_offsets(emaps):
    """
    Apply cumulative schedule offsets to already-resolved date maps, in place.

    `emaps` is an iterable of {id: entry} maps (one per content type) that
    together form ONE shared calendar — unlike anchors, which are strictly
    per-model. A row's applied offset is the sum of `offset_days` from every
    offset-carrying row whose base start_date is at or before this row's,
    wherever that row lives. So an offset set on a banner also pushes every
    later Champions Meeting and League of Heroes event.

    Only PREDICTED rows take part, as either source or target:
    - a confirmed date is a fact from the game, so it is never shifted;
    - and because a confirmed row stops *contributing* too, an offset goes
      inert by itself once its row is confirmed. That second half is the
      important one: a newly confirmed row becomes the anchor, so its real
      date already carries the slip, and a still-live offset would count it
      twice. Nothing has to be cleaned up by hand.

    An offset is also skipped when it lands at or before the TARGET's own
    anchor (`entry["anchor_start"]`, stamped by compute_effective_dates). A
    prediction is measured forward from that anchor's confirmed global date, so
    every slip up to that date is already inside the number this pass is
    shifting; adding it again double-counts. The "goes inert once confirmed"
    rule above only covers the case where the slip and the row that absorbed it
    are the same row — it cannot see a *different* model's anchor sitting after
    the offset. That is exactly the League of Heroes shape: its anchor is the
    single row carrying a global date, and because that date was filled in from
    the already-slip-corrected calendar rather than confirmed by the game, every
    later LoH row inherited the accumulated offset and then had it applied a
    second time (~7 days late across the board, against the source spreadsheet).

    Note the per-model catch: when a banner confirms, its offset also stops
    reaching later Champions Meeting / League of Heroes rows, whose own anchors
    have not moved — so those can snap back. Set an offset on the CM/LoH row
    itself if that matters.

    Every entry gains `applied_offset_days`; it stays 0 for confirmed rows,
    unresolved (null-date) rows, and predicted rows with nothing before them.
    """
    emaps = list(emaps)

    offset_points = [
        (entry["start_date"], entry["offset_days"])
        for emap in emaps
        for entry in emap.values()
        if entry["is_predicted"]
        and entry["offset_days"]
        and entry["start_date"] is not None
    ]
    if not offset_points:
        return

    for emap in emaps:
        for entry in emap.values():
            start = entry["start_date"]
            if not entry["is_predicted"] or start is None:
                continue
            # `.get`, not `[...]`: hand-built maps (tests, and any caller not
            # going through compute_effective_dates) simply get no anchor
            # filtering, which is the behaviour they had before.
            anchor_start = entry.get("anchor_start")
            # <= so a row's own offset applies to itself, not only to the rows
            # behind it — a slip starting here delays this row too. The second
            # clause drops anything already baked into the anchor this row was
            # predicted from; see the docstring.
            total = sum(
                days
                for point_start, days in offset_points
                if point_start <= start
                and (anchor_start is None or point_start > anchor_start)
            )
            if not total:
                continue
            shift = timedelta(days=total)
            entry["applied_offset_days"] = total
            entry["start_date"] = start + shift
            if entry["end_date"] is not None:
                # Same shift on both ends, so the run length is preserved.
                entry["end_date"] = entry["end_date"] + shift


def build_effective_date_maps(models=(BannerTimeline, ChampionsMeeting, LeagueOfHeroes)):
    """Resolve every content type at once: one base map per model (each with
    its OWN anchor, exactly as before), then the shared cross-model offset pass
    over all of them together. Returns {model: emap}.

    Use this rather than calling build_effective_date_map per model — offsets
    are only correct when the maps are resolved as one calendar."""
    # Read once and pass down: otherwise each model costs its own query for a
    # value that cannot change mid-request.
    prediction_factor = CalculationConstants.load().prediction_factor
    maps = {
        model: build_effective_date_map(model, prediction_factor=prediction_factor)
        for model in models
    }
    apply_schedule_offsets(maps.values())
    return maps


def effective_sort_key(entry):
    """Sort key for an effective-date map entry (or None). Timelines with no
    resolved start date sort last (via the leading flag), then by start date
    ascending. The sentinel keeps null entries comparable to each other without
    ever being compared against a real datetime."""
    start = entry["start_date"] if entry else None
    return (start is None, start if start is not None else _NO_DATE_SENTINEL)


def planned_effective_start(planned_banner, emap):
    """Resolve the effective-date entry for a UserPlannedBanner via whichever of
    its three banner FKs is set.

    All three kinds carry the same banner_timeline FK, which is what lets one
    lookup serve them all. Miss a branch here and rows of that kind resolve to
    None and sort to the front of the user's sheet — silently, since None is a
    legal "no dates yet" answer for a genuinely undated row.
    """
    timeline_id = None
    if planned_banner.banner_uma_id is not None:
        timeline_id = planned_banner.banner_uma.banner_timeline_id
    elif planned_banner.banner_support_id is not None:
        timeline_id = planned_banner.banner_support.banner_timeline_id
    elif planned_banner.banner_step_up_id is not None:
        timeline_id = planned_banner.banner_step_up.banner_timeline_id
    return emap.get(timeline_id)


def game_event_effective_dates(game_event, banner_timeline_emap, *,
                               end_buffer=GAME_EVENT_END_DATE_BUFFER):
    """
    Resolve a GameEvent's dates by following its banner_timeline FK into an
    already-built BannerTimeline effective-date map (from
    build_effective_date_map(BannerTimeline)) — no new anchor/prediction math
    of GameEvent's own, mirroring planned_effective_start's cross-model lookup.

    end_date trails the banner's resolved end_date by GAME_EVENT_END_DATE_BUFFER;
    is_predicted and applied_offset_days propagate from the banner's own entry.
    That is also how a GameEvent picks up a schedule offset for free — the
    banner's dates are already shifted by the time this reads them. Unlinked
    events (or a linked banner with no resolved start_date) resolve to
    (None, None, False) — some events (Champions Meeting tie-ins, campaign-wide
    events spanning multiple banners) never have a banner to derive from, by design.
    """
    entry = banner_timeline_emap.get(game_event.banner_timeline_id)
    if entry is None or entry["start_date"] is None:
        return {"start_date": None, "end_date": None, "is_predicted": False,
                "applied_offset_days": 0}
    end_date = (
        entry["end_date"] + end_buffer if entry["end_date"] is not None else None
    )
    return {
        "start_date": entry["start_date"],
        "end_date": end_date,
        "is_predicted": entry["is_predicted"],
        "applied_offset_days": entry["applied_offset_days"],
    }


def build_game_event_date_map(game_events, banner_timeline_emap):
    """Wraps game_event_effective_dates over a queryset/iterable of GameEvent
    rows, resolving each via the shared BannerTimeline map. Used by
    /calculator-data, which needs prediction for unconfirmed banners."""
    end_buffer = game_event_end_buffer()
    return {
        game_event.id: game_event_effective_dates(
            game_event, banner_timeline_emap, end_buffer=end_buffer
        )
        for game_event in game_events
    }


def game_event_confirmed_dates(game_event, *, end_buffer=GAME_EVENT_END_DATE_BUFFER):
    """
    Non-predicting variant: reads the linked banner's raw (confirmed-only)
    global_start_date/global_end_date directly — never predicts, always
    is_predicted=False. Mirrors the convention that standalone reference
    routes (e.g. /leagueofheroes) serve confirmed dates only, with prediction
    reserved for /calculator-data. Caller must select_related("banner_timeline").
    """
    banner_timeline = game_event.banner_timeline
    if banner_timeline is None or banner_timeline.global_start_date is None:
        return {"start_date": None, "end_date": None, "is_predicted": False,
                "applied_offset_days": 0}
    global_end = banner_timeline.global_end_date
    end_date = global_end + end_buffer if global_end is not None else None
    return {
        "start_date": banner_timeline.global_start_date,
        "end_date": end_date,
        "is_predicted": False,
        # Confirmed dates are never offset, so this is always 0 here.
        "applied_offset_days": 0,
    }


def build_game_event_confirmed_date_map(game_events):
    """Wraps game_event_confirmed_dates over a queryset/iterable of GameEvent
    rows. Used by the standalone /events route."""
    end_buffer = game_event_end_buffer()
    return {
        game_event.id: game_event_confirmed_dates(game_event, end_buffer=end_buffer)
        for game_event in game_events
    }


def scenario_effective_dates(scenario, banner_timeline_emap):
    """
    Resolve a Scenario's START from its launch banner, by following its
    banner_timeline FK into an already-built BannerTimeline effective-date map.
    No prediction of its own, exactly like game_event_effective_dates.

    There is NO end date and there must not be one. A scenario is released and
    then stays available permanently — a newer scenario doesn't retire an older
    one — so borrowing the launch banner's end would invent an expiry the
    scenario does not have. See the Scenario model docstring.

    end_date stays in the returned dict as a permanent None so this entry has
    the same shape as every other effective-date entry: _ResolvedDateMixin._entry
    and effective_sort_key both index these blindly. It is simply never emitted
    on the wire — see StartInstantDateMixin.

    is_predicted and applied_offset_days propagate from the banner's own entry,
    which is how a scenario picks up a schedule offset for free: the banner's
    dates are already shifted by the time this reads them.

    An unlinked scenario (or one whose banner has no resolved start) resolves to
    a null start and sorts last, exactly as an unlinked GameEvent does.
    """
    entry = banner_timeline_emap.get(scenario.banner_timeline_id)
    if entry is None or entry["start_date"] is None:
        return {"start_date": None, "end_date": None, "is_predicted": False,
                "applied_offset_days": 0}
    return {
        "start_date": entry["start_date"],
        "end_date": None,
        "is_predicted": entry["is_predicted"],
        "applied_offset_days": entry["applied_offset_days"],
    }


def build_scenario_date_map(scenarios, banner_timeline_emap):
    """Wraps scenario_effective_dates over a queryset/iterable of Scenario rows,
    resolving each via the shared BannerTimeline map."""
    return {
        scenario.id: scenario_effective_dates(scenario, banner_timeline_emap)
        for scenario in scenarios
    }


def daily_legend_race_effective_dates(release, banner_timeline_emap):
    """
    Resolve a DailyLegendRaceRelease's START: its banner's resolved start plus
    the release's own signed `offset_days`.

    The same start-only shape as scenario_effective_dates, with one addition.
    A batch of daily legend races often lands a day or three after the banner
    it arrives with, and `offset_days` is the editor's record of that. It is a
    per-release nudge, NOT a schedule offset: it moves this release and nothing
    after it, so it is added here, after apply_schedule_offsets has run, and is
    not counted in `applied_offset_days`. That field keeps meaning what it means
    everywhere else (cascading schedule offset baked into the banner's date).

    No end date, ever: once a batch joins the daily races it stays. See
    scenario_effective_dates for why the key is kept as a permanent None.

    An unlinked release (or one whose banner has no resolved start) resolves to
    a null start. The client drops it, which is how an editor enters a future
    batch before the timeline has a banner to link it to.
    """
    entry = banner_timeline_emap.get(release.banner_timeline_id)
    if entry is None or entry["start_date"] is None:
        return {"start_date": None, "end_date": None, "is_predicted": False,
                "applied_offset_days": 0}
    return {
        "start_date": entry["start_date"] + timedelta(days=release.offset_days),
        "end_date": None,
        "is_predicted": entry["is_predicted"],
        "applied_offset_days": entry["applied_offset_days"],
    }


def build_daily_legend_race_date_map(releases, banner_timeline_emap):
    """Wraps daily_legend_race_effective_dates over a queryset/iterable of
    DailyLegendRaceRelease rows, resolving each via the shared BannerTimeline
    map."""
    return {
        release.id: daily_legend_race_effective_dates(release, banner_timeline_emap)
        for release in releases
    }


#: The part number carrying an anniversary's headline banner. Every anniversary
#: opens with a Part 1 lead-up campaign — "the anniversary is coming, here are
#: some rewards" — and the anniversary proper starts one part later, on JP 02-24
#: or 08-24, the game's own launch date. True of all ten anniversaries in
#: seed_anniversary_campaigns.CAMPAIGNS, and corroborated by the source sheet's
#: "Anniversary" column, which marks exactly that one banner per campaign.
MAIN_ANNIVERSARY_PART = 2


def _opening_part(parts):
    """The earliest-starting resolved part — when the campaign first opens."""
    return min(parts, key=lambda part: part[1]["start_date"])[1]


def _main_part(anniversary_event, parts):
    """The resolved part that IS the event, given [(part_number, entry)].

    `start_date` says when a campaign opens; this says when the thing it is named
    after actually happens. For an anniversary those differ by roughly ten days,
    because Part 1 is a run-up of login rewards rather than the anniversary
    itself. Calendar landmarks (the planner's section bands, the timeline's
    campaign card) and the instant a purchase's paid carats are credited all
    belong at the event, not at the run-up.

    Selected on part_number, never on date order: the 5th Anniversary's Part 4
    opens BEFORE its Part 3 — concurrent banners, which is how the sheet records
    them — so "the second part by date" would not be Part 2.

    Falling back to the opening covers two real shapes. The 0.5th Anniversary has
    no Part 2 in the timeline data at all (that banner has no BannerTimeline row,
    so only its Part 3 link resolves), and a campaign an editor has linked only a
    Part 1 to has no later part to point at. Both are better served by the
    opening date than by no date.

    Only `anniversary` campaigns have a run-up. A New Year campaign's Part 1 IS
    the New Year banner (New Years 2025 = Katsuragi Ace + Mr. C.B.), and the
    `campaign` catch-all is a single-part promotion, so for both the main part is
    the opening one and this returns exactly what it always did.
    """
    if anniversary_event.event_type == "anniversary":
        main = [part for part in parts if part[0] >= MAIN_ANNIVERSARY_PART]
        if main:
            return min(main, key=lambda part: part[0])[1]
    return _opening_part(parts)


def anniversary_event_effective_dates(anniversary_event, banner_timeline_emap):
    """
    Resolve an AnniversaryEvent's date range by spanning every BannerTimeline
    "Part" it is linked to — start = the earliest resolved part start, end = the
    latest resolved part end. Like game_event_effective_dates this does no
    prediction of its own; it reads an already-built BannerTimeline map, which is
    what keeps campaigns on the one shared calendar and gives them schedule
    offsets for free.

    `main_start_date` is a THIRD date alongside those two: the start of the part
    that is the event itself rather than its run-up — see _main_part. It always
    falls inside [start_date, end_date] and is never null when start_date isn't,
    so a consumer can prefer it unconditionally.

    is_predicted is True if ANY contributing part is predicted: the range is only
    as certain as its least certain edge, so a campaign whose Part 1 is confirmed
    but whose Part 4 is still predicted must show as predicted.

    A campaign with no links (or whose links have no resolved dates) resolves to
    all-null dates and sorts last, exactly as an unlinked GameEvent does. Callers
    must not assume a campaign is dated.
    """
    parts, ends = [], []
    for link in anniversary_event.banner_links.all():
        entry = banner_timeline_emap.get(link.banner_timeline_id)
        if entry is None or entry["start_date"] is None:
            continue
        parts.append((link.part_number, entry))
        if entry["end_date"] is not None:
            ends.append(entry["end_date"])

    if not parts:
        return {"start_date": None, "main_start_date": None, "end_date": None,
                "is_predicted": False, "applied_offset_days": 0}

    main = _main_part(anniversary_event, parts)
    return {
        "start_date": _opening_part(parts)["start_date"],
        "main_start_date": main["start_date"],
        "end_date": max(ends) if ends else None,
        "is_predicted": any(entry["is_predicted"] for _, entry in parts),
        # The offset that moved the MAIN part, which is the instant purchases are
        # credited at. It tracks main_start_date rather than start_date for that
        # reason; reporting max() across the parts would describe some other
        # part's shift, not the one the projection actually uses.
        "applied_offset_days": main["applied_offset_days"],
    }


def build_anniversary_event_date_map(anniversary_events, banner_timeline_emap):
    """Wraps anniversary_event_effective_dates over a queryset/iterable of
    AnniversaryEvent rows, resolving each via the shared BannerTimeline map."""
    return {
        event.id: anniversary_event_effective_dates(event, banner_timeline_emap)
        for event in anniversary_events
    }
