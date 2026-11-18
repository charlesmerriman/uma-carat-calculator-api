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
as code. The skill pass of 2026-11-03 (notes/course-map-skill-pass.md, B5)
gave the last Global unknowns their phrases (`is_overtake`, `distance_diff_top`
in metres, `lastspurt==2`, `straight_front_type==2`, a corner's number, the
`order_rate_*_continue` family, ...), each sourced from Global's descriptions
and named at its line. Adding one is adding a line here, with the skills that
confirm it named in the commit.

THE SHAPE, per effect slot (a skill has one or two):

    {"after": condition,              # the precondition: must have held earlier
     "when":  condition,              # the condition itself
     "effects": [phrase, ...],
     "duration": "2.4 s per 1,000 m" | "Whole race" | None,
     "cooldown": "Once per race" | "30 s per 1,000 m" | None}

where a condition is {"all": [phrase, ...], "any": [[phrase, ...], ...]}
(see describe_condition) and a phrase is {"text": str, "raw": bool}. `describe()` adds `wit_check`
and `complete` (no raw phrase anywhere), for the page and for counting
coverage.

Units, from zephyr's horse notes and checked on known skills: durations and
cooldowns are ten-thousandths of a second; speeds, accelerations, stats and
recovery are the value ÷ 10,000 (m/s, m/s², stat points, share of max
endurance). A duration and a cooldown are the game's base values: it scales
both by the race's length ÷ 1,000 m.

THE WORDING (Daptrius's list and Henry, for the Encyclopedia, 2026-11-12):
a flat stat reads "+40 Power", number first; NO UNIT is shown but seconds
(Henry, 2026-10-23: "Velocity +0.35", never "+0.35 m/s"); and a duration,
like a cooldown that can repeat, is stated against the race's length, because
the game scales both by length ÷ 1,000 m (a 30 s cooldown is 60 s on a
2,000 m race, a 2.4 s effect lasts 4.8 s there). Daptrius confirmed the
cooldown wording on 2026-11-12 and that "activation time and cooldown scale
the same" on 2026-11-13 (board, "Dedicated pages for umas, support cards and
skills").
"""

from dataclasses import dataclass, field

from .skill_conditions import CLAUSE

STYLES = {1: "Front Runner", 2: "Pace Chaser", 3: "Late Surger", 4: "End Closer"}
DISTANCES = {1: "Sprint", 2: "Mile", 3: "Medium", 4: "Long"}
# Global's words for the four phases: "early-race", "mid-race", "late-race",
# and the last spurt (phase_random==3 skills all say "last spurt").
PHASES = {0: "early-race", 1: "mid-race", 2: "late-race", 3: "in the last spurt phase"}
# The same phases as a noun, for "in the first half of ...": PHASES[3] already
# starts with "in", which made "in the first half of in the last spurt phase".
PHASE_NOUNS = {0: "early-race", 1: "mid-race", 2: "late-race", 3: "the last spurt phase"}
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
    # A two-part skill whose second part waits for its first (Daptrius, board,
    # 2026-11-17). Every one of the 146 JP skills using it has two parts.
    ("is_activate_other_skill_detail", 1): "After Part 1 fires",
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
    # The skill pass, 2026-11-03 (notes/course-map-skill-pass.md, B5): each
    # from Global's descriptions of the skills that use it.
    ("is_overtake", 1): "While trying to pass another runner",  # Hard Worker, Lightning Surge, Hot Pursuit
    ("straight_front_type", 2): "On the backstretch",  # Victory Cheer!, Break It Down!
    ("lastspurt", 2): "While still going strong",  # The Duty of Dignity Calls, In One Fell Swoop
    ("is_behind_in", 1): "On the outside",  # Shadow Break, Rising Dragon ("passing ... on the outside")
    ("grade", 100): "In a G1 or similarly major race",  # Raise My Soul's Blade!, G1 Averseness
    ("motivation", 5): "In great mood",  # Us Girlies Keep Winnin'! ♪
    ("is_activate_any_skill", 1): "Just as another skill activates",  # Dreams Donned with Pride!
    ("is_dirtgrade", 1): "In a collaborative graded race",  # Collaborative Graded Races
    ("running_style_equal_popularity_one", 1): "When the favorite runs the same strategy",  # Target in Sight
    ("same_skill_horse_count", 1): "As the only runner with this skill",  # Lone Wolf
    ("is_other_character_activate_advantage_skill", 9): "When a runner uses a recovery skill",  # Catch 'Em Off Guard
    ("order_rate_in20_continue", 1): "Having stayed in the front 20% so far",  # G00 1st. F∞;, Keep Pushing Ahead
    ("order_rate_in50_continue", 1): "Having stayed in the front 50% so far",  # Presents from X
    ("order_rate_in80_continue", 1): "Having stayed in the front 80% so far",  # Schwarzes Schwert
    ("order_rate_out40_continue", 1): "Having stayed behind the front 40% so far",  # Schwarzes Schwert
    # The wording pass of 2026-11-18 (board "Skills: conditions..."). Each from
    # the game's own text of the skills named: Global's description, or for a
    # JP-only skill the JP client's description (never UmaTL's translation).
    ("order_rate_out20_continue", 1): "Having stayed behind the front 20% so far",  # Sunny Breeze
    ("order_rate_out50_continue", 1): "Having stayed behind the front 50% so far",  # 202491, 100441 "toward the back"
    ("order_rate_out70_continue", 1): "Having stayed behind the front 70% so far",  # 110611 "toward the very back"
    ("order_rate_in40_continue", 1): "Having stayed in the front 40% so far",  # 111041 先団をキープ
    ("is_goodstart", 1): "After a good start",  # 101451 スタートが上手くいくと
    ("is_abroad", 1): "At an overseas racecourse",  # 111321 海外レース場なら
    ("is_abroad", 0): "At a racecourse in Japan",  # 113202111, the other branch of 海外レース場なら
    ("corner_count", 0): "On a course with no corners",  # 202991 to 202993 直線のコースなら
    ("near_infront_count", 0): "With no runner close ahead",  # 203061, 203071 前がひらけていると
    ("is_activate_heal_skill", 1): "After recovering endurance with a skill",  # 110771 スキルで持久力を回復していて
    ("run_at_full_speed_random", 1): "At a random moment while spurting at full strength",  # 204222 全開スパート中どこかで
    ("up_slope_random_later_half", 1): "At a random moment on an uphill in the second half of the race",  # 103003111
    # 104702111 終盤が迫った直線
    ("phase_latter_half_straight_random", 1): "At a random moment on a straight in the second half of mid-race",
    # The value is the effect type the other runner's skill has, as 9 above:
    # 31 acceleration (100801 加速スキル), 27 and 22 speed (203371, 108002211 速度スキル).
    ("is_other_character_activate_advantage_skill", 31): "When a runner uses an acceleration skill",
    ("is_other_character_activate_advantage_skill", 27): "When a runner uses a speed skill",
    ("is_other_character_activate_advantage_skill", 22): "When a runner uses a current speed skill",
    # 113401211 1番人気のウマ娘がスキルを発動すると
    ("is_popularity_top_character_activate_advantage_skill", -1): "When the favorite uses a skill",
}

# The overseas racecourses, which no Global skill names yet (racecourse_names
# reads the names off Global's "... Racecourse" skills). From the JP client's
# descriptions of their skills: 202731 ロンシャン, 204232 サンタアニタパーク, 204242 デルマー.
OVERSEAS_RACECOURSES = {10201: "Longchamp", 10202: "Santa Anita Park", 10203: "Del Mar"}
# track_id 10001..10010 is Japan's ten JRA (central) racecourses: 120171 and
# 100801 read it as 中央の芝 / 中央のダート ("central turf / dirt").
JRA_TRACKS = (10001, 10010)

# A phase-numbered place: the variable's value is the phase, as for phase_random.
# 202721 レース中盤のコーナーのどこかで (==1), 203621 レース中盤の直線のどこかで (==1),
# 104202111 終盤の直線のどこかで (==2), 104202111 東京なら終盤始めの直線のどこかで.
PHASE_PLACES = {
    "phase_corner_random": "At a random moment on a corner {phase}",
    "phase_straight_random": "At a random moment on a straight {phase}",
    "phase_first_half_straight_random": "At a random moment on a straight in the first half of {noun}",
}
# A part of a phase, as RANDOM_IN_PHASE's fixed moments are: 110631 レース中盤始めに
# (firsthalf==1), 105602121 最終盤始めに (==3); 101121 最終盤が迫ったとき
# (laterhalf==2), 110801 中盤が迫ったとき (==0); 101003211 終盤始めの早めに (firstquarter==2).
PHASE_PARTS = {
    "phase_firsthalf": "In the first half of {noun}",
    "phase_laterhalf": "In the second half of {noun}",
    "phase_firstquarter": "In the first quarter of {noun}",
}

# variable >= N reads as "... N s or more", for the timers.
SECONDS = {
    "accumulatetime": "After {n} s of racing",
    "blocked_side_continuetime": "Blocked at the side for {n} s or more",
    "blocked_front_continuetime": "Blocked in front for {n} s or more",
    "infront_near_lane_time": "A runner close ahead in your lane for {n} s or more",
    "behind_near_lane_time": "A runner close behind in your lane for {n} s or more",
    "overtake_target_time": "Trying to pass a runner for {n} s or more",
    # Certain Victory: "after being on the heels of another runner"; the
    # variable's own name says without moving up a place.
    "overtake_target_no_order_up_time": "On another runner's heels for {n} s or more without passing",
}

# variable >= N reads as "After using N or more ... skills".
SKILL_COUNTS = {
    "activate_count_all": "skills",
    "activate_count_heal": "recovery skills",
    "activate_count_start": "skills early-race",
    "activate_count_middle": "skills mid-race",
    "activate_count_end_after": "skills late-race",  # Shake It Out
    "activate_count_later_half": "skills in the second half",  # Racing Spirit: Wit
}
# A rushed runner of a style, in the game's style words (the Frenzied family).
RUSHED_STYLES = {"nige": "Front Runner", "senko": "Pace Chaser", "sashi": "Late Surger", "oikomi": "End Closer"}

BASE_STATS = {"base_speed": "Speed", "base_stamina": "Stamina", "base_power": "Power",
              "base_guts": "Guts", "base_wiz": "Wit"}
# The randomly placed moments inside one phase.
RANDOM_IN_PHASE = {
    "phase_random": "At a random moment {phase}",
    "phase_firsthalf_random": "At a random moment in the first half of {noun}",
    "phase_laterhalf_random": "At a random moment in the second half of {noun}",
    "phase_firstquarter_random": "At a random moment in the first quarter of {noun}",
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
    in_phase = {**RANDOM_IN_PHASE, **PHASE_PLACES, **PHASE_PARTS}
    if variable in in_phase and single and single[0] == "==" and single[1] in PHASES:
        return in_phase[variable].format(phase=PHASES[single[1]], noun=PHASE_NOUNS[single[1]])
    if variable == "furlong" and single and single[0] == "==":
        # 0-based: furlong==1 is the 2nd (203061 2ハロン目), ==3 the 4th (101201 4ハロン目).
        return f"In the race's {ordinal(single[1] + 1)} furlong (200 m)"
    if variable == "distance_rate_after_random" and single and single[0] == "==":
        if single[1] == 50:
            return "At a random moment in the second half of the race"
        return f"At a random moment after {single[1]}% of the race"
    if variable == "distance_type":
        return enumerated(clauses, DISTANCES, "In {} races")
    if variable == "running_style":
        text = enumerated(clauses, STYLES, "As a {}")
        # "As a End Closer" read wrong; the other three styles take "a".
        return text.replace("As a End", "As an End") if text else text
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
    if variable == "track_id" and bounds(clauses)[:2] == JRA_TRACKS:
        return "At a JRA racecourse"
    if variable == "is_dirtgrade" and single == ("!=", 1):
        return "Not in a collaborative graded race"  # 109802211, the other branch of 交流重賞
    if variable == "is_exist_skill_id" and single and single[0] == "==":
        name = context.skill_names.get(single[1])
        return f"When a runner in the race has {name}" if name else None  # 201632 「シンパシー」を持つウマ娘がいる
    if variable == "is_exist_chara_id" and single and single[0] == "==":
        name = context.characters.get(single[1])
        return f"When {name} is in the race" if name else None
    if variable == "is_used_skill_id" and all(op == "==" for op, _ in clauses):
        # Two at once means both: 101201 「無二」「無三」が両方発動していると.
        names = [context.skill_names.get(value) for _, value in clauses]
        return f"After using {' and '.join(names)}" if all(names) else None
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
        if single and single[0] == "!=":
            return f"Not on a {number(single[1])} m course"  # 104402111, the other branch of 2200mのレースなら
        return ranged(clauses, "On a course of {n} m or less", "On a course of {n} m or more",
                      "On a course of {lo} to {hi} m", "On a {n} m course")
    if variable == "corner" and single and single[0] == "!=" and single[1] > 0:
        return f"Not on corner {single[1]}"  # 110991 第3コーナーにいないなら
    text = pass_text(variable, clauses)
    return text if text is not None else counted_text(variable, clauses)


def pass_text(variable, clauses):  # pylint: disable=too-many-return-statements,too-many-branches
    """The phrases the skill pass added (notes/course-map-skill-pass.md, B5),
    each sourced from Global's descriptions of the skills that use it."""
    single = clauses[0] if len(clauses) == 1 else None
    low, high, equal, not_equal = bounds(clauses)
    if not_equal is not None:
        return None
    if variable == "corner" and single and single[0] == "==" and single[1] > 0:
        return f"On corner {single[1]}"  # Ahead of the Horizon, "on the third corner"
    if variable == "corner_random" and single and single[0] == "==":
        return f"At a random moment on corner {single[1]}"  # Beautiful Form
    if variable == "distance_diff_top":
        # Metres behind the leader, 2.5 m to a length: <=10 "within 4 lengths"
        # (Top Gear, Solid Strike), <=5 "within 2 lengths" (Ambitious Breeze).
        if high is not None and low is None and equal is None:
            return f"Within {lengths(high * 0.4)} of the lead"
        if low is not None and high is None and equal is None:
            return f"{lengths(low * 0.4)} or more behind the lead"  # 114th Time's the Charm, "far from the lead"
        return None
    if variable == "distance_diff_rate":
        if high is not None and low is None and equal is None:
            return "Close to the lead" if high <= 10 else "Toward the front of the field"  # α-star*; Sunny Breeze
        if low is not None and low >= 75 and high is None:
            return "Around the very back"  # Defeatist
        if low is not None and low >= 50 and high is None:
            return "Toward the back of the field"  # Masterful Gambit, Sturm und Drang: "toward the back"
        return None
    if variable == "distance_diff_top_float" and high is not None and low is None and equal is None:
        # distance_diff_top in tenths of a metre: <=25 is 2.5 m, one length
        # (203131 先頭か先頭から1バ身以内).
        return f"Within {lengths(high / 10 * 0.4)} of the lead"
    places = {"change_order_up_end_after": "late-race",
              "change_order_up_finalcorner_after": "on the final corner or later",
              "change_order_up_middle": "mid-race"}  # 110121 "after passing another runner 3 times mid-race"
    if variable in places and low is not None and high is None:
        times = "once" if low == 1 else f"{low} times"
        # Behold Thine Emperor's Divine Might; Guten Appetit ♪
        return f"After passing another runner {times} {places[variable]}"
    if variable == "motivation" and low is not None and low >= 4 and high is None and equal is None:
        return "In good or great mood"  # Racing Spirit: Mood
    if variable == "motivation" and high == 4 and low is None and equal is None:
        return "Not in great mood"  # 109802221, the other branch of 絶好調なら
    if variable == "visiblehorse" and low is not None and high is None and equal is None:
        return f"With {low} or more runners in view"  # Magical☆Marvelous★World
    if variable == "random_lot" and equal is not None:
        return f"{equal}% chance, rolled before the race"  # Risk-Taker (15), Risk-Maker (30), Lucky Seven (50)
    if variable == "running_style_count_same" and high is not None and high <= 1 and low is None:
        return "With no other runner on the same strategy"  # Maverick
    if variable == "running_style_count_same_rate" and low is not None and high is None:
        return "With many runners on the same strategy"  # Competitive Spirit
    if variable == "same_skill_horse_count" and low is not None and high is None and equal is None:
        return f"With {low} or more runners carrying this skill"  # Sympathy
    if variable in ("temptation_opponent_count_behind", "temptation_opponent_count_infront") and low is not None:
        return "With a rushed runner " + ("behind" if variable.endswith("behind") else "ahead")  # Trick
    if variable.startswith("running_style_temptation_opponent_count_") and low is not None:
        style = RUSHED_STYLES.get(variable.rsplit("_", 1)[1])
        return f"With a rushed {style} in the race" if style else None  # Frenzied Front Runners
    if variable == "remain_distance_viewer_id" and low is not None and high is not None:
        return f"When that runner has between {number(low)} and {number(high)} m to go"  # Cheers of a Fellow Dreamer
    return None


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
    if variable in SKILL_COUNTS and equal is not None and low is None and high is None:
        return f"After using exactly {equal} {SKILL_COUNTS[variable]}"  # 111051 ちょうど7回かちょうど13回
    if variable in SKILL_COUNTS and high is not None and low is None and equal is None:
        return f"Having used {high} or fewer {SKILL_COUNTS[variable]}"  # 110151, the other branch of "at least 7"
    if variable == "activate_count_all_team" and only_low:
        return f"After your team uses {low} or more skills"  # 1300061 チームでスキルを6回以上発動すると
    if variable == "fan_count" and only_low:
        return f"With {number(low)} or more fans"  # 120061 ファンが146,738人以上なら
    if variable == "succession_skill_count" and only_low:
        return f"Holding {low} or more inherited skills"  # 204622 継承スキルを3つ以上持っていると
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
        if low is not None and high is not None and equal is None:
            return f"{stat} {number(low)} to {number(high)}"  # Strong Steps: "exceeds a certain amount" but not greatly
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
        return done(f"{signed(value)} {stats[kind]}")
    if kind == 32:
        # 203781 (+20) 全ての基礎能力がわずかに上がる, 203691 (-20) ...わずかに下がる.
        return done(f"{signed(value)} all stats")
    if kind == 37:
        # 110071 "activate 2 [rare skills] regardless of their conditions", 910071 "activate 1 rare skill".
        count = int(value)
        return done(f"Activates {count} rare skill{'' if count == 1 else 's'} regardless of their conditions")
    if kind == 27:
        return done(f"Velocity {signed(value)}")
    if kind in (21, 22):
        return done(f"Current speed {signed(value)}")
    if kind == 31:
        return done(f"Acceleration {signed(value)}")
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
              (29, 1): "Higher chance of being rushed",  # 202511 "increasing chance of rushing"
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
        # A duration that scales (`duration_usage` other than 1) says so, as a
        # scaling value does: the files give a code per kind of scaling but not
        # what each means, so the page marks it without guessing (Daptrius,
        # board "Skill Effect and Duration Scaling", 2026-11-17).
        "duration": ("Whole race" if duration < 0
                     else f"{seconds(duration)} per 1,000 m"
                     + (" (varies)" if slot.get("duration_usage", 1) != 1 else "")
                     if duration > 0 else None),
        "cooldown": ("Once per race" if cooldown >= ONCE_PER_RACE
                     else f"{seconds(cooldown)} per 1,000 m" if cooldown > 0 else None),
    }


CHAIN_VARIABLE = "is_activate_other_skill_detail"


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
        # How a skill with several slots fires (Daptrius, board, 2026-11-17).
        # Usually the game checks each slot in order and fires the FIRST whose
        # conditions hold: alternatives, which a page calls Trigger 1, 2. A
        # skill whose later slot waits on `is_activate_other_skill_detail`
        # fires one after the other: parts, called Part 1, 2.
        "chained": any(
            CHAIN_VARIABLE in (slot.get(key) or "")
            for slot in slots for key in ("precondition", "condition")
        ),
        "slots": described,
        "complete": not any(item["raw"] for item in phrases),
    }


def raw_conditions(skill):
    """
    The game's own condition strings, one {"after", "when"} per effect slot, in
    the same order as describe()'s `slots`: the skill page's Detailed view
    shows them beside the reworded clauses (Daptrius's list, item 8). Only
    the skill page sends them; the guide payload, which also reads describe(),
    stays as it is.
    """
    slots = skill.effects or [{"precondition": skill.precondition, "condition": skill.condition}]
    return [{"after": slot.get("precondition") or "", "when": slot.get("condition") or ""} for slot in slots]


class LazySkillNames:
    """
    {skill game id: shown name}, loaded on the first look-up only. Only a
    handful of JP skills name another skill (`is_used_skill_id`), and the guide
    page builds a context on every request, so the 2,000-row query runs only
    when a page holds one of them.
    """

    def __init__(self):
        self._names = None

    def get(self, game_id, default=None):
        if self._names is None:
            from .models import Skill  # pylint: disable=import-outside-toplevel
            self._names = {
                row.game_id: row.shown_name
                for row in Skill.objects.only("game_id", "name", "translated_name", "jp_name")
                if row.shown_name
            }
        return self._names.get(game_id, default)

    def __getitem__(self, game_id):
        name = self.get(game_id)
        if name is None:
            raise KeyError(game_id)
        return name


def context_from_database():
    """A TextContext from the database: two queries, plus a third only if a
    skill names another (LazySkillNames)."""
    # Imported here: models import nothing from this module, and a module-level
    # import would make the readable text depend on Django being set up.
    from .models import Skill, Uma  # pylint: disable=import-outside-toplevel
    from .skill_conditions import racecourse_names  # pylint: disable=import-outside-toplevel

    racecourses = {**OVERSEAS_RACECOURSES, **racecourse_names(
        Skill.objects.filter(name__contains="Racecourse").only("name", "condition")
    )}
    skill_names = LazySkillNames()
    characters = {}
    for game_id, name in Uma.objects.exclude(game_id__isnull=True).values_list(
        "game_id", "name",
    ).order_by("id"):
        # Uma.character_id's rule (game_id // 100); "Special Week (Summer)"
        # -> "Special Week", the first row winning.
        characters.setdefault(game_id // 100, name.split(" (")[0])
    return TextContext(racecourses, skill_names, characters)
