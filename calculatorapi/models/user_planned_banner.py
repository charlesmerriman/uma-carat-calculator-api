from django.db import models
from django.core.exceptions import ValidationError
from .custom_user import CustomUser
from .plan import Plan
from .banner_uma import BannerUma
from .banner_support import BannerSupport
from .banner_step_up import BannerStepUp


class UserPlannedBanner(models.Model):
    # TRANSITIONAL (multi-plan, release 1 of 2). A row is owned by its PLAN, and
    # the plan by a user -- `plan.user` is the real owner. `user` survives this
    # release only because `git push origin master` runs migrate while the old
    # code is still serving, and that code writes `user` and knows nothing of
    # `plan`. Release 2 sweeps any plan-less row it left behind into its
    # owner's active plan, makes `plan` NOT NULL and drops `user`.
    # Until then: every write sets BOTH, and every read filters on `plan`.
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="banners",
        blank=True,
        null=True,
    )
    banner_uma = models.ForeignKey(
        BannerUma,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    banner_support = models.ForeignKey(
        BannerSupport,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    banner_step_up = models.ForeignKey(
        BannerStepUp,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    # Deliberately overloaded: on a step-up row this counts STEPS, not pulls,
    # mirroring the source sheet's own overload of the same column. One column
    # beats a second sitting null on almost every row, and a row's kind is fixed
    # at creation so the meaning can never change under it. The client reads it
    # through plannedSteps() / plannedPulls() so nothing assumes pulls.
    number_of_pulls = models.IntegerField()
    # Copies the user intends to obtain WITHOUT pulling, using a selector ticket
    # or an SSR crystal. Only the count is stored — which resource pays for each
    # copy is derived on every render from the projected balances and the
    # banner's JP eligibility, so it can never go stale against them.
    reserved_copies = models.IntegerField(default=0)
    # The owner's own free-text reminder about this row ("skip if the selector
    # covers her"). It belongs to the ROW, so it is per plan and nothing in the
    # projection ever reads it. The length cap lives on the serializer
    # (NOTE_MAX_LENGTH in views/user_planned_banner.py): a Postgres text column
    # enforces none, and the API is the only writer. Private text -- it is
    # served to its owner only, and plans.copy_plan() blanks it whenever a copy
    # changes owner.
    note = models.TextField(blank=True, default="")
    # Which featured cards the row's odds are about -- choices, so they live on
    # the row like the counts. Plain card ids rather than FKs: whether an id
    # names an Uma or a SupportCard follows the row's target, and a pair of FKs
    # per field would be four columns for two numbers. The CLIENT ignores an id
    # that is not (or no longer) among the banner's featured cards, so an editor
    # removing a card can never 400 a plan its owner did not touch -- the trap
    # step-up selections had to be grandfathered out of.
    #
    # primary_card: the card the odds strip is about. Null = the client's
    #   default, the banner's first card of its highest rarity.
    # second_card: a second card to show joint odds for ("MLB of the first, and
    #   how many of this one"). Null = two-card odds off, the normal state.
    primary_card = models.PositiveIntegerField(null=True, blank=True)
    second_card = models.PositiveIntegerField(null=True, blank=True)
    # primary_target: how many copies of primary_card the two-card odds take
    #   it to before any free copy (a 200-pull exchange or a reserved copy)
    #   goes to second_card. Null = the client's default for the banner type
    #   (one copy of an uma, MLB of a support card). Any 1..5. A choice about
    #   the strip, so it copies across accounts like the ids.
    primary_target = models.PositiveSmallIntegerField(null=True, blank=True)

    # ── Row options (the panel under a row, beside its note) ──────────────────
    # Each one overrides a projection default for THIS row only, and each one's
    # "unset" value is what every row did before it existed, so no plan's
    # numbers moved when they were added.
    #
    # tickets_to_use: at most this many matching gacha tickets pay for the
    #   row's pulls. NULL = automatic: as many as the pulls need, or none while
    #   the stats block's spend_tickets_on_banners is off. A number here wins
    #   over that switch (owner's call, 2026-10-16): an explicit per-row choice
    #   beats the global default. 0 holds every ticket back on this row alone.
    tickets_to_use = models.PositiveSmallIntegerField(null=True, blank=True)

    # paid_carat_use: which paid-carat pulls the row may buy, overriding the
    #   stats block's discounted_paid_pulls / full_price_paid_pulls pair.
    #   NULL = follow them. Ignored on a step-up row, which is paid-only by
    #   definition (its whole ladder costs paid carats).
    PAID_NONE = "none"
    PAID_DISCOUNT = "discount"
    PAID_ALL = "all"
    PAID_CARAT_USE_CHOICES = [
        (PAID_NONE, "Don't use paid carats"),
        (PAID_DISCOUNT, "Daily discount pull only"),
        (PAID_ALL, "Discount and full price"),
    ]
    paid_carat_use = models.CharField(
        max_length=8, choices=PAID_CARAT_USE_CHOICES, null=True, blank=True
    )

    # pulls_already_made: pulls the player has ALREADY made on this banner
    #   while it is live. They cost nothing in the projection (those carats
    #   have left the balance the player typed in); they only move the
    #   200-pull exchange, so the odds and the pity-threshold colouring see the
    #   banner's real counter. A fact about the player's game account rather
    #   than a choice, which is why plans.copy_plan() blanks it whenever a copy
    #   changes owner, exactly like `note`.
    pulls_already_made = models.PositiveSmallIntegerField(default=0)

    # excluded_from_projection: the row stays in the plan (greyed) but spends
    #   nothing, so a "maybe" banner can be weighed without deleting it. Income
    #   is unaffected either way: it is a pure function of each banner's end
    #   date, never of the rows around it.
    excluded_from_projection = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # Exactly one target, now across three columns. Widened from the
            # two-way only_one_support_or_uma; every pre-existing row already
            # satisfies this (each carries one of uma/support, with a null
            # step-up), so the migration needs no data pass.
            models.CheckConstraint(
                condition=(
                    models.Q(banner_uma__isnull=False,
                             banner_support__isnull=True,
                             banner_step_up__isnull=True)
                    | models.Q(banner_uma__isnull=True,
                               banner_support__isnull=False,
                               banner_step_up__isnull=True)
                    | models.Q(banner_uma__isnull=True,
                               banner_support__isnull=True,
                               banner_step_up__isnull=False)
                ),
                name="exactly_one_banner_target",
            )
        ]

    @property
    def banner_target(self):
        """Whichever banner this row points at, or None.

        The one place the three FKs are inspected server-side, mirroring
        plannedBannerTarget() on the client. Callers wanting "the banner"
        should not re-derive the precedence each time.
        """
        return self.banner_uma or self.banner_support or self.banner_step_up

    def clean(self):
        targets = [self.banner_uma, self.banner_support, self.banner_step_up]
        chosen = [target for target in targets if target]
        if not chosen:
            raise ValidationError(
                "One of banner_uma, banner_support or banner_step_up must be set."
            )
        if len(chosen) > 1:
            raise ValidationError(
                "Only one of banner_uma, banner_support or banner_step_up may be set."
            )

    def __str__(self):
        banner = self.banner_target
        banner_name = banner.name if banner else "(no banner)"
        # "pulls" is the wrong noun on a step-up row, where the same column
        # counts steps.
        unit = "steps" if self.banner_step_up_id else "pulls"
        return f"{self.user.username} - {banner_name} ({self.number_of_pulls} {unit})"
