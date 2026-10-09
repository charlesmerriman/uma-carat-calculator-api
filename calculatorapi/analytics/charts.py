"""
Chart.js data for the dashboard page, drawn by django-unfold's own chart
component.

unfold (0.101) loads Chart.js on every admin page, and its app.js renderCharts()
draws any <canvas class="chart"> carrying `data-type` and a JSON `data-value`
once the page loads. The template includes unfold/components/chart/<type>.html,
which writes that canvas, so this module only has to produce the JSON. Colours
are unfold's theme tokens, written as var(--color-...): renderCharts resolves
them on the page, and re-colours the grid when the theme flips between light and
dark.

Every chart sits above the table it draws: the table keeps the exact numbers
and is what the CSV carries. Charts are presentation only, like tables.py, and
never reach the CSV or the JSON.

The options are passed explicitly because unfold's defaults hide the legend,
which a two-line chart needs, and draw 4px-thin bars.
"""

import datetime

from django.utils import timezone

# Two shades of the theme's primary colour: the main series, and the one beside it.
MAIN = "var(--color-primary-600)"
SECOND = "var(--color-primary-300)"

# Tick and legend text: unfold's own grey, readable on either theme.
_MUTED = "#9ca3af"


def _options(legend):
    """Chart.js options shared by every chart here."""
    return {
        "animation": False,
        "responsive": True,
        # The template gives each chart a fixed-height box to fill.
        "maintainAspectRatio": False,
        "plugins": {
            "legend": {
                "display": legend,
                "align": "end",
                "labels": {"color": _MUTED, "boxWidth": 6, "boxHeight": 6,
                           "usePointStyle": True, "pointStyle": "circle"},
            },
            "tooltip": {"enabled": True},
        },
        "scales": {
            "x": {"ticks": {"color": _MUTED, "maxTicksLimit": 12},
                  "grid": {"display": False}},
            # precision 0: every chart here counts people or pulls.
            "y": {"beginAtZero": True, "ticks": {"color": _MUTED, "precision": 0}},
        },
    }


def line_chart(labels, series):
    """A line chart. `series` is [(label, values, colour)]; a None value is a
    gap in the line, not a zero."""
    return {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {"label": label, "data": values, "borderColor": colour,
                 "backgroundColor": colour, "borderWidth": 2, "pointRadius": 0,
                 "tension": 0.2}
                for label, values, colour in series
            ],
        },
        "options": _options(legend=len(series) > 1),
    }


def bar_chart(labels, values, label):
    """A one-series bar chart."""
    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [{"label": label, "data": values, "backgroundColor": MAIN,
                          "borderRadius": 4, "maxBarThickness": 40}],
        },
        "options": _options(legend=False),
    }


def daily_traffic(daily_visits, days):
    """Page views and unique visitors per day, oldest first.

    The table omits days with no traffic; a line needs every day, so the
    window is filled in with zeros here (a day with no row had no traffic).
    """
    if not daily_visits:
        return None
    by_day = {row["date"]: row for row in daily_visits}
    today = timezone.localdate()
    window = [today - datetime.timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    return line_chart(
        [f"{day:%m-%d}" for day in window],
        [("Page views", [by_day[day]["page_views"] if day in by_day else 0
                         for day in window], MAIN),
         ("Unique visitors", [by_day[day]["unique_visitors"] if day in by_day else 0
                              for day in window], SECOND)],
    )


def monthly_traffic(monthly_visits):
    """Page views and monthly unique visitors per month, oldest first."""
    if not monthly_visits:
        return None
    rows = list(reversed(monthly_visits))
    return line_chart(
        [f"{row['month']:%Y-%m}" for row in rows],
        [("Page views", [row["page_views"] for row in rows], MAIN),
         ("Unique visitors", [row["unique_visitors"] for row in rows], SECOND)],
    )


def history(history_rows):
    """Users and engaged users at the start of each month, oldest first.

    None until the snapshots reach a month, so the lines start where the
    history does. No chart at all until one month has account figures.
    """
    rows = list(reversed(history_rows))
    if not any(row["total_users"] is not None for row in rows):
        return None
    return line_chart(
        [f"{row['month']:%Y-%m}" for row in rows],
        [("Users", [row["total_users"] for row in rows], MAIN),
         ("Engaged", [row["engaged_users"] for row in rows], SECOND)],
    )


def rank_distribution(rows):
    """Users per rank, in game order. "Not set" stays in the table and out of
    the bars, so the bars show the shape of the people who answered."""
    answered = [row for row in rows if row["name"] != "Not set"]
    if not any(row["count"] for row in answered):
        return None
    return bar_chart([row["name"] for row in answered],
                     [row["count"] for row in answered], "Users")


def demand(calendar_rows):
    """Planned pulls by the month banners end."""
    if not calendar_rows:
        return None
    return bar_chart([row["month"] for row in calendar_rows],
                     [row["total_pulls"] for row in calendar_rows], "Total pulls")
