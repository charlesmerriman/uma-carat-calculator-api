"""
The Rankings page's routes (/app/rankings, notes/rankings-page-plan.md),
one per tab:

  GET /rankings/guides                     every pick in the published PvP guides
  GET /rankings/gallery/<kind>/<event_id>  one event's gallery posts, counted
                                           (the PvP page's Rankings tab)

EARLY ACCESS ONLY (owner, 2026-10-27: "Patreon only for now"). Every route
checks benefits.early_access on the server; a guest gets 401, anyone else
403. The page and its navbar item are drawn only for early access too, but
that is presentation: this is the gate.

HOURLY (rankings_cache.py). Each payload is built at most once an hour,
stamped with `generated_at`, and never invalidated: the page says how old it
is. Gzipped (`gzip_page`): the rows repeat their keys, so ~280 KB of JSON is
~12 KB on the wire. The site has no GZipMiddleware; /calculator-data gzips
its own.

GUIDES: ROWS, NOT COUNTS. The client does the counting, so the sheet's tick
boxes (card type, scenario, strategy, CM / LoH, deck tier) and the event
window are instant, and the rules for what counts live in one place,
utils/guideRankings.ts. The server sends each guide's event, its scenario
and its rows, nothing it would have to agree with the client about:

  cards  one per deck slot that holds a card: the card, the deck's id (so
         the client can count decks, for a card's share), style and tier
         (below), and its role: "main", "borrow" (the
         borrowed card, counted like any other: owner), or "alt" (an "or
         Kitasan Black" alternative, shown but not counted)
  umas   one per starred uma: the uma, the style, the stars

A deck's tier comes from its label. The guides keep Beginner, Standard and
Whale for every style, and most guides add an honorable mention deck, which
the sheet's "Include" filter switches on its own. Everything else is a
one-off deck named for its idea ("xoguri pace", "runaway suzuka"): about 20
of 650 decks on prod, 2026-10-27. Those are "other".

Never sent: placeholder cards ("Your best SSR Speed card" is no card to
pull), note-only slots ("Any Wit"), and drafts. Past events are sent; the
client picks the window.
"""

from collections import Counter, defaultdict

from django.db.models import F
from django.views.decorators.gzip import gzip_page
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import benefits, gallery, pvp_teams
from calculatorapi.models import PvpDeckSlot, PvpGuide, PvpUmaRecommendation, Scenario, SupportCard
from calculatorapi.rankings_cache import cached_payload

TIERS = ("beginner", "standard", "whale")
# A gallery member's borrowed card sits in slot 6 (gallery.snapshot_from_team).
BORROW_SLOT = 6
# Matched as the label's START, any case: prod has "Honorable mention",
# "Honorable mentions" and "Honorable mention deck".
HONORABLE = "honorable mention"

REFUSED = {"error": "Rankings are open to supporters first."}


def deck_tier(label):
    """A deck's label as a tier: "beginner" / "standard" / "whale" /
    "honorable", else "other"."""
    word = label.strip().lower()
    if word in TIERS:
        return word
    return "honorable" if word.startswith(HONORABLE) else "other"


def slot_role(is_borrow, order):
    """An alternative (order 1 and up) first, a borrowed card's included."""
    if order > 0:
        return "alt"
    return "borrow" if is_borrow else "main"


def guides_payload():
    """{"scenarios": [{"id", "name"}], "guides": [{"kind", "event",
    "scenario", "cards": [...], "umas": [...]}]}. The scenarios are the ones
    some published guide names, in release order (JP's, the order the
    sheet's filter lists them); the guides in no particular order. Four
    queries whatever the guide count."""
    guides = {}
    for guide_id, cm_id, loh_id, scenario_id in PvpGuide.objects.filter(is_published=True).values_list(
        "id", "champions_meeting_id", "league_of_heroes_id", "scenario_id"
    ):
        guides[guide_id] = {
            "kind": "cm" if cm_id else "loh",
            "event": cm_id or loh_id,
            "scenario": scenario_id,
            "cards": [],
            "umas": [],
        }
    _add_cards(guides)
    _add_umas(guides)
    return {"scenarios": _scenarios_in_use(guides), "guides": list(guides.values())}


def _add_cards(guides):
    slots = (
        PvpDeckSlot.objects.filter(
            deck__guide__is_published=True,
            support_card__isnull=False,
            support_card__is_placeholder=False,
        )
        .order_by("deck__guide_id", "deck__order", "deck_id", "slot", "order")
        .values_list(
            "deck__guide_id", "deck_id", "deck__style", "deck__label", "is_borrow", "order", "support_card_id",
        )
    )
    for guide_id, deck_id, style, label, is_borrow, order, card_id in slots:
        guides[guide_id]["cards"].append({
            "card": card_id,
            "deck": deck_id,
            "style": style,
            "tier": deck_tier(label),
            "role": slot_role(is_borrow, order),
        })


def _add_umas(guides):
    umas = (
        PvpUmaRecommendation.objects.filter(guide__is_published=True)
        .order_by("guide_id", "order", "id")
        .values_list("guide_id", "style", "uma_id", "stars")
    )
    for guide_id, style, uma_id, stars in umas:
        guides[guide_id]["umas"].append({"uma": uma_id, "style": style, "stars": stars})


def _scenarios_in_use(guides):
    used = {guide["scenario"] for guide in guides.values()} - {None}
    return list(
        Scenario.objects.filter(id__in=used)
        .order_by(F("banner_timeline__jp_start_date").asc(nulls_last=True), "id")
        .values("id", "name")
    )


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_guides(request):
    if not benefits.early_access(request.user):
        return Response(REFUSED, status=403)
    return Response(cached_payload("guides", guides_payload))


def gallery_payload(event):
    """One event's visible gallery posts, counted: {"posts", "decks", "umas":
    [{"uma", "teams", "styles": {style: members}}], "cards": [{"card",
    "decks", "borrows"}]}, most first.

    COUNTED HERE, unlike the guides: the tab has no filters, so the client
    needs totals, and a few hundred posts' snapshots stay on the server.
    An uma counts once per post with her (a team never repeats a character);
    a card once per member deck holding it, the borrowed card included (the
    owner's rule for the guides, kept here), with the borrows counted apart.
    Ties go to the lower id. `decks` is every member counted, a card's share's denominator; `posts`
    an uma's. Placeholder cards ("your best SSR Speed card") are no card to
    pull, so they never count. Two queries.
    """
    snapshots = list(gallery.visible_posts().filter(**event).values_list("team_snapshot", flat=True))
    card_ids = {card["support_card"] for snapshot in snapshots for member in snapshot for card in member["cards"]}
    placeholders = set(
        SupportCard.objects.filter(id__in=card_ids, is_placeholder=True).values_list("id", flat=True)
    )

    teams, styles = Counter(), defaultdict(Counter)
    decks, borrows = Counter(), Counter()
    members = 0
    for snapshot in snapshots:
        for member in snapshot:
            members += 1
            teams[member["uma"]] += 1
            styles[member["uma"]][member["style"]] += 1
            for card in member["cards"]:
                if card["support_card"] in placeholders:
                    continue
                decks[card["support_card"]] += 1
                if card["slot"] == BORROW_SLOT:
                    borrows[card["support_card"]] += 1

    return {
        "posts": len(snapshots),
        "decks": members,
        "umas": [
            {"uma": uma, "teams": count, "styles": dict(styles[uma])}
            for uma, count in _most_first(teams)
        ],
        "cards": [
            {"card": card, "decks": count, "borrows": borrows[card]}
            for card, count in _most_first(decks)
        ],
    }


def _most_first(counter):
    """(id, count), most first, ties by id so the order never depends on the posts'."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_gallery(request, kind, event_id):
    if not benefits.early_access(request.user):
        return Response(REFUSED, status=403)
    try:
        event = pvp_teams.resolve_event(kind, event_id)
    except pvp_teams.TeamRefused:
        return Response({"error": "Event not found"}, status=404)
    return Response(cached_payload(f"gallery:{kind}:{event_id}", lambda: gallery_payload(event)))
