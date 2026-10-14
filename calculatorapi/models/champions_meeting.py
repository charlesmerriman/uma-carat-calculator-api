from django.db import models

class ChampionsMeeting(models.Model):
    name = models.CharField(max_length=255, null=False)
    cm_number = models.IntegerField(verbose_name="meeting number")
    # JP server dates: always known well in advance (JP runs content first).
    # Nullable so historical rows migrated from the old schema (which only had
    # confirmed global dates) can be backfilled gradually in the admin.
    jp_start_date = models.DateTimeField(blank=True, null=True)
    jp_end_date = models.DateTimeField(blank=True, null=True)
    # Global server dates: only filled once the meeting is officially confirmed
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
    image = models.ImageField(upload_to="champions_meetings/", blank=True, null=True)
    track = models.CharField(max_length=255)
    surface_type = models.CharField(max_length=255)
    distance = models.CharField(max_length=255)
    length = models.CharField(max_length=255)
    track_condition = models.CharField(max_length=255)
    season = models.CharField(max_length=255)
    weather = models.CharField(max_length=255)
    direction = models.CharField(max_length=255)
    # Never rendered, and replaced by the PvP guide's stat rows
    # (models/pvp_guide.py, PvpStatConfig). Off the admin form and the API
    # since the guide shipped; the columns drop in the team-builder release once
    # production confirms they are all zero. The default keeps an admin add
    # working while the form no longer offers them.
    speed_recommendation = models.IntegerField(default=0)
    stamina_recommendation = models.IntegerField(default=0)
    power_recommendation = models.IntegerField(default=0)
    guts_recommendation = models.IntegerField(default=0)
    wit_recommendation = models.IntegerField(default=0)

    class Meta:
        # Proper-noun casing (default capfirst would render "Champions meetings").
        verbose_name = "Champions Meeting"
        verbose_name_plural = "Champions Meetings"

    def __str__(self):
        return str(self.name)
