"""What people plan to pull on: banner popularity, step-ups, when the
demand falls due, and the umas they pick as favourites.

ONE PASS OVER THE ROWS (notes/rankings-speed-plan.md). Prod holds ~320,000
active planned-banner rows (2026-10-28). The popularity tables, the demand
calendar and the Rankings page's pull figures used to read them once each:
seven passes, two holding every row in Python at once (~130 MB, a 31 s
Rankings build on the one shared CPU). Now active_people() streams the rows
ONCE, a person at a time, and each figure is a small tally that fold() feeds:

    popularity, demand = BannerTally(), DemandTally(timeline_dates)
    planners = fold(popularity, demand)
    banner_popularity("banner_uma", timeline_dates, popularity)

Called without a tally, each function folds its own, so a caller that wants
one figure still gets it in one call."""

import datetime
import statistics
from collections import Counter, defaultdict
from itertools import groupby
from operator import itemgetter

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from ..models import BannerStepUp, BannerSupport, BannerUma, UserOshi, UserPlannedBanner, UserStepUpSelection
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


# Rows fetched per round trip while streaming: big enough that the round trips
# don't add up, small enough that one chunk is a few hundred KB.
STREAM_CHUNK = 10_000


def active_people():
    """Every person with a row in their active plan, one at a time, as
    (user_id, [(key, number_of_pulls), ...]). A key is ("uma" | "support" |
    "step_up", banner id); on a step-up the number is STEPS (rules file
    step-up.md).

    Ordered by person and streamed (`iterator()`), so only one person's rows
    are in Python at a time, however many rows the site holds. One query.
    """
    rows = (
        active_rows()
        .order_by("user_id")
        .values_list("user_id", "banner_uma_id", "banner_support_id", "banner_step_up_id", "number_of_pulls")
        .iterator(chunk_size=STREAM_CHUNK)
    )
    for user_id, group in groupby(rows, key=itemgetter(0)):
        yield user_id, [(_row_key(uma, support, step_up), pulls) for _, uma, support, step_up, pulls in group]


def _row_key(uma, support, step_up):
    # The exactly_one_banner_target constraint guarantees exactly one is set.
    if uma is not None:
        return ("uma", uma)
    if support is not None:
        return ("support", support)
    return ("step_up", step_up)


def fold(*tallies):
    """Feed every active person to each tally (anything with `add(user_id,
    rows)`), in ONE pass. Returns how many people have an active row: the
    distinct planners."""
    people = 0
    for user_id, rows in active_people():
        people += 1
        for tally in tallies:
            tally.add(user_id, rows)
    return people


def banner_timelines():
    """{key: banner_timeline_id} for every banner of the three kinds. Three
    small queries (a few hundred banners), so a pass over the rows never joins
    the banner tables."""
    timelines = {}
    for kind, model in (("uma", BannerUma), ("support", BannerSupport), ("step_up", BannerStepUp)):
        for banner_id, timeline_id in model.objects.values_list("id", "banner_timeline_id"):
            timelines[(kind, banner_id)] = timeline_id
    return timelines


def live_banner_keys(timeline_dates, timelines=None):
    """The keys of every banner still to come or running (not "ended")."""
    now = timezone.now()
    timelines = banner_timelines() if timelines is None else timelines
    return {
        key for key, timeline_id in timelines.items()
        if banner_status(timeline_dates.get(timeline_id), now) != "ended"
    }


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


def _medians(fk_name, sane):
    """{banner id: the median of its sane rows' number_of_pulls}, one query.

    The MEDIAN ("typical") beside the average, for the Rankings page's Pull
    plans tab (notes/rankings-page-plan.md): a handful of 1,000-pull whales
    drags an average far past what most planners budget, and the median is
    what one ordinary planner does. SQL has no portable median, so the rows
    come back sorted and Python takes the middle; at prod's ~100k active rows
    that is one pass, and the Rankings tab builds it at most once an hour.
    """
    by_banner = defaultdict(list)
    for banner_id, pulls in active_rows().filter(sane, **{f"{fk_name}__isnull": False}).values_list(
        fk_name, "number_of_pulls"
    ):
        by_banner[banner_id].append(pulls)
    return {banner_id: statistics.median(pulls) for banner_id, pulls in by_banner.items()}


def banner_popularity(fk_name, timeline_dates, tally=None):
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

    Active plans only: see active_rows(). `tally` is a BannerTally already
    fed by fold(); without one, this folds its own.
    """
    if tally is None:
        tally = BannerTally()
        fold(tally)
    kind, model = {"banner_uma": ("uma", BannerUma), "banner_support": ("support", BannerSupport)}[fk_name]
    planned = {banner_id: people for (row_kind, banner_id), people in tally.planners.items() if row_kind == kind}
    # Keyed by id, so two banners that share a name never merge.
    banners = {
        banner["id"]: banner
        for banner in model.objects.filter(id__in=planned).values(
            "id", "name", "banner_timeline_id", "banner_timeline__name",
        )
    }
    now = timezone.now()
    rows = []
    for banner_id, people in planned.items():
        banner = banners[banner_id]
        sane = tally.sane[(kind, banner_id)]
        rows.append({
            "id": banner_id,
            "name": banner["name"],
            "timeline": banner["banner_timeline__name"],
            **_dated(banner["banner_timeline_id"], timeline_dates, now),
            "planners": people,
            # A banner planned only by an outlier has no sane row: zero, not
            # an error.
            "total_pulls": sum(sane),
            "avg_pulls": round(sum(sane) / len(sane), 1) if sane else 0,
            # The MEDIAN ("typical") beside the average, for the Rankings
            # page's Pull plans tab: a handful of 1,000-pull whales drags an
            # average far past what most planners budget.
            "median_pulls": statistics.median(sane) if sane else 0,
            "excluded": tally.excluded[(kind, banner_id)],
            # Sorting only: a banner with NO sane row goes after one whose
            # sane rows sum to zero (the old SQL's NULLS LAST).
            "_has_sane": bool(sane),
        })
    # Most planners, then most pulls, then the lower id, so ties never wobble
    # between builds.
    rows.sort(key=lambda row: (-row["planners"], not row["_has_sane"], -row["total_pulls"], row["id"]))
    for row in rows:
        del row["_has_sane"]
    return rows


class BannerTally:  # pylint: disable=too-few-public-methods
    """Per uma and support banner: its planners (each person once), the pulls
    of its SANE rows (a list, for the sum, the average and the median), and
    how many rows were over SANE_MAX_PULLS. What banner_popularity reads.
    Step-up rows are left to step_up_popularity (STEPS, its own cap)."""

    def __init__(self):
        self.planners = Counter()
        self.sane = defaultdict(list)
        self.excluded = Counter()

    def add(self, _user_id, rows):
        seen = set()
        for key, pulls in rows:
            if key[0] == "step_up":
                continue
            # A person with two rows on one banner is one planner.
            if key not in seen:
                seen.add(key)
                self.planners[key] += 1
            if pulls <= SANE_MAX_PULLS:
                self.sane[key].append(pulls)
            else:
                self.excluded[key] += 1


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
    medians = _medians("banner_step_up", sane)
    step_ups = (BannerStepUp.objects
                .filter(id__in=planned.keys() | picked.keys())
                .select_related("anniversary_event"))
    now = timezone.now()
    rows = []
    for step_up in step_ups:
        plan = planned.get(step_up.id, {})
        rows.append({
            "id": step_up.id,
            "name": step_up.name,
            "campaign": step_up.anniversary_event.name,
            "card_type": step_up.get_card_type_display(),
            **_dated(step_up.banner_timeline_id, timeline_dates, now),
            "planners": plan.get("planners", 0),
            "picked": picked.get(step_up.id, 0),
            "total_steps": plan.get("total_steps") or 0,
            "avg_steps": round(plan.get("avg_steps") or 0, 1),
            "median_steps": medians.get(step_up.id, 0),
            "excluded": plan.get("excluded", 0),
        })
    rows.sort(key=lambda row: (-row["planners"], -row["total_steps"], row["name"]))
    return rows


def _month_start(moment):
    return moment.date().replace(day=1)


def _next_month(month_start):
    return (month_start + datetime.timedelta(days=32)).replace(day=1)


def demand_calendar(timeline_dates, tally=None):
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

    `tally` is a DemandTally already fed by fold(); without one, this folds
    its own.
    """
    if tally is None:
        tally = DemandTally(timeline_dates)
        fold(tally)
    calendar = []
    for month in [*tally.months, LATER]:
        if month not in tally.buckets:
            continue
        bucket = tally.buckets[month]
        calendar.append({
            "month": month if month == LATER else f"{month:%Y-%m}",
            "banners": len(bucket["banners"]),
            "planners": bucket["planners"],
            "total_pulls": bucket["total_pulls"],
            "excluded": bucket["excluded"],
            "step_up_planners": bucket["step_up_planners"],
        })
    return calendar


LATER = "Later"
# A banner's month when it has ended: left out of the calendar.
_ENDED = object()


class DemandTally:  # pylint: disable=too-few-public-methods
    """What demand_calendar reads, per month bucket: the banners planned
    there, the people (each once per month), the sane pulls, the rows over
    SANE_MAX_PULLS, and the people planning a step-up there."""

    def __init__(self, timeline_dates, timelines=None):
        self.now = timezone.now()
        self.months = [_month_start(self.now)]
        while len(self.months) < DEMAND_MONTHS:
            self.months.append(_next_month(self.months[-1]))
        self.timeline_dates = timeline_dates
        self.timelines = banner_timelines() if timelines is None else timelines
        self.month_of = {}
        self.buckets = defaultdict(lambda: {"banners": set(), "planners": 0, "total_pulls": 0,
                                            "excluded": 0, "step_up_planners": 0})

    def _month(self, key):
        """The bucket a banner's rows land in, or _ENDED. Worked out once per
        banner, not once per row."""
        if key not in self.month_of:
            entry = self.timeline_dates.get(self.timelines.get(key))
            if banner_status(entry, self.now) == "ended":
                self.month_of[key] = _ENDED
            else:
                end = entry["end_date"] if entry else None
                month = _month_start(end) if end is not None else LATER
                self.month_of[key] = month if month in self.months else LATER
        return self.month_of[key]

    def add(self, _user_id, rows):
        planner_months, step_up_months = set(), set()
        for key, pulls in rows:
            month = self._month(key)
            if month is _ENDED:
                continue
            # Touched first, so a month with only step-up rows still lists.
            bucket = self.buckets[month]
            if key[0] == "step_up":
                step_up_months.add(month)
                continue
            bucket["banners"].add(key)
            planner_months.add(month)
            if pulls <= SANE_MAX_PULLS:
                bucket["total_pulls"] += pulls
            else:
                bucket["excluded"] += 1
        # This person counts once in each month they plan in.
        for month in planner_months:
            self.buckets[month]["planners"] += 1
        for month in step_up_months:
            self.buckets[month]["step_up_planners"] += 1


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
    return [{"uma": row["uma__name"], "uma_id": row["uma_id"], "people": row["people"],
             "as_picture": row["as_picture"]} for row in rows]
