"""
add_missing_umas.py

Appends `calculatorapi.uma` fixture rows for character cards that:
  1. exist in scripts/data/character_cards.json (the master card list), AND
  2. have an image in the DigitalOcean Space under the umas/ prefix, AND
  3. are not already represented in calculatorapi/fixtures/umas.json.

Why this is needed: the timeline fixture was extended with newer banners, but
the Uma table was never backfilled with the characters those banners feature
(e.g. Shinko Windy, Satono Crown). Every existing Uma row's image is named
`umas/{card_id}-...png`, so card_id is the reliable join key across all three
sources.

Besides the game id, the Uma model needs a display name + image; the name is derived from
the master card list: `name_en` for base cards, `name_en (version_en)` for alt
outfits — matching the convention already used in umas.json (e.g.
"Vivlos (Summer)", "Grass Wonder (New Year)").

Cards in the master list with no image in the Space are skipped and reported,
never invented. New rows are appended (existing pks are never touched).

Run from backend/:
    python scripts/add_missing_umas.py
"""

import json
import os
import re
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

FIXTURES_DIR = Path(__file__).parent.parent / "calculatorapi" / "fixtures"
UMAS_PATH = FIXTURES_DIR / "umas.json"
CARDS_PATH = Path(__file__).parent / "data" / "character_cards.json"

BUCKET = os.environ["DO_SPACES_BUCKET_NAME"]
ENDPOINT = os.environ["DO_SPACES_ENDPOINT_URL"]
ACCESS_KEY = os.environ["DO_SPACES_ACCESS_KEY"]
SECRET_KEY = os.environ["DO_SPACES_SECRET_KEY"]

# card_id is the leading 6-digit token of every umas/ image filename.
CARD_ID_RE = re.compile(r"(\d{6})-")


def card_id_from_image(path: str) -> int | None:
    m = CARD_ID_RE.search(path or "")
    return int(m.group(1)) if m else None


def list_space_uma_keys() -> dict[int, str]:
    """card_id -> full Space object key, for every umas/ image."""
    s3 = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )
    keys: dict[int, str] = {}
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": "umas/", "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            cid = card_id_from_image(obj["Key"])
            if cid is not None:
                keys[cid] = obj["Key"]
        if resp.get("IsTruncated"):
            token = resp["NextContinuationToken"]
        else:
            break
    return keys


def display_name(card: dict) -> str:
    """name_en for base cards; 'name_en (version_en)' for alt outfits."""
    name = card["name_en"]
    if card.get("is_alt") and card.get("version_en") and card["version_en"] != "Original":
        return f"{name} ({card['version_en']})"
    return name


def main() -> None:
    umas = json.loads(UMAS_PATH.read_text(encoding="utf-8"))
    cards = json.loads(CARDS_PATH.read_text(encoding="utf-8"))
    space_keys = list_space_uma_keys()

    existing_card_ids = {
        card_id_from_image(r["fields"]["image"])
        for r in umas
        if card_id_from_image(r["fields"]["image"]) is not None
    }
    max_pk = max((r["pk"] for r in umas), default=0)

    cards_by_id = {c["card_id"]: c for c in cards}

    additions = []
    skipped_no_image = []
    for card_id in sorted(cards_by_id):
        if card_id in existing_card_ids:
            continue
        if card_id not in space_keys:
            skipped_no_image.append(card_id)
            continue
        additions.append((card_id, cards_by_id[card_id], space_keys[card_id]))

    next_pk = max_pk
    for card_id, card, key in additions:
        next_pk += 1
        umas.append({
            "model": "calculatorapi.uma",
            "pk": next_pk,
            "fields": {
                "name": display_name(card),
                "game_id": card_id,
                "image": key,
                "admin_comments": "",
            },
        })

    if additions:
        UMAS_PATH.write_text(
            json.dumps(umas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    print(f"umas.json: {len(umas) - len(additions)} existing, {len(additions)} added (new pks {max_pk + 1}..{next_pk})")
    for card_id, card, key in additions:
        print(f"  + pk={_pk_for(umas, key)}  {display_name(card):40s}  {key}")

    print(f"\n{len(skipped_no_image)} card(s) in master list with NO Space image (skipped):")
    for card_id in skipped_no_image:
        c = cards_by_id[card_id]
        print(f"  - {card_id}  {display_name(c)}")


def _pk_for(umas, key):
    for r in umas:
        if r["fields"]["image"] == key:
            return r["pk"]
    return "?"


if __name__ == "__main__":
    main()
