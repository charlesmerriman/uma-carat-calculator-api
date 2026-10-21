"""
The site's voice in Discord: send a message as the bot, or edit one it sent.

Named discord_messages, not discord: a module called discord.py would shadow
the discord.py library the bot worker imports.

HOW IT TALKS TO DISCORD
-----------------------
Plain REST with the bot token (`Authorization: Bot <token>`), through
`requests`, the way oauth.py and patreon_api.py reach their providers. No
library and no open connection: posting and editing a message are single HTTP
calls, and the always-on Gateway connection belongs to the bot worker, never
to Django (discord-bot-plan.md, decision 2).

The bot token is used rather than a webhook because Discord only lets the
application that SENT a message receive clicks on its buttons, and the
moderation cards carry buttons the bot worker answers. A webhook message can
never have them.

THE CONTRACT, SAME AS gallery_moderation.alert
----------------------------------------------
* Off while DISCORD_BOT_TOKEN or the channel id is blank: locally, in tests,
  and in production until both are set. No call is made at all.
* After the transaction commits (`transaction.on_commit`), so a message never
  announces a write that was rolled back. Outside a transaction (a management
  command) that is immediately.
* Short timeouts, one retry on a 429, and every failure logged and swallowed.
  Discord being slow or down must never fail a deploy or a request.
* **Never twice for one key** (models/discord_message.py). `send` claims the
  key before posting; a key that already has a row is skipped.
"""

import logging
import time

import requests
from django.conf import settings
from django.db import IntegrityError, transaction

from calculatorapi.models import DiscordMessage

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
TIMEOUT_SECONDS = 3
# Discord answers a rate-limited call with 429 and how long to wait. We wait
# once, and only this long at most: a deploy or a request blocked for longer
# than that is worse than a missed message, which the next run can still send.
MAX_RETRY_WAIT_SECONDS = 3

# Discord's own limits (https://docs.discord.com/developers, checked 2026-10-14).
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_TITLE_LIMIT = 256

# Nothing the site posts pings anyone unless the caller names the role: a typed
# "@everyone" in a patch note stays plain text. Explicit even though the bot may
# hold Mention All in the server (owner's choice, 2026-10-20).
def _allowed_mentions(ping_roles=()):
    return {"parse": [], "roles": [str(role) for role in ping_roles]}


def is_configured(channel_id=""):
    """Whether the site can post: a token, and the channel it would post to."""
    return bool(settings.DISCORD_BOT_TOKEN and channel_id)


def _headers():
    return {
        "Authorization": f"Bot {settings.DISCORD_BOT_TOKEN}",
        # Discord asks bots to name themselves; requests' default is refused
        # by some of its edge rules.
        "User-Agent": "UmaCaratCalculator (https://umacaratcalculator.com, 1.0)",
    }


def _call(method, path, body):
    """One REST call with a single 429 retry. Returns the parsed JSON on a 2xx,
    None on anything else (already logged)."""
    url = f"{API_BASE}{path}"
    for attempt in (1, 2):
        try:
            response = requests.request(
                method, url, json=body, headers=_headers(), timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            logger.warning("Discord %s %s failed", method, path, exc_info=True)
            return None
        if response.status_code == 429 and attempt == 1:
            try:
                wait = float(response.json().get("retry_after", 1))
            except (ValueError, AttributeError):
                wait = 1.0
            time.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
            continue
        if response.ok:
            return response.json()
        # The body is Discord's error ("Missing Access", "Unknown Channel"),
        # never our token, so it is safe and useful in the log.
        logger.warning(
            "Discord %s %s answered %s: %s",
            method, path, response.status_code, response.text[:300],
        )
        return None
    return None


def _payload(content, embed, ping_roles=(), components=None):
    body = {"allowed_mentions": _allowed_mentions(ping_roles)}
    if content is not None:
        body["content"] = content
    if embed is not None:
        body["embeds"] = [embed]
    # Button rows. None leaves a message's buttons as they are on an edit;
    # an empty list takes them all off.
    if components is not None:
        body["components"] = components
    return body


def has_message(key):
    """Whether anything is recorded under `key`: sent, or a claim in flight."""
    return DiscordMessage.objects.filter(key=key).exists()


def send(key, channel_id, *, content=None, embed=None, ping_roles=(), components=None):  # pylint: disable=too-many-arguments
    """Post a message under `key`, once ever. Returns nothing: the post happens
    after the transaction commits.

    `ping_roles` are the role ids the message may ping; any other mention in
    `content` renders but notifies nobody.

    The claim (inserting the row) happens inside the deferred callback, not
    here, so a rolled-back transaction leaves no orphaned claim behind.
    """
    if not is_configured(channel_id) or has_message(key):
        return

    def post():
        try:
            # The unique key is the lock: of two runs racing to post the same
            # thing, exactly one gets the row.
            with transaction.atomic():
                claim = DiscordMessage.objects.create(key=key, channel_id=str(channel_id))
        except IntegrityError:
            return
        sent = _call(
            "POST", f"/channels/{channel_id}/messages",
            _payload(content, embed, ping_roles, components),
        )
        if sent is None:
            # Give the key back, so the next run (the next deploy, for a
            # changelog entry) tries again rather than believing it was sent.
            claim.delete()
            return
        claim.message_id = str(sent["id"])
        claim.save(update_fields=["message_id", "updated_at"])

    transaction.on_commit(post)


def edit(key, *, content=None, embed=None, components=None):
    """Replace the text of the message sent under `key`. A key never sent, or
    still in flight, is left alone: there is nothing to edit yet."""
    if not settings.DISCORD_BOT_TOKEN:
        return
    row = DiscordMessage.objects.filter(key=key).exclude(message_id="").first()
    if row is None:
        return

    def patch():
        if _call(
            "PATCH",
            f"/channels/{row.channel_id}/messages/{row.message_id}",
            _payload(content, embed, components=components),
        ) is not None:
            row.save(update_fields=["updated_at"])

    transaction.on_commit(patch)


def cut_lines(lines, limit, more_text):
    """Join `lines` with newlines, dropping whole lines from the end until the
    text and a closing `more_text(n)` line fit in `limit` characters. A line is
    never cut in half: a patch note that stops mid-word reads as broken."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    for keep in range(len(lines) - 1, -1, -1):
        tail = more_text(len(lines) - keep)
        text = "\n".join([*lines[:keep], tail])
        if len(text) <= limit:
            return text
    return more_text(len(lines))[:limit]
