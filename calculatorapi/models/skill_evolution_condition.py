from django.db import models

from .skill import Skill


class SkillEvolutionCondition(models.Model):
    """
    One requirement for unlocking an evolved skill ("Reach 600 Stamina",
    "Win the Japan Cup"), from the game's `skill_upgrade_condition` table.

    Hangs off the EVOLVED skill, not off whoever holds it, because every
    evolved skill has exactly one source and the three kinds don't share
    another place: an outfit's evolution is an `evolved` UmaSkill row, a
    succession evolution one too, and a training scenario's has no uma at all
    (`Skill.evolution_scenario`). The text is per condition (checked on the JP
    data, 2026-10-23), so it lives here, in the same three slots as a skill's:
    global's English first, then UmaTL's, with the Japanese for the admin.

    Game-owned: the import replaces an evolved skill's conditions as a set on
    every run. The raw numbers are kept verbatim for a later reader, like
    `Skill.condition`.
    """

    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="evolution_conditions",
    )
    game_id = models.PositiveIntegerField(unique=True, help_text="skill_upgrade_condition.id")
    num = models.IntegerField(help_text="The game's raw value.")
    sub_num = models.IntegerField(help_text="The game's raw value.")
    timing_type = models.IntegerField(help_text="The game's raw value.")
    count_type = models.IntegerField(help_text="The game's raw value.")
    text = models.TextField(blank=True, default="", help_text="Global's English text.")
    translated_text = models.TextField(blank=True, default="", help_text="UmaTL's English text.")
    jp_text = models.TextField(blank=True, default="", help_text="The JP client's text.")

    class Meta:
        ordering = ("game_id",)
        verbose_name = "evolution condition"
        verbose_name_plural = "evolution conditions"

    def __str__(self):
        return self.shown_text or self.jp_text or f"condition {self.game_id}"

    @property
    def shown_text(self):
        """What a player reads: global's text, else the translation, else ""."""
        return self.text or self.translated_text
