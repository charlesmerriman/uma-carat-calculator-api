"""
A request limit per visitor, so one browser can't take the API down
(notes/rate-limit-plan.md).

On 2026-11-22 one browser stuck in a reload loop made 700-880 requests a minute
for over an hour and held the API's one CPU at 75-100%. The busiest normal
browser that night made 43 a minute. This middleware counts each visitor's
requests and, past a limit far above anything a person does, answers 429 for
ten minutes without doing any of the request's work.

WHO "ONE VISITOR" IS
--------------------
Two keys per request, each with its own counters. A request is refused when
either key is blocked.

- The ACCOUNT: the Authorization header, hashed. Read raw, with no database
  lookup: this runs before authentication, which is the point (a refused
  request should cost nothing). A made-up token gets a fresh key every time;
  catching that is the address key's job.
- The ADDRESS: `do-connecting-ip`, which App Platform sets to the client's
  address. Not `X-Forwarded-For`'s first entry: the browser can write that one
  (visits.client_ip's docstring). An IPv6 address counts by its /64, because
  one home line is handed a whole /64 and could rotate through it. Needed
  because some requests carry no token at all: the 2026-11-22 loop began on
  the home page, whose /changelog, /supporters and /site-content send none.

Both are hashed with a key derived from SECRET_KEY before they touch the cache,
so the cache never holds a raw address or token, and nothing here logs either.

FAIL OPEN
---------
No `do-connecting-ip` (DO's own health checks, local dev, the test client), or
a value that isn't an address: no address key, so no address limit. Falling
back to REMOTE_ADDR instead would put every visitor behind DO's proxy on ONE
counter and refuse the whole site at once.

THE RULES (settings.RATE_LIMIT_WINDOWS, RATE_LIMIT_BLOCK_SECONDS)
------------------------------------------------------------------
Each rule is "at most N requests in a window of S seconds": 300 a minute
catches a flood fast, 1,200 in ten minutes catches a script tuned to stay just
under 300. Crossing either blocks the key for ten minutes. A request made while
blocked is refused without being counted, and doesn't extend the block, so a
tab that keeps retrying is free again ten minutes later.

The windows are FIXED (12:00:00-12:00:59, then 12:01:00-...): one number per
key per window, `add` then `incr`. DRF's throttles instead keep a list of every
request's time per client and rewrite it on each request, which at 300 a
minute is a 300-item list pickled per request. A fixed window can let up to
twice a limit through across a boundary, which doesn't matter at limits this
far above normal use.

MODES (settings.RATE_LIMIT_ACCOUNT_MODE, RATE_LIMIT_ADDRESS_MODE)
------------------------------------------------------------------
"enforce" refuses. "observe" counts and logs what it WOULD have refused, and
refuses nothing: the address half ships this way until the release checks that
`do-connecting-ip` holds the visitor and can't be forged. "off" skips the key.
Anything else fails when the middleware loads, which is at boot: App Platform
then keeps the running deployment, which beats a limit that is quietly not
there.

THE BUILD
---------
Every web build fetches about 3,080 Encyclopedia pages from the live API
(scripts/prerender.mjs in the web repo), which would trip the address limit
and fail the deploy. A request whose `X-Build-Key` matches
settings.RATE_LIMIT_BUILD_KEY skips both keys. A leaked key only gets back to
the behaviour before this file existed.

WHY A MIDDLEWARE, NOT A DRF THROTTLE
------------------------------------
DRF throttles run after authentication (a database lookup) and content
negotiation, so a refused request would still cost work; a view that sets its
own `throttle_classes` replaces the default list and would drop a global one;
and plain Django views (the share pages, the admin) never see them. This runs
before all of that, on every route. It sits after CorsMiddleware in MIDDLEWARE
so a 429 still carries CORS headers, and after WhiteNoise so static files
never count.

WHAT IS LOGGED
--------------
One line per block (or would-block): which key kind, which rule, the count, the
path and the referer's path. Every ten minutes, one summary line, written by
the first request after the boundary (there is no scheduler): requests seen,
distinct keys of each kind, the busiest key's count under each rule, blocks
and refusals, build-key requests, and how many requests carried
`do-connecting-ip` and how many of those matched `X-Forwarded-For`'s first
entry. Counts only. The summary is how the release decides the address half
is safe to enforce.

ONE PROCESS
-----------
The counters live in the "ratelimit" LocMem alias, inside this one gunicorn
process (gunicorn.conf.py). A second worker or instance would each keep its
own, which in effect multiplies every limit by the number of copies, until the
cache moves to a shared backend (notes/hot-route-capacity-plan.md).
"""


import collections
import hmac
import ipaddress
import logging
import math
import threading
import time
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse
from django.utils.crypto import salted_hmac

logger = logging.getLogger(__name__)

CACHE_ALIAS = "ratelimit"

ENFORCE = "enforce"
OBSERVE = "observe"
OFF = "off"
MODES = (ENFORCE, OBSERVE, OFF)

ACCOUNT = "account"
ADDRESS = "address"

# Request.META names for the two headers read here.
ADDRESS_HEADER = "HTTP_DO_CONNECTING_IP"
BUILD_KEY_HEADER = "HTTP_X_BUILD_KEY"

# How often the summary line is written, in seconds.
SUMMARY_SECONDS = 600

# Set on a refused request, so RequestStatsMiddleware files it under one route
# label instead of thousands of "(no route)" lines in the bot's alert.
REFUSED_ATTRIBUTE = "rate_limited"

REFUSAL_TEXT = "Too many requests. Try again in a few minutes."

# The salt for the key hashes, so these digests can't be compared with any
# other HMAC the project makes from SECRET_KEY.
_SALT = "calculatorapi.rate_limit"

# Keys are cut to this many hex characters: 80 bits, far past any chance of
# two visitors sharing one, and short in the cache.
_DIGEST_LENGTH = 20


def _now():
    """The clock, as a function the tests can replace."""
    return time.time()


def _digest(kind, value):
    return f"{kind}:{salted_hmac(_SALT, value, algorithm='sha256').hexdigest()[:_DIGEST_LENGTH]}"


def account_key(request):
    """The request's Authorization header, hashed; None without one."""
    header = request.META.get("HTTP_AUTHORIZATION", "").strip()
    return _digest("a", header) if header else None


def visitor_address(request):
    """The address App Platform says the request came from, as an
    ipaddress object, or None when there is no usable one.

    If the header ever holds a list, the LAST entry is taken: a proxy that
    appends puts its own view of the client last, after anything the client
    sent. With one address, the last entry is that address."""
    raw = request.META.get(ADDRESS_HEADER, "")
    candidate = raw.split(",")[-1].strip()
    if not candidate:
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    # "::ffff:203.0.113.7" is an IPv4 visitor written as IPv6: count it as
    # the IPv4 address, or one visitor would have two counters.
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def address_key(request):
    """The visitor's address (an IPv6 one by its /64), hashed; None when the
    header is missing or isn't an address (see "Fail open")."""
    address = visitor_address(request)
    if address is None:
        return None
    if address.version == 6:
        address = ipaddress.ip_network(f"{address}/64", strict=False).network_address
    return _digest("ip", str(address))


def _keys_for(request):
    """[(kind, mode, key)] for every kind that is on and has a key here."""
    keys = []
    for kind, mode, key_for in (
        (ACCOUNT, settings.RATE_LIMIT_ACCOUNT_MODE, account_key),
        (ADDRESS, settings.RATE_LIMIT_ADDRESS_MODE, address_key),
    ):
        if mode == OFF:
            continue
        key = key_for(request)
        if key is not None:
            keys.append((kind, mode, key))
    return keys


def _is_build(request):
    expected = settings.RATE_LIMIT_BUILD_KEY
    if not expected:
        return False
    return hmac.compare_digest(request.META.get(BUILD_KEY_HEADER, ""), expected)


def _blocked_name(key):
    return f"{key}:blocked"


def _blocks(cache, keys, now):
    """(when the latest ENFORCED block among the keys ends, or None; the set
    of keys under any block, enforced or observed)."""
    enforced_until = None
    held = set()
    for _, mode, key in keys:
        # The stored end time decides, not only the cache's own expiry, so
        # the block ends on this module's clock (the one the tests move).
        until = cache.get(_blocked_name(key))
        if until is None or until <= now:
            continue
        held.add(key)
        if mode == ENFORCE:
            enforced_until = max(enforced_until or until, until)
    return enforced_until, held


def _count(cache, key, seconds, now):
    """Add this request to the key's current window of `seconds`; return the
    window's count including it."""
    name = f"{key}:{seconds}:{int(now // seconds)}"
    # A few seconds past the window, so the key outlives every request in it.
    timeout = seconds + 5
    cache.add(name, 0, timeout=timeout)
    try:
        return cache.incr(name)
    except ValueError:
        # Gone between add and incr: LocMem culls a third of its keys when it
        # fills. Start the window again from this request.
        cache.set(name, 1, timeout=timeout)
        return 1


def _path_only(url):
    """A referer's path, without its host or query string."""
    return urlsplit(url).path if url else "-"


def _block(cache, request, entry, rule, now):
    """Block the key in `entry` ((kind, mode, key), as _keys_for builds it) for
    the block time and log one line about it. `rule` is (window seconds, limit,
    the count that crossed it). Returns when the block ends."""
    kind, mode, key = entry
    seconds, limit, count = rule
    until = now + settings.RATE_LIMIT_BLOCK_SECONDS
    cache.set(_blocked_name(key), until, timeout=settings.RATE_LIMIT_BLOCK_SECONDS)
    SUMMARY.add(f"blocks:{kind}")
    logger.warning(
        "rate limit: %s %s for %d s after %d requests in %d s (limit %d); "
        "this request %s %s, from page %s",
        "blocked an" if mode == ENFORCE else "would block an",
        kind, settings.RATE_LIMIT_BLOCK_SECONDS, count, seconds, limit,
        request.method, request.path,
        _path_only(request.META.get("HTTP_REFERER", "")),
    )
    return until


def _count_all(cache, request, keys, now):
    """Count the request on each key, blocking any key it takes past a rule.
    Returns (when an ENFORCED block it caused ends, or None; whether it caused
    an observed one)."""
    longest = max(seconds for seconds, _ in settings.RATE_LIMIT_WINDOWS)
    enforced_until = None
    observed = False
    for entry in keys:
        kind, mode, key = entry
        for seconds, limit in settings.RATE_LIMIT_WINDOWS:
            count = _count(cache, key, seconds, now)
            SUMMARY.saw_count(kind, seconds, count, longest)
            if count <= limit:
                continue
            until = _block(cache, request, entry, (seconds, limit, count), now)
            if mode == ENFORCE:
                enforced_until = max(enforced_until or until, until)
            else:
                observed = True
            break
    return enforced_until, observed


def _refusal(seconds_left):
    response = JsonResponse({"detail": REFUSAL_TEXT}, status=429)
    response["Retry-After"] = str(max(1, math.ceil(seconds_left)))
    return response


class Summary:
    """The ten-minute summary line's numbers, for this process. Every update
    holds the lock (a few dictionary operations)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._period = None
        # Plain tallies by name: "requests", "keys:address", "blocks:account"...
        self.counts = collections.Counter()
        # {(kind, seconds): the highest count any one key reached}
        self.busiest = {}

    def roll(self, now):
        """Start a new period if `now` is past the current one, logging the
        finished period's line first. At most one line per period."""
        period = int(now // SUMMARY_SECONDS)
        with self._lock:
            if self._period == period:
                return
            line = self._line() if self._period is not None and self.counts["requests"] else None
            self._period = period
            self.counts = collections.Counter()
            self.busiest = {}
        if line:
            logger.warning("rate limit summary, last %d min: %s", SUMMARY_SECONDS // 60, line)

    def _line(self):
        n = self.counts
        busiest = ", ".join(
            f"{kind} {count} in {seconds}s"
            for (kind, seconds), count in sorted(self.busiest.items())
        ) or "none"
        return (
            f"{n['requests']} requests; {n['keys:account']} accounts, "
            f"{n['keys:address']} addresses; busiest: {busiest}; "
            f"blocks: {n['blocks:account']} account, {n['blocks:address']} address; "
            f"refused {n['refused']}, would refuse {n['would_refuse']}; "
            f"build {n['build']}; do-connecting-ip on {n['with_address']}, "
            f"matching x-forwarded-for's first entry on {n['matches_forwarded']}"
        )

    def add(self, name):
        with self._lock:
            self.counts[name] += 1

    def saw_request(self, request):
        address = request.META.get(ADDRESS_HEADER, "").strip()
        forwarded_first = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        with self._lock:
            self.counts["requests"] += 1
            if address:
                self.counts["with_address"] += 1
                if forwarded_first == address:
                    self.counts["matches_forwarded"] += 1

    def saw_count(self, kind, seconds, count, longest):
        with self._lock:
            # A key's first request in the longest window is a new key this
            # period: the windows and the summary share boundaries.
            if count == 1 and seconds == longest:
                self.counts[f"keys:{kind}"] += 1
            self.busiest[(kind, seconds)] = max(self.busiest.get((kind, seconds), 0), count)


# The one summary, for this process.
SUMMARY = Summary()


class RateLimitMiddleware:  # pylint: disable=too-few-public-methods
    """Refuses a request with 429 when its account or address is blocked, and
    counts it otherwise. See the module docstring."""

    def __init__(self, get_response):
        for name in ("RATE_LIMIT_ACCOUNT_MODE", "RATE_LIMIT_ADDRESS_MODE"):
            if getattr(settings, name) not in MODES:
                raise ImproperlyConfigured(
                    f"{name} must be one of {', '.join(MODES)}, not {getattr(settings, name)!r}"
                )
        self.get_response = get_response

    def __call__(self, request):
        refusal = self.check(request)
        if refusal is not None:
            setattr(request, REFUSED_ATTRIBUTE, True)
            return refusal
        return self.get_response(request)

    @staticmethod
    def check(request):
        """A 429 response for a refused request; None to let it through."""
        now = _now()
        SUMMARY.roll(now)
        SUMMARY.saw_request(request)
        if _is_build(request):
            SUMMARY.add("build")
            return None
        keys = _keys_for(request)
        if not keys:
            return None
        cache = caches[CACHE_ALIAS]

        # A key already blocked refuses the request (or, observing, would)
        # WITHOUT counting it on any key: counting a refused request on the
        # address would run the counter up for everyone else behind it.
        blocked_until, held = _blocks(cache, keys, now)
        if blocked_until is not None:
            SUMMARY.add("refused")
            return _refusal(blocked_until - now)

        blocked_until, observed = _count_all(
            cache, request, [k for k in keys if k[2] not in held], now
        )
        if blocked_until is not None:
            SUMMARY.add("refused")
            return _refusal(blocked_until - now)
        if held or observed:
            SUMMARY.add("would_refuse")
        return None
