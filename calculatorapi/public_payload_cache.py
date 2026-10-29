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

So the public half is built once, rendered to JSON bytes, gzipped, and kept. A
guest is then answered with those gzip bytes verbatim -- no queries, no
serializers, no JSON parsing or rendering, no compression. A signed-in user gets
the same catalogue bytes with their own ~9KB of user-scoped keys STITCHED onto
the end (see "STITCHING" below): only their part is serialized or compressed.

WHY GZIP, AND WHY ONCE
----------------------
Until 2026-10 the cache held the raw JSON and every guest hit did
``json.loads`` + a DRF re-render of it: 12.5ms of CPU and a 9MB burst of Python
objects per request locally, several times that on the production shared vCPU.
Under a banner-launch crowd on 2026-10-14 that pinned the CPU at 99% and pushed
the 512MB instance into an out-of-memory restart. Serving the stored bytes as
they are costs 0.04ms and 1.2MB.

The bytes are gzipped at store time (level 6: 1.24MB -> ~103KB, ~8ms once per
rebuild) for two reasons. Each gunicorn thread is tied up for as long as it
takes to push the body out to the ingress, and a twelfth of the bytes is a
twelfth of that time. And compressing once here is the whole trick:
Django's GZipMiddleware would compress the megabyte again on every request,
which spends the CPU this module exists to save.

Only compressed bytes are kept, never the raw JSON beside them. The rare client
that does not accept gzip pays a ~1ms decompress instead. Both compressed forms
(the guest's and the signed-in head below) travel in ONE cache value, a
``CachedPayload``, so there is one thing to store, evict and invalidate and the
two can never drift apart.

STITCHING (THE SIGNED-IN RESPONSE)
----------------------------------
Until 2026-10-27 a signed-in read decompressed the cached JSON, ``json.loads``-ed
it into ~1MB of Python objects, merged the user keys in and let DRF render the
whole thing back out: ~36ms and a ~10MB burst per request locally, sent as
1.27MB uncompressed, for a user part of ~9KB. Two in three /app loads are
signed in, so that was most of the server's work on a banner day.

Now the cache also keeps a "head": the public payload WITHOUT the six user
keys, minus its closing ``}``, compressed as an unfinished deflate stream. A
signed-in request renders only its own six keys as ``,"user_plans":...}``,
compresses just that, and ``signed_in_gzip()`` joins the two into one gzip
file. The JSON it decompresses to is the head's keys followed by the user's,
each key once.

Why joining compressed pieces is legal: deflate (the compression inside gzip)
is a sequence of blocks, and only the last one is marked final. Flushing the
head with ``Z_SYNC_FLUSH`` ends it on a byte boundary WITHOUT that mark, so a
second, independently compressed stream can follow it and the decoder simply
reads on. The second stream only refers back to its own bytes, which are
always within the decoder's reach. gzip's trailer (a CRC-32 and the length of
the uncompressed data) is computed by continuing the head's CRC over the tail,
so nothing is ever decompressed to produce it. The result is a single,
ordinary gzip member; pigz (parallel gzip) builds its files the same way.

The test that guards it decompresses with a strict single-member decoder and
compares the JSON against the old merge, key for key.

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

import gzip
import struct
import threading
import zlib
from typing import NamedTuple

from django.core.cache import cache

# Bump the suffix when the payload's SHAPE changes (a key added or removed, a
# serializer field altered) or the stored FORMAT does. A deploy does not clear
# the cache on its own -- the process restart does -- but an explicit version
# means a rolling restart can never serve a half-old shape to a new frontend.
# v2 (2026-10): the value became gzip bytes instead of raw JSON.
# v3 (2026-10-27): the value became a CachedPayload (guest gzip + signed-in head).
CACHE_KEY = "calculator_data:public:v3"

# Level 6 is gzip's default. Level 9 saves another 3KB for twice the time,
# level 1 costs 30KB more for less than half; neither moves the needle when
# it runs once per content edit.
GZIP_LEVEL = 6

# Backstop only. With one worker the signal below does the real work, so an
# admin edit shows at once whatever this says. What it does bound is the writes
# the signal cannot see: a bulk writer that forgets invalidate(), and anything
# run through `doctl apps console`, which is its own process with its own cache
# and so cannot clear this one.
#
# It was 300 until 2026-10-27, and that cost more than it looked. Every expiry
# is a full rebuild (~47MB of Python objects at its peak, measured locally),
# 288 of them a day, and the memory a rebuild borrows is kept by the process
# afterwards, not handed back to the OS. Busy moments overlapped several of
# them (see rebuild_lock below) and pushed the 512MB instance past DO's 80%
# memory alert. Thirty minutes is six times fewer rebuilds, and still short
# enough that a console write shows up within a coffee break.
CACHE_TTL_SECONDS = 1800

# Held by whichever request finds the cache empty, for as long as it takes to
# rebuild and store the payload. Without it, every gunicorn thread that misses
# in the same few seconds runs its own copy of the ~2s rebuild: four threads,
# four 47MB bursts at once, for four identical answers. With it the first
# thread builds, the rest wait on the lock and then read what it stored.
#
# A plain threading.Lock is enough because this is ONE process (see above);
# it does nothing across processes, which is one more thing a move to Redis
# would need to replace. The waiters are not slower than before: they would
# otherwise have spent the same seconds building the payload themselves.
rebuild_lock = threading.Lock()

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
    # A player's PvP teams. Saved by auto-save, so the 2026-09-23 lesson above
    # applies in full. Served by /pvp-teams, never here.
    "pvpteam",
    "pvpteammember",
    "pvpteamcard",
    # Logged CM / LoH results: the CM Runs view auto-saves on every
    # dropdown change. Served by /pvp-runs, never here.
    "pvprun",
    "pvprunrunner",
    "pvprunrace",
    # A player's veterans: edited on the Collection page, written in bulk by
    # an import. Served by /trained-umas, never here.
    "traineduma",
    "trainedumaskill",
    # What the player owns: tapped on the Collection grid, written in bulk
    # by an import. Served by /roster, never here.
    "owneduma",
    "ownedsupportcard",
    # The gallery. A vote, a view or a comment is a write on every click, so
    # the same lesson applies; none of it is on /calculator-data.
    "gallerypost",
    "galleryvote",
    "gallerycomment",
    "gallerycommentvote",
    "galleryreport",
    "blockedterm",
    # Accounts and auth.
    "customuser",
    "socialaccount",
    # Their own endpoints; absent from /calculator-data entirely.
    # A spark is named only on one veteran's GET /trained-umas/<id>.
    "spark",
    "changelogentry",
    "changelogchange",
    "patreontier",
    "patreonsupporter",
    "patreoncredentials",
    # Written by sync_changelog on deploy and, later, on every moderation card.
    "discordmessage",
    # The team board's queue, read only by the bot API.
    "teamqueueentry",
    # The Rankings page's start-of-week snapshots, written on a rankings rebuild
    # (rankings_snapshots.py). Read only by /rankings/*, never here.
    "rankingssnapshot",
    "sitepage",
    "faqcategory",
    "faqitem",
    # The PvP guide's children: served by GET /pvp-guides, never here. NOT the
    # guide itself, whose is_published feeds has_guide on both race arrays, so
    # publishing or unpublishing a guide must drop the payload. A few rebuilds
    # per event is fine.
    "pvpstatconfig",
    "pvpumarecommendation",
    "pvpskillrecommendation",
    "pvpstylenote",
    "pvpdeck",
    "pvpdeckslot",
    "pvpteamcomp",
    # A revision is written on every autosave of the site's guide editor.
    "pvpguiderevision",
    # Analytics counters. These are written on EVERY visit -- leaving them out
    # of the denylist would invalidate the cache continuously and make the whole
    # thing a no-op.
    "dailyvisit",
    "monthlyvisit",
    "visitorhash",
    "landingpagevisit",
    "referrervisit",
    # The admin analytics report's daily copy. Written once a day by a staff
    # page load or a deploy, never part of the catalogue.
    "analyticssnapshot",
})


class CachedPayload(NamedTuple):
    """The one cache value: both compressed forms of the public payload."""

    # The whole guest response, gzipped. Sent exactly as it is.
    guest_gzip: bytes
    # The public payload minus the user keys and its closing brace, as an
    # UNFINISHED deflate stream (see "STITCHING" above). Never sent on its own.
    head_deflate: bytes
    # CRC-32 and byte length of the head's uncompressed JSON, which the gzip
    # trailer of every stitched response is continued from.
    head_crc: int
    head_length: int


# A minimal gzip header (RFC 1952): magic, deflate, no flags, no timestamp,
# no extra-flags, OS "unknown". Python's own gzip.compress(mtime=0) writes the
# same ten bytes apart from the extra-flags byte, which decoders ignore.
_GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"


def _raw_deflater():
    # Negative wbits = raw deflate: no zlib/gzip wrapper, so the pieces can be
    # framed by hand into one gzip member.
    return zlib.compressobj(GZIP_LEVEL, zlib.DEFLATED, -zlib.MAX_WBITS)


def read_entry():
    """The cached CachedPayload, or None on a miss."""
    return cache.get(CACHE_KEY)


def read():
    """The cached GUEST payload as gzipped JSON bytes, or None on a miss.

    ``gzip.decompress()`` it for the JSON itself.
    """
    entry = read_entry()
    return None if entry is None else entry.guest_gzip


def store(guest_json, head_json):
    """Compress and keep the public payload, and return the CachedPayload.

    `guest_json` is the full guest response; `head_json` is the same payload
    rendered WITHOUT the user-scoped keys, which signed-in responses supply.
    Returned so the request that paid for the rebuild can answer from it
    without a second cache read.
    """
    # Drop the closing brace: the user keys are appended after the head's last
    # value, and signed_in_gzip() supplies the brace that closes them.
    head = head_json[:-1]
    deflater = _raw_deflater()
    # Z_SYNC_FLUSH, not finish: everything so far is written out and
    # byte-aligned, but the stream stays open for the tail to continue it.
    head_deflate = deflater.compress(head) + deflater.flush(zlib.Z_SYNC_FLUSH)
    entry = CachedPayload(
        guest_gzip=gzip.compress(guest_json, compresslevel=GZIP_LEVEL),
        head_deflate=head_deflate,
        head_crc=zlib.crc32(head),
        head_length=len(head),
    )
    cache.set(CACHE_KEY, entry, CACHE_TTL_SECONDS)
    return entry


def signed_in_gzip(entry, user_json):
    """One gzip file: the cached head with `user_json`'s keys stitched on.

    `user_json` is the rendered user-scoped half, a JSON object. Only it is
    compressed here (~9KB, well under a millisecond); the catalogue is reused
    as it was compressed at store time.
    """
    # '{"user_plans":...}' becomes ',"user_plans":...}': its keys continue the
    # head's object, and its closing brace closes the whole response.
    tail = b"," + user_json[1:]
    deflater = _raw_deflater()
    # A plain flush() finishes the stream: its last block carries the "final"
    # mark the head's flush deliberately left off.
    tail_deflate = deflater.compress(tail) + deflater.flush()
    trailer = struct.pack(
        "<II",
        # zlib.crc32's second argument continues a running CRC, so the CRC of
        # head + tail comes without the head's bytes in hand.
        zlib.crc32(tail, entry.head_crc),
        # gzip stores the uncompressed length modulo 2**32.
        (entry.head_length + len(tail)) & 0xFFFFFFFF,
    )
    return _GZIP_HEADER + entry.head_deflate + tail_deflate + trailer


# The date lookups a signed-in request needs to order and annotate its own rows
# (views/calculator.py build_user_context): resolved banner and campaign dates,
# and each card's first JP date. They are built from the same catalogue tables
# as the payload, so they go stale at the same moments and are dropped with it
# by invalidate(). Kept under their own key rather than inside CachedPayload so
# a guest's read never pays to unpickle them. Bump the suffix when the tuple's
# shape changes.
USER_CONTEXT_KEY = "calculator_data:user_context:v1"


def read_user_context():
    """The cached (emap, anniversary_emap, card_context), or None on a miss.

    LocMem unpickles on every read, so each caller gets its own copy and no
    request can mutate another's.
    """
    return cache.get(USER_CONTEXT_KEY)


def store_user_context(context):
    cache.set(USER_CONTEXT_KEY, context, CACHE_TTL_SECONDS)


def is_cached():
    """Whether the guest payload is in the cache right now, without reading it
    (a read would unpickle the whole entry). For GET /bot/health: a dropped
    cache is what made the 2026-09-15 and 09-23 outages."""
    return cache.has_key(CACHE_KEY)


def invalidate(**_kwargs):
    """Drop the cached payload and the user context built from the same
    tables. Also the signal receiver, hence **_kwargs.

    Deletes rather than rebuilds: the next reader repopulates it. Rebuilding
    here would put a multi-second job inside an admin save's request/response
    cycle, and would run once per row on a bulk edit.
    """
    cache.delete_many([CACHE_KEY, USER_CONTEXT_KEY])


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
