"""
The Rankings page's server-side figures that the client can't count itself
(notes/rankings-redesign-plan.md, step 4): each needs per-person rows the
browser never sees, so it is counted here and only the totals leave.

  pull_plan_figures   per banner: how planners spread their pulls (or steps)
                      over a few buckets, and the three banners most often in
                      the same plans ("also planning")
  budget_by_class     per Team Trials class: people, and the MEDIAN carats held
                      and pulls planned (typical figures, never one person's)
  selector_windows    per campaign selector product: on sale, upcoming or past,
                      from its campaign's derived dates

Every figure follows the analytics' rules: staff out, ACTIVE plans only
(analytics.banners.active_rows), an implausible pull count left out of the
quantities (the SANE_MAX_* caps) but its planner still counted.
"""

import statistics
from collections import Counter, defaultdict

from django.utils import timezone

from calculatorapi.analytics.banners import active_rows
from calculatorapi.analytics.common import SANE_MAX_PULLS, SANE_MAX_STEPS
from calculatorapi.models import AnniversaryEvent, AnniversaryEventProduct, BannerTimeline
from calculatorapi.predictions import anniversary_event_effective_dates, build_effective_date_maps

# Each bucket starts at its edge; the last is open ("300 and up"). A step-up's
# planned figure is ladder STEPS (rules file step-up.md), so its buckets are of
# steps: one step, two, three, four, five and up.
PULL_EDGES = (0, 50, 100, 200, 300)
STEP_EDGES = (0, 2, 3, 4, 5)
# Banners listed under "also planning".
ALSO_PLANNING = 3


def _bucket(value, edges):
    """The index of the bucket `value` falls in."""
    index = 0
    for at, edge in enumerate(edges):
        if value >= edge:
            index = at
    return index


def pull_plan_figures(live_keys, names):
    """{(type, id): {"spread": {"edges", "planners"}, "also": [...]}} for every
    planned banner, in one pass over the active rows.

    SPREAD: each planner once per banner, by what they plan on it (pulls, or
    steps for a step-up), in buckets from PULL_EDGES / STEP_EDGES. A row over
    the sane cap is left out here as it is from the average: its planner
    really plans the banner, but not a billion pulls. So a spread can sum to
    fewer than the row's `planners`, by the row's `excluded`.

    ALSO PLANNING: among the people planning a banner, the other banners they
    plan most, by distinct people. Only banners still to come or running
    (`live_keys`): an ended banner is nothing to plan beside. `names` maps a
    key to its display name.
    """
    planned, sane = _planned_by_person()
    spreads = {}
    together = defaultdict(Counter)
    for user_id, banners in planned.items():
        for key, total in banners.items():
            edges = STEP_EDGES if key[0] == "step_up" else PULL_EDGES
            spread = spreads.setdefault(key, {"edges": list(edges), "planners": [0] * len(edges)})
            if sane[(user_id, key)]:
                spread["planners"][_bucket(total, edges)] += 1
            for other in banners:
                if other != key and other in live_keys:
                    together[key][other] += 1

    return {
        key: {
            "spread": spread,
            "also": [
                {"type": other[0], "id": other[1], "name": names.get(other, ""), "planners": people}
                # Most people first, then the key, so the order never wobbles between builds.
                for other, people in sorted(together[key].items(), key=_most_first)[:ALSO_PLANNING]
            ],
        }
        for key, spread in spreads.items()
    }


def _most_first(item):
    return (-item[1], item[0])


def _planned_by_person():
    """({user: {banner key: pulls or steps planned}}, {(user, key): sane}), one
    query over the active rows. One row over the cap marks that person's
    figure for the banner as not sane."""
    planned = defaultdict(lambda: defaultdict(int))
    sane = {}
    for user_id, uma_id, support_id, step_up_id, pulls in active_rows().values_list(
        "user_id", "banner_uma_id", "banner_support_id", "banner_step_up_id", "number_of_pulls"
    ):
        key = ("uma", uma_id) if uma_id else ("support", support_id) if support_id else ("step_up", step_up_id)
        cap = SANE_MAX_STEPS if key[0] == "step_up" else SANE_MAX_PULLS
        planned[user_id][key] += pulls
        sane[(user_id, key)] = sane.get((user_id, key), True) and pulls <= cap
    return planned, sane


def _median(values):
    return round(statistics.median(values)) if values else None


def budget_by_class(engaged):
    """Per Team Trials class, highest first: {"name", "people", "carats",
    "pulls"} among `engaged` (non-staff people who use the calculator) with a
    class set. `carats` is the median carats held (`current_carat`, the
    account's own stats block); `pulls` the median pulls planned in the active
    plan, among the class's people who plan any (step-up steps aren't pulls,
    so they're out). Typical figures, never one person's: a median of a class
    with one person IS that person, so a class needs more than one."""
    people = defaultdict(list)
    for user_id, rank_name, income, carats in engaged.filter(team_trials_rank__isnull=False).values_list(
        "id", "team_trials_rank__name", "team_trials_rank__income_amount", "current_carat"
    ):
        people[(income, rank_name)].append((user_id, carats))

    pulls = defaultdict(int)
    sane_pulls = active_rows().filter(banner_step_up__isnull=True, number_of_pulls__lte=SANE_MAX_PULLS)
    for user_id, count in sane_pulls.values_list("user_id", "number_of_pulls"):
        pulls[user_id] += count

    rows = []
    for (_income, name), members in sorted(people.items(), key=lambda item: (-(item[0][0] or 0), item[0][1])):
        if len(members) < 2:
            continue
        planned = [pulls[user_id] for user_id, _ in members if pulls[user_id] > 0]
        rows.append({
            "name": name,
            "people": len(members),
            "carats": _median([carats for _, carats in members]),
            "pulls": _median(planned),
        })
    return rows


def selector_windows():
    """{product id: {"status", "end_date"}} for every selector product: "on_sale"
    while its campaign runs, "upcoming" before (or undated), "past" after. A
    campaign owns no dates; they come from its banner parts on the shared
    calendar (predictions.anniversary_event_effective_dates)."""
    timeline_dates = build_effective_date_maps()[BannerTimeline]
    now = timezone.now()
    windows = {}
    for event in AnniversaryEvent.objects.prefetch_related("banner_links"):
        dates = anniversary_event_effective_dates(event, timeline_dates)
        start, end = dates["start_date"], dates["end_date"]
        if start is not None and start <= now and (end is None or now <= end):
            status = "on_sale"
        elif end is not None and end < now:
            status = "past"
        else:
            status = "upcoming"
        windows[event.id] = {"status": status, "end_date": end.isoformat() if end else None}
    return {
        product_id: windows.get(event_id, {"status": "upcoming", "end_date": None})
        for product_id, event_id in AnniversaryEventProduct.objects.values_list("id", "anniversary_event_id")
    }
