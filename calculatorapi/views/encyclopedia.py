"""
GET /encyclopedia/umas/<game_id> and GET /encyclopedia/cards/<game_id>: one
outfit's or one support card's page in the Encyclopedia
(notes/encyclopedia-plan.md). The lists need no endpoint of their own: the
web draws them from GET /pvp-catalogue, which is public and already loaded.

WHO: the Encyclopedia is supporters and staff first (owner, 2026-11-12), so
both answer as GET /skills does: a guest 401, anyone without early access 403
(views/skills.refused). The bytes are the same for every reader let in and
hold nothing about a person, so one cached copy serves all of them. "You own
this" is worked out in the browser from the Collection, never here.

KEYED BY THE GAME'S ID, never the row's pk or a name: names change (a JP-only
uma takes its Global name on release, and 146 card names are shared) but the
game's id is the same in every database and both clients, so a pasted link
keeps working.

WHAT A PAGE CARRIES, and what it leaves to the client:
  * The facts the import owns (aptitudes, stats at every star, growth, type,
    the Japanese name), the editors' `purpose`, and every skill the outfit or
    card gives, as the Skills page's summary row plus where it comes from.
  * Its relatives: the character's other outfits and support cards.
  * NO DATES and NO BANNERS. The client already has every banner, with its
    predicted dates, from /calculator-data; reading them there keeps date
    prediction in one place. The page sends `id` (the row's pk) so the client
    can find the outfit or card on those banners and in the Collection.

WHAT NEVER APPEARS: admin_comments (editors' notes), a placeholder card (a
stand-in, not a game card), an uma that can't race (casual outfits and NPCs:
Uma.trainees()), and any skill Skill.objects.shown() leaves out.
"""

from django.db.models import Q
from django.http import Http404
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes

from calculatorapi import skills_cache
from calculatorapi.models import Skill, SupportCard, SupportCardSkill, Uma, UmaSkill
from calculatorapi.views.pvp_catalogue import image_url
from calculatorapi.views.skills import _bytes, _json, refused, summary

APTITUDE_KEYS = ("turf", "dirt", "short", "mile", "medium", "long", "front", "pace", "late", "end")
STAT_KEYS = ("speed", "stamina", "power", "guts", "wit")

# The order a page lists an outfit's skills in, the order the game teaches them.
UMA_SKILL_ORDER = {"unique": 0, "innate": 1, "awakening": 2, "evolved": 3}
CARD_SKILL_ORDER = {"hint": 0, "event": 1}


def _file_url(field):
    return image_url(field.name if field else "")


def base_name(uma_name):
    """The character's name from an outfit row's: "Special Week (Summer)" ->
    "Special Week". A group card names its members this way (group_members)."""
    return uma_name.split(" (", 1)[0].strip()


def _skill_rows(rows):
    """The page's skill list from UmaSkill or SupportCardSkill rows: the Skills
    page's summary of each (cost and type included), plus where it comes from."""
    found = []
    for row in rows:
        skill = row.skill
        found.append({
            **summary(skill),
            "source": row.source,
            "level": getattr(row, "level", None),
            "evolves_from": skill.evolves_from.game_id if skill.evolves_from_id else None,
        })
    return found


def _shown_skill_ids():
    return Skill.objects.shown().values("id")


def _outfit_row(uma):
    return {
        "id": uma.id, "game_id": uma.game_id, "name": uma.name, "title": uma.title,
        "portrait": _file_url(uma.portrait), "rarity": uma.rarity, "on_global": uma.is_on_global,
    }


def _card_row(card):
    return {
        "id": card.id, "game_id": card.game_id, "name": card.name, "title": card.title,
        "image": _file_url(card.image), "card_type": card.card_type, "rarity": card.rarity,
        "on_global": card.is_on_global,
    }


def _outfits_of(character_ids, names=()):
    """The trainee outfits of these characters (by the game id's range), and of
    these character NAMES for the outfits global hasn't numbered alike."""
    if not character_ids and not names:
        return []
    match = Q()
    for character in character_ids:
        match |= Q(game_id__gte=character * 100, game_id__lt=(character + 1) * 100)
    for name in names:
        match |= Q(name=name) | Q(name__startswith=f"{name} (")
    return [_outfit_row(uma) for uma in Uma.trainees().filter(match).order_by("game_id")]


def _cards_of(character_id, name):
    """The character's support cards: their own (by character id) and every
    group card that lists them. A group card is filed under ONE member's
    character id, so it is matched by its members instead, never by id."""
    cards = SupportCard.objects.filter(is_placeholder=False).exclude(game_id=None)
    own = cards.filter(character_id=character_id).exclude(card_type="group") if character_id else cards.none()
    groups = [card for card in cards.filter(card_type="group") if name in card.group_members]
    return [_card_row(card) for card in sorted([*own, *groups], key=lambda card: card.game_id)]


def build_uma(uma):
    skills = (
        UmaSkill.objects.filter(uma=uma, skill_id__in=_shown_skill_ids())
        .select_related("skill__evolves_from")
    )
    character = uma.character_id
    return {
        "id": uma.id,
        "game_id": uma.game_id,
        "name": uma.name,
        "title": uma.title,
        "jp_name": uma.jp_name,
        "jp_title": uma.jp_title,
        "portrait": _file_url(uma.portrait),
        "image": _file_url(uma.image),
        "rarity": uma.rarity,
        "running_style": uma.running_style,
        "on_global": uma.is_on_global,
        "purpose": uma.purpose,
        "apt": {key: getattr(uma, f"apt_{key}") for key in APTITUDE_KEYS},
        "base": {key: getattr(uma, f"base_{key}") for key in STAT_KEYS},
        "growth": {key: getattr(uma, f"growth_{key}") for key in STAT_KEYS},
        "star_stats": uma.star_stats,
        "skills": sorted(
            _skill_rows(skills),
            key=lambda row: (UMA_SKILL_ORDER.get(row["source"], 9), row["level"] or 0, row["name"]),
        ),
        "outfits": [row for row in _outfits_of([character]) if row["id"] != uma.id],
        "cards": _cards_of(character, base_name(uma.name)),
    }


def build_card(card):
    skills = (
        SupportCardSkill.objects.filter(support_card=card, skill_id__in=_shown_skill_ids())
        .select_related("skill__evolves_from")
    )
    is_group = card.card_type == "group"
    # A group card's people are its members, by name; any other card's is its
    # own character, by the game id's range.
    outfits = (
        _outfits_of([], card.group_members) if is_group
        else _outfits_of([card.character_id] if card.character_id else [], [card.name])
    )
    others = [] if is_group else [
        row for row in _cards_of(card.character_id, card.name) if row["id"] != card.id
    ]
    return {
        "id": card.id,
        "game_id": card.game_id,
        "name": card.name,
        "title": card.title,
        "jp_name": card.jp_name,
        "jp_title": card.jp_title,
        "image": _file_url(card.image),
        "image_borderless": _file_url(card.image_borderless),
        "card_type": card.card_type,
        "rarity": card.rarity,
        "character_id": card.character_id,
        "on_global": card.is_on_global,
        "released_at": card.global_release_at.isoformat() if card.global_release_at else None,
        "group_members": card.group_members,
        "purpose": card.purpose,
        "skills": sorted(
            _skill_rows(skills),
            key=lambda row: (CARD_SKILL_ORDER.get(row["source"], 9), row["name"]),
        ),
        "outfits": outfits,
        "cards": others,
    }


def _page(request, kind, game_id, find, build):
    """The shared shape of both endpoints: gate, cache, build on a miss."""
    if (refusal := refused(request)) is not None:
        return refusal
    body = skills_cache.read_page(kind, game_id)
    if body is None:
        row = find(game_id)
        if row is None:
            raise Http404(f"No {kind} with that id.")
        body = _bytes(build(row))
        skills_cache.store_page(kind, game_id, body)
    return _json(body)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def encyclopedia_uma(request, game_id):
    return _page(
        request, "uma", game_id,
        lambda game_id: Uma.trainees().filter(game_id=game_id).first(), build_uma,
    )


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def encyclopedia_card(request, game_id):
    return _page(
        request, "card", game_id,
        lambda game_id: SupportCard.objects.filter(game_id=game_id, is_placeholder=False).first(),
        build_card,
    )
