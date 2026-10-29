"""
The health probe: the site checked from outside every minute, like a visitor,
with ONE alert when it goes bad and ONE line when it comes back
(discord-bot-plan.md, Step 7).

WHAT A CHECK IS
---------------
Two requests, one after the other, each with a 10-second limit:

* `/api/calculator-data` as a guest: the API's busiest route, the one that
  struggled on banner launch days. Expect 200 and JSON.
* `/`: the prerendered home page. Expect 200 and the app's root element.

A request is BAD on an error, a timeout, a non-200, the wrong kind of page,
or a time to first byte over PROBE_SLOW_SECONDS (3 s by default). A check is
bad when either request is.

WHY THREE IN A ROW (hysteresis)
-------------------------------
One slow reading is noise: a deploy restarting, a cold cache. So the state
only flips after three bad checks in a row (healthy -> trouble, one alert),
and back after three good ones (trouble -> healthy, one recovery line).
Nothing repeats while it stays bad, and the probe never speeds up or retries:
a slow site on launch day is exactly the case it watches, not one to add load
to.

The day's tallies (checks, how many were good, first-byte times) are kept in
memory for the digest's "site health" line. A restart starts them over,
which the digest says.

THE SLOWEST HOUR, AND A LINE PER CHECK
--------------------------------------
gunicorn's access line times a request from when a thread picks it up, so it
can't see a queue: on 2026-10-29 `/site-content` took 67 ms in the app and
15-17 s for a visitor. The probe sees what a visitor sees, so the tallies also
keep the API request's first byte per HOUR, and the digest names the day's
slowest hour (notes/server-diagnostics-plan.md, item 8). Only the API request:
the home page is a static file from the CDN and would water the number down.
Each check also writes one log line ("probe api 200 0.31s, home 200 0.08s"),
so the run log holds the minute-by-minute history, and keeps it for days once
the logs are forwarded somewhere (the plan's item 3).
"""

import asyncio
import collections
import datetime
import logging
import math
from dataclasses import dataclass

import aiohttp
import discord

logger = logging.getLogger(__name__)

TRIP_AFTER = 3
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = "UmaCaratCalculatorHealth (https://umacaratcalculator.com, 1.0)"
KEEP_DAYS = 2


@dataclass(frozen=True)
class Reading:
    what: str
    ok: bool
    status: int | None = None
    seconds: float | None = None
    problem: str = ""

    def describe(self):
        if self.ok:
            return f"{self.what} ok {self.seconds:.1f}s"
        return f"{self.what} {self.problem}"


def _looks_right(what, body):
    if what == "api":
        return body.lstrip()[:1] == b"{"
    return b'id="root"' in body


async def probe(session, site_url, slow_seconds):
    """One check: a Reading for the API and one for the home page."""
    readings = []
    loop = asyncio.get_running_loop()
    for what, path in (("api", "/api/calculator-data"), ("home", "/")):
        started = loop.time()
        try:
            async with session.get(f"{site_url}{path}") as response:
                # The request returns once the headers are in: first byte.
                first_byte = loop.time() - started
                body = await response.read()
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            name = "timed out" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__
            readings.append(Reading(what, False, problem=name))
            continue
        if status != 200:
            problem = f"HTTP {status}"
        elif first_byte > slow_seconds:
            problem = f"slow {first_byte:.1f}s"
        elif not _looks_right(what, body):
            problem = "unexpected page"
        else:
            problem = ""
        readings.append(Reading(what, not problem, status, first_byte, problem))
    return readings


def log_line(readings):
    """One check for the run log: "api 200 0.31s, home 200 0.08s", or the
    problem for a request that got no answer ("api timed out")."""
    parts = []
    for reading in readings:
        if reading.seconds is None:
            parts.append(f"{reading.what} {reading.problem}")
        else:
            parts.append(f"{reading.what} {reading.status} {reading.seconds:.2f}s")
    return ", ".join(parts)


def _p95(seconds):
    """The 95th percentile of `seconds` (nearest rank), or None for none."""
    ordered = sorted(seconds)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else None


def new_session():
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT},
    )


class Tracker:
    """The hysteresis state machine, and the day's tallies for the digest."""

    def __init__(self):
        self.healthy = True
        self.bad_streak = 0
        self.good_streak = 0
        self.recent = collections.deque(maxlen=TRIP_AFTER)
        self.trouble_since = None
        self.days = {}

    def feed(self, readings, when):
        """Record one check. Returns "down" when the state flips to trouble,
        "up" when it flips back, else None."""
        ok = all(reading.ok for reading in readings)
        self.recent.append((when, readings))
        day = self.days.setdefault(when.date(), {"checks": 0, "good": 0, "seconds": [], "hours": {}})
        day["checks"] += 1
        day["good"] += ok
        day["seconds"] += [reading.seconds for reading in readings if reading.seconds is not None]
        api = next((reading for reading in readings if reading.what == "api"), None)
        if api is not None:
            hour = day["hours"].setdefault(when.hour, {"checks": 0, "good": 0, "seconds": []})
            hour["checks"] += 1
            hour["good"] += api.ok
            if api.seconds is not None:
                hour["seconds"].append(api.seconds)
        for old in [d for d in self.days if d < when.date() - datetime.timedelta(days=KEEP_DAYS)]:
            del self.days[old]

        if ok:
            self.good_streak, self.bad_streak = self.good_streak + 1, 0
        else:
            self.bad_streak, self.good_streak = self.bad_streak + 1, 0
        if self.healthy and self.bad_streak >= TRIP_AFTER:
            self.healthy = False
            self.trouble_since = self.recent[0][0]
            return "down"
        if not self.healthy and self.good_streak >= TRIP_AFTER:
            self.healthy = True
            return "up"
        return None

    def summary(self, day):
        """{checks, uptime_pct, p95_seconds, slowest_hour} for `day`, or None
        without data. slowest_hour is {hour, p95_seconds, checks, good} for the
        API request, or None."""
        tally = self.days.get(day)
        if not tally or not tally["checks"]:
            return None
        p95 = _p95(tally["seconds"])
        return {
            "checks": tally["checks"],
            "uptime_pct": round(100 * tally["good"] / tally["checks"], 2),
            "p95_seconds": round(p95, 2) if p95 is not None else None,
            "slowest_hour": _slowest_hour(tally.get("hours") or {}),
        }


def _slowest_hour(hours):
    """The worst hour of a day for the API request: the lowest share of good
    checks first (an outage hour beats a merely slow one), then the highest
    95th-percentile first byte. Ties go to the earlier hour."""
    if not hours:
        return None

    def badness(item):
        hour, tally = item
        p95 = _p95(tally["seconds"])
        return (tally["good"] / tally["checks"], -(p95 or 0.0), hour)

    hour, tally = min(hours.items(), key=badness)
    p95 = _p95(tally["seconds"])
    return {
        "hour": hour,
        "p95_seconds": round(p95, 2) if p95 is not None else None,
        "checks": tally["checks"],
        "good": tally["good"],
    }


def _clock(when):
    return when.strftime("%H:%M UTC")


# How many of GET bot/health's routes the alert lists: enough to show what the
# API was busy with, short enough to read on a phone.
ROUTES_SHOWN = 5


def _seconds(ms):
    return f"{ms / 1000:.1f} s"


def render_routes(routes):
    """The alert's lines for GET bot/health's snapshot: the busiest routes by
    total time, with their database share, how many requests were in progress,
    and a warning when the guest payload isn't cached (what made the 2026-09-15
    and 09-23 outages). Empty when there is nothing to say."""
    lines = []
    rows = routes.get("routes") or []
    if rows:
        head = f"Busiest routes, last {routes.get('minutes', 5)} minutes"
        if routes.get("in_flight") is not None:
            head += f" ({routes['in_flight']} in progress now)"
        lines.append(head + ":")
        for row in rows[:ROUTES_SHOWN]:
            # In backticks: a pattern like `plan/<str:public_id>` must not be
            # read as Discord markdown.
            count = f"{row['count']:,} request{'' if row['count'] == 1 else 's'}"
            lines.append(
                f"`{row['route']}` {count}, {_seconds(row['total_ms'])} in all "
                f"({_seconds(row['db_ms'])} database), slowest {_seconds(row['max_ms'])}"
            )
    if routes.get("database"):
        lines.append(database_line(routes["database"]))
    if routes.get("payload_cached") is False:
        lines.append("The guest payload isn't cached: every guest load is rebuilding it.")
    return lines


def database_line(database):
    """"Database: 5 connections (1 active, 4 idle), 2 waiting on a lock": the
    database's side of a slow spell. Many active connections, or any waiting
    on a lock, point at the database; few point back at the web server."""
    connections = database.get("connections") or {}
    total = sum(connections.values())
    line = f"Database: {total} connection{'' if total == 1 else 's'}"
    if connections:
        # Active first: it's the number that matters in a slow spell.
        ordered = sorted(connections.items(), key=lambda item: (item[0] != "active", item[0]))
        line += " (" + ", ".join(f"{count} {state}" for state, count in ordered) + ")"
    waiting = database.get("waiting_on_locks") or 0
    if waiting:
        line += f", {waiting} waiting on a lock"
    return line


def render_alert(tracker, peaks, routes=None):
    """The one alert: what went wrong in the last three checks, the API's
    resources over the last five minutes when DigitalOcean is reachable, and
    what the API was busy with when it answered GET bot/health in time."""
    lines = [f"{_clock(when)}: " + ", ".join(r.describe() for r in readings) for when, readings in tracker.recent]
    slow_only = all(
        reading.ok or reading.problem.startswith("slow") for _, readings in tracker.recent for reading in readings
    )
    if peaks:
        parts = []
        if "cpu_percentage" in peaks:
            parts.append(f"CPU up to {peaks['cpu_percentage']:.0f}%")
        if "memory_percentage" in peaks:
            parts.append(f"memory up to {peaks['memory_percentage']:.0f}%")
        if "restart_count" in peaks:
            parts.append(f"restarts {peaks['restart_count']:.0f}")
        lines += ["", "API, last 5 minutes: " + ", ".join(parts)]
    route_lines = render_routes(routes) if routes else []
    if route_lines:
        lines += ["", *route_lines]
    return discord.Embed(
        title="The site is slow" if slow_only else "The site looks down",
        description="\n".join(lines),
        colour=0xF59E0B if slow_only else 0xE5484D,
    )


def render_recovery(tracker, now):
    minutes = round((now - tracker.trouble_since).total_seconds() / 60) if tracker.trouble_since else None
    took = f" after about {minutes} minute{'' if minutes == 1 else 's'}" if minutes else ""
    return f"The site is back to normal{took}."
