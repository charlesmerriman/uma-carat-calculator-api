"""
fetch_umatl.py

Saves the slice of UmaTL's English translation this project uses: the names
and descriptions of skills, and the text of skill evolution conditions, for
the ids in the JP snapshot. Written to scripts/data/umatl/ and COMMITTED, like
the game snapshots, so the import never reaches the network and a translation
change shows up as a reviewable diff.

UmaTL (https://github.com/UmaTL/hachimi-tl-en) translates the JP client's text.
Its `localized_data/text_data_dict.json` mirrors the game's `text_data` table
as {category: {index: text}}, the same key the extractor reads:

  47   skill names                  by skill id
  48   skill descriptions           by skill id
  290  evolution condition text     by skill_upgrade_condition id

WHERE IT RANKS. Global's own English always wins; a translation is shown only
while a skill has no global text (skills-plan-3.md, "Sources"). The slice keeps
every JP id anyway, global ones included: an old translated name stays useful
as an alias after global names the skill, so a guide that wrote it still links.

TERMS. UmaTL is not openly licensed. Its data may be used with attribution, in
community projects whose scope differs from UmaTL's. ATTRIBUTION.md travels
with the slice, and the site credits UmaTL wherever its text shows.

Each run pins one commit (a branch or tag is resolved to its sha) and records
it in meta.json, so a refresh is always a deliberate, dated step.

Run from backend/, after the JP snapshot exists:
    python scripts/fetch_umatl.py               # latest main
    python scripts/fetch_umatl.py --ref <sha>   # a specific commit
"""

import argparse
import datetime
import json
from pathlib import Path

import requests

REPO = "UmaTL/hachimi-tl-en"
SOURCE_PATH = "localized_data/text_data_dict.json"
DATA = Path(__file__).parent / "data"
DEFAULT_SNAPSHOT = DATA / "jp_snapshot"
DEFAULT_OUT = DATA / "umatl"

# text_data categories, as in extract_master_snapshot.py.
CATEGORIES = {
    "skill_names": "47",
    "skill_descriptions": "48",
    "evolution_conditions": "290",
}

ATTRIBUTION = """\
# UmaTL translations

The files in this folder are a slice of **UmaTL**'s English translation of the
Umamusume: Pretty Derby JP client: skill names, skill descriptions and skill
evolution conditions.

- Source: https://github.com/{repo} (`{path}`)
- Commit: `{commit}` ({committed_at})
- Credits: https://github.com/UmaTL/hachimi-tl-en/wiki/Translation-Progress

UmaTL is a fan community project. It is **not** released under an open licence:
its data may only be used, with attribution, in community projects relevant to
the game whose scope differs from UmaTL's. Underlying translations belong to
their contributors and sources, and original game data is the copyright of
Cygames. See UmaTL's README ("Data usage") for the full terms.

This project shows this text only where the global game has no official English
yet, and credits UmaTL wherever it appears. Regenerate with
`python scripts/fetch_umatl.py`; don't edit by hand.
"""


def wanted_ids(snapshot_dir):
    """{output key: set of str ids} the JP snapshot needs text for."""
    skills = json.loads((snapshot_dir / "skills.json").read_text(encoding="utf-8"))
    evolutions = json.loads((snapshot_dir / "evolutions.json").read_text(encoding="utf-8"))
    skill_ids = {str(skill["id"]) for skill in skills}
    condition_ids = {
        str(condition["id"])
        for kind in evolutions.values()
        for evolution in kind
        for condition in evolution["conditions"]
    }
    return {
        "skill_names": skill_ids,
        "skill_descriptions": skill_ids,
        "evolution_conditions": condition_ids,
    }


def slice_text(full, wanted):
    """
    Keep only the wanted ids of the three categories, sorted by id.

    Blank entries are dropped: an empty translation is no translation, and
    keeping it would make the import treat the skill as translated.
    """
    out = {}
    for key, category in CATEGORIES.items():
        texts = full.get(category, {})
        out[key] = {
            index: texts[index]
            for index in sorted(wanted[key] & texts.keys(), key=int)
            if texts[index].strip()
        }
    return out


def resolve_commit(ref):
    """The sha and commit date a branch, tag or sha points at."""
    response = requests.get(f"https://api.github.com/repos/{REPO}/commits/{ref}", timeout=30)
    response.raise_for_status()
    body = response.json()
    return body["sha"], body["commit"]["committer"]["date"]


def download(sha):
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{SOURCE_PATH}"
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--ref", default="main", help="branch, tag or commit (default: main)")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT, help="the JP snapshot")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--dry-run", action="store_true", help="print counts, write nothing")
    args = parser.parse_args()

    if not (args.snapshot / "skills.json").is_file():
        raise SystemExit(f"no JP snapshot at {args.snapshot}; run extract_master_snapshot.py --region jp first")

    wanted = wanted_ids(args.snapshot)
    sha, committed_at = resolve_commit(args.ref)
    text = slice_text(download(sha), wanted)
    counts = {key: len(entries) for key, entries in text.items()}
    for key, count in counts.items():
        print(f"{key:<22} {count:>6} of {len(wanted[key])}")

    if args.dry_run:
        print(f"\nDry run at {sha}: nothing written.")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    meta = {
        "repo": REPO,
        "path": SOURCE_PATH,
        "commit": sha,
        "committed_at": committed_at,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "counts": counts,
    }
    for name, payload in (("text.json", text), ("meta.json", meta)):
        (args.out / name).write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
        )
    (args.out / "ATTRIBUTION.md").write_text(
        ATTRIBUTION.format(repo=REPO, path=SOURCE_PATH, commit=sha, committed_at=committed_at),
        encoding="utf-8",
    )
    print(f"\nWrote {args.out} at {sha}")


if __name__ == "__main__":
    main()
