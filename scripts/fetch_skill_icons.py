"""
fetch_skill_icons.py

Downloads the generic skill icons (63 global, 125 with JP's) and, on request, uploads them to the
DigitalOcean Space under `skills/<icon_id>.png`, which is where
`manage.py link_skill_images` expects them.

WHY NOT THE GAME CLIENT: its asset index is encrypted on global and the asset
folder is 36 GB of unnamed bundles, so the icons are fetched from gametora's
public copy of the same textures instead, one file per icon id. They are
generic UI assets (a running shoe, a green arrow), which is why hosting them
the way the site already hosts card art was judged fine.

The icon ids come from the committed snapshot (skills.json), so a new icon the
game adds is fetched the next time the snapshot is refreshed and this runs.
Files already in the local folder are not re-downloaded; files already in the
Space are not re-uploaded.

Run from backend/:
    python scripts/fetch_skill_icons.py                # download to scripts/data/skill_icons/
    python scripts/fetch_skill_icons.py --upload       # ...and upload to the Space
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

SNAPSHOT_SKILLS = Path(__file__).parent / "data" / "master_snapshot" / "skills.json"
# JP's skills use icons global has none of yet (evolved skills' pink icons,
# 2026-10-23), so its snapshot's ids are fetched too when it is there.
JP_SNAPSHOT_SKILLS = Path(__file__).parent / "data" / "jp_snapshot" / "skills.json"
DEFAULT_OUT = Path(__file__).parent / "data" / "skill_icons"
SOURCE_URL = "https://gametora.com/images/umamusume/skill_icons/utx_ico_skill_{icon_id}.png"
SPACE_PREFIX = "skills/"
USER_AGENT = "Mozilla/5.0 (umacaratcalculator.com asset fetch)"


def icon_ids():
    """Every icon id a global or JP skill uses. 0 is no icon at all."""
    ids = set()
    for path in (SNAPSHOT_SKILLS, JP_SNAPSHOT_SKILLS):
        if path.is_file():
            ids |= {skill["icon_id"] for skill in json.loads(path.read_text(encoding="utf-8"))}
    return sorted(ids - {0, None})


def download(ids, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    fetched, kept, missing = 0, 0, []
    for icon_id in ids:
        target = out_dir / f"{icon_id}.png"
        if target.is_file() and target.stat().st_size > 0:
            kept += 1
            continue
        request = urllib.request.Request(
            SOURCE_URL.format(icon_id=icon_id), headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                target.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            # The source lacks a few new icons; report them and go on, so one
            # gap doesn't stop the rest.
            if exc.code != 404:
                raise
            missing.append(icon_id)
            continue
        fetched += 1
        time.sleep(0.2)  # be polite; ~125 files at most
    print(f"icons: {fetched} downloaded, {kept} already present, in {out_dir}")
    if missing:
        print(f"not at the source ({len(missing)}): {', '.join(map(str, missing))}")


def upload(ids, out_dir):
    """Put each icon in the Space, public-read, skipping any already there."""
    # Imported here so the download half works without boto3 or credentials.
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
    for icon_id in ids:
        key = f"{SPACE_PREFIX}{icon_id}.png"
        if key in existing:
            kept += 1
            continue
        # An icon the source lacked was never downloaded (download() names them).
        if not (out_dir / f"{icon_id}.png").is_file():
            continue
        s3.upload_file(
            str(out_dir / f"{icon_id}.png"), bucket, key,
            ExtraArgs={"ACL": "public-read", "ContentType": "image/png"},
        )
        sent += 1
    print(f"space: {sent} uploaded, {kept} already present, under {SPACE_PREFIX}")


def main():
    parser = argparse.ArgumentParser(description="Fetch the skill icons, optionally upload them.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="local folder for the PNGs")
    parser.add_argument("--upload", action="store_true", help="also upload to the Space")
    args = parser.parse_args()

    ids = icon_ids()
    print(f"{len(ids)} icon id(s) in the snapshot")
    download(ids, args.out)
    if args.upload:
        upload(ids, args.out)


if __name__ == "__main__":
    main()
