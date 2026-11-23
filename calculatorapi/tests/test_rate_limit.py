"""The per-visitor request limit (calculatorapi/rate_limit.py,
notes/rate-limit-plan.md).

Every request here goes to a path with no route: the middleware runs before URL
resolution, so it sees these exactly like real ones, and a 404 costs no database
work. The limits are shrunk to 5 a minute and 8 in ten minutes so a test needs
a handful of requests, and the module's clock is replaced so a test can walk
through a window or a block without waiting.
"""

import logging
from unittest import mock

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from calculatorapi import rate_limit
from calculatorapi.middleware import route_label
from calculatorapi.tests.base import CalculatorTestCase

PATH = "/no-such-route-for-rate-limit-tests"

# 1,800,000,000 is a multiple of 600, so every test starts at the beginning of
# a minute window AND a ten-minute window.
T0 = 1_800_000_000

LIMITS = {
    "RATE_LIMIT_ACCOUNT_MODE": "enforce",
    "RATE_LIMIT_ADDRESS_MODE": "enforce",
    "RATE_LIMIT_WINDOWS": ((60, 5), (600, 8)),
    "RATE_LIMIT_BLOCK_SECONDS": 600,
    "RATE_LIMIT_BUILD_KEY": "",
}


@override_settings(**LIMITS)
class RateLimitTestCase(CalculatorTestCase):
    """Shared setup: a clock the test moves, and a fresh summary."""

    def setUp(self):
        self.now = T0
        clock = mock.patch.object(rate_limit, "_now", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        # Keep the block lines out of the test output. assertLogs still sees
        # them: it attaches its own handler to the logger it watches.
        quiet = mock.patch.object(logging.getLogger("calculatorapi"), "handlers", [])
        quiet.start()
        self.addCleanup(quiet.stop)
        summary = mock.patch.object(rate_limit, "SUMMARY", rate_limit.Summary())
        summary.start()
        self.addCleanup(summary.stop)

    def hit(self, token=None, address=None, **headers):
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Token {token}"
        if address:
            headers["HTTP_DO_CONNECTING_IP"] = address
        return self.client.get(PATH, **headers)

    def hits(self, count, **kwargs):
        return [self.hit(**kwargs).status_code for _ in range(count)]


class AccountLimitTests(RateLimitTestCase):
    def test_under_the_limit_every_request_goes_through(self):
        self.assertEqual(self.hits(5, token="abc"), [404] * 5)

    def test_the_request_past_the_minute_limit_is_refused(self):
        self.hits(5, token="abc")
        response = self.hit(token="abc")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "600")
        self.assertEqual(response.json(), {"detail": rate_limit.REFUSAL_TEXT})

    def test_the_ten_minute_rule_catches_a_steady_pace_under_the_minute_rule(self):
        self.assertEqual(self.hits(5, token="abc"), [404] * 5)
        self.now += 60
        # 3 more make 8 in the ten minutes, the limit; the 9th is refused.
        self.assertEqual(self.hits(4, token="abc"), [404, 404, 404, 429])

    def test_a_block_lasts_its_time_then_clears(self):
        self.hits(6, token="abc")
        self.now += 300
        response = self.hit(token="abc")
        self.assertEqual((response.status_code, response["Retry-After"]), (429, "300"))
        self.now = T0 + 599
        self.assertEqual(self.hit(token="abc").status_code, 429)
        self.now = T0 + 600
        self.assertEqual(self.hit(token="abc").status_code, 404)

    def test_requests_while_blocked_neither_count_nor_extend_the_block(self):
        self.hits(6, token="abc")
        for offset in range(10, 600, 10):
            self.now = T0 + offset
            self.hit(token="abc")
        self.now = T0 + 600
        # A fresh start: the full five again, then refused.
        self.assertEqual(self.hits(6, token="abc"), [404] * 5 + [429])

    def test_accounts_are_counted_apart(self):
        self.hits(6, token="abc")
        self.assertEqual(self.hit(token="xyz").status_code, 404)

    def test_a_blocked_account_is_refused_from_any_address(self):
        statuses = [self.hit(token="abc", address=f"203.0.113.{n}").status_code for n in range(1, 7)]
        self.assertEqual(statuses, [404] * 5 + [429])
        self.assertEqual(self.hit(token="abc", address="203.0.113.99").status_code, 429)
        self.assertEqual(self.hit(token="xyz", address="203.0.113.1").status_code, 404)


class AddressLimitTests(RateLimitTestCase):
    def test_addresses_are_counted_apart(self):
        self.assertEqual(self.hits(6, address="203.0.113.7"), [404] * 5 + [429])
        self.assertEqual(self.hit(address="203.0.113.8").status_code, 404)

    def test_a_blocked_address_refuses_every_account_on_it(self):
        self.hits(6, address="203.0.113.7")
        self.assertEqual(self.hit(token="abc", address="203.0.113.7").status_code, 429)

    def test_a_refused_request_is_not_counted_on_its_address(self):
        # Block an account by rotating addresses, so no address trips.
        for n in range(1, 7):
            self.hit(token="abc", address=f"198.51.100.{n}")
        # Ten refused requests from one address must not use up its budget,
        # or one blocked account would run the counter up for everyone on it.
        self.assertEqual(self.hits(10, token="abc", address="203.0.113.7"), [429] * 10)
        self.assertEqual(self.hits(6, address="203.0.113.7"), [404] * 5 + [429])

    def test_an_ipv6_visitor_is_counted_by_their_64(self):
        self.hits(3, address="2001:db8:1:2::1")
        statuses = self.hits(3, address="2001:db8:1:2:ffff:ffff:ffff:9")
        self.assertEqual(statuses, [404, 404, 429])
        self.assertEqual(self.hit(address="2001:db8:1:3::1").status_code, 404)

    def test_an_ipv4_address_written_as_ipv6_is_the_same_visitor(self):
        self.hits(3, address="::ffff:203.0.113.7")
        self.assertEqual(self.hits(3, address="203.0.113.7"), [404, 404, 429])

    def test_a_list_counts_its_last_entry(self):
        self.hits(3, address="198.51.100.1, 203.0.113.7")
        self.hits(2, address="192.0.2.50,203.0.113.7")
        self.assertEqual(self.hit(address="203.0.113.7").status_code, 429)

    def test_no_address_header_is_never_address_limited(self):
        # DO's health checks and local dev have no header. Falling back to
        # REMOTE_ADDR would put everyone behind DO's proxy on one counter.
        self.assertEqual(self.hits(20), [404] * 20)

    def test_a_value_that_is_not_an_address_is_ignored(self):
        self.assertEqual(self.hits(20, address="unknown"), [404] * 20)


class ModeTests(RateLimitTestCase):
    @override_settings(RATE_LIMIT_ACCOUNT_MODE="off", RATE_LIMIT_ADDRESS_MODE="off")
    def test_off_counts_nothing(self):
        self.assertEqual(self.hits(20, token="abc", address="203.0.113.7"), [404] * 20)

    @override_settings(RATE_LIMIT_ACCOUNT_MODE="off", RATE_LIMIT_ADDRESS_MODE="observe")
    def test_observe_logs_once_and_never_refuses(self):
        with self.assertLogs("calculatorapi.rate_limit", level="WARNING") as logs:
            statuses = self.hits(20, address="203.0.113.7")
        self.assertEqual(statuses, [404] * 20)
        trips = [line for line in logs.output if "would block an address" in line]
        self.assertEqual(len(trips), 1)

    @override_settings(RATE_LIMIT_ACCOUNT_MODE="enforce", RATE_LIMIT_ADDRESS_MODE="observe")
    def test_an_observed_address_does_not_stop_an_enforced_account(self):
        # The address would block after five, but only observes, so the
        # account's own count decides: refused on the sixth.
        self.assertEqual(self.hits(6, token="abc", address="203.0.113.7"), [404] * 5 + [429])
        # Another account on that address goes through.
        self.assertEqual(self.hit(token="xyz", address="203.0.113.7").status_code, 404)

    @override_settings(RATE_LIMIT_ADDRESS_MODE="enfroce")
    def test_a_mistyped_mode_stops_the_middleware_loading(self):
        # Django loads middleware at boot, so a typo in the live spec fails the
        # deploy and App Platform keeps the running one.
        with self.assertRaises(ImproperlyConfigured):
            rate_limit.RateLimitMiddleware(lambda request: None)


class BuildKeyTests(RateLimitTestCase):
    @override_settings(RATE_LIMIT_BUILD_KEY="build-secret")
    def test_the_build_key_skips_the_limit(self):
        statuses = self.hits(20, address="203.0.113.7", HTTP_X_BUILD_KEY="build-secret")
        self.assertEqual(statuses, [404] * 20)

    @override_settings(RATE_LIMIT_BUILD_KEY="build-secret")
    def test_a_wrong_build_key_is_counted(self):
        statuses = self.hits(6, address="203.0.113.7", HTTP_X_BUILD_KEY="guess")
        self.assertEqual(statuses, [404] * 5 + [429])

    def test_with_no_key_configured_an_empty_header_is_not_a_pass(self):
        statuses = self.hits(6, address="203.0.113.7", HTTP_X_BUILD_KEY="")
        self.assertEqual(statuses, [404] * 5 + [429])


class RefusalTests(RateLimitTestCase):
    def test_a_refusal_carries_cors_headers(self):
        # `npm run dev:live` calls the API cross-origin; without the header the
        # browser reports a CORS failure instead of the 429.
        origin = settings.CORS_ALLOWED_ORIGINS[0]
        self.hits(5, token="abc")
        response = self.hit(token="abc", HTTP_ORIGIN=origin)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Access-Control-Allow-Origin"], origin)

    def test_a_refused_request_gets_its_own_route_label(self):
        self.hits(5, token="abc")
        response = self.hit(token="abc")
        self.assertEqual(route_label(response.wsgi_request), "GET (rate limited)")

    def test_the_block_line_names_no_address_and_no_token(self):
        with self.assertLogs("calculatorapi.rate_limit", level="WARNING") as logs:
            self.hits(
                6, token="secret-token-value", address="203.0.113.7",
                HTTP_REFERER="https://umacaratcalculator.com/app/selectors?plan=9",
            )
        text = "\n".join(logs.output)
        self.assertIn("blocked an account", text)
        self.assertIn("from page /app/selectors", text)
        for private in ("203.0.113.7", "secret-token-value", "plan=9"):
            self.assertNotIn(private, text)

    def test_the_counters_stay_out_of_the_default_cache(self):
        # In "default" they would push the /calculator-data payload out.
        self.hits(3, token="abc", address="203.0.113.7")
        self.assertEqual(len(caches["default"]._cache), 0)  # pylint: disable=protected-access
        self.assertGreater(len(caches[rate_limit.CACHE_ALIAS]._cache), 0)  # pylint: disable=protected-access


class SummaryTests(RateLimitTestCase):
    def test_a_summary_line_every_ten_minutes(self):
        self.hits(3, token="abc", address="203.0.113.7", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
        self.hit(address="198.51.100.4")
        self.hits(6, address="192.0.2.9")
        self.now = T0 + 600
        with self.assertLogs("calculatorapi.rate_limit", level="WARNING") as logs:
            self.hit()
        (line,) = [line for line in logs.output if "rate limit summary" in line]
        self.assertIn("10 requests", line)
        self.assertIn("1 accounts, 3 addresses", line)
        self.assertIn("address 6 in 60s", line)
        self.assertIn("blocks: 0 account, 1 address", line)
        self.assertIn("refused 1", line)
        self.assertIn("do-connecting-ip on 10, matching x-forwarded-for's first entry on 3", line)

    def test_the_first_request_writes_no_line(self):
        # A fresh process has no finished period to report.
        with self.assertNoLogs("calculatorapi.rate_limit", level="WARNING"):
            self.hit(address="203.0.113.7")


class PlacementTests(CalculatorTestCase):
    def test_it_sits_after_cors_and_static_files_and_before_authentication(self):
        order = settings.MIDDLEWARE
        here = order.index("calculatorapi.rate_limit.RateLimitMiddleware")
        self.assertLess(order.index("corsheaders.middleware.CorsMiddleware"), here)
        self.assertLess(order.index("whitenoise.middleware.WhiteNoiseMiddleware"), here)
        self.assertLess(order.index("calculatorapi.middleware.RequestStatsMiddleware"), here)
        self.assertLess(here, order.index("django.contrib.auth.middleware.AuthenticationMiddleware"))

    def test_a_test_run_starts_with_the_limit_off(self):
        self.assertEqual(settings.RATE_LIMIT_ACCOUNT_MODE, "off")
        self.assertEqual(settings.RATE_LIMIT_ADDRESS_MODE, "off")
