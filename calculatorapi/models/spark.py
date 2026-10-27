"""
A spark (the game's "factor"): what a veteran passes on when she is a parent.

A veteran's game export lists her sparks, and each parent's, as bare ids
(`factor_info_array[].factor_id`). This table is what those ids mean, so the
Collection's veteran card can name them: "Speed ★★★", "Oka Sho ★".

GAME DATA, ONE WAY IN
---------------------
Filled by `import_game_data` from scripts/data/master_snapshot/sparks.json,
which scripts/extract_master_snapshot.py reads from the global client's own
`succession_factor` table and its English names (text_data category 147). The
import runs on every deploy, so a new uma's unique spark is named as soon as
the snapshot is refreshed for her. Nobody edits a spark: there is no admin page.

The primary key IS the game's id. Nothing else points at a spark row, a
veteran's `game_data` keeps the ids as the game wrote them, and a lookup by
id is the only read (GET /trained-umas/<id>, views/trained_uma.py).

`stars` is the game's `rarity` column, 1..3. The export's own `level` field is
0 on every spark and is never read.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SparkKind(models.TextChoices):
    """The game's `factor_type`, by the colour a player knows it by."""

    STAT = "stat", "Stat"              # blue: Speed, Stamina, Power, Guts, Wit
    APTITUDE = "aptitude", "Aptitude"  # pink: Turf, Sprint, Front Runner...
    UNIQUE = "unique", "Unique skill"  # green: an outfit's unique skill
    SKILL = "skill", "Skill"            # white
    RACE = "race", "Race"              # white: a race won
    SCENARIO = "scenario", "Scenario"  # white: a training scenario
    CARNIVAL = "carnival", "Carnival bonus"


class Spark(models.Model):
    id = models.IntegerField(primary_key=True, help_text="The game's factor_id.")
    kind = models.CharField(max_length=16, choices=SparkKind.choices)
    stars = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ("id",)

    def __str__(self):
        return f"{self.name} {'★' * self.stars}"
