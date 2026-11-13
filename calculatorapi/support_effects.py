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
Seven formats are confirmed from the same shots (`format_confirmed`): Friendship
Bonus, Mood Effect and Hint Frequency are percents, Hint Levels a level, Power
Bonus, Initial Friendship Gauge and Specialty Priority plain numbers. Every
other type is sent as the plain number the files hold, never dressed with a
unit by a guess, until someone confirms it.
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


def unique_effect(unique):
    """
    The unique effect for the page: {"level", "text"}, or None when the card
    has none or there are no confirmed words for it.

    The text is the game's own sentence where the global client has one. A
    JP-only card has no English sentence, so its typed effects are named
    instead ("Guts Bonus 1, Initial Guts 20"), and only when every one is a
    plain named type: a conditional type (100 and up) carries numbers nobody
    here has decoded.
    """
    if not unique:
        return None
    if unique.get("text"):
        return {"level": unique["level"], "text": unique["text"]}
    parts = []
    for effect in unique.get("effects", []):
        known = TYPES.get(effect["type"])
        if known is None or effect.get("params"):
            return None
        parts.append(f"{known['name']} {effect['value']}")
    if not parts:
        return None
    return {"level": unique["level"], "text": ", ".join(parts)}
