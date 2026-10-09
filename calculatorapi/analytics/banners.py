"""What people plan to pull on: banner popularity, step-ups, when the
demand falls due, and the umas they pick as favourites."""

import datetime
from collections import defaultdict

from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from ..models import BannerStepUp, UserOshi, UserPlannedBanner, UserStepUpSelection
from .common import SANE_MAX_PULLS, SANE_MAX_STEPS

# How many calendar months the demand calendar lists one by one, this one
# included, before it folds the rest into "Later".
DEMAND_MONTHS = 6


def active_rows():
    """Every planned banner row the analytics count: non-staff, active plan.

    ACTIVE PLANS ONLY. An account can hold several plans, and the spare ones
    are what-ifs: someone comparing "200 pulls" against "skip it" on the same
    banner intends ONE of those. Summing every plan would report 200 pulls of
    demand from a person who may intend none, and would let one user with five
    copies of a plan move an average five times. The active plan is the one
    they have open, so it is the best single answer to "what does this person
    plan to do", and it keeps every figure here meaning what it meant when an
    account had exactly one plan.

    Every row-based figure in this package starts here, so the filter lives in
    one place.
    """
    return (
        UserPlannedBanner.objects
        # Never count staff/admin test accounts.
        .filter(user__is_staff=False)
        # TRANSITIONAL: the isnull half goes with release 2 of multi-plan. Until
        # `plan` is NOT NULL, a row the old code wrote during the deploy window
        # has no plan yet (plans._adopt_planless_rows) and is still a real row.
        .filter(Q(plan__is_active=True) | Q(plan__isnull=True))
    )


def banner_status(entry, now):
    """"upcoming", "running" or "ended" for one effective-date entry.

    A timeline with no resolvable start yet counts as upcoming: undated means
    nobody knows when, not that it is over. One with a start but no end is
    running once the start has passed.
    """
    start = entry["start_date"] if entry else None
    end = entry["end_date"] if entry else None
    if start is None or start > now:
        return "upcoming"
    if end is not None and end <= now:
        return "ended"
    return "running"


def _dated(timeline_id, timeline_dates, now):
    """The date columns every banner row carries, from the effective-date map.

    The map resolves PREDICTED dates too, so a banner that is not yet
    announced still shows when it is expected (`predicted` says which). These
    rows used to carry only the confirmed global dates, which left every
    predicted banner blank.
    """
    entry = timeline_dates.get(timeline_id)
    return {
        "start_date": entry["start_date"] if entry else None,
        "end_date": entry["end_date"] if entry else None,
        "predicted": bool(entry and entry["is_predicted"]),
        "status": banner_status(entry, now),
    }


def banner_popularity(fk_name, timeline_dates):
    """
    Popularity rows for one banner type. fk_name is the FK field on
    UserPlannedBanner: "banner_uma" or "banner_support". `timeline_dates` is
    the BannerTimeline effective-date map (predictions.build_effective_date_maps).

    Grouped by banner id (name/timeline included for display), ranked by
    distinct planners, then total pulls. Every row is returned, ended banners
    included; `status` lets a reader set the ended ones aside.

    WHY `planners` IS COUNTED UNFILTERED WHILE THE PULL FIGURES ARE NOT.
    They answer different questions. Someone who typed 999,999,999 into a
    banner's pull field really does have that banner in their plan, and
    popularity — the primary signal this table exists for — should say so.
    What they do NOT have is a budget of a billion pulls, so their row is
    excluded from the two figures that treat the number as a quantity. Filtering
    both would quietly understate demand; filtering neither is what produced a
    447,572 average.

    `excluded` reports how many rows were dropped from those two figures, so a
    surprising average can be checked against the count that caused it instead
    of being reverse-engineered from the arithmetic.

    Active plans only: see active_rows().
    """
    # Non-null ints, so ~sane is a clean complement with no third case.
    sane = Q(number_of_pulls__lte=SANE_MAX_PULLS)
    rows = (
        active_rows()
        # Only this banner type.
        .filter(**{f"{fk_name}__isnull": False})
        # values() before annotate() = GROUP BY these fields. Including the
        # FK id guarantees two banners that share a name never merge.
        .values(
            fk_name,
            f"{fk_name}__name",
            f"{fk_name}__banner_timeline_id",
            f"{fk_name}__banner_timeline__name",
        )
        .annotate(
            planners=Count("user", distinct=True),
            total_pulls=Sum("number_of_pulls", filter=sane),
            avg_pulls=Avg("number_of_pulls", filter=sane),
            excluded=Count("id", filter=~sane),
        )
        # nulls_last matters now that total_pulls is filtered: a banner whose
        # every row was excluded aggregates to NULL, and Postgres sorts NULLs
        # FIRST in a plain DESC — which would put the one banner we have no
        # figures for at the top of the table.
        .order_by("-planners", F("total_pulls").desc(nulls_last=True))
    )
    now = timezone.now()
    return [
        {
            "name": row[f"{fk_name}__name"],
            "timeline": row[f"{fk_name}__banner_timeline__name"],
            **_dated(row[f"{fk_name}__banner_timeline_id"], timeline_dates, now),
            "planners": row["planners"],
            # Both aggregates come back NULL when the filter matched nothing,
            # which is a real state (a banner planned only by the outlier), not
            # an error — report it as zero rather than crashing the page.
            "total_pulls": row["total_pulls"] or 0,
            "avg_pulls": round(row["avg_pulls"] or 0, 1),
            "excluded": row["excluded"],
        }
        for row in rows
    ]


def step_up_popularity(timeline_dates):
    """One row per step-up banner anyone plans or has picked cards for.

    STEPS, NEVER PULLS. On a step-up row `number_of_pulls` holds the number of
    ladder STEPS (.claude/rules/step-up.md), so these figures are steps, are
    bounded by their own SANE_MAX_STEPS, and are never added to a pull total.

    `picked` counts people who chose their own ten cards for the step-up (any
    stored UserStepUpSelection). An untouched step-up shows a virtual default
    and stores nothing, so this is "customised", not "has a selection". It is
    counted over everyone, not only planners: someone can pick cards before
    deciding to climb.

    Two queries merged here, not one: annotating both reverse relations on
    BannerStepUp would join plan rows against selection rows, and the step
    SUM would count each plan row once per selection.
    """
    sane = Q(number_of_pulls__lte=SANE_MAX_STEPS)
    planned = {
        row["banner_step_up"]: row
        for row in (
            active_rows()
            .filter(banner_step_up__isnull=False)
            .values("banner_step_up")
            .annotate(
                planners=Count("user", distinct=True),
                total_steps=Sum("number_of_pulls", filter=sane),
                avg_steps=Avg("number_of_pulls", filter=sane),
                excluded=Count("id", filter=~sane),
            )
        )
    }
    picked = dict(
        UserStepUpSelection.objects
        .filter(user__is_staff=False)
        .values("banner_step_up")
        .annotate(people=Count("user", distinct=True))
        .values_list("banner_step_up", "people")
    )
    step_ups = (BannerStepUp.objects
                .filter(id__in=planned.keys() | picked.keys())
                .select_related("anniversary_event"))
    now = timezone.now()
    rows = []
    for step_up in step_ups:
        plan = planned.get(step_up.id, {})
        rows.append({
            "name": step_up.name,
            "campaign": step_up.anniversary_event.name,
            "card_type": step_up.get_card_type_display(),
            **_dated(step_up.banner_timeline_id, timeline_dates, now),
            "planners": plan.get("planners", 0),
            "picked": picked.get(step_up.id, 0),
            "total_steps": plan.get("total_steps") or 0,
            "avg_steps": round(plan.get("avg_steps") or 0, 1),
            "excluded": plan.get("excluded", 0),
        })
    rows.sort(key=lambda row: (-row["planners"], -row["total_steps"], row["name"]))
    return rows


def _month_start(moment):
    return moment.date().replace(day=1)


def _next_month(month_start):
    return (month_start + datetime.timedelta(days=32)).replace(day=1)


def demand_calendar(timeline_dates):
    """Planned demand grouped by the month each banner ENDS, soonest first.

    The end date because that is when the carats leave: income in the
    projection is a pure function of a banner's end date, so this is the order
    a saving plan meets its banners in. The next DEMAND_MONTHS months are
    listed one by one and everything after (and anything still undated) folds
    into "Later". Ended banners are left out; this is about what is coming.

    Per month: how many banners have planners, how many distinct people plan
    an Uma or Support banner there (someone with two counts once), the pulls
    they budget (sane rows only, Ignored says how many were not), and how many
    people plan a step-up there. Step-up rows hold STEPS, so they never reach
    the pull total.
    """
    now = timezone.now()
    this_month = _month_start(now)
    months = [this_month]
    while len(months) < DEMAND_MONTHS:
        months.append(_next_month(months[-1]))
    later = "Later"

    buckets = defaultdict(lambda: {"banners": set(), "planners": set(),
                                   "total_pulls": 0, "excluded": 0,
                                   "step_up_planners": set()})
    rows = active_rows().values(
        "user_id", "number_of_pulls",
        "banner_uma_id", "banner_uma__banner_timeline_id",
        "banner_support_id", "banner_support__banner_timeline_id",
        "banner_step_up_id", "banner_step_up__banner_timeline_id",
    )
    for row in rows:
        timeline_id = (row["banner_uma__banner_timeline_id"]
                       or row["banner_support__banner_timeline_id"]
                       or row["banner_step_up__banner_timeline_id"])
        entry = timeline_dates.get(timeline_id)
        if banner_status(entry, now) == "ended":
            continue
        end = entry["end_date"] if entry else None
        month = _month_start(end) if end is not None else later
        bucket = buckets[month if month in months else later]
        if row["banner_step_up_id"] is not None:
            bucket["step_up_planners"].add(row["user_id"])
            continue
        bucket["banners"].add(("uma", row["banner_uma_id"])
                              if row["banner_uma_id"] is not None
                              else ("support", row["banner_support_id"]))
        bucket["planners"].add(row["user_id"])
        if row["number_of_pulls"] <= SANE_MAX_PULLS:
            bucket["total_pulls"] += row["number_of_pulls"]
        else:
            bucket["excluded"] += 1

    calendar = []
    for month in [*months, later]:
        if month not in buckets:
            continue
        bucket = buckets[month]
        calendar.append({
            "month": month if month == later else f"{month:%Y-%m}",
            "banners": len(bucket["banners"]),
            "planners": len(bucket["planners"]),
            "total_pulls": bucket["total_pulls"],
            "excluded": bucket["excluded"],
            "step_up_planners": len(bucket["step_up_planners"]),
        })
    return calendar


# How many umas the favourites leaderboard lists.
FAVOURITES_LIMIT = 20


def favourite_umas():
    """The most-picked favourite umas: distinct non-staff people per uma.

    "As picture" counts the people for whom it is the FIRST favourite, which is
    the account picture. A costume variant is its own Uma row, so it appears
    under its own name. An aggregate preference, like the banner tables: no
    person is listed, only how many chose each uma.
    """
    rows = (
        UserOshi.objects.filter(user__is_staff=False)
        .values("uma_id", "uma__name")
        .annotate(people=Count("user", distinct=True),
                  as_picture=Count("user", distinct=True, filter=Q(position=0)))
        .order_by("-people", "-as_picture", "uma__name")[:FAVOURITES_LIMIT]
    )
    return [{"uma": row["uma__name"], "people": row["people"],
             "as_picture": row["as_picture"]} for row in rows]
