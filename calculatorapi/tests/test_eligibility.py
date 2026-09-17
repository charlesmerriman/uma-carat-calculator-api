"""Selector eligibility: which cards a selector ticket can take at a given cutoff."""

import datetime

from django.utils import timezone

from calculatorapi.eligibility import (
    build_first_jp_date_maps,
    is_eligible,
    is_intrinsically_selectable,
    selection_refusal_reason,
)
from calculatorapi.models import Uma, SupportCard, UmasOnUmaBanner, SupportsOnSupportBanner
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_timeline, make_uma_banner, make_support_banner


class SelectorEligibilityTests(CalculatorTestCase):
    """A card's JP release date is derived from its earliest banner appearance."""

    def setUp(self):
        self.old_timeline = make_timeline(
            name='Old', jp_start_date=timezone.make_aware(datetime.datetime(2024, 1, 31)),
            global_start_date=None, global_end_date=None,
        )
        self.new_timeline = make_timeline(
            name='New', jp_start_date=timezone.make_aware(datetime.datetime(2026, 2, 14)),
            global_start_date=None, global_end_date=None,
        )

    def _uma_on(self, timeline, name):
        uma = Uma.objects.create(name=name)
        UmasOnUmaBanner.objects.create(
            uma=uma, banner_uma=make_uma_banner(timeline, name=f'{name} banner')
        )
        return uma

    def test_first_jp_date_is_the_earliest_banner_appearance(self):
        uma = self._uma_on(self.new_timeline, 'Rerun Uma')
        # Same uma also appeared on the older banner — the earliest wins.
        UmasOnUmaBanner.objects.create(
            uma=uma, banner_uma=make_uma_banner(self.old_timeline, name='Rerun original')
        )
        uma_dates, _ = build_first_jp_date_maps()
        self.assertEqual(uma_dates[uma.id].date(), datetime.date(2024, 1, 31))

    def test_cards_with_no_banner_are_absent_not_null(self):
        orphan = Uma.objects.create(name='Never Featured')
        uma_dates, _ = build_first_jp_date_maps()
        self.assertNotIn(orphan.id, uma_dates)

    def test_support_cards_get_their_own_map(self):
        card = SupportCard.objects.create(name='Test SSR', game_id=30184)
        SupportsOnSupportBanner.objects.create(
            support_card=card,
            banner_support=make_support_banner(self.old_timeline, name='SSR banner'),
        )
        _, support_dates = build_first_jp_date_maps()
        self.assertEqual(support_dates[card.id].date(), datetime.date(2024, 1, 31))

    def test_cutoff_is_inclusive(self):
        # Sakura Bakushin O debuted exactly on the 3rd Anniversary's cutoff and
        # the source sheet lists her as selectable — the boundary is IN.
        on_cutoff = timezone.make_aware(datetime.datetime(2024, 1, 31))
        self.assertTrue(is_eligible(on_cutoff, datetime.date(2024, 1, 31)))

    def test_card_released_after_cutoff_is_ineligible(self):
        after = timezone.make_aware(datetime.datetime(2024, 2, 1))
        self.assertFalse(is_eligible(after, datetime.date(2024, 1, 31)))

    def test_null_cutoff_means_unrestricted(self):
        self.assertTrue(is_eligible(timezone.now(), None))

    def test_unknown_release_date_is_ineligible_under_a_real_cutoff(self):
        # Conservative: claiming a selector covers a card it can't is worse than
        # hiding one it could.
        self.assertFalse(is_eligible(None, datetime.date(2024, 1, 31)))


class IntrinsicSelectorGateTests(CalculatorTestCase):
    """The second gate: units no selector can take at ANY cutoff."""

    def test_an_ordinary_uma_is_selectable_by_default(self):
        # The defaults have to land on "selectable": an uma with no rarity yet
        # (anything not on global) counts as ★3, or every picker would lose the
        # part of the catalogue nobody has game data for.
        uma = Uma.objects.create(name='Ordinary')
        self.assertFalse(uma.is_time_limited)
        self.assertIsNone(uma.rarity)
        self.assertTrue(uma.is_three_star)
        self.assertTrue(is_intrinsically_selectable(uma))

    def test_time_limited_uma_is_never_selectable(self):
        uma = Uma.objects.create(name='Limited', is_time_limited=True)
        self.assertFalse(is_intrinsically_selectable(uma))

    def test_non_three_star_uma_is_never_selectable(self):
        for rarity in (1, 2):
            uma = Uma.objects.create(name=f'Star {rarity}', rarity=rarity)
            self.assertFalse(is_intrinsically_selectable(uma))

    def test_three_star_uma_is_selectable(self):
        uma = Uma.objects.create(name='Three Star', rarity=3)
        self.assertTrue(is_intrinsically_selectable(uma))

    def test_support_cards_have_no_intrinsic_gate(self):
        # Neither gate exists on SupportCard; absent must read as unrestricted
        # rather than as "not a ★3". An R card makes the point: SupportCard has
        # a `rarity` of its own, and the gate must not start reading it.
        card = SupportCard.objects.create(name='Any R', game_id=10001)
        self.assertEqual(card.rarity, 1)
        self.assertTrue(is_intrinsically_selectable(card))

    def test_intrinsic_gate_bites_under_a_null_cutoff(self):
        # THE point of the second gate. A null cutoff makes the temporal gate
        # wave everything through; a time-limited uma must still be refused.
        uma = Uma.objects.create(name='Limited', is_time_limited=True)
        self.assertTrue(is_eligible(None, None))
        self.assertIsNotNone(
            selection_refusal_reason(uma, None, None, 'this selector')
        )

    def test_intrinsic_refusal_names_the_card_and_not_a_cutoff(self):
        uma = Uma.objects.create(name='Limited', is_time_limited=True)
        reason = selection_refusal_reason(uma, None, None, 'this selector')
        self.assertIn('Limited', reason)
        self.assertNotIn('cutoff', reason)

    def test_a_selectable_uma_inside_the_cutoff_is_refused_for_nothing(self):
        uma = Uma.objects.create(name='Fine')
        released = timezone.make_aware(datetime.datetime(2024, 1, 1))
        self.assertIsNone(
            selection_refusal_reason(
                uma, released, datetime.date(2024, 1, 31), 'this selector'
            )
        )

    def test_cutoff_refusal_still_fires_for_a_selectable_uma(self):
        uma = Uma.objects.create(name='Too New')
        released = timezone.make_aware(datetime.datetime(2024, 2, 1))
        reason = selection_refusal_reason(
            uma, released, datetime.date(2024, 1, 31), 'this step-up'
        )
        self.assertIn('this step-up', reason)
        self.assertIn('2024-01-31', reason)
