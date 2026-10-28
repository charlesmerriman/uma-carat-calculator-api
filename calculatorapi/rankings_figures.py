"""
The Rankings page's server-side figures that the client can't count itself
(notes/rankings-redesign-plan.md, step 4): each needs per-person rows the
browser never sees, so it is counted here and only the totals leave.

  PlanFigures         per banner: how planners spread their pulls (or steps)
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

from calculatorapi.analytics.banners import STREAM_CHUNK, active_rows
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


class PlanFigures:
    """{(type, id): {"spread": {"edges", "planners"}, "also": [...]}} for every
    planned banner, as a tally that analytics.banners.fold() feeds one person
    at a time (the one pass over the active rows; notes/rankings-speed-plan.md).

    SPREAD: each planner once per banner, by what they plan on it (pulls, or
    steps for a step-up), in buckets from PULL_EDGES / STEP_EDGES. A row over
    the sane cap is left out here as it is from the average: its planner
    really plans the banner, but not a billion pulls. So a spread can sum to
    fewer than the row's `planners`, by the row's `excluded`.

    ALSO PLANNING: among the people planning a banner, the other banners they
    plan most, by distinct people. Only banners still to come or running
    (`live_keys`): an ended banner is nothing to plan beside.
    """

    def __init__(self, live_keys):
        self.live_keys = live_keys
        self.spreads = {}
        self.together = defaultdict(Counter)

    def add(self, _user_id, rows):
        # What this person plans per banner, and whether every row of it is
        # under the cap. One row over the cap marks the banner's figure for
        # this person as not sane.
        totals, sane = defaultdict(int), {}
        for key, pulls in rows:
            cap = SANE_MAX_STEPS if key[0] == "step_up" else SANE_MAX_PULLS
            totals[key] += pulls
            sane[key] = sane.get(key, True) and pulls <= cap
        live = [key for key in totals if key in self.live_keys]
        for key, total in totals.items():
            edges = STEP_EDGES if key[0] == "step_up" else PULL_EDGES
            spread = self.spreads.setdefault(key, {"edges": list(edges), "planners": [0] * len(edges)})
            if sane[key]:
                spread["planners"][_bucket(total, edges)] += 1
            # Counter.update counts a whole list in C: this loop is the pairs
            # of a person's banners, ~20 x 20 each, the costliest step of the
            # build when it was one Python += per pair. It counts the banner
            # beside itself too; result() skips that.
            self.together[key].update(live)

    def result(self, names):
        """The figures, keyed (type, id). `names` maps a key to its display name."""
        return {
            key: {
                "spread": spread,
                "also": [
                    {"type": other[0], "id": other[1], "name": names.get(other, ""), "planners": people}
                    # Most people first, then the key, so the order never wobbles between builds.
                    for other, people in sorted(
                        ((other, people) for other, people in self.together[key].items() if other != key),
                        key=_most_first,
                    )[:ALSO_PLANNING]
                ],
            }
            for key, spread in self.spreads.items()
        }


def _most_first(item):
    return (-item[1], item[0])


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
    # Streamed (iterator), so the ~300,000 rows never sit in memory at once.
    for user_id, count in sane_pulls.values_list("user_id", "number_of_pulls").iterator(chunk_size=STREAM_CHUNK):
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
