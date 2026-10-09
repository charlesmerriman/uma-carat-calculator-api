"""Who the report counts: the non-staff user base and its engaged subset."""

from django.db.models import Q

from ..models import CustomUser
from .income_settings import PAID_PRODUCT_FIELDS, RANK_FIELDS, RESOURCE_FIELDS


def non_staff_users():
    """Every account the report may count.

    Staff accounts (the site owner, devs) are excluded from every metric so
    admin test data never skews the numbers.
    """
    return CustomUser.objects.filter(is_staff=False)


def engaged_q():
    """
    Q filter matching "engaged" users: anyone who has changed at least one
    calculator setting away from its default, or planned at least one banner.

    Every CustomUser stat defaults to False/0/null, so accounts that
    registered but never touched the calculator would otherwise drag every
    percentage down. Reports show both denominators (total vs engaged).
    """
    q = Q(userplannedbanner__isnull=False)
    # Planning a campaign purchase is using the calculator too. Without this, a
    # user whose only action was planning a selector would be a buyer who is
    # not "engaged", and "% of engaged" could pass 100.
    q |= Q(userplannedpurchase__isnull=False)
    for field, _ in PAID_PRODUCT_FIELDS:
        q |= Q(**{field: True})
    for field, _ in RANK_FIELDS:
        q |= Q(**{f"{field}__isnull": False})
    for field, _ in RESOURCE_FIELDS:
        # "changed from default" = any non-zero value (defaults are all 0)
        q |= ~Q(**{field: 0})
    return q


def engaged_subset(users):
    """The engaged subset of `users`, as a queryset safe to count and aggregate.

    The engaged filter joins through UserPlannedBanner, which can duplicate
    user rows; re-filtering by pk keeps `engaged` a clean single-table
    queryset that is safe to count and aggregate over.
    """
    return users.filter(pk__in=users.filter(engaged_q()).values("pk"))
