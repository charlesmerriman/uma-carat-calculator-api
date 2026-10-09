"""The admin analytics report, section by section: who counts as engaged, the
income settings, banner dates and status, step-ups, the demand calendar, the
derived traffic figures, and the people sections (growth, activity, providers,
supporters, feature adoption, favourites).

The report's machinery (cache, table shape, snapshots, page, CSV) is tested in
test_analytics.py.
"""

import datetime
import json

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
    PatreonSupporter,
    PatreonTier,
    Plan,
    ReferrerVisit,
    SocialAccount,
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


def _joined(user, days_ago):
    """Backdate an account. date_joined has a default, not auto_now, but an
    update() keeps the test honest about what it sets."""
    CustomUser.objects.filter(pk=user.pk).update(
        date_joined=timezone.now() - datetime.timedelta(days=days_ago))


class AnalyticsGrowthTests(CalculatorTestCase):
    """New accounts per month and per week, staff left out."""

    def test_counts_by_month_and_week(self):
        _joined(make_user('today'), 0)
        _joined(make_user('earlier'), 10)
        make_user('staff', is_staff=True)
        report = build_analytics_report()
        months = report['growth_by_month']
        weeks = report['growth_by_week']
        self.assertEqual(len(months), 12)
        self.assertTrue(months[0]['partial'])
        self.assertEqual(sum(row['new_accounts'] for row in months), 2)
        self.assertEqual(len(weeks), 8)
        self.assertEqual(weeks[0]['new_accounts'], 1)   # the last seven days
        self.assertEqual(weeks[1]['new_accounts'], 1)   # 7 to 13 days ago


class AnalyticsActivityTests(CalculatorTestCase):
    """Saves and sign-ins from stored timestamps; nothing new is kept."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        day = datetime.timedelta(days=1)
        cls.fresh = make_user('fresh')          # saved 2 days ago, joined 30 days ago
        cls.lapsed = make_user('lapsed')        # saved 40 days ago, joined 45 days ago
        cls.signer = make_user('signer')        # signed in 10 days ago, joined 60 days ago
        for user, joined, saved in ((cls.fresh, 30, 2), (cls.lapsed, 45, 40),
                                    (cls.signer, 60, 59)):
            _joined(user, joined)
            plan = Plan.objects.create(user=user, name='Main', is_active=True)
            # auto_now ignores a value passed to save(); update() sets it.
            Plan.objects.filter(pk=plan.pk).update(updated_at=now - saved * day)
        SocialAccount.objects.create(
            user=cls.signer, provider=SocialAccount.PROVIDER_GOOGLE, subject_id='g1',
            last_login_at=now - 10 * day)
        cls.rows = {row['measure']: row for row in build_analytics_report()['activity']}

    def test_saves_in_each_window(self):
        self.assertEqual(self.rows['Saved in the last 7 days']['users'], 1)
        self.assertEqual(self.rows['Saved in the last 30 days']['users'], 1)

    def test_sign_ins_in_the_last_30_days(self):
        self.assertEqual(self.rows['Signed in in the last 30 days']['users'], 1)

    def test_came_back_after_the_first_week(self):
        # fresh saved 28 days after joining and signer signed in 50 days after;
        # lapsed saved only 5 days after joining.
        row = self.rows['Came back after their first week']
        self.assertEqual((row['users'], row['out_of']), (2, 3))


class AnalyticsSignInProviderTests(CalculatorTestCase):
    """People per provider, overlaps, and password accounts."""

    def test_counts_people_per_provider_and_overlaps(self):
        both = make_user('both')
        for provider, subject in ((SocialAccount.PROVIDER_GOOGLE, 'g'),
                                  (SocialAccount.PROVIDER_DISCORD, 'd')):
            SocialAccount.objects.create(user=both, provider=provider, subject_id=subject)
        make_user('password')
        rows = {row['label']: row['users']
                for row in build_analytics_report()['sign_in_providers']}
        self.assertEqual((rows['Google'], rows['Discord'], rows['Patreon']), (1, 1, 0))
        self.assertEqual(rows['Two or more'], 1)
        self.assertEqual(rows['None (a password account)'], 1)


class AnalyticsSupporterTests(CalculatorTestCase):
    """Active patrons by tier; linked leaves staff out; email never read."""

    def test_counts_by_tier_in_tier_order(self):
        top = PatreonTier.objects.create(name='Classic Class', order=0)
        junior = PatreonTier.objects.create(name='Junior Class', order=10)
        PatreonSupporter.objects.create(
            display_name='A', patreon_user_id='1', tier=junior, is_public=True,
            linked_user=make_user('linked'), email='a@example.com')
        PatreonSupporter.objects.create(
            display_name='B', patreon_user_id='2', tier=junior,
            linked_user=make_user('staff', is_staff=True))
        PatreonSupporter.objects.create(display_name='C', patreon_user_id='3', tier=top)
        PatreonSupporter.objects.create(
            display_name='D', patreon_user_id='4', tier=top, is_active=False)
        report = build_analytics_report()
        self.assertEqual(
            report['supporters'],
            [{'tier': 'Classic Class', 'active': 1, 'linked': 0, 'public': 0},
             {'tier': 'Junior Class', 'active': 2, 'linked': 1, 'public': 1}])
        self.assertEqual(report['comparison']['figures']['active_supporters']['now'], 3)
        self.assertNotIn('a@example.com', json.dumps(report, default=str))


class AnalyticsFeatureAdoptionTests(CalculatorTestCase):
    """One row per feature; row features read the active plan only."""

    def test_each_feature_counts_its_users_and_spare_plans_do_not(self):
        user = make_user('user')
        active = Plan.objects.create(user=user, name='Main', is_active=True)
        spare = Plan.objects.create(user=user, name='Spare', is_active=False)
        uma = make_uma_banner(make_timeline())
        UserPlannedBanner.objects.create(
            user=user, plan=active, banner_uma=uma, number_of_pulls=5, note='save up')
        # Only the spare plan uses two-card odds: it must not count.
        UserPlannedBanner.objects.create(
            user=user, plan=spare, banner_uma=uma, number_of_pulls=5, second_card=1)
        rows = {row['feature']: row['users']
                for row in build_analytics_report()['feature_adoption']}
        self.assertEqual(rows['More than one plan'], 1)
        self.assertEqual(rows['A note on a planned banner'], 1)
        self.assertEqual(rows['Two-card odds on a banner'], 0)
        self.assertEqual(rows['A favourite uma'], 0)


class AnalyticsFavouriteTests(CalculatorTestCase):
    """Favourite umas ranked by people, with who shows it as their picture."""

    def test_ranks_by_people_and_counts_pictures(self):
        rice, spe = Uma.objects.create(name='Rice'), Uma.objects.create(name='Spe')
        for index, (first, second) in enumerate(((rice, spe), (rice, None), (spe, rice))):
            user = make_user(f'fan{index}')
            UserOshi.objects.create(user=user, uma=first, position=0)
            if second:
                UserOshi.objects.create(user=user, uma=second, position=1)
        UserOshi.objects.create(user=make_user('staff', is_staff=True), uma=spe, position=0)
        rows = build_analytics_report()['favourite_umas']
        self.assertEqual(rows[0], {'uma': 'Rice', 'people': 3, 'as_picture': 2})
        self.assertEqual(rows[1], {'uma': 'Spe', 'people': 2, 'as_picture': 1})


class AnalyticsReferrerTests(CalculatorTestCase):
    """The referrer table names the busiest sites and folds the rest."""

    def test_the_25_busiest_are_named_and_the_rest_share_a_row(self):
        today = timezone.localdate()
        for index in range(30):
            ReferrerVisit.objects.create(date=today, host=f'site{index:02}.com',
                                         page_views=100 - index)
        ReferrerVisit.objects.create(date=today, host='other', page_views=7)
        rows = build_analytics_report()['referrers']
        self.assertEqual(len(rows), 26)
        self.assertEqual(rows[0]['name'], 'site00.com')
        # site25..site29 (75+74+73+72+71) and the "other" bucket (7).
        self.assertEqual(rows[-1]['name'], 'Everything else')
        self.assertEqual(rows[-1]['visits'], 365 + 7)
