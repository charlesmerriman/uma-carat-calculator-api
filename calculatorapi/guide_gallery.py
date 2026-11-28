"""
Community guides: players' own PvP guides, the third kind of gallery post
(notes/community-guides-plan.md).

The gallery's shared layer (gallery.py, models/gallery.py) already handles any
post: votes, comments, reports, views, moderation, erasure. This module holds
what only a guide post has.

WHO, FOR NOW: EARLY ACCESS (the owner, 2026-11-27: "Patreon-gated feature, at
least at the beginning"; decision 1: everyone with early access reads AND
writes). Two functions, so reading can open to everyone on a later day while
writing stays with supporters, with no second code path added that day:

    can_use(user)    read the list and the guides, vote, comment, report
    can_write(user)  start, save and post a guide of one's own

Every route that reaches a guide post asks gallery_access.can_open, which
asks can_use for this kind; the page never decides alone.

NOT pvp_guide_editing.can_edit. That function decides who edits the SITE's
guides (every staff account and nobody else, the owner, 2026-10-21) and also
opens Private guides and the Edit guide tab. A player's guide is that
player's own data, like a team or a plan, and becomes public only by posting,
through the gallery's rules (opt-in, filters, reports, erasure).
"""

from calculatorapi import benefits

REFUSED_TEXT = "Community guides are open to supporters first."


def can_use(user):
    """Whether `user` may read community guides right now."""
    return benefits.early_access(user)


def can_write(user):
    """Whether `user` may start, save or post a guide of their own right now.
    A blocked account keeps reading but cannot write; a lapsed supporter keeps
    their guides and posts (a lapse never deletes data) and writes again when
    the pledge comes back."""
    return can_use(user) and not user.publishing_blocked
