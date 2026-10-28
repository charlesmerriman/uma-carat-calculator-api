"""Half-day cache for the Rankings page's payloads (views/rankings.py).

ONE BUILD EVERY HALF DAY, NO INVALIDATION. The owner's decision: every
Rankings tab is built at most once every 12 hours and says how old it is
("Updated 5 hours ago"). Hourly at first (2026-10-27,
notes/rankings-page-plan.md); half a day since 2026-10-28
(notes/rankings-speed-plan.md), since a cold Pull plans build took 31 s of
the one CPU. So unlike the /calculator-data, catalogue and skills caches
there are no signal receivers here: a guide saved at 10:05 shows in the
rankings by 22:05 at the latest, and that is the promise. Each payload carries `generated_at` for the page's
"Updated" line.

ITS OWN ALIAS, "rankings" (settings.CACHES). The `default` cache's 40 slots
exist to keep the /calculator-data payload in memory (rules file
backend-models.md), and a per-event PvP payload per tab would crowd it.
Same single-process caveat as every LocMem cache here: two processes would
each build their own copy, half a day apart at worst.
"""

from django.core.cache import caches
from django.utils import timezone

from calculatorapi.rankings_snapshots import attach_previous

CACHE_ALIAS = "rankings"
CACHE_TTL_SECONDS = 12 * 60 * 60
# Bump the version when a payload's SHAPE changes, so a deploy never serves
# half a day of the old shape to the new client. (A deploy restarts the process,
# which empties LocMem anyway; the version is for the day that changes.)
KEY = "rankings:{name}:v4"


def cached_payload(name, build, public=None):
    """The payload cached under `name`, built with `build()` on a miss and
    stamped with `generated_at` (ISO 8601, UTC). `name` includes any
    parameters the payload depends on ("pvp:cm:21").

    `public` turns the counted payload into what is sent (rankings_shares.py:
    shares, never a count of people). The snapshot gets the counted one, the
    cache and the browser only `public`'s.

    A build also carries `previous` (the week's start, for rank movement) and
    may rotate that snapshot and post the week's movers
    (rankings_snapshots.attach_previous). Cached together, so the snapshot
    table is read at most once every half day per name."""
    key = KEY.format(name=name)
    cache = caches[CACHE_ALIAS]
    payload = cache.get(key)
    if payload is None:
        now = timezone.now()
        payload = attach_previous(name, {"generated_at": now.isoformat(), **build()}, now)
        if public is not None:
            payload = public(payload)
        cache.set(key, payload, CACHE_TTL_SECONDS)
    return payload
