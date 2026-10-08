from rest_framework import serializers
from calculatorapi.models import BannerUma
from .banner_rate_overrides import rate_overrides
from .banner_timeline import BannerTimelineSerializer
from .uma import UmaSerializer


class BannerUmaSerializer(serializers.ModelSerializer):
    banner_timeline = BannerTimelineSerializer()
    umas = UmaSerializer(many=True, read_only=True)
    # {uma id: rate} for the umas whose rate breaks the rule. Callers prefetch
    # `umasonumabanner_set`. See banner_rate_overrides.py.
    rate_overrides = serializers.SerializerMethodField()

    class Meta:
        model = BannerUma
        fields = (
            "id",
            "banner_timeline",
            "name",
            "free_pulls",
            # Stars this banner's option in the planner's dropdown.
            "is_recommended",
            "admin_comments",
            "umas",
            "rate_up_picks",
            "rate_overrides",
        )

    def get_rate_overrides(self, obj):
        return rate_overrides(obj.umasonumabanner_set, "uma_id")
