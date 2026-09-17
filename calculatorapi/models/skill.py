from django.db import models


class SkillRarity(models.IntegerChoices):
    """
    The game's own `skill_data.rarity` numbering, stored as-is.

    3 and 4 are the two halves of a ★1/★2 character's unique: the weaker one
    they start with and the one that replaces it at ★3. 6 (evolved) does not
    exist on global yet; the value is here so the import is a copy when it does.
    """
    WHITE = 1, "White"
    GOLD = 2, "Gold"
    UNIQUE_BEFORE_THREE_STAR = 3, "Unique (★1/★2)"
    UNIQUE_AT_THREE_STAR = 4, "Unique (upgraded at ★3)"
    UNIQUE = 5, "Unique"
    EVOLVED = 6, "Evolved"


class SkillTier(models.IntegerChoices):
    """`skill_data.group_rate`: where a skill sits within its white/gold group."""
    PENALTY = -1, "× penalty"
    WHITE = 1, "○ white"
    GOLD = 2, "◎ gold"


class SkillQuerySet(models.QuerySet):
    """
    "Is this skill on global yet?", asked of the database.

    THE RULE: a skill is on global when the game gives it an English
    description. It is derived from the text and never stored, so it cannot
    disagree with the data, and a skill that releases fixes itself on the next
    import. `Skill.is_on_global` is the same rule for one row; keep the two in
    step. `\\S` rather than `!= ""` so whitespace alone does not count as text.

    Anything that shows skills to a player (the CM planner, a skill page) must
    go through `on_global()`. A skill that is not there yet may sit in the
    backend, but a player never sees it.
    """

    def on_global(self):
        return self.filter(description__regex=r"\S")

    def not_on_global(self):
        return self.exclude(description__regex=r"\S")


class Skill(models.Model):
    """
    One skill as the global game defines it, imported from the game's data.

    Every row here comes from `manage.py import_game_data` reading the committed
    snapshot, and the import overwrites the game-owned columns on every run.
    The editor-owned columns are `image` (usually set in bulk by
    `link_skill_images`, but a hand pick sticks) and `admin_comments`.

    "IS THIS THE GOLD VERSION OF SOMETHING?" is answered by `group_id`: the
    white, gold and × versions of one effect share it, and `tier` says which
    this one is. The admin shows the siblings in a read-only column rather than
    through a second table. `evolves_from` is the other relationship, and it
    stays empty until global has skill evolution.
    """

    game_id = models.PositiveIntegerField(
        unique=True,
        help_text="The game's skill id. Evolved ids reach nine digits.",
    )
    name = models.CharField(max_length=255, help_text="The global English name.")
    description = models.TextField(
        blank=True,
        default="",
        help_text="The game's own description text. Shown by default.",
    )
    description_detailed = models.TextField(
        blank=True,
        default="",
        help_text=(
            "A fan translation with the concrete numbers, for a future "
            "\"detailed\" toggle. Blank until that source is imported."
        ),
    )
    rarity = models.PositiveSmallIntegerField(choices=SkillRarity.choices)
    group_id = models.PositiveIntegerField(
        help_text="Shared by the white, gold and × versions of one effect.",
    )
    tier = models.SmallIntegerField(
        choices=SkillTier.choices,
        help_text="Which version within the group this is.",
    )
    icon_id = models.PositiveIntegerField(
        help_text="One of 63 generic icons, shared by every skill of its kind.",
    )
    cost = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Skill points to learn it. Uniques have no cost.",
    )
    precondition = models.TextField(
        blank=True,
        default="",
        help_text="The game's raw precondition string, kept verbatim for a later parser.",
    )
    condition = models.TextField(
        blank=True,
        default="",
        help_text="The game's raw activation condition string, kept verbatim.",
    )
    evolves_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evolutions",
        help_text="For an evolved skill, the skill it evolved from. Empty on global for now.",
    )
    image = models.ImageField(upload_to="skills/", blank=True, null=True)
    admin_comments = models.TextField(blank=True, null=True, help_text="Notes for editors.")

    objects = SkillQuerySet.as_manager()

    class Meta:
        ordering = ("name", "game_id")

    def __str__(self):
        return f"{self.name} ({self.game_id})"

    @property
    def is_on_global(self):
        """Has this skill released on global? See SkillQuerySet for the rule."""
        return bool(self.description.strip())

    def siblings(self):
        """The other versions of this effect: same group, different tier."""
        return Skill.objects.filter(group_id=self.group_id).exclude(pk=self.pk).order_by("-tier")
