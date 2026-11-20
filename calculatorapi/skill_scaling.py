"""
What a scaling skill's number or duration depends on, by the game's code.

The game files mark a scaling effect with a code (`value_usage` on an ability,
`duration_usage` on a slot; 1 means fixed) and never say what the code means.
The meanings below are Daptrius's, from GameTora's figures (board post "Skill
Effect and Duration Scaling", 2026-11-18). Nobody has checked them in game;
Daptrius and the owner chose to show them anyway (2026-11-19/20): the figures
are readily available game facts, and only GameTora's WORDING is off limits,
so every label here is our own. Plan: notes/skill-scaling-plan.md.

Each code is a list of bands, and each band changes the skill's own base:
times a multiplier, or plus a flat amount. A code is shared by a skill and
its inherited copy (Wisdom of the Sun, 0.35, and Sun's Blessing, 0.15, are
both code 2), so we store the rule, never a total, and the numbers are
worked out from each skill's own base. A flat amount is the same on both
copies (Daptrius, 2026-11-18).

A code missing here (value 40 and 41: nobody knows the bands yet) keeps the
plain "(varies)" mark.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    when: str        # the table's left column: "Under 20,000"
    times: float = 1.0
    plus: float = 0.0  # in shown units: m/s for a value, seconds for a duration

    def apply(self, base):
        return base * self.times + self.plus


@dataclass(frozen=True)
class Scaling:
    basis: str      # ends the inline line: "Velocity +0.28 to +0.42, by fan count"
    heading: str    # the table's left column heading
    bands: tuple


def _stat(name):
    # Burning Spirit and Ignited Spirit, one code per stat. All five use the
    # same bands (Daptrius, 2026-11-18): the team's combined base stat, after
    # mood and before green skills.
    return Scaling(
        f"by your team's combined base {name}", f"Team's base {name}, combined", (
            Band("Under 1,200", 0.8), Band("1,200 to 1,799", 0.9), Band("1,800 to 2,599", 1.0),
            Band("2,600 to 3,599", 1.1), Band("3,600 or more", 1.2),
        ))


def _behind(plus):
    return Scaling("by distance behind the leader", "Behind the leader", (
        Band("Under 20 m"), Band("20 m or more", plus=plus),
    ))


_BOXED_IN = (Band("Under 2 s", 1), Band("2 to 4 s", 2), Band("4 to 6 s", 3), Band("6 s or more", 4))


def _scenario(basis, heading, low, high):
    """The scenario skills: x1, x1.1 from `low`, x1.2 from `high`."""
    return Scaling(basis, heading, (
        Band(f"Under {low:,}", 1.0), Band(f"{low:,} to {high - 1:,}", 1.1), Band(f"{high:,} or more", 1.2),
    ))


VALUE = {
    # +1% of the base per skill learned, up to 20 skills.
    2: Scaling("by skills learned", "Skills learned", (
        Band("None", 1.0), Band("10", 1.1), Band("20 or more", 1.2),
    )),
    3: _stat("Speed"), 4: _stat("Stamina"), 5: _stat("Power"), 6: _stat("Guts"), 7: _stat("Wit"),
    # Nothing Ventured: the -100% base is scaled by a random draw.
    8: Scaling("at random", "Chance", (Band("60%", 0), Band("30%", 0.02), Band("10%", 0.04))),
    10: Scaling("by races won in training", "Races won", (
        Band("Under 6", 0.8), Band("6 to 13", 0.9), Band("14 to 17", 1.0), Band("18 to 24", 1.1),
        Band("25 or more", 1.2),
    )),
    11: Scaling("by overtakes on late-race corners", "Overtakes", (
        Band("0 or 1", 1.0), Band("2", 1.1), Band("3", 1.2), Band("4 or more", 1.25),
    )),
    12: Scaling("by fan count", "Fans", (
        Band("Under 20,000", 0.8), Band("20,000 to 49,999", 0.9), Band("50,000 to 99,999", 1.0),
        Band("100,000 to 159,999", 1.1), Band("160,000 or more", 1.2),
    )),
    13: Scaling("by your highest base stat", "Highest base stat", (
        Band("Under 600", 0.8), Band("600 to 799", 0.9), Band("800 to 999", 1.0),
        Band("1,000 to 1,099", 1.1), Band("1,100 or more", 1.2),
    )),
    14: Scaling("by green skills activated this race", "Green skills activated", (
        Band("0 to 2", 0), Band("3 or 4", 1), Band("5", 2), Band("6 or more", 3),
    )),
    19: _behind(0.1),
    20: Scaling("by time spent boxed in mid-race", "Boxed in (longest)", _BOXED_IN),
    # Final Speed: base plus mood, track, racecourse and skill bonuses.
    22: Scaling("by final Speed", "Final Speed", (
        Band("Under 1,700", 0), Band("1,700 to 1,799", 1), Band("1,800 to 1,899", 2),
        Band("1,900 to 1,999", 3), Band("2,000 or more", 4),
    )),
    23: Scaling("by final Speed", "Final Speed", (
        Band("Under 1,400", 1), Band("1,400 to 1,599", 2), Band("1,600 or more", 3),
    )),
    24: Scaling("by total Overseas Aptitude (Project L'Arc)", "Overseas Aptitude", (
        Band("Under 10", 1.0), Band("10 to 19", 1.1), Band("20 or more", 1.2),
    )),
    25: Scaling("by the biggest lead before the late race", "Biggest lead", (
        Band("Under 10 m", 1.0), Band("10 to 25 m", 1.4), Band("25 m or more", 1.8),
    )),
    26: Scaling("by disciplines won (U.A.F.)", "Disciplines won", (
        Band("Under 10", 1.0), Band("10 to 14", 1.1), Band("15 or more", 1.2),
    )),
    27: _scenario("by Cooking Points (Great Food Festival)", "Cooking Points", 10_000, 16_000),
    28: _scenario("by Research Level (Run! Mecha Umamusume)", "Research Level", 1_000, 2_000),
    # Love comes from fans: the start, moving up mid-race, and skills.
    30: Scaling("by Love from fans", "Love", (
        Band("Under 3,000", 1.0), Band("3,000 to 49,999", 1.4), Band("50,000 to 249,999", 1.8),
        Band("250,000 to 749,999", 2.2), Band("750,000 to 1,499,999", 2.6),
        Band("1,500,000 to 2,999,999", 2.8), Band("3,000,000 or more", 3.0),
    )),
    31: _scenario("by Development Points (Design Your Island)", "Development Points", 2_500, 3_500),
    32: Scaling("by Legendary Inn rank", "Legendary Inn", (Band("Not reached", 1.0), Band("Reached", 1.2))),
    34: _behind(0.2),
    35: Scaling("by race", "Race", (
        Band("Other races"), Band("Mile", plus=0.1),
        Band("Arima Kinen course, with 146,738 fans or more", plus=0.2),
    )),
    36: Scaling("by distance", "Race", (Band("Other races"), Band("Medium", plus=0.2))),
    37: Scaling("by team rank", "Team rank", (Band("Below S", 1.0), Band("S or higher", 1.2))),
}

DURATION = {
    # 0.8 + metres behind / 62.5, capped at 1.6 (50 m): an even rise, so the
    # table samples it.
    2: Scaling("by distance behind the leader", "Behind the leader", (
        Band("0 m", 0.8), Band("25 m", 1.2), Band("50 m or more", 1.6),
    )),
    3: Scaling("by HP left when it fires", "HP left", (
        Band("Under 2,000", 1), Band("2,000 to 2,399", 1.5), Band("2,400 to 2,599", 2),
        Band("2,600 to 2,799", 2.2), Band("2,800 to 2,999", 2.5), Band("3,000 to 3,199", 3),
        Band("3,200 to 3,499", 3.5), Band("3,500 or more", 4),
    )),
    # +1 s per overtake while it lasts, up to 3; the same second on the
    # inherited copy (Daptrius, 2026-11-18).
    4: Scaling("by overtakes while it lasts", "Overtakes", (
        Band("None"), Band("1", plus=1), Band("2", plus=2), Band("3 or more", plus=3),
    )),
    5: Scaling("by time spent boxed in mid-race", "Boxed in (longest)", _BOXED_IN),
    7: Scaling("by HP left when it fires", "HP left", (
        Band("Under 1,500", 1), Band("1,500 to 1,799", 1.5), Band("1,800 to 1,999", 2),
        Band("2,000 to 2,099", 2.5), Band("2,100 or more", 3),
    )),
    # +0.5 s per uphill finished before it fires, up to 4.
    8: Scaling("by uphills finished before it fires", "Uphills", (
        Band("None"), Band("1", plus=0.5), Band("2", plus=1), Band("3", plus=1.5), Band("4 or more", plus=2),
    )),
}

# An extra effect that fires again during the skill (`additional_activate_type`
# on the ability): when, and how many times at most (Daptrius, 2026-11-18).
REPEATS = {
    1: ("on each overtake", 3),                  # Red-Hot Discipline!
    2: ("each time another skill activates", 3),  # Ticket to Your Dreams!
    3: ("each time another skill activates", 2),  # Lightning Flare
}
