from django.db import models
from .banner_support import BannerSupport
from .support_card import SupportCard
from .umas_on_uma_banner import rate_override_field


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

    class Meta:
        # Shown as the inline section title on the support card banner edit page.
        verbose_name = "support card on banner"
        verbose_name_plural = "support cards on banner"

    def __str__(self):
        return f"{self.support_card.name} on {self.banner_support.name}"
