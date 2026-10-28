"""Half-day cache for the Rankings page's payloads (views/rankings.py).

ONE BUILD EVERY HALF DAY, NO INVALIDATION. The owner's decision: every
Rankings tab is built at most once every 12 hours and says how old it is
("Updated 5 hours ago"). Hourly at first (2026-10-27,
notes/rankings-page-plan.md); half a day since 2026-10-28
(notes/rankings-speed-plan.md), since a cold Pull plans build took 31 s of
the one CPU. So unlike the /calculator-data, catalogue and skills caches
there are no signal receivers here: a guide saved at 10:05 shows in the
rankings by 22:05 at the latest (plus one build), and that is the promise.
Each payload carries `generated_at` for the page's "Updated" line.

NOBODY WAITS FOR A REBUILD (notes/rankings-speed-plan.md PR 3; the owner,
2026-10-28: "keep for a half day, then rebuild"). Three places a payload can
come from, in order:

  1. the `rankings` LocMem cache (this process's copy);
  2. the RankingsPayload row (the last build, which outlives a deploy, so a
     restart starts warm);
  3. a build, inline: only when neither exists, i.e. the first build ever
     for a key. The only time a visitor waits.

A copy older than STALE_AFTER is still served at once; the request that
notices starts ONE rebuild in a background thread, and the next visitor gets
the new build. A per-key lock keeps two requests from rebuilding the same key.

ITS OWN ALIAS, "rankings" (settings.CACHES). The `default` cache's 40 slots
exist to keep the /calculator-data payload in memory (rules file
backend-models.md), and a per-event PvP payload per tab would crowd it.
Same single-process caveat as every LocMem cache here: two processes would
each keep their own copy, and the lock below would only hold within one.
"""

import datetime
import json
import logging
import threading

from django.core.cache import caches
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.utils import timezone

from calculatorapi.models import RankingsPayload
from calculatorapi.rankings_snapshots import attach_previous

logger = logging.getLogger(__name__)

CACHE_ALIAS = "rankings"
STALE_AFTER = datetime.timedelta(hours=12)
# Bump the version when a payload's SHAPE changes, so a deploy never serves
# the old shape to the new client: the stored rows are keyed with it too, so
# a bump leaves them unread and the first visit builds the new shape.
KEY = "rankings:{name}:v4"

# Keys with a rebuild running in this process, behind one lock.
_rebuilding = set()
_rebuilding_lock = threading.Lock()


def _in_thread(job):
    threading.Thread(target=job, name="rankings-rebuild", daemon=True).start()


def run_inline(job):
    """The runner the tests use (tests/base.py): a test's rows sit in an
    uncommitted transaction that a thread's own connection can't see."""
    job()


# How a background rebuild runs. Swapped for run_inline in tests only.
run_rebuild = _in_thread


def cached_payload(name, build, public=None):
    """The payload cached under `name`, stamped with `generated_at` (ISO 8601,
    UTC). `name` includes any parameters the payload depends on ("pvp:cm:21").
    Built with `build()` only when no copy exists at all; a stale copy is
    served and rebuilt behind it (module docstring).

    `public` turns the counted payload into what is sent (rankings_shares.py:
    shares, never a count of people). The snapshot gets the counted one; the
    cache, the stored row and the browser only `public`'s.

    A build also carries `previous` (the week's start, for rank movement) and
    may rotate that snapshot and post the week's movers
    (rankings_snapshots.attach_previous)."""
    key = KEY.format(name=name)
    cache = caches[CACHE_ALIAS]
    payload = cache.get(key)
    if payload is None:
        stored = RankingsPayload.objects.filter(key=key).values_list("payload", flat=True).first()
        if stored is None:
            return _build_and_keep(key, name, build, public)
        payload = stored
        cache.set(key, payload, None)
    if timezone.now() - datetime.datetime.fromisoformat(payload["generated_at"]) >= STALE_AFTER:
        _start_rebuild(key, name, build, public)
    return payload


def _build_and_keep(key, name, build, public):
    """Build, stamp, keep in both places, and return the payload as sent."""
    now = timezone.now()
    payload = attach_previous(name, {"generated_at": now.isoformat(), **build()}, now)
    if public is not None:
        payload = public(payload)
    # Through JSON once, so the copy from the cache and the copy from the
    # table are the same bytes on the wire (dates as DjangoJSONEncoder writes
    # them), whichever one a visitor gets.
    payload = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
    RankingsPayload.objects.update_or_create(key=key, defaults={"generated_at": now, "payload": payload})
    caches[CACHE_ALIAS].set(key, payload, None)
    return payload


def _start_rebuild(key, name, build, public):
    """One background rebuild of `key`, unless one is already running."""
    with _rebuilding_lock:
        if key in _rebuilding:
            return
        _rebuilding.add(key)

    def job():
        try:
            _build_and_keep(key, name, build, public)
        except Exception:  # pylint: disable=broad-exception-caught
            # The stale copy keeps serving; the next request past STALE_AFTER
            # tries again. Logged so the run log shows why.
            logger.exception("Rankings rebuild failed: %s", key)
        finally:
            with _rebuilding_lock:
                _rebuilding.discard(key)
            # A thread's connection is its own; Django closes a request's at
            # the end of the request, and nothing would close this one.
            if run_rebuild is not run_inline:
                connection.close()

    run_rebuild(job)
