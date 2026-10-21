"""The board: the queue follows the tags, Up next renders and is edited in
place, the Monday summary posts once, and the mirror echoes a team copy's
status onto its public post once per change."""

import datetime
import unittest
from types import SimpleNamespace

import discord

from bot import board as board_module
from bot import format as fmt
from bot.api import Answer
from bot.board import Board, queue_order, render_up_next, render_weekly, status_of
from tests.fakes import CONFIG, FEEDBACK, TEAM_BOARD, TEAM_ROLE, FakeApi, FakeForum, FakeInteraction, FakeThread, tag

OPS = 1562200271222866020
BOARD_CONFIG = SimpleNamespace(**{**CONFIG.__dict__, "ops_channel_id": OPS})
FEEDBACK_TAGS = ("Bug", "Suggestion", "Data correction", "Question", "Under review", "Planned", "Done", "Won't do")
# Real-looking snowflakes: ids encode their creation time.
MONDAY = datetime.date(2026, 10, 26)


def snowflake(when):
    return discord.utils.time_snowflake(when)


def post(thread_id, tags, name=None):
    return FakeThread(thread_id, TEAM_BOARD, tags, name=name or f"Post {thread_id}")


class FakeMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.edits = []
        self.pinned = False
        self.embed = None

    async def edit(self, **kwargs):
        if self.id == "gone":
            raise discord.NotFound(SimpleNamespace(status=404, reason="gone"), "gone")
        self.edits.append(kwargs["embed"])

    async def pin(self, **_kwargs):
        self.pinned = True


class FakeOps:
    def __init__(self):
        self.sent = []
        self.partials = {}

    async def send(self, **kwargs):
        message = FakeMessage(777000 + len(self.sent))
        message.embed = kwargs["embed"]
        self.sent.append(message)
        return message

    def get_partial_message(self, message_id):
        return self.partials.setdefault(message_id, FakeMessage(message_id))


class BoardFixture(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.ops = FakeOps()
        self.feedback = FakeForum(FEEDBACK, FEEDBACK_TAGS)
        self.public = FakeThread(1552020789509755194, FEEDBACK, ("Suggestion", "Under review"))
        self.threads = []

    def board(self, *answers):
        self.api = FakeApi(*answers)  # pylint: disable=attribute-defined-outside-init

        async def get_channel(channel_id):
            return {OPS: self.ops, FEEDBACK: self.feedback, self.public.id: self.public}[channel_id]

        async def list_threads():
            return self.threads

        return Board(self.api, BOARD_CONFIG, get_channel, list_threads)

    def calls(self):
        return [call[0] for call in self.api.calls]


class RenderTests(unittest.TestCase):

    def test_now_next_backlog(self):
        threads = [
            post(10, ("Bug", "High", "In progress"), "Fix the logo"),
            post(11, ("Feature", "Normal", "Planned"), "Local times"),
            post(12, ("Feature", "Low", "Planned"), "Frames [beta]"),
            post(13, ("Polish", "High"), "Odds tooltips"),
            post(14, ("Chore", "Normal"), "Lazy gallery"),
            post(15, ("General",), "How this board works"),
            post(16, ("Bug", "Done"), "Old bug"),
        ]
        text = render_up_next(threads, ["12", "11", "10"]).description
        self.assertIn("**Now**\n🔨 [Fix the logo]", text)
        self.assertIn("1. [Frames (beta)]", text)
        self.assertIn("2. [Local times]", text)
        self.assertIn("**Backlog** (2)\n🟠 High\n- [Odds tooltips]", text)
        self.assertIn("🟡 Normal\n- [Lazy gallery]", text)
        self.assertNotIn("How this board works", text)
        self.assertNotIn("Old bug", text)

    def test_an_empty_board(self):
        text = render_up_next([], []).description
        self.assertIn("Nothing in progress.", text)
        self.assertIn(fmt.NOTHING_NEXT, text)

    def test_queue_order_is_oldest_first_and_only_committed_posts(self):
        threads = [post(30, ("Bug", "Planned")), post(20, ("Bug", "In progress")), post(25, ("Bug",))]
        self.assertEqual(queue_order(threads), [20, 30])

    def test_the_weekly_summary(self):
        old = snowflake(datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc))
        new = snowflake(datetime.datetime(2026, 10, 24, tzinfo=datetime.timezone.utc))
        threads = [
            post(old, ("Bug",), "Old one"), post(new, ("Feature", "Planned"), "New one"), post(new + 1, ("General",)),
        ]
        embed = render_weekly(threads, MONDAY)
        self.assertIn("New: 1 · Planned: 1 · In progress: 0", embed.description)
        self.assertIn("[New one]", embed.description)
        self.assertNotIn("Old one", embed.description.split("New this week")[1])


class QueueFollowsTagsTests(BoardFixture):

    async def test_planned_joins_done_leaves(self):
        board = self.board(
            Answer(201, {}), Answer(404, {}), Answer(200, {"thread_ids": []}), Answer(409, {"message_id": "5"}),
        )
        before, after = post(10, ("Bug",)), post(10, ("Bug", "Planned"))
        self.assertEqual(await board.on_thread_update(before, after), "Planned")
        self.assertEqual(self.api.calls[0], ("queue_add", 10))

        board = self.board(
            Answer(200, {}), Answer(404, {}), Answer(200, {"thread_ids": []}), Answer(409, {"message_id": "5"}),
        )
        await board.on_thread_update(post(10, ("Bug", "Planned")), post(10, ("Bug", "Done")))
        self.assertEqual(self.api.calls[0], ("queue_remove", 10))

    async def test_planned_to_in_progress_keeps_its_place(self):
        board = self.board(Answer(404, {}), Answer(200, {"thread_ids": ["10"]}), Answer(409, {"message_id": "5"}))
        await board.on_thread_update(post(10, ("Bug", "Planned")), post(10, ("Bug", "In progress")))
        self.assertNotIn("queue_add", self.calls())
        self.assertNotIn("queue_remove", self.calls())

    async def test_an_unrelated_edit_does_nothing(self):
        board = self.board()
        self.assertIsNone(await board.on_thread_update(post(10, ("Bug",)), post(10, ("Bug",))))
        self.assertEqual(self.api.calls, [])
        elsewhere = FakeThread(9, FEEDBACK, ("Bug",))
        self.assertIsNone(await board.on_thread_update(elsewhere, elsewhere))

    async def test_reconcile_sends_the_committed_posts(self):
        self.threads = [post(30, ("Bug", "Planned")), post(20, ("Bug", "In progress")), post(25, ("Bug",))]
        board = self.board(Answer(200, {}), Answer(200, {"thread_ids": ["20", "30"]}), Answer(409, {"message_id": "5"}))
        await board.reconcile()
        self.assertEqual(self.api.calls[0], ("queue_sync", [20, 30]))


class UpNextTests(BoardFixture):

    async def test_first_time_posts_pins_and_records(self):
        board = self.board(Answer(200, {"thread_ids": []}), Answer(201, {}), Answer(200, {}))
        await board.refresh()
        (message,) = self.ops.sent
        self.assertTrue(message.pinned)
        self.assertEqual(self.api.calls[-1], ("record", f"upnext:{OPS}", message.id))

    async def test_later_it_edits_the_same_message(self):
        board = self.board(Answer(200, {"thread_ids": []}), Answer(409, {"message_id": "555"}))
        await board.refresh()
        self.assertEqual(self.ops.sent, [])
        self.assertEqual(len(self.ops.partials[555].edits), 1)

    async def test_a_deleted_message_is_posted_again(self):
        self.ops.partials[555] = FakeMessage("gone")
        self.ops.get_partial_message = lambda message_id: self.ops.partials[555]
        board = self.board(Answer(200, {"thread_ids": []}), Answer(409, {"message_id": "555"}), Answer(200, {}))
        await board.refresh()
        self.assertEqual(len(self.ops.sent), 1)
        self.assertEqual(self.calls()[-1], "record")


class WeeklyTests(BoardFixture):

    async def test_posts_on_monday_once(self):
        board = self.board(Answer(201, {}), Answer(200, {}))
        self.assertTrue(await board.weekly(MONDAY))
        self.assertEqual(self.api.calls[0], ("claim", "weekly:20261026", OPS))
        self.assertEqual(len(self.ops.sent), 1)

        board = self.board(Answer(409, {"message_id": "1"}))
        self.assertFalse(await board.weekly(MONDAY))
        self.assertEqual(len(self.ops.sent), 1)

    async def test_not_on_other_days(self):
        board = self.board()
        self.assertFalse(await board.weekly(MONDAY + datetime.timedelta(days=1)))
        self.assertEqual(self.api.calls, [])


class MirrorTests(BoardFixture):

    def promoted(self):
        return Answer(200, {"key": f"promote:{self.public.id}", "message_id": "900"})

    async def test_done_reaches_the_public_post_with_a_reply(self):
        board = self.board(self.promoted())
        self.assertEqual(await board.mirror(post(900, ("Feature", "Done")), "Done"), "Done")
        self.assertEqual([t.name for t in self.public.applied_tags], ["Suggestion", "Done"])
        self.assertEqual(self.public.sent, [fmt.PUBLIC_DONE])

    async def test_in_progress_shows_as_planned_and_replies_once(self):
        board = self.board(self.promoted(), self.promoted())
        await board.mirror(post(900, ("Feature", "Planned")), "Planned")
        await board.mirror(post(900, ("Feature", "In progress")), "In progress")
        self.assertEqual([t.name for t in self.public.applied_tags], ["Suggestion", "Planned"])
        self.assertEqual(self.public.sent, [fmt.PUBLIC_PLANNED])

    async def test_a_post_that_never_was_public_is_left_alone(self):
        board = self.board(Answer(404, {}))
        self.assertIsNone(await board.mirror(post(901, ("Bug", "Done")), "Done"))
        self.assertEqual(self.public.sent, [])

    async def test_back_to_new_mirrors_nothing(self):
        board = self.board()
        self.assertIsNone(await board.mirror(post(900, ("Bug",)), None))
        self.assertEqual(self.api.calls, [])


class QueueCommandTests(BoardFixture):

    def teammate(self):
        interaction = FakeInteraction(roles=(TEAM_ROLE,), channel_id=5)
        return interaction

    async def test_move_reports_the_new_place_and_refreshes(self):
        interaction = self.teammate()
        board = self.board(
            Answer(200, {"thread_ids": ["11", "10"]}),
            Answer(200, {"thread_ids": ["11", "10"]}),
            Answer(409, {"message_id": "5"}),
        )
        await board.move(interaction, "10", 2)
        self.assertEqual(self.api.calls[0], ("queue_move", "10", 2))
        self.assertEqual(interaction.calls[1][1], fmt.MOVED.format(position=2))
        self.assertEqual(len(self.ops.partials[5].edits), 1)

    async def test_move_an_unqueued_post(self):
        interaction = self.teammate()
        await self.board(Answer(404, {})).move(interaction, "10", 1)
        self.assertEqual(interaction.last_text(), fmt.NOT_QUEUED)

    async def test_queue_needs_the_team_role(self):
        interaction = FakeInteraction(roles=(), channel_id=5)
        board = self.board()
        await board.move(interaction, "10", 1)
        self.assertEqual(interaction.last_text(), fmt.NEED_TEAM_ROLE)
        self.assertEqual(self.api.calls, [])

    async def test_autocomplete_numbers_and_filters(self):
        self.threads = [post(10, ("Bug", "Planned"), "Fix the logo"), post(11, ("Feature", "Planned"), "Local times")]
        board = self.board(Answer(200, {"thread_ids": ["11", "10"]}))
        choices = await board.suggest("logo")
        self.assertEqual([(c.name, c.value) for c in choices], [("2. Fix the logo", "10")])

    async def test_autocomplete_right_after_boot(self):
        """A cold cache is read even when the clock is young (a fresh container)."""
        self.threads = [post(10, ("Bug", "Planned"), "Fix the logo")]
        board = self.board(Answer(200, {"thread_ids": ["10"]}))
        original = board_module.time.monotonic
        board_module.time.monotonic = lambda: 5.0
        try:
            choices = await board.suggest("")
        finally:
            board_module.time.monotonic = original
        self.assertEqual([c.value for c in choices], ["10"])

    def test_status_helper(self):
        self.assertEqual(status_of(SimpleNamespace(applied_tags=[tag("Bug"), tag("Won't do")])), "Won't do")
