"""
The game's skill condition strings, parsed. Shared by the track-green matcher
(pvp_track_skills.py) and the readable text (skill_text.py).

A condition is the game's own little language, kept verbatim on the skill:

    order>=3&order_rate<=50@order>=3&remain_distance<=200

`@` separates alternatives (any one is enough) and `&` joins the clauses of
one alternative (all must hold). Every clause is `variable operator integer`.
A precondition has the same shape and must have held earlier in the race.
"""

import re

CLAUSE = re.compile(r"\s*([a-z_0-9]+)\s*(==|!=|>=|<=|>|<)\s*(-?\d+)\s*")
OPERATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}
RACECOURSE_NAME = re.compile(r"^(?P<track>.+?) Racecourse [○◎×]$")


def parse_condition(condition):
    """`a==1&b==2@c==3` -> [[("a", "==", 1), ("b", "==", 2)], [("c", "==", 3)]],
    or None when any clause is not a plain comparison (so it isn't ours to read)."""
    alternatives = []
    for alternative in condition.split("@"):
        clauses = []
        for clause in alternative.split("&"):
            match = CLAUSE.fullmatch(clause)
            if match is None:
                return None
            var, op, value = match.groups()
            clauses.append((var, op, int(value)))
        alternatives.append(clauses)
    return alternatives


def condition_variables(parsed):
    return {var for alternative in parsed for var, _, _ in alternative}


def condition_holds(parsed, values):
    """True when any alternative has every clause true. The caller makes sure
    every variable is in `values`."""
    return any(
        all(OPERATORS[op](values[var], value) for var, op, value in alternative)
        for alternative in parsed
    )


def racecourse_names(skills):
    """
    {track id: "Tokyo", ...}, read off the racecourse skills' own names:
    "Tokyo Racecourse ◎" has the condition `track_id==10006`. So there is no
    id table to keep in step with the game. `skills` is any iterable of rows
    with `name` and `condition` (a queryset, or snapshot rows wrapped).
    """
    names = {}
    for skill in skills:
        match = RACECOURSE_NAME.match(skill.name or "")
        parsed = parse_condition(skill.condition) if match and skill.condition else None
        if parsed and len(parsed) == 1 and len(parsed[0]) == 1:
            var, op, value = parsed[0][0]
            if var == "track_id" and op == "==":
                names[value] = match["track"].strip()
    return names
