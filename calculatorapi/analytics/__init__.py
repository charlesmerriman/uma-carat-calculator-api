"""
Aggregate, anonymized usage statistics for the admin analytics dashboard.

This package is pure query logic — no HTTP concerns — so the math can be
unit-tested directly and reused by every reader: the HTML dashboard, the CSV
export (both in views/analytics.py) and the admin index's KPI cards
(admin_dashboard.py).

Privacy note: everything returned here is an aggregate (counts, percentages,
averages). No function in this package may ever return per-user rows or any
identifying field (username, email, etc.).

Where things live:

    report.py           build_analytics_report(): the whole report as a dict
    people.py           who is counted: non-staff users, the engaged subset
    income_settings.py  paid products, selectors, ranks, resources
    banners.py          banner popularity
    traffic.py          site traffic, from the visit counters in visits.py
    common.py           pct() and the sanity bounds every section shares
    snapshots.py        one stored copy a day; the comparison and History
    cache.py            get_report(): the five-minute copy every reader uses
    tables.py           the report as tables, the one shape both renderers read

The names below are re-exported so callers import from the package and never
from a module inside it.
"""

from .cache import REPORT_CACHE_KEY, get_report
from .common import SANE_MAX_PULLS, SANE_MAX_RESOURCE
from .report import build_analytics_report
from .tables import csv_rows, page_tables, report_tables

__all__ = [
    "REPORT_CACHE_KEY",
    "SANE_MAX_PULLS",
    "SANE_MAX_RESOURCE",
    "build_analytics_report",
    "csv_rows",
    "get_report",
    "page_tables",
    "report_tables",
]
