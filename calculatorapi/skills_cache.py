"""Server-side cache for ``GET /skills``, ``GET /skills/<game_id>`` and the
Encyclopedia's ``GET /encyclopedia/<kind>/<game_id>`` pages.

The Encyclopedia's uma and card pages live here too (2026-11-12) because they
are built from exactly the tables the skill pages are, so one generation and
one set of signals covers all of them.

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
* The SHARED LOOKUPS (since 2026-11-23): the part of a skill page that is the
  same for every skill (views/skills.SharedLookups: whose skill each one is,
  the names its text needs, what is out on Global) is one more entry here,
  under the same generation, so an edit drops it with the pages. Before, each
  page built its own: about 3,850 rows to show a handful, and the web build's
  2,223 skill pages read over 8 million of them on every deploy.
  A page built from them EXPIRES WITH THEM (`store_detail`'s `built_at`), never
  a full TTL after. "Out on Global" depends on the clock, not only on writes
  (global_release.py), and a page kept 5 minutes past lookups that were
  already 5 minutes old would let the tag lag a release by 10.
"""

import math
import time
import uuid

from django.core.cache import caches

CACHE_ALIAS = "skills"
GENERATION_KEY = "skills:generation"
# Bump the suffix when a payload's SHAPE changes.
LIST_KEY = "skills:list:v4:{generation}"
DETAIL_KEY = "skills:detail:v6:{generation}:{game_id}"
# An Encyclopedia page: `kind` is "uma" or "card" (views/encyclopedia.py).
PAGE_KEY = "skills:page:v10:{generation}:{kind}:{game_id}"
# The lookups every skill page shares (views/skills.SharedLookups), one entry.
SHARED_KEY = "skills:shared:v1:{generation}"

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


def store_detail(game_id, json_bytes, built_at=None):
    """Keep a skill page. `built_at` is when the shared lookups it was built
    from were (SharedLookups.built_at): the page expires with them."""
    timeout = CACHE_TTL_SECONDS if built_at is None else seconds_left(built_at)
    _cache().set(DETAIL_KEY.format(generation=_generation(), game_id=game_id), json_bytes, timeout)


def seconds_left(built_at):
    """What remains of the TTL of something built at `built_at` (a time.time()).
    0 once it has run out, which the cache takes as "don't keep this"."""
    return max(0, math.ceil(built_at + CACHE_TTL_SECONDS - time.time()))


def read_shared():
    """The shared lookups for this generation, or None on a miss."""
    return _cache().get(SHARED_KEY.format(generation=_generation()))


def store_shared(shared):
    """Keep the shared lookups, for the TTL from their `built_at`."""
    _cache().set(SHARED_KEY.format(generation=_generation()), shared, seconds_left(shared.built_at))


def read_page(kind, game_id):
    """One cached Encyclopedia page as JSON bytes, or None on a miss."""
    return _cache().get(PAGE_KEY.format(generation=_generation(), kind=kind, game_id=game_id))


def store_page(kind, game_id, json_bytes):
    _cache().set(
        PAGE_KEY.format(generation=_generation(), kind=kind, game_id=game_id),
        json_bytes, CACHE_TTL_SECONDS,
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
