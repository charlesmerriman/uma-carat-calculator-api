"""Free card events on /calculator-data (`story_event_data`), for the Timeline:
story events, main story chapters, anniversary missions and special events,
told apart by `kind`.

Display only: the income ledger never reads these (it reads GameEvent), and
the cards and umas here never reach selector eligibility, which is built from
banner links alone (eligibility.build_first_jp_date_maps). See
notes/story-events-plan.md and notes/story-events-plan-2.md.

A reward is a support card (`cards`) or an uma (`umas`), in two lists rather
than one list with a type: every reader of `cards` written before umas existed
(the Timeline row, search, the PvP release check) keeps reading support cards
only, and a reader that wants umas asks for them by name.
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
    # FramedCard's inputs, as on a banner's cards (SupportCardSerializer).
    image_borderless = serializers.ImageField(source="support_card.image_borderless")
    card_type = serializers.CharField(source="support_card.card_type")
    rarity = serializers.IntegerField(source="support_card.rarity")
    purpose = serializers.CharField(source="support_card.purpose")
    # The key of the card's Encyclopedia page, as on a banner's cards. Null for
    # a placeholder, which has no page.
    game_id = serializers.SerializerMethodField()

    class Meta:
        model = StoryEventCard
        fields = ("id", "name", "title", "image", "image_borderless", "card_type", "rarity", "purpose", "game_id",
                  "must_get", "note")

    def get_game_id(self, obj):
        card = obj.support_card
        return None if card.is_placeholder else card.game_id


class StoryEventUmaSerializer(serializers.ModelSerializer):
    """One free uma (Special Week (Commander) from a main story chapter), with
    what a framed uma tile draws and the editors' mark.

    Not UmaSerializer, for the same reason the card above is not
    SupportCardSerializer: no `first_jp_date`, no selector gates, no
    `admin_comments`. `title` is Global's only (no UmaTL, which needs a credit).
    """

    id = serializers.IntegerField(source="uma.id")
    name = serializers.CharField(source="uma.name")
    title = serializers.CharField(source="uma.title")
    image = serializers.ImageField(source="uma.image")
    portrait = serializers.ImageField(source="uma.portrait")
    portrait_layers = serializers.ReadOnlyField(source="uma.portrait_layers")
    rarity = serializers.IntegerField(source="uma.rarity")
    purpose = serializers.CharField(source="uma.purpose")
    game_id = serializers.IntegerField(source="uma.game_id")

    class Meta:
        model = StoryEventCard
        fields = ("id", "name", "title", "image", "portrait", "portrait_layers", "rarity",
                  "purpose", "game_id", "must_get", "note")


class StoryEventSerializer(StoryEventDateMixin, serializers.ModelSerializer):
    """An event with its kind, its resolved dates and its free rewards.

    `name` is the name a player reads: Global's, else the editors' working
    name, else "" (the client then says the kind's label, "Story event" or
    "Main story"). The Japanese name is never sent. `end_date` is null on a
    main story chapter, which never closes; `claim_end_date` is set on story
    events only (predictions.story_event_dates).
    """

    name = serializers.CharField(source="shown_name")
    cards = serializers.SerializerMethodField()
    umas = serializers.SerializerMethodField()

    class Meta:
        model = StoryEvent
        fields = (
            "id", "kind", "game_id", "name", "image", "banner_timeline",
            "start_date", "end_date", "claim_end_date", "is_predicted", "applied_offset_days",
            "carat_amount", "uma_ticket_amount", "support_ticket_amount",
            "ssr_shard_amount", "sr_shard_amount",
            "cards", "umas",
        )

    # BARE .all() in both: the view prefetches `cards` with the card and the uma
    # joined. Any .select_related() or .order_by() here would build a new
    # queryset and ask once per event (the same trap as
    # BannerSupportNestedSerializer). The split happens in Python.
    def get_cards(self, obj):
        rows = [row for row in obj.cards.all() if row.support_card_id is not None]
        return StoryEventCardSerializer(rows, many=True).data

    def get_umas(self, obj):
        rows = [row for row in obj.cards.all() if row.uma_id is not None]
        return StoryEventUmaSerializer(rows, many=True).data


def story_event_queryset():
    """Every event the Timeline can place (it has a banner), with its rewards in
    their order, the card or uma joined: two queries in all."""
    return StoryEvent.objects.filter(banner_timeline__isnull=False).prefetch_related(
        Prefetch(
            "cards",
            queryset=StoryEventCard.objects.select_related("support_card", "uma").order_by(
                "order", "id"),
        )
    )
