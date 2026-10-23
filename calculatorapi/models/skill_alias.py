from django.db import models

from .skill import Skill


class SkillAlias(models.Model):
    """
    A name a skill used to go by, so a guide that wrote it keeps linking.

    Names change twice in a skill's life: UmaTL revises its translations, and
    global's official name replaces the translation (or a name an editor
    filled in early) when the skill reaches global. The import adds the old
    name here whenever it overwrites one. Every name lookup (a `[[mention]]`,
    the `[[` suggestions, the sheet importer) matches the shown name OR an
    alias, and the site always shows the current name.

    Why a table and not a rewrite: the alternative is the import editing
    `[[Old]]` into `[[New]]` inside guide prose, a deploy changing what writers
    wrote. An alias leaves their text alone and still resolves.
    """

    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name="aliases")
    name = models.CharField(max_length=255, db_index=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["skill", "name"], name="unique_skill_alias"),
        ]
        verbose_name = "skill alias"
        verbose_name_plural = "skill aliases"

    def __str__(self):
        return f"{self.name} → {self.skill.admin_label}"
