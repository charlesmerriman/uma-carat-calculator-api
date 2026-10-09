"""Who the report counts: the non-staff user base and its engaged subset."""

from django.db.models import Exists, OuterRef, Q

from ..models import (
    CustomUser,
    IncomeProfile,
    Plan,
    UserOshi,
    UserPlannedBanner,
    UserPlannedPurchase,
    UserStepUpSelection,
)
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
