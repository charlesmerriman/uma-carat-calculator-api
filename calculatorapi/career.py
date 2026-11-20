"""
A character's career goals, worded for the Encyclopedia's uma page
(notes/encyclopedia-plan.md, Phase 3).

THE CALENDAR: a career is three years of 24 turns, two per month, so a turn
is a half month. Turn 1 is the Junior Year's early January; turn 27 is the
Classic Year's early February (Special Week's Kisaragi Sho, as in the game).
The program table's own month and half agree with this for every race goal
(checked 2026-11-12), which is why one function can label them.

WHAT IS SHOWN: race goals (kind 1) that name their race, and the two other
kinds as far as someone who plays has confirmed how the game words them
(Daptrius, board "Dedicated pages for umas, support cards and skills",
2026-11-12, on Oguri Cap's two):

- kind 3, a fan count: `value_1` fans by the end of that turn ("Junior Year,
  late December, Oguri Cap must get 3,000 fans by the end of that turn").
- kind 2, placings in a grade: `condition_id` is the grade's code, `value_1`
  the placing and `value_2` how many races ("Senior Year, late June, Oguri Cap
  must place 3rd or better in 2 G1 races": 100, 3, 2). A grade below G1 means
  THAT GRADE OR HIGHER, and a placing of 1 is a win (Daptrius, 2026-11-13, on
  the two in the files: Tamamo Cross's 300, 3, 4 is "4 G3 or higher" and
  Smart Falcon's 700, 1, 2 is "1st in 2 Pre-OP or higher races"). Only the
  three codes he read are worded (PLACING_GRADES); any other stays counted in
  `hidden`, never worded by a guess.

NOT IN THE FILES WE READ: a goal an event can swap for another (Agnes
Tachyon's Derby becomes the NHK Mile Cup when her mood is too low; Daptrius,
same reply). The page lists the default route.
"""

YEARS = ("Junior Year", "Classic Year", "Senior Year")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TURNS_PER_YEAR = 24

# race.grade -> how players write it. Read off the races that carry each code
# (100: Japanese Derby, Arima Kinen; 200: Yayoi Sho; 300: Kisaragi Sho; 400:
# the Open "Stakes" races). 900 is the debut, whose name already says so.
GRADE_LABELS = {100: "G1", 200: "G2", 300: "G3", 400: "OP"}

RACE_GOAL = 1
PLACINGS_GOAL = 2
FANS_GOAL = 3
# The grades a placings goal is worded for, as the sentence says them: see the
# top of this file. Nothing ranks above G1, so it needs no "or higher".
PLACING_GRADES = {100: "G1 {races}", 300: "{races} at G3 or higher", 700: "{races} at Pre-OP or higher"}


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


def ordinal(number):
    """3 -> "3rd", 11 -> "11th", 22 -> "22nd"."""
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def goal_text(goal):
    """
    A goal that isn't one race, in the game's own terms, or None when its
    wording isn't confirmed (see the top of this file). The page shows the
    text where a race goal shows its race.
    """
    kind = goal.get("kind")
    if kind == FANS_GOAL and goal.get("value_1", 0) > 0:
        return f"Get {goal['value_1']:,} fans"
    if kind == PLACINGS_GOAL and goal.get("condition_id") in PLACING_GRADES:
        place, races = goal.get("value_1", 0), goal.get("value_2", 0)
        if place < 1 or races < 1:
            return None
        # "2 G1 races", "4 races at G3 or higher".
        where = PLACING_GRADES[goal["condition_id"]].format(races="race" if races == 1 else "races")
        # A placing of 1 is a win: nobody says "place 1st or better".
        return f"Win {races} {where}" if place == 1 else f"Place {ordinal(place)} or better in {races} {where}"
    return None


def shown_goals(goals):
    """
    (rows, hidden) for the page: every goal it can word, in order, and how
    many were left out, so the page can say so.

    A row's `kind` is "race", "fans" or "placings". A race row names its race,
    grade and the placing needed; the other two carry their whole sentence in
    `race` (the slot the page draws), with no grade and an empty `need`.
    """
    rows, hidden = [], 0
    for goal in goals:
        label = turn_label(goal.get("turn", 0))
        if label is None:
            hidden += 1
        elif goal.get("kind") == RACE_GOAL and goal.get("race"):
            rows.append({
                "turn": goal["turn"],
                "when": label,
                "kind": "race",
                "race": goal["race"],
                "grade": GRADE_LABELS.get(goal.get("grade")),
                "need": need_label(goal.get("top", 0)),
            })
        elif goal_text(goal):
            rows.append({
                "turn": goal["turn"],
                "when": label,
                "kind": "fans" if goal["kind"] == FANS_GOAL else "placings",
                "race": goal_text(goal),
                "grade": None,
                "need": "",
            })
        else:
            hidden += 1
    return rows, hidden
