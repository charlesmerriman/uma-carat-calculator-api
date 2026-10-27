"""
GET /pvp-rankings: every pick in the published PvP guides, for the PvP page's
Rankings view, which counts how often the guides recommend each support card
and uma (board post "PvP rankings", owner's go 2026-10-27).

EARLY ACCESS ONLY (owner, 2026-10-27: "supporter only for now"). The guide
routes are open and the client locks them; this one checks on the server too
(benefits.early_access: a supporter, staff, or an account granted it), since
it reads every guide at once, the locked ones included. A guest gets 401,
anyone else 403.

ROWS, NOT COUNTS. The client does the counting, so its filters (style, card
type, deck tier, CM or LoH, how many upcoming events) are instant and the
rules for what counts live in one place, utils/pvpRankings.ts. The server
sends each guide's event and its rows, nothing it would have to agree with
the client about:

  cards  one per deck slot that holds a card: the card, the deck's style and
         label, its tier ("beginner" / "standard" / "whale", else "other"
         for honorable mentions and one-off decks), and its role: "main",
         "borrow" (the borrowed card, counted like any other: owner), or
         "alt" (an "or Kitasan Black" alternative, shown but not counted)
  umas   one per starred uma: the uma, the style, the stars

Never sent: placeholder cards ("Your best SSR Speed card" is no card to
pull), note-only slots ("Any Wit"), and drafts. Past events are sent; the
client knows the dates (the calculator payload) and keeps the upcoming ones.

Three queries whatever the guide count, and no cache: a few thousand small
rows, fetched only when an early-access reader opens the view. A cache would
need every guide save to invalidate it for no measurable gain.
"""

from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import benefits
from calculatorapi.models import PvpDeckSlot, PvpGuide, PvpUmaRecommendation

TIERS = ("beginner", "standard", "whale")


def deck_tier(label):
    """A deck's label as a tier: the three the guides keep for every style,
    whatever their case, else "other"."""
    word = label.strip().lower()
    return word if word in TIERS else "other"


def slot_role(is_borrow, order):
    """An alternative (order 1 and up) first, a borrowed card's included."""
    if order > 0:
        return "alt"
    return "borrow" if is_borrow else "main"


def rankings_payload():
    """{"guides": [{"kind", "event", "cards": [...], "umas": [...]}]}, one per
    published guide, in no particular order."""
    guides = {}
    for guide_id, cm_id, loh_id in PvpGuide.objects.filter(is_published=True).values_list(
        "id", "champions_meeting_id", "league_of_heroes_id"
    ):
        guides[guide_id] = {
            "kind": "cm" if cm_id else "loh",
            "event": cm_id or loh_id,
            "cards": [],
            "umas": [],
        }

    slots = (
        PvpDeckSlot.objects.filter(
            deck__guide__is_published=True,
            support_card__isnull=False,
            support_card__is_placeholder=False,
        )
        .order_by("deck__guide_id", "deck__order", "deck_id", "slot", "order")
        .values_list("deck__guide_id", "deck__style", "deck__label", "is_borrow", "order", "support_card_id")
    )
    for guide_id, style, label, is_borrow, order, card_id in slots:
        guides[guide_id]["cards"].append({
            "card": card_id,
            "style": style,
            "deck": label,
            "tier": deck_tier(label),
            "role": slot_role(is_borrow, order),
        })

    umas = (
        PvpUmaRecommendation.objects.filter(guide__is_published=True)
        .order_by("guide_id", "order", "id")
        .values_list("guide_id", "style", "uma_id", "stars")
    )
    for guide_id, style, uma_id, stars in umas:
        guides[guide_id]["umas"].append({"uma": uma_id, "style": style, "stars": stars})

    return {"guides": list(guides.values())}


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def pvp_rankings(request):
    if not benefits.early_access(request.user):
        return Response(
            {"error": "Rankings are open to supporters first."}, status=403
        )
    return Response(rankings_payload())
