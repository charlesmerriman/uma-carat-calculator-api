from django.db import models

from .banner_timeline import BannerTimeline
from .uma import Uma


class DailyLegendRaceRelease(models.Model):
    """A batch of umas joining the Daily Legend Races, e.g. "2nd Anniversary".

    Once an uma is in the daily races a player can run one race a day for her,
    forever, each giving a fixed number of pieces. Every uma has her own daily
    race, so several can be ground at once. Before that, an uma's only source
    was her original limited Legend Race event (a few days, three races a day).

    A SINGLE dated instant, like Scenario: the batch arrives and then stays.
    Its date is borrowed from the banner it arrives with (usually the
    anniversary's last part), plus a signed `offset_days` for the batches that
    land a day or three after that banner starts. Dates resolve in
    predictions.daily_legend_race_effective_dates against the shared
    BannerTimeline map, so a prediction or a schedule offset on the banner
    moves the release with it.

    Linked to a BANNER, not to an AnniversaryEvent. A batch arrives with one
    specific part rather than when the campaign opens (the 2nd Anniversary
    campaign opens weeks before its races arrive), and the 1.5th batch arrived
    with a banner that is not one of its campaign's parts at all.

    banner_timeline is nullable + SET_NULL, same reasoning as Scenario: the
    batch's umas stay real content if the banner is deleted. An unlinked
    release is undated and stays off the site, which is also how an editor
    enters a future batch before the timeline reaches it.

    Rarity is never stored here. The page groups umas by Uma.rarity.
    """

    name = models.CharField(
        max_length=255,
        help_text='What the batch is called on the site, e.g. "2nd Anniversary".',
    )
    # Optional, like Scenario.image: without one the Timeline card collapses
    # to a chip, the name and the date.
    image = models.ImageField(upload_to="daily_legend_races/", null=True, blank=True)
    banner_timeline = models.ForeignKey(
        BannerTimeline,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_legend_race_releases",
        help_text=(
            "The banner this batch arrives with, usually the anniversary's last "
            "part. Its start date is the batch's date. Leave it blank to keep "
            "the batch off the site until the timeline reaches it."
        ),
    )
    # Signed: a batch can land before its banner as well as after it.
    offset_days = models.SmallIntegerField(
        default=0,
        help_text=(
            "Days to add to the banner's start date. Use a minus sign to "
            "subtract. 0 means the same day as the banner."
        ),
    )
    umas = models.ManyToManyField(
        Uma,
        through="DailyLegendRaceUma",
        related_name="daily_legend_race_releases",
    )

    class Meta:
        verbose_name = "daily legend race release"
        verbose_name_plural = "daily legend race releases"

    def __str__(self):
        return str(self.name)


class DailyLegendRaceUma(models.Model):
    """One uma in one release. Edited as an inline on the release's page."""

    release = models.ForeignKey(
        DailyLegendRaceRelease,
        on_delete=models.CASCADE,
        related_name="uma_links",
    )
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "uma"
        verbose_name_plural = "umas"
        constraints = [
            # An uma joins the daily races once. A constraint rather than a form
            # check, so the shell and any future script cannot get round it.
            # The admin inline still reports a clash as a form error, because
            # Django validates a UniqueConstraint on save.
            models.UniqueConstraint(
                fields=["uma"],
                name="one_daily_legend_race_per_uma",
                violation_error_message=(
                    "This uma is already in another daily legend race release."
                ),
            ),
        ]

    def __str__(self):
        return f"{self.uma} in {self.release}"
