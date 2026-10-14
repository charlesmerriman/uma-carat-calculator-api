from django.db import models


class LeagueOfHeroes(models.Model):
    name = models.CharField(max_length=255)
    loh_number = models.IntegerField(
        default=0,
        verbose_name="league number",
        # Unlike ChampionsMeeting.cm_number this carries a default, because the
        # column was added to a table that already had rows — 0031 creates it,
        # 0032 backfills the existing rows from their names.
    )
    # JP server dates: always known well in advance (JP runs content first).
    # Nullable so historical rows migrated from the old schema (which only had
    # confirmed global dates) can be backfilled gradually in the admin.
    jp_start_date = models.DateTimeField(blank=True, null=True)
    jp_end_date = models.DateTimeField(blank=True, null=True)
    # Global server dates: only filled once the event is officially confirmed
    # (~1 month out). When null, the global dates are predicted from the JP
    # dates (see calculatorapi/predictions.py).
    global_start_date = models.DateTimeField(blank=True, null=True)
    global_end_date = models.DateTimeField(blank=True, null=True)
    # Manual correction to the JP-based prediction, for when global slips its
    # schedule. Pushes this row AND every dated row after it (banners, Champions
    # Meetings and League of Heroes alike) forward by this many days; offsets
    # stack. Ignored entirely once this row's global dates are confirmed — see
    # calculatorapi/predictions.py.
    schedule_offset_days = models.IntegerField(
        default=0,
        blank=True,
        verbose_name="schedule offset (days)",
        help_text="Days to push this and every later date forward. Leave at 0 unless "
                  "global has slipped its schedule. Ignored once global dates are confirmed.",
    )
    image = models.ImageField(upload_to="league_of_heroes/", blank=True, null=True)
    # Course details and stat recommendations, mirroring ChampionsMeeting field
    # for field so the two render through the same timeline card.
    #
    # These are entered by hand in the admin and are unknown until the event is
    # announced, so "unknown" is a sentinel rather than NULL: "TBD" for text and
    # 0 for the recommendations. That's the same convention championsMeetings
    # fixtures use, and the frontend already translates both into a "Not
    # announced" / "TBD" pending state (see isTrackDetailAvailable and
    # isRecommendationAvailable in components/timeline/RaceEventCard.tsx).
    # The defaults double as the migration's value for pre-existing rows.
    track = models.CharField(max_length=255, default="TBD")
    surface_type = models.CharField(max_length=255, default="TBD")
    distance = models.CharField(max_length=255, default="TBD")
    length = models.CharField(max_length=255, default="TBD")
    track_condition = models.CharField(max_length=255, default="TBD")
    season = models.CharField(max_length=255, default="TBD")
    weather = models.CharField(max_length=255, default="TBD")
    direction = models.CharField(max_length=255, default="TBD")
    # The five recommendations: see the note on ChampionsMeeting. Unused, and
    # dropped in the team-builder release.
    speed_recommendation = models.IntegerField(default=0)
    stamina_recommendation = models.IntegerField(default=0)
    power_recommendation = models.IntegerField(default=0)
    guts_recommendation = models.IntegerField(default=0)
    wit_recommendation = models.IntegerField(default=0)

    class Meta:
        # Fixes the auto-generated plural "league of heroess".
        verbose_name = "League of Heroes event"
        verbose_name_plural = "League of Heroes events"

    def __str__(self):
        return str(self.name)
