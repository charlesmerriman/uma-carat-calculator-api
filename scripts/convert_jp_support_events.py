"""
convert_jp_support_events.py

Writes the support card EVENT skill files the import reads, from Daptrius's
capture of the JP game server (`support_skill_lists.json`, schema_version 1):

  scripts/data/master_snapshot/support_events.json   cards global has
  scripts/data/jp_snapshot/support_events.json       cards only JP has

Both are rows of {support_card_id, skill_id}, sorted, one per (card, skill).

WHY THE SERVER, NOT THE MDB: hints have a clean table in master.mdb
(single_mode_hint_gain, see extract_master_snapshot.py), but event skills live
in story-choice data with no usable link back to the card. The game client
asks the server for each card's list instead (`/support_card/
get_extend_master_info`), and Daptrius captured those answers on 2026-11-14.
Until 2026-11-17 the list came from gametora's card pages
(fetch_support_events.py, now retired): the site shows no gametora work
(owner and Daptrius, 2026-11-12), and the capture covers every card gametora
did, plus the ones it missed.

WHAT IS READ: `card_pools` only, one per card. An empty `skill_ids` means the
card gives no event skills (Daptrius). `story_lists`, which tie a skill to
the exact event, cover only a few cards and have no column to land in yet, so
they are ignored. The capture's own caveat holds: these are skills a card CAN
give, with no hint levels or guarantees.

THE GLOBAL RULE, as for hints: the capture is JP's, so a global card keeps
only skills global's snapshot knows. A skill JP added later (202992 on five
global cards, 2026-11-14) is dropped and counted rather than linked to a card
global players can't get it from. A JP-only card keeps every skill, and the
import's name check guards its id the same way it guards that card's hints.

A card id neither snapshot knows is reported and skipped (a card newer than
our JP snapshot): re-extract the snapshot, then re-run this.

Run from backend/ (the capture stays wherever it was downloaded):
    python scripts/convert_jp_support_events.py <path>/support_skill_lists.json
    python scripts/convert_jp_support_events.py <path> --dry-run
"""

import argparse
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
GLOBAL_DIR = DATA_DIR / "master_snapshot"
JP_DIR = DATA_DIR / "jp_snapshot"


def load(path):
    """A committed or captured JSON file, parsed."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_capture(path):
    """The capture's card pools as {card id: set of skill ids}, after a shape check."""
    capture = load(path)
    if capture.get("schema_version") != 1 or capture.get("region") != "jp":
        raise SystemExit(
            f"expected a schema_version 1 JP capture, got "
            f"{capture.get('schema_version')!r} / {capture.get('region')!r}"
        )
    pools = {}
    for pool in capture["card_pools"]:
        if pool.get("kind") != "card_event_skills":
            continue
        # A card captured twice keeps the union; the capture picks the latest
        # per card already, so this is belt and braces.
        pools.setdefault(pool["support_card_id"], set()).update(pool["skill_ids"])
    return pools


def split(pools, global_cards, global_skills, jp_cards):
    """The two files' rows, plus what was left out and why."""
    global_rows, jp_rows, dropped, unknown = [], [], [], []
    for card_id in sorted(pools):
        if card_id in global_cards:
            for skill_id in sorted(pools[card_id]):
                if skill_id in global_skills:
                    global_rows.append({"support_card_id": card_id, "skill_id": skill_id})
                else:
                    dropped.append((card_id, skill_id))
        elif card_id in jp_cards:
            jp_rows.extend(
                {"support_card_id": card_id, "skill_id": skill_id}
                for skill_id in sorted(pools[card_id])
            )
        else:
            unknown.append(card_id)
    return global_rows, jp_rows, dropped, unknown


def main():
    """Read the capture, split it by region, report, and write unless --dry-run."""
    parser = argparse.ArgumentParser(
        description="Convert a JP server capture into event skill files.",
    )
    parser.add_argument("capture", help="support_skill_lists.json, or the folder holding it")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args()

    path = Path(args.capture)
    if path.is_dir():
        path = path / "support_skill_lists.json"
    pools = read_capture(path)

    global_cards = {card["id"] for card in load(GLOBAL_DIR / "support_cards.json")}
    global_skills = {skill["id"] for skill in load(GLOBAL_DIR / "skills.json")}
    jp_cards = {card["id"] for card in load(JP_DIR / "support_cards.json")} - global_cards
    global_rows, jp_rows, dropped, unknown = split(pools, global_cards, global_skills, jp_cards)

    print(f"{len(pools)} card(s) in the capture")
    print(f"global: {len(global_rows)} row(s); dropped {len(dropped)} naming a skill not on global")
    print(f"JP-only: {len(jp_rows)} row(s)")
    if unknown:
        print(f"skipped {len(unknown)} card id(s) neither snapshot knows: {unknown}")
    missing = sorted(global_cards - set(pools))
    if missing:
        print(f"{len(missing)} global card(s) the capture doesn't cover: {missing}")

    if args.dry_run:
        print("dry run: nothing written")
        return
    for directory, rows in ((GLOBAL_DIR, global_rows), (JP_DIR, jp_rows)):
        out = directory / "support_events.json"
        out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
