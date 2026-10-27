"""
import_game_data with the JP snapshot and the UmaTL slice: who wins each
column, what stays hidden, evolutions, and the links of JP-only outfits.

Each test builds a small snapshot trio in a temporary folder, laid out as the
repo lays it out (master_snapshot/ beside jp_snapshot/ and umatl/), so the
command finds the two extra sources the way it does in a real run.
"""

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command

from calculatorapi.models import (
    Skill, SkillAlias, SkillEvolutionCondition, SupportCard, SupportCardSkill, Uma, UmaSkill,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.test_skills import SNAPSHOT_DIR, ahead_skill, snapshot_skill, write_snapshot

GOURMAND, BANQUET, RULER, FAN_SKILL = 201351, 110681, 100101111, 200999
SPECIAL_WEEK = 100101


def jp_skill(skill_id, name, rarity, **overrides):
    """One skill as the JP snapshot carries it: the same shape as global's."""
    fields = {"description": f"{name}の説明", "rarity": rarity, "group_id": skill_id // 10,
              "group_rate": 1, "icon_id": 20011, "cost": 100}
    fields.update(overrides)
    return snapshot_skill(skill_id, name, **fields)


def evolution(skill_id, card_id, rank, condition_ids):
    return {
        "skill_id": skill_id, "card_id": card_id, "rank": rank, "start_date": 0,
        "conditions": [
            {"id": cid, "num": 1, "sub_num": 1, "timing_type": 1, "count_type": 1,
             "text": f"条件{cid}"}
            for cid in condition_ids
        ],
    }


class JpImportTests(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.global_dir = self.root / "master_snapshot"
        self.global_dir.mkdir()
        self.special_week = Uma.objects.create(name="Special Week", game_id=SPECIAL_WEEK)

        self.global_skills = [snapshot_skill(GOURMAND, "Gourmand", rarity=2, group_id=20135,
                                             group_rate=2)]
        self.ahead = [
            ahead_skill(BANQUET, "Bring on the Banquet!"),
            ahead_skill(RULER, "Gluttonous Ruler"),
        ]
        self.jp = {
            "skills": [
                jp_skill(GOURMAND, "食いしん坊", rarity=2, group_rate=2, icon_id=20021, cost=170),
                jp_skill(BANQUET, "宴だ", rarity=5, icon_id=20013, cost=None),
                jp_skill(RULER, "はらぺこ大将", rarity=6, icon_id=20026, cost=None),
                jp_skill(FAN_SKILL, "JPスキル", rarity=1),
            ],
            "cards": [{"id": SPECIAL_WEEK, "chara_id": 1001}],
            "card_skills": [
                {"card_id": SPECIAL_WEEK, "skill_id": FAN_SKILL, "source": "innate", "level": None},
                {"card_id": SPECIAL_WEEK, "skill_id": GOURMAND, "source": "awakening", "level": 3},
            ],
            "support_cards": [],
            "support_hints": [],
            "evolutions": {
                "outfit": [evolution(RULER, SPECIAL_WEEK, 3, [10010101])],
                "scenario": [], "succession": [],
            },
        }
        self.umatl = {
            "skill_names": {str(FAN_SKILL): "Fan Skill", str(GOURMAND): "Glutton"},
            "skill_descriptions": {str(FAN_SKILL): "Fan text."},
            "evolution_conditions": {"10010101": "Reach 600 Stamina."},
        }
        self.condition_text = {"10010101": "Attain at least 600 Stamina"}

    def run_import(self, *flags, extra_global=None):
        """Write the three sources and import. `extra_global` replaces whole
        global files ({"support_cards": [...]}) after the defaults."""
        write_snapshot(self.global_dir, self.global_skills, ahead=self.ahead)
        for name, payload in (extra_global or {}).items():
            (self.global_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        (self.global_dir / "evolution_condition_text.json").write_text(
            json.dumps(self.condition_text), encoding="utf-8",
        )
        jp_dir = self.root / "jp_snapshot"
        jp_dir.mkdir(exist_ok=True)
        for name, payload in self.jp.items():
            (jp_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
        umatl_dir = self.root / "umatl"
        umatl_dir.mkdir(exist_ok=True)
        (umatl_dir / "text.json").write_text(json.dumps(self.umatl), encoding="utf-8")
        out = StringIO()
        call_command("import_game_data", "--no-input", "--snapshot", str(self.global_dir),
                     *flags, stdout=out)
        return out.getvalue()

    def skill(self, game_id):
        return Skill.objects.get(game_id=game_id)

    def test_each_path_takes_its_columns_from_the_right_source(self):
        self.run_import()

        # On global: global's name and structure, JP's and UmaTL's text beside it.
        gourmand = self.skill(GOURMAND)
        self.assertEqual((gourmand.availability, gourmand.name, gourmand.icon_id),
                         ("global", "Gourmand", 10011))
        self.assertEqual((gourmand.jp_name, gourmand.translated_name), ("食いしん坊", "Glutton"))
        self.assertEqual(gourmand.shown_name, "Gourmand")

        # Announced: global's text, JP's exact structure (no guessing), shown.
        banquet = self.skill(BANQUET)
        self.assertEqual((banquet.availability, banquet.name), ("announced", "Bring on the Banquet!"))
        self.assertEqual((banquet.rarity, banquet.icon_id, banquet.cost), (5, 20013, None))
        self.assertTrue(banquet.is_on_global)

        # JP only: the translation is what a player would read, and it is hidden.
        fan = self.skill(FAN_SKILL)
        self.assertEqual((fan.availability, fan.name, fan.shown_name), ("jp", "", "Fan Skill"))
        self.assertEqual((fan.jp_name, fan.shown_description), ("JPスキル", "Fan text."))
        self.assertFalse(fan.is_on_global)

    def test_an_evolution_gets_its_base_outfit_and_conditions(self):
        out = self.run_import()

        ruler = self.skill(RULER)
        self.assertEqual((ruler.availability, ruler.name), ("announced", "Gluttonous Ruler"))
        # Announced but evolved: kept from players until the site labels it.
        self.assertFalse(ruler.is_on_global)
        self.assertNotIn(ruler, Skill.objects.on_global())
        # The rank rule: the one gold Special Week learns at awakening rank 3.
        self.assertEqual(ruler.evolves_from, self.skill(GOURMAND))
        self.assertEqual(ruler.version_label, "evolved")
        row = UmaSkill.objects.get(skill=ruler)
        self.assertEqual((row.uma, row.source, row.level), (self.special_week, "evolved", 3))
        [condition] = ruler.evolution_conditions.all()
        self.assertEqual(
            (condition.text, condition.translated_text, condition.jp_text),
            ("Attain at least 600 Stamina", "Reach 600 Stamina.", "条件10010101"),
        )
        self.assertIn("evolutions:", out)

    def test_a_second_run_writes_nothing(self):
        self.run_import()
        self.assertIn("Nothing to write.", self.run_import())

    def test_an_editors_name_sticks_until_global_ships_one(self):
        self.run_import()
        Skill.objects.filter(game_id=FAN_SKILL).update(name="Early Name")
        self.run_import()
        self.assertEqual(self.skill(FAN_SKILL).name, "Early Name")

        # Global announces it: its official name wins, and the early one is
        # kept as an alias so a guide that wrote it still links.
        self.ahead.append(ahead_skill(FAN_SKILL, "Official Name"))
        self.run_import()
        fan = self.skill(FAN_SKILL)
        self.assertEqual((fan.name, fan.availability), ("Official Name", "announced"))
        self.assertEqual(list(fan.aliases.values_list("name", flat=True)), ["Early Name"])

    def test_a_revised_translation_leaves_an_alias(self):
        self.run_import()
        self.umatl["skill_names"][str(FAN_SKILL)] = "Better Fan Name"
        self.run_import()
        self.assertEqual(self.skill(FAN_SKILL).translated_name, "Better Fan Name")
        self.assertTrue(SkillAlias.objects.filter(skill__game_id=FAN_SKILL, name="Fan Skill").exists())

    def test_an_editors_base_correction_sticks(self):
        self.run_import()
        other = Skill.objects.create(game_id=200001, name="Other Gold", rarity=2, group_id=20000,
                                     tier=2)
        Skill.objects.filter(game_id=RULER).update(evolves_from=other)
        self.run_import()
        self.assertEqual(self.skill(RULER).evolves_from, other)

    def test_no_single_gold_at_the_rank_leaves_the_base_empty(self):
        self.jp["skills"].append(jp_skill(201361, "二つ目", rarity=2, group_rate=2))
        self.jp["card_skills"].append(
            {"card_id": SPECIAL_WEEK, "skill_id": 201361, "source": "awakening", "level": 3},
        )
        out = self.run_import()
        self.assertIsNone(self.skill(RULER).evolves_from)
        self.assertIn("no single gold at that rank", out)
        # The skill and its conditions still land.
        self.assertEqual(SkillEvolutionCondition.objects.filter(skill__game_id=RULER).count(), 1)

    def test_a_jp_outfit_gets_jp_links_and_stays_off_global(self):
        self.run_import()
        self.assertCountEqual(
            self.special_week.skills.values_list("skill__game_id", "source"),
            [(FAN_SKILL, "innate"), (GOURMAND, "awakening"), (RULER, "evolved")],
        )
        self.special_week.refresh_from_db()
        self.assertFalse(self.special_week.is_on_global)

    def test_a_jp_card_whose_row_names_someone_else_gets_no_links(self):
        # Global names character 1068 (through its support card), so a JP card
        # of that character must be a row with that name in it.
        self.jp["support_cards"] = [{"id": 30300, "chara_id": 1068, "card_type": "speed"}]
        self.jp["support_hints"] = [{"support_card_id": 30300, "skill_id": FAN_SKILL, "hint_group": 1}]
        wrong = SupportCard.objects.create(name="Somebody Else", game_id=30300)

        out = self.run_import(extra_global={"support_cards": [{
            "id": 30028, "chara_id": 1068, "chara_name": "Kitasan Black", "name": "Kitasan Black",
            "title": "", "rarity": 3, "card_type": "speed", "skill_set_id": 1,
        }]})

        self.assertFalse(SupportCardSkill.objects.filter(support_card=wrong).exists())
        self.assertIn("JP support card 30300", out)

    def test_a_jp_card_takes_its_type_from_jp_and_a_global_card_keeps_globals(self):
        self.jp["support_cards"] = [
            {"id": 30300, "chara_id": 1068, "card_type": "speed"},
            # On global too, where it is a Wit card: global's type wins.
            {"id": 30028, "chara_id": 1068, "card_type": "speed"},
        ]
        jp_card = SupportCard.objects.create(name="[New Outfit] Kitasan Black", game_id=30300)
        global_card = SupportCard.objects.create(name="Kitasan Black", game_id=30028)

        self.run_import(extra_global={"support_cards": [{
            "id": 30028, "chara_id": 1068, "chara_name": "Kitasan Black", "name": "Kitasan Black",
            "title": "", "rarity": 3, "card_type": "wit", "skill_set_id": 1,
        }]})

        jp_card.refresh_from_db()
        global_card.refresh_from_db()
        self.assertEqual((jp_card.card_type, jp_card.is_on_global), ("speed", False))
        self.assertEqual(global_card.card_type, "wit")

    def test_a_jp_card_whose_row_names_someone_else_gets_no_type(self):
        self.jp["support_cards"] = [{"id": 30300, "chara_id": 1068, "card_type": "speed"}]
        wrong = SupportCard.objects.create(name="Somebody Else", game_id=30300)

        self.run_import(extra_global={"support_cards": [{
            "id": 30028, "chara_id": 1068, "chara_name": "Kitasan Black", "name": "Kitasan Black",
            "title": "", "rarity": 3, "card_type": "speed", "skill_set_id": 1,
        }]})

        wrong.refresh_from_db()
        self.assertEqual(wrong.card_type, "")

    def test_global_only_ignores_both(self):
        self.run_import("--global-only")
        self.assertFalse(Skill.objects.filter(game_id=FAN_SKILL).exists())
        self.assertEqual(self.skill(GOURMAND).jp_name, "")


class CommittedJpSnapshotTests(CalculatorTestCase):
    """The real files: what reaches players must not move, and a rerun is a no-op."""

    def test_the_committed_snapshots_import_cleanly(self):
        call_command("import_game_data", "--no-input", "--snapshot", str(SNAPSHOT_DIR),
                     stdout=StringIO())

        defined = json.loads((SNAPSHOT_DIR / "skills.json").read_text(encoding="utf-8"))
        jp = json.loads((SNAPSHOT_DIR.parent / "jp_snapshot" / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual(Skill.objects.count(), len({s["id"] for s in defined} | {s["id"] for s in jp}))
        # Players see what global has: released, plus announced non-evolved.
        self.assertEqual(
            Skill.objects.on_global().count(),
            Skill.objects.filter(availability="global").count()
            + Skill.objects.filter(availability="announced").exclude(rarity=6).count(),
        )
        self.assertFalse(Skill.objects.on_global().filter(availability="jp").exists())
        # No guessing: every announced skill has the JP client's icon.
        self.assertFalse(Skill.objects.filter(availability="announced", icon_id__isnull=True).exists())
        # Every outfit evolution found its base by the rule.
        evolutions = json.loads(
            (SNAPSHOT_DIR.parent / "jp_snapshot" / "evolutions.json").read_text(encoding="utf-8")
        )
        evolved_ids = [e["skill_id"] for kind in evolutions.values() for e in kind]
        self.assertEqual(
            Skill.objects.filter(game_id__in=evolved_ids, evolves_from__isnull=False).count(),
            len(evolved_ids),
        )

        out = StringIO()
        call_command("import_game_data", "--no-input", "--snapshot", str(SNAPSHOT_DIR), stdout=out)
        self.assertIn("Nothing to write.", out.getvalue())
