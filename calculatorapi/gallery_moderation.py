"""
Keeping the gallery usable: the filters every piece of public text passes
before it is saved, and the moderation that follows a report.

Public text in the gallery is a post's caption, a comment, and (since posts
and comments show it) a display name. check_text() runs on all three. A
refusal is one plain sentence the client shows as is, and the text is never
saved, so nothing refused can reach a page later by some other path.

It also holds what happens after a report: the auto-hide and the moderator's
three decisions (keep, remove, block the author). The admin's report queue
and the bot API (a moderator's click on a card in Discord) call those;
gallery.report calls after_report. Each one ends by bringing the
item's card in Discord's #calc-mod-queue up to date (moderation_cards).

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

import re

from django.db import transaction
from django.utils import timezone

from calculatorapi import moderation_cards
from calculatorapi.text_folding import normalise
from calculatorapi.models import (
    BlockedTerm,
    BlockedTermMatch,
    GalleryHiddenReason,
    GalleryReportResolution,
)


TERMS_REFUSED = "That can't be posted. Please check the posting rules."
LINKS_REFUSED = "Links can't be posted in the gallery."

# What counts as a link. Deliberately the patterns spam uses rather than a
# full URL grammar, which would also catch innocent text like "v1.12" or
# "Mr.Taiki". A scheme, a www. host, or a Discord invite.
_LINK = re.compile(
    r"(?:\bhttps?://|\bwww\.|\bdiscord(?:app)?\.(?:gg|com/invite)/|\bdsc\.gg/)",
    re.IGNORECASE,
)

class TextRefused(Exception):
    """Public text a filter refused. The message is shown to the person as is."""


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


def _show(item):
    if item.is_hidden:
        item.is_hidden = False
        item.hidden_reason = ""
        item.hidden_at = None
        item.save(update_fields=["is_hidden", "hidden_reason", "hidden_at"])


def _distinct_reporters(item):
    return _open_reports(item).exclude(reporter=None).values("reporter").distinct().count()


def _hide(item, reason):
    item.is_hidden = True
    item.hidden_reason = reason
    item.hidden_at = timezone.now()
    item.save(update_fields=["is_hidden", "hidden_reason", "hidden_at"])


def after_report(report):
    """What a new report sets off: the item hides once enough different
    people have reported it, and its moderation card shows the new report."""
    item = report.post or report.comment
    if not item.is_hidden and _distinct_reporters(item) >= AUTO_HIDE_REPORTS:
        _hide(item, GalleryHiddenReason.REPORTS)
    moderation_cards.refresh(item)


def _resolve(item, moderator, resolution, discord_moderator):
    _open_reports(item).update(
        resolved_at=timezone.now(),
        resolved_by=moderator,
        resolved_by_discord=discord_moderator,
        resolution=resolution,
    )


# Each decision takes the deciding account (the admin's request.user), or for
# a click on a card in Discord, `moderator=None` and `discord_moderator` set to
# the clicker's "name (id)" (views/bot_api.py). Exactly one of the two names
# the person; a Discord moderator never has an account here.

def keep(item, moderator, *, discord_moderator=""):
    """The moderator looked and it is fine: show it again, close its reports."""
    with transaction.atomic():
        _show(item)
        _resolve(item, moderator, GalleryReportResolution.KEPT, discord_moderator)
        moderation_cards.refresh(item)


def remove(item, moderator, *, discord_moderator=""):
    """The moderator took it down: hidden for good, its reports closed. Kept
    in the database (hidden, not deleted) so the decision can be reversed."""
    with transaction.atomic():
        _remove(item, moderator, discord_moderator)
        moderation_cards.refresh(item)


def _remove(item, moderator, discord_moderator, resolution=GalleryReportResolution.REMOVED):
    _hide(item, GalleryHiddenReason.MODERATOR)
    _resolve(item, moderator, resolution, discord_moderator)


def block_author(item, moderator, *, discord_moderator=""):
    """Remove the item and stop its author posting anywhere in the gallery.
    Everything else they posted disappears through the read filter, and comes
    back if the block is lifted."""
    with transaction.atomic():
        _remove(item, moderator, discord_moderator, GalleryReportResolution.BLOCKED)
        if item.author is not None:
            item.author.publishing_blocked = True
            item.author.save(update_fields=["publishing_blocked"])
        # After the block, so the card can say the author was blocked too.
        moderation_cards.refresh(item)


def unblock_author(item):
    """Lift the block on `item`'s author: they can post again, and everything
    else they posted comes back through the read filter. The item itself stays
    removed; a moderator who wants it back keeps it as well. Returns whether
    there was a block to lift."""
    author = item.author
    if author is None or not author.publishing_blocked:
        return False
    with transaction.atomic():
        author.publishing_blocked = False
        author.save(update_fields=["publishing_blocked"])
        # A decided card says "and its author blocked"; it no longer should.
        moderation_cards.refresh(item)
    return True


def undo_last_decision(item):
    """Put `item` back as it was before its last decision, for a misclick:
    the reports that decision closed are open again (with no moderator on
    them), a block it set is lifted, and the item is visible or hidden exactly
    as its open reports alone would leave it (hidden again only if enough
    people reported it, the auto-hide rule). The card goes back to open, with
    its buttons, so the moderator can choose again.

    Only the LAST decision: the reports one decision closed share its
    `resolved_at` (one update() call stamps them all), so earlier rounds stay
    closed. Returns False when there is nothing to undo: never decided, or
    already open again (a new report arrived, or someone undid it first)."""
    with transaction.atomic():
        if _open_reports(item).exists():
            return False
        last = item.reports.exclude(resolved_at=None).order_by("-resolved_at").first()
        if last is None:
            return False
        author = item.author
        if last.resolution == GalleryReportResolution.BLOCKED and author is not None and author.publishing_blocked:
            author.publishing_blocked = False
            author.save(update_fields=["publishing_blocked"])
        item.reports.filter(resolved_at=last.resolved_at).update(
            resolved_at=None, resolved_by=None, resolved_by_discord="", resolution=""
        )
        if _distinct_reporters(item) >= AUTO_HIDE_REPORTS:
            _hide(item, GalleryHiddenReason.REPORTS)
        else:
            _show(item)
        moderation_cards.refresh(item)
    return True
