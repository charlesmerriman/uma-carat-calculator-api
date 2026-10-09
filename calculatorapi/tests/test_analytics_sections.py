"""The admin analytics report, section by section: who counts as engaged, the
income settings, banner dates and status, step-ups, the demand calendar and the
derived traffic figures.

The report's machinery (cache, table shape, snapshots, page, CSV) is tested in
test_analytics.py.
"""

import datetime

from django.utils import timezone

from calculatorapi.admin_dashboard import dashboard_callback
from calculatorapi.analytics import build_analytics_report
from calculatorapi.analytics.common import SANE_MAX_STEPS
from calculatorapi.analytics.income_settings import INCOME_TOGGLES
from calculatorapi.models import (
    ClubRank,
    CustomUser,
    DailyVisit,
    IncomeProfile,
    MonthlyVisit,
    Plan,
    SupportCard,
    Uma,
    UserOshi,
    UserPlannedBanner,
    UserStepUpSelection,
)
from calculatorapi.models.game_stats import GameStats
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_step_up_banner,
    make_support_banner,
    make_timeline,
    make_uma_banner,
    make_user,
)


class AnalyticsEngagedTests(CalculatorTestCase):
    """Every feature a section counts makes its users engaged (people.engaged_q)."""

    def _engaged(self):
        return build_analytics_report()['engaged_users']

    def test_a_new_account_is_not_engaged(self):
        CustomUser.objects.create_user(username='lurker', password='x')
        self.assertEqual(self._engaged(), 0)

    def test_each_feature_on_its_own_makes_a_user_engaged(self):
        cases = {
            'oshi': lambda user: UserOshi.objects.create(
                user=user, uma=Uma.objects.create(name='Fave'), position=0),
            'income profile': lambda user: IncomeProfile.objects.create(user=user),
            'second plan': lambda user: Plan.objects.create(
                user=user, name='What if', is_active=False),
            'display name': lambda user: CustomUser.objects.filter(pk=user.pk)
                .update(display_name='Trainer'),
            'toggle that starts on, off': lambda user: CustomUser.objects
                .filter(pk=user.pk).update(misc_earnings=False),
            'shop count': lambda user: CustomUser.objects.filter(pk=user.pk)
                .update(shop_uma_tickets_bought=2),
            'selector balance': lambda user: CustomUser.objects.filter(pk=user.pk)
                .update(uma_selector_ticket=1),
        }
        for name, act in cases.items():
            with self.subTest(name):
                CustomUser.objects.all().delete()
                act(CustomUser.objects.create_user(username='u', password='x'))
                self.assertEqual(self._engaged(), 1)

    def test_a_user_with_many_rows_counts_once(self):
        user = CustomUser.objects.create_user(username='busy', password='x')
        timeline = make_timeline()
        for index in range(3):
            UserPlannedBanner.objects.create(
                user=user, banner_uma=make_uma_banner(timeline, name=f'U{index}'),
                number_of_pulls=10)
        Plan.objects.create(user=user, name='Spare', is_active=False)
        IncomeProfile.objects.create(user=user)
        self.assertEqual(self._engaged(), 1)


class AnalyticsIncomeSettingsTests(CalculatorTestCase):
    """Income settings: every toggle, among engaged users, with changes from default."""

    def test_every_boolean_on_game_stats_is_listed(self):
        booleans = {field.name for field in GameStats._meta.get_fields()
                    if field.get_internal_type() == 'BooleanField'}
        self.assertEqual(booleans, {field for field, _ in INCOME_TOGGLES})

    def test_a_toggle_that_starts_on_counts_who_switched_it_off(self):
        CustomUser.objects.create_user(username='lurker', password='x')
        CustomUser.objects.create_user(username='off', password='x', misc_earnings=False)
        CustomUser.objects.create_user(username='on', password='x', current_carat=5)
        rows = {row['key']: row for row in build_analytics_report()['income_settings']}
        misc = rows['misc_earnings']
        # Two engaged; the lurker is left out, so no share passes 100.
        self.assertEqual(misc['default'], 'On')
        self.assertEqual((misc['users_on'], misc['changed']), (1, 1))
        self.assertEqual((misc['pct_on'], misc['pct_changed']), (50.0, 50.0))

    def test_shop_tickets_list_the_default_first_then_each_count(self):
        CustomUser.objects.create_user(username='a', password='x', current_carat=1)
        CustomUser.objects.create_user(
            username='b', password='x', shop_uma_tickets_bought=2,
            shop_support_tickets_bought=9)
        rows = build_analytics_report()['shop_tickets']
        self.assertEqual([row['bought'] for row in rows],
                         ['Not set (the default)', '2', '9'])
        self.assertEqual((rows[0]['uma'], rows[0]['support']), (1, 1))
        self.assertEqual((rows[1]['uma'], rows[2]['support']), (1, 1))

    def test_resource_quartiles_match_the_worked_example(self):
        for index, carats in enumerate(
                [0, 0, 2000, 5000, 9000, 12000, 30000, 45000, 400000]):
            # A rank makes the people at zero engaged too.
            CustomUser.objects.create_user(
                username=f'u{index}', password='x', current_carat=carats,
                club_rank=ClubRank.objects.get_or_create(
                    name='A', income_amount=1)[0])
        carats = build_analytics_report()['resource_averages'][0]
        self.assertEqual((carats['p25'], carats['median'], carats['p75']),
                         (2000, 9000, 30000))
        self.assertEqual(carats['zero_pct'], 22.2)
        self.assertEqual(carats['avg'], 55888.9)


class AnalyticsBannerDatesTests(CalculatorTestCase):
    """Banner rows carry effective dates, predicted or not, and a status."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        day = datetime.timedelta(days=1)
        planner = make_user('planner')
        # A confirmed anchor starting tomorrow, then a JP-only banner after it,
        # which predictions resolve to a later global date.
        make_timeline(name='Anchor', jp_start_date=now - 400 * day,
                      jp_end_date=now - 390 * day,
                      global_start_date=now + day, global_end_date=now + 8 * day)
        predicted = make_timeline(name='Predicted', jp_start_date=now - 370 * day,
                                  jp_end_date=now - 360 * day)
        ended = make_timeline(name='Ended', global_start_date=now - 40 * day,
                              global_end_date=now - 10 * day)
        for name, timeline, pulls in (('Future', predicted, 50), ('Old', ended, 90)):
            UserPlannedBanner.objects.create(
                user=planner, banner_uma=make_uma_banner(timeline, name=name),
                number_of_pulls=pulls)
        cls.rows = {row['name']: row for row in build_analytics_report()['popular_uma_banners']}

    def test_a_predicted_banner_shows_its_predicted_dates(self):
        future = self.rows['Future']
        self.assertTrue(future['predicted'])
        self.assertIsNotNone(future['start_date'])
        self.assertEqual(future['status'], 'upcoming')

    def test_an_ended_banner_is_kept_and_marked(self):
        self.assertEqual(self.rows['Old']['status'], 'ended')
        self.assertFalse(self.rows['Old']['predicted'])

    def test_the_admin_index_skips_ended_banners_for_the_top_card(self):
        # Old has more pulls but has ended.
        cards = dashboard_callback(None, {})['kpi_cards']
        self.assertEqual(cards[3]['value'], 'Future')


class AnalyticsStepUpTests(CalculatorTestCase):
    """Step-up popularity: steps, never pulls; active plan only; picks counted."""

    @classmethod
    def setUpTestData(cls):
        cls.step_up = make_step_up_banner(name='SSR Step-Up')
        climber = make_user('climber')
        picker = make_user('picker')
        staff = make_user('staff', is_staff=True)
        UserPlannedBanner.objects.create(
            user=climber, banner_step_up=cls.step_up, number_of_pulls=5)
        UserPlannedBanner.objects.create(
            user=picker, banner_step_up=cls.step_up, number_of_pulls=SANE_MAX_STEPS + 1)
        spare = Plan.objects.create(user=climber, name='Spare', is_active=False)
        UserPlannedBanner.objects.create(
            user=climber, plan=spare, banner_step_up=cls.step_up, number_of_pulls=3)
        UserPlannedBanner.objects.create(
            user=staff, banner_step_up=cls.step_up, number_of_pulls=5)
        card = SupportCard.objects.create(name='An SSR', game_id=30001)
        for user in (picker, staff):
            UserStepUpSelection.objects.create(
                user=user, banner_step_up=cls.step_up, slot=1, support=card)
        cls.row = build_analytics_report()['step_up_popularity'][0]

    def test_counts_steps_from_the_active_plan_without_staff(self):
        self.assertEqual(self.row['name'], 'SSR Step-Up')
        self.assertEqual(self.row['planners'], 2)
        self.assertEqual(self.row['total_steps'], 5)
        self.assertEqual(self.row['excluded'], 1)

    def test_counts_who_chose_their_own_cards(self):
        self.assertEqual(self.row['picked'], 1)

    def test_step_ups_never_reach_the_pull_tables(self):
        report = build_analytics_report()
        self.assertEqual(report['popular_uma_banners'], [])
        self.assertEqual(report['popular_support_banners'], [])


class AnalyticsDemandCalendarTests(CalculatorTestCase):
    """Demand by the month banners end: people once, pulls summed, step-ups apart."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        day = datetime.timedelta(days=1)
        soon = make_timeline(name='Soon', global_start_date=now - day,
                             global_end_date=now + 2 * day)
        far = make_timeline(name='Far', global_start_date=now + 300 * day,
                            global_end_date=now + 310 * day)
        ended = make_timeline(name='Ended', global_start_date=now - 30 * day,
                              global_end_date=now - day)
        alice, bob = make_user('alice'), make_user('bob')
        uma = make_uma_banner(soon, name='Soon Uma')
        support = make_support_banner(soon, name='Soon Support')
        UserPlannedBanner.objects.create(user=alice, banner_uma=uma, number_of_pulls=100)
        UserPlannedBanner.objects.create(user=alice, banner_support=support, number_of_pulls=50)
        UserPlannedBanner.objects.create(user=bob, banner_uma=uma, number_of_pulls=999_999)
        UserPlannedBanner.objects.create(
            user=bob, banner_uma=make_uma_banner(far, name='Far Uma'), number_of_pulls=10)
        UserPlannedBanner.objects.create(
            user=bob, banner_uma=make_uma_banner(ended, name='Old Uma'), number_of_pulls=10)
        UserPlannedBanner.objects.create(
            user=alice, banner_step_up=make_step_up_banner(timeline=soon), number_of_pulls=5)
        cls.soon_month = (now + 2 * day).date().replace(day=1)
        cls.rows = {row['month']: row for row in build_analytics_report()['demand_calendar']}

    def test_groups_by_the_month_the_banner_ends(self):
        row = self.rows[f'{self.soon_month:%Y-%m}']
        self.assertEqual(row['banners'], 2)
        self.assertEqual(row['planners'], 2)        # alice once, despite two banners
        self.assertEqual(row['total_pulls'], 150)   # bob's absurd row left out
        self.assertEqual(row['excluded'], 1)
        self.assertEqual(row['step_up_planners'], 1)

    def test_beyond_six_months_folds_into_later(self):
        self.assertEqual(self.rows['Later']['total_pulls'], 10)

    def test_ended_banners_are_left_out(self):
        self.assertEqual(sum(row['banners'] for row in self.rows.values()), 3)


class AnalyticsTrafficDerivedTests(CalculatorTestCase):
    """Totals, visit-days per visitor and the week against the one before."""

    def test_visit_days_per_visitor_match_the_worked_example(self):
        # Ten visitors last month: all ten came on the 1st, and one of them came
        # back on each of the next nineteen days. 29 visit-days over 10 people.
        this_month = timezone.localdate().replace(day=1)
        last_month = (this_month - datetime.timedelta(days=1)).replace(day=1)
        for day in range(1, 21):
            DailyVisit.objects.create(
                date=last_month.replace(day=day), page_views=1,
                unique_visitors=10 if day == 1 else 1)
        MonthlyVisit.objects.create(month=last_month, page_views=29, unique_visitors=10)
        MonthlyVisit.objects.create(month=this_month, page_views=1, unique_visitors=1)
        rows = {row['month']: row for row in build_analytics_report()['monthly_visits']}
        self.assertEqual(rows[last_month]['days_per_visitor'], 2.9)
        self.assertFalse(rows[last_month]['partial'])
        self.assertTrue(rows[this_month]['partial'])

    def test_the_total_row_sums_the_window_as_visit_days(self):
        today = timezone.localdate()
        DailyVisit.objects.create(date=today, page_views=7, unique_visitors=3)
        DailyVisit.objects.create(
            date=today - datetime.timedelta(days=1), page_views=4, unique_visitors=2)
        totals = build_analytics_report()['daily_totals']
        self.assertEqual((totals['page_views'], totals['visit_days']), (11, 5))

    def test_this_week_against_the_one_before(self):
        today = timezone.localdate()
        DailyVisit.objects.create(date=today, page_views=30, unique_visitors=3)
        DailyVisit.objects.create(
            date=today - datetime.timedelta(days=8), page_views=20, unique_visitors=2)
        views = build_analytics_report()['traffic_weeks'][0]
        self.assertEqual((views['this_week'], views['last_week']), (30, 20))
        self.assertEqual(views['change_pct'], 50.0)

    def test_no_change_from_an_empty_week(self):
        DailyVisit.objects.create(
            date=timezone.localdate(), page_views=5, unique_visitors=1)
        self.assertIsNone(build_analytics_report()['traffic_weeks'][0]['change_pct'])
