"""The whole report, assembled from the section modules."""

from django.utils import timezone

from .banners import banner_popularity
from .common import pct
from .income_settings import (
    paid_products,
    rank_distributions,
    resource_statistics,
    selector_purchases,
)
from .people import engaged_subset, non_staff_users
from .snapshots import comparison, history
from .traffic import traffic


def build_analytics_report():
    """
    Build the full analytics snapshot as a plain dict.

    Sections:
      - overview: total vs engaged user counts
      - traffic: daily and monthly site visits (the one section with history)
      - paid_products: daily carat pack / training pass adoption
      - selector_purchases / any_selector: planned buyers per campaign
        selector, and people planning at least one
      - rank_distributions: users per rank, per rank type
      - resource_averages: mean, median and dropped count per resource,
        among engaged users
      - popular_uma_banners / popular_support_banners: ranked pull plans
      - comparison: the tracked figures now vs the snapshot ~30 days back
      - history: one row per month, from the first snapshot of each

    Implausible values are excluded from every figure that treats a stored
    number as a QUANTITY (see SANE_MAX_PULLS / SANE_MAX_RESOURCE), and from
    none of the figures that merely count people. Each affected section reports
    how many values it dropped, so the exclusion is visible on the page rather
    than being something a reader has to know about.

    Every other section is a snapshot of the database as it stands right now.
    Traffic is accumulated over time by calculatorapi/visits.py; for the rest,
    `comparison` and `history` read the daily copies snapshots.py keeps.
    """
    users = non_staff_users()
    total_users = users.count()
    engaged = engaged_subset(users)
    engaged_users = engaged.count()

    report = {
        "generated_at": timezone.now(),
        **traffic(),
        "total_users": total_users,
        "engaged_users": engaged_users,
        "engaged_pct": pct(engaged_users, total_users),
        "paid_products": paid_products(users, total_users, engaged_users),
        **selector_purchases(total_users, engaged_users),
        "rank_distributions": rank_distributions(users, total_users),
        # Averaging over never-configured accounts full of zeroes would be
        # meaningless, so this section uses the engaged denominator.
        "resource_averages": resource_statistics(engaged),
        "popular_uma_banners": banner_popularity("banner_uma"),
        "popular_support_banners": banner_popularity("banner_support"),
    }
    # Last, because both read the figures above. Neither is ever stored in a
    # snapshot (snapshots.NOT_STORED): they are built FROM snapshots.
    report["comparison"] = comparison(report)
    report["history"] = history(report["monthly_visits"])
    return report
