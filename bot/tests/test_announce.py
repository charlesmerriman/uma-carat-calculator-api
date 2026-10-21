"""Step 8: how each moment is worded, when it's due, and that each posts once."""

import dataclasses
import datetime
import unittest
from types import SimpleNamespace

from bot import announce
from bot.api import Answer
from tests.fakes import CONFIG, FakeApi

CHANNEL = 1489347594197930077
PING = 1557514236601700503
NOW = datetime.datetime(2026, 10, 27, 22, 5, tzinfo=datetime.timezone.utc)


def moment(kind="banner", milestone="live", predicted=False, at=NOW - datetime.timedelta(minutes=5), **extra):
    base = {
        "key": f"{kind}-207-{milestone}", "kind": kind, "milestone": milestone, "predicted": predicted,
        "at": at.isoformat(), "name": "Agnes Tachyon + Tokai Teio",
        "url": "https://umacaratcalculator.com/app/timeline",
        "starts_at": "2026-10-27T22:00:00+00:00", "ends_at": "2026-11-06T21:59:59+00:00",
        "image": "https://cdn.example/banner.png", "umas": ["Agnes Tachyon", "Tokai Teio"], "cards": [],
    }
    return {**base, **extra}


class WordingTests(unittest.TestCase):

    def test_titles(self):
        cases = {
            ("banner", "tomorrow", False): "Starts tomorrow: Agnes Tachyon + Tokai Teio",
            ("banner", "tomorrow", True): "Predicted to start tomorrow: Agnes Tachyon + Tokai Teio",
            ("banner", "live", False): "Live now: Agnes Tachyon + Tokai Teio",
            ("cm", "tomorrow", True): "Predicted: registration opens tomorrow for Agnes Tachyon + Tokai Teio",
            ("cm", "finals_registration_closing", False):
                "Finals registration closes in 12 hours: Agnes Tachyon + Tokai Teio",
            ("legend_races", "live", False): "New daily legend races are live: Agnes Tachyon + Tokai Teio",
        }
        for (kind, milestone, predicted), title in cases.items():
            with self.subTest(kind=kind, milestone=milestone):
                self.assertEqual(announce.title_for(moment(kind, milestone, predicted)), title)

    def test_body_uses_discord_timestamps_and_flags_predictions(self):
        text = announce.describe(moment("banner", "tomorrow", predicted=True))
        start = int(datetime.datetime(2026, 10, 27, 22, tzinfo=datetime.timezone.utc).timestamp())
        self.assertIn(f"Starts <t:{start}:F> (<t:{start}:R>)", text)
        self.assertIn("Featured umas: Agnes Tachyon, Tokai Teio", text)
        self.assertIn(announce.PREDICTED_NOTE, text)
        self.assertNotIn(announce.PREDICTED_NOTE, announce.describe(moment()))

    def test_a_cm_lists_its_schedule(self):
        schedule = {"registration_opens": "2026-10-28T22:00:00+00:00", "finals": "2026-11-05T22:00:00+00:00"}
        text = announce.describe(moment("cm", "registration_opens", umas=[], schedule=schedule))
        opens = int(datetime.datetime(2026, 10, 28, 22, tzinfo=datetime.timezone.utc).timestamp())
        finals = int(datetime.datetime(2026, 11, 5, 22, tzinfo=datetime.timezone.utc).timestamp())
        self.assertIn(f"Registration opens: <t:{opens}:F>", text)
        self.assertIn(f"Finals: <t:{finals}:F>", text)

    def test_no_dashes_in_what_the_bot_says(self):
        for kind in ("banner", "cm", "loh", "campaign", "legend_races"):
            for milestone in ("tomorrow", "live") if kind not in ("cm", "loh") else ("tomorrow", "round_one", "finals"):
                text = announce.title_for(moment(kind, milestone)) + announce.describe(moment(kind, milestone))
                self.assertNotIn("—", text)

    def test_due_for_two_hours_after_its_instant(self):
        self.assertTrue(announce.is_due(moment(at=NOW), NOW))
        self.assertTrue(announce.is_due(moment(at=NOW - datetime.timedelta(hours=1, minutes=59)), NOW))
        self.assertFalse(announce.is_due(moment(at=NOW - datetime.timedelta(hours=3)), NOW))
        self.assertFalse(announce.is_due(moment(at=NOW + datetime.timedelta(minutes=1)), NOW))


class FakeChannel:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, content=None, **kwargs):
        if self.fail:
            import discord  # pylint: disable=import-outside-toplevel
            raise discord.HTTPException(SimpleNamespace(status=403, reason="Missing Access"), "no")
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=990000 + len(self.sent))


class TickTests(unittest.IsolatedAsyncioTestCase):

    def announcer(self, *answers, ping=None, channel=None):
        self.api = FakeApi(*answers)  # pylint: disable=attribute-defined-outside-init
        self.channel = channel or FakeChannel()  # pylint: disable=attribute-defined-outside-init

        async def get_channel(_channel_id):
            return self.channel

        config = dataclasses.replace(CONFIG, announce_channel_id=CHANNEL, announce_ping_role_id=ping)
        return announce.Announcer(self.api, config, get_channel)

    async def test_posts_the_due_ones_once(self):
        moments = [moment(), moment("banner", "tomorrow", at=NOW + datetime.timedelta(hours=5))]
        announcer = self.announcer(Answer(200, {"moments": moments}), Answer(201, {}), Answer(200, {}))
        self.assertEqual(await announcer.tick(NOW), ["announce:banner-207-live"])
        content, kwargs = self.channel.sent[0]
        self.assertIsNone(content)
        self.assertEqual(kwargs["embed"].title, "Live now: Agnes Tachyon + Tokai Teio")
        self.assertEqual(kwargs["embed"].image.url, "https://cdn.example/banner.png")
        self.assertEqual(self.api.calls[1], ("claim", "announce:banner-207-live", CHANNEL))

        announcer = self.announcer(Answer(200, {"moments": [moment()]}), Answer(409, {"message_id": "1"}))
        self.assertEqual(await announcer.tick(NOW), [])
        self.assertEqual(self.channel.sent, [])

    async def test_with_a_ping_role_only_live_pings(self):
        moments = [moment(), moment("banner", "tomorrow", at=NOW)]
        answers = [
            Answer(200, {"moments": moments}), Answer(201, {}), Answer(200, {}), Answer(201, {}), Answer(200, {}),
        ]
        announcer = self.announcer(*answers, ping=PING)
        await announcer.tick(NOW)
        (live, reminder) = self.channel.sent
        self.assertEqual(live[0], f"<@&{PING}>")
        self.assertEqual([role.id for role in live[1]["allowed_mentions"].roles], [PING])
        self.assertIsNone(reminder[0])

    async def test_a_refused_post_gives_its_claim_back(self):
        announcer = self.announcer(Answer(200, {"moments": [moment()]}), Answer(201, {}), Answer(204, {}),
                                   channel=FakeChannel(fail=True))
        self.assertEqual(await announcer.tick(NOW), [])
        self.assertEqual(self.api.calls[-1], ("release", "announce:banner-207-live"))

    async def test_site_down_posts_nothing(self):
        self.assertEqual(await self.announcer("down").tick(NOW), [])
