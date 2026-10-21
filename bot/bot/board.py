"""
The team board's order and its echoes (discord-bot-plan.md, Step 6b): the
ordered queue, the pinned "Up next" message in #calc-ops, the Monday summary,
and the status mirror back to the public post a team copy came from.

THE QUEUE FOLLOWS THE TAGS
--------------------------
The order lives on the site (TeamQueueEntry); WHAT is in it comes from the
board's status tags, so the team never maintains two lists:

    tagged Planned or In progress    -> in the queue (new ones join the bottom)
    tagged Done or Won't do, or none -> out of it (the rest close up)

The worker sees each tag change as it happens (on_thread_update). Because a
restart or an outage can miss one, it also repairs the queue against the
board on start and every hour (`reconcile`), keeping every post's place.

UP NEXT
-------
One bot message, pinned in #calc-ops (the board's own pin is "How this board
works"): Now (In progress), Next (the numbered queue), then the backlog by
priority. Edited in place on every change. Its id is recorded on the site
under `upnext:<channel>`, so a restart edits the same message, and a deleted
one is simply posted again.

THE MIRROR
----------
A team copy made by "Send to team board" carries the public post it came from
(the site's `promote:<public post>` record, whose message is the copy). When
the copy's status changes, the public post's status follows: Planned or In
progress show as Planned, Done as Done, Won't do as Won't do, with one short
reply each time the public status actually changes.
"""

import asyncio
import datetime
import logging
import time

import discord
from discord import app_commands

from bot import format as fmt
from bot.api import SiteUnavailable
from bot.team import FEEDBACK_STATUS_TAGS, MAX_TAGS, NO_MENTIONS, PRIORITY_TAGS, TYPE_TAGS, resolve_tags

logger = logging.getLogger(__name__)

QUEUED = ("Planned", "In progress")
FINISHED = ("Done", "Won't do")
PRIORITY_EMOJI = {"High": "🟠", "Normal": "🟡", "Low": "⚪"}
PUBLIC_STATUS = {"Planned": "Planned", "In progress": "Planned", "Done": "Done", "Won't do": "Won't do"}
PUBLIC_REPLY = {"Planned": fmt.PUBLIC_PLANNED, "Done": fmt.PUBLIC_DONE, "Won't do": fmt.PUBLIC_WONT}
DESCRIPTION_LIMIT = 4096
NEW_POST_DAYS = 7
TITLES_CACHE_SECONDS = 60


def tag_names(thread):
    return [tag.name for tag in thread.applied_tags]


def status_of(thread):
    """The post's status tag, or None for a new post."""
    return next((name for name in tag_names(thread) if name in (*QUEUED, *FINISHED)), None)


def priority_of(thread):
    return next((name for name in tag_names(thread) if name in PRIORITY_TAGS), None)


def is_task(thread):
    """A post with a type tag. Notes like "How this board works" have none."""
    return any(name in TYPE_TAGS for name in tag_names(thread))


def created(thread):
    return discord.utils.snowflake_time(thread.id)


def link(thread):
    """A markdown link to the post. Brackets in a title would end the link
    text early, so they become parentheses."""
    title = thread.name.replace("[", "(").replace("]", ")")
    return f"[{title}]({thread.jump_url})"


def queue_order(threads):
    """The posts that belong in the queue, oldest first: the order new ones
    join in when the queue is rebuilt."""
    return [thread.id for thread in sorted(threads, key=lambda t: t.id) if status_of(thread) in QUEUED]


def fit(lines, limit=DESCRIPTION_LIMIT):
    """Join lines, dropping from the end until it fits, with a closing note."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    for keep in range(len(lines) - 1, -1, -1):
        text = "\n".join([*lines[:keep], f"…and {len(lines) - keep} more lines on the board"])
        if len(text) <= limit:
            return text
    return text[:limit]


def render_up_next(threads, queue_ids):
    """The Up next embed: Now, Next (numbered by the site's queue), Backlog."""
    by_id = {thread.id: thread for thread in threads}
    queued = [by_id[int(tid)] for tid in queue_ids if int(tid) in by_id]

    def line(thread):
        emoji = PRIORITY_EMOJI.get(priority_of(thread), "")
        return f"{link(thread)} {emoji}".rstrip()

    now = [thread for thread in queued if status_of(thread) == "In progress"]
    upcoming = [thread for thread in queued if status_of(thread) == "Planned"]
    backlog = sorted(
        (thread for thread in threads if status_of(thread) is None and is_task(thread)),
        key=lambda t: (PRIORITY_TAGS.index(priority_of(t)) if priority_of(t) else len(PRIORITY_TAGS), t.id),
    )
    lines = ["**Now**"]
    lines += [f"🔨 {line(thread)}" for thread in now] or ["Nothing in progress."]
    lines += ["", "**Next**"]
    lines += [f"{number}. {line(thread)}" for number, thread in enumerate(upcoming, start=1)] or [fmt.NOTHING_NEXT]
    lines += ["", f"**Backlog** ({len(backlog)})"]
    last_priority = object()
    for thread in backlog:
        priority = priority_of(thread)
        if priority != last_priority:
            lines.append(f"{PRIORITY_EMOJI.get(priority, '')} {priority or 'No priority'}".strip())
            last_priority = priority
        lines.append(f"- {link(thread)}")
    embed = discord.Embed(title="Up next", description=fit(lines), colour=0xE6D28A)
    embed.set_footer(text=fmt.UP_NEXT_FOOTER)
    embed.timestamp = discord.utils.utcnow()
    return embed


def render_weekly(threads, today):
    """The Monday summary: counts per status, and the posts that arrived in
    the last week."""
    tasks = [thread for thread in threads if is_task(thread)]
    counts = {status: 0 for status in ("New", *QUEUED, *FINISHED)}
    for thread in tasks:
        counts[status_of(thread) or "New"] += 1
    since = datetime.datetime.combine(today, datetime.time(), tzinfo=datetime.timezone.utc) - datetime.timedelta(
        days=NEW_POST_DAYS
    )
    arrived = sorted((t for t in tasks if created(t) >= since), key=lambda t: t.id)
    lines = [" · ".join(f"{status}: {count}" for status, count in counts.items()), ""]
    lines.append(f"**New this week** ({len(arrived)})")
    lines += [f"- {link(thread)}" for thread in arrived] or ["Nothing new."]
    return discord.Embed(title=f"Team board, week of {today.isoformat()}", description=fit(lines), colour=0xE6D28A)


class Board:
    """The queue, Up next, the Monday summary and the mirror. `get_channel`
    returns a channel by id; `list_threads` returns every team-board post,
    open and archived. Both are coroutines the client provides."""

    def __init__(self, api, config, get_channel, list_threads):
        self.api = api
        self.config = config
        self.get_channel = get_channel
        self.list_threads = list_threads
        self._refresh_lock = asyncio.Lock()
        self._titles = (0.0, {})

    def _on_board(self, thread):
        return getattr(thread, "parent_id", None) == self.config.team_board_id

    # ── Keeping the queue in step with the tags ──

    async def on_thread_create(self, thread):
        if not self._on_board(thread):
            return
        if status_of(thread) in QUEUED:
            await self._queue_call(self.api.queue_add, thread.id)
        await self.refresh()

    async def on_thread_update(self, before, after):
        """A post's tags or title changed. Returns the new status when it moved."""
        if not self._on_board(after):
            return None
        old, new = status_of(before), status_of(after)
        moved = old != new
        if moved:
            if new in QUEUED and old not in QUEUED:
                await self._queue_call(self.api.queue_add, after.id)
            elif old in QUEUED and new not in QUEUED:
                await self._queue_call(self.api.queue_remove, after.id)
            await self.mirror(after, new)
        if moved or before.name != after.name or priority_of(before) != priority_of(after):
            await self.refresh()
        return new if moved else None

    async def on_thread_delete(self, thread):
        if self._on_board(thread):
            await self._queue_call(self.api.queue_remove, thread.id)
            await self.refresh()

    async def _queue_call(self, call, *args):
        try:
            return await call(*args)
        except SiteUnavailable:
            # The hourly reconcile repairs whatever this missed.
            return None

    async def reconcile(self):
        """Rebuild the queue from the board's tags, keeping every place, then
        refresh Up next. On start and every hour."""
        threads = await self.list_threads()
        await self._queue_call(self.api.queue_sync, queue_order(threads))
        await self.refresh(threads)

    # ── Up next ──

    async def refresh(self, threads=None):
        """Bring the pinned Up next message up to date (one at a time)."""
        async with self._refresh_lock:
            try:
                threads = threads if threads is not None else await self.list_threads()
                answer = await self.api.queue()
            except SiteUnavailable:
                return
            self._titles = (time.monotonic(), {thread.id: thread.name for thread in threads})
            embed = render_up_next(threads, answer.data.get("thread_ids", []))
            await self._publish_up_next(embed)

    async def _publish_up_next(self, embed):
        ops = await self.get_channel(self.config.ops_channel_id)
        key = f"upnext:{self.config.ops_channel_id}"
        try:
            claim = await self.api.claim(key, self.config.ops_channel_id)
        except SiteUnavailable:
            return
        message_id = claim.data.get("message_id") if claim.status == 409 else None
        if message_id:
            try:
                await ops.get_partial_message(int(message_id)).edit(embed=embed)
                return
            except discord.NotFound:
                logger.info("Up next message %s is gone; posting a new one", message_id)
        message = await ops.send(embed=embed, allowed_mentions=NO_MENTIONS)
        try:
            await message.pin(reason="The team board's Up next")
        except discord.HTTPException:
            logger.warning("Could not pin Up next in %s (needs Pin Messages)", self.config.ops_channel_id)
        try:
            await self.api.record(key, message.id)
        except SiteUnavailable:
            logger.error("Up next %s posted but not recorded", message.id)

    # ── The Monday summary ──

    async def weekly(self, today):
        """Post the week's summary once, on a Monday. Returns whether it posted."""
        if today.weekday() != 0:
            return False
        key = f"weekly:{today:%Y%m%d}"
        try:
            claim = await self.api.claim(key, self.config.ops_channel_id)
        except SiteUnavailable:
            return False
        if claim.status != 201:
            return False
        threads = await self.list_threads()
        ops = await self.get_channel(self.config.ops_channel_id)
        message = await ops.send(embed=render_weekly(threads, today), allowed_mentions=NO_MENTIONS)
        try:
            await self.api.record(key, message.id)
        except SiteUnavailable:
            logger.error("Weekly summary %s posted but not recorded", message.id)
        return True

    # ── The mirror ──

    async def mirror(self, team_thread, status):
        """Echo a team copy's new status onto the public post it came from.
        Returns the public status set, or None when nothing changed."""
        public_status = PUBLIC_STATUS.get(status)
        if public_status is None:
            return None
        try:
            record = await self.api.find_record(team_thread.id)
        except SiteUnavailable:
            return None
        key = record.data.get("key", "") if record.ok else ""
        if not key.startswith("promote:"):
            return None
        try:
            public = await self.get_channel(int(key.split(":", 1)[1]))
            names = tag_names(public)
            if public_status in names:
                return None
            kept = [name for name in names if name not in FEEDBACK_STATUS_TAGS][: MAX_TAGS - 1]
            forum = await self.get_channel(self.config.feedback_forum_id)
            await public.edit(archived=False, applied_tags=resolve_tags(forum, [*kept, public_status]))
            await public.send(PUBLIC_REPLY[public_status], allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            logger.warning("Could not mirror %s onto %s", status, key, exc_info=True)
            return None
        return public_status

    # ── /queue ──

    def refusal(self, interaction):
        if interaction.guild_id != self.config.guild_id:
            return fmt.WRONG_PLACE
        roles = getattr(interaction.user, "roles", None) or ()
        if not any(role.id == self.config.team_role_id for role in roles):
            return fmt.NEED_TEAM_ROLE
        return None

    async def _guard(self, interaction):
        reason = self.refusal(interaction)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True

    async def show(self, interaction):
        if not await self._guard(interaction):
            return
        try:
            threads = await self.list_threads()
            answer = await self.api.queue()
        except SiteUnavailable:
            await interaction.followup.send(fmt.SITE_DOWN, ephemeral=True)
            return
        embed = render_up_next(threads, answer.data.get("thread_ids", []))
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def move(self, interaction, post, position):
        if not await self._guard(interaction):
            return
        if not str(post).isdigit():
            await interaction.followup.send(fmt.NOT_QUEUED, ephemeral=True)
            return
        try:
            answer = await self.api.queue_move(post, position)
        except SiteUnavailable:
            await interaction.followup.send(fmt.SITE_DOWN, ephemeral=True)
            return
        if answer.status == 404:
            await interaction.followup.send(fmt.NOT_QUEUED, ephemeral=True)
            return
        ids = answer.data.get("thread_ids", [])
        await interaction.followup.send(fmt.MOVED.format(position=ids.index(str(post)) + 1), ephemeral=True)
        await self.refresh()

    async def suggest(self, current):
        """Autocomplete: queued posts whose title contains what's typed."""
        stamp, titles = self._titles
        if time.monotonic() - stamp > TITLES_CACHE_SECONDS:
            try:
                titles = {thread.id: thread.name for thread in await self.list_threads()}
            except discord.HTTPException:
                titles = {}
            self._titles = (time.monotonic(), titles)
        try:
            ids = (await self.api.queue()).data.get("thread_ids", [])
        except SiteUnavailable:
            return []
        typed = (current or "").lower()
        return [
            app_commands.Choice(name=f"{number}. {titles.get(int(tid), tid)}"[:100], value=tid)
            for number, tid in enumerate(ids, start=1)
            if typed in titles.get(int(tid), "").lower()
        ][:25]

    def commands(self):
        group = app_commands.Group(name="queue", description="The team board's order (team only)")

        async def autocomplete(interaction: discord.Interaction, current: str):  # pylint: disable=unused-argument
            return await self.suggest(current)

        @group.command(name="show", description="Show Up next: in progress, the numbered queue, the backlog")
        async def show(interaction: discord.Interaction):
            await self.show(interaction)

        @group.command(name="move", description="Move a queued post to a position (1 is the top)")
        @app_commands.describe(post="A queued post", position="1 is the top")
        @app_commands.autocomplete(post=autocomplete)
        async def move(interaction: discord.Interaction, post: str, position: app_commands.Range[int, 1, 500]):
            await self.move(interaction, post, position)

        @group.command(name="top", description="Move a queued post to the top")
        @app_commands.describe(post="A queued post")
        @app_commands.autocomplete(post=autocomplete)
        async def top(interaction: discord.Interaction, post: str):
            await self.move(interaction, post, 1)

        return group
