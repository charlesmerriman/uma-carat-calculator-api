"""
Everything the worker says, in one place: the replies a moderator sees and
the embeds for /mod. Plain voice on purpose: moderation, errors and anything
touching privacy stay out of the bot's persona (discord-bot-plan.md, "Persona").
The few friendly lines that DO carry it are defined in bot/persona.py and
re-exported here, so callers keep reading fmt.<NAME>.

The site's copy rules apply here too (.claude/rules/copy.md): no em dashes,
and it should read as a person wrote it. A test scans this file for dashes.

Names and user text come from the site plain. They are escaped here, so a
display name can't bold itself, link somewhere or ping a role, and reported
text sits in a code block the way it does on the site's own cards.
"""

import discord

from bot import persona

COLOUR_OPEN = 0xE5484D
COLOUR_DONE = 0x6B7280
TEXT_LIMIT = 1000
FIELD_LIMIT = 1024

NEED_ROLE = "You need the Moderator role to do that."
WRONG_PLACE = "That only works in the moderators' channel of our server."
SITE_DOWN = "The site didn't answer. Try again in a minute."
GONE = "That post or comment isn't there any more."
BAD_ID = "That doesn't look like a post link or id."
DONE = {
    "keep": "Kept. It's visible again and the card is updated.",
    "remove": "Removed. It's hidden and the card is updated.",
    "block": "Removed, and its author can't post any more. Undo the block with /mod unblock.",
}
CONFIRM_BLOCK = (
    "Block this author? They won't be able to post, comment, vote or report, and "
    "everything they've posted is hidden until a moderator unblocks them."
)
CONFIRM_LABEL = "Yes, block"
BLOCKING = "Blocking..."
UNBLOCKED = "Unblocked. They can post again; this post stays as it was decided."
NOT_BLOCKED = "Its author isn't blocked."
TERM_ADDED = "Added. Captions, comments and display names with \"{term}\" are refused from now on."
TERM_EXISTS = "That term is already blocked."
NO_REPORTS = "Nothing is waiting. No open reports."
UNDONE = "Undone. Its reports are open again, so pick again on the card."
NOTHING_TO_UNDO = "Nothing to undo: it's already open."

# The team board.
NEED_TEAM_ROLE = "That's for the Web Developer team."
ONLY_FEEDBACK = "That only works on a post in the carat calculator feedback forum."
BOARD_FAILED = "Discord didn't take the post. Try again in a minute."
REQUEST_POSTED = persona.REQUEST_POSTED
SENT_TO_BOARD = "On the team board now: {link}. The public post is marked Under review."
ALREADY_ON_BOARD = "It's already on the team board: {link}"
PROMOTE_IN_FLIGHT = "Someone is sending it right now. Check the team board in a moment."
PUBLIC_UNDER_REVIEW = persona.PUBLIC_UNDER_REVIEW

# The board's queue (/queue) and the Monday summary.
NOT_QUEUED = "That post isn't in the queue. Tag it Planned on the team board first."
MOVED = "Moved to number {position}."
QUEUE_FOOTER = "Tag a team post Planned to add it. Reorder with /queue move."
NOTHING_NEXT = "Nothing is planned yet."
# What the public post that a team copy came from is told when the copy moves.
# In the bot's voice (bot/persona.py).
PUBLIC_PLANNED = persona.PUBLIC_PLANNED
PUBLIC_DONE = persona.PUBLIC_DONE
PUBLIC_WONT = persona.PUBLIC_WONT

RESOLUTIONS = {"kept": "kept", "removed": "removed", "blocked": "removed (author blocked)"}


def escape(text):
    """User text as plain text in Discord: no markdown, no mentions."""
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text or ""))


def cut(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def quoted(text):
    """User text in a code block. Three backticks would close it early."""
    text = (text or "").replace("```", "`\u200b``").strip() or "(empty)"
    return f"```\n{cut(text, TEXT_LIMIT)}\n```"


def person(who):
    if not who:
        return "an account that was deleted"
    name = escape(who.get("display_name")) or "no display name"
    return f"{name} ({escape(who.get('handle'))})"


def already_handled(data):
    """The 409 reply: who got there first, when the site knows."""
    resolution = RESOLUTIONS.get(data.get("resolution"), "handled")
    who = data.get("handled_by")
    if who:
        return f"Already {resolution} by {escape(who)}."
    return "Nothing to decide: it has no open reports."


def item_embed(item):
    """One item for /mod post, laid out like the site's open card."""
    kind = item.get("kind", "post")
    embed = discord.Embed(
        title=f"{kind.capitalize()} reported" if item.get("open_reports") else kind.capitalize(),
        url=item.get("url"),
        description=quoted(item.get("text")),
        colour=COLOUR_OPEN if item.get("open_reports") else COLOUR_DONE,
    )
    embed.add_field(name="Author", value=person(item.get("author")), inline=True)
    status = item.get("status", "")
    if item.get("author_blocked"):
        status += ". Author blocked"
    embed.add_field(name="Status", value=status or "Unknown", inline=True)
    reports = item.get("open_reports") or []
    if reports:
        lines = []
        for report in reports:
            line = f"**{escape(report.get('reason'))}**, by {person(report.get('reporter'))}"
            if report.get("note"):
                line += f': "{escape(report["note"])}"'
            lines.append(line)
        count = len(reports)
        embed.add_field(
            name=f"{count} open report{'' if count == 1 else 's'}",
            value=cut("\n".join(lines), FIELD_LIMIT),
            inline=False,
        )
    elif item.get("decided"):
        decided = item["decided"]
        outcome = RESOLUTIONS.get(decided.get("resolution"), "decided")
        embed.add_field(name="Decided", value=f"{outcome.capitalize()} by {escape(decided.get('by'))}", inline=False)
    return embed


def reports_embed(data):
    """The /mod reports list: one line per item, newest report first."""
    items = data.get("items") or []
    lines = []
    for entry in items:
        reports = entry.get("open_reports", 0)
        hidden = ", hidden" if entry.get("is_hidden") else ""
        lines.append(
            f"`{entry.get('kind')} {entry.get('ref')}`: {reports} report{'' if reports == 1 else 's'}{hidden}. "
            f"{escape(entry.get('preview'))}"
        )
    total = data.get("total", len(items))
    embed = discord.Embed(
        title=f"{total} item{'' if total == 1 else 's'} waiting",
        description=cut("\n".join(lines), 4096),
        colour=COLOUR_OPEN,
    )
    if total > len(items):
        embed.set_footer(text=f"Showing the newest {len(items)}. The rest are in the admin.")
    return embed
