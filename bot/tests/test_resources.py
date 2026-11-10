"""The resource warnings: the gauges' two lines, restart spotting, the
reasons, and Ops posting them in #calc-ops."""

import dataclasses
import datetime
import unittest

from bot import resources
from bot.api import Answer
from bot.ops import Ops
from tests.fakes import CONFIG, TEAM_ROLE, FakeApi
from tests.test_ops import OPS_ID, ROUTES, FakeChannel

STEP = 120  # DigitalOcean samples about every 2 minutes
NOW = datetime.datetime(2026, 11, 10, 18, 0, tzinfo=datetime.timezone.utc)
NOW_TS = int(NOW.timestamp())


def series(values, end=NOW_TS):
    """[(unix seconds, value)] ending at `end`, one sample per STEP."""
    start = end - STEP * (len(values) - 1)
    return [(start + STEP * index, float(value)) for index, value in enumerate(values)]


def hours(count, value):
    return [value] * (count * 3600 // STEP)


class GaugeTests(unittest.TestCase):

    def test_hovering_at_the_line_warns_once(self):
        # 2026-11-10: memory wobbled across 80% all day and the email fired three times.
        gauge = resources.Gauge(resources.MEMORY_HIGH, resources.MEMORY_CLEAR)
        wobble = [79, 81, 82, 81, 79, 81, 82, 81, 81, 79, 78, 81, 82, 81]
        changes = []
        for end in range(3, len(wobble) + 1):
            points = series(wobble[:end], end=NOW_TS - STEP * (len(wobble) - end))
            changes.append(gauge.feed(points, points[-1][0]))
        self.assertEqual([change for change in changes if change], ["high"])
        self.assertTrue(gauge.high)

    def test_clears_only_under_the_lower_line(self):
        gauge = resources.Gauge(resources.MEMORY_HIGH, resources.MEMORY_CLEAR)
        self.assertEqual(gauge.feed(series([81, 82, 83]), NOW_TS), "high")
        self.assertIsNone(gauge.feed(series([78, 77, 76]), NOW_TS))
        self.assertEqual(gauge.feed(series([46, 45, 45]), NOW_TS), "clear")

    def test_one_sample_or_stale_data_changes_nothing(self):
        gauge = resources.Gauge(resources.CPU_HIGH, resources.CPU_CLEAR)
        self.assertIsNone(gauge.feed(series([99]), NOW_TS))
        self.assertIsNone(gauge.feed(series([99, 99, 99], end=NOW_TS - 3600), NOW_TS))
        self.assertIsNone(gauge.feed([], NOW_TS))
        self.assertFalse(gauge.high)


class RestartWatchTests(unittest.TestCase):

    def test_a_running_total_reports_once(self):
        watch = resources.RestartWatch()
        points = series([0, 0, 1, 1, 1])
        self.assertEqual(watch.feed(points), points[2][0])
        self.assertIsNone(watch.feed(points))
        later = series([1, 1, 2], end=NOW_TS + 600)
        self.assertEqual(watch.feed(later), later[2][0])

    def test_a_count_per_interval_reports_once(self):
        watch = resources.RestartWatch()
        points = series([0, 1, 0, 0])
        self.assertEqual(watch.feed(points), points[1][0])
        self.assertIsNone(watch.feed(points))
        self.assertIsNone(resources.RestartWatch().feed(series([0, 0, 0])))


# Memory shaped like 2026-11-09/10: flat at 64%, a deploy to 45%, back to 65%
# within the hour, then an evening rush to 79% and a creep to 81%.
CREEP = series(
    hours(6, 64) + [45, 50, 53] + [55 + n for n in range(10)] + hours(3, 65)
    + [65 + n // 6 for n in range(84)] + hours(8, 79) + [80, 81, 81, 81]
)
CPU_DAY = series(hours(25, 10) + [12] * 30)


class ReasonTests(unittest.TestCase):

    def test_a_creep_names_the_restart_and_the_biggest_climb(self):
        text = "\n".join(resources.explain_memory(CREEP, CPU_DAY))
        self.assertIn("Memory has crept up since the server last restarted (<t:", text)
        self.assertIn(":f>, at 45%)", text)
        self.assertIn("won't come back down on its own", text)
        self.assertIn("The biggest climb was 65% to 79% between <t:", text)
        self.assertIn("(a usual moment is 10%)", text)
        self.assertIn("A redeploy resets it", text)
        self.assertNotIn("killed", text)
        self.assertFalse(resources.is_fast_jump(CREEP))

    def test_a_jump_says_something_heavy_is_running(self):
        memory = series(hours(2, 60) + [62, 64, 66, 68, 70, 72, 74, 75, 75, 76, 76, 76])
        cpu = series(hours(2, 10) + [55] * 12)
        text = "\n".join(resources.explain_memory(memory, cpu))
        self.assertIn("Memory jumped from 60% to 76% in the last 30 minutes while CPU averaged ", text)
        self.assertIn("Something heavy is running right now", text)
        self.assertTrue(resources.is_fast_jump(memory))

    def test_a_restart_in_the_last_30_minutes_is_not_a_jump(self):
        memory = series(hours(2, 70) + [40, 50, 60, 70, 80, 82, 82])
        self.assertFalse(resources.is_fast_jump(memory))

    def test_near_the_limit_says_redeploy_soon(self):
        text = "\n".join(resources.explain_memory(series(hours(3, 91)), CPU_DAY))
        self.assertIn("**At 100% the server is killed and restarted**", text)
        self.assertIn("over the last day", text)

    def test_cpu_compares_with_yesterday_and_the_daily_peak(self):
        busy = series(hours(25, 30) + [90] * 30)
        text = "\n".join(resources.explain_cpu(busy, busy[-1][0], 15))
        self.assertIn("CPU has averaged 90% over the last 15 minutes. That's 3.0x this time yesterday (30%)", text)
        self.assertIn("15:00 to 16:00 UTC is the site's busiest hour every day.", text)
        usual = series(hours(25, 82) + [88] * 30)
        text = "\n".join(resources.explain_cpu(usual, usual[-1][0], 9))
        self.assertIn("This time yesterday it was 82%, so this is the usual daily rush", text)
        self.assertNotIn("15:00", text)

    def test_restart_reasons(self):
        restart = NOW_TS - 600
        oom = series(hours(1, 70) + [96, 99], end=restart)
        self.assertIn("**ran out of memory**: memory reached 99%",
                      resources.explain_restart(oom, CPU_DAY, restart, False)[0])
        pegged = series(hours(1, 99), end=restart)
        self.assertIn("CPU was maxed out (99%)", resources.explain_restart(CREEP, pegged, restart, False)[0])
        calm = resources.explain_restart(series(hours(1, 60), end=restart), CPU_DAY, restart, False)[0]
        self.assertIn("looked normal just before (memory 60%, CPU 12%)", calm)
        self.assertIn("--type run_restarted", calm)
        self.assertEqual(
            resources.explain_restart(oom, CPU_DAY, restart, True),
            ["A deploy was rolling out at the same moment, so this is most likely the deploy, not a crash."],
        )


class FakeDo:
    """DigitalOcean with fixed series; `minutes` picks the 30-minute or the day read."""
    app_id = "app1"

    def __init__(self, recent, history=None, deployments=()):
        self.recent = recent
        self.history = history or recent
        self._deployments = list(deployments)
        self.reads = []

    async def series(self, metric, minutes):
        self.reads.append((metric, minutes))
        source = self.recent if minutes == resources.RECENT_MINUTES else self.history
        return source.get(metric)

    async def deployments(self):
        return self._deployments


OPS_CONFIG = dataclasses.replace(CONFIG, ops_channel_id=OPS_ID, site_url="http://x")


class OpsResourceTests(unittest.IsolatedAsyncioTestCase):

    def ops(self, do, *answers):
        self.channel = FakeChannel()  # pylint: disable=attribute-defined-outside-init

        async def get_channel(_channel_id):
            return self.channel

        return Ops(FakeApi(*answers), OPS_CONFIG, get_channel, do, started=NOW)

    async def test_memory_warns_once_without_a_ping_then_clears(self):
        do = FakeDo(
            {"memory_percentage": series([81, 81, 81]), "cpu_percentage": series([10, 12, 11]),
             "restart_count": series([0, 0, 0])},
            {"memory_percentage": CREEP, "cpu_percentage": CPU_DAY},
        )
        ops = self.ops(do)
        self.assertEqual(await ops.resources(NOW), ["memory high"])
        self.assertEqual(await ops.resources(NOW), [])
        (content, kwargs), = self.channel.sent
        self.assertIsNone(content)
        self.assertEqual(kwargs["embed"].title, "API memory is high (81%)")
        self.assertIn("crept up since the server last restarted", kwargs["embed"].description)
        # A creep is not explained by the last 5 minutes' routes: no bot/health read.
        self.assertNotIn("Busiest routes", kwargs["embed"].description)

        do.recent["memory_percentage"] = series([46, 45, 45])
        self.assertEqual(await ops.resources(NOW), ["memory clear"])
        self.assertEqual(self.channel.sent[-1][0], "API memory is back to normal (45%).")

    async def test_cpu_warning_carries_the_busiest_routes(self):
        do = FakeDo(
            {"memory_percentage": series([50, 50, 50]), "cpu_percentage": series([93, 95, 97]),
             "restart_count": series([0, 0, 0])},
            {"memory_percentage": series(hours(2, 50)), "cpu_percentage": series(hours(25, 30) + [95] * 10)},
        )
        ops = self.ops(do, Answer(200, ROUTES))
        self.assertEqual(await ops.resources(NOW), ["cpu high"])
        embed = self.channel.sent[0][1]["embed"]
        self.assertEqual(embed.title, "API CPU is high (95%)")
        self.assertIn("this time yesterday (30%)", embed.description)
        self.assertIn("Busiest routes, last 5 minutes (4 in progress now):", embed.description)

    async def test_a_crash_pings_the_team_and_a_deploy_does_not(self):
        recent = {"memory_percentage": series([97, 99, 30]), "cpu_percentage": series([40, 40, 5]),
                  "restart_count": series([0, 0, 1])}
        ops = self.ops(FakeDo(recent))
        self.assertEqual(await ops.resources(NOW), ["restart"])
        content, kwargs = self.channel.sent[0]
        self.assertEqual(content, f"<@&{TEAM_ROLE}>")
        self.assertEqual(kwargs["embed"].title, "The API restarted")
        self.assertIn("ran out of memory", kwargs["embed"].description)

        deploy = {"created_at": (NOW - datetime.timedelta(minutes=8)).isoformat(),
                  "updated_at": (NOW - datetime.timedelta(minutes=1)).isoformat()}
        ops = self.ops(FakeDo(recent, deployments=[deploy]))
        self.assertEqual(await ops.resources(NOW), ["restart"])
        content, kwargs = self.channel.sent[0]
        self.assertIsNone(content)
        self.assertIn("most likely the deploy", kwargs["embed"].description)

    async def test_digitalocean_unreachable_posts_nothing(self):
        ops = self.ops(FakeDo({}))
        self.assertEqual(await ops.resources(NOW), [])
        self.assertEqual(self.channel.sent, [])
