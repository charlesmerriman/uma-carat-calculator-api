"""Hourly cache for the Rankings page's payloads (views/rankings.py).

ONE BUILD AN HOUR, NO INVALIDATION. The owner's decision (2026-10-27,
notes/rankings-page-plan.md): every Rankings tab is built at most once an
hour and says how old it is ("Updated 40 minutes ago"). So unlike the
/calculator-data, catalogue and skills caches there are no signal receivers
here: a guide saved at 10:05 shows in the rankings by 11:05 at the latest,
and that is the promise. Each payload carries `generated_at` for the page's
"Updated" line.

ITS OWN ALIAS, "rankings" (settings.CACHES). The `default` cache's 40 slots
exist to keep the /calculator-data payload in memory (rules file
backend-models.md), and a per-event PvP payload per tab would crowd it.
Same single-process caveat as every LocMem cache here: two processes would
each build their own copy, an hour apart at worst.
"""

from django.core.cache import caches
from django.utils import timezone

CACHE_ALIAS = "rankings"
CACHE_TTL_SECONDS = 60 * 60
# Bump the version when a payload's SHAPE changes, so a deploy never serves
# an hour of the old shape to the new client. (A deploy restarts the process,
# which empties LocMem anyway; the version is for the day that changes.)
KEY = "rankings:{name}:v1"


def cached_payload(name, build):
    """The payload cached under `name`, built with `build()` on a miss and
    stamped with `generated_at` (ISO 8601, UTC). `name` includes any
    parameters the payload depends on ("pvp:cm:21")."""
    key = KEY.format(name=name)
    cache = caches[CACHE_ALIAS]
    payload = cache.get(key)
    if payload is None:
        payload = {"generated_at": timezone.now().isoformat(), **build()}
        cache.set(key, payload, CACHE_TTL_SECONDS)
    return payload
