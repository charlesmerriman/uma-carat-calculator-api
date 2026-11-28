"""
Community guides: a player's own PvP guide, written, posted and listed
(notes/community-guides-plan.md). The service is guide_gallery.py.

GET    /community-guides/mine?kind=&event=   my working copies (for one event, or all)
POST   /community-guides                     {kind, event, title?, start} -> 201 the document
GET    /community-guides/<id>/document       the document           (its author)
PUT    /community-guides/<id>/document       {version, title?, ...document} -> {document, guide}
POST   /community-guides/<id>/start-over     {version, start} -> {document, guide}
DELETE /community-guides/<id>                204, refused while its post is up
POST   /community-guides/<id>/publish        {confirm_public_identity} -> the post (201 new, 200 updated)
GET    /pvp-guide-gallery/<kind>/<event_id>  ?sort=top|new -> the event's guides

`start` is "empty", "official" (the event's site guide, when the writer can
read it) or the id of another of the writer's guides for the same event.

Reading one guide post, voting, counting a view, comments, reports and taking
a post down are the gallery's shared routes (views/gallery.py), which ask
gallery_access.can_open for this kind like every other.

SHAPES THE EDITOR ALREADY READS
-------------------------------
A working copy travels in the site editor's document shape (plus `title`,
`kind`, `post`), a save answers {document, guide} with `guide` the reader's
own shape for the preview, a refusal answers {error, errors: [{path,
message}]}, and a stale save answers 409 {error, current}: exactly what
views/pvp_guide_editor.py answers, so the same editor components work on both.

Someone else's working copy, and one that does not exist, are the same 404.
"""

from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from calculatorapi import gallery_ranking, guide_gallery
from calculatorapi.views.gallery import GalleryReadThrottle, post_payload, serialize_posts
from calculatorapi.views.pvp_gallery import GalleryPublishThrottle

NOT_FOUND = {"error": "That guide isn't available."}
REFUSED = {"error": guide_gallery.REFUSED_TEXT}


class CommunityGuideSaveThrottle(UserRateThrottle):
    """The site editor's rate: autosave at a two-second pause cannot reach it."""

    scope = "community_guide_save"


class CommunityGuideActionThrottle(UserRateThrottle):
    """Starting a guide and starting over: a handful an hour. A GET of the
    writer's own list is not an action (the save rate covers it)."""

    scope = "community_guide_action"

    def allow_request(self, request, view):
        return request.method == "GET" or super().allow_request(request, view)


def _refused(refusal):
    """A refusal in the editor's one shape: a sentence, and every problem with
    its path (an empty path when it names no field)."""
    errors = getattr(refusal, "errors", None) or [{"path": refusal.field or "", "message": refusal.message}]
    return Response({"error": refusal.message, "errors": errors}, status=refusal.status)


def _stale(stale):
    return Response(
        {"error": str(stale), "current": guide_gallery.document_payload(stale.guide)},
        status=status.HTTP_409_CONFLICT,
    )


def _answer(guide, request):
    """A working copy after a write: the document and the reader's shape."""
    return {
        "document": guide_gallery.document_payload(guide),
        "guide": guide_gallery.render_working_copy(guide, request),
    }


def _own(request, guide_id):
    return guide_gallery.owned_guide(request.user, guide_id)


def _start_value(raw):
    """The body's `start`: "empty", "official", or an id. None for anything else."""
    if raw in ("empty", "official"):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


@api_view(["GET", "POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([CommunityGuideSaveThrottle, CommunityGuideActionThrottle])
def community_guides(request):
    """GET: my working copies. POST: start one."""
    if request.method == "GET":
        kind, event = request.query_params.get("kind"), request.query_params.get("event")
        if kind is not None and (kind not in ("cm", "loh") or not str(event).isdigit()):
            return Response({"error": "Name an event as kind and event."}, status=status.HTTP_400_BAD_REQUEST)
        guides = guide_gallery.mine(request.user, kind, int(event) if kind else None)
        return Response({
            "guides": [guide_gallery.list_row(guide) for guide in guides],
            "cap": guide_gallery.COMMUNITY_GUIDE_CAP,
            "can_write": guide_gallery.can_write(request.user),
        })
    data = request.data
    start = _start_value(data.get("start", "empty"))
    kind, event = data.get("kind"), data.get("event")
    if start is None or not isinstance(event, int) or isinstance(event, bool):
        return _refused(guide_gallery.GuideRefused("Pick an event and where to start."))
    title = data.get("title", "")
    if not isinstance(title, str):
        title = ""
    try:
        guide = guide_gallery.create_guide(request.user, kind=kind, event_id=event, title=title, start=start)
    except guide_gallery.GuideRefused as refusal:
        return _refused(refusal)
    return Response(guide_gallery.document_payload(guide), status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([CommunityGuideSaveThrottle])
def community_guide_document(request, guide_id):
    guide = _own(request, guide_id)
    if guide is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    if request.method == "GET":
        return Response(_answer(guide, request))
    try:
        saved = guide_gallery.save(guide, request.data)
    except guide_gallery.Stale as stale:
        return _stale(stale)
    except guide_gallery.GuideRefused as refusal:
        return _refused(refusal)
    return Response(_answer(saved, request))


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([CommunityGuideActionThrottle])
def community_guide_start_over(request, guide_id):
    guide = _own(request, guide_id)
    if guide is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    start, version = _start_value(request.data.get("start")), request.data.get("version")
    if start is None or not isinstance(version, int) or isinstance(version, bool):
        return _refused(guide_gallery.GuideRefused("Pick where to start over from."))
    try:
        saved = guide_gallery.start_over(guide, version, start)
    except guide_gallery.Stale as stale:
        return _stale(stale)
    except guide_gallery.GuideRefused as refusal:
        return _refused(refusal)
    return Response(_answer(saved, request))


@api_view(["DELETE"])
@permission_classes([permissions.IsAuthenticated])
def community_guide_detail(request, guide_id):
    guide = _own(request, guide_id)
    if guide is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    try:
        guide_gallery.delete_guide(guide)
    except guide_gallery.GuideRefused as refusal:
        return _refused(refusal)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryPublishThrottle])
def community_guide_publish(request, guide_id):
    guide = _own(request, guide_id)
    if guide is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    try:
        post, created = guide_gallery.publish(
            request.user, guide, confirm_public_identity=request.data.get("confirm_public_identity"),
        )
    except guide_gallery.GuideRefused as refusal:
        return _refused(refusal)
    return Response(
        post_payload(post, request.user),
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryReadThrottle])
def pvp_guide_gallery_feed(request, kind, event_id):
    """One event's community guides, Top or New. Early access first: anyone
    else gets the same 403 the guides themselves answer."""
    if not guide_gallery.can_use(request.user):
        return Response(REFUSED, status=status.HTTP_403_FORBIDDEN)
    try:
        event, field = guide_gallery.resolve_event(kind, event_id)
    except guide_gallery.GuideRefused:
        return Response({"error": "Event not found"}, status=status.HTTP_404_NOT_FOUND)
    sort = request.query_params.get("sort", gallery_ranking.SORT_TOP)
    if sort not in guide_gallery.FEED_SORTS:
        sort = gallery_ranking.SORT_TOP
    ranked = guide_gallery.feed({field: event}, sort)
    shown = ranked[:guide_gallery.FEED_LIMIT_MAX]
    return Response({
        "count": len(ranked),
        "sort": sort,
        "has_more": len(ranked) > len(shown),
        "results": serialize_posts(shown, request.user),
    })
