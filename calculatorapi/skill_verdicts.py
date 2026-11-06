"""
A verdict per skill on a course: where it fires against the late-race start,
in metres (notes/course-map-plan-2.md, "2. A verdict per skill").

Built beside the zones (skill_zones.py) into the same per-course map
(views/race_course.py), because a verdict depends only on the skill and the
course, and computed once per course into its cache. The client words it
(utils/courseLayers.ts, verdictText); nothing here is a sentence a player
reads except `needs`, which is skill_text.py's verified phrases.

THE FIELDS, each present only when it says something:

    effects       ["accel", "speed", "heal", "debuff"]: what the skill does
                  to whom, from its ability types (skill_text.py's table)
    fires_at      a fixed zone's first metre: it fires there when its other
                  clauses hold then
    spread        [first, last]: the metres a random trigger can land on
    spurt         {"from", "to"[, "odds"][, "useful"]}: an accel or speed
                  skill's trigger in metres from the late-race start (negative
                  is before); `odds`, an accel's share landing well (rule 5);
                  `useful`, its ranking score: that share times the speed its
                  acceleration adds, plus the share its speed carries into the
                  late race times that speed (m/s both)
    before_spurt  a heal's share of triggers landing before the late-race start
    carry         a speed skill's share that carries its speed into the late race
                  (absent when no part of its trigger lies before the late-race start)
    carry_window  [first, last) metres it must fire in to do so (the band drawn)
    repeats       how many times a 30 s cooldown skill can fire here (2 or more)
    needs         what else must hold, in words (placing, timers, skills used)
    unreadable    true: a clause the map can't read yet, so no timing at all
    chain         {"counts": "heal", "at_least": 3}: set off by other skills
    paths         one entry per way the skill can fire here, when its ways need
                  different things (each with zones, fires_at or spread, spurt,
                  before_spurt, carry, carry_window, needs, unreadable); the
                  top-level fields then summarise them, `useful` the best path's

THE RULES (each a test in tests/test_skill_verdicts.py):

1. A fixed zone fires at its start, when its other clauses hold then. A
   clause the map can't predict (a placing, an event, HP, skills used) means
   it fires at the first metre of the zone where that holds; the words still
   read from the zone's start, and the RANK (`useful`) counts the zone's
   metres before the window as chances to fire early and be used up (the
   owner, 2026-11-03: a zone opening at the spurt, No Stopping Me!, keeps its
   full rank; one opening on an earlier straight, Tamamo's, loses it).
2. A random trigger is one 10 m point picked before the race (the community
   mechanics doc), by length within its stretch, or equal per stretch first
   for `straight_random` and the slopes (Daptrius, 2026-11-03). A share is given only where that is known and the
   random clause is the alternative's only place clause; anything else gets
   its `spread` and no share.
3. Once a race, first point wins: skill_zones.zones_for(once_per_race=True).
4. A clause skill_zones calls unconfirmed blocks every timing field.
5. An accel lands well when it fires early enough to still run 0.5 s into the
   late race and at most 50 m after its start (proc_window); durations and
   cooldowns stretch by distance / 1000, and seconds become metres at the
   mid-race speed of the skill's styles (Daptrius's answers, below).
6. Every verdict assumes the skill passes its Wit check (`activate_lot`).
7. A skill whose ways of firing need different things gets a verdict per way
   (`paths`), and a skill set off by other skills with no place of its own
   fires from the phase those skills live in (`activate_count_middle` from the
   mid-race start).
8. `all_corner_random` places four points, each picking a corner (equal) then
   a metre along it; a once-a-race skill fires at the first point reached, a
   short-cooldown one at any of them (Daptrius, 2026-11-03).
"""

import math

from .skill_conditions import parse_condition
from .skill_text import ONCE_PER_RACE, SKILL_COUNTS, describe_condition
from .skill_zones import PHASE_STARTS, CourseFacts, classify, zones_for

# ── Daptrius's answers (2026-11-03, messages 1567150957861671044,
# 1567167972005388289 and 1567219956226199664; notes/course-map-plan-2.md,
# "Daptrius's answers"). The formulas are uma-skill-tools' RaceSolver.ts. ──

# An accel lands well when at least this many seconds of it are left once the
# late race starts (Daptrius: "probably between 0.5 and 1 second into the accel
# phase. That is a PvPer question"; the owner chose 0.5)...
ACCEL_LEFT_AT_SPURT = 0.5
# ...and no later than this many metres after it ("100 meters after the accel
# zone is pretty useless ... ideally within 50 meters of the late race").
PROC_LATE_EDGE = 50
# How many trigger points each multi-point variable places: all_corner_random
# places 4, each choosing its corner on its own; straight_random can fire
# again over several laps, but how many points it places is unknown, so None.
TRIGGER_POINTS = {"all_corner_random": 4, "straight_random": None}
# Not-a-place clauses settled before the race or by the start (rule 1): the
# zone then fires at its first metre for certain. Every other not-a-place
# clause is a state the map can't predict (a placing, an event, HP, skills
# used). `accumulatetime` is settled once its seconds fall before the zone.
SETTLED = frozenset({
    "popularity", "post_number", "grade", "motivation", "random_lot", "running_style_count_same",
    "running_style_count_same_rate", "running_style_equal_popularity_one", "is_badstart", "is_exist_chara_id",
    "same_skill_horse_count", "is_dirtgrade", "time",
})
SETTLED_FAMILIES = ("base_", "running_style_count_")
# The phase a counted skill lives in: where a skill set off by N of them can
# fire from (rule 7). Others are unbounded.
CHAIN_BOUNDS = {"middle": PHASE_STARTS[1], "end_after": PHASE_STARTS[2], "later_half": 0.5}

# The stats a verdict assumes where the game's numbers need one: 1200 Wit for
# the section randomness (Daptrius), 1200 Power to match, A aptitudes.
ASSUMED_WIT = 1200
ASSUMED_POWER = 1200
# Target speed in the mid race, per running style (Speed.StrategyPhaseCoefficient).
MID_RACE_COEFFICIENT = {"front": 0.98, "pace": 0.991, "late": 0.998, "end": 1.0}
# The mid race's acceleration coefficient is 1.0 for every style.
BASE_ACCEL = 0.0006


def stretch(distance):
    """A duration's and a cooldown's multiplier on a course (Daptrius: both
    "scale by the same formula", distance / 1000)."""
    return distance / 1000


def base_speed(distance):
    return 20 - (distance - 2000) / 1000


def section_randomness(wit=ASSUMED_WIT):
    """The average per-section speed offset, as a share of base speed: a random
    factor from max - 0.65 to max percent, so on average max - 0.325."""
    top = wit / 5500 * math.log10(wit * 0.1)
    return (top - 0.325) / 100


def mid_race_speed(distance, styles=None):
    """The m/s a runner holds in the mid race: base speed times her style's
    coefficient, plus the average section randomness at 1200 Wit. A skill for
    any style takes the four styles' mean (they differ by 2% at most)."""
    coefficients = [MID_RACE_COEFFICIENT[style] for style in styles or MID_RACE_COEFFICIENT]
    return base_speed(distance) * (sum(coefficients) / len(coefficients) + section_randomness())


def mid_race_accel(power=ASSUMED_POWER):
    """m/s² while speeding up in the mid race, on the flat, at A aptitudes."""
    return BASE_ACCEL * math.sqrt(500 * power)


# ── The game's numbers ──────────────────────────────────────────────────────

ACCELERATION, VELOCITY, CURRENT_SPEED_DOWN, CURRENT_SPEED_UP, RECOVERY = 31, 27, 21, 22, 9
SELF = 1  # target_type: the skill's own runner
TENTHS_OF_A_MS = 10_000  # durations and cooldowns are ten-thousandths of a second
# Random variables whose point is picked by length along their stretch.
BY_LENGTH = frozenset({
    "phase_random", "phase_firsthalf_random", "phase_laterhalf_random", "phase_firstquarter_random",
    "is_finalcorner_random", "last_straight_random", "distance_rate_after_random", "corner_random",
})
# Random variables that pick one stretch first, each as likely as the next,
# then a point along it (Daptrius, 2026-11-03, for the slopes), by the course
# fact holding their stretches.
PER_STRETCH = {"straight_random": "straights", "up_slope_random": "uphills", "down_slope_random": "downhills"}
RANDOM_VARIABLES = BY_LENGTH | set(PER_STRETCH) | {"all_corner_random"}
_KIND_ORDER = ("accel", "speed", "heal", "debuff")


def effect_kinds(effects):
    """What the skill does, in a fixed order: to its own runner, accel, speed,
    heal; to others, debuff (a speed, acceleration or endurance loss)."""
    kinds = set()
    for slot in effects or []:
        for ability in slot.get("abilities", []):
            kind, value = ability.get("type"), ability.get("value", 0)
            if ability.get("target_type", SELF) != SELF:
                if value < 0 and kind in (ACCELERATION, VELOCITY, CURRENT_SPEED_DOWN, RECOVERY):
                    kinds.add("debuff")
            elif value > 0 and kind == ACCELERATION:
                kinds.add("accel")
            elif value > 0 and kind in (VELOCITY, CURRENT_SPEED_UP):
                kinds.add("speed")
            elif value > 0 and kind == RECOVERY:
                kinds.add("heal")
    return [kind for kind in _KIND_ORDER if kind in kinds]


def late_race_start(distance):
    return PHASE_STARTS[2] * distance


def is_once_per_race(effects):
    """A cooldown that outlasts every race (skill_text.ONCE_PER_RACE)."""
    slot = (effects or [{}])[0]
    return slot.get("cooldown", ONCE_PER_RACE) >= ONCE_PER_RACE


def _trigger(entry):
    """(fires_at, spread): fires_at when every zone is fixed or a point, spread
    when any is random; (None, None) off the course or anywhere."""
    zones = entry["zones"] if entry["kind"] == "place" else []
    if not zones:
        return None, None
    if {zone["kind"] for zone in zones} <= {"fixed", "point"}:
        return min(zone["start"] for zone in zones), None
    return None, [min(zone["start"] for zone in zones), max(zone["end"] for zone in zones)]


def _random_variable(parsed):
    """The one random variable a condition's trigger rides on, or None when
    there is none, several, or one shares its alternative with another place
    clause (then its point can land where the other clause fails)."""
    found = set()
    for clauses in parsed:
        places = [variable for variable, _, _ in clauses if classify(variable) == "place"]
        randoms = [variable for variable in places if variable in RANDOM_VARIABLES]
        if randoms and len(places) > 1:
            return None
        found.update(randoms)
    return found.pop() if len(found) == 1 else None


def _overlap(start, end, low, high):
    return max(0.0, min(end, high) - max(start, low))


def _corner_point_cdf(zones, metre):
    """The share of one all_corner_random point before `metre`: a corner
    picked at random, then a metre along it (rule 8)."""
    return sum(min(1.0, max(0.0, (metre - start) / (end - start))) for start, end in zones) / len(zones)


def share_between(entry, variable, facts, low, high, once=True):  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-return-statements
    """The share of a random trigger landing in [low, high), by rule 2, or None."""
    zones = entry["zones"]
    if variable == "corner_random" and len(zones) > 1:
        return None  # several corners, one point each: not one stretch by length
    if variable == "all_corner_random":
        corners = [(zone["start"], zone["end"]) for zone in zones if zone["end"] > zone["start"]]
        if not corners:
            return None
        points = TRIGGER_POINTS[variable]
        if once:
            # The first of the four points: none may fall before `low`, and at
            # least one before `high`.
            return (1 - _corner_point_cdf(corners, low)) ** points - (1 - _corner_point_cdf(corners, high)) ** points
        one = _corner_point_cdf(corners, high) - _corner_point_cdf(corners, low)
        return 1 - (1 - one) ** points
    if variable in BY_LENGTH:
        total = sum(zone["end"] - zone["start"] for zone in zones)
        if total <= 0:
            return None
        return sum(_overlap(zone["start"], zone["end"], low, high) for zone in zones) / total
    if variable in PER_STRETCH:
        # A stretch first, each as likely as the next, then a point along it.
        parts = []
        for straight_start, straight_end in getattr(facts, PER_STRETCH[variable]):
            for zone in zones:
                start, end = max(straight_start, zone["start"]), min(straight_end, zone["end"])
                if end > start:
                    parts.append((start, end))
        if not parts:
            return None
        return sum(_overlap(start, end, low, high) / (end - start) for start, end in parts) / len(parts)
    return None


def _share(entry, fires_at, variable, facts, window, once=True):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """A fixed trigger's share of [low, high) is 1 or 0; a random one's by rule 2."""
    low, high = window
    if fires_at is not None:
        return 1.0 if low <= fires_at < high else 0.0
    return share_between(entry, variable, facts, low, high, once) if variable else None


def is_settled(variable, op, value, zone_start, distance, styles):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    """Whether a not-a-place clause holds for certain by a zone's start."""
    if variable in SETTLED or variable.startswith(SETTLED_FAMILIES):
        return True
    if variable == "temptation_count" and op == "==" and value == 0:
        return True  # "not rushed so far": holds from the start unless it ever fails
    if variable == "accumulatetime" and op in (">=", ">"):
        return value * mid_race_speed(distance, styles) <= zone_start
    return False


def is_gated(parsed, zone_start, distance, styles):
    """Whether any alternative has a clause the map can't predict by the zone's start."""
    return any(
        classify(variable) == "not-a-place" and not is_settled(variable, op, value, zone_start, distance, styles)
        for clauses in parsed for variable, op, value in clauses
    )


def _gated_share(entry, low, high):
    """A gated fixed zone's rank share: the window's by-length share of the zone
    clipped to [its start, the window's end), since every metre before the
    window is a chance to fire early and be used up (rule 1)."""
    start = min(zone["start"] for zone in entry["zones"])
    considered = sum(_overlap(zone["start"], zone["end"], start, high) for zone in entry["zones"])
    if considered <= 0:
        return 0.0
    return sum(_overlap(zone["start"], zone["end"], low, high) for zone in entry["zones"]) / considered


def needs_for(parsed, context):
    """What must hold besides the place, in skill_text's verified words; a
    clause it can't word adds one plain note. Empty when any alternative needs
    nothing more (then the skill can fire without it)."""
    others = []
    for clauses in parsed:
        kept = [f"{variable}{op}{value}" for variable, op, value in clauses if classify(variable) == "not-a-place"]
        if not kept:
            return []
        others.append("&".join(kept))
    described = describe_condition("@".join(others), context)
    phrases = list(described["all"]) + [item for alternative in described["any"] for item in alternative]
    words = [item["text"] for item in described["all"] if not item["raw"]]
    choices = [alternative for alternative in described["any"] if not any(item["raw"] for item in alternative)]
    if choices:
        # "In the lead and the runner behind within 1 length": only the first
        # phrase of a joined alternative keeps its capital.
        readable = [" and ".join([texts[0]] + [text[:1].lower() + text[1:] for text in texts[1:]])
                    for texts in ([item["text"] for item in alternative] for alternative in choices)]
        words.append(", or ".join([readable[0]] + [text[:1].lower() + text[1:] for text in readable[1:]]))
    if any(item["raw"] for item in phrases):
        words.append("Plus a condition we can't read yet")
    return words


def chain_for(parsed):
    """A verified "after using N skills" clause as {"counts", "at_least"}, or None."""
    for clauses in parsed:
        for variable, op, value in clauses:
            if variable in SKILL_COUNTS and op in (">=", ">"):
                return {"counts": variable.removeprefix("activate_count_"),
                        "at_least": value if op == ">=" else value + 1}
    return None


def _seconds(effects, kind):
    """The duration of the first slot doing `kind`, in seconds, or None."""
    wanted = {"speed": (VELOCITY, CURRENT_SPEED_UP), "accel": (ACCELERATION,)}.get(kind, ())
    for slot in effects or []:
        if any(ability.get("type") in wanted for ability in slot.get("abilities", [])):
            duration = slot.get("duration", 0)
            return duration / TENTHS_OF_A_MS if duration > 0 else None
    return None


def accel_strength(effects):
    """The speed an accel adds over its run, m/s: its acceleration (m/s²) times
    its base duration (s). 0.4 for 3 s is 1.2; a weaker or shorter one less."""
    seconds = _seconds(effects, "accel") or 0
    for slot in effects or []:
        for ability in slot.get("abilities", []):
            if ability.get("type") == ACCELERATION and ability.get("target_type", SELF) == SELF:
                return ability.get("value", 0) / TENTHS_OF_A_MS * seconds
    return 0.0


def speed_added(effects):
    """The target speed a skill adds to its own runner, m/s (0.35 for most golds)."""
    return _self_value(effects, VELOCITY) / TENTHS_OF_A_MS


def _share_by_length(entry, low, high):
    """Rule 2's by-length share over the zones, for ranking where a variable's
    own weighting isn't confirmed."""
    total = sum(zone["end"] - zone["start"] for zone in entry["zones"])
    if total <= 0:
        return None
    return sum(_overlap(zone["start"], zone["end"], low, high) for zone in entry["zones"]) / total


def _self_value(effects, kind):
    """The first self-targeted ability of `kind`'s value, in its game unit
    (ten-thousandths), or 0."""
    for slot in effects or []:
        for ability in slot.get("abilities", []):
            if ability.get("type") == kind and ability.get("target_type", SELF) == SELF:
                return ability.get("value", 0)
    return 0


def carry_window(effects, distance, styles=None):
    """[first, last) metres where a speed skill must fire to carry its speed
    into the late race, or None when no point does.

    Daptrius, 2026-11-03: "speed carryover is all about target speed". A skill
    giving target speed alone needs time to reach it, so it must fire early
    enough to get there before the late race starts, and is useless closer in;
    one that also gives current speed or acceleration still helps right up to
    the late-race start."""
    seconds = _seconds(effects, "speed")
    if seconds is None:
        return None
    speed = mid_race_speed(distance, styles)
    late = late_race_start(distance)
    first = late - seconds * stretch(distance) * speed
    mixed = _self_value(effects, CURRENT_SPEED_UP) > 0 or _self_value(effects, ACCELERATION) > 0
    # The seconds a target-speed-only skill takes to reach its new speed.
    reach = 0 if mixed else _self_value(effects, VELOCITY) / TENTHS_OF_A_MS / mid_race_accel()
    last = late - reach * speed
    return (first, last) if last > first else None


def proc_window(effects, distance, styles=None):
    """[first, last) metres where an accel lands well: early enough that it
    still runs ACCEL_LEFT_AT_SPURT seconds into the late race, at the latest
    PROC_LATE_EDGE metres after the late-race start. None without a duration."""
    seconds = _seconds(effects, "accel")
    if seconds is None:
        return None
    late = late_race_start(distance)
    before = max(0.0, seconds * stretch(distance) - ACCEL_LEFT_AT_SPURT) * mid_race_speed(distance, styles)
    return late - before, late + PROC_LATE_EDGE


def repeats_for(parsed, effects, facts, styles=None):
    """How many times a short-cooldown skill on several random points can fire
    here: the most points that sit a cooldown apart on its stretches. None
    for a once-a-race skill, or where its points aren't known."""
    if is_once_per_race(effects):
        return None
    variables = {variable for clauses in parsed for variable, _, _ in clauses}
    variable = next((name for name in TRIGGER_POINTS if name in variables), None)
    points = TRIGGER_POINTS.get(variable) if variable else None
    if not points:
        return None
    seconds = effects[0]["cooldown"] / TENTHS_OF_A_MS
    gap = seconds * stretch(facts.distance) * mid_race_speed(facts.distance, styles)
    stretches = ([(start, end) for start, end, _ in facts.corners] if variable == "all_corner_random"
                 else facts.straights)
    count, earliest = 0, 0.0
    for start, end in sorted(stretches):
        while count < points and max(start, earliest) < end:
            count += 1
            earliest = max(start, earliest) + gap
    return count


def verdict_for(condition, effects, course, entry, context):
    """The verdict fields for one skill on one course (the module docstring).
    `entry` is its zones_for result; `context` a skill_text.TextContext."""
    out = {}
    kinds = effect_kinds(effects)
    if kinds:
        out["effects"] = kinds
    parsed = parse_condition(condition) if condition else None
    if not parsed:
        return out
    needs = needs_for(parsed, context)
    if needs:
        out["needs"] = needs
    chain = chain_for(parsed)
    if chain:
        out["chain"] = chain
        bound = CHAIN_BOUNDS.get(chain["counts"])
        if entry["kind"] == "anywhere" and bound is not None:
            # Rule 7: set off by skills of one phase, so it can't fire before it.
            entry = {**entry, "kind": "place",
                     "zones": [{"start": round(bound * course.distance, 1), "end": course.distance, "kind": "fixed"}]}
            out.update(kind=entry["kind"], zones=entry["zones"])
    if any(classify(variable) == "unconfirmed" for clauses in parsed for variable, _, _ in clauses):
        out["unreadable"] = True
        return out
    paths = _paths(parsed, kinds, effects, course, context)
    if paths is not None:
        out["paths"] = paths
        out.update(_summary(paths, course))
        return out
    out.update(_timing(parsed, kinds, effects, course, entry))
    return out


def _paths(parsed, kinds, effects, course, context):
    """Rule 7: one verdict per way of firing, when the live ways need different
    things; None when they don't (then one merged verdict stands)."""
    if len(parsed) < 2:
        return None
    paths = []
    for clauses in parsed:
        alone = "&".join(f"{variable}{op}{value}" for variable, op, value in clauses)
        entry = zones_for(alone, course)
        if entry["kind"] == "never":
            continue
        path = {"zones": entry["zones"]}
        needs = needs_for([clauses], context)
        if needs:
            path["needs"] = needs
        if any(classify(variable) == "unconfirmed" for variable, _, _ in clauses):
            path["unreadable"] = True
        else:
            path.update(_timing([clauses], kinds, effects, course, entry))
        paths.append(path)
    if len(paths) < 2 or len({tuple(path.get("needs", [])) for path in paths}) < 2:
        return None
    return paths


def _summary(paths, course):
    """The top-level fields over the paths: the whole stretch they cover, the
    best path's rank; no odds or shares, since those differ by path."""
    out = {}
    placed = [path for path in paths if "fires_at" in path or "spread" in path]
    firsts = [path["fires_at"] if "fires_at" in path else path["spread"][0] for path in placed]
    lasts = [path["fires_at"] if "fires_at" in path else path["spread"][1] for path in placed]
    if firsts:
        out["spread"] = [min(firsts), max(lasts)]
        late = late_race_start(course.distance)
        spurts = [path["spurt"] for path in paths if "spurt" in path]
        if spurts:
            out["spurt"] = {"from": round(min(firsts) - late), "to": round(max(lasts) - late)}
            ranked = [spurt for spurt in spurts if "useful" in spurt]
            if ranked:
                best = max(ranked, key=lambda spurt: spurt["useful"])
                out["spurt"]["useful"] = best["useful"]
                for part in ("accel", "carry"):
                    if part in best:
                        out["spurt"][part] = best[part]
    if all(path.get("unreadable") for path in paths):
        out["unreadable"] = True
    return out


def _timing(parsed, kinds, effects, course, entry):  # pylint: disable=too-many-locals
    """The timing fields: where it fires, and against the late-race start
    (one share function over every window, so the locals are the windows)."""
    out = {}
    fires_at, spread = _trigger(entry)
    if fires_at is None and spread is None:
        return out
    if fires_at is not None:
        out["fires_at"] = round(fires_at)
    else:
        out["spread"] = [round(spread[0]), round(spread[1])]
    facts = CourseFacts(course)
    late = late_race_start(course.distance)
    variable = _random_variable(parsed)
    once = is_once_per_race(effects)
    styles = entry.get("styles")
    gated = fires_at is not None and is_gated(parsed, fires_at, course.distance, styles)

    def share(window):
        return _share(entry, fires_at, variable, facts, window, once)

    def landing_share(window):
        """The share of the trigger landing in a window, for ranking: a gated
        fixed zone by rule 1's clipped share; by length where a random
        variable's weighting isn't known, so every skill still gets an order."""
        if gated:
            return _gated_share(entry, *window)
        known = share(window)
        return known if known is not None or fires_at is not None else _share_by_length(entry, *window)

    carry_rank = 0.0
    window = carry_window(effects, course.distance, styles) if "speed" in kinds else None
    first_metre = fires_at if fires_at is not None else spread[0]
    # A trigger wholly at or after the late-race start has nothing to carry
    # into it (the skill pass, bug A2): no band, no share, no carry words.
    # Drawing the band anyway put a "Carry-over window" under every
    # final-straight unique (Victoria por plancha, board 2026-11-05).
    if window is not None and first_metre < late:
        # The band the map draws, and the share landing in it. A trigger wholly
        # before the band keeps it: the client words that "Ends before the late race".
        out["carry_window"] = [round(window[0]), round(window[1])]
        # What it carries, for the accels column's words: the target speed it
        # adds and how long it runs here (owner, 2026-11-04).
        out["carry_speed"] = round(speed_added(effects), 2)
        out["carry_seconds"] = round((_seconds(effects, "speed") or 0) * stretch(course.distance), 1)
        if share(window) is not None:
            out["carry"] = round(share(window), 2)
        carry_rank = landing_share(window) or 0.0
    if "accel" in kinds or "speed" in kinds:
        first, last = (fires_at, fires_at) if fires_at is not None else spread
        out["spurt"] = {"from": round(first - late), "to": round(last - late)}
        window = proc_window(effects, course.distance, styles) if "accel" in kinds else None
        if window is not None:
            odds = share(window)
            if odds is not None:
                out["spurt"]["odds"] = round(odds, 2)
            # How useful at the spurt start, for ordering: the share landing
            # well times the speed its acceleration adds, plus the share its
            # speed carries into the late race times that speed (the owner,
            # 2026-11-03: carry-over is prized, Tamamo's unique does both).
            accel_part = (landing_share(window) or 0) * accel_strength(effects)
            carry_part = carry_rank * speed_added(effects)
            out["spurt"]["useful"] = round(accel_part + carry_part, 3)
            # The two parts, shown as chips on the accels column (owner, 2026-11-04).
            out["spurt"]["accel"] = round(accel_part, 3)
            out["spurt"]["carry"] = round(carry_part, 3)
    if "heal" in kinds and share((0, late)) is not None:
        out["before_spurt"] = round(share((0, late)), 2)
    repeats = repeats_for(parsed, effects, facts, styles)
    if repeats and repeats > 1:
        out["repeats"] = repeats
    return out
