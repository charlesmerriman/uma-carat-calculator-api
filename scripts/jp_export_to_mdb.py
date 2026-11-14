"""
jp_export_to_mdb.py

Rebuilds a game database (master.mdb, plain SQLite) from a CSV export of one, so
extract_master_snapshot.py can read it exactly as it reads the global client's.

Why this exists: nobody on the team runs the JP client, so the JP data reaches
us as a teammate's export, not as the .mdb itself. The export (first received
2026-10-23, JP game data of 2026-10-02) is a folder holding:

  masterdata_schema_and_previews.json   every table's columns with their SQLite
                                        types (from PRAGMA table_info), plus a
                                        few preview rows
  masterdata_tables/<table>.csv         every row of every table, header first
  masterdata_catalog.csv                row counts, checked against the CSVs
  source_metadata.json                  which client, the .mdb's size and date

THE TYPES MATTER. A CSV holds text only. Each table is recreated with the
columns' declared types, so SQLite's type affinity turns "100101" back into the
integer 100101 on insert; without it every id comparison in the extractor
would compare text against numbers and quietly match nothing.

The export is someone else's file, so it is treated as data: table and column
names must look like the game's (lower-case words and digits) before they go
into any SQL, and nothing in the folder is ever executed.

A LATER EXPORT WITHOUT THE TYPES FILE. The 2026-10-26 export (JP game data of
that day) came without masterdata_schema_and_previews.json. It was rebuilt
with the 2026-10-23 export's file copied in beside it, plus the columns added
since: skill_data's third slot typed like its slot-2 twins, and four columns
in tables the extractor never reads as INTEGER. A column the file doesn't
know stops the rebuild ("has columns the schema doesn't"), so nothing is
guessed silently.

SKILL ICONS: skill_data_processed.csv (Daptrius, 2026-11-14). The JP
skill_data leaves icon_id at 0 on some released skills (176 in the 2026-10-26
export, 197 in the snapshot of 2026-11-02), and the game draws them some
other way. Daptrius's skill_data_processed.csv is the same table with those
icons filled in; he said it will come with every export from now on. When an
export holds one (in masterdata_tables/ or at the top of the folder), it is
loaded AS skill_data, and the catalogue's skill_data count is checked against
it like any table. On 2026-11-14 it differed from that day's skill_data only
in icon_id.

Run from backend/ (the output path is required: the rebuilt file is ~35 MB and
belongs in a scratch folder, never in the repo or next to the export):
    python scripts/jp_export_to_mdb.py /path/to/export /tmp/jp/master.mdb
Then:
    python scripts/extract_master_snapshot.py --region jp --mdb /tmp/jp/master.mdb
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

# The game's table and column names, and the type names PRAGMA table_info
# reports. Anything else in the schema file is refused rather than quoted.
NAME = re.compile(r"^[a-z0-9_]+$")
TYPE = re.compile(r"^[A-Za-z ]*$")

# Some cells hold whole stories; the default 128 KiB field limit is too small.
csv.field_size_limit(sys.maxsize)


def checked(value, pattern, what):
    if not isinstance(value, str) or not pattern.match(value):
        raise SystemExit(f"refusing unexpected {what} in the schema file: {value!r}")
    return value


def create_table(conn, table, columns):
    """CREATE TABLE with the declared types and the original primary key."""
    defs = [
        f'"{checked(c["name"], NAME, "column name")}" {checked(c["type"], TYPE, "column type")}'
        for c in columns
    ]
    # pk is 0 for an ordinary column and 1, 2, ... for each part of the key.
    key = [c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"]]
    if key:
        defs.append("PRIMARY KEY (" + ", ".join(f'"{name}"' for name in key) + ")")
    conn.execute(f'CREATE TABLE "{table}" ({", ".join(defs)})')


def load_rows(conn, table, columns, csv_path):
    """Insert every CSV row. Returns the count, for the catalogue check."""
    known = {c["name"] for c in columns}
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        unknown = set(header) - known
        if unknown:
            raise SystemExit(f"{csv_path.name} has columns the schema doesn't: {sorted(unknown)}")
        names = ", ".join(f'"{name}"' for name in header)
        marks = ", ".join("?" for _ in header)
        count = 0
        for row in reader:
            conn.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', row)
            count += 1
    return count


# A table whose CSV a teammate's processed copy replaces, by the copy's file name.
PROCESSED = {"skill_data": "skill_data_processed.csv"}


def table_csv(export_dir, table):
    """The CSV to load for `table`: a processed copy when the export has one
    (module docstring, SKILL ICONS), else masterdata_tables/<table>.csv."""
    name = PROCESSED.get(table)
    if name:
        for candidate in (export_dir / "masterdata_tables" / name, export_dir / name):
            if candidate.is_file():
                print(f"{table}: loading {candidate.name} in place of {table}.csv")
                return candidate
    return export_dir / "masterdata_tables" / f"{table}.csv"


def rebuild(export_dir, out_path):
    schema = json.loads(
        (export_dir / "masterdata_schema_and_previews.json").read_text(encoding="utf-8")
    )["tables"]
    with (export_dir / "masterdata_catalog.csv").open(encoding="utf-8-sig", newline="") as f:
        expected = {row["table_name"]: int(row["row_count"]) for row in csv.DictReader(f)}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    conn = sqlite3.connect(out_path)
    try:
        for table, info in schema.items():
            checked(table, NAME, "table name")
            create_table(conn, table, info["columns"])
            count = load_rows(conn, table, info["columns"], table_csv(export_dir, table))
            # A short CSV means a truncated export; better to stop than to
            # extract a snapshot that is silently missing rows.
            if table in expected and count != expected[table]:
                raise SystemExit(f"{table}: {count} rows in the CSV, the catalogue says {expected[table]}")
        conn.commit()
    finally:
        conn.close()
    return len(schema)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("export", type=Path, help="the export folder (holds masterdata_tables/)")
    parser.add_argument("out", type=Path, help="where to write the rebuilt master.mdb")
    args = parser.parse_args()

    if not (args.export / "masterdata_tables").is_dir():
        raise SystemExit(f"no masterdata_tables/ in {args.export}")
    tables = rebuild(args.export, args.out)
    print(f"Rebuilt {tables} tables into {args.out}")


if __name__ == "__main__":
    main()
