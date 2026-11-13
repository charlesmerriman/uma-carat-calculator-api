"""
GET /skills and GET /skills/<game_id>: every skill a player may read about,
for the site's Skills page and the guide editor's skill picker.

WHICH SKILLS: Skill.objects.shown(), any skill with a name (models/skill.py,
shown_q): global's, UmaTL's, or the Japanese one when it has neither (owner
and Daptrius, 2026-11-12). A JP-only skill is included and says so
(`availability`), and `uses_translation` says when its text is UmaTL's, which
the site credits.

THE LIST is small rows for searching and filtering (~2,000, a few hundred KB
raw): the shown name, every other name the skill answers to
(Skill.search_names: the translation, aliases, the Japanese one), colour,
version and where it is. THE DETAIL is
one skill in full: its text, when it fires and what it does
(skill_text.describe), every outfit and card that gives it, what it evolves
from and into, and what unlocks an evolution.

SUPPORTERS AND STAFF FIRST (since 2026-11-12, the Encyclopedia): both sit
behind pvp_guide_editing.sees_skills_preview, the site's early-access rule.
A guest gets 401 and anyone else 403, as /rankings/* does. The cached bytes
are the same for every reader let in and hold nothing about a person.

Both are cached as rendered bytes in their own cache alias
(skills_cache.py). Keyed by the game's skill id, which is the same in every
database and in both game clients, so a page's URL never changes.
"""

import json

from django.http import Http404, HttpResponse
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import pvp_guide_editing, skill_text, skills_cache
from calculatorapi.models import Skill, UmaSkill
from calculatorapi.models.skill import skill_families
from calculatorapi.skill_types import skill_type
from calculatorapi.views.pvp_catalogue import image_url

# A unique's inherited copy is the same id with its leading 1 made a 9:
# Special Week's Shooting Star is 100011, the copy a parent passes on 900011.
# Checked 2026-11-12: all 272 uniques with such an id have their copy, and no
# copy is without its unique. A ★1/★2 outfit's starting unique (five digits)
# has none.
UNIQUE_IDS, INHERITED_IDS = range(100000, 200000), range(900000, 1000000)
INHERITED_OFFSET = 800000


def _shown():
    return Skill.objects.shown().select_related("evolves_from")


def summary(skill):
    """The fields a list row and a detail page share."""
    return {
        "id": skill.id,
        "game_id": skill.game_id,
        "name": skill.shown_name,
        "image": image_url(skill.image.name if skill.image else ""),
        "rarity": skill.rarity,
        "tier": skill.tier,
        "group_id": skill.group_id,
        "version": skill.version_label,
        "is_evolved": skill.evolves_from_id is not None,
        "availability": skill.availability,
        "uses_translation": skill.uses_translation,
        # Skill points; null for a unique, which isn't bought.
        "cost": skill.cost,
        # The Type filter's key (skill_types.py).
        "type": skill_type(skill.icon_id, skill.effects),
    }


class Owners:
    """
    Whose skill a skill is, for a whole build in two queries: the outfits a
    unique or an evolution belongs to (an inherited copy takes its unique's),
    and the unique / inherited pairing. Search matches a unique by the uma's
    name (Daptrius's item 11), and an evolution names its uma instead of
    reading "(evolved)".
    """

    def __init__(self):
        self.names = {}
        rows = (UmaSkill.objects.filter(source__in=("unique", "evolved"))
                .values_list("skill__game_id", "uma__name").order_by("uma__name"))
        for game_id, name in rows:
            self.names.setdefault(game_id, []).append(name)
        self.shown_ids = set(Skill.objects.shown().values_list("game_id", flat=True))

    def pair(self, game_id):
        """The game id of a unique's inherited copy, or of a copy's unique."""
        if game_id in UNIQUE_IDS:
            other = game_id + INHERITED_OFFSET
        elif game_id in INHERITED_IDS:
            other = game_id - INHERITED_OFFSET
        else:
            return None
        return other if other in self.shown_ids else None

    def of(self, game_id):
        names = self.names.get(game_id)
        if names is None and game_id in INHERITED_IDS:
            names = self.names.get(game_id - INHERITED_OFFSET)
        return list(dict.fromkeys(names or []))

    def fields(self, game_id):
        return {"owners": self.of(game_id), "pair": self.pair(game_id)}


def build_list():
    owners = Owners()
    return [
        {**summary(skill), "other_names": skill.search_names, **owners.fields(skill.game_id)}
        for skill in _shown().prefetch_related("aliases").by_shown_name()
    ]


def sources(skill):
    """Every outfit and card that gives the skill, on-global ones first."""
    umas = [
        {"id": row.uma_id, "name": row.uma.name, "game_id": row.uma.game_id,
         "image": image_url(row.uma.portrait.name if row.uma.portrait else ""),
         "source": row.source, "level": row.level, "on_global": row.uma.is_on_global}
        for row in skill.umas.select_related("uma").order_by(
            "-uma__is_on_global", "source", "level", "uma__name",
        )
    ]
    cards = [
        {"id": row.support_card_id, "name": row.support_card.name,
         # A card's name is its character's, shared by all her cards; the title tells them apart.
         "title": row.support_card.title,
         "game_id": row.support_card.game_id,
         "image": image_url(row.support_card.image.name if row.support_card.image else ""),
         # The frameless texture for FramedCard; None until it is uploaded.
         "image_borderless": image_url(
             row.support_card.image_borderless.name if row.support_card.image_borderless else ""
         ),
         "source": row.source, "on_global": row.support_card.is_on_global,
         # For "Hide R" / "Hide SR" (Daptrius's item 3) and the type icon.
         "rarity": row.support_card.rarity, "card_type": row.support_card.card_type}
        for row in skill.support_cards.select_related("support_card").order_by(
            "-support_card__is_on_global", "source", "support_card__name",
        )
    ]
    return {"umas": umas, "cards": cards}


def evolution(skill, owners):
    """What this skill evolves from and into, and what unlocks it if evolved.
    Only skills a page may show are named (shown_q), which since 2026-11-12
    includes a step known only by its Japanese name. Each `into` row names
    the outfit that evolves it (`owners`)."""
    base = skill.evolves_from
    return {
        "from": summary(base) if base and base.is_shown else None,
        "into": [
            {**summary(row), "owners": owners.of(row.game_id)}
            for row in Skill.objects.shown().filter(evolves_from=skill)
            .select_related("evolves_from").by_shown_name()
        ],
        "conditions": unlock_conditions(skill),
        "scenario": skill.evolution_scenario,
    }


def unlock_conditions(skill):
    """What unlocks an evolved skill, worded as a player reads it: global's
    English, else UmaTL's, else the Japanese (flagged). Shared by the skill's
    page and the uma page, which lists them under each evolution (Daptrius,
    2026-11-13). Prefetch `evolution_conditions` when calling it in a loop."""
    return [
        {"text": condition.shown_text or condition.jp_text,
         "uses_translation": not condition.text and bool(condition.translated_text),
         "is_japanese": not condition.shown_text}
        for condition in skill.evolution_conditions.all()
    ]


def build_detail(skill):
    owners = Owners()
    pair_id = owners.pair(skill.game_id)
    pair = _shown().filter(game_id=pair_id).first() if pair_id else None
    return {
        **summary(skill),
        "other_names": skill.search_names,
        "owners": owners.of(skill.game_id),
        # The unique's inherited copy, or the copy's unique, as a list row.
        "pair": summary(pair) if pair else None,
        "description": skill.shown_description,
        "readable": skill_text.describe(skill, skill_text.context_from_database()),
        "sources": sources(skill),
        "evolution": evolution(skill, owners),
        # Its white, gold and evolved forms on global (skill_families): the
        # same "other versions" a guide's skill chip lists.
        "versions": [summary(row) for row in skill_families([skill]).get(skill.pk, [])],
    }


REFUSED = {"error": "The Encyclopedia is open to supporters first."}


def refused(request):
    """The 403 for a signed-in reader without early access, else None. A guest
    never gets this far: IsAuthenticated answers 401 first. Shared with
    views/encyclopedia.py, so the skill and Encyclopedia pages open together."""
    if pvp_guide_editing.sees_skills_preview(request.user):
        return None
    return Response(REFUSED, status=403)


def _bytes(payload):
    return json.dumps(payload, separators=(",", ":")).encode()


def _json(body):
    # Bytes already rendered, so JsonResponse would undo the caching.
    return HttpResponse(body, content_type="application/json")  # pylint: disable=http-response-with-content-type-json


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def skill_list(request):
    if (refusal := refused(request)) is not None:
        return refusal
    body = skills_cache.read_list()
    if body is None:
        body = _bytes(build_list())
        skills_cache.store_list(body)
    return _json(body)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def skill_detail(request, game_id):
    if (refusal := refused(request)) is not None:
        return refusal
    body = skills_cache.read_detail(game_id)
    if body is None:
        skill = _shown().prefetch_related("aliases").filter(game_id=game_id).first()
        if skill is None:
            raise Http404("No skill with that id.")
        body = _bytes(build_detail(skill))
        skills_cache.store_detail(game_id, body)
    return _json(body)
