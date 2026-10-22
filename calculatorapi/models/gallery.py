"""
The gallery: posts other players can see, upvote, comment on, copy and report.

Today the one kind of post is a PvP team (the PvP page's Gallery view). The
community pull-plan gallery planned after it is meant to be a second KIND of
the same post, so everything that is not about the team itself is generic:
votes, comments, reports, views and copies all hang off GalleryPost by a real
foreign key.

WHY ONE GENERIC PARENT
----------------------
The alternatives were a GenericForeignKey per child table, which gives up FK
integrity and cascades (a vote could point at a post that no longer exists),
or a second copy of the vote, comment and report tables per gallery. One
parent with a `kind` keeps every cascade and constraint in the database. The
columns only one kind uses are nullable and tied to that kind by a check
constraint, the same XOR idiom PvpTeam and PvpGuide use for their event.

A POST IS A FROZEN COPY
-----------------------
`team_snapshot` is copied from the player's team when they post, and editing
the team afterwards never changes it. With votes on a post, a live team could
be gamed: collect votes on a strong team, then swap the cards, and the votes
now vouch for something else. Posting again for the same event replaces the
copy and clears the votes (gallery.py does it), keeping the post's link and
its comments, which then read "On an earlier version" by `content_updated_at`.

PRIVATE, AND OFF THE CACHED ROUTE
---------------------------------
Every model here is user data (content_snapshot.PRIVATE_MODELS) and is listed
in public_payload_cache._IRRELEVANT_MODELS, so a vote or a comment never
rebuilds /calculator-data. What a post shows about its author goes through
public_identity.poster_block and nothing else.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone

from calculatorapi.public_ids import PUBLIC_ID_LENGTH, generate_public_id
# The same folding the matcher applies, so a stored term and the text it
# is checked against are normalised identically. -> text_folding.py
from calculatorapi.text_folding import normalise

from .champions_meeting import ChampionsMeeting
from .custom_user import CustomUser
from .league_of_heroes import LeagueOfHeroes
from .pvp_team import PvpTeam

# The one line of public text a post carries. The team's own name stays
# private: a post never shows it.
GALLERY_CAPTION_MAX_LENGTH = 140
# The longer text only the open post shows: why the
# team is built the way it is, or whatever its author wants to say. Never in
# the feed's rows. About 350 words.
GALLERY_STRATEGY_MAX_LENGTH = 2000
# Feedback on a team, not an essay. Also the most a spammer gets per comment.
GALLERY_COMMENT_MAX_LENGTH = 500
GALLERY_REPORT_NOTE_MAX_LENGTH = 200


class GalleryPostKind(models.TextChoices):
    PVP_TEAM = "pvp_team", "PvP team"


class GalleryHiddenReason(models.TextChoices):
    """Why a post or comment is hidden. Blank while it is visible."""

    # Enough people reported it (gallery_moderation.AUTO_HIDE_REPORTS) and it
    # waits for a moderator. Reversible: "Keep" in the admin restores it.
    REPORTS = "reports", "Hidden by reports, waiting for review"
    # A moderator removed it.
    MODERATOR = "moderator", "Removed by a moderator"


class GalleryReportReason(models.TextChoices):
    SPAM = "spam", "Spam or advertising"
    HARASSMENT = "harassment", "Harassment or hate"
    SEXUAL_OR_VIOLENT = "sexual_or_violent", "Sexual or violent content"
    PERSONAL_INFO = "personal_info", "Someone's personal information"
    OTHER = "other", "Something else"


class GalleryReportResolution(models.TextChoices):
    KEPT = "kept", "Kept"
    REMOVED = "removed", "Removed"
    # Removed AND its author blocked. Its own value (since 2026-10-21) so an
    # undo knows to lift the block too; older blocks are recorded as REMOVED.
    BLOCKED = "blocked", "Removed, author blocked"


class BlockedTermMatch(models.TextChoices):
    # Whole words only, so a short term does not catch longer innocent words.
    WORD = "word", "Whole word"
    ANYWHERE = "anywhere", "Anywhere, even inside a word"


def _hidden_is_explained():
    """A hidden row says why; a visible one carries no reason."""
    return Q(is_hidden=False, hidden_reason="") | (Q(is_hidden=True) & ~Q(hidden_reason=""))


class GalleryPost(models.Model):
    """One post in a gallery. For a PvP team, a frozen copy of the team."""

    # The post's permanent link: /post/<public_id>. Kept through re-posts, so
    # a link pasted in Discord keeps working when its author updates the team.
    # gallery.generate_unique_post_id() passes one explicitly; the default is
    # the backstop, as on Plan.public_id.
    public_id = models.CharField(
        max_length=PUBLIC_ID_LENGTH,
        default=generate_public_id,
        unique=True,
        editable=False,
    )
    author = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="gallery_posts"
    )
    kind = models.CharField(max_length=20, choices=GalleryPostKind.choices)
    caption = models.CharField(
        max_length=GALLERY_CAPTION_MAX_LENGTH, blank=True, default=""
    )
    # Plain text, line breaks kept, never turned into links (the filters
    # refuse links anyway). Only in the full post payload (serialize_posts).
    # Not "notes": that is a PvpTeam's PRIVATE sticky notes, which the API
    # tests forbid anywhere in a gallery payload.
    strategy = models.CharField(
        max_length=GALLERY_STRATEGY_MAX_LENGTH, blank=True, default=""
    )
    # Defaults rather than auto_now_add so the demo data (seed_gallery_demo)
    # can spread posts over past days; every real write sets them explicitly.
    published_at = models.DateTimeField(default=timezone.now)
    # Moves on every re-post. A comment older than this was written about an
    # earlier version of the team.
    content_updated_at = models.DateTimeField(default=timezone.now)

    is_hidden = models.BooleanField(default=False)
    hidden_reason = models.CharField(
        max_length=20, choices=GalleryHiddenReason.choices, blank=True, default=""
    )
    hidden_at = models.DateTimeField(null=True, blank=True)

    # Engagement counters, bumped with F() + 1 so two requests cannot lose one
    # another's increment. Views are deduplicated in the cache, never by a
    # table of who viewed what (gallery.record_view).
    view_count = models.PositiveIntegerField(default=0)
    copy_count = models.PositiveIntegerField(default=0)

    # ── PvP team posts ──────────────────────────────────────────────────
    champions_meeting = models.ForeignKey(
        ChampionsMeeting,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gallery_posts",
    )
    league_of_heroes = models.ForeignKey(
        LeagueOfHeroes,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gallery_posts",
    )
    # [{"position", "uma", "style", "cards": [{"slot", "support_card"}]}],
    # positions 0..2. JSON rather than child rows for the PvpTeam.notes
    # reason: always read and written whole, never queried by column. Checked
    # on write by gallery.py (three umas, eighteen cards, no repeated
    # character); an id that later leaves the catalogue is dropped on read.
    team_snapshot = models.JSONField(default=list, blank=True)
    # A short hash of the three umas and their styles. Part of the preview
    # image's URL, so a re-post that changes an uma gets a new URL and Discord
    # and X fetch the new picture; a re-post that only changes cards keeps it.
    preview_version = models.CharField(max_length=12, blank=True, default="")
    # The team it was copied from, so My team can say which team is posted.
    # Never serialized. SET_NULL: deleting the team leaves the post up.
    source_team = models.ForeignKey(
        PvpTeam,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gallery_posts",
    )

    class Meta:
        ordering = ("-published_at", "-id")
        constraints = [
            # A PvP post has exactly one event; any other kind has none.
            models.CheckConstraint(
                condition=(
                    Q(kind=GalleryPostKind.PVP_TEAM)
                    & (
                        Q(champions_meeting__isnull=False, league_of_heroes__isnull=True)
                        | Q(champions_meeting__isnull=True, league_of_heroes__isnull=False)
                    )
                )
                | (
                    ~Q(kind=GalleryPostKind.PVP_TEAM)
                    & Q(champions_meeting__isnull=True, league_of_heroes__isnull=True)
                ),
                name="gallery_post_event_matches_kind",
            ),
            # One post per person per event, enforced here and not only in
            # the view, so two quick clicks cannot make two.
            models.UniqueConstraint(
                fields=["author", "champions_meeting"],
                condition=Q(champions_meeting__isnull=False),
                name="one_gallery_post_per_author_cm",
            ),
            models.UniqueConstraint(
                fields=["author", "league_of_heroes"],
                condition=Q(league_of_heroes__isnull=False),
                name="one_gallery_post_per_author_loh",
            ),
            models.CheckConstraint(
                condition=_hidden_is_explained(),
                name="gallery_post_hidden_has_reason",
            ),
        ]

    def __str__(self):
        return f"{self.public_id} by {self.author.username}"

    @property
    def event(self):
        return self.champions_meeting or self.league_of_heroes

    @property
    def event_kind(self):
        """"cm" or "loh", the tag the PvP URLs use. None for other kinds."""
        if self.champions_meeting_id:
            return "cm"
        if self.league_of_heroes_id:
            return "loh"
        return None


class GalleryVote(models.Model):
    """One account's upvote on one post.

    No weight column: how much a vote counts comes from the voter's pledge at
    the time the gallery is ranked (gallery_ranking.py), never from the time
    they voted. A stored weight would keep counting extra after a lapse.
    """

    post = models.ForeignKey(GalleryPost, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="gallery_votes"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "user"], name="one_gallery_vote_per_user"),
        ]

    def __str__(self):
        return f"{self.user.username} on {self.post.public_id}"


class GalleryComment(models.Model):
    """A comment on a post, or a reply to one. One level of replies: a reply
    to a reply is attached to its top comment by gallery.add_comment, so
    `parent.parent` is always None.

    TWO KINDS OF PARENT (owner, 2026-10-22: guide-comments-plan.md, option A).
    A comment hangs off a gallery post OR off a PvP guide, never both (a check
    constraint), and a guide comment names the guide's SECTION it was left in:
    blank for the whole guide (the side column), else a running style. The
    official guide is not a player's post, so it gets no host post of its own;
    instead every path that reads a comment's parent asks the comment
    (`link_path`, `where`), and reports, moderation cards, blocked terms,
    throttles and erase_users work unchanged.
    """

    post = models.ForeignKey(
        GalleryPost, on_delete=models.CASCADE, related_name="comments", null=True, blank=True
    )
    guide = models.ForeignKey(
        "calculatorapi.PvpGuide", on_delete=models.CASCADE, related_name="comments", null=True, blank=True
    )
    # A guide comment's section: "" for the whole guide, else a running style
    # ("front", "pace"...). Always "" on a post's comment.
    section = models.CharField(max_length=20, blank=True, default="")
    # SET_NULL, not CASCADE: when an account goes, a comment others replied
    # to stays as "[deleted]" so the thread keeps its shape (gallery.erase_user).
    author = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gallery_comments",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )
    # Blank only once deleted. A CharField rather than a TextField so the
    # length limit is a real column limit on PostgreSQL too.
    body = models.CharField(max_length=GALLERY_COMMENT_MAX_LENGTH, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    edited_at = models.DateTimeField(null=True, blank=True)

    is_hidden = models.BooleanField(default=False)
    hidden_reason = models.CharField(
        max_length=20, choices=GalleryHiddenReason.choices, blank=True, default=""
    )
    hidden_at = models.DateTimeField(null=True, blank=True)
    # Deleted by its author, or its author's account was deleted, while it has
    # replies: the body is blanked and it shows "[deleted]". A comment with no
    # replies is removed outright instead.
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=_hidden_is_explained(),
                name="gallery_comment_hidden_has_reason",
            ),
            models.CheckConstraint(
                condition=(
                    Q(post__isnull=False, guide__isnull=True, section="")
                    | Q(post__isnull=True, guide__isnull=False)
                ),
                name="gallery_comment_one_parent",
            ),
        ]
        indexes = [models.Index(fields=["guide", "section", "created_at"], name="guide_comment_thread")]

    def __str__(self):
        who = self.author.username if self.author else "[deleted]"
        return f"{who} on {self.where()}"

    def where(self):
        """What the comment is on, for staff: "post abc123" or "the CM 21
        guide, Front"."""
        if self.post_id:
            return f"post {self.post.public_id}"
        guide = self.guide
        event = guide.champions_meeting or guide.league_of_heroes
        section = self.section.capitalize() if self.section else "Overview"
        return f"the {event} guide, {section}"

    def link_path(self):
        """The site path that shows the comment's thread: a post's share link,
        or the guide's page at its section."""
        if self.post_id:
            return f"/post/{self.post.public_id}"
        guide = self.guide
        kind = "cm" if guide.champions_meeting_id else "loh"
        event_id = guide.champions_meeting_id or guide.league_of_heroes_id
        style = f"&style={self.section}" if self.section else ""
        return f"/app/pvp?event={kind}-{event_id}{style}"


class GalleryCommentVote(models.Model):
    """One account's thumbs-up on one comment (Henry, board 2026-10-20).

    A count, nothing more: no weight, no thumbs-down, never on your own
    comment (gallery.vote_comment). The count is public; who voted is never
    sent anywhere, not even to the comment's author.
    """

    comment = models.ForeignKey(GalleryComment, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="gallery_comment_votes")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["comment", "user"], name="one_comment_vote_per_user"),
        ]

    def __str__(self):
        return f"{self.user.username} on comment {self.comment_id}"


class GalleryReport(models.Model):
    """Somebody flagging a post or a comment for a moderator.

    Exactly one of `post` / `comment` is set. The reporter is shown to staff in
    the admin and, while the report is open, to moderators on the item's card in
    Discord's private #calc-mod-queue (display name and handle only;
    moderation_cards.py). Never to the person reported, never in public. Cleared
    rather than deleted with their account, so the moderation record survives.
    """

    reporter = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gallery_reports",
    )
    post = models.ForeignKey(
        GalleryPost,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports",
    )
    comment = models.ForeignKey(
        GalleryComment,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports",
    )
    reason = models.CharField(max_length=20, choices=GalleryReportReason.choices)
    note = models.CharField(
        max_length=GALLERY_REPORT_NOTE_MAX_LENGTH, blank=True, default=""
    )
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Who decided, when it was a moderator clicking a card's button in Discord:
    # their Discord username and user id, "name (id)". A Discord moderator has
    # no account here and never gets one (linking is not sign-in), so
    # `resolved_by` stays empty for those decisions and this says who it was.
    # Blank for every decision made in the admin.
    resolved_by_discord = models.CharField(max_length=100, blank=True, default="")
    resolution = models.CharField(
        max_length=10, choices=GalleryReportResolution.choices, blank=True, default=""
    )

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(post__isnull=False, comment__isnull=True)
                    | Q(post__isnull=True, comment__isnull=False)
                ),
                name="gallery_report_one_target",
            ),
            # One report per person per item. Partial on the reporter too, so
            # reports left by deleted accounts (reporter cleared) never collide.
            models.UniqueConstraint(
                fields=["reporter", "post"],
                condition=Q(post__isnull=False, reporter__isnull=False),
                name="one_gallery_report_per_user_post",
            ),
            models.UniqueConstraint(
                fields=["reporter", "comment"],
                condition=Q(comment__isnull=False, reporter__isnull=False),
                name="one_gallery_report_per_user_comment",
            ),
        ]

    def __str__(self):
        target = f"post {self.post.public_id}" if self.post_id else f"comment {self.comment_id}"
        return f"{self.get_reason_display()} on {target}"

    @property
    def is_open(self):
        return self.resolved_at is None


class BlockedTerm(models.Model):
    """A word or phrase the gallery refuses in captions, comments and
    display names (gallery_moderation.check_text). Edited by staff in the
    admin. Stored lowercased; matching normalises the text the same way."""

    term = models.CharField(max_length=60, unique=True)
    match = models.CharField(
        max_length=10, choices=BlockedTermMatch.choices, default=BlockedTermMatch.WORD
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("term",)
        verbose_name = "blocked term"
        verbose_name_plural = "blocked terms"

    def __str__(self):
        return self.term

    def save(self, *args, **kwargs):
        self.term = normalise(self.term).strip()
        super().save(*args, **kwargs)
