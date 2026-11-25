"""A Timeline banner's link (/banner/<id>) and a FAQ question's (/help/<slug>).

Pins what each preview says, because Discord and X keep their own copy of it:
catalogue facts for a banner (who it features, its dates, and a branded card
over its art), the question and the start of its answer for a FAQ link. Pins
too that a person is sent on to the page the link names, and that an unknown
id or slug gets the site's own tags with a 200, like /post, /plan and /guide.
"""

import datetime
import io
import re
import shutil
import tempfile
from html import unescape

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from calculatorapi.models import (
    BannerTimeline, FaqCategory, FaqItem, SupportCard, SupportsOnSupportBanner, Uma, UmasOnUmaBanner,
)
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.tests.factories import make_support_banner, make_timeline, make_uma_banner
from calculatorapi.views.faq_share import excerpt, plain_text

MEDIA = tempfile.mkdtemp(prefix="link-preview-tests-")


def meta(html, attribute, name):
    """The content of <meta {attribute}="{name}">, unescaped, or None."""
    match = re.search(rf'<meta {attribute}="{re.escape(name)}" content="([^"]*)"', html)
    return unescape(match.group(1)) if match else None


def redirect_target(html):
    """Where the page's script sends a browser."""
    match = re.search(r'window\.location\.replace\("([^"]*)"\)', html)
    return match.group(1).encode().decode("unicode_escape") if match else None


def png(width=64, height=36):
    """A real (tiny) 16:9 PNG, so the picture has art to draw."""
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 60)).save(out, format="PNG")
    return out.getvalue()


def at(year, month, day, hour=22):
    return datetime.datetime(year, month, day, hour, tzinfo=datetime.timezone.utc)


@override_settings(STORAGES=PLAIN_TEST_STORAGES, MEDIA_ROOT=MEDIA, FRONTEND_URL="https://site.example")
class BannerSharePageTests(CalculatorTestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        # The row's name is an editor's label ("(Rerun)"); the preview never shows it.
        self.banner = make_timeline(
            name="Taiki Shuttle (Camping) + Yamanin Zephyr (Rerun)",
            global_start_date=at(2026, 6, 11), global_end_date=at(2026, 6, 20, 21),
        )
        self.banner.image = SimpleUploadedFile("taiki.png", png(), content_type="image/png")
        self.banner.save()
        uma_banner = make_uma_banner(self.banner, name="Taiki Shuttle (Camping) + Yamanin Zephyr")
        for name in ("Taiki Shuttle (Camping)", "Yamanin Zephyr"):
            UmasOnUmaBanner.objects.create(banner_uma=uma_banner, uma=Uma.objects.create(name=name))
        self.support = make_support_banner(self.banner, name="TM Opera O")
        SupportsOnSupportBanner.objects.create(
            banner_support=self.support, support_card=SupportCard.objects.create(name="TM Opera O"),
        )
        # A stand-in card is never named.
        SupportsOnSupportBanner.objects.create(
            banner_support=self.support,
            support_card=SupportCard.objects.create(name="Any SSR Speed", is_placeholder=True),
        )

    def page(self, path, status=200):
        response = self.client.get(path)
        self.assertEqual(response.status_code, status)
        return response.content.decode()

    def test_a_banner_says_who_it_features_and_its_dates_on_its_own_picture(self):
        html = self.page(f"/banner/{self.banner.id}")

        # The Timeline card's own title (featuredTitle), not the row's name.
        self.assertEqual(
            meta(html, "property", "og:title"), "Taiki Shuttle (Camping), Yamanin Zephyr and TM Opera O",
        )
        self.assertNotIn("Rerun", html)
        self.assertEqual(meta(html, "property", "og:description"), "2026/6/11 through 2026/6/20.")
        self.assertEqual(meta(html, "property", "og:url"), f"https://site.example/banner/{self.banner.id}")
        # The branded card (banner_preview.py), versioned, shown large.
        self.assertRegex(
            meta(html, "property", "og:image"),
            rf"^https://site\.example/banner/{self.banner.id}/preview\.jpg\?v=\w+$",
        )
        self.assertEqual(meta(html, "property", "og:image:width"), "1200")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")
        self.assertNotIn("Any SSR Speed", html)

    def test_people_are_sent_on_to_the_card_on_the_timeline(self):
        html = self.page(f"/banner/{self.banner.id}")
        target = f"https://site.example/app/timeline?focus=banner-{self.banner.id}"
        self.assertEqual(redirect_target(html), target)
        self.assertIn(f'href="{target}"', html)
        self.assertIn('<meta name="robots" content="noindex">', html)

    def test_a_predicted_banner_says_its_dates_are_estimated(self):
        # The anchor: the latest banner with both a JP and a confirmed global date.
        make_timeline(
            name="Anchor", jp_start_date=at(2024, 1, 1), jp_end_date=at(2024, 1, 10),
            global_start_date=at(2025, 1, 1), global_end_date=at(2025, 1, 10),
        )
        predicted = make_timeline(name="Kitasan Black", jp_start_date=at(2024, 6, 1), jp_end_date=at(2024, 6, 10))
        html = self.page(f"/banner/{predicted.id}")
        description = meta(html, "property", "og:description")
        self.assertIn(" through ", description)
        self.assertIn("estimated from the JP schedule.", description)

    def test_a_banner_with_nobody_art_or_dates_yet_still_gets_its_card(self):
        bare = BannerTimeline.objects.create(name="Mystery banner")
        html = self.page(f"/banner/{bare.id}")
        # Nobody announced: the row's name is all there is.
        self.assertEqual(meta(html, "property", "og:title"), "Mystery banner")
        # Nothing to say about it: the site's own description, never an empty one.
        self.assertTrue(meta(html, "property", "og:description"))
        # The card on the plain background.
        picture = self.client.get(f"/banner/{bare.id}/preview.jpg")
        self.assertEqual(picture.status_code, 200)
        self.assertEqual(picture["Content-Type"], "image/jpeg")

    def test_an_address_naming_no_banner_gets_the_site_tags_and_opens_the_timeline(self):
        """A 200, like /post, /plan and /guide: an old link still unfurls as
        the site (owner, 2026-11-25)."""
        for path in ("/banner/999999", "/banner/taiki", "/banner/0", "/banner/12x"):
            html = self.page(path)
            self.assertEqual(meta(html, "property", "og:title"), "Uma Musume Carat Calculator", path)
            self.assertEqual(meta(html, "property", "og:image"), "https://site.example/social-card.png")
            self.assertEqual(redirect_target(html), "https://site.example/app/timeline")

    def test_the_picture_is_a_versioned_branded_card(self):
        image = meta(self.page(f"/banner/{self.banner.id}"), "property", "og:image")
        path = image.removeprefix("https://site.example")
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("immutable", response["Cache-Control"])
        picture = Image.open(io.BytesIO(response.content))
        self.assertEqual(picture.size, (1200, 630))

        # Who it features changed: a new address, and the old one redirects to it.
        UmasOnUmaBanner.objects.create(
            banner_uma=self.banner.uma_banners.first(), uma=Uma.objects.create(name="Kitasan Black"),
        )
        stale = self.client.get(path)
        self.assertEqual(stale.status_code, 302)
        self.assertNotEqual(stale["Location"], path)

        self.assertEqual(self.client.get("/banner/999999/preview.jpg").status_code, 404)

    def test_art_that_cannot_be_read_leaves_the_plain_card(self):
        self.banner.image = SimpleUploadedFile("broken.png", b"not a picture", content_type="image/png")
        self.banner.save()
        with self.assertLogs("calculatorapi", level="WARNING"):
            response = self.client.get(f"/banner/{self.banner.id}/preview.jpg")
        self.assertEqual(response.status_code, 200)

    def test_opening_it_writes_nothing(self):
        before = BannerTimeline.objects.count(), SupportCard.objects.count()
        self.page(f"/banner/{self.banner.id}")
        self.client.get(f"/banner/{self.banner.id}/preview.jpg")
        self.assertEqual((BannerTimeline.objects.count(), SupportCard.objects.count()), before)

    def test_a_long_list_is_cut_in_the_title_and_given_whole_below(self):
        """As the card does for a Golden Week revival: two names and a count,
        then everyone in the description. A card sharing an uma's name once."""
        for name in ("Kitasan Black", "Taiki Shuttle (Camping)"):
            SupportsOnSupportBanner.objects.create(
                banner_support=self.support, support_card=SupportCard.objects.create(name=name),
            )
        html = self.page(f"/banner/{self.banner.id}")
        self.assertEqual(meta(html, "property", "og:title"), "Taiki Shuttle (Camping), Yamanin Zephyr and 2 more")
        self.assertEqual(
            meta(html, "property", "og:description"),
            "2026/6/11 through 2026/6/20. "
            "Featuring Taiki Shuttle (Camping), Yamanin Zephyr, TM Opera O and Kitasan Black.",
        )


@override_settings(FRONTEND_URL="https://site.example")
class FaqSharePageTests(CalculatorTestCase):

    def setUp(self):
        category = FaqCategory.objects.create(slug="test-share-section", title="Banners", order=99)
        self.item = FaqItem.objects.create(
            category=category,
            slug="test-how-do-step-up-banners-work",
            question="How do step-up banners work?",
            answer=(
                "Step-up banners are **paid-only** banners. See the [Timeline](/app/timeline).\n\n"
                "- Step 1 costs 750 paid carats\n- Step 5 is ==guaranteed=="
            ),
        )

    def page(self, path, status=200):
        response = self.client.get(path)
        self.assertEqual(response.status_code, status)
        return response.content.decode()

    def test_a_question_says_its_question_and_the_start_of_its_answer(self):
        html = self.page(f"/help/{self.item.slug}")
        self.assertEqual(meta(html, "property", "og:title"), "How do step-up banners work?")
        self.assertEqual(
            meta(html, "property", "og:description"),
            "Step-up banners are paid-only banners. See the Timeline. Step 1 costs 750 paid carats "
            "Step 5 is guaranteed",
        )
        self.assertEqual(meta(html, "property", "og:url"), f"https://site.example/help/{self.item.slug}")
        self.assertEqual(meta(html, "property", "og:image"), "https://site.example/social-card.png")

    def test_people_are_sent_on_to_the_question_on_the_faq(self):
        html = self.page(f"/help/{self.item.slug}")
        self.assertEqual(redirect_target(html), f"https://site.example/faq#{self.item.slug}")
        self.assertIn('<meta name="robots" content="noindex">', html)

    def test_an_unknown_question_gets_the_faq_tags_and_opens_the_faq(self):
        html = self.page("/help/no-such-question")
        self.assertEqual(meta(html, "property", "og:title"), "FAQ | Uma Musume Carat Calculator")
        self.assertEqual(redirect_target(html), "https://site.example/faq")

    def test_a_long_answer_is_cut_at_a_word(self):
        text = plain_text("word " * 200)
        cut = excerpt(text)
        self.assertLessEqual(len(cut), 301)
        self.assertTrue(cut.endswith("word…"))

    def test_markdown_comes_out_as_plain_text(self):
        self.assertEqual(
            plain_text("## Heading\n\n> A *quote* with `code`, __bold__ and ![pic](/x.png).\n\n1. One\n2. Two"),
            "Heading A quote with code, bold and pic. One Two",
        )
        # Underscores inside a word are not emphasis.
        self.assertEqual(plain_text("snake_case_name stays"), "snake_case_name stays")

    def test_opening_it_writes_nothing(self):
        before = FaqItem.objects.count()
        self.page(f"/help/{self.item.slug}")
        self.assertEqual(FaqItem.objects.count(), before)
