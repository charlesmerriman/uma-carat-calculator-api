"""
The bot's connection to Discord: one discord.py Client holding the Gateway
(the always-open WebSocket Discord pushes clicks and commands through).
discord.py owns the heartbeats, reconnects and rate limits; this file only
says what to do with what arrives.

Intents are the defaults: buttons, slash commands, message menus and new
forum posts need no privileged intent, so the bot asks for no member list, no
presences and no message content. (A message menu hands over the message it
was used on, text included, without Message Content.)
"""

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import tasks

from bot.api import SiteApi
from bot import health
from bot.announce import Announcer
from bot.board import Board
from bot.digitalocean import DigitalOcean
from bot.moderation import Moderation
from bot.ops import Ops
from bot.team import Team

logger = logging.getLogger(__name__)


class SiteBot(discord.Client):

    def __init__(self, config):
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.api = SiteApi(config.api_base_url, config.api_secret)
        self.tree = app_commands.CommandTree(self)
        self.moderation = Moderation(self.api, config)
        self.team = Team(self.api, config, self._channel) if config.team_enabled else None
        self.board = Board(self.api, config, self._channel, self._board_threads) if config.board_enabled else None
        self.do = DigitalOcean(config.do_token, config.do_app_id) if config.do_token and config.do_app_id else None
        self.ops = Ops(self.api, config, self._channel, self.do) if config.ops_enabled else None
        self.deploy_news = config.deploys_enabled and self.ops is not None
        self.probe_session = None
        self.announcer = Announcer(self.api, config, self._channel) if config.announce_channel_id else None
        self._loops_started = False

    async def setup_hook(self):
        """Runs once, before the Gateway connects."""
        await self.api.start()
        if self.do is not None:
            await self.do.start()
        if self.ops is not None:
            self.probe_session = health.new_session()
        guild = discord.Object(id=self.config.guild_id)
        self.tree.add_command(self.moderation.commands(), guild=guild)
        if self.team is not None:
            for command in self.team.commands():
                self.tree.add_command(command, guild=guild)
        if self.board is not None:
            self.tree.add_command(self.board.commands(), guild=guild)
        # Sent on every start: cheap, and a renamed or new command shows up
        # without a separate step. Guild-scoped, so it applies in seconds.
        synced = await self.tree.sync(guild=guild)
        logger.info("Registered %s command group(s) on guild %s", len(synced), self.config.guild_id)

    async def on_ready(self):
        logger.info("Connected as %s (%s)", self.user, getattr(self.user, "id", "?"))
        # on_ready fires again after every reconnect; the loops start once.
        if self._loops_started:
            return
        self._loops_started = True
        if self.board is not None:
            self.hourly_reconcile.start()
            self.monday_summary.start()
        if self.ops is not None:
            self.health_probe.start()
            self.daily_digest.start()
        if self.deploy_news:
            self.deploy_watch.start()
        if self.announcer is not None:
            self.announce_watch.start()

    @tasks.loop(minutes=10)
    async def announce_watch(self):
        """Banner and event moments that just came due (bot/announce.py)."""
        try:
            await self.announcer.tick(discord.utils.utcnow())
        except discord.HTTPException:
            logger.warning("Announcements failed", exc_info=True)

    @tasks.loop(seconds=60)
    async def health_probe(self):
        """The site from outside, every minute (bot/health.py)."""
        try:
            await self.ops.check(self.probe_session, discord.utils.utcnow())
        except discord.HTTPException:
            logger.warning("Could not post a health change", exc_info=True)

    @tasks.loop(minutes=5)
    async def daily_digest(self):
        """Yesterday's numbers in #calc-ops, from the digest hour on.
        Not a once-a-day timer: one failed try at 08:00 cost the whole day's
        digest on 2026-10-22. Every 5 minutes it tries again until the day is
        settled (Ops.digest_due), and 5 minutes is also the site's report cache
        (analytics/cache.py), so a retry reads a build the site finished after
        we gave up. A restart past the hour still posts, once, thanks to the claim."""
        now = discord.utils.utcnow()
        if not self.ops.digest_due(now):
            return
        try:
            await self.ops.digest(now.date())
        except discord.HTTPException:
            logger.warning("Daily digest failed; retrying in 5 minutes", exc_info=True)

    @tasks.loop(minutes=2)
    async def deploy_watch(self):
        """A line in #calc-dev-log per finished DigitalOcean deploy."""
        try:
            await self.ops.deploys()
        except discord.HTTPException:
            logger.warning("Deploy news failed", exc_info=True)

    @tasks.loop(hours=1)
    async def hourly_reconcile(self):
        """Repair the queue against the board's tags.
        The first run is at start-up, which covers anything missed while down."""
        try:
            await self.board.reconcile()
        except discord.HTTPException:
            logger.warning("Queue reconcile failed; next try in an hour", exc_info=True)

    @tasks.loop(time=datetime.time(hour=9, tzinfo=datetime.timezone.utc))
    async def monday_summary(self):
        """09:00 UTC daily; Board.weekly posts only on Mondays, once per week."""
        try:
            await self.board.weekly(discord.utils.utcnow().date())
        except discord.HTTPException:
            logger.warning("Weekly summary failed", exc_info=True)

    async def _board_threads(self):
        """Every team-board post, open and archived."""
        forum = await self._channel(self.config.team_board_id)
        threads = list(forum.threads)
        async for thread in forum.archived_threads(limit=None):
            threads.append(thread)
        return threads

    async def _channel(self, channel_id):
        """A channel from the cache, else from Discord."""
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def on_thread_create(self, thread):
        if self.team is not None:
            await self.team.on_thread_create(thread)
        if self.board is not None:
            await self.board.on_thread_create(thread)

    async def on_thread_update(self, before, after):
        if self.board is not None:
            await self.board.on_thread_update(before, after)

    async def on_thread_delete(self, thread):
        if self.board is not None:
            await self.board.on_thread_delete(thread)

    async def on_interaction(self, interaction):
        # Slash commands are answered by the CommandTree; component clicks
        # (buttons) come here, wherever their message is.
        if interaction.type == discord.InteractionType.component:
            try:
                await self.moderation.on_click(interaction)
            except discord.HTTPException:
                # Most often the 3-second window already closed (the worker
                # was busy or reconnecting). The clicker sees Discord's own
                # "interaction failed" and can click again.
                logger.warning("Could not answer a click", exc_info=True)

    async def close(self):
        await self.api.close()
        if self.do is not None:
            await self.do.close()
        if self.probe_session is not None:
            await self.probe_session.close()
        await super().close()
