"""
A skill's TYPE, for the Skills page's Type filter (Daptrius's list, item 1:
"the icon family is the type"; notes/encyclopedia-plan.md, PR 3).

THE RULE: the game draws a skill with one of ~140 icons, and an icon id is a
family and a variant: `icon_id // 10` is the family, the last digit the
variant (1 white, 2 gold, 6 evolved, and a few others). Every skill in one
family does the same kind of thing, so the family is the type.

HOW THE TABLE WAS MADE (2026-11-12): not from memory. Each family was named by
the effect its skills actually carry (the game's ability type, the same codes
skill_text.ability_text reads), counted over every shown skill. An ability is
written (type, who it lands on); "self" is target 1.

  1001  +Speed (91 of 132 abilities), 80 skills       the flat stat greens,
  1002  +Stamina (70 of 85), 71 skills                one family per stat
  1003  +Power (19 of 28), 18 skills
  1004  +Guts (14 of 15), 14 skills
  1005  +Wit (24 of 40), 26 skills
  1006  Speed, Stamina and Power together (Impulse, Lucky Seven), 5 skills
  2001  velocity (1,225), the plain "speed up"
  2002  recovery (165)
  2004  acceleration (283)
  2005  lane movement / navigation (20)
  2006  the start: gate and slow-start effects
  2009  field of view (11)
  2010-2038  one skill or two each, scenario and event skills (Burning
             Spirit, the Blessings, Racing Spirit): mixed effects
  3001  current speed down on OTHER runners, 19 skills    aimed at other
  3002  acceleration down on the runners ahead, 4         runners, one
  3004  rushed runners take longer to calm down, 4        family per kind
  3005  endurance drained from other runners, 28          of harm
  3007  field of view down on other runners, 5
  0     the uniques (197 shown): a unique's icon is its own art

THE SUB-TYPES (Daptrius, board "Dedicated pages for umas, support cards and
skills", 2026-11-12: "Green skills need to be split out into their sub types.
Each family has common effects, some of which are desired. Others not. Debuffs
also need to be split out."). A sub-type's key is its parent's plus a suffix
("stat_speed", "debuff_stamina"), so "every green" is a prefix match and a
client that knows only the parents still files each skill under one.

CURRENT SPEED is the one type that isn't an icon family (same reply: "It
would be nice to split current speed skills into their own category"). A
skill drawn with the velocity icon that raises its own runner's CURRENT speed
(ability 21 or 22 on self; skill_text words both "Current speed") is
"current_speed", whether or not it raises target speed too: 253 of the 1,176
velocity-family skills. The split is the Speed group's only: a recovery or
acceleration skill that also raises current speed keeps its family's type.

DAPTRIUS CONFIRMS the names before the Encyclopedia merges into develop. An
icon this table doesn't place (a new family) is "other", never a guess.
"""

# Ordered as the filter lists them. A key with an underscore suffix is a
# sub-type of the key before the underscore.
TYPE_LABELS = {
    "unique": "Unique",
    "velocity": "Speed",
    "current_speed": "Current speed",
    "acceleration": "Acceleration",
    "recovery": "Recovery",
    "navigation": "Lane movement",
    "start": "Start",
    "vision": "Vision",
    "stat": "Stat boost",
    "stat_speed": "Stat boost: Speed",
    "stat_stamina": "Stat boost: Stamina",
    "stat_power": "Stat boost: Power",
    "stat_guts": "Stat boost: Guts",
    "stat_wit": "Stat boost: Wit",
    "stat_several": "Stat boost: Several stats",
    "debuff": "Debuff",
    "debuff_speed": "Debuff: Speed",
    "debuff_acceleration": "Debuff: Acceleration",
    "debuff_rushing": "Debuff: Rushing",
    "debuff_stamina": "Debuff: Stamina drain",
    "debuff_vision": "Debuff: Vision",
    "scenario": "Scenario",
    "other": "Other",
}

# family (icon_id // 10) -> type key
FAMILIES = {
    1001: "stat_speed",
    1002: "stat_stamina",
    1003: "stat_power",
    1004: "stat_guts",
    1005: "stat_wit",
    1006: "stat_several",
    2001: "velocity",
    2002: "recovery",
    2004: "acceleration",
    2005: "navigation",
    2006: "start",
    2009: "vision",
    **{family: "scenario" for family in range(2010, 2039)},
    3001: "debuff_speed",
    3002: "debuff_acceleration",
    # 3003 and 3006 hold no skill today: a debuff of a kind nobody has named yet.
    3003: "debuff",
    3004: "debuff_rushing",
    3005: "debuff_stamina",
    3006: "debuff",
    3007: "debuff_vision",
}

# The game's two "current speed" ability types (skill_text.ability_text), and
# the target that means the skill's own runner.
CURRENT_SPEED_ABILITIES = (21, 22)
SELF = 1


def raises_own_current_speed(effects):
    """Does any of the skill's abilities raise its own runner's current speed?
    `effects` is Skill.effects: per condition slot, the abilities."""
    return any(
        ability.get("type") in CURRENT_SPEED_ABILITIES
        and ability.get("target_type", SELF) == SELF
        and ability.get("value", 0) > 0
        for slot in effects or []
        for ability in slot.get("abilities", [])
    )


def skill_type(icon_id, effects=None):
    """
    The TYPE_LABELS key for a skill, from its icon and, for the Speed group,
    its effects. A unique's icon is 0; no icon (an announced skill not yet
    defined) and an unplaced family are "other".
    """
    if icon_id == 0:
        return "unique"
    if icon_id is None:
        return "other"
    family = FAMILIES.get(icon_id // 10, "other")
    if family == "velocity" and raises_own_current_speed(effects):
        return "current_speed"
    return family
