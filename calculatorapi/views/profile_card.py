"""
GET /profile-cards/post/<public_id>    the card of whoever posted this team.
GET /profile-cards/comment/<id>        the card of whoever wrote this comment.

Clicking a name on a post or comment opens this. AllowAny, like the post page
it sits on.

NO ADDRESS FOR A PERSON
-----------------------
A card is reached through something the person posted, never an id of theirs:
an account has no public id on purpose, and a /u/<name> page would give every
player a permanent public address. So a card can't be linked to or bookmarked,
and these routes reach only what the post and comment threads already show.

WHAT IT HOLDS, AND WHY NOTHING ELSE
-----------------------------------
    favorites  every favorite uma their tier covers (public_identity)
    posts      their visible gallery posts, teams and Community plans, newest
               POSTS_SHOWN; a team says its event, a plan its totals
    plans      their shared plans' totals, only if they turned that on

The name, picture and tier aren't here: the card opens from a post or comment
that already shows them, so the client has them. A plan's name and id never
leave (the same rule as views/plan_public.py). "label", never "name": every
gallery test forbids a "name" key, so a team's private name can't slip through.

A hidden post, a blocked author, a deleted or hidden comment and an unknown id
all answer one 404 body. So does a Community plans post, or a comment on one,
for a viewer plan_gallery.can_open refuses, and their card lists no plan posts:
nothing links to a page they can't open (early access first, Design 8 of
notes/pull-plan-database-plan.md).
"""

from collections import defaultdict

from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from calculatorapi import gallery, gallery_ranking, plan_gallery, public_identity
from calculatorapi.models import GalleryComment, GalleryPost, GalleryPostKind, Plan, UserPlannedBanner
from calculatorapi.views.gallery import vote_fields
from calculatorapi.views.plan_public import plan_totals

POSTS_SHOWN = 10

_NOT_FOUND = {"error": "This card isn't available."}


class ProfileCardThrottle(UserRateThrottle):
    """A person opens a handful. Stops a script walking the comment ids."""

    scope = "profile_card"


def _post_row(post, known):
    """One post on the card: what it is, and a team's event or a plan's totals."""
    row = {
        "public_id": post.public_id,
        "kind": post.kind,
        "caption": post.caption,
        "score": vote_fields(post.ups, post.downs, None, None)["score"],
    }
    if post.is_plan:
        row_count, pulls, steps = plan_gallery.summary(plan_gallery.rows_for_display(post.plan_snapshot, known))
        # "totals", not "plan": the card's tests forbid a "plan" key, which is
        # what a plan's id is called everywhere else.
        row["totals"] = {"row_count": row_count, "pull_count": pulls, "step_count": steps}
    else:
        row["event"] = {
            "kind": post.event_kind,
            "id": post.champions_meeting_id or post.league_of_heroes_id,
            "label": post.event.name,
        }
    return row


def _posts(user, viewer):
    visible = gallery.visible_posts().filter(author=user)
    if not (plan_gallery.can_use(viewer) or (viewer.is_authenticated and viewer.pk == user.pk)):
        visible = visible.exclude(kind=GalleryPostKind.PLAN)
    posts = list(
        gallery_ranking.with_points(visible)
        .select_related("champions_meeting", "league_of_heroes")
        .order_by("-published_at", "-id")[:POSTS_SHOWN]
    )
    known = plan_gallery.known_banners([post.plan_snapshot for post in posts if post.is_plan])
    return [_post_row(post, known) for post in posts]


def _plans(user):
    """Shared plans' totals, for an account that opted in. Sharing a plan only
    says "anyone with the link", so listing it takes its own yes."""
    if not user.show_shared_plans_on_card:
        return []
    public_ids = dict(Plan.objects.filter(user=user, is_shared=True).values_list("id", "public_id"))
    rows = defaultdict(list)
    for row in UserPlannedBanner.objects.filter(plan_id__in=public_ids):
        rows[row.plan_id].append(row)
    cards = []
    for plan_id, public_id in sorted(public_ids.items()):
        pulls, steps = plan_totals(rows[plan_id])
        cards.append({
            "public_id": public_id,
            "row_count": len(rows[plan_id]),
            "pull_count": pulls,
            "step_count": steps,
        })
    return cards


def _card(user, viewer):
    return Response({
        "favorites": public_identity.favorite_umas(user),
        "posts": _posts(user, viewer),
        "plans": _plans(user),
    })


def _comment_author(comment_id, viewer):
    """Who wrote this comment, or None if this reader couldn't see it."""
    comment = (
        GalleryComment.objects.select_related("author", "guide", "post")
        .filter(pk=comment_id, is_deleted=False, is_hidden=False)
        .first()
    )
    if comment is None or comment.author is None or comment.author.publishing_blocked:
        return None
    if comment.post_id and not gallery.visible_posts().filter(pk=comment.post_id).exists():
        return None
    if comment.post_id and not plan_gallery.can_open(comment.post, viewer):
        return None
    if comment.guide_id and not comment.guide.is_out_of_draft:
        return None
    return comment.author


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([ProfileCardThrottle])
def profile_card_for_post(request, public_id):
    try:
        post = gallery.get_post(public_id)
    except GalleryPost.DoesNotExist:
        return Response(_NOT_FOUND, status=404)
    if not plan_gallery.can_open(post, request.user):
        return Response(_NOT_FOUND, status=404)
    return _card(post.author, request.user)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([ProfileCardThrottle])
def profile_card_for_comment(request, comment_id):
    author = _comment_author(comment_id, request.user)
    return Response(_NOT_FOUND, status=404) if author is None else _card(author, request.user)
