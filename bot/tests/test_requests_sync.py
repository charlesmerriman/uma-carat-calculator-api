"""The PR-merge sync: which links count, and what a post's tags become."""

import unittest

from actions.requests_sync import linked_posts, new_tags, sync

GUILD = "1410431883912941619"
BOARD = "1562200270061043872"
TAGS = {"1": "Bug", "2": "Normal", "3": "Planner", "10": "Planned", "11": "In progress", "12": "Done", "13": "Won't do"}


class FakeDiscord:

    def __init__(self, posts):
        self.posts = posts
        self.writes = []

    def call(self, method, path, body=None):
        if method == "GET" and path == f"/channels/{BOARD}":
            return {"available_tags": [{"id": tag_id, "name": name} for tag_id, name in TAGS.items()]}
        if method == "GET":
            return self.posts[path.rsplit("/", 1)[1]]
        self.writes.append((method, path, body))
        return {}


class LinkTests(unittest.TestCase):

    def test_our_server_only_each_once(self):
        body = (
            f"Fixes https://discord.com/channels/{GUILD}/111 and "
            f"https://discord.com/channels/{GUILD}/111/999 plus "
            f"https://ptb.discord.com/channels/{GUILD}/222, "
            "not https://discord.com/channels/42/333"
        )
        self.assertEqual(linked_posts(body, GUILD), ["111", "222"])
        self.assertEqual(linked_posts(None, GUILD), [])


class TagTests(unittest.TestCase):

    def test_status_swapped_others_kept(self):
        self.assertEqual(new_tags(["1", "2", "10"], TAGS), ["1", "2", "11"])
        self.assertEqual(new_tags(["1", "3"], TAGS), ["1", "3", "11"])

    def test_finished_or_already_in_progress_is_left(self):
        for current in (["1", "12"], ["1", "13"], ["1", "11"]):
            with self.subTest(current=current):
                self.assertIsNone(new_tags(current, TAGS))


class SyncTests(unittest.TestCase):

    def event(self, body, merged=True):
        return {"pull_request": {"merged": merged, "number": 140, "body": body}}

    def test_a_merge_replies_and_moves_the_post(self):
        discord = FakeDiscord({"111": {"parent_id": BOARD, "name": "Logo", "applied_tags": ["1", "10"]}})
        lines = sync(discord, self.event(f"https://discord.com/channels/{GUILD}/111"),
                     guild_id=GUILD, board_id=BOARD, repo="uma-carat-calculator-web")
        self.assertIn("moved to In progress", lines[0])
        (reply, retag) = discord.writes
        self.assertEqual(reply[1], "/channels/111/messages")
        self.assertIn("uma-carat-calculator-web#140", reply[2]["content"])
        self.assertEqual(reply[2]["allowed_mentions"], {"parse": []})
        self.assertEqual(retag, ("PATCH", "/channels/111", {"applied_tags": ["1", "11"]}))

    def test_dry_run_and_other_channels_write_nothing(self):
        discord = FakeDiscord({
            "111": {"parent_id": BOARD, "name": "Logo", "applied_tags": ["1"]},
            "222": {"parent_id": "555", "name": "Elsewhere", "applied_tags": []},
        })
        body = f"https://discord.com/channels/{GUILD}/111 https://discord.com/channels/{GUILD}/222"
        lines = sync(discord, self.event(body), guild_id=GUILD, board_id=BOARD, repo="x", dry_run=True)
        self.assertEqual(discord.writes, [])
        self.assertIn("not a team-board post", lines[1])

    def test_unmerged_does_nothing(self):
        discord = FakeDiscord({})
        self.assertEqual(sync(discord, self.event("x", merged=False), guild_id=GUILD, board_id=BOARD, repo="x"),
                         ["Not merged: nothing to do."])
