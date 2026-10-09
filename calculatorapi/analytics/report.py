"""The whole report, assembled from the section modules."""

from django.utils import timezone

from ..models import BannerTimeline
from ..predictions import build_effective_date_maps
from .banners import (
    banner_popularity,
    demand_calendar,
    favourite_umas,
    step_up_popularity,
)
from .common import pct
from .income_settings import (
    income_settings,
    rank_distributions,
    resource_statistics,
    selector_purchases,
    shop_tickets,
)
from .people import (
    activity,
    engaged_subset,
    feature_adoption,
    growth,
    non_staff_users,
    sign_in_providers,
    supporters,
)
from .snapshots import comparison, history
from .traffic import traffic


def build_analytics_report():
    """
    Build the full analytics snapshot as a plain dict.

    Sections:
      - overview: total vs engaged user counts
      - traffic: daily and monthly site visits (the one section with history)
      - income_settings: every income toggle among engaged users, with how
        many changed it from its default; shop_tickets: the monthly counts
      - selector_purchases / any_selector: planned buyers per campaign
        selector, and people planning at least one
      - rank_distributions: users per rank, per rank type
      - resource_averages: mean, median and dropped count per resource,
        among engaged users
      - popular_uma_banners / popular_support_banners: ranked pull plans,
        with effective (predicted when unconfirmed) dates and a status
      - step_up_popularity: ranked step-up plans, in STEPS
      - demand_calendar: planned demand by the month banners end
      - growth_by_month / growth_by_week: new accounts
      - activity: saves and sign-ins in recent windows, and who came back
      - sign_in_providers: people per provider, and overlaps
      - supporters: active patrons by tier (patrons, not users)
      - feature_adoption: people using each shipped feature
      - favourite_umas: the most-picked favourites
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
    # Resolved once for every banner section, as one calendar: the plural
    # builder is the only correct one (.claude/rules/backend-models.md).
    timeline_dates = build_effective_date_maps()[BannerTimeline]

    report = {
        "generated_at": timezone.now(),
        **traffic(),
        "total_users": total_users,
        "engaged_users": engaged_users,
        "engaged_pct": pct(engaged_users, total_users),
        **growth(users),
        "activity": activity(users, total_users),
        "sign_in_providers": sign_in_providers(users, total_users),
        "supporters": supporters(),
        "feature_adoption": feature_adoption(users, engaged_users),
        "income_settings": income_settings(engaged, engaged_users),
        "shop_tickets": shop_tickets(engaged),
        **selector_purchases(total_users, engaged_users),
        "rank_distributions": rank_distributions(users, total_users),
        # Averaging over never-configured accounts full of zeroes would be
        # meaningless, so this section uses the engaged denominator.
        "resource_averages": resource_statistics(engaged),
        "popular_uma_banners": banner_popularity("banner_uma", timeline_dates),
        "popular_support_banners": banner_popularity("banner_support", timeline_dates),
        "step_up_popularity": step_up_popularity(timeline_dates),
        "demand_calendar": demand_calendar(timeline_dates),
        "favourite_umas": favourite_umas(),
    }
    # Last, because both read the figures above. Neither is ever stored in a
    # snapshot (snapshots.NOT_STORED): they are built FROM snapshots.
    report["comparison"] = comparison(report)
    report["history"] = history(report["monthly_visits"])
    return report
