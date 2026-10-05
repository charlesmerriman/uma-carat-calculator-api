"""Daily legend race releases: dates, the /calculator-data payload, the one-release-per-uma
rule, the cache, and the page seed."""

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache, site_content_seed
from calculatorapi.models import (
    BannerTimeline, CalculationConstants, CustomUser, DailyLegendRaceRelease,
    DailyLegendRaceUma, FaqItem, SitePage, Uma,
)
from calculatorapi.predictions import (
    build_daily_legend_race_date_map,
    build_effective_date_maps,
)

from .base import PLAIN_TEST_STORAGES, CalculatorTestCase
from .factories import _dt, make_daily_legend_race, make_timeline


def _uma(name, rarity=None):
    return Uma.objects.create(name=name, rarity=rarity)


def _resolve(release):
    emap = build_effective_date_maps()[BannerTimeline]
    return build_daily_legend_race_date_map([release], emap)[release.id]


class DailyLegendRaceDateTests(CalculatorTestCase):
    """A release borrows its banner's START, adds its own offset, and has no end."""

    def setUp(self):
        self.start = _dt(2026, 12, 21, 22)
        self.banner = make_timeline(
            name='2nd Anniversary Part 3', global_start_date=self.start,
            global_end_date=self.start + datetime.timedelta(days=14),
        )

    def test_start_is_the_banner_start_and_there_is_no_end(self):
        resolved = _resolve(make_daily_legend_race(banner_timeline=self.banner))

        self.assertEqual(resolved['start_date'], self.start)
        self.assertIsNone(resolved['end_date'])
        self.assertFalse(resolved['is_predicted'])

    def test_a_positive_offset_moves_the_release_later(self):
        resolved = _resolve(
            make_daily_legend_race(banner_timeline=self.banner, offset_days=3)
        )
        self.assertEqual(resolved['start_date'], self.start + datetime.timedelta(days=3))

    def test_a_negative_offset_moves_the_release_earlier(self):
        resolved = _resolve(
            make_daily_legend_race(banner_timeline=self.banner, offset_days=-2)
        )
        self.assertEqual(resolved['start_date'], self.start - datetime.timedelta(days=2))

    def test_the_release_offset_is_not_reported_as_a_schedule_offset(self):
        # applied_offset_days means "cascading schedule offset baked into the
        # banner's date" everywhere else. The release's own nudge is not one.
        resolved = _resolve(
            make_daily_legend_race(banner_timeline=self.banner, offset_days=3)
        )
        self.assertEqual(resolved['applied_offset_days'], 0)

    def test_unlinked_release_resolves_to_a_null_start(self):
        resolved = _resolve(make_daily_legend_race(banner_timeline=None, offset_days=3))

        self.assertIsNone(resolved['start_date'])
        self.assertIsNone(resolved['end_date'])
        self.assertFalse(resolved['is_predicted'])

    def test_prediction_and_schedule_offset_carry_through_from_the_banner(self):
        now = timezone.now()
        # Anchor: a confirmed JP+global pair the predictor measures from.
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2022, 1, 1), jp_end_date=_dt(2022, 1, 14),
            global_start_date=now, global_end_date=now + datetime.timedelta(days=14),
        )
        predicted = make_timeline(
            name='Predicted banner',
            jp_start_date=_dt(2022, 6, 1), jp_end_date=_dt(2022, 6, 14),
            global_start_date=None, global_end_date=None,
            schedule_offset_days=5,
        )
        release = make_daily_legend_race(banner_timeline=predicted, offset_days=1)

        emap = build_effective_date_maps()[BannerTimeline]
        resolved = build_daily_legend_race_date_map([release], emap)[release.id]

        self.assertTrue(resolved['is_predicted'])
        # The banner's date already includes its schedule offset; the release
        # adds its own day on top.
        self.assertEqual(
            resolved['start_date'],
            emap[predicted.id]['start_date'] + datetime.timedelta(days=1),
        )
        self.assertEqual(resolved['applied_offset_days'], 5)


class DailyLegendRaceApiTests(CalculatorTestCase):
    """/calculator-data serves releases: start-only, sorted, umas grouped-ready."""

    def setUp(self):
        self.client = APIClient()
        self.banner = make_timeline(name='Banner', global_start_date=_dt(2026, 12, 21, 22))

    def _rows(self):
        return self.client.get('/calculator-data').data['daily_legend_race_data']

    def test_row_has_a_start_and_no_end_date_key_at_all(self):
        release = make_daily_legend_race(
            name='2nd Anniversary', banner_timeline=self.banner, offset_days=1,
        )
        row = next(r for r in self._rows() if r['id'] == release.id)

        self.assertEqual(row['name'], '2nd Anniversary')
        self.assertEqual(row['start_date'], '2026-12-22T22:00:00Z')
        self.assertFalse(row['is_predicted'])
        # Absent, not null: see StartInstantDateMixin.
        self.assertNotIn('end_date', row)
        # Folded into start_date, so neither travels.
        self.assertNotIn('offset_days', row)
        self.assertNotIn('banner_timeline', row)

    def test_umas_carry_a_resolved_rarity_and_come_sorted(self):
        make_daily_legend_race(banner_timeline=self.banner, umas=[
            _uma('King Halo', rarity=1),
            _uma('Vodka', rarity=2),
            _uma('Tokai Teio', rarity=3),
            # Not on global yet, so the import left rarity blank. Counts as ★3.
            _uma('Agnes Tachyon (Summer)', rarity=None),
        ])
        umas = self._rows()[0]['umas']

        self.assertEqual(
            [(u['name'], u['rarity']) for u in umas],
            [('Agnes Tachyon (Summer)', 3), ('Tokai Teio', 3),
             ('Vodka', 2), ('King Halo', 1)],
        )
        self.assertEqual(set(umas[0]), {'id', 'name', 'image', 'rarity'})

    def test_releases_sort_by_date_and_undated_ones_go_last(self):
        later = make_timeline(name='Later', global_start_date=_dt(2027, 4, 11))
        undated = make_daily_legend_race(name='6th Anniversary', banner_timeline=None)
        second = make_daily_legend_race(name='2.5th', banner_timeline=later)
        first = make_daily_legend_race(name='2nd', banner_timeline=self.banner)

        self.assertEqual(
            [r['id'] for r in self._rows()], [first.id, second.id, undated.id]
        )

    def test_query_count_does_not_grow_with_releases(self):
        def cold_query_count():
            public_payload_cache.invalidate()
            with CaptureQueriesContext(connection) as ctx:
                self.client.get('/calculator-data')
            return len(ctx.captured_queries)

        # The first request ever creates the constants row (three extra
        # queries), so it is made here, before anything is counted.
        CalculationConstants.load()
        make_daily_legend_race(banner_timeline=self.banner, umas=[_uma('A')])
        one = cold_query_count()
        for n in range(4):
            make_daily_legend_race(
                name=f'R{n}', banner_timeline=self.banner,
                umas=[_uma(f'B{n}'), _uma(f'C{n}')],
            )
        self.assertEqual(cold_query_count(), one)

    def test_grind_numbers_are_served_with_the_constants(self):
        constants = self.client.get('/calculator-data').data['calculation_constants']

        self.assertEqual(constants['daily_legend_race_piece_goal'], 150)
        self.assertEqual(constants['daily_legend_race_event_pieces'], 80)
        self.assertEqual(constants['daily_legend_race_pieces_per_day'], 1)

    def test_an_admin_edit_invalidates_the_cached_payload(self):
        self.assertTrue(public_payload_cache.affects_public_payload(DailyLegendRaceRelease))
        self.assertTrue(public_payload_cache.affects_public_payload(DailyLegendRaceUma))

        release = make_daily_legend_race(banner_timeline=self.banner)
        self.assertEqual(self._rows()[0]['umas'], [])
        DailyLegendRaceUma.objects.create(release=release, uma=_uma('Hishi Amazon'))

        self.assertEqual([u['name'] for u in self._rows()[0]['umas']], ['Hishi Amazon'])

    def test_constants_edit_reaches_the_payload(self):
        constants = CalculationConstants.load()
        constants.daily_legend_race_piece_goal = 140
        constants.save()

        data = self.client.get('/calculator-data').data
        self.assertEqual(data['calculation_constants']['daily_legend_race_piece_goal'], 140)


class OneReleasePerUmaTests(CalculatorTestCase):
    """An uma joins the daily races once; the database refuses a second batch."""

    def setUp(self):
        self.uma = _uma('Special Week')
        self.first = make_daily_legend_race(name='1st', umas=[self.uma])
        self.second = make_daily_legend_race(name='1.5th')

    def test_the_database_refuses_an_uma_in_two_releases(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            DailyLegendRaceUma.objects.create(release=self.second, uma=self.uma)

    def test_validation_reports_the_clash_in_plain_words(self):
        # What the admin inline shows: full_clean() runs validate_constraints().
        link = DailyLegendRaceUma(release=self.second, uma=self.uma)
        with self.assertRaises(ValidationError) as ctx:
            link.full_clean()
        self.assertIn('already in another daily legend race release', str(ctx.exception))

    def test_deleting_the_banner_unlinks_the_release_and_keeps_its_umas(self):
        banner = make_timeline(name='Banner')
        self.first.banner_timeline = banner
        self.first.save()

        banner.delete()
        self.first.refresh_from_db()

        self.assertIsNone(self.first.banner_timeline_id)
        self.assertEqual(list(self.first.umas.all()), [self.uma])


class DailyLegendRacesPageSeedTests(CalculatorTestCase):
    """The page row 0074 creates, and the rule that it never overwrites."""

    def test_the_migration_created_the_page(self):
        page = SitePage.objects.get(slug=SitePage.Slug.DAILY_LEGEND_RACES)
        self.assertEqual(page.title, 'Daily Legend Races')
        self.assertIn('own race', page.body)

    def test_seeding_again_leaves_an_edited_page_alone(self):
        SitePage.objects.filter(slug='daily-legend-races').update(body='Edited.\n')

        site_content_seed.seed_page(SitePage, 'daily-legend-races')

        self.assertEqual(SitePage.objects.get(slug='daily-legend-races').body, 'Edited.\n')

    def test_seeding_creates_the_page_when_it_is_missing(self):
        SitePage.objects.filter(slug='daily-legend-races').delete()

        site_content_seed.seed_page(SitePage, 'daily-legend-races')

        self.assertTrue(SitePage.objects.filter(slug='daily-legend-races').exists())

    def test_seeding_one_page_does_not_touch_the_faq(self):
        # The reason seed_page exists: seed() would re-create deleted FAQ items.
        FaqItem.objects.all().delete()

        site_content_seed.seed_page(SitePage, 'daily-legend-races')

        self.assertFalse(FaqItem.objects.exists())


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class DailyLegendRaceAdminTests(CalculatorTestCase):
    """The list columns an editor reads: the offset start and "On the site"."""

    def setUp(self):
        self.client.force_login(
            CustomUser.objects.create_superuser(username='boss', password='x')
        )

    def test_changelist_shows_offset_start_count_and_visibility(self):
        banner = make_timeline(name='Symboli Kris S', global_start_date=_dt(2026, 12, 21, 22))
        make_daily_legend_race(
            name='2nd Anniversary', banner_timeline=banner, offset_days=1,
            umas=[_uma('Hishi Amazon'), _uma('Mejiro Dober')],
        )
        make_daily_legend_race(name='6th Anniversary', banner_timeline=None)

        res = self.client.get(reverse('admin:calculatorapi_dailylegendracerelease_changelist'))

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Dec. 22, 2026')
        rows = {r.name: r for r in res.context['cl'].result_list}
        self.assertEqual(rows['2nd Anniversary']._uma_count, 2)  # pylint: disable=protected-access
        admin_obj = res.context['cl'].model_admin
        self.assertTrue(admin_obj.on_the_site(rows['2nd Anniversary']))
        self.assertFalse(admin_obj.on_the_site(rows['6th Anniversary']))
