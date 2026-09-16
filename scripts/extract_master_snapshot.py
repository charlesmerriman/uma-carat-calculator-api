"""
extract_master_snapshot.py

Reads the global game client's own database (master.mdb, a plain SQLite file the
Steam client downloads on every update) and writes the slice this project needs
as JSON under scripts/data/master_snapshot/. The JSON is COMMITTED; the mdb is
not. That is the point: `manage.py import_game_data` reads the JSON, so every
import is reviewable as a diff and reproducible on a machine without the game.

What comes out, one file per concern (all sorted by id, so re-runs diff cleanly):

  meta.json           when, from which mdb (size, mtime), and row counts
  skills.json         every skill: name, description, rarity, white/gold group,
                      icon, skill point cost, activation conditions
  cards.json          every uma outfit ("card"): names, initial stars, running
                      style, growth bonuses, and per-star stats + aptitudes
  card_skills.json    which skills each outfit has: unique / innate / awakening
  support_cards.json  every support card: names, rarity, type, character
  support_hints.json  which skills each support card can teach (hints)

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

Global only, by construction: this mdb is the global client's, so it holds
nothing that has not released on global. Tutorial cards (ids above 9,000,000,
default_rarity 0) are skipped. Evolved skills (rarity 6) do not exist on global
yet; the `skill_upgrade_*` tables are empty and are not read.

Run from backend/:
    python scripts/extract_master_snapshot.py --dry-run     # counts only
    python scripts/extract_master_snapshot.py               # write the JSON
    python scripts/extract_master_snapshot.py --mdb /path/to/master.mdb
"""

import argparse
import datetime
import json
import sqlite3
from pathlib import Path

DEFAULT_MDB = Path("/mnt/c/Users/Zac82/AppData/LocalLow/Cygames/Umamusume/master/master.mdb")
DEFAULT_OUT = Path(__file__).parent / "data" / "master_snapshot"

# text_data categories. The table is (category, index) -> text; index is the
# id of whatever the category describes.
TEXT_CARD_FULL = 4          # "[Special Dreamer] Special Week"
TEXT_CARD_TITLE = 5         # "[Special Dreamer]"
TEXT_CHARA_NAME = 6         # "Special Week", by character id
TEXT_SKILL_NAME = 47
TEXT_SKILL_DESC = 48
TEXT_SUPPORT_FULL = 75      # "[Get Lots of Hugs for Me] Oguri Cap"
TEXT_SUPPORT_TITLE = 76
TEXT_SUPPORT_CHARA = 77

TUTORIAL_CARD_MIN_ID = 9_000_000

RUNNING_STYLES = {1: "front", 2: "pace", 3: "late", 4: "end"}

# support_card_data.command_id names the trained stat; 0 with
# support_card_type 2 is a friend card and 3 a group card.
COMMAND_TYPES = {101: "speed", 102: "stamina", 103: "power", 105: "guts", 106: "wit"}
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
    for row in conn.execute("""
        SELECT id, rarity, group_id, group_rate, icon_id, skill_category,
               precondition_1, condition_1, precondition_2, condition_2
        FROM skill_data ORDER BY id
    """):
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
        })
    return skills


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


def extract_support_cards(conn):
    full_names = text_map(conn, TEXT_SUPPORT_FULL)
    titles = text_map(conn, TEXT_SUPPORT_TITLE)
    chara_names = text_map(conn, TEXT_SUPPORT_CHARA)

    cards = []
    for row in conn.execute("SELECT * FROM support_card_data ORDER BY id"):
        card_id = row["id"]
        cards.append({
            "id": card_id,
            "chara_id": row["chara_id"],
            "chara_name": chara_names.get(card_id, ""),
            "name": full_names.get(card_id, ""),
            "title": titles.get(card_id, ""),
            "rarity": row["rarity"],
            "card_type": (
                COMMAND_TYPES.get(row["command_id"])
                or SUPPORT_CARD_TYPES.get(row["support_card_type"], "?")
            ),
            "skill_set_id": row["skill_set_id"],
        })

    hints = [
        {"support_card_id": card_id, "skill_id": skill_id, "hint_group": group}
        for card_id, skill_id, group in conn.execute("""
            SELECT support_card_id, hint_value_1, hint_group
            FROM single_mode_hint_gain WHERE hint_gain_type = ?
            ORDER BY support_card_id, hint_group, hint_value_1
        """, (HINT_GAIN_SKILL,))
    ]
    return cards, hints


def extract(mdb_path):
    conn = sqlite3.connect(f"file:{mdb_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row  # columns by name, so no positional unpacking
    try:
        skills = extract_skills(conn)
        cards, card_skills = extract_cards(conn)
        support_cards, support_hints = extract_support_cards(conn)
    finally:
        conn.close()

    stat = mdb_path.stat()
    meta = {
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "mdb_size_bytes": stat.st_size,
        "mdb_modified_at": datetime.datetime.fromtimestamp(
            stat.st_mtime, datetime.timezone.utc
        ).isoformat(timespec="seconds"),
        "counts": {
            "skills": len(skills),
            "cards": len(cards),
            "card_skills": len(card_skills),
            "support_cards": len(support_cards),
            "support_hints": len(support_hints),
        },
    }
    return {
        "meta.json": meta,
        "skills.json": skills,
        "cards.json": cards,
        "card_skills.json": card_skills,
        "support_cards.json": support_cards,
        "support_hints.json": support_hints,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--mdb", type=Path, default=DEFAULT_MDB, help="path to master.mdb")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    args = parser.parse_args()

    if not args.mdb.is_file():
        raise SystemExit(f"master.mdb not found at {args.mdb}")

    files = extract(args.mdb)
    for name, count in files["meta.json"]["counts"].items():
        print(f"{name:<14} {count:>6}")

    if args.dry_run:
        print("\nDry run: nothing written.")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (args.out / name).write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
        )
    print(f"\nWrote {len(files)} file(s) to {args.out}")


if __name__ == "__main__":
    main()
