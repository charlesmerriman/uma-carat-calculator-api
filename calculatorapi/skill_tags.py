"""
A skill's TAGS, for the Skills page's condition and effect filters with their
"match all / match any" switch (Daptrius's list, item 2: filter by conditions
and effects, e.g. "final corner", "Front Runner", "+speed").

READ FROM THE GAME'S CODE, NEVER FROM THE ENGLISH. Each tag comes from the
parsed condition (skill_conditions.parse_condition) and the ability types, and
only from variables whose meaning skill_text.py has already confirmed, so a
tag never says more than the skill's own page does. A skill matches a tag
when ANY of its slots, preconditions or `@` alternatives carries it: a skill
that fires "on a corner or on a straight" is found under both.

A strategy, distance or surface tag means the skill ASKS for it. A skill with
no strategy condition works for every strategy but carries no strategy tag,
so "Front Runner" finds Front Runner skills, as the game's own "(Front
Runner)" label does.

THE TAGS (the web's filter chips read the same keys, utils/skillSearch.ts):

    when   early mid late spurt corner final-corner straight final-straight uphill downhill
    race   sprint mile medium long turf dirt
    style  front-runner pace-chaser late-surger end-closer
    does   speed current-speed accel recovery stats vision navigation debuff
"""

from .skill_conditions import parse_condition

PHASE_TAGS = {0: "early", 1: "mid", 2: "late", 3: "spurt"}
DISTANCE_TAGS = {1: "sprint", 2: "mile", 3: "medium", 4: "long"}
SURFACE_TAGS = {1: "turf", 2: "dirt"}
STYLE_TAGS = {1: "front-runner", 2: "pace-chaser", 3: "late-surger", 4: "end-closer"}
# A variable that names one of a small set of values: each value it allows is a tag.
ENUMERATED = {"phase": PHASE_TAGS, "distance_type": DISTANCE_TAGS,
              "ground_type": SURFACE_TAGS, "running_style": STYLE_TAGS}

# A variable whose value IS the phase (skill_text: RANDOM_IN_PHASE,
# PHASE_PLACES, PHASE_PARTS), with the place it also names, if any.
PHASE_VARIABLES = {
    "phase_random": None,
    "phase_firsthalf_random": None,
    "phase_laterhalf_random": None,
    "phase_firstquarter_random": None,
    "phase_firsthalf": None,
    "phase_laterhalf": None,
    "phase_firstquarter": None,
    "phase_corner_random": "corner",
    "phase_straight_random": "straight",
    "phase_first_half_straight_random": "straight",
}

# variable == value -> tags (skill_text's FLAGS for the same pairs).
FLAG_TAGS = {
    ("is_lastspurt", 1): ("spurt",),
    ("run_at_full_speed_random", 1): ("spurt",),
    ("is_finalcorner_laterhalf", 1): ("final-corner",),
    ("is_finalcorner_random", 1): ("final-corner", "corner"),
    ("all_corner_random", 1): ("corner",),
    ("straight_random", 1): ("straight",),
    ("is_last_straight", 1): ("final-straight",),
    ("is_last_straight_onetime", 1): ("final-straight",),
    ("last_straight_random", 1): ("final-straight",),
    ("up_slope_random", 1): ("uphill",),
    ("up_slope_random_later_half", 1): ("uphill",),
    ("down_slope_random", 1): ("downhill",),
    ("phase_latter_half_straight_random", 1): ("mid", "straight"),
    ("straight_front_type", 2): ("straight",),
}

# Ability types (skill_text.ability_text): a skill "does" these for itself.
EFFECT_TAGS = {27: "speed", 21: "current-speed", 22: "current-speed", 31: "accel", 9: "recovery",
               1: "stats", 2: "stats", 3: "stats", 4: "stats", 5: "stats", 32: "stats",
               8: "vision", 28: "navigation"}
SELF = 1


def _values(clauses, universe):
    """The values in `universe` a variable's clauses allow, or [] when they
    allow all of them (no restriction, so no tag)."""
    allowed = [value for value in universe if all(_holds(op, value, bound) for op, bound in clauses)]
    return [] if len(allowed) == len(universe) else allowed


def _holds(op, value, bound):
    return {"==": value == bound, "!=": value != bound, ">=": value >= bound,
            "<=": value <= bound, ">": value > bound, "<": value < bound}[op]


def _alternative_tags(clauses):
    """The tags of one `&` alternative: (variable, op, value) triples."""
    grouped = {}
    for variable, op, value in clauses:
        grouped.setdefault(variable, []).append((op, value))
    tags = set()
    for variable, ops in grouped.items():
        single = ops[0] if len(ops) == 1 else None
        if single and single[0] == "==" and (variable, single[1]) in FLAG_TAGS:
            tags.update(FLAG_TAGS[(variable, single[1])])
        if variable in ENUMERATED:
            names = ENUMERATED[variable]
            tags.update(names[v] for v in _values(ops, list(names)))
        elif variable in PHASE_VARIABLES and single and single[0] == "==" and single[1] in PHASE_TAGS:
            tags.add(PHASE_TAGS[single[1]])
            if PHASE_VARIABLES[variable]:
                tags.add(PHASE_VARIABLES[variable])
        elif variable == "slope" and single and single[0] == "==":
            tags.update({1: ("uphill",), 2: ("downhill",)}.get(single[1], ()))
        elif variable == "corner_random" and single and single[0] == "==":
            tags.add("corner")
    return tags | _corner_tags(grouped)


def _corner_tags(grouped):
    """Corners and straights, read with the final corner as skill_text's
    describe_clauses does: is_finalcorner==1 with corner==0 is the final
    straight, with corner!=0 the final corner."""
    final = grouped.get("is_finalcorner") == [("==", 1)]
    corner = grouped.get("corner")
    if corner and len(corner) == 1:
        op, value = corner[0]
        if (op, value) == ("==", 0):
            return {"final-straight" if final else "straight"}
        if (op, value) == ("!=", 0) or (op == "==" and value > 0):
            return {"final-corner", "corner"} if final else {"corner"}
        return set()
    return {"final-corner"} if final else set()


def condition_tags(condition):
    """Every tag any alternative of one condition string carries."""
    parsed = parse_condition(condition) if condition else None
    return set().union(*(_alternative_tags(alternative) for alternative in parsed)) if parsed else set()


def effect_tags(ability):
    """What one ability does: its kind for the runner herself, or `debuff`
    when it lowers something for other runners."""
    kind, value, target = ability.get("type"), ability.get("value", 0), ability.get("target_type", SELF)
    if target != SELF:
        return {"debuff"} if value < 0 else set()
    if kind == 9:
        return {"recovery"} if value > 0 else set()
    if kind in EFFECT_TAGS and value > 0:
        return {EFFECT_TAGS[kind]}
    return set()


def skill_tags(skill):
    """The sorted tags of one skill: anything with `effects`, `precondition`
    and `condition`, as skill_text.describe takes."""
    slots = skill.effects or [{"precondition": skill.precondition, "condition": skill.condition}]
    tags = set()
    for slot in slots:
        tags |= condition_tags(slot.get("precondition") or "")
        tags |= condition_tags(slot.get("condition") or "")
        for ability in slot.get("abilities", []):
            tags |= effect_tags(ability)
    return sorted(tags)
