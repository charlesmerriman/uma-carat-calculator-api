"""Step-up banners, and the ten cards a user picks at one."""

# One method per case, so a thorough test class outgrows the public-method cap.
# A setUp that builds a scenario keeps one attribute per object under test.
# pylint: disable=too-many-public-methods,too-many-instance-attributes

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi.views.user_planned_banner import UserPlannedBannerSerializer
from calculatorapi.models import (
    Uma, SupportCard,
    BannerStepUp, UserPlannedBanner,
    UserStepUpSelection,
    UmasOnUmaBanner, SupportsOnSupportBanner,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_user,
    make_timeline,
    make_uma_banner,
    make_support_banner,
    make_step_up_banner,
    make_anniversary_event,
    auth_client,
    _dt,
)


class BannerStepUpTests(CalculatorTestCase):
    """The step-up model, its constraint, and how it reaches the API."""

    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_max_steps_is_five_per_banner_sold(self):
        # The real ceiling on a plan. The sheet's own 35-step cap is an artifact
        # of its lookup table's extent, and with at most 3 banners never binds.
        self.assertEqual(make_step_up_banner(banner_count=1).max_steps, 5)
        self.assertEqual(make_step_up_banner(banner_count=3).max_steps, 15)

    def test_clean_rejects_a_timeline_from_another_campaign(self):
        # The two FKs are both load-bearing: the timeline dates the row, the
        # campaign supplies the cutoff. A mismatch would date a step-up off an
        # unrelated window and silently project the wrong income.
        part = make_timeline(name='Campaign Part')
        event = make_anniversary_event(name='Campaign', parts=(part,))
        stranger = make_timeline(name='Unrelated Banner')

        step_up = BannerStepUp(
            anniversary_event=event, banner_timeline=stranger, name='Bad',
        )
        with self.assertRaises(ValidationError) as ctx:
            step_up.full_clean()
        self.assertIn('banner_timeline', ctx.exception.error_dict)

    def test_clean_accepts_a_timeline_that_is_one_of_the_campaign_parts(self):
        part_one = make_timeline(name='Part 1')
        part_two = make_timeline(name='Part 2')
        event = make_anniversary_event(name='Campaign', parts=(part_one, part_two))

        step_up = BannerStepUp(
            anniversary_event=event, banner_timeline=part_two, name='Good',
            card_type='support', banner_count=3,
        )
        step_up.full_clean()  # must not raise

    def test_planned_row_accepts_a_step_up_as_its_only_target(self):
        step_up = make_step_up_banner()
        row = UserPlannedBanner.objects.create(
            user=self.user, banner_step_up=step_up, number_of_pulls=10,
        )
        self.assertEqual(row.banner_target, step_up)

    def test_constraint_rejects_two_targets_including_the_new_one(self):
        step_up = make_step_up_banner()
        uma_banner = make_uma_banner()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserPlannedBanner.objects.create(
                    user=self.user, banner_uma=uma_banner,
                    banner_step_up=step_up, number_of_pulls=1,
                )

    def test_constraint_still_rejects_a_row_with_no_target(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserPlannedBanner.objects.create(user=self.user, number_of_pulls=1)

    def test_str_counts_steps_not_pulls_on_a_step_up_row(self):
        # number_of_pulls is deliberately overloaded; the noun has to follow.
        step_up = make_step_up_banner(name='5th Anniversary SSR Select Step-Up')
        row = UserPlannedBanner.objects.create(
            user=self.user, banner_step_up=step_up, number_of_pulls=10,
        )
        self.assertIn('10 steps', str(row))

    def test_calculator_data_serves_step_up_banners(self):
        make_step_up_banner(name='5th Anniversary SSR Select Step-Up',
                            card_type='support', banner_count=3)

        res = self.client.get('/calculator-data')

        self.assertEqual(res.status_code, 200)
        rows = res.data['banner_step_up_data']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['name'], '5th Anniversary SSR Select Step-Up')
        self.assertEqual(row['card_type'], 'support')
        # Derived server-side so the count and the rule stay together.
        self.assertEqual(row['max_steps'], 15)
        # Nested exactly like its uma/support peers -- that shared shape is what
        # lets the client resolve all three kinds through one code path.
        self.assertIn('banner_timeline', row)
        self.assertIn('start_date', row['banner_timeline'])

    def test_step_up_row_folds_in_its_campaign_cutoff(self):
        # A step-up's candidates are back-catalogue cards bounded by the
        # campaign's cutoff. Folded in the same way campaign products fold it,
        # so a row can show the bound without joining the campaign itself.
        part = make_timeline(name='Part 2')
        event = make_anniversary_event(
            name='5th Anniversary', jp_cutoff_date=datetime.date(2026, 1, 30),
            parts=(part,),
        )
        make_step_up_banner(event=event, timeline=part)

        res = self.client.get('/calculator-data')

        # Serialized as an ISO string, the same shape the campaign products
        # emit theirs in.
        self.assertEqual(
            res.data['banner_step_up_data'][0]['jp_cutoff_date'], '2026-01-30',
        )

    def test_planned_step_up_round_trips_through_patch(self):
        step_up = make_step_up_banner()
        payload = {
            'user_planned_banner_data': [
                {'number_of_pulls': 10, 'reserved_copies': 0,
                 'banner_uma': None, 'banner_support': None,
                 'banner_step_up': step_up.id},
            ],
            # Sent alongside because a PATCH body missing a collection leaves it
            # alone -- an omitted one would make this pass without saving.
            'user_planned_purchase_data': [],
        }
        res = self.client.patch('/calculator-data', payload, format='json')
        self.assertEqual(res.status_code, 200)

        saved = UserPlannedBanner.objects.get(user=self.user)
        self.assertEqual(saved.banner_step_up_id, step_up.id)
        self.assertIsNone(saved.banner_uma_id)

        # And it comes back nested, not as a bare id.
        got = self.client.get('/calculator-data')
        row = got.data['user_planned_banner_data'][0]
        self.assertEqual(row['banner_step_up']['id'], step_up.id)

    def test_patch_rejects_a_row_with_two_targets(self):
        step_up = make_step_up_banner()
        uma_banner = make_uma_banner()
        res = self.client.patch('/calculator-data', {
            'user_planned_banner_data': [
                {'number_of_pulls': 1, 'reserved_copies': 0,
                 'banner_uma': uma_banner.id, 'banner_support': None,
                 'banner_step_up': step_up.id},
            ],
            'user_planned_purchase_data': [],
        }, format='json')
        # A readable 400 rather than a 500 from the database constraint.
        self.assertEqual(res.status_code, 400)

    def test_partial_update_keeps_the_existing_target(self):
        # _replace_user_rows patches with partial=True. A body that names only
        # number_of_pulls must not read as "no target provided".
        step_up = make_step_up_banner()
        row = UserPlannedBanner.objects.create(
            user=self.user, banner_step_up=step_up, number_of_pulls=5,
        )
        serializer = UserPlannedBannerSerializer(
            row, data={'number_of_pulls': 10}, partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_planned_step_up_sorts_by_its_own_timeline(self):
        # planned_effective_start has to follow the third FK too; without that
        # branch a step-up row resolves to None and sorts to the front.
        early = make_timeline(
            name='Early',
            global_start_date=_dt(2030, 1, 1), global_end_date=_dt(2030, 1, 20),
        )
        late = make_timeline(
            name='Late',
            global_start_date=_dt(2030, 6, 1), global_end_date=_dt(2030, 6, 20),
        )
        event = make_anniversary_event(name='Campaign', parts=(early, late))
        late_step_up = make_step_up_banner(event=event, timeline=late)

        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=make_uma_banner(timeline=early),
            number_of_pulls=1,
        )
        UserPlannedBanner.objects.create(
            user=self.user, banner_step_up=late_step_up, number_of_pulls=1,
        )

        res = self.client.get('/calculator-data')
        rows = res.data['user_planned_banner_data']
        self.assertEqual(len(rows), 2)
        # The step-up dates off June, so it sorts second -- not first, which is
        # where an unresolved (None) key would put it.
        self.assertIsNotNone(rows[1]['banner_step_up'])


class UserStepUpSelectionTests(CalculatorTestCase):
    """The ten cards a user intends to pick at a step-up, and the rules on them.

    Nothing here should ever affect a projected number -- these tests exist to
    prove the record round-trips and that the constraints hold, not that any
    carat total moved. See UserStepUpSelection's docstring.
    """

    def setUp(self):
        self.user = make_user()
        self.client, _ = auth_client(self.user)
        self.event = make_anniversary_event(
            name='5th Anniversary',
            jp_cutoff_date=datetime.date(2026, 1, 30),
            parts=[make_timeline(name='5th Part 2')],
        )
        part = self.event.banner_links.first().banner_timeline
        self.uma_step_up = make_step_up_banner(
            event=self.event, timeline=part, name='5th ★3 Select Step-Up',
            card_type='uma', banner_count=2,
        )
        self.support_step_up = make_step_up_banner(
            event=self.event, timeline=part, name='5th SSR Select Step-Up',
            card_type='support', banner_count=3,
        )
        # One uma inside the cutoff and one released after it.
        self.eligible_uma = self._uma_first_seen('Kiseki', datetime.datetime(2025, 6, 1))
        self.other_uma = self._uma_first_seen('Buena Vista', datetime.datetime(2025, 8, 1))
        self.late_uma = self._uma_first_seen('Too New', datetime.datetime(2026, 6, 1))
        self.eligible_support = self._support_first_seen(
            'Matikanefukukitaru', 30286, datetime.datetime(2025, 6, 1)
        )

    def _uma_first_seen(self, name, when):
        timeline = make_timeline(
            name=f'{name} debut', jp_start_date=timezone.make_aware(when),
        )
        uma = Uma.objects.create(name=name)
        UmasOnUmaBanner.objects.create(
            uma=uma, banner_uma=make_uma_banner(timeline, name=f'{name} banner')
        )
        return uma

    def _support_first_seen(self, name, game_id, when):
        timeline = make_timeline(
            name=f'{name} debut', jp_start_date=timezone.make_aware(when),
        )
        card = SupportCard.objects.create(name=name, game_id=game_id)
        SupportsOnSupportBanner.objects.create(
            support_card=card,
            banner_support=make_support_banner(timeline, name=f'{name} banner'),
        )
        return card

    def _patch(self, selections):
        return self.client.patch(
            '/calculator-data',
            {'user_step_up_selection_data': selections},
            format='json',
        )

    def _slot(self, **overrides):
        """A valid uma-step-up selection row, id-less as the client sends them."""
        row = {
            'banner_step_up': self.uma_step_up.id,
            'uma': self.eligible_uma.id,
            'slot': 1,
        }
        row.update(overrides)
        return row

    # ── Constraints ──────────────────────────────────────────────────────

    def test_constraint_rejects_a_row_with_no_card(self):
        # An empty slot is an ABSENT row, not a row with both FKs null.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserStepUpSelection.objects.create(
                    user=self.user, banner_step_up=self.uma_step_up, slot=1,
                )

    def test_constraint_rejects_a_row_with_both_cards(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserStepUpSelection.objects.create(
                    user=self.user, banner_step_up=self.uma_step_up, slot=1,
                    uma=self.eligible_uma, support=self.eligible_support,
                )

    def test_constraint_rejects_two_cards_in_one_slot(self):
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserStepUpSelection.objects.create(
                    user=self.user, banner_step_up=self.uma_step_up, slot=1,
                    uma=self.other_uma,
                )

    def test_the_same_slot_is_free_on_a_different_step_up(self):
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma,
        )
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.support_step_up, slot=1,
            support=self.eligible_support,
        )  # must not raise

    def test_the_same_slot_is_free_for_a_different_user(self):
        other = make_user(username='other')
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma,
        )
        UserStepUpSelection.objects.create(
            user=other, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma,
        )  # must not raise

    def test_constraint_rejects_a_second_step_five_target(self):
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma, is_target=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserStepUpSelection.objects.create(
                    user=self.user, banner_step_up=self.uma_step_up, slot=2,
                    uma=self.other_uma, is_target=True,
                )

    def test_many_non_targets_are_unconstrained(self):
        # The target constraint is a PARTIAL index -- is_target=False rows must
        # not collide with each other.
        for slot in range(1, 11):
            UserStepUpSelection.objects.create(
                user=self.user, banner_step_up=self.uma_step_up, slot=slot,
                uma=self.eligible_uma,
            )
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 10)

    def test_clean_rejects_a_support_card_on_an_uma_step_up(self):
        row = UserStepUpSelection(
            user=self.user, banner_step_up=self.uma_step_up, slot=1,
            support=self.eligible_support,
        )
        with self.assertRaises(ValidationError) as ctx:
            row.full_clean()
        self.assertIn('support', ctx.exception.error_dict)

    def test_deleting_a_card_removes_the_selection(self):
        # CASCADE rather than SET_NULL: a nulled FK would leave a row that
        # exactly_one_selection_card forbids. The UI derives empty slots from
        # missing rows, so this still reads as "slot 4 is free".
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.uma_step_up, slot=4,
            uma=self.eligible_uma,
        )
        self.eligible_uma.delete()
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 0)

    # ── API: writing ─────────────────────────────────────────────────────

    def test_patch_creates_selections(self):
        res = self._patch([
            self._slot(slot=1),
            self._slot(slot=2, uma=self.eligible_uma.id, is_target=True),
        ])
        self.assertEqual(res.status_code, 200)
        rows = UserStepUpSelection.objects.filter(user=self.user).order_by('slot')
        self.assertEqual([r.slot for r in rows], [1, 2])
        self.assertEqual([r.is_target for r in rows], [False, True])

    def test_patch_replaces_wholesale_when_rows_carry_no_id(self):
        self._patch([self._slot(slot=1), self._slot(slot=2)])
        res = self._patch([self._slot(slot=7)])
        self.assertEqual(res.status_code, 200)
        rows = UserStepUpSelection.objects.filter(user=self.user)
        self.assertEqual([r.slot for r in rows], [7])

    def test_moving_a_card_between_slots_does_not_trip_the_unique_constraint(self):
        # The reason the client sends id-less rows. With ids this would be a
        # row-by-row update, transiently duplicating slot 2 and 500ing.
        self._patch([
            self._slot(slot=1, uma=self.eligible_uma.id),
            self._slot(slot=2, uma=self.other_uma.id),
        ])
        res = self._patch([
            self._slot(slot=1, uma=self.other_uma.id),
            self._slot(slot=2, uma=self.eligible_uma.id),
        ])
        self.assertEqual(res.status_code, 200)
        by_slot = {
            r.slot: r.uma_id
            for r in UserStepUpSelection.objects.filter(user=self.user)
        }
        self.assertEqual(by_slot, {1: self.other_uma.id, 2: self.eligible_uma.id})

    def test_empty_list_clears_every_selection(self):
        self._patch([self._slot(slot=1)])
        res = self._patch([])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 0)

    def test_absent_key_leaves_selections_alone(self):
        self._patch([self._slot(slot=1)])
        res = self.client.patch(
            '/calculator-data', {'user_stats_data': {'current_carat': 500}},
            format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 1)

    # ── API: validation ──────────────────────────────────────────────────

    def test_rejects_a_support_card_on_an_uma_step_up(self):
        res = self._patch([{
            'banner_step_up': self.uma_step_up.id,
            'support': self.eligible_support.id, 'slot': 1,
        }])
        self.assertEqual(res.status_code, 400)

    def test_rejects_an_uma_on_a_support_step_up(self):
        res = self._patch([{
            'banner_step_up': self.support_step_up.id,
            'uma': self.eligible_uma.id, 'slot': 1,
        }])
        self.assertEqual(res.status_code, 400)

    def test_rejects_a_row_naming_no_card(self):
        res = self._patch([{'banner_step_up': self.uma_step_up.id, 'slot': 1}])
        self.assertEqual(res.status_code, 400)

    def test_rejects_a_slot_outside_one_to_ten(self):
        for bad_slot in (0, 11):
            res = self._patch([self._slot(slot=bad_slot)])
            self.assertEqual(res.status_code, 400, f'slot {bad_slot} should 400')

    def test_rejects_a_card_released_after_the_cutoff(self):
        res = self._patch([self._slot(uma=self.late_uma.id)])
        self.assertEqual(res.status_code, 400)
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 0)

    def test_a_null_cutoff_admits_everything(self):
        self.event.jp_cutoff_date = None
        self.event.save()
        res = self._patch([self._slot(uma=self.late_uma.id)])
        self.assertEqual(res.status_code, 200)

    def test_a_stored_pick_survives_the_cutoff_being_narrowed(self):
        """The lockout guard. Cutoffs are reference data that editors correct as
        real JP dates surface; re-checking untouched picks would 400 the whole
        PATCH -- taking the user's stats and banners with it -- over a change
        they did not make and cannot see.
        """
        self.event.jp_cutoff_date = None
        self.event.save()
        self.assertEqual(self._patch([self._slot(uma=self.late_uma.id)]).status_code, 200)

        # An editor now narrows the cutoff, making that stored pick ineligible.
        self.event.jp_cutoff_date = datetime.date(2026, 1, 30)
        self.event.save()

        res = self._patch([self._slot(uma=self.late_uma.id)])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 1)

    def test_grandfathering_does_not_admit_a_new_ineligible_pick(self):
        # Only what is already stored is forgiven; a fresh late pick is refused.
        self._patch([self._slot(slot=1, uma=self.eligible_uma.id)])
        res = self._patch([
            self._slot(slot=1, uma=self.eligible_uma.id),
            self._slot(slot=2, uma=self.late_uma.id),
        ])
        self.assertEqual(res.status_code, 400)

    def test_rejects_a_time_limited_uma(self):
        self.eligible_uma.is_time_limited = True
        self.eligible_uma.save(update_fields=['is_time_limited'])
        res = self._patch([self._slot(uma=self.eligible_uma.id)])
        self.assertEqual(res.status_code, 400)

    def test_rejects_a_non_three_star_uma(self):
        self.eligible_uma.rarity = 2
        self.eligible_uma.save(update_fields=['rarity'])
        res = self._patch([self._slot(uma=self.eligible_uma.id)])
        self.assertEqual(res.status_code, 400)

    def test_the_intrinsic_gate_survives_a_null_cutoff(self):
        """A null cutoff relaxes the DATE, not the unit's own availability.

        This is the case the old `if cutoff is None: return` early-out let
        straight through -- the whole reason the intrinsic gate could not be
        folded in behind the cutoff check.
        """
        self.event.jp_cutoff_date = None
        self.event.save()
        self.eligible_uma.is_time_limited = True
        self.eligible_uma.save(update_fields=['is_time_limited'])
        res = self._patch([self._slot(uma=self.eligible_uma.id)])
        self.assertEqual(res.status_code, 400)

    def test_a_stored_pick_survives_being_flagged_time_limited(self):
        # Same lockout guard as the cutoff: an editor flagging a unit must not
        # 400 the plans of everyone who already picked it.
        self.assertEqual(
            self._patch([self._slot(uma=self.eligible_uma.id)]).status_code, 200
        )
        self.eligible_uma.is_time_limited = True
        self.eligible_uma.save(update_fields=['is_time_limited'])

        res = self._patch([self._slot(uma=self.eligible_uma.id)])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 1)

    def test_a_rejected_row_rolls_the_whole_patch_back(self):
        self._patch([self._slot(slot=1)])
        res = self.client.patch(
            '/calculator-data',
            {
                'user_stats_data': {'current_carat': 9999},
                'user_step_up_selection_data': [self._slot(uma=self.late_uma.id)],
            },
            format='json',
        )
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.current_carat, 9999)
        # And the pre-existing selection is still there.
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 1)

    # ── API: reading ─────────────────────────────────────────────────────

    def test_calculator_data_serves_the_users_selections(self):
        self._patch([self._slot(slot=3, is_target=True)])
        res = self.client.get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        rows = res.data['user_step_up_selection_data']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['banner_step_up'], self.uma_step_up.id)
        self.assertEqual(rows[0]['uma'], self.eligible_uma.id)
        self.assertEqual(rows[0]['slot'], 3)
        self.assertTrue(rows[0]['is_target'])

    def test_a_guest_gets_an_empty_selection_list(self):
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma,
        )
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['user_step_up_selection_data'], [])

    def test_selections_are_scoped_to_their_owner(self):
        other = make_user(username='other')
        UserStepUpSelection.objects.create(
            user=other, banner_step_up=self.uma_step_up, slot=1,
            uma=self.eligible_uma,
        )
        res = self.client.get('/calculator-data')
        self.assertEqual(res.data['user_step_up_selection_data'], [])

    def test_whole_body_round_trip(self):
        """Every collection in one PATCH, as the client actually saves.

        A repro that omits a collection returns a false 200 and hides a failing
        row, so this sends all of them.
        """
        uma_banner = make_uma_banner(name='Planner Banner')
        res = self.client.patch(
            '/calculator-data',
            {
                'user_stats_data': {'current_paid_carat': 5000},
                'user_planned_banner_data': [
                    {'banner_uma': uma_banner.id, 'number_of_pulls': 30,
                     'reserved_copies': 0},
                ],
                'user_planned_purchase_data': [],
                'user_step_up_selection_data': [
                    self._slot(slot=1),
                    self._slot(slot=2, is_target=True),
                ],
            },
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)

        self.user.refresh_from_db()
        self.assertEqual(self.user.current_paid_carat, 5000)
        self.assertEqual(UserPlannedBanner.objects.filter(user=self.user).count(), 1)
        self.assertEqual(UserStepUpSelection.objects.filter(user=self.user).count(), 2)

        # And it reads back through the same endpoint.
        body = self.client.get('/calculator-data').data
        self.assertEqual(len(body['user_step_up_selection_data']), 2)
        self.assertEqual(len(body['user_planned_banner_data']), 1)
