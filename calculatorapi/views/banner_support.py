from rest_framework import serializers
from calculatorapi.models import BannerSupport
from .banner_rate_overrides import rate_overrides
from .banner_timeline import BannerTimelineSerializer
from .support_card import SupportCardSerializer


class BannerSupportSerializer(serializers.ModelSerializer):
    banner_timeline = BannerTimelineSerializer()
    support_cards = SupportCardSerializer(many=True, read_only=True)
    # {support card id: rate}; callers prefetch `supportsonsupportbanner_set`.
    rate_overrides = serializers.SerializerMethodField()

    class Meta:
        model = BannerSupport
        fields = (
            "id",
            "banner_timeline",
            "name",
            "free_pulls",
            # Stars this banner's option in the planner's dropdown.
            "is_recommended",
            "admin_comments",
            "support_cards",
            "rate_up_picks",
            "rate_overrides",
        )

    def get_rate_overrides(self, obj):
        return rate_overrides(obj.supportsonsupportbanner_set, "support_card_id")
