"""Site traffic: the one section that sees guests, and the one with history."""

from ..visits import build_visit_report


def traffic():
    """The report keys for site traffic, from the visit counters.

    Counts everyone who loaded the site, signed in or not — unlike every
    other section here, which can only see accounts. Guests are most of the
    traffic, so this is the only number on the page that reflects them.

    Recording and reading the counters is visits.py's job; this module is
    where figures DERIVED from them belong, so the report has one place to
    ask for traffic.
    """
    visits = build_visit_report()
    return {
        "daily_visits": visits["daily"],
        "monthly_visits": visits["monthly"],
        "daily_window_days": visits["daily_window_days"],
    }
