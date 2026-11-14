"""
global_release.py: "on global" means released on global, by a started
confirmed banner or the game's own date, compared with now (owner, 2026-11-14).
"""

import datetime

from django.utils import timezone

from calculatorapi.global_release import (
    annotate_cards, annotate_umas, card_is_released, game_release_passed,
    released_cards, released_umas, uma_is_released,
)
from calculatorapi.models import SupportCard, SupportsOnSupportBanner, Uma, UmasOnUmaBanner
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    RELEASED_ON_GLOBAL, make_support_banner, make_timeline, make_uma_banner,
)

DAY = datetime.timedelta(days=1)


def on_uma_banner(uma, **timeline):
    UmasOnUmaBanner.objects.create(banner_uma=make_uma_banner(make_timeline(**timeline)), uma=uma)


def on_support_banner(card, **timeline):
    SupportsOnSupportBanner.objects.create(
        banner_support=make_support_banner(make_timeline(**timeline)), support_card=card,
    )


class UmaReleaseTests(CalculatorTestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_the_games_date_releases_an_outfit_on_no_banner(self):
        # The launch roster: on no banner, dated by the client.
        uma = Uma.objects.create(name="Gold Ship", global_release_at=RELEASED_ON_GLOBAL)
        self.assertTrue(uma_is_released(uma))

    def test_a_date_still_ahead_is_not_out_until_it_passes(self):
        # The client ships outfits before their banners.
        uma = Uma.objects.create(name="Mejiro Ryan", global_release_at=self.now + DAY)
        self.assertFalse(uma_is_released(uma, self.now))
        self.assertTrue(uma_is_released(uma, self.now + 2 * DAY))

    def test_no_date_and_no_banner_is_jp_only(self):
        self.assertFalse(uma_is_released(Uma.objects.create(name="Special Week")))

    def test_a_started_confirmed_banner_releases_it_without_the_games_date(self):
        # Daptrius, 2026-11-13: Daitaku Helios read "JP only" after her banner
        # because the snapshot predated her. The banner alone now releases her.
        uma = Uma.objects.create(name="Daitaku Helios")
        on_uma_banner(uma, global_start_date=self.now - DAY, global_end_date=self.now + 9 * DAY)
        self.assertTrue(uma_is_released(uma))

    def test_a_banner_still_ahead_does_not(self):
        uma = Uma.objects.create(name="Daitaku Helios")
        on_uma_banner(uma, global_start_date=self.now + DAY, global_end_date=self.now + 9 * DAY)
        self.assertFalse(uma_is_released(uma, self.now))
        self.assertTrue(uma_is_released(uma, self.now + 2 * DAY))

    def test_a_predicted_banner_never_counts(self):
        # No global dates: a prediction from JP, not a release.
        uma = Uma.objects.create(name="Daitaku Helios")
        on_uma_banner(uma, jp_start_date=self.now - 400 * DAY, jp_end_date=self.now - 390 * DAY)
        self.assertFalse(uma_is_released(uma))

    def test_the_bulk_readers_agree(self):
        out = Uma.objects.create(name="Gold Ship", global_release_at=RELEASED_ON_GLOBAL)
        ahead = Uma.objects.create(name="Mejiro Ryan", global_release_at=self.now + DAY)
        self.assertEqual(released_umas(), {out.id})
        rows = {uma.id: uma.on_global for uma in annotate_umas(Uma.objects.all())}
        self.assertEqual(rows, {out.id: True, ahead.id: False})


class CardReleaseTests(CalculatorTestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_the_games_date_releases_an_event_card(self):
        card = SupportCard.objects.create(name="Welfare SSR", global_release_at=RELEASED_ON_GLOBAL)
        self.assertTrue(card_is_released(card))

    def test_the_2099_mark_is_never_out(self):
        card = SupportCard.objects.create(
            name="Twin Turbo", global_release_at=datetime.datetime(2099, 1, 1, tzinfo=datetime.timezone.utc),
        )
        self.assertFalse(card_is_released(card))
        self.assertIsNone(game_release_passed(card.global_release_at))

    def test_a_started_confirmed_banner_releases_a_card(self):
        card = SupportCard.objects.create(name="K.S.Miracle")
        on_support_banner(card, global_start_date=self.now - DAY, global_end_date=self.now + 9 * DAY)
        self.assertTrue(card_is_released(card))
        self.assertEqual(released_cards(), {card.id})

    def test_a_predicted_banner_never_counts(self):
        card = SupportCard.objects.create(name="K.S.Miracle")
        on_support_banner(card, jp_start_date=self.now - 400 * DAY, jp_end_date=self.now - 390 * DAY)
        self.assertFalse(card_is_released(card))
        self.assertFalse(annotate_cards(SupportCard.objects.filter(pk=card.pk)).get().on_global)
