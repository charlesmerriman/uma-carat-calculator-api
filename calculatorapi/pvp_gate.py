"""
The PvP page's early-access gate, as the API needs it: only to decide what a
guide's link preview may show (views/guide_share.py; owner, 2026-10-24,
guide-visuals-plan-2.md release D). A locked guide's preview names the event
and nothing from the guide, so a pasted link never gives away what the gate
holds back.

A COPY OF THE FRONTEND'S RULE
-----------------------------
The page decides what it draws in frontend/src/hooks/usePvpAccess.ts
(PVP_EARLY_ACCESS, PVP_OPEN_CM_NUMBER, lockedPvpEvents) and
frontend/src/utils/pvpGuide.ts (defaultPvpEvent: the free guide is the event
running now, else the next). The two repos cannot share code, so this mirrors
it and each side names the other. FLIP BOTH TOGETHER: opening PvP to everyone
is PVP_EARLY_ACCESS = False here and there. If they drift, a preview shows or
hides too much; who may open the page is unaffected, and the PvP API itself
stays open either way.
"""

from django.utils import timezone

from calculatorapi.models import ChampionsMeeting, LeagueOfHeroes
from calculatorapi.predictions import build_effective_date_maps

# False opens every guide's preview, as the frontend's flag opens the page.
PVP_EARLY_ACCESS = True

# The one Champions Meeting open in full while the page is early (by its
# number, never its id), or None.
PVP_OPEN_CM_NUMBER = 21

# The URL's kind -> the event model, as GET /pvp-guides/<kind>/<id> reads it.
EVENT_MODELS = {"cm": ChampionsMeeting, "loh": LeagueOfHeroes}


def free_event(now=None, maps=None):
    """The free guide's event as ("cm" | "loh", id): the event running now,
    else the next to start, else the last; CMs and LoHs together, in start
    order (a CM first on a tie), as defaultPvpEvent orders them. None with no
    dated event."""
    now = now or timezone.now()
    maps = maps or build_effective_date_maps()
    events = []
    for order, (kind, model) in enumerate(EVENT_MODELS.items()):
        for event_id, dates in maps[model].items():
            if dates.get("start_date") is not None:
                events.append((dates["start_date"], order, kind, event_id, dates.get("end_date")))
    events.sort(key=lambda event: (event[0], event[1]))
    for start, _, kind, event_id, end in events:
        if end is not None and start <= now <= end:
            return (kind, event_id)
    for start, _, kind, event_id, _ in events:
        if start > now:
            return (kind, event_id)
    return (events[-1][2], events[-1][3]) if events else None


def guide_is_open(kind, event, now=None, maps=None):
    """Whether everyone may read this event's guide on the page today, so its
    preview may show the guide's facts."""
    if not PVP_EARLY_ACCESS:
        return True
    if kind == "cm" and PVP_OPEN_CM_NUMBER is not None and event.cm_number == PVP_OPEN_CM_NUMBER:
        return True
    return free_event(now, maps) == (kind, event.id)
