from django.db import models

from .skill import Skill
from .uma import Uma


class UmaSkillSource(models.TextChoices):
    """How an outfit comes by a skill."""
    UNIQUE = "unique", "Unique"
    INNATE = "innate", "Innate"
    AWAKENING = "awakening", "Awakening"
    EVOLVED = "evolved", "Evolved"


class UmaSkill(models.Model):
    """
    One skill one outfit carries, and how.

    Imported from the game data by `manage.py import_game_data`, which adds
    rows it does not find and never deletes one, so an editor's addition (a
    JP-only outfit filled in by hand, say) survives every run. Edited as an
    inline on the uma's admin page.

    `level` means different things per source, which is why it is one nullable
    column rather than two: for `awakening` it is the awakening rank (2..5) that
    unlocks the skill; for `unique` it is the star count at which that unique
    first applies (1 for the weaker one a ★1/★2 character starts with, 3 for
    the one that replaces it); for `innate` it is null. A ★1/★2 outfit therefore
    has two `unique` rows, and that is correct. For `evolved` it is the
    awakening rank that unlocks the evolution (3, 5 or 7), or null for an
    inherited unique's evolution (the game's succession kind).

    AN EVOLVED SKILL BELONGS TO ONE OUTFIT (the game keys it by card id, and
    two outfits of one character evolve differently), so a skill has at most
    one `evolved` row: the `one_outfit_per_evolved_skill` constraint.
    """

    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="skills")
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="umas")
    source = models.CharField(max_length=10, choices=UmaSkillSource.choices)
    level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Awakening rank (2 to 5), or the star count a unique applies from.",
    )
    notes = models.TextField(blank=True, default="", help_text="Notes for editors.")

    class Meta:
        ordering = ("source", "level", "skill__name")
        constraints = [
            models.UniqueConstraint(
                fields=["uma", "skill", "source"], name="unique_uma_skill_source",
            ),
            models.UniqueConstraint(
                fields=["skill"],
                condition=models.Q(source="evolved"),
                name="one_outfit_per_evolved_skill",
            ),
        ]
        verbose_name = "uma skill"
        verbose_name_plural = "uma skills"

    def __str__(self):
        return f"{self.uma.name}: {self.skill.name} ({self.source})"
