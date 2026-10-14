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

    @property
    def version_label(self):
        """"○ white", "◎ gold" or "evolved": how a picker or a chip names
        this version of the effect. The × penalty version keeps its own label
        but is never offered as a recommendation."""
        if self.evolves_from_id:
            return "evolved"
        return self.get_tier_display()

    def family(self):
        """The other versions of this skill a player can be shown, in order:
        white, gold, then evolved. See skill_families()."""
        return skill_families([self]).get(self.pk, [])


def _root_group(skill):
    """The group an evolved skill's base belongs to, or the skill's own."""
    if skill.evolves_from_id:
        return skill.evolves_from.group_id
    return skill.group_id


def _version_rank(skill):
    # White (1), gold (2), then evolved; the game id breaks ties.
    if skill.evolves_from_id:
        return (3, skill.game_id)
    return (skill.tier, skill.game_id)


def skill_families(skills):
    """{skill id: [the other versions of that skill]} for many skills at once.

    A FAMILY IS DERIVED, NEVER STORED. The game data already ties the
    versions of one effect together: the white (○), gold (◎) and × penalty
    rows share a `group_id`, and an evolved skill points at its base through
    `evolves_from`. So a guide stores one row per effect, pointing at the
    version its author named, and the other versions come from here: the
    rows in the same group plus the evolutions of that group (and, for an
    evolved skill, its base's group), minus the × penalty version and minus
    anything not on global (SkillQuerySet.on_global, the one rule for what a
    player may be shown). One query for the whole set, so the guide API's
    query count stays flat however many skills a guide holds.

    When global gets skill evolution, the import fills `evolves_from` and
    evolved versions appear here without a code change.
    """
    skills = list(skills)
    if not skills:
        return {}
    group_ids = {skill.group_id for skill in skills}
    rows = (
        Skill.objects.on_global()
        .filter(
            models.Q(group_id__in=group_ids)
            | models.Q(evolves_from__group_id__in=group_ids)
            | models.Q(evolutions__group_id__in=group_ids)
        )
        .exclude(tier=SkillTier.PENALTY)
        .select_related("evolves_from")
        .distinct()
    )
    by_root = {}
    for row in rows:
        by_root.setdefault(_root_group(row), []).append(row)
    families = {}
    for skill in skills:
        members = by_root.get(_root_group(skill), [])
        families[skill.pk] = sorted(
            (row for row in members if row.pk != skill.pk), key=_version_rank,
        )
    return families
