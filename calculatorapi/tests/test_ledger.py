"""The dated income ledger the projection reads, and the calculation constants behind it."""

import datetime
from io import StringIO

from django.contrib.auth.models import Group
from django.db import models
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi.ledger import AMOUNT_FIELDS, build_income_ledger
from calculatorapi.views.ledger import IncomeLedgerRowSerializer
from calculatorapi.views.calculation_constants import CalculationConstantsSerializer
from calculatorapi.predictions import (
    PREDICTION_FACTOR,
    GAME_EVENT_END_DATE_BUFFER,
    build_game_event_date_map,
    compute_effective_dates,
    build_effective_date_map,
    build_effective_date_maps,
)
from calculatorapi.models import (
    CustomUser,
    BannerTimeline,
    ChampionsMeeting, LeagueOfHeroes, GameEvent,
    CalculationConstants,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    make_user,
    make_timeline,
    make_champions_meeting,
    make_league_of_heroes,
    make_game_event,
    _dt,
    _predicted,
    _iso,
)


class CalculationConstantsTests(CalculatorTestCase):
    """The singleton holding every tunable number the projection uses."""

    def test_load_creates_one_row_and_returns_it_thereafter(self):
        first = CalculationConstants.load()
        second = CalculationConstants.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CalculationConstants.objects.count(), 1)

    def test_saving_a_second_instance_overwrites_the_first(self):
        # save() pins the pk, so a second row cannot exist even from the shell.
        CalculationConstants.load()
        CalculationConstants(pull_cost_carats=999).save()
        self.assertEqual(CalculationConstants.objects.count(), 1)
        self.assertEqual(CalculationConstants.load().pull_cost_carats, 999)

    def test_delete_is_refused(self):
        constants = CalculationConstants.load()
        constants.delete()
        self.assertEqual(CalculationConstants.objects.count(), 1)

    def test_defaults_match_the_current_calibration(self):
        # A fresh database must start correct rather than zeroed.
        constants = CalculationConstants.load()
        self.assertEqual(constants.daily_base_carats, 75)
        self.assertEqual(constants.weekly_bonus_carats, 150)
        self.assertEqual(constants.pull_cost_carats, 150)
        # 1800/month = the 60/day the projection currently uses. The sheet says
        # 3000; closing that gap is a deliberate parity change, not a default.
        self.assertEqual(constants.misc_earnings_monthly, 1800)
        # Paid tier only — the free tier earns no shards from the pass.
        self.assertEqual(constants.training_pass_paid_ssr_shards, 1)
        self.assertEqual(float(constants.prediction_factor), PREDICTION_FACTOR)
        self.assertEqual(
            constants.game_event_end_buffer_days, GAME_EVENT_END_DATE_BUFFER.days
        )

    def test_endpoint_serves_the_constants_as_numbers(self):
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        payload = res.data['calculation_constants']
        self.assertEqual(payload['daily_base_carats'], 75)
        # Decimals must arrive as numbers, not DRF's default strings: the client
        # multiplies by them, and "0.664" * 2 is a silent NaN in JavaScript.
        self.assertIsInstance(payload['prediction_factor'], float)
        self.assertIsInstance(payload['throughout_decay_k'], float)
        self.assertNotIn('id', payload)

    def test_edited_value_reaches_the_endpoint(self):
        constants = CalculationConstants.load()
        constants.misc_earnings_monthly = 4200
        constants.save()
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.data['calculation_constants']['misc_earnings_monthly'], 4200)

    def test_prediction_factor_drives_predicted_dates(self):
        # The whole point of making it editable: changing it must move the dates
        # /calculator-data serves, with no deploy.
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_timeline(
            name='Predicted',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
        )
        constants = CalculationConstants.load()
        constants.prediction_factor = '1.000'
        constants.save()

        emap = build_effective_date_map()
        predicted = next(e for e in emap.values() if e['is_predicted'])
        # At a factor of 1.0 the 30-day JP gap maps to a 30-day global gap,
        # landing on that day's reset.
        self.assertEqual(predicted['start_date'], _dt(2025, 7, 1, 22, 0, 0))

    def test_game_event_buffer_is_configurable(self):
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_game_event(banner_timeline=timeline, carat_amount=100)
        constants = CalculationConstants.load()
        constants.game_event_end_buffer_days = 9
        constants.save()

        events = GameEvent.objects.select_related('banner_timeline').all()
        emap = build_game_event_date_map(events, build_effective_date_map())
        entry = next(iter(emap.values()))
        self.assertEqual(entry['end_date'], _dt(2025, 6, 8) + datetime.timedelta(days=9))

    def test_pure_functions_still_run_without_the_database_value(self):
        # compute_effective_dates keeps a module-constant default so it stays
        # DB-free for direct unit tests.
        rows = [{
            'id': 1,
            'jp_start_date': _dt(2025, 1, 1), 'jp_end_date': _dt(2025, 1, 8),
            'global_start_date': _dt(2025, 6, 1), 'global_end_date': _dt(2025, 6, 8),
            'schedule_offset_days': 0,
        }]
        out = compute_effective_dates(rows)
        self.assertEqual(out[1]['start_date'], _dt(2025, 6, 1))


class CalculationConstantsSerializerTests(CalculatorTestCase):
    def test_every_decimal_constant_serializes_as_a_number(self):
        """No constant may reach the client as a decimal STRING.

        DRF serializes DecimalField as a string by default, and the client feeds
        these straight into arithmetic where `"0.003" * 500` is a silent NaN
        rather than an error. Each such field therefore needs an explicit
        FloatField on the serializer — easy to forget when adding one, and
        invisible until a number goes wrong somewhere unrelated. This walks the
        model instead of naming fields, so it covers constants added later.
        """
        data = CalculationConstantsSerializer(CalculationConstants.load()).data
        decimal_fields = [
            field.name for field in CalculationConstants._meta.get_fields()
            if isinstance(field, models.DecimalField)
        ]
        self.assertTrue(decimal_fields, "expected some DecimalField constants")

        stringly = [name for name in decimal_fields if isinstance(data[name], str)]
        self.assertEqual(
            stringly, [],
            f"these reach the client as strings; add serializers.FloatField(): {stringly}",
        )


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class CalculationConstantsAdminTests(CalculatorTestCase):
    """The singleton admin page. Its list → add → edit flow is deliberately
    non-standard, and a broken fieldset only surfaces on render."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = CustomUser.objects.create_superuser(username='boss', password='x')

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_changelist_redirects_straight_to_the_form(self):
        res = self.client.get(reverse('admin:calculatorapi_calculationconstants_changelist'))
        self.assertEqual(res.status_code, 302)
        constants = CalculationConstants.load()
        self.assertEqual(
            res.url,
            reverse('admin:calculatorapi_calculationconstants_change',
                    args=(constants.pk,)),
        )

    def test_change_form_renders_every_fieldset(self):
        # A field named in `fieldsets` that doesn't exist on the model raises
        # here rather than at import time.
        constants = CalculationConstants.load()
        res = self.client.get(
            reverse('admin:calculatorapi_calculationconstants_change',
                    args=(constants.pk,))
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Daily income')
        self.assertContains(res, 'Global date prediction')

    def test_add_is_refused_once_the_row_exists(self):
        CalculationConstants.load()
        res = self.client.get(reverse('admin:calculatorapi_calculationconstants_add'))
        self.assertEqual(res.status_code, 403)

    def test_delete_is_refused(self):
        constants = CalculationConstants.load()
        res = self.client.get(
            reverse('admin:calculatorapi_calculationconstants_delete',
                    args=(constants.pk,))
        )
        self.assertEqual(res.status_code, 403)

    def test_content_editors_get_no_permission_on_it(self):
        # It is left out of CONTENT_MODELS on purpose: these numbers move every
        # user's projection, and some of them move banner dates.
        call_command('create_content_editor_group', stdout=StringIO())
        editor = make_user(username='editor', is_staff=True)
        editor.groups.add(Group.objects.get(name='Content editors'))
        self.assertFalse(editor.has_perm('calculatorapi.change_calculationconstants'))


class LedgerTests(CalculatorTestCase):
    """build_income_ledger: the flat dated timeline the projection queries.

    It computes no income — it places rows on the calendar. So these pin dates,
    ordering, and which rows exist at all; the amounts only need to survive the
    trip intact."""

    def _one_row(self):
        """The ledger's single row, asserting there is exactly one.

        Preferred over `row, = self._ledger()`: a wrong count fails as a
        readable assertion rather than a ValueError, and the expected count
        becomes part of what each test states.
        """
        rows = self._ledger()
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _ledger(self):
        """Build the ledger the same way /calculator-data does."""
        date_maps = build_effective_date_maps()
        emap = date_maps[BannerTimeline]
        events = GameEvent.objects.select_related('banner_timeline').all()
        return build_income_ledger(
            game_events=events,
            game_event_emap=build_game_event_date_map(events, emap),
            race_sources=(
                ('champions_meeting', ChampionsMeeting.objects.all(),
                 date_maps[ChampionsMeeting]),
                ('league_of_heroes', LeagueOfHeroes.objects.all(),
                 date_maps[LeagueOfHeroes]),
            ),
        )

    def test_event_row_is_dated_at_its_banner_start_and_carries_amounts(self):
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_game_event(banner_timeline=timeline, carat_amount=1200,
                        uma_ticket_amount=3, ssr_shard_amount=5)
        row = self._one_row()
        self.assertEqual(row['kind'], 'event')
        self.assertEqual(row['date'], _dt(2025, 6, 1))
        self.assertEqual(row['carats'], 1200)
        self.assertEqual(row['uma_tickets'], 3)
        self.assertEqual(row['ssr_shards'], 5)
        # Untouched amounts are present as zeros, never absent.
        self.assertEqual(row['sr_crystals'], 0)

    def test_throughout_end_is_the_banner_end_not_the_event_end(self):
        # The event's own resolved end trails its banner by the buffer, but the
        # decay curve runs over the BANNER. Emitting it pre-stripped is what
        # stops the client re-deriving it from its own copy of the constant.
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_game_event(banner_timeline=timeline, carats_throughout=900)
        row = self._one_row()
        self.assertEqual(row['carats_throughout'], 900)
        self.assertEqual(row['throughout_end'], _dt(2025, 6, 8))

    def test_all_zero_event_is_omitted(self):
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_game_event(banner_timeline=timeline)
        self.assertEqual(self._ledger(), [])

    def test_undated_rows_are_omitted(self):
        # An unlinked event and a race event with no resolvable dates have no
        # position on the calendar, so they are dropped rather than emitted null.
        make_game_event(banner_timeline=None, carat_amount=500)
        make_champions_meeting(name='No dates', global_start_date=None,
                               global_end_date=None,
                               jp_start_date=_dt(2025, 1, 1),
                               jp_end_date=_dt(2025, 1, 8))
        self.assertEqual(self._ledger(), [])

    def test_race_rows_are_dated_at_end_less_lead_time_and_carry_no_amounts(self):
        # A Champions Meeting settles its placements at the daily reset a day
        # before its window closes (end less 23:59:59); League of Heroes has no
        # lead time. The gap between the two is the only place these otherwise
        # identical kinds diverge, so it is asserted side by side rather than
        # in separate tests.
        make_champions_meeting(
            name='CM', global_start_date=_dt(2025, 6, 1),
            global_end_date=_dt(2025, 6, 8),
        )
        make_league_of_heroes(
            name='LoH', global_start_date=_dt(2025, 7, 1),
            global_end_date=_dt(2025, 7, 8),
        )
        rows = self._ledger()
        self.assertEqual(len(rows), 2)
        cm, loh = rows[0], rows[1]
        self.assertEqual((cm['kind'], cm['date']),
                         ('champions_meeting', _dt(2025, 6, 7, 0, 0, 1)))
        self.assertEqual((loh['kind'], loh['date']),
                         ('league_of_heroes', _dt(2025, 7, 8)))
        # Amounts stay zero: what a placement pays depends on the user's rank.
        for row in (cm, loh):
            self.assertEqual(row['carats'], 0)
            self.assertEqual(row['uma_tickets'], 0)

    def test_race_rows_carry_their_event_number(self):
        # The client caps the rank a few specific events pay at (League of
        # Heroes #1 only ran to Platinum 1), so a race row has to say WHICH
        # event it is. Game events have no such number; they carry null, so the
        # field is still present on every row.
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 5, 1), global_end_date=_dt(2025, 5, 8),
        )
        make_game_event(banner_timeline=timeline, carat_amount=100)
        make_champions_meeting(
            name='CM', cm_number=12, global_start_date=_dt(2025, 6, 1),
            global_end_date=_dt(2025, 6, 8),
        )
        make_league_of_heroes(
            name='LoH', loh_number=3, global_start_date=_dt(2025, 7, 1),
            global_end_date=_dt(2025, 7, 8),
        )
        numbers = {row['kind']: row['event_number'] for row in self._ledger()}
        self.assertEqual(numbers, {
            'event': None, 'champions_meeting': 12, 'league_of_heroes': 3,
        })

    def test_cm_lead_time_keeps_the_time_of_day(self):
        # Confirmed global windows run 22:00 -> 21:59:59, not midnight to
        # midnight. The lead time is a timedelta off the resolved end, so it
        # shifts the whole instant and must NOT truncate to a date — a CM
        # closing at 21:59:59 settles at the reset the previous day, 22:00:00.
        # Not 21:59:59: that is a banner's closing second, and the client's
        # `date <= end` gate would then credit a CM ending the day after a
        # banner to that banner, one minute before the rewards exist.
        make_champions_meeting(
            name='CM', global_start_date=_dt(2025, 6, 1, 22, 0, 0),
            global_end_date=_dt(2025, 6, 8, 21, 59, 59),
        )
        self.assertEqual(self._one_row()['date'], _dt(2025, 6, 7, 22, 0, 0))

    def test_past_events_are_included(self):
        # Deliberate: the ledger is a set of dated facts with no "as of today"
        # gate. The sheet bakes one into its CM/LoH columns; we apply it
        # client-side instead so the whole calculation shares one anchor.
        make_champions_meeting(
            name='Long gone', global_start_date=_dt(2020, 1, 1),
            global_end_date=_dt(2020, 1, 8),
        )
        row = self._one_row()
        self.assertEqual(row['date'], _dt(2020, 1, 7, 0, 0, 1))

    def test_rows_are_sorted_by_date(self):
        late = make_timeline(name='Late', global_start_date=_dt(2025, 9, 1),
                             global_end_date=_dt(2025, 9, 8))
        early = make_timeline(name='Early', global_start_date=_dt(2025, 3, 1),
                              global_end_date=_dt(2025, 3, 8))
        make_game_event(name='Late event', banner_timeline=late, carat_amount=1)
        make_game_event(name='Early event', banner_timeline=early, carat_amount=1)
        make_champions_meeting(name='Mid', global_start_date=_dt(2025, 6, 1),
                               global_end_date=_dt(2025, 6, 8))
        self.assertEqual(
            [row['name'] for row in self._ledger()],
            ['Early event', 'Mid', 'Late event'],
        )

    def test_schedule_offset_moves_ledger_dates(self):
        # The offset is applied while the date maps are built, so the ledger
        # inherits it without knowing it exists.
        make_timeline(
            name='Anchor',
            jp_start_date=_dt(2025, 1, 1), jp_end_date=_dt(2025, 1, 8),
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        delayed = make_timeline(
            name='Delayed',
            jp_start_date=_dt(2025, 1, 31), jp_end_date=_dt(2025, 2, 7),
            schedule_offset_days=5,
        )
        make_game_event(banner_timeline=delayed, carat_amount=100)
        row = self._one_row()
        self.assertEqual(row['date'], _predicted(_dt(2025, 6, 1), 30, offset_days=5))
        self.assertTrue(row['is_predicted'])


class LedgerSerializerTests(CalculatorTestCase):
    def test_serializer_emits_every_amount_field(self):
        # ledger.AMOUNT_FIELDS is what fills the zeros on every row; the
        # serializer is what puts them on the wire. If one grows a field the
        # other doesn't, the client silently reads undefined.
        declared = set(IncomeLedgerRowSerializer().get_fields())
        self.assertTrue(set(AMOUNT_FIELDS).issubset(declared))

    def test_endpoint_serializes_ledger_rows(self):
        timeline = make_timeline(
            name='Confirmed',
            global_start_date=_dt(2025, 6, 1), global_end_date=_dt(2025, 6, 8),
        )
        make_game_event(banner_timeline=timeline, carat_amount=1200,
                        carats_throughout=900)
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        row, = res.data['income_ledger']
        self.assertEqual(row['date'], _iso(_dt(2025, 6, 1)))
        self.assertEqual(row['throughout_end'], _iso(_dt(2025, 6, 8)))
        self.assertEqual(row['carats'], 1200)
        self.assertEqual(row['kind'], 'event')


    def test_endpoint_serializes_race_event_number(self):
        make_league_of_heroes(name='LoH', loh_number=1,
                              global_start_date=_dt(2025, 7, 1),
                              global_end_date=_dt(2025, 7, 8))
        res = APIClient().get('/calculator-data')
        self.assertEqual(res.status_code, 200)
        row, = res.data['income_ledger']
        self.assertEqual((row['kind'], row['event_number']),
                         ('league_of_heroes', 1))
