"""A guide's link (/guide/<kind>-<id>): the link-preview page and its picture.

Pins what a preview may say, because Discord and X keep their own copy of it:
a Public guide's facts (ranking, stats, course) and never its prose; the event
alone for a Supporters guide, a Private one, or no guide (guide_access.py,
guide-visibility-plan.md); the site's own tags for an address naming no event.
"""

import datetime
import re
import shutil
import tempfile

from django.test import override_settings
from django.utils import timezone

from calculatorapi.models import PvpGuide, PvpStatConfig
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
        # A Public guide's event.
        self.open_cm = make_champions_meeting(
            name="Champions Meeting 21 Capricorn Cup", cm_number=21,
            global_start_date=days(30), global_end_date=days(36),
        )
        # Running now: no longer free by the calendar, only by its setting.
        self.running = make_champions_meeting(
            name="Champions Meeting 20 Sagittarius Cup", cm_number=20,
            global_start_date=days(-2), global_end_date=days(4),
        )
        # Over.
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
    def guide(event, ranking="", visibility="public", **fields):
        return PvpGuide.objects.create(
            champions_meeting=event, style_ranking=ranking, visibility=visibility,
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

    def test_a_supporters_guide_shows_the_event_alone_whatever_the_calendar(self):
        """The setting is the only rule (owner, 2026-11-02): the running
        event's guide is no longer free by itself, and an old Public one is."""
        self.guide(self.running, "End>Front", visibility="supporters")
        self.guide(self.past, "Front>End")

        locked = self.page(f"/guide/cm-{self.running.id}")
        self.assertEqual(meta(locked, "property", "og:title"), "Champions Meeting 20 Sagittarius Cup")
        description = meta(locked, "property", "og:description")
        self.assertNotIn("Front", description)
        self.assertNotIn("Speed", description)
        self.assertIn("Tokyo 2400m Long, Turf, Right.", description)
        self.assertIn(" through ", description)

        past = self.page(f"/guide/cm-{self.past.id}")
        self.assertEqual(meta(past, "property", "og:title"), "Scorpio Cup PvP guide")
        self.assertIn("Front &gt; End.", meta(past, "property", "og:description"))

    def test_a_draft_or_no_guide_shows_the_event_alone(self):
        self.open_guide.visibility = "private"
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
