"""
Keeping the gallery usable: the filters every piece of public text passes
before it is saved.

Public text in the gallery is a post's caption, a comment, and (since posts
and comments show it) a display name. check_text() runs on all three. A
refusal is one plain sentence the client shows as is, and the text is never
saved, so nothing refused can reach a page later by some other path.

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
import unicodedata

from calculatorapi.models import BlockedTerm, BlockedTermMatch

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
