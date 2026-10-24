"""A guide's link (/guide/<kind>-<id>): the link-preview page and its picture.

Pins what a preview may say, because Discord and X keep their own copy of it:
an open guide's facts (ranking, stats, course) and never its prose; the event
alone while the early-access gate holds the guide back (pvp_gate.py), for a
draft, or with no guide; the site's own tags for an address naming no event.
"""

import datetime
import re
import shutil
import tempfile
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from calculatorapi import pvp_gate
from calculatorapi.models import PvpGuide, PvpStatConfig
from calculatorapi.predictions import build_effective_date_maps
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.tests.factories import make_champions_meeting, make_league_of_heroes

MEDIA = tempfile.mkdtemp(prefix="guide-share-tests-")


def meta(html, attribute, name):
    """The content of <meta {attribute}="{name}">, or None."""
    match = re.search(rf'<meta {attribute}="{re.escape(name)}" content="([^"]*)"', html)
    return match.group(1) if match else None


def days(count):
    return timezone.now() + datetime.timedelta(days=count)


@override_settings(STORAGES=PLAIN_TEST_STORAGES, MEDIA_ROOT=MEDIA, FRONTEND_URL="https://site.example")
class GuideSharePageTests(CalculatorTestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        # CM 21 is open in full while PvP is early (pvp_gate.PVP_OPEN_CM_NUMBER).
        self.open_cm = make_champions_meeting(
            name="Champions Meeting 21 Capricorn Cup", cm_number=21,
            global_start_date=days(30), global_end_date=days(36),
        )
        # Running now: the free guide.
        self.running = make_champions_meeting(
            name="Champions Meeting 20 Sagittarius Cup", cm_number=20,
            global_start_date=days(-2), global_end_date=days(4),
        )
        # Over, and not CM 21: locked.
        self.past = make_champions_meeting(
            name="Champions Meeting 19 Scorpio Cup", cm_number=19,
            global_start_date=days(-40), global_end_date=days(-34),
        )
        self.open_guide = self.guide(self.open_cm, "Pace>>End>Late>Front")
        PvpStatConfig.objects.create(
            guide=self.open_guide, style="everyone",
            speed="1600", stamina="350", power="1200+", guts="700+", wit="1100+",
        )
        PvpStatConfig.objects.create(guide=self.open_guide, style="pace", label="Monster", speed="1700")

    @staticmethod
    def guide(event, ranking="", **fields):
        return PvpGuide.objects.create(
            champions_meeting=event, style_ranking=ranking, is_published=True,
            meta_analysis="Henry's private reasoning, never in a preview.", **fields,
        )

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_an_open_guide_says_its_ranking_stats_and_course(self):
        html = self.page(f"/guide/cm-{self.open_cm.id}")

        self.assertEqual(meta(html, "property", "og:title"), "Capricorn Cup PvP guide")
        description = meta(html, "property", "og:description")
        self.assertEqual(
            description,
            "Pace ≫ End &gt; Late &gt; Front. Speed 1600, Stamina 350, Power 1200+, Guts 700+, Wit 1100+ "
            "(and 1 more stat row). Tokyo 2400m Long, Turf, Right.",
        )
        self.assertNotIn("Henry", html)
        # People go on to the page (the <noscript> link holds the plain address).
        self.assertIn(f'href="https://site.example/app/pvp?event=cm-{self.open_cm.id}"', html)
        self.assertRegex(
            meta(html, "property", "og:image"),
            rf"^https://site\.example/guide/cm-{self.open_cm.id}/preview\.jpg\?v=\w+$",
        )
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")

    def test_the_tab_it_was_copied_from_passes_through_and_nothing_else(self):
        html = self.page(f"/guide/cm-{self.open_cm.id}?style=pace")
        shared = f"https://site.example/guide/cm-{self.open_cm.id}?style=pace"
        self.assertEqual(meta(html, "property", "og:url"), shared)
        self.assertIn(f'href="https://site.example/app/pvp?event=cm-{self.open_cm.id}&amp;style=pace"', html)

        html = self.page(f"/guide/cm-{self.open_cm.id}?style=%3Cscript%3E")
        self.assertEqual(meta(html, "property", "og:url"), f"https://site.example/guide/cm-{self.open_cm.id}")

    def test_the_free_guide_is_open_while_the_rest_are_locked(self):
        self.guide(self.running, "End>Front")
        self.guide(self.past, "Front>End")

        running = self.page(f"/guide/cm-{self.running.id}")
        self.assertEqual(meta(running, "property", "og:title"), "Sagittarius Cup PvP guide")

        locked = self.page(f"/guide/cm-{self.past.id}")
        self.assertEqual(meta(locked, "property", "og:title"), "Champions Meeting 19 Scorpio Cup")
        description = meta(locked, "property", "og:description")
        self.assertNotIn("Front", description)
        self.assertNotIn("Speed", description)
        self.assertIn("Tokyo 2400m Long, Turf, Right.", description)
        self.assertIn(" through ", description)

    def test_the_gate_off_opens_every_guide(self):
        self.guide(self.past, "Front>End")
        with mock.patch.object(pvp_gate, "PVP_EARLY_ACCESS", False):
            html = self.page(f"/guide/cm-{self.past.id}")
        self.assertEqual(meta(html, "property", "og:title"), "Scorpio Cup PvP guide")
        self.assertIn("Front &gt; End.", meta(html, "property", "og:description"))

    def test_a_draft_or_no_guide_shows_the_event_alone(self):
        self.open_guide.is_published = False
        self.open_guide.save()
        html = self.page(f"/guide/cm-{self.open_cm.id}")
        self.assertEqual(meta(html, "property", "og:title"), "Champions Meeting 21 Capricorn Cup")
        self.assertNotIn("Pace", meta(html, "property", "og:description"))

        loh = make_league_of_heroes(name="League of Heroes 4 Dirt Mile", loh_number=4)
        html = self.page(f"/guide/loh-{loh.id}")
        self.assertEqual(meta(html, "property", "og:title"), "League of Heroes 4 Dirt Mile")

    def test_an_address_naming_no_event_gets_the_site_tags(self):
        for path in ("/guide/cm-999999", "/guide/nope", "/guide/cm-0x1"):
            html = self.page(path)
            self.assertEqual(meta(html, "property", "og:image"), "https://site.example/social-card.png")

    def test_the_picture_is_versioned_by_what_it_draws(self):
        html = self.page(f"/guide/cm-{self.open_cm.id}")
        image = meta(html, "property", "og:image").replace("https://site.example", "")

        response = self.client.get(image)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("immutable", response["Cache-Control"])
        self.assertEqual(response.content[:3], b"\xff\xd8\xff")

        # An edit draws a new picture under a new address; the old one redirects.
        self.open_guide.style_ranking = "End>Pace"
        self.open_guide.save()
        stale = self.client.get(image)
        self.assertEqual(stale.status_code, 302)
        self.assertNotEqual(stale["Location"], image)

        self.assertEqual(self.client.get("/guide/cm-999999/preview.jpg").status_code, 404)

    def test_opening_it_writes_nothing(self):
        before = PvpGuide.objects.count(), PvpStatConfig.objects.count()
        self.page(f"/guide/cm-{self.open_cm.id}")
        self.client.get(f"/guide/cm-{self.open_cm.id}/preview.jpg")
        self.assertEqual((PvpGuide.objects.count(), PvpStatConfig.objects.count()), before)


class FreeEventTests(CalculatorTestCase):
    """pvp_gate.free_event mirrors the frontend's defaultPvpEvent."""

    def free(self):
        return pvp_gate.free_event(maps=build_effective_date_maps())

    def test_the_running_event_wins_then_the_next_then_the_last(self):
        old = make_champions_meeting(cm_number=1, global_start_date=days(-30), global_end_date=days(-24))
        later = make_league_of_heroes(loh_number=2, global_start_date=days(20), global_end_date=days(26))
        sooner = make_champions_meeting(cm_number=3, global_start_date=days(10), global_end_date=days(16))
        self.assertEqual(self.free(), ("cm", sooner.id))

        running = make_league_of_heroes(loh_number=4, global_start_date=days(-1), global_end_date=days(5))
        self.assertEqual(self.free(), ("loh", running.id))

        for row in (later, sooner, running):
            row.delete()
        self.assertEqual(self.free(), ("cm", old.id))

    def test_a_cm_comes_first_on_a_tie(self):
        start, end = days(10), days(16)
        make_league_of_heroes(loh_number=5, global_start_date=start, global_end_date=end)
        cm = make_champions_meeting(cm_number=6, global_start_date=start, global_end_date=end)
        self.assertEqual(self.free(), ("cm", cm.id))

    def test_no_events_no_free_guide(self):
        self.assertIsNone(self.free())
