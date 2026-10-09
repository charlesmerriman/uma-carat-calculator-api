"""
Site traffic counting: page views and distinct visitors, per day and per month.

Same split as the calculatorapi/analytics package -- this module is pure logic with no
HTTP concerns beyond reading headers off a request object, so the hashing and
the rollup can be unit-tested directly. views/visits.py owns the endpoint and
analytics/traffic.py folds build_visit_report() into the dashboard.

PRIVACY CONTRACT. No function here may persist an IP address, a user agent, or
anything else that identifies a request. The only per-visitor artifact written
is a digest salted with the calendar month, which makes it useless once that
month is over -- see _visitor_hash below for the reasoning and its limits.

The beacon also reports the landing page and the referring site's NAME. Both
are reduced here to a short fixed vocabulary (a known route, a bare hostname,
"other", "direct") before anything is written, and both land in totals tables
with no hash beside them, so neither can be tied to a visitor. Disclosed in the
Privacy Policy's "Our own counter" paragraph.
"""

import hashlib
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import DatabaseError, models
from django.utils import timezone

from .models import (
    DailyVisit,
    LandingPageVisit,
    MonthlyVisit,
    ReferrerVisit,
    VisitorHash,
)

logger = logging.getLogger(__name__)

# Crawlers announce themselves in the user agent. This is a blunt filter and it
# will never catch a crawler that lies, but it removes the bulk of the noise --
# without it the counts are dominated by whatever is indexing the site rather
# than by people. Also catches our own tooling (curl, urllib) so a health check
# or a manual poke doesn't register as traffic.
_BOT_UA = re.compile(
    r"bot|crawler|spider|slurp|headless|curl|wget|python-|scrapy|monitor|preview",
    re.IGNORECASE,
)

# How many days of hashes to keep before they are prunable. Only affects the
# scratch table -- DailyVisit and MonthlyVisit rows are never pruned.
#
# Must stay comfortably longer than one month: the monthly-unique check asks
# "any row for this hash since the 1st?", so pruning a visitor's earlier rows
# mid-month would let them be counted twice. 90 days leaves two months of slack.
VISITOR_HASH_RETENTION_DAYS = 90


# The pages a landing count may name: the public routes the frontend prerenders
# (frontend/src/prerenderRoutes.ts) plus /account and /login. Anything else, a
# typo, an old link, a path a bot probes, counts as OTHER, so the table can only
# ever hold this short list. A NEW PUBLIC ROUTE GOES HERE TOO, or its visits
# read as "other" (CLAUDE.md, "Every public route is prerendered").
KNOWN_ROUTES = frozenset({
    "/",
    "/about",
    "/faq",
    "/terms",
    "/privacy-policy",
    "/changelog",
    "/feedback",
    "/guides/carat-income",
    "/app",
    "/app/timeline",
    "/app/selectors",
    "/app/legend-races",
    "/account",
    "/login",
})

OTHER = "other"
# No referrer at all: a bookmark, a typed address, and most chat apps
# (Discord's desktop client sends none), so this runs high.
DIRECT = "direct"

# Distinct referring sites recorded per day before new ones fold into OTHER. A
# referrer-spam run sends thousands of invented hostnames; without a cap each
# would become a row.
MAX_REFERRER_HOSTS_PER_DAY = 100

_HOSTNAME = re.compile(r"^[a-z0-9.-]+$")


def _without_www(host):
    return host[4:] if host.startswith("www.") else host


def landing_route(path):
    """The route a visit started on, as counted, or None if none was sent.

    None means an older frontend build that does not send `path`, and nothing
    is written for it: "absent" and "other" are different answers.
    """
    if path is None:
        return None
    # The frontend sends a bare pathname; strip a query or fragment anyway,
    # since the server never trusts the client to have done it.
    route = path.split("?", 1)[0].split("#", 1)[0].lower().rstrip("/") or "/"
    return route if route in KNOWN_ROUTES else OTHER


def _own_host():
    """This site's own hostname, from FRONTEND_URL, without www."""
    return _without_www((urlsplit(settings.FRONTEND_URL).hostname or "").lower())


def referrer_host(ref):
    """The referring site's name, as counted, or None if none was sent.

    "Sent and empty" means the browser reported no referrer: DIRECT. "Not sent"
    means an older build: None, and nothing is written. A visit that starts
    from one of our own pages is also DIRECT, since it is still ours. Anything
    that does not look like a bare hostname counts as OTHER.
    """
    if ref is None:
        return None
    host = _without_www(ref.strip().lower())
    if not host or host == _own_host():
        return DIRECT
    if len(host) > 100 or not _HOSTNAME.match(host):
        return OTHER
    return host


def _bump(model, **key):
    """Add one to the counter row for `key`, creating it at zero first.

    F() for the same reason as the daily counter: two simultaneous beacons
    must not read the same value and lose an increment.
    """
    row, _ = model.objects.get_or_create(**key)
    model.objects.filter(pk=row.pk).update(page_views=models.F("page_views") + 1)


def _record_landing_and_referrer(request, today):
    """Count the visit's landing page and referring site, when the beacon sent them."""
    route = landing_route(request.GET.get("path"))
    if route is not None:
        _bump(LandingPageVisit, date=today, route=route)

    host = referrer_host(request.GET.get("ref"))
    if host is not None:
        seen_today = ReferrerVisit.objects.filter(date=today)
        if (not seen_today.filter(host=host).exists()
                and seen_today.count() >= MAX_REFERRER_HOSTS_PER_DAY):
            host = OTHER
        _bump(ReferrerVisit, date=today, host=host)


def _client_ip(request):
    """The visitor's IP, honouring the proxy in front of us.

    REMOTE_ADDR alone is wrong in production: App Platform terminates TLS at a
    load balancer, so every request arrives from the same handful of internal
    addresses and every visitor would hash identically -- unique_visitors would
    read 1 forever. X-Forwarded-For carries "client, proxy1, proxy2", so the
    FIRST entry is the original client.

    That first entry is client-settable and therefore not trustworthy. It is
    fine here because the only thing a forged value can do is inflate a unique
    count on a staff-only dashboard. Do not reuse this for anything that gates
    access.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _visitor_hash(ip, user_agent, day):
    """A per-MONTH, non-reversible bucket for one visitor.

    The salt carries the calendar month, so the same person hashes identically
    all month and to something unrelated in the next one. That span is a
    deliberate choice and the only reason a true monthly-unique count is
    possible: counting someone once per month *means* recognising them across
    that month, and no amount of cleverness avoids it. A daily salt (the
    obvious privacy-maximising choice) can only ever produce visit-days.

    What the month scope does not give up:
      - Nothing identifying is stored. Recovering an IP would mean brute-forcing
        the address space against an unknown SECRET_KEY.
      - Nobody can be followed across months; the link breaks at every boundary.
      - No cookie or client-side identifier is involved, so this cannot be
        correlated with anything outside our own database.

    Disclosed in the Privacy Policy's "Traffic Measurement" section, which
    states the month-long span explicitly.
    """
    raw = f"{settings.SECRET_KEY}:{day:%Y-%m}:{ip}:{user_agent}"
    # Half a SHA-256. Collision odds are negligible at any traffic level this
    # site will see, and a narrower unique index is cheaper.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_visit(request):
    """Count one visit. Returns True if it was counted, False if skipped.

    Never raises. A traffic counter must not be able to fail a visitor's
    request, so database trouble is logged and swallowed -- losing a data point
    is strictly better than serving them an error.

    Reads two optional query parameters the beacon sends, `path` (the landing
    page) and `ref` (the referring site's hostname). A bot is filtered out
    before either is read.
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    if _BOT_UA.search(user_agent):
        return False

    today = timezone.localdate()
    month_start = today.replace(day=1)
    try:
        day_row, _ = DailyVisit.objects.get_or_create(date=today)
        month_row, _ = MonthlyVisit.objects.get_or_create(month=month_start)
        visitor = _visitor_hash(_client_ip(request), user_agent, today)

        # F() rather than read-modify-write: two simultaneous beacons would
        # otherwise read the same value and one increment would vanish. This
        # pushes the arithmetic into the database, so no transaction or lock is
        # needed for the counts to stay correct.
        DailyVisit.objects.filter(pk=day_row.pk).update(
            page_views=models.F("page_views") + 1
        )
        MonthlyVisit.objects.filter(pk=month_row.pk).update(
            page_views=models.F("page_views") + 1
        )

        # Asked BEFORE today's row is written, or it would always find itself.
        # The hash is month-scoped, so any hit here means this visitor has
        # already been counted for the month.
        seen_this_month = VisitorHash.objects.filter(
            visitor_hash=visitor, date__gte=month_start
        ).exists()

        # The unique constraint on (date, visitor_hash) does the deduplication;
        # created=False means we have already counted this visitor today.
        _, first_today = VisitorHash.objects.get_or_create(
            date=today, visitor_hash=visitor
        )

        if first_today:
            DailyVisit.objects.filter(pk=day_row.pk).update(
                unique_visitors=models.F("unique_visitors") + 1
            )
            # Gated on first_today as well as the month check, which is what
            # makes this exact under concurrency: two simultaneous first-ever
            # visits both read seen_this_month=False, but the unique constraint
            # lets only one of them win first_today, so the month is bumped once.
            if not seen_this_month:
                MonthlyVisit.objects.filter(pk=month_row.pk).update(
                    unique_visitors=models.F("unique_visitors") + 1
                )

        _record_landing_and_referrer(request, today)
        return True
    except DatabaseError:
        logger.warning("Failed to record site visit", exc_info=True)
        return False


def build_visit_report(days=30, months=12):
    """Daily rows for the last `days`, plus a monthly rollup of `months`.

    Both windows are trailing and inclusive of today. Days with no traffic have
    no row at all rather than a zero -- the tables read as "days we saw
    anything", which is the honest rendering of what was recorded.
    """
    today = timezone.localdate()

    daily = list(
        DailyVisit.objects.filter(
            date__gte=today - timedelta(days=days - 1)
        ).values("date", "page_views", "unique_visitors")
    )

    # Read straight off the monthly counters rather than aggregating the daily
    # rows. Summing daily uniques would count a person once per day they
    # appeared; MonthlyVisit.unique_visitors is accumulated against the
    # month-scoped hash as visits arrive, so it is a true monthly-active figure.
    monthly = list(
        MonthlyVisit.objects.values("month", "page_views", "unique_visitors")[:months]
    )

    return {
        "daily": daily,
        "monthly": monthly,
        "daily_window_days": days,
        "landing_pages": _window_totals(LandingPageVisit, "route", today, days),
        "referrers": _window_totals(ReferrerVisit, "host", today, days),
    }


def _window_totals(model, field, today, days):
    """Visits per `field` value over the last `days` (today included), beside
    the `days` before them, most visits first.

    `share` is of this window's visits. A value seen only in the earlier window
    is still listed, at zero, so a page or site that dropped away shows up.
    """
    start = today - timedelta(days=days - 1)
    earlier_start = start - timedelta(days=days)

    def totals(rows):
        return dict(rows.values(field).order_by()
                    .annotate(visits=models.Sum("page_views"))
                    .values_list(field, "visits"))

    current = totals(model.objects.filter(date__gte=start))
    earlier = totals(model.objects.filter(date__gte=earlier_start, date__lt=start))
    total = sum(current.values())
    names = sorted(current.keys() | earlier.keys(),
                   key=lambda name: (-current.get(name, 0), name))
    return [
        {"name": name, "visits": current.get(name, 0),
         "share": round(current.get(name, 0) / total * 100, 1) if total else 0.0,
         "earlier": earlier.get(name, 0)}
        for name in names
    ]
