"""A gallery post's permanent link (/post/<id>) and its link-preview picture."""

import io
import os
import re
import shutil
import tempfile
from unittest import mock

from django.test import override_settings
from PIL import Image

from calculatorapi import gallery_preview
from calculatorapi.models import GalleryHiddenReason, GalleryPost
from calculatorapi.tests.base import PLAIN_TEST_STORAGES
from calculatorapi.tests.test_gallery import GalleryFixture

MEDIA = tempfile.mkdtemp(prefix="gallery-share-tests-")


def meta(html, attribute, name):
    """The content of <meta {attribute}="{name}">, or None."""
    match = re.search(rf'<meta {attribute}="{re.escape(name)}" content="([^"]*)"', html)
    return match.group(1) if match else None


# Plain file storage in a scratch folder: the default storage is the Spaces
# bucket, which a test must never read from or write to.
@override_settings(STORAGES=PLAIN_TEST_STORAGES, MEDIA_ROOT=MEDIA, FRONTEND_URL="https://site.example")
class SharePageTests(GalleryFixture):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.author = self.person(display_name="SecretName")
        self.post_row = self.post(self.author)
        GalleryPost.objects.filter(pk=self.post_row.pk).update(caption="secret caption")
        self.post_row.refresh_from_db()

    def page(self, public_id=None):
        response = self.client.get(f"/post/{public_id or self.post_row.public_id}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_tags_describe_the_team_and_nothing_else(self):
        html = self.page()
        title = meta(html, "property", "og:title")
        self.assertEqual(title, f"{self.cm.name} team: Maruzensky, Oguri Cap, Gold Ship")
        self.assertEqual(meta(html, "name", "twitter:card"), "summary_large_image")
        self.assertEqual(meta(html, "name", "robots"), "noindex")
        share = f"https://site.example/post/{self.post_row.public_id}"
        self.assertEqual(meta(html, "property", "og:url"), share)
        self.assertIn(f'<link rel="canonical" href="{share}">', html)
        self.assertEqual(
            meta(html, "property", "og:image"),
            f"{share}/preview.jpg?v={gallery_preview.image_version(self.post_row)}",
        )
        self.assertEqual(meta(html, "property", "og:image:width"), "1200")
        self.assertNotIn("SecretName", html)
        self.assertNotIn("secret caption", html)

    def test_people_are_sent_on_to_the_post(self):
        html = self.page()
        self.assertIn("window.location.replace(", html)
        self.assertIn(f"post\\u003D{self.post_row.public_id}", html)
        self.assertIn(f"event=cm-{self.cm.id}&amp;view=gallery&amp;post={self.post_row.public_id}", html)

    def test_hidden_or_unknown_posts_unfurl_as_the_site(self):
        GalleryPost.objects.filter(pk=self.post_row.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.MODERATOR
        )
        for public_id in (self.post_row.public_id, "nothere1"):
            with self.subTest(public_id=public_id):
                html = self.page(public_id)
                self.assertEqual(meta(html, "property", "og:title"), "Uma Musume Carat Calculator")
                self.assertEqual(meta(html, "property", "og:image"), "https://site.example/social-card.png")
                self.assertEqual(meta(html, "name", "twitter:card"), "summary")
                self.assertNotIn("SecretName", html)

    def image(self, query=""):
        return self.client.get(f"/post/{self.post_row.public_id}/preview.jpg{query}")

    def test_the_picture_is_a_large_card_jpeg(self):
        response = self.image(f"?v={gallery_preview.image_version(self.post_row)}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("immutable", response["Cache-Control"])
        picture = Image.open(io.BytesIO(response.content))
        self.assertEqual((picture.format, picture.size), ("JPEG", (1200, 630)))

    def test_a_real_portrait_is_drawn(self):
        os.makedirs(os.path.join(MEDIA, "umas"), exist_ok=True)
        Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(os.path.join(MEDIA, "umas", "100401.png"))
        picture = Image.open(io.BytesIO(self.image().content)).convert("RGB")
        column = gallery_preview.WIDTH // 3
        middle = (column // 2, gallery_preview.PORTRAIT_TOP + gallery_preview.PORTRAIT // 2)
        red, green, blue = picture.getpixel(middle)
        self.assertGreater(red, 200)
        self.assertLess(green + blue, 80)

    def test_rendered_once_then_served_from_the_cache(self):
        with mock.patch.object(gallery_preview, "render", wraps=gallery_preview.render) as render:
            self.image()
            self.image()
        self.assertEqual(render.call_count, 1)

    def test_an_old_version_redirects_to_the_current_one(self):
        response = self.image("?v=oldversion1")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"/post/{self.post_row.public_id}/preview.jpg?v={gallery_preview.image_version(self.post_row)}",
        )

    def test_the_drawing_is_part_of_the_version(self):
        """A new drawing gets a new address: the picture is served immutable,
        so a crawler that cached the old one would never ask again."""
        version = gallery_preview.image_version(self.post_row)
        self.assertTrue(version.startswith(self.post_row.preview_version))
        self.assertTrue(version.endswith(f".{gallery_preview.LAYOUT}"))
        # The bare hash a pre-strip preview was cached under now redirects.
        self.assertEqual(self.image(f"?v={self.post_row.preview_version}").status_code, 302)

    def test_the_strip_names_the_event_and_nothing_a_person_wrote(self):
        with mock.patch.object(gallery_preview.share_card, "brand_strip",
                               wraps=gallery_preview.share_card.brand_strip) as strip:
            self.image()
        self.assertEqual(strip.call_args.kwargs["right"], self.cm.name)
        for call in strip.call_args_list:
            self.assertNotIn("secret caption", str(call))
            self.assertNotIn("SecretName", str(call))

    def test_no_picture_for_a_hidden_post(self):
        GalleryPost.objects.filter(pk=self.post_row.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS
        )
        self.assertEqual(self.image().status_code, 404)

    def test_missing_portraits_become_silhouettes(self):
        # No files exist under MEDIA for these umas: every portrait read fails.
        shutil.rmtree(os.path.join(MEDIA, "umas"), ignore_errors=True)
        jpeg = gallery_preview.render(self.post_row.team_snapshot)
        self.assertEqual(Image.open(io.BytesIO(jpeg)).size, (1200, 630))
