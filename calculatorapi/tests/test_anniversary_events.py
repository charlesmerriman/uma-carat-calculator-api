"""GET /calculator-data: anniversary campaigns, their products and the parts they open with.

Split out of test_calculator_get.py (2026-11-16) when the campaign-part tests
took that module past pylint's 1,000 lines.
"""

import datetime

from rest_framework.test import APIClient

from calculatorapi.models import AnniversaryEventBanner, AnniversaryEventProduct, BannerStepUp
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_user,
    make_timeline,
    make_anniversary_event,
    auth_client,
    _iso,
)

# An anniversary's 1500 Carat Pack, the product migration 0138 moves to Part 3.
PACK_1500 = {'product_type': 'carat_pack', 'name': '1500 Carat Pack', 'usd_cost': 14, 'paid_carat_amount': 1500}


class AnniversaryEventApiTests(CalculatorTestCase):
    """The campaign payload and the banner strip it attaches to."""

    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.timeline = make_timeline(name='3rd Anniv Part 1')
        self.event = make_anniversary_event(
            name='3rd Anniversary',
            jp_cutoff_date=datetime.date(2024, 1, 31),
            parts=[self.timeline],
            products=[
                {'product_type': 'carat_pack', 'name': '7500 Carat Pack',
                 'usd_cost': 70, 'paid_carat_amount': 7500,
                 'webstore_multiplier': '1.10', 'max_quantity': 3, 'order': 1},
                {'product_type': 'uma_selector', 'name': '$21 Uma Selector',
                 'usd_cost': 21, 'paid_carat_amount': 1500, 'order': 2},
            ],
        )

    def test_products_are_serialized_with_numeric_money(self):
        # Asserted against the RENDERED payload, not res.data: res.data still
        # holds Decimals and dates, and the point here is the wire format the
        # browser actually parses.
        event = self.client.get('/calculator-data').json()['anniversary_event_data'][0]
        pack = next(p for p in event['products'] if p['product_type'] == 'carat_pack')

        # JSON numbers, not DRF's default Decimal-as-string — the client does
        # arithmetic on these directly.
        self.assertEqual(pack['usd_cost'], 70.0)
        self.assertEqual(pack['webstore_multiplier'], 1.10)
        self.assertIsInstance(pack['usd_cost'], float)
        self.assertEqual(pack['paid_carat_amount'], 7500)
        self.assertEqual(pack['max_quantity'], 3)

    def test_product_inherits_the_campaign_cutoff(self):
        event = self.client.get('/calculator-data').json()['anniversary_event_data'][0]
        selector = next(p for p in event['products'] if p['product_type'] == 'uma_selector')

        self.assertEqual(selector['jp_cutoff_date'], '2024-01-31')
        # The raw override stays null — this cutoff came from the campaign.
        self.assertIsNone(selector['jp_cutoff_date_override'])

    def test_product_override_beats_the_campaign_cutoff(self):
        AnniversaryEventProduct.objects.create(
            anniversary_event=self.event, product_type='support_selector',
            name='$70 SSR Selector', usd_cost=70, paid_carat_amount=7500,
            jp_cutoff_date=datetime.date(2023, 1, 1),
        )
        event = self.client.get('/calculator-data').json()['anniversary_event_data'][0]
        selector = next(p for p in event['products'] if p['name'] == '$70 SSR Selector')

        self.assertEqual(selector['jp_cutoff_date'], '2023-01-01')

    def test_campaign_emits_a_main_start_date_alongside_its_window(self):
        """The wire carries the event's own start, not just the campaign's."""
        part2 = make_timeline(
            name='3rd Anniv Part 2',
            global_start_date=self.timeline.global_end_date,
            global_end_date=self.timeline.global_end_date + datetime.timedelta(days=20),
        )
        AnniversaryEventBanner.objects.create(
            anniversary_event=self.event, banner_timeline=part2, part_number=2,
        )

        event = self.client.get('/calculator-data').json()['anniversary_event_data'][0]

        self.assertEqual(event['start_date'], _iso(self.timeline.global_start_date))
        self.assertEqual(event['main_start_date'], _iso(part2.global_start_date))
        self.assertEqual(event['end_date'], _iso(part2.global_end_date))

    def _add_parts_2_and_3(self):
        part2 = make_timeline(
            name='3rd Anniv Part 2', global_start_date=self.timeline.global_end_date,
            global_end_date=self.timeline.global_end_date + datetime.timedelta(days=20),
        )
        part3 = make_timeline(
            name='3rd Anniv Part 3', global_start_date=part2.global_start_date + datetime.timedelta(days=14),
            global_end_date=part2.global_end_date + datetime.timedelta(days=14),
        )
        for number, timeline in ((2, part2), (3, part3)):
            AnniversaryEventBanner.objects.create(
                anniversary_event=self.event, banner_timeline=timeline, part_number=number,
            )
        return part2, part3

    def test_a_product_opens_with_its_part_or_the_main_part(self):
        """`opens_at` is when its carats credit: Part 3's start for a 1500 pack
        set to Part 3, the main part (Part 2) for the rest, and the main part
        again when the part it names isn't linked (an editor's typo)."""
        part2, part3 = self._add_parts_2_and_3()
        AnniversaryEventProduct.objects.create(
            anniversary_event=self.event, product_type='carat_pack', name='1500 Carat Pack',
            usd_cost=14, paid_carat_amount=1500, opens_with_part=3,
        )
        AnniversaryEventProduct.objects.create(
            anniversary_event=self.event, product_type='carat_pack', name='Spark Enhancement',
            usd_cost=21, paid_carat_amount=1500, opens_with_part=9,
        )
        event = self.client.get('/calculator-data').json()['anniversary_event_data'][0]
        opens = {p['name']: (p['opens_with_part'], p['opens_at']) for p in event['products']}
        self.assertEqual(opens['1500 Carat Pack'], (3, _iso(part3.global_start_date)))
        self.assertEqual(opens['7500 Carat Pack'], (None, _iso(part2.global_start_date)))
        self.assertEqual(opens['Spark Enhancement'], (9, _iso(part2.global_start_date)))

    def test_the_1500_packs_move_to_part_3_on_deploy(self):
        """Migration 0138: anniversaries with a Part 3 only, blanks only."""
        import importlib  # pylint: disable=import-outside-toplevel
        from django.apps import apps as django_apps  # pylint: disable=import-outside-toplevel
        set_part_3 = importlib.import_module(
            'calculatorapi.migrations.0138_anniversary_1500_packs_open_in_part_3'
        ).set_part_3
        self._add_parts_2_and_3()
        pack = AnniversaryEventProduct.objects.create(
            anniversary_event=self.event, product_type='carat_pack', name='1500 Carat Pack',
            usd_cost=14, paid_carat_amount=1500,
        )
        new_year = make_anniversary_event(
            name='New Years', event_type='new_year', parts=[make_timeline(), make_timeline(), make_timeline()],
            products=[PACK_1500],
        )
        no_part_3 = make_anniversary_event(
            name='1.5th Anniversary', parts=[make_timeline(), make_timeline()],
            products=[PACK_1500],
        )
        exception = make_anniversary_event(
            name='2.5th Anniversary', parts=[make_timeline(), make_timeline(), make_timeline()],
            products=[PACK_1500],
        )
        set_part_3(django_apps, None)
        pack.refresh_from_db()
        self.assertEqual(pack.opens_with_part, 3)
        # Daptrius's one exception sells it with Part 2.
        self.assertIsNone(exception.products.get().opens_with_part)
        self.assertIsNone(new_year.products.get().opens_with_part)
        self.assertIsNone(no_part_3.products.get().opens_with_part)
        self.assertIsNone(self.event.products.get(name='7500 Carat Pack').opens_with_part)

    def test_banner_carries_its_campaign_and_part_number(self):
        res = self.client.get('/calculator-data')
        timeline = next(
            t for t in res.json()['banner_timeline_data'] if t['id'] == self.timeline.id
        )
        self.assertEqual(timeline['anniversary_event']['name'], '3rd Anniversary')
        self.assertEqual(timeline['anniversary_event']['part_number'], 1)

    def test_unattached_banner_reports_no_campaign(self):
        loose = make_timeline(name='Ordinary banner')
        res = self.client.get('/calculator-data')
        timeline = next(
            t for t in res.json()['banner_timeline_data'] if t['id'] == loose.id
        )
        self.assertIsNone(timeline['anniversary_event'])

    def test_banner_carries_its_step_ups_for_the_timeline_chip(self):
        BannerStepUp.objects.create(
            banner_timeline=self.timeline, anniversary_event=self.event,
            name='3rd Anniversary Star-3 Select Step-Up',
            card_type='uma', banner_count=1,
        )
        BannerStepUp.objects.create(
            banner_timeline=self.timeline, anniversary_event=self.event,
            name='3rd Anniversary SSR Select Step-Up',
            card_type='support', banner_count=2,
        )
        res = self.client.get('/calculator-data')
        timeline = next(
            t for t in res.json()['banner_timeline_data'] if t['id'] == self.timeline.id
        )

        by_type = {s['card_type']: s for s in timeline['banner_step_ups']}
        self.assertEqual(by_type['uma']['banner_count'], 1)
        self.assertEqual(by_type['support']['banner_count'], 2)
        # A summary, not the full record: nesting banner_timeline here would send
        # each timeline back inside itself.
        self.assertNotIn('banner_timeline', by_type['uma'])

    def test_banner_with_no_step_ups_reports_an_empty_list(self):
        loose = make_timeline(name='Ordinary banner')
        res = self.client.get('/calculator-data')
        timeline = next(
            t for t in res.json()['banner_timeline_data'] if t['id'] == loose.id
        )
        self.assertEqual(timeline['banner_step_ups'], [])

    def test_campaigns_are_public(self):
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()['anniversary_event_data']), 1)
