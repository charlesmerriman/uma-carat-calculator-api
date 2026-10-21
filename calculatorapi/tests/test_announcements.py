"""GET /bot/upcoming: banner and event moments from the Timeline's own dates.
Predicted dates only get a "tomorrow" reminder; a row the Timeline doesn't
date is never announced; the keys never change."""

import datetime

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from calculatorapi.models import CalculationConstants
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_anniversary_event,
    make_champions_meeting,
    make_daily_legend_race,
    make_league_of_heroes,
    make_timeline,
    make_uma_banner,
)

KEY = "bot-test-key"


@override_settings(BOT_API_SECRET=KEY)
class UpcomingTests(CalculatorTestCase):

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)

    def at(self, **delta):
        return self.now + datetime.timedelta(**delta)

    def moments(self, days=14):
        response = self.client.get(reverse("bot-upcoming"), {"days": days}, HTTP_X_BOT_KEY=KEY)
        self.assertEqual(response.status_code, 200)
        return {moment["key"]: moment for moment in response.json()["moments"]}

    def test_a_confirmed_banner_gets_tomorrow_and_live(self):
        timeline = make_timeline("Kitasan Black", global_start_date=self.at(days=3), global_end_date=self.at(days=13))
        make_uma_banner(timeline)
        moments = self.moments()
        tomorrow, live = moments[f"banner-{timeline.id}-tomorrow"], moments[f"banner-{timeline.id}-live"]
        self.assertEqual(tomorrow["at"], self.at(days=2).isoformat())
        self.assertEqual(live["at"], self.at(days=3).isoformat())
        self.assertFalse(live["predicted"])
        self.assertEqual(live["name"], "Kitasan Black")
        self.assertTrue(live["url"].endswith("/app/timeline"))

    def test_a_predicted_banner_only_gets_a_reminder(self):
        # Predicted global = the anchor's global start plus the JP gap scaled by
        # the prediction factor (0.664): 60 JP days after the anchor is ~40
        # global days after it, so ~10 days from now.
        make_timeline("Anchor", global_start_date=self.at(days=-30), global_end_date=self.at(days=-20),
                      jp_start_date=self.at(days=-400), jp_end_date=self.at(days=-390))
        predicted = make_timeline("Guess", jp_start_date=self.at(days=-340), jp_end_date=self.at(days=-330))
        moments = {key: m for key, m in self.moments(days=60).items() if key.startswith(f"banner-{predicted.id}-")}
        self.assertEqual(list(moments), [f"banner-{predicted.id}-tomorrow"])
        self.assertTrue(moments[f"banner-{predicted.id}-tomorrow"]["predicted"])

    def test_a_cm_gets_each_milestone_from_the_constants(self):
        cm = make_champions_meeting(
            "CM 22", cm_number=22, global_start_date=self.at(days=4), global_end_date=self.at(days=10),
        )
        moments = self.moments()
        constants = CalculationConstants.load()
        opens = self.at(days=4, hours=constants.cm_registration_opens_hours)
        self.assertEqual(moments[f"cm-{cm.id}-tomorrow"]["at"], (opens - datetime.timedelta(hours=24)).isoformat())
        self.assertEqual(moments[f"cm-{cm.id}-registration_opens"]["at"], opens.isoformat())
        self.assertEqual(moments[f"cm-{cm.id}-round_one"]["at"], self.at(days=4).isoformat())
        closing = self.at(days=4, hours=constants.cm_finals_registration_closes_hours - 12)
        self.assertEqual(moments[f"cm-{cm.id}-finals_registration_closing"]["at"], closing.isoformat())
        self.assertIn(f"cm-{cm.id}-finals", moments)
        self.assertEqual(set(moments[f"cm-{cm.id}-finals"]["schedule"]),
                         {"registration_opens", "round_one", "finals_registration_closing", "finals"})

    def test_loh_skips_milestones_not_known_yet(self):
        loh = make_league_of_heroes("LoH 3", global_start_date=self.at(days=2), global_end_date=self.at(days=8))
        keys = {key for key in self.moments() if key.startswith(f"loh-{loh.id}-")}
        self.assertEqual(keys, {f"loh-{loh.id}-tomorrow", f"loh-{loh.id}-round_one"})

    def test_campaign_uses_the_main_part_and_legend_races_need_a_banner(self):
        run_up = make_timeline("Part 1", global_start_date=self.at(days=1), global_end_date=self.at(days=5))
        main = make_timeline("Part 2", global_start_date=self.at(days=6), global_end_date=self.at(days=12))
        event = make_anniversary_event("4th Anniversary", parts=(run_up, main))
        dated = make_daily_legend_race("Batch 13", banner_timeline=main, offset_days=1)
        tentative = make_daily_legend_race("Batch 14")
        moments = self.moments()
        self.assertEqual(moments[f"campaign-{event.id}-live"]["at"], self.at(days=6).isoformat())
        self.assertEqual(moments[f"legend_races-{dated.id}-live"]["at"], self.at(days=7).isoformat())
        self.assertFalse(any(key.startswith(f"legend_races-{tentative.id}-") for key in moments))

    def test_the_window(self):
        past = make_timeline("Last week", global_start_date=self.at(days=-7), global_end_date=self.at(days=3))
        far = make_timeline("Far off", global_start_date=self.at(days=40), global_end_date=self.at(days=50))
        keys = set(self.moments(days=14))
        self.assertFalse(any(key.startswith((f"banner-{past.id}-", f"banner-{far.id}-")) for key in keys))
        self.assertIn(f"banner-{far.id}-live", self.moments(days=60))

    def test_keys_fit_the_worker_claim_shape(self):
        make_champions_meeting(global_start_date=self.at(days=4), global_end_date=self.at(days=10))
        for key in self.moments():
            response = self.client.post(
                reverse("bot-message-claim"), {"key": f"announce:{key}", "channel_id": "1489347594197930077"},
                content_type="application/json", HTTP_X_BOT_KEY=KEY,
            )
            self.assertEqual(response.status_code, 201, key)

    def test_locked_and_bad_days(self):
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.client.get(reverse("bot-upcoming")).status_code, 404)
        response = self.client.get(reverse("bot-upcoming"), {"days": "x"}, HTTP_X_BOT_KEY=KEY)
        self.assertEqual(response.status_code, 400)
