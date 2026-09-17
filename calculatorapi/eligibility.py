"""Selector eligibility: which cards a given selector ticket is allowed to pick.

A selector can only take cards that were released on JP on or before its cutoff
date. There is no stored "JP release date" column — it is derived as the earliest
JP start date among the banners a card has appeared on, which was validated
against the source sheet's own cutoffs:

    30184 Sakura Bakushin O -> 2024-01-31  (the 3rd Anniversary cutoff exactly)
    30210 Smart Falcon      -> 2024-07-29  (3.5th, cutoff 2024-07-31)
    30262 Duramente         -> 2025-07-31  (4.5th cutoff exactly)
    30287 Neo Universe      -> 2026-01-30  (5th cutoff exactly)

Three of those sit exactly on their cutoff and are listed as selectable, which is
why the comparison is inclusive. Deriving rather than storing keeps this correct
for free as banner data is added; a stored column would drift.

Note this makes eligibility USUALLY BINDING, and that is correct: a campaign's
cutoff falls before its own banners, so a selector can essentially never take the
featured unit of the anniversary that granted it. Selectors are for older and
rerun banners; SSR crystals cover the rest.

TWO GATES, NOT ONE
------------------
The date above is the TEMPORAL gate. There is a second, INTRINSIC one: some umas
can never be taken by a selector at any cutoff -- time-limited units, and units
that are not ★3. That is not derivable from banner data (they sit on ordinary
banners alongside selectable units), so it is stored on the row as
`Uma.is_time_limited` and `Uma.rarity`, the latter read through the
`Uma.is_three_star` property (an unknown rarity counts as ★3). This is the one
part of selector eligibility that IS stored; the date half stays derived.

The gates are independent, and the intrinsic one bites even under an
unrestricted (null) cutoff, which the temporal one waves through. Go through
`selection_refusal_reason` rather than calling `is_eligible` alone -- checking
only the date silently offers cards the game will not grant.
"""

from django.db.models import Min

from .models import SupportCard, Uma


def build_first_jp_date_maps():
    """Return ({uma_id: date}, {support_card_id: date}) in two aggregate queries.

    Cards that have never appeared on a banner are absent from the maps rather
    than present with None, so callers can distinguish "released before X" from
    "we have no idea when this was released".
    """
    uma_rows = (
        Uma.objects.annotate(
            first_jp=Min("umasonumabanner__banner_uma__banner_timeline__jp_start_date")
        )
        .filter(first_jp__isnull=False)
        .values_list("id", "first_jp")
    )
    support_rows = (
        SupportCard.objects.annotate(
            first_jp=Min(
                "supportsonsupportbanner__banner_support__banner_timeline__jp_start_date"
            )
        )
        .filter(first_jp__isnull=False)
        .values_list("id", "first_jp")
    )
    return dict(uma_rows), dict(support_rows)


def is_eligible(first_jp_date, cutoff_date):
    """Can a selector with `cutoff_date` pick a card first seen on `first_jp_date`?

    A null cutoff means the selector is unrestricted, so everything qualifies. A
    card with no known JP date is treated as ineligible under a real cutoff --
    the conservative reading, since claiming a selector covers a card it cannot
    is a worse failure than hiding one it could.
    """
    if cutoff_date is None:
        return True
    if first_jp_date is None:
        return False
    # jp_start_date is a DateTimeField so the aggregate yields a datetime, while
    # the cutoff is a plain date. Normalise before comparing -- Python refuses to
    # order a datetime against a date.
    released = first_jp_date.date() if hasattr(first_jp_date, "date") else first_jp_date
    # Inclusive: cards released ON the cutoff date are selectable (see above).
    return released <= cutoff_date


def is_intrinsically_selectable(card):
    """Can a selector EVER take this card, at any cutoff?

    Support cards carry no such restriction, so anything without the flags is
    selectable -- absent means "no restriction exists for this kind of card",
    which is also what an uma with nothing set reads as (not time-limited, and
    an unknown rarity counts as ★3).
    """
    if getattr(card, "is_time_limited", False):
        return False
    return getattr(card, "is_three_star", True)


def selection_refusal_reason(card, first_jp_date, cutoff_date, subject):
    """Why a selector may not take `card`, as a user-facing sentence, or None.

    The single entry point for "is this pick legal", because the two gates fail
    for unrelated reasons and a caller that checks one will not think to check
    the other. `subject` names what is doing the refusing ("this selector",
    "this step-up") so the message reads correctly at each call site.

    Callers are expected to grandfather picks the user already had stored BEFORE
    reaching here -- both gates move as editors correct reference data, and
    rejecting an untouched pick 400s the whole atomic PATCH.
    """
    if not is_intrinsically_selectable(card):
        return (
            f"{card.name} is not available to selectors -- it is either "
            f"time-limited or not a ★3."
        )
    if not is_eligible(first_jp_date, cutoff_date):
        return (
            f"{card.name} was released on JP after {subject}'s cutoff "
            f"({cutoff_date.isoformat()}) and cannot be selected."
        )
    return None
