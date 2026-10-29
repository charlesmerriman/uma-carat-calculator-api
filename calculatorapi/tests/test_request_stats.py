"""The access line's database share: calculatorapi/middleware.py and the format
in gunicorn.conf.py that prints it."""

import re
import runpy

from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory
from gunicorn.glogging import SafeAtoms

from calculatorapi.middleware import ENVIRON_KEY, RequestStatsMiddleware
from .base import CalculatorTestCase


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
