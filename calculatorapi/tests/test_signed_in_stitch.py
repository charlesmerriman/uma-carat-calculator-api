"""GET /calculator-data for a signed-in user: the stitched gzip response."""

import datetime
import gzip
import json
import zlib
from unittest import mock

from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.models import UserPlannedBanner
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import auth_client, make_timeline, make_uma_banner, make_user
from calculatorapi.tests.test_calculator_get import _EXPECTED_GET_KEYS
from calculatorapi.views import calculator as calculator_view
from calculatorapi.views.calculator import (
    USER_PAYLOAD_KEYS,
    _build_user_payload,
    build_user_context,
)


class SignedInStitchTests(CalculatorTestCase):
    """A signed-in response is the cached head with the user's keys stitched on.

    -> public_payload_cache.signed_in_gzip, "STITCHING" in that module
    """

    def setUp(self):
        self.user = make_user()
        self.client, self.token = auth_client(self.user)
        self.timeline = make_timeline(name='Stitched Banner')
        self.uma_banner = make_uma_banner(timeline=self.timeline)
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=self.uma_banner, number_of_pulls=7)

    @staticmethod
    def _strict_gunzip(data):
        """Decode exactly ONE gzip member, and fail on anything after it.

        gzip.decompress() quietly reads several concatenated members, which
        some browsers do not; a stitched response must be one member.
        """
        decoder = zlib.decompressobj(wbits=31)  # 31 = gzip framing
        text = decoder.decompress(data) + decoder.flush()
        assert decoder.eof, 'the gzip member never ended'
        assert decoder.unused_data == b'', 'bytes after the first gzip member'
        return text

    @staticmethod
    def _keys_once(pairs):
        keys = [key for key, _ in pairs]
        assert len(keys) == len(set(keys)), f'a key sent twice: {keys}'
        return dict(pairs)

    def _old_merge(self):
        """What the signed-in response was before stitching: the guest
        payload with the user's keys merged over it, as JSON."""
        guest = json.loads(gzip.decompress(public_payload_cache.read()))
        emap, anniversary_emap, card_context = build_user_context()
        user = json.loads(JSONRenderer().render(_build_user_payload(
            self.user, emap=emap, anniversary_emap=anniversary_emap,
            card_context=card_context)))
        return {**guest, **user}

    def _assert_stitched_matches_the_old_merge(self, res):
        self.assertEqual(res['Content-Encoding'], 'gzip')
        body = json.loads(self._strict_gunzip(res.content), object_pairs_hook=self._keys_once)
        self.assertEqual(body, self._old_merge())
        self.assertEqual(set(body), _EXPECTED_GET_KEYS)
        self.assertEqual(body['user_planned_banner_data'][0]['number_of_pulls'], 7)

    def test_a_cache_hit_stitches_to_exactly_the_old_merge(self):
        APIClient().get('/calculator-data')  # a guest warms the cache
        self._assert_stitched_matches_the_old_merge(
            self.client.get('/calculator-data', HTTP_ACCEPT_ENCODING='gzip'))

    def test_the_request_that_rebuilt_stitches_too(self):
        # The miss path answers from what store() returned.
        self.assertIsNone(public_payload_cache.read())
        self._assert_stitched_matches_the_old_merge(
            self.client.get('/calculator-data', HTTP_ACCEPT_ENCODING='gzip'))

    def test_user_payload_keys_names_every_key_the_user_half_returns(self):
        emap, anniversary_emap, card_context = build_user_context()
        user_half = _build_user_payload(
            self.user, emap=emap, anniversary_emap=anniversary_emap,
            card_context=card_context)
        self.assertEqual(set(user_half), USER_PAYLOAD_KEYS)
        # And the head leaves exactly those to the user half.
        self.assertTrue(USER_PAYLOAD_KEYS <= _EXPECTED_GET_KEYS)

    def test_a_client_not_asking_for_gzip_gets_the_stitched_json_plain(self):
        res = self.client.get('/calculator-data')
        self.assertFalse(res.has_header('Content-Encoding'))
        self.assertEqual(res.json()['user_planned_banner_data'][0]['number_of_pulls'], 7)


class UserContextCacheTests(CalculatorTestCase):
    """The signed-in date lookups are built once and dropped with the payload.

    -> views/calculator.py user_context, public_payload_cache.USER_CONTEXT_KEY
    """

    def setUp(self):
        self.user = make_user()
        self.client, self.token = auth_client(self.user)
        now = timezone.now()
        self.early = make_timeline(
            name='Early', global_start_date=now,
            global_end_date=now + datetime.timedelta(days=5))
        self.late = make_timeline(
            name='Late', global_start_date=now + datetime.timedelta(days=10),
            global_end_date=now + datetime.timedelta(days=15))
        # number_of_pulls tells the two rows apart in the response.
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=make_uma_banner(timeline=self.early), number_of_pulls=1)
        UserPlannedBanner.objects.create(
            user=self.user, banner_uma=make_uma_banner(timeline=self.late), number_of_pulls=2)

    def _row_order(self):
        rows = self.client.get('/calculator-data').json()['user_planned_banner_data']
        return [row['number_of_pulls'] for row in rows]

    def _builds_during(self, fn):
        with mock.patch.object(
                calculator_view, 'build_user_context',
                wraps=calculator_view.build_user_context) as build:
            fn()
        return build.call_count

    def test_a_warm_signed_in_load_does_not_rebuild_the_lookups(self):
        self.assertEqual(self._builds_during(self._row_order), 1)
        self.assertIsNotNone(public_payload_cache.read_user_context())
        self.assertEqual(self._builds_during(self._row_order), 0)

    def test_a_plan_switch_reads_the_same_cache(self):
        self._row_order()
        plan_id = self.client.get('/calculator-data').json()['active_plan_id']
        self.assertEqual(
            self._builds_during(lambda: self.client.get(f'/plans/{plan_id}')), 0)

    def test_a_catalogue_save_drops_the_lookups_with_the_payload(self):
        self._row_order()
        self.early.save()
        self.assertIsNone(public_payload_cache.read_user_context())
        self.assertIsNone(public_payload_cache.read())

    def test_a_users_own_save_keeps_them(self):
        self._row_order()
        UserPlannedBanner.objects.filter(user=self.user).first().save()
        self.assertIsNotNone(public_payload_cache.read_user_context())

    def test_a_moved_banner_reorders_the_next_signed_in_load(self):
        # The point of dropping them together: a stale emap would keep
        # sorting the rows by the old dates.
        self.assertEqual(self._row_order(), [1, 2])
        now = timezone.now()
        self.early.global_start_date = now + datetime.timedelta(days=20)
        self.early.global_end_date = now + datetime.timedelta(days=25)
        self.early.save()
        self.assertEqual(self._row_order(), [2, 1])
