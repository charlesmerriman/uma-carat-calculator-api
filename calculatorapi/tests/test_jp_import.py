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
from calculatorapi.global_release import card_is_released, uma_is_released
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.test_skills import SNAPSHOT_DIR, ahead_skill, snapshot_skill, write_snapshot
from calculatorapi.tests.test_umas import snapshot_card

GOURMAND, BANQUET, RULER, FAN_SKILL = 201351, 110681, 100101111, 200999
SPECIAL_WEEK = 100101


def jp_skill(skill_id, name, rarity, **overrides):
    """One skill as the JP snapshot carries it: the same shape as global's."""
    fields = {"description": f"{name}の説明", "rarity": rarity, "group_id": skill_id // 10,
              "group_rate": 1, "icon_id": 20011, "cost": 100}
    fields.update(overrides)
    return snapshot_skill(skill_id, name, **fields)


def jp_outfit(card_id, **overrides):
    """One outfit as the JP snapshot carries it: ★3 Special Week by default,
    with Japanese names and three star rows (★3 to ★5)."""
    fields = {
        "chara_name": "スペシャルウィーク", "name": "[スペシャルドリーマー]スペシャルウィーク",
        "title": "[スペシャルドリーマー]", "default_rarity": 3, "running_style": "front",
    }
    fields.update(overrides)
    card = snapshot_card(card_id, **fields)
    card["stars"] = [
        {**card["stars"][2], "star": star, "stats": {**card["stars"][2]["stats"], "speed": speed}}
        for star, speed in ((3, 83), (4, 92), (5, 102))
    ]
    return card


def jp_support_card(card_id, card_type, chara_id=1068, **overrides):
    """One support card as the JP snapshot carries it, Kitasan Black's by default."""
    card = {"id": card_id, "chara_id": chara_id, "chara_name": "キタサンブラック",
            "name": "[新衣装]キタサンブラック", "title": "[新衣装]", "rarity": 3,
            "card_type": card_type, "skill_set_id": 1, "start_date": 0}
    card.update(overrides)
    return card


def evolution(skill_id, card_id, rank, condition_ids):
    return {
        "skill_id": skill_id, "card_id": card_id, "rank": rank, "start_date": 0,
        "conditions": [
            {"id": cid, "num": 1, "sub_num": 1, "timing_type": 1, "count_type": 1,
             "text": f"条件{cid}"}
            for cid in condition_ids
        ],
    }


class JpImportTests(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes,too-many-public-methods
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
            "cards": [jp_outfit(SPECIAL_WEEK)],
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
        # GameTora's names, by game id: search aliases only, never shown.
        self.gametora = {}

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
        gametora_dir = self.root / "gametora"
        gametora_dir.mkdir(exist_ok=True)
        (gametora_dir / "names.json").write_text(
            json.dumps({str(game_id): name for game_id, name in self.gametora.items()}),
            encoding="utf-8",
        )
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
        self.assertFalse(uma_is_released(self.special_week))

    def test_a_jp_outfit_takes_its_facts_from_jp_but_never_rarity_or_title(self):
        Uma.objects.filter(pk=self.special_week.pk).update(rarity=2, title="[Editor's Title]")
        self.run_import()

        uma = Uma.objects.get(pk=self.special_week.pk)
        # The facts the Encyclopedia shows, marked there as JP data.
        self.assertEqual((uma.running_style, uma.apt_turf, uma.apt_pace), (1, 7, 5))
        self.assertEqual((uma.base_speed, uma.growth_power), (83, 20))
        self.assertEqual([row["star"] for row in uma.star_stats], [3, 4, 5])
        self.assertEqual([row["speed"] for row in uma.star_stats], [83, 92, 102])
        self.assertEqual((uma.jp_name, uma.jp_title), ("スペシャルウィーク", "[スペシャルドリーマー]"))
        # Rarity feeds selectors and odds, the title is the editors' until global's.
        self.assertEqual((uma.rarity, uma.title), (2, "[Editor's Title]"))
        self.assertFalse(uma_is_released(uma))

    def test_a_jp_outfit_with_no_rarity_keeps_it_blank(self):
        self.run_import()
        self.special_week.refresh_from_db()
        self.assertIsNone(self.special_week.rarity)
        self.assertTrue(self.special_week.is_three_star)

    def test_global_wins_every_fact_and_jp_adds_only_the_japanese_name(self):
        global_card = snapshot_card(SPECIAL_WEEK, chara_name="Special Week", default_rarity=3,
                                    running_style="late", title="[Special Dreamer]")
        global_card["stars"] = [{**star, "star": star["star"] + 2} for star in global_card["stars"]]
        self.run_import(extra_global={"cards": [global_card]})

        uma = Uma.objects.get(pk=self.special_week.pk)
        self.assertEqual((uma.running_style, uma.title, uma.rarity), (3, "[Special Dreamer]", 3))
        # Global's three rows, not JP's speeds.
        self.assertEqual([row["speed"] for row in uma.star_stats], [87, 93, 98])
        self.assertEqual(uma.jp_name, "スペシャルウィーク")
        self.assertTrue(uma_is_released(uma))

    def test_a_distrusted_jp_outfit_takes_nothing(self):
        # Global names character 1001, so a JP-only outfit of it must be a row
        # with that name in it; this one names somebody else.
        wrong = Uma.objects.create(name="Somebody Else", game_id=100102)
        self.jp["cards"].append(jp_outfit(100102))
        self.run_import(extra_global={"cards": [snapshot_card(100103, chara_name="Special Week")]})

        wrong.refresh_from_db()
        self.assertEqual((wrong.apt_turf, wrong.star_stats, wrong.jp_name), (None, [], ""))

    def test_a_jp_card_takes_its_character_and_japanese_name(self):
        self.jp["support_cards"] = [jp_support_card(30300, "speed")]
        card = SupportCard.objects.create(name="[New Outfit] Kitasan Black", game_id=30300)
        self.run_import()

        card.refresh_from_db()
        self.assertEqual((card.card_type, card.character_id), ("speed", 1068))
        self.assertEqual((card.jp_name, card.jp_title), ("キタサンブラック", "[新衣装]"))

    def test_umatl_fills_titles_a_unique_sentence_and_a_race_name_each_in_its_own_slot(self):
        """notes/encyclopedia-jp-fills-plan.md, PR 3: UmaTL's title goes to `translated_title`
        (never `title`, the editors' until Global's), its unique sentence to the unique effect's
        `translated_text`, and its race name to a JP route's goal, marked `translated`."""
        self.jp["support_cards"] = [jp_support_card(30300, "speed")]
        self.jp["support_effects"] = [{"id": 30300, "effects": [{"type": 3, "levels": {"1": 5}}],
                                       "unique": {"level": 1, "effects": [
                                           {"type": 101, "value": 80, "params": [3, 1, 0, 0]}]}}]
        self.jp["objectives"] = [{"chara_id": SPECIAL_WEEK // 100, "goals": [
            {"turn": 12, "kind": 1, "race": "ジュニア級メイクデビュー", "grade": 900, "top": 0, "race_instance_id": 1},
            {"turn": 40, "kind": 1, "race": "新しいレース", "grade": 100, "top": 3, "race_instance_id": 2},
        ]}]
        self.umatl.update({
            "outfit_titles": {str(SPECIAL_WEEK): "[Special Dreamer]"},
            "card_titles": {"30300": "[New Outfit]"},
            "unique_effects": {"30300": "Speed Bonus (Friendship 80+)"},
            "race_names": {"1": "UmaTL Debut", "2": "A New Race"},
        })
        card = SupportCard.objects.create(name="Kitasan Black", game_id=30300)
        self.run_import(extra_global={"race_names": {"1": "Junior Make Debut"}})

        uma = Uma.objects.get(pk=self.special_week.pk)
        self.assertEqual((uma.title, uma.translated_title, uma.shown_title),
                         ("", "[Special Dreamer]", "[Special Dreamer]"))
        self.assertTrue(uma.uses_translated_title)
        # Global's race name wins; UmaTL names only the race Global has no name for.
        self.assertEqual([(goal["race"], goal.get("translated", False)) for goal in uma.career_objectives],
                         [("Junior Make Debut", False), ("A New Race", True)])
        card.refresh_from_db()
        self.assertEqual((card.title, card.translated_title), ("", "[New Outfit]"))
        self.assertEqual(card.unique_effect["translated_text"], "Speed Bonus (Friendship 80+)")

    def test_an_editors_title_beats_umatls(self):
        self.umatl["outfit_titles"] = {str(SPECIAL_WEEK): "[Special Dreamer]"}
        Uma.objects.filter(pk=self.special_week.pk).update(title="[Editor's Title]")
        self.run_import()
        uma = Uma.objects.get(pk=self.special_week.pk)
        self.assertEqual((uma.shown_title, uma.uses_translated_title), ("[Editor's Title]", False))

    def test_a_jp_card_whose_row_names_someone_else_gets_no_links(self):
        # Global names character 1068 (through its support card), so a JP card
        # of that character must be a row with that name in it.
        self.jp["support_cards"] = [jp_support_card(30300, "speed")]
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
            jp_support_card(30300, "speed"),
            # On global too, where it is a Wit card: global's type wins.
            jp_support_card(30028, "speed"),
        ]
        jp_card = SupportCard.objects.create(name="[New Outfit] Kitasan Black", game_id=30300)
        global_card = SupportCard.objects.create(name="Kitasan Black", game_id=30028)

        self.run_import(extra_global={"support_cards": [{
            "id": 30028, "chara_id": 1068, "chara_name": "Kitasan Black", "name": "Kitasan Black",
            "title": "", "rarity": 3, "card_type": "wit", "skill_set_id": 1,
        }]})

        jp_card.refresh_from_db()
        global_card.refresh_from_db()
        self.assertEqual((jp_card.card_type, card_is_released(jp_card)), ("speed", False))
        self.assertEqual(global_card.card_type, "wit")

    def test_a_jp_card_whose_row_names_someone_else_gets_no_type(self):
        self.jp["support_cards"] = [jp_support_card(30300, "speed")]
        wrong = SupportCard.objects.create(name="Somebody Else", game_id=30300)

        self.run_import(extra_global={"support_cards": [{
            "id": 30028, "chara_id": 1068, "chara_name": "Kitasan Black", "name": "Kitasan Black",
            "title": "", "rarity": 3, "card_type": "speed", "skill_set_id": 1,
        }]})

        wrong.refresh_from_db()
        self.assertEqual(wrong.card_type, "")

    def test_a_gametora_name_becomes_a_search_alias_and_is_never_shown(self):
        self.gametora = {
            FAN_SKILL: "Fan Favourite",   # new: an alias
            GOURMAND: "GOURMAND",         # global's name, any case: nothing
            BANQUET: "宴だ",               # the JP name: nothing
            999999: "No Such Skill",      # no row: skipped
        }
        out = self.run_import()

        self.assertEqual(
            list(SkillAlias.objects.values_list("skill__game_id", "name")),
            [(FAN_SKILL, "Fan Favourite")],
        )
        # Never shown: the translation is still what a player reads.
        self.assertEqual(self.skill(FAN_SKILL).shown_name, "Fan Skill")
        # But a lookup by it finds the skill.
        self.assertEqual(
            Skill.objects.resolve_names(["fan favourite"])["fan favourite"].game_id, FAN_SKILL,
        )
        self.assertIn("1 GameTora name(s) added as search aliases", out)
        # Aliases are only ever added, so a rerun has nothing to do.
        self.assertIn("Nothing to write.", self.run_import())

    def test_a_revised_gametora_name_keeps_the_old_one(self):
        self.gametora = {FAN_SKILL: "Fan Favourite"}
        self.run_import()
        self.gametora = {FAN_SKILL: "Fan's Favourite"}
        self.run_import()
        self.assertCountEqual(
            self.skill(FAN_SKILL).aliases.values_list("name", flat=True),
            ["Fan Favourite", "Fan's Favourite"],
        )

    def test_a_gametora_name_that_is_another_skills_name_keeps_meaning_that_skill(self):
        # GameTora calls the JP skill "Gourmand", which is global's name for another.
        self.gametora = {FAN_SKILL: "Gourmand"}
        out = self.run_import()
        self.assertTrue(SkillAlias.objects.filter(skill__game_id=FAN_SKILL, name="Gourmand").exists())
        self.assertIn(f"GameTora's name for {FAN_SKILL}, 'Gourmand', is also skill {GOURMAND}'s name", out)
        # A [[mention]] ranks shown names before aliases.
        self.assertEqual(Skill.objects.resolve_names(["Gourmand"])["gourmand"].game_id, GOURMAND)

    def test_global_only_ignores_both(self):
        self.gametora = {GOURMAND: "Big Eater"}
        self.run_import("--global-only")
        self.assertFalse(Skill.objects.filter(game_id=FAN_SKILL).exists())
        self.assertEqual(self.skill(GOURMAND).jp_name, "")
        self.assertFalse(SkillAlias.objects.exists())
        # Nor any JP fact on an outfit or a card.
        self.special_week.refresh_from_db()
        self.assertEqual((self.special_week.apt_turf, self.special_week.star_stats,
                          self.special_week.jp_name), (None, [], ""))


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
        # GameTora's names arrive as aliases, and none repeats its skill's own name.
        self.assertTrue(SkillAlias.objects.exists())
        for skill in Skill.objects.filter(aliases__isnull=False).prefetch_related("aliases"):
            own = {name.lower() for name in (skill.name, skill.translated_name, skill.jp_name) if name}
            self.assertFalse(own & {alias.name.lower() for alias in skill.aliases.all()}, skill.game_id)

        out = StringIO()
        call_command("import_game_data", "--no-input", "--snapshot", str(SNAPSHOT_DIR), stdout=out)
        self.assertIn("Nothing to write.", out.getvalue())
