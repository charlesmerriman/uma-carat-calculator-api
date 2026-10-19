"""A shared plan's link (/plan/<id>): the link-preview page.

Pins what the preview may say about a plan, because Discord and X keep their
own copy of it: totals and catalogue names always, notes only under
share_notes, and nothing about the owner ever.
"""

import io
import os
import re
import shutil
import tempfile

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from calculatorapi import plan_preview, plans
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

    def test_the_picture_is_the_drawn_card(self):
        self.row(make_uma_banner(name="T.M. Opera"), pulls=150)

        html = self.page()

        image = meta(html, "property", "og:image")
        self.assertRegex(
            image, rf"^https://site\.example/plan/{self.plan.public_id}/preview\.jpg\?v=[0-9a-f]{{10}}$"
        )
        self.assertEqual(meta(html, "property", "og:image:type"), "image/jpeg")
        self.assertEqual(meta(html, "property", "og:image:width"), "1200")
        self.assertEqual(meta(html, "property", "og:image:height"), "630")
        self.assertEqual(meta(html, "property", "og:image:alt"), "Shared pull plan: 150 pulls on 1 banner")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")

    def test_a_plan_with_no_art_still_gets_its_card(self):
        self.row()

        html = self.page()

        self.assertIn("/preview.jpg?v=", meta(html, "property", "og:image"))
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")

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


@override_settings(STORAGES=PLAIN_TEST_STORAGES, MEDIA_ROOT=MEDIA, FRONTEND_URL="https://site.example")
class PlanSharePictureTests(CalculatorTestCase):
    """The drawn card (plan_preview.py): what it says, where it lives, and that
    it never says more than the tags."""

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
        self.plan.share_notes = True
        self.plan.save()
        UserPlannedBanner.objects.create(
            user=self.owner, plan=self.plan, number_of_pulls=150, reserved_copies=0,
            note="secret note", banner_uma=make_uma_banner(name="T.M. Opera"),
        )

    def picture(self, query=""):
        return self.client.get(f"/plan/{self.plan.public_id}/preview.jpg{query}")

    def current_version(self):
        html = self.client.get(f"/plan/{self.plan.public_id}").content.decode()
        return meta(html, "property", "og:image").split("?v=")[1]

    def test_the_picture_is_a_large_card_jpeg(self):
        response = self.picture(f"?v={self.current_version()}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("immutable", response["Cache-Control"])
        picture = Image.open(io.BytesIO(response.content))
        self.assertEqual((picture.format, picture.size), ("JPEG", (1200, 630)))

    def test_it_says_the_totals_and_names_and_never_the_notes(self):
        with mock.patch.object(plan_preview, "render", wraps=plan_preview.render) as render:
            self.picture()
        drawn = render.call_args.args[0]
        self.assertEqual(drawn.headline, "150 pulls on 1 banner")
        self.assertEqual(drawn.names, "T.M. Opera")
        for private in ("secret note", "SecretName", "sharer", "My secret plan name"):
            self.assertNotIn(private, f"{drawn.headline} {drawn.names}")

    def test_an_edited_plan_gets_a_new_address(self):
        before = self.current_version()
        UserPlannedBanner.objects.filter(plan=self.plan).update(number_of_pulls=200)
        after = self.current_version()
        self.assertNotEqual(before, after)
        response = self.picture(f"?v={before}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/plan/{self.plan.public_id}/preview.jpg?v={after}")

    def test_rendered_once_then_served_from_the_cache(self):
        with mock.patch.object(plan_preview, "render", wraps=plan_preview.render) as render:
            self.picture()
            self.picture()
        self.assertEqual(render.call_count, 1)

    def test_no_picture_for_a_private_or_unknown_plan(self):
        self.plan.is_shared = False
        self.plan.save()
        self.assertEqual(self.picture().status_code, 404)
        self.assertEqual(self.client.get("/plan/nothere1/preview.jpg").status_code, 404)

    def test_the_art_is_drawn_behind_the_words(self):
        os.makedirs(MEDIA, exist_ok=True)
        timeline = make_timeline(name="Pictured")
        art = io.BytesIO()
        Image.new("RGB", (800, 400), (0, 200, 0)).save(art, format="PNG")
        timeline.image = SimpleUploadedFile("green.png", art.getvalue(), content_type="image/png")
        timeline.save()
        UserPlannedBanner.objects.filter(plan=self.plan).delete()
        UserPlannedBanner.objects.create(
            user=self.owner, plan=self.plan, number_of_pulls=10, reserved_copies=0,
            banner_uma=make_uma_banner(timeline=timeline, name="Pictured banner"),
        )
        picture = Image.open(io.BytesIO(self.picture().content)).convert("RGB")
        # The right edge, under the lighter end of the shade: still green.
        red, green, blue = picture.getpixel((1190, 600))
        self.assertGreater(green, red + 20)
        self.assertGreater(green, blue + 20)

    def test_unreadable_art_leaves_the_plain_card(self):
        timeline = make_timeline(name="Broken")
        timeline.image = SimpleUploadedFile("broken.png", b"not a png", content_type="image/png")
        timeline.save()
        UserPlannedBanner.objects.filter(plan=self.plan).delete()
        UserPlannedBanner.objects.create(
            user=self.owner, plan=self.plan, number_of_pulls=10, reserved_copies=0,
            banner_uma=make_uma_banner(timeline=timeline, name="Broken banner"),
        )
        response = self.picture()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(response.content)).size, (1200, 630))
