"""
What the Discord bot may announce, and when (discord-bot-plan.md, Step 8).
Served by GET /bot/upcoming; the worker words each moment and posts it once.

ONE CALENDAR
------------
Every date here comes from the same maps the Timeline and the planner use:
`build_effective_date_maps` for banners, Champions Meetings and League of
Heroes (with schedule offsets applied across all three), and the banner map
for campaigns and daily legend race batches. So the bot can never name a day
the site doesn't show, and a row with no resolved date is never announced.

WHAT IS A MOMENT
----------------
One thing worth a post at one instant, with a key that never changes:
`<kind>-<id>-<milestone>`, which the worker claims as `announce:<key>` before
posting (the never-twice rule). The milestones:

    banner, campaign, legend_races   tomorrow (24 h before the start), live
    cm, loh                          tomorrow (24 h before registration opens),
                                     registration_opens, round_one,
                                     finals_registration_closing (12 h before
                                     it closes), finals

PREDICTED DATES ONLY EVER GET "TOMORROW"
----------------------------------------
A predicted date is the site's guess, labelled as one on the Timeline. The bot
may remind people about it (and says "predicted"), but every other milestone
("live now", the CM rounds) is only announced for a CONFIRMED date: the bot
must never sound surer than the site is.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from calculatorapi.models import (
    AnniversaryEvent,
    BannerTimeline,
    CalculationConstants,
    ChampionsMeeting,
    DailyLegendRaceRelease,
    LeagueOfHeroes,
)
from calculatorapi.predictions import (
    anniversary_event_effective_dates,
    build_effective_date_maps,
    daily_legend_race_effective_dates,
)

REMINDER_BEFORE = timedelta(hours=24)
FINALS_CLOSING_NOTICE = timedelta(hours=12)
# How far back a moment is still returned: a worker that was down at the
# instant can still post it a little late, never hours late.
LATE_WINDOW = timedelta(hours=6)
MAX_DAYS = 60
RACE_MILESTONES = (
    ("registration_opens", "registration_opens_hours"),
    ("round_one", None),
    ("finals_registration_closing", "finals_registration_closes_hours"),
    ("finals", "finals_hours"),
)


def _image(request, field):
    if not field:
        return None
    try:
        return request.build_absolute_uri(field.url)
    except ValueError:
        return None


def _timeline_url():
    return f"{settings.FRONTEND_URL}/app/timeline"


def _moment(kind, row_id, milestone, at, entry, **details):
    return {
        "key": f"{kind}-{row_id}-{milestone}",
        "kind": kind,
        "milestone": milestone,
        "at": at,
        "predicted": entry["is_predicted"],
        **details,
    }


def _start_moments(kind, row_id, start, entry, **details):
    """tomorrow (any date) and live (confirmed only) for a start date."""
    moments = [_moment(kind, row_id, "tomorrow", start - REMINDER_BEFORE, entry, starts_at=start, **details)]
    if not entry["is_predicted"]:
        moments.append(_moment(kind, row_id, "live", start, entry, starts_at=start, **details))
    return moments


def _banner_moments(request, emap):
    moments = []
    rows = BannerTimeline.objects.prefetch_related("uma_banners__umas", "support_banners__support_cards")
    for timeline in rows:
        entry = emap.get(timeline.id)
        if not entry or entry["start_date"] is None:
            continue
        moments += _start_moments(
            "banner", timeline.id, entry["start_date"], entry,
            name=timeline.name,
            ends_at=entry["end_date"],
            umas=sorted({uma.name for banner in timeline.uma_banners.all() for uma in banner.umas.all()}),
            cards=sorted({
                card.name for banner in timeline.support_banners.all() for card in banner.support_cards.all()
            }),
            image=_image(request, timeline.image),
            url=_timeline_url(),
        )
    return moments


def _race_schedule(prefix, start, constants):
    """{milestone: instant} for every milestone the constants define."""
    times = {}
    for milestone, field in RACE_MILESTONES:
        hours = 0 if field is None else getattr(constants, f"{prefix}_{field}")
        if hours is not None:
            times[milestone] = start + timedelta(hours=hours)
    return times


def _race_moments(request, kind, model, emap, constants):
    """A CM or LoH: a reminder before registration opens, then each schedule
    milestone the constants define (the LoH ones stay blank until known)."""
    prefix = "cm" if kind == "cm" else "loh"
    moments = []
    for event in model.objects.all():
        entry = emap.get(event.id)
        if not entry or entry["start_date"] is None:
            continue
        start = entry["start_date"]
        times = _race_schedule(prefix, start, constants)
        details = {
            "name": event.name,
            "starts_at": start,
            "ends_at": entry["end_date"],
            "image": _image(request, event.image),
            "url": _timeline_url(),
            "schedule": dict(times),
        }
        opens = times.get("registration_opens", start)
        moments.append(_moment(kind, event.id, "tomorrow", opens - REMINDER_BEFORE, entry, **details))
        if entry["is_predicted"]:
            continue
        for milestone, at in times.items():
            if milestone == "finals_registration_closing":
                at -= FINALS_CLOSING_NOTICE
            moments.append(_moment(kind, event.id, milestone, at, entry, **details))
    return moments


def _campaign_moments(request, banner_map):
    moments = []
    for event in AnniversaryEvent.objects.prefetch_related("banner_links"):
        dates = anniversary_event_effective_dates(event, banner_map)
        # The anniversary ITSELF is its main part, not the run-up (Part 1).
        start = dates.get("main_start_date") or dates.get("start_date")
        if start is None:
            continue
        moments += _start_moments(
            "campaign", event.id, start, dates,
            name=event.name, event_type=event.event_type, ends_at=dates.get("end_date"),
            image=_image(request, event.image), url=_timeline_url(),
        )
    return moments


def _legend_race_moments(request, banner_map):
    moments = []
    for release in DailyLegendRaceRelease.objects.prefetch_related("umas"):
        dates = daily_legend_race_effective_dates(release, banner_map)
        if dates["start_date"] is None:
            # Unlinked: a tentative batch the Timeline doesn't date. Never announced.
            continue
        moments += _start_moments(
            "legend_races", release.id, dates["start_date"], dates,
            name=release.name, umas=sorted(uma.name for uma in release.umas.all()),
            image=_image(request, release.image), url=f"{settings.FRONTEND_URL}/app/legend-races",
        )
    return moments


def upcoming(request, days):
    """Every moment due from LATE_WINDOW ago to `days` ahead, soonest first."""
    days = max(1, min(days, MAX_DAYS))
    now = timezone.now()
    maps = build_effective_date_maps()
    banner_map = maps[BannerTimeline]
    constants = CalculationConstants.load()
    moments = [
        *_banner_moments(request, banner_map),
        *_race_moments(request, "cm", ChampionsMeeting, maps[ChampionsMeeting], constants),
        *_race_moments(request, "loh", LeagueOfHeroes, maps[LeagueOfHeroes], constants),
        *_campaign_moments(request, banner_map),
        *_legend_race_moments(request, banner_map),
    ]
    window_start, window_end = now - LATE_WINDOW, now + timedelta(days=days)
    due = [moment for moment in moments if window_start <= moment["at"] <= window_end]
    return sorted(due, key=lambda moment: (moment["at"], moment["key"]))
