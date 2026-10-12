"""
GET  /plans/public/<public_id>        what a shared plan holds, as catalogue ids
                                      and counts: the landing card's summary.
POST /plans/public/<public_id>/copy   take a copy of it. Signed in: a new plan
                                      in the caller's account. Guest: the rows
                                      to keep on the device, plus the owner's
                                      stats only if the guest asks for them.

Both AllowAny. Both answer 404 unless the plan's owner switched sharing on
(plans.get_shared_plan), with the same body as an id that does not exist.

A SHARED PLAN IS COPIED, NEVER VIEWED
-------------------------------------
There is no "open someone's plan" here, read-only or otherwise. A plan is
editable only by its owner (views/plan_routes.py, every id through
plans.get_owned_plan), and these routes have no path that writes to the source
beyond bumping its copy count. That is why they live in their own module with
their own serializer rather than as a flag on the owner's routes: one bad
branch in a shared view would turn "show me that plan" into "let me edit it".

WHAT LEAVES THE ACCOUNT
-----------------------
The summary: catalogue ids, a row count, the copy count, the last-updated date
and the two share options. No pull counts, no notes, no stats, no plan id, no
plan name, nothing about the owner. The landing card resolves the ids against
the catalogue the client already holds.

The guest copy is the ONE place full rows leave an account, and only for a
plan its owner chose to share: the rows in the shape the device store keeps
(catalogue ids and counts, no row id, no user, no plan), each `note` only
when the owner turned share_notes on, and the stats block only when the owner
turned share_stats on AND the guest asked for it (`include_stats`). A guest's
device holds one set of stats, so taking the owner's replaces their own; that
has to be the guest's choice, never a side effect of the owner's. A signed-in copy never sends any of that back, it
creates the plan server-side (plans.copy_shared_plan) and returns it the way
POST /plans does.

THROTTLES
---------
UserRateThrottle, not AnonRateThrottle: these routes are open to guests AND
accounts, and AnonRateThrottle does not count a signed-in caller at all.
UserRateThrottle keys an account by its id and a guest by address. Rates in
settings.py.
"""

from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from calculatorapi import plans
from calculatorapi.models import PLAN_CAP, BannerTimeline, Plan, UserPlannedBanner
from calculatorapi.predictions import build_effective_date_maps
from calculatorapi.views.calculator import planned_banners_in_order
from calculatorapi.views.income_profile import stats_serializer
from calculatorapi.views.plan import PlanSerializer
from calculatorapi.views.plan_routes import plan_with_rows

# One body for "no such id" and "not shared", so the two cannot be told apart.
_NOT_FOUND = {"error": "Plan not found"}


class PublicPlanThrottle(UserRateThrottle):
    """Caps the summary route. Each request costs a date-map build."""

    scope = "public_plan"


class PublicPlanCopyThrottle(UserRateThrottle):
    """Caps the copy route: each accepted request writes (a new plan, or the
    count) and the guest copy hands out a plan's full rows."""

    scope = "public_plan_copy"


class SharedPlannedBannerSerializer(serializers.ModelSerializer):
    """One row of a shared plan as a guest copy stores it.

    The device store's shape (frontend services/guestPlanStore.ts): catalogue
    ids and counts. Not UserPlannedBannerSerializer, which is the OWNER'S row:
    it carries the row `id`, the owner's `user` id and the `plan` id, and nests
    the whole banner. This field list is the whitelist; `note` is dropped
    unless the context says the owner shares notes.
    """

    class Meta:
        model = UserPlannedBanner
        fields = (
            "number_of_pulls",
            "reserved_copies",
            "note",
            "primary_card",
            "second_card",
            "primary_target",
            "banner_uma",
            "banner_support",
            "banner_step_up",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("share_notes"):
            data.pop("note")
        return data


def _rows_in_order(plan):
    """The plan's rows in the order its owner sees them (see
    planned_banners_in_order). Only the banner FKs are joined: both sort keys
    read them, and neither payload here nests a banner."""
    rows = UserPlannedBanner.objects.filter(plan=plan).select_related(
        "banner_uma", "banner_support", "banner_step_up"
    )
    return planned_banners_in_order(
        rows, emap=build_effective_date_maps()[BannerTimeline]
    )


def _summary(plan):
    rows = _rows_in_order(plan)
    return {
        "public_id": plan.public_id,
        "updated_at": plan.updated_at,
        "copy_count": plan.copy_count,
        # So the card can say what a copy brings beyond the rows.
        "share_stats": plan.share_stats,
        "share_notes": plan.share_notes,
        "row_count": len(rows),
        # Exactly one of the three is set on every row (the model's
        # exactly_one_banner_target constraint).
        "rows": [
            {
                "banner_uma": row.banner_uma_id,
                "banner_support": row.banner_support_id,
                "banner_step_up": row.banner_step_up_id,
            }
            for row in rows
        ],
    }


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([PublicPlanThrottle])
def public_plan_summary(request, public_id):
    """The landing card's data. Reading it never creates or changes anything,
    so a link preview or a crawler fetching the page is harmless."""
    try:
        plan = plans.get_shared_plan(public_id)
    except Plan.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    return Response(_summary(plan))


def _guest_copy(request, source):
    """What a guest takes away: the rows and, if the owner shared them AND the
    guest asked, the stats. Counted once the payload is built, so a refused or
    failed request is not a copy."""
    # Checked strictly, like PATCH /plans/<id>'s operation keys: "yes" or 1 is
    # a client bug, and guessing would decide whose carats land on the device.
    include_stats = request.data.get("include_stats", False)
    if not isinstance(include_stats, bool):
        return Response({"include_stats": ["Must be true or false."]},
                        status=status.HTTP_400_BAD_REQUEST)
    rows = SharedPlannedBannerSerializer(
        _rows_in_order(source), many=True,
        context={"share_notes": source.share_notes},
    ).data
    stats = (
        stats_serializer(plans.stats_target(source)).data
        if source.share_stats and include_stats else None
    )
    plans.count_copy(source)
    return Response({"user_planned_banner_data": rows, "user_stats_data": stats})


def _account_copy(request, source):
    """A new plan in the caller's account, returned as POST /plans returns
    one: created inactive, the client switches to it."""
    # The name is validated exactly as POST /plans validates one, by the same
    # serializer. Only `name` is passed in, so nothing else in the body can
    # reach it.
    serializer = PlanSerializer(data={"name": request.data.get("name")})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    try:
        plan = plans.copy_shared_plan(
            source, owner=request.user, name=serializer.validated_data["name"]
        )
    except plans.PlanCapReached:
        return Response(
            {"error": f"You can keep up to {PLAN_CAP} plans."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(plan_with_rows(plan), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([PublicPlanCopyThrottle])
def public_plan_copy(request, public_id):
    """Take a copy. A POST, never a side effect of opening the link: a GET
    that created plans would fire from every link preview and crawler."""
    try:
        source = plans.get_shared_plan(public_id)
    except Plan.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    if request.user.is_authenticated:
        return _account_copy(request, source)
    return _guest_copy(request, source)
