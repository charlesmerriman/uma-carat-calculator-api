"""
The PvP guide: what to build for one Champions Meeting or League of Heroes event.

Written by the community sheet's authors in the admin and shown on the site's
PvP page. Replaces the never-rendered ChampionsMeetingUmaRecommendation table
and the five `*_recommendation` stat columns on the two event models.

ONE GUIDE TABLE FOR BOTH EVENT KINDS
------------------------------------
ChampionsMeeting and LeagueOfHeroes are two models because each predicts its
dates against its own anchors (predictions.py). A guide holds no dates at all:
the client already has the event, dates included, from /calculator-data. So
one PvpGuide points at exactly one of the two through a nullable OneToOne pair
and the `exactly_one_pvp_event` constraint (the idiom of
UserPlannedBanner.exactly_one_banner_target). Two guide models would mean every
child table, inline and serializer twice.

EVERY SECTION IS OPTIONAL
-------------------------
The sheet has two formats. The JP-era "PvP List" has decks per style and tier;
the newer per-cup tabs have stat tables, tiered skill lists and team comps and
no decks. The children below are the superset of both, and an editor fills
whichever they have. The page skips a section with no rows.

SKILLS SHOWN ARE SKILLS ON GLOBAL
---------------------------------
A recommended skill points at a real Skill row so it gets its icon and the
game's description. Only skills on global may be picked or shown, enforced in
three layers: the FK's limit_choices_to (the admin's autocomplete and form),
PvpSkillRecommendation.clean(), and the filter in views/pvp_guide.py. See
SkillQuerySet.on_global for the rule itself.

Everything here is CONTENT (content_snapshot.CONTENT_MODELS), and every FK
points at content.
"""

import difflib
import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .champions_meeting import ChampionsMeeting
from .league_of_heroes import LeagueOfHeroes
from .scenario import Scenario
from .skill import Skill
from .support_card import SupportCard
from .uma import Uma

# The help text on every note and text box an editor fills. The site turns
# ==this== into red text (frontend utils/remarkHighlight.ts), the sheet's way of
# marking a must-have.
HIGHLIGHT_HELP = (
    "Wrap words in two equals signs to show them in red: ==Must have==. "
    "Put a skill's name in double square brackets to link it: [[Groundwork]]. "
    "Both work in every note and text box here."
)

# [[Skill name]]: a mention of a skill, linked on the site to the Skill row of
# that name (its icon and the game's description). Matched ignoring case
# against skills on global only; saving refuses a name that matches none
# (SkillMentionsMixin), so every mention a visitor sees resolves.
SKILL_MENTION = re.compile(r"\[\[([^\[\]\n]+)\]\]")


def find_skill_mentions(text):
    """The names inside [[...]] in one text, stripped, in order."""
    return [match.strip() for match in SKILL_MENTION.findall(text or "")]


def unknown_skill_mentions(names):
    """The names that match no skill on global, ignoring case."""
    wanted = {name.casefold(): name for name in names if name}
    if not wanted:
        return []
    known = {
        name.casefold()
        for name in Skill.objects.on_global().values_list("name", flat=True)
    }
    return [name for key, name in wanted.items() if key not in known]


def _suggest(name):
    """Up to three global skill names close to a mistyped one."""
    names = list(Skill.objects.on_global().values_list("name", flat=True))
    by_key = {candidate.casefold(): candidate for candidate in names}
    close = difflib.get_close_matches(name.casefold(), list(by_key), n=3, cutoff=0.6)
    return [by_key[key] for key in close]


class SkillMentionsMixin:  # pylint: disable=too-few-public-methods
    """Refuses a save whose text mentions a skill that does not exist.

    `MENTION_FIELDS` names the text fields to check. The error lands on the
    field, with suggestions, so an editor fixes a typo instead of publishing a
    mention that would show as plain text.
    """

    MENTION_FIELDS = ()

    def clean(self):
        errors = {}
        for field in self.MENTION_FIELDS:
            unknown = unknown_skill_mentions(find_skill_mentions(getattr(self, field, "")))
            if not unknown:
                continue
            parts = []
            for name in unknown:
                suggestions = _suggest(name)
                hint = f" (did you mean {', '.join(suggestions)}?)" if suggestions else ""
                parts.append(f"[[{name}]]{hint}")
            errors[field] = "No skill on global has this name: " + "; ".join(parts)
        if errors:
            raise ValidationError(errors)
        super().clean()

MARKDOWN_HELP = (
    "Markdown. A blank line starts a new paragraph, **bold**, *italic*, "
    "and a link is [the words](/faq). "
) + HIGHLIGHT_HELP


STAT_HELP = (
    "As the guide writes it: 1200, 1100+, 578* (with the * explained in the footnote). "
    "Leave blank for a dash."
)


class PvpStyle(models.TextChoices):
    """
    A running style as a guide talks about it, plus "everyone".

    Text rather than Uma.RunningStyle's 1..4 integers: "front" on the wire needs
    no lookup on the client, and the guide needs two values the game data does
    not have (runaway, a front runner that blocks, and everyone, for rows that
    apply to the whole team). APTITUDE_FOR_STYLE is the one bridge between this
    and the uma's aptitude columns.
    """
    RUNAWAY = "runaway", "Runaway"
    FRONT = "front", "Front"
    PACE = "pace", "Pace"
    LATE = "late", "Late"
    END = "end", "End"
    EVERYONE = "everyone", "Everyone"


# Display order. Sorted in Python by the serializer rather than in SQL, because
# the database would sort the strings alphabetically.
STYLE_ORDER = (
    PvpStyle.EVERYONE,
    PvpStyle.RUNAWAY,
    PvpStyle.FRONT,
    PvpStyle.PACE,
    PvpStyle.LATE,
    PvpStyle.END,
)
STYLE_RANK = {style.value: index for index, style in enumerate(STYLE_ORDER)}

# Which Uma aptitude column a style reads. A runaway is a front runner, so it
# reads the front aptitude. Used by the team builder's aptitude badges (R2).
APTITUDE_FOR_STYLE = {
    PvpStyle.RUNAWAY.value: "apt_front",
    PvpStyle.FRONT.value: "apt_front",
    PvpStyle.PACE.value: "apt_pace",
    PvpStyle.LATE.value: "apt_late",
    PvpStyle.END.value: "apt_end",
}


class PvpSkillCategory(models.TextChoices):
    """
    How a guide groups its skills. The sheet's "Green" and "Debuffs" groups are
    `style=everyone` rows in the green and debuff categories.
    """
    ACCELERATION = "acceleration", "Acceleration"
    SPEED = "speed", "Speed"
    GREEN = "green", "Green"
    RECOVERY = "recovery", "Recovery"
    DEBUFF = "debuff", "Debuff"
    OTHER = "other", "Other"


def _style_field(allow_everyone=True, **kwargs):
    """The `style` column every child carries.

    The choices list is always the full one so the admin's dropdown and the
    wire values stay one vocabulary. Where "everyone" makes no sense (a starred
    uma runs one style) a CheckConstraint on the model refuses it.
    """
    help_text = (
        "Which running style this is for. \"Everyone\" means the whole team."
        if allow_everyone
        else "Which running style this is for."
    )
    return models.CharField(
        max_length=10, choices=PvpStyle.choices, help_text=help_text, **kwargs
    )


def _order_field():
    return models.PositiveSmallIntegerField(
        default=0,
        help_text="Lower numbers show first.",
    )


class PvpGuide(SkillMentionsMixin, models.Model):
    """One event's guide. Its children hold every section."""

    MENTION_FIELDS = ("short_description", "meta_analysis")

    # PROTECT on both: a guide is weeks of an editor's work, so the admin
    # refuses to delete an event that still has one rather than taking the
    # guide with it.
    champions_meeting = models.OneToOneField(
        ChampionsMeeting,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pvp_guide",
    )
    league_of_heroes = models.OneToOneField(
        LeagueOfHeroes,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pvp_guide",
        verbose_name="League of Heroes event",
    )
    scenario = models.ForeignKey(
        Scenario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pvp_guides",
        help_text="The training scenario this guide's builds are for.",
    )
    style_ranking = models.CharField(
        max_length=120,
        blank=True,
        help_text="Strongest style first, for example: Front >> Runaway >= End > Late > Pace",
    )
    short_description = models.TextField(blank=True, help_text=MARKDOWN_HELP)
    meta_analysis = models.TextField(blank=True, help_text=MARKDOWN_HELP)
    is_published = models.BooleanField(
        default=False,
        verbose_name="published",
        help_text=(
            "Visitors only see the guide once this is ticked. Until then it is a "
            "draft that only signed-in staff can see on the site."
        ),
    )
    admin_comments = models.TextField(
        blank=True,
        help_text="Notes for editors. Never shown on the site.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "PvP guide"
        verbose_name_plural = "PvP guides"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(champions_meeting__isnull=False, league_of_heroes__isnull=True)
                    | models.Q(champions_meeting__isnull=True, league_of_heroes__isnull=False)
                ),
                name="exactly_one_pvp_event",
            ),
        ]

    @property
    def event(self):
        """Whichever event this guide is for, or None on an unsaved blank form."""
        return self.champions_meeting or self.league_of_heroes

    @property
    def event_type(self):
        """The same tag EventTypeMixin puts on the event itself."""
        return "champions_meeting" if self.champions_meeting_id else "league_of_heroes"

    def clean(self):
        # The constraint again, as a form error an editor can read instead of a
        # database error page.
        has_cm = self.champions_meeting_id is not None
        has_loh = self.league_of_heroes_id is not None
        if has_cm == has_loh:
            raise ValidationError(
                "Pick one event: a Champions Meeting or a League of Heroes event, not both."
            )
        super().clean()

    def __str__(self):
        event = self.event
        return str(event) if event else "PvP guide (no event)"


class PvpStatConfig(SkillMentionsMixin, models.Model):
    """One row of recommended stats. Several per style is the point: the sheet
    shows variants side by side ("901* + 1 Gold", "Trackblazer")."""

    MENTION_FIELDS = ("footnote",)

    guide = models.ForeignKey(PvpGuide, on_delete=models.CASCADE, related_name="stat_configs")
    style = _style_field()
    label = models.CharField(
        max_length=60,
        blank=True,
        help_text="Optional, for a variant: \"901* + 1 Gold\", \"Trackblazer\", \"Minimum\".",
    )
    # Text, not numbers: the sheet's values carry meaning past the number.
    # "1100+" is at least 1100, "578*" points at a footnote, "901* + 1 Gold"
    # is a threshold with a condition. An integer column would drop all of
    # that and refuse what an editor copies across. Blank shows as an en dash,
    # the sheet's "-" for a stat it says nothing about.
    speed = models.CharField(max_length=20, blank=True, help_text=STAT_HELP)
    stamina = models.CharField(max_length=20, blank=True)
    power = models.CharField(max_length=20, blank=True)
    guts = models.CharField(max_length=20, blank=True)
    wit = models.CharField(max_length=20, blank=True)
    footnote = models.CharField(max_length=200, blank=True, help_text=HIGHLIGHT_HELP)
    order = _order_field()

    class Meta:
        ordering = ("order", "id")
        verbose_name = "stat recommendation"
        verbose_name_plural = "stat recommendations"

    def __str__(self):
        return f"{self.get_style_display()} {self.label}".strip()


class PvpUmaRecommendation(SkillMentionsMixin, models.Model):
    """A starred uma for one style."""

    MENTION_FIELDS = ("note",)

    guide = models.ForeignKey(
        PvpGuide, on_delete=models.CASCADE, related_name="uma_recommendations"
    )
    style = _style_field(allow_everyone=False)
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="pvp_recommendations")
    stars = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="1 to 5.",
    )
    role = models.CharField(
        max_length=40,
        blank=True,
        help_text="Optional short tag shown on the picture, like \"Runaway blocker\".",
    )
    note = models.CharField(
        max_length=300,
        blank=True,
        help_text="Shown when someone hovers the uma. " + HIGHLIGHT_HELP,
    )
    order = _order_field()

    class Meta:
        # The view sorts by stars first; this is the tiebreak among equal stars.
        ordering = ("order", "id")
        verbose_name = "recommended uma"
        verbose_name_plural = "recommended umas"
        constraints = [
            models.UniqueConstraint(
                fields=["guide", "style", "uma"], name="unique_pvp_uma_per_style",
            ),
            models.CheckConstraint(
                condition=~models.Q(style=PvpStyle.EVERYONE),
                name="pvp_uma_recommendation_has_a_style",
            ),
            models.CheckConstraint(
                condition=models.Q(stars__gte=1, stars__lte=5),
                name="pvp_uma_recommendation_stars_1_to_5",
            ),
        ]

    def clean(self):
        if self.style == PvpStyle.EVERYONE:
            raise ValidationError({"style": "A recommended uma runs one style. Pick it."})
        super().clean()

    def __str__(self):
        return f"{self.uma.name} ({self.get_style_display()}, {self.stars}★)"


class PvpSkillRecommendation(SkillMentionsMixin, models.Model):
    """A recommended skill, linked to the game's skill data."""

    MENTION_FIELDS = ("note",)

    guide = models.ForeignKey(
        PvpGuide, on_delete=models.CASCADE, related_name="skill_recommendations"
    )
    style = _style_field()
    category = models.CharField(max_length=20, choices=PvpSkillCategory.choices)
    tier = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MaxValueValidator(4)],
        help_text="0 (take first) to 4. Leave blank if the guide has no tiers.",
    )
    # Layer one of the on_global rule: the admin's autocomplete and form only
    # offer skills with an English description. Same regex as on_global().
    skill = models.ForeignKey(
        Skill,
        on_delete=models.CASCADE,
        related_name="pvp_recommendations",
        limit_choices_to=models.Q(description__regex=r"\S"),
        help_text="Only skills already on global can be picked.",
    )
    note = models.CharField(max_length=200, blank=True, help_text=HIGHLIGHT_HELP)
    order = _order_field()

    class Meta:
        ordering = ("order", "id")
        verbose_name = "recommended skill"
        verbose_name_plural = "recommended skills"
        constraints = [
            models.UniqueConstraint(
                fields=["guide", "style", "category", "skill"],
                name="unique_pvp_skill_per_style_and_category",
            ),
        ]

    def clean(self):
        # Layer two, for a save that reached past the form's choices.
        if self.skill_id is not None and not self.skill.is_on_global:
            raise ValidationError({"skill": "That skill is not on global yet."})
        super().clean()

    def __str__(self):
        return f"{self.skill.name} ({self.get_style_display()}, {self.get_category_display()})"


class PvpStyleNote(SkillMentionsMixin, models.Model):
    """One block of advice per style: the sheet's per-style "Rec. Skills" prose."""

    MENTION_FIELDS = ("body",)

    guide = models.ForeignKey(PvpGuide, on_delete=models.CASCADE, related_name="style_notes")
    style = _style_field()
    body = models.TextField(help_text=MARKDOWN_HELP)

    class Meta:
        ordering = ("id",)
        verbose_name = "style note"
        verbose_name_plural = "style notes"
        constraints = [
            models.UniqueConstraint(fields=["guide", "style"], name="unique_pvp_style_note"),
        ]

    def __str__(self):
        return f"{self.get_style_display()} note"


class PvpDeck(SkillMentionsMixin, models.Model):
    """
    One example deck. Its six slots are rows of PvpDeckSlot, edited on the
    deck's own admin page: the admin allows one level of inlines, and the guide
    page already holds the deck list itself.
    """

    MENTION_FIELDS = ("note",)

    guide = models.ForeignKey(PvpGuide, on_delete=models.CASCADE, related_name="decks")
    style = _style_field()
    label = models.CharField(
        max_length=60,
        help_text="\"Beginner\", \"Standard\", \"Whale\" or anything else.",
    )
    note = models.CharField(max_length=300, blank=True, help_text=HIGHLIGHT_HELP)
    order = _order_field()

    class Meta:
        ordering = ("order", "id")
        verbose_name = "PvP deck"
        verbose_name_plural = "PvP decks"

    def __str__(self):
        return f"{self.guide}: {self.label} ({self.get_style_display()})"


class PvpDeckSlot(SkillMentionsMixin, models.Model):
    """
    One card in a deck slot, or an alternative for it.

    Order 0 is the card; order 1 and up are alternatives shown under it ("or
    any speed"). A row may name no card when its note says it all, but it must
    carry one or the other (`slot_has_card_or_note`).
    """

    MENTION_FIELDS = ("note",)

    deck = models.ForeignKey(PvpDeck, on_delete=models.CASCADE, related_name="slots")
    slot = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(6)],
        help_text="1 to 6.",
    )
    is_borrow = models.BooleanField(
        default=False,
        verbose_name="borrow",
        help_text="Tick on the borrowed card's slot.",
    )
    order = models.PositiveSmallIntegerField(
        default=0,
        help_text="0 for the card. 1 and up for alternatives in the same slot.",
    )
    support_card = models.ForeignKey(
        SupportCard,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pvp_deck_slots",
    )
    note = models.CharField(
        max_length=120,
        blank=True,
        help_text="Why this card: \"2SP\", \"Gold + Link\", or \"or any speed\" with no card.",
    )

    class Meta:
        ordering = ("slot", "order", "id")
        verbose_name = "deck slot"
        verbose_name_plural = "deck slots"
        constraints = [
            models.UniqueConstraint(
                fields=["deck", "slot", "order"], name="unique_pvp_deck_slot_order",
            ),
            models.CheckConstraint(
                condition=models.Q(support_card__isnull=False) | ~models.Q(note=""),
                name="slot_has_card_or_note",
            ),
            models.CheckConstraint(
                condition=models.Q(slot__gte=1, slot__lte=6),
                name="pvp_deck_slot_1_to_6",
            ),
        ]

    def clean(self):
        if self.support_card_id is None and not self.note.strip():
            raise ValidationError("Pick a card, or write a note like \"or any speed\".")
        super().clean()

    def __str__(self):
        card = self.support_card.name if self.support_card_id else self.note
        return f"Slot {self.slot}: {card}"


class PvpHonorableMention(SkillMentionsMixin, models.Model):
    """
    A card worth having that did not make a deck. Its own table rather than a
    slotless deck: mentions have no slots, so they stay a one-level inline on
    the guide page.
    """

    MENTION_FIELDS = ("note",)

    guide = models.ForeignKey(
        PvpGuide, on_delete=models.CASCADE, related_name="honorable_mentions"
    )
    style = _style_field()
    support_card = models.ForeignKey(
        SupportCard, on_delete=models.CASCADE, related_name="pvp_mentions"
    )
    note = models.CharField(max_length=200, blank=True, help_text=HIGHLIGHT_HELP)
    order = _order_field()

    class Meta:
        ordering = ("order", "id")
        verbose_name = "honorable mention"
        verbose_name_plural = "honorable mentions"
        constraints = [
            models.UniqueConstraint(
                fields=["guide", "style", "support_card"], name="unique_pvp_mention_per_style",
            ),
        ]

    def __str__(self):
        return f"{self.support_card.name} ({self.get_style_display()})"


class PvpTeamComp(SkillMentionsMixin, models.Model):
    """A recommended team shape, like "2 Front + 1 Pace"."""

    MENTION_FIELDS = ("text", "note")

    guide = models.ForeignKey(PvpGuide, on_delete=models.CASCADE, related_name="team_comps")
    text = models.CharField(max_length=120, help_text="For example: 2 Front + 1 Pace")
    note = models.CharField(max_length=200, blank=True, help_text=HIGHLIGHT_HELP)
    order = _order_field()

    class Meta:
        ordering = ("order", "id")
        verbose_name = "team comp"
        verbose_name_plural = "team comps"

    def __str__(self):
        return str(self.text)


class PvpGuideSkills(PvpGuide):
    """
    The same rows as PvpGuide, registered a second time so the admin can give
    a guide's recommended skills a page of their own: one table per position
    (Everyone, Runaway, Front, Pace, Late, End). See PvpGuideSkillsAdmin.

    A proxy, so no table and no data of its own: it exists only because Django
    registers one admin per model. It is in neither snapshot list for the same
    reason (test_every_model_is_either_content_or_private skips proxies).
    """

    class Meta:
        proxy = True
        verbose_name = "PvP guide skills"
        verbose_name_plural = "PvP guide skills"
