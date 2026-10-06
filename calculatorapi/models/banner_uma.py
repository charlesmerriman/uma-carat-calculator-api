from django.db import models
from .banner_timeline import BannerTimeline
from .uma import Uma


class BannerUma(models.Model):
    banner_timeline = models.ForeignKey(BannerTimeline, on_delete=models.CASCADE, related_name="uma_banners")
    name = models.CharField(max_length=255, null=False)
    umas = models.ManyToManyField(
        Uma, through="UmasOnUmaBanner"
    )
    admin_comments = models.TextField(blank=True, null=True, help_text="Notes for editors.")
    free_pulls = models.IntegerField(
        default=0,
        help_text="Free pulls players get on this banner — the calculator counts these toward affordability.",
    )
    # Editorial and presentation-only -- nothing in the projection reads it. It
    # gives the Timeline's "Featured Umamusume" panel its SSR treatment and stars
    # this banner in the planner's dropdown.
    #
    # Per banner, not per BannerTimeline, on purpose: the uma and support banners
    # sharing a window are pulled on independently, and a step-up points at a
    # campaign's timeline too -- a window-level flag would recommend all three.
    is_recommended = models.BooleanField(
        default=False,
        verbose_name="recommended",
        help_text="Tick when this banner is exceptionally worth pulling on. Highlights it "
                  "on the Timeline and in the calculator's banner dropdown.",
    )
    # Odds input, read by the client's rate-up rule (frontend utils/rateUpRates.ts).
    # A select banner lists every card the player COULD choose, but only the
    # ones they pick are rate-ups, so the rule must split the pool by the picks,
    # not by the list. Blank on every ordinary banner.
    rate_up_picks = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="rate-up picks",
        help_text="Select banners only (\"10 Select 2\"): how many of the listed cards the "
                  "player picks to rate up. Leave blank when every listed card is a rate-up.",
    )

    class Meta:
        # Default would be "banner uma / banner umas" — confusing for editors.
        verbose_name = "uma banner"
        verbose_name_plural = "uma banners"

    def __str__(self):
        return str(self.name)
