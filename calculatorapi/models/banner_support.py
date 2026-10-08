from django.db import models
from .banner_timeline import BannerTimeline
from .support_card import SupportCard


class BannerSupport(models.Model):
    banner_timeline = models.ForeignKey(BannerTimeline, on_delete=models.CASCADE, related_name="support_banners")
    name = models.CharField(max_length=255, null=False)
    support_cards = models.ManyToManyField(
        SupportCard, through="SupportsOnSupportBanner"
    )
    admin_comments = models.TextField(blank=True, null=True, help_text="Notes for editors.")
    free_pulls = models.IntegerField(
        default=0,
        help_text="Free pulls players get on this banner — the calculator counts these toward affordability.",
    )
    # Same flag as BannerUma.is_recommended -- see the note there for why it lives
    # per banner rather than on the shared BannerTimeline.
    is_recommended = models.BooleanField(
        default=False,
        verbose_name="recommended",
        help_text="Tick when this banner is exceptionally worth pulling on. Highlights it "
                  "on the Timeline and in the calculator's banner dropdown.",
    )
    # Same field as BannerUma.rate_up_picks -- see the note there.
    rate_up_picks = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="rate-up picks",
        help_text="Select banners only (\"10 Select 2\"): how many of the listed cards the "
                  "player picks to rate up. Leave blank when every listed card is a rate-up.",
    )

    class Meta:
        # Default would be "banner support / banner supports" — confusing for editors.
        verbose_name = "support card banner"
        verbose_name_plural = "support card banners"

    def __str__(self):
        return str(self.name)
