"""discord_messages: posting and editing as the bot, once per key, never failing the caller."""

from unittest import mock

import requests
from django.test import override_settings

from calculatorapi import discord_messages
from calculatorapi.models import DiscordMessage
from calculatorapi.tests.base import CalculatorTestCase

CHANNEL = "1000"


def warns(test):
    """The one log line a swallowed failure must leave."""
    return test.assertLogs("calculatorapi.discord_messages", "WARNING")


def discord_answer(status=200, body=None):
    """A stand-in for requests' Response, shaped like Discord's reply."""
    answer = mock.Mock()
    answer.status_code = status
    answer.ok = 200 <= status < 300
    answer.json.return_value = body if body is not None else {"id": "555"}
    answer.text = str(body)
    return answer


@override_settings(DISCORD_BOT_TOKEN="test-token")
class SendTests(CalculatorTestCase):

    def send(self, key="test:1", **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            discord_messages.send(key, CHANNEL, content="Hello", **kwargs)

    def test_off_without_a_token_or_a_channel(self):
        with mock.patch("requests.request") as call:
            with self.settings(DISCORD_BOT_TOKEN=""):
                self.send()
            with self.captureOnCommitCallbacks(execute=True):
                discord_messages.send("test:2", "", content="Hello")
        call.assert_not_called()
        self.assertFalse(DiscordMessage.objects.exists())

    def test_posts_as_the_bot_and_records_the_message(self):
        with mock.patch("requests.request", return_value=discord_answer()) as call:
            self.send(embed={"title": "Hi"})
        method, url = call.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{discord_messages.API_BASE}/channels/{CHANNEL}/messages")
        self.assertEqual(call.call_args.kwargs["headers"]["Authorization"], "Bot test-token")
        body = call.call_args.kwargs["json"]
        self.assertEqual(body["embeds"], [{"title": "Hi"}])
        # Nothing the site posts may ping anyone.
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        row = DiscordMessage.objects.get(key="test:1")
        self.assertEqual((row.channel_id, row.message_id), (CHANNEL, "555"))

    def test_one_key_posts_once(self):
        with mock.patch("requests.request", return_value=discord_answer()) as call:
            self.send()
            self.send()
        call.assert_called_once()

    def test_nothing_is_sent_when_the_transaction_rolls_back(self):
        with mock.patch("requests.request") as call:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                discord_messages.send("test:1", CHANNEL, content="Hello")
        # Never run = what a rollback does to an on_commit callback.
        self.assertEqual(len(callbacks), 1)
        call.assert_not_called()
        self.assertFalse(DiscordMessage.objects.exists())

    def test_a_connection_error_is_swallowed_and_the_key_given_back(self):
        with mock.patch("requests.request", side_effect=requests.ConnectionError), warns(self):
            self.send()
        self.assertFalse(DiscordMessage.objects.exists())
        # Given back means the next run tries again.
        with mock.patch("requests.request", return_value=discord_answer()) as call:
            self.send()
        call.assert_called_once()
        self.assertTrue(DiscordMessage.objects.filter(key="test:1").exists())

    def test_a_refusal_gives_the_key_back(self):
        refused = discord_answer(403, {"message": "Missing Access"})
        with mock.patch("requests.request", return_value=refused), warns(self) as logs:
            self.send()
        # Discord's reason reaches the log, so a missing permission is findable.
        self.assertIn("Missing Access", logs.output[0])
        self.assertFalse(DiscordMessage.objects.exists())

    def test_a_429_is_retried_once_after_a_capped_wait(self):
        answers = [discord_answer(429, {"retry_after": 30}), discord_answer()]
        with mock.patch("requests.request", side_effect=answers) as call, \
                mock.patch("calculatorapi.discord_messages.time.sleep") as sleep:
            self.send()
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once_with(discord_messages.MAX_RETRY_WAIT_SECONDS)
        self.assertEqual(DiscordMessage.objects.get().message_id, "555")

    def test_a_second_429_gives_up(self):
        answers = [discord_answer(429, {"retry_after": 0.1})] * 2
        with mock.patch("requests.request", side_effect=answers) as call, \
                mock.patch("calculatorapi.discord_messages.time.sleep"), warns(self):
            self.send()
        self.assertEqual(call.call_count, 2)
        self.assertFalse(DiscordMessage.objects.exists())


@override_settings(DISCORD_BOT_TOKEN="test-token")
class EditTests(CalculatorTestCase):

    def edit(self, key="test:1"):
        with self.captureOnCommitCallbacks(execute=True):
            discord_messages.edit(key, content="Changed")

    def test_edits_the_recorded_message(self):
        DiscordMessage.objects.create(key="test:1", channel_id=CHANNEL, message_id="555")
        with mock.patch("requests.request", return_value=discord_answer()) as call:
            self.edit()
        method, url = call.call_args.args
        self.assertEqual(method, "PATCH")
        self.assertEqual(url, f"{discord_messages.API_BASE}/channels/{CHANNEL}/messages/555")
        self.assertEqual(call.call_args.kwargs["json"]["content"], "Changed")

    def test_nothing_to_edit_without_a_sent_message(self):
        # An in-flight claim has no message id yet.
        DiscordMessage.objects.create(key="test:2", channel_id=CHANNEL)
        with mock.patch("requests.request") as call:
            self.edit("test:1")
            self.edit("test:2")
        call.assert_not_called()

    def test_a_failed_edit_is_swallowed(self):
        DiscordMessage.objects.create(key="test:1", channel_id=CHANNEL, message_id="555")
        with mock.patch("requests.request", side_effect=requests.Timeout), warns(self):
            self.edit()
        self.assertTrue(DiscordMessage.objects.filter(key="test:1").exists())


class CutLinesTests(CalculatorTestCase):

    def more(self, hidden):
        return f"…and {hidden} more"

    def test_text_that_fits_is_untouched(self):
        self.assertEqual(discord_messages.cut_lines(["a", "b"], 10, self.more), "a\nb")

    def test_cuts_whole_lines_and_says_how_many(self):
        lines = ["one line", "two line", "three line"]
        text = discord_messages.cut_lines(lines, 25, self.more)
        self.assertEqual(text, "one line\n…and 2 more")
        self.assertLessEqual(len(text), 25)
