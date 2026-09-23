"""GET /calculator-data: the payload, the guest-readable reference endpoints, and the cache."""

import datetime
import json

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.predictions import GAME_EVENT_END_DATE_BUFFER
from calculatorapi.models import (
    AnniversaryEventBanner,
    AnniversaryEventProduct,
    BannerStepUp,
    DailyVisit,
    IncomeProfile,
    Plan,
    Uma,
    UserOshi,
    UserPlannedBanner,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_user,
    make_timeline,
    make_uma_banner,
    make_champions_meeting,
    make_league_of_heroes,
    make_game_event,
    make_scenario,
    make_anniversary_event,
    auth_client,
    _dt,
    _predicted,
    _iso,
)


_EXPECTED_GET_KEYS = {
    'club_rank_data', 'team_trials_rank_data', 'champions_meeting_rank_data',
    'league_of_heroes_rank_data', 'banner_uma_data', 'banner_support_data',
    'banner_step_up_data',
    'user_planned_banner_data', 'champions_meeting_data', 'league_of_heroes_event_data',
    'events_data', 'user_stats_data', 'banner_timeline_data',
    'anniversary_event_data', 'scenario_data', 'user_planned_purchase_data',
    'user_step_up_selection_data',
    'user_plans', 'active_plan_id',
    'income_ledger', 'calculation_constants',
}


class CalculatorGetTests(CalculatorTestCase):
    def setUp(self):
        self.user = make_user()
        self.client, self.token = auth_client(self.user)

    def test_unauthenticated_returns_200_with_empty_user_data(self):
        # Guests get the full reference payload; user-scoped keys are empty/null.
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data.keys()), _EXPECTED_GET_KEYS)
        self.assertIsNone(res.data['user_stats_data'])
        self.assertEqual(res.data['user_planned_banner_data'], [])
        self.assertEqual(res.data['user_planned_purchase_data'], [])

    def test_get_with_invalid_token_returns_401(self):
        # TokenAuthentication rejects a present-but-invalid token before
        # permissions run, even under AllowAny. The frontend relies on this
        # to detect a stale token and retry the fetch as a guest.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Token deadbeef')
        res = client.get('/calculator-data')
        self.assertEqual(res.status_code, 401)

    def test_authenticated_returns_200(self):
        res = self.client.get('/calculator-data')
        self.assertEqual(res.status_code, 200)

    def test_response_contains_all_expected_keys(self):
        res = self.client.get('/calculator-data')
        self.assertEqual(set(res.data.keys()), _EXPECTED_GET_KEYS)

    def test_planned_banners_scoped_to_requesting_user(self):
        # This user's banner should appear; the other user's should not.
        uma_banner = make_uma_banner()
        UserPlannedBanner.objects.create(user=self.user, banner_uma=uma_banner, number_of_pulls=5)
        other_user = make_user('otheruser')
        UserPlannedBanner.objects.create(user=other_user, banner_uma=uma_banner, number_of_pulls=10)

        res = self.client.get('/calculator-data')
        self.assertEqual(len(res.data['user_planned_banner_data']), 1)
        self.assertEqual(res.data['user_planned_banner_data'][0]['number_of_pulls'], 5)

    def test_timeline_data_exposes_resolved_and_predicted_fields(self):
        make_timeline(name='Confirmed')  # default: confirmed global banner
        res = self.client.get('/calculator-data')
        entry = res.data['banner_timeline_data'][0]
        for key in ('start_date', 'end_date', 'is_predicted',
                    'jp_start_date', 'global_start_date'):
            self.assertIn(key, entry)
        self.assertFalse(entry['is_predicted'])
        self.assertTrue(entry['start_date'].endswith('Z'))

    def test_predicted_dates_are_consistent_across_all_paths(self):
        # Anchor: confirmed banner with a JP date. Target: JP-only, so predicted.
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted_tl = make_timeline(
            name='Predicted',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        uma_banner = make_uma_banner(timeline=predicted_tl)
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=uma_banner, number_of_pulls=3
        )

        res = self.client.get('/calculator-data')

        expected_start = _iso(_predicted(_dt(2025, 6, 1), 30))

        top = next(t for t in res.data['banner_timeline_data'] if t['id'] == predicted_tl.id)
        self.assertTrue(top['is_predicted'])
        self.assertEqual(top['start_date'], expected_start)

        nested_uma = next(
            b for b in res.data['banner_uma_data']
            if b['banner_timeline']['id'] == predicted_tl.id
        )
        self.assertTrue(nested_uma['banner_timeline']['is_predicted'])
        self.assertEqual(nested_uma['banner_timeline']['start_date'], expected_start)

        planned_tl = res.data['user_planned_banner_data'][0]['banner_uma']['banner_timeline']
        self.assertEqual(planned_tl['start_date'], expected_start)
        self.assertTrue(planned_tl['is_predicted'])

    def test_schedule_offset_is_consistent_across_all_paths(self):
        """Same shape as the prediction-consistency test above: an offset banner
        must report the SAME shifted date at top level, nested in banner_uma_data,
        and two levels deep inside user_planned_banner_data."""
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        offset_tl = make_timeline(
            name='Slipped',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
            schedule_offset_days=7,
        )
        uma_banner = make_uma_banner(timeline=offset_tl)
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=uma_banner, number_of_pulls=3
        )

        res = self.client.get('/calculator-data')

        # Predicted off the anchor, then pushed 7 days by the offset; the 7-day
        # run length is preserved because both ends move together.
        shifted_start = _predicted(_dt(2025, 6, 1), 30, offset_days=7)
        expected_start = _iso(shifted_start)
        expected_end = _iso(shifted_start + datetime.timedelta(days=7))

        top = next(t for t in res.data['banner_timeline_data'] if t['id'] == offset_tl.id)
        self.assertEqual(top['start_date'], expected_start)
        self.assertEqual(top['end_date'], expected_end)
        self.assertTrue(top['is_predicted'])
        self.assertEqual(top['schedule_offset_days'], 7)
        self.assertEqual(top['applied_offset_days'], 7)

        nested_uma = next(
            b for b in res.data['banner_uma_data']
            if b['banner_timeline']['id'] == offset_tl.id
        )
        self.assertEqual(nested_uma['banner_timeline']['start_date'], expected_start)

        planned_tl = res.data['user_planned_banner_data'][0]['banner_uma']['banner_timeline']
        self.assertEqual(planned_tl['start_date'], expected_start)
        self.assertEqual(planned_tl['applied_offset_days'], 7)

    def test_schedule_offset_cascades_to_later_rows_of_every_content_type(self):
        """One shared calendar: a banner offset also pushes later Champions
        Meetings and League of Heroes events, while earlier rows stay put."""
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        # Predicted off the anchor and carrying the +7.
        make_timeline(
            name='Slipped',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
            schedule_offset_days=7,
        )
        # A shorter JP gap puts this BEFORE the slip point, so it stays untouched.
        earlier_tl = make_timeline(
            name='Earlier',
            jp_start_date=_dt(2025, 1, 21), jp_end_date=_dt(2025, 1, 28),
        )
        # CM/LoH each predict off their OWN anchor, both landing after the slip.
        make_champions_meeting(
            name='CM Anchor', cm_number=1,
            jp_start_date=_dt(2025, 2, 1), jp_end_date=_dt(2025, 2, 8),
            global_start_date=_dt(2025, 6, 10), global_end_date=_dt(2025, 6, 17),
        )
        later_cm = make_champions_meeting(
            name='CM Later', cm_number=2,
            jp_start_date=_dt(2025, 3, 3), jp_end_date=_dt(2025, 3, 10),
        )
        make_league_of_heroes(
            name='LoH Anchor',
            jp_start_date=_dt(2025, 2, 1), jp_end_date=_dt(2025, 2, 8),
            global_start_date=_dt(2025, 6, 10), global_end_date=_dt(2025, 6, 17),
        )
        later_loh = make_league_of_heroes(
            name='LoH Later',
            jp_start_date=_dt(2025, 3, 3), jp_end_date=_dt(2025, 3, 10),
        )

        res = self.client.get('/calculator-data')

        earlier = next(t for t in res.data['banner_timeline_data'] if t['id'] == earlier_tl.id)
        self.assertEqual(earlier['start_date'], _iso(_predicted(_dt(2025, 6, 1), 20)))
        self.assertEqual(earlier['applied_offset_days'], 0)

        # Both predict off their own anchors, land after the slip, inherit the +7.
        inherited = _iso(_predicted(_dt(2025, 6, 10), 30, offset_days=7))

        cm = next(c for c in res.data['champions_meeting_data'] if c['id'] == later_cm.id)
        self.assertEqual(cm['start_date'], inherited)
        self.assertEqual(cm['applied_offset_days'], 7)

        loh = next(e for e in res.data['league_of_heroes_event_data'] if e['id'] == later_loh.id)
        self.assertEqual(loh['start_date'], inherited)
        self.assertEqual(loh['applied_offset_days'], 7)

    def test_confirmed_row_ignores_its_own_offset(self):
        """A confirmed date is a fact — the offset must not move it, and must
        not cascade either (otherwise it double-counts once it anchors)."""
        confirmed_tl = make_timeline(
            name='Confirmed but offset',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
            schedule_offset_days=7,
        )
        later_tl = make_timeline(
            name='Later',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )

        res = self.client.get('/calculator-data')

        confirmed = next(t for t in res.data['banner_timeline_data'] if t['id'] == confirmed_tl.id)
        self.assertEqual(confirmed['start_date'], '2025-06-01T00:00:00Z')
        self.assertEqual(confirmed['applied_offset_days'], 0)

        later = next(t for t in res.data['banner_timeline_data'] if t['id'] == later_tl.id)
        self.assertEqual(later['start_date'], _iso(_predicted(_dt(2025, 6, 1), 30)))
        self.assertEqual(later['applied_offset_days'], 0)

    def test_game_event_inherits_its_banners_offset_plus_the_buffer(self):
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        offset_tl = make_timeline(
            name='Slipped',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
            schedule_offset_days=7,
        )
        event = make_game_event(name='Slipped Event', banner_timeline=offset_tl)

        res = self.client.get('/calculator-data')
        entry = next(e for e in res.data['events_data'] if e['id'] == event.id)

        # The banner's 7-day run shifts whole by the offset; the event's end
        # still trails it by the usual 4-day buffer.
        shifted_start = _predicted(_dt(2025, 6, 1), 30, offset_days=7)
        self.assertEqual(entry['start_date'], _iso(shifted_start))
        self.assertEqual(entry['end_date'], _iso(
            shifted_start + datetime.timedelta(days=7) + GAME_EVENT_END_DATE_BUFFER))
        self.assertEqual(entry['applied_offset_days'], 7)

    def test_champions_meeting_exposes_resolved_and_predicted_fields(self):
        make_champions_meeting(name='Confirmed CM')  # default: confirmed global
        res = self.client.get('/calculator-data')
        entry = res.data['champions_meeting_data'][0]
        for key in ('start_date', 'end_date', 'is_predicted',
                    'jp_start_date', 'global_start_date'):
            self.assertIn(key, entry)
        self.assertFalse(entry['is_predicted'])
        self.assertTrue(entry['start_date'].endswith('Z'))

    def test_league_of_heroes_exposes_resolved_and_predicted_fields(self):
        make_league_of_heroes(name='Confirmed LoH')  # default: confirmed global
        res = self.client.get('/calculator-data')
        entry = res.data['league_of_heroes_event_data'][0]
        for key in ('start_date', 'end_date', 'is_predicted',
                    'jp_start_date', 'global_start_date'):
            self.assertIn(key, entry)
        self.assertFalse(entry['is_predicted'])
        self.assertTrue(entry['start_date'].endswith('Z'))

    def test_champions_meeting_predicts_from_jp_when_global_unconfirmed(self):
        # Anchor: confirmed CM with a JP date. Target: JP-only, so predicted.
        make_champions_meeting(
            name='Anchor CM', cm_number=1,
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted = make_champions_meeting(
            name='Predicted CM', cm_number=2,
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        res = self.client.get('/calculator-data')
        entry = next(c for c in res.data['champions_meeting_data'] if c['id'] == predicted.id)
        self.assertTrue(entry['is_predicted'])
        self.assertEqual(entry['start_date'], _iso(_predicted(_dt(2025, 6, 1), 30)))

    def test_league_of_heroes_predicts_from_jp_when_global_unconfirmed(self):
        make_league_of_heroes(
            name='Anchor LoH',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted = make_league_of_heroes(
            name='Predicted LoH',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        res = self.client.get('/calculator-data')
        entry = next(l for l in res.data['league_of_heroes_event_data'] if l['id'] == predicted.id)
        self.assertTrue(entry['is_predicted'])
        self.assertEqual(entry['start_date'], _iso(_predicted(_dt(2025, 6, 1), 30)))

    def test_cm_and_loh_predictions_use_separate_anchors(self):
        # A confirmed CM must NOT act as an anchor for LoH prediction (and vice
        # versa) — each content type resolves against its own map.
        make_champions_meeting(
            name='CM Anchor', cm_number=1,
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        # LoH with only a JP date and NO confirmed LoH anchor -> unresolved,
        # not predicted off the CM anchor.
        loh = make_league_of_heroes(
            name='LoH JP only',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        res = self.client.get('/calculator-data')
        entry = next(l for l in res.data['league_of_heroes_event_data'] if l['id'] == loh.id)
        self.assertFalse(entry['is_predicted'])
        self.assertIsNone(entry['start_date'])

    def test_game_event_exposes_resolved_and_predicted_fields(self):
        timeline = make_timeline(
            name='Confirmed Banner',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        event = make_game_event(
            name='Linked Event', banner_timeline=timeline,
            carat_amount=80, carats_throughout=1050,
        )
        res = self.client.get('/calculator-data')
        entry = next(e for e in res.data['events_data'] if e['id'] == event.id)
        for key in ('start_date', 'end_date', 'is_predicted', 'banner_timeline'):
            self.assertIn(key, entry)
        self.assertEqual(entry['start_date'], '2025-06-01T00:00:00Z')
        # end_date trails the banner's own end_date by GAME_EVENT_END_DATE_BUFFER (4 days).
        self.assertEqual(entry['end_date'], '2025-06-12T00:00:00Z')
        self.assertFalse(entry['is_predicted'])
        self.assertEqual(entry['banner_timeline'], timeline.id)
        self.assertEqual(entry['carat_amount'], 80)
        self.assertEqual(entry['carats_throughout'], 1050)

    def test_game_event_predicts_via_linked_banner_timeline(self):
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted_tl = make_timeline(
            name='Predicted',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        event = make_game_event(name='Predicted Event', banner_timeline=predicted_tl)
        res = self.client.get('/calculator-data')
        entry = next(e for e in res.data['events_data'] if e['id'] == event.id)
        # Banner runs 7d from its predicted start, then the event's +4d buffer.
        predicted_start = _predicted(_dt(2025, 6, 1), 30)
        self.assertTrue(entry['is_predicted'])
        self.assertEqual(entry['start_date'], _iso(predicted_start))
        self.assertEqual(entry['end_date'], _iso(
            predicted_start + datetime.timedelta(days=7) + GAME_EVENT_END_DATE_BUFFER))

    def test_game_event_with_no_banner_timeline_resolves_null_dates(self):
        event = make_game_event(name='Unlinked Event', banner_timeline=None)
        res = self.client.get('/calculator-data')
        entry = next(e for e in res.data['events_data'] if e['id'] == event.id)
        self.assertIsNone(entry['start_date'])
        self.assertIsNone(entry['end_date'])
        self.assertFalse(entry['is_predicted'])
        self.assertIsNone(entry['banner_timeline'])


class ReferenceEndpointGuestAccessTests(CalculatorTestCase):
    """Read-only reference endpoints are open to guests."""

    def test_reference_reads_return_200_for_guests(self):
        client = APIClient()
        for url in (
            '/clubranks', '/teamtrialranks', '/championsmeetingranks',
            '/leagueofheroesranks', '/leagueofheroes', '/events',
        ):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_event_writes_still_require_admin(self):
        # Guests (and non-admin users) must not be able to write reference data.
        res = APIClient().post('/events', {'name': 'x'})
        self.assertIn(res.status_code, (401, 403))

    def test_events_endpoint_serves_confirmed_only_no_prediction(self):
        # The standalone /events route must never predict -- a JP-only banner
        # should show null dates here, even though the same event predicts
        # through /calculator-data (test_game_event_predicts_via_linked_banner_timeline).
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        predicted_tl = make_timeline(
            name='Predicted',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        event = make_game_event(name='Predicted Event', banner_timeline=predicted_tl)
        res = APIClient().get('/events')
        entry = next(e for e in res.data if e['id'] == event.id)
        self.assertIsNone(entry['start_date'])
        self.assertIsNone(entry['end_date'])
        self.assertFalse(entry['is_predicted'])

    def test_standalone_routes_are_unaffected_by_schedule_offsets(self):
        """Offsets ride on top of predictions, and the standalone routes serve
        confirmed dates only — so they must not shift. Same two-tier split as
        test_events_endpoint_serves_confirmed_only_no_prediction above."""
        confirmed_tl = make_timeline(
            name='Confirmed',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
            schedule_offset_days=7,
        )
        event = make_game_event(name='Confirmed Event', banner_timeline=confirmed_tl)
        loh = make_league_of_heroes(
            name='Confirmed LoH',
            global_start_date=_dt(2025, 6, 10), global_end_date=_dt(2025, 6, 17),
            schedule_offset_days=7,
        )

        events_res = APIClient().get('/events')
        entry = next(e for e in events_res.data if e['id'] == event.id)
        self.assertEqual(entry['start_date'], '2025-06-01T00:00:00Z')
        self.assertEqual(entry['applied_offset_days'], 0)

        loh_res = APIClient().get('/leagueofheroes')
        loh_entry = next(e for e in loh_res.data if e['id'] == loh.id)
        self.assertEqual(loh_entry['start_date'], '2025-06-10T00:00:00Z')
        self.assertEqual(loh_entry['applied_offset_days'], 0)


class ScenarioApiTests(CalculatorTestCase):
    """/calculator-data serves scenarios, start-only and image-optional."""

    def setUp(self):
        self.client = APIClient()

    def test_scenario_payload_has_a_start_and_no_end_date_key_at_all(self):
        now = timezone.now()
        banner = make_timeline(
            name='Launch banner', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=14),
        )
        scenario = make_scenario(name='Hashire! Mecha Umamusume',
                                 banner_timeline=banner)

        response = self.client.get('/calculator-data')
        row = next(
            r for r in response.data['scenario_data'] if r['id'] == scenario.id
        )

        self.assertEqual(row['name'], 'Hashire! Mecha Umamusume')
        self.assertIsNotNone(row['start_date'])
        # Absent, not null. A structurally-always-null end_date would invite a
        # consumer to render a range that does not exist -- see
        # StartInstantDateMixin.
        self.assertNotIn('end_date', row)

    def test_banner_timeline_is_emitted_as_a_bare_id_for_band_pinning(self):
        now = timezone.now()
        banner = make_timeline(name='Launch banner', global_start_date=now)
        scenario = make_scenario(banner_timeline=banner)

        response = self.client.get('/calculator-data')
        row = next(
            r for r in response.data['scenario_data'] if r['id'] == scenario.id
        )

        # The frontend pins the scenario's band directly above this banner's
        # planner row, so it needs the id rather than a nested banner.
        self.assertEqual(row['banner_timeline'], banner.id)

    def test_scenario_without_an_image_still_serves(self):
        # The normal state while a scenario is being entered -- art lands later.
        now = timezone.now()
        banner = make_timeline(name='Launch banner', global_start_date=now)
        scenario = make_scenario(banner_timeline=banner, image=None)

        response = self.client.get('/calculator-data')
        row = next(
            r for r in response.data['scenario_data'] if r['id'] == scenario.id
        )

        self.assertIsNone(row['image'])
        self.assertIsNotNone(row['start_date'])

    def test_unlinked_scenario_serves_with_a_null_start(self):
        scenario = make_scenario(banner_timeline=None)

        response = self.client.get('/calculator-data')
        row = next(
            r for r in response.data['scenario_data'] if r['id'] == scenario.id
        )

        self.assertIsNone(row['start_date'])

    def test_guests_can_read_scenarios(self):
        now = timezone.now()
        banner = make_timeline(name='Launch banner', global_start_date=now)
        make_scenario(banner_timeline=banner)

        response = self.client.get('/calculator-data')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['scenario_data']), 1)


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

    def test_banner_carries_its_campaign_and_part_number(self):
        res = self.client.get('/calculator-data')
        timeline = next(
            t for t in res.data['banner_timeline_data'] if t['id'] == self.timeline.id
        )
        self.assertEqual(timeline['anniversary_event']['name'], '3rd Anniversary')
        self.assertEqual(timeline['anniversary_event']['part_number'], 1)

    def test_unattached_banner_reports_no_campaign(self):
        loose = make_timeline(name='Ordinary banner')
        res = self.client.get('/calculator-data')
        timeline = next(
            t for t in res.data['banner_timeline_data'] if t['id'] == loose.id
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
            t for t in res.data['banner_timeline_data'] if t['id'] == self.timeline.id
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
            t for t in res.data['banner_timeline_data'] if t['id'] == loose.id
        )
        self.assertEqual(timeline['banner_step_ups'], [])

    def test_campaigns_are_public(self):
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['anniversary_event_data']), 1)


class PublicPayloadCacheTests(CalculatorTestCase):
    """The server-side cache behind GET /calculator-data.

    -> calculatorapi/public_payload_cache.py
    """

    def setUp(self):
        # Every case starts on a miss: CalculatorTestCase empties the cache
        # before each test.
        self.user = make_user()
        self.client, self.token = auth_client(self.user)
        self.timeline = make_timeline(name='Cached Banner')
        self.uma_banner = make_uma_banner(timeline=self.timeline)

    def test_first_request_populates_the_cache(self):
        self.assertIsNone(public_payload_cache.read())
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(public_payload_cache.read())

    def test_second_guest_request_costs_no_queries(self):
        APIClient().get('/calculator-data')
        # The whole point: a warm cache answers a guest without touching the DB.
        with self.assertNumQueries(0):
            res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data.keys()), _EXPECTED_GET_KEYS)

    def test_cached_response_matches_an_uncached_one(self):
        cold = APIClient().get('/calculator-data').data
        warm = APIClient().get('/calculator-data').data
        self.assertEqual(json.dumps(cold, default=str),
                         json.dumps(warm, default=str))

    def test_content_write_invalidates(self):
        APIClient().get('/calculator-data')
        self.assertIsNotNone(public_payload_cache.read())
        make_timeline(name='Newly Added')
        self.assertIsNone(public_payload_cache.read())

    def test_content_delete_invalidates(self):
        APIClient().get('/calculator-data')
        self.timeline.delete()
        self.assertIsNone(public_payload_cache.read())

    def test_a_new_banner_shows_up_on_the_next_request(self):
        # The invalidation the content editor actually cares about.
        first = APIClient().get('/calculator-data')
        names = [t['name'] for t in first.data['banner_timeline_data']]
        self.assertNotIn('Second Banner', names)

        make_timeline(name='Second Banner')

        second = APIClient().get('/calculator-data')
        names = [t['name'] for t in second.data['banner_timeline_data']]
        self.assertIn('Second Banner', names)

    def test_a_visit_row_does_not_invalidate(self):
        # Visits are written on EVERY page view. If they invalidated, the cache
        # would be cleared continuously and never serve anything.
        APIClient().get('/calculator-data')
        DailyVisit.objects.create(
            date=timezone.localdate(), page_views=1, unique_visitors=1)
        self.assertIsNotNone(public_payload_cache.read())

    def test_a_user_plan_write_does_not_invalidate(self):
        # User-scoped rows are never part of the cached half, so saving a plan
        # must not throw away the catalogue for everyone else.
        APIClient().get('/calculator-data')
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=self.uma_banner, number_of_pulls=5)
        self.assertIsNotNone(public_payload_cache.read())

    def test_a_user_save_does_not_invalidate(self):
        # Every auto-save touches Plan.updated_at, an account may create an
        # IncomeProfile, and a supporter picks oshis. None of that is in the
        # cached half, so none of it may drop the catalogue. On 2026-09-23 Plan
        # and IncomeProfile were missing from the denylist: each save under a
        # new banner's crowd forced a ~2s rebuild on the one worker, and /app
        # spun forever behind the queue. -> public_payload_cache._IRRELEVANT_MODELS
        oshi_uma = Uma.objects.create(name='Oshi Week')   # catalogue: before the warm-up
        APIClient().get('/calculator-data')

        plan = Plan.objects.create(user=self.user, name='Main')
        plan.save(update_fields=['updated_at'])   # what views/calculator.py does
        self.assertIsNotNone(public_payload_cache.read(), 'a plan save dropped the cache')

        profile = IncomeProfile.objects.create(user=self.user)
        plan.income_profile = profile
        plan.save(update_fields=['income_profile', 'updated_at'])
        self.assertIsNotNone(public_payload_cache.read(), 'an income profile dropped the cache')

        UserOshi.objects.create(user=self.user, uma=oshi_uma, position=0)
        self.assertIsNotNone(public_payload_cache.read(), 'an oshi pick dropped the cache')

        plan.delete()
        self.assertIsNotNone(public_payload_cache.read(), 'a plan delete dropped the cache')

    def test_signed_in_rows_are_never_served_to_a_guest(self):
        """The one that matters: no user's data may reach the shared cache."""
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=self.uma_banner, number_of_pulls=5)
        # The SIGNED-IN request populates the cache first.
        cache.clear()
        signed_in = self.client.get('/calculator-data')
        self.assertEqual(len(signed_in.data['user_planned_banner_data']), 1)

        guest = APIClient().get('/calculator-data')
        self.assertIsNone(guest.data['user_stats_data'])
        self.assertEqual(guest.data['user_planned_banner_data'], [])
        self.assertEqual(guest.data['user_planned_purchase_data'], [])
        self.assertEqual(guest.data['user_step_up_selection_data'], [])

    def test_signed_in_rows_merge_over_a_guest_cached_payload(self):
        # The reverse order: a guest warms the cache, then a signed-in user must
        # still get their own rows merged in rather than the guest's empties.
        APIClient().get('/calculator-data')
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=self.uma_banner, number_of_pulls=7)

        res = self.client.get('/calculator-data')
        self.assertEqual(len(res.data['user_planned_banner_data']), 1)
        self.assertEqual(
            res.data['user_planned_banner_data'][0]['number_of_pulls'], 7)
        self.assertIsNotNone(res.data['user_stats_data'])
        # ...and the catalogue still came through with it.
        self.assertTrue(res.data['banner_timeline_data'])

    def test_one_user_never_sees_another_users_rows_from_the_cache(self):
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=self.uma_banner, number_of_pulls=5)
        self.client.get('/calculator-data')

        other = make_user('otheruser')
        other_client, _ = auth_client(other)
        res = other_client.get('/calculator-data')
        self.assertEqual(res.data['user_planned_banner_data'], [])

    def test_cached_payload_carries_every_expected_key(self):
        # A key missing from the cached half would only show up for guests.
        APIClient().get('/calculator-data')
        cached = json.loads(public_payload_cache.read())
        self.assertEqual(set(cached.keys()), _EXPECTED_GET_KEYS)
