"""
fetch_support_events.py

Writes scripts/data/master_snapshot/support_events.json: which skills each
support card can give through its training EVENTS, one row per (card, skill).

WHY NOT THE GAME DATABASE: hints have a clean table there (see
extract_master_snapshot.py), but event skills come from story-choice data with
no usable link back to the card. gametora's per-card pages carry the list as a
plain `event_skills` array, so that is the agreed source for this one piece.

HOW: gametora is a Next.js site; each card page has a JSON twin at
`/_next/data/<buildId>/umamusume/supports/<slug>.json`. The build id is read
off one HTML page at the start (it changes on every deploy). The slug is
`<id>-<name>` where the name is gametora's own English name for the card,
which scripts/data/support_cards_source.json carries as `name_en` (a
gametora-shaped file); the game's character name from the snapshot is tried
second. A card that resolves under neither slug is reported, not guessed.

Global only, by construction: the card ids come from the committed snapshot,
and a skill id gametora lists that the snapshot does not know (a skill the
card gives on JP but not yet on global) is dropped and counted, so the file
never names a skill the import cannot find.
Idempotent and deterministic: rows are sorted, so a re-run diffs cleanly.

Run from backend/:
    python scripts/fetch_support_events.py
    python scripts/fetch_support_events.py --only 30024 30067   # a few cards
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_DIR = DATA_DIR / "master_snapshot"
OUT_PATH = SNAPSHOT_DIR / "support_events.json"
GAMETORA_NAMES = DATA_DIR / "support_cards_source.json"

PAGE_URL = "https://gametora.com/umamusume/supports/{slug}"
DATA_URL = "https://gametora.com/_next/data/{build_id}/umamusume/supports/{slug}.json"
USER_AGENT = "Mozilla/5.0 (umacaratcalculator.com data fetch)"


def slugify(name):
    """gametora's slug rule as observed: lowercase, punctuation dropped, spaces to dashes."""
    cleaned = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("-", " "))
    return re.sub(r"\s+", "-", cleaned.strip())


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def current_build_id(any_slug):
    html = get(PAGE_URL.format(slug=any_slug))
    match = re.search(r'"buildId":"([^"]+)"', html)
    if not match:
        raise SystemExit("could not find gametora's buildId on a card page")
    return match.group(1)


def fetch_card(build_id, slugs):
    """The card's page data under the first slug that resolves, else None."""
    for slug in slugs:
        try:
            payload = json.loads(get(DATA_URL.format(build_id=build_id, slug=slug)))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise
        return payload["pageProps"]["itemData"]
    return None


def main():
    parser = argparse.ArgumentParser(description="Fetch support card event skills from gametora.")
    parser.add_argument("--only", type=int, nargs="*", help="card ids to fetch (default: all)")
    args = parser.parse_args()

    cards = json.loads((SNAPSHOT_DIR / "support_cards.json").read_text(encoding="utf-8"))
    global_skills = {
        skill["id"]
        for skill in json.loads((SNAPSHOT_DIR / "skills.json").read_text(encoding="utf-8"))
    }
    gametora_names = {
        row["id"]: row["name_en"]
        for row in json.loads(GAMETORA_NAMES.read_text(encoding="utf-8"))
    }
    if args.only:
        cards = [card for card in cards if card["id"] in set(args.only)]

    build_id = current_build_id(f"{cards[0]['id']}-{slugify(cards[0]['chara_name'])}")
    print(f"gametora build {build_id}; fetching {len(cards)} card(s)")

    rows, unresolved, not_on_global = [], [], 0
    for card in cards:
        candidates = []
        for name in (gametora_names.get(card["id"]), card["chara_name"]):
            if name and f"{card['id']}-{slugify(name)}" not in candidates:
                candidates.append(f"{card['id']}-{slugify(name)}")
        data = fetch_card(build_id, candidates)
        if data is None:
            unresolved.append(f"{card['id']}  {card['name']}  tried {', '.join(candidates)}")
            continue
        for skill_id in data.get("event_skills") or []:
            if skill_id not in global_skills:
                not_on_global += 1
                continue
            rows.append({"support_card_id": card["id"], "skill_id": skill_id})
        time.sleep(0.2)  # be polite

    rows.sort(key=lambda row: (row["support_card_id"], row["skill_id"]))
    OUT_PATH.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    print(
        f"wrote {len(rows)} event skill row(s) for {len(cards) - len(unresolved)} card(s) "
        f"to {OUT_PATH}; dropped {not_on_global} row(s) naming a skill not on global"
    )
    if unresolved:
        print(f"\n{len(unresolved)} card(s) not found on gametora under any tried slug:")
        for line in unresolved:
            print(f"  {line}")


if __name__ == "__main__":
    main()
