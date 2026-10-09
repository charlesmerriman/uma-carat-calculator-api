"""
The report as tables: the one shape both renderers read.

build_analytics_report() returns a plain dict, and that dict stays the contract
for the tests and the KPI cards. This module is presentation only. It turns the
dict into a list of SECTIONS, each a title, an optional help paragraph, a list
of columns and the rows those columns read. The dashboard page and the CSV are
each one loop over that list, so they cannot drift apart again. They had: the
page showed one "Runs" column where the CSV had Start and End, and the two
titled most sections differently.

Adding a section is two steps: a function in a section module that computes the
numbers, and one entry in report_tables() saying how they read. Neither
renderer needs touching.

A section is a dict:

    {
        "key": "paid_products",      # unique; the anchor id on the page
        "title": "Paid products",
        "help": "...",               # optional paragraph under the title
        "columns": [Column(...), ...],
        "rows": [{...}, ...],        # dicts carrying every column's key
        "footer": {...} or None,     # a totals row, keyed like the rows
        "empty": "...",              # the page's text when there are no rows
    }

Column KINDS decide formatting, and only here:

    text     shown as is
    int      a count
    num      a figure the report already rounded (an average, a median)
    pct      a percentage: "12.5%" on the page, 12.5 in the CSV so a
             spreadsheet can chart it
    date     YYYY-MM-DD. Blank when missing: a banner that is only predicted
             has no confirmed date, and a strftime on None used to 500 the
             whole CSV download as soon as anyone planned a future banner.
    month    YYYY-MM
    ignored  values left out as implausible (see common.SANE_MAX_PULLS). The
             CSV writes the count; the page writes an en dash for zero, so the
             rare row that dropped something stands out.
    delta    a change between two figures: signed on the page ("+3", "-0.5"),
             a plain number in the CSV

None is a blank cell in both renderers.
"""

from typing import NamedTuple

from django.utils.text import slugify


class Column(NamedTuple):
    """One column of a section: its header, the row key it reads, its kind."""

    label: str
    key: str
    kind: str = "text"


KINDS = frozenset({"text", "int", "num", "pct", "date", "month", "ignored",
                   "delta"})

_DATE_FORMATS = {"date": "%Y-%m-%d", "month": "%Y-%m"}


def _section(key, title, columns, rows, *, help_text="", footer=None,
             empty="Nothing recorded yet."):
    # pylint: disable=too-many-arguments
    # Four required fields plus three optional keyword-only ones: this is the
    # section dict's constructor, and its parameters are its keys.
    return {
        "key": key,
        "title": title,
        "help": help_text,
        "columns": columns,
        "rows": rows,
        "footer": footer,
        "empty": empty,
    }


# Shared column runs. "% of engaged" is usually the more useful of the two:
# brand-new accounts start with everything off and drag "% of total" down.
_SHARES = [
    Column("% of total", "pct_of_total", "pct"),
    Column("% of engaged", "pct_of_engaged", "pct"),
]

_BANNER_COLUMNS = [
    Column("Banner", "name"),
    Column("Timeline", "timeline"),
    Column("Start", "start_date", "date"),
    Column("End", "end_date", "date"),
    Column("Planners", "planners", "int"),
    Column("Total pulls", "total_pulls", "int"),
    Column("Avg pulls", "avg_pulls", "num"),
    Column("Ignored values", "excluded", "ignored"),
]

# The Overview's figures, compared with the snapshot ~30 days back.
_OVERVIEW_FIGURES = ("total_users", "engaged_users", "engaged_pct")

# The History's columns: a choice from snapshots.FIGURES, which may track more
# than fits across a page.
_HISTORY_COLUMNS = [
    Column("Month", "month", "month"),
    Column("Users", "total_users", "int"),
    Column("Engaged", "engaged_users", "int"),
    Column("Daily Carat Pack", "daily_carat", "int"),
    Column("Training Pass", "training_pass", "int"),
    Column("Any selector", "any_selector", "int"),
    Column("Unique visitors", "unique_visitors", "int"),
]

_BANNER_HELP = (
    "Planners counts everyone who has the banner in their plan. Pull figures "
    "leave out implausibly large entries; “Ignored values” is how many."
)


def report_tables(report):
    """Every section of the report, in page order, as tables."""
    days = report["daily_window_days"]
    any_selector = report["any_selector"]
    compared = report["comparison"]["figures"]
    since = report["comparison"]["since"]

    sections = [
        _section(
            "overview", "Overview",
            [Column("Metric", "metric"), Column("Value", "value", "num"),
             Column("30 days ago", "then", "num"),
             Column("Change", "delta", "delta")],
            [
                {"metric": compared[key]["label"], "value": compared[key]["now"],
                 "then": compared[key]["then"], "delta": compared[key]["delta"]}
                for key in _OVERVIEW_FIGURES
            ],
            help_text=(
                f"“30 days ago” is the daily snapshot from {since:%Y-%m-%d}, "
                "the nearest one on or before that day."
                if since else
                "“30 days ago” fills in once a daily snapshot that old exists. "
                "One is kept each day the admin is opened, and on every deploy."
            ),
        ),
        _section(
            "history", "History",
            _HISTORY_COLUMNS, report["history"],
            help_text=(
                "Where things stood as each month began: the first daily "
                "snapshot of the month. Account figures are blank for a month "
                "with no snapshot, which is every month before they began. "
                "Unique visitors are that month’s count from Site traffic."
            ),
            empty="No history yet.",
        ),
        _section(
            "daily_visits", f"Site traffic: last {days} days",
            [Column("Date", "date", "date"), Column("Page views", "page_views", "int"),
             Column("Unique visitors", "unique_visitors", "int")],
            report["daily_visits"],
            help_text=(
                "Counts every visitor, signed in or not: the only section here "
                "that can see guests. One page view is one browser session, not "
                "one click. Days with no traffic are omitted rather than shown "
                "as zero."
            ),
            empty="No visits recorded yet.",
        ),
        _section(
            "monthly_visits", "Site traffic: by month",
            # A true monthly-active count, so it is SMALLER than the sum of the
            # daily uniques above. The qualifier is in the header because a
            # reader who tries to reconcile the two columns will otherwise
            # assume one of them is wrong.
            [Column("Month", "month", "month"), Column("Page views", "page_views", "int"),
             Column("Unique visitors (counted once per month)",
                    "unique_visitors", "int")],
            report["monthly_visits"],
            help_text=(
                "Monthly unique visitors are a true monthly-active count: "
                "someone who visits on fifteen days in a month counts once. It "
                "is therefore smaller than the sum of that month’s daily unique "
                "visitors, and the two are not meant to reconcile."
            ),
            empty="No visits recorded yet.",
        ),
        _section(
            "paid_products", "Paid products",
            [Column("Product", "label"), Column("Users", "count", "int"),
             *_SHARES],
            report["paid_products"],
        ),
        _section(
            "selector_purchases", "Campaign selectors",
            [Column("Campaign", "campaign"), Column("Selector", "label"),
             Column("Users", "count", "int"),
             Column("Card picked", "picked", "int"), *_SHARES],
            report["selector_purchases"],
            # Not a sum of the rows above: someone buying two selectors is in
            # both.
            footer={
                "campaign": "",
                "label": "Any selector",
                "count": any_selector["count"],
                "picked": None,
                "pct_of_total": any_selector["pct_of_total"],
                "pct_of_engaged": any_selector["pct_of_engaged"],
            },
            empty="No selector products exist yet.",
        ),
    ]

    for distribution in report["rank_distributions"]:
        label = distribution["label"]
        sections.append(_section(
            "rank_" + slugify(label).replace("-", "_"),
            f"Rank distribution: {label}",
            [Column("Rank", "name"), Column("Users", "count", "int"),
             Column("% of total", "pct_of_total", "pct")],
            distribution["rows"],
        ))

    sections += [
        _section(
            "resources", "Current resources (engaged users)",
            # Median leads the average deliberately: it is the figure that
            # survives an extreme value, and in a spreadsheet the first numeric
            # column is the one that gets charted. "Ignored values" is carried
            # so a reader who charts a month of downloads can see whether a jump
            # was users or a typo.
            [Column("Resource", "label"), Column("Median", "median", "num"),
             Column("Average", "avg", "num"),
             Column("Ignored values", "excluded", "ignored")],
            report["resource_averages"],
            help_text=(
                "Median is the typical holding. Unlike the average, no single "
                "account can move it. “Ignored values” counts values too large "
                "to be real answers, which are left out of both figures; saved "
                "plans are never altered."
            ),
        ),
        _section(
            "popular_uma_banners", "Popular Uma banners",
            _BANNER_COLUMNS, report["popular_uma_banners"],
            help_text=_BANNER_HELP, empty="No planned Uma banners yet.",
        ),
        _section(
            "popular_support_banners", "Popular Support banners",
            _BANNER_COLUMNS, report["popular_support_banners"],
            help_text=_BANNER_HELP, empty="No planned Support banners yet.",
        ),
    ]
    return sections


def page_cell(value, kind):
    """One cell as the dashboard page shows it: always a string."""
    if kind == "ignored" and not value:
        return "–"
    if value is None:
        return ""
    if kind == "pct":
        return f"{value}%"
    if kind == "delta":
        return f"{value:+}"
    if kind in _DATE_FORMATS:
        return value.strftime(_DATE_FORMATS[kind])
    return str(value)


def csv_cell(value, kind):
    """One cell as the CSV writes it. Numbers stay numbers, so a spreadsheet
    can chart them; only dates become text."""
    if value is None:
        return ""
    if kind in _DATE_FORMATS:
        return value.strftime(_DATE_FORMATS[kind])
    return value


def _cells(row, columns, formatter):
    return [formatter(row[column.key], column.kind) for column in columns]


def page_tables(report):
    """report_tables(), with every cell already formatted for the page.

    The template can only loop, so the formatting happens here: `cells` holds
    one list of strings per row, `footer_cells` the totals row or None.
    """
    return [
        {
            **section,
            "cells": [_cells(row, section["columns"], page_cell)
                      for row in section["rows"]],
            "footer_cells": (
                _cells(section["footer"], section["columns"], page_cell)
                if section["footer"] else None
            ),
        }
        for section in report_tables(report)
    ]


def csv_rows(report):
    """Every section as CSV rows: title, header, data, footer, a blank line.

    Unlike the page, an empty section still writes its title and header, so a
    month of downloads lines up section for section in a spreadsheet.
    """
    for section in report_tables(report):
        columns = section["columns"]
        yield [section["title"]]
        yield [column.label for column in columns]
        for row in section["rows"]:
            yield _cells(row, columns, csv_cell)
        if section["footer"]:
            yield _cells(section["footer"], columns, csv_cell)
        yield []
