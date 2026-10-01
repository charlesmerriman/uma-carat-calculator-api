"""
GET    /plans        the caller's plans (name, which is active). No rows.
POST   /plans        create one: blank, or `copy_from` another of theirs.
GET    /plans/<id>   one plan plus ITS banner rows, stats and purchases. What
                     a switch fetches.
PATCH  /plans/<id>   rename it, make it the active plan, and/or give it its
                     own stats (`separate_income`: true / false).
DELETE /plans/<id>   delete it and its rows. The last plan is refused.

All IsAuthenticated. A guest has one unnamed plan in memory and never calls
these.

WHY SWITCHING HAS ITS OWN ROUTE
-------------------------------
GET /calculator-data already returns the active plan's rows, so a switch could
"activate, then refetch". But that response is about a megabyte of catalogue
that does not change when someone picks a different plan. GET /plans/<id>
returns the one collection that does.

WHY SAVING ROWS IS NOT HERE
---------------------------
Banner rows are still written by PATCH /calculator-data, now carrying a
`plan_id`. The auto-save sends stats, banners, purchases and selections
together and they stand or fall together in one transaction; splitting banners
off to this route would let half a save land.

OWNERSHIP
---------
Every plan id below is resolved through plans.get_owned_plan() and nothing
else. Somebody else's plan is a 404, identical to one that does not exist.
When plans can be published, reading another person's plan gets its OWN view
and serializer -- never a flag on these. Same reasoning as account_linking.py
being separate from social_auth.py: one bad branch in a shared view would turn
"show me that plan" into "let me edit it".
"""

from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import plans
from calculatorapi.models import PLAN_CAP, Plan
from calculatorapi.views.calculator import (
    build_user_context,
    serialize_planned_banners,
    serialize_planned_purchases,
)
from calculatorapi.views.income_profile import stats_serializer
from calculatorapi.views.plan import PlanSerializer

_NOT_FOUND = {"error": "Plan not found"}


def _plan_list(user):
    # get_active_plan first, so an account that has never opened the calculator
    # is answered with its Main plan rather than an empty list.
    plans.get_active_plan(user)
    return PlanSerializer(Plan.objects.filter(user=user), many=True).data


def _plan_with_rows(plan):
    """A plan, its banner rows, its stats and its purchases, shaped like the
    matching /calculator-data keys so the client stores them with the same
    code. Everything that changes on a switch, and nothing that does not."""
    emap, anniversary_emap, card_context = build_user_context()
    return {
        "plan": PlanSerializer(plan).data,
        # The stats this plan is projected against: its income profile's, or
        # the account's. Same shape either way, so a switch swaps them in with
        # the rows and the client never learns which it got.
        "user_stats_data": stats_serializer(plans.stats_target(plan)).data,
        "user_planned_banner_data": serialize_planned_banners(
            plan, emap=emap, card_context=card_context
        ),
        # The purchases of that same stats block (plans.purchase_scope).
        "user_planned_purchase_data": serialize_planned_purchases(
            plans.purchase_scope(plan), anniversary_emap=anniversary_emap
        ),
    }


@api_view(["GET", "POST"])
@permission_classes([permissions.IsAuthenticated])
def plan_list(request):
    if request.method == "GET":
        return Response(_plan_list(request.user))

    serializer = PlanSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    copy_from = None
    if request.data.get("copy_from") is not None:
        # The source must be the caller's own, by the same check as any other
        # plan id. copy_plan() itself does not check -- see its docstring.
        try:
            copy_from = plans.get_owned_plan(request.user, request.data["copy_from"])
        except Plan.DoesNotExist:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

    try:
        plan = plans.create_plan(
            request.user, serializer.validated_data["name"], copy_from=copy_from
        )
    except plans.PlanCapReached:
        return Response(
            {"error": f"You can keep up to {PLAN_CAP} plans."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Created INACTIVE. The client switches to it with a PATCH if it wants to,
    # after it has flushed any pending save for the plan it is leaving.
    return Response(_plan_with_rows(plan), status=status.HTTP_201_CREATED)


def _update_plan(request, plan):
    """PATCH /plans/<id>: rename, activate, and/or toggle its own stats. The
    name goes through the serializer; the other two are operations on the
    plan, handled beside it, because each has to do more than write a field."""
    serializer = PlanSerializer(plan, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    # separate_income is validated BEFORE anything is written, so a bad value
    # cannot land a rename and then refuse.
    separate = request.data.get("separate_income")
    if separate is not None and not isinstance(separate, bool):
        return Response({"separate_income": ["Must be true or false."]},
                        status=status.HTTP_400_BAD_REQUEST)
    serializer.save()
    # Only `true` means anything. There is no "deactivate": an account
    # always has exactly one active plan, so the way to leave a plan is to
    # activate another.
    if request.data.get("is_active") is True:
        plans.set_active_plan(plan)
    # true creates a profile seeded from the plan's current stats and points
    # the plan at it; false points it back at the account and drops an
    # unshared profile. The client refetches GET /plans/<id> for the stats.
    if separate is True:
        plans.attach_income_profile(plan)
    elif separate is False:
        plans.detach_income_profile(plan)
    return Response(PlanSerializer(plan).data)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def plan_detail(request, plan_id):
    try:
        plan = plans.get_owned_plan(request.user, plan_id)
    except Plan.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(_plan_with_rows(plan))

    if request.method == "PATCH":
        return _update_plan(request, plan)

    # DELETE. An account always has a plan to open on, so the last one stays.
    # Emptying it is what clearing its rows is for.
    if Plan.objects.filter(user=request.user).count() <= 1:
        return Response(
            {"error": "You can't delete your only plan."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    active = plans.delete_plan(plan)
    # The client needs to know where it landed if it deleted the open plan.
    return Response({"active_plan_id": active.id})
