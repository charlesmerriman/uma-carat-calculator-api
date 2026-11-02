"""
Who may read a PvP guide (owner, 2026-11-02, notes/guide-visibility-plan.md).
The ONE place the API decides it.

    Private     staff only (pvp_guide_editing.can_edit)
    Supporters  early access (benefits.early_access: a pledge, staff, or the
                admin's grant) and staff
    Public      everyone

The rule is data, PvpGuide.visibility, set in the site's editor or the admin.
It replaced pvp_gate.py, which held a COPY of a rule written in the frontend
(the rolling free guide, the one open CM) that had to be flipped in both repos
together. Now /calculator-data sends each event's setting to the page
(views/pvp_guide.annotate_has_guide), so the page and this module read the
same row and nothing is mirrored by hand.

ENFORCED HERE for the guide and its comments (owner, 2026-11-02, replacing
"early access is a client gate only" for guide content): GET /pvp-guides and
the guide comment routes ask can_read. My team and the Gallery stay page-only
gates; PvpGuide.tools_public opens them to everyone on a Public guide, and the
page reads it from the payload.
"""

from calculatorapi import benefits, pvp_guide_editing
from calculatorapi.models import PvpGuideVisibility


def can_read(user, guide):
    """Whether `user` may read `guide` on the site (anonymous included)."""
    if guide.visibility == PvpGuideVisibility.PUBLIC:
        return True
    if pvp_guide_editing.can_edit(user, guide):
        return True
    return guide.visibility == PvpGuideVisibility.SUPPORTERS and benefits.early_access(user)


def open_to_everyone(guide):
    """Whether anyone at all may read it, so its link preview may show the
    guide's facts (views/guide_share.py)."""
    return guide.visibility == PvpGuideVisibility.PUBLIC
