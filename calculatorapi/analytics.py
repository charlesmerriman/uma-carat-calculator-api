"""
Aggregate, anonymized usage statistics for the admin analytics dashboard.

This module is pure query logic — no HTTP concerns — so the math can be
unit-tested directly and reused by both the HTML dashboard and the CSV
export (see views/analytics.py).

Privacy note: everything returned here is an aggregate (counts, percentages,
averages). No function in this module may ever return per-user rows or any
identifying field (username, email, etc.).
"""

from statistics import median

from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from .models import CustomUser, UserPlannedBanner
from .visits import build_visit_report

# Resource fields averaged for the "Current Resources" section.
# (model field name, human label) — order controls display order.
RESOURCE_FIELDS = [
    ("current_carat", "Carats"),
    ("current_paid_carat", "Paid Carats"),
    ("uma_ticket", "Uma Tickets"),
    ("support_ticket", "Support Tickets"),
    ("ssr_crystals", "SSR Crystals"),
    ("sr_crystals", "SR Crystals"),
    ("ssr_shards", "SSR Shards"),
    ("sr_shards", "SR Shards"),
]

# The four income-rank FKs on CustomUser: (field name, human label).
RANK_FIELDS = [
    ("team_trials_rank", "Team Trials"),
    ("club_rank", "Club Rank"),
    ("champions_meeting_rank", "Champion's Meeting"),
    ("league_of_heroes_rank", "League of Heroes"),
]

# The two paid products the client cares most about.
PAID_PRODUCT_FIELDS = [
    ("daily_carat", "Daily Carat Pack"),
    ("training_pass", "Training Pass"),
]


# ── Sanity bounds ────────────────────────────────────────────────────────────
# Ceilings above which a stored number stops being an answer and starts being
# someone finding out what the field does.
#
# These are ANALYTICS-ONLY, and deliberately NOT validation. The API accepts any
# value on purpose: a user is free to sandbox "what if I had a billion carats"
# and watch their own projection respond, and that is a legitimate thing to want
# from a calculator. These bounds decide only what counts as a DATA POINT on
# this page — nobody's saved plan is touched, rejected or rewritten.
#
# Both sit orders of magnitude above any real answer, so what they exclude is
# unambiguous rather than merely unusual. A cautious ceiling would be the wrong
# trade: wrongly dropping a genuine whale biases the report quietly, while a
# ceiling this high can only catch values that were never answers at all.
#
#   pulls    — pity is 200 and MLB of a five-copy card is ~1,000 pulls, so
#              2,000 is ten pity copies budgeted for one banner: double what
#              maxing out a banner costs, and still a number someone could
#              plausibly mean.
#   resource — 10,000,000 carats is ~66,000 pulls' worth, and the same ceiling
#              is generous past absurdity for tickets, crystals and shards.
#
# What prompted them: the client sanitiser caps typed input at nine digits
# (frontend NumberField.sanitise), so a user leaning on a digit key lands on
# exactly 999,999,999. That one value, on one account, was adding ~169,000 to
# every resource mean and turning a 145-avg banner into a 447,572 one.
SANE_MAX_PULLS = 2_000
SANE_MAX_RESOURCE = 10_000_000


def _pct(part, whole):
    """Percentage rounded to one decimal; 0.0 when the denominator is empty."""
    return round(part / whole * 100, 1) if whole else 0.0


def _engaged_q():
    """
    Q filter matching "engaged" users: anyone who has changed at least one
    calculator setting away from its default, or planned at least one banner.

    Every CustomUser stat defaults to False/0/null, so accounts that
    registered but never touched the calculator would otherwise drag every
    percentage down. Reports show both denominators (total vs engaged).
    """
    q = Q(userplannedbanner__isnull=False)
    for field, _ in PAID_PRODUCT_FIELDS:
        q |= Q(**{field: True})
    for field, _ in RANK_FIELDS:
        q |= Q(**{f"{field}__isnull": False})
    for field, _ in RESOURCE_FIELDS:
        # "changed from default" = any non-zero value (defaults are all 0)
        q |= ~Q(**{field: 0})
    return q


def _banner_popularity(fk_name):
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


def _resource_statistics(engaged):
    """Mean, median and dropped-value count for each resource field.

    ONE query for all eight columns, both statistics computed in Python.

    Why not in SQL: a median has no portable aggregate — Postgres has
    PERCENTILE_CONT, the SQLite the tests run on has nothing — and doing the
    mean the same way is what guarantees the two figures describe the identical
    filtered set. The cost is materialising engaged_users x 8 ints; at the
    current few thousand accounts that is a single query and well under a
    megabyte. If this page ever reports on a user base two or three orders of
    magnitude larger, move the mean back to a filtered Avg() and the median to a
    database-specific percentile.

    The MEDIAN is the number to trust, and it is here for a reason that outlives
    the bounds above: carat balances are genuinely long-tailed, so a handful of
    real whales pull the mean well off where most people sit even when every
    value in the set is honest. A median cannot be moved by an extreme value at
    all, which makes it the only figure on this page that stays meaningful
    whatever anyone types.

    Filtering is PER FIELD, not per user: an account with plausible carats and
    an absurd crystal count still contributes its carats. Dropping the whole row
    would discard good answers to punish a bad one.
    """
    fields = [field for field, _ in RESOURCE_FIELDS]
    rows = list(engaged.values_list(*fields))
    # zip(*[]) is empty rather than eight empty columns, so an empty user base
    # would otherwise fall out of the loop below and report no rows at all.
    columns = list(zip(*rows)) if rows else [()] * len(fields)

    statistics = []
    for (field, label), values in zip(RESOURCE_FIELDS, columns):
        # The lower bound is not redundant with the client's floor of 0: these
        # fields are plain IntegerFields and the API accepts a negative, which
        # would drag a mean down as effectively as a huge value drags it up.
        sane = [value for value in values if 0 <= value <= SANE_MAX_RESOURCE]
        statistics.append({
            "label": label,
            "avg": round(sum(sane) / len(sane), 1) if sane else 0,
            "median": round(median(sane), 1) if sane else 0,
            "excluded": len(values) - len(sane),
        })
    return statistics


def build_analytics_report():
    """
    Build the full analytics snapshot as a plain dict.

    Sections:
      - overview: total vs engaged user counts
      - traffic: daily and monthly site visits (the one section with history)
      - paid_products: daily carat pack / training pass adoption
      - rank_distributions: users per rank, per rank type
      - resource_averages: mean, median and dropped count per resource,
        among engaged users
      - popular_uma_banners / popular_support_banners: ranked pull plans

    Implausible values are excluded from every figure that treats a stored
    number as a QUANTITY (see SANE_MAX_PULLS / SANE_MAX_RESOURCE), and from
    none of the figures that merely count people. Each affected section reports
    how many values it dropped, so the exclusion is visible on the page rather
    than being something a reader has to know about.

    Everything but `traffic` is a snapshot of the database as it stands right
    now. Traffic is accumulated over time by calculatorapi/visits.py, so it is
    the only part of this report that reads as a trend.
    """
    # Staff accounts (the site owner, devs) are excluded from every metric so
    # admin test data never skews the numbers.
    users = CustomUser.objects.filter(is_staff=False)
    total_users = users.count()

    # The engaged filter joins through UserPlannedBanner, which can duplicate
    # user rows; re-filtering by pk keeps `engaged` a clean single-table
    # queryset that is safe to count and aggregate over.
    engaged = users.filter(pk__in=users.filter(_engaged_q()).values("pk"))
    engaged_users = engaged.count()

    # ── Paid products ────────────────────────────────────────────────────
    paid_products = []
    for field, label in PAID_PRODUCT_FIELDS:
        count = users.filter(**{field: True}).count()
        paid_products.append({
            "label": label,
            "count": count,
            "pct_of_total": _pct(count, total_users),
            "pct_of_engaged": _pct(count, engaged_users),
        })

    # ── Rank distributions ───────────────────────────────────────────────
    rank_distributions = []
    for field, label in RANK_FIELDS:
        # Non-null ranks, grouped per rank, in game order (by income).
        # Grouping includes income_amount so equal names in different tiers
        # stay distinct; ordering is deterministic across SQLite/Postgres.
        grouped = (
            users.filter(**{f"{field}__isnull": False})
            .values(f"{field}__name", f"{field}__income_amount")
            .annotate(count=Count("id"))
            .order_by(f"{field}__income_amount", f"{field}__name")
        )
        entries = [
            {
                "name": row[f"{field}__name"],
                "count": row["count"],
                "pct_of_total": _pct(row["count"], total_users),
            }
            for row in grouped
        ]
        # Users who never picked this rank — reported explicitly rather than
        # relying on DB-specific NULL ordering.
        not_set = users.filter(**{f"{field}__isnull": True}).count()
        entries.append({
            "name": "Not set",
            "count": not_set,
            "pct_of_total": _pct(not_set, total_users),
        })
        rank_distributions.append({"label": label, "rows": entries})

    # ── Resource averages (engaged users only) ───────────────────────────
    # Averaging over never-configured accounts full of zeroes would be
    # meaningless, so this section uses the engaged denominator.
    resource_averages = _resource_statistics(engaged)

    # ── Traffic ──────────────────────────────────────────────────────────
    # Counts everyone who loaded the site, signed in or not — unlike every
    # other section here, which can only see accounts. Guests are most of the
    # traffic, so this is the only number on the page that reflects them.
    visits = build_visit_report()

    return {
        "generated_at": timezone.now(),
        "daily_visits": visits["daily"],
        "monthly_visits": visits["monthly"],
        "daily_window_days": visits["daily_window_days"],
        "total_users": total_users,
        "engaged_users": engaged_users,
        "engaged_pct": _pct(engaged_users, total_users),
        "paid_products": paid_products,
        "rank_distributions": rank_distributions,
        "resource_averages": resource_averages,
        "popular_uma_banners": _banner_popularity("banner_uma"),
        "popular_support_banners": _banner_popularity("banner_support"),
    }
