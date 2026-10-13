"""
A player's own team for one Champions Meeting or League of Heroes event.

Three umas, each with a running style and six support cards (slot 6 is the
borrowed card). Built on the PvP page's "My team" view and, from R2b, in the
navbar's team drawer. Saved to the account here; a guest keeps the same shape
on their device (frontend services/guestPvpTeamStore.ts) and never calls the
routes.

PRIVATE, LIKE A PLAN
--------------------
Every row here is user data (content_snapshot.PRIVATE_MODELS) and none of it
reaches /calculator-data (public_payload_cache._IRRELEVANT_MODELS, so saving a
team never drops the catalogue). The name, the notes and the pin are seen only
by the owner. Posting a team to the gallery is R3's opt-in and will add its
own columns; nothing here is public.

ONE TABLE FOR BOTH EVENT KINDS
------------------------------
The same nullable FK pair and XOR constraint as PvpGuide (models/pvp_guide.py),
for the same reason: a team holds no dates, the client already has the event.

WHAT A SAVE MAY HOLD
--------------------
A draft may be incomplete (an empty slot is simply no row), but it may never
hold a shape the game refuses: the same uma twice, the same card twice in one
deck, or two outfits of one character on a team. The first two are unique
constraints; the character rule spans rows and is checked by pvp_teams.py on
every save, not only when posting, so a draft never drifts into a team that
cannot be posted without the player noticing.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .champions_meeting import ChampionsMeeting
from .custom_user import CustomUser
from .league_of_heroes import LeagueOfHeroes
from .pvp_guide import PvpStyle
from .support_card import SupportCard
from .uma import Uma

# The most teams one account may hold for ONE event. Checked on creation only,
# like PLAN_CAP: it never refuses a save to a team that exists, so lowering it
# later strands nobody's work.
PVP_TEAM_CAP_PER_EVENT = 5

# A team is three umas; a deck is five owned cards plus one borrowed.
PVP_TEAM_SIZE = 3
PVP_DECK_SIZE = 6
PVP_BORROW_SLOT = 6

# Notes on a team (R2b's sticky notes). Stored as a JSON list rather than a
# child table: they are always read and written whole, with the team, and a
# table would add a model, a migration and a reconcile for no query anyone
# runs. views/pvp_team.py NoteSerializer is the validator.
PVP_NOTE_CAP = 10
PVP_NOTE_MAX_LENGTH = 1000


class PvpNoteColor(models.TextChoices):
    YELLOW = "yellow", "Yellow"
    PINK = "pink", "Pink"
    BLUE = "blue", "Blue"
    GREEN = "green", "Green"


class PvpTeam(models.Model):
    """One saved team. Members and their cards hang off it."""

    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="pvp_teams"
    )
    champions_meeting = models.ForeignKey(
        ChampionsMeeting,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pvp_teams",
    )
    league_of_heroes = models.ForeignKey(
        LeagueOfHeroes,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pvp_teams",
    )
    # Private to its owner, like Plan.name. Blank means the client shows
    # "Team 1", "Team 2" by position.
    name = models.CharField(max_length=40, blank=True, default="")
    # The drawer's pin (R2b): the team it opens on regardless of which event is
    # next. At most one per account, by the partial constraint below, the
    # Plan.is_active idiom. Zero is fine and common: no pin means "the team for
    # the running or next event". A pin on an event that has ended is ignored
    # by the client, not cleared here.
    is_focus = models.BooleanField(default=False)
    # [{"text": str, "color": PvpNoteColor}], at most PVP_NOTE_CAP. See above.
    notes = models.JSONField(default=list, blank=True)
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
                name="exactly_one_pvp_team_event",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_focus=True),
                name="one_focus_pvp_team_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.name or 'Team'} ({self.event})"

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


class PvpTeamMember(models.Model):
    """One of the team's three umas, in a position 0..2, running one style."""

    team = models.ForeignKey(PvpTeam, on_delete=models.CASCADE, related_name="members")
    position = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(PVP_TEAM_SIZE - 1)]
    )
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="+")
    # A style a uma actually runs: never "everyone", which only a guide row uses.
    style = models.CharField(max_length=10, choices=PvpStyle.choices)

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["team", "position"], name="unique_pvp_team_position"
            ),
            models.UniqueConstraint(fields=["team", "uma"], name="unique_pvp_team_uma"),
            models.CheckConstraint(
                condition=Q(position__lte=PVP_TEAM_SIZE - 1),
                name="pvp_team_position_in_range",
            ),
            models.CheckConstraint(
                condition=~Q(style=PvpStyle.EVERYONE),
                name="pvp_team_member_runs_one_style",
            ),
        ]

    def __str__(self):
        return f"{self.position}: {self.uma}"


class PvpTeamCard(models.Model):
    """One support card in a member's deck. Slot 6 is the borrowed card."""

    member = models.ForeignKey(
        PvpTeamMember, on_delete=models.CASCADE, related_name="cards"
    )
    slot = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(PVP_DECK_SIZE)]
    )
    support_card = models.ForeignKey(
        SupportCard, on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        ordering = ("slot",)
        constraints = [
            models.UniqueConstraint(
                fields=["member", "slot"], name="unique_pvp_deck_slot"
            ),
            # The game refuses a borrowed copy of a card already in the deck,
            # so the borrow slot is included.
            models.UniqueConstraint(
                fields=["member", "support_card"], name="unique_pvp_deck_card"
            ),
            models.CheckConstraint(
                condition=Q(slot__gte=1, slot__lte=PVP_DECK_SIZE),
                name="pvp_deck_slot_in_range",
            ),
        ]

    def __str__(self):
        return f"{self.slot}: {self.support_card}"
