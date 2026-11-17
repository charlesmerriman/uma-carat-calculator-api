"""Story events: dates, the /calculator-data payload, the admin, and the two
things they must never feed (income and selector eligibility).
notes/story-events-plan.md."""

import datetime

from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.eligibility import build_first_jp_date_maps
from calculatorapi.models import (
    BannerTimeline, CustomUser, StoryEvent, StoryEventCard, SupportCard,
)
from calculatorapi.predictions import (
    STORY_EVENT_CLAIM_DAYS,
    build_effective_date_maps,
    build_story_event_date_map,
)

from .base import PLAIN_TEST_STORAGES, CalculatorTestCase
from .factories import _dt, _iso, make_timeline

DAY = datetime.timedelta(days=1)


def _card(name, game_id, rarity=3, **fields):
    return SupportCard.objects.create(name=name, game_id=game_id, rarity=rarity, **fields)


def _event(game_id=1022, banner=None, **fields):
    return StoryEvent.objects.create(game_id=game_id, banner_timeline=banner, **fields)


def _resolve(event):
    emap = build_effective_date_maps()[BannerTimeline]
    return build_story_event_date_map([event], emap)[event.id]


def _payload():
    return APIClient().get("/calculator-data").json()["story_event_data"]


class StoryEventDateTests(CalculatorTestCase):
    """The game's own instants once Global has run the event, the banner's before."""

    def setUp(self):
        # Sweet Memories Teatime and its banner, as the game has them.
        self.start = _dt(2026, 11, 9, 22)
        self.end = _dt(2026, 11, 23, 21, 59, 59)
        self.banner = make_timeline(
            name="Ines Fujin (Valentine)", global_start_date=self.start, global_end_date=self.end,
        )

    def test_the_game_dates_win_when_global_has_them(self):
        event = _event(
            banner=self.banner,
            global_start_at=self.start, global_end_at=self.end,
            global_claim_end_at=_dt(2026, 11, 26, 21, 59, 59),
        )
        resolved = _resolve(event)
        self.assertEqual(resolved["start_date"], self.start)
        self.assertEqual(resolved["end_date"], self.end)
        self.assertEqual(resolved["claim_end_date"], _dt(2026, 11, 26, 21, 59, 59))
        self.assertFalse(resolved["is_predicted"])

    def test_an_event_global_has_not_run_borrows_its_banner(self):
        resolved = _resolve(_event(banner=self.banner))
        self.assertEqual(resolved["start_date"], self.start)
        self.assertEqual(resolved["end_date"], self.end)
        # Every Global event so far closes its claims three days after it ends.
        self.assertEqual(STORY_EVENT_CLAIM_DAYS, 3 * DAY)
        self.assertEqual(resolved["claim_end_date"], self.end + 3 * DAY)

    def test_a_predicted_banner_makes_a_predicted_event(self):
        # The anchor (confirmed) and an unconfirmed banner a month later on JP.
        make_timeline(name="Anchor", jp_start_date=_dt(2023, 1, 1),
                      jp_end_date=_dt(2023, 1, 10), global_start_date=self.start,
                      global_end_date=self.end)
        upcoming = make_timeline(name="Upcoming", jp_start_date=_dt(2023, 2, 1),
                                 jp_end_date=_dt(2023, 2, 10))
        resolved = _resolve(_event(game_id=1024, banner=upcoming))
        self.assertTrue(resolved["is_predicted"])
        self.assertEqual(resolved["claim_end_date"], resolved["end_date"] + 3 * DAY)

    def test_a_missing_claim_deadline_is_filled_the_same_way(self):
        event = _event(banner=self.banner, global_start_at=self.start, global_end_at=self.end)
        self.assertEqual(_resolve(event)["claim_end_date"], self.end + 3 * DAY)

    def test_no_banner_and_no_dates_resolves_to_nothing(self):
        resolved = _resolve(_event())
        self.assertIsNone(resolved["start_date"])
        self.assertIsNone(resolved["claim_end_date"])


class StoryEventPayloadTests(CalculatorTestCase):
    """What the Timeline reads: `story_event_data` on /calculator-data."""

    def setUp(self):
        self.banner = make_timeline(name="Rice Shower (Halloween)")
        self.bourbon = _card("Mihono Bourbon", 30066, title="[The Ghost Finds Halloween Magic]",
                             purpose="A wit card for long races.")
        self.event = _event(
            game_id=1006, banner=self.banner, name="Halloween Makeover!", jp_name="ハロウィン",
            carat_amount=2160, uma_ticket_amount=2, support_ticket_amount=2,
            ssr_shard_amount=3, sr_shard_amount=3,
        )
        StoryEventCard.objects.create(
            story_event=self.event, support_card=self.bourbon, must_get=True, note="Worth the grind",
        )

    def test_an_event_carries_its_rewards_dates_and_cards(self):
        [entry] = _payload()
        self.assertEqual(entry["game_id"], 1006)
        self.assertEqual(entry["name"], "Halloween Makeover!")
        self.assertEqual(entry["banner_timeline"], self.banner.id)
        self.assertEqual(
            (entry["carat_amount"], entry["uma_ticket_amount"], entry["support_ticket_amount"],
             entry["ssr_shard_amount"], entry["sr_shard_amount"]),
            (2160, 2, 2, 3, 3),
        )
        self.assertEqual(entry["start_date"], _iso(self.banner.global_start_date))
        self.assertEqual(entry["claim_end_date"], _iso(self.banner.global_end_date + 3 * DAY))
        self.assertEqual(entry["cards"], [{
            "id": self.bourbon.id, "name": "Mihono Bourbon",
            "title": "[The Ghost Finds Halloween Magic]", "image": None, "rarity": 3,
            "purpose": "A wit card for long races.", "game_id": 30066,
            "must_get": True, "note": "Worth the grind",
        }])

    def test_the_japanese_name_is_never_sent(self):
        self.event.name = ""
        self.event.save()
        [entry] = _payload()
        self.assertEqual(entry["name"], "")
        self.assertNotIn("ハロウィン", str(entry))
        self.assertNotIn("jp_name", entry)

    def test_a_working_name_shows_until_global_names_the_event(self):
        self.event.name = ""
        self.event.working_name = "Make up in Halloween"
        self.event.save()
        self.assertEqual(_payload()[0]["name"], "Make up in Halloween")
        self.event.name = "Halloween Makeover!"
        self.event.save()
        self.assertEqual(_payload()[0]["name"], "Halloween Makeover!")

    def test_a_translated_card_title_is_not_sent(self):
        # UmaTL's titles must be credited where they show, and this row has no
        # credit line: a card Global hasn't titled shows its name alone.
        self.bourbon.title = ""
        self.bourbon.translated_title = "[Halloween Ghost]"
        self.bourbon.save()
        self.assertEqual(_payload()[0]["cards"][0]["title"], "")

    def test_an_event_with_no_banner_is_not_sent(self):
        _event(game_id=1050, name="Somewhere later")
        self.assertEqual([e["game_id"] for e in _payload()], [1006])

    def test_cards_come_in_their_order(self):
        sr = _card("Nice Nature", 20026, rarity=2)
        StoryEventCard.objects.create(story_event=self.event, support_card=sr, order=0)
        StoryEventCard.objects.filter(support_card=self.bourbon).update(order=1)
        public_payload_cache.invalidate()  # a queryset update() skips the signal
        self.assertEqual([c["name"] for c in _payload()[0]["cards"]],
                         ["Nice Nature", "Mihono Bourbon"])

    def test_an_edit_reaches_the_cached_payload(self):
        _payload()
        self.event.carat_amount = 2910
        self.event.save()
        self.assertEqual(_payload()[0]["carat_amount"], 2910)

    def test_more_events_ask_no_more_queries(self):
        def count():
            public_payload_cache.invalidate()
            with CaptureQueriesContext(connection) as queries:
                APIClient().get("/calculator-data")
            return len(queries)

        count()  # warm the per-process caches first
        one = count()
        for number in range(5):
            event = _event(game_id=1100 + number, banner=make_timeline(name=f"B{number}"))
            for card_number in range(2):
                StoryEventCard.objects.create(
                    story_event=event, support_card=_card(f"C{number}{card_number}",
                                                          40000 + number * 10 + card_number))
        self.assertEqual(count(), one)

    def test_one_card_once_per_event(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryEventCard.objects.create(story_event=self.event, support_card=self.bourbon)


class StoryEventsFeedNothingTests(CalculatorTestCase):
    """Display only: an event's card never becomes a dated catalogue card, and
    the event never reaches the income ledger."""

    def setUp(self):
        self.welfare = _card("Hishi Akebono", 30133)
        event = _event(banner=make_timeline(), carat_amount=2160)
        StoryEventCard.objects.create(story_event=event, support_card=self.welfare)

    def test_a_card_on_an_event_has_no_jp_date_for_the_selectors(self):
        self.assertNotIn(self.welfare.id, build_first_jp_date_maps()[1])

    def test_the_card_on_the_wire_carries_no_selector_key(self):
        card = _payload()[0]["cards"][0]
        self.assertFalse({"first_jp_date", "global_release_at", "released_at"} & set(card))

    def test_the_income_ledger_does_not_read_it(self):
        ledger = APIClient().get("/calculator-data").json()["income_ledger"]
        self.assertFalse([row for row in ledger if row.get("carats") == 2160])


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class StoryEventAdminTests(CalculatorTestCase):

    def setUp(self):
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))
        self.event = _event(banner=make_timeline(), name="Sweet Memories Teatime",
                            carat_amount=1800)

    def _post(self, **changes):
        data = {
            "working_name": "", "banner_timeline": self.event.banner_timeline_id,
            "carat_amount": 1800, "uma_ticket_amount": 0, "support_ticket_amount": 0,
            "ssr_shard_amount": 0, "sr_shard_amount": 0,
            "cards-TOTAL_FORMS": 0, "cards-INITIAL_FORMS": 0,
            "cards-MIN_NUM_FORMS": 0, "cards-MAX_NUM_FORMS": 1000,
            **changes,
        }
        url = reverse("admin:calculatorapi_storyevent_change", args=(self.event.pk,))
        return self.client.post(url, data)

    def test_the_pages_render(self):
        self.assertEqual(
            self.client.get(reverse("admin:calculatorapi_storyevent_changelist")).status_code, 200)
        self.assertEqual(self.client.get(
            reverse("admin:calculatorapi_storyevent_change", args=(self.event.pk,))).status_code, 200)

    def test_changing_a_reward_marks_the_rewards_as_set_by_hand(self):
        self.assertEqual(self._post(carat_amount=2160).status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.carat_amount, 2160)
        self.assertTrue(self.event.rewards_edited)

    def test_an_edit_elsewhere_leaves_the_rewards_to_the_import(self):
        self.assertEqual(self._post(working_name="Sweet Memory Tea Time").status_code, 302)
        self.event.refresh_from_db()
        self.assertFalse(self.event.rewards_edited)

    def test_the_game_fields_cannot_be_changed_here(self):
        self._post(name="Something else", game_id=9999)
        self.event.refresh_from_db()
        self.assertEqual((self.event.name, self.event.game_id), ("Sweet Memories Teatime", 1022))

    def test_the_card_rows_ask_nothing_per_row(self):
        url = reverse("admin:calculatorapi_storyevent_change", args=(self.event.pk,))
        StoryEventCard.objects.create(story_event=self.event, support_card=_card("A", 30001))
        self.client.get(url)  # warm the per-process caches (content types) first
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(self.client.get(url).status_code, 200)
        for number in range(2, 8):
            StoryEventCard.objects.create(
                story_event=self.event, support_card=_card(f"C{number}", 30000 + number))
        with CaptureQueriesContext(connection) as seven:
            self.client.get(url)
        self.assertEqual(len(one), len(seven))
