from django.db import models

from .skill import Skill
from .support_card import SupportCard


class SupportCardSkillSource(models.TextChoices):
    """How a support card gives a skill."""
    HINT = "hint", "Hint"
    EVENT = "event", "Event"


class SupportCardSkill(models.Model):
    """
    One skill one support card can give, and how.

    `hint` rows come from the game's own data (the card's hint table) and
    `event` rows from gametora's per-card pages, since the game's story-choice
    data has no clean table for them. Both are imported by
    `manage.py import_game_data`, which adds rows it does not find and never
    deletes one. Edited as an inline on the support card's admin page.
    """

    support_card = models.ForeignKey(
        SupportCard, on_delete=models.CASCADE, related_name="skills",
    )
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="support_cards")
    source = models.CharField(max_length=10, choices=SupportCardSkillSource.choices)
    notes = models.TextField(blank=True, default="", help_text="Notes for editors.")

    class Meta:
        ordering = ("source", "skill__name")
        constraints = [
            models.UniqueConstraint(
                fields=["support_card", "skill", "source"],
                name="unique_support_card_skill_source",
            ),
        ]
        verbose_name = "support card skill"
        verbose_name_plural = "support card skills"

    def __str__(self):
        return f"{self.support_card.name}: {self.skill.name} ({self.source})"
