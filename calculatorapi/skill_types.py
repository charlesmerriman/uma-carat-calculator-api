"""
A skill's TYPE, for the Skills page's Type filter (Daptrius's list, item 1:
"the icon family is the type"; notes/encyclopedia-plan.md, PR 3).

THE RULE: the game draws a skill with one of ~140 icons, and an icon id is a
family and a variant: `icon_id // 10` is the family, the last digit the
variant (1 white, 2 gold, 6 evolved, and a few others). Every skill in one
family does the same kind of thing, so the family is the type.

HOW THE TABLE WAS MADE (2026-11-12): not from memory. Each family was named by
the effect its skills actually carry (the game's ability type, the same codes
skill_text.ability_text reads), counted over every shown skill:

  1001-1005  a flat stat: Speed (92 of 98 abilities), Stamina, Power, Guts, Wit
  1006       several stats at once (Impulse)
  2001       velocity (1,225), the plain "speed up"
  2002       recovery (165)
  2004       acceleration (283)
  2005       lane movement / navigation (20)
  2006       the start: gate and slow-start effects
  2009       field of view (11)
  2010-2038  one skill or two each, scenario and event skills (Burning
             Spirit, the Blessings, Racing Spirit): mixed effects
  3001-3007  aimed at OTHER runners: speed, acceleration and endurance
             drains, rushing, vision
  0          the uniques (197 shown): a unique's icon is its own art

DAPTRIUS CONFIRMS the grouping and the names before the Encyclopedia merges
into develop. An icon this table doesn't place (a new family) is "other",
never a guess.
"""

# Ordered as the filter lists them.
TYPE_LABELS = {
    "unique": "Unique",
    "stat": "Stat boost",
    "velocity": "Speed",
    "acceleration": "Acceleration",
    "recovery": "Recovery",
    "navigation": "Lane movement",
    "start": "Start",
    "vision": "Vision",
    "debuff": "Debuff",
    "scenario": "Scenario",
    "other": "Other",
}

# family (icon_id // 10) -> type key
FAMILIES = {
    **{family: "stat" for family in range(1001, 1007)},
    2001: "velocity",
    2002: "recovery",
    2004: "acceleration",
    2005: "navigation",
    2006: "start",
    2009: "vision",
    **{family: "scenario" for family in range(2010, 2039)},
    **{family: "debuff" for family in range(3001, 3008)},
}


def skill_type(icon_id):
    """The TYPE_LABELS key for a skill's icon. A unique's icon is 0; no icon
    (an announced skill not yet defined) and an unplaced family are "other"."""
    if icon_id == 0:
        return "unique"
    if icon_id is None:
        return "other"
    return FAMILIES.get(icon_id // 10, "other")
