from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from .banner_uma import BannerUma
from .uma import Uma

# Shared with SupportsOnSupportBanner so the two inlines read the same.
RATE_OVERRIDE_HELP = (
    "Only when this card's rate breaks the usual rule, as a decimal: 0.005 for 0.5%. "
    "Leave blank and the calculator works it out from rarity and how many cards share "
    "the rate-up. Copy the number from the in-game rates page."
)


def rate_override_field():
    return models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="rate override",
        help_text=RATE_OVERRIDE_HELP,
    )


class UmasOnUmaBanner(models.Model):
    banner_uma = models.ForeignKey(BannerUma, on_delete=models.CASCADE)
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE)
    recommendation = models.TextField(
        blank=True,
        null=True,
        help_text="Optional recommendation text for this uma on this banner.",
    )
    # Per card ON A BANNER, not per card: the same uma can be 0.75% on one
    # banner and 0.5% on another. Blank on nearly every row; the client's
    # rule covers them (frontend utils/rateUpRates.ts).
    rate_override = rate_override_field()

    class Meta:
        # Shown as the inline section title on the uma banner edit page.
        verbose_name = "uma on banner"
        verbose_name_plural = "umas on banner"

    def __str__(self):
        return f"{self.uma.name} on {self.banner_uma.name}"
