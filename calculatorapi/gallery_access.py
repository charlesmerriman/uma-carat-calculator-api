"""
Who may reach one gallery post, whatever its kind: the ONE gate every route to
a post asks (the detail, votes, comments, reports, views, copies, profile
cards). A new route that reaches a post asks it too.

    team   anyone
    plan   plan_gallery.can_use   (the Pull Plan Library: early access first)
    guide  guide_gallery.can_use  (community guides: early access first)

and a post's own author always reaches it, so a lapsed supporter can still
read and take down what they posted.

Its own module because both kinds' modules import gallery.py, so gallery.py
cannot import them back. Moved here from plan_gallery.can_open when guides
became the third kind (notes/community-guides-plan.md), so the rule is
decided by kind, by name, never by "not a plan".
"""

from calculatorapi import guide_gallery, plan_gallery
from calculatorapi.models import GalleryPostKind

_GATES = {
    GalleryPostKind.PLAN: plan_gallery.can_use,
    GalleryPostKind.PVP_GUIDE: guide_gallery.can_use,
}


def can_open(post, viewer):
    """Whether `viewer` (anonymous included) may reach `post` on any route."""
    if post.is_team:
        return True
    if viewer is not None and viewer.is_authenticated and viewer.pk == post.author_id:
        return True
    # An unknown kind is a KeyError, loudly, rather than a post served to anyone.
    return _GATES[post.kind](viewer)


def can_list(kind, viewer):
    """Whether `viewer` may see posts of `kind` in lists (a profile card's)."""
    return kind == GalleryPostKind.PVP_TEAM or _GATES[kind](viewer)


def refused_text(post):
    """The sentence a reader without access gets for this post."""
    return plan_gallery.REFUSED_TEXT if post.is_plan else guide_gallery.REFUSED_TEXT
