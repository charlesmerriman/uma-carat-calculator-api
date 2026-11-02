"""
The course map's two admin buttons, both on the Race courses page, both
"preview first, apply on a second click" like the Patreon import.

"IMPORT COURSES" takes the bundle scripts/build_course_bundle.py builds from
Daptrius's zips: every course's record and a screen-ready path. It creates and
updates RaceCourse rows and never links an event. A re-import keeps what an
editor chose (a swapped path, a hidden map, the lap layout) and never deletes
a row: a course missing from the bundle is listed, not removed.

"LINK COURSES" links every Champions Meeting and League of Heroes to the course
the game says it runs on, read from scripts/data/jp_snapshot/race_links.json
(the extractor writes it from JP's race tables, which know every event global
will run). An event already linked to a different course keeps it unless the
editor ticks "Replace". The preview also lists every event whose typed course
details (the text the timeline card shows) disagree with the game, so an editor
can fix the text; it never rewrites the text itself.

Both are an editor's admin import in the sense of CLAUDE.md "Safety & data":
nothing here runs at deploy and nothing loads content on its own.
"""

import json
import re
from pathlib import Path

from django import forms
from django.conf import settings
from django.db import transaction

from .models import ChampionsMeeting, LeagueOfHeroes, RaceCourse

BUNDLE_FORMAT = 1
PATH_POINTS = 1001
RACE_LINKS_PATH = Path(settings.BASE_DIR) / "scripts" / "data" / "jp_snapshot" / "race_links.json"

# The game's race condition codes as the words the admin's text columns use.
# Read off all 66 linked events on 2026-11-02: every code agreed with what
# editors had typed, apart from the four rows this check exists to catch.
# 0 is the game's "random"; season 5 is the cherry-blossom spring.
SEASONS = {1: "Spring", 2: "Summer", 3: "Fall", 4: "Winter", 5: "Spring"}
WEATHERS = {0: "Random", 1: "Sunny", 2: "Cloudy", 3: "Rainy", 4: "Snowy"}
GROUNDS = {0: "Random", 1: "Firm", 2: "Good", 3: "Soft", 4: "Heavy"}
DIRECTION_WORDS = {"right": "Right", "left": "Left", "straight": "Straight"}

# The fields a re-import refreshes. `later_laps` and `show_map` are an
# editor's (show_map can only be turned OFF by an import), and the path follows
# the editor's choice when the bundle still has that file.
GAME_FIELDS = (
    "racecourse", "track_id", "surface", "distance", "variant", "direction",
    "corners", "straights", "slopes", "surface_changes", "flags", "source_data",
)


class CourseBundleImportForm(forms.Form):
    bundle = forms.FileField(
        label="Course bundle",
        help_text="The .json file scripts/build_course_bundle.py writes from Daptrius's Data folder.",
    )
    dry_run = forms.BooleanField(
        required=False, initial=True, label="Preview only (don't save)",
        help_text="Shows exactly what would change without writing anything.",
    )


class LinkCoursesForm(forms.Form):
    replace_different = forms.BooleanField(
        required=False, initial=False, label="Replace links that differ",
        help_text="An event already linked to a different course keeps it unless this is ticked.",
    )
    dry_run = forms.BooleanField(
        required=False, initial=True, label="Preview only (don't save)",
        help_text="Shows exactly what would change without writing anything.",
    )


# ── Import courses ───────────────────────────────────────────────────────────

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_parts(course, key, fields):
    parts = course.get(key)
    if not isinstance(parts, list):
        raise ValueError(f"{key} is not a list")
    for part in parts:
        if not isinstance(part, dict) or not all(check(part.get(name)) for name, check in fields.items()):
            raise ValueError(f"{key} has an entry that isn't {sorted(fields)}: {part!r}"[:200])


def _check_shape(shape, what):
    if shape is None:
        return
    if not isinstance(shape, list) or len(shape) != PATH_POINTS or not all(
        isinstance(p, list) and len(p) == 2 and _is_number(p[0]) and _is_number(p[1]) for p in shape
    ):
        raise ValueError(f"{what} is not {PATH_POINTS} [x, y] points")


def _check_course(course):
    """Refuse anything the bundle script would not have written."""
    if not isinstance(course, dict):
        raise ValueError("a course is not an object")
    try:
        _check_facts(course)
        _check_drawing(course)
    except ValueError as exc:
        raise ValueError(f"course {course.get('game_id')}: {exc}") from exc


def _check_facts(course):
    """The scalars and the course record."""
    for key in ("game_id", "track_id", "distance"):
        if not isinstance(course.get(key), int) or course[key] <= 0:
            raise ValueError(f"{key} is not a positive whole number")
    for key in ("racecourse", "path_source"):
        if not isinstance(course.get(key), str):
            raise ValueError(f"{key} is not text")
    for key, choices in (("surface", RaceCourse.Surface), ("variant", RaceCourse.Variant),
                         ("direction", RaceCourse.Direction)):
        if course.get(key) not in choices.values:
            raise ValueError(f"{key} {course.get(key)!r} is not one of {list(choices.values)}")
    _check_parts(course, "corners", {"start": _is_number, "length": _is_number,
                                     "number": lambda v: isinstance(v, int)})
    _check_parts(course, "straights", {"start": _is_number, "length": _is_number,
                                       "front": lambda v: isinstance(v, int)})
    _check_parts(course, "slopes", {"start": _is_number, "length": _is_number,
                                    "slope": lambda v: isinstance(v, int)})
    _check_parts(course, "surface_changes", {"start": _is_number,
                                             "surface": lambda v: v in RaceCourse.Surface.values})


def _check_drawing(course):
    """The shapes, the flags and the switches."""
    _check_shape(course.get("shape"), "shape")
    if not isinstance(course.get("path_candidates"), dict):
        raise ValueError("path_candidates is not an object")
    for name, shape in course["path_candidates"].items():
        _check_shape(shape, f"path candidate {name}")
    if not isinstance(course.get("flags"), list) or not all(isinstance(f, str) for f in course["flags"]):
        raise ValueError("flags is not a list of text")
    if not isinstance(course.get("show_map"), bool):
        raise ValueError("show_map is not true or false")
    if not isinstance(course.get("source_data"), dict):
        raise ValueError("source_data is not an object")


def parse_course_bundle(uploaded_file):
    """Read and check a bundle. Returns (region, courses).

    Raises ValueError with a message an editor can act on.
    """
    try:
        bundle = json.loads(uploaded_file.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("That isn't a course bundle: it doesn't read as JSON.") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise ValueError(
            "That isn't a course bundle this page knows (expected format "
            f"{BUNDLE_FORMAT}). Build it with scripts/build_course_bundle.py."
        )
    region = bundle.get("region")
    if region not in RaceCourse.Region.values:
        raise ValueError(f"The bundle's region {region!r} isn't one of {list(RaceCourse.Region.values)}.")
    courses = bundle.get("courses")
    if not isinstance(courses, list) or not courses:
        raise ValueError("The bundle holds no courses.")
    for course in courses:
        _check_course(course)
    if len({c["game_id"] for c in courses}) != len(courses):
        raise ValueError("The bundle lists one course twice.")
    return region, courses


def _course_label(course, region):
    return f"{course['racecourse'] or 'Track ' + str(course['track_id'])} {course['surface']} " \
           f"{course['distance']} m ({course['game_id']}, {region})"


def _new_values(course, existing, summary, label):
    """The fields a bundle course sets on an existing row."""
    values = {field: course[field] for field in GAME_FIELDS}
    if not course["racecourse"]:
        # No English name in the bundle: keep whatever an editor typed.
        values.pop("racecourse")
    paths = dict(course["path_candidates"])
    if course["path_source"]:
        paths[course["path_source"]] = course["shape"]
    chosen = course["path_source"]
    if existing.path_source in paths and existing.path_source != chosen:
        chosen = existing.path_source
        summary["kept_choices"].append(label)
    values["path_source"] = chosen
    values["shape"] = paths.get(chosen) if chosen else None
    values["path_candidates"] = {name: shape for name, shape in paths.items() if name != chosen}
    if not course["show_map"] and existing.show_map:
        values["show_map"] = False
        summary["hidden_now"].append(label)
    return values


def apply_course_bundle(region, courses, dry_run=False):
    """Create and update one region's RaceCourse rows from a checked bundle.

    A preview runs the same writes and rolls them back, like the Patreon
    import: there is no separate "what if" path to drift from a real run.
    """
    summary = {"created": [], "updated": [], "unchanged": 0, "kept_choices": [],
               "hidden_now": [], "hidden": [], "flagged": [], "not_in_bundle": []}
    with transaction.atomic():
        existing = {row.game_id: row for row in RaceCourse.objects.filter(region=region)}
        for course in sorted(courses, key=lambda c: c["game_id"]):
            label = _course_label(course, region)
            if course["flags"]:
                summary["flagged"].append({"course": label, "flags": course["flags"]})
            if not course["show_map"]:
                summary["hidden"].append(label)
            row = existing.get(course["game_id"])
            if row is None:
                summary["created"].append(label)
                RaceCourse.objects.create(
                    region=region, game_id=course["game_id"],
                    **{field: course[field] for field in GAME_FIELDS if field != "racecourse"},
                    racecourse=course["racecourse"] or f"Track {course['track_id']}",
                    shape=course["shape"], path_source=course["path_source"],
                    path_candidates=course["path_candidates"], show_map=course["show_map"],
                )
                continue
            values = _new_values(course, row, summary, label)
            if all(getattr(row, field) == value for field, value in values.items()):
                summary["unchanged"] += 1
                continue
            summary["updated"].append(label)
            for field, value in values.items():
                setattr(row, field, value)
            row.save()
        in_bundle = {c["game_id"] for c in courses}
        summary["not_in_bundle"] = [str(row) for game_id, row in sorted(existing.items())
                                    if game_id not in in_bundle]
        if dry_run:
            transaction.set_rollback(True)
    return summary


# ── Link courses ─────────────────────────────────────────────────────────────

def load_race_links(path=None):
    """The JP snapshot's race links. Raises ValueError if the file is missing."""
    path = Path(path or RACE_LINKS_PATH)
    if not path.is_file():
        raise ValueError(
            f"No race links at {path.name}: refresh the JP snapshot "
            "(scripts/extract_master_snapshot.py --region jp) first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def distance_band(metres):
    """The game's distance band; matches every linked event's typed band (2026-11-02)."""
    if metres <= 1400:
        return "Sprint"
    if metres <= 1800:
        return "Mile"
    if metres <= 2400:
        return "Medium"
    return "Long"


def _letters(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold())


def typed_disagreements(event, course, link):
    """Each typed course detail on the event that disagrees with the game.

    Returns [(field label, typed, game)]. The text stays the editor's to fix.
    """
    length = re.sub(r"\D", "", event.length or "")
    checks = (
        ("Track", event.track, course.racecourse, _letters(event.track) == _letters(course.racecourse)),
        ("Surface type", event.surface_type, course.get_surface_display(),
         _letters(event.surface_type) == course.surface),
        ("Distance", event.distance, distance_band(course.distance),
         _letters(event.distance) == _letters(distance_band(course.distance))),
        ("Length", event.length, str(course.distance), length == str(course.distance)),
        ("Direction", event.direction, DIRECTION_WORDS[course.direction],
         _letters(event.direction) == course.direction),
        ("Track condition", event.track_condition, GROUNDS.get(link["ground"], "?"),
         _letters(event.track_condition) == _letters(GROUNDS.get(link["ground"], ""))),
        ("Season", event.season, SEASONS.get(link["season"], "?"),
         _letters(event.season) == _letters(SEASONS.get(link["season"], ""))),
        ("Weather", event.weather, WEATHERS.get(link["weather"], "?"),
         _letters(event.weather) == _letters(WEATHERS.get(link["weather"], ""))),
    )
    return [(label, typed, game) for label, typed, game, same in checks if not same]


EVENT_KINDS = (
    # (label, model, number field, race_links key)
    ("CM", ChampionsMeeting, "cm_number", "champions"),
    ("LoH", LeagueOfHeroes, "loh_number", "heroes"),
)


def plan_course_links(links):
    """Every event, what the game links it to, and what it would take."""
    courses = {row.game_id: row for row in RaceCourse.objects.filter(region=RaceCourse.Region.JP)}
    rows = []
    for kind, model, number_field, key in EVENT_KINDS:
        for event in model.objects.select_related("course").order_by(number_field):
            number = getattr(event, number_field)
            row = {"event": event, "label": f"{kind} {number}, {event.name}", "course": None,
                   "status": "", "reason": "", "disagreements": []}
            rows.append(row)
            link = links.get(key, {}).get(str(number))
            if link is None:
                row.update(status="unlinked", reason="not in the JP snapshot yet")
                continue
            if link.get("course") is None:
                row.update(status="unlinked", reason=(
                    "the game runs it on several courses "
                    f"({', '.join(str(c) for c in link.get('courses', []))}): link it by hand"))
                continue
            course = courses.get(link["course"])
            if course is None:
                row.update(status="unlinked", reason=f"course {link['course']} isn't imported")
                continue
            row["course"] = course
            if event.course_id == course.id:
                row["status"] = "already"
            elif event.course_id:
                row["status"] = "different"
            else:
                row["status"] = "link"
            row["disagreements"] = typed_disagreements(event, course, link)
    return rows


def apply_course_links(rows, replace_different=False, dry_run=False):
    """Link the planned events; a preview writes and rolls back, as above."""
    summary = {"linked": [], "replaced": [], "kept_different": [], "already": 0,
               "unlinked": [], "hidden": [], "disagreements": []}
    with transaction.atomic():
        for row in rows:
            event, course, label = row["event"], row["course"], row["label"]
            if row["disagreements"]:
                summary["disagreements"].append({"event": label, "fields": row["disagreements"]})
            if row["status"] == "unlinked":
                summary["unlinked"].append(f"{label}: {row['reason']}")
                continue
            if not course.show_map:
                summary["hidden"].append(f"{label}: {course}")
            if row["status"] == "already":
                summary["already"] += 1
                continue
            if row["status"] == "different" and not replace_different:
                summary["kept_different"].append(f"{label}: keeps {event.course}, the game says {course}")
                continue
            summary["replaced" if row["status"] == "different" else "linked"].append(f"{label}: {course}")
            event.course = course
            event.save(update_fields=["course"])
        if dry_run:
            transaction.set_rollback(True)
    return summary
