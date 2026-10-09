"""Site traffic counting (calculatorapi/visits.py): recording a visit, the
daily and monthly rollups, the POST /visit beacon, and pruning the hashes.

The analytics figures derived from these counters are tested in
test_analytics_sections.py (AnalyticsTrafficDerivedTests).
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from calculatorapi.models import DailyVisit, MonthlyVisit, VisitorHash
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.visits import (
    VISITOR_HASH_RETENTION_DAYS,
    build_visit_report,
    record_visit,
)


class VisitRecordingTests(CalculatorTestCase):
    """record_visit()'s counting, deduplication and bot filtering.

    Every test pins the calendar date rather than using the real one: the
    monthly behaviour is the interesting part, and a suite that happened to run
    on the 31st would otherwise straddle a month boundary and fail at random.
    """

    # Mid-month, so ±1 day never crosses a boundary by accident.
    DAY = datetime.date(2026, 3, 15)

    def setUp(self):
        self.factory = RequestFactory()

    def _hit(self, ip='203.0.113.5', agent='Mozilla/5.0', forwarded=None, day=None):
        """One beacon request on `day`. `forwarded` sets X-Forwarded-For."""
        headers = {'REMOTE_ADDR': ip, 'HTTP_USER_AGENT': agent}
        if forwarded is not None:
            headers['HTTP_X_FORWARDED_FOR'] = forwarded
        request = self.factory.post('/visit', **headers)
        with patch('calculatorapi.visits.timezone.localdate',
                   return_value=day or self.DAY):
            return record_visit(request)

    def _daily(self, day=None):
        row = DailyVisit.objects.get(date=day or self.DAY)
        return row.page_views, row.unique_visitors

    def _monthly(self, month=None):
        row = MonthlyVisit.objects.get(month=month or self.DAY.replace(day=1))
        return row.page_views, row.unique_visitors

    def test_first_visit_creates_both_counter_rows(self):
        self.assertTrue(self._hit())
        self.assertEqual(self._daily(), (1, 1))
        self.assertEqual(self._monthly(), (1, 1))

    def test_repeat_visitor_adds_a_view_but_not_a_visitor(self):
        self._hit()
        self._hit()
        self._hit()
        self.assertEqual(self._daily(), (3, 1))
        self.assertEqual(self._monthly(), (3, 1))
        # One day, one hash: the unique constraint is doing the deduplication.
        self.assertEqual(VisitorHash.objects.count(), 1)

    def test_different_ip_counts_as_a_new_visitor(self):
        self._hit(ip='203.0.113.5')
        self._hit(ip='198.51.100.9')
        self.assertEqual(self._daily(), (2, 2))
        self.assertEqual(self._monthly(), (2, 2))

    def test_different_user_agent_counts_as_a_new_visitor(self):
        self._hit(agent='Mozilla/5.0')
        self._hit(agent='Mozilla/5.0 (different device)')
        self.assertEqual(self._daily(), (2, 2))

    def test_forwarded_for_wins_over_remote_addr(self):
        """Without this the load balancer's IP is all we ever see in production.

        Both hits arrive from the same REMOTE_ADDR — as they would behind App
        Platform's proxy — but carry different client addresses. If X-Forwarded-For
        were ignored they would collapse into one visitor.
        """
        self._hit(ip='10.0.0.1', forwarded='203.0.113.5')
        self._hit(ip='10.0.0.1', forwarded='198.51.100.9')
        self.assertEqual(self._daily(), (2, 2))

    def test_forwarded_for_uses_the_first_entry(self):
        """"client, proxy1, proxy2" — the client is the leftmost entry."""
        self._hit(ip='10.0.0.1', forwarded='203.0.113.5, 10.0.0.2, 10.0.0.3')
        self._hit(ip='10.0.0.1', forwarded='203.0.113.5, 10.9.9.9')
        self.assertEqual(self._daily(), (2, 1))

    def test_bots_are_not_counted_at_all(self):
        for agent in ['Googlebot/2.1', 'python-urllib/3.11', 'curl/8.0',
                      'HeadlessChrome/120', 'Some Crawler']:
            self.assertFalse(self._hit(agent=agent), agent)
        self.assertFalse(DailyVisit.objects.exists())
        self.assertFalse(MonthlyVisit.objects.exists())

    def test_no_identifying_data_is_stored(self):
        """The privacy contract, asserted rather than assumed."""
        self._hit(ip='203.0.113.5', agent='Mozilla/5.0 (SecretDevice)')
        stored = VisitorHash.objects.get()
        self.assertNotIn('203.0.113.5', stored.visitor_hash)
        self.assertNotIn('SecretDevice', stored.visitor_hash)
        self.assertEqual(len(stored.visitor_hash), 32)

    # ── The monthly-unique semantics ─────────────────────────────────────────

    def test_returning_on_another_day_counts_once_for_the_month(self):
        """The whole point of a month-scoped hash: a real monthly-active count.

        Two days, one person. Each day sees a unique visitor; the month sees one.
        """
        self._hit(day=self.DAY)
        self._hit(day=self.DAY + datetime.timedelta(days=1))

        self.assertEqual(self._daily(self.DAY), (1, 1))
        self.assertEqual(self._daily(self.DAY + datetime.timedelta(days=1)), (1, 1))
        # 2 page views, but ONE visitor — not the sum of the daily uniques.
        self.assertEqual(self._monthly(), (2, 1))

    def test_the_same_visitor_is_new_again_next_month(self):
        """The link breaks at the boundary, which is the privacy property."""
        self._hit(day=datetime.date(2026, 3, 31))
        self._hit(day=datetime.date(2026, 4, 1))

        self.assertEqual(self._monthly(datetime.date(2026, 3, 1)), (1, 1))
        self.assertEqual(self._monthly(datetime.date(2026, 4, 1)), (1, 1))

    def test_hash_is_stable_within_a_month_and_changes_across_months(self):
        self._hit(day=datetime.date(2026, 3, 2))
        self._hit(day=datetime.date(2026, 3, 28))
        march = set(VisitorHash.objects.values_list('visitor_hash', flat=True))
        self.assertEqual(len(march), 1, 'same visitor, same month, same hash')

        self._hit(day=datetime.date(2026, 4, 2))
        everything = set(VisitorHash.objects.values_list('visitor_hash', flat=True))
        self.assertEqual(len(everything), 2, 'new month, unrelated hash')

    def test_two_visitors_across_overlapping_days(self):
        """A mixed month: A on two days, B on one. Three views, two people."""
        day_two = self.DAY + datetime.timedelta(days=1)
        self._hit(ip='203.0.113.5', day=self.DAY)
        self._hit(ip='198.51.100.9', day=self.DAY)
        self._hit(ip='203.0.113.5', day=day_two)

        self.assertEqual(self._daily(self.DAY), (2, 2))
        self.assertEqual(self._daily(day_two), (1, 1))
        # Sum of daily uniques would say 3; the honest answer is 2.
        self.assertEqual(self._monthly(), (3, 2))


class VisitReportTests(CalculatorTestCase):
    """build_visit_report()'s windowing and monthly figures."""

    def test_empty_db_reports_no_traffic(self):
        report = build_visit_report()
        self.assertEqual(report['daily'], [])
        self.assertEqual(report['monthly'], [])

    def test_daily_window_excludes_older_rows(self):
        today = timezone.localdate()
        DailyVisit.objects.create(date=today, page_views=5, unique_visitors=2)
        DailyVisit.objects.create(
            date=today - datetime.timedelta(days=40), page_views=99, unique_visitors=50)

        daily = build_visit_report(days=30)['daily']
        self.assertEqual([row['date'] for row in daily], [today])

    def test_monthly_rows_come_from_the_monthly_counters(self):
        MonthlyVisit.objects.create(
            month=datetime.date(2026, 3, 1), page_views=16, unique_visitors=5)
        MonthlyVisit.objects.create(
            month=datetime.date(2026, 4, 1), page_views=1, unique_visitors=1)

        by_month = {
            row['month'].strftime('%Y-%m'): row
            for row in build_visit_report()['monthly']
        }
        self.assertEqual(by_month['2026-03']['page_views'], 16)
        self.assertEqual(by_month['2026-03']['unique_visitors'], 5)
        self.assertEqual(by_month['2026-04']['page_views'], 1)

    def test_monthly_window_is_limited(self):
        for month in range(1, 13):
            MonthlyVisit.objects.create(
                month=datetime.date(2025, month, 1), page_views=1, unique_visitors=1)

        self.assertEqual(len(build_visit_report(months=6)['monthly']), 6)


class VisitBeaconEndpointTests(CalculatorTestCase):
    """POST /visit — the public write-only beacon."""

    def setUp(self):
        self.url = reverse('site-visit')

    def test_anonymous_post_is_accepted_and_counted(self):
        res = self.client.post(self.url, HTTP_USER_AGENT='Mozilla/5.0')
        self.assertEqual(res.status_code, 204)
        self.assertEqual(res.content, b'')
        self.assertEqual(DailyVisit.objects.get().page_views, 1)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_bot_gets_the_same_204_but_is_not_counted(self):
        """The response must not reveal that the bot filter fired."""
        res = self.client.post(self.url, HTTP_USER_AGENT='Googlebot/2.1')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(DailyVisit.objects.exists())

    def test_repeated_hits_are_eventually_throttled(self):
        # 60/hour, so the 61st is refused. Guards the one thing standing
        # between an open counter and anyone who wants to run it up.
        for _ in range(60):
            self.client.post(self.url, HTTP_USER_AGENT='Mozilla/5.0')
        res = self.client.post(self.url, HTTP_USER_AGENT='Mozilla/5.0')
        self.assertEqual(res.status_code, 429)


class PruneVisitorHashesCommandTests(CalculatorTestCase):
    """The housekeeping command must never touch the permanent counters."""

    def setUp(self):
        self.today = timezone.localdate()
        self.old_date = self.today - datetime.timedelta(days=120)
        DailyVisit.objects.create(
            date=self.old_date, page_views=50, unique_visitors=20)
        MonthlyVisit.objects.create(
            month=self.old_date.replace(day=1), page_views=50, unique_visitors=12)
        VisitorHash.objects.create(date=self.old_date, visitor_hash='a' * 32)
        VisitorHash.objects.create(date=self.today, visitor_hash='b' * 32)

    def test_prunes_old_hashes_but_keeps_the_counters(self):
        call_command('prune_visitor_hashes', stdout=StringIO())
        self.assertEqual(
            list(VisitorHash.objects.values_list('date', flat=True)),
            [self.today],
        )
        # The whole point: the historical numbers survive their scratch data.
        self.assertEqual(DailyVisit.objects.get(date=self.old_date).page_views, 50)
        self.assertEqual(
            MonthlyVisit.objects.get(month=self.old_date.replace(day=1)).unique_visitors,
            12,
        )

    def test_default_retention_cannot_break_a_month_in_progress(self):
        """Guards the invariant the docstring warns about.

        The monthly check asks "any row for this hash since the 1st?", so the
        window has to outlast a month by a clear margin — otherwise a visitor
        whose earlier rows were pruned mid-month gets counted twice.
        """
        self.assertGreaterEqual(VISITOR_HASH_RETENTION_DAYS, 45)

    def test_dry_run_changes_nothing(self):
        call_command('prune_visitor_hashes', '--dry-run', stdout=StringIO())
        self.assertEqual(VisitorHash.objects.count(), 2)

    def test_exits_cleanly_when_there_is_nothing_to_prune(self):
        out = StringIO()
        call_command('prune_visitor_hashes', '--days', '3650', stdout=out)
        self.assertIn('Nothing to prune', out.getvalue())
        self.assertEqual(VisitorHash.objects.count(), 2)
