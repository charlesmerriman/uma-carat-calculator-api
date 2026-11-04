"""
A racecourse layout from the game's own files: the record the course map draws.

One row is one of the game's course sets (`race_course_set`: a racecourse, a
surface, a distance and a variant such as Kyoto's inner course), as Daptrius's
scraper read it from the client's course parameters. Everything the map shows
that is not stored here (the phases, the sections, the elevation profile) is
derived from these metres at render time, so there is one place a course can be
wrong. The plan: notes/course-map-plan.md.

WHY `region` IS HERE WHEN EVERY ROW IS JP
-----------------------------------------
Global ran its early events (before League of Heroes 3) on tracks JP has since
changed. No difference was found on the 16 courses those events use, so every
event links to the JP row for now (owner, 2026-11-02). If one does turn up, that
course gains a `global` row and the early events on it are re-linked; nothing
that reads a course has to ask which version applies, because the event's link
already names one row.

THE GAME'S NUMBERS, VERBATIM
----------------------------
Corners keep the game's number (counted back from the finish in fours, so the
final corner is 4; -1 is a normal corner the game numbers oddly), straights
their front type, slopes the raw value (percent times 10,000: 20000 is a 2%
climb). The import cleans only what it can prove wrong: a part running past the
finish is cut at the line, a straight is paired with the next end in race order
(the scraper's pairing broke three records), and corner 90 on course 10613 is
dropped (Daptrius: not a real corner). Each clean-up is written to `flags`.

`source_data` keeps the scraper's whole record, admin-only and never serialized,
so a later layer that needs a field nobody modelled (track width, a JP-era flag)
reads it from there instead of a re-import.
"""

from django.db import models


class RaceCourse(models.Model):
    class Region(models.TextChoices):
        JP = "jp", "JP"
        GLOBAL = "global", "Global"

    class Surface(models.TextChoices):
        TURF = "turf", "Turf"
        DIRT = "dirt", "Dirt"

    class Direction(models.TextChoices):
        # The game's turn codes: 1 right, 2 left, 4 straight.
        RIGHT = "right", "Right-handed"
        LEFT = "left", "Left-handed"
        STRAIGHT = "straight", "Straight"

    class Variant(models.TextChoices):
        # The game's `inout`: 1 the only course, 2 inner, 3 outer, 4 outer then
        # inner (Hanshin 3200 runs an outer loop, then an inner one).
        NONE = "", "None"
        INNER = "inner", "Inner"
        OUTER = "outer", "Outer"
        OUTER_INNER = "outer_inner", "Outer, then inner"

    class LaterLaps(models.TextChoices):
        INSIDE = "inside", "Inside lap one"
        OUTSIDE = "outside", "Outside lap one"

    region = models.CharField(
        max_length=8, choices=Region.choices, default=Region.JP,
        help_text="Whose game files this row came from. Every row is JP today.",
    )
    game_id = models.PositiveIntegerField(
        verbose_name="game course id",
        help_text="The game's course set id, e.g. 10701 for Chukyo turf 1200 m.",
    )
    racecourse = models.CharField(max_length=64, help_text="The racecourse's name, e.g. Chukyo.")
    track_id = models.PositiveIntegerField(
        help_text="The game's racecourse id (10007 is Chukyo), the number skill conditions use.",
    )
    surface = models.CharField(max_length=8, choices=Surface.choices)
    distance = models.PositiveIntegerField(help_text="Metres.")
    variant = models.CharField(max_length=16, choices=Variant.choices, blank=True, default="")
    direction = models.CharField(max_length=8, choices=Direction.choices)
    # The course itself, all in metres from the gate. A stretch in none of the
    # lists is a gap the game leaves, drawn as one, never filled in.
    corners = models.JSONField(default=list, blank=True, help_text="[{start, length, number}]")
    straights = models.JSONField(default=list, blank=True, help_text="[{start, length, front}]")
    slopes = models.JSONField(
        default=list, blank=True,
        help_text="[{start, length, slope}]; slope is percent times 10,000 (20000 is a 2% climb).",
    )
    surface_changes = models.JSONField(
        default=list, blank=True,
        help_text="[{start, surface}]: a dirt course that starts on turf reads turf at 0, dirt at 100.",
    )
    # The real shape, in screen axes: 1,001 [x, y] points, point i at i / 1000
    # of the distance, finish at the bottom (course_shapes.to_screen). Null for
    # a straight course or one with no path file.
    shape = models.JSONField(null=True, blank=True)
    path_source = models.CharField(
        max_length=64, blank=True,
        help_text="The game's path file the shape came from.",
    )
    # Where the name codes left two path files (17 JP courses, rail variants at
    # most 4.4 m apart), the other one, ready to swap in: {file name: shape}.
    path_candidates = models.JSONField(default=dict, blank=True)
    later_laps = models.CharField(
        max_length=8, choices=LaterLaps.choices, default=LaterLaps.INSIDE,
        help_text="On a course over one lap: whether later laps are drawn inside or outside lap one.",
    )
    flags = models.JSONField(
        default=list, blank=True,
        help_text="What the import cleaned up or could not: shown here, never on the site.",
    )
    show_map = models.BooleanField(
        default=True,
        help_text="Untick to hide this course's map everywhere, e.g. while its record is being checked.",
    )
    source_data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Race course"
        verbose_name_plural = "Race courses"
        ordering = ("racecourse", "surface", "distance", "variant")
        constraints = [
            # Both regions use the same course ids, so the id is unique only
            # together with whose files the row came from.
            models.UniqueConstraint(fields=("region", "game_id"), name="unique_race_course_per_region"),
        ]

    def __str__(self):
        variant = f" {self.get_variant_display().lower()}" if self.variant else ""
        return f"{self.racecourse} {self.surface}{variant} {self.distance} m ({self.get_region_display()})"
