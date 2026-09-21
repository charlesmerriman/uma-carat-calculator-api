"""PATCH /calculator-data: saving planned banners, campaign purchases and reserved copies."""

# A setUp that builds a scenario keeps one attribute per object under test.
# pylint: disable=too-many-instance-attributes

import datetime

from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi.models import (
    Uma, SupportCard,
    UserPlannedBanner,
    AnniversaryEventProduct,
    UserPlannedPurchase,
    UmasOnUmaBanner,
)
from calculatorapi import plans
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.views.user_planned_banner import NOTE_MAX_LENGTH
from calculatorapi.tests.factories import (
    make_user,
    make_timeline,
    make_uma_banner,
    make_support_banner,
    make_anniversary_event,
    auth_client,
)


class CalculatorPatchTests(CalculatorTestCase):
    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)

    # stats ────────────────────────────────────────────────────────────────────

    def test_patch_stats_updates_user(self):
        res = self.client.patch(
            '/calculator-data',
            {'user_stats_data': {'current_carat': 9999}},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_carat, 9999)

    def test_invalid_banner_rolls_back_stats_saved_earlier_in_the_same_patch(self):
        """A partly-invalid PATCH must persist nothing.

        Regression: `return`ing a Response from inside `transaction.atomic()`
        exits the block normally, so Django committed. Stats are written before
        banners, so a rejected banner used to leave the stats change behind —
        the exact split state the transaction is there to prevent.
        """
        res = self.client.patch(
            '/calculator-data',
            {
                'user_stats_data': {'current_carat': 4242},
                # Neither banner_uma nor banner_support — fails validation.
                'user_planned_banner_data': [{'number_of_pulls': 10}],
            },
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.current_carat, 4242)

    def test_patch_stats_updates_misc_earnings_toggle(self):
        # misc_earnings defaults to True; confirm the serializer accepts and
        # persists a toggle-off through the same PATCH path as the other stats.
        self.assertTrue(self.user.misc_earnings)
        res = self.client.patch(
            '/calculator-data',
            {'user_stats_data': {'misc_earnings': False}},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.misc_earnings)

    def test_patch_stats_updates_pull_strategy_toggles(self):
        # The three toggles added for the Settings menu round-trip through the
        # same partial-PATCH path. Confirm their defaults, then flip each and
        # verify it persists.
        self.assertTrue(self.user.monthly_shop_tickets)
        self.assertTrue(self.user.discounted_paid_pulls)
        self.assertTrue(self.user.full_price_paid_pulls)
        res = self.client.patch(
            '/calculator-data',
            {'user_stats_data': {
                'monthly_shop_tickets': False,
                'discounted_paid_pulls': False,
                'full_price_paid_pulls': False,
            }},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.monthly_shop_tickets)
        self.assertFalse(self.user.discounted_paid_pulls)
        self.assertFalse(self.user.full_price_paid_pulls)

    def test_patch_invalid_stats_returns_400(self):
        res = self.client.patch(
            '/calculator-data',
            {'user_stats_data': {'current_carat': 'not-a-number'}},
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    # banner create ────────────────────────────────────────────────────────────

    def test_patch_creates_new_banner(self):
        uma_banner = make_uma_banner()
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'banner_uma': uma_banner.id, 'banner_support': None, 'number_of_pulls': 5}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserPlannedBanner.objects.filter(user=self.user).count(), 1)
        self.assertEqual(UserPlannedBanner.objects.get(user=self.user).number_of_pulls, 5)

    # banner update ────────────────────────────────────────────────────────────

    def test_patch_updates_existing_banner(self):
        uma_banner = make_uma_banner()
        planned = UserPlannedBanner.objects.create(
            user=self.user, banner_uma=uma_banner, number_of_pulls=5
        )
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'id': planned.id, 'banner_uma': uma_banner.id, 'banner_support': None, 'number_of_pulls': 10}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        planned.refresh_from_db()
        self.assertEqual(planned.number_of_pulls, 10)

    # banner delete ────────────────────────────────────────────────────────────

    def test_patch_deletes_banners_absent_from_request(self):
        uma_banner = make_uma_banner()
        keep = UserPlannedBanner.objects.create(user=self.user, banner_uma=uma_banner, number_of_pulls=5)
        drop = UserPlannedBanner.objects.create(user=self.user, banner_uma=uma_banner, number_of_pulls=3)

        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'id': keep.id, 'banner_uma': uma_banner.id, 'banner_support': None, 'number_of_pulls': 5}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(UserPlannedBanner.objects.filter(id=keep.id).exists())
        self.assertFalse(UserPlannedBanner.objects.filter(id=drop.id).exists())

    # ownership ────────────────────────────────────────────────────────────────

    def test_patch_cannot_update_another_users_banner(self):
        """Sending an id that belongs to a different user returns 404."""
        uma_banner = make_uma_banner()
        other_user = make_user('otheruser')
        other_banner = UserPlannedBanner.objects.create(
            user=other_user, banner_uma=uma_banner, number_of_pulls=5
        )
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'id': other_banner.id, 'banner_uma': uma_banner.id, 'banner_support': None, 'number_of_pulls': 99}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 404)
        other_banner.refresh_from_db()
        self.assertEqual(other_banner.number_of_pulls, 5)  # unchanged

    # serializer validation ────────────────────────────────────────────────────

    def test_patch_both_banner_types_returns_400(self):
        """Providing both banner_uma and banner_support violates the XOR constraint."""
        uma_banner = make_uma_banner()
        support_banner = make_support_banner()
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {
                    'banner_uma': uma_banner.id,
                    'banner_support': support_banner.id,
                    'number_of_pulls': 5,
                }
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    def test_patch_neither_banner_type_returns_400(self):
        """Omitting both banner fields is invalid even though both are optional individually."""
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'banner_uma': None, 'banner_support': None, 'number_of_pulls': 5}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 400)

    # auth ─────────────────────────────────────────────────────────────────────

    def test_patch_unauthenticated_returns_401(self):
        res = APIClient().patch('/calculator-data', {}, format='json')
        self.assertEqual(res.status_code, 401)

    # NOTE: transaction.atomic() in update_calculator_data only rolls back on
    # an unhandled *exception*, not on an early `return Response(...)`. If stats
    # save succeeds but a banner update then returns a 4xx, the stats change is
    # already committed. This is a known limitation in the current implementation.


class UserPlannedPurchaseTests(CalculatorTestCase):
    """The PATCH upsert and the selector-target validation behind it."""

    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.timeline = make_timeline(
            name='Anniv', jp_start_date=timezone.make_aware(datetime.datetime(2024, 2, 14)),
        )
        self.event = make_anniversary_event(
            name='3rd Anniversary',
            jp_cutoff_date=datetime.date(2024, 1, 31),
            parts=[self.timeline],
        )
        self.pack = AnniversaryEventProduct.objects.create(
            anniversary_event=self.event, product_type='carat_pack',
            name='7500 Carat Pack', usd_cost=70, paid_carat_amount=7500,
            max_quantity=3,
        )
        self.uma_selector = AnniversaryEventProduct.objects.create(
            anniversary_event=self.event, product_type='uma_selector',
            name='$21 Uma Selector', usd_cost=21, paid_carat_amount=1500,
        )
        # An uma old enough for the cutoff, and one too new for it.
        self.eligible_uma = self._uma_first_seen('Eligible', datetime.datetime(2023, 6, 1))
        self.late_uma = self._uma_first_seen('Too New', datetime.datetime(2024, 6, 1))

    def _uma_first_seen(self, name, when):
        timeline = make_timeline(
            name=f'{name} debut', jp_start_date=timezone.make_aware(when),
        )
        uma = Uma.objects.create(name=name)
        UmasOnUmaBanner.objects.create(
            uma=uma, banner_uma=make_uma_banner(timeline, name=f'{name} banner')
        )
        return uma

    def _patch(self, purchases):
        return self.client.patch(
            '/calculator-data',
            {'user_planned_purchase_data': purchases},
            format='json',
        )

    def test_creates_a_pack_purchase(self):
        res = self._patch([{'product': self.pack.id, 'quantity': 2}])
        self.assertEqual(res.status_code, 200)

        purchase = UserPlannedPurchase.objects.get(user=self.user)
        self.assertEqual(purchase.product_id, self.pack.id)
        self.assertEqual(purchase.quantity, 2)

    def test_updates_an_existing_purchase_by_id(self):
        self._patch([{'product': self.pack.id, 'quantity': 1}])
        existing = UserPlannedPurchase.objects.get(user=self.user)

        res = self._patch([{'id': existing.id, 'product': self.pack.id, 'quantity': 3}])
        self.assertEqual(res.status_code, 200)

        existing.refresh_from_db()
        self.assertEqual(existing.quantity, 3)
        self.assertEqual(UserPlannedPurchase.objects.filter(user=self.user).count(), 1)

    def test_empty_list_clears_the_plan(self):
        self._patch([{'product': self.pack.id, 'quantity': 1}])
        res = self._patch([])

        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserPlannedPurchase.objects.filter(user=self.user).count(), 0)

    def test_absent_key_leaves_the_plan_alone(self):
        self._patch([{'product': self.pack.id, 'quantity': 1}])
        res = self.client.patch(
            '/calculator-data', {'user_stats_data': {'current_carat': 500}}, format='json'
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserPlannedPurchase.objects.filter(user=self.user).count(), 1)

    def test_cannot_update_another_users_purchase(self):
        other = make_user(username='someone-else')
        theirs = UserPlannedPurchase.objects.create(
            user=other, product=self.pack, quantity=1
        )
        res = self._patch([{'id': theirs.id, 'product': self.pack.id, 'quantity': 9}])

        self.assertEqual(res.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.quantity, 1)

    def test_accepts_an_eligible_selector_target(self):
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.eligible_uma.id}
        ])
        self.assertEqual(res.status_code, 200)

    def test_rejects_a_target_released_after_the_cutoff(self):
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.late_uma.id}
        ])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(UserPlannedPurchase.objects.filter(user=self.user).count(), 0)

    def _save_target(self, uma):
        """Save a selector pick and hand back the stored row."""
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1, 'target_uma': uma.id}
        ])
        self.assertEqual(res.status_code, 200)
        return UserPlannedPurchase.objects.get(user=self.user)

    def _tighten_cutoff_past(self, uma_first_seen):
        """Move the campaign cutoff back so an already-saved pick is now too new."""
        self.event.jp_cutoff_date = uma_first_seen
        self.event.save(update_fields=['jp_cutoff_date'])

    def test_grandfathers_a_saved_target_when_the_cutoff_moves(self):
        # The production regression: a pick legal when it was made (the campaign
        # had a later cutoff, or none at all) must not start rejecting the whole
        # PATCH once an editor or a backfill tightens that cutoff.
        saved = self._save_target(self.eligible_uma)
        self._tighten_cutoff_past(datetime.date(2023, 1, 1))

        res = self._patch([
            {'id': saved.id, 'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.eligible_uma.id}
        ])

        self.assertEqual(res.status_code, 200)
        saved.refresh_from_db()
        self.assertEqual(saved.target_uma_id, self.eligible_uma.id)

    def test_a_grandfathered_row_does_not_block_the_rest_of_the_plan(self):
        # The reason the regression was severe: one stale pick took stats and
        # banners down with it, because the whole PATCH shares one transaction.
        saved = self._save_target(self.eligible_uma)
        self._tighten_cutoff_past(datetime.date(2023, 1, 1))

        res = self.client.patch(
            '/calculator-data',
            {
                'user_stats_data': {'current_carat': 4321},
                'user_planned_purchase_data': [
                    {'id': saved.id, 'product': self.uma_selector.id,
                     'quantity': 1, 'target_uma': self.eligible_uma.id}
                ],
            },
            format='json',
        )

        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_carat, 4321)

    def test_rejects_a_time_limited_target(self):
        self.eligible_uma.is_time_limited = True
        self.eligible_uma.save(update_fields=['is_time_limited'])
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.eligible_uma.id}
        ])
        self.assertEqual(res.status_code, 400)

    def test_rejects_a_non_three_star_target(self):
        self.eligible_uma.rarity = 2
        self.eligible_uma.save(update_fields=['rarity'])
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.eligible_uma.id}
        ])
        self.assertEqual(res.status_code, 400)

    def test_the_intrinsic_gate_survives_an_unrestricted_selector(self):
        # A null cutoff makes the DATE unrestricted; the unit is still refused.
        # The old `if cutoff is None: return` early-out admitted this.
        self.event.jp_cutoff_date = None
        self.event.save()
        self.late_uma.is_time_limited = True
        self.late_uma.save(update_fields=['is_time_limited'])
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.late_uma.id}
        ])
        self.assertEqual(res.status_code, 400)

    def test_grandfathers_a_saved_target_that_is_flagged_time_limited_later(self):
        # An editor flagging a unit must not lock its plan's owner out of saving.
        saved = self._save_target(self.eligible_uma)
        self.eligible_uma.is_time_limited = True
        self.eligible_uma.save(update_fields=['is_time_limited'])

        res = self._patch([
            {'id': saved.id, 'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.eligible_uma.id}
        ])
        self.assertEqual(res.status_code, 200)

    def test_still_rejects_a_changed_target_under_a_tightened_cutoff(self):
        # Grandfathering covers the stored pairing only — editing the row is the
        # user acting now, and gets checked against the cutoff as it stands.
        saved = self._save_target(self.eligible_uma)
        self._tighten_cutoff_past(datetime.date(2023, 1, 1))

        res = self._patch([
            {'id': saved.id, 'product': self.uma_selector.id, 'quantity': 1,
             'target_uma': self.late_uma.id}
        ])

        self.assertEqual(res.status_code, 400)
        saved.refresh_from_db()
        self.assertEqual(saved.target_uma_id, self.eligible_uma.id)

    def test_grandfathering_does_not_survive_moving_to_another_campaign(self):
        # Same target, different campaign — a new pairing, so the destination
        # campaign's cutoff applies rather than the one it was saved under.
        saved = self._save_target(self.eligible_uma)
        stricter = make_anniversary_event(
            name='1st Anniversary',
            jp_cutoff_date=datetime.date(2022, 1, 31),
            parts=[make_timeline(
                name='1st Anniv',
                jp_start_date=timezone.make_aware(datetime.datetime(2022, 2, 14)),
            )],
        )
        other_selector = AnniversaryEventProduct.objects.create(
            anniversary_event=stricter, product_type='uma_selector',
            name='$21 Uma Selector', usd_cost=21, paid_carat_amount=1500,
        )

        res = self._patch([
            {'id': saved.id, 'product': other_selector.id, 'quantity': 1,
             'target_uma': self.eligible_uma.id}
        ])

        self.assertEqual(res.status_code, 400)

    def test_rejects_a_target_on_a_carat_pack(self):
        res = self._patch([
            {'product': self.pack.id, 'quantity': 1, 'target_uma': self.eligible_uma.id}
        ])
        self.assertEqual(res.status_code, 400)

    def test_rejects_a_support_target_on_an_uma_selector(self):
        card = SupportCard.objects.create(name='An SSR', game_id=30001)
        res = self._patch([
            {'product': self.uma_selector.id, 'quantity': 1, 'target_support': card.id}
        ])
        self.assertEqual(res.status_code, 400)

    def test_a_failed_row_rolls_back_the_whole_patch(self):
        # Stats are written before purchases; an invalid purchase must undo them.
        res = self.client.patch(
            '/calculator-data',
            {
                'user_stats_data': {'current_carat': 12345},
                'user_planned_purchase_data': [
                    {'product': self.uma_selector.id, 'target_uma': self.late_uma.id}
                ],
            },
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.current_carat, 12345)

    def test_purchases_are_scoped_to_the_requesting_user(self):
        other = make_user(username='not-me')
        UserPlannedPurchase.objects.create(user=other, product=self.pack, quantity=5)
        self._patch([{'product': self.pack.id, 'quantity': 1}])

        res = self.client.get('/calculator-data')
        self.assertEqual(len(res.data['user_planned_purchase_data']), 1)
        self.assertEqual(res.data['user_planned_purchase_data'][0]['quantity'], 1)


class ReservedCopiesTests(CalculatorTestCase):
    """reserved_copies rides along on the existing planned-banner payload."""

    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.banner = make_uma_banner()

    def test_defaults_to_zero_and_round_trips(self):
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'banner_uma': self.banner.id, 'number_of_pulls': 100}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserPlannedBanner.objects.get(user=self.user).reserved_copies, 0)

        planned = UserPlannedBanner.objects.get(user=self.user)
        res = self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [
                {'id': planned.id, 'banner_uma': self.banner.id,
                 'number_of_pulls': 100, 'reserved_copies': 2}
            ]},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        planned.refresh_from_db()
        self.assertEqual(planned.reserved_copies, 2)

        res = self.client.get('/calculator-data')
        self.assertEqual(res.data['user_planned_banner_data'][0]['reserved_copies'], 2)


class PlannedBannerNoteTests(CalculatorTestCase):
    """A row's note rides along on the planned-banner payload, like reserved_copies."""

    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.banner = make_uma_banner()

    def _patch(self, row):
        return self.client.patch(
            '/calculator-data',
            {'user_planned_banner_data': [row]},
            format='json',
        )

    def test_defaults_to_blank_and_round_trips(self):
        res = self._patch({'banner_uma': self.banner.id, 'number_of_pulls': 100})
        self.assertEqual(res.status_code, 200)
        planned = UserPlannedBanner.objects.get(user=self.user)
        self.assertEqual(planned.note, '')

        res = self._patch({
            'id': planned.id, 'banner_uma': self.banner.id,
            'number_of_pulls': 100, 'note': '  skip if the selector covers her  ',
        })
        self.assertEqual(res.status_code, 200)
        planned.refresh_from_db()
        # trim_whitespace: stored without the padding.
        self.assertEqual(planned.note, 'skip if the selector covers her')

        res = self.client.get('/calculator-data')
        self.assertEqual(
            res.data['user_planned_banner_data'][0]['note'],
            'skip if the selector covers her',
        )

    def test_a_body_without_note_keeps_the_stored_one(self):
        """A cached pre-notes bundle must not wipe notes when it saves."""
        planned = UserPlannedBanner.objects.create(
            user=self.user, plan=plans.get_active_plan(self.user),
            banner_uma=self.banner, number_of_pulls=100, note='keep me',
        )
        res = self._patch({
            'id': planned.id, 'banner_uma': self.banner.id, 'number_of_pulls': 120,
        })
        self.assertEqual(res.status_code, 200)
        planned.refresh_from_db()
        self.assertEqual(planned.number_of_pulls, 120)
        self.assertEqual(planned.note, 'keep me')

    def test_a_note_can_be_cleared(self):
        planned = UserPlannedBanner.objects.create(
            user=self.user, plan=plans.get_active_plan(self.user),
            banner_uma=self.banner, number_of_pulls=100, note='old',
        )
        res = self._patch({
            'id': planned.id, 'banner_uma': self.banner.id,
            'number_of_pulls': 100, 'note': '',
        })
        self.assertEqual(res.status_code, 200)
        planned.refresh_from_db()
        self.assertEqual(planned.note, '')

    def test_a_note_over_the_cap_is_a_400_and_saves_nothing(self):
        res = self._patch({
            'banner_uma': self.banner.id, 'number_of_pulls': 100,
            'note': 'x' * (NOTE_MAX_LENGTH + 1),
        })
        self.assertEqual(res.status_code, 400)
        self.assertFalse(UserPlannedBanner.objects.filter(user=self.user).exists())

        res = self._patch({
            'banner_uma': self.banner.id, 'number_of_pulls': 100,
            'note': 'x' * NOTE_MAX_LENGTH,
        })
        self.assertEqual(res.status_code, 200)
