"""Moderation cards in Discord's #calc-mod-queue: one per reported item, edited
as it moves, cut down once decided or deleted, and never more about a person
than a display name and a handle."""

import json
from unittest import mock

import requests
from django.test import override_settings

from calculatorapi import gallery, gallery_moderation, moderation_cards
from calculatorapi.models import DiscordMessage, GalleryReportReason, SocialAccount
from calculatorapi.tests.factories import make_user
from calculatorapi.tests.test_gallery import GalleryFixture

CHANNEL = "4000"


def ok(message_id="900"):
    answer = mock.Mock(status_code=200, ok=True)
    answer.json.return_value = {"id": message_id}
    return answer


@override_settings(DISCORD_BOT_TOKEN="test-token", DISCORD_MOD_QUEUE_CHANNEL_ID=CHANNEL)
class ModerationCardTests(GalleryFixture):  # pylint: disable=too-many-public-methods

    def setUp(self):
        super().setUp()
        self.author = self.person(display_name="SpeedySuzuka")
        self.post_row = self.post(self.author)
        self.moderator = make_user("mod", is_staff=True)
        self.moderator.display_name = "Zac"
        self.moderator.save()

    def act(self, fn, *args, **kwargs):
        """Run `fn` with Discord faked. Returns the fake, so calls can be read."""
        with mock.patch("requests.request", return_value=ok()) as call:
            with self.captureOnCommitCallbacks(execute=True):
                fn(*args, **kwargs)
        return call

    def report(self, user=None, note="", **target):
        target = target or {"post": self.post_row}
        return gallery.report(
            user or self.person(), reason=GalleryReportReason.SPAM, note=note, **target
        )

    @staticmethod
    def sent(call):
        """(method, embed) of the last call."""
        return call.call_args.args[0], call.call_args.kwargs["json"]["embeds"][0]

    def test_silent_without_the_settings(self):
        with self.settings(DISCORD_MOD_QUEUE_CHANNEL_ID=""):
            call = self.act(self.report)
        call.assert_not_called()
        self.assertFalse(DiscordMessage.objects.exists())

    def test_the_first_report_sends_one_card(self):
        reporter = self.person(display_name="MooncakeMile")
        call = self.act(self.report, reporter, note="posted this five times")
        method, card = self.sent(call)
        self.assertEqual(method, "POST")
        self.assertTrue(call.call_args.args[1].endswith(f"/channels/{CHANNEL}/messages"))
        self.assertEqual(card["title"], "Post reported")
        text = json.dumps(card)
        self.assertIn("SpeedySuzuka", text)
        self.assertIn("MooncakeMile", text)
        self.assertIn(reporter.username, text)
        self.assertIn("posted this five times", text)
        self.assertIn(f"/post/{self.post_row.public_id}", text)
        self.assertEqual(
            DiscordMessage.objects.get(key=f"card:post:{self.post_row.pk}").message_id, "900"
        )

    def test_nothing_more_about_the_reporter(self):
        reporter = self.person(display_name="MooncakeMile")
        reporter.email = "mooncake@example.com"
        reporter.save()
        SocialAccount.objects.create(user=reporter, provider="discord", subject_id="81234567890")
        _, card = self.sent(self.act(self.report, reporter))
        text = json.dumps(card)
        for private in ("mooncake@example.com", "81234567890", "discord", "Discord"):
            self.assertNotIn(private, text)

    def test_later_reports_edit_the_same_card(self):
        self.act(self.report)
        call = self.act(self.report)
        method, card = self.sent(call)
        self.assertEqual(method, "PATCH")
        self.assertTrue(call.call_args.args[1].endswith(f"/channels/{CHANNEL}/messages/900"))
        self.assertEqual(card["fields"][2]["name"], "2 open reports")
        self.assertEqual(DiscordMessage.objects.count(), 1)

    def test_the_auto_hide_shows_on_the_card(self):
        for _ in range(gallery_moderation.AUTO_HIDE_REPORTS):
            call = self.act(self.report)
        _, card = self.sent(call)
        self.assertEqual(card["fields"][1]["value"], "Hidden after reports, waiting for a moderator")

    def test_a_decision_cuts_the_card_to_one_line(self):
        self.act(self.report, self.person(display_name="MooncakeMile"), note="spam")
        call = self.act(gallery_moderation.keep, self.post_row, self.moderator)
        method, card = self.sent(call)
        self.assertEqual(method, "PATCH")
        self.assertRegex(card["title"], r"^Post reported 1 time: kept by Zac on \d{4}/\d{1,2}/\d{1,2}$")
        # The text and every name are gone from Discord; the admin keeps them.
        self.assertEqual(set(card), {"title", "color"})
        self.assertNotIn("MooncakeMile", json.dumps(card))
        self.assertNotIn("SpeedySuzuka", json.dumps(card))

    def test_block_says_so(self):
        self.act(self.report)
        _, card = self.sent(self.act(gallery_moderation.block_author, self.post_row, self.moderator))
        self.assertIn("removed and its author blocked by Zac", card["title"])

    def test_a_new_report_opens_a_decided_card_again(self):
        self.act(self.report)
        self.act(gallery_moderation.remove, self.post_row, self.moderator)
        _, card = self.sent(self.act(self.report))
        self.assertEqual(card["title"], "Post reported")
        self.assertEqual(card["fields"][2]["name"], "1 open report")

    def test_a_take_down_strips_the_card(self):
        self.act(self.report)
        post_id = self.post_row.pk
        _, card = self.sent(self.act(gallery.take_down, self.post_row))
        self.assertEqual(card, {"title": "Post reported, then deleted", "color": moderation_cards.DONE_COLOUR})
        self.assertTrue(DiscordMessage.objects.filter(key=f"card:post:{post_id}").exists())

    def test_a_reported_comment_and_its_deletion(self):
        writer = self.person(display_name="Chatty")
        comment = gallery.add_comment(writer, self.post_row, "rude words here")
        _, card = self.sent(self.act(self.report, comment=comment))
        self.assertEqual(card["title"], "Comment reported")
        self.assertIn("rude words here", card["description"])
        # A comment with a reply stays as "[deleted]": the row lives on, so the
        # card is stripped by hand rather than by the delete hook.
        gallery.add_comment(self.person(), self.post_row, "a reply", parent_id=comment.pk)
        _, card = self.sent(self.act(gallery.delete_comment, comment))
        self.assertEqual(card["title"], "Comment reported, then deleted")

    def test_a_post_going_takes_its_comments_cards_with_it(self):
        comment = gallery.add_comment(self.person(), self.post_row, "fine words")
        self.act(self.report, comment=comment)
        call = self.act(gallery.take_down, self.post_row)
        titles = [c.kwargs["json"]["embeds"][0]["title"] for c in call.call_args_list]
        self.assertIn("Comment reported, then deleted", titles)

    def test_erasing_the_author_strips_the_card(self):
        self.act(self.report)
        _, card = self.sent(self.act(gallery.erase_users, [self.author]))
        self.assertEqual(card["title"], "Post reported, then deleted")

    def test_erasing_a_reporter_takes_their_name_off(self):
        reporter = self.person(display_name="MooncakeMile")
        self.act(self.report, reporter)
        _, card = self.sent(self.act(gallery.erase_users, [reporter]))
        text = json.dumps(card)
        self.assertNotIn("MooncakeMile", text)
        self.assertNotIn(reporter.username, text)
        self.assertIn("an account that was deleted", text)

    def test_user_text_cannot_format_or_close_the_block(self):
        comment = gallery.add_comment(self.person(), self.post_row, "``` **loud** ```")
        _, card = self.sent(self.act(self.report, comment=comment))
        body = card["description"]
        self.assertTrue(body.startswith("```\n") and body.endswith("\n```"))
        self.assertEqual(body.count("```"), 2)

    def test_discord_being_down_never_fails_a_report(self):
        with mock.patch("requests.request", side_effect=requests.ConnectionError), \
                self.assertLogs("calculatorapi.discord_messages", "WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                self.report()
        self.assertEqual(self.post_row.reports.count(), 1)
        self.assertFalse(DiscordMessage.objects.exists())

    # ── Buttons (only while the bot API is on) ──

    @staticmethod
    def components(call):
        return call.call_args.kwargs["json"].get("components")

    def test_no_buttons_without_the_bot_api(self):
        with self.settings(BOT_API_SECRET=""):
            call = self.act(self.report)
        self.assertEqual(self.components(call), [])
        self.assertEqual(self.sent(call)[1]["fields"][3]["name"], "Decide")

    @override_settings(BOT_API_SECRET="bot-key")
    def test_an_open_card_carries_the_three_buttons(self):
        call = self.act(self.report)
        (row,) = self.components(call)
        ref = self.post_row.public_id
        self.assertEqual(
            [button["custom_id"] for button in row["components"]],
            [f"mod:keep:post:{ref}", f"mod:remove:post:{ref}", f"mod:block:post:{ref}"],
        )
        self.assertTrue(all(len(button["custom_id"]) <= 100 for button in row["components"]))
        self.assertEqual(self.sent(call)[1]["fields"][3]["name"], "Links")

    @override_settings(BOT_API_SECRET="bot-key")
    def test_a_comment_button_names_the_comment_row(self):
        comment = gallery.add_comment(self.person(), self.post_row, "Nice team")
        call = self.act(self.report, comment=comment)
        (row,) = self.components(call)
        self.assertEqual(row["components"][0]["custom_id"], f"mod:keep:comment:{comment.pk}")

    @override_settings(BOT_API_SECRET="bot-key")
    def test_a_decision_leaves_only_undo_and_a_deletion_nothing(self):
        self.act(self.report)
        (row,) = self.components(self.act(gallery_moderation.keep, self.post_row, self.moderator))
        self.assertEqual(
            [(button["label"], button["custom_id"]) for button in row["components"]],
            [("Undo", f"mod:undo:post:{self.post_row.public_id}")],
        )
        self.act(self.report)
        self.assertEqual(self.components(self.act(gallery.take_down, self.post_row)), [])

    @override_settings(BOT_API_SECRET="bot-key")
    def test_an_undo_opens_the_card_again(self):
        self.act(self.report, self.person(display_name="MooncakeMile"))
        self.act(gallery_moderation.remove, self.post_row, self.moderator)
        call = self.act(gallery_moderation.undo_last_decision, self.post_row)
        _, card = self.sent(call)
        self.assertEqual(card["title"], "Post reported")
        self.assertIn("MooncakeMile", json.dumps(card))
        self.assertEqual(len(self.components(call)[0]["components"]), 3)

    def test_a_discord_decision_names_the_discord_moderator(self):
        self.act(self.report)
        call = self.act(
            gallery_moderation.remove, self.post_row, None,
            discord_moderator="oguri_fan (123456789012345678)",
        )
        title = self.sent(call)[1]["title"]
        self.assertIn("removed by oguri\\_fan (Discord) on", title)
        self.assertNotIn("123456789012345678", title)
