"""
GET /skills and GET /skills/<game_id>: every skill a player may read about,
for the site's Skills page and the guide editor's skill picker.

WHICH SKILLS: Skill.objects.shown(), any skill with an English name, global's
or UmaTL's (models/skill.py, shown_q). A JP-only skill is included and says so
(`availability`), and `uses_translation` says when its text is UmaTL's, which
the site credits. One with only a Japanese name is left out.

THE LIST is small rows for searching and filtering (~2,000, a few hundred KB
raw): the shown name, every other name the skill answers to (the translation,
old names, the Japanese one), colour, version and where it is. THE DETAIL is
one skill in full: its text, when it fires and what it does
(skill_text.describe), every outfit and card that gives it, what it evolves
from and into, and what unlocks an evolution.

SHELVED (2026-10-23): both answer 404 to anyone but a guide editor
(pvp_guide_editing.sees_skills_preview) until the work is picked up again.

Both are cached as rendered bytes in their own cache alias
(skills_cache.py). Keyed by the game's skill id, which is the same in every
database and in both game clients, so a page's URL never changes.
"""

import json

from django.http import Http404, HttpResponse
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes

from calculatorapi import pvp_guide_editing, skill_text, skills_cache
from calculatorapi.models import Skill
from calculatorapi.models.skill import skill_families
from calculatorapi.views.pvp_catalogue import image_url


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
    }


def other_names(skill):
    """Every name besides the shown one, for search: the translation when
    global's name is shown, old names kept as aliases, the Japanese name."""
    names = [skill.translated_name] if skill.name and skill.translated_name else []
    names += [alias.name for alias in skill.aliases.all()]
    names.append(skill.jp_name)
    shown = skill.shown_name
    return list(dict.fromkeys(name for name in names if name and name != shown))


def build_list():
    return [
        {**summary(skill), "other_names": other_names(skill)}
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
         "game_id": row.support_card.game_id,
         "image": image_url(row.support_card.image.name if row.support_card.image else ""),
         "source": row.source, "on_global": row.support_card.is_on_global}
        for row in skill.support_cards.select_related("support_card").order_by(
            "-support_card__is_on_global", "source", "support_card__name",
        )
    ]
    return {"umas": umas, "cards": cards}


def evolution(skill):
    """What this skill evolves from and into, and what unlocks it if evolved.
    Only skills a page may show are named; a step with no English name is left
    out rather than shown in Japanese."""
    base = skill.evolves_from
    return {
        "from": summary(base) if base and base.is_shown else None,
        "into": [
            summary(row) for row in Skill.objects.shown().filter(evolves_from=skill)
            .select_related("evolves_from").by_shown_name()
        ],
        "conditions": [
            {"text": condition.shown_text or condition.jp_text,
             "uses_translation": not condition.text and bool(condition.translated_text),
             "is_japanese": not condition.shown_text}
            for condition in skill.evolution_conditions.all()
        ],
        "scenario": skill.evolution_scenario,
    }


def build_detail(skill):
    return {
        **summary(skill),
        "other_names": other_names(skill),
        "description": skill.shown_description,
        "readable": skill_text.describe(skill, skill_text.context_from_database()),
        "sources": sources(skill),
        "evolution": evolution(skill),
        # Its white, gold and evolved forms on global (skill_families): the
        # same "other versions" a guide's skill chip lists.
        "versions": [summary(row) for row in skill_families([skill]).get(skill.pk, [])],
    }


def _bytes(payload):
    return json.dumps(payload, separators=(",", ":")).encode()


def _json(body):
    # Bytes already rendered, so JsonResponse would undo the caching.
    return HttpResponse(body, content_type="application/json")  # pylint: disable=http-response-with-content-type-json


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def skill_list(request):
    # Shelved: the guide editors only (pvp_guide_editing.sees_skills_preview).
    if not pvp_guide_editing.sees_skills_preview(request.user):
        raise Http404("No such page.")
    body = skills_cache.read_list()
    if body is None:
        body = _bytes(build_list())
        skills_cache.store_list(body)
    return _json(body)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def skill_detail(request, game_id):
    if not pvp_guide_editing.sees_skills_preview(request.user):
        raise Http404("No such page.")
    body = skills_cache.read_detail(game_id)
    if body is None:
        skill = _shown().prefetch_related("aliases").filter(game_id=game_id).first()
        if skill is None:
            raise Http404("No skill with that id.")
        body = _bytes(build_detail(skill))
        skills_cache.store_detail(game_id, body)
    return _json(body)
