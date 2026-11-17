from django.db import models

from .banner_timeline import BannerTimeline
from .support_card import SupportCard


class StoryEvent(models.Model):
    """A story event: the game's limited-time event that runs alongside a banner
    and gives away a free support card (a "welfare" card), shown on the Timeline
    under its banner. notes/story-events-plan.md is the plan.

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

    DATES. An event runs with its banner, and its rewards can be claimed for a
    few days after. The game's own instants are used when Global has run the
    event; until then they come from the banner (predictions.story_event_dates).
    """

    game_id = models.PositiveIntegerField(
        unique=True,
        verbose_name="game id",
        help_text="The game's story event id, e.g. 1022. Written by the game-data import.",
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
    image = models.ImageField(
        upload_to="story_events/", null=True, blank=True,
        help_text="The event's logo. Optional: the row is drawn without one.",
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
        verbose_name = "story event"
        verbose_name_plural = "story events"
        ordering = ("game_id",)

    @property
    def shown_name(self):
        """Global's name, else the editors' working name, else blank."""
        return self.name or self.working_name

    def __str__(self):
        return self.shown_name or self.jp_name or f"Story event {self.game_id}"


class StoryEventCard(models.Model):
    """A free support card a story event gives away, with the editors' mark.

    No copies column: every event gives five of each card (Daptrius,
    2026-11-17), so the site says so in its copy.

    `must_get` is the one mark the site has (owner, 2026-11-17): only the cards
    the old sheet marked "Must" carry it, and a card without it shows nothing in
    its place. Never a scale.
    """

    story_event = models.ForeignKey(StoryEvent, on_delete=models.CASCADE, related_name="cards")
    support_card = models.ForeignKey(
        SupportCard, on_delete=models.CASCADE, related_name="story_event_links")
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
        verbose_name = "free card"
        verbose_name_plural = "free cards"
        ordering = ("order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("story_event", "support_card"), name="one_story_event_card_per_event",
            ),
        ]

    def __str__(self):
        return f"{self.support_card.name} from {self.story_event}"
