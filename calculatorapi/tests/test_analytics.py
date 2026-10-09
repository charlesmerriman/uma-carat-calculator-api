"""The admin analytics report: its cache, table shape, snapshots, page and CSV.

Per-section figures are in test_analytics_sections.py; the visit counting the
traffic sections read is in test_visits.py.
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from calculatorapi.admin_dashboard import dashboard_callback
from calculatorapi.analytics import build_analytics_report, get_report, report_tables
from calculatorapi.analytics.common import REPORT_SHAPE
from calculatorapi.analytics.snapshots import NOT_STORED, ensure_today
from calculatorapi.analytics.tables import KINDS, csv_cell, page_cell
from calculatorapi.models import (
    AnalyticsSnapshot,
    AnniversaryEventProduct,
    CustomUser,
    ClubRank,
    IncomeProfile,
    Plan,
    Uma,
    UserPlannedBanner,
    UserPlannedPurchase,
    DailyVisit, MonthlyVisit,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    make_anniversary_event,
    make_step_up_banner,
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
        for setting in report['income_settings']:
            self.assertEqual(setting['users_on'], 0)
            self.assertEqual(setting['changed'], 0)
            self.assertEqual(setting['pct_on'], 0.0)
        self.assertEqual(report['shop_tickets'], [])
        self.assertEqual(report['step_up_popularity'], [])
        self.assertEqual(report['demand_calendar'], [])
        for resource in report['resource_averages']:
            self.assertEqual(resource['avg'], 0)
            self.assertEqual(resource['median'], 0)
            self.assertEqual(resource['excluded'], 0)
        self.assertEqual(report['selector_purchases'], [])
        self.assertEqual(report['any_selector']['count'], 0)
        self.assertEqual(report['popular_uma_banners'], [])
        self.assertEqual(report['popular_support_banners'], [])
        self.assertIsNone(report['comparison']['since'])
        self.assertEqual(report['history'], [])


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
        daily, training = self.report['income_settings'][0], self.report['income_settings'][1]
        self.assertEqual(daily['label'], 'Daily Carat Pack')
        self.assertEqual(daily['users_on'], 1)       # whale only (staff ignored)
        self.assertEqual(daily['changed'], 1)        # starts off, so on = changed
        self.assertEqual(daily['pct_on'], 33.3)      # of the three engaged
        self.assertEqual(training['label'], 'Training Pass')
        self.assertEqual(training['users_on'], 2)    # whale + dolphin
        self.assertEqual(training['pct_on'], 66.7)

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


class AnalyticsSelectorPurchaseTests(CalculatorTestCase):
    """The campaign selector table under Paid products.

    The scenario:
      - picker:  plans the uma selector with a card picked, and the support
                 selector without one
      - twice:   plans the uma selector on the account AND on an income
                 profile, neither picked -- one person, not two
      - packer:  plans only the carat pack -- not a selector buyer
      - lurker:  nothing at all
      - staff:   plans the uma selector -- invisible
    """

    @classmethod
    def setUpTestData(cls):
        make_anniversary_event(name='1st Anniversary', products=[
            {'name': 'Carat Pack', 'product_type': 'carat_pack'},
            {'name': 'Uma Selector', 'product_type': 'uma_selector', 'order': 1},
            {'name': 'Support Selector', 'product_type': 'support_selector', 'order': 2},
            {'name': 'Unwanted Selector', 'product_type': 'uma_selector', 'order': 3},
        ])
        products = {p.name: p for p in AnniversaryEventProduct.objects.all()}
        uma = Uma.objects.create(name='Picked Uma')

        picker = CustomUser.objects.create_user(username='picker', password='x')
        twice = CustomUser.objects.create_user(username='twice', password='x')
        packer = CustomUser.objects.create_user(username='packer', password='x')
        CustomUser.objects.create_user(username='lurker', password='x')
        staff = CustomUser.objects.create_user(
            username='staff', password='x', is_staff=True)

        UserPlannedPurchase.objects.create(
            user=picker, product=products['Uma Selector'], target_uma=uma)
        UserPlannedPurchase.objects.create(
            user=picker, product=products['Support Selector'])
        UserPlannedPurchase.objects.create(
            user=twice, product=products['Uma Selector'])
        UserPlannedPurchase.objects.create(
            user=twice, product=products['Uma Selector'],
            income_profile=IncomeProfile.objects.create(user=twice))
        UserPlannedPurchase.objects.create(
            user=packer, product=products['Carat Pack'])
        UserPlannedPurchase.objects.create(
            user=staff, product=products['Uma Selector'], target_uma=uma)

        cls.report = build_analytics_report()
        cls.rows = {row['label']: row for row in cls.report['selector_purchases']}

    def test_lists_selectors_only_in_product_order(self):
        self.assertEqual(
            [row['label'] for row in self.report['selector_purchases']],
            ['Uma Selector', 'Support Selector', 'Unwanted Selector'],
        )
        self.assertEqual(self.rows['Uma Selector']['campaign'], '1st Anniversary')

    def test_buyers_are_distinct_people_and_staff_free(self):
        # picker + twice; twice's two rows are one person, staff ignored
        uma = self.rows['Uma Selector']
        self.assertEqual(uma['count'], 2)
        self.assertEqual(uma['pct_of_total'], 50.0)
        # engaged = picker, twice, packer (a planned purchase counts)
        self.assertEqual(self.report['engaged_users'], 3)
        self.assertEqual(uma['pct_of_engaged'], 66.7)

    def test_picked_counts_only_buyers_with_a_card_chosen(self):
        self.assertEqual(self.rows['Uma Selector']['picked'], 1)
        self.assertEqual(self.rows['Support Selector']['count'], 1)
        self.assertEqual(self.rows['Support Selector']['picked'], 0)

    def test_a_selector_nobody_plans_is_listed_at_zero(self):
        self.assertEqual(self.rows['Unwanted Selector']['count'], 0)

    def test_any_selector_counts_each_person_once(self):
        # picker buys two selectors but is one person; packer is not a buyer
        self.assertEqual(self.report['any_selector']['count'], 2)
        self.assertEqual(self.report['any_selector']['pct_of_total'], 50.0)


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


class AnalyticsReportCacheTests(CalculatorTestCase):
    """get_report(): built at most once per five minutes, shared by every reader."""

    def test_a_second_read_is_served_from_the_cache(self):
        get_report()
        with self.assertNumQueries(0):
            get_report()

    def test_the_cached_copy_does_not_see_new_rows(self):
        # Staleness is the price of the cache. The TTL bounds it and the page
        # prints when the report was built, so it is visible, not silent.
        make_user('first')
        self.assertEqual(get_report()['total_users'], 1)
        make_user('second')
        self.assertEqual(get_report()['total_users'], 1)

    def test_refresh_rebuilds_and_replaces_the_cached_copy(self):
        make_user('first')
        get_report()
        make_user('second')
        self.assertEqual(get_report(refresh=True)['total_users'], 2)
        self.assertEqual(get_report()['total_users'], 2)

    def test_the_admin_index_cards_read_the_cached_report(self):
        """The KPI cards run on every /admin/ load, so they must not rebuild."""
        get_report()
        with self.assertNumQueries(0):
            context = dashboard_callback(None, {})
        self.assertEqual(context['kpi_cards'][0]['title'], 'Total users')


class AnalyticsTablesTests(CalculatorTestCase):
    """The one table shape the page and the CSV both render (analytics/tables.py).

    The seed gives every section at least one row, so the structure check has
    something to check in each.
    """

    @classmethod
    def setUpTestData(cls):
        club = ClubRank.objects.create(name='A', income_amount=100)
        user = CustomUser.objects.create_user(
            username='user', password='x', club_rank=club, current_carat=10)
        timeline = make_timeline()
        UserPlannedBanner.objects.create(
            user=user, banner_uma=make_uma_banner(timeline), number_of_pulls=5)
        UserPlannedBanner.objects.create(
            user=user, banner_support=make_support_banner(timeline),
            number_of_pulls=5)
        make_anniversary_event(products=[
            {'name': 'Uma Selector', 'product_type': 'uma_selector'},
        ])
        UserPlannedPurchase.objects.create(
            user=user, product=AnniversaryEventProduct.objects.get())
        UserPlannedBanner.objects.create(
            user=user, banner_step_up=make_step_up_banner(), number_of_pulls=5)
        DailyVisit.objects.create(
            date=timezone.localdate(), page_views=1, unique_visitors=1)
        MonthlyVisit.objects.create(
            month=timezone.localdate().replace(day=1),
            page_views=1, unique_visitors=1)
        cls.tables = report_tables(build_analytics_report())

    def test_every_row_fills_every_column(self):
        """A section cannot ship with a column its rows do not fill."""
        for section in self.tables:
            with self.subTest(section=section['key']):
                self.assertTrue(section['rows'], 'the seed should give this section a row')
                keys = {column.key for column in section['columns']}
                rows = section['rows'] + [section['footer']] * bool(section['footer'])
                for row in rows:
                    self.assertLessEqual(keys, row.keys())

    def test_every_column_has_a_kind_the_renderers_know(self):
        for section in self.tables:
            for column in section['columns']:
                self.assertIn(column.kind, KINDS, (section['key'], column.label))

    def test_section_keys_are_unique(self):
        # They are the page's anchor ids.
        keys = [section['key'] for section in self.tables]
        self.assertEqual(len(keys), len(set(keys)))

    def test_kinds_format_for_each_renderer(self):
        day = datetime.date(2026, 10, 9)
        self.assertEqual(page_cell(12.5, 'pct'), '12.5%')
        self.assertEqual(csv_cell(12.5, 'pct'), 12.5)
        self.assertEqual(page_cell(day, 'date'), '2026-10-09')
        self.assertEqual(csv_cell(day, 'month'), '2026-10')
        # A predicted banner has no confirmed date: blank, never a crash.
        self.assertEqual(page_cell(None, 'date'), '')
        self.assertEqual(csv_cell(None, 'date'), '')
        # Nothing ignored reads as a dash on the page, a number in the CSV.
        self.assertEqual(page_cell(0, 'ignored'), '–')
        self.assertEqual(csv_cell(0, 'ignored'), 0)
        self.assertEqual(page_cell(3, 'ignored'), '3')


def _store_snapshot(day, **report):
    """A stored snapshot holding only the keys a test cares about, which is
    also what a row from an older, smaller report shape looks like."""
    return AnalyticsSnapshot.objects.create(date=day, shape='test', report=report)


def _days_ago(days):
    return timezone.localdate() - datetime.timedelta(days=days)


class AnalyticsSnapshotTests(CalculatorTestCase):
    """snapshots.ensure_today(): one stored copy of the report per day."""

    def test_the_first_rebuild_of_a_day_keeps_one_snapshot(self):
        get_report()
        get_report(refresh=True)
        self.assertEqual(AnalyticsSnapshot.objects.count(), 1)
        self.assertEqual(AnalyticsSnapshot.objects.get().date, timezone.localdate())

    def test_a_race_past_the_exists_check_still_leaves_one_row(self):
        report = build_analytics_report()
        self.assertTrue(ensure_today(report))
        # Both requests saw "missing"; the unique date lets only one insert win.
        with patch('django.db.models.query.QuerySet.exists', return_value=False):
            self.assertFalse(ensure_today(report))
        self.assertEqual(AnalyticsSnapshot.objects.count(), 1)

    def test_dated_by_the_day_the_report_was_built(self):
        report = build_analytics_report()
        report['generated_at'] -= datetime.timedelta(days=1)
        ensure_today(report)
        self.assertEqual(AnalyticsSnapshot.objects.get().date, _days_ago(1))

    def test_stores_the_figures_but_not_traffic_or_what_is_built_from_snapshots(self):
        make_user('someone')
        ensure_today(build_analytics_report())
        row = AnalyticsSnapshot.objects.get()
        self.assertEqual(row.shape, REPORT_SHAPE)
        self.assertEqual(row.report['total_users'], 1)
        self.assertFalse(NOT_STORED & row.report.keys())


class AnalyticsComparisonTests(CalculatorTestCase):
    """The Overview's "30 days ago": the nearest snapshot on or before then."""

    def test_no_snapshot_old_enough_leaves_every_comparison_blank(self):
        _store_snapshot(_days_ago(10), total_users=0)
        comparison = build_analytics_report()['comparison']
        self.assertIsNone(comparison['since'])
        for row in comparison['figures'].values():
            self.assertIsNone(row['then'])
            self.assertIsNone(row['delta'])

    def test_reads_the_nearest_snapshot_on_or_before_30_days_back(self):
        _store_snapshot(_days_ago(45), total_users=1)
        _store_snapshot(_days_ago(10), total_users=5)   # too recent
        for name in ('a', 'b', 'c'):
            make_user(name)
        comparison = build_analytics_report()['comparison']
        self.assertEqual(comparison['since'], _days_ago(45))
        total = comparison['figures']['total_users']
        self.assertEqual((total['now'], total['then'], total['delta']), (3, 1, 2))

    def test_a_snapshot_from_before_income_settings_still_reads_daily_carat(self):
        _store_snapshot(_days_ago(40), paid_products=[
            {'label': 'Daily Carat Pack', 'count': 4}])
        figures = build_analytics_report()['comparison']['figures']
        self.assertEqual(figures['daily_carat']['then'], 4)

    def test_a_figure_an_older_shape_lacks_reads_blank_not_an_error(self):
        _store_snapshot(_days_ago(40), total_users=2)   # no paid_products, no any_selector
        figures = build_analytics_report()['comparison']['figures']
        self.assertEqual(figures['total_users']['delta'], -2)
        self.assertIsNone(figures['daily_carat']['then'])
        self.assertIsNone(figures['any_selector']['delta'])


class AnalyticsHistoryTests(CalculatorTestCase):
    """One row per month from its first snapshot, beside that month's visitors."""

    @classmethod
    def setUpTestData(cls):
        this_month = timezone.localdate().replace(day=1)
        cls.last_month = (this_month - datetime.timedelta(days=1)).replace(day=1)
        cls.two_months_ago = (cls.last_month - datetime.timedelta(days=1)).replace(day=1)
        _store_snapshot(cls.last_month.replace(day=3), total_users=4)
        _store_snapshot(cls.last_month.replace(day=20), total_users=9)
        MonthlyVisit.objects.create(
            month=cls.last_month, page_views=50, unique_visitors=30)
        # Traffic only: a month from before snapshots began.
        MonthlyVisit.objects.create(
            month=cls.two_months_ago, page_views=20, unique_visitors=12)
        cls.history = build_analytics_report()['history']

    def test_a_month_reads_its_first_snapshot(self):
        row = self.history[0]
        self.assertEqual(row['month'], self.last_month)
        self.assertEqual(row['total_users'], 4)
        self.assertEqual(row['unique_visitors'], 30)

    def test_a_month_with_only_traffic_has_blank_account_figures(self):
        row = self.history[1]
        self.assertEqual(row['month'], self.two_months_ago)
        self.assertIsNone(row['total_users'])
        self.assertEqual(row['unique_visitors'], 12)

    def test_a_month_with_neither_is_left_out(self):
        # This month has no snapshot (build_analytics_report never writes one)
        # and no traffic.
        self.assertEqual(len(self.history), 2)


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

    def test_ended_banners_fold_away_on_the_page_but_stay_in_the_csv(self):
        now = timezone.now()
        ended = make_timeline(global_start_date=now - datetime.timedelta(days=40),
                              global_end_date=now - datetime.timedelta(days=10))
        UserPlannedBanner.objects.create(
            user=make_user('planner'), banner_uma=make_uma_banner(ended, name='Gone'),
            number_of_pulls=10)
        self._staff_client()
        page = self.client.get(self.url).content.decode()
        self.assertIn('<summary>Ended (1)</summary>', page)
        self.assertIn('No upcoming or running Uma banners are planned.', page)
        body = self.client.get(self.url, {'format': 'csv'}).content.decode()
        self.assertIn('Gone,', body)
        self.assertIn(',ended,', body)

    def test_staff_user_gets_dashboard(self):
        self._staff_client()
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Daily Carat Pack')
        self.assertContains(res, 'Download CSV')
        self.assertContains(res, 'Refresh now')

    def test_the_csv_is_the_cached_report_the_page_showed(self):
        self._staff_client()
        make_user('first')
        self.client.get(self.url)
        make_user('second')
        body = self.client.get(self.url, {'format': 'csv'}).content.decode()
        # Value 1, the then-cached count; no snapshot is 30 days old, so the
        # comparison cells are blank.
        self.assertIn('Total users (non-staff),1,,\r\n', body)

    def test_a_staff_visit_keeps_one_snapshot_a_day(self):
        self._staff_client()
        self.client.get(self.url)
        self.client.get(self.url)
        self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(AnalyticsSnapshot.objects.count(), 1)

    def test_page_and_csv_show_the_comparison_and_history(self):
        _store_snapshot(_days_ago(35), total_users=0)
        make_user('newcomer')
        self._staff_client()
        page = self.client.get(self.url)
        self.assertContains(page, '30 days ago')
        self.assertContains(page, '<td>+1</td>', html=True)
        self.assertContains(page, 'id="history"')
        body = self.client.get(self.url, {'format': 'csv'}).content.decode()
        self.assertIn('Total users (non-staff),1,0,1\r\n', body)
        self.assertIn('History', body)

    def test_the_admin_index_card_says_how_much_users_grew(self):
        since = _days_ago(35)
        _store_snapshot(since, total_users=0)
        make_user('newcomer')
        cards = dashboard_callback(None, {})['kpi_cards']
        self.assertEqual(cards[0]['footer'], f'+1 since {since:%Y-%m-%d}')

    def test_refresh_rebuilds_then_redirects_to_the_plain_url(self):
        self._staff_client()
        make_user('first')
        self.client.get(self.url)
        make_user('second')
        res = self.client.get(self.url, {'refresh': '1'})
        self.assertRedirects(res, self.url, fetch_redirect_response=False)
        self.assertEqual(get_report()['total_users'], 2)

    def test_csv_download(self):
        self._staff_client()
        res = self.client.get(self.url, {'format': 'csv'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv')
        self.assertIn('attachment; filename="analytics-', res['Content-Disposition'])
        body = res.content.decode()
        self.assertIn('Income settings', body)
        self.assertIn('Campaign selectors', body)
        self.assertIn('Popular Uma banners', body)

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
        self.assertIn('Site traffic', body)
        # The monthly column is a true monthly-active count and is therefore
        # SMALLER than the sum of the daily uniques. The qualifier in the header
        # is what stops a reader treating that gap as a bug.
        self.assertIn('Unique visitors (counted once per month)', body)


class SnapshotAnalyticsCommandTests(CalculatorTestCase):
    """manage.py snapshot_analytics: the deploy chain's daily point."""

    def _run(self):
        out = StringIO()
        call_command('snapshot_analytics', stdout=out)
        return out.getvalue()

    def test_keeps_todays_snapshot_then_does_nothing(self):
        self.assertIn('Kept the analytics snapshot', self._run())
        self.assertIn('already exists', self._run())
        self.assertEqual(AnalyticsSnapshot.objects.count(), 1)

    def test_a_failing_report_never_fails_the_deploy(self):
        with patch('calculatorapi.management.commands.snapshot_analytics'
                   '.build_analytics_report', side_effect=RuntimeError('boom')):
            output = self._run()   # returns: no exception, so exit status 0
        self.assertIn('Snapshot skipped', output)
        self.assertFalse(AnalyticsSnapshot.objects.exists())
