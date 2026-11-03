"""
upload_borderless_cards.py

Uploads the borderless support card art to the DigitalOcean Space under
`support_cards_borderless/<game_id>-<Name>-<type>.webp`, which is where
`manage.py link_borderless_art --kind cards` looks for it.

The source is Daptrius's Drive "Data" download (notes/support-card-art-plan.md):
three zips, or the folders they unzip to, holding the game's own card textures
at `Data/jp/scraper/Game Images/support_cards/tex_support_card_<id>__...png`.
A texture IS the borderless card: 1536 x 2048, no rarity frame, no type icon,
no rounded corners. The site frames it itself (frontend FramedCard, from
Daptrius's SVG overlays). Pass every zip or folder at once: the textures are
split across them, and a card found twice (a zip and its own folder) is sent
once.

Each texture is shrunk to the site's 450 x 600 and saved as WebP at quality 85:
53 KB for Oguri Cap against the PNG's 504 KB (measured 2026-11-02), about 30 MB
for the whole set.

THE FILE NAME is for people: the admin image picker lists this folder, and
`30024-Oguri-Cap-pwr.webp` is something an editor can read. It follows the
bordered pictures' names (`support_cards/30024-Oguri-Cap-pwr.png`): the
character's English name from the global snapshot (global's name wins), else
from Daptrius's `Data/shared/support_cards.json` for a card not on global yet,
and the card type's short tag. The link command needs only the leading id.

The download's files are data: a zip entry is read only when its name matches
the texture pattern, nothing in it is executed, and Pillow's own size limit
guards the decode.

Files already in the Space are not re-uploaded, so a re-run after the game adds
cards sends only the new ones. A REPLACED texture (same name, new art) is
therefore skipped too: delete it from the Space first.

Run from backend/:
    python scripts/upload_borderless_cards.py "<Data folder>"            # report only
    python scripts/upload_borderless_cards.py "<Data folder>" --upload   # send to the Space
"""

import argparse
import io
import json
import os
import re
import zipfile
from pathlib import Path

from PIL import Image

SPACE_PREFIX = "support_cards_borderless/"
SIZE = (450, 600)
WEBP_QUALITY = 85
# The game's texture name: five digits, then the export's double underscore.
TEXTURE = re.compile(r"^tex_support_card_(\d{5})__.*\.png$", re.IGNORECASE)
NAMES_ENTRY = "shared/support_cards.json"
SNAPSHOTS = Path(__file__).parent / "data"
# The tags the bordered pictures already use (`support_cards/...-pwr.png`).
# Group cards had none of their own; `grp` is new.
TYPE_TAGS = {
    "speed": "spd", "stamina": "stm", "power": "pwr", "guts": "gts",
    "wit": "wts", "friend": "pls", "group": "grp",
}


def _entries(source):
    """(path inside the source, bytes-loader) for every file in a zip or folder.

    A loader, not the bytes: the report-only run never reads 2 GB of PNGs.
    """
    if source.is_dir():
        return [
            (path.relative_to(source).as_posix(), path.read_bytes)
            for path in sorted(source.rglob("*")) if path.is_file()
        ]
    archive = zipfile.ZipFile(source)  # pylint: disable=consider-using-with
    return [
        (info.filename, (lambda info=info: archive.read(info)))
        for info in archive.infolist() if not info.is_dir()
    ]


def read_sources(sources):
    """Every card texture across the sources, {game_id: loader}, plus the
    English names Daptrius's list carries, {game_id: name}."""
    textures, names = {}, {}
    for source in sources:
        for path, loader in _entries(source):
            match = TEXTURE.match(Path(path).name)
            if match:
                # First one wins: a zip and its unzipped folder hold the same file.
                textures.setdefault(int(match.group(1)), loader)
            elif path.endswith(NAMES_ENTRY) and not names:
                names = {
                    row["id"]: row.get("name_en") or ""
                    for row in json.loads(loader())
                }
    return textures, names


def load_snapshot(name):
    path = SNAPSHOTS / name / "support_cards.json"
    return {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))}


def slug(text):
    """`Oguri Cap` -> `Oguri-Cap`; anything but ASCII letters and digits goes."""
    return "-".join(re.findall(r"[A-Za-z0-9]+", text))


def file_name(game_id, global_cards, jp_cards, daptrius_names):
    """`30024-Oguri-Cap-pwr.webp`, leaving out whatever is unknown."""
    card = global_cards.get(game_id) or jp_cards.get(game_id) or {}
    name = slug(global_cards[game_id]["chara_name"]) if game_id in global_cards else slug(
        daptrius_names.get(game_id, "")
    )
    tag = TYPE_TAGS.get(card.get("card_type", ""), "")
    return "-".join(part for part in (str(game_id), name, tag) if part) + ".webp"


def to_webp(png_bytes):
    """The texture at the site's 450 x 600, as WebP bytes."""
    with Image.open(io.BytesIO(png_bytes)) as image:
        image = image.convert("RGB").resize(SIZE, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "WEBP", quality=WEBP_QUALITY, method=6)
        return out.getvalue()


def upload(files):
    """Put each file in the Space, public-read, skipping any already there.
    `files` is {name: loader of the texture's PNG bytes}."""
    # Imported here so the report-only run works without boto3 or credentials.
    import boto3  # pylint: disable=import-outside-toplevel
    from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel

    load_dotenv(Path(__file__).parent.parent / ".env")
    bucket = os.environ["DO_SPACES_BUCKET_NAME"]
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ["DO_SPACES_ENDPOINT_URL"],
        aws_access_key_id=os.environ["DO_SPACES_ACCESS_KEY"],
        aws_secret_access_key=os.environ["DO_SPACES_SECRET_KEY"],
    )
    existing = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=SPACE_PREFIX):
        existing.update(obj["Key"] for obj in page.get("Contents", []))

    sent, kept = 0, 0
    for name, loader in files.items():
        key = f"{SPACE_PREFIX}{name}"
        if key in existing:
            kept += 1
            continue
        s3.put_object(
            Bucket=bucket, Key=key, Body=to_webp(loader()),
            ACL="public-read", ContentType="image/webp",
        )
        sent += 1
    print(f"space: {sent} uploaded, {kept} already present, under {SPACE_PREFIX}")


def main():
    parser = argparse.ArgumentParser(description="Upload the borderless support card art to the Space.")
    parser.add_argument("sources", type=Path, nargs="+",
                        help="the Data download: its zips, the folders they unzip to, or the folder holding them")
    parser.add_argument("--upload", action="store_true", help="send to the Space (default: report only)")
    args = parser.parse_args()

    textures, daptrius_names = read_sources(args.sources)
    global_cards, jp_cards = load_snapshot("master_snapshot"), load_snapshot("jp_snapshot")
    files = {
        file_name(game_id, global_cards, jp_cards, daptrius_names): loader
        for game_id, loader in sorted(textures.items())
    }
    missing = sorted(set(jp_cards) - set(textures))
    unnamed = sorted(name for name in files if not re.match(r"^\d{5}-[A-Za-z]", name))
    print(f"{len(files)} card texture(s) to send; the JP snapshot lists {len(jp_cards)} card(s)")
    if missing:
        print(f"{len(missing)} card(s) with no texture: {', '.join(map(str, missing))}")
    if unnamed:
        print(f"{len(unnamed)} file(s) with no English name: {', '.join(unnamed)}")
    if args.upload:
        upload(files)


if __name__ == "__main__":
    main()
