"""
extract_master_snapshot.py

Reads a game client's own database (master.mdb, a plain SQLite file the Steam
client downloads on every update) and writes the slice this project needs as
JSON. The JSON is COMMITTED; the mdb is not. That is the point:
`manage.py import_game_data` reads the JSON, so every import is reviewable as a
diff and reproducible on a machine without the game.

Two regions, one reader. `--region global` (the default) reads the global
client into scripts/data/master_snapshot/. `--region jp` reads a JP mdb into
scripts/data/jp_snapshot/: the same files, in Japanese, for the skills, outfits
and cards global doesn't have yet. Nobody here runs the JP client, so its mdb
is rebuilt from a teammate's CSV export first (scripts/jp_export_to_mdb.py).
The two share ids: every global skill exists in JP under the same id (checked
2026-10-23), which is what lets the import match them.

What comes out, one file per concern (all sorted by id, so re-runs diff cleanly):

  meta.json           region, when, from which mdb (size, mtime), and row counts
  skills.json         every skill: name, description, rarity, white/gold group,
                      icon, skill point cost, activation conditions, and the
                      raw `effects` (see effect_slots)
  skill_text_ahead.json  global only: skills the client has official English
                      text for but does not define yet: id, name, description
  cards.json          every uma outfit ("card"): names, initial stars, running
                      style, growth bonuses, and per-star stats + aptitudes
  card_skills.json    which skills each outfit has: unique / innate / awakening
  support_cards.json  every support card: names, rarity, type, character, and
                      a group card's `members` (the umas it stands for)
  support_hints.json  which skills each support card can teach (hints)
  evolutions.json     evolved skills: whose they are, what they evolve from
                      when the game says, and the conditions that unlock them
                      (see extract_evolutions). Empty lists on global so far
  evolution_condition_text.json  global only: the official English text of
                      evolution conditions, keyed by condition id. Like skill
                      names, this text ships before global has the conditions
  objectives.json     global only: each character's career goals on the default
                      route (see extract_objectives)
  support_effects.json  every support card's effects as the game states them:
                      each effect's value at the levels the game names, and
                      the unique effect (see extract_support_effects)
  support_effect_types.json  global only: each effect type's English name and
                      help text ("Friendship Bonus", "Increases the
                      effectiveness of Friendship Training")
  sparks.json         global only: every spark (the game's "factor"), what a
                      veteran's export lists by id: kind, stars and name
  race_links.json     jp only: each CM's and LoH's course id and race
                      conditions, by number, which the admin's "Link courses"
                      reads (see extract_race_links)

What the ids encode, because the import asserts on it:

  uma card id   100101  = character 1001, outfit 01   (character = id // 100)
  support id    30024   = leading digit is rarity: 1 R, 2 SR, 3 SSR
  unique skill  100011  = character 1001's base-outfit unique. ★1/★2 characters
                          carry a weaker rarity-3 unique (10271) until ★3, when
                          the rarity-4 one (100271) replaces it.
  white / gold  share skill_data.group_id; group_rate is the tier:
                          1 white, 2 gold, -1 the "×" penalty version

Two places the schema is not what it looks like:

  * card_rarity_data.skill_set is NOT a skill id. It keys the `skill_set` table,
    whose skill_id1 is the unique skill that star level carries.
  * single_mode_hint_gain is keyed per card by `support_card_id`, not by the
    card's `skill_set_id` (which several cards of one character share as
    `hint_id`). Rows with hint_gain_type 1 are stat hints and are skipped.

The global client defines nothing that has not released on global, with one
exception: four JP-only support cards (30058 to 30061) sit in support_card_data
dated 2099-01-01, which is why each card carries its start_date. Its TEXT
runs ahead, though: the names and descriptions of upcoming skills ship before
their definitions, and skill_text_ahead.json carries them. Tutorial cards (ids
above 9,000,000, default_rarity 0) are skipped. Evolved skills (rarity 6) exist
only in JP so far; global's `skill_upgrade_*` tables are empty.

Run from backend/:
    python scripts/extract_master_snapshot.py --dry-run     # counts only
    python scripts/extract_master_snapshot.py               # write the JSON
    python scripts/extract_master_snapshot.py --mdb /path/to/master.mdb
    python scripts/extract_master_snapshot.py --region jp --mdb /tmp/jp/master.mdb
"""

import argparse
import datetime
import json
import sqlite3
from pathlib import Path

DEFAULT_MDB = Path("/mnt/c/Users/Zac82/AppData/LocalLow/Cygames/Umamusume/master/master.mdb")
# Per region. JP has no default mdb: it is always a rebuilt export, wherever
# that was written.
DEFAULT_OUT = {
    "global": Path(__file__).parent / "data" / "master_snapshot",
    "jp": Path(__file__).parent / "data" / "jp_snapshot",
}

# text_data categories. The table is (category, index) -> text; index is the
# id of whatever the category describes.
TEXT_CARD_FULL = 4          # "[Special Dreamer] Special Week"
TEXT_CARD_TITLE = 5         # "[Special Dreamer]"
TEXT_CHARA_NAME = 6         # "Special Week", by character id
TEXT_SKILL_NAME = 47
TEXT_COMMAND_NAME = 55      # "Speed", by training command id (101, 102, ...)
TEXT_SKILL_DESC = 48
TEXT_SUPPORT_FULL = 75      # "[Get Lots of Hugs for Me] Oguri Cap"
TEXT_SUPPORT_TITLE = 76
TEXT_SUPPORT_CHARA = 77
TEXT_EVOLUTION_CONDITION = 290  # "Reach 600 Stamina", by skill_upgrade_condition.id
TEXT_SPARK_NAME = 147       # "Speed", "Oka Sho", by succession_factor.factor_id
TEXT_RACE_INSTANCE = 28     # "Kisaragi Sho", by race_instance.id
TEXT_EFFECT_NAME = 151      # "Friendship Bonus", by support_card_effect_table.type
TEXT_EFFECT_HELP = 154      # "Increases the effectiveness of Friendship Training", by the same type
TEXT_UNIQUE_EFFECT = 155    # "Guts Bonus and Initial Guts", by support card id

# succession_factor.factor_type -> what the spark is, read from the global
# client on 2026-10-27: 1 the five stats (blue in the game), 2 the surface,
# distance and style aptitudes (pink), 3 an outfit's unique skill (green), 4 a
# white skill, 5 a race won, 6 a training scenario, 7 the Carnival bonus. A
# type the game adds later is written as its number, so a review sees it and
# the import skips it until it has a name here.
SPARK_KINDS = {1: "stat", 2: "aptitude", 3: "unique", 4: "skill", 5: "race", 6: "scenario", 7: "carnival"}

TUTORIAL_CARD_MIN_ID = 9_000_000

RUNNING_STYLES = {1: "front", 2: "pace", 3: "late", 4: "end"}

# support_card_data.command_id is the training command the card boosts; 0 with
# support_card_type 2 is a friend card and 3 a group card. Which stat a command
# id means is READ FROM THE GAME (TEXT_COMMAND_NAME), never listed here: the ids
# are not in stat order (102 is Power, 103 Guts, 105 Stamina), and a hand-typed
# table had three of the five wrong until 2026-09-17, which put every stamina,
# power and guts card under the wrong type.
STAT_TYPES = {"speed", "stamina", "power", "guts", "wit"}
# The words each client's TEXT_COMMAND_NAME uses for those five stats. Global's
# are the type names themselves; JP's are the game's Japanese ones.
STAT_WORDS = {
    "global": {name: name for name in STAT_TYPES},
    "jp": {"スピード": "speed", "スタミナ": "stamina", "パワー": "power", "根性": "guts", "賢さ": "wit"},
}
SUPPORT_CARD_TYPES = {2: "friend", 3: "group"}

HINT_GAIN_SKILL = 0   # hint_value_1 is a skill id; type 1 rows are stat hints


# The game's column names for the five stats, in the order this project uses.
STAT_COLUMNS = (("speed", "speed"), ("stamina", "stamina"), ("power", "pow"),
                ("guts", "guts"), ("wit", "wiz"))


def stat_block(row, prefix):
    """{speed, stamina, power, guts, wit} read from `<prefix><game column>`."""
    return {ours: row[f"{prefix}{theirs}"] for ours, theirs in STAT_COLUMNS}


def text_map(conn, category):
    """{index: text} for one text_data category."""
    rows = conn.execute(
        'SELECT "index", text FROM text_data WHERE category = ?', (category,)
    )
    return dict(rows.fetchall())


def extract_skills(conn):
    names = text_map(conn, TEXT_SKILL_NAME)
    descriptions = text_map(conn, TEXT_SKILL_DESC)
    costs = dict(conn.execute(
        "SELECT id, need_skill_point FROM single_mode_skill_need_point"
    ).fetchall())

    skills = []
    for row in conn.execute("SELECT * FROM skill_data ORDER BY id"):
        # JP's skill_data grew a third slot (condition_3 and its abilities,
        # first seen in the 2026-10-26 export) that no skill fills yet. Stop
        # the day one does, rather than drop it: effect_slots reads two.
        if "condition_3" in row.keys() and (row["condition_3"] or row["ability_type_3_1"]):
            raise SystemExit(
                f"skill {row['id']} uses skill_data's third slot; extend effect_slots before extracting"
            )
        skills.append({
            "id": row["id"],
            "name": names.get(row["id"], ""),
            "description": descriptions.get(row["id"], ""),
            "rarity": row["rarity"],
            "group_id": row["group_id"],
            "group_rate": row["group_rate"],
            "icon_id": row["icon_id"],
            "skill_category": row["skill_category"],
            # Uniques and a handful of white/gold skills have no cost row.
            "cost": costs.get(row["id"]),
            "precondition_1": row["precondition_1"],
            "condition_1": row["condition_1"],
            "precondition_2": row["precondition_2"],
            "condition_2": row["condition_2"],
            # 1: the skill rolls against wit before it fires; 0: it always
            # fires once its condition holds (zephyr's horse notes, tentative).
            "activate_lot": row["activate_lot"],
            "effects": effect_slots(row),
        })
    return skills


def effect_slots(row):
    """
    What a skill does, in the game's raw numbers, one entry per condition slot.

    skill_data has two slots, each a (precondition, condition) pair with its own
    duration, cooldown and up to three abilities. Slot 2 exists only when
    condition_2 does (no row has slot-2 abilities without it, 2026-10-23).

    Kept VERBATIM, like the condition strings: the site's readable effect text
    is built from these in code (calculatorapi/skill_text.py), so a better
    wording never needs a re-extract. The units, from zephyr's horse notes:
    `duration` and `cooldown` are tenths of a millisecond (÷ 10,000 = seconds),
    and most ability values are ÷ 10,000 too, per `type`. A `*_usage` column
    says how the game scales the number (1 = as is).
    """
    slots = []
    for n in (1, 2):
        if n == 2 and not row["condition_2"]:
            break
        abilities = []
        for m in (1, 2, 3):
            ability_type = row[f"ability_type_{n}_{m}"]
            if not ability_type:
                continue
            abilities.append({
                "type": ability_type,
                "value": row[f"float_ability_value_{n}_{m}"],
                "value_usage": row[f"ability_value_usage_{n}_{m}"],
                "value_level_usage": row[f"ability_value_level_usage_{n}_{m}"],
                "additional_activate_type": row[f"additional_activate_type_{n}_{m}"],
                "target_type": row[f"target_type_{n}_{m}"],
                "target_value": row[f"target_value_{n}_{m}"],
            })
        slots.append({
            "duration": row[f"float_ability_time_{n}"],
            "duration_usage": row[f"ability_time_usage_{n}"],
            "cooldown": row[f"float_cooldown_time_{n}"],
            "abilities": abilities,
        })
    return slots


def extract_skill_text_ahead(conn, skills):
    """
    Skills the client has English text for but does not define yet.

    The global client ships the text of upcoming content ahead of the content
    itself: on 2026-10-14 its name table held 1,122 skills against 734
    `skill_data` rows. The other 388 carry the official English name AND
    description: the next months of uniques with their inherited copies,
    general skills from cards to come, two evolved skills per outfit (nine-digit
    ids), and event bonus skills (seven-digit ids). All of it is written here,
    as the game has it; import_game_data decides which kinds become rows.
    """
    names = text_map(conn, TEXT_SKILL_NAME)
    descriptions = text_map(conn, TEXT_SKILL_DESC)
    defined = {skill["id"] for skill in skills}
    return [
        {"id": skill_id, "name": names[skill_id], "description": descriptions.get(skill_id, "")}
        for skill_id in sorted(names)
        if skill_id not in defined and names[skill_id].strip()
    ]


def extract_cards(conn):
    """Outfits with their per-star rows. Also returns the unique-skill rows."""
    full_names = text_map(conn, TEXT_CARD_FULL)
    titles = text_map(conn, TEXT_CARD_TITLE)
    chara_names = text_map(conn, TEXT_CHARA_NAME)

    # skill_set.id -> the unique skill that set carries (skill_id1).
    unique_by_set = dict(conn.execute("SELECT id, skill_id1 FROM skill_set").fetchall())

    rarity_rows = {}
    for row in conn.execute("SELECT * FROM card_rarity_data ORDER BY card_id, rarity"):
        rarity_rows.setdefault(row["card_id"], []).append({
            "star": row["rarity"],
            "unique_skill_id": unique_by_set.get(row["skill_set"]),
            "stats": stat_block(row, ""),
            "max_stats": stat_block(row, "max_"),
            # The game's 1..8 scale: G F E D C B A S.
            "aptitude": {
                "turf": row["proper_ground_turf"], "dirt": row["proper_ground_dirt"],
                "short": row["proper_distance_short"], "mile": row["proper_distance_mile"],
                "medium": row["proper_distance_middle"], "long": row["proper_distance_long"],
                "front": row["proper_running_style_nige"],
                "pace": row["proper_running_style_senko"],
                "late": row["proper_running_style_sashi"],
                "end": row["proper_running_style_oikomi"],
            },
        })

    cards, card_skills = [], []
    for row in conn.execute("SELECT * FROM card_data ORDER BY id"):
        card_id = row["id"]
        if card_id >= TUTORIAL_CARD_MIN_ID:
            continue
        stars = rarity_rows.get(card_id, [])
        cards.append({
            "id": card_id,
            "chara_id": row["chara_id"],
            "chara_name": chara_names.get(row["chara_id"], ""),
            "name": full_names.get(card_id, ""),
            "title": titles.get(card_id, ""),
            "default_rarity": row["default_rarity"],
            "running_style": RUNNING_STYLES.get(row["running_style"], str(row["running_style"])),
            "growth": stat_block(row, "talent_"),
            "stars": stars,
        })

        # Unique skills: one row per distinct skill, at the first star that
        # carries it. A ★3 card has one; a ★1/★2 card has two (see docstring).
        seen = set()
        for star in stars:
            skill_id = star["unique_skill_id"]
            if skill_id and skill_id not in seen:
                seen.add(skill_id)
                card_skills.append({
                    "card_id": card_id, "skill_id": skill_id,
                    "source": "unique", "level": star["star"],
                })

        # Innate (need_rank 0) and awakening (need_rank 2..5) skills.
        for skill_id, need_rank in conn.execute("""
            SELECT skill_id, need_rank FROM available_skill_set
            WHERE available_skill_set_id = ? ORDER BY need_rank, skill_id
        """, (row["available_skill_set_id"],)):
            card_skills.append({
                "card_id": card_id, "skill_id": skill_id,
                "source": "innate" if need_rank == 0 else "awakening",
                "level": need_rank or None,
            })

    return cards, card_skills


def extract_support_cards(conn, region="global"):
    full_names = text_map(conn, TEXT_SUPPORT_FULL)
    titles = text_map(conn, TEXT_SUPPORT_TITLE)
    chara_names = text_map(conn, TEXT_SUPPORT_CHARA)

    # {101: "speed", ...} in the game's own words, kept to the five stats so an
    # unrelated command name can never become a card type.
    stat_words = STAT_WORDS[region]
    command_types = {
        command_id: stat_words[name.lower()]
        for command_id, name in text_map(conn, TEXT_COMMAND_NAME).items()
        if name.lower() in stat_words
    }
    if set(command_types.values()) != STAT_TYPES:
        raise SystemExit(
            f"text_data category {TEXT_COMMAND_NAME} no longer names all five training "
            f"commands (found {sorted(command_types.values())}); check the category id"
        )

    # A group card stands for several umas, and each counts as that uma's card
    # in a deck (no Team Sirius in Special Week's deck, nor beside her own
    # card). support_card_data.chara_id files the card under ONE of them, so
    # support_card_group is the only place the rest are named. Kept in the
    # game's row order, as character names (category 6, by character id): the
    # team builder matches cards to umas by name, see utils/pvpTeams.ts.
    person_names = text_map(conn, TEXT_CHARA_NAME)
    members = {}
    for card_id, chara_id in conn.execute(
        "SELECT support_card_id, chara_id FROM support_card_group ORDER BY support_card_id, id"
    ):
        if not person_names.get(chara_id):
            raise SystemExit(
                f"support_card_group lists character {chara_id} for card {card_id}, "
                f"which text_data category {TEXT_CHARA_NAME} does not name"
            )
        members.setdefault(card_id, []).append(person_names[chara_id])

    cards = []
    for row in conn.execute("SELECT * FROM support_card_data ORDER BY id"):
        card_id = row["id"]
        card = {
            "id": card_id,
            "chara_id": row["chara_id"],
            "chara_name": chara_names.get(card_id, ""),
            "name": full_names.get(card_id, ""),
            "title": titles.get(card_id, ""),
            "rarity": row["rarity"],
            "card_type": (
                command_types.get(row["command_id"])
                or SUPPORT_CARD_TYPES.get(row["support_card_type"], "?")
            ),
            "skill_set_id": row["skill_set_id"],
            # When the card comes out on this region, as unix seconds. The
            # global client carries a few JP-only cards dated 2099-01-01, its
            # "not released here" mark, so being in the table is not being
            # out (import_game_data reads this for is_on_global).
            "start_date": row["start_date"],
        }
        # Group cards only, so the other 260 rows diff the same as before.
        if card["card_type"] == "group":
            if card_id not in members:
                raise SystemExit(
                    f"group card {card_id} has no support_card_group rows; "
                    "check the table, or the team builder loses its deck rule"
                )
            card["members"] = members[card_id]
        cards.append(card)

    hints = [
        {"support_card_id": card_id, "skill_id": skill_id, "hint_group": group}
        for card_id, skill_id, group in conn.execute("""
            SELECT support_card_id, hint_value_1, hint_group
            FROM single_mode_hint_gain WHERE hint_gain_type = ?
            ORDER BY support_card_id, hint_group, hint_value_1
        """, (HINT_GAIN_SKILL,))
    ]
    return cards, hints


def extract_sparks(conn):
    """
    Every spark: what a veteran passes on when she is a parent. Her export
    lists them as ids (`factor_info_array`, and each parent's), and this is
    what the ids mean. `stars` is the row's `rarity`, 1..3: the export's own
    `level` field is 0 on every spark and says nothing.
    """
    names = text_map(conn, TEXT_SPARK_NAME)
    return [
        {
            "id": row["factor_id"],
            "kind": SPARK_KINDS.get(row["factor_type"], str(row["factor_type"])),
            "stars": row["rarity"],
            "name": names.get(row["factor_id"], ""),
        }
        for row in conn.execute(
            "SELECT factor_id, factor_type, rarity FROM succession_factor ORDER BY factor_id"
        )
    ]


# single_mode_route_race.target_type: 1 is the character's own career goal; 3
# the scenario's finals (turns 74 to 78), the same for everyone, left out.
GOAL_TARGET = 1
# condition_type 1: run a race (condition_id is a single_mode_program id). The
# other kinds (2, 3, 7, 8, 9: 57 rows on 2026-11-12, kind 3 looks like a fan
# count) are extracted as they are, undecoded; the site shows none of them
# until one is confirmed (notes/encyclopedia-plan.md, PR 11).
RACE_GOAL = 1


def extract_objectives(conn):
    """
    Each character's career goals on the default route (scenario_id 0), in
    order: [{"chara_id", "goals": [{"turn", "kind", ...}]}]. A race goal names
    its race through the career program (text_data 28 by race instance, the
    client's own words), with the race's grade code and the placing needed
    (condition_value_1: 1 win, 2/3/5 that place or better, 0 just run it).
    Keyed by CHARACTER, as the game keys them: every outfit shares them.
    """
    names = text_map(conn, TEXT_RACE_INSTANCE)
    programs = {
        row["id"]: row for row in conn.execute(
            "SELECT p.id, p.race_instance_id, r.grade FROM single_mode_program p "
            "JOIN race_instance ri ON ri.id = p.race_instance_id JOIN race r ON r.id = ri.race_id"
        )
    }
    routes = conn.execute(
        "SELECT chara_id, race_set_id FROM single_mode_route WHERE scenario_id = 0 ORDER BY chara_id, priority"
    ).fetchall()
    found = []
    for route in routes:
        goals = []
        for row in conn.execute(
            "SELECT turn, condition_type, condition_id, condition_value_1, condition_value_2 "
            "FROM single_mode_route_race WHERE race_set_id = ? AND target_type = ? ORDER BY sort_id",
            (route["race_set_id"], GOAL_TARGET),
        ):
            goal = {"turn": row["turn"], "kind": row["condition_type"]}
            program = programs.get(row["condition_id"]) if row["condition_type"] == RACE_GOAL else None
            if program is not None:
                goal.update(race=names.get(program["race_instance_id"], ""), grade=program["grade"],
                            top=row["condition_value_1"])
            else:
                goal.update(condition_id=row["condition_id"], value_1=row["condition_value_1"],
                            value_2=row["condition_value_2"])
            goals.append(goal)
        found.append({"chara_id": route["chara_id"], "goals": goals})
    return found


# support_card_effect_table's value columns and the card level each one names.
# A value of -1 means "the game names no value at this level".
EFFECT_LEVEL_COLUMNS = (("init", 1),) + tuple((f"limit_lv{level}", level) for level in range(5, 55, 5))
EFFECT_UNSET = -1
# A real card's id is five digits; the tutorial's copies sit above nine million.
TUTORIAL_CARD_IDS_FROM = 9_000_000


def extract_support_effects(conn, region="global"):
    """
    Each support card's effects, exactly as the game's tables state them:

      [{"id": 30001,
        "effects": [{"type": 1, "levels": {"1": 10, "25": 15, "30": 15, "45": 20}}, ...],
        "unique": {"level": 30, "effects": [{"type": 6, "value": 1}, {"type": 12, "value": 20}],
                   "text": "Guts Bonus and Initial Guts"}}]

    `effects` is support_card_effect_table: one row per card and effect type,
    with a value at SOME of the levels 1, 5, 10 ... 50. Only those named
    levels are kept. What the value is between two of them is not in the
    files: calculatorapi/support_effects.py works it out, by the rule checked
    against real cards, so the snapshot stays a copy of the game.

    `unique` is support_card_unique_effect: the level that unlocks it and up
    to two typed effects. A type of 100 or more is a conditional effect whose
    extra numbers (`params`) nobody here has decoded, so the page words a
    unique effect with the game's OWN sentence (text_data 155, by card id),
    which only the global client has in English. Null for a card with none.
    """
    if not has_table(conn, "support_card_effect_table"):
        return []
    sentences = text_map(conn, TEXT_UNIQUE_EFFECT) if region == "global" else {}
    uniques = {}
    for row in conn.execute("SELECT * FROM support_card_unique_effect ORDER BY id"):
        effects = []
        for slot in ("0", "1"):
            if row[f"type_{slot}"]:
                effect = {"type": row[f"type_{slot}"], "value": row[f"value_{slot}"]}
                params = [row[f"value_{slot}_{index}"] for index in range(1, 5)]
                if any(params):
                    effect["params"] = params
                effects.append(effect)
        unique = {"level": row["lv"], "effects": effects}
        if sentences.get(row["id"], "").strip():
            unique["text"] = sentences[row["id"]].strip()
        uniques[row["id"]] = unique

    cards = {}
    for row in conn.execute(
        "SELECT * FROM support_card_effect_table WHERE id < ? ORDER BY id, type", (TUTORIAL_CARD_IDS_FROM,)
    ):
        levels = {
            str(level): row[column] for column, level in EFFECT_LEVEL_COLUMNS if row[column] != EFFECT_UNSET
        }
        if levels:
            cards.setdefault(row["id"], []).append({"type": row["type"], "levels": levels})
    return [
        {"id": card_id, "effects": effects, "unique": uniques.get(card_id)}
        for card_id, effects in sorted(cards.items())
    ]


def extract_support_effect_types(conn):
    """[{"type": 1, "name": "Friendship Bonus", "help": "Increases the ..."}], the game's own English."""
    helps = text_map(conn, TEXT_EFFECT_HELP)
    return [
        {"type": effect_type, "name": name.strip(), "help": helps.get(effect_type, "").strip()}
        for effect_type, name in sorted(text_map(conn, TEXT_EFFECT_NAME).items())
        if name.strip()
    ]


def has_table(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def extract_race_links(conn):
    """Which course each Champions Meeting and League of Heroes runs on, by number.

    The game names it: a CM round (`champions_race_condition`) or a LoH stage
    (`heroes_race_condition`) points at a race instance, the instance at a race,
    the race at its course set. The admin's "Link courses" reads this file to
    link events to RaceCourse rows (notes/course-map-plan.md), so a snapshot
    refresh is what brings a new event's course.

    Every round of one event has run on one course so far. If a future event
    ever doesn't, its `course` is null and `courses` lists them all, for an
    editor to pick by hand. The conditions are round 0's / stage 0's, as the
    game codes them (0 is random); "Link courses" turns them into words.
    """
    if not has_table(conn, "champions_race_condition"):
        return {"champions": {}, "heroes": {}}
    course_of = dict(conn.execute(
        "SELECT race_instance.id, race.course_set FROM race_instance"
        " JOIN race ON race.id = race_instance.race_id"
    ).fetchall())

    def entry(courses, season, weather, ground):
        courses = sorted(set(courses))
        found = {"course": courses[0] if len(courses) == 1 else None,
                 "season": season, "weather": weather, "ground": ground}
        if len(courses) != 1:
            found["courses"] = courses
        return found

    champions = {}
    rounds = conn.execute(
        "SELECT champions_id, round_id, race_instance_id, race_condition_id"
        " FROM champions_race_condition ORDER BY champions_id, round_id"
    ).fetchall()
    for number in sorted({row["champions_id"] for row in rounds}):
        own = [row for row in rounds if row["champions_id"] == number]
        condition = conn.execute(
            "SELECT season, weather, ground FROM race_condition WHERE id = ?",
            (own[0]["race_condition_id"],),
        ).fetchone()
        champions[str(number)] = entry(
            [course_of[row["race_instance_id"]] for row in own], *condition,
        )

    heroes = {}
    stages = conn.execute(
        "SELECT heroes_id, stage, race_instance_id, season, weather, ground"
        " FROM heroes_race_condition ORDER BY heroes_id, stage"
    ).fetchall()
    for number in sorted({row["heroes_id"] for row in stages}):
        own = [row for row in stages if row["heroes_id"] == number]
        heroes[str(number)] = entry(
            [course_of[row["race_instance_id"]] for row in own],
            own[0]["season"], own[0]["weather"], own[0]["ground"],
        )
    return {"champions": champions, "heroes": heroes}


def extract_evolutions(conn):
    """
    Evolved skills: whose, from what, and how they unlock. Three kinds:

      outfit      skill_upgrade_description: one outfit's own evolution of a
                  skill it learns, unlocked at awakening `rank` (3, 5 or 7)
      scenario    skill_upgrade_speciality: a training scenario's evolution
      succession  skill_upgrade_succession_skill: an inherited unique's

    The scenario and succession tables name the base (`base_skill_id`). The
    OUTFIT table does not, and no other table does either (searched all 626 JP
    tables, 2026-10-23). The import fills that link by a rule the owner
    approved (an outfit has exactly one gold skill at that awakening rank);
    this file keeps only what the game says.

    Conditions join on each table's own `id` (skill_upgrade_condition's
    `description_id`), which is NOT always the evolved skill's id: a succession
    row 901351 evolves into skill 91101351. Each condition keeps the game's raw
    numbers and its text from TEXT_EVOLUTION_CONDITION, in the client's language.
    `upgrade_type` (1 outfit, 2 scenario, 3 succession) is a JP-only column and
    is not copied: the list a condition sits in already says it.
    """
    texts = text_map(conn, TEXT_EVOLUTION_CONDITION)
    conditions = {}
    for row in conn.execute("SELECT * FROM skill_upgrade_condition ORDER BY id"):
        conditions.setdefault(row["description_id"], []).append({
            "id": row["id"],
            "num": row["num"],
            "sub_num": row["sub_num"],
            "timing_type": row["timing_type"],
            "count_type": row["count_type"],
            "text": texts.get(row["id"], ""),
        })

    outfit = [
        {"skill_id": row["skill_id"], "card_id": row["card_id"], "rank": row["rank"],
         "start_date": row["start_date"], "conditions": conditions.get(row["id"], [])}
        for row in conn.execute("SELECT * FROM skill_upgrade_description ORDER BY skill_id")
    ]
    # Global's client has no rows in either of these, and no table at all
    # for some, so a missing table reads as empty.
    scenario = [
        {"skill_id": row["skill_id"], "scenario_id": row["scenario_id"],
         "base_skill_id": row["base_skill_id"], "start_date": row["start_date"],
         "conditions": conditions.get(row["id"], [])}
        for row in (
            conn.execute("SELECT * FROM skill_upgrade_speciality ORDER BY skill_id")
            if has_table(conn, "skill_upgrade_speciality") else []
        )
    ]
    succession = [
        {"skill_id": row["skill_id"], "card_id": row["card_id"],
         "base_skill_id": row["base_skill_id"], "start_date": row["start_date"],
         "conditions": conditions.get(row["id"], [])}
        for row in (
            conn.execute("SELECT * FROM skill_upgrade_succession_skill ORDER BY skill_id")
            if has_table(conn, "skill_upgrade_succession_skill") else []
        )
    ]
    return {"outfit": outfit, "scenario": scenario, "succession": succession}


def extract(mdb_path, region="global"):
    conn = sqlite3.connect(f"file:{mdb_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row  # columns by name, so no positional unpacking
    try:
        skills = extract_skills(conn)
        cards, card_skills = extract_cards(conn)
        support_cards, support_hints = extract_support_cards(conn, region)
        evolutions = extract_evolutions(conn)
        support_effects = extract_support_effects(conn, region)
        # Text ahead of definitions is a global thing: JP defines what it
        # names, and global's English is the text worth having early. So are
        # the spark names: global's English is what a veteran card shows.
        region_only = {}
        if region == "global":
            region_only = {
                "skill_text_ahead.json": extract_skill_text_ahead(conn, skills),
                "evolution_condition_text.json": {
                    str(condition_id): text
                    for condition_id, text in sorted(text_map(conn, TEXT_EVOLUTION_CONDITION).items())
                    if text.strip()
                },
                "sparks.json": extract_sparks(conn),
                "objectives.json": extract_objectives(conn),
                "support_effect_types.json": extract_support_effect_types(conn),
            }
        # The course links come from JP's tables: they know every event
        # global will run, while global's stop at the events it has run.
        if region == "jp":
            region_only = {"race_links.json": extract_race_links(conn)}
    finally:
        conn.close()

    stat = mdb_path.stat()
    counts = {
        "skills": len(skills),
        "cards": len(cards),
        "card_skills": len(card_skills),
        "support_cards": len(support_cards),
        "support_hints": len(support_hints),
        "evolutions_outfit": len(evolutions["outfit"]),
        "evolutions_scenario": len(evolutions["scenario"]),
        "evolutions_succession": len(evolutions["succession"]),
        "support_effects": len(support_effects),
    }
    files = {
        "skills.json": skills,
        "cards.json": cards,
        "card_skills.json": card_skills,
        "support_cards.json": support_cards,
        "support_hints.json": support_hints,
        "evolutions.json": evolutions,
        "support_effects.json": support_effects,
    }
    if region == "jp":
        counts.update({f"race_links_{kind}": len(region_only["race_links.json"][kind])
                       for kind in ("champions", "heroes")})
    else:
        counts.update({name.removesuffix(".json"): len(payload) for name, payload in region_only.items()})
    files.update(region_only)
    meta = {
        "region": region,
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "mdb_size_bytes": stat.st_size,
        "mdb_modified_at": datetime.datetime.fromtimestamp(
            stat.st_mtime, datetime.timezone.utc
        ).isoformat(timespec="seconds"),
        "counts": counts,
    }
    return {"meta.json": meta, **files}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--region", choices=sorted(DEFAULT_OUT), default="global",
                        help="which client the mdb is from (default: global)")
    parser.add_argument("--mdb", type=Path, help="path to master.mdb (required for jp)")
    parser.add_argument("--out", type=Path, help="output directory (default: per region)")
    parser.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    parser.add_argument("--only", action="append", metavar="FILE",
                        help="write only this file (e.g. objectives.json), leaving the rest as committed; repeatable")
    args = parser.parse_args()

    if args.mdb is None:
        if args.region != "global":
            raise SystemExit("--region jp needs --mdb (rebuild one with scripts/jp_export_to_mdb.py)")
        args.mdb = DEFAULT_MDB
    args.out = args.out or DEFAULT_OUT[args.region]
    if not args.mdb.is_file():
        raise SystemExit(f"master.mdb not found at {args.mdb}")

    files = extract(args.mdb, args.region)
    for name, count in files["meta.json"]["counts"].items():
        print(f"{name:<24} {count:>6}")

    if args.dry_run:
        print("\nDry run: nothing written.")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    if args.only:
        # A new file from a NEWER mdb than the committed snapshot, without
        # refreshing the rest (a content update is its own change).
        files = {name: payload for name, payload in files.items() if name in args.only}
    for name, payload in files.items():
        (args.out / name).write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
        )
    print(f"\nWrote {len(files)} file(s) to {args.out}")


if __name__ == "__main__":
    main()
