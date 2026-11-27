from django.core.exceptions import ValidationError
from django.db import models
from .banner_support import BannerSupport
from .support_card import SupportCard, SupportCardRarity
from .umas_on_uma_banner import rate_override_field


class TimelineDisplay(models.TextChoices):
    """How an SR or R rate-up appears on the Timeline (notes/banner-sr-r-plan.md).

    The Timeline only: the odds, the Encyclopedia and the PvP tools use every
    rate-up whatever this says. An SSR ignores it and is always drawn.
    `AUTO` is resolved when the payload is built, never stored as its answer
    (calculatorapi/banner_rate_ups.py), so an SSR an editor adds later still
    changes what an SR on the same banner does.
    """
    AUTO = "auto", "Automatic"
    NAMED = "named", "Listed under the card"
    PICTURE = "picture", "Picture (SR only)"
    HIDDEN = "hidden", "Hidden"


class SupportsOnSupportBanner(models.Model):
    banner_support = models.ForeignKey(BannerSupport, on_delete=models.CASCADE)
    support_card = models.ForeignKey(SupportCard, on_delete=models.CASCADE)
    recommendation = models.TextField(
        blank=True,
        null=True,
        help_text="Optional recommendation text for this card on this banner.",
    )
    # See UmasOnUmaBanner.rate_override.
    rate_override = rate_override_field()
    # Per link, not per card: the same SR can be worth a picture on one banner
    # and only a line on another. The deploy's rate-up import writes AUTO.
    timeline_display = models.CharField(
        "on the Timeline",
        max_length=8,
        choices=TimelineDisplay.choices,
        default=TimelineDisplay.AUTO,
        help_text=(
            "SR and R cards only; an SSR is always drawn. Automatic: an R is listed under "
            "the banner card, and an SR gets its picture when it is the banner's only SR "
            "beside a single SSR, and is listed otherwise. Hidden only leaves the card off "
            "the Timeline: the odds and the Encyclopedia still use it."
        ),
    )

    class Meta:
        # Shown as the inline section title on the support card banner edit page.
        verbose_name = "support card on banner"
        verbose_name_plural = "support cards on banner"

    def __str__(self):
        return f"{self.support_card.name} on {self.banner_support.name}"

    def clean(self):
        # An R is never drawn (the owner's rule, 2026-11-20); its picture
        # setting would read as "listed" anyway, so the form says so instead.
        if (
            self.timeline_display == TimelineDisplay.PICTURE
            and self.support_card_id is not None
            and self.support_card.rarity == SupportCardRarity.R
        ):
            raise ValidationError({"timeline_display": "An R card is never drawn. Pick Listed or Hidden."})
