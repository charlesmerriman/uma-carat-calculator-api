"""
GET  /plans/public/<public_id>        what a shared plan holds, row by row: the
                                      landing card's summary.
POST /plans/public/<public_id>/copy   take a copy of it. Signed in: a new plan
                                      in the caller's account. Guest: the rows
                                      to keep on the device, plus the owner's
                                      resources (stats and planned purchases)
                                      only if the guest asks for them.

Both AllowAny. Both answer 404 unless the plan's owner switched sharing on
(plans.get_shared_plan), with the same body as an id that does not exist.

A SHARED PLAN IS COPIED, NOT OPENED
-----------------------------------
The landing card shows what a plan holds and offers a copy; it is never the
owner's plan opened in the calculator. A plan is editable only by its owner (views/plan_routes.py, every id through
plans.get_owned_plan), and these routes have no path that writes to the source
beyond bumping its copy count. That is why they live in their own module with
their own serializer rather than as a flag on the owner's routes: one bad
branch in a shared view would turn "show me that plan" into "let me edit it".

WHAT LEAVES THE ACCOUNT
-----------------------
The summary: the rows, a row count, the plan's pull and step totals, the copy
count, the last-updated date and the two share options. No stats, no plan id,
no plan name, nothing about the owner. The landing card resolves the rows'
ids against the catalogue the client already holds. (The totals arrived with
the link preview, owner's call 2026-10-16: the same two numbers the preview at
/plan/<id> states, views/plan_share.py.)

The rows are the same rows a guest copy takes, through one serializer
(SharedPlannedBannerSerializer, owner's call 2026-10-30): the card shows each
banner's pulls because anyone holding the link could already copy them. The
shape is the one the device store keeps (catalogue ids and counts, no row id,
no user, no plan), each `note` only when the owner turned share_notes on.

The guest copy is the only place the owner's resources leave an account: the
stats block and its planned purchases, only when the owner turned share_stats
on AND the guest asked for them (`include_stats`). A guest's device holds one set of
resources, so taking the owner's replaces their own; that has to be the
guest's choice, never a side effect of the owner's. A signed-in copy never sends any of that back, it
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
from calculatorapi.models import (
    PLAN_CAP,
    BannerTimeline,
    Plan,
    UserPlannedBanner,
    UserPlannedPurchase,
)
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
    """One row of a shared plan, on the landing card and in a guest copy.

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
            # The row options that are choices. NOT pulls_already_made: that
            # is the owner's own pull history, so a guest copy starts at 0.
            "tickets_to_use",
            "paid_carat_use",
            "excluded_from_projection",
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


class SharedPlannedPurchaseSerializer(serializers.ModelSerializer):
    """One of the owner's planned purchases as a guest copy stores it: the
    device store's shape (catalogue ids and a count). No row id, no `user`, no
    `income_profile`: nothing that points back at the owner's account."""

    class Meta:
        model = UserPlannedPurchase
        fields = ("product", "quantity", "target_uma", "target_support")
        read_only_fields = fields


def rows_in_order(plan):
    """The plan's rows in the order its owner sees them (see
    planned_banners_in_order). The banner FKs are joined because both sort
    keys read them, and their timelines because the link preview
    (views/plan_share.py) looks for art on them; one join is cheaper than a
    query per row there."""
    rows = UserPlannedBanner.objects.filter(plan=plan).select_related(
        "banner_uma__banner_timeline",
        "banner_support__banner_timeline",
        "banner_step_up__banner_timeline",
    )
    return planned_banners_in_order(
        rows, emap=build_effective_date_maps()[BannerTimeline]
    )


def plan_totals(rows):
    """(pulls, steps) across a plan's rows.

    `number_of_pulls` counts STEPS on a step-up row (the column's overload,
    models/user_planned_banner.py), so the two are summed apart and never
    converted: a step's pull count depends on the ladder, and the preview and
    the card only state what the owner typed.

    A row the owner left out of the projection is left out here too: it is a
    "maybe", and a total that counts it would claim more than the plan spends."""
    counted = [row for row in rows if not row.excluded_from_projection]
    pulls = sum(row.number_of_pulls for row in counted if not row.banner_step_up_id)
    steps = sum(row.number_of_pulls for row in counted if row.banner_step_up_id)
    return pulls, steps


def _summary(plan):
    rows = rows_in_order(plan)
    pulls, steps = plan_totals(rows)
    return {
        "public_id": plan.public_id,
        "updated_at": plan.updated_at,
        "copy_count": plan.copy_count,
        # So the card can say what a copy brings beyond the rows.
        "share_stats": plan.share_stats,
        "share_notes": plan.share_notes,
        "row_count": len(rows),
        "pull_count": pulls,
        "step_count": steps,
        # Exactly one banner id is set on every row (the model's
        # exactly_one_banner_target constraint).
        "rows": SharedPlannedBannerSerializer(
            rows, many=True, context={"share_notes": plan.share_notes}
        ).data,
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
    guest asked, their resources (the stats block and its purchases). Counted
    once the payload is built, so a refused or failed request is not a copy."""
    # Checked strictly, like PATCH /plans/<id>'s operation keys: "yes" or 1 is
    # a client bug, and guessing would decide whose carats land on the device.
    include_stats = request.data.get("include_stats", False)
    if not isinstance(include_stats, bool):
        return Response({"include_stats": ["Must be true or false."]},
                        status=status.HTTP_400_BAD_REQUEST)
    rows = SharedPlannedBannerSerializer(
        rows_in_order(source), many=True,
        context={"share_notes": source.share_notes},
    ).data
    stats = purchases = None
    if source.share_stats and include_stats:
        stats = stats_serializer(plans.stats_target(source)).data
        # The same block's purchases (plans.purchase_scope), so a plan with
        # its own resources hands out its own, not the account's.
        purchases = SharedPlannedPurchaseSerializer(
            UserPlannedPurchase.objects.filter(
                **plans.purchase_scope(source)
            ).order_by("id"),
            many=True,
        ).data
    plans.count_copy(source)
    return Response({
        "user_planned_banner_data": rows,
        "user_stats_data": stats,
        "user_planned_purchase_data": purchases,
    })


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
