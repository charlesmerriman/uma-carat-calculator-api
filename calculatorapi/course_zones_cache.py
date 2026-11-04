"""Server-side cache for ``GET /race-courses/<id>/skill-zones``.

A sibling of skills_cache.py, built the same way (read its docstring, and
public_payload_cache.py's, for the one-process LocMem constraint and why the
receivers are connected with ``weak=False``).

* Its OWN alias, "course-zones" (settings.CACHES), one entry per course, so
  the maps never crowd "default".
* A GENERATION, bumped by any write to a course or a skill, since a skill's
  condition changes its zones on every course and LocMem can't delete by
  prefix. `import_game_data` writes skills in bulk, which signals can't see,
  so it calls invalidate() itself, like every bulk writer; the TTL is the
  backstop for anything else.
"""

import uuid

from django.core.cache import caches

CACHE_ALIAS = "course-zones"
GENERATION_KEY = "course-zones:generation"
# Bump the suffix when the payload's SHAPE changes.
# v2: the verdict fields (skill_verdicts.py); v3: an accel's `useful`;
# v4: `paths`, the chain bound and the backstretch (the skill pass).
ZONES_KEY = "course-zones:v4:{generation}:{course_id}"
CACHE_TTL_SECONDS = 1800

_RELEVANT_MODELS = frozenset({"racecourse", "skill"})


def _cache():
    return caches[CACHE_ALIAS]


def _generation():
    generation = _cache().get(GENERATION_KEY)
    if generation is None:
        generation = uuid.uuid4().hex
        _cache().set(GENERATION_KEY, generation, None)
    return generation


def read(course_id):
    """The cached map as JSON bytes, or None on a miss."""
    return _cache().get(ZONES_KEY.format(generation=_generation(), course_id=course_id))


def store(course_id, json_bytes):
    _cache().set(ZONES_KEY.format(generation=_generation(), course_id=course_id), json_bytes, CACHE_TTL_SECONDS)


def invalidate(**_kwargs):
    """Move to a new generation. Also the signal receiver, hence **_kwargs."""
    _cache().set(GENERATION_KEY, uuid.uuid4().hex, None)


def _invalidate_now_and_on_commit():
    """Now AND once the write commits: public_payload_cache._invalidate_now_and_on_commit."""
    from django.db import transaction  # pylint: disable=import-outside-toplevel
    invalidate()
    transaction.on_commit(invalidate)


def _on_write(sender, **_kwargs):
    meta = getattr(sender, "_meta", None)
    if meta is not None and meta.app_label == "calculatorapi" and meta.model_name in _RELEVANT_MODELS:
        _invalidate_now_and_on_commit()


def connect_invalidation_signals():
    """Wire invalidation to course and skill writes. Called from AppConfig.ready()."""
    # pylint: disable=import-outside-toplevel
    from django.db.models.signals import post_delete, post_save

    post_save.connect(_on_write, weak=False, dispatch_uid="course_zones_cache_save")
    post_delete.connect(_on_write, weak=False, dispatch_uid="course_zones_cache_delete")
