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
from bot.board import Board
from bot.moderation import Moderation
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
        self._loops_started = False

    async def setup_hook(self):
        """Runs once, before the Gateway connects."""
        await self.api.start()
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
        if self.board is not None and not self._loops_started:
            self._loops_started = True
            self.hourly_reconcile.start()
            self.monday_summary.start()

    @tasks.loop(hours=1)
    async def hourly_reconcile(self):
        """Repair the queue against the board's tags (and refresh Up next).
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
        await super().close()
