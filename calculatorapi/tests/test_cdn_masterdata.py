"""
scripts/cdn_masterdata.py: the game CDN's manifest format and the walk from a
version number to its master.mdb. And scripts/masterdata_check.py's decisions:
what it reads, when it fails, when it opens a snapshot PR.

No test touches the network. The manifests are built here in the layout the
live CDN serves (checked byte by byte against version 10008795 on 2026-11-28),
and a fake session answers each URL. Cygames' own files are not committed.
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import lz4.frame

from .base import CalculatorTestCase
from .test_snapshot_scripts import load_script

cdn = load_script("cdn_masterdata")


def varint(value):
    """The big-endian 7-bit varint the manifests use."""
    groups = [value & 0x7F]
    value >>= 7
    while value:
        groups.append(value & 0x7F)
        value >>= 7
    groups.reverse()
    return bytes([g | 0x80 for g in groups[:-1]] + [groups[-1]])


def platform_manifest(entries):
    """The root / platform layout: magic, count, 7 header bytes, entries."""
    body = b"\xbf\x11\x00\x08" + varint(len(entries)) + b"\x18\x20\x03\x40\x12\x21\x08"
    for name, size, prefix in entries:
        body += name + b"\x00" + varint(size) + prefix
    return body


def category_manifest(entries):
    """A category: magic, count, a varint, 10 header bytes, entries.
    An entry is (names, size, hash16); one name takes the 0 0 0 form."""
    body = b"\xbf\x11\x00\x0c" + varint(len(entries)) + b"\x30" + bytes(range(10))
    for names, size, digest in entries:
        body += names[0] + b"\x00"
        body += b"\x00\x00\x00" if len(names) == 1 else names[1] + b"\x00" + b"\x01\x02"
        body += varint(size) + digest
    return body


class FakeResponse:  # pylint: disable=too-few-public-methods
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:  # pylint: disable=too-few-public-methods
    """Answers the URLs it was given; everything else is a 403, like the CDN."""

    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def get(self, url, timeout):  # pylint: disable=unused-argument
        self.asked.append(url)
        status, content = self.routes.get(url, (403, b""))
        return FakeResponse(status, content)


def mdb_bytes():
    """A real (tiny) SQLite file's bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.mdb"
        conn = sqlite3.connect(path)
        conn.execute("create table skill_data (id integer)")
        conn.commit()
        conn.close()
        return path.read_bytes()


def fake_cdn(version=10008795, mdb=None, digest=bytes(range(1, 9)) + bytes(8)):
    """Routes for one version's whole tree, as the live CDN lays it out."""
    packed_mdb = lz4.frame.compress(mdb if mdb is not None else mdb_bytes())
    category = lz4.frame.compress(category_manifest([
        ([b"master.mdb.lz4"], len(packed_mdb), digest),
    ]))
    category_prefix = b"C" * 8
    platform = lz4.frame.compress(platform_manifest([
        (b"story", 10, b"S" * 8),
        (b"master", len(category), category_prefix),
    ]))
    platform_prefix = b"P" * 8
    root = lz4.frame.compress(platform_manifest([
        (b"Windows", len(platform), platform_prefix),
        (b"iOS", 1, b"I" * 8),
    ]))
    routes = {
        cdn.root_manifest_url(version): (200, root),
        cdn.object_url("Manifest", cdn.asset_name(platform_prefix, len(platform), b"Windows")): (200, platform),
        cdn.object_url("Manifest", cdn.asset_name(category_prefix, len(category), b"master")): (200, category),
        cdn.object_url("Generic", cdn.asset_name(digest[:8], len(packed_mdb), b"master.mdb.lz4")): (200, packed_mdb),
    }
    return routes, len(packed_mdb), digest


class ManifestFormatTests(CalculatorTestCase):
    def test_varint_reads_the_live_master_size(self):
        # The bytes 10008795's master entry carries: 5,738,552 bytes.
        self.assertEqual(cdn.read_varint(bytes.fromhex("82dea038ff"), 0), (5738552, 4))
        self.assertEqual(cdn.read_varint(b"\x05", 0), (5, 1))

    def test_platform_manifest_round_trips(self):
        entries = [(b"Windows", 684, b"\x01" * 8), (b"Android", 300, b"\x02" * 8)]
        self.assertEqual(cdn.parse_platform_manifest(platform_manifest(entries)), entries)

    def test_category_manifest_reads_both_entry_shapes(self):
        entries = [([b"master.mdb.lz4"], 5738552, bytes(16)),
                   ([b"a", b"b"], 7, b"\x09" * 16)]
        self.assertEqual(cdn.parse_category_manifest(category_manifest(entries)), entries)

    def test_asset_name_is_base32_sha1_of_prefix_size_name(self):
        # A real one: 10008795's root names its Windows manifest with this
        # prefix and size, and the live CDN served that name (2026-11-28).
        self.assertEqual(cdn.asset_name(bytes.fromhex("05648c6c18a426ad"), 684, b"Windows"),
                         "P4ASKRM4DXYT2DTTJXFRC6MXJW72J2QX")
        self.assertEqual(cdn.object_url("Generic", "P2IKSR"),
                         f"{cdn.CDN_BASE}/resources/Generic/P2/P2IKSR")


class CdnWalkTests(CalculatorTestCase):
    def test_master_entry_reads_size_and_hash_without_the_download(self):
        routes, size, digest = fake_cdn()
        session = FakeSession(routes)
        entry = cdn.master_entry(10008795, session)
        self.assertEqual(entry, {"version": 10008795, "size": size, "hash": digest.hex()})
        self.assertFalse(any("/Generic/" in url for url in session.asked))

    def test_a_missing_version_is_none(self):
        self.assertIsNone(cdn.master_entry(10008805, FakeSession({})))
        self.assertFalse(cdn.version_exists(10008805, FakeSession({})))

    def test_a_manifest_the_tree_names_but_the_cdn_lacks_is_an_error(self):
        routes, _, _ = fake_cdn()
        del routes[next(url for url in routes if "/Manifest/" in url)]
        with self.assertRaises(cdn.CdnError):
            cdn.master_entry(10008795, FakeSession(routes))

    def test_download_writes_an_openable_sqlite_file(self):
        routes, _, digest = fake_cdn()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sub" / "master.mdb"
            entry = cdn.download_mdb(10008795, out, FakeSession(routes))
            self.assertEqual(entry["hash"], digest.hex())
            conn = sqlite3.connect(out)
            self.assertEqual(conn.execute("select count(*) from skill_data").fetchone(), (0,))
            conn.close()

    def test_download_refuses_bytes_that_are_not_sqlite(self):
        routes, _, _ = fake_cdn(mdb=b"not a database at all")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(cdn.CdnError):
            cdn.download_mdb(10008795, Path(tmp) / "master.mdb", FakeSession(routes))

    def test_download_refuses_a_size_the_manifest_did_not_promise(self):
        routes, _, _ = fake_cdn()
        url = next(url for url in routes if "/Generic/" in url)
        routes[url] = (200, routes[url][1] + b"extra")
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(cdn.CdnError):
            cdn.download_mdb(10008795, Path(tmp) / "master.mdb", FakeSession(routes))

    def test_scan_tries_every_number(self):
        routes = {cdn.root_manifest_url(v): (200, b"x") for v in (10008795, 10008797)}
        session = FakeSession(routes)
        self.assertEqual(cdn.scan(10008794, 5, session), [10008795, 10008797])
        self.assertEqual(len(session.asked), 5)

    def test_any_other_error_is_raised_not_read_as_missing(self):
        session = FakeSession({cdn.root_manifest_url(1): (500, b"")})
        with self.assertRaises(RuntimeError):
            cdn.master_entry(1, session)


check = load_script("masterdata_check")


class FakeCdn:  # pylint: disable=too-few-public-methods
    """Stands in for cdn_masterdata: `entries` maps version -> hash; absent is missing."""

    CdnError = cdn.CdnError

    def __init__(self, entries):
        self.entries = entries
        self.read = []

    def master_entry(self, version, session=None):  # pylint: disable=unused-argument
        self.read.append(version)
        digest = self.entries.get(version)
        return None if digest is None else {"version": version, "size": 5, "hash": digest}

    def scan(self, start, count, session=None):  # pylint: disable=unused-argument
        return [v for v in range(start, start + count) if v in self.entries]


def known(version, status, digest="aa" * 16):
    return {"region": "global", "version": version, "status": status, "master_hash": digest}


class DailyCheckTests(CalculatorTestCase):
    META = {"cdn_version": 10008795, "cdn_master_hash": "aa" * 16, "counts": {"skills": 754}}

    def test_a_fresh_table_starts_from_the_committed_snapshot(self):
        fake = FakeCdn({10008795: "aa" * 16, 10008800: "bb" * 16})
        findings = check.collect([], self.META, fake)
        self.assertEqual([(f["version"], f["master_hash"]) for f in findings],
                         [(10008795, "aa" * 16), (10008800, "bb" * 16)])
        self.assertEqual(fake.read[0], 10008795)

    def test_the_live_version_is_read_first_and_old_ones_not_at_all(self):
        fake = FakeCdn({10008790: "11" * 16, 10008795: "aa" * 16, 10008800: "bb" * 16})
        versions = [known(10008790, "old"), known(10008795, "live"), known(10008800, "new")]
        findings = check.collect(versions, self.META, fake)
        self.assertEqual(fake.read[0], 10008795)
        self.assertNotIn(10008790, fake.read)
        self.assertEqual([f["version"] for f in findings], [10008795, 10008800])

    def test_a_refused_anchor_fails_the_run_instead_of_reading_as_nothing_new(self):
        with self.assertRaises(check.CheckError):
            check.collect([known(10008795, "live")], self.META, FakeCdn({}))

    def test_a_known_version_that_stops_answering_fails_the_run(self):
        versions = [known(10008795, "live"), known(10008800, "new")]
        with self.assertRaises(check.CheckError):
            check.collect(versions, self.META, FakeCdn({10008795: "aa" * 16}))

    def test_no_anchor_at_all_says_what_to_do(self):
        with self.assertRaisesRegex(check.CheckError, "mark one live"):
            check.collect([], {"counts": {}}, FakeCdn({}))

    def test_the_scan_starts_above_the_newest_known_version(self):
        fake = FakeCdn({10008795: "aa" * 16, 10008800: "bb" * 16, 10008841: "cc" * 16})
        versions = [known(10008795, "live"), known(10008800, "ignored")]
        findings = check.collect(versions, self.META, fake)
        # 10008800 is ignored, so not re-read; the scan covers 10008801..10008840.
        self.assertEqual([f["version"] for f in findings], [10008795])

    def test_only_a_live_version_that_differs_needs_a_snapshot(self):
        self.assertIsNone(check.snapshot_needed([known(10008800, "new", "bb" * 16)], self.META))
        self.assertIsNone(check.snapshot_needed([known(10008795, "live")], self.META))
        revised = known(10008795, "live", "cc" * 16)
        self.assertEqual(check.snapshot_needed([revised], self.META), revised)
        moved = known(10008800, "live", "bb" * 16)
        self.assertEqual(check.snapshot_needed([moved], self.META), moved)
        self.assertEqual(check.branch_name(moved), "data/global-masterdata-10008800-bbbbbbbb")

    def test_an_open_pr_branch_means_nothing_to_do(self):
        with mock.patch.object(check, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertIsNone(check.open_snapshot_pr(known(10008800, "live", "bb" * 16), self.META))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[:3], ("git", "ls-remote", "--exit-code"))

    def test_the_pr_body_lists_the_counts_that_moved(self):
        table = check.count_changes(self.META, {"counts": {"skills": 1009, "cards": 118}})
        self.assertIn("| skills | 754 | 1009 |", table)
        self.assertIn("| cards | 0 | 118 |", table)
        self.assertIn("No row counts changed", check.count_changes(self.META, self.META))
