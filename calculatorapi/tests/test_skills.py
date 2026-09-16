"""Skills: the imported table, its versions, and the icon linking."""

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from calculatorapi.models import CustomUser, Skill
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "data" / "master_snapshot"


def snapshot_skill(skill_id=200012, name="Right-Handed ○", **overrides):
    """One skill as extract_master_snapshot.py writes it: white Right-Handed by default."""
    skill = {
        "id": skill_id, "name": name,
        "description": "Moderately increase performance on right-handed tracks.",
        "rarity": 1, "group_id": 20001, "group_rate": 1,
        "icon_id": 10011, "skill_category": 0, "cost": 90,
        "precondition_1": "", "condition_1": "rotation==1",
        "precondition_2": "", "condition_2": "",
    }
    skill.update(overrides)
    return skill


def write_snapshot(directory, skills):
    directory = Path(directory)
    (directory / "skills.json").write_text(json.dumps(list(skills)), encoding="utf-8")
    (directory / "cards.json").write_text("[]", encoding="utf-8")
    (directory / "support_cards.json").write_text("[]", encoding="utf-8")
    return directory


def import_game_data(directory, *flags):
    out = StringIO()
    call_command("import_game_data", "--no-input", "--snapshot", str(directory), *flags, stdout=out)
    return out.getvalue()


class SkillImportTests(CalculatorTestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)

    def test_creates_skills_that_do_not_exist(self):
        write_snapshot(self.tmp.name, [
            snapshot_skill(), snapshot_skill(200011, "Right-Handed ◎", group_rate=2, cost=110),
        ])

        out = import_game_data(self.tmp.name)

        self.assertEqual(Skill.objects.count(), 2)
        white = Skill.objects.get(game_id=200012)
        self.assertEqual(white.name, "Right-Handed ○")
        self.assertEqual(white.rarity, 1)
        self.assertEqual(white.tier, 1)
        self.assertEqual(white.group_id, 20001)
        self.assertEqual(white.icon_id, 10011)
        self.assertEqual(white.cost, 90)
        self.assertEqual(white.condition, "rotation==1")
        self.assertIn("creating 2", out)

    def test_updates_the_game_columns_and_keeps_the_editor_ones(self):
        skill = Skill.objects.create(
            game_id=200012, name="Old Name", rarity=1, group_id=20001, tier=1,
            icon_id=10011, image="skills/hand-picked.png", admin_comments="keep",
        )
        write_snapshot(self.tmp.name, [snapshot_skill()])

        import_game_data(self.tmp.name)

        skill.refresh_from_db()
        self.assertEqual(skill.name, "Right-Handed ○")
        self.assertEqual(skill.cost, 90)
        self.assertEqual(skill.image.name, "skills/hand-picked.png")
        self.assertEqual(skill.admin_comments, "keep")

    def test_unique_skill_has_no_cost(self):
        write_snapshot(self.tmp.name, [
            snapshot_skill(100011, "Shooting Star", rarity=5, group_id=10001, cost=None),
        ])

        import_game_data(self.tmp.name)

        self.assertIsNone(Skill.objects.get().cost)

    def test_detailed_description_comes_only_from_the_gametora_file(self):
        write_snapshot(self.tmp.name, [snapshot_skill()])
        gametora = Path(self.tmp.name) / "gametora.json"
        gametora.write_text(json.dumps([
            {"id": "200012", "desc_en": "Speed +0.2 m/s on right-handed tracks."},
            {"id": 999, "desc_en": "not in the snapshot; ignored"},
        ]), encoding="utf-8")

        import_game_data(self.tmp.name)
        self.assertEqual(Skill.objects.get().description_detailed, "")

        import_game_data(self.tmp.name, "--gametora", str(gametora))
        self.assertEqual(
            Skill.objects.get().description_detailed, "Speed +0.2 m/s on right-handed tracks.",
        )

    def test_dry_run_creates_nothing(self):
        write_snapshot(self.tmp.name, [snapshot_skill()])

        out = import_game_data(self.tmp.name, "--dry-run")

        self.assertEqual(Skill.objects.count(), 0)
        self.assertIn("would create 1", out)

    def test_second_run_is_current(self):
        write_snapshot(self.tmp.name, [snapshot_skill()])
        import_game_data(self.tmp.name)

        out = import_game_data(self.tmp.name)

        self.assertIn("already current 1", out)
        self.assertEqual(Skill.objects.count(), 1)

    def test_the_committed_snapshot_imports_every_skill(self):
        # The real file: 718 skills, every one with a name, and the two
        # halves of every ★1/★2 unique both present.
        import_game_data(SNAPSHOT_DIR)

        self.assertGreaterEqual(Skill.objects.count(), 718)
        self.assertFalse(Skill.objects.filter(name="").exists())
        self.assertEqual(Skill.objects.filter(rarity=3).count(), Skill.objects.filter(rarity=4).count())
        self.assertEqual(Skill.objects.values("icon_id").distinct().count(), 63)


class SkillVersionsTests(CalculatorTestCase):
    """The white / gold / × grouping the admin reads back."""

    def setUp(self):
        self.white = Skill.objects.create(
            game_id=200012, name="Right-Handed ○", rarity=1, group_id=20001, tier=1, icon_id=10011,
        )
        self.gold = Skill.objects.create(
            game_id=200011, name="Right-Handed ◎", rarity=1, group_id=20001, tier=2, icon_id=10011,
        )
        self.penalty = Skill.objects.create(
            game_id=200013, name="Right-Handed ×", rarity=1, group_id=20001, tier=-1, icon_id=10014,
        )
        Skill.objects.create(
            game_id=200022, name="Left-Handed ○", rarity=1, group_id=20002, tier=1, icon_id=10011,
        )

    def test_siblings_are_the_same_group_best_tier_first(self):
        self.assertEqual(
            list(self.white.siblings().values_list("game_id", flat=True)), [200011, 200013],
        )

    def test_evolution_link_is_optional_and_self_referencing(self):
        evolved = Skill.objects.create(
            game_id=200011001, name="Right-Handed ◎ (evolved)", rarity=6,
            group_id=20001, tier=2, icon_id=10011, evolves_from=self.gold,
        )
        self.assertEqual(list(self.gold.evolutions.all()), [evolved])
        self.gold.delete()
        evolved.refresh_from_db()
        self.assertIsNone(evolved.evolves_from)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class LinkSkillImagesTests(CalculatorTestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.tmp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.with_icon = Skill.objects.create(
            game_id=200012, name="Right-Handed ○", rarity=1, group_id=20001, tier=1, icon_id=10011,
        )
        self.same_icon = Skill.objects.create(
            game_id=200022, name="Left-Handed ○", rarity=1, group_id=20002, tier=1, icon_id=10011,
        )
        self.without_icon = Skill.objects.create(
            game_id=200013, name="Right-Handed ×", rarity=1, group_id=20001, tier=-1, icon_id=10014,
        )
        default_storage.save("skills/10011.png", ContentFile(b"png"))

    def _run(self, *flags):
        out = StringIO()
        call_command("link_skill_images", *flags, stdout=out)
        return out.getvalue()

    def test_links_every_skill_whose_icon_file_exists(self):
        out = self._run()

        self.with_icon.refresh_from_db()
        self.same_icon.refresh_from_db()
        self.without_icon.refresh_from_db()
        self.assertEqual(self.with_icon.image.name, "skills/10011.png")
        self.assertEqual(self.same_icon.image.name, "skills/10011.png")
        self.assertFalse(self.without_icon.image)
        self.assertIn("skills/10014.png", out)

    def test_leaves_a_hand_picked_image_alone(self):
        self.with_icon.image = "skills/custom.png"
        self.with_icon.save()

        self._run()

        self.with_icon.refresh_from_db()
        self.assertEqual(self.with_icon.image.name, "skills/custom.png")

    def test_dry_run_writes_nothing(self):
        out = self._run("--dry-run")

        self.with_icon.refresh_from_db()
        self.assertFalse(self.with_icon.image)
        self.assertIn("Would link 2", out)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class SkillAdminTests(CalculatorTestCase):

    def setUp(self):
        self.client.force_login(CustomUser.objects.create_superuser(username="boss", password="x"))
        self.white = Skill.objects.create(
            game_id=200012, name="Right-Handed ○", rarity=1, group_id=20001, tier=1, icon_id=10011,
        )
        Skill.objects.create(
            game_id=200011, name="Right-Handed ◎", rarity=1, group_id=20001, tier=2, icon_id=10011,
        )

    def test_change_page_lists_the_other_versions(self):
        res = self.client.get(reverse("admin:calculatorapi_skill_change", args=[self.white.pk]))

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "◎ gold Right-Handed ◎ (200011)")

    def test_search_by_group_id_finds_the_family(self):
        res = self.client.get(reverse("admin:calculatorapi_skill_changelist"), {"q": "20001"})

        self.assertContains(res, "Right-Handed ○")
        self.assertContains(res, "Right-Handed ◎")
