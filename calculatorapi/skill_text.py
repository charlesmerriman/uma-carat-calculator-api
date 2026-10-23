"""
A skill's conditions and effects in plain English, built from the game's raw
data (`Skill.condition`, `Skill.effects`) at read time and never stored, so a
better wording is a code change and never a re-import.

THE PHRASE TABLES ARE VERIFIED, NOT INFERRED (owner rule, 2026-10-16: no
guessing). Every variable and effect type below was checked on 2026-10-23
against global's own English descriptions of the skills that use it (for
example: all 62 single-condition skills with `phase_random==1` say
"mid-race", every `ability_type` 31 says "acceleration"), or is the meaning
the track-green matcher already relies on (pvp_track_skills.py). Anything
else comes back as the game's own code, flagged `raw`, for the page to show
as code. Known unknowns on 2026-10-23: `distance_diff_top` (units),
`distance_diff_rate`, `is_overtake`, `lastspurt==2`, `is_behind_in`,
`straight_front_type`, a corner's number, `grade`, the `order_rate_*_continue`
family, and several JP-only variables. Adding one is adding a line here, with
the skills that confirm it named in the commit.

THE SHAPE, per effect slot (a skill has one or two):

    {"after": condition,              # the precondition: must have held earlier
     "when":  condition,              # the condition itself
     "effects": [phrase, ...],
     "duration": "2.4 s" | "Whole race" | None,
     "cooldown": "Once per race" | "30 s" | None}

where a condition is {"all": [phrase, ...], "any": [[phrase, ...], ...]}
(see describe_condition) and a phrase is {"text": str, "raw": bool}. `describe()` adds `wit_check`
and `complete` (no raw phrase anywhere), for the page and for counting
coverage.

Units, from zephyr's horse notes and checked on known skills: durations and
cooldowns are ten-thousandths of a second; speeds, accelerations, stats and
recovery are the value ÷ 10,000 (m/s, m/s², stat points, share of max
endurance). A duration is the game's base value; the game stretches it on
longer courses.
"""

from dataclasses import dataclass, field

from .skill_conditions import CLAUSE

STYLES = {1: "Front Runner", 2: "Pace Chaser", 3: "Late Surger", 4: "End Closer"}
DISTANCES = {1: "Sprint", 2: "Mile", 3: "Medium", 4: "Long"}
# Global's words for the four phases: "early-race", "mid-race", "late-race",
# and the last spurt (phase_random==3 skills all say "last spurt").
PHASES = {0: "early-race", 1: "mid-race", 2: "late-race", 3: "in the last spurt phase"}
SURFACES = {1: "turf", 2: "dirt"}
TRACK_CONDITIONS = {1: "firm", 2: "good", 3: "soft", 4: "heavy"}
WEATHER = {1: "sunny", 2: "cloudy", 3: "rainy", 4: "snowy"}
# 5 is the game's late spring (Spring Runner reads season 1 or 5).
SEASONS = {1: "spring", 2: "summer", 3: "fall", 4: "winter", 5: "spring"}
SLOPES = {0: "On flat ground", 1: "On an uphill", 2: "On a downhill"}
ROTATION = {1: "On a right-handed track", 2: "On a left-handed track"}

# A cooldown this long outlasts every race: the skill fires once.
ONCE_PER_RACE = 5_000_000
# target_value at or above this means every runner who fits, not a count.
ALL_RUNNERS = 10

# variable == 1 (or another fixed value) reads as one fixed phrase.
FLAGS = {
    ("is_lastspurt", 1): "In the last spurt",
    ("is_finalcorner", 1): "On the final corner or later",
    ("is_finalcorner", 0): "Before the final corner",
    ("is_finalcorner_laterhalf", 1): "In the second half of the final corner",
    ("is_finalcorner_random", 1): "At a random moment on the final corner",
    ("all_corner_random", 1): "At a random moment on a corner",
    ("straight_random", 1): "At a random moment on a straight",
    ("is_last_straight", 1): "On the final straight",
    ("is_last_straight_onetime", 1): "On entering the final straight",
    ("last_straight_random", 1): "At a random moment on the final straight",
    ("up_slope_random", 1): "At a random moment on an uphill",
    ("down_slope_random", 1): "At a random moment on a downhill",
    ("always", 1): "Always",
    ("is_surrounded", 1): "While surrounded",
    ("is_temptation", 0): "While not rushed",
    ("is_temptation", 1): "While rushed",
    ("temptation_count", 0): "If not rushed so far this race",
    ("lane_type", 0): "Near the inner rail",
    ("is_move_lane", 1): "While moving sideways",
    ("is_move_lane", 2): "While moving sideways",
    ("is_badstart", 0): "After a clean start",
    ("is_badstart", 1): "After a slow start",
    ("blocked_front", 1): "Blocked in front",
    ("time", 4): "At night",
    ("is_basis_distance", 1): "At a standard distance (a multiple of 400 m)",
    ("is_basis_distance", 0): "At a non-standard distance",
}

# variable >= N reads as "... N s or more", for the timers.
SECONDS = {
    "accumulatetime": "After {n} s of racing",
    "blocked_side_continuetime": "Blocked at the side for {n} s or more",
    "blocked_front_continuetime": "Blocked in front for {n} s or more",
    "infront_near_lane_time": "A runner close ahead in your lane for {n} s or more",
    "behind_near_lane_time": "A runner close behind in your lane for {n} s or more",
    "overtake_target_time": "Trying to pass a runner for {n} s or more",
}

# variable >= N reads as "After using N or more ... skills".
SKILL_COUNTS = {
    "activate_count_all": "skills",
    "activate_count_heal": "recovery skills",
    "activate_count_start": "skills early-race",
    "activate_count_middle": "skills mid-race",
}

BASE_STATS = {"base_speed": "Speed", "base_stamina": "Stamina", "base_power": "Power",
              "base_guts": "Guts", "base_wiz": "Wit"}
# The randomly placed moments inside one phase.
RANDOM_IN_PHASE = {
    "phase_random": "At a random moment {phase}",
    "phase_firsthalf_random": "At a random moment in the first half of {phase}",
    "phase_laterhalf_random": "At a random moment in the second half of {phase}",
    "phase_firstquarter_random": "At a random moment in the first quarter of {phase}",
}


@dataclass
class TextContext:
    """Names the phrases need that the condition only holds as ids."""
    racecourses: dict = field(default_factory=dict)   # track id -> "Tokyo"
    skill_names: dict = field(default_factory=dict)   # skill game id -> shown name
    characters: dict = field(default_factory=dict)    # character id -> "Silence Suzuka"


def phrase(text, raw=False):
    return {"text": text, "raw": raw}


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def number(value):
    """1.50 -> "1.5", 40.0 -> "40", 2400 -> "2,400"."""
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def signed(value):
    return f"+{number(value)}" if value >= 0 else f"-{number(-value)}"


def bounds(clauses):
    """
    (low, high, equal, not_equal) for one variable's integer comparisons, as
    inclusive bounds: `> 50` becomes a low of 51. None where unset.
    """
    low = high = equal = not_equal = None
    for op, value in clauses:
        if op == "==":
            equal = value
        elif op == "!=":
            not_equal = value
        elif op == ">=":
            low = value if low is None else max(low, value)
        elif op == ">":
            low = value + 1 if low is None else max(low, value + 1)
        elif op == "<=":
            high = value if high is None else min(high, value)
        elif op == "<":
            high = value - 1 if high is None else min(high, value - 1)
    return low, high, equal, not_equal


def value_set(clauses, universe):
    """The values in `universe` every clause allows, for small enumerations."""
    allowed = set(universe)
    for op, value in clauses:
        allowed = {v for v in allowed if CHECKS[op](v, value)}
    return sorted(allowed)


CHECKS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b,
}


def enumerated(clauses, names, template):
    """`distance_type==3` -> "In Medium races"; `!=3` -> "Not in Medium races"."""
    if len(clauses) == 1 and clauses[0][0] == "!=" and clauses[0][1] in names:
        text = template.format(names[clauses[0][1]])
        return "Not " + text[0].lower() + text[1:]
    allowed = value_set(clauses, names)
    if not allowed or any(v not in names for v in allowed):
        return None
    labels = list(dict.fromkeys(names[v] for v in allowed))
    return template.format(" or ".join(labels))


def phases_text(clauses):
    allowed = value_set(clauses, PHASES)
    if not allowed:
        return None
    if allowed == [3]:
        return "In the last spurt phase"
    if allowed[-1] == 3 and len(allowed) > 1 and allowed == list(range(allowed[0], 4)):
        return PHASES[allowed[0]].capitalize() + " or later"
    return " or ".join(PHASES[p] for p in allowed).capitalize()


def ranged(clauses, below, above, between, exact=None):
    """A numeric variable with up to two bounds, through four templates."""
    low, high, equal, not_equal = bounds(clauses)
    if not_equal is not None:
        return None
    if equal is not None:
        return (exact or between).format(lo=number(equal), hi=number(equal), n=number(equal))
    if low is not None and high is not None:
        return between.format(lo=number(low), hi=number(high))
    if high is not None:
        return below.format(n=number(high))
    if low is not None:
        return above.format(n=number(low))
    return None


def remain_text(clauses):
    low, high, equal, _ = bounds(clauses)
    if equal is None and low is not None and high is not None and high - low <= 10:
        # The game brackets a moment tightly: 199 to 201 m is "at 200 m".
        return f"At about {number(round((low + high) / 2, -1))} m to go"
    return ranged(clauses, "{n} m or less to go", "{n} m or more to go",
                  "Between {lo} and {hi} m to go", "At {n} m to go")


def distance_rate_text(clauses):
    low, high, _, _ = bounds(clauses)
    if low == 50 and high is None:
        return "In the second half of the race"
    return ranged(clauses, "In the first {n}% of the race", "After {n}% of the race",
                  "Between {lo}% and {hi}% of the race")


def order_text(clauses):  # pylint: disable=too-many-return-statements
    low, high, equal, not_equal = bounds(clauses)
    if not_equal is not None:
        return None
    if equal is not None:
        return "In the lead" if equal == 1 else f"In {ordinal(equal)} place"
    if low is not None and high is not None:
        return f"Between {ordinal(low)} and {ordinal(high)} place"
    if high is not None:
        return f"In {ordinal(high)} place or better"
    if low == 1:
        return ""  # any place at all: the game's way of saying nothing
    if low is not None:
        return f"In {ordinal(low)} place or lower"
    return None


def order_rate_text(clauses):
    # order_rate is a place as a share of the field: 50 is halfway back.
    low, high, _, not_equal = bounds(clauses)
    strict_low = any(op == ">" for op, _ in clauses)
    if not_equal is not None:
        return None
    if low is not None and high is not None:
        shown_low = low - 1 if strict_low else low
        return f"Between {shown_low}% and {high}% back in the field"
    if high is not None:
        return f"In the front {high}% of the field"
    if low is not None:
        if strict_low:
            return f"Behind the front {low - 1}% of the field"
        return f"{low}% or further back in the field"
    return None


def lengths(n):
    return f"{number(n)} length" + ("" if n == 1 else "s")


def gap_text(variable, clauses):
    low, high, equal, not_equal = bounds(clauses)
    if equal is not None or not_equal is not None:
        return None
    ahead = variable == "bashin_diff_infront"
    if high is not None and low is None:
        return (f"Within {lengths(high)} of the runner ahead" if ahead
                else f"The runner behind within {lengths(high)}")
    if low is not None and high is None:
        return (f"{lengths(low)} or more behind the runner ahead" if ahead
                else f"{lengths(low)} or more clear of the runner behind")
    return None


def clause_text(variable, clauses, context):  # pylint: disable=too-many-return-statements,too-many-branches
    """One variable's clauses as a phrase, or None when it isn't verified."""
    single = clauses[0] if len(clauses) == 1 else None
    if single and (variable, single[1]) in FLAGS and single[0] == "==":
        return FLAGS[(variable, single[1])]
    if variable == "phase":
        return phases_text(clauses)
    if variable in RANDOM_IN_PHASE and single and single[0] == "==" and single[1] in PHASES:
        return RANDOM_IN_PHASE[variable].format(phase=PHASES[single[1]])
    if variable == "distance_rate_after_random" and single and single[0] == "==":
        if single[1] == 50:
            return "At a random moment in the second half of the race"
        return f"At a random moment after {single[1]}% of the race"
    if variable == "distance_type":
        return enumerated(clauses, DISTANCES, "In {} races")
    if variable == "running_style":
        return enumerated(clauses, STYLES, "As a {}")
    if variable == "ground_type":
        return enumerated(clauses, SURFACES, "On {}")
    if variable == "ground_condition":
        return enumerated(clauses, TRACK_CONDITIONS, "On a {} track")
    if variable == "weather":
        return enumerated(clauses, WEATHER, "In {} weather")
    if variable == "season":
        return enumerated(clauses, SEASONS, "In {}")
    if variable == "rotation" and single and single[0] == "==":
        return ROTATION.get(single[1])
    if variable == "slope" and single and single[0] == "==":
        return SLOPES.get(single[1])
    if variable == "corner" and single and single[1] == 0:
        return {"==": "On a straight", "!=": "On a corner"}.get(single[0])
    if variable == "track_id" and single and single[1] in context.racecourses:
        name = context.racecourses[single[1]]
        return {"==": f"At {name} Racecourse", "!=": f"Not at {name} Racecourse"}.get(single[0])
    if variable == "is_exist_chara_id" and single and single[0] == "==":
        name = context.characters.get(single[1])
        return f"When {name} is in the race" if name else None
    if variable == "is_used_skill_id" and single and single[0] == "==":
        name = context.skill_names.get(single[1])
        return f"After using {name}" if name else None
    if variable == "change_order_onetime" and single:
        return {"<": "Just after passing another runner",
                ">": "Just after being passed"}.get(single[0]) if single[1] == 0 else None
    if variable == "compete_fight_count" and single == (">", 0):
        return "While dueling"
    if variable == "remain_distance":
        return remain_text(clauses)
    if variable == "distance_rate":
        return distance_rate_text(clauses)
    if variable == "order":
        return order_text(clauses)
    if variable == "order_rate":
        return order_rate_text(clauses)
    if variable in ("bashin_diff_infront", "bashin_diff_behind"):
        return gap_text(variable, clauses)
    if variable == "course_distance":
        return ranged(clauses, "On a course of {n} m or less", "On a course of {n} m or more",
                      "On a course of {lo} to {hi} m", "On a {n} m course")
    return counted_text(variable, clauses)


def counted_text(variable, clauses):  # pylint: disable=too-many-return-statements
    """The variables read as a count or a threshold: timers, skills used,
    runners around; then stats and ranks (standing_text)."""
    low, high, equal, not_equal = bounds(clauses)
    if not_equal is not None:
        return None
    only_low = low is not None and high is None and equal is None
    if variable in SECONDS and only_low:
        return SECONDS[variable].format(n=number(low))
    if variable in SKILL_COUNTS and only_low:
        return f"After using {low} or more {SKILL_COUNTS[variable]}"
    if variable == "near_count" and only_low:
        return "With a runner nearby" if low == 1 else f"With {low} or more runners nearby"
    if variable.startswith("running_style_count_") and variable.endswith("_otherself") and only_low:
        style = {"nige": 1, "senko": 2, "sashi": 3, "oikomi": 4}.get(variable.split("_")[3])
        if style:
            if low == 1:
                return f"With another {STYLES[style]} in the race"
            return f"With {low} or more other {STYLES[style]}s in the race"
    return standing_text(variable, clauses, (low, high, equal))


def standing_text(variable, clauses, bound):  # pylint: disable=too-many-return-statements
    """A runner's stats and ranks: base stats, endurance left, popularity, bracket."""
    low, high, equal = bound
    only_low = low is not None and high is None and equal is None
    if variable in BASE_STATS:
        stat = BASE_STATS[variable]
        if only_low:
            return f"{stat} {number(low)} or higher"
        if high is not None and low is None and equal is None:
            return f"{stat} {number(high)} or lower"
    if variable == "hp_per":
        if only_low:
            return "With endurance left" if low == 1 else f"With {low}% endurance or more left"
        if high is not None and low is None and equal is None:
            return f"With {high}% endurance or less left"
    if variable == "popularity":
        if equal == 1:
            return "As the favorite"
        if high is not None and low is None:
            return f"Among the top {high} favorites"
        if only_low:
            return f"{ordinal(low)} favorite or lower"
    if variable == "post_number":
        return ranged(clauses, "In bracket {n} or lower", "In bracket {n} or higher",
                      "In brackets {lo} to {hi}", "In bracket {n}")
    return None


def describe_condition(condition, context):
    """
    {"all": [phrase, ...], "any": [[phrase, ...], ...]}: every phrase in
    `all` holds, and so does at least one of the `any` lists (none when
    empty). The clauses every alternative shares are pulled into `all`, so
    Xceleration reads as its four common clauses plus "the runner ahead OR the
    runner behind within a length". A string the parser can't read at all
    comes back whole, as one raw phrase.
    """
    if not condition:
        return {"all": [], "any": []}
    alternatives = []
    for alternative in condition.split("@"):
        grouped = {}
        for clause in alternative.split("&"):
            match = CLAUSE.fullmatch(clause)
            if match is None:
                return {"all": [phrase(condition, raw=True)], "any": []}
            variable, op, value = match.groups()
            grouped.setdefault(variable, []).append((op, int(value), clause.strip()))
        alternatives.append(describe_clauses(grouped, context))
    return factor(alternatives)


def factor(alternatives):
    """Split alternatives into what they all share and what tells them apart."""
    if len(alternatives) == 1:
        return {"all": alternatives[0], "any": []}
    shared = [item for item in alternatives[0] if all(item in other for other in alternatives[1:])]
    rest = [[item for item in alternative if item not in shared] for alternative in alternatives]
    # An alternative with nothing of its own is the shared clauses alone, which
    # the others only narrow: (A and B) or A is A.
    if any(not alternative for alternative in rest):
        return {"all": shared, "any": []}
    # Two alternatives that read the same (the game sometimes lists both
    # directions of a lane move) are one.
    unique = [alt for i, alt in enumerate(rest) if alt not in rest[:i]]
    if len(unique) == 1:
        return {"all": shared + unique[0], "any": []}
    return {"all": shared, "any": unique}


def describe_clauses(grouped, context):
    phrases = []
    # Two clauses that read as one place on the track.
    final = grouped.get("is_finalcorner")
    corner = grouped.get("corner")
    if final and corner and [c[:2] for c in final] == [("==", 1)] and len(corner) == 1:
        op, value, _ = corner[0]
        if value == 0 and op in ("==", "!="):
            phrases.append(phrase("On the final straight" if op == "==" else "On the final corner"))
            del grouped["is_finalcorner"], grouped["corner"]
    for variable, clauses in grouped.items():
        text = clause_text(variable, [c[:2] for c in clauses], context)
        if text is None:
            phrases.append(phrase(" & ".join(c[2] for c in clauses), raw=True))
        elif text:
            phrases.append(phrase(text))
    return phrases


def target_text(ability, context):  # pylint: disable=too-many-return-statements
    """Who an ability lands on, or "" for the skill's own runner."""
    kind, value = ability.get("target_type", 1), ability.get("target_value", 0)
    def counted(where):
        if value >= ALL_RUNNERS:
            return f"runners {where}"
        return f"the runner {where}" if value == 1 else f"up to {value} runners {where}"
    if kind == 1:
        return ""
    if kind == 9:
        return counted("ahead")
    if kind == 10:
        return counted("behind")
    if kind == 18 and value in STYLES:
        return f"{STYLES[value]}s"
    if kind == 21 and value in STYLES:
        return f"rushed {STYLES[value]}s"
    if kind == 19:
        return "rushed runners ahead"
    if kind == 20:
        return "rushed runners behind"
    if kind == 22:
        return context.characters.get(value, "a named runner")
    if kind == 23:
        return "a runner who used a recovery skill"
    if kind == 4:
        return "other runners"
    return None


def ability_text(ability, context):  # pylint: disable=too-many-return-statements
    kind, raw_value = ability["type"], ability["value"]
    value = raw_value / 10_000
    target = target_text(ability, context)
    varies = ability.get("value_usage", 1) != 1
    if target is None:
        return phrase(f"effect {kind}: {signed(value)}, target {ability.get('target_type')}", raw=True)
    def done(text):
        text += " (varies)" if varies else ""
        return phrase(f"{text} ({target})" if target else text)
    stats = {1: "Speed", 2: "Stamina", 3: "Power", 4: "Guts", 5: "Wit"}
    if kind in stats:
        return done(f"{stats[kind]} {signed(value)}")
    if kind == 27:
        return done(f"Velocity {signed(value)} m/s")
    if kind in (21, 22):
        return done(f"Current speed {signed(value)} m/s")
    if kind == 31:
        return done(f"Acceleration {signed(value)} m/s²")
    if kind == 9:
        share = number(abs(value) * 100)
        return done(f"Recovers {share}% endurance" if value >= 0 else f"Drains {share}% endurance")
    if kind == 8:
        return done(f"Field of view {signed(value)}")
    if kind == 28:
        return done(f"Navigation {signed(value)}")
    # Types whose number has no confirmed unit: the direction only, worded as
    # global words the skills that use it, and only for that direction.
    labels = {(6, 0): "Becomes a runaway", (10, 1): "Less time lost to a slow start",
              (14, 1): "More time lost to a slow start", (29, -1): "Lower chance of being rushed",
              (13, 1): "Rushed runners take longer to calm down",
              (35, 1): "Less likely to get surrounded"}
    direction = (raw_value > 0) - (raw_value < 0)
    if (kind, direction) in labels:
        return done(labels[(kind, direction)])
    return phrase(f"effect {kind}: {signed(value)}" + (f" ({target})" if target else ""), raw=True)


def seconds(raw):
    return f"{number(raw / 10_000)} s"


def describe_slot(slot, context):
    duration = slot.get("duration", 0)
    cooldown = slot.get("cooldown", 0)
    return {
        "after": describe_condition(slot.get("precondition", ""), context),
        "when": describe_condition(slot.get("condition", ""), context),
        "effects": [ability_text(ability, context) for ability in slot.get("abilities", [])],
        "duration": "Whole race" if duration < 0 else (seconds(duration) if duration > 0 else None),
        "cooldown": ("Once per race" if cooldown >= ONCE_PER_RACE
                     else seconds(cooldown) if cooldown > 0 else None),
    }


def describe(skill, context=None):
    """
    The readable conditions and effects of one skill: any object with
    `effects`, `precondition`, `condition` and `activate_lot`. A skill with no
    effects yet (an import from before 2026-10-23) still gets its slot-1
    condition, with no effects.
    """
    context = context or TextContext()
    slots = skill.effects or [{"precondition": skill.precondition, "condition": skill.condition}]
    described = [describe_slot(slot, context) for slot in slots]
    phrases = [
        item for slot in described
        for condition in (slot["after"], slot["when"])
        for item in [*condition["all"], *sum(condition["any"], [])]
    ] + [item for slot in described for item in slot["effects"]]
    lot = getattr(skill, "activate_lot", None)
    return {
        "wit_check": None if lot is None else bool(lot),
        "slots": described,
        "complete": not any(item["raw"] for item in phrases),
    }


def context_from_database():
    """A TextContext from the database: three queries, for a page of skills."""
    # Imported here: models import nothing from this module, and a module-level
    # import would make the readable text depend on Django being set up.
    from .models import Skill, Uma  # pylint: disable=import-outside-toplevel
    from .skill_conditions import racecourse_names  # pylint: disable=import-outside-toplevel

    racecourses = racecourse_names(
        Skill.objects.filter(name__contains="Racecourse").only("name", "condition")
    )
    skill_names = {
        row.game_id: row.shown_name
        for row in Skill.objects.only("game_id", "name", "translated_name")
        if row.shown_name
    }
    characters = {}
    for game_id, name in Uma.objects.exclude(game_id__isnull=True).values_list(
        "game_id", "name",
    ).order_by("id"):
        # Uma.character_id's rule (game_id // 100); "Special Week (Summer)"
        # -> "Special Week", the first row winning.
        characters.setdefault(game_id // 100, name.split(" (")[0])
    return TextContext(racecourses, skill_names, characters)
