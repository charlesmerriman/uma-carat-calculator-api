from rest_framework import serializers
from calculatorapi.models import SupportCard
from .mixins import FirstJpDateMixin


class SupportCardSerializer(FirstJpDateMixin, serializers.ModelSerializer):
    context_key = "support_first_jp_dates"

    class Meta:
        model = SupportCard
        fields = (
            "id",
            "name",
            "image",
            "admin_comments",
            # Public and rendered: the Timeline tile's hover overlay.
            "purpose",
            "first_jp_date",
            # The rate-up rule's input (R / SR / SSR as 1 / 2 / 3). Null for a
            # card with no game id, which the client reads as SSR.
            "rarity",
        )
