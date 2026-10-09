"""Who the report counts, and who they are: the non-staff user base, its
engaged subset, growth, activity, sign-in providers, supporters and which
features people use."""

import datetime

from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone

from ..models import (
    CustomUser,
    IncomeProfile,
    PatreonSupporter,
    Plan,
    SocialAccount,
    UserOshi,
    UserPlannedBanner,
    UserPlannedPurchase,
    UserStepUpSelection,
)
from .banners import active_rows
from .common import months_back, pct
from .income_settings import (
    INCOME_TOGGLES,
    RANK_FIELDS,
    RESOURCE_FIELDS,
    SHOP_TICKET_FIELDS,
    toggle_default,
)


def non_staff_users():
    """Every account the report may count.

    Staff accounts (the site owner, devs) are excluded from every metric so
    admin test data never skews the numbers.
    """
    return CustomUser.objects.filter(is_staff=False)


def _has(model, **lookups):
    """True for a user with at least one `model` row (matching `lookups`).

    An EXISTS subquery rather than a join through the reverse relation. Each
    join multiplies a user's rows by how many related rows they have, so a
    filter joining banners, purchases, selections and oshis at once would
    produce banners x purchases x selections x oshis rows per person. EXISTS
    answers yes or no per user and never duplicates anyone.
    """
    return Q(Exists(model.objects.filter(user=OuterRef("pk"), **lookups)))


def engaged_q():
    """
    Q filter matching "engaged" users: anyone who has used the calculator in
    any way a new account has not.

    Every stat on a new account starts at its default, so accounts that
    registered but never touched the calculator would otherwise drag every
    percentage down. Reports show both denominators (total vs engaged).

    THE RULE EVERY CLAUSE FOLLOWS: if a section counts people who did X, then
    doing X must make a person engaged. Otherwise the section's numerator holds
    people its denominator does not, and "% of engaged" can pass 100. That is
    how planning a selector got its clause, and why every feature below has one.
    """
    q = (
        # Planned a banner, in ANY plan. Planning a what-if is using the
        # calculator too, so a spare plan counts here even though the banner
        # tables read only the active one.
        _has(UserPlannedBanner)
        # Planned a campaign purchase (selectors, packs).
        | _has(UserPlannedPurchase)
        # Chose their own ten cards for a step-up.
        | _has(UserStepUpSelection)
        # Picked a favourite uma (every account has one slot).
        | _has(UserOshi)
        # Made a separate stats block for a plan (another game account).
        | _has(IncomeProfile)
        # Has more than one plan: one is always active, so any inactive plan
        # means a second exists.
        | _has(Plan, is_active=False)
        # Set a display name on the Account page.
        | (Q(display_name__isnull=False) & ~Q(display_name=""))
    )
    # Switched an income toggle away from where a new account starts: on for
    # the paid products and webstore bonus, off for the ones that start on.
    for field, _ in INCOME_TOGGLES:
        q |= ~Q(**{field: toggle_default(field)})
    # Set how many shop tickets they buy (NULL is the default).
    for field, _ in SHOP_TICKET_FIELDS:
        q |= Q(**{f"{field}__isnull": False})
    # Picked a rank.
    for field, _ in RANK_FIELDS:
        q |= Q(**{f"{field}__isnull": False})
    # Entered a balance ("changed from default" = any non-zero value).
    for field, _ in RESOURCE_FIELDS:
        q |= ~Q(**{field: 0})
    return q


def engaged_subset(users):
    """The engaged subset of `users`, as a queryset safe to count and aggregate.

    Every relation in engaged_q() is an EXISTS, so filtering cannot duplicate a
    user row and needs no de-duplicating wrapper.
    """
    return users.filter(engaged_q())


# How many calendar months and 7-day weeks the growth tables list.
GROWTH_MONTHS = 12
GROWTH_WEEKS = 8

# A week: the line between "tried it once" and "came back".
FIRST_WEEK = datetime.timedelta(days=7)


def growth(users):
    """New non-staff accounts per calendar month and per 7-day week.

    From date_joined, which purge_user_pii leaves alone, so a purged account
    still counts in the month it joined. An account deleted outright is gone
    from every month: these are accounts that still exist. A month or week with
    no sign-ups is listed at zero, since zero is an answer.
    """
    today = timezone.localdate()
    this_month = today.replace(day=1)
    first_month = months_back(this_month, GROWTH_MONTHS - 1)
    by_month = {}
    for month, joined in (users.filter(date_joined__date__gte=first_month)
                          .annotate(month=TruncMonth("date_joined"))
                          .values("month").order_by()
                          .annotate(joined=Count("id"))
                          .values_list("month", "joined")):
        # TruncMonth on a DateTimeField gives a datetime back.
        if isinstance(month, datetime.datetime):
            month = month.date()
        by_month[month] = joined
    months = [
        {"period": f"{month:%Y-%m}", "new_accounts": by_month.get(month, 0),
         "partial": month == this_month}
        for month in (months_back(this_month, count) for count in range(GROWTH_MONTHS))
    ]

    # Weeks end today and step back seven days at a time, so the first is the
    # last seven days (today included) and no day falls in two windows.
    first_day = today - datetime.timedelta(days=7 * GROWTH_WEEKS - 1)
    joined_days = [moment.date() for moment in
                   users.filter(date_joined__date__gte=first_day)
                   .values_list("date_joined", flat=True)]
    weeks = []
    for count in range(GROWTH_WEEKS):
        end = today - datetime.timedelta(days=7 * count)
        start = end - datetime.timedelta(days=6)
        weeks.append({
            "period": f"{start:%Y-%m-%d} to {end:%Y-%m-%d}",
            "new_accounts": sum(1 for day in joined_days if start <= day <= end),
            "partial": count == 0,
        })
    return {"growth_by_month": months, "growth_by_week": weeks}


def _saved_since(moment):
    """A user whose plan or income profile was saved at or after `moment`."""
    return (Q(Exists(Plan.objects.filter(user=OuterRef("pk"), updated_at__gte=moment)))
            | Q(Exists(IncomeProfile.objects.filter(user=OuterRef("pk"),
                                                    updated_at__gte=moment))))


def activity(users, total_users):
    """Who still uses their account, from what is already stored.

    No new column and nothing new kept about anyone (decided 2026-10-09):
    saves stamp Plan.updated_at and IncomeProfile.updated_at
    (views/calculator.py), sign-ins stamp SocialAccount.last_login_at. Django's
    own last_login is NOT maintained (token auth never calls login()), so it is
    never read.

    Sign-ins undercount by how sign-in works: a session lasts until the person
    signs out, so a daily user may not have signed in for months. Saves are the
    better signal; both are shown.

    "Came back after their first week" asks, of accounts older than a week, how
    many saved or signed in more than a week after joining. Each row carries
    its own denominator (`out_of`), since that one is not the whole user base.
    """
    now = timezone.now()
    after_first_week = OuterRef("date_joined") + FIRST_WEEK
    came_back = (
        Q(Exists(Plan.objects.filter(user=OuterRef("pk"),
                                     updated_at__gt=after_first_week)))
        | Q(Exists(IncomeProfile.objects.filter(user=OuterRef("pk"),
                                                updated_at__gt=after_first_week)))
        | Q(Exists(SocialAccount.objects.filter(user=OuterRef("pk"),
                                                last_login_at__gt=after_first_week)))
    )
    signed_in = Q(Exists(SocialAccount.objects.filter(
        user=OuterRef("pk"), last_login_at__gte=now - datetime.timedelta(days=30))))
    older = users.filter(date_joined__lte=now - FIRST_WEEK)
    older_count = older.count()
    rows = [
        ("Saved in the last 7 days",
         users.filter(_saved_since(now - datetime.timedelta(days=7))).count(),
         total_users),
        ("Saved in the last 30 days",
         users.filter(_saved_since(now - datetime.timedelta(days=30))).count(),
         total_users),
        ("Signed in in the last 30 days", users.filter(signed_in).count(), total_users),
        ("Came back after their first week", older.filter(came_back).count(),
         older_count),
    ]
    return [
        {"measure": measure, "users": count, "out_of": out_of,
         "share": pct(count, out_of)}
        for measure, count, out_of in rows
    ]


def sign_in_providers(users, total_users):
    """How people sign in: distinct users per provider, and the overlaps.

    Counts only. The provider's subject id is never selected, so nothing here
    could be matched back to an account at Google, Discord or Patreon. An
    account with no provider at all is a password account.
    """
    per_provider = dict(
        SocialAccount.objects.filter(user__in=users)
        .values("provider").order_by()
        .annotate(people=Count("user", distinct=True))
        .values_list("provider", "people")
    )
    several = (users.annotate(providers=Count("social_accounts__provider", distinct=True))
               .filter(providers__gte=2).count())
    none = users.filter(~Exists(SocialAccount.objects.filter(user=OuterRef("pk")))).count()
    counts = [(label, per_provider.get(provider, 0))
              for provider, label in SocialAccount.PROVIDER_CHOICES]
    counts += [("Two or more", several), ("None (a password account)", none)]
    return [{"label": label, "users": count, "pct_of_total": pct(count, total_users)}
            for label, count in counts]


def supporters():
    """Active Patreon supporters by tier, in tier order.

    These are PATRONS, not users: someone can support without ever making an
    account, so the staff exclusion does not apply to the tier counts. "Linked"
    counts supporters linked to a non-staff site account. Entitlement is derived
    elsewhere (benefits.py) and is not needed here. The supporter `email` column
    is never selected: values() names every field this reads.
    """
    rows = (
        PatreonSupporter.objects.filter(is_active=True)
        .values("tier__name", "tier__order")
        .annotate(
            active=Count("id"),
            linked=Count("id", filter=Q(linked_user__isnull=False,
                                        linked_user__is_staff=False)),
            public=Count("id", filter=Q(is_public=True)),
        )
        .order_by(F("tier__order").asc(nulls_last=True), "tier__name")
    )
    return [
        {"tier": row["tier__name"] or "No tier", "active": row["active"],
         "linked": row["linked"], "public": row["public"]}
        for row in rows
    ]


def _row_feature(**lookups):
    """A user with an ACTIVE-plan banner row matching `lookups`."""
    return Q(Exists(active_rows().filter(user=OuterRef("pk"), **lookups)))


# One row per feature shipped since the dashboard was built: (label, filter).
# Each filter sits beside its label, so this list is also the definition of
# what "uses it" means. Row-level features read the ACTIVE plan only, like
# every banner figure. Each one is also covered by engaged_q, so no share can
# pass 100% of engaged. Lambdas because Exists() needs a fresh OuterRef query
# per use.
FEATURES = [
    ("More than one plan", lambda: _has(Plan, is_active=False)),
    ("An income profile (another game account)", lambda: _has(IncomeProfile)),
    ("A note on a planned banner", lambda: _row_feature(note__gt="")),
    ("Two-card odds on a banner", lambda: _row_feature(second_card__isnull=False)),
    ("A two-card target set", lambda: _row_feature(primary_target__isnull=False)),
    ("Reserved copies on a banner", lambda: _row_feature(reserved_copies__gt=0)),
    ("Step-up cards chosen", lambda: _has(UserStepUpSelection)),
    ("A favourite uma", lambda: _has(UserOshi)),
    ("Two or more favourites (a supporter perk)",
     lambda: _has(UserOshi, position__gte=1)),
    ("A campaign purchase planned",
     lambda: _has(UserPlannedPurchase, quantity__gte=1)),
    ("Shop ticket counts set",
     lambda: (Q(shop_uma_tickets_bought__isnull=False)
              | Q(shop_support_tickets_bought__isnull=False))),
    ("Tickets kept off planned banners", lambda: Q(spend_tickets_on_banners=False)),
    ("A display name",
     lambda: Q(display_name__isnull=False) & ~Q(display_name="")),
]


def feature_adoption(users, engaged_users):
    """How many people use each feature, as a share of engaged users."""
    rows = []
    for label, condition in FEATURES:
        count = users.filter(condition()).count()
        rows.append({"feature": label, "users": count,
                     "pct_of_engaged": pct(count, engaged_users)})
    return rows
