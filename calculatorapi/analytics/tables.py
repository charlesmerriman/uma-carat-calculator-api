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
        "collapse": {...} or None,   # page only: rows to fold away, see below
        "chart": {...} or None,      # page only: a charts.py chart drawn above
    }

`collapse` names rows the page tucks under a closed <details> instead of the
table, {"key": "status", "value": "ended", "label": "Ended banners"}: the
banner tables keep ended banners there, so what is coming reads first. The CSV
ignores it and writes every row, with the Status column saying which is which.

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
    flag     a yes/no: "Yes" or blank in both

None is a blank cell in both renderers, and a string is written as it is
whatever the kind, which is how a totals row puts "Total" in a date column.
"""

import json
from typing import NamedTuple

from django.utils.text import slugify

from . import charts


class Column(NamedTuple):
    """One column of a section: its header, the row key it reads, its kind."""

    label: str
    key: str
    kind: str = "text"


KINDS = frozenset({"text", "int", "num", "pct", "date", "month", "ignored",
                   "delta", "flag"})

def _section(key, title, columns, rows, *, help_text="", footer=None,
             empty="Nothing recorded yet.", collapse=None, chart=None):
    # pylint: disable=too-many-arguments
    # Four required fields plus optional keyword-only ones: this is the
    # section dict's constructor, and its parameters are its keys.
    return {
        "key": key,
        "title": title,
        "help": help_text,
        "columns": columns,
        "rows": rows,
        "footer": footer,
        "empty": empty,
        "collapse": collapse,
        "chart": chart,
    }


# Shared column runs. "% of engaged" is usually the more useful of the two:
# brand-new accounts start with everything off and drag "% of total" down.
_SHARES = [
    Column("% of total", "pct_of_total", "pct"),
    Column("% of engaged", "pct_of_engaged", "pct"),
]

# Every banner row carries effective dates: confirmed when the game has
# announced them, otherwise predicted (the Predicted column says which).
_DATED = [
    Column("Start", "start_date", "date"),
    Column("End", "end_date", "date"),
    Column("Predicted", "predicted", "flag"),
    Column("Status", "status"),
]

_BANNER_COLUMNS = [
    Column("Banner", "name"),
    Column("Timeline", "timeline"),
    *_DATED,
    Column("Planners", "planners", "int"),
    Column("Total pulls", "total_pulls", "int"),
    Column("Avg pulls", "avg_pulls", "num"),
    Column("Ignored values", "excluded", "ignored"),
]

_ENDED = {"key": "status", "value": "ended", "label": "Ended"}


def _visit_source_columns(label):
    """Columns for the landing-page and referrer tables (`label` names the first)."""
    return [Column(label, "name"), Column("Visits", "visits", "int"),
            Column("Share", "share", "pct"),
            Column("The 30 days before", "earlier", "int")]

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
    Column("Supporters", "active_supporters", "int"),
    Column("Unique visitors", "unique_visitors", "int"),
]

_BANNER_HELP = (
    "Planners counts everyone who has the banner in their active plan. Pull "
    "figures leave out implausibly large entries; “Ignored values” is how many. "
    "Dates are predicted until the game announces them. Ended banners are "
    "folded away below the table."
)


def report_tables(report):
    """Every section of the report, in page order, as tables."""
    return [
        *_overview_tables(report),
        *_traffic_tables(report),
        *_people_tables(report),
        *_settings_tables(report),
        *_banner_tables(report),
    ]


def _overview_tables(report):
    compared = report["comparison"]["figures"]
    since = report["comparison"]["since"]
    return [
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
            chart=charts.history(report["history"]),
        ),
    ]


def _traffic_tables(report):
    days = report["daily_window_days"]
    totals = report["daily_totals"]
    return [
        _section(
            "daily_visits", f"Site traffic: last {days} days",
            [Column("Date", "date", "date"),
             Column("Page views", "page_views", "int"),
             Column("Unique visitors", "unique_visitors", "int")],
            report["daily_visits"],
            help_text=(
                "Counts every visitor, signed in or not: the only section here "
                "that can see guests. One page view is one browser session, not "
                "one click. Days with no traffic are omitted rather than shown "
                "as zero. The total row adds up each day’s unique visitors, so "
                "someone who came on five days counts five times: read it as "
                "visit-days, never as people."
            ),
            footer={"date": "Total", "page_views": totals["page_views"],
                    "unique_visitors": totals["visit_days"]},
            empty="No visits recorded yet.",
            chart=charts.daily_traffic(report["daily_visits"], days),
        ),
        _section(
            "traffic_weeks", "Site traffic: last 7 days against the 7 before",
            [Column("Measure", "metric"),
             Column("Last 7 days", "this_week", "int"),
             Column("The 7 before", "last_week", "int"),
             Column("Change (%)", "change_pct", "delta")],
            report["traffic_weeks"],
            help_text=(
                "The last 7 days include today, which is still running. Change "
                "is blank when the earlier week had nothing to grow from."
            ),
        ),
        _section(
            "landing_pages", "Landing pages (last 30 days)",
            _visit_source_columns("Page"), report["landing_pages"],
            help_text=(
                "The page each visit started on. These are visits, never "
                "visitors: one person who comes back three times counts three "
                "times. “other” is any address that is not one of the site’s "
                "pages, such as a typo or an old link."
            ),
            empty="No landing pages recorded yet.",
            chart=charts.landing_pages(report["landing_pages"]),
        ),
        _section(
            "referrers", "Referring sites (last 30 days)",
            _visit_source_columns("Site"), report["referrers"],
            help_text=(
                "The site that linked each visit here, by name only. “direct” "
                "means the browser said nothing, which covers bookmarks, typed "
                "addresses and most chat apps: Discord’s desktop app sends no "
                "referrer, so Discord traffic mostly lands in “direct”, and "
                "that row runs high. The 25 busiest sites are named; the rest "
                "share the last row."
            ),
            empty="No referring sites recorded yet.",
        ),
        _section(
            "monthly_visits", "Site traffic: by month",
            # A true monthly-active count, so it is SMALLER than the sum of the
            # daily uniques above. The qualifier is in the header because a
            # reader who tries to reconcile the two columns will otherwise
            # assume one of them is wrong.
            [Column("Month", "month", "month"),
             Column("Page views", "page_views", "int"),
             Column("Unique visitors (counted once per month)",
                    "unique_visitors", "int"),
             Column("Visit-days per visitor", "days_per_visitor", "num"),
             Column("Partial", "partial", "flag")],
            report["monthly_visits"],
            help_text=(
                "Monthly unique visitors are a true monthly-active count: "
                "someone who visits on fifteen days in a month counts once. It "
                "is therefore smaller than the sum of that month’s daily unique "
                "visitors, and the two are not meant to reconcile. Their ratio "
                "is visit-days per visitor: ten visitors where one came on "
                "twenty days and nine came once is 29 visit-days over 10 "
                "people, 2.9. The month still running is marked partial."
            ),
            empty="No visits recorded yet.",
            chart=charts.monthly_traffic(report["monthly_visits"]),
        ),
    ]


_GROWTH_COLUMNS = [
    Column("Period", "period"),
    Column("New accounts", "new_accounts", "int"),
    Column("Partial", "partial", "flag"),
]


def _people_tables(report):
    supporters = report["supporters"]
    return [
        _section(
            "growth_by_month", "New accounts by month",
            _GROWTH_COLUMNS, report["growth_by_month"],
            help_text=(
                "Non-staff accounts by the month they joined. Accounts deleted "
                "since are not counted. The month still running is partial."
            ),
        ),
        _section(
            "growth_by_week", "New accounts by week",
            _GROWTH_COLUMNS, report["growth_by_week"],
            help_text="Seven-day weeks ending today; the first is still running.",
        ),
        _section(
            "activity", "Activity",
            [Column("Measure", "measure"), Column("Users", "users", "int"),
             Column("Out of", "out_of", "int"), Column("Share", "share", "pct")],
            report["activity"],
            help_text=(
                "A save is any change to a plan or its stats, or a new plan. "
                "Sign-ins undercount: "
                "a sign-in lasts until the person signs out, so someone who uses "
                "the site daily may not have signed in for months. “Came back” "
                "counts, of accounts older than a week, those who saved or signed "
                "in more than a week after joining."
            ),
        ),
        _section(
            "sign_in_providers", "Sign-in providers",
            [Column("Provider", "label"), Column("Users", "users", "int"),
             Column("% of total", "pct_of_total", "pct")],
            report["sign_in_providers"],
            help_text=(
                "People per provider; someone linked to two counts under both "
                "and once in “Two or more”."
            ),
        ),
        _section(
            "supporters", "Supporters",
            [Column("Tier", "tier"), Column("Active", "active", "int"),
             Column("Linked to an account", "linked", "int"),
             Column("Shown publicly", "public", "int")],
            supporters,
            help_text=(
                "Active Patreon supporters, who need no account here, so staff "
                "are not left out of these counts. “Linked” leaves out staff "
                "accounts. “Shown publicly” is who agreed to be named on the "
                "About page."
            ),
            footer={"tier": "Total",
                    "active": sum(row["active"] for row in supporters),
                    "linked": sum(row["linked"] for row in supporters),
                    "public": sum(row["public"] for row in supporters)},
            empty="No active supporters.",
        ),
        _section(
            "feature_adoption", "Feature adoption",
            [Column("Feature", "feature"), Column("Users", "users", "int"),
             Column("% of engaged", "pct_of_engaged", "pct")],
            report["feature_adoption"],
            help_text=(
                "People using each feature. Anything on a planned banner counts "
                "the active plan only, like the banner tables."
            ),
        ),
    ]


def _settings_tables(report):
    any_selector = report["any_selector"]
    sections = [
        _section(
            "income_settings", "Income settings (engaged users)",
            [Column("Setting", "label"), Column("Default", "default"),
             Column("Users on", "users_on", "int"),
             Column("% on", "pct_on", "pct"),
             Column("Changed from default", "changed", "int"),
             Column("% changed", "pct_changed", "pct")],
            report["income_settings"],
            help_text=(
                "Counted among engaged users, because the settings that start "
                "on are on for every account that never opened the calculator "
                "too. “Changed from default” is how many switched it: on for a "
                "setting that starts off, off for one that starts on. For a "
                "setting that starts on, that is the number that says something."
            ),
        ),
        _section(
            "shop_tickets", "Shop tickets bought a month (engaged users)",
            [Column("Tickets a month", "bought"), Column("Uma", "uma", "int"),
             Column("Support", "support", "int")],
            report["shop_tickets"],
            help_text=(
                "How many monthly shop tickets people say they buy. “Not set” "
                "follows the default an editor sets. The counts only change the "
                "projection while Monthly shop tickets is on."
            ),
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
            chart=charts.rank_distribution(distribution["rows"]),
        ))

    sections.append(_section(
        "resources", "Current resources (engaged users)",
        # Median leads the average deliberately: it is the figure that
        # survives an extreme value, and in a spreadsheet the first numeric
        # column is the one that gets charted. "Ignored values" is carried
        # so a reader who charts a month of downloads can see whether a jump
        # was users or a typo.
        [Column("Resource", "label"), Column("Median", "median", "num"),
         Column("p25", "p25", "num"), Column("p75", "p75", "num"),
         Column("At zero", "zero_pct", "pct"),
         Column("Average", "avg", "num"),
         Column("Ignored values", "excluded", "ignored")],
        report["resource_averages"],
        help_text=(
            "Median is the typical holding. Unlike the average, no single "
            "account can move it. Half of everyone sits between p25 and p75: "
            "with nine people holding 0, 0, 2,000, 5,000, 9,000, 12,000, "
            "30,000, 45,000 and 400,000 carats, half hold between 2,000 and "
            "30,000 while one whale lifts the average to 55,889. “At zero” is "
            "the share holding none. “Ignored values” counts values too large "
            "to be real answers, which are left out of every figure; saved "
            "plans are never altered."
        ),
    ))
    return sections


def _banner_tables(report):
    return [
        _section(
            "popular_uma_banners", "Popular Uma banners",
            _BANNER_COLUMNS, report["popular_uma_banners"],
            help_text=_BANNER_HELP,
            empty="No upcoming or running Uma banners are planned.",
            collapse=_ENDED,
        ),
        _section(
            "popular_support_banners", "Popular Support banners",
            _BANNER_COLUMNS, report["popular_support_banners"],
            help_text=_BANNER_HELP,
            empty="No upcoming or running Support banners are planned.",
            collapse=_ENDED,
        ),
        _section(
            "step_up_popularity", "Step-up banners",
            [Column("Step-up", "name"), Column("Campaign", "campaign"),
             Column("Card type", "card_type"), *_DATED,
             Column("Planners", "planners", "int"),
             Column("Chose their cards", "picked", "int"),
             Column("Total steps", "total_steps", "int"),
             Column("Avg steps", "avg_steps", "num"),
             Column("Ignored values", "excluded", "ignored")],
            report["step_up_popularity"],
            help_text=(
                "Step-up plans count ladder STEPS, never pulls: one step is one "
                "10-pull, paid carats only, at most five per banner. “Chose "
                "their cards” is everyone who picked their own ten, planning to "
                "climb or not; an untouched step-up uses the default ten and "
                "is not counted."
            ),
            empty="No upcoming or running step-ups are planned.",
            collapse=_ENDED,
        ),
        _section(
            "demand_calendar", "Demand by month",
            [Column("Month the banner ends", "month"),
             Column("Banners", "banners", "int"),
             Column("Planners", "planners", "int"),
             Column("Total pulls", "total_pulls", "int"),
             Column("Ignored values", "excluded", "ignored"),
             Column("Step-up planners", "step_up_planners", "int")],
            report["demand_calendar"],
            help_text=(
                "Planned Uma and Support banners grouped by the month they end, "
                "which is when the carats leave a saving plan. Someone with two "
                "banners in a month counts once in Planners and twice in Total "
                "pulls. Step-ups are counted apart because their plans are in "
                "steps. Ended banners are left out; “Later” holds everything "
                "past the next six months and anything still undated."
            ),
            empty="Nothing upcoming is planned.",
            chart=charts.demand(report["demand_calendar"]),
        ),
        _section(
            "favourite_umas", "Favourite umas",
            [Column("Uma", "uma"), Column("People", "people", "int"),
             Column("As their picture", "as_picture", "int")],
            report["favourite_umas"],
            help_text=(
                "The 20 umas most often picked as a favourite. “As their "
                "picture” counts people for whom it is the first favourite, "
                "the one shown as their account picture. A costume is its own "
                "uma here."
            ),
            empty="No favourites picked yet.",
        ),
    ]


# How each kind reads, per renderer. A kind missing from a table is written as
# it is: str() on the page, the raw value (a number stays a number) in the CSV.
_PAGE_FORMATS = {
    "flag": lambda value: "Yes" if value else "",
    "pct": lambda value: f"{value}%",
    "delta": lambda value: f"{value:+}",
    "date": lambda value: value.strftime("%Y-%m-%d"),
    "month": lambda value: value.strftime("%Y-%m"),
}
_CSV_FORMATS = {
    "flag": _PAGE_FORMATS["flag"],
    "date": _PAGE_FORMATS["date"],
    "month": _PAGE_FORMATS["month"],
}


def page_cell(value, kind):
    """One cell as the dashboard page shows it: always a string."""
    if kind == "ignored" and not value:
        return "–"
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _PAGE_FORMATS.get(kind, str)(value)


def csv_cell(value, kind):
    """One cell as the CSV writes it. Numbers stay numbers, so a spreadsheet
    can chart them; only dates and flags become text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _CSV_FORMATS.get(kind, lambda raw: raw)(value)


def _cells(row, columns, formatter):
    return [formatter(row[column.key], column.kind) for column in columns]


def _page_chart(chart):
    if chart is None:
        return None
    return {
        "template": f"unfold/components/chart/{chart['type']}.html",
        "data": json.dumps(chart["data"]),
        "options": json.dumps(chart["options"]),
    }


def _is_collapsed(row, collapse):
    return collapse is not None and row[collapse["key"]] == collapse["value"]


def page_tables(report):
    """report_tables(), with every cell already formatted for the page.

    The template can only loop, so the formatting happens here: `cells` holds
    one list of strings per row, `collapsed_cells` the rows `collapse` folds
    away, `footer_cells` the totals row or None. A chart becomes the unfold
    component to include and its data and options as JSON strings, which the
    component writes into the canvas's data attributes.
    """
    tables = []
    for section in report_tables(report):
        columns, collapse = section["columns"], section["collapse"]
        tables.append({
            **section,
            "cells": [_cells(row, columns, page_cell) for row in section["rows"]
                      if not _is_collapsed(row, collapse)],
            "collapsed_cells": [_cells(row, columns, page_cell)
                                for row in section["rows"]
                                if _is_collapsed(row, collapse)],
            "footer_cells": (_cells(section["footer"], columns, page_cell)
                             if section["footer"] else None),
            "chart": _page_chart(section["chart"]),
        })
    return tables


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
