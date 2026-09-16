"""Umas as rows: the game id, its backfill, and the duplicate merge that runs first."""

import json
import tempfile
from contextlib import redirect_stdout
from importlib import import_module
from io import StringIO
from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.db import IntegrityError

from calculatorapi.models import (
    AnniversaryEventProduct,
    ChampionsMeetingUmaRecommendation,
    Uma,
    UmasOnUmaBanner,
    UserOshi,
    UserPlannedPurchase,
    UserStepUpSelection,
)
from calculatorapi.models.uma import game_id_from_image
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_anniversary_event,
    make_champions_meeting,
    make_step_up_banner,
    make_uma_banner,
    make_user,
)


def merge(*flags):
    """Run the command scripted and hand back what it printed."""
    out = StringIO()
    call_command("merge_duplicate_umas", "--no-input", *flags, stdout=out)
    return out.getvalue()


class MergeDuplicateUmasTests(CalculatorTestCase):
    """
    The scenario the command was written for: a rerun banner's backfill made a
    second "Seiun Sky (Rerun)" row sharing the original's image.
    """

    def setUp(self):
        self.original = Uma.objects.create(
            name="Seiun Sky", image="umas/102001-Seiun-Sky.png",
        )
        self.rerun = Uma.objects.create(
            name="Seiun Sky (Rerun)", image="umas/102001-Seiun-Sky.png",
        )
        # A different outfit of the same character: a different card id, so
        # it must never be pulled into the merge.
        self.ballroom = Uma.objects.create(
            name="Seiun Sky (Ballroom)", image="umas/102002-Seiun-Sky-Ballroom.png",
        )
        self.debut_banner = make_uma_banner(name="Seiun Sky")
        self.rerun_banner = make_uma_banner(name="Seiun Sky (Rerun)")
        UmasOnUmaBanner.objects.create(banner_uma=self.debut_banner, uma=self.original)
        UmasOnUmaBanner.objects.create(banner_uma=self.rerun_banner, uma=self.rerun)

    def test_relinks_the_rerun_banner_and_deletes_the_copy(self):
        merge()

        self.assertFalse(Uma.objects.filter(pk=self.rerun.pk).exists())
        self.assertTrue(Uma.objects.filter(pk=self.original.pk).exists())
        self.assertTrue(Uma.objects.filter(pk=self.ballroom.pk).exists())
        self.assertEqual(
            list(self.rerun_banner.umas.values_list("pk", flat=True)), [self.original.pk],
        )
        self.assertEqual(self.original.umasonumabanner_set.count(), 2)

    def test_drops_the_link_when_the_banner_already_lists_the_original(self):
        # The join has no unique constraint, so a blind re-point would show
        # the same uma twice on the tile.
        UmasOnUmaBanner.objects.create(banner_uma=self.rerun_banner, uma=self.original)

        out = merge()

        self.assertEqual(
            UmasOnUmaBanner.objects.filter(
                banner_uma=self.rerun_banner, uma=self.original,
            ).count(),
            1,
        )
        self.assertIn("dropped 1 row(s)", out)

    def test_dry_run_reports_and_writes_nothing(self):
        out = merge("--dry-run")

        self.assertIn("Would merge 1 duplicate(s)", out)
        self.assertIn("Seiun Sky (Rerun)", out)
        self.assertTrue(Uma.objects.filter(pk=self.rerun.pk).exists())
        self.assertEqual(self.rerun_banner.umas.get(), self.rerun)

    def test_second_run_finds_nothing(self):
        merge()
        out = merge()

        self.assertIn("Nothing to merge", out)
        self.assertEqual(Uma.objects.count(), 2)

    def test_prefix_of_the_newer_upload_style_is_the_same_id(self):
        # Newer uploads carry the star rarity in front: `3-102001-...`. Same
        # card id, so still the same outfit.
        self.rerun.image = "umas/3-102001-Seiun-Sky.png"
        self.rerun.save()

        merge()

        self.assertFalse(Uma.objects.filter(pk=self.rerun.pk).exists())

    def test_umas_without_an_image_are_never_grouped(self):
        Uma.objects.create(name="(All)")
        Uma.objects.create(name="(All) 2")

        out = merge()

        self.assertIn("Merged 1 uma(s)", out)
        self.assertEqual(Uma.objects.filter(name__startswith="(All)").count(), 2)

    def test_two_originals_sharing_an_image_are_left_alone(self):
        # Neither row says it is the copy, so the command cannot pick a
        # survivor. It reports the pair for an editor instead of guessing.
        self.rerun.name = "Seiun Sky (Summer)"
        self.rerun.save()

        out = merge()

        self.assertIn("Left alone: 1 group(s)", out)
        self.assertIn("2 rows lack the (Rerun) suffix", out)
        self.assertEqual(Uma.objects.count(), 3)

    def test_group_of_only_copies_is_left_alone(self):
        self.original.name = "Seiun Sky (rerun)"
        self.original.save()

        out = merge()

        self.assertIn("none is the original", out)
        self.assertEqual(Uma.objects.count(), 3)


class MergeDuplicateUmasRelationTests(CalculatorTestCase):
    """
    Every other model that points at an uma follows it to the kept row. These
    are the rows a CASCADE delete would otherwise silently take with the copy.
    """

    def setUp(self):
        self.original = Uma.objects.create(
            name="Narita Brian", image="umas/101601-Narita-Brian.png",
        )
        self.rerun = Uma.objects.create(
            name="Narita Brian (Rerun)", image="umas/101601-Narita-Brian.png",
        )
        self.user = make_user("merge_user")

    def test_oshi_follows_the_merge(self):
        UserOshi.objects.create(user=self.user, uma=self.rerun, position=0)

        merge()

        self.assertEqual(self.user.oshis.get().uma, self.original)

    def test_oshi_is_dropped_when_the_person_already_picked_the_original(self):
        # unique (user, uma) would reject the re-point. The copy's row is
        # dropped and the person keeps the original at its own position.
        UserOshi.objects.create(user=self.user, uma=self.original, position=0)
        UserOshi.objects.create(user=self.user, uma=self.rerun, position=1)

        out = merge()

        self.assertEqual(list(self.user.oshis.values_list("uma_id", "position")),
                         [(self.original.pk, 0)])
        self.assertIn("dropped 1 row(s)", out)

    def test_step_up_selection_follows_the_merge(self):
        step_up = make_step_up_banner(card_type="uma")
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=step_up, slot=1, uma=self.rerun,
        )

        merge()

        self.assertEqual(UserStepUpSelection.objects.get().uma, self.original)

    def test_planned_purchase_target_follows_the_merge(self):
        event = make_anniversary_event()
        selector = AnniversaryEventProduct.objects.create(
            anniversary_event=event, product_type="uma_selector",
            name="Uma Selector", usd_cost=21, paid_carat_amount=1500,
        )
        purchase = UserPlannedPurchase.objects.create(
            user=self.user, product=selector, quantity=1, target_uma=self.rerun,
        )

        merge()

        purchase.refresh_from_db()
        # SET_NULL on this FK: without the re-point the target would simply
        # vanish from the person's plan.
        self.assertEqual(purchase.target_uma, self.original)

    def test_champions_meeting_recommendation_follows_and_never_doubles(self):
        cm = make_champions_meeting()
        ChampionsMeetingUmaRecommendation.objects.create(uma=self.original, champions_meeting=cm)
        ChampionsMeetingUmaRecommendation.objects.create(uma=self.rerun, champions_meeting=cm)
        other_cm = make_champions_meeting(name="Other CM", cm_number=2)
        ChampionsMeetingUmaRecommendation.objects.create(uma=self.rerun, champions_meeting=other_cm)

        merge()

        self.assertEqual(
            sorted(
                ChampionsMeetingUmaRecommendation.objects.filter(uma=self.original)
                .values_list("champions_meeting_id", flat=True)
            ),
            sorted([cm.pk, other_cm.pk]),
        )
        self.assertEqual(ChampionsMeetingUmaRecommendation.objects.count(), 2)


class GameIdFromImageTests(CalculatorTestCase):
    """The filename convention the backfill and the merge both read."""

    def test_plain_filename(self):
        self.assertEqual(game_id_from_image("umas/102001-Seiun-Sky.png"), 102001)

    def test_rarity_prefixed_filename(self):
        # 17 prod rows on 2026-09-16 carry the star count first.
        self.assertEqual(game_id_from_image("umas/3-111801-Admire-Groove.png"), 111801)

    def test_no_dash_after_the_id_is_not_an_id(self):
        # The prod "(All)" placeholder: `1-101000.png`.
        self.assertIsNone(game_id_from_image("umas/1-101000.png"))

    def test_id_must_open_the_filename(self):
        self.assertIsNone(game_id_from_image("umas/Seiun-Sky-102001-alt.png"))

    def test_empty(self):
        self.assertIsNone(game_id_from_image(""))
        self.assertIsNone(game_id_from_image(None))


class GameIdColumnTests(CalculatorTestCase):

    def test_is_unique(self):
        Uma.objects.create(name="Seiun Sky", game_id=102001)
        with self.assertRaises(IntegrityError):
            Uma.objects.create(name="Seiun Sky (Rerun)", game_id=102001)

    def test_null_is_not_a_value(self):
        # Placeholder rows have no id and there can be several of them.
        Uma.objects.create(name="(All)")
        Uma.objects.create(name="(All) 2")
        self.assertEqual(Uma.objects.filter(game_id__isnull=True).count(), 2)


class GameIdBackfillTests(CalculatorTestCase):
    """
    0059's RunPython, exercised against the live registry. The historical
    model it asks for has the same two fields it reads, so the function runs
    unchanged.
    """

    @staticmethod
    def _backfill():
        migration = import_module("calculatorapi.migrations.0059_uma_game_id")
        out = StringIO()
        with redirect_stdout(out):
            migration.backfill_game_ids(apps, None)
        return out.getvalue()

    def test_fills_from_the_image_filename(self):
        plain = Uma.objects.create(name="Seiun Sky", image="umas/102001-Seiun-Sky.png")
        prefixed = Uma.objects.create(
            name="Admire Groove", image="umas/3-111801-Admire-Groove.png",
        )

        self._backfill()

        plain.refresh_from_db()
        prefixed.refresh_from_db()
        self.assertEqual(plain.game_id, 102001)
        self.assertEqual(prefixed.game_id, 111801)

    def test_leaves_the_later_duplicate_null_instead_of_failing(self):
        original = Uma.objects.create(name="Seiun Sky", image="umas/102001-Seiun-Sky.png")
        rerun = Uma.objects.create(
            name="Seiun Sky (Rerun)", image="umas/102001-Seiun-Sky.png",
        )

        out = self._backfill()

        original.refresh_from_db()
        rerun.refresh_from_db()
        self.assertEqual(original.game_id, 102001)
        self.assertIsNone(rerun.game_id)
        self.assertIn("run merge_duplicate_umas", out)

    def test_rows_without_an_id_stay_null_and_are_listed(self):
        placeholder = Uma.objects.create(name="(All)", image="umas/1-101000.png")
        bare = Uma.objects.create(name="No Picture")

        out = self._backfill()

        placeholder.refresh_from_db()
        bare.refresh_from_db()
        self.assertIsNone(placeholder.game_id)
        self.assertIsNone(bare.game_id)
        self.assertIn("2 uma(s) left without a game_id", out)


def write_snapshot(directory, cards=(), support_cards=()):
    """A minimal snapshot directory in the shape extract_master_snapshot.py writes."""
    directory = Path(directory)
    (directory / "cards.json").write_text(json.dumps(list(cards)), encoding="utf-8")
    (directory / "support_cards.json").write_text(
        json.dumps(list(support_cards)), encoding="utf-8",
    )
    return directory


def snapshot_card(card_id=102701, chara_name="Mejiro Ryan", default_rarity=1, **overrides):
    """One outfit as the snapshot describes it, ★1 Mejiro Ryan by default."""
    def star(number, speed):
        return {
            "star": number, "unique_skill_id": 10271,
            "stats": {"speed": speed, "stamina": 90, "power": 100, "guts": 80, "wit": 70},
            "max_stats": {"speed": 1200, "stamina": 1200, "power": 1200, "guts": 1200, "wit": 1200},
            "aptitude": {
                "turf": 7, "dirt": 1, "short": 2, "mile": 5, "medium": 7, "long": 7,
                "front": 1, "pace": 5, "late": 7, "end": 7,
            },
        }
    card = {
        "id": card_id, "chara_id": card_id // 100, "chara_name": chara_name,
        "name": f"[Down the Line] {chara_name}", "title": "[Down the Line]",
        "default_rarity": default_rarity, "running_style": "late",
        "growth": {"speed": 0, "stamina": 0, "power": 20, "guts": 0, "wit": 10},
        "stars": [star(1, 87), star(2, 93), star(3, 98)],
    }
    card.update(overrides)
    return card


def import_game_data(directory, *flags):
    out = StringIO()
    call_command("import_game_data", "--no-input", "--snapshot", str(directory), *flags, stdout=out)
    return out.getvalue()


class ImportGameDataUmaTests(CalculatorTestCase):
    """`import_game_data` filling the Uma columns from a snapshot directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.uma = Uma.objects.create(name="Mejiro Ryan", game_id=102701)

    def test_fills_every_game_column_from_the_initial_star_row(self):
        write_snapshot(self.tmp.name, cards=[snapshot_card()])

        import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertEqual(self.uma.title, "[Down the Line]")
        self.assertEqual(self.uma.rarity, 1)
        self.assertEqual(self.uma.running_style, 3)
        self.assertEqual(self.uma.apt_turf, 7)
        self.assertEqual(self.uma.apt_dirt, 1)
        self.assertEqual(self.uma.apt_end, 7)
        # Base stats come from the ★1 row, the card's initial star count.
        self.assertEqual(self.uma.base_speed, 87)
        self.assertEqual(self.uma.growth_power, 20)
        self.assertEqual(self.uma.growth_speed, 0)
        self.assertEqual(self.uma.character_id, 1027)

    def test_sets_is_three_star_from_the_rarity(self):
        # The checkbox the selector pickers read. It defaults to True, so a
        # ★1 card that was never unticked would otherwise stay selectable.
        write_snapshot(self.tmp.name, cards=[snapshot_card()])

        import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertFalse(self.uma.is_three_star)

    def test_three_star_card_ticks_the_box_back(self):
        self.uma.is_three_star = False
        self.uma.save()
        write_snapshot(self.tmp.name, cards=[snapshot_card(default_rarity=3)])

        import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertTrue(self.uma.is_three_star)
        self.assertEqual(self.uma.base_speed, 98)

    def test_never_touches_the_editor_columns(self):
        self.uma.purpose = "Great pace parent."
        self.uma.admin_comments = "keep"
        self.uma.image = "umas/102701-Mejiro-Ryan.png"
        self.uma.is_time_limited = True
        self.uma.save()
        write_snapshot(self.tmp.name, cards=[snapshot_card()])

        import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertEqual(self.uma.name, "Mejiro Ryan")
        self.assertEqual(self.uma.purpose, "Great pace parent.")
        self.assertEqual(self.uma.admin_comments, "keep")
        self.assertEqual(self.uma.image.name, "umas/102701-Mejiro-Ryan.png")
        self.assertTrue(self.uma.is_time_limited)

    def test_never_creates_an_uma(self):
        write_snapshot(self.tmp.name, cards=[snapshot_card(card_id=100101, chara_name="Special Week")])

        out = import_game_data(self.tmp.name)

        self.assertEqual(Uma.objects.count(), 1)
        self.assertIn("not in database 1", out)
        self.assertIn("100101", out)

    def test_name_mismatch_is_skipped_and_reported(self):
        # A wrong game_id would otherwise overwrite this row with another
        # card's numbers.
        write_snapshot(self.tmp.name, cards=[snapshot_card(chara_name="Special Week")])

        out = import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertIsNone(self.uma.rarity)
        self.assertIn("name mismatch 1", out)
        self.assertIn("check the id", out)

    def test_name_check_ignores_punctuation(self):
        # Our rows spell it "TM Opera O"; the game spells it "T.M. Opera O".
        self.uma.name = "TM Opera O (New Year)"
        self.uma.save()
        write_snapshot(self.tmp.name, cards=[snapshot_card(chara_name="T.M. Opera O")])

        import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertEqual(self.uma.rarity, 1)

    def test_alt_outfit_names_still_match_on_the_character(self):
        self.uma.name = "Mejiro Ryan (Summer)"
        self.uma.save()
        write_snapshot(self.tmp.name, cards=[snapshot_card()])

        import_game_data(self.tmp.name)

        self.uma.refresh_from_db()
        self.assertEqual(self.uma.rarity, 1)

    def test_dry_run_writes_nothing(self):
        write_snapshot(self.tmp.name, cards=[snapshot_card()])

        out = import_game_data(self.tmp.name, "--dry-run")

        self.uma.refresh_from_db()
        self.assertIsNone(self.uma.rarity)
        self.assertTrue(self.uma.is_three_star)
        self.assertIn("would update 1", out)
        self.assertIn("Dry run", out)

    def test_second_run_finds_everything_current(self):
        write_snapshot(self.tmp.name, cards=[snapshot_card()])
        import_game_data(self.tmp.name)

        out = import_game_data(self.tmp.name)

        self.assertIn("already current 1", out)
        self.assertIn("Nothing to write", out)

    def test_the_committed_snapshot_is_readable(self):
        # The real files, so a malformed commit fails here and not in prod.
        out = import_game_data(
            Path(__file__).resolve().parents[2] / "scripts" / "data" / "master_snapshot",
            "--dry-run",
        )
        self.assertIn("umas:", out)
        self.assertIn("support cards:", out)
