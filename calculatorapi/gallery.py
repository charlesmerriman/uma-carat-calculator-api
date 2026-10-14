"""
The operations on gallery posts. Every gallery view goes through these.

The plans.py / pvp_teams.py split, for the same reasons: the rules (who may
post, what a post must hold, one post per event, who may vote, how a view or
a copy is counted) live here once, so the views, the demo data and the
moderation admin cannot each re-derive them.

What a post is: models/gallery.py. How a gallery is ordered:
gallery_ranking.py. What is shown about a poster: public_identity.py. The
text filters: gallery_moderation.py.
"""

import datetime
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from calculatorapi import gallery_moderation, visits
from calculatorapi.models import (
    GalleryComment,
    GalleryPost,
    GalleryPostKind,
    GalleryReport,
    GalleryReportReason,
    GalleryVote,
    GALLERY_CAPTION_MAX_LENGTH,
    GALLERY_COMMENT_MAX_LENGTH,
    GALLERY_REPORT_NOTE_MAX_LENGTH,
    PVP_DECK_SIZE,
    PVP_TEAM_SIZE,
    SupportCard,
    Uma,
)
from calculatorapi.public_ids import generate_public_id

# How long one viewer's view of one post is remembered, so opening it again
# the same day is not a second view.
VIEW_WINDOW_SECONDS = 24 * 60 * 60


class GalleryRefused(Exception):
    """An action the gallery's rules refuse. `message` is shown to the person
    as is (written as copy, .claude/rules/copy.md); `status` is the HTTP code
    the view answers with: 400 for something they can fix, 403 for something
    they may not do."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── Reading ─────────────────────────────────────────────────────────────────


def visible_posts():
    """Posts anyone may see: not hidden, and not by a blocked account.

    THE read filter. `publishing_blocked` hides through here rather than by
    changing the posts, which is what lets unblocking restore them.
    """
    return GalleryPost.objects.filter(is_hidden=False, author__publishing_blocked=False)


def get_post(public_id, viewer=None):
    """The post with this public id IF `viewer` may see it, else
    GalleryPost.DoesNotExist. Its author always may, so the account page can
    show a hidden post as "Hidden while we review it"."""
    post = GalleryPost.objects.select_related("author").get(public_id=public_id)
    hidden = post.is_hidden or post.author.publishing_blocked
    if hidden and not (viewer and viewer.is_authenticated and viewer.pk == post.author_id):
        raise GalleryPost.DoesNotExist
    return post


def generate_unique_post_id():
    """A public id no post holds yet. plans.generate_unique_public_id's
    reasoning: the field default cannot see the table, this loop can, and the
    unique constraint is the backstop for a same-instant race."""
    public_id = generate_public_id()
    while GalleryPost.objects.filter(public_id=public_id).exists():
        public_id = generate_public_id()
    return public_id


# ── Posting a PvP team ──────────────────────────────────────────────────────


def snapshot_from_team(team):
    """The frozen copy a post holds, or GalleryRefused when the team is not
    finished: three umas each with a style, all eighteen card slots filled,
    and no two outfits of one character (the game's CM rule).

    The character rule is also enforced on every team save (pvp_teams.py), so
    a draft cannot hold a team that breaks it; it is checked again here
    because a post must not depend on how the team got into the database.
    """
    members = list(team.members.select_related("uma").prefetch_related("cards"))
    positions = sorted(member.position for member in members)
    if positions != list(range(PVP_TEAM_SIZE)):
        raise GalleryRefused("A team needs three umas before it can be posted.")
    for member in members:
        slots = sorted(card.slot for card in member.cards.all())
        if slots != list(range(1, PVP_DECK_SIZE + 1)):
            raise GalleryRefused("Fill all six card slots for every uma before posting.")
    characters = [member.uma.character_id for member in members]
    known = [character for character in characters if character is not None]
    if len(set(known)) != len(known):
        raise GalleryRefused("A team can't have two outfits of the same uma.")
    return [
        {
            "position": member.position,
            "uma": member.uma_id,
            "style": member.style,
            "cards": [
                {"slot": card.slot, "support_card": card.support_card_id}
                for card in sorted(member.cards.all(), key=lambda card: card.slot)
            ],
        }
        for member in sorted(members, key=lambda member: member.position)
    ]


def preview_version_for(snapshot):
    """A short hash of what the preview image draws: each uma and its style,
    in position order. Cards are not in the picture, so a re-post that only
    changes cards keeps the version, and the image URL with it."""
    drawn = "|".join(f"{member['uma']}:{member['style']}" for member in snapshot)
    return hashlib.sha1(drawn.encode("utf-8")).hexdigest()[:12]


def _clean_caption(caption):
    caption = (caption or "").strip()
    if len(caption) > GALLERY_CAPTION_MAX_LENGTH:
        raise GalleryRefused(
            f"Keep the caption to {GALLERY_CAPTION_MAX_LENGTH} characters."
        )
    try:
        gallery_moderation.check_text(caption)
    except gallery_moderation.TextRefused as refusal:
        raise GalleryRefused(str(refusal)) from refusal
    return caption


def check_may_publish(user):
    """GalleryRefused unless `user` may post or comment at all."""
    if user.publishing_blocked:
        raise GalleryRefused("Your account can't post in the gallery.", status=403)
    if not user.display_name:
        raise GalleryRefused("Set a display name first.")


def publish_team(user, team, *, caption="", confirm_public_identity=False):
    """Post `team` to its event's gallery, or replace the post already there.

    Returns (post, created). A replacement keeps the post's public id, so its
    link keeps working, and its comments, which read "On an earlier version"
    from then on; it CLEARS the votes, because they were for a different
    team. `confirm_public_identity` must be literally True: it is the
    person's agreement, in the dialog, to show their name, picture and tier.

    A hidden post stays hidden when replaced: re-posting is not a way out of
    moderation.
    """
    if confirm_public_identity is not True:
        raise GalleryRefused("Tick the box to agree to what will show with your team.")
    check_may_publish(user)
    if team.user_id != user.pk:
        # The view resolves the team through pvp_teams.get_owned_team; this is
        # the backstop for any other caller.
        raise GalleryRefused("That team isn't yours.", status=403)
    caption = _clean_caption(caption)
    snapshot = snapshot_from_team(team)
    event = (
        {"champions_meeting_id": team.champions_meeting_id}
        if team.champions_meeting_id
        else {"league_of_heroes_id": team.league_of_heroes_id}
    )
    now = timezone.now()
    fields = {
        "caption": caption,
        "team_snapshot": snapshot,
        "preview_version": preview_version_for(snapshot),
        "source_team": team,
        "content_updated_at": now,
    }
    with transaction.atomic():
        post = (
            GalleryPost.objects.select_for_update()
            .filter(author=user, **event)
            .first()
        )
        if post is None:
            post = GalleryPost.objects.create(
                public_id=generate_unique_post_id(),
                author=user,
                kind=GalleryPostKind.PVP_TEAM,
                published_at=now,
                **event,
                **fields,
            )
            return post, True
        for name, value in fields.items():
            setattr(post, name, value)
        post.save(update_fields=list(fields))
        post.votes.all().delete()
    return post, False


def take_down(post):
    """Remove a post with its votes, comments and reports (all CASCADE)."""
    post.delete()


def posted_team_ids(user):
    """{team id: post public id} for `user`'s teams that are posted, so My
    team can say which team is in the gallery."""
    return dict(
        GalleryPost.objects.filter(author=user, source_team__isnull=False)
        .values_list("source_team_id", "public_id")
    )


def snapshot_for_display(snapshot):
    """A stored snapshot with anything that has left the catalogue dropped.

    An uma or card deleted (or merged away) after posting would otherwise send
    the client an id it cannot draw. Two queries for the whole snapshot.
    """
    uma_ids = {member["uma"] for member in snapshot}
    card_ids = {card["support_card"] for member in snapshot for card in member["cards"]}
    umas = set(Uma.objects.filter(id__in=uma_ids).values_list("id", flat=True))
    cards = set(SupportCard.objects.filter(id__in=card_ids).values_list("id", flat=True))
    return [
        {
            **member,
            "cards": [card for card in member["cards"] if card["support_card"] in cards],
        }
        for member in snapshot
        if member["uma"] in umas
    ]


# ── Votes ───────────────────────────────────────────────────────────────────


def upvote_count(post):
    """The number shown on a post: people who upvoted, blocked accounts not
    counted. Not the weighted points, which never leave the server."""
    return post.votes.filter(user__publishing_blocked=False).count()


def vote(user, post):
    """Upvote `post` as `user`. Idempotent: a second call changes nothing."""
    if post.author_id == user.pk:
        raise GalleryRefused("You can't upvote your own team.", status=403)
    if user.publishing_blocked:
        raise GalleryRefused("Your account can't vote in the gallery.", status=403)
    GalleryVote.objects.get_or_create(post=post, user=user)


def unvote(user, post):
    """Take `user`'s upvote off `post`. Idempotent."""
    GalleryVote.objects.filter(post=post, user=user).delete()


# ── Views and copies ────────────────────────────────────────────────────────


def _viewer_key(request, post):
    """Who is viewing, as a cache key that is useless outside this process.

    The account id when signed in. Otherwise a digest of the IP address and
    user agent salted with the secret key and TODAY, the recipe of
    visits._visitor_hash with a day in place of the month: enough to tell
    one visitor from another for a day, and unlinkable across days.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        who = f"u{user.pk}"
    else:
        ip = visits._client_ip(request)  # pylint: disable=protected-access
        agent = request.META.get("HTTP_USER_AGENT", "")
        raw = f"{settings.SECRET_KEY}:{timezone.now().date().isoformat()}:{ip}:{agent}"
        who = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"gallery-view:{post.pk}:{who}"


def record_view(request, post):
    """Count one view of `post`, at most once per viewer per day. Returns
    whether it counted.

    NOTHING ABOUT THE VIEWER REACHES THE DATABASE. The "already counted" mark
    is a cache key that expires in a day, and only the post's counter is
    written. The cost of that: a restart empties the cache, so somebody who
    opens a post again after a deploy is counted twice that day. A view count
    is approximate social proof; a table of who viewed what would be a privacy
    cost for no real gain.

    Not counted: the author's own views, and user agents that say they are
    bots (visits._BOT_UA), which is what keeps Discord's and X's crawlers of
    the share page from inflating the number.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.pk == post.author_id:
        return False
    if visits._BOT_UA.search(request.META.get("HTTP_USER_AGENT", "")):  # pylint: disable=protected-access
        return False
    if not cache.add(_viewer_key(request, post), 1, VIEW_WINDOW_SECONDS):
        return False
    GalleryPost.objects.filter(pk=post.pk).update(view_count=F("view_count") + 1)
    return True


def copy_snapshot(request, post):
    """The team a "Copy to my teams" adds, counting the copy unless it is the
    author's own. The client adds the team through its ordinary path
    (POST /pvp-teams, or a guest's device store), so the rules every path into
    a team applies, the card release rule included, apply here too."""
    user = getattr(request, "user", None)
    if not (user is not None and user.is_authenticated and user.pk == post.author_id):
        GalleryPost.objects.filter(pk=post.pk).update(copy_count=F("copy_count") + 1)
    return snapshot_for_display(post.team_snapshot)


# ── Comments ────────────────────────────────────────────────────────────────

# The same text from the same person within this window is refused: the cheap
# half of spam is pasting one line under every post.
REPEAT_WINDOW = datetime.timedelta(hours=24)


def _clean_comment(body):
    body = (body or "").strip()
    if not body:
        raise GalleryRefused("Write something first.")
    if len(body) > GALLERY_COMMENT_MAX_LENGTH:
        raise GalleryRefused(f"Keep comments to {GALLERY_COMMENT_MAX_LENGTH} characters.")
    try:
        gallery_moderation.check_text(body)
    except gallery_moderation.TextRefused as refusal:
        raise GalleryRefused(str(refusal)) from refusal
    return body


def add_comment(user, post, body, parent_id=None):
    """A new comment on `post`, or a reply. Returns the comment.

    One level of replies: a reply to a reply is attached to the top comment it
    sits under, so the thread never nests deeper than the page draws it.
    Commenting needs what posting needs (a display name, not blocked): the
    comment shows the same public block.
    """
    check_may_publish(user)
    body = _clean_comment(body)
    parent = None
    if parent_id is not None:
        try:
            parent = post.comments.get(pk=int(parent_id), is_hidden=False)
        except (TypeError, ValueError, GalleryComment.DoesNotExist) as error:
            raise GalleryRefused("That comment isn't there any more.") from error
        parent = parent.parent or parent
    since = timezone.now() - REPEAT_WINDOW
    if GalleryComment.objects.filter(author=user, body=body, created_at__gte=since).exists():
        raise GalleryRefused("You've already posted that comment.")
    return GalleryComment.objects.create(post=post, author=user, parent=parent, body=body)


def get_own_comment(user, comment_id):
    """The comment with this id IF `user` wrote it and it is not deleted, else
    GalleryComment.DoesNotExist. Somebody else's comment reads as missing."""
    try:
        return GalleryComment.objects.select_related("post").get(
            pk=int(comment_id), author=user, is_deleted=False
        )
    except (TypeError, ValueError) as error:
        raise GalleryComment.DoesNotExist from error


def edit_comment(comment, body):
    """Replace a comment's text. It runs the same filters as a new one and is
    marked edited, so a reply that answered the old text still makes sense."""
    comment.body = _clean_comment(body)
    comment.edited_at = timezone.now()
    comment.save(update_fields=["body", "edited_at"])
    return comment


def delete_comment(comment):
    """Remove a comment. One with replies stays as "[deleted]", body and author
    cleared, so the replies under it keep their place."""
    if comment.parent_id is None and comment.replies.exists():
        comment.body = ""
        comment.author = None
        comment.is_deleted = True
        comment.save(update_fields=["body", "author", "is_deleted"])
    else:
        comment.delete()


def thread(post, viewer=None):
    """The post's comments a viewer may see: top comments oldest first, each
    with its replies. A list of (comment, state, [(reply, state), ...]).

    state is "visible", "deleted" ("[deleted]"), "removed" ("[removed]", hidden
    by moderation or by its author's block) or "under_review" (the viewer's
    OWN hidden comment, so they know why nobody else sees it). A removed top
    comment with no visible replies is left out: the placeholder only exists
    to keep a conversation's shape.
    """
    viewer_id = viewer.pk if viewer is not None and viewer.is_authenticated else None
    comments = list(post.comments.select_related("author"))

    def state(comment):
        if comment.is_deleted:
            return "deleted"
        hidden = comment.is_hidden or (comment.author and comment.author.publishing_blocked)
        if not hidden:
            return "visible"
        return "under_review" if comment.author_id == viewer_id else "removed"

    replies = {}
    for comment in comments:
        if comment.parent_id:
            replies.setdefault(comment.parent_id, []).append((comment, state(comment)))
    result = []
    for comment in comments:
        if comment.parent_id:
            continue
        under = [(reply, kind) for reply, kind in replies.get(comment.pk, []) if kind != "removed"]
        kind = state(comment)
        if kind in ("removed", "deleted") and not under:
            continue
        result.append((comment, kind, under))
    return result


# ── Reports ─────────────────────────────────────────────────────────────────


def report(user, *, reason, post=None, comment=None, note=""):
    """File a report on a post or a comment. Returns the report.

    Raises GalleryRefused for your own item (400), when blocked (403) and on a
    repeat (409). Then gallery_moderation decides whether the item hides and
    whether a moderator is pinged.
    """
    if user.publishing_blocked:
        raise GalleryRefused("Your account can't report in the gallery.", status=403)
    target = post or comment
    if target.author_id == user.pk:
        raise GalleryRefused("You can't report your own post or comment.")
    if reason not in GalleryReportReason.values:
        raise GalleryRefused("Pick a reason.")
    note = (note or "").strip()[:GALLERY_REPORT_NOTE_MAX_LENGTH]
    try:
        with transaction.atomic():
            filed = GalleryReport.objects.create(
                reporter=user, post=post, comment=comment, reason=reason, note=note
            )
    except IntegrityError as error:
        raise GalleryRefused("You've already reported this.", status=409) from error
    gallery_moderation.after_report(filed)
    return filed


# ── Erasing a person ────────────────────────────────────────────────────────


def erase_users(users):
    """Remove what these accounts put in the gallery. Called by
    DELETE /account (before the account row goes) and purge_user_pii.

      * their posts, with every vote, comment and report on them;
      * their votes;
      * their comments: removed when nobody replied, otherwise "[deleted]"
        with the author and the text cleared, so replies keep their place;
      * their reports stay, with the reporter cleared: the moderation record.

    ONE function for both paths, so an account deletion and a purge cannot
    drift into erasing different things. Takes a queryset or list of users.
    """
    ids = [user.pk for user in users]
    GalleryPost.objects.filter(author_id__in=ids).delete()
    GalleryVote.objects.filter(user_id__in=ids).delete()
    comments = GalleryComment.objects.filter(author_id__in=ids)
    # Replies first: a reply has no replies of its own, so it always goes.
    comments.filter(parent__isnull=False).delete()
    comments.filter(replies__isnull=True).delete()
    comments.update(body="", author=None, is_deleted=True, edited_at=None)
    GalleryReport.objects.filter(reporter_id__in=ids).update(reporter=None)
