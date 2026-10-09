"""Site traffic: the one section that sees guests, and the one with history."""

import datetime

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from ..models import DailyVisit
from ..visits import OTHER, build_visit_report

# How many referring sites the table names before folding the rest into one row.
TOP_REFERRERS = 25

# "visit-days": a daily unique visitor summed across days. Someone who comes on
# five days is five visit-days. Every figure that sums daily uniques says so,
# because the sum is NOT a count of people and reads like one.


def traffic():
    """The report keys for site traffic: the counters, and figures derived
    from them.

    Counts everyone who loaded the site, signed in or not — unlike every
    other section here, which can only see accounts. Guests are most of the
    traffic, so this is the only number on the page that reflects them.

    Recording and reading the counters is visits.py's job; this module is
    where figures DERIVED from them belong, so the report has one place to
    ask for traffic.
    """
    visits = build_visit_report()
    daily = visits["daily"]
    return {
        "daily_visits": daily,
        "daily_totals": {
            "page_views": sum(day["page_views"] for day in daily),
            "visit_days": sum(day["unique_visitors"] for day in daily),
        },
        "monthly_visits": _with_visit_days(visits["monthly"]),
        "daily_window_days": visits["daily_window_days"],
        "traffic_weeks": _week_over_week(daily),
        "landing_pages": visits["landing_pages"],
        "referrers": _top_referrers(visits["referrers"]),
    }


def _top_referrers(rows):
    """The TOP_REFERRERS busiest sites, then everything else in one row.

    The "other" bucket visits.py already keeps (malformed hosts, the daily cap)
    folds into that last row too, so the table ends with one line for all the
    rest however it got there.
    """
    named = [row for row in rows if row["name"] != OTHER]
    kept, rest = named[:TOP_REFERRERS], named[TOP_REFERRERS:]
    rest += [row for row in rows if row["name"] == OTHER]
    if rest:
        kept.append({
            "name": "Everything else",
            "visits": sum(row["visits"] for row in rest),
            "share": round(sum(row["share"] for row in rest), 1),
            "earlier": sum(row["earlier"] for row in rest),
        })
    return kept


def _with_visit_days(monthly):
    """Each month's row plus how many days, on average, a visitor came back.

    visit-days per visitor = the month's daily uniques summed / its monthly
    uniques. Worked example: ten visitors in a month, one comes on twenty days
    and nine come once. The daily uniques sum to 29 and the monthly count is
    10, so 2.9. A month where everyone comes once reads 1.0.

    The current month is still running, so it is flagged `partial`: its figure
    will only grow.
    """
    if not monthly:
        return []
    this_month = timezone.localdate().replace(day=1)
    visit_days = dict(
        DailyVisit.objects
        .filter(date__gte=min(row["month"] for row in monthly))
        .annotate(month=TruncMonth("date"))
        .values("month")
        # order_by() clears the model's "-date" ordering, which would otherwise
        # split each month's group back into days.
        .order_by()
        .annotate(total=Sum("unique_visitors"))
        .values_list("month", "total")
    )
    rows = []
    for row in monthly:
        days = visit_days.get(row["month"], 0)
        rows.append({
            **row,
            "visit_days": days,
            "days_per_visitor": (round(days / row["unique_visitors"], 1)
                                 if row["unique_visitors"] else None),
            "partial": row["month"] == this_month,
        })
    return rows


def _week_over_week(daily):
    """The last seven days (today included) against the seven before them.

    A day with no row had no traffic, which counts as zero in a sum, so gaps
    need no filling. `change_pct` is None when the earlier week was empty,
    since growth from nothing has no percentage.
    """
    today = timezone.localdate()
    week_start = today - datetime.timedelta(days=6)
    previous_start = week_start - datetime.timedelta(days=7)
    rows = []
    for key, label in (("page_views", "Page views"),
                       ("unique_visitors", "Visit-days (daily uniques summed)")):
        this_week = sum(day[key] for day in daily if day["date"] >= week_start)
        last_week = sum(day[key] for day in daily
                        if previous_start <= day["date"] < week_start)
        rows.append({
            "metric": label,
            "this_week": this_week,
            "last_week": last_week,
            "change_pct": (round((this_week - last_week) / last_week * 100, 1)
                           if last_week else None),
        })
    return rows
