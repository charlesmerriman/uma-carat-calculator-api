"""Story events on /calculator-data (`story_event_data`), for the Timeline.

Display only: the income ledger never reads these (it reads GameEvent), and
the cards here never reach selector eligibility, which is built from banner
links alone (eligibility.build_first_jp_date_maps). See
notes/story-events-plan.md.
"""

from django.db.models import Prefetch
from rest_framework import serializers

from calculatorapi.models import StoryEvent, StoryEventCard
from calculatorapi.views.mixins import StoryEventDateMixin


class StoryEventCardSerializer(serializers.ModelSerializer):
    """One free card, flattened: the card's own fields plus the editors' mark.

    Deliberately NOT SupportCardSerializer. That one carries `first_jp_date`,
    the selector key, and a card that only appears here must never look like a
    dated catalogue card to the client (the selectors build their catalogue
    from banners, and this keeps it that way by construction). It also leaves
    out `admin_comments`.

    `title` is Global's title only, never UmaTL's translation: a translated
    title must be credited wherever it shows, and the Timeline row has no
    credit line. A card Global hasn't released shows its name alone.
    """

    id = serializers.IntegerField(source="support_card.id")
    name = serializers.CharField(source="support_card.name")
    title = serializers.CharField(source="support_card.title")
    image = serializers.ImageField(source="support_card.image")
    rarity = serializers.IntegerField(source="support_card.rarity")
    purpose = serializers.CharField(source="support_card.purpose")
    # The key of the card's Encyclopedia page, as on a banner's cards. Null for
    # a placeholder, which has no page.
    game_id = serializers.SerializerMethodField()

    class Meta:
        model = StoryEventCard
        fields = ("id", "name", "title", "image", "rarity", "purpose", "game_id",
                  "must_get", "note")

    def get_game_id(self, obj):
        card = obj.support_card
        return None if card.is_placeholder else card.game_id


class StoryEventSerializer(StoryEventDateMixin, serializers.ModelSerializer):
    """A story event with its resolved dates and its free cards.

    `name` is the name a player reads: Global's, else the editors' working
    name, else "" (the client then says "Story event"). The Japanese name is
    never sent.
    """

    name = serializers.CharField(source="shown_name")
    cards = serializers.SerializerMethodField()

    class Meta:
        model = StoryEvent
        fields = (
            "id", "game_id", "name", "image", "banner_timeline",
            "start_date", "end_date", "claim_end_date", "is_predicted", "applied_offset_days",
            "carat_amount", "uma_ticket_amount", "support_ticket_amount",
            "ssr_shard_amount", "sr_shard_amount",
            "cards",
        )

    def get_cards(self, obj):
        # BARE .all(): the view prefetches `cards` with the card joined. Any
        # .select_related() or .order_by() here would build a new queryset and
        # ask once per event (the same trap as BannerSupportNestedSerializer).
        return StoryEventCardSerializer(obj.cards.all(), many=True).data


def story_event_queryset():
    """Every event the Timeline can place (it has a banner), with its cards in
    their order, the card joined: two queries in all."""
    return StoryEvent.objects.filter(banner_timeline__isnull=False).prefetch_related(
        Prefetch(
            "cards",
            queryset=StoryEventCard.objects.select_related("support_card").order_by("order", "id"),
        )
    )
