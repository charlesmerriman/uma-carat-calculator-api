"""
The course an event runs on, as the client draws it (models/race_course.py).

Sent nested on each Champions Meeting and League of Heroes, on
/calculator-data and the LoH list: the corners, straights and slopes in
metres, 0.5 to 1.3 KB an event, about 2 KB gzipped for every event together.
Never the shape (its own endpoint below, fetched when a map is drawn), the
import's flags or the scraper's source record.
"""

import json

from django.http import HttpResponse
from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import course_zones_cache
from calculatorapi.models import RaceCourse, Skill
from calculatorapi.skill_text import context_from_database
from calculatorapi.skill_verdicts import is_once_per_race, verdict_for
from calculatorapi.skill_zones import zones_for


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


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def race_course_skill_zones(request, course_id):  # pylint: disable=unused-argument
    """Where every global skill can fire on one course (skill_zones.py), and
    its verdict there (skill_verdicts.py).

    `{skill id: {kind, zones, styles, ...verdict fields}}`, one map per course,
    which the guide's skills and the veterans' layer both read
    (notes/course-map-plan.md, "The layer menu"; notes/course-map-plan-2.md).
    Public like the guide; a hidden map is a 404 like a missing one. Built
    once per course and cached (course_zones_cache.py).
    """
    course = RaceCourse.objects.filter(pk=course_id, show_map=True).only(
        "id", "distance", "track_id", "direction", "surface", "corners", "straights", "slopes",
    ).first()
    if course is None:
        return Response({"detail": "Not found."}, status=404)
    cached = course_zones_cache.read(course.id)
    if cached is None:
        context = context_from_database()
        zones = {}
        for skill_id, condition, effects in Skill.objects.on_global().values_list("id", "condition", "effects"):
            entry = zones_for(condition, course, once_per_race=is_once_per_race(effects))
            entry.update(verdict_for(condition, effects, course, entry, context))
            zones[str(skill_id)] = entry
        cached = json.dumps(zones, separators=(",", ":")).encode()
        course_zones_cache.store(course.id, cached)
    # Cached bytes, served as they are (views/skills.py does the same).
    return HttpResponse(cached, content_type="application/json")  # pylint: disable=http-response-with-content-type-json
