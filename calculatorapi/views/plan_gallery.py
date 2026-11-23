"""
Community plans: posting a plan, the feed, and copying one.

POST /plans/<id>/publish                   post the plan, or update its post (signed in)
GET  /plan-gallery                         the feed, ranked and filtered
     ?sort=top|new&window=day|week|month|all
     &range=<scenario id>&strategy=front,pace&spending=<value>&sweat=<value>&limit=N
POST /plan-gallery/posts/<public_id>/copy  take a copy: a new plan, or (a guest)
                                           the rows for the device

Everything else about a plan post (reading it, votes, views, comments,
reports, taking it down) is on the shared /gallery/posts routes
(views/gallery.py), as a team's is. The rules are plan_gallery.py's; these
views only shape requests.

All three refuse, with one sentence, anyone plan_gallery.can_use() says no
to: early access first (the owner, 2026-11-20). The AllowAny on the feed and
the copy is for when it opens to everyone; until then a guest is refused.

POSTING IS THE OPT-IN, AND SO ARE NOTES AND INCOME
--------------------------------------------------
`confirm_public_identity`, `include_notes` and `include_income` each count only
when literally `true`: agreeing to show your name, and choosing to show your
notes and your income, are the person's own ticks in the dialog, every time.

A COPY IS A POST, NEVER A SIDE EFFECT OF READING
------------------------------------------------
Copying is a POST, as a shared plan's is (views/plan_public.py): a GET that
created plans would fire from every crawler and link preview.
"""

from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from calculatorapi import gallery, gallery_ranking, plan_gallery, plans
from calculatorapi.models import PLAN_CAP, PLAN_STRATEGIES, GalleryPost, Plan, PlanSpending, PlanSweat
from calculatorapi.views.gallery import (
    NOT_FOUND,
    GalleryCopyThrottle,
    GalleryReadThrottle,
    post_payload,
    refused,
    serialize_posts,
)
from calculatorapi.views.plan import PlanSerializer
from calculatorapi.views.plan_routes import plan_with_rows
from calculatorapi.views.pvp_gallery import GALLERY_LIMIT_MAX, GALLERY_PAGE_SIZE, GalleryPublishThrottle

REFUSED = {"error": plan_gallery.REFUSED_TEXT}


def _int_param(request, name):
    """A whole-number query parameter, or None when missing or not a number."""
    try:
        return int(request.query_params[name])
    except (KeyError, ValueError):
        return None


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryPublishThrottle])
def plan_publish(request, plan_id):
    try:
        plan = plans.get_owned_plan(request.user, plan_id)
    except Plan.DoesNotExist:
        return Response({"error": "Plan not found"}, status=status.HTTP_404_NOT_FOUND)
    body = request.data
    try:
        post, created = plan_gallery.publish_plan(
            request.user,
            plan,
            caption=body.get("caption", ""),
            strategy=body.get("strategy", ""),
            spending=body.get("spending", ""),
            sweat=body.get("sweat", ""),
            strategies=body.get("strategies"),
            include_notes=body.get("include_notes", False),
            include_income=body.get("include_income", False),
            confirm_public_identity=body.get("confirm_public_identity", False),
        )
    except gallery.GalleryRefused as refusal:
        return refused(refusal)
    return Response(
        post_payload(post, request.user),
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryReadThrottle])
def plan_gallery_feed(request):
    """The top `limit` plans for one sort and set of filters. "Load more"
    asks for a longer list from the top, never the next slice: Top moves."""
    if not plan_gallery.can_use(request.user):
        return Response(REFUSED, status=status.HTTP_403_FORBIDDEN)
    params = request.query_params
    sort = params.get("sort") if params.get("sort") in plan_gallery.SORTS else plan_gallery.SORT_TOP
    window = params.get("window") if params.get("window") in plan_gallery.TOP_WINDOWS else plan_gallery.DEFAULT_WINDOW
    limit = _int_param(request, "limit")
    limit = min(max(limit, 1), GALLERY_LIMIT_MAX) if limit is not None else GALLERY_PAGE_SIZE
    # An unknown value for a filter is no filter at all, as an unknown sort is.
    strategies = [style for style in params.get("strategy", "").split(",") if style in PLAN_STRATEGIES]
    spending = params.get("spending") if params.get("spending") in PlanSpending.values else ""
    sweat = params.get("sweat") if params.get("sweat") in PlanSweat.values else ""

    bundle = plan_gallery.ranked(sort, window)
    matches = plan_gallery.filtered(
        bundle, range_id=_int_param(request, "range"), strategies=strategies, spending=spending, sweat=sweat
    )
    ids = [entry[0] for entry in matches[:limit]]
    # Fetched through the read filter: the remembered order decides order
    # only, never what is shown (plan_gallery.py).
    posts = gallery_ranking.with_points(
        gallery.visible_posts().filter(id__in=ids).select_related("author", "plan_range")
    )
    by_id = {post.id: post for post in posts}
    return Response({
        "count": len(matches),
        "sort": sort,
        "window": window if sort == plan_gallery.SORT_TOP else None,
        "limit": limit,
        "has_more": len(matches) > limit,
        "results": serialize_posts([by_id[post_id] for post_id in ids if post_id in by_id], request.user),
        "filters": plan_gallery.filter_choices(bundle),
    })


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryCopyThrottle])
def plan_post_copy(request, public_id):
    """Signed in: a new plan in the caller's account, returned as POST /plans
    returns one (inactive; the client switches to it). A guest: the rows, in
    the device store's shape, and nothing else. Counted unless it is the
    author's own post."""
    try:
        post = gallery.get_post(public_id, request.user)
    except GalleryPost.DoesNotExist:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    if not post.is_plan:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    if not plan_gallery.can_use(request.user):
        return Response(REFUSED, status=status.HTTP_403_FORBIDDEN)
    known = plan_gallery.known_banners([post.plan_snapshot])
    if request.user.is_authenticated:
        return _account_copy(request, post, known)
    rows = plan_gallery.rows_for_display(post.plan_snapshot, known)
    gallery.count_post_copy(request, post)
    # The keys a shared plan's guest copy uses, so the client stores both the
    # same way. Never the author's income: a copy runs on the copier's numbers.
    return Response({
        "user_planned_banner_data": rows,
        "user_stats_data": None,
        "user_planned_purchase_data": None,
    })


def _account_copy(request, post, known):
    """A new plan in the caller's account, as POST /plans returns one."""
    # The name is checked exactly as POST /plans checks one; only `name` is
    # passed in, so nothing else in the body can reach it.
    serializer = PlanSerializer(data={"name": request.data.get("name")})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    try:
        plan = plans.plan_from_snapshot(
            post.plan_snapshot, owner=request.user, name=serializer.validated_data["name"], known=known
        )
    except plans.PlanCapReached:
        return Response({"error": f"You can keep up to {PLAN_CAP} plans."}, status=status.HTTP_400_BAD_REQUEST)
    gallery.count_post_copy(request, post)
    return Response(plan_with_rows(plan), status=status.HTTP_201_CREATED)
