from django.db import models
from django.db.models import Q

from .banner_timeline import BannerTimeline
from .support_card import SupportCard
from .uma import Uma


class StoryEventKind(models.TextChoices):
    """What kind of event a row is. The game numbers each kind on its own, so a
    `game_id` means something only beside its kind (notes/story-events-plan-2.md).

    story        a story event (`story_event_data`), runs with its banner
    main_story   a main story chapter (`main_story_part`), arrives with a banner
                 and never closes
    anniversary  an anniversary's Part 2 missions and login bonus (the missions'
                 `event_id` in `mission_data`), outlasts its banner
    special      a one-off event (`fan_raid_data`, or one an editor adds by hand)
    """

    STORY = "story", "Story event"
    MAIN_STORY = "main_story", "Main story"
    ANNIVERSARY = "anniversary", "Anniversary missions"
    SPECIAL = "special", "Special event"


class StoryEvent(models.Model):
    """An event that gives away free cards, shown on the Timeline under its
    banner: a story event (most rows), a main story chapter, an anniversary's
    missions or a special event (`kind`). notes/story-events-plan.md is the plan
    for story events, notes/story-events-plan-2.md for the other kinds. The
    class keeps its first name: renaming it would rename the table, the payload
    key and the snapshot entry for nothing a player sees.

    NOT A GameEvent. A GameEvent row is one INCOME bucket per banner window (207
    of them, named after the banner's umas), read by the income ledger. A story
    event is the named thing the game runs with about one banner in four. This
    table is display only: nothing in the income engine, the odds or selector
    eligibility reads it, and its reward figures are the event's own (2,160
    carats for a recent one), not the window's.

    WHO OWNS WHICH COLUMN. The deploy's import_game_data writes what the game
    states: `game_id`, `name` (Global's), `jp_name`, the five instants, and the
    rewards while `rewards_edited` is off. Editors own the picture, the working
    English name of an event Global hasn't named yet, and each card's mark and
    note. An editor who changes a reward ticks `rewards_edited` (the admin does
    it for them), and the import leaves the rewards alone from then on: the
    team's own figures (Daptrius's Carat Data Sheet) win over the files'.

    DATES. A story event runs with its banner, and its rewards can be claimed
    for a few days after. A main story chapter has a start and no end. An
    anniversary's missions and a special event have their own end. The game's
    own instants are used when Global has run the event; until then they come
    from the banner (predictions.story_event_dates has the rule per kind).
    """

    kind = models.CharField(
        max_length=16,
        choices=StoryEventKind.choices,
        default=StoryEventKind.STORY,
        db_index=True,
        help_text="What kind of event this is. It decides the row's label and which dates it shows.",
    )
    # Unique within a kind, not across the table: main story part 7 and story
    # event 7 would be different things. Blank on an event an editor adds by
    # hand (a gift or a collaboration the game's files don't describe).
    game_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="game id",
        help_text=(
            "The game's id for this event within its kind, e.g. story event 1022. Written by "
            "the game-data import; blank on an event added by hand."
        ),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Global's name for the event. Written by the import; blank until Global runs it.",
    )
    # Shown while `name` is blank, so Global's official name replaces it the day
    # the game ships one (owner, 2026-11-17). Never the Japanese title: an event
    # with neither reads "Story event" on the site.
    working_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "An English name for an event Global hasn't named yet, from the Carat Data Sheet. "
            "Shown only while Name is blank."
        ),
    )
    jp_name = models.CharField(
        max_length=255, blank=True, default="", verbose_name="JP name",
        help_text="The event's name in the JP client. For editors only; the site never shows it.",
    )
    # Every kind's picture lives here and draws in the same place on the row
    # (owner, 2026-11-20: "prepare a place for those images just like the story
    # events"). The import never writes it.
    image = models.ImageField(
        upload_to="story_events/", null=True, blank=True,
        help_text=(
            "The event's logo or picture. Optional: the row is drawn without one. Best as a "
            "transparent PNG twice as wide as tall (1024 x 512), like the story event logos, so "
            "stacked rows line up. For a batch, name files by kind and id: main-story-7.png, "
            "anniversary-138.png, special-1001.png."
        ),
    )
    banner_timeline = models.ForeignKey(
        BannerTimeline,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="story_events",
        help_text=(
            "The banner this event runs with. The import matches it by start date and "
            "never changes it once set, so a correction here sticks."
        ),
    )

    global_start_at = models.DateTimeField(null=True, blank=True, verbose_name="Global start")
    global_end_at = models.DateTimeField(null=True, blank=True, verbose_name="Global end")
    global_claim_end_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Global claim deadline",
        help_text="The last moment to claim the event's rewards, a few days after it ends.",
    )
    jp_start_at = models.DateTimeField(null=True, blank=True, verbose_name="JP start")
    jp_end_at = models.DateTimeField(null=True, blank=True, verbose_name="JP end")

    carat_amount = models.PositiveIntegerField(default=0, verbose_name="carats")
    uma_ticket_amount = models.PositiveIntegerField(default=0, verbose_name="uma scout tickets")
    support_ticket_amount = models.PositiveIntegerField(
        default=0, verbose_name="support scout tickets")
    ssr_shard_amount = models.PositiveIntegerField(
        default=0, verbose_name="SSR crystal shards",
        help_text="Rainbow Crystal Shards.")
    sr_shard_amount = models.PositiveIntegerField(
        default=0, verbose_name="SR crystal shards",
        help_text="Gold Crystal Shards.")
    rewards_edited = models.BooleanField(
        default=False,
        verbose_name="rewards set by hand",
        help_text=(
            "Ticked automatically when a reward is changed here. While ticked, the game-data "
            "import leaves the rewards alone. Untick it to let the next deploy refill them from "
            "the game files."
        ),
    )

    class Meta:
        verbose_name = "free card event"
        verbose_name_plural = "free card events"
        ordering = ("kind", "game_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("kind", "game_id"), condition=Q(game_id__isnull=False),
                name="one_story_event_per_kind_and_game_id",
            ),
        ]

    @property
    def shown_name(self):
        """Global's name, else the editors' working name, else blank."""
        return self.name or self.working_name

    def __str__(self):
        label = StoryEventKind(self.kind).label if self.kind in StoryEventKind.values else "Event"
        number = f" {self.game_id}" if self.game_id is not None else ""
        return self.shown_name or self.jp_name or f"{label}{number}"


class StoryEventCard(models.Model):
    """A free reward an event gives away, with the editors' mark: a support
    card, or an uma (Special Week (Commander) from a main story chapter). Exactly
    one of the two, by a check constraint (the UserPlannedBanner idiom).

    No copies column: every event gives five of each support card (Daptrius,
    2026-11-17; the game files agree for every kind), and an uma is one.

    `must_get` is the one mark the site has (owner, 2026-11-17): only the cards
    the old sheet marked "Must" carry it, and a card without it shows nothing in
    its place. Never a scale.
    """

    story_event = models.ForeignKey(StoryEvent, on_delete=models.CASCADE, related_name="cards")
    support_card = models.ForeignKey(
        SupportCard, on_delete=models.CASCADE, null=True, blank=True,
        related_name="story_event_links")
    # Never read by selector eligibility, like the card above: an uma on an
    # event and no banner stays refused under a real cutoff.
    uma = models.ForeignKey(
        Uma, on_delete=models.CASCADE, null=True, blank=True, related_name="story_event_links",
        help_text="A free character, instead of a support card.")
    must_get = models.BooleanField(
        default=False,
        verbose_name="must get",
        help_text="Marks a card worth going out of your way for. Most cards don't have it.",
    )
    note = models.CharField(
        max_length=80, blank=True, default="",
        help_text="Optional, shown beside the card. 80 characters.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "free reward"
        verbose_name_plural = "free rewards"
        ordering = ("order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("story_event", "support_card"), name="one_story_event_card_per_event",
            ),
            models.UniqueConstraint(
                fields=("story_event", "uma"), condition=Q(uma__isnull=False),
                name="one_story_event_uma_per_event",
            ),
            models.CheckConstraint(
                condition=(Q(support_card__isnull=False, uma__isnull=True)
                           | Q(support_card__isnull=True, uma__isnull=False)),
                name="story_event_reward_is_a_card_or_an_uma",
            ),
        ]

    def __str__(self):
        reward = self.support_card if self.support_card_id else self.uma
        return f"{reward.name if reward else '?'} from {self.story_event}"
