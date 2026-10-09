"""What people plan to pull on: one popularity table per banner type."""

from django.db.models import Avg, Count, F, Q, Sum

from ..models import UserPlannedBanner
from .common import SANE_MAX_PULLS


def banner_popularity(fk_name):
    """
    Popularity rows for one banner type. fk_name is the FK field on
    UserPlannedBanner: "banner_uma" or "banner_support".

    Grouped by banner id (name/timeline included for display), ranked by
    distinct planners, then total pulls.

    WHY `planners` IS COUNTED UNFILTERED WHILE THE PULL FIGURES ARE NOT.
    They answer different questions. Someone who typed 999,999,999 into a
    banner's pull field really does have that banner in their plan, and
    popularity — the primary signal this table exists for — should say so.
    What they do NOT have is a budget of a billion pulls, so their row is
    excluded from the two figures that treat the number as a quantity. Filtering
    both would quietly understate demand; filtering neither is what produced a
    447,572 average.

    `excluded` reports how many rows were dropped from those two figures, so a
    surprising average can be checked against the count that caused it instead
    of being reverse-engineered from the arithmetic.

    ACTIVE PLANS ONLY. An account can hold several plans, and the spare ones
    are what-ifs: someone comparing "200 pulls" against "skip it" on the same
    banner intends ONE of those. Summing every plan would report 200 pulls of
    demand from a person who may intend none, and would let one user with five
    copies of a plan move an average five times. The active plan is the one
    they have open, so it is the best single answer to "what does this person
    plan to do", and it keeps every figure here meaning what it meant when an
    account had exactly one plan.
    """
    # Non-null ints, so ~sane is a clean complement with no third case.
    sane = Q(number_of_pulls__lte=SANE_MAX_PULLS)
    rows = (
        UserPlannedBanner.objects
        # Only this banner type, and never count staff/admin test accounts.
        .filter(**{f"{fk_name}__isnull": False}, user__is_staff=False)
        # TRANSITIONAL: the isnull half goes with release 2 of multi-plan. Until
        # `plan` is NOT NULL, a row the old code wrote during the deploy window
        # has no plan yet (plans._adopt_planless_rows) and is still a real row.
        .filter(Q(plan__is_active=True) | Q(plan__isnull=True))
        # values() before annotate() = GROUP BY these fields. Including the
        # FK id guarantees two banners that share a name never merge.
        .values(
            fk_name,
            f"{fk_name}__name",
            f"{fk_name}__banner_timeline__name",
            f"{fk_name}__banner_timeline__global_start_date",
            f"{fk_name}__banner_timeline__global_end_date",
        )
        .annotate(
            planners=Count("user", distinct=True),
            total_pulls=Sum("number_of_pulls", filter=sane),
            avg_pulls=Avg("number_of_pulls", filter=sane),
            excluded=Count("id", filter=~sane),
        )
        # nulls_last matters now that total_pulls is filtered: a banner whose
        # every row was excluded aggregates to NULL, and Postgres sorts NULLs
        # FIRST in a plain DESC — which would put the one banner we have no
        # figures for at the top of the table.
        .order_by("-planners", F("total_pulls").desc(nulls_last=True))
    )
    return [
        {
            "name": row[f"{fk_name}__name"],
            "timeline": row[f"{fk_name}__banner_timeline__name"],
            # Confirmed global dates; predicted banners report null here (fine
            # for an aggregate popularity report). Output keys stay the same so
            # the dashboard/CSV need no change.
            "start_date": row[f"{fk_name}__banner_timeline__global_start_date"],
            "end_date": row[f"{fk_name}__banner_timeline__global_end_date"],
            "planners": row["planners"],
            # Both aggregates come back NULL when the filter matched nothing,
            # which is a real state (a banner planned only by the outlier), not
            # an error — report it as zero rather than crashing the page.
            "total_pulls": row["total_pulls"] or 0,
            "avg_pulls": round(row["avg_pulls"] or 0, 1),
            "excluded": row["excluded"],
        }
        for row in rows
    ]
