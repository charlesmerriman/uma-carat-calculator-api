"""
A character's career goals, worded for the Encyclopedia's uma page
(notes/encyclopedia-plan.md, Phase 3).

THE CALENDAR: a career is three years of 24 turns, two per month, so a turn
is a half month. Turn 1 is the Junior Year's early January; turn 27 is the
Classic Year's early February (Special Week's Kisaragi Sho, as in the game).
The program table's own month and half agree with this for every race goal
(checked 2026-11-12), which is why one function can label them.

WHAT IS SHOWN: race goals only (kind 1) that name their race. The other kinds
(a fan count, "place in N races of a grade") are in the data but not decoded,
so they are counted in `hidden`, never worded by a guess.
"""

YEARS = ("Junior Year", "Classic Year", "Senior Year")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TURNS_PER_YEAR = 24

# race.grade -> how players write it. Read off the races that carry each code
# (100: Japanese Derby, Arima Kinen; 200: Yayoi Sho; 300: Kisaragi Sho; 400:
# the Open "Stakes" races). 900 is the debut, whose name already says so.
GRADE_LABELS = {100: "G1", 200: "G2", 300: "G3", 400: "OP"}

RACE_GOAL = 1


def turn_label(turn):
    """turn 27 -> "Classic Year, Early Feb". None past the third year (the
    scenario's finals), which no shown goal reaches."""
    index = turn - 1
    year = index // TURNS_PER_YEAR
    if not 0 <= year < len(YEARS):
        return None
    within = index % TURNS_PER_YEAR
    half = "Early" if within % 2 == 0 else "Late"
    return f"{YEARS[year]}, {half} {MONTHS[within // 2]}"


def need_label(top):
    """The placing a goal asks for: 1 win, N top N, 0 just run it."""
    if top == 0:
        return "Run it"
    if top == 1:
        return "Win"
    return f"Top {top}"


def shown_goals(goals):
    """
    (rows, hidden) for the page: the race goals worded, in order, and how
    many goals of other kinds were left out, so the page can say so.
    """
    rows, hidden = [], 0
    for goal in goals:
        label = turn_label(goal.get("turn", 0))
        if goal.get("kind") != RACE_GOAL or not goal.get("race") or label is None:
            hidden += 1
            continue
        rows.append({
            "turn": goal["turn"],
            "when": label,
            "race": goal["race"],
            "grade": GRADE_LABELS.get(goal.get("grade")),
            "need": need_label(goal.get("top", 0)),
        })
    return rows, hidden
