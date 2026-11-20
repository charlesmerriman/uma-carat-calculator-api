from django.db import models

from .anniversary_event import AnniversaryEvent


PRODUCT_TYPE_CHOICES = [
    ("carat_pack", "Carat pack"),
    ("uma_selector", "Uma selector"),
    ("support_selector", "Support selector"),
]


class AnniversaryEventProduct(models.Model):
    """One purchasable line on a campaign: a discounted carat pack or a selector.

    Packs and selectors are one tagged model rather than two, because they are
    structurally the same thing — a priced item, attached to a campaign, bought
    in some quantity, crediting paid carats. The only difference is that
    selectors additionally grant a ticket, which `product_type` records. This
    codebase already narrows on tag fields elsewhere (see `event_type` on the
    timeline union), so the frontend pattern is established.

    Real-money prices live in the database, never in code, so they can be
    corrected without a deploy when the store changes.
    """

    anniversary_event = models.ForeignKey(
        AnniversaryEvent,
        on_delete=models.CASCADE,
        related_name="products",
    )
    product_type = models.CharField(
        max_length=20,
        choices=PRODUCT_TYPE_CHOICES,
        default="carat_pack",
    )
    name = models.CharField(
        max_length=255,
        help_text="As shown to the user, e.g. '7500 Carat Pack' or '$21 Uma Selector'.",
    )
    # Decimal rather than an integer number of dollars: every price in the sheet
    # today is whole dollars, but store tiers priced at .99 are common enough
    # that hardcoding whole dollars would buy a migration later for nothing.
    usd_cost = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    paid_carat_amount = models.IntegerField(
        default=0,
        help_text="Paid carats granted per unit, before any webstore bonus.",
    )
    # The webstore sells the same packs with a bonus (1.2x on the largest pack,
    # 1.1x on the rest). Stored per product because the rate differs by pack, and
    # applied only when the user has enabled the webstore toggle.
    webstore_multiplier = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=1.00,
        help_text="Carat multiplier when bought via the webstore. 1.00 = no bonus.",
    )
    max_quantity = models.PositiveIntegerField(
        default=1,
        help_text="Purchase limit for this campaign. Selectors are normally 1.",
    )
    # Per-product override of the campaign's cutoff — not every selector in a
    # campaign is equally permissive. Null falls back to the campaign's own
    # jp_cutoff_date; see `effective_jp_cutoff_date`.
    jp_cutoff_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="JP cutoff date override",
        help_text="Leave blank to inherit the campaign's cutoff.",
    )
    # The campaign part this goes on sale with. Blank: the campaign's main part
    # (Part 2 of an anniversary, predictions._main_part), which is every product
    # but the anniversaries' 1500 Carat Packs (Part 3, Daptrius 2026-11-12). A
    # part number rather than a date because campaign dates are predicted and
    # move with the timeline; a part follows them. The purchase's paid carats
    # credit at that part's start (AnniversaryEventSerializer, `opens_at`).
    opens_with_part = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="opens with part",
        help_text=(
            "Leave blank: it goes on sale with the campaign's main part (Part 2 of "
            "an anniversary). Set 3 for a product that only goes on sale with Part 3."
        ),
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "campaign product"
        verbose_name_plural = "campaign products"

    @property
    def effective_jp_cutoff_date(self):
        """This product's cutoff, falling back to its campaign's.

        Returns None when neither is set, which means "no restriction" — not
        "restricted to nothing". Callers must treat None as permissive.
        """
        if self.jp_cutoff_date:
            return self.jp_cutoff_date
        return self.anniversary_event.jp_cutoff_date

    @property
    def grants_selector(self):
        return self.product_type in ("uma_selector", "support_selector")

    def __str__(self):
        return f"{self.anniversary_event.name} - {self.name}"
