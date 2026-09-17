"""
check_against_gametora.py

A second opinion on the committed master snapshot. Dev-only, read-only: it
WRITES NOTHING and nothing of gametora's is stored. The project's game data
comes from the game's own files (extract_master_snapshot.py); this script only
says where an independent source disagrees with them, so a person can go and
look.

Two questions, either or both:

  --numbers   For every uma outfit and support card IN the snapshot, do our
              extracted numbers match gametora's? Umas: initial rarity, the five
              base stats, the five growth bonuses, the ten aptitudes. Support
              cards: rarity and type. A mismatch usually means the extractor
              read the wrong star row or column, which no test of ours would
              catch because the tests share its assumptions.

  --stale     For every card we know of that is NOT in the snapshot, has
              gametora's global release date (`release_en`) already passed?
              Then the snapshot is behind the game: launch the global client
              once so it re-downloads master.mdb, re-run
              extract_master_snapshot.py, commit, deploy.

With neither flag it answers both.

gametora runs on the JP game's balance, and a JP balance patch can reach a card
before global gets it. So a `--numbers` mismatch is a prompt to check the game,
never a reason to change the snapshot by hand.

HOW: the same JSON twins fetch_support_events.py reads, one per card page. Slugs
come from scripts/data/character_cards.json (`gametora_url`) and
scripts/data/support_cards_source.json (`<id>-<name_en>`), which are also the
lists of cards "we know of" for --stale.

Run from backend/:
    python scripts/check_against_gametora.py
    python scripts/check_against_gametora.py --stale
    python scripts/check_against_gametora.py --numbers --only 100101 30024

Exit code 1 when anything is reported, so it can gate a refresh checklist.
"""

import argparse
import datetime
import json
import sys
import time
import urllib.error

from fetch_support_events import DATA_DIR, SNAPSHOT_DIR, get, slugify

DATA_URL = "https://gametora.com/_next/data/{build_id}/umamusume/{kind}/{slug}.json"
PAGE_URL = "https://gametora.com/umamusume/{kind}/{slug}"

STAT_NAMES = ("speed", "stamina", "power", "guts", "wit")
# gametora lists aptitudes in this order, as letters; the snapshot keys them by
# name on the game's 1..8 scale (G is 1, S is 8).
APTITUDE_NAMES = ("turf", "dirt", "short", "mile", "medium", "long", "front", "pace", "late", "end")
APTITUDE_GRADES = {letter: grade for grade, letter in enumerate("GFEDCBAS", start=1)}

# Where gametora's word for a card type is not the game's English one.
GAMETORA_TYPE_NAMES = {"intelligence": "wit"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def current_build_id(kind, slug):
    """gametora's Next.js build id, which changes on every one of their deploys."""
    html = get(PAGE_URL.format(kind=kind, slug=slug))
    marker = '"buildId":"'
    start = html.find(marker)
    if start == -1:
        raise SystemExit("could not find gametora's buildId on a card page")
    start += len(marker)
    return html[start:html.index('"', start)]


def get_with_retry(url, attempts=4):
    """`get`, retried on a dropped connection: a full run is several hundred fetches."""
    for attempt in range(1, attempts + 1):
        try:
            return get(url)
        except urllib.error.HTTPError:
            raise  # a real answer (404 means "wrong slug"), not a network blip
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
    return None


def fetch_item(build_id, kind, slugs):
    """The page's itemData under the first slug that resolves, else None."""
    for slug in slugs:
        try:
            payload = json.loads(
                get_with_retry(DATA_URL.format(build_id=build_id, kind=kind, slug=slug))
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise
        time.sleep(0.2)  # be polite
        return payload["pageProps"]["itemData"]
    return None


def uma_differences(card, item):
    """Human-readable lines where the snapshot outfit and gametora's page disagree."""
    initial = next(star for star in card["stars"] if star["star"] == card["default_rarity"])
    ours = {
        "rarity": card["default_rarity"],
        "base stats": [initial["stats"][stat] for stat in STAT_NAMES],
        "growth": [card["growth"][stat] for stat in STAT_NAMES],
        "aptitudes": [initial["aptitude"][name] for name in APTITUDE_NAMES],
    }
    theirs = {
        "rarity": item.get("rarity"),
        "base stats": item.get("base_stats"),
        "growth": item.get("stat_bonus"),
        "aptitudes": [APTITUDE_GRADES.get(letter) for letter in item.get("aptitude") or []],
    }
    return [
        f"{field}: snapshot {value}, gametora {theirs[field]}"
        for field, value in ours.items() if value != theirs[field]
    ]


def support_differences(card, item):
    ours = {"rarity": card["rarity"], "type": card["card_type"]}
    theirs = {
        "rarity": item.get("rarity"),
        "type": GAMETORA_TYPE_NAMES.get(item.get("type"), item.get("type")),
    }
    return [
        f"{field}: snapshot {value}, gametora {theirs[field]}"
        for field, value in ours.items() if value != theirs[field]
    ]


def support_slugs(card_id, *names):
    slugs = []
    for name in names:
        if name and f"{card_id}-{slugify(name)}" not in slugs:
            slugs.append(f"{card_id}-{slugify(name)}")
    return slugs


def check_numbers(build_id, only, character_slugs, support_names):
    """Compare every snapshot card with gametora. Returns the lines to report."""
    report = []

    for card in load(SNAPSHOT_DIR / "cards.json"):
        if only and card["id"] not in only:
            continue
        slug = character_slugs.get(card["id"])
        item = fetch_item(build_id, "characters", [slug]) if slug else None
        if item is None:
            report.append(f"uma {card['id']} {card['name']}: not found on gametora")
            continue
        report += [f"uma {card['id']} {card['name']}: {line}" for line in uma_differences(card, item)]

    for card in load(SNAPSHOT_DIR / "support_cards.json"):
        if only and card["id"] not in only:
            continue
        slugs = support_slugs(card["id"], support_names.get(card["id"]), card["chara_name"])
        item = fetch_item(build_id, "supports", slugs)
        if item is None:
            report.append(f"support {card['id']} {card['name']}: not found on gametora")
            continue
        report += [
            f"support {card['id']} {card['name']}: {line}"
            for line in support_differences(card, item)
        ]

    return report


def check_stale(build_id, only, characters, supports):
    """Cards outside the snapshot whose global release date has passed."""
    today = datetime.date.today().isoformat()
    in_snapshot = {card["id"] for card in load(SNAPSHOT_DIR / "cards.json")}
    in_snapshot |= {card["id"] for card in load(SNAPSHOT_DIR / "support_cards.json")}

    candidates = [
        ("characters", row["card_id"], row["name_en"], [row["gametora_url"]])
        for row in characters
    ] + [
        ("supports", row["id"], row["name_en"], support_slugs(row["id"], row["name_en"]))
        for row in supports
    ]

    report = []
    for kind, card_id, name, slugs in candidates:
        if card_id in in_snapshot or (only and card_id not in only):
            continue
        item = fetch_item(build_id, kind, slugs)
        # A card gametora has no page for yet is not evidence of anything.
        released = (item or {}).get("release_en")
        if released and released <= today:
            report.append(
                f"{kind[:-1]} {card_id} {name}: on global since {released}, "
                "missing from the snapshot"
            )
    return report


def main():
    parser = argparse.ArgumentParser(description="Cross-check the master snapshot with gametora.")
    parser.add_argument("--numbers", action="store_true", help="compare snapshot cards' numbers")
    parser.add_argument("--stale", action="store_true", help="look for released cards we lack")
    parser.add_argument("--only", type=int, nargs="*", help="card ids to check (default: all)")
    args = parser.parse_args()
    both = not (args.numbers or args.stale)
    only = set(args.only or [])

    characters = load(DATA_DIR / "character_cards.json")
    supports = load(DATA_DIR / "support_cards_source.json")
    build_id = current_build_id("characters", characters[0]["gametora_url"])
    print(f"gametora build {build_id}")

    report = []
    if args.numbers or both:
        print("comparing the snapshot's numbers...")
        report += check_numbers(
            build_id, only,
            {row["card_id"]: row["gametora_url"] for row in characters},
            {row["id"]: row["name_en"] for row in supports},
        )
    if args.stale or both:
        print("looking for released cards the snapshot lacks (one fetch per unreleased card)...")
        report += check_stale(build_id, only, characters, supports)

    if not report:
        print("\nNothing to report: the snapshot and gametora agree.")
        return
    print(f"\n{len(report)} thing(s) to look at:")
    for line in report:
        print(f"  {line}")
    sys.exit(1)


if __name__ == "__main__":
    main()
