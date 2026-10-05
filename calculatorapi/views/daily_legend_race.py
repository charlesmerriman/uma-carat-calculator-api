from rest_framework import serializers

from calculatorapi.models import DailyLegendRaceRelease, Uma

from .mixins import StartInstantDateMixin


class DailyLegendRaceUmaSerializer(serializers.ModelSerializer):
    """Just what a tile on the legend races page needs.

    Not UmaSerializer: that one carries the selector gates, the overlay text
    and editors' comments, and needs the first-JP-date map in context. None of
    it means anything here.

    `rarity` is resolved on the server (blank counts as ★3, the rule
    Uma.is_three_star already applies), so the client groups by a plain 1/2/3
    and never repeats the rule.
    """

    rarity = serializers.SerializerMethodField()

    class Meta:
        model = Uma
        fields = ("id", "name", "image", "rarity")

    def get_rarity(self, obj):
        return obj.rarity or 3


class DailyLegendRaceReleaseSerializer(StartInstantDateMixin, serializers.ModelSerializer):
    """A batch of umas joining the daily legend races.

    StartInstantDateMixin, same as ScenarioSerializer: a release has a start
    and no end, so `end_date` is absent from the payload entirely.

    `banner_timeline` is a bare id, as on ScenarioSerializer: the Timeline
    shows a release as a note on the card of the banner it arrives with, and
    already holds every banner in banner_timeline_data. `offset_days` stays
    off the wire; the start date already includes it.
    """

    umas = serializers.SerializerMethodField()

    class Meta:
        model = DailyLegendRaceRelease
        fields = (
            "id",
            "name",
            "image",
            "banner_timeline",
            "start_date",
            "is_predicted",
            "applied_offset_days",
            "umas",
        )

    def get_umas(self, obj):
        # BARE .all() on purpose, as in BannerUmaNestedSerializer.get_umas: the
        # select_related("uma") lives in the view's Prefetch(), and calling it
        # here would build a fresh queryset that bypasses the prefetch cache.
        # Ordered in Python for the same reason (order_by() would re-query).
        umas = sorted(
            (link.uma for link in obj.uma_links.all()),
            key=lambda uma: (-(uma.rarity or 3), uma.name.casefold()),
        )
        return DailyLegendRaceUmaSerializer(umas, many=True, context=self.context).data
