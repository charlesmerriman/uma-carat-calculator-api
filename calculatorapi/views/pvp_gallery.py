"""
The PvP team gallery: posting a team, and one event's feed.

POST /pvp-teams/<id>/publish            post the team, or replace your post for
                                        its event (signed in)
GET  /pvp-gallery/<kind>/<event_id>     the event's posts, ranked (anyone)
     ?sort=hot|top|new&uma=<id>&limit=N

Everything about one post (votes, views, copies, taking it down) is on the
shared /gallery/posts routes (views/gallery.py).

PUBLISHING IS THE OPT-IN
------------------------
The body must carry `"confirm_public_identity": true`, literally: it is the
person's agreement, in the post dialog, that their display name, favourite uma
picture and (if they allow it) supporter tier show beside the team. The rules
are gallery.publish_team's; this view only shapes the request.
"""

from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response

from calculatorapi import gallery, gallery_ranking, pvp_teams
from calculatorapi.models import PvpTeam
from calculatorapi.views.gallery import (
    GalleryReadThrottle,
    GalleryThrottle,
    post_payload,
    refused,
    serialize_posts,
    uma_tally,
)

# How many rows a feed request returns by default, and the most it may ask for.
# "Load more" asks for a LONGER list from the top, never for the next slice:
# Hot and Top are re-ranked on every request, so an offset page cut from a
# fresh ranking repeats a post that sank and skips one that rose since the
# page before it. Re-sending the rows above costs a few kilobytes; one event's
# gallery is small (gallery_ranking.py, "Scale").
GALLERY_PAGE_SIZE = 20
GALLERY_LIMIT_MAX = 200


class GalleryPublishThrottle(GalleryThrottle):
    """Posting again replaces your post and moves it to the top of New, so it
    is capped: a handful of honest re-posts an hour, not a bump every minute."""

    scope = "gallery_publish"


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryPublishThrottle])
def pvp_team_publish(request, team_id):
    try:
        team = pvp_teams.get_owned_team(request.user, team_id)
    except PvpTeam.DoesNotExist:
        return Response({"error": "Team not found"}, status=status.HTTP_404_NOT_FOUND)
    caption = request.data.get("caption", "")
    if not isinstance(caption, str):
        return Response({"error": "The caption must be text."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        post, created = gallery.publish_team(
            request.user,
            team,
            caption=caption,
            confirm_public_identity=request.data.get("confirm_public_identity"),
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
def pvp_gallery_feed(request, kind, event_id):
    """One event's posts, ranked, the top `limit` of them.

    The whole event is ranked and then cut, because Hot depends on every
    post's age and points together; one event's gallery is small enough
    (gallery_ranking.py, "Scale"). `uma` keeps the posts whose team has that
    uma; `umas` lists every uma in the event's posts, filter or not, with how
    many posts have it, so the filter only offers umas that find something.
    """
    try:
        event = pvp_teams.resolve_event(kind, event_id)
    except pvp_teams.TeamRefused:
        return Response({"error": "Event not found"}, status=status.HTTP_404_NOT_FOUND)
    sort = request.query_params.get("sort", gallery_ranking.SORT_HOT)
    if sort not in gallery_ranking.SORTS:
        sort = gallery_ranking.SORT_HOT
    limit = _int_param(request, "limit", GALLERY_PAGE_SIZE)
    limit = min(max(limit, 1), GALLERY_LIMIT_MAX) if limit is not None else GALLERY_PAGE_SIZE
    # An unreadable uma is no filter at all; a readable one nobody used is an
    # empty feed, which the client words as such.
    uma = _int_param(request, "uma", None)

    posts = gallery.visible_posts().filter(**event).select_related("author")
    ranked = gallery_ranking.ranked(posts, sort)
    tally = uma_tally(ranked)
    if uma is not None:
        ranked = [post for post in ranked if any(member["uma"] == uma for member in post.team_snapshot)]
    return Response({
        "count": len(ranked),
        "sort": sort,
        "limit": limit,
        "has_more": len(ranked) > limit,
        "results": serialize_posts(ranked[:limit], request.user),
        "umas": tally,
    })


def _int_param(request, name, default):
    """A whole-number query parameter, `default` when it is missing, and None
    when it is there but not a number."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return None
