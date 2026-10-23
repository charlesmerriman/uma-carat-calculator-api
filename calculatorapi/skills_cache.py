"""Server-side cache for ``GET /skills`` and ``GET /skills/<game_id>``.

A sibling of pvp_catalogue_cache.py, built the same way: read that module's
docstring (and public_payload_cache.py's before it) for the one-process LocMem
constraint, the bulk writers signals cannot see, and why the receivers are
module-level functions connected with ``weak=False``.

WHAT IS DIFFERENT
-----------------
* Its OWN cache alias, "skills" (settings.CACHES). One entry per skill page
  is up to ~2,200 keys, and in "default" they would crowd out the
  /calculator-data payload, whose 40 slots exist to keep it in memory.
* A GENERATION instead of a delete. A write anywhere in the skill tables
  changes every skill's page (a renamed card shows on every skill it teaches),
  and LocMem cannot delete by prefix, so every key carries the current
  generation and a write moves to a new one. Old entries are never read again
  and age out with the TTL or the bound.
"""

import uuid

from django.core.cache import caches

CACHE_ALIAS = "skills"
GENERATION_KEY = "skills:generation"
# Bump the suffix when a payload's SHAPE changes.
LIST_KEY = "skills:list:v1:{generation}"
DETAIL_KEY = "skills:detail:v1:{generation}:{game_id}"

# Backstop only, as in pvp_catalogue_cache.py.
CACHE_TTL_SECONDS = 300

# The models whose rows the two payloads are built from. Lower-case names.
_RELEVANT_MODELS = frozenset({
    "skill",
    "skillalias",
    "skillevolutioncondition",
    "umaskill",
    "supportcardskill",
    "uma",
    "supportcard",
})


def _cache():
    return caches[CACHE_ALIAS]


def _generation():
    generation = _cache().get(GENERATION_KEY)
    if generation is None:
        generation = uuid.uuid4().hex
        _cache().set(GENERATION_KEY, generation, None)
    return generation


def read_list():
    """The cached list as JSON bytes, or None on a miss."""
    return _cache().get(LIST_KEY.format(generation=_generation()))


def store_list(json_bytes):
    _cache().set(LIST_KEY.format(generation=_generation()), json_bytes, CACHE_TTL_SECONDS)


def read_detail(game_id):
    return _cache().get(DETAIL_KEY.format(generation=_generation(), game_id=game_id))


def store_detail(game_id, json_bytes):
    _cache().set(
        DETAIL_KEY.format(generation=_generation(), game_id=game_id), json_bytes, CACHE_TTL_SECONDS,
    )


def invalidate(**_kwargs):
    """Move to a new generation. Also the signal receiver, hence **_kwargs."""
    _cache().set(GENERATION_KEY, uuid.uuid4().hex, None)


def _invalidate_now_and_on_commit():
    """Now AND once the write commits: public_payload_cache._invalidate_now_and_on_commit."""
    from django.db import transaction  # pylint: disable=import-outside-toplevel
    invalidate()
    transaction.on_commit(invalidate)


def affects_skills(sender):
    """True if saving/deleting `sender` should drop the cached skill pages."""
    meta = getattr(sender, "_meta", None)
    if meta is None:
        return False
    return meta.app_label == "calculatorapi" and meta.model_name in _RELEVANT_MODELS


def _on_write(sender, **kwargs):
    if affects_skills(sender):
        _invalidate_now_and_on_commit()


def connect_invalidation_signals():
    """Wire invalidation to skill-table writes. Called from AppConfig.ready()."""
    # pylint: disable=import-outside-toplevel
    from django.db.models.signals import post_delete, post_save

    post_save.connect(_on_write, weak=False, dispatch_uid="skills_cache_save")
    post_delete.connect(_on_write, weak=False, dispatch_uid="skills_cache_delete")
