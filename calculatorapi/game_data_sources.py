"""
The game data's second and third sources, beside the global snapshot: the JP
snapshot (scripts/data/jp_snapshot/) and the UmaTL slice (scripts/data/umatl/).

Read by `manage.py import_game_data`, which keeps the planning and writing;
this module only knows what the two sources say. Global's own data always
outranks both (the command's docstring has the order).
"""

import json
from pathlib import Path

from django.core.management.base import CommandError

from .models import SupportCard, Uma
from .models.skill import SkillRarity


def _letters(text):
    """Lowercase letters and digits only, so "TM Opera O" and "T.M. Opera O" agree."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def name_matches(row_name, chara_name):
    """Our row names always contain the character's name; a miss is a wrong game_id."""
    return _letters(chara_name) in _letters(row_name)


class SecondarySources:
    """
    The JP snapshot and the UmaTL slice, either of which may be absent.

    Both sit beside the global snapshot (scripts/data/jp_snapshot/ and
    scripts/data/umatl/) unless given on the command line. A missing one is
    not an error: a test's temporary snapshot has neither, and an import
    without them behaves exactly as it did before 2026-10-23. A path given
    explicitly must exist.
    """

    def __init__(self, global_dir, jp_dir=None, umatl_dir=None):
        # The siblings are found only beside the real snapshot folder: beside
        # a test's temporary one they would be /tmp/jp_snapshot, which is
        # nobody's.
        global_dir = Path(global_dir)
        beside = global_dir.parent if global_dir.name == "master_snapshot" else None
        jp_path = Path(jp_dir) if jp_dir else (beside / "jp_snapshot" if beside else None)
        umatl_path = Path(umatl_dir) if umatl_dir else (beside / "umatl" if beside else None)
        self.jp = self._load_jp(jp_path, required=bool(jp_dir)) if jp_path else None
        self.umatl = self._load_umatl(umatl_path, required=bool(umatl_dir)) if umatl_path else None
        self.jp_skills = {skill["id"]: skill for skill in self.jp["skills"]} if self.jp else {}

    @classmethod
    def empty(cls):
        """Neither source: the import as it was before the JP snapshot."""
        sources = cls.__new__(cls)
        sources.jp, sources.umatl, sources.jp_skills = None, None, {}
        return sources

    @staticmethod
    def _load_jp(directory, required):
        if not directory.is_dir():
            if required:
                raise CommandError(f"JP snapshot directory not found: {directory}")
            return None
        files = {}
        for name in ("skills", "cards", "card_skills", "support_cards", "support_hints", "evolutions"):
            path = directory / f"{name}.json"
            if not path.is_file():
                raise CommandError(f"JP snapshot file missing: {path}")
            files[name] = json.loads(path.read_text(encoding="utf-8"))
        return files

    @staticmethod
    def _load_umatl(directory, required):
        path = directory / "text.json"
        if not path.is_file():
            if required:
                raise CommandError(f"UmaTL slice not found: {path}")
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def text_slots(self, game_id):
        """
        The translated and Japanese text for one skill. Each source is written
        only when it is loaded: with UmaTL loaded, a translation it dropped
        becomes "" (the column is the import's), and without it the column is
        left alone.
        """
        values = {}
        jp = self.jp_skills.get(game_id)
        if jp is not None:
            values["jp_name"] = jp["name"]
            values["jp_description"] = jp["description"]
        if self.umatl is not None:
            key = str(game_id)
            values["translated_name"] = self.umatl["skill_names"].get(key, "")
            values["translated_description"] = self.umatl["skill_descriptions"].get(key, "")
        return values

    def condition_translation(self, condition_id):
        if self.umatl is None:
            return ""
        return self.umatl.get("evolution_conditions", {}).get(str(condition_id), "")


def evolution_base(evolution, gold_awakening):
    """
    The skill an OUTFIT's evolution evolves from, or None when the rule
    doesn't give exactly one.

    The game stores no base for these (skills-plan-3.md, "Discovery"). The
    rule, approved by the owner on 2026-10-23: an outfit's evolution at
    awakening rank N evolves the one gold skill that outfit learns at rank N.
    It held for all 631 JP outfit evolutions (2026-10-02 data), and the
    effects match (Special Week's rank-3 mid-race recovery gold evolves into
    a mid-race recovery). `gold_awakening` is {(card id, rank): [gold ids]}.
    """
    golds = gold_awakening.get((evolution["card_id"], evolution["rank"]), [])
    return golds[0] if len(golds) == 1 else None


def plan_evolutions(sources, condition_text):
    """
    Everything the JP snapshot says about evolved skills, as plain data:

      bases       {evolved id: base id}: the game's own base for scenario and
                  succession evolutions, the rank rule's for an outfit's
      ruled       the evolved ids whose base came from the rule (written
                  only while empty, so an editor's correction sticks)
      unruled     outfit evolutions the rule couldn't place, for the report
      scenarios   {evolved id: scenario id}
      junction    (card id, evolved id, "evolved", level) rows for UmaSkill
      conditions  {evolved id: [condition values]}

    Condition text: global's official English when it shipped it
    (evolution_condition_text.json), UmaTL's translation, and the Japanese.
    """
    plan = {"bases": {}, "ruled": set(), "unruled": [], "scenarios": {},
            "junction": [], "conditions": {}}
    if sources.jp is None:
        return plan
    gold_awakening = {}
    for row in sources.jp["card_skills"]:
        skill = sources.jp_skills.get(row["skill_id"])
        if row["source"] == "awakening" and skill and skill["rarity"] == SkillRarity.GOLD:
            gold_awakening.setdefault((row["card_id"], row["level"]), []).append(row["skill_id"])

    evolutions = sources.jp["evolutions"]
    for evolution in evolutions["outfit"]:
        base = evolution_base(evolution, gold_awakening)
        if base is None:
            plan["unruled"].append(
                f"evolved skill {evolution['skill_id']} (outfit {evolution['card_id']}, "
                f"rank {evolution['rank']}): no single gold at that rank; base left empty"
            )
        else:
            plan["bases"][evolution["skill_id"]] = base
            plan["ruled"].add(evolution["skill_id"])
        plan["junction"].append((evolution["card_id"], evolution["skill_id"], "evolved", evolution["rank"]))
    for evolution in evolutions["succession"]:
        plan["bases"][evolution["skill_id"]] = evolution["base_skill_id"]
        plan["junction"].append((evolution["card_id"], evolution["skill_id"], "evolved", None))
    for evolution in evolutions["scenario"]:
        plan["bases"][evolution["skill_id"]] = evolution["base_skill_id"]
        plan["scenarios"][evolution["skill_id"]] = evolution["scenario_id"]

    for kind in ("outfit", "succession", "scenario"):
        for evolution in evolutions[kind]:
            plan["conditions"][evolution["skill_id"]] = [
                {
                    "game_id": condition["id"],
                    "num": condition["num"],
                    "sub_num": condition["sub_num"],
                    "timing_type": condition["timing_type"],
                    "count_type": condition["count_type"],
                    "text": condition_text.get(str(condition["id"]), ""),
                    "translated_text": sources.condition_translation(condition["id"]),
                    "jp_text": condition["text"],
                }
                for condition in evolution["conditions"]
            ]
    return plan


def jp_owner_links(sources, snapshot):
    """
    Skill links for outfits and support cards global doesn't have, from the
    JP snapshot. Outfits and cards ON global keep global's links only: JP's
    lists can differ, and global's are what a player there gets.

    THE NAME CHECK, in English: a JP card's names are Japanese, so its
    character id is looked up in global's data instead, and our row's name
    must contain that character's English name, as for global cards. A
    character global doesn't have at all can't be checked; its rows are
    trusted and counted as `unchecked` in the report.
    """
    result = {"uma_rows": [], "card_rows": [], "uma_distrusted": set(),
              "card_distrusted": set(), "lines": [], "unchecked": 0}
    if sources.jp is None:
        return result
    english = {card["chara_id"]: card["chara_name"] for card in snapshot["cards"]}
    english.update(
        (card["chara_id"], card["chara_name"])
        for card in snapshot["support_cards"] if card["chara_name"]
    )
    for label, model, jp_cards, global_cards, distrusted in (
        ("outfit", Uma, sources.jp["cards"], snapshot["cards"], result["uma_distrusted"]),
        ("support card", SupportCard, sources.jp["support_cards"], snapshot["support_cards"],
         result["card_distrusted"]),
    ):
        on_global = {card["id"] for card in global_cards}
        rows = {row.game_id: row for row in model.objects.exclude(game_id__isnull=True)}
        for card in jp_cards:
            row = rows.get(card["id"])
            if row is None or card["id"] in on_global:
                continue
            name = english.get(card["chara_id"])
            # A group card is filed under ONE member's character, and named
            # for the group ("Carvers of History"), so there is nothing to
            # check its name against either.
            if name is None or card.get("card_type") == "group":
                result["unchecked"] += 1
            elif not name_matches(row.name, name):
                distrusted.add(card["id"])
                result["lines"].append(
                    f"JP {label} {card['id']}: our pk={row.pk} '{row.name}' is not {name}; "
                    "links skipped, check the id"
                )
    jp_umas = {card["id"] for card in sources.jp["cards"]} - {c["id"] for c in snapshot["cards"]}
    jp_cards = (
        {card["id"] for card in sources.jp["support_cards"]}
        - {card["id"] for card in snapshot["support_cards"]}
    )
    result["uma_rows"] = [
        (row["card_id"], row["skill_id"], row["source"], row["level"])
        for row in sources.jp["card_skills"] if row["card_id"] in jp_umas
    ]
    result["card_rows"] = [
        (row["support_card_id"], row["skill_id"], "hint", None)
        for row in sources.jp["support_hints"] if row["support_card_id"] in jp_cards
    ]
    return result
