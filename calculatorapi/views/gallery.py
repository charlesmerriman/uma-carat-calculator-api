"""
Everything about ONE gallery post, whatever gallery it is in.

GET    /gallery/posts/<public_id>         the post in full            (anyone)
DELETE /gallery/posts/<public_id>         take it down                (its author)
PUT    /gallery/posts/<public_id>/vote    vote it {"value": "up"|"down"} (signed in)
DELETE /gallery/posts/<public_id>/vote    take the vote back          (signed in)
POST   /gallery/posts/<public_id>/view    count a view                (anyone)
POST   /gallery/posts/<public_id>/copy    the team, for "Copy to my teams" (anyone)
GET    /gallery/my-posts                  the caller's own posts, hidden too (signed in)
GET    /gallery/posts/<public_id>/comments   the thread, oldest first (anyone)
POST   /gallery/posts/<public_id>/comments   comment or reply      (signed in)
PATCH  /gallery/comments/<id>             edit your comment       (its author)
DELETE /gallery/comments/<id>             delete your comment     (its author)
POST   /gallery/reports                   report a post or comment (signed in)

The feed that lists posts is per gallery (views/pvp_gallery.py); these routes
are shared so the community plan gallery reuses them unchanged.

WHAT A POST SENDS, AND WHAT IT NEVER DOES
-----------------------------------------
serialize_posts() is the field list: catalogue ids and styles for the team,
the counts people see, and public_identity.poster_block for the author. Never
a username, a user id, the team's name, notes or pin, a tier order, or who
voted, viewed or reported. A test pins the exact keys.

VOTES ON THE WIRE (Henry, 2026-10-26)
-------------------------------------
`score` is ups minus downs, never below 0 for anyone; the ranking uses the
real number. `my_vote` is "up", "down" or "none", null for a guest (who
cannot vote). Staff also get `split`, {"up", "down"}, to spot a pile-on
either way: how many, never who. vote_fields() builds all three.

A hidden post, a post by a blocked account and a post that does not exist all
answer the same 404, except to the post's own author, who sees theirs with
"hidden": true so the account page can say it is under review.
"""

import datetime

from django.core.cache import caches
from django.db.models import Count
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from calculatorapi import gallery, gallery_ranking, public_identity, pvp_guide_editing
from calculatorapi.models import GalleryComment, GalleryPost, GalleryVote, PvpGuide, SupportCard, Uma, VoteValue
from calculatorapi.models.pvp_guide import PvpStyle
from calculatorapi.views.pvp_guide import EVENT_FIELD_FOR_KIND

NOT_FOUND = {"error": "This team isn't available any more."}


class GalleryThrottle(UserRateThrottle):
    """Every gallery throttle counts in the "gallery" cache, not "default",
    so browsing the gallery cannot push the /calculator-data payload out of
    its 40 slots (settings.CACHES explains)."""

    cache = caches["gallery"]


class GalleryReadThrottle(GalleryThrottle):
    """Feeds, posts and comments. A page of posts is one ranking query plus a
    few lookups; 120/hour is a busy reader with room to spare, and stops a
    script walking the id space."""

    scope = "gallery_read"


class GalleryVoteThrottle(GalleryThrottle):
    scope = "gallery_vote"


class GalleryViewThrottle(GalleryThrottle):
    """Checked by hand in the view, not through @throttle_classes: an
    over-limit view is dropped quietly (still 204) rather than answered 429,
    because the client fires it and forgets."""

    scope = "gallery_view"


class GalleryCopyThrottle(GalleryThrottle):
    """The public_plan_copy figure: nobody copies thirty teams in an hour, and
    a script inflating a copy count is capped, not stopped."""

    scope = "gallery_copy"


def refused(refusal):
    """A GalleryRefused as a response: one sentence the client shows, and the
    field at fault when the refusal names one."""
    body = {"error": refusal.message}
    if refusal.field:
        body["field"] = refusal.field
    return Response(body, status=refusal.status)


def _known_ids(posts):
    """The uma and card ids in these posts' snapshots that are still in the
    catalogue: two queries for a whole page, for snapshot_for_display's rule."""
    uma_ids, card_ids = set(), set()
    for post in posts:
        for member in post.team_snapshot:
            uma_ids.add(member["uma"])
            card_ids.update(card["support_card"] for card in member["cards"])
    return (
        set(Uma.objects.filter(id__in=uma_ids).values_list("id", flat=True)),
        set(SupportCard.objects.filter(id__in=card_ids).values_list("id", flat=True)),
    )



def uma_tally(posts):
    """[{uma, count}] over these posts' teams, most-used first then by id:
    each uma counted once per post, and only umas still in the catalogue, so
    the feed's filter never offers one the rows cannot draw."""
    known, _ = _known_ids(posts)
    counts = {}
    for post in posts:
        for uma in {member["uma"] for member in post.team_snapshot} & known:
            counts[uma] = counts.get(uma, 0) + 1
    return [
        {"uma": uma, "count": count}
        for uma, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]

def _comment_counts(posts):
    """{post id: comments a reader can see}. Hidden, deleted and blocked
    authors' comments are not counted, matching what the thread shows."""
    return dict(
        GalleryComment.objects.filter(post__in=posts, is_hidden=False, is_deleted=False)
        .exclude(author__publishing_blocked=True)
        .values("post")
        .annotate(count=Count("id"))
        .values_list("post", "count")
    )


_VOTE_NAMES = {VoteValue.UP: "up", VoteValue.DOWN: "down"}
_VOTE_VALUES = {name: value for value, name in _VOTE_NAMES.items()}


def vote_fields(ups, downs, mine, viewer):
    """The score, the viewer's vote and (staff only) the split, for a post or
    a comment. `mine` is the viewer's VoteValue, or None."""
    signed_in = viewer is not None and viewer.is_authenticated
    fields = {
        "score": max(ups - downs, 0),
        "my_vote": _VOTE_NAMES.get(mine, "none") if signed_in else None,
    }
    if signed_in and viewer.is_staff:
        fields["split"] = {"up": ups, "down": downs}
    return fields


def vote_value(request):
    """The PUT body's vote: {"value": "up"} or {"value": "down"}. No body
    means up, so a tab still on the old page keeps working. None for
    anything else."""
    return _VOTE_VALUES.get(request.data.get("value", "up"))


def serialize_posts(posts, viewer, *, full=False):
    """Posts on the wire, in the order given. `posts` must carry
    `ups` and `downs` (gallery_ranking.with_points or ranked). `full` adds the
    whole team, the copy count and the event, for the post panel."""
    signed_in = viewer is not None and viewer.is_authenticated
    voted = dict(
        GalleryVote.objects.filter(user=viewer, post__in=posts).values_list("post_id", "value")
    ) if signed_in else {}
    blocks = public_identity.poster_blocks([post.author for post in posts])
    comments = _comment_counts(posts)
    umas, cards = _known_ids(posts)
    rows = []
    for post in posts:
        team = [
            {**member, "cards": [card for card in member["cards"] if card["support_card"] in cards]}
            for member in post.team_snapshot
            if member["uma"] in umas
        ]
        votes = vote_fields(post.ups, post.downs, voted.get(post.pk), viewer)
        row = {
            "public_id": post.public_id,
            "caption": post.caption,
            "published_at": post.published_at,
            "content_updated_at": post.content_updated_at,
            "is_mine": signed_in and viewer.pk == post.author_id,
            "umas": [{"uma": member["uma"], "style": member["style"]} for member in team],
            "stats": {
                "views": post.view_count,
                "score": votes["score"],
                **({"split": votes["split"]} if "split" in votes else {}),
                "comments": comments.get(post.pk, 0),
            },
            # null for a guest: "not voted" and "cannot vote" are different
            # things to draw.
            "my_vote": votes["my_vote"],
            "poster": blocks[post.author_id],
        }
        if full:
            # The long text is the open post's alone: a feed of rows each
            # carrying up to 2,000 characters nobody sees there is waste.
            row["strategy"] = post.strategy
            row["team"] = team
            row["stats"]["copies"] = post.copy_count
            row["event"] = {
                "kind": post.event_kind,
                "id": post.champions_meeting_id or post.league_of_heroes_id,
                # The event's name, for the account page's "Your posts", which
                # has no event list. "label", not "name": every gallery test
                # forbids a "name" key anywhere, so a team's private name can
                # never slip into a payload unnoticed.
                "label": post.event.name,
            }
            row["hidden"] = post.is_hidden or post.author.publishing_blocked
        rows.append(row)
    return rows


def _post_or_404(public_id, viewer):
    try:
        return gallery.get_post(public_id, viewer)
    except GalleryPost.DoesNotExist:
        return None


def post_payload(post, viewer):
    """One post in full, with fresh counts."""
    annotated = gallery_ranking.with_points(GalleryPost.objects.filter(pk=post.pk)).select_related(
        "author", "champions_meeting", "league_of_heroes"
    )
    return serialize_posts(list(annotated), viewer, full=True)[0]


@api_view(["GET", "DELETE"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryReadThrottle])
def gallery_post_detail(request, public_id):
    post = _post_or_404(public_id, request.user)
    if post is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        if not request.user.is_authenticated or request.user.pk != post.author_id:
            # Somebody else's post reads as no post at all.
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        gallery.take_down(post)
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(post_payload(post, request.user))


@api_view(["PUT", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryVoteThrottle])
def gallery_post_vote(request, public_id):
    post = _post_or_404(public_id, request.user)
    if post is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    value = vote_value(request)
    if value is None:
        return Response({"error": "A vote is up or down."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        if request.method == "PUT":
            gallery.vote(request.user, post, value)
        else:
            gallery.unvote(request.user, post)
    except gallery.GalleryRefused as refusal:
        return refused(refusal)
    ups, downs = gallery.tally(post.votes)
    return Response(vote_fields(ups, downs, value if request.method == "PUT" else None, request.user))


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def gallery_post_view(request, public_id):
    """Always 204, counted or not: the client has nothing to do with the
    answer, and a 404 or 429 here would only be noise in its console."""
    post = _post_or_404(public_id, request.user)
    if post is not None and GalleryViewThrottle().allow_request(request, None):
        gallery.record_view(request, post)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryCopyThrottle])
def gallery_post_copy(request, public_id):
    """The team for "Copy to my teams". Reading it never creates anything:
    the client adds the team through its own path (see gallery.copy_snapshot),
    so this is safe for a guest and changes only the count."""
    post = _post_or_404(public_id, request.user)
    if post is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    return Response({
        "kind": post.event_kind,
        "event": post.champions_meeting_id or post.league_of_heroes_id,
        "members": gallery.copy_snapshot(request, post),
    })


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def gallery_my_posts(request):
    """GET /gallery/my-posts: the caller's own posts, hidden ones included,
    newest first, for the account page's "Your posts"."""
    posts = gallery_ranking.with_points(
        GalleryPost.objects.filter(author=request.user).select_related(
            "author", "champions_meeting", "league_of_heroes"
        )
    ).order_by("-content_updated_at", "-id")
    return Response(serialize_posts(list(posts), request.user, full=True))


# ── Comments ────────────────────────────────────────────────────────────────

COMMENT_PAGE_SIZE = 50
# How old an account must be before it comments at the ordinary rate.
NEW_ACCOUNT_AGE = datetime.timedelta(days=1)


class GalleryCommentThrottle(GalleryThrottle):
    """Reading a thread counts as a read; writing counts as a comment, at a
    lower rate for accounts younger than a day. One class switching scope per
    request, because DRF throttles a view, not a method."""

    scope = "gallery_comment"

    def allow_request(self, request, view):
        if request.method == "GET":
            self.scope = "gallery_read"
        elif request.user.is_authenticated and (
            request.user.date_joined > timezone.now() - NEW_ACCOUNT_AGE
        ):
            self.scope = "gallery_comment_new"
        else:
            self.scope = "gallery_comment"
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class GalleryReportThrottle(GalleryThrottle):
    scope = "gallery_report"


def _serialize_comment(comment, state, post, viewer, blocks, votes=None):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """One comment. A deleted or removed one keeps its place and its id (so
    replies stay under it) but carries no text and no person. `post` is None
    for a guide's comment, which has no author to mark and no re-post to be
    "earlier" than. `votes` is gallery.comment_votes() for the page."""
    shown = state in ("visible", "under_review")
    tallies, mine = votes or ({}, {})
    ups, downs = tallies.get(comment.pk, (0, 0)) if shown else (0, 0)
    return {
        "id": comment.pk,
        "state": state,
        "body": comment.body if shown else "",
        "created_at": comment.created_at,
        "edited": shown and comment.edited_at is not None,
        # Written about the team before its author last re-posted it.
        "earlier_version": post is not None and comment.created_at < post.content_updated_at,
        "is_author": shown and post is not None and comment.author_id == post.author_id,
        "is_mine": viewer is not None and viewer.is_authenticated and comment.author_id == viewer.pk,
        "poster": blocks.get(comment.author_id) if shown else None,
        # Thumbs up and down: the score, the viewer's vote. Never who.
        **vote_fields(ups, downs, mine.get(comment.pk) if shown else None, viewer),
    }


def serialize_thread(post, viewer, entries):
    """gallery.thread() / guide_thread() entries on the wire, each with its
    replies. `post` is None for a guide's thread."""
    people = []
    ids = []
    for comment, _, replies in entries:
        people.append(comment.author)
        people.extend(reply.author for reply, _ in replies)
        ids.append(comment.pk)
        ids.extend(reply.pk for reply, _ in replies)
    blocks = public_identity.poster_blocks(people)
    votes = gallery.comment_votes(ids, viewer)
    return [
        {
            **_serialize_comment(comment, state, post, viewer, blocks, votes),
            "replies": [
                _serialize_comment(reply, reply_state, post, viewer, blocks, votes)
                for reply, reply_state in replies
            ],
        }
        for comment, state, replies in entries
    ]


def _thread_page(request, post, entries):
    """One page of a thread, the shape both kinds of thread answer with."""
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    start = (page - 1) * COMMENT_PAGE_SIZE
    window = entries[start:start + COMMENT_PAGE_SIZE]
    return Response({
        "count": len(entries),
        "page": page,
        "has_more": start + COMMENT_PAGE_SIZE < len(entries),
        "results": serialize_thread(post, request.user, window),
    })


def _new_comment(request, *, post=None, guide=None, section=""):
    """POST {"body", "parent"?}: the shared half of commenting on a post or a guide."""
    if not request.user.is_authenticated:
        return Response({"error": "Sign in to comment."}, status=status.HTTP_401_UNAUTHORIZED)
    body = request.data.get("body", "")
    if not isinstance(body, str):
        return Response({"error": "Write something first."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        comment = gallery.add_comment(
            request.user, post, body, request.data.get("parent"), guide=guide, section=section
        )
    except gallery.GalleryRefused as refusal:
        return refused(refusal)
    blocks = public_identity.poster_blocks([comment.author])
    return Response(
        {**_serialize_comment(comment, "visible", post, request.user, blocks), "replies": []},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryCommentThrottle])
def gallery_post_comments(request, public_id):
    """GET: the thread, a page of top comments at a time, oldest first.
    POST {"body", "parent"?}: comment, signed in."""
    post = _post_or_404(public_id, request.user)
    if post is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    if request.method == "POST":
        return _new_comment(request, post=post)
    return _thread_page(request, post, gallery.thread(post, request.user))


# A guide's comment sections: the whole guide ("", the side column), or a
# running style. Never "everyone", which is not a tab.
GUIDE_SECTIONS = frozenset({""} | {style.value for style in PvpStyle if style != PvpStyle.EVERYONE})


def _visible_guide(kind, event_id, viewer):
    """The event's guide if the viewer may read it (published, or a draft its
    editors preview), else None: the rule the guide's own route keeps."""
    field = EVENT_FIELD_FOR_KIND.get(kind)
    if field is None:
        return None
    guide = PvpGuide.objects.filter(**{field: event_id}).first()
    if guide is None or not (guide.is_published or pvp_guide_editing.can_edit(viewer, guide)):
        return None
    return guide


@api_view(["GET", "POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GalleryCommentThrottle])
def guide_comments(request, kind, event_id):
    """GET ?section=<style or blank>&page=N: one section of a PvP guide's
    comments, the same shape as a post's thread. POST {"body", "parent"?,
    "section"}: comment there, signed in (guide-comments-plan.md)."""
    guide = _visible_guide(kind, event_id, request.user)
    if guide is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    source = request.data if request.method == "POST" else request.query_params
    section = source.get("section", "") or ""
    if section not in GUIDE_SECTIONS:
        return Response({"error": "No such section."}, status=status.HTTP_400_BAD_REQUEST)
    if request.method == "POST":
        return _new_comment(request, guide=guide, section=section)
    return _thread_page(request, None, gallery.guide_thread(guide, section, request.user))


@api_view(["PUT", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryVoteThrottle])
def gallery_comment_vote(request, comment_id):
    """PUT {"value": "up"|"down"}: vote on a comment you can see. DELETE:
    take it back. Answers the new score and your vote, like a post's vote."""
    comment = _visible_comment(comment_id, request.user)
    if comment is None:
        return Response({"error": "Comment not found"}, status=status.HTTP_404_NOT_FOUND)
    value = vote_value(request)
    if value is None:
        return Response({"error": "A vote is up or down."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        if request.method == "PUT":
            gallery.vote_comment(request.user, comment, value)
        else:
            gallery.unvote_comment(request.user, comment)
    except gallery.GalleryRefused as refusal:
        return refused(refusal)
    ups, downs = gallery.tally(comment.votes)
    return Response(vote_fields(ups, downs, value if request.method == "PUT" else None, request.user))


@api_view(["PATCH", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryCommentThrottle])
def gallery_comment_detail(request, comment_id):
    """PATCH {"body"}: edit your comment. DELETE: delete it. Somebody else's
    comment reads as missing."""
    try:
        comment = gallery.get_own_comment(request.user, comment_id)
    except GalleryComment.DoesNotExist:
        return Response({"error": "Comment not found"}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "DELETE":
        gallery.delete_comment(comment)
        return Response(status=status.HTTP_204_NO_CONTENT)
    body = request.data.get("body", "")
    if not isinstance(body, str):
        return Response({"error": "Write something first."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        gallery.edit_comment(comment, body)
    except gallery.GalleryRefused as refusal:
        return refused(refusal)
    blocks = public_identity.poster_blocks([comment.author])
    state = "under_review" if comment.is_hidden else "visible"
    votes = gallery.comment_votes([comment.pk], request.user)
    return Response(
        {**_serialize_comment(comment, state, comment.post, request.user, blocks, votes), "replies": []}
    )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([GalleryReportThrottle])
def gallery_report(request):
    """POST {"post": public_id} or {"comment": id}, with "reason" and an
    optional "note". 201 with no body: the reporter learns nothing about what
    happens next, which is the point."""
    reason = request.data.get("reason", "")
    note = request.data.get("note", "")
    if not isinstance(note, str) or not isinstance(reason, str):
        return Response({"error": "Pick a reason."}, status=status.HTTP_400_BAD_REQUEST)
    target = {}
    if request.data.get("post"):
        post = _post_or_404(str(request.data["post"]), request.user)
        if post is None:
            return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        target["post"] = post
    elif request.data.get("comment"):
        comment = _visible_comment(request.data["comment"], request.user)
        if comment is None:
            return Response({"error": "Comment not found"}, status=status.HTTP_404_NOT_FOUND)
        target["comment"] = comment
    else:
        return Response({"error": "Say what you're reporting."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        gallery.report(request.user, reason=reason, note=note, **target)
    except gallery.GalleryRefused as refusal:
        return refused(refusal)
    return Response(status=status.HTTP_201_CREATED)


def _visible_comment(comment_id, viewer):
    """A comment the viewer can see (and so can report), or None."""
    try:
        comment = GalleryComment.objects.select_related("post", "author", "guide").get(
            pk=int(comment_id), is_hidden=False, is_deleted=False
        )
    except (TypeError, ValueError, GalleryComment.DoesNotExist):
        return None
    if comment.author is not None and comment.author.publishing_blocked:
        return None
    if comment.guide_id:
        guide = comment.guide
        visible = guide.is_published or pvp_guide_editing.can_edit(viewer, guide)
        return comment if visible else None
    return comment if _post_or_404(comment.post.public_id, viewer) else None
