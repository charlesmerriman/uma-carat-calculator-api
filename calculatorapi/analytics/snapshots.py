"""
Daily snapshots of the report, and the history read back from them.

Every section but traffic describes the database right now, so on its own the
page cannot say whether anything is growing. One stored copy a day
(AnalyticsSnapshot) is what lets it show "30 days ago" and a monthly History.

No scheduler writes them. The first rebuild of the report on a new day keeps a
copy (cache.get_report calls ensure_today), so any staff visit to /admin/ or the
analytics page leaves that day's row, and the deploy chain's
`snapshot_analytics` command adds one per release. A day nobody looked and
nothing deployed has no row; every reader below copes with gaps.

READ STORED REPORTS ONLY THROUGH figure(). The dict's shape changes between
releases (a key added, a label renamed), so an old row may lack what a newer
reader asks for. figure() answers None for anything it cannot find, and None is
a blank cell on the page. Never render a stored report as a page.
"""

import datetime

from django.utils import timezone

from ..models import AnalyticsSnapshot
from .common import REPORT_SHAPE

# How far back "30 days ago" looks: the nearest snapshot on or before this
# many days before today.
COMPARE_DAYS = 30

# How many calendar months the History lists, this one included.
HISTORY_MONTHS = 12

# Report keys a snapshot leaves out. The traffic lists have their own permanent
# history in DailyVisit / MonthlyVisit. `comparison` and `history` are built
# FROM snapshots, so storing them would nest every earlier snapshot inside each
# new one.
NOT_STORED = frozenset({"daily_visits", "monthly_visits", "comparison", "history"})


def _paid_product(label):
    """A reader for one paid product's user count, found by its label."""
    def read(report):
        return next(row["count"] for row in report["paid_products"]
                    if row["label"] == label)
    return read


# The named numbers tracked over time: (key, label, reader). A reader takes a
# report dict and returns one number. Adding a figure here puts it in the
# comparison and the History rows for every snapshot that holds it; older
# snapshots simply read None.
FIGURES = [
    ("total_users", "Total users (non-staff)", lambda report: report["total_users"]),
    ("engaged_users", "Engaged users", lambda report: report["engaged_users"]),
    ("engaged_pct", "Engaged %", lambda report: report["engaged_pct"]),
    ("daily_carat", "Daily Carat Pack", _paid_product("Daily Carat Pack")),
    ("training_pass", "Training Pass", _paid_product("Training Pass")),
    ("any_selector", "Any selector", lambda report: report["any_selector"]["count"]),
]


def figure(report, reader):
    """One number from a report, or None when this report's shape lacks it."""
    try:
        return reader(report)
    except (KeyError, TypeError, StopIteration):
        return None


def figures(report):
    """Every tracked figure of one report, keyed like FIGURES."""
    return {key: figure(report, reader) for key, _, reader in FIGURES}


def ensure_today(report):
    """Keep `report` as the snapshot for the day it was built, unless one exists.

    Returns True when this call wrote the row.

    "Exists, then create", with the unique date as the referee. exists() is
    the cheap check every rebuild pays and never loads a stored report. Two
    requests can both pass it in the same instant; get_or_create then lets only
    one insert win and hands the other the existing row, so a race leaves one
    row, never two and never an error.
    """
    day = timezone.localtime(report["generated_at"]).date()
    if AnalyticsSnapshot.objects.filter(date=day).exists():
        return False
    _, created = AnalyticsSnapshot.objects.get_or_create(
        date=day,
        defaults={
            "shape": REPORT_SHAPE,
            "report": {key: value for key, value in report.items()
                       if key not in NOT_STORED},
        },
    )
    return created


def nearest_on_or_before(day):
    """The latest snapshot dated `day` or earlier, or None."""
    return (AnalyticsSnapshot.objects.filter(date__lte=day)
            .order_by("-date").first())


def comparison(report):
    """Today's figures beside those of the nearest snapshot COMPARE_DAYS back.

    `since` is that snapshot's date, so the page can say exactly what it is
    comparing with; None (and every `then` and `delta` None) until a snapshot
    that old exists. A delta is only given when both ends have the figure.
    """
    target = timezone.localdate() - datetime.timedelta(days=COMPARE_DAYS)
    then_row = nearest_on_or_before(target)
    then = figures(then_row.report) if then_row else {}
    rows = {}
    for key, label, reader in FIGURES:
        now_value = figure(report, reader)
        then_value = then.get(key)
        delta = None
        if now_value is not None and then_value is not None:
            # Rounded so a percentage's float noise (0.30000000000000004)
            # never reaches the page.
            delta = round(now_value - then_value, 1)
        rows[key] = {"label": label, "now": now_value, "then": then_value,
                     "delta": delta}
    return {"since": then_row.date if then_row else None, "figures": rows}


def _months_back(month_start, count):
    """The first day of the month `count` months before `month_start`."""
    index = month_start.year * 12 + month_start.month - 1 - count
    return datetime.date(index // 12, index % 12 + 1, 1)


def _first_of_each_month(since):
    """The stored report of each month's FIRST snapshot from `since` on, by month.

    Two queries on purpose. The first reads dates only, so finding the
    earliest row per month never loads a year of stored reports; the second
    loads just the dozen it picked.
    """
    firsts = {}
    for day in (AnalyticsSnapshot.objects.filter(date__gte=since)
                .order_by("date").values_list("date", flat=True)):
        firsts.setdefault(day.replace(day=1), day)
    rows = AnalyticsSnapshot.objects.filter(date__in=firsts.values())
    return {row.date.replace(day=1): row.report for row in rows}


def history(monthly_visits):
    """One row per month, newest first: tracked figures and unique visitors.

    The figures come from the month's FIRST snapshot, so each row is where
    things stood as that month began (the Overview has today). Unique visitors
    come from the monthly traffic counters, so account growth sits next to
    traffic. A month with traffic but no snapshot (every month before
    snapshots began) still gets a row, with its account figures blank; a month
    with neither is left out.
    """
    this_month = timezone.localdate().replace(day=1)
    stored = _first_of_each_month(_months_back(this_month, HISTORY_MONTHS - 1))
    visitors = {row["month"]: row["unique_visitors"] for row in monthly_visits}
    rows = []
    for count in range(HISTORY_MONTHS):
        month = _months_back(this_month, count)
        report = stored.get(month)
        if report is None and month not in visitors:
            continue
        rows.append({
            "month": month,
            **(figures(report) if report is not None
               else dict.fromkeys(key for key, _, _ in FIGURES)),
            "unique_visitors": visitors.get(month),
        })
    return rows
