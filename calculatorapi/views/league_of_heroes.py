from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework import serializers, permissions
from calculatorapi.models import LeagueOfHeroes
from .mixins import EffectiveDateMixin, EventTypeMixin
from .pvp_guide import annotate_has_guide


class LeagueOfHeroesSerializer(EventTypeMixin, EffectiveDateMixin, serializers.ModelSerializer):
    # Whether the event has a published PvP guide (views/pvp_guide.py).
    has_guide = serializers.SerializerMethodField()
    event_type_value = "league_of_heroes"

    class Meta:
        model = LeagueOfHeroes
        # Field-for-field identical to ChampionsMeetingSerializer apart from the
        # number's name — the two feed the same timeline card.
        fields = (
            "id", "name", "event_type", "loh_number",
            "start_date", "end_date", "is_predicted",
            "jp_start_date", "jp_end_date", "global_start_date", "global_end_date",
            "schedule_offset_days", "applied_offset_days",
            "image",
            "track", "surface_type", "distance", "length",
            "track_condition", "season", "weather", "direction",
            "has_guide",
        )

    def get_has_guide(self, obj):
        # Annotated by views.pvp_guide.annotate_has_guide where the queryset is
        # built; an unannotated row has no published guide as far as we know.
        return getattr(obj, "has_guide", False)


class LeagueOfHeroesViewSet(ViewSet):
    # Pure reference data — readable by guests (was inheriting the global
    # IsAuthenticated default).
    permission_classes = [permissions.AllowAny]

    def list(self, request):
        # Standalone route serves raw confirmed dates (mixin falls back to the
        # global_* fields with is_predicted=False); only /calculator-data injects
        # the prediction map. Order by global_start_date since the old start_date
        # column is gone.
        events = annotate_has_guide(LeagueOfHeroes.objects.all()).order_by("global_start_date")
        serializer = LeagueOfHeroesSerializer(events, many=True)
        return Response(serializer.data)
