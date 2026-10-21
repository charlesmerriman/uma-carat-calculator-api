"""
The bot's connection to Discord: one discord.py Client holding the Gateway
(the always-open WebSocket Discord pushes clicks and commands through).
discord.py owns the heartbeats, reconnects and rate limits; this file only
says what to do with what arrives.

Intents are the defaults: buttons and slash commands need no privileged
intent, so the bot asks for no member list, no presences and no message
content.
"""

import logging

import discord
from discord import app_commands

from bot.api import SiteApi
from bot.moderation import Moderation

logger = logging.getLogger(__name__)


class SiteBot(discord.Client):

    def __init__(self, config):
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.api = SiteApi(config.api_base_url, config.api_secret)
        self.tree = app_commands.CommandTree(self)
        self.moderation = Moderation(self.api, config)

    async def setup_hook(self):
        """Runs once, before the Gateway connects."""
        await self.api.start()
        guild = discord.Object(id=self.config.guild_id)
        self.tree.add_command(self.moderation.commands(), guild=guild)
        # Sent on every start: cheap, and a renamed or new command shows up
        # without a separate step. Guild-scoped, so it applies in seconds.
        synced = await self.tree.sync(guild=guild)
        logger.info("Registered %s command group(s) on guild %s", len(synced), self.config.guild_id)

    async def on_ready(self):
        logger.info("Connected as %s (%s)", self.user, getattr(self.user, "id", "?"))

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
