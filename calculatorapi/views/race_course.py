"""
The course an event runs on, as the client draws it (models/race_course.py).

Sent nested on each Champions Meeting and League of Heroes, on
/calculator-data and the LoH list: the corners, straights and slopes in
metres, 0.5 to 1.3 KB an event, about 2 KB gzipped for every event together.
Never the shape (its own endpoint below, fetched when a map is drawn), the
import's flags or the scraper's source record.
"""

from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi.models import RaceCourse


class RaceCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = RaceCourse
        fields = (
            "id", "region", "game_id", "racecourse", "surface", "distance", "variant",
            "direction", "corners", "straights", "slopes", "surface_changes", "later_laps",
        )


def course_payload(event):
    """The event's course for the wire, or None when it has none or its map is hidden.

    The queryset must select_related("course"), or this is a query per event.
    """
    course = event.course
    if course is None or not course.show_map:
        return None
    return RaceCourseSerializer(course).data


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def race_course_shape(request, course_id):  # pylint: disable=unused-argument
    """The course's real shape for the track view: `{"shape": [[x, y], ...]}`.

    About 10 KB, so it is fetched when a map is drawn (the guide's track view,
    the Timeline card's drawer) and never rides /calculator-data. The shape is
    game data, so it is public whoever may read the event's guide (owner,
    2026-11-03: the Timeline shows the real course map). `shape` is null for a
    course with no path file; a hidden map is a 404 like a missing course.
    """
    # A list, so a course without a path (shape None) and no course at all differ.
    found = list(RaceCourse.objects.filter(pk=course_id, show_map=True).values_list("shape", flat=True)[:1])
    if not found:
        return Response({"detail": "Not found."}, status=404)
    return Response({"shape": found[0]})
