"""
Where on a course a skill can fire: its condition plus a course, as stretches
of metres (notes/course-map-plan.md, "The zones module", PR 6).

A condition is alternatives (`@`) of clauses (`&`), parsed by
skill_conditions.parse_condition. Each alternative starts as the whole course
and each clause narrows it; the alternatives' stretches are then merged.

WHAT A CLAUSE CAN DO
--------------------
* PLACE: narrows to a stretch. `fixed` (it fires there), `random` (at one
  random point in it), `point` (one spot).
* COURSE GATE (`track_id`, `rotation`, `ground_type`, `distance_type`,
  `is_basis_distance`, JP's `course_distance`): checked against the course.
  A failing gate removes its alternative; a skill whose every alternative
  fails is `never` here. A passing gate narrows nothing.
* RUNNER GATE (`running_style`): returned as `styles` for the client to match
  against the guide tab's or the veteran's style.
* EVENT GATE (`ground_condition`, `weather`, `season`): the event's own
  columns, often "Random", so they narrow nothing for now.
* NOT A PLACE (order, HP, timers, rivals...): narrows nothing.

NO GUESSING (standing rule, 2026-10-16): a place variable gets a stretch only
where its meaning is verified, the same list skill_text.py's phrases are built
on, plus corner numbers, settled with Daptrius on 2026-11-02 (counted back
from the finish in fours), and `corner_random`, a random point on the LAST
corner with that number (Daptrius, 2026-11-03: "the last time a corner
appears in the race").
Anything else, `straight_front_type` among them, narrows nothing, so an
alternative made of nothing else reads `anywhere`. A variable this module has never seen is
caught by the test that classifies every variable in the global skills,
before a player sees a wrong zone.

WHAT A SKILL COMES OUT AS
-------------------------
    {"kind": "place" | "anywhere" | "passive" | "never",
     "zones": [{"start", "end", "kind": "fixed" | "random" | "point"}],
     "styles": ["front", ...] | None}

`passive` (an alternative of gates only, which passes: a track green) and
`anywhere` (an alternative no place narrows) win over places, because then
the skill can fire anywhere; their zones are empty. The precondition (what
must have held EARLIER) is not a place and is not read here.

ONCE A RACE, FIRST POINT WINS (notes/course-map-plan-2.md, section 2): a skill
that fires once a race, whose alternatives are each a random point and nothing
else, fires at whichever point the runner reaches first. So it can't fire after
the earliest of the alternatives' last metres: Swinging Maestro's four corner
points mean its first numbered corner. `zones_for(..., once_per_race=True)`
clips the zones there; any other clause keeps the union, since a later
alternative can fire when an earlier one's other clause fails.
"""

from .skill_conditions import parse_condition

# The race's fractions, the course map's GAME_GEOMETRY (utils/courseGeometry.ts).
PHASE_STARTS = (0, 1 / 6, 2 / 3, 5 / 6, 1)

# The game's running_style values as the guide's style keys (PvpStyle).
STYLE_KEYS = {1: "front", 2: "pace", 3: "late", 4: "end"}
SURFACE_CODES = {"turf": 1, "dirt": 2}
ROTATION_CODES = {"right": 1, "left": 2, "straight": 4}
# pvp_track_skills.py's distance bands, by metres.
DISTANCE_BANDS = ((1400, 1), (1800, 2), (2400, 3))
BASIS_DISTANCE_STEP = 400

PLACE_VARIABLES = frozenset({
    "phase", "phase_random", "phase_firsthalf_random", "phase_laterhalf_random",
    "phase_firstquarter_random", "corner", "corner_random", "all_corner_random", "is_finalcorner",
    "is_finalcorner_random", "is_finalcorner_laterhalf", "straight_random",
    "is_last_straight", "is_last_straight_onetime", "last_straight_random", "is_lastspurt",
    "distance_rate", "distance_rate_after_random", "remain_distance", "slope",
    "up_slope_random", "down_slope_random", "always",
})
COURSE_GATES = frozenset({
    "track_id", "rotation", "ground_type", "distance_type", "is_basis_distance", "course_distance",
})
EVENT_GATES = frozenset({"ground_condition", "weather", "season"})
RUNNER_GATE = "running_style"
# Place-like, but their meaning is not verified: they narrow nothing.
UNCONFIRMED = frozenset({"straight_front_type", "lastspurt"})
# Who, when and how, never where.
NOT_A_PLACE = frozenset({
    "order", "order_rate", "accumulatetime", "change_order_onetime", "bashin_diff_behind",
    "bashin_diff_infront", "is_overtake", "distance_diff_top", "distance_diff_rate",
    "blocked_side_continuetime", "blocked_front_continuetime", "blocked_front", "popularity",
    "temptation_count", "is_temptation", "is_move_lane", "hp_per", "overtake_target_time",
    "infront_near_lane_time", "behind_near_lane_time", "post_number", "near_count",
    "is_behind_in", "is_badstart", "random_lot", "change_order_up_end_after",
    "change_order_up_finalcorner_after", "is_activate_any_skill", "is_exist_chara_id",
    "compete_fight_count", "grade", "is_surrounded", "time", "is_dirtgrade", "lane_type",
    "same_skill_horse_count", "is_other_character_activate_advantage_skill",
    "remain_distance_viewer_id", "motivation", "running_style_equal_popularity_one",
    "is_used_skill_id", "behind_near_lane_time_set1", "overtake_target_no_order_up_time",
})
NOT_A_PLACE_FAMILIES = (
    "activate_count_", "base_", "order_rate_in", "order_rate_out", "running_style_count_",
    "temptation_opponent_count_", "running_style_temptation_opponent_count_",
)


_KINDS = (
    ("place", PLACE_VARIABLES),
    ("course", COURSE_GATES),
    ("runner", frozenset({RUNNER_GATE})),
    ("event", EVENT_GATES),
    ("unconfirmed", UNCONFIRMED),
    ("not-a-place", NOT_A_PLACE),
)


def classify(variable):
    """Which of the module's kinds a condition variable is, or None if unknown."""
    for kind, variables in _KINDS:
        if variable in variables:
            return kind
    return "not-a-place" if variable.startswith(NOT_A_PLACE_FAMILIES) else None


# ── Stretches ───────────────────────────────────────────────────────────────

def _intersect(a, b):
    out = []
    for start, end in a:
        for other_start, other_end in b:
            low, high = max(start, other_start), min(end, other_end)
            if low < high or (low == high and (start == end or other_start == other_end)):
                out.append((low, high))
    return out


def _complement(stretches, distance):
    out, reached = [], 0
    for start, end in sorted(stretches):
        if start > reached:
            out.append((reached, start))
        reached = max(reached, end)
    if reached < distance:
        out.append((reached, distance))
    return out


def _compare(op, value):
    """The clause's comparison, for a rule that reads it as a stretch."""
    return {
        ">=": lambda limit: (limit, None), ">": lambda limit: (limit, None),
        "<=": lambda limit: (None, limit), "<": lambda limit: (None, limit),
    }.get(op, lambda limit: None)(value)


class CourseFacts:
    """The parts of a course the rules read, in metres."""

    def __init__(self, course):
        self.distance = course.distance
        self.corners = sorted((c["start"], c["start"] + c["length"], c["number"]) for c in course.corners)
        self.straights = sorted((s["start"], s["start"] + s["length"]) for s in course.straights)
        self.uphills = [(s["start"], s["start"] + s["length"]) for s in course.slopes if s["slope"] > 0]
        self.downhills = [(s["start"], s["start"] + s["length"]) for s in course.slopes if s["slope"] < 0]

    @property
    def final_straight(self):
        """The straight that runs to the finish (a metre of slack), or None."""
        final = self.straights[-1] if self.straights else None
        return final if final and final[1] >= self.distance - 1 else None

    @property
    def final_corner(self):
        return self.corners[-1][:2] if self.corners else None

    def phase(self, number):
        if not 0 <= number <= 3:
            return None
        return (PHASE_STARTS[number] * self.distance, PHASE_STARTS[number + 1] * self.distance)


def _phase_clause(facts, variable, op, value):
    """The phase variables: a whole phase, or a random point in it or in a part of it."""
    phase = facts.phase(value)
    if variable == "phase":
        if phase is None:
            return None
        start, end = phase
        stretches = {
            "==": [(start, end)], "!=": _complement([(start, end)], facts.distance),
            ">=": [(start, facts.distance)], ">": [(end, facts.distance)],
            "<=": [(0, end)], "<": [(0, start)],
        }.get(op)
        return (stretches, "fixed") if stretches is not None else None
    if op != "==" or phase is None:
        return None
    start, end = phase
    middle = (start + end) / 2
    part = {
        "phase_random": (start, end),
        "phase_firsthalf_random": (start, middle),
        "phase_laterhalf_random": (middle, end),
        "phase_firstquarter_random": (start, start + (end - start) / 4),
    }[variable]
    return [part], "random"


def _flag_clause(facts, variable, value):
    """The `== 1` (or 0) place flags, as stretches."""
    corners = [(start, end) for start, end, _ in facts.corners]
    final_corner, final_straight = facts.final_corner, facts.final_straight
    rules = {
        ("all_corner_random", 1): (corners, "random"),
        ("straight_random", 1): (facts.straights, "random"),
        ("up_slope_random", 1): (facts.uphills, "random"),
        ("down_slope_random", 1): (facts.downhills, "random"),
        ("slope", 1): (facts.uphills, "fixed"),
        ("slope", 2): (facts.downhills, "fixed"),
        ("slope", 0): (_complement(facts.uphills + facts.downhills, facts.distance), "fixed"),
        ("always", 1): ([(0, 0)], "point"),
    }
    if final_corner:
        rules.update({
            ("is_finalcorner", 1): ([(final_corner[0], facts.distance)], "fixed"),
            ("is_finalcorner", 0): ([(0, final_corner[0])], "fixed"),
            ("is_finalcorner_random", 1): ([final_corner], "random"),
            ("is_finalcorner_laterhalf", 1): ([((final_corner[0] + final_corner[1]) / 2, final_corner[1])], "fixed"),
        })
    if final_straight:
        rules.update({
            ("is_last_straight", 1): ([final_straight], "fixed"),
            ("is_last_straight_onetime", 1): ([(final_straight[0], final_straight[0])], "point"),
            ("last_straight_random", 1): ([final_straight], "random"),
        })
    rules[("is_lastspurt", 1)] = ([(PHASE_STARTS[2] * facts.distance, facts.distance)], "fixed")
    return rules.get((variable, value))


def place_clause(facts, variable, op, value):
    """One place clause as (stretches, kind), or None when it narrows nothing."""
    if variable.startswith("phase"):
        return _phase_clause(facts, variable, op, value)
    if variable == "corner":
        return _corner_clause(facts, op, value)
    if variable == "corner_random":
        # A random point on the last corner with that number: on a course
        # over one lap, the final lap's (Daptrius, 2026-11-03).
        numbered = [(start, end) for start, end, number in facts.corners if number == value]
        return (sorted(numbered)[-1:], "random") if op == "==" else None
    if variable in ("distance_rate", "remain_distance", "distance_rate_after_random"):
        return _distance_clause(facts.distance, variable, op, value)
    if op == "==":
        return _flag_clause(facts, variable, value)
    return None


def _corner_clause(facts, op, value):
    """corner==0 is "not on a corner" (the straights, and any gap); a number is
    every corner the game gives that number (one per lap)."""
    if value == 0 and op in ("==", "!="):
        corners = [(start, end) for start, end, _ in facts.corners]
        return (_complement(corners, facts.distance) if op == "==" else corners), "fixed"
    numbered = [(start, end) for start, end, number in facts.corners if number == value]
    return {
        "==": (numbered, "fixed"),
        "!=": (_complement(numbered, facts.distance), "fixed"),
    }.get(op)


def _distance_clause(distance, variable, op, value):
    """Shares of the race and metres left, as stretches."""
    if variable == "distance_rate_after_random":
        return ([(value * distance / 100, distance)], "random") if op == "==" else None
    bounds = _compare(op, value)
    if bounds is None:
        return None
    low, high = bounds
    if variable == "distance_rate":
        # "from N% of the race" (low) or "up to N%" (high).
        return [(low * distance / 100, distance) if low is not None else (0, high * distance / 100)], "fixed"
    # remain_distance: low ("N or more left") is the stretch BEFORE the last N.
    return [(0, distance - low) if low is not None else (distance - high, distance)], "fixed"


def course_gate_holds(course, variable, op, value):
    """A course gate against the course: True, False, or None when it can't say."""
    actual = {
        "track_id": course.track_id,
        "rotation": ROTATION_CODES.get(course.direction),
        "ground_type": SURFACE_CODES.get(course.surface),
        "distance_type": next((band for limit, band in DISTANCE_BANDS if course.distance <= limit), 4),
        "is_basis_distance": int(course.distance % BASIS_DISTANCE_STEP == 0),
        "course_distance": course.distance,
    }.get(variable)
    if actual is None:
        return None
    return {
        "==": actual == value, "!=": actual != value, ">=": actual >= value,
        "<=": actual <= value, ">": actual > value, "<": actual < value,
    }[op]


def _alternative(facts, course, clauses):
    """One alternative as (kind, stretches, stretch kind, styles, has_other),
    `has_other` saying a clause that isn't a place or a gate was there."""
    stretches = [(0, facts.distance)]
    stretch_kind = None
    styles = None
    has_other = False
    for variable, op, value in clauses:
        kind = classify(variable)
        if kind == "course":
            if course_gate_holds(course, variable, op, value) is False:
                return "never", [], None, None, False
        elif kind == "runner":
            allowed = {value} if op == "==" else set(STYLE_KEYS) - {value} if op == "!=" else set(STYLE_KEYS)
            styles = allowed if styles is None else styles & allowed
        elif kind == "place":
            narrowed = place_clause(facts, variable, op, value)
            if narrowed is None:
                has_other = True
                continue
            part, part_kind = narrowed
            stretches = _intersect(stretches, part)
            # A random point or a single spot, once anywhere in the clauses, is the kind.
            if part_kind == "point" or stretch_kind == "point":
                stretch_kind = "point"
            elif part_kind == "random" or stretch_kind == "random":
                stretch_kind = "random"
            else:
                stretch_kind = "fixed"
        elif kind in ("unconfirmed", "not-a-place") or kind is None:
            has_other = True
        # Event gates narrow nothing and say nothing.
    if stretch_kind is not None:
        if not stretches:
            return "never", [], None, None, False
        return "place", stretches, stretch_kind, styles, has_other
    return ("anywhere" if has_other else "passive"), [], None, styles, has_other


def _first_point_wins(live):
    """The live alternatives' stretches, clipped where a once-a-race skill made
    only of random points must already have fired; None when the rule doesn't
    apply (the module docstring)."""
    if len(live) < 2 or any(alt[0] != "place" or alt[2] != "random" or alt[4] for alt in live):
        return None
    bound = min(max(end for _, end in alt[1]) for alt in live)
    return [(start, min(end, bound)) for alt in live for start, end in alt[1] if start < bound]


def zones_for(condition, course, once_per_race=False):
    """A skill's condition on a course, in the module docstring's shape.
    `once_per_race`: the skill's cooldown outlasts the race (most skills)."""
    parsed = parse_condition(condition) if condition else None
    if not parsed:
        return {"kind": "anywhere", "zones": [], "styles": None}
    facts = CourseFacts(course)
    alternatives = [_alternative(facts, course, clauses) for clauses in parsed]
    live = [alt for alt in alternatives if alt[0] != "never"]
    if not live:
        return {"kind": "never", "zones": [], "styles": None}

    style_keys = _style_keys(live)
    clipped = _first_point_wins(live) if once_per_race else None

    kinds = {alt[0] for alt in live}
    for summary in ("passive", "anywhere"):
        if summary in kinds:
            return {"kind": summary, "zones": [], "styles": style_keys}

    zones = []
    if clipped is not None:
        zones = [{"start": round(start, 1), "end": round(end, 1), "kind": "random"} for start, end in clipped]
    else:
        for _, stretches, stretch_kind, _, _ in live:
            zones += [{"start": round(start, 1), "end": round(end, 1), "kind": stretch_kind}
                      for start, end in stretches]
    return {"kind": "place", "zones": _merge(zones), "styles": style_keys}


def _style_keys(live):
    """The union of the styles of the alternatives that can fire, in the game's
    order (front first, as the guide's tabs run); None when one has no style
    clause, since then every style can."""
    styles = set()
    for _, _, _, alt_styles, _ in live:
        if alt_styles is None:
            return None
        styles |= alt_styles
    return [STYLE_KEYS[style] for style in sorted(styles)]


def _merge(zones):
    """Touching or overlapping stretches of one kind become one."""
    merged = []
    for zone in sorted(zones, key=lambda z: (z["kind"], z["start"], z["end"])):
        if not merged:
            merged.append(dict(zone))
            continue
        last = merged[-1]
        if last["kind"] == zone["kind"] != "point" and zone["start"] <= last["end"]:
            last["end"] = max(last["end"], zone["end"])
        elif last != zone:
            merged.append(dict(zone))
    return sorted(merged, key=lambda z: (z["start"], z["end"]))
