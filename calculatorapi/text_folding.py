"""Text folding for the gallery's blocked-terms filter.

One function, in a module of its own because two sides must fold text the
same way: gallery_moderation (the matcher, which imports the models) and
models.BlockedTerm.save() (which stores each term pre-folded). Living in
gallery_moderation made the model import it back, a cycle pylint fails CI
on. This module imports no models, so either side can import it at the top.
"""

import unicodedata

# Characters that render as nothing and exist to split a word past a filter.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))


def normalise(text):
    """Text as the matcher sees it: NFKC (folds full-width and other
    look-alike forms onto plain letters), invisible characters removed,
    lowercased. BlockedTerm.save() stores its term through this too."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE).casefold()
