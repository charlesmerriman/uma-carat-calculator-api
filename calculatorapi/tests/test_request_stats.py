"""The access line's database share: calculatorapi/middleware.py and the format
in gunicorn.conf.py that prints it."""

import re
import runpy
from unittest import mock

from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from gunicorn.glogging import SafeAtoms

from calculatorapi import middleware
from calculatorapi.middleware import (
    ENVIRON_KEY,
    NO_ROUTE,
    WINDOW_MINUTES,
    RequestStatsMiddleware,
    RouteStats,
    route_label,
)
from .base import CalculatorTestCase

BOT_KEY = "bot-test-key"


def _view_running(queries):
    """A get_response that runs `queries` trivial queries, like a view would."""

    def get_response(_request):
        with connection.cursor() as cursor:
            for _ in range(queries):
                cursor.execute("SELECT 1")
        return HttpResponse("ok")

    return get_response


class RequestStatsMiddlewareTests(CalculatorTestCase):

    def test_the_tally_counts_the_queries_the_request_ran(self):
        request = RequestFactory().get("/anything")
        RequestStatsMiddleware(_view_running(3))(request)
        self.assertRegex(request.META[ENVIRON_KEY], r"^db=3q/\d+ms$")

    def test_a_request_with_no_queries_says_so(self):
        request = RequestFactory().get("/anything")
        RequestStatsMiddleware(_view_running(0))(request)
        self.assertEqual(request.META[ENVIRON_KEY], "db=0q/0ms")

    def test_the_wrapper_is_gone_once_the_request_is_done(self):
        # A wrapper left behind would keep counting into a finished request's
        # tally, and pile up one per request on the thread's connection.
        before = list(connection.execute_wrappers)
        RequestStatsMiddleware(_view_running(1))(RequestFactory().get("/anything"))
        self.assertEqual(connection.execute_wrappers, before)

    def test_a_real_route_leaves_its_tally_on_the_request(self):
        # Through the whole stack: the middleware is installed and first.
        self.assertEqual(settings.MIDDLEWARE[0], "calculatorapi.middleware.RequestStatsMiddleware")
        response = self.client.get("/calculator-data")
        self.assertEqual(response.status_code, 200)
        match = re.fullmatch(r"db=(\d+)q/\d+ms", response.wsgi_request.META[ENVIRON_KEY])
        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 0)


class AccessLogFormatTests(CalculatorTestCase):
    """gunicorn.conf.py isn't importable as a module (the dot in its name), so it
    is read the way gunicorn reads it, and the line rendered the way gunicorn
    renders it (SafeAtoms prints "-" for a missing key)."""

    def setUp(self):
        super().setUp()
        config = runpy.run_path(str(settings.BASE_DIR / "gunicorn.conf.py"))
        self.fmt = config["access_log_format"]
        self.atoms = {
            "h": "10.0.0.1", "l": "-", "u": "-", "t": "[29/Oct/2026:15:36:41 +0000]",
            "r": "PATCH /calculator-data HTTP/1.1", "s": "200", "b": "29",
            "f": "https://umacaratcalculator.com/app", "a": "Mozilla/5.0", "M": 412,
        }

    def test_the_database_share_sits_just_before_the_time(self):
        line = self.fmt % SafeAtoms({**self.atoms, "{calc_db}e": "db=14q/35ms"})
        self.assertTrue(line.endswith('"Mozilla/5.0" db=14q/35ms 412ms'), line)

    def test_a_request_django_never_saw_prints_a_dash(self):
        line = self.fmt % SafeAtoms(self.atoms)
        self.assertTrue(line.endswith('"Mozilla/5.0" - 412ms'), line)


class FakeClock:  # pylint: disable=too-few-public-methods
    """time.time for RouteStats, moved by hand."""

    def __init__(self, at=1_800_000_000.0):
        self.at = at

    def __call__(self):
        return self.at


class RouteStatsTests(CalculatorTestCase):

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.stats = RouteStats(clock=self.clock)

    def request(self, route, ms, db_ms=0.0, queries=0):
        self.stats.started()
        self.stats.finished(route, ms, db_ms, queries)

    def test_totals_by_route_busiest_first(self):
        self.request("GET calculator-data", 100, 10, 2)
        self.request("PATCH calculator-data", 900, 300, 17)
        self.request("GET calculator-data", 300, 30, 2)
        snapshot = self.stats.busiest(5)
        self.assertEqual(snapshot["requests"], 3)
        self.assertEqual(snapshot["in_flight"], 0)
        self.assertEqual(
            snapshot["routes"],
            [
                {"route": "PATCH calculator-data", "count": 1, "total_ms": 900, "max_ms": 900, "db_ms": 300,
                 "queries": 17},
                {"route": "GET calculator-data", "count": 2, "total_ms": 400, "max_ms": 300, "db_ms": 40,
                 "queries": 4},
            ],
        )

    def test_the_window_asked_for_and_nothing_older(self):
        self.request("GET changelog", 50)
        self.clock.at += 4 * 60
        self.request("GET changelog", 70)
        # The last 5 minutes hold both; the last 3, only the newer one.
        self.assertEqual(self.stats.busiest(5)["requests"], 2)
        self.assertEqual(self.stats.busiest(3)["requests"], 1)

    def test_minutes_past_the_window_are_forgotten(self):
        self.request("GET changelog", 50)
        self.clock.at += WINDOW_MINUTES * 60
        self.request("GET site-content", 5)
        self.assertEqual(self.stats.busiest(WINDOW_MINUTES)["routes"][0]["route"], "GET site-content")
        self.assertEqual(len(self.stats._minutes), 1)  # pylint: disable=protected-access

    def test_requests_in_progress(self):
        self.stats.started()
        self.stats.started()
        self.stats.finished("GET site-content", 5, 0, 0)
        self.assertEqual(self.stats.busiest(5)["in_flight"], 1)

    def test_the_list_is_capped(self):
        for n in range(12):
            self.request(f"GET route-{n}", n)
        self.assertEqual(len(self.stats.busiest(5, limit=8)["routes"]), 8)


class RouteLabelTests(CalculatorTestCase):

    def test_a_route_is_named_by_its_pattern_never_its_path(self):
        response = self.client.get("/plan/AbCdEfGh")
        self.assertEqual(route_label(response.wsgi_request), "GET plan/<str:public_id>")

    def test_a_path_with_no_route(self):
        request = RequestFactory().get("/static/nothing.css")
        self.assertEqual(route_label(request), f"GET {NO_ROUTE}")


@override_settings(BOT_API_SECRET=BOT_KEY)
class BotHealthTests(CalculatorTestCase):

    def setUp(self):
        super().setUp()
        # Fresh counters: the process-wide ones also hold every other test's
        # requests from the last few minutes, which can push this test's
        # routes out of the top eight (it flaked that way under --parallel).
        patcher = mock.patch.object(middleware, "ROUTE_STATS", RouteStats())
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, query="", key=BOT_KEY):
        headers = {"HTTP_X_BOT_KEY": key} if key is not None else {}
        return self.client.get(f"/bot/health{query}", **headers)

    def test_locked_like_every_bot_route(self):
        self.assertEqual(self.get(key=None).status_code, 403)
        self.assertEqual(self.get(key="wrong").status_code, 403)
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.get().status_code, 404)

    def test_the_busiest_routes_and_the_cache(self):
        self.client.get("/site-content")
        self.client.get("/site-content")
        body = self.get().json()
        self.assertEqual(body["minutes"], 5)
        # Only the health request itself is in progress, and it doesn't count.
        self.assertEqual(body["in_flight"], 0)
        self.assertIn("payload_cached", body)
        site_content = next(row for row in body["routes"] if row["route"] == "GET site-content")
        self.assertEqual(site_content["count"], 2)
        self.assertEqual(
            set(site_content), {"route", "count", "total_ms", "max_ms", "db_ms", "queries"}
        )

    def test_the_window_is_clamped_and_checked(self):
        self.assertEqual(self.get("?minutes=99").json()["minutes"], WINDOW_MINUTES)
        self.assertEqual(self.get("?minutes=0").json()["minutes"], 1)
        self.assertEqual(self.get("?minutes=five").status_code, 400)
