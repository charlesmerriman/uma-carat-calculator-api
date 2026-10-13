"""
GET /pvp-catalogue: everything the PvP team builder's picker searches.

Public, read-only, the same for everyone, and cached (pvp_catalogue_cache.py).
Fetched once by the builder the first time a picker opens, never on /app load.

    umas           Uma.pickable(): id, name, image, portrait, rarity,
                   running_style, character_id, is_variant, apt
                   ({turf, dirt, ..., end}, 1..8 = G..S, null when unknown)
                   and skills [{skill, source, level}]
    support_cards  every card with art: id, name, image, card_type, rarity,
                   title and skills [{skill, source}]
    skills         skills ON GLOBAL only: id, name, description, image,
                   rarity, tier, group_id, cost

Skills are a lookup table rather than embedded in each card: one skill is
taught by dozens of cards, and the picker's "search by skill" walks from a
skill to its cards anyway. A junction row pointing at a skill not on global is
dropped, so a player never meets a skill the game has not given them yet (the
rule in SkillQuerySet.on_global).

WHY NOT /umas AND /calculator-data
----------------------------------
/umas is three fields a row for the oshi picker, and /calculator-data knows
only cards that were on a banner, with no skills or aptitudes at all. This is
the one route that has both, and it is big (~500 KB raw, ~100 KB gzipped),
which is why it is its own lazily-fetched, cached route.

Built from plain values() queries rather than serializers: five queries for
the whole catalogue, and a few thousand junction rows that a nested
serializer would walk one by one.
"""

import json
from collections import defaultdict

from django.core.files.storage import default_storage
from django.http import HttpResponse
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes

from calculatorapi import pvp_catalogue_cache
from calculatorapi.models import Skill, SupportCard, SupportCardSkill, Uma, UmaSkill

# The uma's ten aptitude columns, keyed by the short names the client uses.
APTITUDE_FIELDS = (
    "turf", "dirt", "short", "mile", "medium", "long",
    "front", "pace", "late", "end",
)


def _url(name):
    """An image field's stored name -> its URL, or None when unset. The same
    URL an ImageField serializer gives with no request in its context, which
    is how /calculator-data's cached payload builds them too."""
    return default_storage.url(name) if name else None


def build_catalogue():
    """The catalogue as a plain dict. The view caches it rendered."""
    skills = list(
        Skill.objects.on_global()
        .order_by("name", "id")
        .values("id", "name", "description", "image", "rarity", "tier", "group_id", "cost")
    )
    for skill in skills:
        skill["image"] = _url(skill["image"])
    global_ids = {skill["id"] for skill in skills}

    uma_skills = defaultdict(list)
    for row in UmaSkill.objects.filter(skill_id__in=global_ids).values(
        "uma_id", "skill_id", "source", "level"
    ).order_by("uma_id", "source", "level", "skill_id"):
        uma_skills[row["uma_id"]].append(
            {"skill": row["skill_id"], "source": row["source"], "level": row["level"]}
        )

    card_skills = defaultdict(list)
    for row in SupportCardSkill.objects.filter(skill_id__in=global_ids).values(
        "support_card_id", "skill_id", "source"
    ).order_by("support_card_id", "source", "skill_id"):
        card_skills[row["support_card_id"]].append(
            {"skill": row["skill_id"], "source": row["source"]}
        )

    umas = []
    for uma in Uma.pickable().order_by("name", "id"):
        umas.append({
            "id": uma.id,
            "name": uma.name,
            "image": _url(uma.image.name),
            # The borderless cut when there is one (Uma.portrait). The builder
            # draws the bordered art; the portrait is there for the drawer's
            # small tiles, where a rarity border reads as a frame in a frame.
            "portrait": _url(uma.portrait.name),
            "rarity": uma.rarity,
            "running_style": uma.running_style,
            # Two outfits of one character may not share a team (the game's
            # CM rule). The client checks with this; pvp_teams.py re-checks.
            "character_id": uma.character_id,
            "is_variant": uma.is_costume_variant,
            "apt": {key: getattr(uma, f"apt_{key}") for key in APTITUDE_FIELDS},
            "skills": uma_skills.get(uma.id, []),
        })

    support_cards = []
    for card in (
        SupportCard.objects.exclude(image="").exclude(image__isnull=True)
        .order_by("name", "id")
        .values("id", "name", "image", "card_type", "rarity", "title")
    ):
        card["image"] = _url(card["image"])
        card["skills"] = card_skills.get(card["id"], [])
        support_cards.append(card)

    return {"umas": umas, "support_cards": support_cards, "skills": skills}


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def pvp_catalogue(request):  # pylint: disable=unused-argument
    """Answered with the cached bytes verbatim. Unlike /calculator-data there
    is no per-user half to merge in, so nothing here needs the parsed dict."""
    body = pvp_catalogue_cache.read()
    if body is None:
        body = json.dumps(build_catalogue(), separators=(",", ":")).encode()
        pvp_catalogue_cache.store(body)
    # Bytes already rendered, so JsonResponse (which serialises a dict) would
    # undo the point of caching them.
    return HttpResponse(body, content_type="application/json")  # pylint: disable=http-response-with-content-type-json
