"""
cdn_masterdata.py

Fetches the Global game's master.mdb straight from the game's public CDN, the
same files the client downloads, with no install, key or login. The extractor
(`extract_master_snapshot.py --cdn-version N`) and the daily check
(`masterdata_check.py`) both go through this module, so the manifest format is
decoded in one place. Plan and research: notes/masterdata-cdn-plan.md (notes repo).

HOW THE CDN IS LAID OUT (checked against the live CDN, 2026-11-27):

  {CDN_BASE}/{version}/manifests/manifestdat/root.manifest.bsv.lz4
      The ONLY url with a version number in it. Lists the platforms
      (Windows, iOS, Android).
  {CDN_BASE}/resources/Manifest/XX/<name>
      A platform's manifest (37 categories), then a category's manifest.
      The `master` category holds one entry, master.mdb.lz4.
  {CDN_BASE}/resources/Generic/XX/<name>
      The file itself: an LZ4 frame around a plain SQLite database
      (about 5.7 MB packed, 17.7 MB unpacked).

  Every <name> is base32(SHA1(prefix8 + size as 8 big-endian bytes + name)),
  where prefix8 and size come from the parent manifest's entry, and XX is its
  first two characters. Content-addressed: a name never points at stale bytes.

  Every manifest is an LZ4 frame. Unpacked, it starts with 4 magic bytes and a
  varint entry count, then a few header bytes this module skips (their meaning
  isn't known; the layout below is what the live files hold, and the tests pin
  it). Varints are big-endian 7-bit groups, high bit set on every byte but the
  last.

WHAT "VERSION" MEANS: the CDN has no listing and nothing on it says which
version the game is running. A version exists when its root manifest answers;
a missing one answers 403 (not 404) on this CDN. So a 403 is ambiguous: "no
such version" or "the CDN is refusing us". Callers that scan for new versions
must first confirm a known version still answers (masterdata_check.py does).

A REVISION of a version (its master.mdb replaced while the number stays the
same) shows as a new size or hash on the master entry. `master_entry()` reads
those from the manifests alone, three small requests, without the big download.

CLI, from backend/ (needs `lz4`, in requirements-dev.txt; never in production):
    python scripts/cdn_masterdata.py check 10008795       # size and hash
    python scripts/cdn_masterdata.py fetch 10008795 /tmp/mdb   # writes master.mdb
    python scripts/cdn_masterdata.py scan 10008790 20     # which numbers exist
"""

import argparse
import base64
import hashlib
import sqlite3
import sys
from pathlib import Path

import requests

CDN_BASE = "https://assets-umamusume-en.akamaized.net/dl/vertical"
PLATFORM = b"Windows"
MASTER_CATEGORY = b"master"
MASTER_FILE = b"master.mdb.lz4"
LZ4_MAGIC = b"\x04\x22\x4d\x18"
SQLITE_MAGIC = b"SQLite format 3\x00"
# The CDN's answers for "no such object". Global answers 403; 404 is kept for
# safety (the JP host answers 404).
MISSING = (403, 404)
TIMEOUT = 60


class CdnError(Exception):
    """The CDN answered, but not in a way this module understands."""


# ---------------------------------------------------------------- the format

def read_varint(data, i):
    """A big-endian 7-bit varint at `data[i]`: (value, index after it)."""
    value = 0
    while True:
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def parse_platform_manifest(data):
    """
    The root manifest and a platform's manifest share one layout:
    [magic 4][count varint][7 header bytes] then per entry
    [name][0][size varint][prefix 8]. Returns [(name, size, prefix8)].
    """
    count, i = read_varint(data, 4)
    i += 7
    entries = []
    for _ in range(count):
        end = data.index(0, i)
        name = data[i:end]
        i = end + 1
        size, i = read_varint(data, i)
        prefix = data[i:i + 8]
        i += 8
        entries.append((name, size, prefix))
    return entries


def parse_category_manifest(data):
    """
    A category's manifest: [magic 4][count varint][varint][10 header bytes],
    then per entry [name][0], either [0 0 0] or [second name][0][2 bytes],
    then [size varint][hash 16]. Returns [(names, size, hash16)].
    The hash's first 8 bytes are the prefix for the file's CDN name.
    """
    count, i = read_varint(data, 4)
    _, i = read_varint(data, i)
    i += 10
    entries = []
    for _ in range(count):
        end = data.index(0, i)
        names = [data[i:end]]
        i = end + 1
        if data[i] == 0:
            i += 3
        else:
            end = data.index(0, i)
            names.append(data[i:end])
            i = end + 1 + 2
        size, i = read_varint(data, i)
        digest = data[i:i + 16]
        i += 16
        entries.append((names, size, digest))
    return entries


def asset_name(prefix8, size, name):
    """The content-addressed CDN name of a file a manifest entry describes."""
    digest = hashlib.sha1(prefix8 + size.to_bytes(8, "big") + name).digest()
    return base64.b32encode(digest).decode().rstrip("=")


def root_manifest_url(version):
    return f"{CDN_BASE}/{int(version)}/manifests/manifestdat/root.manifest.bsv.lz4"


def object_url(folder, name):
    return f"{CDN_BASE}/resources/{folder}/{name[:2]}/{name}"


# ---------------------------------------------------------------- the network

def _unpack(raw):
    """Unpack an LZ4 frame. Imported here so the parsers need no lz4."""
    import lz4.frame  # pylint: disable=import-outside-toplevel
    if raw[:4] != LZ4_MAGIC:
        raise CdnError(f"expected an LZ4 frame, got {raw[:4].hex()}")
    return lz4.frame.decompress(raw)


def _get(url, session):
    """The body, or None when the CDN says the object doesn't exist."""
    response = session.get(url, timeout=TIMEOUT)
    if response.status_code in MISSING:
        return None
    response.raise_for_status()
    return response.content


def _required(url, session):
    """Like _get, but a missing object is an error: the manifest named it."""
    body = _get(url, session)
    if body is None:
        raise CdnError(f"the manifest names {url}, but the CDN says it doesn't exist")
    return body


def _master_entry_raw(version, session):
    """(names, size, hash16) of the master.mdb entry, or None if no such version."""
    root = _get(root_manifest_url(version), session)
    if root is None:
        return None
    platforms = parse_platform_manifest(_unpack(root))
    platform = next((p for p in platforms if p[0] == PLATFORM), None)
    if platform is None:
        raise CdnError(f"version {version} has no {PLATFORM.decode()} platform")
    name = asset_name(platform[2], platform[1], platform[0])
    categories = parse_platform_manifest(_unpack(_required(object_url("Manifest", name), session)))
    category = next((c for c in categories if c[0] == MASTER_CATEGORY), None)
    if category is None:
        raise CdnError(f"version {version} has no master category")
    name = asset_name(category[2], category[1], category[0])
    entries = parse_category_manifest(_unpack(_required(object_url("Manifest", name), session)))
    entry = next((e for e in entries if e[0][0] == MASTER_FILE), None)
    if entry is None:
        raise CdnError(f"version {version}'s master category has no {MASTER_FILE.decode()}")
    return entry


def master_entry(version, session=None):
    """
    {"version", "size", "hash"} for `version`'s master.mdb, or None when the
    CDN says the version doesn't exist. `hash` is the manifest's 16 bytes in
    hex: together with `size`, what changes when a version is revised.
    """
    entry = _master_entry_raw(version, session or requests.Session())
    if entry is None:
        return None
    _, size, digest = entry
    return {"version": int(version), "size": size, "hash": digest.hex()}


def download_mdb(version, out_path, session=None):
    """
    Writes `version`'s master.mdb (unpacked SQLite) to `out_path` and returns
    the same dict as master_entry(). Raises CdnError if the version doesn't
    exist or the bytes aren't what the manifest promised.
    """
    session = session or requests.Session()
    entry = _master_entry_raw(version, session)
    if entry is None:
        raise CdnError(f"version {version} is not on the CDN")
    names, size, digest = entry
    raw = _required(object_url("Generic", asset_name(digest[:8], size, names[0])), session)
    if len(raw) != size:
        raise CdnError(f"master.mdb.lz4 is {len(raw)} bytes, the manifest says {size}")
    data = _unpack(raw)
    if not data.startswith(SQLITE_MAGIC):
        raise CdnError("master.mdb is not a SQLite database")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    # Open it once read-only: a truncated file fails here, not in the extractor.
    conn = sqlite3.connect(f"file:{out_path}?mode=ro", uri=True)
    try:
        conn.execute("select count(*) from sqlite_master").fetchone()
    finally:
        conn.close()
    return {"version": int(version), "size": size, "hash": digest.hex()}


def version_exists(version, session=None):
    """Does the CDN answer for this version's root manifest? One 77-byte request."""
    return _get(root_manifest_url(version), session or requests.Session()) is not None


def scan(start, count, session=None):
    """The versions in [start, start + count) that exist. Tries EVERY number:
    they went up in steps of 5 in November 2026, but nothing promises that."""
    session = session or requests.Session()
    return [v for v in range(int(start), int(start) + int(count)) if version_exists(v, session)]


# ---------------------------------------------------------------- the CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="print a version's master.mdb size and hash")
    check.add_argument("version", type=int)
    fetch = commands.add_parser("fetch", help="download a version's master.mdb")
    fetch.add_argument("version", type=int)
    fetch.add_argument("out_dir", type=Path)
    scan_cmd = commands.add_parser("scan", help="list which version numbers exist")
    scan_cmd.add_argument("start", type=int)
    scan_cmd.add_argument("count", type=int)
    args = parser.parse_args(argv)

    if args.command == "check":
        entry = master_entry(args.version)
        if entry is None:
            print(f"{args.version}: not on the CDN")
            return 1
        print(f"{entry['version']}: master.mdb.lz4 {entry['size']} bytes, hash {entry['hash']}")
    elif args.command == "fetch":
        out = args.out_dir / "master.mdb"
        entry = download_mdb(args.version, out)
        print(f"wrote {out} ({out.stat().st_size} bytes) from {entry['version']}, hash {entry['hash']}")
    else:
        found = scan(args.start, args.count)
        print("found:", ", ".join(map(str, found)) if found else "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
