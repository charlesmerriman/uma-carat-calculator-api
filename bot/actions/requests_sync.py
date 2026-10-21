"""
A merged pull request moves the team-board posts it links to "In progress".

Run by the "Team board sync" workflow (.github/workflows/requests-sync.yml)
when a PR into develop is merged, in either repo. It reads the PR's
description for links to posts in #calc-team-board
(https://discord.com/channels/<server>/<post>), and for each one:

1. replies in the post: "Merged into develop in <repo>#<n>, ships with the
   next release.", with mentions off;
2. swaps the post's status tag for In progress, keeping its other tags.

A post already Done or Won't do is left alone (a late follow-up PR doesn't
reopen it), and a link to anything that isn't a team-board post in our server
is ignored. Moving a post to Done stays a person's call at release time
(/triage, mode 3).

Standard library only, so the workflow needs no install step. The bot token
comes from the repo secret DISCORD_BOT_TOKEN; the ids are plain routing values.
`--dry-run` prints what it would do and changes nothing.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
STATUS_TAGS = ("Planned", "In progress", "Done", "Won't do")
IN_PROGRESS = "In progress"
FINISHED = ("Done", "Won't do")
MAX_TAGS = 5
LINK = re.compile(r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)")


def linked_posts(text, guild_id):
    """The post ids `text` links to in our server, in order, each once. A
    link to a message inside a post (/channels/<server>/<post>/<message>)
    still names the post."""
    seen = []
    for guild, post in LINK.findall(text or ""):
        if guild == str(guild_id) and post not in seen:
            seen.append(post)
    return seen


def new_tags(current_ids, tags_by_id):
    """The tag ids a post should carry once In progress, or None to leave it:
    its status tags replaced by In progress, every other tag kept."""
    names = [tags_by_id[tag_id] for tag_id in current_ids if tag_id in tags_by_id]
    if any(name in FINISHED for name in names) or IN_PROGRESS in names:
        return None
    by_name = {name: tag_id for tag_id, name in tags_by_id.items()}
    if IN_PROGRESS not in by_name:
        return None
    kept = [tag_id for tag_id in current_ids if tags_by_id.get(tag_id) not in STATUS_TAGS]
    return [*kept[: MAX_TAGS - 1], by_name[IN_PROGRESS]]


class Discord:

    def __init__(self, token):
        self.token = token

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            f"{API}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bot {self.token}",
                "User-Agent": "DiscordBot (https://umacaratcalculator.com, 1.0)",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)


def sync(discord, event, *, guild_id, board_id, repo, dry_run=False):  # pylint: disable=too-many-arguments
    """Do the work for one pull_request event. Returns lines describing it."""
    pr = event.get("pull_request") or {}
    if not pr.get("merged"):
        return ["Not merged: nothing to do."]
    posts = linked_posts(pr.get("body"), guild_id)
    if not posts:
        return ["No team-board links in the description."]
    tags_by_id = {tag["id"]: tag["name"] for tag in discord.call("GET", f"/channels/{board_id}")["available_tags"]}
    text = f"Merged into develop in {repo}#{pr.get('number')}, ships with the next release."
    done = []
    for post_id in posts:
        try:
            post = discord.call("GET", f"/channels/{post_id}")
        except urllib.error.HTTPError as exc:
            done.append(f"{post_id}: not readable ({exc.code}), skipped")
            continue
        if str(post.get("parent_id")) != str(board_id):
            done.append(f"{post_id}: not a team-board post, skipped")
            continue
        tags = new_tags(post.get("applied_tags") or [], tags_by_id)
        if tags is None:
            done.append(f"{post_id} ({post.get('name')}): already in progress or finished, left alone")
            continue
        if not dry_run:
            discord.call("POST", f"/channels/{post_id}/messages", {"content": text, "allowed_mentions": {"parse": []}})
            discord.call("PATCH", f"/channels/{post_id}", {"applied_tags": tags})
        done.append(f"{post_id} ({post.get('name')}): replied and moved to In progress")
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print("DISCORD_BOT_TOKEN is not set; nothing to do.")
        return 0
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    lines = sync(
        Discord(token), event,
        guild_id=os.environ["DISCORD_GUILD_ID"],
        board_id=os.environ["DISCORD_TEAM_BOARD_ID"],
        repo=os.environ.get("GITHUB_REPOSITORY", "").split("/")[-1],
        dry_run=args.dry_run,
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
