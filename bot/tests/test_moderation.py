"""The click handler and the /mod commands, against fakes: who may act, what
reaches the site, and what the moderator is told."""

import unittest

from bot import format as fmt
from bot.api import Answer
from bot.moderation import Moderation, parse_custom_id, parse_post_ref
from tests.fakes import CONFIG, FakeApi, FakeInteraction

MODERATOR = "oguri_fan (123456789012345678)"


class ParsingTests(unittest.TestCase):

    def test_our_button_ids(self):
        self.assertEqual(parse_custom_id("mod:keep:post:aB3dE5fG"), ("keep", "post", "aB3dE5fG"))
        self.assertEqual(parse_custom_id("mod:block-confirm:comment:61"), ("block-confirm", "comment", "61"))

    def test_anything_else_is_not_ours(self):
        for custom_id in (None, "", "mod:delete:post:x", "mod:keep:team:1", "mod:keep:post:a/b", "other:keep"):
            with self.subTest(custom_id=custom_id):
                self.assertIsNone(parse_custom_id(custom_id))

    def test_a_post_link_or_id(self):
        self.assertEqual(parse_post_ref("https://umacaratcalculator.com/post/aB3dE5fG"), "aB3dE5fG")
        self.assertEqual(parse_post_ref("https://umacaratcalculator.com/post/aB3dE5fG/"), "aB3dE5fG")
        self.assertEqual(parse_post_ref(" aB3dE5fG "), "aB3dE5fG")
        self.assertIsNone(parse_post_ref("not a link!"))


class ClickTests(unittest.IsolatedAsyncioTestCase):

    async def click(self, custom_id, *answers, **who):
        api = FakeApi(*answers)
        interaction = FakeInteraction(custom_id, **who)
        handled = await Moderation(api, CONFIG).on_click(interaction)
        return handled, interaction, api

    async def test_a_click_that_is_not_ours_is_left_alone(self):
        handled, interaction, api = await self.click("pvp:vote:1")
        self.assertFalse(handled)
        self.assertEqual(interaction.calls, [])
        self.assertEqual(api.calls, [])

    async def test_without_the_role_nothing_reaches_the_site(self):
        _, interaction, api = await self.click("mod:keep:post:aB3dE5fG", roles=(999,))
        self.assertEqual(interaction.kinds(), ["send_message"])
        self.assertEqual(interaction.last_text(), fmt.NEED_ROLE)
        self.assertTrue(interaction.calls[0][2]["ephemeral"])
        self.assertEqual(api.calls, [])

    async def test_another_servers_click_is_left_to_its_own_worker(self):
        handled, interaction, api = await self.click("mod:keep:post:aB3dE5fG", guild_id=1)
        self.assertFalse(handled)
        self.assertEqual(interaction.calls, [])
        self.assertEqual(api.calls, [])

    async def test_outside_the_channel_or_without_membership_is_refused(self):
        for who in ({"channel_id": 2}, {"member": False}):
            with self.subTest(who=who):
                _, interaction, api = await self.click("mod:keep:post:aB3dE5fG", **who)
                self.assertIn(interaction.last_text(), (fmt.WRONG_PLACE, fmt.NEED_ROLE))
                self.assertEqual(api.calls, [])

    async def test_keep_acknowledges_first_then_asks_the_site_once(self):
        _, interaction, api = await self.click("mod:keep:post:aB3dE5fG", Answer(200, {}))
        self.assertEqual(interaction.kinds(), ["defer", "followup"])
        self.assertTrue(interaction.calls[0][2]["ephemeral"])
        self.assertEqual(api.calls, [("moderate", "post", "aB3dE5fG", "keep", MODERATOR)])
        self.assertEqual(interaction.last_text(), fmt.DONE["keep"])

    async def test_someone_got_there_first(self):
        answer = Answer(409, {"handled_by": "Zac", "resolution": "removed"})
        _, interaction, _ = await self.click("mod:keep:comment:61", answer)
        self.assertEqual(interaction.last_text(), "Already removed by Zac.")

    async def test_the_site_down_says_try_again(self):
        _, interaction, _ = await self.click("mod:remove:post:aB3dE5fG", "down")
        self.assertEqual(interaction.last_text(), fmt.SITE_DOWN)

    async def test_a_deleted_item(self):
        _, interaction, _ = await self.click("mod:remove:post:aB3dE5fG", Answer(404, {}))
        self.assertEqual(interaction.last_text(), fmt.GONE)

    async def test_block_asks_first_and_calls_nothing(self):
        _, interaction, api = await self.click("mod:block:post:aB3dE5fG")
        self.assertEqual(interaction.kinds(), ["send_message"])
        self.assertEqual(interaction.last_text(), fmt.CONFIRM_BLOCK)
        (button,) = interaction.calls[0][2]["view"].children
        self.assertEqual(button.custom_id, "mod:block-confirm:post:aB3dE5fG")
        self.assertEqual(api.calls, [])

    async def test_the_confirmation_blocks_and_takes_its_button_away(self):
        _, interaction, api = await self.click("mod:block-confirm:post:aB3dE5fG", Answer(200, {}))
        self.assertEqual(interaction.kinds(), ["edit_message", "followup"])
        self.assertIsNone(interaction.calls[0][2]["view"])
        self.assertEqual(api.calls, [("moderate", "post", "aB3dE5fG", "block", MODERATOR)])
        self.assertEqual(interaction.last_text(), fmt.DONE["block"])


class CommandTests(unittest.IsolatedAsyncioTestCase):
    """Commands work anywhere in the server (their replies are private), but
    still need the role."""

    async def test_commands_need_the_role(self):
        interaction = FakeInteraction(roles=(), channel_id=5)
        api = FakeApi()
        await Moderation(api, CONFIG).reports(interaction)
        self.assertEqual(interaction.last_text(), fmt.NEED_ROLE)
        self.assertEqual(api.calls, [])

    async def test_reports_lists_or_says_nothing_is_waiting(self):
        interaction = FakeInteraction(channel_id=5)
        await Moderation(FakeApi(Answer(200, {"items": [], "total": 0})), CONFIG).reports(interaction)
        self.assertEqual(interaction.last_text(), fmt.NO_REPORTS)

        interaction = FakeInteraction(channel_id=5)
        listed = {"items": [{"kind": "post", "ref": "aB3dE5fG", "preview": "Caption", "open_reports": 2}], "total": 1}
        await Moderation(FakeApi(Answer(200, listed)), CONFIG).reports(interaction)
        self.assertIn("aB3dE5fG", interaction.calls[-1][2]["embed"].description)

    async def test_post_shows_the_card_with_buttons_while_open(self):
        item = {"kind": "post", "ref": "aB3dE5fG", "text": "Caption: hi", "status": "Visible",
                "author": {"display_name": "SpeedySuzuka", "handle": "demo"},
                "open_reports": [{"reason": "Spam", "note": "", "reporter": None}]}
        interaction = FakeInteraction()
        api = FakeApi(Answer(200, item))
        await Moderation(api, CONFIG).post(interaction, "https://umacaratcalculator.com/post/aB3dE5fG")
        self.assertEqual(api.calls, [("item", "post", "aB3dE5fG")])
        sent = interaction.calls[-1][2]
        self.assertEqual(
            [button.custom_id for button in sent["view"].children],
            ["mod:keep:post:aB3dE5fG", "mod:remove:post:aB3dE5fG", "mod:block:post:aB3dE5fG"],
        )

    async def test_unblock_and_its_answers(self):
        for status, reply in ((200, fmt.UNBLOCKED), (409, fmt.NOT_BLOCKED), (404, fmt.GONE)):
            with self.subTest(status=status):
                interaction = FakeInteraction()
                api = FakeApi(Answer(status, {}))
                await Moderation(api, CONFIG).unblock(interaction, "aB3dE5fG")
                self.assertEqual(api.calls, [("unblock", "post", "aB3dE5fG", MODERATOR)])
                self.assertEqual(interaction.last_text(), reply)

    async def test_term(self):
        interaction = FakeInteraction()
        api = FakeApi(Answer(201, {"term": "bad", "match": "anywhere"}))
        await Moderation(api, CONFIG).term(interaction, "BAD", anywhere=True)
        self.assertEqual(api.calls, [("add_term", "BAD", "anywhere", MODERATOR)])
        self.assertIn('"bad"', interaction.last_text())

    async def test_a_bad_post_id_never_reaches_the_site(self):
        interaction = FakeInteraction()
        api = FakeApi()
        await Moderation(api, CONFIG).post(interaction, "???")
        self.assertEqual(interaction.last_text(), fmt.BAD_ID)
        self.assertEqual(api.calls, [])

    def test_the_command_group(self):
        group = Moderation(FakeApi(), CONFIG).commands()
        self.assertEqual(group.name, "mod")
        self.assertEqual(sorted(command.name for command in group.commands), ["post", "reports", "term", "unblock"])
