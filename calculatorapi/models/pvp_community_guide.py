"""
A player's own PvP guide, the working copy (notes/community-guides-plan.md).

WHY A DOCUMENT AND NOT THE GUIDE TABLES
---------------------------------------
The site's own guides live in nine tables (models/pvp_guide.py) that are
CONTENT: they travel to every developer's machine through pull_prod_content,
and Rankings, link previews and the Timeline's guide star read them. A
player's guide is that player's own data, like a team or a plan. So it is
kept whole, as the same JSON document the site's editor reads and writes
(pvp_guide_document.py), in a table of its own that is PRIVATE. Nothing that
reads the site's guides can see one, so "count only the official ones" holds
with no filter to forget.

THE WORKING COPY AND THE POST
-----------------------------
This row is what the writer's editor autosaves: private, never filtered,
never shown to anyone else. Posting freezes a copy onto a GalleryPost
(`guide_snapshot`, kind "pvp_guide"), which is what readers see, vote on and
comment on. Editing afterwards changes nothing anyone voted on until the
writer posts again, the gallery's rule for teams and plans.

A person may hold up to guide_gallery.COMMUNITY_GUIDE_CAP working copies for
one event (the owner, 2026-11-27), each with at most one post. The cap is a
count the service checks under a row lock, not a constraint.
"""

from django.db import models
from django.db.models import Q

from .champions_meeting import ChampionsMeeting
from .custom_user import CustomUser
from .league_of_heroes import LeagueOfHeroes

# The guide's title: the working copy's name in "My guides" and the post's
# title once posted. The gallery's caption limit, since it becomes one.
COMMUNITY_GUIDE_TITLE_MAX_LENGTH = 140


class PvpCommunityGuide(models.Model):
    """One player's guide for one event, as they are writing it."""

    author = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="community_guides"
    )
    champions_meeting = models.ForeignKey(
        ChampionsMeeting,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="community_guides",
    )
    league_of_heroes = models.ForeignKey(
        LeagueOfHeroes,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="community_guides",
    )
    title = models.CharField(max_length=COMMUNITY_GUIDE_TITLE_MAX_LENGTH, blank=True, default="")
    # The guide as pvp_guide_document's shape, with catalogue ids. Checked on
    # every save by guide_gallery (shape, catalogue, caps), never filtered:
    # the filters run when it is posted.
    document = models.JSONField(default=dict, blank=True)
    # The concurrency token, bumped by every write: a save names the version
    # it started from, and a stale one is refused (the site editor's rule).
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(champions_meeting__isnull=False, league_of_heroes__isnull=True)
                    | Q(champions_meeting__isnull=True, league_of_heroes__isnull=False)
                ),
                name="exactly_one_community_guide_event",
            ),
        ]
        indexes = [
            models.Index(fields=["author", "champions_meeting"], name="community_guide_author_cm"),
            models.Index(fields=["author", "league_of_heroes"], name="community_guide_author_loh"),
        ]

    def __str__(self):
        return f"{self.author.username} - {self.title or 'Untitled'} ({self.event})"

    @property
    def event(self):
        return self.champions_meeting or self.league_of_heroes

    @property
    def kind(self):
        """The short tag the URLs and the client use: "cm" or "loh"."""
        return "cm" if self.champions_meeting_id else "loh"

    @property
    def event_id(self):
        return self.champions_meeting_id or self.league_of_heroes_id
