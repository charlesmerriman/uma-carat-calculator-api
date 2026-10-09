"""What people set in the calculator: the income toggles, shop tickets,
campaign selectors, income ranks and the resources they hold."""

from statistics import median, quantiles

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
    # Selector tickets are not gacha tickets (they take one card outright), but
    # a balance held is still a balance held.
    ("uma_selector_ticket", "Uma Selector Tickets"),
    ("support_selector_ticket", "Support Selector Tickets"),
]

# The four income-rank FKs on CustomUser: (field name, human label).
RANK_FIELDS = [
    ("team_trials_rank", "Team Trials"),
    ("club_rank", "Club Rank"),
    ("champions_meeting_rank", "Champion's Meeting"),
    ("league_of_heroes_rank", "League of Heroes"),
]

# Every income toggle on GameStats: (field name, human label), in the order
# the table lists them. The two paid products lead because they are the
# question the client asked first. Each toggle's DEFAULT is read off the model
# field (toggle_default), never restated here, and AnalyticsIncomeSettingsTests
# fails if a boolean is added to GameStats without a line here.
INCOME_TOGGLES = [
    ("daily_carat", "Daily Carat Pack"),
    ("training_pass", "Training Pass"),
    ("misc_earnings", "Misc earnings"),
    ("monthly_shop_tickets", "Monthly shop tickets"),
    ("spend_tickets_on_banners", "Spend tickets on banners"),
    ("discounted_paid_pulls", "Discounted paid pulls"),
    ("full_price_paid_pulls", "Full-price paid pulls"),
    ("include_purchases_in_projection", "Campaign purchases in the projection"),
    ("webstore_bonus", "Webstore bonus"),
]

# The two monthly shop counts: (field name, column key). NULL means "the
# default", an admin-editable constant, which is why the field is nullable.
SHOP_TICKET_FIELDS = [
    ("shop_uma_tickets_bought", "uma"),
    ("shop_support_tickets_bought", "support"),
]

# The campaign products that grant a selector ticket (see
# AnniversaryEventProduct.grants_selector). Carat packs are deliberately not
# reported: the client asked about selectors.
SELECTOR_PRODUCT_TYPES = ("uma_selector", "support_selector")


def toggle_default(field):
    """Where a new account starts for one income toggle: the model's default."""
    return CustomUser._meta.get_field(field).default


def income_settings(engaged, engaged_users):
    """One row per income toggle, counted among ENGAGED users, in one query.

    WHY ENGAGED ONLY. Five of the nine toggles start ON. Every account that
    never opened the calculator has them on too, so "users on" over everyone
    would be mostly lurkers, and "% of engaged" could pass 100. Among engaged
    users both numbers mean something. For a toggle that starts off nothing
    changes: switching it on makes a person engaged (people.engaged_q), so
    every user who has it on is already in the count.

    `changed` is how many switched the toggle away from where a new account
    starts: on for a toggle that starts off, off for one that starts on. It is
    the only honest adoption number for a toggle that starts on, since "users
    on" there mostly measures who never touched it.
    """
    counts = engaged.aggregate(**{
        field: Count("id", filter=Q(**{field: True}))
        for field, _ in INCOME_TOGGLES
    })
    rows = []
    for field, label in INCOME_TOGGLES:
        default = toggle_default(field)
        users_on = counts[field]
        changed = engaged_users - users_on if default else users_on
        rows.append({
            "key": field,
            "label": label,
            "default": "On" if default else "Off",
            "users_on": users_on,
            "pct_on": pct(users_on, engaged_users),
            "changed": changed,
            "pct_changed": pct(changed, engaged_users),
        })
    return rows


def shop_tickets(engaged):
    """How many shop tickets people say they buy a month, uma and support.

    One row per stored count, "Not set" (the admin default) first, then the
    counts in order, with how many engaged users chose each. Counted whether or
    not Monthly shop tickets is on, since the count is kept either way.
    """
    tallies = {}
    for field, column in SHOP_TICKET_FIELDS:
        for value, people in (engaged.values(field).order_by(field)
                              .annotate(people=Count("id"))
                              .values_list(field, "people")):
            tallies.setdefault(value, {"uma": 0, "support": 0})[column] = people
    # None sorts first: it is the default, the row most people sit in.
    ordered = sorted(tallies, key=lambda value: (value is not None, value or 0))
    return [
        {"bought": "Not set (the default)" if value is None else str(value),
         **tallies[value]}
        for value in ordered
    ]


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


def _quartiles(values):
    """(p25, p75) of a non-empty list.

    The "inclusive" method treats the list as the whole population, so a
    quartile lands on a real value whenever it can. Worked example, nine
    people holding 0, 0, 2000, 5000, 9000, 12000, 30000, 45000 and 400000
    carats: p25 is 2000 and p75 is 30000 (the 3rd and 7th values), so half of
    them sit between the two, while the mean is 55889 because of one whale.
    """
    if len(values) == 1:
        # quantiles() needs two points before Python 3.13.
        return values[0], values[0]
    cuts = quantiles(values, n=4, method="inclusive")
    return cuts[0], cuts[2]


def resource_statistics(engaged):
    """Median, quartiles, share at zero, mean and dropped-value count for each
    resource field.

    ONE query for every column, all the statistics computed in Python.

    Why not in SQL: a median has no portable aggregate — Postgres has
    PERCENTILE_CONT, the SQLite the tests run on has nothing — and doing the
    mean the same way is what guarantees the two figures describe the identical
    filtered set. The cost is materialising engaged_users x 10 ints; at the
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
        p25, p75 = _quartiles(sane) if sane else (0, 0)
        statistics.append({
            "label": label,
            "avg": round(sum(sane) / len(sane), 1) if sane else 0,
            "median": round(median(sane), 1) if sane else 0,
            "p25": round(p25, 1),
            "p75": round(p75, 1),
            # The quartiles' blind spot: when a quarter of people hold none,
            # p25 reads 0 and says nothing more. This says how many.
            "zero_pct": pct(sane.count(0), len(sane)),
            "excluded": len(values) - len(sane),
        })
    return statistics
