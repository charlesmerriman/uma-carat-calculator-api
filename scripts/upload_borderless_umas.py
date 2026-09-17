"""
upload_borderless_umas.py

Uploads the borderless uma art to the DigitalOcean Space under
`umas_borderless/<game_id>-<Name>.png`, which is where
`manage.py link_borderless_umas` looks for it.

The source is the artist's Drive export: a zip (or an already-extracted
folder) holding uma outfits AND support cards side by side. The two are told
apart by the id the filename starts with: an uma outfit's game id is six digits
(`114901-Phalaenopsis.png`), a support card's is five
(`30201-Narita-Taishin-Wit.png`). Only the uma files are uploaded; SupportCard
has no borderless field yet, so its files are counted and left behind.

Filenames are kept as they are, because the admin image picker lists this
folder and `114901-Phalaenopsis.png` is something an editor can read. The link
command only needs the leading id.

Files already in the Space are not re-uploaded, so a re-run after the artist
adds a few outfits sends only the new ones. A REPLACED file (same name, new
art) is therefore skipped too: delete it from the Space first.

Run from backend/:
    python scripts/upload_borderless_umas.py ~/Borderless.zip            # report only
    python scripts/upload_borderless_umas.py ~/Borderless.zip --upload   # send to the Space
"""

import argparse
import os
import re
import zipfile
from pathlib import Path

SPACE_PREFIX = "umas_borderless/"
# Six digits, then a separator or the extension: an uma outfit id. Five digits
# is a support card and does not match.
UMA_FILE = re.compile(r"^(\d{6})(?!\d).*\.png$", re.IGNORECASE)
# The export carries a blank transparent square named 100000.png, a stand-in
# for the "(All)" placeholder row. That row has no game_id to match on and
# blank art is no use to it, so the file is skipped by name.
SKIPPED_IDS = {100000}


def read_source(source):
    """Return {filename: bytes-loader} for every PNG in the zip or folder.

    A loader, not the bytes: the report-only run never needs 80 MB in memory.
    """
    if source.is_dir():
        return {path.name: path.read_bytes for path in sorted(source.rglob("*.png"))}
    archive = zipfile.ZipFile(source)  # pylint: disable=consider-using-with
    return {
        Path(info.filename).name: (lambda info=info: archive.read(info))
        for info in archive.infolist()
        if not info.is_dir() and info.filename.lower().endswith(".png")
    }


def uma_files(files):
    """Split the export into the uma files worth sending and a count of the rest."""
    umas, other = {}, 0
    for name, loader in files.items():
        match = UMA_FILE.match(name)
        if match and int(match.group(1)) not in SKIPPED_IDS:
            umas[name] = loader
        else:
            other += 1
    return umas, other


def upload(umas):
    """Put each file in the Space, public-read, skipping any already there."""
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
    for name, loader in umas.items():
        key = f"{SPACE_PREFIX}{name}"
        if key in existing:
            kept += 1
            continue
        s3.put_object(
            Bucket=bucket, Key=key, Body=loader(),
            ACL="public-read", ContentType="image/png",
        )
        sent += 1
    print(f"space: {sent} uploaded, {kept} already present, under {SPACE_PREFIX}")


def main():
    parser = argparse.ArgumentParser(description="Upload the borderless uma art to the Space.")
    parser.add_argument("source", type=Path, help="the Drive export: a .zip or an extracted folder")
    parser.add_argument("--upload", action="store_true", help="send to the Space (default: report only)")
    args = parser.parse_args()

    umas, other = uma_files(read_source(args.source))
    print(f"{len(umas)} uma file(s) to send; {other} other file(s) (support cards, placeholders) left behind")
    if args.upload:
        upload(umas)


if __name__ == "__main__":
    main()
