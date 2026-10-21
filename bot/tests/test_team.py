"""The team board: /request, "Make this a request", "Send to team board" and
the auto-tagger, against fake forums and a fake site."""

import unittest
from types import SimpleNamespace

from bot import format as fmt
from bot.api import Answer
from bot.team import AREA_TAGS, PRIORITY_TAGS, STATUS_TAGS, TYPE_TAGS, Team, complete_tags, quote, title_from
from tests.fakes import CONFIG, FEEDBACK, TEAM_BOARD, TEAM_ROLE, FakeApi, FakeForum, FakeInteraction, FakeThread

TEAM_TAGS = (*TYPE_TAGS, *PRIORITY_TAGS, *AREA_TAGS, *STATUS_TAGS)
FEEDBACK_TAGS = ("Bug", "Suggestion", "Data correction", "Question", "Under review", "Planned", "Done", "Won't do")


def names(thread):
    return [t.name for t in thread.applied_tags]


class TagRuleTests(unittest.TestCase):

    def test_missing_priority_and_area_are_filled(self):
        self.assertEqual(complete_tags(["Bug"]), ["Bug", "Normal", "General"])
        self.assertEqual(complete_tags(["Feature", "High", "Planner"]), ["Feature", "High", "Planner"])

    def test_a_post_without_a_type_is_left_alone(self):
        self.assertEqual(complete_tags(["General"]), ["General"])

    def test_never_past_five(self):
        full = ["Feature", "Planner", "Timeline", "PvP & gallery", "Planned"]
        self.assertEqual(complete_tags(full), full)

    def test_quote_and_title(self):
        self.assertEqual(quote("one\n\ntwo"), "> one\n>\n> two")
        self.assertEqual(title_from("\n  First line \nsecond", "x"), "First line")
        self.assertEqual(title_from("", "Request from Zac"), "Request from Zac")


class TeamFixture(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.api = None
        self.board = FakeForum(TEAM_BOARD, TEAM_TAGS)
        self.feedback = FakeForum(FEEDBACK, FEEDBACK_TAGS)

    def team(self, *answers):
        self.api = FakeApi(*answers)

        async def get_channel(channel_id):
            return {TEAM_BOARD: self.board, FEEDBACK: self.feedback}[channel_id]

        return Team(self.api, CONFIG, get_channel)

    @staticmethod
    def teammate(**kwargs):
        interaction = FakeInteraction(roles=(TEAM_ROLE,), channel_id=5, **kwargs)
        interaction.user.display_name = "Zac"
        return interaction


class RequestTests(TeamFixture):

    async def test_request_posts_with_full_tags(self):
        interaction = self.teammate()
        await self.team().request(interaction, "Logo turns white", "Bug", details="On light themes")
        (post,) = self.board.created
        self.assertEqual(post.name, "Logo turns white")
        self.assertEqual(names(post), ["Bug", "Normal", "General"])
        self.assertIn("On light themes", post.content)
        self.assertIn("Requested by Zac", post.content)
        self.assertEqual(interaction.last_text(), fmt.REQUEST_POSTED.format(link=post.jump_url))

    async def test_request_needs_the_team_role(self):
        interaction = FakeInteraction(roles=(), channel_id=5)
        await self.team().request(interaction, "x", "Bug")
        self.assertEqual(interaction.last_text(), fmt.NEED_TEAM_ROLE)
        self.assertEqual(self.board.created, [])

    async def test_a_refused_post_says_so(self):
        self.board.fail = True
        interaction = self.teammate()
        await self.team().request(interaction, "x", "Bug")
        self.assertEqual(interaction.last_text(), fmt.BOARD_FAILED)

    async def test_make_this_a_request_opens_the_form(self):
        interaction = self.teammate()
        message = SimpleNamespace(
            content="Can the timeline show local time?", author=SimpleNamespace(display_name="Henry"),
        )
        await self.team().make_request(interaction, message)
        self.assertEqual(interaction.kinds(), ["send_modal"])

    async def test_the_form_posts_a_quote_and_a_link(self):
        interaction = self.teammate()
        message = SimpleNamespace(
            content="Can the timeline show local time?", author=SimpleNamespace(display_name="Henry"),
            jump_url="https://discord.com/channels/1/2/3",
        )
        await self.team().submit_request(
            interaction, message,
            title="Local time on the timeline", names=["Feature", "Normal", "Timeline"], details="",
        )
        (post,) = self.board.created
        self.assertEqual(names(post), ["Feature", "Normal", "Timeline"])
        self.assertIn("> Can the timeline show local time?", post.content)
        self.assertIn("From Henry: https://discord.com/channels/1/2/3", post.content)


class SendToBoardTests(TeamFixture):

    def public(self, tags=("Suggestion",)):
        thread = FakeThread(1552020789509755194, FEEDBACK, tags, name="Links to other sources")
        message = SimpleNamespace(id=thread.id, content="Please link Gametora", channel=thread)
        return thread, message

    async def test_sends_once_marks_public_and_records(self):
        thread, message = self.public()
        interaction = self.teammate()
        team = self.team(Answer(201, {}), Answer(200, {}))
        await team.send_to_board(interaction, message)
        (post,) = self.board.created
        self.assertEqual(post.name, "Links to other sources")
        self.assertEqual(names(post), ["Feature", "Normal", "General"])
        self.assertIn(thread.jump_url, post.content)
        self.assertIn("> Please link Gametora", post.content)
        self.assertEqual([call[0] for call in self.api.calls], ["claim", "record"])
        self.assertEqual(self.api.calls[0][1], f"promote:{thread.id}")
        self.assertEqual(self.api.calls[1][2], post.id)
        self.assertEqual(names(thread), ["Suggestion", "Under review"])
        self.assertEqual(thread.sent, [fmt.PUBLIC_UNDER_REVIEW])
        self.assertIn(post.jump_url, interaction.last_text())

    async def test_a_second_send_links_the_first(self):
        _, message = self.public()
        interaction = self.teammate()
        await self.team(Answer(409, {"message_id": "900001"})).send_to_board(interaction, message)
        self.assertEqual(self.board.created, [])
        self.assertIn("900001", interaction.last_text())

    async def test_a_failed_post_gives_the_claim_back(self):
        self.board.fail = True
        _, message = self.public()
        interaction = self.teammate()
        await self.team(Answer(201, {}), Answer(204, {})).send_to_board(interaction, message)
        self.assertEqual([call[0] for call in self.api.calls], ["claim", "release"])
        self.assertEqual(interaction.last_text(), fmt.BOARD_FAILED)

    async def test_a_bug_stays_a_bug_and_old_status_is_replaced(self):
        thread, message = self.public(tags=("Bug", "Planned"))
        await self.team(Answer(201, {}), Answer(200, {})).send_to_board(self.teammate(), message)
        self.assertEqual(names(self.board.created[0])[0], "Bug")
        self.assertEqual(names(thread), ["Bug", "Under review"])

    async def test_only_in_the_feedback_forum(self):
        thread = FakeThread(7, 12345)
        interaction = self.teammate()
        await self.team().send_to_board(interaction, SimpleNamespace(id=1, content="x", channel=thread))
        self.assertEqual(interaction.last_text(), fmt.ONLY_FEEDBACK)
        self.assertEqual(self.api.calls, [])

    async def test_site_down_posts_nothing(self):
        _, message = self.public()
        interaction = self.teammate()
        await self.team("down").send_to_board(interaction, message)
        self.assertEqual(self.board.created, [])
        self.assertEqual(interaction.last_text(), fmt.SITE_DOWN)


class AutoTagTests(TeamFixture):

    async def test_a_new_post_gets_its_missing_tags(self):
        thread = FakeThread(5, TEAM_BOARD, ("Bug",))
        self.assertEqual(await self.team().on_thread_create(thread), ["Bug", "Normal", "General"])
        self.assertEqual(names(thread), ["Bug", "Normal", "General"])

    async def test_complete_posts_and_other_forums_are_untouched(self):
        complete = FakeThread(5, TEAM_BOARD, ("Bug", "High", "Planner"))
        elsewhere = FakeThread(6, FEEDBACK, ("Bug",))
        team = self.team()
        self.assertIsNone(await team.on_thread_create(complete))
        self.assertIsNone(await team.on_thread_create(elsewhere))
        self.assertEqual(complete.edits + elsewhere.edits, [])
