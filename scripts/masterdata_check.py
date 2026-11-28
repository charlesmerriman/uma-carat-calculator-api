"""
masterdata_check.py

The daily masterdata check, run by .github/workflows/masterdata-check.yml on a
GitHub runner (never on the web server: it downloads and extracts a 17.7 MB
database). Plan: notes/masterdata-cdn-plan.md (notes repo).

ONE RUN, IN ORDER
  1. GET /game-data/versions: what the site already knows, and which version a
     person has marked live.
  2. Read the CDN (scripts/cdn_masterdata.py):
     - FIRST one version known to exist (the live one, else the committed
       snapshot's). On this CDN a missing version and a refused request both
       answer 403, so a miss means nothing until a hit proves the CDN is
       answering us. If that check fails, the run fails red.
     - every known version still in play (new or live), for a revision. A
       known version that stops answering also fails the run: it was withdrawn,
       or the CDN is refusing us, and either way a person should look.
     - the SCAN_AHEAD numbers above the newest known one. Every number, not
       steps of 5: nothing promises the pattern.
  3. POST what it saw. The site records it and tells #calc-ops about anything
     new or revised, once per version and hash.
  4. If a version is LIVE and differs from the committed snapshot
     (scripts/data/master_snapshot/meta.json's cdn_version / cdn_master_hash),
     extract it and open a snapshot PR into develop, on a branch named after
     the version and hash. If that branch already exists, the PR is already
     open, so nothing is done. A staged version never becomes a PR.

The PR is the review: game data still reaches production only as a committed
snapshot that a deploy imports (CLAUDE.md, "nothing loads content INTO prod").

ENVIRONMENT
  GAME_DATA_URL           the API route, e.g. https://umacaratcalculator.com/api/game-data/versions
  GAME_DATA_SYNC_SECRET   must equal the API's, byte for byte
  GH_TOKEN                for `gh pr create` (the workflow's own token)

Run by hand from backend/ with the same variables; `--report-only` skips step 4.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

# Not a package: make the sibling module importable however this file is loaded.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdn_masterdata  # noqa: E402  pylint: disable=wrong-import-position,import-error

BACKEND = Path(__file__).resolve().parent.parent
META = BACKEND / "scripts" / "data" / "master_snapshot" / "meta.json"
# What a snapshot PR may change: the snapshot, and the colours the extractor
# refreshes alongside characters.json.
SNAPSHOT_PATHS = ["scripts/data/master_snapshot", "calculatorapi/data/character_colours.json"]
SCAN_AHEAD = 40
REGION = "global"
SYNC_HEADER = "X-Game-Data-Sync-Key"
TIMEOUT = 30
IN_PLAY = ("new", "live")


class CheckError(Exception):
    """Something a person should look at. The run exits 1 (a red X)."""


# ---------------------------------------------------------------- the API

def api(method, url, secret, body=None):
    """One call to /game-data/versions. Redirects are NOT followed: requests
    would carry the key to wherever Location points, and a moved site should
    say so (the same reasoning as patreon-sync.yml)."""
    response = requests.request(method, url, json=body, timeout=TIMEOUT, allow_redirects=False,
                                headers={SYNC_HEADER: secret})
    if 300 <= response.status_code < 400:
        raise CheckError(f"{url} redirects to {response.headers.get('Location')}: update GAME_DATA_URL")
    if response.status_code != 200:
        raise CheckError(f"{method} {url} answered HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


# ---------------------------------------------------------------- the decisions

def load_meta(path=META):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect(versions, meta, cdn=cdn_masterdata, session=None):
    """Step 2: read the CDN and return the findings to post. Raises CheckError
    when the CDN's answers can't be trusted."""
    known = [v for v in versions if v["region"] == REGION]
    live = next((v["version"] for v in known if v["status"] == "live"), None)
    anchor = live or meta.get("cdn_version")
    if anchor is None:
        raise CheckError("no version to start from: mark one live in the admin, or commit a "
                         "snapshot made with extract_master_snapshot.py --cdn-version")

    sentinel = cdn.master_entry(anchor, session)
    if sentinel is None:
        raise CheckError(f"{anchor} is known to exist but the CDN says it doesn't: "
                         "it was withdrawn, or the CDN is refusing this machine")

    findings = {anchor: sentinel}
    for row in known:
        if row["status"] not in IN_PLAY or row["version"] in findings:
            continue
        entry = cdn.master_entry(row["version"], session)
        if entry is None:
            raise CheckError(f"{row['version']} ({row['status']}) no longer answers on the CDN")
        findings[row["version"]] = entry

    newest = max([v["version"] for v in known] + [anchor])
    for number in cdn.scan(newest + 1, SCAN_AHEAD, session):
        entry = cdn.master_entry(number, session)
        if entry is not None:
            findings[number] = entry

    return [{"region": REGION, "version": e["version"], "master_size": e["size"],
             "master_hash": e["hash"]} for _, e in sorted(findings.items())]


def snapshot_needed(versions, meta):
    """Step 4's question: the live row, when it differs from the committed
    snapshot; else None."""
    live = next((v for v in versions if v["region"] == REGION and v["status"] == "live"), None)
    if live is None:
        return None
    committed = (meta.get("cdn_version"), meta.get("cdn_master_hash"))
    return None if committed == (live["version"], live["master_hash"]) else live


def branch_name(live):
    return f"data/global-masterdata-{live['version']}-{live['master_hash'][:8]}"


def count_changes(old_meta, new_meta):
    """The PR body's table: every count that moved, old to new."""
    old, new = old_meta.get("counts", {}), new_meta.get("counts", {})
    rows = [f"| {name} | {old.get(name, 0)} | {new[name]} |"
            for name in sorted(new) if old.get(name, 0) != new[name]]
    if not rows:
        return "No row counts changed (the revision changed values only)."
    return "| File | Before | After |\n|---|---|---|\n" + "\n".join(rows)


# ---------------------------------------------------------------- the PR

def run(*command, check=True):
    return subprocess.run(command, cwd=BACKEND, check=check, capture_output=True, text=True)


def open_snapshot_pr(live, old_meta):
    """Extract the live version and open the PR. Returns the PR's URL, or None
    when there was nothing to do."""
    branch = branch_name(live)
    if run("git", "ls-remote", "--exit-code", "--heads", "origin", branch, check=False).returncode == 0:
        print(f"{branch} already exists: its PR is open, nothing to do")
        return None

    run(sys.executable, "scripts/extract_master_snapshot.py", "--cdn-version", str(live["version"]))
    new_meta = load_meta()
    if new_meta.get("cdn_master_hash") != live["master_hash"]:
        # Revised again between the check and the download. Tomorrow's run
        # sees the new hash and starts over; a PR from bytes nobody recorded
        # would be the wrong one.
        raise CheckError(f"{live['version']} changed on the CDN during this run; it will be retried")

    run("git", "switch", "-c", branch)
    run("git", "add", "--", *SNAPSHOT_PATHS)
    title = f"chore(game-data): Global masterdata {live['version']}"
    before = old_meta.get("cdn_version") or "the Steam client's mdb"
    run("git", "-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", title)
    run("git", "push", "-u", "origin", branch)
    body = (
        f"The daily masterdata check: version {live['version']} is marked live and differs "
        f"from the committed snapshot ({before}). Hash `{live['master_hash']}`.\n\n"
        f"{count_changes(old_meta, new_meta)}\n\n"
        "Before merging: `python manage.py import_game_data --dry-run` on this branch. "
        "Ships with the next release."
    )
    return run("gh", "pr", "create", "--head", branch, "--base", "develop",
               "--title", title, "--body", body).stdout.strip()


# ---------------------------------------------------------------- main

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--report-only", action="store_true", help="record and report; never open a PR")
    args = parser.parse_args(argv)

    url = os.environ.get("GAME_DATA_URL", "")
    secret = os.environ.get("GAME_DATA_SYNC_SECRET", "")
    if not url or not secret:
        print("GAME_DATA_URL and GAME_DATA_SYNC_SECRET must both be set.")
        return 1
    try:
        meta = load_meta()
        versions = api("GET", url, secret)["versions"]
        findings = collect(versions, meta)
        answer = api("POST", url, secret, {"findings": findings})
        for change in answer["changes"]:
            print(f"{change['region']} {change['version']}: {change['change']}")
        live = snapshot_needed(answer["versions"], meta)
        if live is None:
            print("The committed snapshot matches the live version (or none is live).")
        elif args.report_only:
            print(f"{live['version']} is live and differs from the snapshot; --report-only, no PR")
        else:
            pr = open_snapshot_pr(live, meta)
            if pr:
                print(f"Opened {pr}")
    except (CheckError, cdn_masterdata.CdnError, requests.RequestException) as exc:
        print(f"::error::{exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"::error::{' '.join(exc.cmd)} failed:\n{exc.stdout}\n{exc.stderr}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
