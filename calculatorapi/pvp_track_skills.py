"""
The green skills a race's track calls for, read off the game's own data.

The guide admin's "Fill track green skills" button. A track green (Tokyo
Racecourse, Right-Handed, Firm Conditions, Winter Runner, Sunny Days, Standard
Distance, Sharp Turns, Muddy ...) activates on nothing but the race itself, and
the game says which race in the skill's raw `condition`:

    rotation==1                                  Right-Handed
    track_id==10006                              Tokyo Racecourse
    ground_condition==2@ground_condition==3@...  Wet Conditions
    ground_type==2&ground_condition==3@...       Muddy

`@` separates alternatives and `&` joins the clauses of one alternative. So the
matcher turns the event's track fields into those same game variables and
evaluates each skill's condition against them. Nothing here lists the skills:
a track green the game adds later is offered without a code change.

WHICH SKILLS COUNT. A general track family: every version's condition reads
only race variables (below), and the family has both a ○ and a ◎ version. The
pair rule is what leaves out the one-off skills that happen to share a
condition (scenario skills like "Medium Race Enthusiast", event rewards like
"Unquenched Thirst", the penalty "♡ 3D Nail Art"); every general family has
both versions. The ○ is the one added (owner, 2026-10-17; it used to be the
◎), and the sheet importer resolves "○/◎" to the ○ too, so every path that
names a green skill names the same version. The ◎ and the gold come along on
the site through `skill_families()`, and a team holding the ◎ still covers it.

THE RACECOURSE IDS ARE READ OFF THE SKILL NAMES ("Tokyo Racecourse ◎" has
`track_id==10006`), so there is no id table to keep in step with the game. A
track with no racecourse skill on global (Longchamp, Del Mar) gets no id; every
`track_id` clause is then false, which is right, since no skill names it. A
typo in the event's track looks the same, so the report says so either way.

An event field this can't read ("TBD", a typo, two fields swapped) is reported
rather than guessed, and any skill whose condition needs it is skipped.
"""

import re
from typing import NamedTuple

from .models import PvpSkillRecommendation, Skill
# The parser is shared with the readable skill text; re-exported here for
# the callers that have always found it in this module.
from .skill_conditions import (  # pylint: disable=unused-import
    RACECOURSE_NAME, condition_holds, condition_variables, parse_condition,
)
from .models.pvp_guide import PvpSkillCategory, PvpStyle
from .models.skill import SkillRarity, SkillTier

class TrackField(NamedTuple):
    """How one event field reads as a game variable."""
    label: str  # in the report
    variable: str  # the game's name for it in a condition
    words: dict  # {accepted text, lower case: the game's value}


# Keyed by the event's field name. The game's numbering, checked against the
# conditions in the master snapshot: Wet Conditions is ground_condition 2/3/4
# and Muddy (dirt) 3/4, so 1 is firm and 4 heavy; Spring Runner is season 1 or
# 5 (5 is the game's late spring).
FIELD_VALUES = {
    "direction": TrackField("Direction", "rotation", {"right": 1, "left": 2}),
    "track_condition": TrackField("Track condition", "ground_condition", {
        "firm": 1, "good": 2, "soft": 3, "heavy": 4,
    }),
    "season": TrackField("Season", "season", {
        "spring": 1, "summer": 2, "fall": 3, "autumn": 3, "winter": 4,
    }),
    "weather": TrackField("Weather", "weather", {"sunny": 1, "cloudy": 2, "rainy": 3, "snowy": 4}),
    "surface_type": TrackField("Surface", "ground_type", {"turf": 1, "dirt": 2}),
    "distance": TrackField("Distance", "distance_type", {
        "sprint": 1, "short": 1, "mile": 2, "medium": 3, "long": 4,
    }),
}

# Every variable the event can answer. A condition that reads anything else
# (an order, a phase, a corner) is not a track green.
RACE_VARIABLES = frozenset(
    {"track_id", "is_basis_distance"} | {spec.variable for spec in FIELD_VALUES.values()}
)

# A "standard" distance in the game is a multiple of 400 m (1200, 1600, 2000,
# 2400, ...). The JRA's own definition, and the one Standard Distance uses.
BASIS_DISTANCE_STEP = 400

UNFILLED = {"", "tbd", "tba", "?"}


def _track_skills():
    """[(skill, parsed condition)] for every skill on global whose condition
    reads only race variables. One query."""
    rows = []
    for skill in Skill.objects.on_global().exclude(condition=""):
        parsed = parse_condition(skill.condition)
        if parsed is not None and condition_variables(parsed) <= RACE_VARIABLES:
            rows.append((skill, parsed))
    return rows


def _racecourse_ids(track_skills):
    """{"tokyo": 10006, ...} from the racecourse skills' own names."""
    ids = {}
    for skill, parsed in track_skills:
        match = RACECOURSE_NAME.match(skill.name)
        if match and len(parsed) == 1 and len(parsed[0]) == 1:
            var, op, value = parsed[0][0]
            if var == "track_id" and op == "==":
                ids[match["track"].strip().lower()] = value
    return ids


def _normalise(raw):
    return str(raw or "").strip().lower()


def read_event(event, racecourse_ids):
    """The event's track fields as game variables, plus what couldn't be read.

    Returns (values, problems). A variable missing from `values` is one the
    event doesn't say; a skill that needs it is skipped.
    """
    values = {}
    problems = []

    track = _normalise(event.track)
    if track in UNFILLED:
        problems.append("Track isn't filled in yet, so no racecourse skill was added.")
    else:
        # A track with no racecourse skill gets an id no condition names, so
        # every track_id clause is false (see the module docstring).
        values["track_id"] = racecourse_ids.get(track, 0)
        if track not in racecourse_ids:
            problems.append(
                f"Track is \"{event.track}\", which has no racecourse skill on global, so "
                "no racecourse or Sharp Turns skill was added. If that's a typo, fix it "
                "on the event."
            )

    for field, spec in FIELD_VALUES.items():
        label = spec.label
        raw = getattr(event, field)
        text = _normalise(raw)
        if text in UNFILLED:
            problems.append(f"{label} isn't filled in yet.")
            continue
        # "Right-Handed" and "Right" both read as right.
        found = next(
            (value for word, value in spec.words.items() if text == word or text.startswith(word + "-")),
            None,
        )
        if found is None:
            known = ", ".join(sorted({word.capitalize() for word in spec.words}))
            problems.append(f"{label} is \"{raw}\", which isn't one of: {known}.")
        else:
            values[spec.variable] = found

    digits = re.sub(r"[^\d]", "", str(event.length or ""))
    if digits:
        values["is_basis_distance"] = int(int(digits) % BASIS_DISTANCE_STEP == 0)
    else:
        problems.append(
            f"Length is \"{event.length}\", not a number of metres, so Standard and "
            "Non-Standard Distance were left out."
        )

    return values, problems


def suggest(guide):
    """What the button would add to this guide.

    Returns {"add": [Skill], "already": [Skill], "problems": [str]}: the ○ of
    each matching family the guide doesn't name yet, the ones it already has
    (in any style or category, any version), and the report from read_event.
    """
    track_skills = _track_skills()
    race, problems = read_event(guide.event, _racecourse_ids(track_skills))

    families = {}
    for skill, parsed in track_skills:
        families.setdefault(skill.group_id, []).append((skill, parsed))

    held_groups = set(
        PvpSkillRecommendation.objects.filter(guide=guide).values_list("skill__group_id", flat=True)
    )

    add, already = [], []
    for group_id in sorted(families):
        members = families[group_id]
        tiers = {skill.tier for skill, _ in members if skill.rarity == SkillRarity.WHITE}
        if not {SkillTier.WHITE, SkillTier.GOLD} <= tiers:
            continue
        pick, parsed = next(
            (skill, parsed) for skill, parsed in members
            if skill.rarity == SkillRarity.WHITE and skill.tier == SkillTier.WHITE
        )
        if not condition_variables(parsed) <= set(race):
            continue  # needs a field the event doesn't say; already reported
        if not condition_holds(parsed, race):
            continue
        (already if group_id in held_groups else add).append(pick)

    return {"add": add, "already": already, "problems": problems}


def fill(guide):
    """Add the suggested skills to the guide's Everyone / Green list, after the
    rows already there. Returns the skills added.

    bulk_create raises no signals, so the caller invalidates the public payload
    cache (the Timeline's has_guide reads it).
    """
    skills = suggest(guide)["add"]
    last = (
        PvpSkillRecommendation.objects
        .filter(guide=guide, style=PvpStyle.EVERYONE, category=PvpSkillCategory.GREEN)
        .order_by("-order")
        .values_list("order", flat=True)
        .first()
    )
    start = 0 if last is None else last + 1
    PvpSkillRecommendation.objects.bulk_create([
        PvpSkillRecommendation(
            guide=guide,
            style=PvpStyle.EVERYONE,
            category=PvpSkillCategory.GREEN,
            skill=skill,
            order=start + index,
        )
        for index, skill in enumerate(skills)
    ])
    return skills
