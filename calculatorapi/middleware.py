"""
Each request's database share, for gunicorn's access line
(notes/server-diagnostics-plan.md, item 7).

The access line already ends in how long a request took ("... 412ms"). That
number can't say whether the time went to Postgres or to Python: on 2026-10-29
it took a local reproduction to learn that one auto-save cost 133 queries. This
middleware counts every query a request runs and adds up their time, and the
line then reads "... db=133q/290ms 412ms".

HOW IT REACHES THE LOG LINE
---------------------------
Django's `request.META` IS the WSGI environ dict (WSGIRequest sets
`self.META = environ`), and gunicorn writes the access line after the response
from that same dict: `%({calc_db}e)s` in gunicorn.conf.py reads the CALC_DB key
(gunicorn lower-cases environ keys for its atoms). So the numbers never reach
the visitor, which a `Server-Timing` header would have done (a query count can
give away whether a lookup found a row), and there is no second log line per
request. A request that never got this far prints "-" in that place.

WHAT IS COUNTED
---------------
Every query on the default connection while the request is inside the
middleware stack, the session and auth lookups included, because it sits first
in MIDDLEWARE. An executemany is one query. Not counted: anything a streaming
response runs while it is being sent, which this API doesn't do. The cost is
one Python call per query.

THE ROUTE TOTALS, FOR THE HEALTH ALERT (item 4)
-----------------------------------------------
The same middleware adds each finished request to ROUTE_STATS: per minute, for
the last WINDOW_MINUTES, the request count, total and slowest time and database
time of every route PATTERN ("plan/<str:public_id>", never the raw path, so no
plan or post ids), plus how many requests are inside Django right now.
`GET /bot/health` serves it and Oguri Bot's "site is slow" alert quotes the
busiest routes, so the alert itself says what the API was busy with.

It lives in this process's memory, which is only the whole picture because
production runs ONE gunicorn process (gunicorn.conf.py). A second worker or
instance would each hold its own share, the same reason the payload cache must
move to a shared backend before either happens.
"""

import threading
import time

from django.db import connection

# The environ key gunicorn.conf.py's access_log_format reads as %({calc_db}e)s.
ENVIRON_KEY = "CALC_DB"

# How far back ROUTE_STATS remembers, in minutes; GET /bot/health asks for at
# most this many.
WINDOW_MINUTES = 10

# A request Django answered before resolving a URL (WhiteNoise serving a static
# file, a redirect from CommonMiddleware) has no route pattern.
NO_ROUTE = "(no route)"


class QueryTally:
    """An execute wrapper (Django's hook for instrumenting queries) that counts
    the queries passing through it and adds up how long they took."""

    def __init__(self):
        self.count = 0
        self.seconds = 0.0

    def __call__(self, execute, sql, params, many, context):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            # In `finally` so a query that raised (a deadlock, a constraint)
            # still counts: those are exactly the slow ones worth seeing.
            self.count += 1
            self.seconds += time.perf_counter() - started

    def describe(self):
        return f"db={self.count}q/{round(self.seconds * 1000)}ms"


class RouteStats:
    """Per-minute totals by route for the last WINDOW_MINUTES, and the number of
    requests in progress. Thread-safe: gunicorn's threads all write here, so
    every read and write holds the lock (each is a few dict operations)."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        # {epoch minute: {"GET calculator-data": [count, total_ms, max_ms, db_ms, queries]}}
        self._minutes = {}
        self._in_flight = 0

    def started(self):
        with self._lock:
            self._in_flight += 1

    def finished(self, route, ms, db_ms, queries):
        minute = int(self._clock() // 60)
        with self._lock:
            self._in_flight -= 1
            row = self._minutes.setdefault(minute, {}).setdefault(route, _empty_row())
            _fold(row, 1, ms, ms, db_ms, queries)
            # Forget minutes past the window here, on the write path, so the
            # dict never holds more than WINDOW_MINUTES of them.
            for old in [m for m in self._minutes if m <= minute - WINDOW_MINUTES]:
                del self._minutes[old]

    def busiest(self, minutes, limit=8):
        """The last `minutes` (the current, partial one included), routes by
        total time, most first: {in_flight, requests, routes: [...]}."""
        now = int(self._clock() // 60)
        totals = {}
        with self._lock:
            in_flight = self._in_flight
            for minute, routes in self._minutes.items():
                if minute > now - minutes:
                    for route, row in routes.items():
                        _fold(totals.setdefault(route, _empty_row()), *row)
        ranked = sorted(totals.items(), key=lambda item: item[1][1], reverse=True)
        return {
            "in_flight": in_flight,
            "requests": sum(row[0] for row in totals.values()),
            "routes": [_as_json(route, row) for route, row in ranked[:limit]],
        }


# A route's totals, as a list so they can be added to in place:
# [count, total_ms, max_ms, db_ms, queries].
def _empty_row():
    return [0, 0.0, 0.0, 0.0, 0]


def _fold(row, count, total_ms, max_ms, db_ms, queries):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Add one request, or one minute's totals, into `row`."""
    row[0] += count
    row[1] += total_ms
    row[2] = max(row[2], max_ms)
    row[3] += db_ms
    row[4] += queries


def _as_json(route, row):
    count, total_ms, max_ms, db_ms, queries = row
    return {
        "route": route,
        "count": count,
        "total_ms": round(total_ms),
        "max_ms": round(max_ms),
        "db_ms": round(db_ms),
        "queries": queries,
    }


# The one recorder, for this process (see "The route totals" above).
ROUTE_STATS = RouteStats()


def route_label(request):
    """"PATCH calculator-data": the method and the matched URL PATTERN, never
    the path itself, so ids and query strings never reach the totals."""
    match = getattr(request, "resolver_match", None)
    return f"{request.method} {match.route if match else NO_ROUTE}"


class RequestStatsMiddleware:  # pylint: disable=too-few-public-methods
    """Tallies a request's queries and leaves the tally in the environ, where
    gunicorn's access line picks it up, and adds the request to ROUTE_STATS."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tally = QueryTally()
        started = time.perf_counter()
        ROUTE_STATS.started()
        try:
            # An execute wrapper belongs to one connection, and Django gives
            # every thread its own, so gunicorn's four threads never count
            # each other's queries.
            with connection.execute_wrapper(tally):
                response = self.get_response(request)
        finally:
            # In `finally` so the in-progress count can never drift upwards,
            # even if something below Django's own error handling raises.
            ms = (time.perf_counter() - started) * 1000
            ROUTE_STATS.finished(route_label(request), ms, tally.seconds * 1000, tally.count)
        request.META[ENVIRON_KEY] = tally.describe()
        return response
