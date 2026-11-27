from rest_framework import serializers
from calculatorapi.models import BannerSupport
from .banner_rate_overrides import rate_overrides
from .banner_timeline import BannerTimelineSerializer, cached_rate_ups


class BannerSupportSerializer(serializers.ModelSerializer):
    banner_timeline = BannerTimelineSerializer()
    # The SSRs (what a planner row draws and a selector reads), and the SR and
    # R rate-ups beside them with their Timeline `display` (the odds use all of
    # them). Callers prefetch `supportsonsupportbanner_set` with its card.
    support_cards = serializers.SerializerMethodField()
    other_rate_ups = serializers.SerializerMethodField()
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
            "other_rate_ups",
            "rate_up_picks",
            "rate_overrides",
        )

    def get_support_cards(self, obj):
        return cached_rate_ups(obj, self.context)[0]

    def get_other_rate_ups(self, obj):
        return cached_rate_ups(obj, self.context)[1]

    def get_rate_overrides(self, obj):
        return rate_overrides(obj.supportsonsupportbanner_set, "support_card_id")
