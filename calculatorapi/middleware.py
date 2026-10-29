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
"""

import time

from django.db import connection

# The environ key gunicorn.conf.py's access_log_format reads as %({calc_db}e)s.
ENVIRON_KEY = "CALC_DB"


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


class RequestStatsMiddleware:  # pylint: disable=too-few-public-methods
    """Tallies a request's queries and leaves the tally in the environ, where
    gunicorn's access line picks it up."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tally = QueryTally()
        # An execute wrapper belongs to one connection, and Django gives every
        # thread its own, so gunicorn's four threads never count each other's
        # queries.
        with connection.execute_wrapper(tally):
            response = self.get_response(request)
        request.META[ENVIRON_KEY] = tally.describe()
        return response
