"""
build_course_bundle.py

Builds the course map's import file from Daptrius's Drive "Data" zips: one
JSON bundle per region, every course's record plus one screen-ready path,
which an editor uploads on the Race courses page ("Import courses").

Why a bundle and not the zips: the raw files are ~50 MB of path JSON plus the
records, and parsing them in one admin request would run on the 0.5 GB server
that a console script has already taken down once. The bundle is ~3 MB and
holds only what the site keeps (owner, 2026-11-02).

What it reads, from the zips directly (never the unzipped folders, which miss
about a third of the files):
    Data/jp/courses/course_data.json            the scraper's per-course records
    Data/jp/track_paths/<name>__<hash>/*_MonoBehaviour_*.json   the game's paths
and the racecourses' English names from the Global client's own database
(text_data category 35), the same file extract_master_snapshot.py reads.

The cleaning rules live in calculatorapi/course_shapes.py, which the tests
cover. This script only finds the files, runs them, checks every drawn course
turns the way its record says, and reports.

The bundle is game data for an upload, not a repo file: --out must be outside
the repo (a scratch folder).

Run from backend/:
    python scripts/build_course_bundle.py ~/Downloads/Data-*.zip --out /tmp/courses/course_bundle_jp.json
    python scripts/build_course_bundle.py ~/Downloads/Data-*.zip --report-only
"""

import argparse
import datetime
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# The path above has to come first, hence the import down here.
# pylint: disable=wrong-import-position
from calculatorapi.course_shapes import (  # noqa: E402
    course_fields, heading_change, pick_path, to_screen,
)

GLOBAL_MDB = Path("/mnt/c/Users/Zac82/AppData/LocalLow/Cygames/Umamusume/master/master.mdb")
TEXT_RACECOURSE = 35  # text_data category: "Chukyo", by racecourse id
BUNDLE_FORMAT = 1

PATH_MEMBER = re.compile(
    r"^Data/(?P<region>jp|global)/track_paths/(?P<name>an_pos_race[^/]+?)__[A-Z0-9]+/\d+_MonoBehaviour_[^/]+\.json$"
)


def racecourse_names(mdb):
    conn = sqlite3.connect(f"file:{mdb}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT `index`, text FROM text_data WHERE category = ?", (TEXT_RACECOURSE,)
        ).fetchall()
    finally:
        conn.close()
    return {int(index): text for index, text in rows}


def read_zips(zips, region):
    """The region's course records and its path files, from every zip."""
    records = None
    paths = {}
    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.namelist():
                if member == f"Data/{region}/courses/course_data.json":
                    records = json.loads(archive.read(member))
                    continue
                found = PATH_MEMBER.match(member)
                if found and found["region"] == region:
                    paths[found["name"]] = json.loads(archive.read(member))
    if records is None:
        raise SystemExit(f"no Data/{region}/courses/course_data.json in those zips")
    return records, paths


def screen_shape(path_file, fields):
    key = path_file["key"]
    final = fields["straights"][-1]["start"] if fields["straights"] else None
    return to_screen(key["valueX"], key["valueZ"], fields["distance"], final)


def add_shape(fields, master, paths):
    """Pick the course's path, put it in screen axes, and check it isn't mirrored.

    Returns the other path files the codes left (for the report).
    """
    chosen, others = pick_path(master, paths)
    fields.update({"shape": None, "path_source": chosen or "", "path_candidates": {}})
    if chosen is None:
        fields["flags"].append("no path file: no real shape (the schematic draws it)")
        return others
    try:
        fields["shape"] = screen_shape(paths[chosen], fields)
        fields["path_candidates"] = {name: screen_shape(paths[name], fields) for name in others}
    except ValueError as exc:
        fields["flags"].append(f"path {chosen}: {exc}")
        fields["shape"] = None
        return others
    if fields["direction"] != "straight":
        turn = heading_change(fields["shape"])
        if (turn > 0) != (fields["direction"] == "right"):
            # A mirrored course would be drawn the wrong way round: never ship
            # one. Not seen on any JP course; this is the tripwire.
            fields["flags"].append(
                f"path turns {turn:+.0f} degrees on screen, wrong for {fields['direction']}: hidden")
            fields["show_map"] = False
    return others


def build(records, paths, names, region):
    courses, report = [], []
    for game_id, record in sorted(records.items(), key=lambda kv: int(kv[0])):
        fields = course_fields(record, names)
        if fields is None:
            report.append(f"{game_id}: no master row, skipped")
            continue
        fields["region"] = region
        others = add_shape(fields, record["masterdata"], paths)
        courses.append(fields)
        chosen = fields["path_source"]
        notes = list(fields["flags"])
        if others:
            notes.append(f"path {chosen} chosen; also {', '.join(others)}")
        if not fields["show_map"]:
            notes.append("map hidden")
        if notes:
            name = f"{game_id} {fields['racecourse']} {fields['surface']} {fields['distance']} m"
            report.append(f"{name}: " + "; ".join(notes))
    return courses, report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("zips", nargs="+", type=Path, help="Daptrius's Data-*.zip files")
    parser.add_argument("--region", choices=("jp", "global"), default="jp")
    parser.add_argument("--names-mdb", type=Path, default=GLOBAL_MDB,
                        help="the Global client's master.mdb, for the racecourses' English names")
    parser.add_argument("--out", type=Path, help="where to write the bundle (outside the repo)")
    parser.add_argument("--report-only", action="store_true", help="print the report, write nothing")
    args = parser.parse_args()

    if not args.report_only:
        if args.out is None:
            raise SystemExit("--out is required unless --report-only")
        if args.out.resolve().is_relative_to(BACKEND.parent):
            raise SystemExit("--out must be outside the workspace: the bundle is an upload, not a repo file")

    records, paths = read_zips(args.zips, args.region)
    courses, report = build(records, paths, racecourse_names(args.names_mdb), args.region)
    for line in report:
        print(line)
    shaped = sum(1 for c in courses if c["shape"])
    hidden = sum(1 for c in courses if not c["show_map"])
    print(f"\n{len(courses)} courses ({shaped} with a real shape, {hidden} hidden), "
          f"{len(records) - len(courses)} skipped, {len(paths)} path files read")

    if args.report_only:
        return
    bundle = {
        "format": BUNDLE_FORMAT,
        "region": args.region,
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": [zip_path.name for zip_path in args.zips],
        "courses": courses,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
