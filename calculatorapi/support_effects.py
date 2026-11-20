"""
A support card's effects at any level, for the Encyclopedia's card page
(notes/encyclopedia-plan.md, Phase 2).

WHAT IS STORED (SupportCard.effects) is what the game's table states: for each
effect type, a value at SOME of the levels 1, 5, 10 ... 50. Special Week's SSR
[The Brightest Star in Japan!] (30001) has Friendship Bonus 10 at level 1, 15
at 25, 15 at 30 and 20 at 45, and nothing else. This module works out the
rest, in ONE function (`value_at`), so if the rule is ever found wrong, one
function changes and no data is rewritten.

THE RULE, and how it was checked. Between two named levels the value moves in
a straight line and is rounded DOWN; before an effect's first named level the
card doesn't have the effect; after its last it holds flat.

  Friendship Bonus at level 35: the nearest named levels are 30 (15) and 45
  (20). That is 5 more over 15 levels, a third of a point per level. Level 35
  is 5 levels in: 15 + 5 x 1/3 = 16.67, shown as 16.

Daptrius checked it on 2026-11-12 (board, "Dedicated pages for umas, support
cards and skills") with that card at five levels, every one between named
ones. All 25 values agree with the rule:

  level              35   36   40   41   42
  Friendship Bonus   16   17   18   18   19
  Mood Effect        45   46   50   51   52
  Initial Friendship 21   22   23   23   24
  Hint Frequency     33   34   36   37   38
  Specialty Priority 25   26   30   31   32

and at all five Power Bonus (first named at 35) reads 1 while Training
Effectiveness (first named at 45) reads "Unlocked at level 45": no value
before the first named level. His shots are of the card's stepper on
GameTora, not the game's own screen, which the plan had asked for. They are
kept beside the plan (notes/encyclopedia/card-effect-check/), and the test
suite holds the table above cell for cell.

HOW A VALUE IS WRITTEN (percent, a plain number, "Lv 2") is not in the game
files. calculatorapi/data/support_effect_types.json holds each type's name and
help text (the game's own English, text_data 151 and 154) and a `format`.
Sixteen are confirmed (`format_confirmed`). Seven from the same shots:
Friendship Bonus, Mood Effect and Hint Frequency are percents, Hint Levels a
level, Power Bonus, Initial Friendship Gauge and Specialty Priority plain
numbers. Nine from Daptrius's list on 2026-11-13 (same board post): Training
Effectiveness, Race Bonus, Fan Bonus, Event Recovery, Event Effectiveness,
Failure Protection and Energy Cost Reduction are percents, Skill Point Bonus
and Wit Friendship Recovery plain numbers. Every other type (the stat
bonuses, the initial and max stats) is sent as the plain number the files
hold, never dressed with a unit by a guess. Minigame Effectiveness is one of
those: he has never seen it, and no card in either client has it.

WHAT THE PAGE DOESN'T ADD UP. The table and the unique effect are shown side
by side, never summed, because they don't simply add (Daptrius, same reply):
a unique Friendship Bonus or Specialty Priority multiplies with the card's
own. A card with Specialty Priority 35 and a unique 40 behaves like
(100 + 35) x 1.4 = 189, so 89, not 75. Everything else adds, as far as he
knows. The API names which of the two a unique multiplies (`multiplies`);
the page stopped saying so in a line on 2026-11-13 (Daptrius: noise).

WHAT A UNIQUE EFFECT MEANS. Its conditional types (101 to 122) each have a
reader, in the section "What the unique effects mean" below, with where each
meaning came from (notes/unique-effects-plan.md).
"""

import json
import math
from pathlib import Path

# support_card_limit: the level cap at each limit break, 0 to 4 (MLB), by
# rarity (1 R, 2 SR, 3 SSR). Read off the global client's table, 2026-11-12.
CAPS = {
    1: (20, 25, 30, 35, 40),
    2: (25, 30, 35, 40, 45),
    3: (30, 35, 40, 45, 50),
}

TYPES_FILE = Path(__file__).resolve().parent / "data" / "support_effect_types.json"


def load_types(path=TYPES_FILE):
    """{type: {"name", "help", "format", "format_confirmed"}} from the committed file."""
    return {row["type"]: row for row in json.loads(Path(path).read_text(encoding="utf-8"))}


# The game's fixed vocabulary: read once, when the module loads.
TYPES = load_types()


def caps(rarity):
    """The level cap at each limit break for a card of this rarity; () when the rarity is unknown."""
    return CAPS.get(rarity, ())


def named_points(levels):
    """{"1": 10, "25": 15} -> [(1, 10), (25, 15)], in level order."""
    return sorted((int(level), value) for level, value in levels.items())


def value_at(levels, level):
    """
    An effect's value at `level`, from the levels the game names (the rule and
    its check are at the top of this file). None before the first named level:
    the card doesn't have the effect yet.
    """
    points = named_points(levels)
    if not points or level < points[0][0]:
        return None
    for (low_level, low), (high_level, high) in zip(points, points[1:]):
        if low_level <= level <= high_level:
            # A straight line between the two named levels, rounded down.
            return math.floor(low + (high - low) * (level - low_level) / (high_level - low_level))
    # Past the last named level it holds.
    return points[-1][1]


def table(effects, rarity):
    """
    The card page's effects table: each effect with its name, help text,
    format, the level that unlocks it, and its value at every level from 1 to
    the card's top cap (index 0 is level 1; None before it unlocks).

    In the order the card gains them: by first named level, then by type,
    which is the order a card's own effect list reads in.
    """
    levels = caps(rarity)
    if not levels:
        return []
    top = levels[-1]
    rows = []
    for effect in effects or []:
        known = TYPES.get(effect["type"])
        points = named_points(effect["levels"])
        # A type the file doesn't name is one the game added since: left out
        # until its name is extracted, never shown as a bare number.
        if known is None or not points or points[0][0] > top:
            continue
        rows.append({
            "type": effect["type"],
            "name": known["name"],
            "help": known["help"],
            "format": known["format"],
            "unlock": points[0][0],
            "values": [value_at(effect["levels"], level) for level in range(1, top + 1)],
        })
    return sorted(rows, key=lambda row: (row["unlock"], row["type"]))


# Friendship Bonus and Specialty Priority: a unique effect's value of either
# multiplies with the card's own (the top of this file).
MULTIPLIED_TYPES = (1, 19)

# ── What the unique effects mean (notes/unique-effects-plan.md) ──
#
# A unique effect is stored as up to two slots, each {"type", "value",
# "params"}. A type under 100 with no params is a plain effect: Guts Bonus 1.
# Types 101 to 122 are conditional, and each reads its numbers its own way:
# one small reader per type below, in READERS. Each reader turns a slot into
# PARTS, every part one thing the card gives:
#
#   type       the effect type it gives (support_effect_types.json), or None
#              for something that isn't an effect type (a failure-rate roll)
#   name       that type's name in the game's own English
#   value      the MOST it gives: what the card list's effect sort counts
#   format     how `value` is written ("percent", "number", "level")
#   condition  "Friendship Gauge 80+" when it needs the gauge, else None
#   line       the phrase the page shows, numbers and units included
#   scope      "card" (this card's own numbers) or "deck" (other cards')
#   sorts      the effect types the sort counts `value` under; empty for a
#              deck effect, or one whose stat depends on the deck
#
# A slot no reader can read gives None, and the page then shows no numbers:
# a type the game adds later stays blank instead of being guessed.
#
# WHERE THE MEANINGS COME FROM. Type 101 was checked against all 33 Global
# rows' sentences (2026-11-14). Types 102 to 122, and the effect types 33 and
# 41, come from Daptrius's list and answers on the team board, 2026-11-16
# ("Support Card Uniques"); every Global card of types 102 to 115 also names
# its effect in the game's own sentence, and the test suite holds that check.
# His descriptions were GameTora's, so they explain the numbers only: every
# line here is written from the numbers and the game's own effect names.

FRIENDSHIP_GAUGE = 101

# Two effect types the game files never name, on Global or JP (text_data 151
# stops at 32). Daptrius, 2026-11-16: 33 is +1 to hint levels, 41 is +1 to
# all five training stat bonuses. They're written with the game's names for
# the types they stand for, and sort under those.
ALIASES = {33: (17,), 41: (3, 4, 5, 6, 7)}

# Type 116's `value`: which skills it counts (Daptrius, 2026-11-16; 2 is
# acceleration, which he verified).
SKILL_KINDS = {1: "speed", 2: "acceleration", 3: "recovery"}

# Up to five support cards train on one facility, and a deck holds six whose
# Friendship Gauge tops out at 100 (Daptrius's tops for types 109 and 110).
CARDS_PER_TRAINING = 5
DECK_GAUGE_TOTAL = 600
# A facility levels up from 1 to 5 (type 111).
TOP_FACILITY_LEVEL = 5


def written(value, fmt):
    """A value as the page writes it: 10 -> "10%" for a percent, "Lv 1" for a
    level, else the number ("10,000"). The card page's `effectValue()`
    (frontend utils/encyclopedia.ts) writes the effects table the same way."""
    text = f"{value:,}" if isinstance(value, int) else f"{value:g}"
    if fmt == "percent":
        return f"{text}%"
    if fmt == "level":
        return f"Lv {text}"
    return text


def part(kind, value, line, condition=None, *, scope="card", name=None, fmt=None, sorts=None):  # pylint: disable=too-many-arguments
    """One part (the shape is at the top of this section). `kind` None is a
    part that isn't an effect type, so it needs its own `name` and `fmt`."""
    known = TYPES.get(kind, {})
    if sorts is None:
        sorts = (kind,) if scope == "card" and kind in TYPES else ()
    return {"type": kind, "name": name or known["name"], "value": value, "format": fmt or known["format"],
            "condition": condition, "line": line, "scope": scope, "sorts": list(sorts)}


def typed_part(kind, amount, condition=None):
    """A plain "<effect> <amount>" part, the effect types 33 and 41 included.
    None for a type the names file doesn't have."""
    if kind in ALIASES:
        targets = ALIASES[kind]
        names = [TYPES[target]["name"] for target in targets]
        fmt = TYPES[targets[0]]["format"]
        # "Speed, Stamina, Power, Guts and Wit Bonus": the shared last word
        # ("Bonus") only once, on the last name.
        words = [name.rsplit(" ", 1)[0] for name in names[:-1]] + names[-1:]
        name = joined(words)
        return part(targets[0] if len(targets) == 1 else kind, amount, f"{name} {written(amount, fmt)}",
                    condition, name=name, fmt=fmt, sorts=targets)
    if kind not in TYPES:
        return None
    return part(kind, amount, f"{TYPES[kind]['name']} {written(amount, TYPES[kind]['format'])}", condition)


def effect_line(kind, text):
    """`text` after the effect's own name: ("Training Effectiveness", "...")."""
    return f"{TYPES[kind]['name']} {text}"


def pct(kind, value):
    """`value` written in `kind`'s format."""
    return written(value, TYPES[kind]["format"])


TRAINING = 8  # Training Effectiveness


def read_gauge(value, params):
    """101: at Friendship Gauge `value`+, up to two (type, amount) pairs in
    `params`, a 0 type for an empty slot. 30053: 80, [3, 1, 30, 1] is Speed
    Bonus 1 and Skill Point Bonus 1 (Friendship Gauge 80+)."""
    condition = f"Friendship Gauge {value}+"
    return [typed_part(kind, amount, condition) for kind, amount in zip(params[0::2], params[1::2]) if kind]


def read_off_type(value, params):
    """102: at gauge `value`+, Training Effectiveness params[0] on a training
    that isn't the card's type. 30083: 80, [20]."""
    line = effect_line(TRAINING, f"{pct(TRAINING, params[0])} on a training that isn't its type")
    return [part(TRAINING, params[0], line, f"Friendship Gauge {value}+")]


def read_card_types(value, params):
    """103: Training Effectiveness params[0] with `value`+ card types in the
    deck. 30085: 5, [15]."""
    return [part(TRAINING, params[0], effect_line(
        TRAINING, f"{pct(TRAINING, params[0])} with {value} or more card types in the deck"))]


def read_fans(value, params):
    """104: Training Effectiveness 1 per `value` fans, up to params[0].
    30086: 10000, [20] (the game: "up to 200,000 fans")."""
    return [part(TRAINING, params[0], effect_line(
        TRAINING, f"{pct(TRAINING, 1)} per {value:,} fans, up to {pct(TRAINING, params[0])}"))]


def read_deck_initial_stats(value, params):
    """105: +`value` to the initial stat of each deck card's type; Friend and
    Group cards +params[0] to every stat. 30090: 10, [2]. Which stats depends
    on the deck, so it doesn't sort."""
    return [part(None, value, f"Initial stats +{value} for each card in the deck, in that card's stat "
                              f"(Friend and Group cards: +{params[0]} to every stat)",
                 name="Initial stats", fmt="number")]


def read_stacks(value, params):
    """106: params[1] of effect params[0] per Friendship Training with this
    card, up to `value` times. 30112: 5, [1, 3] is Friendship Bonus 3% a time,
    up to 15%."""
    kind, amount = params[0], params[1]
    top = amount * value
    return [part(kind, top, effect_line(
        kind, f"{pct(kind, amount)} per Friendship Training with this card, up to {pct(kind, top)}"))]


def read_low_energy(value, params):
    """107: effect `value`, from params[3] at full Energy up to params[2] at
    params[1] Energy or less (Daptrius: 5 at 100 to 15 at 30). 30094:
    1, [10, 30, 15, 5]. params[0] isn't explained, and isn't shown."""
    low, top, threshold = params[3], params[2], params[1]
    return [part(value, top, effect_line(
        value, f"{pct(value, low)} to {pct(value, top)}, more the lower your Energy "
               f"({pct(value, top)} at {threshold} Energy or less)"))]


def read_max_energy(value, params):
    """108: effect `value` params[2], plus params[1] / 100 per point of max
    Energy over params[0], up to params[3]. 30095: 8, [100, 75, 5, 20]:
    5 + (120 - 100) x 0.75 = 20 at 120 max Energy. Smooth per point
    (Daptrius: max Energy comes in steps of 4 anyway)."""
    base, rate, top = params[2], params[1] / 100, params[3]
    return [part(value, top, effect_line(
        value, f"{pct(value, base)}, plus {pct(value, rate)} per point of max Energy over {params[0]}, "
               f"up to {pct(value, top)}"))]


def read_deck_gauge(value, params):
    """109: effect `value` 1 per params[0] total Friendship Gauge across the
    deck. 30099: 8, [30]; six cards at 100 is 600, so the top is 20."""
    top = DECK_GAUGE_TOTAL // params[0]
    return [part(value, top, effect_line(
        value, f"{pct(value, 1)} per {params[0]} total Friendship Gauge in the deck, up to {pct(value, top)}"))]


def read_cards_training(value, params):
    """110: effect `value` params[0] per support card on the training, the
    card itself included, so never below one step (Daptrius, 2026-11-16: 5%
    to 25%). 30102: 8, [5]."""
    step, top = params[0], params[0] * CARDS_PER_TRAINING
    return [part(value, top, effect_line(
        value, f"{pct(value, step)} per support card on the training, itself included "
               f"({pct(value, step)} to {pct(value, top)})"))]


def read_facility_level(value, params):
    """111: effect `value` params[0] per level of the facility it trains on,
    levels 1 to 5. 30107: 8, [5]."""
    top = params[0] * TOP_FACILITY_LEVEL
    return [part(value, top, effect_line(
        value, f"{pct(value, params[0])} per level of the training facility, "
               f"up to {pct(value, top)} at level {TOP_FACILITY_LEVEL}"))]


def read_no_failure(value, _params):
    """112: a `value`% chance a training's failure rate drops to 0. 30108: 20."""
    return [part(None, value, f"{written(value, 'percent')} chance a training's failure rate drops to 0%",
                 name="Failure rate", fmt="percent")]


def read_on_friendship(value, params):
    """113: effect `value` params[0] on Friendship Training. 30127: 2, [60] is
    Mood Effect 60%; 30052: 28, [30] is Energy Cost Reduction 30%."""
    return [part(value, params[0], effect_line(value, f"{pct(value, params[0])} on Friendship Training"))]


def read_high_energy(value, params):
    """114: effect `value` from params[0] at 0 Energy to params[1] at 100.
    30115: 8, [5, 20]."""
    return [part(value, params[1], effect_line(
        value, f"{pct(value, params[0])} to {pct(value, params[1])}, more the higher your Energy"))]


def read_deck_effect(value, params):
    """115: every card in the deck gets effect `value` params[0]. 30146:
    14, [5] is Initial Friendship Gauge 5. Other cards' numbers: not sorted."""
    return [part(value, params[0], effect_line(value, f"{pct(value, params[0])} for every card in the deck"),
                 scope="deck")]


def read_skills(value, params):
    """116: effect params[0], params[1] per learned skill of kind `value`, up
    to params[2] skills. 30154: 3, [3, 1, 3] is Speed Bonus 1 per recovery
    skill, up to 3."""
    kind, amount, most = params[0], params[1], params[2]
    if value not in SKILL_KINDS or kind not in TYPES:
        return [None]
    return [part(kind, amount * most, effect_line(
        kind, f"{pct(kind, amount)} per {SKILL_KINDS[value]} skill learned, up to {most} skills"))]


def read_all_facilities(value, params):
    """117: effect `value` 1 per level across all five facilities (5 to
    params[0]), up to params[1]. 30148: 8, [25, 20]."""
    return [part(value, params[1], effect_line(
        value, f"{pct(value, 1)} per level across all five facilities, up to {pct(value, params[1])}"))]


def read_two_trainings(_value, params):
    """118: at gauge params[0]+, the card can appear in 2 trainings at once.
    30160: 1, [60]."""
    return [part(None, 2, "Can appear in 2 trainings at once", f"Friendship Gauge {params[0]}+",
                 name="Trainings", fmt="number")]


def read_deck_priority(value, params):
    """119: at gauge params[1]+, every card in the deck gets Specialty
    Priority `value`. 30188: 50, [1, 60]. Daptrius, 2026-11-16: list the
    amount, never say whether it adds or multiplies; params[0] isn't
    explained."""
    return [part(19, value, effect_line(19, f"{pct(19, value)} for every card in the deck"),
                 f"Friendship Gauge {params[1]}+", scope="deck")]


def read_deck_stat_bonus(_value, params):
    """120: at gauge params[0]+, a stat bonus of params[1] for each deck card,
    in that card's stat, up to params[2] per stat; Friend and Group cards give
    Skill Point Bonus (an amount the data doesn't state). 30264: 1,
    [80, 1, 2]. Which stats depends on the deck, so it doesn't sort."""
    return [part(None, params[1], f"Stat bonus {params[1]} for each card in the deck, in that card's stat, "
                                  f"up to {params[2]} per stat; Friend and Group cards give Skill Point Bonus",
                 f"Friendship Gauge {params[0]}+", name="Stat bonuses", fmt="number")]


def read_deck_gauge_gain(value, params):
    """121: every support gains `value` more Friendship Gauge from training,
    params[0] when training with this card. 30207: 1, [2]."""
    return [part(None, params[0], f"Every support gains {value} more Friendship Gauge from training, "
                                  f"{params[0]} when training with this card",
                 scope="deck", name="Friendship Gauge", fmt="number")]


def read_next_turn(value, params):
    """122: supports that train with this card get effect `value` params[0]
    on the next turn. 30257: 19, [60] is Specialty Priority 60."""
    return [part(value, params[0], effect_line(
        value, f"{pct(value, params[0])} on the next turn for supports that train with this card"), scope="deck")]


READERS = {
    101: read_gauge, 102: read_off_type, 103: read_card_types, 104: read_fans,
    105: read_deck_initial_stats, 106: read_stacks, 107: read_low_energy, 108: read_max_energy,
    109: read_deck_gauge, 110: read_cards_training, 111: read_facility_level, 112: read_no_failure,
    113: read_on_friendship, 114: read_high_energy, 115: read_deck_effect, 116: read_skills,
    117: read_all_facilities, 118: read_two_trainings, 119: read_deck_priority, 120: read_deck_stat_bonus,
    121: read_deck_gauge_gain, 122: read_next_turn,
}

# The types whose reader names an effect by the slot's `value` or `params`,
# which must be one the names file has.
EFFECT_IN_VALUE = (107, 108, 109, 110, 111, 113, 114, 115, 117, 122)


def effect_amounts(effect):
    """
    One unique-effect slot as parts (the shape is at the top of this
    section), or None when the slot can't be read: a type no reader knows, or
    an effect type the names file doesn't have.
    """
    kind = effect["type"]
    params = list(effect.get("params") or []) + [0, 0, 0, 0]
    if kind < 100 and not effect.get("params"):
        parts = [typed_part(kind, effect["value"])]
    elif kind in READERS:
        if kind in EFFECT_IN_VALUE and effect["value"] not in TYPES:
            return None
        if kind == 106 and params[0] not in TYPES:
            return None
        parts = READERS[kind](effect["value"], params)
    else:
        return None
    if not parts or any(found is None for found in parts):
        return None
    return parts


def unique_amounts(unique):
    """
    Every part of a unique effect (see `effect_amounts`), or None when any
    slot can't be read: a partial list would understate the effect on the
    page. The sort reads the slots one by one instead (`unique_parts`).
    """
    parts = []
    for effect in unique.get("effects", []):
        amounts = effect_amounts(effect)
        if amounts is None:
            return None
        parts.extend(amounts)
    return parts or None


def joined(names):
    """["A"] -> "A"; ["A", "B"] -> "A and B"; ["A", "B", "C"] -> "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def named_text(amounts):
    """
    The words for a unique effect with no sentence of its own: the parts'
    lines, a condition in brackets after the ones it covers ("Speed Bonus 1
    and Skill Point Bonus 1 (Friendship Gauge 80+)"). The numbers are in it,
    so the page doesn't repeat them under it (`from_amounts`).
    """
    groups = {}
    for found in amounts:
        groups.setdefault(found["condition"], []).append(found["line"])
    return ", ".join(
        joined(lines) + (f" ({condition})" if condition else "") for condition, lines in groups.items()
    )


def multiplied(text, effects, amounts=None):
    """
    The effects a unique effect multiplies instead of adds, by name: those of
    MULTIPLIED_TYPES that it gives the card itself AND the card's own table
    has (with none of its own there is nothing to multiply with). What it
    gives is read from its numbers when they're known, else from its words. A
    deck effect never counts: Daptrius asked that Ryoka Tsurugi's Specialty
    Priority for every card be listed as an amount only (2026-11-16).
    """
    own = {effect["type"] for effect in effects or []}
    if amounts is not None:
        gives = {kind for found in amounts for kind in found["sorts"]}
        return [TYPES[kind]["name"] for kind in MULTIPLIED_TYPES if kind in own and kind in gives]
    return [TYPES[kind]["name"] for kind in MULTIPLIED_TYPES if kind in own and TYPES[kind]["name"] in text]


def unique_effect(unique, effects=None):
    """
    The unique effect for the page: {"level", "text", "translated",
    "from_amounts", "amounts", "multiplies"}, or None when the card has none
    or there are no confirmed words for it. `effects` is the card's own stored
    table, for `multiplies`.

    The text is the game's own sentence where the global client has one, else
    UmaTL's (`translated_text`, the import's; `translated` is then True and
    the page credits UmaTL), else the parts' own lines (`named_text`;
    `from_amounts` is then True), only when every slot can be read. `amounts`
    holds the parts whenever every slot can be read, beside either sentence
    too: neither states the numbers. Each part goes out without `sorts`,
    which only the card list's sort reads (`unique_parts`).
    """
    if not unique:
        return None
    amounts = unique_amounts(unique)
    text = (unique.get("text") or "").strip()
    translation = (unique.get("translated_text") or "").strip()
    translated = not text and bool(translation)
    if not text:
        text = translation
    from_amounts = False
    if not text:
        if amounts is None:
            return None
        text, from_amounts = named_text(amounts), True
    sent = None
    if amounts is not None:
        sent = [{key: value for key, value in found.items() if key != "sorts"} for found in amounts]
    return {"level": unique["level"], "text": text, "translated": translated, "from_amounts": from_amounts,
            "amounts": sent, "multiplies": multiplied(text, effects, amounts)}


# ── For sorting the card list by an effect (notes/card-effect-sort-plan.md) ──


def at_caps(effects, rarity):
    """
    Each effect type's value at the card's five limit-break caps (index 0 is
    0LB, 4 is MLB), by `value_at`, so the list sorts on the same numbers the
    card page shows. None at a cap the effect hasn't unlocked by. Types the
    file doesn't name are left out, as `table` leaves them out.
    """
    levels = caps(rarity)
    if not levels:
        return {}
    found = {}
    for effect in effects or []:
        if effect["type"] not in TYPES or not effect.get("levels"):
            continue
        values = [value_at(effect["levels"], level) for level in levels]
        if any(value is not None for value in values):
            found[effect["type"]] = values
    return found


def unique_parts(unique):
    """
    The unique effect's parts for a sort: {"level", "values": {type: value},
    "conditional"}. Each part this card gives itself counts at its most (as
    if its condition is met; owner, 2026-11-14 for the gauge, 2026-11-16 for
    the rest), summed under every type in its `sorts`. A deck effect, or one
    whose stat depends on the deck, counts nowhere (owner, 2026-11-16). A
    slot no reader can read is left out and `conditional` says so, so the
    list can mark the card instead of guessing. None when the card has no
    unique effect, or none with any parts.
    """
    if not unique or not unique.get("effects"):
        return None
    values, conditional = {}, False
    for effect in unique["effects"]:
        amounts = effect_amounts(effect)
        if amounts is None:
            conditional = True
            continue
        for found in amounts:
            for kind in found["sorts"]:
                values[kind] = values.get(kind, 0) + found["value"]
    return {"level": unique["level"], "values": values, "conditional": conditional}
