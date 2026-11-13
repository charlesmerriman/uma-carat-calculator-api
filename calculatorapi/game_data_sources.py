"""
The game data's other sources, beside the global snapshot: the JP snapshot
(scripts/data/jp_snapshot/), the UmaTL slice (scripts/data/umatl/) and
GameTora's skill names (scripts/data/gametora/, search aliases only).

Read by `manage.py import_game_data`, which keeps the planning and writing;
this module only knows what the sources say. Global's own data always
outranks them (the command's docstring has the order).
"""

import json
from pathlib import Path

from django.core.management.base import CommandError

from .models import SkillAlias, SupportCard, Uma
from .models.skill import SkillRarity


def _letters(text):
    """Lowercase letters and digits only, so "TM Opera O" and "T.M. Opera O" agree."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def name_matches(row_name, chara_name):
    """Our row names always contain the character's name; a miss is a wrong game_id."""
    return _letters(chara_name) in _letters(row_name)


class SecondarySources:
    """
    The JP snapshot, the UmaTL slice and GameTora's skill names, any of which
    may be absent.

    All sit beside the global snapshot (scripts/data/jp_snapshot/,
    scripts/data/umatl/, scripts/data/gametora/) unless given on the command
    line. A missing one is not an error: a test's temporary snapshot has none,
    and an import without them behaves exactly as it did before 2026-10-23. A
    path given explicitly must exist.

    GameTora's names are never shown on the site (owner and Daptrius,
    2026-11-12): the import makes them SkillAlias rows, so search finds a
    skill by the name players know it by. scripts/fetch_gametora_skill_names.py
    has the why.
    """

    def __init__(self, global_dir, jp_dir=None, umatl_dir=None, gametora_dir=None):
        # The siblings are found only beside the real snapshot folder: beside
        # a test's temporary one they would be /tmp/jp_snapshot, which is
        # nobody's.
        global_dir = Path(global_dir)
        beside = global_dir.parent if global_dir.name == "master_snapshot" else None
        jp_path = Path(jp_dir) if jp_dir else (beside / "jp_snapshot" if beside else None)
        umatl_path = Path(umatl_dir) if umatl_dir else (beside / "umatl" if beside else None)
        gametora_path = Path(gametora_dir) if gametora_dir else (beside / "gametora" if beside else None)
        self.jp = self._load_jp(jp_path, required=bool(jp_dir)) if jp_path else None
        self.umatl = self._load_umatl(umatl_path, required=bool(umatl_dir)) if umatl_path else None
        self.jp_skills = {skill["id"]: skill for skill in self.jp["skills"]} if self.jp else {}
        # {game id: GameTora's English name}; {} when the file isn't there.
        self.gametora_names = (
            self._load_gametora_names(gametora_path, required=bool(gametora_dir)) if gametora_path else {}
        )

    @classmethod
    def empty(cls):
        """Neither source: the import as it was before the JP snapshot."""
        sources = cls.__new__(cls)
        sources.jp, sources.umatl, sources.jp_skills, sources.gametora_names = None, None, {}, {}
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
        # Optional: a JP snapshot from before 2026-11-12 has no card effects.
        path = directory / "support_effects.json"
        files["support_effects"] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        return files

    @staticmethod
    def _load_umatl(directory, required):
        path = directory / "text.json"
        if not path.is_file():
            if required:
                raise CommandError(f"UmaTL slice not found: {path}")
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _load_gametora_names(directory, required):
        path = directory / "names.json"
        if not path.is_file():
            if required:
                raise CommandError(f"GameTora names not found: {path}")
            return {}
        names = json.loads(path.read_text(encoding="utf-8"))
        return {int(game_id): name for game_id, name in names.items() if name.strip()}

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


# The columns that hold a name a player can search: global's, UmaTL's, the JP one.
NAME_COLUMNS = ("name", "translated_name", "jp_name")


def plan_gametora_aliases(gametora_names, rows, changed, created, replaced):  # pylint: disable=too-many-locals
    """
    ([(game id, name)] to add as aliases, [(game id, name, other game id)]
    clashes to report) for GameTora's names. `rows` are the skills in the
    database, `changed` / `created` / `replaced` the skill plan's lists, so
    the names compared are the ones each skill will have after this run.

    A name becomes an alias only when the skill doesn't already answer to it,
    ignoring case: not its global, UmaTL or JP name after this run, not an
    alias it already has, not a replaced name this run keeps. Aliases are only
    ever added, so a name GameTora later revises stays findable, which is what
    the table is for. A GameTora id with no skill row is skipped.

    A clash is a GameTora name that is ANOTHER skill's global or UmaTL name. It
    is still added: resolve_names ranks shown names before aliases, so a
    `[[mention]]` of it keeps meaning the other skill.
    """
    if not gametora_names:
        return [], []
    # {game id: {column: value}}: the database's names, then this run's.
    final_names = {row.game_id: {column: getattr(row, column) for column in NAME_COLUMNS}
                   for row in rows}
    for row, diff in changed:
        final_names[row.game_id].update({column: diff[column] for column in NAME_COLUMNS if column in diff})
    for row in created:
        final_names[row.game_id] = {column: getattr(row, column) for column in NAME_COLUMNS}
    known = {
        game_id: {value.lower() for value in names.values() if value}
        for game_id, names in final_names.items()
    }
    for game_id, name in SkillAlias.objects.values_list("skill__game_id", "name"):
        known.setdefault(game_id, set()).add(name.lower())
    for game_id, name in replaced:
        known.setdefault(game_id, set()).add(name.lower())
    shown_by = {}
    for game_id, names in final_names.items():
        for column in ("name", "translated_name"):
            if names[column]:
                shown_by.setdefault(names[column].lower(), set()).add(game_id)

    added, clashes = [], []
    for game_id, name in sorted(gametora_names.items()):
        if game_id not in final_names or name.lower() in known[game_id]:
            continue
        added.append((game_id, name))
        others = sorted(shown_by.get(name.lower(), set()) - {game_id})
        if others:
            clashes.append((game_id, name, others[0]))
    return added, clashes


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

    Also the facts global's files can't give a row it doesn't have, from the
    same JP snapshot (global's always win, because a row on global is never in
    these):
      `card_facts`  {game id: {"card_type", "character_id"}} for JP-only
                    support cards. The type first (a blank one matched no
                    filter in the team builder's card picker, board
                    2026-10-26), the character id for the Encyclopedia.
      `uma_cards`   {game id: the JP snapshot's outfit} for JP-only outfits,
                    which the command turns into aptitudes, stats and growth
                    (jp_uma_values). The Encyclopedia shows them marked as JP
                    data (owner, 2026-11-12).

    THE NAME CHECK, in English: a JP card's names are Japanese, so its
    character id is looked up in global's data instead, and our row's name
    must contain that character's English name, as for global cards. A
    character global doesn't have at all can't be checked; its rows are
    trusted and counted as `unchecked` in the report.
    """
    result = {"uma_rows": [], "card_rows": [], "card_facts": {}, "uma_cards": {},
              "uma_distrusted": set(), "card_distrusted": set(), "lines": [], "unchecked": 0}
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
    # A distrusted id is no better a key for its facts than for its skills.
    result["card_facts"] = _jp_card_facts(sources, jp_cards - result["card_distrusted"])
    result["uma_cards"] = {
        card["id"]: card for card in sources.jp["cards"]
        if card["id"] in jp_umas and card["id"] not in result["uma_distrusted"]
    }
    return result


def _jp_card_facts(sources, card_ids):
    """{game id: {"character_id", "card_type"}} for the JP-only cards in `card_ids`;
    a card the JP snapshot gives no type keeps its type out of the dict."""
    facts = {}
    for card in sources.jp["support_cards"]:
        if card["id"] in card_ids:
            facts[card["id"]] = {"character_id": card["chara_id"]}
            if card.get("card_type"):
                facts[card["id"]]["card_type"] = card["card_type"]
    return facts


def jp_names(sources, uma_distrusted, card_distrusted):
    """
    ({outfit id: {"jp_name", "jp_title"}}, {card id: {...}}) from the JP
    snapshot, for every outfit and card it holds, global ones included: the
    Japanese name is a fact about the row wherever it is out. An id either
    name check rejected (global's or jp_owner_links') is skipped, so a wrong
    game_id doesn't put another character's name on the row.
    """
    if sources.jp is None:
        return {}, {}
    return tuple(
        {
            card["id"]: {"jp_name": card["chara_name"], "jp_title": card["title"]}
            for card in cards if card["id"] not in distrusted
        }
        for cards, distrusted in (
            (sources.jp["cards"], uma_distrusted),
            (sources.jp["support_cards"], card_distrusted),
        )
    )
