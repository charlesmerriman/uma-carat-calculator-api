"""
Moderating from Discord: the card buttons and the /mod commands.

THE FLOW OF A CLICK
-------------------
Django posts each report card with Keep / Remove / Block author buttons whose
custom id says everything: `mod:<action>:<kind>:<ref>`. Discord sends the
click to us (only the application that sent a message gets its clicks), and:

1. Is it ours? Anything not shaped like a moderation id is ignored.
2. Is the clicker allowed? In our server, in the moderators' channel, holding
   the Moderator role. This is the second of three locks (decision 6): the
   channel's own permissions are the first, the site's shared key the third.
3. Acknowledge at once. Discord gives an interaction 3 seconds before the
   clicker sees "This interaction failed"; a deferred acknowledgement buys 15
   minutes, plenty for the site to answer even on a slow day.
4. Ask the site to decide, as "discord username (discord id)". The site
   edits the card itself, through the same code the admin uses, so the card
   and the admin can't disagree. We only tell the clicker how it went.

Block asks first (decision 5): the click answers privately with a "Yes,
block" button, and only that second click calls the site.

Every reply is ephemeral: only the moderator who clicked sees it, so the
channel holds the cards and nothing else.
"""

import logging
import re

import discord
from discord import app_commands

from bot import format as fmt
from bot.api import SiteUnavailable

logger = logging.getLogger(__name__)

# A post's ref is its public id (8 letters and digits), a comment's its row id.
CUSTOM_ID = re.compile(r"^mod:(keep|remove|block|block-confirm):(post|comment):([A-Za-z0-9]{1,40})$")
# A pasted /post/ link or a bare public id, for the commands.
POST_REF = re.compile(r"(?:/post/)?([A-Za-z0-9]{4,40})/?$")


def parse_custom_id(custom_id):
    """(action, kind, ref) for one of our button ids, else None."""
    match = CUSTOM_ID.match(custom_id or "")
    return match.groups() if match else None


def parse_post_ref(text):
    """The public id in a pasted post link or id, else None."""
    match = POST_REF.search((text or "").strip())
    return match.group(1) if match else None


def moderator_name(user):
    """How the site records a Discord moderator: username, then the id that
    still finds them after a rename."""
    return f"{user.name} ({user.id})"


def refusal(interaction, config, *, channel_bound):
    """Why this person may not moderate here, or None if they may.
    `channel_bound`: buttons only count in #calc-mod-queue; commands work
    anywhere in the server, since their replies are private anyway."""
    if interaction.guild_id != config.guild_id:
        return fmt.WRONG_PLACE
    if channel_bound and interaction.channel_id != config.mod_queue_channel_id:
        return fmt.WRONG_PLACE
    # A Member carries its roles; a plain User (outside a server) doesn't.
    roles = getattr(interaction.user, "roles", None) or ()
    if not any(role.id == config.moderator_role_id for role in roles):
        return fmt.NEED_ROLE
    return None


# The private copies of buttons (/mod post, the block question) are only
# containers: on_click answers every click by its id, wherever the button is.
# A timeout lets discord.py forget them; the card's own buttons never expire,
# since Django sent those and no view holds them.
PRIVATE_VIEW_SECONDS = 15 * 60


def decision_buttons(kind, ref):
    """The card's three buttons, for /mod post's private copy of a card."""
    view = discord.ui.View(timeout=PRIVATE_VIEW_SECONDS)
    for action, label, style in (
        ("keep", "Keep", discord.ButtonStyle.success),
        ("remove", "Remove", discord.ButtonStyle.danger),
        ("block", "Block author", discord.ButtonStyle.danger),
    ):
        view.add_item(discord.ui.Button(label=label, style=style, custom_id=f"mod:{action}:{kind}:{ref}"))
    return view


def confirm_block_button(kind, ref):
    view = discord.ui.View(timeout=PRIVATE_VIEW_SECONDS)
    view.add_item(discord.ui.Button(
        label=fmt.CONFIRM_LABEL, style=discord.ButtonStyle.danger, custom_id=f"mod:block-confirm:{kind}:{ref}",
    ))
    return view


class Moderation:
    """The click handler and the /mod commands, around one SiteApi."""

    def __init__(self, api, config):
        self.api = api
        self.config = config

    # ── Buttons ──

    async def on_click(self, interaction):
        """Every component click reaches here (client.on_interaction). Returns
        whether it was ours, for the tests and the log."""
        parsed = parse_custom_id((interaction.data or {}).get("custom_id"))
        if parsed is None:
            return False
        action, kind, ref = parsed
        # The confirm button lives on a private reply, not in the channel's
        # feed, but it still arrives from the channel the card is in.
        reason = refusal(interaction, self.config, channel_bound=True)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return True

        if action == "block":
            await interaction.response.send_message(
                fmt.CONFIRM_BLOCK, view=confirm_block_button(kind, ref), ephemeral=True
            )
            return True

        if action == "block-confirm":
            # Take the button off the private question first, so a second
            # click can't send a second request. This is the acknowledgement.
            await interaction.response.edit_message(content=fmt.BLOCKING, view=None)
            action = "block"
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)

        reply = await self.decide(action, kind, ref, interaction.user)
        await interaction.followup.send(reply, ephemeral=True)
        return True

    async def decide(self, action, kind, ref, user):
        """Ask the site to decide; the reply for the moderator."""
        try:
            answer = await self.api.moderate(kind, ref, action, moderator_name(user))
        except SiteUnavailable:
            return fmt.SITE_DOWN
        if answer.ok:
            logger.info("%s %s %s by %s", action, kind, ref, moderator_name(user))
            return fmt.DONE[action]
        if answer.status == 409:
            return fmt.already_handled(answer.data)
        if answer.status == 404:
            return fmt.GONE
        logger.error("Site refused a %s on %s %s: %s %s", action, kind, ref, answer.status, answer.data)
        return fmt.SITE_DOWN

    # ── /mod commands ──

    async def _guard(self, interaction):
        """Refuse and return False, or acknowledge privately and return True."""
        reason = refusal(interaction, self.config, channel_bound=False)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True

    async def reports(self, interaction):
        if not await self._guard(interaction):
            return
        try:
            answer = await self.api.reports()
        except SiteUnavailable:
            await interaction.followup.send(fmt.SITE_DOWN, ephemeral=True)
            return
        if not answer.data.get("items"):
            await interaction.followup.send(fmt.NO_REPORTS, ephemeral=True)
            return
        await interaction.followup.send(embed=fmt.reports_embed(answer.data), ephemeral=True)

    async def post(self, interaction, link):
        if not await self._guard(interaction):
            return
        ref = parse_post_ref(link)
        if ref is None:
            await interaction.followup.send(fmt.BAD_ID, ephemeral=True)
            return
        try:
            answer = await self.api.item("post", ref)
        except SiteUnavailable:
            await interaction.followup.send(fmt.SITE_DOWN, ephemeral=True)
            return
        if answer.status == 404:
            await interaction.followup.send(fmt.GONE, ephemeral=True)
            return
        kwargs = {"embed": fmt.item_embed(answer.data), "ephemeral": True}
        if answer.data.get("open_reports"):
            # The same buttons as the card; a click on them runs on_click.
            kwargs["view"] = decision_buttons("post", ref)
        await interaction.followup.send(**kwargs)

    async def unblock(self, interaction, link):
        if not await self._guard(interaction):
            return
        ref = parse_post_ref(link)
        if ref is None:
            await interaction.followup.send(fmt.BAD_ID, ephemeral=True)
            return
        try:
            answer = await self.api.unblock("post", ref, moderator_name(interaction.user))
        except SiteUnavailable:
            await interaction.followup.send(fmt.SITE_DOWN, ephemeral=True)
            return
        reply = {200: fmt.UNBLOCKED, 404: fmt.GONE, 409: fmt.NOT_BLOCKED}.get(answer.status, fmt.SITE_DOWN)
        await interaction.followup.send(reply, ephemeral=True)

    async def term(self, interaction, text, anywhere=False):
        if not await self._guard(interaction):
            return
        try:
            answer = await self.api.add_term(
                text, "anywhere" if anywhere else "word", moderator_name(interaction.user)
            )
        except SiteUnavailable:
            await interaction.followup.send(fmt.SITE_DOWN, ephemeral=True)
            return
        if answer.status == 201:
            reply = fmt.TERM_ADDED.format(term=fmt.escape(answer.data.get("term", text)))
        elif answer.status == 409:
            reply = fmt.TERM_EXISTS
        else:
            reply = fmt.escape(answer.data.get("detail")) or fmt.SITE_DOWN
        await interaction.followup.send(reply, ephemeral=True)

    def commands(self):
        """The /mod group, registered on our one server (guild-scoped, so a
        change shows up in seconds rather than the hour a global one takes)."""
        group = app_commands.Group(name="mod", description="Gallery moderation (Moderator role only)")

        @group.command(name="reports", description="List the posts and comments waiting for a decision")
        async def reports(interaction: discord.Interaction):
            await self.reports(interaction)

        @group.command(name="post", description="Show a post and its open reports, with the buttons")
        @app_commands.describe(link="The post's link or its id")
        async def post(interaction: discord.Interaction, link: str):
            await self.post(interaction, link)

        @group.command(name="unblock", description="Let a post's author post again")
        @app_commands.describe(link="The link or id of one of their posts")
        async def unblock(interaction: discord.Interaction, link: str):
            await self.unblock(interaction, link)

        @group.command(name="term", description="Refuse a word or phrase in captions, comments and names")
        @app_commands.describe(
            text="The word or phrase",
            anywhere="Also match inside longer words (only for terms that never appear innocently)",
        )
        async def term(interaction: discord.Interaction, text: str, anywhere: bool = False):
            await self.term(interaction, text, anywhere)

        return group
