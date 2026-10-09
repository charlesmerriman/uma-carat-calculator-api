"""What people set in the calculator: paid products, campaign selectors,
income ranks and the resources they hold."""

from statistics import median

from django.db.models import Count, Q

from ..models import AnniversaryEventProduct, CustomUser
from .common import SANE_MAX_RESOURCE, pct


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

# The campaign products that grant a selector ticket (see
# AnniversaryEventProduct.grants_selector). Carat packs are deliberately not
# reported: the client asked about selectors.
SELECTOR_PRODUCT_TYPES = ("uma_selector", "support_selector")


def paid_products(users, total_users, engaged_users):
    """Adoption of the two purchasable income sources, one row each."""
    rows = []
    for field, label in PAID_PRODUCT_FIELDS:
        count = users.filter(**{field: True}).count()
        rows.append({
            "label": label,
            "count": count,
            "pct_of_total": pct(count, total_users),
            "pct_of_engaged": pct(count, engaged_users),
        })
    return rows


def rank_distributions(users, total_users):
    """Users per rank, in game order, one distribution per rank type."""
    distributions = []
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
                "pct_of_total": pct(row["count"], total_users),
            }
            for row in grouped
        ]
        # Users who never picked this rank — reported explicitly rather than
        # relying on DB-specific NULL ordering.
        not_set = users.filter(**{f"{field}__isnull": True}).count()
        entries.append({
            "name": "Not set",
            "count": not_set,
            "pct_of_total": pct(not_set, total_users),
        })
        distributions.append({"label": label, "rows": entries})
    return distributions


def selector_purchases(total_users, engaged_users):
    """Who plans to buy each campaign selector: one row per selector product.

    A "buyer" is a distinct non-staff user with a planned purchase of the
    product at quantity 1 or more.

    COUNTED PER PERSON, ACROSS EVERY STATS BLOCK. A purchase belongs either to
    the account or to one of its income profiles (UserPlannedPurchase), so the
    same person can plan one product twice: once for their main game account
    and once for an alt. That is still one person buying, and distinct=True on
    the user is what keeps them from being counted twice. Unlike banner
    popularity there is no "active plan" filter to apply, because a purchase
    never lives on a plan.

    `picked` is how many of those buyers have chosen the card the selector
    will be spent on. An unpicked selector funds nothing in the projection, so
    the gap between the two columns is people who bought in but have not
    decided yet.

    Every selector product is listed, including ones nobody plans to buy: a
    zero is an answer here, and a missing row would look like a missing
    product.

    Returns the two report keys this section owns. `any_selector` counts
    people planning AT LEAST ONE selector. It is its own query because the
    per-product counts cannot be added up: someone buying two selectors
    appears in two rows.
    """
    # The join path from a product to its purchases; Count(filter=...) puts
    # these conditions inside the aggregate, so a product with no matching
    # purchase still comes back, with a count of 0.
    bought = Q(
        userplannedpurchase__user__is_staff=False,
        userplannedpurchase__quantity__gte=1,
    )
    has_pick = (
        Q(userplannedpurchase__target_uma__isnull=False)
        | Q(userplannedpurchase__target_support__isnull=False)
    )
    products = (
        AnniversaryEventProduct.objects
        .filter(product_type__in=SELECTOR_PRODUCT_TYPES)
        .annotate(
            buyers=Count("userplannedpurchase__user", filter=bought, distinct=True),
            picked=Count(
                "userplannedpurchase__user", filter=bought & has_pick, distinct=True
            ),
        )
        # Campaigns own no dates (they borrow them from the timeline), so id
        # order stands in for "oldest campaign first"; within a campaign, the
        # admin's own product order.
        .order_by("anniversary_event_id", "order", "id")
        .values("name", "anniversary_event__name", "buyers", "picked")
    )
    rows = [
        {
            "campaign": product["anniversary_event__name"],
            "label": product["name"],
            "count": product["buyers"],
            "picked": product["picked"],
            "pct_of_total": pct(product["buyers"], total_users),
            "pct_of_engaged": pct(product["buyers"], engaged_users),
        }
        for product in products
    ]

    any_count = (
        CustomUser.objects
        .filter(
            is_staff=False,
            userplannedpurchase__product__product_type__in=SELECTOR_PRODUCT_TYPES,
            userplannedpurchase__quantity__gte=1,
        )
        .distinct()
        .count()
    )
    return {
        "selector_purchases": rows,
        "any_selector": {
            "count": any_count,
            "pct_of_total": pct(any_count, total_users),
            "pct_of_engaged": pct(any_count, engaged_users),
        },
    }


def resource_statistics(engaged):
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
