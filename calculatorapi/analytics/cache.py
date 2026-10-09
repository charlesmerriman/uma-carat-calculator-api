"""The cached report every reader shares."""

from django.core.cache import cache

from .common import REPORT_SHAPE
from .report import build_analytics_report
from .snapshots import ensure_today

# ── The cached report ────────────────────────────────────────────────────────
# build_analytics_report() is a few dozen aggregate queries, and the admin
# landing page reads it for four KPI cards on EVERY load, not just when someone
# opens the analytics page. So the report is held for five minutes and every
# reader shares the one copy. The key is the report's shape version, so a new
# shape never meets a cached old one (common.REPORT_SHAPE says when to bump it).
REPORT_CACHE_KEY = REPORT_SHAPE
REPORT_CACHE_TTL_SECONDS = 300


def get_report(refresh=False):
    """The analytics report, rebuilt at most once per five minutes.

    Every reader goes through here: the dashboard page, its CSV, and the admin
    index's KPI cards. Sharing one copy is also what makes a downloaded CSV
    match the page the person was just looking at.

    `refresh=True` rebuilds and re-caches regardless; it backs the page's
    "Refresh now" link. Every rebuild also makes sure the day has its
    snapshot (snapshots.ensure_today).

    There is no invalidation, on purpose. The report only reads, so an old copy
    is merely old, never wrong, and the page prints when it was built. Like
    public_payload_cache this lives in one process's LocMem; were there ever
    several processes, each would hold its own copy and two loads could differ
    by up to the TTL, which is harmless on a staff page.
    """
    if not refresh:
        report = cache.get(REPORT_CACHE_KEY)
        if report is not None:
            return report
    report = build_analytics_report()
    # The first rebuild on a new day keeps a copy as that day's snapshot. Here,
    # and only on a rebuild, so a cache hit stays free and every reader (the
    # page, the CSV, the admin index's cards) writes history without knowing.
    ensure_today(report)
    cache.set(REPORT_CACHE_KEY, report, REPORT_CACHE_TTL_SECONDS)
    return report
