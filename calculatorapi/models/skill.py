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
    """
    `skill_data.group_rate`: where a skill sits within its group.

    The labels below fit the common group, a white ○ and its gold. 58 groups
    (most green skills, the Straightaways / Corners whites) have a second
    white instead: the ○ at 1 and the ◎ at 2, both rarity WHITE. Twelve of
    those also have a gold above both, at 3, which has no member here. So the
    number never says the colour on its own: `rarity` does, and
    `Skill.version_label` is the label to show.
    """
    PENALTY = -1, "× penalty"
    WHITE = 1, "○ white"
    GOLD = 2, "◎ gold"


class SkillAvailability(models.TextChoices):
    """
    Where a skill is in the game, as the import last saw it. Import-owned and
    read-only in the admin: `global` exactly when the skill is in the global
    client's skill table, `announced` when the global client only has its
    official English text, `jp` for everything else (a JP-only skill, from
    the JP snapshot). A skill moves global-ward by itself on the import after
    the client that has it.
    """
    GLOBAL = "global", "On global"
    ANNOUNCED = "announced", "Announced for global"
    JP = "jp", "JP only"


def on_global_q(prefix=""):
    """
    THE RULE, as a Q object, for every query that filters skills a player may
    see, directly or through a relation (`prefix="skill__"`). The queryset's
    on_global(), the guide's prefetch and the recommendation FK's
    limit_choices_to all read it, so there is one rule, not copies of it.
    Callable with no arguments, which is how limit_choices_to calls it.

    Today that is: on global; or announced for it, unless evolved. Announced
    rows count because guides for upcoming CMs already name them
    (2026-10-16). An announced EVOLVED skill doesn't: global ships the names
    of every outfit's evolutions long before evolution itself, and a gold
    skill's "other versions" would list them as if they were in the game. They
    wait for the site to label JP-only content (skills-plan-3.md, steps 4-5).
    A JP-only skill is never shown, yet.
    """
    return (
        models.Q(**{f"{prefix}availability": SkillAvailability.GLOBAL})
        | (
            models.Q(**{f"{prefix}availability": SkillAvailability.ANNOUNCED})
            & ~models.Q(**{f"{prefix}rarity": SkillRarity.EVOLVED})
        )
    )


class SkillQuerySet(models.QuerySet):
    """
    "May a player be shown this skill?", asked of the database.

    THE RULE is `on_global_q()`: the skill is on global, or announced for it
    and not evolved (the stored `availability`). The name is older than the
    rule and reads "Global has published it", released or not; `availability`
    says which.

    Until 2026-10-23 the rule was derived from the text (an English
    description meant on global). It became a stored column when the JP
    snapshot came in: a JP-only skill can carry English text too (a
    translation, or a name an editor filled in early), so text no longer says
    where a skill is. The import is the only writer.

    Anything that shows skills to a player (the CM planner, a skill page) must
    go through `on_global()`. A skill that is not there yet may sit in the
    backend, but a player never sees it.
    """

    def on_global(self):
        return self.filter(on_global_q())

    def not_on_global(self):
        return self.exclude(on_global_q())


class Skill(models.Model):
    """
    One skill as the game defines it, imported from the game's data.

    Every row here comes from `manage.py import_game_data` reading the committed
    snapshot, and the import overwrites the game-owned columns on every run.
    The exception is a skill the game has only announced: its rarity, group
    and tier are guesses written once (an editor may correct them), its icon
    and cost are empty, and all of them become the game's own values the day
    the client defines it.
    The editor-owned columns are `image` (usually set in bulk by
    `link_skill_images`, but a hand pick sticks) and `admin_comments`.

    THREE TEXT SLOTS, GLOBAL FIRST (owner, 2026-10-16). `name` and
    `description` hold global's official English; `translated_*` UmaTL's
    fan translation of the JP text; `jp_*` the JP client's own. What a player
    reads is `shown_name` / `shown_description`: the global text when there
    is any, else the translation, each field on its own. Read those, never a
    slot directly, wherever a skill reaches a player. A skill with neither is
    admin-only and labelled by its Japanese name (`admin_label`).

    "IS THIS THE GOLD VERSION OF SOMETHING?" is answered by `group_id`: the
    white (○, and in some groups a ◎ too), gold and × versions of one effect
    share it, `tier` says where in the group this one sits, and `rarity` says
    its colour (SkillTier). The admin shows the siblings in a read-only column rather than
    through a second table. `evolves_from` is the other relationship, and it
    stays empty until global has skill evolution.
    """

    game_id = models.PositiveIntegerField(
        unique=True,
        help_text="The game's skill id. Evolved ids reach nine digits.",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="The global English name. Blank while global has none.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Global's own description text. Shown first.",
    )
    translated_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="UmaTL's English name, shown only while global has none. Import-owned.",
    )
    translated_description = models.TextField(
        blank=True,
        default="",
        help_text="UmaTL's English description, shown only while global has none. Import-owned.",
    )
    jp_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="The JP client's name. Admin only: search, and a label when nothing else exists.",
    )
    jp_description = models.TextField(
        blank=True,
        default="",
        help_text="The JP client's description. Admin only.",
    )
    availability = models.CharField(
        max_length=10,
        choices=SkillAvailability.choices,
        default=SkillAvailability.GLOBAL,
        help_text="Where the game has this skill. Set by the import only.",
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
        null=True,
        blank=True,
        help_text=(
            "One of 63 generic icons, shared by every skill of its kind. Empty for a "
            "skill the game has only announced (text ahead of release)."
        ),
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
    effects = models.JSONField(
        blank=True,
        default=list,
        help_text=(
            "The game's raw effects, verbatim: per condition slot, the duration, "
            "cooldown and abilities (scripts/extract_master_snapshot.py, effect_slots). "
            "The readable text is built from these in code."
        ),
    )
    evolution_scenario = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "For a training scenario's evolution, the game's scenario id. An outfit's "
            "evolution is an 'evolved' uma skill row instead."
        ),
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
        return f"{self.admin_label} ({self.game_id})"

    @property
    def is_on_global(self):
        """May a player be shown this skill? The same rule as on_global_q()."""
        if self.availability == SkillAvailability.GLOBAL:
            return True
        return self.availability == SkillAvailability.ANNOUNCED and self.rarity != SkillRarity.EVOLVED

    @property
    def shown_name(self):
        """What a player reads: global's name, else the translation, else ""."""
        return self.name or self.translated_name

    @property
    def shown_description(self):
        """As shown_name, for the description, decided on its own."""
        return self.description or self.translated_description

    @property
    def admin_label(self):
        """The admin's name for a row: the shown name, else the Japanese one."""
        return self.shown_name or self.jp_name or f"skill {self.game_id}"

    def siblings(self):
        """The other versions of this effect: same group, different tier."""
        return Skill.objects.filter(group_id=self.group_id).exclude(pk=self.pk).order_by("-tier")

    @property
    def version_label(self):
        """"○ white", "◎ white", "◎ gold" or "evolved": how a picker or a
        chip names this version of the effect. The × penalty version keeps its
        own label but is never offered as a recommendation.

        The colour is read from `rarity`, never from `tier` alone: in a ○/◎
        group tier 2 is the white ◎ and the gold sits at tier 3 (SkillTier).
        Reading the tier alone named Right-Handed ◎ a gold and Right-Handed
        Demon "3"."""
        if self.evolves_from_id:
            return "evolved"
        if self.tier == SkillTier.PENALTY:
            return SkillTier.PENALTY.label
        if self.rarity == SkillRarity.GOLD:
            return SkillTier.GOLD.label
        # A white at tier 2 is the ◎ of a ○/◎ pair. A unique's inherited copy
        # is a lone white at tier 2 as well, and reads "◎ white" too.
        if self.tier == SkillTier.GOLD:
            return "◎ white"
        return SkillTier.WHITE.label

    def family(self):
        """The other versions of this skill a player can be shown, in order:
        ○ white, ◎ white, gold, then evolved. See skill_families()."""
        return skill_families([self]).get(self.pk, [])


def _root_group(skill):
    """The group an evolved skill's base belongs to, or the skill's own."""
    if skill.evolves_from_id:
        return skill.evolves_from.group_id
    return skill.group_id


def _version_rank(skill):
    # The group's own order: ○ white (1), then a ◎ white or a gold (2), then
    # the gold above a ◎ (3), then evolved; the game id breaks ties.
    if skill.evolves_from_id:
        return (3, skill.game_id)
    return (skill.tier, skill.game_id)


def skill_families(skills):
    """{skill id: [the other versions of that skill]} for many skills at once.

    A FAMILY IS DERIVED, NEVER STORED. The game data already ties the
    versions of one effect together: the white (○, and a ◎ in some groups),
    gold and × penalty rows share a `group_id`, and an evolved skill points at its base through
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
