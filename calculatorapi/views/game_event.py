from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework import serializers, status, permissions
from calculatorapi.models import GameEvent
from calculatorapi.predictions import (
    build_game_event_confirmed_date_map,
    effective_sort_key,
)
from calculatorapi.views.mixins import GameEventDateMixin


class GameEventSerializer(GameEventDateMixin, serializers.ModelSerializer):
    class Meta:
        model = GameEvent
        fields = (
            "id", "name", "image", "start_date", "end_date", "is_predicted",
            # GameEvent has no schedule_offset_days of its own — it inherits
            # whatever offset its linked banner ended up with.
            "applied_offset_days",
            "banner_timeline",
            "carat_amount", "carats_throughout",
            "support_ticket_amount", "uma_ticket_amount",
            "sr_shard_amount", "sr_crystal_amount",
            "ssr_shard_amount", "ssr_crystal_amount",
        )


class GameEventViewSet(ViewSet):
    """Game events as reference data, read-only by construction.

    Events are edited in the admin. The API's own create/update/destroy went
    on 2026-10-15 together with the staff token login that was their only
    key (see views/changelog.py for the same cut and the reason).
    """
    permission_classes = [permissions.AllowAny]

    def list(self, request):
        game_events = GameEvent.objects.select_related("banner_timeline").all()
        # Standalone route serves confirmed dates only (no prediction), same
        # convention as /leagueofheroes — prediction is reserved for /calculator-data.
        emap = build_game_event_confirmed_date_map(game_events)
        game_events = sorted(game_events, key=lambda ge: effective_sort_key(emap.get(ge.id)))
        serializer = GameEventSerializer(game_events, many=True, context={"effective_dates": emap})
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        try:
            game_event = GameEvent.objects.select_related("banner_timeline").get(pk=pk)
        except GameEvent.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        emap = build_game_event_confirmed_date_map([game_event])
        serializer = GameEventSerializer(game_event, context={"effective_dates": emap})
        return Response(serializer.data)
