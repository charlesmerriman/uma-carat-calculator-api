"""
Every tunable number the carat projection uses, in one editable row.

WHY A MODEL AND NOT A CONSTANTS FILE
------------------------------------
These values are calibrated against a live source spreadsheet that changes
without notice. Holding them in code means a re-fit is a deploy; holding them in
one admin page means it is data entry. It also ends a specific hazard: the game
event buffer used to be written twice — once in `predictions.py` and once in the
frontend's `gameConstants.ts`, in two separate repositories, with a comment on
each warning that they must be kept in sync by hand.

WHY TYPED FIELDS AND NOT A KEY/VALUE TABLE
------------------------------------------
One column per constant buys admin validation, grouped fieldsets, a serializer
that documents the contract, and no stringly-typed lookups on the client. The
cost is a migration whenever a constant is added — which is irrelevant, because
a new constant needs frontend code to consume it anyway.

SINGLETON
---------
Exactly one row, always pk=1. `save()` pins the pk so a second row cannot be
created even through the shell, and deletion is refused. Read it with `load()`.

NOT CACHED, DELIBERATELY
------------------------
`load()` runs one primary-key lookup per request, which is noise next to the
queries `/calculator-data` already makes. Caching it would be worse than
useless: production runs several worker processes, so an edit saved by one
worker would not invalidate a local cache held by the others, and the site would
serve two different sets of constants depending on which worker answered.
"""

from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class CalculationConstants(models.Model):
    # ── Daily income ─────────────────────────────────────────────────────────
    daily_base_carats = models.IntegerField(
        default=75,
        validators=[MinValueValidator(0)],
        help_text="Carats from daily quests, every day. Sheet: the 75 in AN42.",
    )
    weekly_bonus_carats = models.IntegerField(
        default=150,
        validators=[MinValueValidator(0)],
        help_text=(
            "TOTAL login bonus carats per week, spread evenly across the seven "
            "days rather than paid on specific ones. Sheet: the 150 in AN42's "
            "75 + 150/7."
        ),
    )

    # ── Packs & passes ───────────────────────────────────────────────────────
    daily_carat_pack_per_day = models.IntegerField(
        default=50,
        validators=[MinValueValidator(0)],
        help_text="Daily Carat Pack drip, credited as FREE carats. Sheet: AR42.",
    )
    daily_carat_pack_paid_carats = models.IntegerField(
        default=500,
        validators=[MinValueValidator(0)],
        help_text=(
            "Daily Carat Pack repurchase bonus, credited as PAID carats — the "
            "balance that funds discounted pulls. Sheet: AZ42."
        ),
    )
    daily_carat_pack_cycle_days = models.IntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        help_text="Days between Daily Carat Pack repurchases.",
    )
    training_pass_start_date = models.DateField(
        default="2027-08-15",
        help_text=(
            "The date the Training Pass feature launches on Global. Nothing is "
            "earned from it before this."
        ),
    )
    training_pass_monthly_free_carats = models.IntegerField(
        default=1850,
        validators=[MinValueValidator(0)],
        help_text=(
            "FREE half of the paid Training Pass's monthly carats. With the paid "
            "half below this totals the pass's headline figure. Sheet: AQ42."
        ),
    )
    training_pass_monthly_paid_carats = models.IntegerField(
        default=350,
        validators=[MinValueValidator(0)],
        help_text="PAID half of the paid Training Pass's monthly carats. Sheet: BA42.",
    )
    monthly_base_reward = models.IntegerField(
        default=500,
        validators=[MinValueValidator(0)],
        help_text=(
            "Monthly carats for an account WITHOUT the paid Training Pass. "
            "Replaced by the paid figures above, never added to them."
        ),
    )
    training_pass_free_uma_tickets = models.IntegerField(
        default=2, validators=[MinValueValidator(0)],
        help_text="Monthly uma tickets every account gets once the pass launches.",
    )
    training_pass_free_support_tickets = models.IntegerField(
        default=2, validators=[MinValueValidator(0)],
        help_text="Monthly support tickets every account gets once the pass launches.",
    )
    training_pass_paid_bonus_uma_tickets = models.IntegerField(
        default=2, validators=[MinValueValidator(0)],
        help_text="EXTRA monthly uma tickets from the paid pass, added to the free tier's.",
    )
    training_pass_paid_bonus_support_tickets = models.IntegerField(
        default=2, validators=[MinValueValidator(0)],
        help_text="EXTRA monthly support tickets from the paid pass, added to the free tier's.",
    )
    training_pass_paid_ssr_shards = models.IntegerField(
        default=1, validators=[MinValueValidator(0)],
        help_text=(
            "Monthly SSR uncap shards from an ACTIVE paid Training Pass. The "
            "free tier earns none, so unlike the tickets above there is no free "
            "counterpart to add this to. Delivered on the same monthly clock as "
            "the pass's carats and tickets."
        ),
    )

    # ── Login campaigns & annual gifts ───────────────────────────────────────
    misc_earnings_monthly = models.IntegerField(
        default=1800,
        validators=[MinValueValidator(0)],
        help_text=(
            "Monthly approximation of gifts, career and Team Trials extras, "
            "dripped daily as monthly/30. Toggled per user. "
            "The live value is 2700 (90 a day), set here in the admin; this "
            "field default is only what a fresh database starts from. For "
            "reference the source sheet's own figure (Timeline!AW1) is 3000."
        ),
    )
    misc_earnings_delay_days = models.IntegerField(
        default=30,
        validators=[MinValueValidator(0)],
        help_text="Ramp-in before misc earnings start, counted from today. Sheet: AV42.",
    )
    fifty_day_login_carats = models.IntegerField(
        default=150, validators=[MinValueValidator(0)],
        help_text="Carats per completed login-campaign cycle. Sheet: AU42.",
    )
    fifty_day_login_cycle_days = models.IntegerField(
        default=50, validators=[MinValueValidator(1)],
        help_text="Length of that login campaign cycle, in days.",
    )
    valentines_carats = models.IntegerField(
        default=500, validators=[MinValueValidator(0)],
        help_text="Valentine's Day gift. Universal — no toggle.",
    )
    valentines_month = models.IntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Month of the Valentine's gift, 1-12.",
    )
    valentines_day = models.IntegerField(
        default=14, validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Day of month of the Valentine's gift.",
    )
    white_day_carats = models.IntegerField(
        default=500, validators=[MinValueValidator(0)],
        help_text="White Day gift. Universal — no toggle.",
    )
    white_day_month = models.IntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Month of the White Day gift, 1-12.",
    )
    white_day_day = models.IntegerField(
        default=14, validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Day of month of the White Day gift.",
    )
    monthly_shop_uma_tickets = models.IntegerField(
        default=4, validators=[MinValueValidator(0)],
        help_text=(
            "Uma tickets a player is ASSUMED to buy from the shop each month, "
            "until they set their own count. Matches the source sheet."
        ),
    )
    monthly_shop_support_tickets = models.IntegerField(
        default=4, validators=[MinValueValidator(0)],
        help_text=(
            "Support tickets a player is assumed to buy from the shop each "
            "month, until they set their own count."
        ),
    )
    # The two above are the DEFAULT purchase; these are the most the shop
    # sells. Separate numbers on purpose: raising the cap lets a player who
    # buys more say so without moving the projection of everyone who never
    # touched the setting.
    monthly_shop_uma_tickets_max = models.IntegerField(
        default=9, validators=[MinValueValidator(0)],
        help_text="Most uma tickets the shop sells in a month. Caps a player's own count.",
    )
    monthly_shop_support_tickets_max = models.IntegerField(
        default=9, validators=[MinValueValidator(0)],
        help_text="Most support tickets the shop sells in a month. Caps a player's own count.",
    )
    monthly_shop_restock_day = models.IntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text=(
            "Day of month the shop restocks. Counting from here rather than the "
            "1st stops a banner ending on the 1st being credited a bundle the "
            "player cannot buy yet. Sheet: BG42."
        ),
    )

    # ── Pull costs & uncap ───────────────────────────────────────────────────
    pull_cost_carats = models.IntegerField(
        default=150, validators=[MinValueValidator(1)],
        help_text="Carats per full-price pull.",
    )
    discounted_pull_cost_carats = models.IntegerField(
        default=50, validators=[MinValueValidator(1)],
        help_text=(
            "Carats per discounted pull. PAID carats only, and capped at one per "
            "day of a banner's run."
        ),
    )
    shards_per_crystal = models.IntegerField(
        default=20, validators=[MinValueValidator(1)],
        help_text="Shards that combine into one uncap crystal.",
    )

    # ── Step-up banners ──────────────────────────────────────────────────────
    # Five scalars cover the WHOLE ladder because the cycle repeats every five
    # steps: cost(n) = floor(n/5) * 5000 + the partial sum of the first n%5.
    # A list column or a second model would only be re-encoding that repetition.
    step_up_cost_step_1 = models.IntegerField(
        default=500, validators=[MinValueValidator(0)],
        help_text="Paid carats for step 1 of a Select Step-Up ladder. Sheet: AL360.",
    )
    step_up_cost_step_2 = models.IntegerField(
        default=700, validators=[MinValueValidator(0)],
        help_text="Paid carats for step 2.",
    )
    step_up_cost_step_3 = models.IntegerField(
        default=1000, validators=[MinValueValidator(0)],
        help_text="Paid carats for step 3 (first guaranteed card).",
    )
    step_up_cost_step_4 = models.IntegerField(
        default=1300, validators=[MinValueValidator(0)],
        help_text="Paid carats for step 4 (second guaranteed card).",
    )
    step_up_cost_step_5 = models.IntegerField(
        default=1500, validators=[MinValueValidator(0)],
        help_text=(
            "Paid carats for step 5, where the player CHOOSES the guaranteed "
            "card. The five together total 5,000 for one completed banner."
        ),
    )
    step_up_pulls_per_step = models.IntegerField(
        default=10, validators=[MinValueValidator(1)],
        help_text="Pulls per step. Each step is a 10-pull.",
    )
    step_up_target_rate = models.DecimalField(
        max_digits=6, decimal_places=4, default="0.0030",
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=(
            "Per-pull chance of the ONE card the player is chasing. Derived: the "
            "game's ~3% total ★3/SSR rate spread across the 10 cards they "
            "selected. Change it only if the pool size or the headline rate "
            "changes — it is not an independent dial."
        ),
    )
    step_up_max_rounds = models.IntegerField(
        default=7, validators=[MinValueValidator(1)],
        help_text=(
            "Safety bound on completed ladders, NOT the live constraint. Real "
            "cost and odds clamp at a banner's own banner_count x 5, which is "
            "always lower. The source sheet's 35-step cap is an artifact of its "
            "lookup table's extent rather than a game rule."
        ),
    )

    # ── Throughout-carat decay curve ─────────────────────────────────────────
    throughout_end_offset_days = models.IntegerField(
        default=4, validators=[MinValueValidator(0)],
        help_text=(
            "Days trimmed off a banner's end before the decay curve is measured. "
            "Sheet: end_date - ('Carat Calculator'!G25 + 1)."
        ),
    )
    throughout_filter_grace_days = models.IntegerField(
        default=4, validators=[MinValueValidator(0)],
        help_text=(
            "Grace window deciding which banners can still reach an event's "
            "throughout carats. Sheet: AQ32, which reads 3 — ours was fitted to 4 "
            "against published per-banner figures, so one of the offsets above "
            "may be absorbing a day. Confirm with the parity harness before "
            "treating either as authoritative."
        ),
    )
    throughout_decay_k = models.DecimalField(
        max_digits=4, decimal_places=2, default="2.00",
        validators=[MinValueValidator(0)],
        help_text=(
            "Steepness of the curve's early exponential leg. Higher front-loads "
            "more of the pool into the first days of a banner."
        ),
    )
    throughout_decay_linear_slope = models.DecimalField(
        max_digits=4, decimal_places=2, default="0.80",
        validators=[MinValueValidator(0)],
        help_text="Slope of the curve's slower linear leg, which governs the tail.",
    )

    # ── Global date prediction ───────────────────────────────────────────────
    prediction_factor = models.DecimalField(
        max_digits=5, decimal_places=3, default="0.664",
        validators=[MinValueValidator(0), MaxValueValidator(2)],
        help_text=(
            "Global covers JP's back catalogue faster than real time; each day of "
            "JP gap maps to this many days of Global gap. Raising it pushes every "
            "unconfirmed banner LATER. Retune if the observed cadence shifts."
        ),
    )
    game_event_end_buffer_days = models.IntegerField(
        default=4, validators=[MinValueValidator(0)],
        help_text=(
            "Days a game event's end trails its linked banner's end by. Used both "
            "to date events and to recover the banner window the decay curve runs "
            "over."
        ),
    )

    class Meta:
        verbose_name = "calculation constants"
        verbose_name_plural = "calculation constants"

    def __str__(self):
        return "Calculation constants"

    def save(self, *args, **kwargs):
        # Pin the pk so a second row can never exist, even from the shell.
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Refused. The projection reads this row on every request; deleting it
        would have `load()` silently recreate it with defaults, quietly throwing
        away a calibration."""

    @classmethod
    def load(cls):
        """The one row, created with defaults on first access.

        Defaults are the current calibration, so a fresh database — a new
        deployment, or a developer's local SQLite — starts correct rather than
        zeroed.
        """
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance
