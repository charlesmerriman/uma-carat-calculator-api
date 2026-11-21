"""The other kinds of free card event: main story chapters, anniversary missions
and special events, beside story events in the same table (`StoryEvent.kind`),
and an uma as a reward. Their dates, the payload, the admin, and that none of
it reaches selector eligibility. notes/story-events-plan-2.md, PR 1."""

import datetime

from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from calculatorapi.eligibility import build_first_jp_date_maps
from calculatorapi.models import CustomUser, StoryEvent, StoryEventCard, StoryEventKind, Uma

from .base import PLAIN_TEST_STORAGES, CalculatorTestCase
from .factories import _dt, make_timeline
from .test_story_events import _card, _event, _payload, _resolve

DAY = datetime.timedelta(days=1)


def _uma(name="Special Week (Commander)", game_id=100103, **fields):
    return Uma.objects.create(name=name, game_id=game_id, rarity=3, **fields)


class EventKindDateTests(CalculatorTestCase):
    """Each kind keeps the dates its row shows (predictions.story_event_dates)."""

    def setUp(self):
        self.start = _dt(2026, 3, 12, 22)
        self.end = _dt(2026, 3, 23, 21, 59, 59)
        self.banner = make_timeline(
            name="Kitasan Black + Matikanetannhauser", global_start_date=self.start,
            global_end_date=self.end, jp_start_date=_dt(2022, 2, 23, 22),
            jp_end_date=_dt(2022, 3, 7, 21, 59, 59),
        )

    def test_a_main_story_chapter_has_a_start_and_no_end(self):
        chapter = _event(game_id=7, kind=StoryEventKind.MAIN_STORY, banner=self.banner)
        resolved = _resolve(chapter)
        self.assertEqual(resolved["start_date"], self.start)
        self.assertIsNone(resolved["end_date"])
        self.assertIsNone(resolved["claim_end_date"])

    def test_a_main_story_chapter_global_has_dated_still_has_no_end(self):
        chapter = _event(game_id=7, kind=StoryEventKind.MAIN_STORY, banner=self.banner,
                         global_start_at=_dt(2026, 6, 25, 22), global_end_at=_dt(2099, 1, 1))
        resolved = _resolve(chapter)
        self.assertEqual(resolved["start_date"], _dt(2026, 6, 25, 22))
        self.assertIsNone(resolved["end_date"])

    def test_anniversary_missions_keep_their_own_end_and_no_claim_date(self):
        missions = _event(
            game_id=138, kind=StoryEventKind.ANNIVERSARY, banner=self.banner,
            global_start_at=self.start, global_end_at=_dt(2026, 4, 15, 14, 59, 59),
        )
        resolved = _resolve(missions)
        self.assertEqual(resolved["end_date"], _dt(2026, 4, 15, 14, 59, 59))
        self.assertIsNone(resolved["claim_end_date"])
        self.assertFalse(resolved["is_predicted"])

    def test_upcoming_missions_run_their_jp_length_from_the_banner(self):
        # The 2nd anniversary's missions on JP: 2023-02-24 noon to 2023-03-30 05:00
        # (Japan), so 33 days and 17 hours, counted from the banner they sit under.
        missions = _event(
            game_id=302, kind=StoryEventKind.ANNIVERSARY, banner=self.banner,
            jp_start_at=_dt(2023, 2, 24, 3), jp_end_at=_dt(2023, 3, 29, 19, 59, 59),
        )
        length = _dt(2023, 3, 29, 19, 59, 59) - _dt(2023, 2, 24, 3)
        resolved = _resolve(missions)
        self.assertEqual(resolved["start_date"], self.start)
        self.assertEqual(resolved["end_date"], self.start + length)
        self.assertIsNone(resolved["claim_end_date"])

    def test_upcoming_missions_with_no_jp_length_end_with_the_banner(self):
        missions = _event(game_id=302, kind=StoryEventKind.ANNIVERSARY, banner=self.banner)
        self.assertEqual(_resolve(missions)["end_date"], self.end)

    def test_a_story_event_is_unchanged(self):
        resolved = _resolve(_event(banner=self.banner))
        self.assertEqual(resolved["end_date"], self.end)
        self.assertEqual(resolved["claim_end_date"], self.end + 3 * DAY)


# Local storage: a picture's URL comes from the default storage, which is the
# S3 bucket the local .env names and CI has none of.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class EventKindPayloadTests(CalculatorTestCase):
    """`kind` on the wire, and an uma reward in its own list."""

    def setUp(self):
        self.banner = make_timeline(name="Seiun Sky (Rerun) + Narita Brian (Rerun)")
        self.card = _card("Special Week", 30105, title="[Take Hold of Our Dreams!]")
        self.uma = _uma(title="[Ruler of Japan]", purpose="A long-distance ace.")
        self.chapter = _event(game_id=7, kind=StoryEventKind.MAIN_STORY, banner=self.banner,
                              name="Main Story Act 1, Finale Part 2")
        StoryEventCard.objects.create(story_event=self.chapter, support_card=self.card, order=0)
        StoryEventCard.objects.create(story_event=self.chapter, uma=self.uma, order=1)

    def test_a_chapter_sends_its_kind_card_and_uma_apart(self):
        [entry] = _payload()
        self.assertEqual(entry["kind"], "main_story")
        self.assertEqual(entry["end_date"], None)
        self.assertEqual([card["id"] for card in entry["cards"]], [self.card.id])
        self.assertEqual(entry["umas"], [{
            "id": self.uma.id, "name": "Special Week (Commander)", "title": "[Ruler of Japan]",
            "image": None, "portrait": None, "portrait_layers": None, "rarity": 3,
            "purpose": "A long-distance ace.", "game_id": 100103,
            "must_get": False, "note": "",
        }])

    def test_a_story_event_sends_its_kind_and_no_umas(self):
        _event(game_id=1022, banner=make_timeline(name="Ines Fujin (Valentine)"))
        entry = next(e for e in _payload() if e["game_id"] == 1022)
        self.assertEqual(entry["kind"], "story")
        self.assertEqual(entry["umas"], [])

    def test_an_uma_on_the_wire_carries_no_selector_key(self):
        uma = _payload()[0]["umas"][0]
        self.assertFalse({"first_jp_date", "global_release_at", "is_time_limited",
                          "is_three_star", "admin_comments"} & set(uma))

    def test_a_picture_on_any_kind_reaches_the_payload(self):
        StoryEvent.objects.filter(pk=self.chapter.pk).update(image="story_events/main-story-7.png")
        from calculatorapi import public_payload_cache  # pylint: disable=import-outside-toplevel
        public_payload_cache.invalidate()  # update() skips the signal
        self.assertTrue(_payload()[0]["image"].endswith("story_events/main-story-7.png"))

    def test_more_umas_ask_no_more_queries(self):
        from rest_framework.test import APIClient  # pylint: disable=import-outside-toplevel
        from calculatorapi import public_payload_cache  # pylint: disable=import-outside-toplevel

        def count():
            public_payload_cache.invalidate()
            with CaptureQueriesContext(connection) as queries:
                APIClient().get("/calculator-data")
            return len(queries)

        count()
        one = count()
        for number in range(4):
            event = _event(game_id=10 + number, kind=StoryEventKind.MAIN_STORY,
                           banner=make_timeline(name=f"B{number}"))
            StoryEventCard.objects.create(story_event=event, uma=_uma(f"U{number}", 200000 + number))
        self.assertEqual(count(), one)


class EventKindConstraintTests(CalculatorTestCase):

    def test_a_game_id_is_unique_within_its_kind_only(self):
        _event(game_id=7)
        _event(game_id=7, kind=StoryEventKind.MAIN_STORY)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _event(game_id=7, kind=StoryEventKind.MAIN_STORY)

    def test_events_made_by_hand_need_no_game_id(self):
        _event(game_id=None, kind=StoryEventKind.SPECIAL, name="Welcome to Umayuru")
        _event(game_id=None, kind=StoryEventKind.SPECIAL, name="A collab")
        self.assertEqual(StoryEvent.objects.filter(game_id=None).count(), 2)

    def test_a_reward_is_a_card_or_an_uma_never_both_or_neither(self):
        event = _event()
        card, uma = _card("A", 30001), _uma()
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryEventCard.objects.create(story_event=event, support_card=card, uma=uma)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryEventCard.objects.create(story_event=event)

    def test_one_uma_once_per_event(self):
        event, uma = _event(), _uma()
        StoryEventCard.objects.create(story_event=event, uma=uma)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoryEventCard.objects.create(story_event=event, uma=uma)


class EventUmaFeedsNothingTests(CalculatorTestCase):
    """An uma given by an event and on no banner stays refused under a real
    cutoff: the twin of the story event card test."""

    def test_an_uma_on_an_event_has_no_jp_date_for_the_selectors(self):
        uma = _uma()
        StoryEventCard.objects.create(
            story_event=_event(game_id=7, kind=StoryEventKind.MAIN_STORY, banner=make_timeline()),
            uma=uma,
        )
        self.assertNotIn(uma.id, build_first_jp_date_maps()[0])


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class EventKindAdminTests(CalculatorTestCase):

    def setUp(self):
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))

    def _form(self, **changes):
        return {
            "kind": "special", "game_id": "", "name": "", "working_name": "Welcome to Umayuru",
            "jp_name": "", "banner_timeline": make_timeline().pk,
            "global_start_at_0": "", "global_start_at_1": "", "global_end_at_0": "",
            "global_end_at_1": "", "global_claim_end_at_0": "", "global_claim_end_at_1": "",
            "jp_start_at_0": "", "jp_start_at_1": "", "jp_end_at_0": "", "jp_end_at_1": "",
            "carat_amount": 0, "uma_ticket_amount": 0, "support_ticket_amount": 0,
            "ssr_shard_amount": 0, "sr_shard_amount": 0,
            "cards-TOTAL_FORMS": 0, "cards-INITIAL_FORMS": 0,
            "cards-MIN_NUM_FORMS": 0, "cards-MAX_NUM_FORMS": 1000,
            **changes,
        }

    def test_an_editor_can_add_an_event_the_files_dont_describe(self):
        response = self.client.post(reverse("admin:calculatorapi_storyevent_add"), self._form())
        self.assertEqual(response.status_code, 302, response.content[:2000])
        event = StoryEvent.objects.get()
        self.assertEqual((event.kind, event.game_id, event.working_name),
                         ("special", None, "Welcome to Umayuru"))

    def test_an_event_made_by_hand_stays_editable(self):
        event = _event(game_id=None, kind=StoryEventKind.SPECIAL, banner=make_timeline())
        url = reverse("admin:calculatorapi_storyevent_change", args=(event.pk,))
        response = self.client.post(url, self._form(kind="anniversary", name="Gift"))
        self.assertEqual(response.status_code, 302, response.content[:2000])
        event.refresh_from_db()
        self.assertEqual((event.kind, event.name), ("anniversary", "Gift"))

    def test_an_imported_events_kind_is_locked(self):
        event = _event(game_id=7, kind=StoryEventKind.MAIN_STORY, banner=make_timeline(),
                       name="Main Story Act 1, Finale Part 2")
        url = reverse("admin:calculatorapi_storyevent_change", args=(event.pk,))
        self.client.post(url, self._form(kind="story", game_id=8))
        event.refresh_from_db()
        self.assertEqual((event.kind, event.game_id), ("main_story", 7))

    def test_the_list_filters_by_kind(self):
        _event(game_id=7, kind=StoryEventKind.MAIN_STORY, name="Chapter")
        _event(game_id=1022, name="Teatime")
        url = reverse("admin:calculatorapi_storyevent_changelist") + "?kind__exact=main_story"
        response = self.client.get(url)
        self.assertContains(response, "Chapter")
        self.assertNotContains(response, "Teatime")

    def test_uma_rows_ask_nothing_per_row(self):
        event = _event(banner=make_timeline())
        url = reverse("admin:calculatorapi_storyevent_change", args=(event.pk,))
        StoryEventCard.objects.create(story_event=event, uma=_uma("U1", 200001))
        self.client.get(url)
        with CaptureQueriesContext(connection) as one:
            self.assertEqual(self.client.get(url).status_code, 200)
        for number in range(2, 8):
            StoryEventCard.objects.create(story_event=event, uma=_uma(f"U{number}", 200000 + number))
        with CaptureQueriesContext(connection) as seven:
            self.client.get(url)
        self.assertEqual(len(one), len(seven))
