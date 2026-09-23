"""Server-side cache for the public half of ``GET /calculator-data``.

WHY THIS EXISTS
---------------
``/calculator-data`` is the app's hot route: every visit to /app blocks on it
before anything renders. Measured against production it cost 1.7-3.7s TTFB
against a ~0.17s baseline for a small endpoint on the same service, for a
~1MB response. Practically all of that is the endpoint's own work -- the
catalogue serializes ~900 nested cards twice over (once under
``banner_timeline_data``, once under ``banner_uma_data``/``banner_support_data``)
and none of it varies per visitor.

So the public half is built once, rendered to JSON bytes, and kept. A guest is
then answered with those bytes verbatim -- no queries, no serializers, no JSON
rendering. A signed-in user pays a ``json.loads`` on them (single-digit ms) and
gets their own user-scoped keys merged in, still skipping the whole catalogue.

WHY IT IS SAFE TO HOLD INDEFINITELY
-----------------------------------
The public payload does not depend on the current date. ``predictions.py``,
``ledger.py`` and ``eligibility.py`` contain no ``now()``/``today()`` at all --
every "today"-relative decision is made client-side against the two anchors in
frontend/docs/resource-projection-logic.md. The cached bytes therefore go stale
only when CONTENT changes, which the signal below catches.

WHAT THIS DOES NOT CATCH
------------------------
Invalidation hangs off post_save/post_delete/m2m_changed, and those do not fire
for ``bulk_create()``, ``bulk_update()``, ``queryset.update()`` or raw SQL. A
management command that writes catalogue rows ONLY that way would leave the
cache stale until the TTL below expires. The two commands that bulk_create
junction rows today (repair_launch_banner, backfill_race_prep_supports) are
safe because each also ``create()``s the parent banner in the same transaction,
which does fire -- but a future bulk-only writer should call ``invalidate()``
itself rather than rely on that.

THE ONE DEPLOYMENT CONSTRAINT -- READ BEFORE SCALING UP
-------------------------------------------------------
This uses Django's local-memory cache, which lives inside a single Python
process. That is correct here because .do/app.yaml runs ``instance_count: 1``
and its gunicorn line passes no ``--workers``, so there is exactly one process:
one cache, and an admin write invalidates the same copy the next request reads.

Threads are fine: gunicorn.conf.py runs one process with several gthread
threads, and they all share this one cache (LocMemCache takes a lock).

Raise the instance count OR add gunicorn WORKERS and that stops holding -- each
process would carry its own cache, and ``invalidate()`` firing in the process
that handled the admin write would leave the others serving stale content.
``CACHE_TTL_SECONDS`` is the backstop for exactly that case: it bounds the
staleness instead of leaving it unbounded. If you do scale out and want edits to
land immediately again, move CACHES onto a shared backend (Redis) -- nothing
else in this module needs to change.
"""

from django.core.cache import cache

# Bump the suffix when the payload's SHAPE changes (a key added or removed, a
# serializer field altered). A deploy does not clear the cache on its own --
# the process restart does -- but an explicit version means a rolling restart
# can never serve a half-old shape to a new frontend.
CACHE_KEY = "calculator_data:public:v1"

# Backstop only. With one worker the signal below does the real work and this
# never matters; with several it caps how long a process can serve stale
# content. Five minutes is short enough not to confuse a content editor and
# long enough that the cache still absorbs essentially all traffic.
CACHE_TTL_SECONDS = 300

# Models whose rows CANNOT change the public payload. Everything else in the
# calculatorapi app invalidates.
#
# Deliberately a denylist rather than an allowlist: a content model added later
# and forgotten about will invalidate by default, which fails toward a cache
# that clears too eagerly (harmless, just a rebuild) rather than toward a page
# serving stale data (a real bug, and an invisible one). Adding a new entry here
# is a decision to make; leaving one out is not a trap.
_IRRELEVANT_MODELS = frozenset({
    # User-scoped: served per-request from the DB, never part of the cached half.
    "userplannedbanner",
    "userplannedpurchase",
    "userstepupselection",
    # Plans, their stats blocks and oshis are user-scoped too. Plan and
    # IncomeProfile shipped on 2026-09-17 and 2026-09-21 WITHOUT being listed
    # here, and every auto-save touches Plan.updated_at (views/calculator.py),
    # so each save dropped the catalogue and the next visitor paid the ~2s
    # rebuild. On 2026-09-23 a new banner's crowd of savers queued the single
    # worker solid and /app spun forever. A new user-owned model goes here the
    # day it is added; test_a_user_save_does_not_invalidate is the guard.
    "plan",
    "incomeprofile",
    "useroshi",
    # Accounts and auth.
    "customuser",
    "socialaccount",
    # Their own endpoints; absent from /calculator-data entirely.
    "changelogentry",
    "changelogchange",
    "patreontier",
    "patreonsupporter",
    "patreoncredentials",
    "sitepage",
    "faqcategory",
    "faqitem",
    # Analytics counters. These are written on EVERY visit -- leaving them out
    # of the denylist would invalidate the cache continuously and make the whole
    # thing a no-op.
    "dailyvisit",
    "monthlyvisit",
    "visitorhash",
})


def read():
    """The cached public payload as JSON bytes, or None on a miss."""
    return cache.get(CACHE_KEY)


def store(json_bytes):
    """Keep the rendered public payload for the next request."""
    cache.set(CACHE_KEY, json_bytes, CACHE_TTL_SECONDS)


def invalidate(**_kwargs):
    """Drop the cached payload. Also the signal receiver, hence **_kwargs.

    Deletes rather than rebuilds: the next reader repopulates it. Rebuilding
    here would put a multi-second job inside an admin save's request/response
    cycle, and would run once per row on a bulk edit.
    """
    cache.delete(CACHE_KEY)


def _invalidate_now_and_on_commit():
    """Drop the cache immediately AND again once the write is durable.

    post_save fires INSIDE the transaction, before commit. Dropping the cache
    only there leaves a window: a concurrent request can miss, rebuild from a
    database that does not yet contain the new row, and cache that stale answer
    -- which then survives until the next write or the TTL. Repeating the drop
    on commit closes it.

    Both, not just on_commit, because Django's TestCase wraps each test in an
    atomic block that is rolled back rather than committed, so on_commit
    callbacks never fire there. The immediate call is what keeps a cached
    payload from leaking between tests.

    Outside an atomic block on_commit runs its callback straight away, so this
    is simply two deletes -- and a delete of an absent key is free.
    """
    from django.db import transaction  # pylint: disable=import-outside-toplevel
    invalidate()
    transaction.on_commit(invalidate)


def affects_public_payload(sender):
    """True if saving/deleting `sender` should drop the cached payload."""
    meta = getattr(sender, "_meta", None)
    if meta is None:
        return False
    return (
        meta.app_label == "calculatorapi"
        and meta.model_name not in _IRRELEVANT_MODELS
    )


# THE RECEIVERS LIVE AT MODULE SCOPE ON PURPOSE -- READ BEFORE MOVING THEM
# -----------------------------------------------------------------------
# Django's Signal.connect() defaults to weak=True: it stores a WEAK reference
# to the receiver, so a receiver nothing else holds is garbage collected and
# silently stops firing. Never an error, never a log line -- the signal simply
# does nothing from then on.
#
# These two were previously nested inside connect_invalidation_signals(). The
# function's frame was their only strong reference, so they died the moment it
# returned and cache invalidation was dead in every process that ran with
# DEBUG=False -- production included, where the 5-minute TTL below was silently
# the ONLY thing refreshing content. It appeared to work locally because with
# DEBUG=True something incidentally retained the frame and kept them alive.
#
# Defined here, the module holds them for the life of the process. weak=False
# is then belt-and-braces: it states the requirement at the call site, so
# nesting them again cannot quietly resurrect the bug.


def _on_write(sender, **kwargs):
    if affects_public_payload(sender):
        _invalidate_now_and_on_commit()


def _on_m2m(sender, instance=None, **kwargs):
    # For an M2M the sender is the THROUGH model; the interesting object is
    # `instance`, the row whose relation changed. Either side is enough of a
    # reason to drop the cache.
    if affects_public_payload(sender) or affects_public_payload(type(instance)):
        _invalidate_now_and_on_commit()


def connect_invalidation_signals():
    """Wire cache invalidation to content writes. Called from AppConfig.ready().

    m2m_changed is connected alongside the row signals because the catalogue's
    card lists are many-to-many. Editing a banner's cards through the admin's
    M2M widget rewrites the junction rows without necessarily saving the banner
    itself, so post_save on the parent alone would miss it.
    """
    # pylint: disable=import-outside-toplevel
    # Imported here, not at module scope: this module is reached from
    # AppConfig.ready(), and importing the signal machinery any earlier risks
    # touching the model registry before Django has finished populating it.
    from django.db.models.signals import m2m_changed, post_delete, post_save

    # dispatch_uid keeps a second ready() (the autoreloader, or a test that
    # reloads apps) from registering duplicate receivers. weak=False keeps them
    # alive -- see the note above the receivers.
    post_save.connect(
        _on_write, weak=False, dispatch_uid="calculator_data_cache_save")
    post_delete.connect(
        _on_write, weak=False, dispatch_uid="calculator_data_cache_delete")
    m2m_changed.connect(
        _on_m2m, weak=False, dispatch_uid="calculator_data_cache_m2m")
