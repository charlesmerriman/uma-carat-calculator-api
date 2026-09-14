"""
What a Patreon supporter is entitled to, and how a request finds out.

ENTITLEMENT IS DERIVED, NEVER STORED
------------------------------------
    is_supporter = a PatreonSupporter row points at this user
                   AND that row is_active
                   AND that row has a tier

There is deliberately NO boolean on CustomUser. A copied flag is a second truth,
and it drifts the moment a pledge lapses or resumes — invisibly, in the
direction nobody notices: someone cancels and keeps their benefits, or renews
and silently does not get them back. The daily Patreon sync already keeps the
supporter row honest, so deriving from it costs one indexed query and cannot go
stale.

WHY THE BENEFIT MAP LIVES IN CODE AND NOT IN THE ADMIN
------------------------------------------------------
A paywall boundary should move through a reviewable diff. As an admin form it is
one mis-typed number away from giving everything away, or from taking a feature
off people who are paying for it, with no record of who changed what.

TIER ORDER IS A THRESHOLD, AND LOWER MEANS HIGHER
-------------------------------------------------
PatreonTier.order is the display rank: 0 is the top tier, and the numbers grow
downwards (see the model). So "you need at least tier X" is `order <= X.order`,
and ANY_PAID_TIER is the loosest check there is. Gating a future benefit on a
specific tier is then a one-line change here and needs no schema work.

WHY NOT CHECK PATREON ON DEMAND INSTEAD
---------------------------------------
Storing each user's own Patreon token would mean owning refresh and revocation
per user, and a cancelled pledge would still read as active until they happened
to re-link. The creator-side daily sync means zero per-user Patreon tokens are
stored anywhere and entitlement stays current through machinery already
deployed.
"""

import logging

from django.db import IntegrityError, transaction
from rest_framework import permissions

from calculatorapi.models import OSHI_SLOT_CAP, PatreonSupporter, SocialAccount

logger = logging.getLogger(__name__)

# A benefit that any paid tier unlocks. Spelled as a name rather than as a bare
# None at each call site, because "no minimum" and "not configured" would
# otherwise look identical in the map below.
ANY_PAID_TIER = None

# ── Benefit keys ─────────────────────────────────────────────────────────────
# One per gated capability. The string is what the API sends the client and what
# a <SupporterOnly> boundary keys off, so it is part of the contract: rename one
# and the frontend silently stops gating.
AD_FREE = "ad_free"
# May pick oshis, the first of which is their account picture. The boolean
# half of the perk; HOW MANY is the ladder below.
OSHI = "oshi"

# feature key -> the tier order a supporter must be at or above, or
# ANY_PAID_TIER. Nothing but the keys defined above belongs here; an unknown key
# raises rather than returning False, so a typo in a gate is a loud failure at
# the first request instead of a feature that quietly refuses everyone.
BENEFITS = {
    AD_FREE: ANY_PAID_TIER,
    OSHI: ANY_PAID_TIER,
}

# ── Oshi slots ───────────────────────────────────────────────────────────────
# The one benefit that is a COUNT rather than a yes/no: 5 oshis on the top tier,
# 3 on the next, 1 on any other paid tier. Rungs are (tier order threshold,
# slots), read top down, and the first rung the supporter clears wins -- the
# same `order <= threshold` test BENEFITS uses, so a tier added below the
# current bottom gets 1 and a tier renumbered above the top gets 5. The
# thresholds are the prod tiers' orders as of 2026-09-13: Senior Class 1,
# Classic Class 2, Junior Class 3.
#
# In code and not on PatreonTier for the same reason BENEFITS is: a paywall
# boundary should move through a reviewable diff, not an admin form.
OSHI_SLOT_LADDER = (
    (1, 5),
    (2, 3),
    (ANY_PAID_TIER, 1),
)

# The model caps `position` at OSHI_SLOT_CAP - 1, so the ladder must never
# grant more than the table can hold.
assert max(slots for _, slots in OSHI_SLOT_LADDER) == OSHI_SLOT_CAP


def entitled_supporter(user):
    """The supporter row backing this user's entitlement, or None.

    Returns None — not a row — for a linked patron whose pledge has lapsed or
    who sits on no tier, because every caller wants the same three conditions
    and splitting them up is how one of them gets forgotten at a call site.
    """
    if user is None or not user.is_authenticated:
        return None
    return (
        PatreonSupporter.objects.select_related("tier")
        .filter(linked_user=user, is_active=True, tier__isnull=False)
        .first()
    )


def is_supporter(user):
    """True when this user has a live pledge on some paid tier."""
    return entitled_supporter(user) is not None


def _meets(supporter, required_order):
    """Whether `supporter`'s tier clears a benefit's threshold."""
    if supporter is None:
        return False
    if required_order is ANY_PAID_TIER:
        return True
    return supporter.tier.order <= required_order


def has_benefit(user, key):
    """Whether `user` is entitled to the benefit named `key`.

    KeyError on an unknown key is deliberate — see BENEFITS.
    """
    return _meets(entitled_supporter(user), BENEFITS[key])


def benefit_keys_for(supporter):
    """Every benefit key an already-resolved supporter row holds, sorted.

    Takes the row rather than the user because callers that render a supporter
    block need the tier anyway — one query, and the tier shown is guaranteed to
    be the tier the list was derived from.
    """
    if supporter is None:
        return []
    return sorted(key for key, order in BENEFITS.items() if _meets(supporter, order))


def benefit_keys(user):
    """Every benefit key this user currently holds, sorted for a stable body."""
    return benefit_keys_for(entitled_supporter(user))


def oshi_slots_for(supporter, *, is_staff=False):
    """How many oshis an already-resolved supporter row is entitled to; 0 for None.

    `is_staff` is a full bypass of the ladder, capped at OSHI_SLOT_CAP rather
    than at the top rung's 5 for the same reason the ladder asserts against
    the cap on import: if the top rung ever grows, staff access grows with it
    without a second number to keep in sync here. This is NOT a case of the
    "entitlement is derived, never stored" rule bending -- `is_staff` is
    already CustomUser's own single source of truth for staff-ness, not a
    copy of Patreon's, so reading it here creates no second truth to drift.
    """
    if is_staff:
        return OSHI_SLOT_CAP
    for threshold, slots in OSHI_SLOT_LADDER:
        if _meets(supporter, threshold):
            return slots
    return 0


def oshi_slots(user):
    """How many oshis `user` may hold right now. 0 for everyone who is not a
    supporter and not staff, which is what makes "has a picture" and "has at
    least one slot" the same question."""
    is_staff = user is not None and user.is_authenticated and user.is_staff
    return oshi_slots_for(entitled_supporter(user), is_staff=is_staff)


class IsSupporter(permissions.BasePermission):
    """DRF permission for a whole endpoint that only supporters may reach.

    For a field or a row INSIDE an otherwise-free endpoint this is the wrong
    tool — refusing the request would take the user's unrelated data down with
    it. See the lapse rule in views/calculator.py.
    """

    message = "This feature is available to Patreon supporters."

    def has_permission(self, request, view):
        return is_supporter(request.user)


# ── Attaching a patron to an account ─────────────────────────────────────────
# The same edge, reachable from both ends, because the two tables fill up
# independently: the sync learns about patrons, the sign-in and link flows learn
# about accounts, and whichever arrives second closes the gap.


def _attach(supporter, user):
    """Point `supporter` at `user`, unless something already owns either end."""
    if supporter.linked_user_id == user.pk:
        return supporter
    if supporter.linked_user_id is not None:
        # Another account already claims this patron. Only reachable if an
        # unlink failed to clear the field, since SocialAccount is unique on
        # (provider, subject_id) — so it is worth a log line rather than a
        # silent no-op, but never worth overwriting: reassigning entitlement
        # away from an account is not something a link request may do.
        logger.warning(
            "PatreonSupporter %s is already linked to a different account", supporter.pk
        )
        return None
    try:
        with transaction.atomic():
            supporter.linked_user = user
            supporter.save(update_fields=["linked_user"])
    except IntegrityError:
        # linked_user is OneToOne, so this means the user already has a
        # different supporter row — a race, or a stale link an unlink missed.
        logger.warning("Could not link PatreonSupporter %s: account already linked", supporter.pk)
        return None
    return supporter


def link_supporter_to_user(user, patreon_user_id):
    """ACCOUNT SIDE: someone just signed in with, or linked, this Patreon id.

    Returns the supporter row if one was already known, else None — in which
    case they either do not pledge, or pledged so recently that the sync has
    not seen them yet. Purely local; the caller decides whether that second
    possibility is worth a request to Patreon.
    """
    if not patreon_user_id:
        return None
    supporter = PatreonSupporter.objects.filter(patreon_user_id=patreon_user_id).first()
    if supporter is None:
        return None
    return _attach(supporter, user)


def link_user_to_supporter(supporter):
    """SYNC SIDE: this patron may already have an account here.

    Called from the reconcile for every row carrying a Patreon id, so a patron
    who linked before they pledged is picked up by the next sync with no second
    action from them.
    """
    if not supporter.patreon_user_id or supporter.linked_user_id is not None:
        return None
    link = SocialAccount.objects.filter(
        provider=SocialAccount.PROVIDER_PATREON, subject_id=supporter.patreon_user_id
    ).first()
    if link is None:
        return None
    return _attach(supporter, link.user)


def unlink_supporter(user):
    """Detach whatever supporter row points at `user`. Returns True if one did.

    Called when a Patreon login is removed from an account. The ROW SURVIVES,
    with its `is_public` consent and `patron_since` intact — they are still a
    patron, they just have no account here to be entitled through. Identical
    treatment to a lapse, and for the same reason: this table is not ours to
    delete from.
    """
    supporter = PatreonSupporter.objects.filter(linked_user=user).first()
    if supporter is None:
        return False
    supporter.linked_user = None
    supporter.save(update_fields=["linked_user"])
    return True
