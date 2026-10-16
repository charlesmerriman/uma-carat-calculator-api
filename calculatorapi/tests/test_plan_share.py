"""A shared plan's link (/plan/<id>): the link-preview page.

Pins what the preview may say about a plan, because Discord and X keep their
own copy of it: totals and catalogue names always, notes only under
share_notes, and nothing about the owner ever.
"""

import re
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from calculatorapi import plans
from calculatorapi.models import DEFAULT_PLAN_NAME, UserPlannedBanner
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.tests.factories import (
    make_step_up_banner,
    make_support_banner,
    make_timeline,
    make_uma_banner,
    make_user,
)

MEDIA = tempfile.mkdtemp(prefix="plan-share-tests-")


def meta(html, attribute, name):
    """The content of <meta {attribute}="{name}">, or None."""
    match = re.search(rf'<meta {attribute}="{re.escape(name)}" content="([^"]*)"', html)
    return match.group(1) if match else None


# Plain file storage in a scratch folder: the default storage is the Spaces
# bucket, which a test must never read from or write to.
@override_settings(STORAGES=PLAIN_TEST_STORAGES, MEDIA_ROOT=MEDIA, FRONTEND_URL="https://site.example")
class PlanSharePageTests(CalculatorTestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.owner = make_user(username="sharer")
        self.owner.display_name = "SecretName"
        self.owner.save()
        self.plan = plans.get_active_plan(self.owner)
        self.plan.name = "My secret plan name"
        self.plan.is_shared = True
        self.plan.save()

    def row(self, banner=None, pulls=10, note="", **target):
        if not target:
            target = {"banner_uma": banner or make_uma_banner()}
        return UserPlannedBanner.objects.create(
            user=self.owner, plan=self.plan, number_of_pulls=pulls,
            reserved_copies=0, note=note, **target,
        )

    def page(self, public_id=None):
        response = self.client.get(f"/plan/{public_id or self.plan.public_id}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_tags_state_the_totals_and_the_banners(self):
        self.row(make_uma_banner(name="T.M. Opera"), pulls=150)
        self.row(pulls=50, banner_support=make_support_banner(name="Oguri Cap"))

        html = self.page()

        self.assertEqual(meta(html, "property", "og:title"), "Shared pull plan: 200 pulls on 2 banners")
        self.assertIn("Oguri Cap (support)", meta(html, "property", "og:description"))
        self.assertIn("T.M. Opera", meta(html, "property", "og:description"))
        self.assertEqual(meta(html, "name", "robots"), "noindex")
        share = f"https://site.example/plan/{self.plan.public_id}"
        self.assertEqual(meta(html, "property", "og:url"), share)
        self.assertIn(f'<link rel="canonical" href="{share}">', html)
        # Nothing about the owner, and not the plan's name or id.
        for private in ("SecretName", "sharer", "My secret plan name", DEFAULT_PLAN_NAME):
            self.assertNotIn(private, html)
        self.assertNotIn(f"/{self.plan.pk}", html)

    def test_step_up_rows_are_counted_as_steps(self):
        self.row(pulls=30)
        self.row(pulls=4, banner_step_up=make_step_up_banner())

        html = self.page()

        self.assertEqual(
            meta(html, "property", "og:title"),
            "Shared pull plan: 30 pulls and 4 step-up steps on 2 banners",
        )

    def test_long_plans_name_three_banners_then_count_the_rest(self):
        for name in ("A", "B", "C", "D", "E"):
            self.row(make_uma_banner(name=f"Banner {name}"))

        description = meta(self.page(), "property", "og:description")

        self.assertIn("and 2 more.", description)
        self.assertNotIn("Banner D", description)

    def test_notes_show_only_when_the_owner_shares_them(self):
        self.row(note="pity  here\nfirst")

        self.assertNotIn("pity", self.page())

        self.plan.share_notes = True
        self.plan.save()
        self.assertIn("Notes: pity here first", meta(self.page(), "property", "og:description"))

    def test_long_notes_are_cut_at_a_word(self):
        self.plan.share_notes = True
        self.plan.save()
        self.row(note="word " * 100)

        description = meta(self.page(), "property", "og:description")
        notes = description.split("Notes: ", 1)[1]

        self.assertTrue(notes.endswith("word…"))
        self.assertLessEqual(len(notes), 201)

    def test_notes_are_escaped(self):
        self.plan.share_notes = True
        self.plan.save()
        self.row(note='"><script>alert(1)</script>')

        html = self.page()

        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_the_first_banner_with_art_is_the_picture(self):
        bare = make_uma_banner(name="No art")
        timeline = make_timeline(name="Pictured")
        timeline.image = SimpleUploadedFile("banner.png", b"png", content_type="image/png")
        timeline.save()
        self.row(bare)
        self.row(make_uma_banner(timeline=timeline, name="Pictured banner"))

        html = self.page()

        image = meta(html, "property", "og:image")
        # Local storage gives a site-relative URL, made absolute for crawlers.
        self.assertRegex(image, r"^https://site\.example/.*banner_timelines/banner")
        self.assertEqual(meta(html, "property", "og:image:alt"), "Pictured banner")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")
        # Uploaded art's size is unknown, so no size tags.
        self.assertIsNone(meta(html, "property", "og:image:width"))

    def test_a_plan_with_no_art_keeps_the_site_picture(self):
        self.row()

        html = self.page()

        self.assertEqual(meta(html, "property", "og:image"), "https://site.example/social-card.png")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary")

    def test_people_are_sent_on_to_the_landing_card(self):
        html = self.page()

        self.assertIn("window.location.replace(", html)
        self.assertIn(f"/app/{self.plan.public_id}", html)

    def test_private_and_unknown_plans_unfurl_as_the_site(self):
        """One answer for both, like the summary's 404: a probe cannot tell a
        private plan's id from a made-up one."""
        self.row(make_uma_banner(name="Hidden banner"), note="hidden note")
        self.plan.is_shared = False
        self.plan.share_notes = True
        self.plan.save()

        for public_id in (self.plan.public_id, "nothere1"):
            with self.subTest(public_id=public_id):
                html = self.page(public_id)
                self.assertEqual(meta(html, "property", "og:title"), "Uma Musume Carat Calculator")
                self.assertNotIn("Hidden banner", html)
                self.assertNotIn("hidden note", html)

    def test_opening_the_link_counts_nothing(self):
        self.page()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.copy_count, 0)
