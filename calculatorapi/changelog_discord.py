"""
Patch notes in Discord's #changelog: one post per entry, written on deploy.

`sync_changelog` calls `announce()` after it has written the file into the
database. It runs on every deploy (backend/.do/app.yaml's run_command), so a
release's patch note reaches Discord the moment the release does.

WHICH ENTRIES POST
------------------
Every entry DATED IN THE LAST WEEK that has never been posted, whether it came
from changelog.yaml or was written by hand in the admin. Its key is
`changelog:<entry id>`, so it posts once, ever (models/discord_message.py).

Why "dated in the last week" rather than "just created by this sync":

* A post that failed (Discord down mid-deploy) is retried by the next deploy,
  because a failed send leaves no row behind.
* An entry written while the token was still blank posts once it is set.
* The first deploy with a token does NOT flood the channel with the whole
  history: anything older than a week stays off Discord, on purpose. There is
  deliberately no backfill.
* DISCORD_CHANGELOG_START_DATE (optional) moves that floor later. Set to the
  day the bot went live, it keeps releases the team already announced by hand
  from posting a second time.

A managed entry the sync UPDATED (a typo fixed in the file) edits its existing
post instead, whatever its date. Hand-written entries are never edited from
here: the admin is not hooked up, and they change rarely enough not to need it.
"""

import datetime
import logging

from django.conf import settings
from django.utils import timezone

from calculatorapi import discord_messages
from calculatorapi.models import ChangelogEntry

logger = logging.getLogger(__name__)

# How far back an unposted entry still posts. A week covers a release that
# shipped while the token was unset, without ever reaching back into history.
POST_WINDOW_DAYS = 7

_CATEGORY_LABELS = {"added": "Added", "fixed": "Fixed", "changed": "Changed"}

# The bot's voice, the one line of it the site sends: after the date, in the
# footer, so the change list itself stays plain. Its other lines live in the
# worker's bot/persona.py; change the two together.
VOICE_THANKS = "Thanks for the feedback that shaped it."


def message_key(entry):
    return f"changelog:{entry.id}"


def _site_date(day):
    # The site's own date wording, formatDate() in the frontend: 2026/10/19.
    return f"{day.year}/{day.month}/{day.day}"


def build_embed(entry):
    """The Discord embed for one entry: its title, one line per change in the
    file's order, a link to the changelog page, and its date."""
    title = f"{entry.version}: {entry.title}" if entry.version else entry.title
    lines = [
        f"**{_CATEGORY_LABELS.get(change.category, change.category.title())}:** {change.text}"
        for change in entry.changes.all()
    ]
    return {
        "title": title[: discord_messages.EMBED_TITLE_LIMIT],
        "url": f"{settings.FRONTEND_URL}/changelog",
        "description": discord_messages.cut_lines(
            lines,
            discord_messages.EMBED_DESCRIPTION_LIMIT,
            lambda hidden: f"…and {hidden} more on the changelog",
        ),
        "footer": {"text": f"{_site_date(entry.date)} · {VOICE_THANKS}"},
    }


def _ping():
    """The @Carat Calc Ping mention, when a role is set. Only on the first post:
    an edit never pings again, and leaves this line as it was."""
    role = settings.DISCORD_CHANGELOG_PING_ROLE_ID
    if not role:
        return {}
    return {"content": f"<@&{role}>", "ping_roles": [role]}


def _post_from():
    """The earliest date that may still post: a week back, or the start date if
    that is later. None when the start date is set but unreadable: posting
    nothing is the safe failure, where ignoring it could repost history."""
    since = timezone.now().date() - datetime.timedelta(days=POST_WINDOW_DAYS)
    start = settings.DISCORD_CHANGELOG_START_DATE.strip()
    if not start:
        return since
    try:
        return max(since, datetime.date.fromisoformat(start))
    except ValueError:
        logger.warning("DISCORD_CHANGELOG_START_DATE %r is not a YYYY-MM-DD date; nothing posted", start)
        return None


def announce(updated_keys=()):
    """Post the week's unposted entries and edit the posts of `updated_keys`
    (the changelog.yaml keys this sync rewrote). Returns (posted, edited): how
    many sends and edits were queued, for the deploy log."""
    channel = settings.DISCORD_CHANGELOG_CHANNEL_ID
    if not discord_messages.is_configured(channel):
        return 0, 0

    since = _post_from()
    if since is None:
        return 0, 0
    recent = (
        ChangelogEntry.objects.filter(date__gte=since)
        .prefetch_related("changes")
        # Oldest first, so the channel reads in release order.
        .order_by("date", "id")
    )
    posted = 0
    for entry in recent:
        if discord_messages.has_message(message_key(entry)):
            continue
        discord_messages.send(message_key(entry), channel, embed=build_embed(entry), **_ping())
        posted += 1

    edited = 0
    for entry in ChangelogEntry.objects.filter(key__in=list(updated_keys)).prefetch_related("changes"):
        if discord_messages.has_message(message_key(entry)):
            discord_messages.edit(message_key(entry), embed=build_embed(entry))
            edited += 1
    return posted, edited
