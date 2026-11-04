"""
The course an event runs on, as the client draws it (models/race_course.py).

Sent nested on each Champions Meeting and League of Heroes, on
/calculator-data and the LoH list: the corners, straights and slopes in
metres, 0.5 to 1.3 KB an event, about 2 KB gzipped for every event together.
Never the shape (the guide payload carries that, notes/course-map-plan.md
PR 3), the import's flags or the scraper's source record.
"""

from rest_framework import serializers

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
