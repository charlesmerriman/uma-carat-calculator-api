"""
fetch_gametora_skill_names.py

Saves GameTora's English skill NAMES, and nothing else of theirs, for search.
Written to scripts/data/gametora/ and COMMITTED, like the UmaTL slice, so the
import never reaches the network and a change shows up as a reviewable diff.

WHY ONLY NAMES, AND ONLY FOR SEARCH (owner and Daptrius, 2026-11-12). Players
know many skills by GameTora's translation ("It's Going to Be Me" is global's
"Certain Victory"), so a search for that name should find the skill. But the
site never DISPLAYS GameTora's text: their loc files read as meant for
projects that don't compete with GameTora, and this site does in places. So
import_game_data turns each name into a SkillAlias, which search, guide
`[[mentions]]` and the sheet importer match and which is never shown. Their
descriptions and other languages are dropped here, so they never enter the
repo. (The separate `import_game_data --gametora FILE` option, admin-only
`description_detailed`, is unrelated and stays as it was.)

The source is GameTora's public loc file, one object per skill: `id` (the
game's skill id, Skill.game_id), `name_en` (GameTora's translation), plus
`name_en_global` where global has named it, descriptions and other languages.
Only `id` and `name_en` are kept.

Run from backend/:
    python scripts/fetch_gametora_skill_names.py             # fetch and write
    python scripts/fetch_gametora_skill_names.py --dry-run   # counts only
"""

import argparse
import datetime
import json
from pathlib import Path

import requests

SOURCE_URL = "https://gametora.com/loc/umamusume/skills.json"
DEFAULT_OUT = Path(__file__).parent / "data" / "gametora"
# A generic client name: no personal or project details go to a third party.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; skill-name-fetch)"}


def names_from(entries):
    """
    {str game id: name} from the loc file's list, sorted by id.

    A blank name is dropped (no name is no alias), and so is an entry without a
    usable id: one malformed row shouldn't stop the refresh, and the count
    printed below shows if many go missing.
    """
    names = {}
    for entry in entries:
        try:
            game_id = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = (entry.get("name_en") or "").strip()
        if name:
            names[str(game_id)] = name
    return {key: names[key] for key in sorted(names, key=int)}


def download():
    response = requests.get(SOURCE_URL, headers=HEADERS, timeout=120)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    args = parser.parse_args()

    entries = download()
    names = names_from(entries)
    print(f"skill names {len(names):>6} of {len(entries)} entries")
    if args.dry_run:
        print("\nDry run: nothing written.")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    meta = {
        "source": SOURCE_URL,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "use": "search aliases only; never displayed (owner, 2026-11-12)",
        "counts": {"skill_names": len(names)},
    }
    for name, payload in (("names.json", names), ("meta.json", meta)):
        (args.out / name).write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
        )
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
