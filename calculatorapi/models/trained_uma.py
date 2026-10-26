"""
A player's veterans: umas they finished training, with the stats, aptitudes and
skills each one ended on (notes/pvp-planner-plan.md, R2, "Veterans for CM Runs").

A FACT ABOUT THE PERSON, NEVER A CHOICE
---------------------------------------
A veteran lives on the account, used by every event's teams and logged runs.
Nothing about it goes on Plan or PvpTeam. A team member and a logged run's
runner hold a LINK (`trained_uma`, nullable, SET_NULL) and never a copy of the
numbers: a game veteran cannot change once its training ends, so the link
always reads what a copy would, and the stats live in one place.

TWO SOURCES, ONE TABLE
----------------------
`import`: read from the player's own game export (umadump's
trained_chara_data.json), parsed in their browser. `import_key` is the game's
own id for the veteran (`trained_chara_id`, unique within one game account),
which is what a re-import and, later, a race replay match on.

ALL OF IT IS KEPT, MINUS OTHER PEOPLE (owner, 2026-10-26)
---------------------------------------------------------
`game_data` holds the veteran's whole game object as the file gave it: sparks,
both parents, the training deck, the career's races, titles, the player's own
game account id and memo. Features promote a field to a real column when they
need to query it (the stats, aptitudes and skills already are). The one thing
removed is OTHER players' account ids (a borrowed veteran's owner, a borrowed
parent's owner): stripped in the browser before upload and again here
(trained_umas.strip_other_players), so they are never stored. `is_borrowed`
remembers that a veteran was someone else's after the id is gone.
`manual`: typed in on the Collection page, the path for phone players. Henry's
"enter the stats and skills of your umas" is this.

A RE-IMPORT NEVER STRIPS CM HISTORY
-----------------------------------
A re-import updates veterans in place by `import_key`. One missing from the new
file is deleted, UNLESS a logged run links it: then it is kept and flagged
`missing_from_import`, so the run keeps its stats (trained_umas.import_veterans).

PRIVATE: content_snapshot.PRIVATE_MODELS, public_payload_cache._IRRELEVANT_MODELS.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .custom_user import CustomUser
from .skill import Skill
from .uma import Uma

# Checked on write, like PLAN_CAP: a save that would pass it is refused, and
# nothing already held is ever removed for it. 1000 (owner, 2026-10-26: "all
# of it"): the owner's account held 256 after 15 months, about 7 KB each.
TRAINED_UMA_CAP = 1000

# The game's stats run 1..1200 in most scenarios and past that in some; the
# ceiling is generous on purpose, it only stops typos like 12000.
TRAINED_UMA_STAT_MAX = 2000
TRAINED_UMA_LABEL_MAX_LENGTH = 40
TRAINED_UMA_SKILL_CAP = 80
# The game's memo is a few characters; the ceiling only stops abuse.
TRAINED_UMA_MEMO_MAX_LENGTH = 100
# One veteran's raw game object, serialized. The owner's average is 7 KB; the
# ceiling stops a crafted request from filling the database.
TRAINED_UMA_GAME_DATA_MAX_BYTES = 64 * 1024

# The ten aptitude columns, in the catalogue's key order (frontend AptitudeKey).
# 1..8 = G..S, as the game stores them. NULL = not known (a hand entry left blank).
APTITUDE_KEYS = ("turf", "dirt", "short", "mile", "medium", "long", "front", "pace", "late", "end")
STAT_FIELDS = ("speed", "stamina", "power", "guts", "wit")


class TrainedUmaSource(models.TextChoices):
    IMPORT = "import", "From the game"
    MANUAL = "manual", "By hand"


def _stat():
    return models.PositiveSmallIntegerField(validators=[MaxValueValidator(TRAINED_UMA_STAT_MAX)])


def _aptitude():
    return models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(8)]
    )


class TrainedUma(models.Model):
    """One veteran. One outfit: a member may only link a veteran of her own outfit."""

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="trained_umas")
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="+")
    # The player's own name for it ("Oguri for CM 21"); blank shows the uma's name.
    label = models.CharField(max_length=TRAINED_UMA_LABEL_MAX_LENGTH, blank=True, default="")

    speed = _stat()
    stamina = _stat()
    power = _stat()
    guts = _stat()
    wit = _stat()

    apt_turf = _aptitude()
    apt_dirt = _aptitude()
    apt_short = _aptitude()
    apt_mile = _aptitude()
    apt_medium = _aptitude()
    apt_long = _aptitude()
    apt_front = _aptitude()
    apt_pace = _aptitude()
    apt_late = _aptitude()
    apt_end = _aptitude()

    # The game's evaluation score; only an import has it.
    rank_score = models.PositiveIntegerField(null=True, blank=True)

    source = models.CharField(max_length=6, choices=TrainedUmaSource.choices)
    # The game's trained_chara_id; set exactly when source is "import".
    import_key = models.PositiveBigIntegerField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    # Kept by a re-import only because a logged run links it (module docstring).
    missing_from_import = models.BooleanField(default=False)
    # The whole game object, other players' ids removed (module docstring).
    # NULL for a hand entry. Never in the list route: it is about 7 KB each.
    game_data = models.JSONField(null=True, blank=True)
    # The player's own note on the veteran, from the game. Shown only to them.
    memo = models.CharField(max_length=TRAINED_UMA_MEMO_MAX_LENGTH, blank=True, default="")
    # Another player's veteran (a rental), from the game's use_type / owner.
    is_borrowed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "import_key"],
                condition=Q(import_key__isnull=False),
                name="one_trained_uma_per_import_key",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source=TrainedUmaSource.IMPORT, import_key__isnull=False)
                    | Q(source=TrainedUmaSource.MANUAL, import_key__isnull=True)
                ),
                name="trained_uma_source_matches_key",
            ),
            *[
                models.CheckConstraint(
                    condition=Q(**{f"{stat}__lte": TRAINED_UMA_STAT_MAX}),
                    name=f"trained_uma_{stat}_in_range",
                )
                for stat in STAT_FIELDS
            ],
            *[
                models.CheckConstraint(
                    # NULL passes a CHECK, which is what "not known" needs.
                    condition=Q(**{f"apt_{key}__gte": 1, f"apt_{key}__lte": 8})
                    | Q(**{f"apt_{key}__isnull": True}),
                    name=f"trained_uma_apt_{key}_in_range",
                )
                for key in APTITUDE_KEYS
            ],
        ]

    def __str__(self):
        return f"{self.user.username} - {self.label or self.uma}"

    def aptitudes(self):
        """{"turf": 7, ...}: the wire's `apt`, the catalogue's shape."""
        return {key: getattr(self, f"apt_{key}") for key in APTITUDE_KEYS}


class TrainedUmaSkill(models.Model):
    """A skill a veteran learned, at its level (a unique skill levels up)."""

    trained_uma = models.ForeignKey(TrainedUma, on_delete=models.CASCADE, related_name="skills")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="+")
    level = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(fields=["trained_uma", "skill"], name="unique_trained_uma_skill"),
            models.CheckConstraint(
                condition=Q(level__gte=1, level__lte=10), name="trained_uma_skill_level_in_range"
            ),
        ]

    def __str__(self):
        return f"{self.skill} (level {self.level})"
