"""The admin analytics report, and the visit counting it is built from."""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from calculatorapi.analytics import build_analytics_report
from calculatorapi.visits import (
    VISITOR_HASH_RETENTION_DAYS,
    build_visit_report,
    record_visit,
)
from calculatorapi.models import (
    CustomUser,
    ClubRank,
    Plan,
    UserPlannedBanner,
    DailyVisit, MonthlyVisit, VisitorHash,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    make_user,
    make_timeline,
    make_uma_banner,
    make_support_banner,
)


class AnalyticsReportEmptyTests(CalculatorTestCase):
    """build_analytics_report() must survive a completely empty database."""

    def test_empty_db_returns_zeroes_without_errors(self):
        report = build_analytics_report()
        self.assertEqual(report['total_users'], 0)
        self.assertEqual(report['engaged_users'], 0)
        self.assertEqual(report['engaged_pct'], 0.0)
        for product in report['paid_products']:
            self.assertEqual(product['count'], 0)
            self.assertEqual(product['pct_of_total'], 0.0)
            self.assertEqual(product['pct_of_engaged'], 0.0)
        for resource in report['resource_averages']:
            self.assertEqual(resource['avg'], 0)
            self.assertEqual(resource['median'], 0)
            self.assertEqual(resource['excluded'], 0)
        self.assertEqual(report['popular_uma_banners'], [])
        self.assertEqual(report['popular_support_banners'], [])


class AnalyticsReportScenarioTests(CalculatorTestCase):
    """One seeded user base, asserted against every report section.

    The scenario:
      - whale:   both paid flags, Club Rank A, 3000 carats, plans Uma X (10)
                 and Support Y (5)
      - dolphin: training pass only, Club Rank B, 1000 carats, plans Uma X (20)
      - planner: default stats, engaged ONLY through planning Uma Z (5)
      - lurker:  registered but never touched anything (not engaged)
      - staff:   admin account with paid flags and a 100-pull plan — must be
                 invisible to every metric
    """

    @classmethod
    def setUpTestData(cls):
        # income_amount drives display order: B (100) must sort before A (200)
        club_b = ClubRank.objects.create(name='B', income_amount=100)
        club_a = ClubRank.objects.create(name='A', income_amount=200)

        cls.whale = CustomUser.objects.create_user(
            username='whale', password='x',
            daily_carat=True, training_pass=True,
            club_rank=club_a, current_carat=3000,
        )
        cls.dolphin = CustomUser.objects.create_user(
            username='dolphin', password='x',
            training_pass=True, club_rank=club_b, current_carat=1000,
        )
        cls.planner = CustomUser.objects.create_user(username='planner', password='x')
        cls.lurker = CustomUser.objects.create_user(username='lurker', password='x')
        cls.staff = CustomUser.objects.create_user(
            username='staff', password='x', is_staff=True,
            daily_carat=True, training_pass=True,
        )

        timeline = make_timeline()
        uma_x = make_uma_banner(timeline, name='Uma X')
        uma_z = make_uma_banner(timeline, name='Uma Z')
        support_y = make_support_banner(timeline, name='Support Y')

        UserPlannedBanner.objects.create(user=cls.whale, banner_uma=uma_x, number_of_pulls=10)
        UserPlannedBanner.objects.create(user=cls.whale, banner_support=support_y, number_of_pulls=5)
        UserPlannedBanner.objects.create(user=cls.dolphin, banner_uma=uma_x, number_of_pulls=20)
        UserPlannedBanner.objects.create(user=cls.planner, banner_uma=uma_z, number_of_pulls=5)
        UserPlannedBanner.objects.create(user=cls.staff, banner_uma=uma_z, number_of_pulls=100)

        # whale also keeps a spare what-if plan: far more pulls on Uma X, and a
        # banner (Uma Z) their real plan does not include at all. Only the
        # ACTIVE plan is what someone intends, so none of this may reach a
        # figure -- every assertion below was written before plans existed and
        # must hold unchanged.
        spare = Plan.objects.create(user=cls.whale, name='What if', is_active=False)
        UserPlannedBanner.objects.create(
            user=cls.whale, plan=spare, banner_uma=uma_x, number_of_pulls=500)
        UserPlannedBanner.objects.create(
            user=cls.whale, plan=spare, banner_uma=uma_z, number_of_pulls=50)

        cls.report = build_analytics_report()

    def test_user_counts_exclude_staff(self):
        self.assertEqual(self.report['total_users'], 4)

    def test_engaged_counts_flag_rank_and_banner_users(self):
        # whale + dolphin (stats) + planner (banner only); lurker excluded
        self.assertEqual(self.report['engaged_users'], 3)
        self.assertEqual(self.report['engaged_pct'], 75.0)

    def test_paid_product_percentages(self):
        daily, training = self.report['paid_products'][0], self.report['paid_products'][1]
        self.assertEqual(daily['label'], 'Daily Carat Pack')
        self.assertEqual(daily['count'], 1)          # whale only (staff ignored)
        self.assertEqual(daily['pct_of_total'], 25.0)
        self.assertEqual(daily['pct_of_engaged'], 33.3)
        self.assertEqual(training['label'], 'Training Pass')
        self.assertEqual(training['count'], 2)       # whale + dolphin
        self.assertEqual(training['pct_of_total'], 50.0)
        self.assertEqual(training['pct_of_engaged'], 66.7)

    def test_club_rank_distribution_ordered_by_income_with_not_set(self):
        club = next(d for d in self.report['rank_distributions']
                    if d['label'] == 'Club Rank')
        names = [row['name'] for row in club['rows']]
        self.assertEqual(names, ['B', 'A', 'Not set'])  # income order, not alphabetical
        counts = {row['name']: row['count'] for row in club['rows']}
        self.assertEqual(counts, {'B': 1, 'A': 1, 'Not set': 2})

    def test_unused_rank_type_reports_everyone_not_set(self):
        team_trials = next(d for d in self.report['rank_distributions']
                           if d['label'] == 'Team Trials')
        self.assertEqual(team_trials['rows'],
                         [{'name': 'Not set', 'count': 4, 'pct_of_total': 100.0}])

    def test_resource_averages_use_engaged_denominator(self):
        carats = next(r for r in self.report['resource_averages']
                      if r['label'] == 'Carats')
        # (3000 + 1000 + 0) / 3 engaged users — lurker's zeroes not averaged in
        self.assertEqual(carats['avg'], 1333.3)
        # planner's 0 is a real answer, so the middle of [0, 1000, 3000] is 1000
        self.assertEqual(carats['median'], 1000)
        self.assertEqual(carats['excluded'], 0)

    def test_uma_banner_popularity_ranked_and_staff_free(self):
        top, second = self.report['popular_uma_banners']
        self.assertEqual(top['name'], 'Uma X')
        self.assertEqual(top['planners'], 2)
        self.assertEqual(top['total_pulls'], 30)
        self.assertEqual(top['avg_pulls'], 15.0)
        # staff's 100-pull plan on Uma Z must not appear anywhere
        self.assertEqual(second['name'], 'Uma Z')
        self.assertEqual(second['planners'], 1)
        self.assertEqual(second['total_pulls'], 5)

    def test_an_inactive_plan_adds_no_planner_and_no_pulls(self):
        """whale's spare plan holds Uma X x500 and Uma Z x50 (see setUpTestData)."""
        by_name = {row['name']: row for row in self.report['popular_uma_banners']}
        # Uma X: still whale 10 + dolphin 20, not 530.
        self.assertEqual(by_name['Uma X']['total_pulls'], 30)
        # Uma Z: still planner alone. whale's what-if is not a second planner.
        self.assertEqual(by_name['Uma Z']['planners'], 1)
        self.assertEqual(by_name['Uma Z']['total_pulls'], 5)

    def test_support_banner_popularity(self):
        (only,) = self.report['popular_support_banners']
        self.assertEqual(only['name'], 'Support Y')
        self.assertEqual(only['planners'], 1)
        self.assertEqual(only['total_pulls'], 5)


class AnalyticsOutlierTests(CalculatorTestCase):
    """Implausible stored values must not reach any figure that is a quantity.

    Reproduces the shape seen in production: one account holding 999,999,999
    (what the client sanitiser yields for any input of nine or more digits)
    alongside a normal user base. The API accepts those values on purpose and
    nothing here rewrites them — they are only excluded from the aggregates.
    """

    @classmethod
    def setUpTestData(cls):
        cls.normal_a = CustomUser.objects.create_user(
            username='normal_a', password='x', current_carat=1000,
        )
        cls.normal_b = CustomUser.objects.create_user(
            username='normal_b', password='x', current_carat=3000,
        )
        # Absurd carats, but a plausible ticket count: the filter is per FIELD,
        # so the tickets must still count.
        cls.outlier = CustomUser.objects.create_user(
            username='outlier', password='x',
            current_carat=999_999_999, uma_ticket=50,
        )

        timeline = make_timeline()
        cls.banner = make_uma_banner(timeline, name='Uma X')
        cls.solo = make_uma_banner(timeline, name='Outlier Only')

        UserPlannedBanner.objects.create(
            user=cls.normal_a, banner_uma=cls.banner, number_of_pulls=100)
        UserPlannedBanner.objects.create(
            user=cls.normal_b, banner_uma=cls.banner, number_of_pulls=200)
        UserPlannedBanner.objects.create(
            user=cls.outlier, banner_uma=cls.banner, number_of_pulls=999_999_999)
        # A banner whose ONLY row is the absurd one — both aggregates come back
        # NULL from the database and must not crash or render as None.
        UserPlannedBanner.objects.create(
            user=cls.outlier, banner_uma=cls.solo, number_of_pulls=999_999_999)

        cls.report = build_analytics_report()

    def _resource(self, label):
        return next(r for r in self.report['resource_averages']
                    if r['label'] == label)

    def _banner(self, name):
        return next(b for b in self.report['popular_uma_banners']
                    if b['name'] == name)

    def test_absurd_resource_is_dropped_from_mean_and_counted(self):
        carats = self._resource('Carats')
        # (1000 + 3000) / 2 — the billion is out, and the two real answers are in
        self.assertEqual(carats['avg'], 2000.0)
        self.assertEqual(carats['median'], 2000)
        self.assertEqual(carats['excluded'], 1)

    def test_filtering_is_per_field_not_per_user(self):
        # The outlier's carats are excluded; their plausible tickets are not.
        tickets = self._resource('Uma Tickets')
        self.assertEqual(tickets['excluded'], 0)
        self.assertEqual(tickets['avg'], round(50 / 3, 1))

    def test_median_is_immune_to_a_legitimate_whale(self):
        # No filtering involved: 9,000,000 is under SANE_MAX_RESOURCE and is a
        # real (if extreme) answer. The mean moves a long way, the median does
        # not — which is the whole reason the column exists.
        CustomUser.objects.create_user(
            username='whale', password='x', current_carat=9_000_000)
        carats = next(r for r in build_analytics_report()['resource_averages']
                      if r['label'] == 'Carats')
        self.assertEqual(carats['excluded'], 1)
        self.assertEqual(carats['avg'], round(9_004_000 / 3, 1))
        self.assertEqual(carats['median'], 3000)

    def test_banner_pull_figures_exclude_the_absurd_row(self):
        banner = self._banner('Uma X')
        self.assertEqual(banner['total_pulls'], 300)
        self.assertEqual(banner['avg_pulls'], 150.0)
        self.assertEqual(banner['excluded'], 1)

    def test_planner_count_still_includes_the_outlier(self):
        # They really do have the banner planned; only their NUMBER is junk.
        self.assertEqual(self._banner('Uma X')['planners'], 3)

    def test_banner_with_only_absurd_rows_reports_zero_not_none(self):
        solo = self._banner('Outlier Only')
        self.assertEqual(solo['planners'], 1)
        self.assertEqual(solo['total_pulls'], 0)
        self.assertEqual(solo['avg_pulls'], 0)
        self.assertEqual(solo['excluded'], 1)

    def test_all_null_banner_sorts_below_one_with_real_figures(self):
        # Postgres sorts NULLs first in a plain DESC; the aggregate asks for
        # nulls_last so the banner we have no figures for cannot lead the table.
        names = [b['name'] for b in self.report['popular_uma_banners']]
        self.assertLess(names.index('Uma X'), names.index('Outlier Only'))

    def test_stored_values_are_never_rewritten(self):
        # The report is read-only: this page filters, it does not clean up.
        self.outlier.refresh_from_db()
        self.assertEqual(self.outlier.current_carat, 999_999_999)
        self.assertEqual(
            UserPlannedBanner.objects
            .filter(user=self.outlier, banner_uma=self.banner)
            .first().number_of_pulls,
            999_999_999,
        )


# Rendering admin templates resolves {% static %} tags; the production
# whitenoise manifest storage requires collectstatic, which never runs in
# tests. Any test class that renders admin pages swaps in plain storage.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class AnalyticsDashboardViewTests(CalculatorTestCase):
    """Access control and response formats for /admin/analytics/."""

    def setUp(self):
        self.url = reverse('admin-analytics')

    def _staff_client(self):
        staff = CustomUser.objects.create_user(
            username='staffer', password='x', is_staff=True)
        self.client.force_login(staff)

    def test_anonymous_redirected_to_admin_login(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/admin/login/', res.url)

    def test_non_staff_user_redirected_not_served(self):
        self.client.force_login(make_user('regular'))
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/admin/login/', res.url)

    def test_staff_user_gets_dashboard(self):
        self._staff_client()
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Daily Carat Pack')
        self.assertContains(res, 'Download CSV')

    def test_csv_download(self):
        self._staff_client()
        res = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv')
        self.assertIn('attachment; filename="analytics-', res['Content-Disposition'])
        body = res.content.decode()
        self.assertIn('Paid Products', body)
        self.assertIn('Popular Uma Banners', body)

    def test_csv_survives_a_planned_banner_with_no_confirmed_dates(self):
        """Regression: Download CSV used to 500 on any predicted banner.

        _banner_popularity() reports the CONFIRMED global dates, which are null
        until a banner is announced, so this fired as soon as one person planned
        anything in the future — the normal case, not an edge case.
        """
        timeline = make_timeline()
        timeline.global_start_date = None
        timeline.global_end_date = None
        timeline.save()
        UserPlannedBanner.objects.create(
            user=make_user('planner'),
            banner_uma=make_uma_banner(timeline, name='Unannounced'),
            number_of_pulls=10,
        )

        self._staff_client()
        res = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(res.status_code, 200)
        self.assertIn('Unannounced', res.content.decode())

    def _seed_traffic(self):
        DailyVisit.objects.create(
            date=timezone.localdate(), page_views=7, unique_visitors=3)
        MonthlyVisit.objects.create(
            month=timezone.localdate().replace(day=1),
            page_views=7, unique_visitors=2)

    def test_dashboard_includes_traffic_sections(self):
        self._seed_traffic()
        self._staff_client()
        res = self.client.get(self.url)
        self.assertContains(res, 'Site traffic')
        self.assertContains(res, 'by month')

    def test_csv_includes_traffic_sections(self):
        self._seed_traffic()
        self._staff_client()
        body = self.client.get(self.url, {'format': 'csv'}).content.decode()
        self.assertIn('Site Traffic', body)
        # The monthly column is a true monthly-active count and is therefore
        # SMALLER than the sum of the daily uniques. The qualifier in the header
        # is what stops a reader treating that gap as a bug.
        self.assertIn('Unique visitors (counted once per month)', body)


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
