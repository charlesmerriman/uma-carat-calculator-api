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
"""

import hashlib
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, models
from django.utils import timezone

from .models import DailyVisit, MonthlyVisit, VisitorHash

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
    }
