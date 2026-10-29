"""
The team's operations feed: the daily digest and the health alerts in
#calc-ops, deploy news in #calc-dev-log (discord-bot-plan.md, Step 7).

Each of the three posts once however often the worker restarts: the digest
claims `digest:<yyyymmdd>` and each deploy `deploy:<uuid>` on the site before
posting, the same never-twice records as everything else the bot writes. A
health alert needs no claim: the state machine in health.py fires once per
change, and a restart starts it healthy, which at worst repeats one alert.
"""

import datetime
import logging

import discord

from bot import health
from bot.api import SiteUnavailable

logger = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()
# Deploy phases worth a line. SUPERSEDED (a newer deploy replaced it before it
# finished, the usual push race) is normal and says nothing new.
DEPLOY_LINES = {"ACTIVE": "✅ Live", "ERROR": "❌ Failed", "CANCELED": "⏹️ Cancelled"}
# A deploy older than the worker by more than this is history, not news: the
# first poll after a restart must not repost the last five deploys.
DEPLOY_GRACE = datetime.timedelta(minutes=15)


def _arrow(now, before):
    if now > before:
        return "↑"
    if now < before:
        return "↓"
    return "→"


def _plural(count, word):
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def render_digest(data, probe):
    """The digest embed from GET /bot/digest's numbers and the probe's day."""
    visits, accounts = data["visits"], data["accounts"]
    yesterday, before = visits["yesterday"], visits["day_before"]
    gallery = data["gallery_last_24_hours"]
    lines = [
        f"**Visitors** {yesterday['visitors']:,} {_arrow(yesterday['visitors'], before['visitors'])} "
        f"(day before {before['visitors']:,}, 7-day average {visits['average_7_days']:,})",
        f"**Page views** {yesterday['page_views']:,}",
        f"**New accounts** {accounts['new_yesterday']:,} "
        f"{_arrow(accounts['new_yesterday'], accounts['new_day_before'])} "
        f"({accounts['new_last_7_days']:,} this week, {accounts['total']:,} in total)",
        f"**Saved a plan in the last 7 days** {data['saved_last_7_days']:,}",
        f"**Active supporters** {data['active_supporters']:,}",
        f"**Gallery, last 24 hours** {_plural(gallery['posts'], 'post')}, {_plural(gallery['comments'], 'comment')}, "
        f"{_plural(gallery['reports'], 'report')}",
    ]
    if data.get("open_report_items"):
        lines.append(f"**Waiting for a moderator** {_plural(data['open_report_items'], 'item')}")
    if probe:
        p95 = f", 95% of responses within {probe['p95_seconds']} s" if probe["p95_seconds"] is not None else ""
        lines.append(f"**Site health** {probe['uptime_pct']}% of {probe['checks']:,} checks good{p95}")
        hour = probe.get("slowest_hour")
        if hour:
            within = (
                f"95% of API responses within {hour['p95_seconds']} s"
                if hour["p95_seconds"] is not None
                else "no API response at all"
            )
            lines.append(
                f"**Slowest hour** {hour['hour']:02d}:00 UTC, {within} "
                f"({hour['good']:,} of {hour['checks']:,} checks good)"
            )
    else:
        lines.append("**Site health** no full day of checks yet (the bot restarted)")
    return discord.Embed(title=f"Yesterday, {data['date']}", description="\n".join(lines), colour=0x3B82F6)


def _megabytes(size_bytes):
    return f"{size_bytes / 1_000_000:,.0f} MB"


def render_database_week(data, today):
    """The Monday post, from GET /bot/database: the database's size and running
    totals, and the queries that took the most total time once
    pg_stat_statements is installed (notes/server-diagnostics-plan.md, item 10)."""
    overview, top = data.get("overview"), data.get("top_queries")
    lines = []
    if overview:
        since = f"since {overview['since'][:10]}" if overview.get("since") else "since the statistics began"
        lines.append(f"**Size** {_megabytes(overview['size_bytes'])}")
        if overview.get("cache_hit_pct") is not None:
            lines.append(f"**Reads served from memory** {overview['cache_hit_pct']}%, {since}")
        lines.append(
            f"**Deadlocks** {overview['deadlocks']:,}, **temp files** {overview['temp_files']:,} "
            f"({_megabytes(overview['temp_bytes'])}), {since}"
        )
    else:
        lines.append("The site sent no database numbers.")
    if top is None:
        lines += ["", "Query statistics are off: `pg_stat_statements` isn't installed on the database yet."]
    elif not top.get("queries"):
        lines += ["", "No query statistics yet."]
    else:
        since = f" since {top['since'][:10]}" if top.get("since") else ""
        lines += ["", f"**Most time-consuming queries**{since}:"]
        for number, query in enumerate(top["queries"], start=1):
            # In backticks, so a backtick inside would end the code span early.
            text = query["query"].replace("`", "'")
            lines.append(f"{number}. `{text}`")
            lines.append(
                f"{_plural(query['calls'], 'call')}, {query['total_ms'] / 1000:,.0f} s in all, "
                f"{query['mean_ms']} ms each"
            )
    return discord.Embed(title=f"The database, week of {today:%Y-%m-%d}", description="\n".join(lines), colour=0x3B82F6)


def deploy_line(deployment, app_id):
    label = DEPLOY_LINES[deployment["phase"]]
    progress = deployment.get("progress") or {}
    steps = ""
    if progress.get("total_steps"):
        steps = f" ({progress.get('success_steps', 0)}/{progress['total_steps']} steps)"
    url = f"https://cloud.digitalocean.com/apps/{app_id}/deployments/{deployment['id']}"
    cause = (deployment.get("cause") or "deploy").replace("github.com/charlesmerriman/", "")
    return f"{label}: {cause}{steps}. [Details]({url})"


def _parse_time(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class Ops:
    """The digest, the probe's alerts and the deploy news, around the site API
    and (optionally) DigitalOcean."""

    def __init__(self, api, config, get_channel, do=None, *, started=None):  # pylint: disable=too-many-arguments
        self.api = api
        self.config = config
        self.get_channel = get_channel
        self.do = do
        self.tracker = health.Tracker()
        self.started = started or discord.utils.utcnow()
        # The UTC date whose digest is settled, so the retry loop stops asking.
        # In memory only: after a restart the site's claim answers instead.
        self.digest_done_for = None
        # The same, for the Monday database post.
        self.database_done_for = None

    # ── The daily digest ──

    def digest_due(self, now):
        """Whether the client's loop should try the digest: from the digest
        hour on, until today's is posted or found already posted. The loop
        runs every few minutes so a failed try is retried the same day."""
        return now.hour >= self.config.digest_hour_utc and self.digest_done_for != now.date()

    async def digest(self, today):
        """Post yesterday's digest once. Returns whether it posted."""
        key = f"digest:{today:%Y%m%d}"
        try:
            claim = await self.api.claim(key, self.config.ops_channel_id)
        except SiteUnavailable:
            return False
        if claim.status != 201:
            # Already posted (by us before a restart, or by hand): done for today.
            self.digest_done_for = today
            return False
        try:
            answer = await self.api.digest()
        except SiteUnavailable:
            # Give the day back, or the next try finds it claimed and gives up
            # (what happened on 2026-10-22, when the read timed out).
            logger.warning("Digest for %s not posted: the site's numbers didn't arrive; will retry", today)
            await self._release(key)
            return False
        if not answer.ok:
            logger.warning("Digest for %s not posted: the site answered %s; will retry", today, answer.status)
            await self._release(key)
            return False
        probe = self.tracker.summary(today - datetime.timedelta(days=1))
        ops = await self.get_channel(self.config.ops_channel_id)
        try:
            message = await ops.send(embed=render_digest(answer.data, probe), allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            await self._release(key)
            raise
        await self._record(key, message.id)
        self.digest_done_for = today
        return True

    # ── The database, on Mondays ──

    def database_due(self, now):
        """Mondays from the digest hour on, until this Monday's post is settled.
        Rides the digest's 5-minute loop, so a failed try is retried the same way."""
        return now.weekday() == 0 and now.hour >= self.config.digest_hour_utc and self.database_done_for != now.date()

    async def database_week(self, today):
        """Post the database's numbers once a week. Returns whether it posted."""
        key = f"database:{today:%Y%m%d}"
        try:
            claim = await self.api.claim(key, self.config.ops_channel_id)
        except SiteUnavailable:
            return False
        if claim.status != 201:
            self.database_done_for = today
            return False
        try:
            answer = await self.api.database()
        except SiteUnavailable:
            answer = None
        if answer is None or not answer.ok:
            logger.warning("Database post for %s not posted: the site's numbers didn't arrive; will retry", today)
            await self._release(key)
            return False
        ops = await self.get_channel(self.config.ops_channel_id)
        try:
            message = await ops.send(embed=render_database_week(answer.data, today), allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            await self._release(key)
            raise
        await self._record(key, message.id)
        self.database_done_for = today
        return True

    # ── The health probe ──

    async def check(self, session, now):
        """One probe; posts the alert or the recovery when the state flips.
        Returns the flip ("down", "up") or None."""
        readings = await health.probe(session, self.config.site_url, self.config.probe_slow_seconds)
        logger.info("probe %s", health.log_line(readings))
        change = self.tracker.feed(readings, now)
        if change is None:
            return None
        ops = await self.get_channel(self.config.ops_channel_id)
        if change == "down":
            peaks = await self.do.peaks() if self.do else None
            routes = await self._busiest_routes()
            role = self.config.team_role_id
            await ops.send(
                content=f"<@&{role}>" if role else None,
                embed=health.render_alert(self.tracker, peaks, routes),
                # The one ping the bot makes: @Web Developer on trouble, by the
                # owner's choice; never anyone else, never on recovery.
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(role)])
                if role else NO_MENTIONS,
            )
        else:
            await ops.send(health.render_recovery(self.tracker, now), allowed_mentions=NO_MENTIONS)
        return change

    async def _busiest_routes(self):
        """GET bot/health's snapshot for the alert, or None. ONE try, no retry:
        the API is the thing that's slow, so it may well not answer, and the
        alert goes out either way (the probe never adds load while the site is
        bad: health.py)."""
        try:
            answer = await self.api.health()
        except SiteUnavailable:
            return None
        return answer.data if answer.ok else None

    # ── Deploy news ──

    async def deploys(self):
        """One #calc-dev-log line per finished deploy. Returns how many it posted."""
        deployments = await self.do.deployments()
        if not deployments:
            return 0
        posted = 0
        for deployment in reversed(deployments):
            if deployment.get("phase") not in DEPLOY_LINES:
                continue
            finished = _parse_time(deployment.get("updated_at"))
            if finished is None or finished < self.started - DEPLOY_GRACE:
                continue
            key = f"deploy:{deployment['id']}"
            try:
                claim = await self.api.claim(key, self.config.devlog_channel_id)
            except SiteUnavailable:
                return posted
            if claim.status != 201:
                continue
            devlog = await self.get_channel(self.config.devlog_channel_id)
            message = await devlog.send(deploy_line(deployment, self.do.app_id), allowed_mentions=NO_MENTIONS)
            await self._record(key, message.id)
            posted += 1
        return posted

    async def _record(self, key, message_id):
        try:
            await self.api.record(key, message_id)
        except SiteUnavailable:
            logger.error("%s posted as %s but not recorded", key, message_id)

    async def _release(self, key):
        try:
            await self.api.release(key)
        except SiteUnavailable:
            pass
