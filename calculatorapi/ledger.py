"""
The income ledger: one flat, date-sorted list of every reward instant on the
calendar.

WHY THIS EXISTS
---------------
The projection used to be an incremental walk — a cursor stepping banner to
banner, accruing income into half-open `(prevEnd, thisEnd]` windows. Every
income source needed its own occurrence counter, and every counter had to *tile*
perfectly or totals drifted with the number of banners planned. A long run of
bugs came out of that requirement.

The source spreadsheet does it the other way round: it builds a flat dated
timeline first, then each banner asks that timeline for an absolute cumulative
total from today to its own end date. Nothing accumulates across banners, so no
counter has to tile. This module is the backend half of that model — the
analogue of the sheet's `Timeline` tab (columns AS-BD), which it imports wholesale
from a master workbook.

WHAT THIS MODULE DOES *NOT* DO
------------------------------
It computes no income. It assembles and *dates* rows; the frontend multiplies,
decays and totals them. That keeps rank selection, user toggles and the decay
curve on the client where they already live.

It also applies no "as of today" filter. Rows are plain dated facts, past ones
included. The sheet bakes a today-gate into its `CM Check`/`LoH Check` columns
(verified: of 2,142 rows, zero flagged rows predate today), but we deliberately
do not — our "today" is resolved client-side, and a server-side gate would put a
second, slightly different anchor into the calculation. The client applies
`today < date <= end` to every source uniformly instead.

Pure and DB-free, mirroring the split in `predictions.py`: this function takes
already-resolved rows and date maps, so it unit-tests directly without fixtures.
`views/ledger.py` holds the thin serializer that puts it on the wire.
"""

from datetime import timedelta

from .predictions import GAME_EVENT_END_DATE_BUFFER

# Row kinds. `event` rows carry amounts; the two race kinds are indicator rows
# that the client scales by the user's rank (the sheet's BL/BM columns), because
# the payout depends on which rank the user picked, not on the event.
KIND_EVENT = "event"
KIND_CHAMPIONS_MEETING = "champions_meeting"
KIND_LEAGUE_OF_HEROES = "league_of_heroes"

# How far BEFORE its listed end date each race kind actually pays out.
#
# A Champions Meeting's finals resolve and distribute placement rewards a day
# before the event window closes — the last 24 hours are the results/wind-down
# tail, not another round. Dating the row at the raw end therefore credited the
# carats a day late, which is visible whenever a banner closes in that gap: the
# CM fell on the wrong side of the banner and its payout slipped to the next row.
#
# The lead is 23:59:59, not 24:00:00, so the row lands ON the award instant.
# Confirmed windows close at 21:59:59; the rewards drop at the daily reset that
# follows, 22:00:00 the day before the listed end (confirmed by Daptrius,
# 2026-10-06). A full 24 hours would date the row one second before that
# reset, which is exactly a banner's closing second: the client gate is
# `date <= banner end`, and that one second decided whether a CM ending the
# day after a banner was credited to it (it must not be — the rewards arrive a
# minute after the banner closes).
#
# League of Heroes is listed explicitly at zero rather than omitted. CM and LoH
# are field-identical and handled identically everywhere else (they even share
# one timeline card), so a divergence between them has to read as deliberate at
# the point it happens, not as a kind someone forgot to add. Giving LoH the same
# lead time later is a one-line change here and nowhere else.
RACE_REWARD_LEAD_TIME = {
    KIND_CHAMPIONS_MEETING: timedelta(hours=23, minutes=59, seconds=59),
    KIND_LEAGUE_OF_HEROES: timedelta(0),
}

# The model field holding each race kind's own number (CM #12, LoH #3), so a
# row can say WHICH meeting it is. The client needs that to cap the rank a few
# specific events pay at: League of Heroes #1 only ran to Platinum 1. Keyed by
# kind for the same reason as the lead time above, since the two kinds differ
# only in what the column is called.
RACE_NUMBER_FIELD = {
    KIND_CHAMPIONS_MEETING: "cm_number",
    KIND_LEAGUE_OF_HEROES: "loh_number",
}

# Every amount field a ledger row can carry, so callers (and the serializer) have
# one list to iterate rather than a hand-maintained copy each.
AMOUNT_FIELDS = (
    "carats",
    "carats_throughout",
    "uma_tickets",
    "support_tickets",
    "ssr_shards",
    "ssr_crystals",
    "sr_shards",
    "sr_crystals",
)


def _row(*, date, kind, source_id, name, is_predicted, **amounts):
    """Build one ledger row with every amount field present.

    Filling the zeros here rather than at each call site means a consumer can
    read `row["sr_crystals"]` unconditionally — no `.get(..., 0)` scattered
    through the client, and no row shape that varies by kind.
    """
    row = {
        "date": date,
        "kind": kind,
        "source_id": source_id,
        "name": name,
        "is_predicted": is_predicted,
        "throughout_end": None,
        "event_number": None,
    }
    for field in AMOUNT_FIELDS:
        row[field] = 0
    row.update(amounts)
    return row


def _game_event_rows(game_events, game_event_emap):
    """One row per GameEvent, dated at its resolved start.

    `carats` (and the ticket/shard/crystal amounts) land as a lump on that date.
    `carats_throughout` is a *pool* spread across the event's life by a decay
    curve the client owns — it is carried here undecayed, with the end the curve
    runs to in `throughout_end`.

    That end is the linked BANNER's end, not the event's: the event's resolved
    end trails its banner by GAME_EVENT_END_DATE_BUFFER, and the curve runs over
    the banner. Removing the buffer here means the client uses the value
    directly. Previously it re-derived this by subtracting its own copy of the
    buffer constant, which had to be kept in sync across two repos by hand.
    """
    rows = []
    for event in game_events:
        entry = game_event_emap.get(event.id)
        if entry is None or entry["start_date"] is None:
            # No linked banner, or a banner whose own dates are unresolved.
            # There is no honest instant to place this on.
            continue

        # An all-zero event can never change a total; keeping it would only pad
        # the payload (~200 events ship on every page load).
        amounts = {
            "carats": event.carat_amount,
            "carats_throughout": event.carats_throughout,
            "uma_tickets": event.uma_ticket_amount,
            "support_tickets": event.support_ticket_amount,
            "ssr_shards": event.ssr_shard_amount,
            "ssr_crystals": event.ssr_crystal_amount,
            "sr_shards": event.sr_shard_amount,
            "sr_crystals": event.sr_crystal_amount,
        }
        if not any(amounts.values()):
            continue

        end_date = entry["end_date"]
        throughout_end = (
            end_date - GAME_EVENT_END_DATE_BUFFER if end_date is not None else None
        )

        row = _row(
            date=entry["start_date"],
            kind=KIND_EVENT,
            source_id=event.id,
            name=event.name,
            is_predicted=entry["is_predicted"],
            **amounts,
        )
        row["throughout_end"] = throughout_end
        rows.append(row)
    return rows


def _race_rows(events, emap, kind):
    """One indicator row per Champions Meeting / League of Heroes event.

    Dated at the event's resolved END date, less that kind's
    RACE_REWARD_LEAD_TIME — the payout lands when the event's placements are
    settled, which for a Champions Meeting is the daily reset a day before the
    window closes.
    Amounts stay zero: what a placement is worth depends on the user's rank row,
    which only the client knows.

    The lead time is applied HERE rather than client-side on purpose. Dating a
    row is this module's whole job, and every consumer — the per-banner rows, the
    income tiles, the uncap panel — then reads one instant instead of each
    keeping its own copy of the offset to subtract.
    """
    lead_time = RACE_REWARD_LEAD_TIME.get(kind, timedelta(0))
    number_field = RACE_NUMBER_FIELD.get(kind)
    rows = []
    for event in events:
        entry = emap.get(event.id)
        if entry is None or entry["end_date"] is None:
            continue
        row = _row(
            date=entry["end_date"] - lead_time,
            kind=kind,
            source_id=event.id,
            name=event.name,
            is_predicted=entry["is_predicted"],
        )
        # The number, not the pk: it is the identity the game and the sheet use
        # ("League of Heroes 1"), and it is the same in every database.
        row["event_number"] = getattr(event, number_field) if number_field else None
        rows.append(row)
    return rows


def build_income_ledger(*, game_events, game_event_emap, race_sources):
    """
    Assemble the full ledger, sorted by date.

    Every argument is an already-resolved input — the querysets the view has in
    hand plus the effective-date maps from `build_effective_date_maps()`. Nothing
    here queries, predicts, or re-anchors; the dates arrive resolved (and
    schedule-offset-shifted) and are used as given.

    `race_sources` is an iterable of `(kind, rows, emap)`. Champions Meetings and
    League of Heroes events are one argument rather than two pairs because they
    are field-identical and handled almost identically — the same reason they
    share one timeline card on the frontend. Their only divergence is
    RACE_REWARD_LEAD_TIME, which is keyed by kind precisely so it stays the one
    visible exception. A third race type would be one more tuple at the call site
    and one more entry there.

    Rows with no resolvable date are dropped rather than emitted with a null: a
    ledger row's whole purpose is its position on the calendar, and a client
    filtering `today < date <= end` would have to special-case the nulls at every
    call site.

    The sort key breaks ties on (kind, source_id) so the order is total and
    stable — two events sharing an instant must not reorder between requests, or
    a cached response and a fresh one disagree for no reason.
    """
    rows = _game_event_rows(game_events, game_event_emap)
    for kind, events, emap in race_sources:
        rows += _race_rows(events, emap, kind)
    rows.sort(key=lambda row: (row["date"], row["kind"], row["source_id"]))
    return rows
