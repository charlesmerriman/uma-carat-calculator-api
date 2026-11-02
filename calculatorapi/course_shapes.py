"""
Daptrius's scraped course records and the game's path files, turned into
RaceCourse fields. Pure functions: no database, no files.

Two callers. scripts/build_course_bundle.py runs them locally over Daptrius's
zips and writes the compact bundle an editor uploads; the tests run them on
small fixtures. The admin import (admin_course_import.py) only reads the
finished bundle, so nothing here ever runs on the server's copy of the raw
files, which are ~50 MB and never leave this machine.

WHAT GETS CLEANED, AND WHY EACH IS SAFE
---------------------------------------
Everything else is the game's numbers verbatim. Each clean-up adds a line to
the course's `flags`, so the admin shows what was touched.

* Straights are re-paired in race order. The game marks a straight with a
  start event and an end event. The scraper paired a start with an end of the
  same "front type", which broke the three records whose types differ:
  Longchamp 2400 lost the 1617 to 1867 m straight before its final straight
  (Daptrius, 2026-11-02: "1617-1867 is a real straight"), and Nakayama 1800
  (10509) got one straight running through its corners 3 and 4. Pairing each
  start with the next end gives exactly those and changes no other record
  (all 142 checked, 2026-11-02).
* Corner 90 (Tokyo 2400, 10613, from 2348 m for 500 m) is dropped. Daptrius:
  "It is not a real corner, do not ask me why its like that."
* A part running past the finish is cut at the line (owner, 2026-11-02:
  trim for now; drawing the data exactly as given may come later).

THE PATH
--------
A path file holds 1,001 points, point i at i / 1000 of the race distance,
in the game's top-down axes. With z pointing up, right-handed courses run
clockwise. SVG's y points DOWN, so drawing (x, z) as it is mirrors every
course; `to_screen` flips z first, then turns the course so its home straight
runs along the bottom (Daptrius: "The visual should always have the finish
line at the bottom"). The units stay metres, so one scale factor keeps the
course's proportions; the client fits it with a viewBox.
"""

import math
import re

# The game's codes, from race_course_set.
SURFACES = {1: "turf", 2: "dirt"}
DIRECTIONS = {1: "right", 2: "left", 4: "straight"}
# `inout`: 1 the only course, 2 inner, 3 outer, 4 outer then inner.
VARIANTS = {1: "", 2: "inner", 3: "outer", 4: "outer_inner"}

# Corner numbers that are not corners. Only one so far.
NOT_CORNERS = {90: "not a real corner (Daptrius, 2026-11-02)"}

# Racecourses Global's text has no English name for yet (it names the 15 that
# Global races on). These are the venues' own names, matching JP's text
# (サンタアニタパーク, デルマー); Global's text wins the day it has them.
NAMES_GLOBAL_LACKS = {10202: "Santa Anita Park", 10203: "Del Mar"}

# A path file's name: an_pos_race<track>_<kind>_<distance>_<ground>_<inout>_<n>.
# Kind 00 is a race path (01, with distance 0000, is not); ground 00 turf and
# 01 dirt; inout 0 to 3 for the master row's 1 to 4; n tells apart the rail
# variants the codes leave (17 JP courses, at most 4.4 m apart).
PATH_NAME = re.compile(r"^an_pos_race(\d+)_(\d\d)_(\d{4})_(\d\d)_(\d)_(\d)$")
PATH_POINTS = 1001


def _number(value):
    """A metre value as the game gives it, as an int when it is a whole number."""
    value = float(value)
    return int(value) if value.is_integer() else round(value, 1)


def pair_straights(events):
    """The straights, from the record's `straight_boundary` events, in race order.

    An event's values are [1, front] for a start and [2, front] for an end (a
    third value marks the final straight). Each start runs to the next end;
    the front type is the start's. Returns (straights, problems): a start with
    no end, or an end with no start, is a problem and draws nothing.
    """
    boundaries = sorted(
        (e for e in events if e.get("event_name") == "straight_boundary"),
        # At one distance an end comes before a start: Longchamp 2400 ends a
        # straight at 1867 m and starts the final one there.
        key=lambda e: (float(e["distance"]), e["values"][0] == 1),
    )
    straights, problems = [], []
    open_start = None
    for event in boundaries:
        kind, front = event["values"][0], event["values"][1]
        at = _number(event["distance"])
        if kind == 1:
            if open_start is not None:
                problems.append(f"straight start at {open_start[0]} m has no end")
            open_start = (at, front)
        elif open_start is None:
            problems.append(f"straight end at {at} m has no start")
        else:
            start, start_front = open_start
            straights.append({"start": start, "length": _number(at - start), "front": start_front})
            open_start = None
    if open_start is not None:
        problems.append(f"straight start at {open_start[0]} m has no end")
    return straights, problems


def surface_changes(events, surface):
    """Where the ground changes, from `ground_change` events (1 turf, 2 dirt).

    Only real changes are kept: a dirt course that starts on turf (Tokyo dirt
    1600, CM 22) reads turf at 0 and dirt at 100; an event repeating the
    surface the runners are already on is noise and dropped.
    """
    changes = []
    current = surface
    for event in sorted(
        (e for e in events if e.get("event_name") == "ground_change"),
        key=lambda e: float(e["distance"]),
    ):
        to = SURFACES.get(event["values"][0])
        if to and to != current:
            changes.append({"start": _number(event["distance"]), "surface": to})
            current = to
    return changes


def trim_at_finish(parts, distance, what):
    """Cut every part at the finish line. Returns (parts, flags)."""
    kept, flags = [], []
    for part in parts:
        start, length = part["start"], part["length"]
        if start >= distance:
            flags.append(f"{what} from {start} m starts at or past the {distance} m finish: dropped")
            continue
        if start + length > distance:
            flags.append(f"{what} from {start} m ran to {_number(start + length)} m: cut at the finish")
            part = {**part, "length": _number(distance - start)}
        kept.append(part)
    return kept, flags


def _corners(record, distance, flags):
    """The record's corners, corner 90 dropped and each cut at the finish."""
    corners = []
    for corner in record.get("corners") or []:
        number = corner["corner_number"]
        start = _number(corner["start"])
        if number in NOT_CORNERS:
            flags.append(f"corner {number} at {start} m: {NOT_CORNERS[number]}, dropped")
            continue
        corners.append({"start": start, "length": _number(corner["length"]), "number": number})
    corners, cut = trim_at_finish(corners, distance, "corner")
    flags += cut
    return corners


def _straights(record, distance, flags):
    """The straights re-paired in race order and cut at the finish, plus the problems."""
    straights, problems = pair_straights(record.get("events") or [])
    scraper_straights = [
        {"start": _number(s["start"]), "length": _number(s["length"]), "front": s["front_type"]}
        for s in record.get("straights") or []
    ]
    if straights != scraper_straights:
        flags.append("straights re-paired in race order (the scraper paired by front type)")
    flags += problems
    straights, cut = trim_at_finish(straights, distance, "straight")
    flags += cut
    return straights, problems


def course_fields(record, racecourse_names):
    """A scraped record as RaceCourse fields, all but the shape.

    `racecourse_names` maps the game's racecourse id to its name (Global's
    text). Returns None for a record with no master row: without it there is
    no distance, surface or direction to draw against (three JP records).
    """
    master = record.get("masterdata")
    if not master:
        return None
    distance = int(master["distance"])
    surface = SURFACES[master["ground"]]
    flags = [f"scraper: {warning}" for warning in record.get("warnings") or []]
    corners = _corners(record, distance, flags)
    straights, problems = _straights(record, distance, flags)
    slopes, cut = trim_at_finish(
        [{"start": _number(s["start"]), "length": _number(s["length"]), "slope": s["slope_raw"]}
         for s in record.get("slopes") or []],
        distance, "slope",
    )
    flags += cut

    track_id = int(master["race_track_id"])
    racecourse = racecourse_names.get(track_id) or NAMES_GLOBAL_LACKS.get(track_id, "")
    if not racecourse:
        flags.append(f"no English name for racecourse {track_id}")

    return {
        "game_id": int(master["id"]),
        "racecourse": racecourse,
        "track_id": track_id,
        "surface": surface,
        "distance": distance,
        "variant": VARIANTS.get(master["inout"], ""),
        "direction": DIRECTIONS[master["turn"]],
        "corners": sorted(corners, key=lambda c: c["start"]),
        "straights": straights,
        "slopes": sorted(slopes, key=lambda s: s["start"]),
        "surface_changes": surface_changes(record.get("events") or [], surface),
        "flags": flags,
        # A course whose straights could not be paired would draw wrongly:
        # hidden until an editor has looked (Longchamp 1000 today, no event).
        "show_map": not problems,
        "source_data": record,
    }


def pick_path(master, names):
    """The path file for a course, from the file names' codes.

    Returns (chosen, others). The codes pick one file for 121 of the 138 JP
    courses that have one; where two remain, the `_0` file is taken and the
    other is returned for the admin's override. A straight course with no
    path file gets (None, []).
    """
    matches = []
    for name in names:
        found = PATH_NAME.match(name)
        if not found:
            continue
        track, kind, distance, ground, inout, variant = found.groups()
        if (
            kind == "00"
            and int(track) == master["race_track_id"]
            and int(distance) == master["distance"]
            and int(ground) == master["ground"] - 1
            and int(inout) == master["inout"] - 1
        ):
            matches.append((int(variant), name))
    if not matches:
        return None, []
    matches.sort()
    return matches[0][1], [name for _, name in matches[1:]]


def _rotate(points, angle):
    cos, sin = math.cos(angle), math.sin(angle)
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in points]


def _level_angle(points, distance, final_straight_start):
    """The turn that lays the home straight (its start to the finish) level."""
    first = (
        round(final_straight_start / distance * (PATH_POINTS - 1))
        if final_straight_start is not None and final_straight_start < distance
        else round(0.9 * (PATH_POINTS - 1))
    )
    (ax, ay), (bx, by) = points[first], points[-1]
    return -math.atan2(by - ay, bx - ax)


def _finish_below(points):
    """How far the finish sits below the course's middle (screen y points down)."""
    return points[-1][1] - sum(y for _, y in points) / len(points)


def to_screen(xs, zs, distance, final_straight_start=None):
    """The game's path in screen axes, finish at the bottom, metres, 0.1 m.

    `final_straight_start` (metres) sets which way is "along the bottom": the
    home straight, from its start to the finish, is turned level, then the
    course is flipped end over end if the finish landed above the middle.
    Without one (a course with no straights) the last tenth of the race is
    used. Returns [[x, y], ...], the box's top-left corner at 0, 0.
    """
    if len(xs) != PATH_POINTS or len(zs) != PATH_POINTS:
        raise ValueError(f"a path has {PATH_POINTS} points, this one {len(xs)}")
    # Flip z into SVG's downward y. Without it every course is mirrored.
    points = [(float(x), -float(z)) for x, z in zip(xs, zs)]

    angle = _level_angle(points, distance, final_straight_start)
    level = _rotate(points, angle)
    flipped = _rotate(points, angle + math.pi)
    # A straight course has no "middle" to sit below: keep it running left to
    # right (the unflipped turn) unless flipping clearly puts the finish lower.
    chosen = flipped if _finish_below(flipped) > _finish_below(level) + 1 else level

    min_x = min(x for x, _ in chosen)
    min_y = min(y for _, y in chosen)
    return [[round(x - min_x, 1), round(y - min_y, 1)] for x, y in chosen]


def heading_change(points):
    """The total turn along a path in screen axes, in degrees.

    Positive is clockwise on screen (y points down), so a right-handed course
    comes out positive and a left-handed one negative: the test that a drawn
    course is not mirrored.
    """
    total = 0.0
    for i in range(1, len(points) - 1):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[i + 1]
        if (x1, y1) == (x0, y0) or (x2, y2) == (x1, y1):
            continue
        before = math.atan2(y1 - y0, x1 - x0)
        after = math.atan2(y2 - y1, x2 - x1)
        total += (after - before + math.pi) % (2 * math.pi) - math.pi
    return math.degrees(total)
