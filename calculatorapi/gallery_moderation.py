"""
Keeping the gallery usable: the filters every piece of public text passes
before it is saved, and the moderation that follows a report.

Public text in the gallery is a post's caption, a comment, and (since posts
and comments show it) a display name. check_text() runs on all three. A
refusal is one plain sentence the client shows as is, and the text is never
saved, so nothing refused can reach a page later by some other path.

It also holds what happens after a report: the auto-hide, the moderator's
three decisions (keep, remove, block the author) and the optional Discord
alert. The admin's report queue calls those; gallery.report calls
after_report.

WHAT IS REFUSED, AND WHY EACH
-----------------------------
* A blocked term (models.BlockedTerm, edited by staff in the admin). Matched
  after normalise() on both sides, so "ＢＡＤ", "bad" and "b\u200bad" are one
  word. The message names the rule, never the word: telling a spammer which
  word tripped the filter is teaching them to misspell it.
* A link, in captions and comments. Ads and invite spam need a link to work, so
  refusing them makes the box useless to a spammer at almost no cost to anyone
  else (a team needs no URL). Nothing is ever turned into a link on screen
  either. Display names are exempt: they are short, unique, and checked by
  the account serializer's own rules.
"""

import logging
import re
import unicodedata

import requests
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from calculatorapi.models import (
    BlockedTerm,
    BlockedTermMatch,
    GalleryHiddenReason,
    GalleryReportResolution,
)

logger = logging.getLogger(__name__)

TERMS_REFUSED = "That can't be posted. Please check the posting rules."
LINKS_REFUSED = "Links can't be posted in the gallery."

# What counts as a link. Deliberately the patterns spam uses rather than a
# full URL grammar, which would also catch innocent text like "v1.12" or
# "Mr.Taiki". A scheme, a www. host, or a Discord invite.
_LINK = re.compile(
    r"(?:\bhttps?://|\bwww\.|\bdiscord(?:app)?\.(?:gg|com/invite)/|\bdsc\.gg/)",
    re.IGNORECASE,
)

# Characters that render as nothing and exist to split a word past a filter.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))


class TextRefused(Exception):
    """Public text a filter refused. The message is shown to the person as is."""


def normalise(text):
    """Text as the matcher sees it: NFKC (folds full-width and other
    look-alike forms onto plain letters), invisible characters removed,
    lowercased. BlockedTerm.save() stores its term through this too."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE).casefold()


def _matches(term, match, text):
    if match == BlockedTermMatch.ANYWHERE:
        return term in text
    # \b on both ends only where the term itself starts or ends with a word
    # character, so a term like "s.o.b" still matches at its edges.
    start = r"\b" if re.match(r"\w", term) else ""
    end = r"\b" if re.search(r"\w$", term) else ""
    return re.search(f"{start}{re.escape(term)}{end}", text) is not None


def contains_blocked_term(text):
    """Whether any BlockedTerm matches `text`. One query for the whole list:
    it is short, and edited rarely."""
    folded = normalise(text)
    return any(
        _matches(term, match, folded)
        for term, match in BlockedTerm.objects.values_list("term", "match")
    )


def check_text(text, *, allow_links=False):
    """Raise TextRefused when `text` may not be posted. Returns None."""
    if not text:
        return
    if not allow_links and _LINK.search(normalise(text)):
        raise TextRefused(LINKS_REFUSED)
    if contains_blocked_term(text):
        raise TextRefused(TERMS_REFUSED)


# ── Reports, auto-hide and the moderator's decisions ────────────────────────

# Open reports from this many different accounts hide a post or comment until
# a moderator decides. Three, not one: a single report is too easy to use as a
# weapon against a team someone dislikes, and three people agreeing is rare
# for anything that is fine.
AUTO_HIDE_REPORTS = 3


def _open_reports(item):
    return item.reports.filter(resolved_at__isnull=True)


def _hide(item, reason):
    item.is_hidden = True
    item.hidden_reason = reason
    item.hidden_at = timezone.now()
    item.save(update_fields=["is_hidden", "hidden_reason", "hidden_at"])


def after_report(report):
    """What a new report sets off: the item hides once enough different
    people have reported it, and a moderator is told about the first report
    and about the hide."""
    item = report.post or report.comment
    open_count = _open_reports(item).exclude(reporter=None).values("reporter").distinct().count()
    hid = False
    if not item.is_hidden and open_count >= AUTO_HIDE_REPORTS:
        _hide(item, GalleryHiddenReason.REPORTS)
        hid = True
    if open_count == 1 or hid:
        kind = "post" if report.post_id else "comment"
        what = "hidden after reports" if hid else "reported"
        alert(f"A gallery {kind} was {what} ({report.get_reason_display()}, {open_count} open).")


def _resolve(item, moderator, resolution):
    _open_reports(item).update(
        resolved_at=timezone.now(), resolved_by=moderator, resolution=resolution
    )


def keep(item, moderator):
    """The moderator looked and it is fine: show it again, close its reports."""
    with transaction.atomic():
        if item.is_hidden:
            item.is_hidden = False
            item.hidden_reason = ""
            item.hidden_at = None
            item.save(update_fields=["is_hidden", "hidden_reason", "hidden_at"])
        _resolve(item, moderator, GalleryReportResolution.KEPT)


def remove(item, moderator):
    """The moderator took it down: hidden for good, its reports closed. Kept
    in the database (hidden, not deleted) so the decision can be reversed."""
    with transaction.atomic():
        _hide(item, GalleryHiddenReason.MODERATOR)
        _resolve(item, moderator, GalleryReportResolution.REMOVED)


def block_author(item, moderator):
    """Remove the item and stop its author posting anywhere in the gallery.
    Everything else they posted disappears through the read filter, and comes
    back if the block is lifted."""
    with transaction.atomic():
        remove(item, moderator)
        if item.author is not None:
            item.author.publishing_blocked = True
            item.author.save(update_fields=["publishing_blocked"])


# ── The moderator alert ─────────────────────────────────────────────────────

ALERT_TIMEOUT_SECONDS = 3


def alert(text):
    """Post one line to the moderators' Discord webhook, after the transaction
    commits. Off while MODERATION_WEBHOOK_URL is blank, which it is locally and
    in tests.

    The line says what happened and links the admin queue. Never the reported
    text and never who reported it: a Discord channel is not the place for
    either. A failure is logged and swallowed, so Discord being down can
    never fail somebody's report.
    """
    url = settings.MODERATION_WEBHOOK_URL
    if not url:
        return
    queue = settings.FRONTEND_URL.rstrip("/") + reverse("admin:calculatorapi_galleryreport_changelist")

    def send():
        try:
            requests.post(url, json={"content": f"{text} {queue}"}, timeout=ALERT_TIMEOUT_SECONDS)
        except requests.RequestException:
            logger.warning("Moderation alert could not be sent", exc_info=True)

    transaction.on_commit(send)
