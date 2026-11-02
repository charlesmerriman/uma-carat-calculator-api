from rest_framework import serializers
from calculatorapi.models import ChampionsMeeting
from .mixins import EffectiveDateMixin, EventTypeMixin
from .race_course import course_payload


class ChampionsMeetingSerializer(EventTypeMixin, EffectiveDateMixin, serializers.ModelSerializer):
    # Whether the event has a published PvP guide (views/pvp_guide.py).
    has_guide = serializers.SerializerMethodField()
    # Who may read that guide, and whether My team and the Gallery open with it
    # (annotate_has_guide; guide-visibility-plan.md).
    guide_visibility = serializers.SerializerMethodField()
    guide_tools_public = serializers.SerializerMethodField()
    # The course the map draws, or null (views/race_course.py).
    course = serializers.SerializerMethodField()
    event_type_value = "champions_meeting"

    class Meta:
        model = ChampionsMeeting
        fields = (
            # cm_number was declared on the frontend's ChampionsMeeting type but
            # never actually sent; added here so the two agree.
            "id", "name", "event_type", "cm_number",
            "start_date", "end_date", "is_predicted",
            "jp_start_date", "jp_end_date", "global_start_date", "global_end_date",
            "schedule_offset_days", "applied_offset_days",
            "image",
            "track", "surface_type", "distance", "length",
            "track_condition", "season", "weather", "direction",
            "course",
            "has_guide", "guide_visibility", "guide_tools_public",
        )

    def get_has_guide(self, obj):
        # Annotated by views.pvp_guide.annotate_has_guide where the queryset is
        # built; an unannotated row has no published guide as far as we know.
        return getattr(obj, "has_guide", False)

    def get_guide_visibility(self, obj):
        return getattr(obj, "guide_visibility", None)

    def get_guide_tools_public(self, obj):
        return bool(getattr(obj, "guide_tools_public", False))

    def get_course(self, obj):
        return course_payload(obj)
