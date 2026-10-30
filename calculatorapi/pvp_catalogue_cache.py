"""Server-side cache for ``GET /pvp-catalogue``, the team builder's catalogue.

A sibling of public_payload_cache.py, not a reuse of it. Read that module's
docstring first: everything it says about the one-process LocMem constraint,
the bulk writers the signals cannot see, and why the receivers live at module
scope with ``weak=False`` applies here unchanged. Generalising that module was
considered and rejected (pvp plan, R2 "Catalogue"): it is the hot route's
load-bearing cache, and sixty lines of duplication are cheaper than touching
it. Once both exist, folding them into one class is a later, optional refactor.

WHAT IS DIFFERENT
-----------------
* An ALLOWLIST of relevant models rather than a denylist. The catalogue is
  every pickable uma, every support card with art and every skill on global,
  with the two skill junctions, and nothing else. A new content model cannot
  change it, so there is no "forgotten model" to fail safe against, and a
  denylist would drop the catalogue on every guide edit and every team save.
* A different key, so dropping one cache never drops the other.

The payload does not depend on the date or the viewer, so it is safe to hold
until one of those five tables changes.
"""

import threading

from django.core.cache import cache

# Bump the suffix when the payload's SHAPE changes.
CACHE_KEY = "pvp_catalogue:v4"

# Backstop only, as in public_payload_cache.py. Left at five minutes: this
# rebuild is ~4MB, not ~47MB, and runs only while someone has the builder open.
CACHE_TTL_SECONDS = 300

# One rebuild at a time, for the reason public_payload_cache.rebuild_lock gives.
rebuild_lock = threading.Lock()

# The models whose rows ARE the catalogue. Lower-case model names.
_RELEVANT_MODELS = frozenset({
    "uma",
    "supportcard",
    "skill",
    "umaskill",
    "supportcardskill",
})


def read():
    """The cached catalogue as JSON bytes, or None on a miss."""
    return cache.get(CACHE_KEY)


def store(json_bytes):
    cache.set(CACHE_KEY, json_bytes, CACHE_TTL_SECONDS)


def invalidate(**_kwargs):
    """Drop the cached catalogue. Also the signal receiver, hence **_kwargs."""
    cache.delete(CACHE_KEY)


def _invalidate_now_and_on_commit():
    """Drop it now AND once the write commits. Why both:
    public_payload_cache._invalidate_now_and_on_commit."""
    from django.db import transaction  # pylint: disable=import-outside-toplevel
    invalidate()
    transaction.on_commit(invalidate)


def affects_catalogue(sender):
    """True if saving/deleting `sender` should drop the cached catalogue."""
    meta = getattr(sender, "_meta", None)
    if meta is None:
        return False
    return meta.app_label == "calculatorapi" and meta.model_name in _RELEVANT_MODELS


# Module scope on purpose, and connected with weak=False: a receiver nothing
# holds is garbage collected and silently stops firing. The full story is
# above the receivers in public_payload_cache.py.


def _on_write(sender, **kwargs):
    if affects_catalogue(sender):
        _invalidate_now_and_on_commit()


def _on_m2m(sender, instance=None, **kwargs):
    if affects_catalogue(sender) or affects_catalogue(type(instance)):
        _invalidate_now_and_on_commit()


def connect_invalidation_signals():
    """Wire invalidation to catalogue writes. Called from AppConfig.ready()."""
    # pylint: disable=import-outside-toplevel
    from django.db.models.signals import m2m_changed, post_delete, post_save

    post_save.connect(_on_write, weak=False, dispatch_uid="pvp_catalogue_cache_save")
    post_delete.connect(_on_write, weak=False, dispatch_uid="pvp_catalogue_cache_delete")
    m2m_changed.connect(_on_m2m, weak=False, dispatch_uid="pvp_catalogue_cache_m2m")
