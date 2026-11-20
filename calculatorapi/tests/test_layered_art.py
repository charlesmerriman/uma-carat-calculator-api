"""The layered uma art (notes/layered-uma-art-plan.md): the two fields, the
pair rule, WebP on upload, the link command and the routes that send it."""

import io
import tempfile
from io import StringIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from PIL import Image

from calculatorapi.models import Uma
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.views.uma import UmaSerializer


def png_bytes(size=(392, 420)):
    buffer = io.BytesIO()
    Image.new("RGBA", size, (200, 30, 30, 128)).save(buffer, "PNG")
    return buffer.getvalue()


class ScratchStorage(CalculatorTestCase):
    """Plain file storage in a scratch folder: the default storage is the
    Spaces bucket, which a test must never read from or write to."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        for override in (override_settings(STORAGES=PLAIN_TEST_STORAGES), override_settings(MEDIA_ROOT=self.tmp.name)):
            override.enable()
            self.addCleanup(override.disable)


class PortraitLayersTests(ScratchStorage):

    def test_none_without_both_layers(self):
        uma = Uma(name="Special Week", game_id=100101)
        self.assertIsNone(uma.portrait_layers)
        uma.image_layer_base = "umas_layered/base/100101-Special-Week.webp"
        self.assertIsNone(uma.portrait_layers, "half a pair never reaches a page")
        uma.image_layer_base, uma.image_layer_foreground = None, "umas_layered/foreground/100101-Special-Week.webp"
        self.assertIsNone(uma.portrait_layers)

    def test_both_layers_as_urls(self):
        uma = Uma(
            name="Special Week", game_id=100101,
            image_layer_base="umas_layered/base/100101-Special-Week.webp",
            image_layer_foreground="umas_layered/foreground/100101-Special-Week.webp",
        )
        self.assertEqual(uma.portrait_layers, {
            "base": default_storage.url("umas_layered/base/100101-Special-Week.webp"),
            "foreground": default_storage.url("umas_layered/foreground/100101-Special-Week.webp"),
        })


class WebpUploadTests(ScratchStorage):

    def test_an_uploaded_png_is_stored_as_lossless_webp(self):
        uma = Uma.objects.create(name="Special Week", game_id=100101)
        uma.image_layer_foreground = SimpleUploadedFile("100101-Special-Week.png", png_bytes(), "image/png")
        uma.save()

        uma.refresh_from_db()
        self.assertEqual(uma.image_layer_foreground.name, "umas_layered/foreground/100101-Special-Week.webp")
        with uma.image_layer_foreground.open("rb") as handle:
            stored = Image.open(handle)
            stored.load()
        self.assertEqual((stored.format, stored.size, stored.mode), ("WEBP", (392, 420), "RGBA"))
        self.assertEqual(stored.getpixel((5, 5)), (200, 30, 30, 128), "lossless keeps every pixel")

    def test_a_file_already_in_storage_is_left_alone(self):
        default_storage.save("umas_layered/base/100101-Special-Week.png", ContentFile(png_bytes((360, 360))))
        uma = Uma.objects.create(
            name="Special Week", game_id=100101, image_layer_base="umas_layered/base/100101-Special-Week.png",
        )
        uma.refresh_from_db()
        self.assertEqual(uma.image_layer_base.name, "umas_layered/base/100101-Special-Week.png")


class LinkLayersTests(ScratchStorage):
    """`link_borderless_art --kind layers`, which runs on every deploy."""

    def setUp(self):
        super().setUp()
        self.paired = Uma.objects.create(name="Phalaenopsis", game_id=114901)
        self.half = Uma.objects.create(name="Kiseki", game_id=113701)
        self.bare = Uma.objects.create(name="Vivlos", game_id=110801)
        for path in ("umas_layered/base/114901-Phalaenopsis.webp",
                     "umas_layered/foreground/114901-Phalaenopsis.webp",
                     "umas_layered/base/113701-Kiseki.webp"):
            default_storage.save(path, ContentFile(b"webp"))

    def _run(self, *flags):
        out = StringIO()
        call_command("link_borderless_art", "--kind", "layers", *flags, stdout=out)
        return out.getvalue()

    def test_links_both_layers_of_a_full_pair_only(self):
        out = self._run()

        for row in (self.paired, self.half):
            row.refresh_from_db()
        self.assertEqual(self.paired.image_layer_base.name, "umas_layered/base/114901-Phalaenopsis.webp")
        self.assertEqual(self.paired.image_layer_foreground.name, "umas_layered/foreground/114901-Phalaenopsis.webp")
        self.assertFalse(self.half.image_layer_base, "half a pair is never linked")
        self.assertIn("1 id(s) have a base or a foreground but not both: 113701", out)
        self.assertIn("Vivlos (110801)", out)

    def test_leaves_a_hand_pick_alone(self):
        self.paired.image_layer_foreground = "umas_layered/foreground/custom.webp"
        self.paired.save()

        self._run()

        self.paired.refresh_from_db()
        self.assertFalse(self.paired.image_layer_base)
        self.assertEqual(self.paired.image_layer_foreground.name, "umas_layered/foreground/custom.webp")

    def test_dry_run_writes_nothing(self):
        out = self._run("--dry-run")

        self.paired.refresh_from_db()
        self.assertFalse(self.paired.image_layer_base)
        self.assertIn("Would link 1 uma(s) from 1 layered pair(s).", out)


class RoutesSendLayersTests(ScratchStorage):
    """Every route that sends `portrait` sends `portrait_layers` beside it."""

    def setUp(self):
        super().setUp()
        self.uma = Uma.objects.create(
            name="Special Week", game_id=100101, image="umas/100101.png",
            image_layer_base="umas_layered/base/100101-Special-Week.webp",
            image_layer_foreground="umas_layered/foreground/100101-Special-Week.webp",
        )

    def test_the_picker_options(self):
        rows = self.client.get("/umas").json()
        row = next(row for row in (rows["results"] if isinstance(rows, dict) else rows) if row["id"] == self.uma.pk)
        self.assertEqual(row["portrait_layers"], self.uma.portrait_layers)

    def test_the_calculator_payload_uma(self):
        # /calculator-data nests this serializer in every banner it sends.
        self.assertEqual(UmaSerializer(self.uma).data["portrait_layers"], self.uma.portrait_layers)

    def test_the_encyclopedia_uma_page(self):
        page = self.client.get(f"/encyclopedia/umas/{self.uma.game_id}")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["portrait_layers"], self.uma.portrait_layers)
