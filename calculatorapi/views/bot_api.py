"""
The bot API: what the Discord bot worker calls when a moderator clicks a
card's button or runs a /mod command (discord-bot-plan.md, Steps 3b and 4).

WHO CALLS IT, AND HOW IT IS LOCKED
----------------------------------
Only the worker, a separate process with no account here. It presents one
shared secret, BOT_API_SECRET, in the X-Bot-Key header, the same pattern as
POST /patreon/sync (views/patreon_supporters.py): while the secret is unset
every route 404s, so an unconfigured deployment exposes nothing, and the key
is compared with `compare_digest`, so it can't be guessed one character at a
time from response times.

The key is the THIRD of three locks (decision 6). The first two are in
Discord: #calc-mod-queue is visible only to @Moderator, and the worker checks
that role on every click before calling here. The key covers what those two
can't, such as the worker's address leaking, but it is not the only lock.

A DISCORD MODERATOR IS A NAME, NEVER AN ACCOUNT
-----------------------------------------------
The worker sends the clicker as `moderator`, "discord username (discord id)".
A decision stores that string in GalleryReport.resolved_by_discord and leaves
`resolved_by` empty. Nothing here creates, finds or signs in an account:
moderating from Discord must never become a way into the site.

WHAT THE ITEM PAYLOAD CARRIES
-----------------------------
The same as the open card in Discord, and nothing more: the reported text,
the author's display name and handle, and each open report's reason, note
and reporter (display name and handle). Never an email, a provider, a linked
account or a user id. Names go out plain; the worker escapes them for Discord.

ITEMS ARE NAMED THE WAY THE BOT SEES THEM
-----------------------------------------
`<kind>/<ref>`: a post by its public id (the one in its /post/ link, which a
moderator can paste into /mod), a comment by its row id, since comments have
no public id. moderation_cards.item_ref builds the same pair for the buttons.
"""

import hmac
import logging
import re
from functools import wraps

from django.conf import settings
from django.db import transaction
from django.http import Http404
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from calculatorapi import gallery_moderation, moderation_cards, team_queue
from calculatorapi.analytics.digest import digest
from calculatorapi.models import (
    BlockedTerm,
    BlockedTermMatch,
    DiscordMessage,
    GalleryComment,
    GalleryPost,
    GalleryReport,
)
from calculatorapi.text_folding import normalise

logger = logging.getLogger(__name__)

BOT_HEADER = "X-Bot-Key"

# The moderator as the worker sends them: a Discord username, then the user's
# id (a snowflake, all digits) in brackets. Required in exactly this shape so
# every record names a person who can be found again after a rename.
_MODERATOR = re.compile(r"^.{1,80} \(\d{5,25}\)$")

DECISIONS = {
    "keep": gallery_moderation.keep,
    "remove": gallery_moderation.remove,
    "block": gallery_moderation.block_author,
}

# GET bot/reports lists at most this many items: a Discord message holds about
# that many readable lines, and the admin has the rest.
REPORTS_LIMIT = 25
PREVIEW_LENGTH = 80


class BotApiThrottle(AnonRateThrottle):
    """A cap on the worker's calls (rate in settings: "bot_api"). Everything
    comes from the worker's one address, so this is per worker, sized for a
    busy moderation hour rather than for a person."""

    scope = "bot_api"


def bot_route(methods):
    """A bot API view: AllowAny for DRF, then the shared-secret check. 404
    while BOT_API_SECRET is unset, 403 on a missing or wrong key, saying
    nothing about which."""

    def decorate(view):
        @wraps(view)
        def guarded(request, *args, **kwargs):
            expected = settings.BOT_API_SECRET
            if not expected:
                raise Http404
            presented = request.headers.get(BOT_HEADER, "")
            if not hmac.compare_digest(presented.encode(), expected.encode()):
                return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)
            return view(request, *args, **kwargs)

        return api_view(methods)(
            permission_classes([permissions.AllowAny])(
                throttle_classes([BotApiThrottle])(guarded)
            )
        )

    return decorate


# ── Shared pieces ──────────────────────────────────────────────────────────

def _item(kind, ref, *, lock=False):
    """The post or comment `<kind>/<ref>` names, or a 404. `lock` takes a row
    lock, so two moderators clicking at once are decided one after the other
    and the second sees the first's decision (a 409), not a double close."""
    if kind == "post":
        queryset = GalleryPost.objects.filter(public_id=ref)
    elif kind == "comment" and ref.isdigit():
        queryset = GalleryComment.objects.filter(pk=int(ref))
    else:
        raise Http404
    if lock:
        # of=("self",): Postgres refuses FOR UPDATE on the nullable side of a
        # join, and the author is nullable (a deleted account's comment).
        queryset = queryset.select_for_update(of=("self",))
    item = queryset.select_related("author").first()
    if item is None:
        raise Http404
    return item


def _person(user):
    """A display name and a handle. The handle is the account's site username,
    which moderators use to find it in the admin; nothing else about it."""
    if user is None:
        return None
    return {"display_name": user.display_name or "", "handle": user.username}


def _moderator(request):
    moderator = str(request.data.get("moderator", "")).strip()
    return moderator if _MODERATOR.match(moderator) else None


def _bad(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _item_payload(item):
    kind, ref = moderation_cards.item_ref(item)
    post = item if kind == "post" else item.post
    reports = list(item.reports.select_related("reporter", "resolved_by").order_by("created_at"))
    open_reports = [report for report in reports if report.resolved_at is None]
    decided = None
    if reports and not open_reports:
        last = max(reports, key=lambda report: report.resolved_at)
        decided = {
            "resolution": last.resolution,
            "by": moderation_cards.decided_by(last),
            "at": last.resolved_at.isoformat(),
        }
    return {
        "kind": kind,
        "ref": ref,
        "url": f"{settings.FRONTEND_URL}/post/{post.public_id}",
        "text": moderation_cards.item_text(item),
        "author": _person(item.author),
        "author_blocked": bool(item.author and item.author.publishing_blocked),
        "is_hidden": item.is_hidden,
        "status": moderation_cards.status_text(item),
        "report_count": len(reports),
        "open_reports": [
            {
                "reason": report.get_reason_display(),
                "note": report.note,
                "reporter": _person(report.reporter),
                "created_at": report.created_at.isoformat(),
            }
            for report in open_reports
        ],
        "decided": decided,
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@bot_route(["GET"])
def bot_reports(request):  # pylint: disable=unused-argument
    """GET /bot/reports: items with an open report, most recently reported
    first, for /mod reports. Counts and a short preview; no names (the
    item route has them, for the one item a moderator opens)."""
    rows = (
        GalleryReport.objects.filter(resolved_at__isnull=True)
        .select_related("post", "comment__post")
        .order_by("-created_at", "-id")
    )
    items = {}
    for report in rows:
        item = report.post or report.comment
        key = (type(item), item.pk)
        if key not in items:
            items[key] = {"item": item, "open_reports": 0, "latest_report_at": report.created_at}
        items[key]["open_reports"] += 1
    listed = []
    for entry in list(items.values())[:REPORTS_LIMIT]:
        item = entry["item"]
        kind, ref = moderation_cards.item_ref(item)
        text = moderation_cards.item_text(item)
        listed.append({
            "kind": kind,
            "ref": ref,
            "preview": text if len(text) <= PREVIEW_LENGTH else text[: PREVIEW_LENGTH - 1].rstrip() + "…",
            "open_reports": entry["open_reports"],
            "is_hidden": item.is_hidden,
            "latest_report_at": entry["latest_report_at"].isoformat(),
        })
    return Response({"items": listed, "total": len(items)})


@bot_route(["GET"])
def bot_item(request, kind, ref):  # pylint: disable=unused-argument
    """GET /bot/items/<kind>/<ref>: one item as its open card shows it."""
    return Response(_item_payload(_item(kind, ref)))


@bot_route(["POST"])
def bot_moderate(request, kind, ref):
    """POST /bot/items/<kind>/<ref>/moderate {action, moderator}: Keep, Remove
    or Block author, exactly as the admin's actions do (gallery_moderation),
    recorded under the Discord moderator. The card is edited by that same
    path, so Discord and the admin can't disagree about what happened.

    409 when nothing is open: someone else got there first, and the answer
    says who, so the worker can tell the clicker."""
    action = request.data.get("action")
    if action not in DECISIONS:
        return _bad("action must be keep, remove or block.")
    moderator = _moderator(request)
    if moderator is None:
        return _bad('moderator must be "discord username (discord id)".')
    with transaction.atomic():
        item = _item(kind, ref, lock=True)
        if not item.reports.filter(resolved_at__isnull=True).exists():
            last = item.reports.exclude(resolved_at=None).order_by("-resolved_at").first()
            return Response(
                {
                    "detail": "Already handled.",
                    "handled_by": moderation_cards.decided_by(last) if last else None,
                    "resolution": last.resolution if last else None,
                },
                status=status.HTTP_409_CONFLICT,
            )
        DECISIONS[action](item, None, discord_moderator=moderator)
    item.refresh_from_db()
    return Response({"done": action, "item": _item_payload(item)})


@bot_route(["POST"])
def bot_undo(request, kind, ref):
    """POST /bot/items/<kind>/<ref>/undo {moderator}: reverse the item's last
    decision, for a misclick (gallery_moderation.undo_last_decision): its
    reports open again, a block it set is lifted, and the card goes back to
    open with its buttons. 409 when there is nothing to undo."""
    moderator = _moderator(request)
    if moderator is None:
        return _bad('moderator must be "discord username (discord id)".')
    with transaction.atomic():
        item = _item(kind, ref, lock=True)
        if not gallery_moderation.undo_last_decision(item):
            return Response({"detail": "Nothing to undo: it's open."}, status=status.HTTP_409_CONFLICT)
    logger.info("Moderation decision undone from Discord by %s (%s %s)", moderator, kind, ref)
    item.refresh_from_db()
    return Response({"item": _item_payload(item)})


@bot_route(["POST"])
def bot_unblock(request, kind, ref):
    """POST /bot/items/<kind>/<ref>/unblock {moderator}: let the item's author
    post again. The item itself stays as decided. 409 when they aren't
    blocked. Who lifted it goes to the log (there is no column for it: a block
    is one flag on the account, and its history lives in the reports)."""
    moderator = _moderator(request)
    if moderator is None:
        return _bad('moderator must be "discord username (discord id)".')
    item = _item(kind, ref)
    if not gallery_moderation.unblock_author(item):
        return Response({"detail": "Its author isn't blocked."}, status=status.HTTP_409_CONFLICT)
    logger.info("Gallery author unblocked from Discord by %s (%s %s)", moderator, kind, ref)
    item.refresh_from_db()
    return Response({"item": _item_payload(item)})


@bot_route(["POST"])
def bot_blocked_term(request):
    """POST /bot/blocked-terms {term, match, moderator}: add a term the gallery
    refuses, as an editor would in the admin. `match` is "word" (the default)
    or "anywhere". Stored normalised, so "ＢＡＤ" and "bad" are one term and
    the second is a 409. The answer never repeats the list."""
    moderator = _moderator(request)
    if moderator is None:
        return _bad('moderator must be "discord username (discord id)".')
    match = request.data.get("match") or BlockedTermMatch.WORD
    if match not in BlockedTermMatch.values:
        return _bad("match must be word or anywhere.")
    term = normalise(str(request.data.get("term", ""))).strip()
    max_length = BlockedTerm._meta.get_field("term").max_length  # pylint: disable=protected-access
    if not term or len(term) > max_length:
        return _bad(f"term must be 1 to {max_length} characters.")
    # get_or_create, not exists() then create(): two adds of one term at once
    # must answer 201 and 409, never a 500 from the unique index.
    _, created = BlockedTerm.objects.get_or_create(term=term, defaults={"match": match})
    if not created:
        return Response({"detail": "That term is already blocked."}, status=status.HTTP_409_CONFLICT)
    logger.info("Blocked term added from Discord by %s", moderator)
    return Response({"term": term, "match": match}, status=status.HTTP_201_CREATED)


# ── The worker's own "never twice" records ─────────────────────────────────
#
# The worker posts things of its own (a team-board copy of a public feedback
# post) that must happen once however often a moderator clicks. It keeps that
# promise in the same table as the site's posts, DiscordMessage, under keys
# only it may write: a prefix list, so a leaked bot key can't reach the
# changelog's or the cards' rows. Claim before posting, record the message
# after, release the claim if the post failed.

# Each prefix the worker may write, and the shape of what follows it:
# promote:<public post>  "Send to team board" (the team copy's id is the message)
# upnext:<channel>        the pinned "Up next" message in #calc-ops
# weekly:<yyyymmdd>       one Monday summary per week
# digest:<yyyymmdd>       one daily digest per day
# deploy:<uuid>           one #calc-dev-log line per DigitalOcean deployment
_DIGITS = r"[0-9]{5,25}"
WORKER_KEYS = {
    "promote": _DIGITS,
    "upnext": _DIGITS,
    "weekly": r"[0-9]{8}",
    "digest": r"[0-9]{8}",
    "deploy": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
}
WORKER_KEY_PREFIXES = tuple(f"{prefix}:" for prefix in WORKER_KEYS)
_SNOWFLAKE = re.compile(f"^{_DIGITS}$")


def _worker_key(key):
    prefix, _, rest = (key or "").partition(":")
    pattern = WORKER_KEYS.get(prefix)
    if pattern is None or not re.fullmatch(pattern, rest):
        raise Http404
    return key


def _message_payload(row):
    return {"key": row.key, "channel_id": row.channel_id, "message_id": row.message_id}


@bot_route(["GET", "POST"])
def bot_message_claim(request):
    """POST /bot/messages {key, channel_id}: claim `key` before posting. 201 when
    it's yours; 409 with what was already recorded under it otherwise.

    GET /bot/messages?message_id=<id>: the worker record that produced that
    message, or 404. How the status mirror finds the public post a team copy
    came from (its `promote:<public post>` key)."""
    if request.method == "GET":
        message_id = request.query_params.get("message_id", "")
        rows = DiscordMessage.objects.filter(message_id=message_id) if _SNOWFLAKE.match(message_id) else []
        row = next((row for row in rows if row.key.startswith(WORKER_KEY_PREFIXES)), None)
        if row is None:
            raise Http404
        return Response(_message_payload(row))
    key = _worker_key(str(request.data.get("key", "")))
    channel_id = str(request.data.get("channel_id", ""))
    if not _SNOWFLAKE.match(channel_id):
        return _bad("channel_id must be a Discord id.")
    row, created = DiscordMessage.objects.get_or_create(key=key, defaults={"channel_id": channel_id})
    return Response(_message_payload(row), status=status.HTTP_201_CREATED if created else status.HTTP_409_CONFLICT)


@bot_route(["GET", "PATCH", "DELETE"])
def bot_message(request, key):
    """GET /bot/messages/<key>: what was recorded, or 404.
    PATCH {message_id}: record the message a claim produced.
    DELETE: give a claim back after a failed post, so a later click can retry.
    Only a claim still in flight (no message yet) can be deleted: a recorded
    message is permanent, or a second click could post a second copy."""
    row = DiscordMessage.objects.filter(key=_worker_key(key)).first()
    if row is None:
        raise Http404
    if request.method == "PATCH":
        message_id = str(request.data.get("message_id", ""))
        if not _SNOWFLAKE.match(message_id):
            return _bad("message_id must be a Discord id.")
        row.message_id = message_id
        row.save(update_fields=["message_id", "updated_at"])
    elif request.method == "DELETE":
        if row.message_id:
            return Response({"detail": "Already posted; not released."}, status=status.HTTP_409_CONFLICT)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(_message_payload(row))


# ── The team board's queue (team_queue.py) ─────────────────────────────────

def _thread_id(value):
    value = str(value or "")
    if not _SNOWFLAKE.match(value):
        raise Http404
    return value


def _queue():
    return Response({"thread_ids": team_queue.entries()})


@bot_route(["GET", "POST"])
def bot_queue(request):
    """GET /bot/queue: post ids in order. POST {thread_id}: add one at the
    bottom (201), or 200 when it was already queued (it keeps its place)."""
    if request.method == "GET":
        return _queue()
    added = team_queue.add(_thread_id(request.data.get("thread_id")))
    response = _queue()
    response.status_code = status.HTTP_201_CREATED if added else status.HTTP_200_OK
    return response


@bot_route(["DELETE"])
def bot_queue_entry(request, thread_id):  # pylint: disable=unused-argument
    """DELETE /bot/queue/<thread_id>: take a post out; 404 if it wasn't in."""
    if not team_queue.remove(_thread_id(thread_id)):
        raise Http404
    return _queue()


@bot_route(["POST"])
def bot_queue_move(request, thread_id):
    """POST /bot/queue/<thread_id>/move {position}: 1 is the top; a position
    past the end means the bottom. 404 if the post isn't queued."""
    try:
        position = int(request.data.get("position"))
    except (TypeError, ValueError):
        return _bad("position must be a whole number.")
    if position < 1:
        return _bad("position starts at 1.")
    if not team_queue.move(_thread_id(thread_id), position):
        raise Http404
    return _queue()


@bot_route(["POST"])
def bot_queue_sync(request):
    """POST /bot/queue/sync {thread_ids}: make the queue hold exactly these
    posts, new ones at the bottom in the order sent, the rest in place. The
    worker's repair after it missed a tag change (a restart, an outage)."""
    thread_ids = request.data.get("thread_ids")
    if not isinstance(thread_ids, list) or not all(_SNOWFLAKE.match(str(tid)) for tid in thread_ids):
        return _bad("thread_ids must be a list of Discord ids.")
    team_queue.sync([str(tid) for tid in thread_ids])
    return _queue()


# ── The daily digest (analytics/digest.py) ─────────────────────────────────

@bot_route(["GET"])
def bot_digest(request):  # pylint: disable=unused-argument
    """GET /bot/digest: yesterday's numbers for #calc-ops. Aggregates only:
    nothing in it names or points at a person. Reading it also keeps today's
    analytics snapshot, as opening the admin dashboard does."""
    return Response(digest())
