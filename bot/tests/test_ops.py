"""Step 7: the probe's state machine and checks, the digest, the alert ping,
and the deploy news."""

import asyncio
import dataclasses
import datetime
import unittest
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestServer

from bot import health
from bot.api import Answer
from bot.config import load
from bot.ops import Ops, deploy_line, render_digest
from tests.fakes import CONFIG, TEAM_ROLE, FakeApi
from tests.test_config import GOOD

OPS_ID = 1562200271222866020
DEVLOG_ID = 1562200272476831915
OPS_CONFIG = dataclasses.replace(CONFIG, ops_channel_id=OPS_ID, devlog_channel_id=DEVLOG_ID, site_url="http://x")
T0 = datetime.datetime(2026, 10, 21, 17, 0, tzinfo=datetime.timezone.utc)
GOOD_CHECK = [health.Reading("api", True, 200, 0.3), health.Reading("home", True, 200, 0.1)]
BAD_CHECK = [health.Reading("api", False, 502, 0.2, "HTTP 502"), health.Reading("home", True, 200, 0.1)]
SLOW_CHECK = [health.Reading("api", False, 200, 6.1, "slow 6.1s"), health.Reading("home", True, 200, 0.1)]


def minutes(n):
    return T0 + datetime.timedelta(minutes=n)


class TrackerTests(unittest.TestCase):

    def test_three_bad_trip_once_three_good_recover_once(self):
        tracker = health.Tracker()
        events = [tracker.feed(BAD_CHECK, minutes(n)) for n in range(2)]
        self.assertEqual(events, [None, None])
        self.assertEqual(tracker.feed(BAD_CHECK, minutes(2)), "down")
        self.assertEqual([tracker.feed(BAD_CHECK, minutes(3 + n)) for n in range(10)], [None] * 10)
        self.assertEqual([tracker.feed(GOOD_CHECK, minutes(13 + n)) for n in range(3)], [None, None, "up"])
        self.assertEqual(
            health.render_recovery(tracker, minutes(16)), "The site is back to normal after about 16 minutes.",
        )

    def test_one_good_check_resets_the_bad_streak(self):
        tracker = health.Tracker()
        for check in (BAD_CHECK, BAD_CHECK, GOOD_CHECK, BAD_CHECK, BAD_CHECK):
            self.assertIsNone(tracker.feed(check, T0))

    def test_summary_for_the_digest(self):
        tracker = health.Tracker()
        for n in range(19):
            tracker.feed(GOOD_CHECK, minutes(n))
        tracker.feed(BAD_CHECK, minutes(19))
        summary = tracker.summary(T0.date())
        self.assertEqual(summary["checks"], 20)
        self.assertEqual(summary["uptime_pct"], 95.0)
        self.assertEqual(summary["p95_seconds"], 0.3)
        self.assertIsNone(tracker.summary(T0.date() - datetime.timedelta(days=1)))

    def test_the_alert_says_slow_or_down(self):
        tracker = health.Tracker()
        for n in range(3):
            tracker.feed(SLOW_CHECK, minutes(n))
        embed = health.render_alert(tracker, {"cpu_percentage": 99.2, "memory_percentage": 91, "restart_count": 1})
        self.assertEqual(embed.title, "The site is slow")
        self.assertIn("17:00 UTC: api slow 6.1s, home ok 0.1s", embed.description)
        self.assertIn("CPU up to 99%, memory up to 91%, restarts 1", embed.description)
        tracker.feed(BAD_CHECK, minutes(3))
        self.assertEqual(health.render_alert(tracker, None).title, "The site looks down")


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    """health.probe against a throwaway server on 127.0.0.1."""

    async def asyncSetUp(self):
        self.api = (200, b'{"banner_uma_data": []}')
        self.home = (200, b'<html><div id="root"></div></html>')

        async def api(_request):
            return web.Response(status=self.api[0], body=self.api[1])

        async def home(_request):
            return web.Response(status=self.home[0], body=self.home[1])

        app = web.Application()
        app.router.add_get("/api/calculator-data", api)
        app.router.add_get("/", home)
        self.server = TestServer(app)
        await self.server.start_server()
        self.session = health.new_session()
        self.site = str(self.server.make_url("")).rstrip("/")

    async def asyncTearDown(self):
        await self.session.close()
        await self.server.close()

    async def test_a_healthy_site(self):
        readings = await health.probe(self.session, self.site, 3.0)
        self.assertTrue(all(reading.ok for reading in readings), readings)

    async def test_each_way_to_be_bad(self):
        cases = (
            ("api", (502, b"Bad gateway"), "HTTP 502"),
            ("home", (200, b"<html>maintenance</html>"), "unexpected page"),
            ("api", (200, b"<html>not json</html>"), "unexpected page"),
        )
        for what, answer, problem in cases:
            with self.subTest(problem=problem, what=what):
                self.api, self.home = (200, b"{}"), (200, b'<div id="root">')
                setattr(self, what, answer)
                readings = {r.what: r for r in await health.probe(self.session, self.site, 3.0)}
                self.assertEqual(readings[what].problem, problem)

    async def test_slow_is_bad(self):
        readings = await health.probe(self.session, self.site, -1.0)
        self.assertTrue(readings[0].problem.startswith("slow"))

    async def test_no_server_is_bad(self):
        await self.server.close()
        readings = await health.probe(self.session, self.site, 3.0)
        self.assertFalse(any(reading.ok for reading in readings))


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=880000 + len(self.sent))


DIGEST = {
    "date": "2026-10-20",
    "visits": {"yesterday": {"visitors": 120, "page_views": 410}, "day_before": {"visitors": 100, "page_views": 300},
               "average_7_days": 98.4},
    "accounts": {"total": 1300, "new_yesterday": 4, "new_day_before": 6, "new_last_7_days": 31},
    "saved_last_7_days": 220, "active_supporters": 12,
    "gallery_last_24_hours": {"posts": 1, "comments": 3, "reports": 0},
    "open_report_items": 2,
}


class OpsFixture(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.channels = {OPS_ID: FakeChannel(), DEVLOG_ID: FakeChannel()}

    def ops(self, *answers, do=None, config=OPS_CONFIG):
        self.api = FakeApi(*answers)  # pylint: disable=attribute-defined-outside-init

        async def get_channel(channel_id):
            return self.channels[channel_id]

        return Ops(self.api, config, get_channel, do, started=T0)


class DigestTests(OpsFixture):

    def test_render(self):
        text = render_digest(DIGEST, {"checks": 1440, "uptime_pct": 99.93, "p95_seconds": 0.42}).description
        self.assertIn("**Visitors** 120 ↑ (day before 100, 7-day average 98.4)", text)
        self.assertIn("**New accounts** 4 ↓ (31 this week, 1,300 in total)", text)
        self.assertIn("1 post, 3 comments, 0 reports", text)
        self.assertIn("**Waiting for a moderator** 2 items", text)
        self.assertIn("99.93% of 1,440 checks good, 95% of responses within 0.42 s", text)
        self.assertIn("no full day of checks yet", render_digest(DIGEST, None).description)

    async def test_posts_once_a_day(self):
        ops = self.ops(Answer(201, {}), Answer(200, DIGEST), Answer(200, {}))
        self.assertTrue(await ops.digest(datetime.date(2026, 10, 21)))
        self.assertEqual(self.api.calls[0], ("claim", "digest:20261021", OPS_ID))
        (content, kwargs) = self.channels[OPS_ID].sent[0]
        self.assertIsNone(content)
        self.assertEqual(kwargs["embed"].title, "Yesterday, 2026-10-20")
        ops = self.ops(Answer(409, {"message_id": "1"}))
        self.assertFalse(await ops.digest(datetime.date(2026, 10, 21)))
        self.assertEqual(len(self.channels[OPS_ID].sent), 1)

    async def test_a_failed_read_gives_the_day_back(self):
        ops = self.ops(Answer(201, {}), Answer(404, {}), Answer(204, {}))
        self.assertFalse(await ops.digest(datetime.date(2026, 10, 21)))
        self.assertEqual(self.api.calls[-1], ("release", "digest:20261021"))

    async def test_a_timed_out_read_gives_the_day_back(self):
        # 2026-10-22: the read timed out after the claim, the claim was kept,
        # and no later try could post the day.
        ops = self.ops(Answer(201, {}), "down", Answer(204, {}))
        self.assertFalse(await ops.digest(datetime.date(2026, 10, 22)))
        self.assertEqual(self.api.calls[-1], ("release", "digest:20261022"))
        self.assertTrue(ops.digest_due(datetime.datetime(2026, 10, 22, 8, 5, tzinfo=datetime.timezone.utc)))

    async def test_due_from_the_hour_until_settled(self):
        ops = self.ops(Answer(201, {}), Answer(200, DIGEST), Answer(200, {}))
        day = datetime.date(2026, 10, 22)

        def at(hour):
            return datetime.datetime(2026, 10, 22, hour, tzinfo=datetime.timezone.utc)

        self.assertFalse(ops.digest_due(at(OPS_CONFIG.digest_hour_utc - 1)))
        self.assertTrue(ops.digest_due(at(OPS_CONFIG.digest_hour_utc)))
        self.assertTrue(await ops.digest(day))
        self.assertFalse(ops.digest_due(at(23)))
        self.assertTrue(ops.digest_due(datetime.datetime(2026, 10, 23, 23, tzinfo=datetime.timezone.utc)))

    async def test_already_claimed_settles_the_day(self):
        ops = self.ops(Answer(409, {"message_id": "1"}))
        self.assertFalse(await ops.digest(datetime.date(2026, 10, 22)))
        self.assertFalse(ops.digest_due(datetime.datetime(2026, 10, 22, 12, tzinfo=datetime.timezone.utc)))


class AlertTests(OpsFixture):

    async def test_trouble_pings_the_team_and_recovery_does_not(self):
        ops = self.ops(config=dataclasses.replace(OPS_CONFIG, team_role_id=TEAM_ROLE))
        checks = iter([BAD_CHECK] * 3 + [GOOD_CHECK] * 3)

        async def fake_probe(*_args):
            return next(checks)

        original = health.probe
        health.probe = fake_probe
        try:
            changes = [await ops.check(None, minutes(n)) for n in range(6)]
        finally:
            health.probe = original
        self.assertEqual(changes, [None, None, "down", None, None, "up"])
        (alert, recovery) = self.channels[OPS_ID].sent
        self.assertEqual(alert[0], f"<@&{TEAM_ROLE}>")
        self.assertEqual([role.id for role in alert[1]["allowed_mentions"].roles], [TEAM_ROLE])
        self.assertFalse(alert[1]["allowed_mentions"].everyone)
        self.assertTrue(recovery[0].startswith("The site is back to normal"))
        self.assertEqual(recovery[1]["allowed_mentions"].to_dict()["parse"], [])


class FakeDo:
    app_id = "6c058011-003f-4a2a-bab7-8ff7491d1cd0"

    def __init__(self, deployments):
        self._deployments = deployments

    async def deployments(self):
        return self._deployments

    async def peaks(self):
        return {}


COMMIT = "commit 3554696 pushed to github.com/charlesmerriman/uma-carat-calculator-api/tree/master"


def deployment(uuid, phase, minutes_after_start, cause=COMMIT):
    when = (T0 + datetime.timedelta(minutes=minutes_after_start)).isoformat().replace("+00:00", "Z")
    return {
        "id": uuid, "phase": phase, "updated_at": when, "cause": cause,
        "progress": {"success_steps": 8, "total_steps": 8},
    }


class DeployTests(OpsFixture):

    async def test_one_line_per_finished_deploy_since_start(self):
        do = FakeDo([
            deployment("53d5dcf5-2339-4d82-b2d8-4701f8170835", "ACTIVE", 5),
            deployment("3b4871e4-29bb-4067-86b0-7c3ef2d986f9", "SUPERSEDED", 4),
            deployment("30a5513d-2f96-4b70-af00-adc8af0ff507", "ERROR", 2),
            deployment("aaaaaaaa-2f96-4b70-af00-adc8af0ff507", "BUILDING", 6),
            deployment("bbbbbbbb-2f96-4b70-af00-adc8af0ff507", "ACTIVE", -600),
        ])
        ops = self.ops(Answer(201, {}), Answer(200, {}), Answer(201, {}), Answer(200, {}), do=do)
        self.assertEqual(await ops.deploys(), 2)
        lines = [content for content, _ in self.channels[DEVLOG_ID].sent]
        self.assertTrue(lines[0].startswith(
            "❌ Failed: commit 3554696 pushed to uma-carat-calculator-api/tree/master (8/8 steps)"
        ))
        self.assertTrue(lines[1].startswith("✅ Live:"))
        self.assertEqual(self.api.calls[0], ("claim", "deploy:30a5513d-2f96-4b70-af00-adc8af0ff507", DEVLOG_ID))

    async def test_an_already_posted_deploy_is_skipped(self):
        do = FakeDo([deployment("53d5dcf5-2339-4d82-b2d8-4701f8170835", "ACTIVE", 5)])
        ops = self.ops(Answer(409, {"message_id": "1"}), do=do)
        self.assertEqual(await ops.deploys(), 0)
        self.assertEqual(self.channels[DEVLOG_ID].sent, [])

    def test_the_line_links_the_dashboard(self):
        canceled = deployment("53d5dcf5-2339-4d82-b2d8-4701f8170835", "CANCELED", 1, cause="app spec updated")
        line = deploy_line(canceled, "app1")
        self.assertEqual(
            line, "⏹️ Cancelled: app spec updated (8/8 steps). "
            "[Details](https://cloud.digitalocean.com/apps/app1/deployments/53d5dcf5-2339-4d82-b2d8-4701f8170835)",
        )


class ConfigTests(unittest.TestCase):

    def test_site_url_from_the_api_url(self):
        self.assertEqual(load(GOOD).site_url, "https://umacaratcalculator.com")
        self.assertEqual(load({**GOOD, "SITE_URL": "http://localhost:5173"}).site_url, "http://localhost:5173")

    def test_numbers(self):
        config = load({**GOOD, "PROBE_SLOW_SECONDS": "5", "DIGEST_HOUR_UTC": "14"})
        self.assertEqual((config.probe_slow_seconds, config.digest_hour_utc), (5.0, 14))

    def test_the_do_token_stays_out_of_repr(self):
        self.assertNotIn("do-secret", repr(load({**GOOD, "DO_API_TOKEN": "do-secret"})))


if __name__ == "__main__":
    asyncio.run(unittest.main())
