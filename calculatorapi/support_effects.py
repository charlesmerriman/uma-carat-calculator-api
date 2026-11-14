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

# A unique effect's type 101 reads "while the Friendship Gauge is at `value`
# or more, gain these": `params` holds up to two (effect type, amount) pairs,
# [type, amount, type, amount], a 0 type for an empty slot. Card 30053's
# value 80 with params 3, 1, 30, 1 is Speed Bonus 1 and Skill Point Bonus 1,
# and the game's own sentence for it is "Speed Bonus and Skill Point Bonus
# (Friendship Gauge 80+)". Checked 2026-11-14 against all 33 type-101 rows in
# the global client: every effect it names and the threshold are in the
# game's sentence (the test suite holds that check over the committed
# snapshot). Every other type of 100 and up is still undecoded.
# notes/encyclopedia-jp-fills-plan.md, PR 1.
FRIENDSHIP_GAUGE = 101


def effect_amounts(effect):
    """
    One unique-effect slot as the game's numbers: [{"type", "name", "value",
    "format", "condition"}], `condition` None for a plain part and
    "Friendship Gauge 80+" for a decoded type 101. None when the slot can't be
    read: an undecoded type, or an effect type the names file doesn't have.
    """
    if effect["type"] in TYPES and not effect.get("params"):
        pairs, condition = [(effect["type"], effect["value"])], None
    elif effect["type"] == FRIENDSHIP_GAUGE and effect.get("params"):
        params = list(effect["params"]) + [0, 0, 0, 0]
        pairs = [(params[index], params[index + 1]) for index in (0, 2) if params[index]]
        condition = f"Friendship Gauge {effect['value']}+"
    else:
        return None
    if not pairs or any(kind not in TYPES for kind, _ in pairs):
        return None
    return [
        {"type": kind, "name": TYPES[kind]["name"], "value": value, "format": TYPES[kind]["format"],
         "condition": condition}
        for kind, value in pairs
    ]


def unique_amounts(unique):
    """
    Every part of a unique effect as numbers (see `effect_amounts`), or None
    when any slot can't be read: a partial list would understate the effect
    on the page. The sort reads the slots one by one instead (`unique_parts`).
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
    The line for a unique effect with no sentence of its own, in the game's
    style: the effects by name, a condition in brackets after the ones it
    covers ("Speed Bonus and Skill Point Bonus (Friendship Gauge 80+)"). The
    numbers are sent beside it in `amounts`.
    """
    groups = {}
    for part in amounts:
        groups.setdefault(part["condition"], []).append(part["name"])
    return ", ".join(
        joined(names) + (f" ({condition})" if condition else "") for condition, names in groups.items()
    )


def multiplied(text, effects, amounts=None):
    """
    The effects a unique effect multiplies instead of adds, by name: those of
    MULTIPLIED_TYPES that it gives AND the card's own table has (with none of
    its own there is nothing to multiply with). What it gives is read from its
    numbers when they're known, else from its words.
    """
    own = {effect["type"] for effect in effects or []}
    if amounts is not None:
        gives = {part["type"] for part in amounts}
        return [TYPES[kind]["name"] for kind in MULTIPLIED_TYPES if kind in own and kind in gives]
    return [TYPES[kind]["name"] for kind in MULTIPLIED_TYPES if kind in own and TYPES[kind]["name"] in text]


def unique_effect(unique, effects=None):
    """
    The unique effect for the page: {"level", "text", "amounts",
    "multiplies"}, or None when the card has none or there are no confirmed
    words for it. `effects` is the card's own stored table, for `multiplies`.

    The text is the game's own sentence where the global client has one. A
    JP-only card has no English sentence, so its effects are named instead
    (`named_text`), and only when every slot can be read. `amounts` holds the
    numbers (`unique_amounts`) whenever every slot can be read, for a card
    with the game's sentence too: that sentence never states them.
    """
    if not unique:
        return None
    amounts = unique_amounts(unique)
    text = (unique.get("text") or "").strip()
    if not text:
        if amounts is None:
            return None
        text = named_text(amounts)
    return {"level": unique["level"], "text": text, "amounts": amounts,
            "multiplies": multiplied(text, effects, amounts)}


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
    "conditional"}. Plain parts and decoded friendship-gauge parts (type 101,
    counted as if the gauge is reached; owner, 2026-11-14) are summed by type.
    A slot nobody here has decoded is left out and `conditional` says so, so
    the list can mark the card instead of guessing. None when the card has no
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
        for part in amounts:
            values[part["type"]] = values.get(part["type"], 0) + part["value"]
    return {"level": unique["level"], "values": values, "conditional": conditional}
