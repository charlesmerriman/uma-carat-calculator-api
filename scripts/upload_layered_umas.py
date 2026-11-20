"""
upload_layered_umas.py

Uploads the layered uma art (notes/layered-uma-art-plan.md) to the
DigitalOcean Space as lossless WebP:

    layers/base/<id>-<Name>.png                -> umas_layered/base/<id>-<Name>.webp
    layers/foreground/392x420/<id>-<Name>.png  -> umas_layered/foreground/<id>-<Name>.webp

which is where `manage.py link_borderless_art --kind layers` looks for them.

The source is Daptrius's Drive export ("Characters"), a zip or an extracted
folder. Only the `layers/` files are read: the flattened 360x420 / 392x420
composites and the 360x420 foregrounds are left behind (the site draws the
wide foreground everywhere, Daptrius 2026-11-18). A file is sent only as half
of a pair, base and foreground under the same name, because the site never
draws one without the other.

Why WebP: a foreground is almost all transparent, yet the PNGs average 185 KB;
lossless WebP is about 10 KB (bases 187 -> 130 KB). Lossless because lossy
WebP greys the art's one-pixel coloured edges.

Files already in the Space are not re-uploaded, so a re-run after Daptrius adds
outfits sends only the new ones. A REPLACED file (same name, new art) is
therefore skipped too: delete it from the Space first.

Run from backend/:
    python scripts/upload_layered_umas.py ~/Characters.zip            # report only
    python scripts/upload_layered_umas.py ~/Characters.zip --upload   # send to the Space
"""

import argparse
import io
import os
import re
import zipfile
from pathlib import Path, PurePosixPath

SPACE_PREFIXES = {"base": "umas_layered/base/", "foreground": "umas_layered/foreground/"}
# Where each layer sits inside the export, as path parts (folder or zip).
SOURCE_FOLDERS = {"base": ("layers", "base"), "foreground": ("layers", "foreground", "392x420")}
# Six digits, then a separator or the extension: an uma outfit id. NPC and
# casual entries ("11001_901001-...") don't match and stay behind.
UMA_FILE = re.compile(r"^(\d{6})(?!\d).*\.png$", re.IGNORECASE)
# The "(All)" placeholder's blank square, skipped by id as the borderless
# upload does.
SKIPPED_IDS = {100000}


def _layer_of(path):
    """Which layer a path in the export belongs to, or None."""
    parts = PurePosixPath(path).parts[:-1]
    for layer, wanted in SOURCE_FOLDERS.items():
        if tuple(parts[-len(wanted):]) == wanted:
            return layer
    return None


def read_source(source):
    """{layer: {filename: bytes-loader}} for every uma PNG in the two layer
    folders. Loaders, not bytes: the report-only run never reads the art."""
    found = {layer: {} for layer in SOURCE_FOLDERS}
    if source.is_dir():
        entries = [
            (path.relative_to(source).as_posix(), path.read_bytes)
            for path in sorted(source.rglob("*.png"))
        ]
    else:
        archive = zipfile.ZipFile(source)  # pylint: disable=consider-using-with
        entries = [
            (info.filename, lambda info=info: archive.read(info))
            for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".png")
        ]
    for path, loader in entries:
        layer = _layer_of(path)
        name = PurePosixPath(path).name
        match = UMA_FILE.match(name)
        if layer and match and int(match.group(1)) not in SKIPPED_IDS:
            found[layer][name] = loader
    return found


def pairs(found):
    """The names present in both layers, and the ones in only one."""
    both = sorted(found["base"].keys() & found["foreground"].keys())
    single = sorted(found["base"].keys() ^ found["foreground"].keys())
    return both, single


def to_webp(png_bytes):
    """Lossless WebP of a PNG, transparency kept."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    buffer = io.BytesIO()
    Image.open(io.BytesIO(png_bytes)).convert("RGBA").save(buffer, "WEBP", lossless=True)
    return buffer.getvalue()


def upload(found, names):
    """Put each pair in the Space, public-read, skipping keys already there."""
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
    for layer, prefix in SPACE_PREFIXES.items():
        existing = set()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            existing.update(obj["Key"] for obj in page.get("Contents", []))
        sent, kept = 0, 0
        for name in names:
            key = f"{prefix}{PurePosixPath(name).with_suffix('.webp').name}"
            if key in existing:
                kept += 1
                continue
            s3.put_object(
                Bucket=bucket, Key=key, Body=to_webp(found[layer][name]()),
                ACL="public-read", ContentType="image/webp",
            )
            sent += 1
        print(f"space: {sent} uploaded, {kept} already present, under {prefix}")


def main():
    """Report the pairs in the export, and send them with --upload."""
    parser = argparse.ArgumentParser(description="Upload the layered uma art to the Space.")
    parser.add_argument("source", type=Path, help="the Drive export: a .zip or an extracted folder")
    parser.add_argument(
        "--upload", action="store_true", help="send to the Space (default: report only)",
    )
    args = parser.parse_args()

    found = read_source(args.source)
    both, single = pairs(found)
    print(f"{len(both)} uma pair(s) to send (base + 392x420 foreground)")
    if single:
        print(f"{len(single)} file(s) with no partner, left behind: {', '.join(single[:20])}")
    if args.upload:
        upload(found, both)


if __name__ == "__main__":
    main()
