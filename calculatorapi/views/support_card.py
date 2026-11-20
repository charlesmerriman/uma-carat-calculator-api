from rest_framework import serializers
from calculatorapi.models import SupportCard
from .mixins import FirstJpDateMixin


class SupportCardSerializer(FirstJpDateMixin, serializers.ModelSerializer):
    context_key = "support_first_jp_dates"
    # The key of the card's Encyclopedia page (/app/cards/<game_id>), which the
    # Timeline's tiles link to. Null for a placeholder: its id is made up and it
    # has no page.
    game_id = serializers.SerializerMethodField()

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
            "game_id",
        )

    def get_game_id(self, obj):
        return None if obj.is_placeholder else obj.game_id
