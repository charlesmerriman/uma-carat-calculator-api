"""
The team board runs itself (discord-bot-plan.md, Step 6a): requests from a
slash command or any message, public feedback sent to the board, and missing
tags filled on new posts.

THE TWO FORUMS
--------------
* `#calc-team-board`, private to @Web Developer: the to-do list. Tags in four
  groups (TYPE, PRIORITY, AREA, STATUS below); no status means "new".
* `#carat-calculator-feedback`, public: what players ask for. A type players
  pick, and a status only moderators and the bot can set.

"Send to team board" links the two: the team gets a copy that links back, and
the public post is marked Under review with a reply, so the person who asked
can see it was heard. It happens once per public post, however many people
click it: the worker claims `promote:<public post id>` on the site first
(views/bot_api.py), the same never-twice table the site's own posts use.

TAGS ARE FOUND BY NAME, NEVER BY ID
-----------------------------------
A tag's id changes if someone deletes and remakes it; its name is what people
see. So the worker reads the forum's tags when it needs them and matches by
name, and a tag that doesn't exist is skipped, never an error.

WHO MAY USE IT
--------------
@Web Developer only (DISCORD_TEAM_ROLE_ID), in our server. Everything the
worker posts has mentions switched off, so a quoted "@everyone" pings nobody.
"""

import logging

import discord
from discord import app_commands

from bot import format as fmt
from bot.api import SiteUnavailable

logger = logging.getLogger(__name__)

TYPE_TAGS = ("Bug", "Feature", "Polish", "Data fix", "Chore", "Brainstorm")
PRIORITY_TAGS = ("High", "Normal", "Low")
AREA_TAGS = ("Planner", "Timeline", "PvP & gallery", "Account", "Admin", "Bot", "General")
STATUS_TAGS = ("Planned", "In progress", "Done", "Won't do")
FEEDBACK_STATUS_TAGS = ("Under review", "Planned", "Done", "Won't do")
# A public post's type, as the team board names it. A question that reaches
# the board is something to think about, hence Brainstorm.
FEEDBACK_TO_TEAM_TYPE = {"Bug": "Bug", "Suggestion": "Feature", "Data correction": "Data fix", "Question": "Brainstorm"}

MAX_TAGS = 5  # Discord's limit per post
TITLE_LIMIT = 100
CONTENT_LIMIT = 2000
QUOTE_LIMIT = 1200
NO_MENTIONS = discord.AllowedMentions.none()


def complete_tags(names):
    """`names` with a priority and an area added where missing (Normal,
    General), never past Discord's five. A post with no type is left alone:
    it's a note, not a task, and guessing its type would be wrong."""
    names = list(dict.fromkeys(names))
    if not any(name in TYPE_TAGS for name in names):
        return names
    for group, default in ((PRIORITY_TAGS, "Normal"), (AREA_TAGS, "General")):
        if len(names) < MAX_TAGS and not any(name in group for name in names):
            names.append(default)
    return names


def resolve_tags(forum, names):
    """The forum's ForumTag objects for `names`, in that order. Missing names
    are skipped and logged: a renamed tag must not stop a post."""
    by_name = {tag.name: tag for tag in forum.available_tags}
    missing = [name for name in names if name not in by_name]
    if missing:
        logger.warning("Forum %s has no tag(s) %s", getattr(forum, "id", "?"), ", ".join(missing))
    return [by_name[name] for name in names if name in by_name][:MAX_TAGS]


def quote(text, limit=QUOTE_LIMIT):
    """`text` as a Discord quote block, cut to `limit`."""
    text = fmt.cut((text or "").strip(), limit) or "(no text)"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def title_from(text, fallback):
    """A post title from the first line of a message."""
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return fmt.cut(first or fallback, TITLE_LIMIT)


def promote_key(thread_id):
    return f"promote:{thread_id}"


class RequestModal(discord.ui.Modal):
    """"Make this a request": a title, the three tag groups, and optional
    details, prefilled from the message."""

    def __init__(self, team, message):
        super().__init__(title="New team board request", timeout=600)
        self.team = team
        self.message = message
        self.name = discord.ui.TextInput(
            default=title_from(message.content, f"Request from {message.author.display_name}"),
            max_length=TITLE_LIMIT,
        )
        self.type = discord.ui.Select(options=[discord.SelectOption(label=name) for name in TYPE_TAGS])
        self.priority = discord.ui.Select(options=[
            discord.SelectOption(label=name, default=name == "Normal") for name in PRIORITY_TAGS
        ])
        self.area = discord.ui.Select(options=[
            discord.SelectOption(label=name, default=name == "General") for name in AREA_TAGS
        ])
        self.details = discord.ui.TextInput(style=discord.TextStyle.paragraph, required=False, max_length=600)
        for text, component in (
            ("Title", self.name), ("Type", self.type), ("Priority", self.priority),
            ("Area", self.area), ("Details (optional)", self.details),
        ):
            self.add_item(discord.ui.Label(text=text, component=component))

    async def on_submit(self, interaction):  # pylint: disable=arguments-differ
        await self.team.submit_request(
            interaction,
            self.message,
            title=self.name.value,
            names=[*self.type.values, *(self.priority.values or ["Normal"]), *(self.area.values or ["General"])],
            details=self.details.value,
        )


class Team:
    """The team-board commands, context menus and the auto-tagger. `get_channel`
    is a coroutine returning a channel by id (the client's cache, else a fetch),
    so the tests can hand in fakes."""

    def __init__(self, api, config, get_channel):
        self.api = api
        self.config = config
        self.get_channel = get_channel

    def refusal(self, interaction):
        if interaction.guild_id != self.config.guild_id:
            return fmt.WRONG_PLACE
        roles = getattr(interaction.user, "roles", None) or ()
        if not any(role.id == self.config.team_role_id for role in roles):
            return fmt.NEED_TEAM_ROLE
        return None

    async def _create(self, title, content, names):
        """Post to the team board; the new thread."""
        forum = await self.get_channel(self.config.team_board_id)
        created = await forum.create_thread(
            name=fmt.cut(title.strip(), TITLE_LIMIT) or "Untitled request",
            content=fmt.cut(content, CONTENT_LIMIT),
            applied_tags=resolve_tags(forum, complete_tags(names)),
            allowed_mentions=NO_MENTIONS,
        )
        return created.thread

    # ── /request ──

    async def request(self, interaction, title, kind, *, priority=None, area=None, details=None):  # pylint: disable=too-many-arguments
        reason = self.refusal(interaction)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        who = fmt.escape(interaction.user.display_name)
        content = f"{(details or '').strip()}\n\n-# Requested by {who} with /request".strip()
        names = [kind, priority or "Normal", area or "General"]
        try:
            thread = await self._create(title, content, names)
        except discord.HTTPException:
            logger.warning("Could not create a /request post", exc_info=True)
            await interaction.followup.send(fmt.BOARD_FAILED, ephemeral=True)
            return
        await interaction.followup.send(fmt.REQUEST_POSTED.format(link=thread.jump_url), ephemeral=True)

    # ── "Make this a request" ──

    async def make_request(self, interaction, message):
        reason = self.refusal(interaction)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        # A form must be the FIRST answer to a click, so no defer before it.
        await interaction.response.send_modal(RequestModal(self, message))

    async def submit_request(self, interaction, message, *, title, names, details):  # pylint: disable=too-many-arguments
        await interaction.response.defer(ephemeral=True, thinking=True)
        author = fmt.escape(message.author.display_name)
        who = fmt.escape(interaction.user.display_name)
        parts = [
            (details or "").strip(),
            quote(message.content),
            f"-# From {author}: {message.jump_url}. Added by {who}.",
        ]
        try:
            thread = await self._create(title, "\n\n".join(part for part in parts if part), names)
        except discord.HTTPException:
            logger.warning("Could not create a request from a message", exc_info=True)
            await interaction.followup.send(fmt.BOARD_FAILED, ephemeral=True)
            return
        await interaction.followup.send(fmt.REQUEST_POSTED.format(link=thread.jump_url), ephemeral=True)

    # ── "Send to team board" ──

    async def send_to_board(self, interaction, message):
        reason = self.refusal(interaction)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        thread = message.channel
        if getattr(thread, "parent_id", None) != self.config.feedback_forum_id:
            await interaction.response.send_message(fmt.ONLY_FEEDBACK, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await self._promote(thread, interaction.user, message)
        await interaction.followup.send(reply, ephemeral=True)

    async def _promote(self, thread, user, message):
        key = promote_key(thread.id)
        try:
            claim = await self.api.claim(key, self.config.team_board_id)
        except SiteUnavailable:
            return fmt.SITE_DOWN
        if claim.status == 409:
            message_id = claim.data.get("message_id")
            if not message_id:
                return fmt.PROMOTE_IN_FLIGHT
            link = f"https://discord.com/channels/{self.config.guild_id}/{message_id}"
            return fmt.ALREADY_ON_BOARD.format(link=link)
        if not claim.ok:
            return fmt.SITE_DOWN

        # A forum post's first message has the post's own id. Used on that
        # message, the menu already handed it over; otherwise read it.
        if message.id == thread.id:
            starter_text = message.content
        else:
            try:
                starter = thread.starter_message or await thread.fetch_message(thread.id)
                starter_text = starter.content
            except discord.HTTPException:
                starter_text = ""
        public_type = next((tag.name for tag in thread.applied_tags if tag.name in FEEDBACK_TO_TEAM_TYPE), None)
        names = [FEEDBACK_TO_TEAM_TYPE.get(public_type, "Feature")]
        content = "\n\n".join([
            f"From <#{self.config.feedback_forum_id}>: {thread.jump_url}",
            quote(starter_text),
            f"-# Sent to the board by {fmt.escape(user.display_name)}.",
        ])
        try:
            team_thread = await self._create(thread.name, content, names)
        except discord.HTTPException:
            logger.warning("Could not create the team copy of %s", thread.id, exc_info=True)
            try:
                await self.api.release(key)
            except SiteUnavailable:
                pass
            return fmt.BOARD_FAILED
        try:
            await self.api.record(key, team_thread.id)
        except SiteUnavailable:
            # The copy exists; only the record is missing, so a second click
            # would make a second copy. Loud in the log so it can be fixed.
            logger.error("Team copy %s of %s posted but not recorded", team_thread.id, thread.id)

        await self._mark_public(thread)
        return fmt.SENT_TO_BOARD.format(link=team_thread.jump_url)

    async def _mark_public(self, thread):
        """Under review on the public post, and a reply its author sees."""
        try:
            forum = await self.get_channel(self.config.feedback_forum_id)
            kept = [tag.name for tag in thread.applied_tags if tag.name not in FEEDBACK_STATUS_TAGS]
            await thread.edit(applied_tags=resolve_tags(forum, [*kept[: MAX_TAGS - 1], "Under review"]))
            await thread.send(fmt.PUBLIC_UNDER_REVIEW, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            logger.warning("Could not mark public post %s", thread.id, exc_info=True)

    # ── New posts on the team board ──

    async def on_thread_create(self, thread):
        """Fill a missing priority and area on a new team post. Returns the
        names it set, or None when it left the post alone."""
        if getattr(thread, "parent_id", None) != self.config.team_board_id:
            return None
        names = [tag.name for tag in thread.applied_tags]
        wanted = complete_tags(names)
        if wanted == names:
            return None
        forum = await self.get_channel(self.config.team_board_id)
        try:
            await thread.edit(applied_tags=resolve_tags(forum, wanted))
        except discord.HTTPException:
            logger.warning("Could not tag new team post %s", thread.id, exc_info=True)
            return None
        return wanted

    # ── Registration ──

    def commands(self):
        """/request plus the two message menus, all guild-scoped."""
        choices = {
            group: [app_commands.Choice(name=name, value=name) for name in names]
            for group, names in (("type", TYPE_TAGS), ("priority", PRIORITY_TAGS), ("area", AREA_TAGS))
        }

        @app_commands.command(name="request", description="Add a request to the team board (team only)")
        @app_commands.describe(
            title="A short title, like the post's name",
            kind="What sort of work it is",
            priority="Normal if left out",
            area="General if left out",
            details="Anything the team should know",
        )
        @app_commands.rename(kind="type")
        @app_commands.choices(kind=choices["type"], priority=choices["priority"], area=choices["area"])
        async def request(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            interaction: discord.Interaction,
            title: app_commands.Range[str, 1, TITLE_LIMIT],
            kind: app_commands.Choice[str],
            priority: app_commands.Choice[str] | None = None,
            area: app_commands.Choice[str] | None = None,
            details: app_commands.Range[str, 0, 1500] | None = None,
        ):
            await self.request(
                interaction, title, kind.value,
                priority=priority.value if priority else None, area=area.value if area else None, details=details,
            )

        async def make_request(interaction: discord.Interaction, message: discord.Message):
            await self.make_request(interaction, message)

        async def send_to_board(interaction: discord.Interaction, message: discord.Message):
            await self.send_to_board(interaction, message)

        return [
            request,
            app_commands.ContextMenu(name="Make this a request", callback=make_request),
            app_commands.ContextMenu(name="Send to team board", callback=send_to_board),
        ]
