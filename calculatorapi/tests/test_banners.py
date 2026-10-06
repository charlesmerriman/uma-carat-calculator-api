"""Banner and card content fields: the category, the Recommended flag, a card's purpose
line, and the rate-up inputs."""

import datetime
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.models import (
    Uma, SupportCard,
    BannerTimeline, BannerUma, BannerSupport,
    UmasOnUmaBanner, SupportsOnSupportBanner,
    BannerCategory, UserPlannedBanner,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import auth_client, make_user


class BannerCategoryTests(CalculatorTestCase):
    """The stored category, and the two commands that populate it."""

    def _timeline(self, name, jp_start, **kwargs):
        return BannerTimeline.objects.create(
            name=name,
            jp_start_date=timezone.make_aware(datetime.datetime(*jp_start)),
            jp_end_date=timezone.make_aware(datetime.datetime(*jp_start) +
                                            datetime.timedelta(days=10)),
            **kwargs)

    def _with_umas(self, timeline, *names):
        banner = BannerUma.objects.create(banner_timeline=timeline, name=' + '.join(names))
        for name in names:
            uma, _ = Uma.objects.get_or_create(name=name)
            UmasOnUmaBanner.objects.create(banner_uma=banner, uma=uma)
        return banner

    def _with_supports(self, timeline, *names):
        banner = BannerSupport.objects.create(banner_timeline=timeline,
                                              name=' + '.join(names))
        for name in names:
            card, _ = SupportCard.objects.get_or_create(name=name)
            SupportsOnSupportBanner.objects.create(banner_support=banner,
                                                   support_card=card)
        return banner

    def test_defaults_to_standard(self):
        timeline = self._timeline('Plain', (2024, 1, 1))
        self.assertEqual(timeline.banner_category, BannerCategory.STANDARD)

    def test_classify_sets_revival_on_many_umas_and_no_supports(self):
        revival = self._timeline('A + B + C', (2025, 4, 30))
        self._with_umas(revival, 'A', 'B', 'C')

        call_command('classify_banner_categories', '--no-input', stdout=StringIO())

        revival.refresh_from_db()
        self.assertEqual(revival.banner_category, BannerCategory.GOLDEN_WEEK_REVIVAL)

    def test_classify_leaves_a_two_uma_banner_alone(self):
        """The concurrent standard banner shares the window and must not be swept up."""
        standard = self._timeline('D + E', (2025, 4, 30))
        self._with_umas(standard, 'D', 'E')
        self._with_supports(standard, 'S1', 'S2')

        call_command('classify_banner_categories', '--no-input', stdout=StringIO())

        standard.refresh_from_db()
        self.assertEqual(standard.banner_category, BannerCategory.STANDARD)

    def test_classify_ignores_many_umas_that_also_have_supports(self):
        """Zero supports is half the rule — three umas alone must not qualify."""
        timeline = self._timeline('F + G + H', (2025, 6, 1))
        self._with_umas(timeline, 'F', 'G', 'H')
        self._with_supports(timeline, 'S3')

        call_command('classify_banner_categories', '--no-input', stdout=StringIO())

        timeline.refresh_from_db()
        self.assertEqual(timeline.banner_category, BannerCategory.STANDARD)

    def test_classify_is_idempotent(self):
        revival = self._timeline('A + B + C', (2025, 4, 30))
        self._with_umas(revival, 'A', 'B', 'C')

        call_command('classify_banner_categories', '--no-input', stdout=StringIO())
        second = StringIO()
        call_command('classify_banner_categories', '--no-input', stdout=second)

        self.assertIn('Nothing to change', second.getvalue())
        revival.refresh_from_db()
        self.assertEqual(revival.banner_category, BannerCategory.GOLDEN_WEEK_REVIVAL)

    def test_classify_dry_run_writes_nothing(self):
        revival = self._timeline('A + B + C', (2025, 4, 30))
        self._with_umas(revival, 'A', 'B', 'C')

        call_command('classify_banner_categories', '--dry-run', stdout=StringIO())

        revival.refresh_from_db()
        self.assertEqual(revival.banner_category, BannerCategory.STANDARD)

    def test_classify_reports_reruns_without_applying_them(self):
        rerun = self._timeline('Gentildonna (Rerun)', (2026, 1, 1))

        out = StringIO()
        call_command('classify_banner_categories', '--no-input', stdout=out)

        self.assertIn('Rerun candidates', out.getvalue())
        rerun.refresh_from_db()
        self.assertEqual(rerun.banner_category, BannerCategory.STANDARD)

    def test_repair_launch_banner_links_umas_parsed_from_the_name(self):
        launch = self._timeline('Special Week + Tokai Teio + Oguri Cap', (2021, 2, 24))
        for name in ['Special Week', 'Tokai Teio', 'Oguri Cap']:
            Uma.objects.create(name=name)

        call_command('repair_launch_banner', '--no-input', stdout=StringIO())

        banner = launch.uma_banners.get()
        self.assertCountEqual(
            [u.name for u in banner.umas.all()],
            ['Special Week', 'Tokai Teio', 'Oguri Cap'])

    def test_repair_launch_banner_is_idempotent(self):
        launch = self._timeline('Special Week', (2021, 2, 24))
        Uma.objects.create(name='Special Week')

        call_command('repair_launch_banner', '--no-input', stdout=StringIO())
        call_command('repair_launch_banner', '--no-input', stdout=StringIO())

        self.assertEqual(launch.uma_banners.count(), 1)

    def test_repair_launch_banner_will_not_create_missing_uma_records(self):
        launch = self._timeline('Special Week + Nonexistent Unit', (2021, 2, 24))
        Uma.objects.create(name='Special Week')

        out = StringIO()
        call_command('repair_launch_banner', '--no-input', stdout=out)

        self.assertIn('Nonexistent Unit', out.getvalue())
        self.assertFalse(Uma.objects.filter(name='Nonexistent Unit').exists())
        self.assertEqual(launch.uma_banners.get().umas.count(), 1)

    def test_category_is_serialized_to_the_timeline_payload(self):
        timeline = self._timeline('A + B + C', (2025, 4, 30),
                                  banner_category=BannerCategory.GOLDEN_WEEK_REVIVAL)
        self._with_umas(timeline, 'A', 'B', 'C')

        res = self.client.get('/calculator-data')

        self.assertEqual(res.status_code, 200)
        row = next(t for t in res.json()['banner_timeline_data']
                   if t['id'] == timeline.pk)
        self.assertEqual(row['banner_category'], 'golden_week_revival')


class BannerRecommendationTests(CalculatorTestCase):
    """The editorial "Recommended" flag on uma and support banners."""

    def setUp(self):
        start = timezone.make_aware(datetime.datetime(2025, 4, 30))
        self.timeline = BannerTimeline.objects.create(
            name='Window', jp_start_date=start,
            jp_end_date=start + datetime.timedelta(days=10))
        self.uma_banner = BannerUma.objects.create(
            banner_timeline=self.timeline, name='Uma banner')
        self.support_banner = BannerSupport.objects.create(
            banner_timeline=self.timeline, name='Support banner')

    def _payload(self):
        res = self.client.get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        return res.json()

    def test_defaults_to_not_recommended(self):
        self.assertFalse(self.uma_banner.is_recommended)
        self.assertFalse(self.support_banner.is_recommended)

    def test_reaches_the_planner_dropdown_payloads(self):
        self.uma_banner.is_recommended = True
        self.uma_banner.save()

        data = self._payload()

        uma = next(b for b in data['banner_uma_data'] if b['id'] == self.uma_banner.pk)
        support = next(b for b in data['banner_support_data']
                       if b['id'] == self.support_banner.pk)
        self.assertIs(uma['is_recommended'], True)
        # Per banner: recommending the uma side leaves its neighbour alone.
        self.assertIs(support['is_recommended'], False)

    def test_reaches_the_timeline_payload(self):
        self.support_banner.is_recommended = True
        self.support_banner.save()

        row = next(t for t in self._payload()['banner_timeline_data']
                   if t['id'] == self.timeline.pk)

        self.assertIs(row['banner_supports'][0]['is_recommended'], True)
        self.assertIs(row['banner_umas'][0]['is_recommended'], False)

    def test_a_save_is_not_hidden_by_the_public_payload_cache(self):
        """Ticking the box in the admin is a plain save(); the cached payload must drop."""
        self._payload()  # warm the cache while the flag is still off
        self.uma_banner.is_recommended = True
        self.uma_banner.save()

        uma = next(b for b in self._payload()['banner_uma_data']
                   if b['id'] == self.uma_banner.pk)
        self.assertIs(uma['is_recommended'], True)


class CardPurposeTests(CalculatorTestCase):
    """The public one-line purpose on umas and support cards."""

    def setUp(self):
        start = timezone.make_aware(datetime.datetime(2025, 4, 30))
        self.timeline = BannerTimeline.objects.create(
            name='Window', jp_start_date=start,
            jp_end_date=start + datetime.timedelta(days=10))
        self.uma = Uma.objects.create(name='Gold Ship', purpose='Great pace parent.')
        self.card = SupportCard.objects.create(
            name='Kitasan Black', purpose='Great for front runners.')
        uma_banner = BannerUma.objects.create(banner_timeline=self.timeline, name='Gold Ship')
        UmasOnUmaBanner.objects.create(banner_uma=uma_banner, uma=self.uma)
        support_banner = BannerSupport.objects.create(
            banner_timeline=self.timeline, name='Kitasan Black')
        SupportsOnSupportBanner.objects.create(
            banner_support=support_banner, support_card=self.card)

    def test_defaults_to_an_empty_string_not_null(self):
        """One representation of "no purpose", so the client checks one thing."""
        self.assertEqual(Uma.objects.create(name='Blank').purpose, '')
        self.assertEqual(SupportCard.objects.create(name='Blank').purpose, '')

    def test_is_capped_at_100_characters(self):
        self.uma.purpose = 'x' * 100
        self.uma.full_clean()  # the cap itself is allowed
        for obj in (self.uma, self.card):
            obj.purpose = 'x' * 101
            with self.assertRaises(ValidationError):
                obj.full_clean()

    def test_is_served_on_the_timeline_tiles(self):
        data = self.client.get('/calculator-data').json()

        row = next(t for t in data['banner_timeline_data'] if t['id'] == self.timeline.pk)
        self.assertEqual(row['banner_umas'][0]['umas'][0]['purpose'], 'Great pace parent.')
        self.assertEqual(row['banner_supports'][0]['support_cards'][0]['purpose'],
                         'Great for front runners.')

    def test_is_served_on_the_planner_payloads_too(self):
        """One serializer per card, so the calculator's copies carry it as well."""
        data = self.client.get('/calculator-data').json()

        uma_banner = next(b for b in data['banner_uma_data']
                          if b['banner_timeline']['id'] == self.timeline.pk)
        support_banner = next(b for b in data['banner_support_data']
                              if b['banner_timeline']['id'] == self.timeline.pk)
        self.assertEqual(uma_banner['umas'][0]['purpose'], 'Great pace parent.')
        self.assertEqual(support_banner['support_cards'][0]['purpose'],
                         'Great for front runners.')


class RateUpInputTests(CalculatorTestCase):
    """What the client's rate-up rule reads: card rarity, picks, and overrides.

    The rule itself is client-side (frontend utils/rateUpRates.ts). These pin
    the inputs reaching it, because a missing one does not error: the client
    falls back to "★3, every listed card a rate-up, no override" and shows the
    old 0.75% without complaint.
    """

    def setUp(self):
        self.client = APIClient()
        self.timeline = BannerTimeline.objects.create(
            name='Kitasan Black + Matikanetannhauser',
            jp_start_date=timezone.make_aware(datetime.datetime(2024, 1, 1)),
            jp_end_date=timezone.make_aware(datetime.datetime(2024, 1, 11)),
        )
        self.kitasan = Uma.objects.create(name='Kitasan Black', rarity=3)
        self.matikane = Uma.objects.create(name='Matikanetannhauser', rarity=2)
        self.uma_banner = BannerUma.objects.create(
            banner_timeline=self.timeline, name='Kitasan Black + Matikanetannhauser')
        UmasOnUmaBanner.objects.create(banner_uma=self.uma_banner, uma=self.kitasan)
        UmasOnUmaBanner.objects.create(
            banner_uma=self.uma_banner, uma=self.matikane, rate_override='0.005')

        self.support_banner = BannerSupport.objects.create(
            banner_timeline=self.timeline, name='Debut 10 Select 2', rate_up_picks=2)
        for card in (SupportCard.objects.create(name='Kitasan Black', rarity=3),
                     SupportCard.objects.create(name='No game id')):
            SupportsOnSupportBanner.objects.create(
                banner_support=self.support_banner, support_card=card)

    def _banners(self):
        data = self.client.get('/calculator-data').json()
        uma = next(b for b in data['banner_uma_data'] if b['id'] == self.uma_banner.pk)
        support = next(b for b in data['banner_support_data']
                       if b['id'] == self.support_banner.pk)
        return uma, support

    def test_serves_each_featured_cards_rarity(self):
        uma, support = self._banners()
        self.assertEqual({u['name']: u['rarity'] for u in uma['umas']},
                         {'Kitasan Black': 3, 'Matikanetannhauser': 2})
        # Null, not a guess: the client reads a missing rarity as SSR.
        self.assertEqual({c['name']: c['rarity'] for c in support['support_cards']},
                         {'Kitasan Black': 3, 'No game id': None})

    def test_serves_rate_up_picks_null_unless_set(self):
        uma, support = self._banners()
        self.assertIsNone(uma['rate_up_picks'])
        self.assertEqual(support['rate_up_picks'], 2)

    def test_serves_only_the_overridden_cards_as_float_rates(self):
        uma, support = self._banners()
        # JSON object keys are strings; the value is a number, not "0.005000".
        self.assertEqual(uma['rate_overrides'], {str(self.matikane.pk): 0.005})
        self.assertEqual(support['rate_overrides'], {})

    def test_planned_rows_carry_the_same_inputs(self):
        """A planned row nests the same banner serializer, so the odds agree."""
        user = make_user()
        UserPlannedBanner.objects.create(
            user=user, banner_uma=self.uma_banner, number_of_pulls=100)
        client, _ = auth_client(user)

        data = client.get('/calculator-data').json()

        banner = data['user_planned_banner_data'][0]['banner_uma']
        self.assertEqual(banner['rate_overrides'], {str(self.matikane.pk): 0.005})
        self.assertIsNone(banner['rate_up_picks'])

    def test_overrides_cost_no_query_per_banner(self):
        """`rate_overrides` reads the prefetched through rows, never its own query."""
        # Warm up first: the very first request also creates the constants
        # singleton, three one-off queries that would skew the comparison.
        self.client.get('/calculator-data')
        public_payload_cache.invalidate()
        with CaptureQueriesContext(connection) as before:
            self.client.get('/calculator-data')

        for i in range(3):
            extra = BannerUma.objects.create(banner_timeline=self.timeline, name=f'Extra {i}')
            UmasOnUmaBanner.objects.create(
                banner_uma=extra, uma=self.kitasan, rate_override='0.004')
        public_payload_cache.invalidate()
        with CaptureQueriesContext(connection) as after:
            self.client.get('/calculator-data')

        self.assertEqual(len(after), len(before))
