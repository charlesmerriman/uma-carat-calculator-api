"""When a Champions Meeting or League of Heroes pays, day by day.

A race event used to pay its whole placement reward at one instant (the
ledger row's `date`). In the game the reward arrives over the event: an LoH
has paid most of it by day 4 of 9, a CM about 20% before its finals
(Daptrius, 2026-10-05; his per-day tables of 2026-09-24). So each race row
also carries `payouts`, the instants its days pay, and each rank (and each
"from event #N" row) may carry `day_amounts`, how much it pays at each.

This module is the ONE place that knows a kind's payout days, so the ledger
(which dates them) and the models (which check a list's length) never keep
copies that drift apart. The client only reads the instants and the amounts.

THE DAYS (Daptrius and the owner, 2026-10-29: notes/v2-10-feedback-plan.md)
---------------------------------------------------------------------------
Every day pays at its START, the reset that opens it (start + N - 1 days):
day 1 pays the day the event opens, and the last day pays 24 hours before
the listed end. The LAST payout is always the row's own `date` (the end less
ledger.RACE_REWARD_LEAD_TIME, the start of the last day), so the total lands
with the final day.

  Champions Meeting  days 1-4 (Round 1 and Round 2), then the finals at the
                     row's date (start of finals day). Day 5 is the gap and
                     pays nothing. Five payouts.
  League of Heroes   main-stage days 1-6, second stage 1 on day 8, second
                     stage 2 at the row's date (start of day 9). Day 7 is
                     ranking only. Eight payouts.

Until 2026-10-29 the early days paid at their END (start + N days), one day
late against the game, while the CM finals already paid at the start of
their day: a CM showed its rounds a day late and its finals right.

An earlier day is clamped to the last instant, so a short or odd window can
never date a day after its own final payout.

TICKETS AND SHARDS (Daptrius, 2026-10-22 and 2026-10-29)
--------------------------------------------------------
A rank's gacha tickets and shards are paid on one day each:

  Champions Meeting  tickets and shards with the finals (the row's date)
  League of Heroes   shards on day 3, tickets on day 4

Each is dated the same way as a carat day (clamped to the last instant), so
it falls on one of `payouts`. The ledger row carries them as `tickets_at` /
`shards_at`.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models

# Day offsets from the event's start for every payout but the last, keyed by
# ledger row kind (ledger.KIND_*; spelled out here because ledger imports this).
# Offset 0 is day 1: a day pays at the reset that opens it.
EARLY_PAYOUT_DAYS = {
    "champions_meeting": (0, 1, 2, 3),
    "league_of_heroes": (0, 1, 2, 3, 4, 5, 7),
}


# The offset a rank's tickets and shards arrive at, keyed like
# EARLY_PAYOUT_DAYS. None is the last payout: a CM's come with the finals.
ITEM_PAYOUT_DAYS = {
    "champions_meeting": {"tickets": None, "shards": None},
    "league_of_heroes": {"tickets": 3, "shards": 2},
}


def payout_count(kind):
    """How many payouts an event of this kind makes: its early days plus the last."""
    return len(EARLY_PAYOUT_DAYS[kind]) + 1


def payout_instants(kind, start, last):
    """The instants an event pays, in order. `last` is the ledger row's date.
    With no start (an event dated by its end only), everything is paid at
    `last`, the behaviour from before this module."""
    if start is None:
        return [last] * payout_count(kind)
    return [min(start + timedelta(days=day), last) for day in EARLY_PAYOUT_DAYS[kind]] + [last]


def item_payout_instant(kind, item, start, last):
    """When an event of this kind pays a rank's `item` ("tickets" or
    "shards"). Dated like a carat day, so it is one of payout_instants(); with
    no start, or on the last day, at `last`."""
    day = ITEM_PAYOUT_DAYS[kind][item]
    if start is None or day is None:
        return last
    return min(start + timedelta(days=day), last)


def day_amounts_error(kind, day_amounts, total):
    """Why a `day_amounts` list is not valid for a row paying `total`, or None.
    Blank is valid: it means "follow the rank's own split" (an era row) or
    "everything on the last day" (a rank), resolved on the client."""
    if not day_amounts:
        return None
    count = payout_count(kind)
    if not isinstance(day_amounts, list) or not all(
        isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0 for amount in day_amounts
    ):
        return "Enter a list of whole numbers of carats, like [390, 300, 600]."
    if len(day_amounts) != count:
        return f"Enter {count} amounts, one per payout day, or leave it blank."
    if sum(day_amounts) != total:
        return f"The days add up to {sum(day_amounts)}, not the {total} carats this row pays."
    return None


DAY_AMOUNTS_HELP = (
    "Carats paid on each payout day, in order, adding up to the carats above. "
    "Champions Meeting: 5 (days 1 to 4, then the finals). League of Heroes: 8 "
    "(days 1 to 6, then second stage 1 and 2). Blank on a rank: everything on the "
    "last day. Blank on a later-event row: the rank's own split, scaled."
)


def day_amounts_field():
    """The `day_amounts` column, the same on a rank and on its era rows."""
    return models.JSONField(default=list, blank=True, verbose_name="carats per day", help_text=DAY_AMOUNTS_HELP)


class DayAmountsChecks:  # pylint: disable=too-few-public-methods
    """Mixed into a rank model or an era row: refuses a `day_amounts` list of
    the wrong length or sum. `RACE_KIND` names the ledger kind it pays for."""

    RACE_KIND = ""

    def clean(self):
        super().clean()  # pylint: disable=no-member
        error = day_amounts_error(self.RACE_KIND, self.day_amounts, self.income_amount)  # pylint: disable=no-member
        if error:
            raise ValidationError({"day_amounts": error})
