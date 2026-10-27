"""
A player's logged results for one Champions Meeting or League of Heroes event:
one row per ticket (an attempt of five races) and one for the final.

Built for the PvP page's "CM Runs" view (notes/pvp-planner-plan.md, "R3 CM
Runs"). Account only: a guest sees a sign-in card, so there is no device copy.

A RUN IS A FACT, A TEAM IS A CHOICE
-----------------------------------
A run copies its lineup (uma and style per spot) when the ticket is started and
owns it from then on. It never points at the PvpTeam it came from: editing or
deleting a team must not rewrite what already happened, and results hang off
the account and the event, not off a team. PvpRunRunner.trained_uma is a
LINK to the veteran that ran (a game veteran is frozen once trained, so a link
reads the same numbers a copy would, and the stats live in one place).

PRIVATE, LIKE A TEAM
--------------------
Every row here is user data (content_snapshot.PRIVATE_MODELS) and none of it
reaches /calculator-data (public_payload_cache._IRRELEVANT_MODELS: the CM Runs
view auto-saves on every dropdown change, the 2026-09-23 lesson). Only the
owner ever reads a row; any later analytics are totals across accounts.

ONE TABLE FOR BOTH EVENT KINDS
------------------------------
The nullable FK pair and XOR constraint of PvpTeam and PvpGuide. Because one of
the pair is always NULL, and NULLs never collide in a unique constraint, each
"one per slot" rule is a PAIR of partial constraints, one per event FK.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .champions_meeting import ChampionsMeeting
from .custom_user import CustomUser
from .league_of_heroes import LeagueOfHeroes
from .pvp_guide import PvpStyle
from .pvp_team import PVP_TEAM_SIZE
from .uma import Uma

# Races in one ticket. A final is one race.
PVP_RUN_RACES = 5
PVP_FINAL_RACES = 1

# The highest day and ticket number per event kind ("cm" / "loh"), checked by
# pvp_runs.py rather than the database so a game change is one line here.
# CM: Round 1 is days 1-2 and Round 2 days 3-4, four attempts a day (GameTora,
# Henry, 2026-10-26). LoH: five tickets a day for six days (Daptrius).
PVP_RUN_DAYS = {"cm": 4, "loh": 6}
PVP_RUN_TICKETS = {"cm": 4, "loh": 5}

# A Champions Meeting final is three trainers, placed 1st to 3rd. LoH has no
# final the site knows the shape of yet, so pvp_runs.py refuses one.
PVP_FINAL_PLACEMENTS = 3

PVP_RUN_NOTE_MAX_LENGTH = 120


class PvpRunStage(models.TextChoices):
    DAY = "day", "Day"
    FINAL = "final", "Final"


class PvpFinalsGroup(models.TextChoices):
    """Which finals the player reached. 1st in Group B is not 1st in Group A,
    so a placement means nothing without it."""
    A = "A", "Group A"
    B = "B", "Group B"


class PvpRun(models.Model):
    """One ticket of one day, or the final, for one event."""

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="pvp_runs")
    champions_meeting = models.ForeignKey(
        ChampionsMeeting,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pvp_runs",
    )
    league_of_heroes = models.ForeignKey(
        LeagueOfHeroes,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pvp_runs",
    )
    stage = models.CharField(max_length=5, choices=PvpRunStage.choices)
    # Set for a day's ticket, NULL for the final (the stage_shape constraint).
    day = models.PositiveSmallIntegerField(null=True, blank=True)
    ticket = models.PositiveSmallIntegerField(null=True, blank=True)
    # The final only; both optional, so a player can log the race before
    # remembering the group, or the other way round.
    finals_group = models.CharField(
        max_length=1, choices=PvpFinalsGroup.choices, blank=True, default=""
    )
    placement = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.CharField(max_length=PVP_RUN_NOTE_MAX_LENGTH, blank=True, default="")
    # The player's word that the races this ticket does not list were never
    # run: with none listed the ticket went unused, with some they stopped
    # after those. Only ever set by the player, never from the clock: "not
    # logged" on a past day may only be slow typing, and a guessed skip would
    # stay wrong. A day's ticket only (pvp_run_stage_shape).
    skipped = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("stage", "day", "ticket")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(champions_meeting__isnull=False, league_of_heroes__isnull=True)
                    | Q(champions_meeting__isnull=True, league_of_heroes__isnull=False)
                ),
                name="exactly_one_pvp_run_event",
            ),
            # A day's ticket has a day and a number; a final has neither. Only
            # a final has a group or a placement, and only a day's ticket can
            # be skipped. The isnull terms are not redundant: NULL >= 1 is
            # unknown, and a CHECK passes unknown.
            models.CheckConstraint(
                condition=(
                    Q(
                        stage=PvpRunStage.DAY,
                        day__isnull=False,
                        ticket__isnull=False,
                        day__gte=1,
                        ticket__gte=1,
                        finals_group="",
                        placement__isnull=True,
                    )
                    | Q(stage=PvpRunStage.FINAL, day__isnull=True, ticket__isnull=True, skipped=False)
                ),
                name="pvp_run_stage_shape",
            ),
            models.UniqueConstraint(
                fields=["user", "champions_meeting", "day", "ticket"],
                condition=Q(champions_meeting__isnull=False, stage=PvpRunStage.DAY),
                name="one_pvp_run_per_cm_ticket",
            ),
            models.UniqueConstraint(
                fields=["user", "league_of_heroes", "day", "ticket"],
                condition=Q(league_of_heroes__isnull=False, stage=PvpRunStage.DAY),
                name="one_pvp_run_per_loh_ticket",
            ),
            models.UniqueConstraint(
                fields=["user", "champions_meeting"],
                condition=Q(champions_meeting__isnull=False, stage=PvpRunStage.FINAL),
                name="one_pvp_final_per_cm",
            ),
            models.UniqueConstraint(
                fields=["user", "league_of_heroes"],
                condition=Q(league_of_heroes__isnull=False, stage=PvpRunStage.FINAL),
                name="one_pvp_final_per_loh",
            ),
        ]

    def __str__(self):
        slot = "final" if self.stage == PvpRunStage.FINAL else f"day {self.day} #{self.ticket}"
        return f"{self.user.username} - {self.event} {slot}"

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


class PvpRunRunner(models.Model):
    """One of the three umas that ran the ticket, in a spot 0..2.

    A copy of the lineup at the time, never a pointer at a team member: see the
    module docstring. Same shape and rules as PvpTeamMember minus the deck."""

    run = models.ForeignKey(PvpRun, on_delete=models.CASCADE, related_name="runners")
    position = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(PVP_TEAM_SIZE - 1)]
    )
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="+")
    style = models.CharField(max_length=10, choices=PvpStyle.choices)
    # The veteran that ran (R2), or NULL. A LINK, never a copy of
    # her numbers: models/trained_uma.py says why. Must be the owner's own
    # veteran of this same outfit (trained_umas.check_links, on every save).
    trained_uma = models.ForeignKey(
        "TrainedUma", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["run", "position"], name="unique_pvp_run_position"
            ),
            models.UniqueConstraint(fields=["run", "uma"], name="unique_pvp_run_uma"),
            models.CheckConstraint(
                condition=Q(position__lte=PVP_TEAM_SIZE - 1),
                name="pvp_run_position_in_range",
            ),
            models.CheckConstraint(
                condition=~Q(style=PvpStyle.EVERYONE),
                name="pvp_run_runner_runs_one_style",
            ),
        ]

    def __str__(self):
        return f"{self.position}: {self.uma}"


class PvpRunRace(models.Model):
    """One race of a ticket and who won it.

    No row means "not raced yet"; a row with a NULL winner means "lost". The
    two are different on screen (an empty dropdown vs "Loss") and in a win
    rate (an unraced race is not in the denominator)."""

    run = models.ForeignKey(PvpRun, on_delete=models.CASCADE, related_name="races")
    race = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(PVP_RUN_RACES)]
    )
    # The spot (PvpRunRunner.position) of the player's uma that finished
    # first, or NULL for a loss. pvp_runs.py checks the spot has a runner.
    winner = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(PVP_TEAM_SIZE - 1)],
    )

    class Meta:
        ordering = ("race",)
        constraints = [
            models.UniqueConstraint(fields=["run", "race"], name="unique_pvp_run_race"),
            models.CheckConstraint(
                condition=Q(race__gte=1, race__lte=PVP_RUN_RACES),
                name="pvp_run_race_in_range",
            ),
            models.CheckConstraint(
                condition=Q(winner__isnull=True) | Q(winner__lte=PVP_TEAM_SIZE - 1),
                name="pvp_run_winner_in_range",
            ),
        ]

    def __str__(self):
        return f"race {self.race}: {'lost' if self.winner is None else f'spot {self.winner}'}"
