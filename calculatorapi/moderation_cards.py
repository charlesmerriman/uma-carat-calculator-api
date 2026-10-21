"""
Moderation cards: one Discord message per reported post or comment, in the
moderators' private #calc-mod-queue, kept up to date as the item's reports move.

WHAT A CARD SHOWS, AND WHEN
---------------------------
* **Open** (at least one report waiting): the reported text, the author's
  display name, whether it is hidden, and every open report with its reason,
  note, and who made it (display name and account handle). Links to the post and
  to the admin's report queue. While BOT_API_SECRET is set, three buttons too:
  Keep, Remove, Block author, which the bot worker answers through
  views/bot_api.py. Without it the card has no buttons and the decision is made
  in the admin: a button nothing listens to would answer "This interaction
  failed", so the secret goes in with the worker (discord-bot-plan.md, Step 4).
* **Decided** (every report resolved): ONE line, "Comment reported 2 times:
  removed by Zac on 2026/10/20", or "by name (Discord)" for a click on the card.
  No text, no names but the moderator's, no buttons. The full record stays in
  the admin; Discord keeps only the outcome (owner's call, 2026-10-20).
* **Deleted** (the author took it down, deleted their account, or staff deleted
  it): "Post reported, then deleted." Nothing else.

A new report on a decided item opens its card again. Every change EDITS the one
card (key `card:post:<id>` / `card:comment:<id>`, discord_messages' never-twice
rule); only an item's first open report sends.

WHY SO LITTLE STAYS IN DISCORD
------------------------------
The admin is our database; a Discord channel is somebody else's server, which
keeps what we send. So the text and the names are there only while a moderator
needs them to decide, and an account deletion takes them back out: the author's
items through the delete hooks, a reporter's name by rebuilding the card
without it. Never on a card: an email, a provider, a linked account, a user id.
The privacy policy's "Reports" bullet describes this; change both together.

User text is shown inside a code block and names are markdown-escaped, so a
caption can't render as a link or a heading in the moderators' channel.
"""

import re

from django.conf import settings
from django.db.models.signals import post_delete
from django.urls import reverse

from calculatorapi import discord_messages
from calculatorapi.models import GalleryComment, GalleryHiddenReason, GalleryPost, GalleryReportResolution

CONTENT_LIMIT = 1000
FIELD_LIMIT = 1024
OPEN_COLOUR = 0xE5484D
DONE_COLOUR = 0x6B7280


def card_key(kind, item_id):
    return f"card:{kind}:{item_id}"


def _kind(item):
    return "post" if isinstance(item, GalleryPost) else "comment"


def item_ref(item):
    """(kind, ref) as the bot names an item: a post by its public id, the one
    a moderator can read in its link, and a comment by its row id, since a
    comment has no public id. Button ids and the bot API's routes both use it."""
    if isinstance(item, GalleryPost):
        return "post", item.public_id
    return "comment", str(item.pk)


def buttons_enabled():
    """Cards carry buttons only while the bot API they lead to is switched on."""
    return bool(settings.BOT_API_SECRET)


def _buttons(item):
    """Keep / Remove / Block author. The custom id is the whole instruction,
    `mod:<action>:<kind>:<ref>` (at most 100 characters; ours stay under 40),
    so the worker needs nothing but the click to act. Block asks again in the
    worker before it calls the site (decision 5)."""
    kind, ref = item_ref(item)
    styles = (("keep", "Keep", 3), ("remove", "Remove", 4), ("block", "Block author", 4))
    return [{
        "type": 1,  # an action row
        "components": [
            {"type": 2, "style": style, "label": label, "custom_id": f"mod:{action}:{kind}:{ref}"}
            for action, label, style in styles
        ],
    }]


def decided_by(report, *, markdown=False):
    """Who closed `report`: a staff member's display name, a Discord
    moderator's name with "(Discord)", or "a moderator" when neither is known
    (a staff account with no display name, or one since deleted). Never a
    staff handle: those name admin logins. `markdown` escapes the name for a
    card; the bot API sends it plain and the worker escapes its own."""
    escape = _escape if markdown else (lambda text: text)
    if report.resolved_by_discord:
        # Stored "name (id)"; the id is for the record, the name for people.
        return f"{escape(report.resolved_by_discord.rsplit(' (', 1)[0])} (Discord)"
    moderator = report.resolved_by
    if moderator and moderator.display_name:
        return escape(moderator.display_name)
    return "a moderator"


def _channel():
    return settings.DISCORD_MOD_QUEUE_CHANNEL_ID


def _escape(text):
    """Display names and notes as plain text: no bold, links or mentions."""
    return re.sub(r"([\\*_`~|>\[\]()#<@-])", r"\\\1", text or "")


def _quoted(text):
    """User text in a code block, cut to fit. Three backticks in the text
    would close the block early, so they are broken up first."""
    text = (text or "").replace("```", "`\u200b``").strip() or "(empty)"
    if len(text) > CONTENT_LIMIT:
        text = text[: CONTENT_LIMIT - 1].rstrip() + "…"
    return f"```\n{text}\n```"


def _person(user):
    if user is None:
        return "an account that was deleted"
    name = _escape(user.display_name) if user.display_name else "no display name"
    return f"{name} ({user.username})"


def _site_date(moment):
    return f"{moment.year}/{moment.month}/{moment.day}"


def item_text(item):
    """The reported text as written: caption and strategy, or the comment."""
    if isinstance(item, GalleryPost):
        parts = [
            f"Caption: {item.caption}" if item.caption else "",
            f"Strategy: {item.strategy}" if item.strategy else "",
        ]
        return "\n\n".join(part for part in parts if part) or "(a team with no caption or strategy)"
    if item.is_deleted:
        return "(deleted by its author)"
    return item.body


def status_text(item):
    if not item.is_hidden:
        return "Visible"
    if item.hidden_reason == GalleryHiddenReason.REPORTS:
        return "Hidden after reports, waiting for a moderator"
    return "Hidden by a moderator"


def _links(item):
    post = item if isinstance(item, GalleryPost) else item.post
    site = settings.FRONTEND_URL.rstrip("/")
    admin = site + reverse("admin:calculatorapi_galleryreport_changelist")
    return f"[View the post]({site}/post/{post.public_id}) · [Open in admin]({admin})"


def _open_card(item, open_reports):
    kind = _kind(item)
    lines = []
    for report in open_reports:
        line = f"**{report.get_reason_display()}**, by {_person(report.reporter)}"
        if report.note:
            line += f': "{_escape(report.note)}"'
        lines.append(line)
    reports = discord_messages.cut_lines(lines, FIELD_LIMIT, lambda hidden: f"…and {hidden} more in the admin")
    count = len(open_reports)
    return {
        "title": f"{kind.capitalize()} reported",
        "color": OPEN_COLOUR,
        "description": _quoted(item_text(item)),
        "fields": [
            {"name": "Author", "value": _person(item.author) if item.author else "deleted", "inline": True},
            {"name": "Status", "value": status_text(item), "inline": True},
            {"name": f"{count} open report{'' if count == 1 else 's'}", "value": reports, "inline": False},
            {"name": "Links" if buttons_enabled() else "Decide", "value": _links(item), "inline": False},
        ],
    }


def _decided_card(item, reports):
    last = max(reports, key=lambda report: report.resolved_at)
    who = decided_by(last, markdown=True)
    if last.resolution == GalleryReportResolution.KEPT:
        outcome = "kept"
    elif item.author is not None and item.author.publishing_blocked:
        outcome = "removed and its author blocked"
    else:
        outcome = "removed"
    times = f"{len(reports)} time{'' if len(reports) == 1 else 's'}"
    return {
        "title": f"{_kind(item).capitalize()} reported {times}: {outcome} by {who} on {_site_date(last.resolved_at)}",
        "color": DONE_COLOUR,
    }


def refresh(item):
    """Bring the item's card up to date: send it on the first open report,
    edit it on every change after. An item never reported has no card."""
    channel = _channel()
    if not discord_messages.is_configured(channel):
        return
    reports = list(item.reports.select_related("reporter", "resolved_by").order_by("created_at"))
    open_reports = [report for report in reports if report.resolved_at is None]
    key = card_key(_kind(item), item.pk)
    if open_reports:
        embed = _open_card(item, open_reports)
        components = _buttons(item) if buttons_enabled() else []
    elif reports:
        embed = _decided_card(item, reports)
        components = []
    else:
        return
    if discord_messages.has_message(key):
        discord_messages.edit(key, embed=embed, components=components)
    elif open_reports:
        discord_messages.send(key, channel, embed=embed, components=components)


def mark_deleted(kind, item_id):
    """Strip an item's card down to "reported, then deleted". A no-op when it
    never had one, which is most items."""
    if not settings.DISCORD_BOT_TOKEN:
        return
    key = card_key(kind, item_id)
    if discord_messages.has_message(key):
        discord_messages.edit(
            key,
            embed={"title": f"{kind.capitalize()} reported, then deleted", "color": DONE_COLOUR},
            components=[],
        )


# Module-level receivers: Signal.connect() holds only a WEAK reference, so a
# receiver defined inside connect_signals() would be garbage collected and
# silently stop firing (the bug public_payload_cache.py describes).
def _post_deleted(sender, instance, **kwargs):  # pylint: disable=unused-argument
    mark_deleted("post", instance.pk)


def _comment_deleted(sender, instance, **kwargs):  # pylint: disable=unused-argument
    mark_deleted("comment", instance.pk)


def connect_signals():
    """Every deletion strips its card: the author's take-down, account
    deletion, the purge, a staff delete in the admin, and a post's comments
    going with it. Called from CalculatorapiConfig.ready()."""
    post_delete.connect(_post_deleted, sender=GalleryPost, dispatch_uid="moderation-card-post")
    post_delete.connect(_comment_deleted, sender=GalleryComment, dispatch_uid="moderation-card-comment")
