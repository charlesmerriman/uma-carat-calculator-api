"""
What the Rankings page's payloads SEND: shares, never a count of people
(owner, 2026-10-28). A count of players, planners, saved teams, races or
buyers lets a reader estimate how many people use the site, so none leaves
the server; the page shows percentages, and so does its API.

THE COUNTS STAY HERE. views/rankings.py still builds every payload with
counts, because the week's snapshot (rankings_snapshots.py) ranks by them
and the Monday post compares them. cached_payload() hands that counted
payload (with its `previous`, ids only) to one of the functions below, and
caches and serves only what comes back. So a snapshot row holds counts and
never reaches a browser.

WHOLE PERCENTS (share()). The page draws whole percents, so a share is
rounded to one: a finer one would give a small total away (1 of 300 is
0.0033; the smallest share in a list would say "about 300"). A share above
zero but under half a percent is sent as SMALL, so the page can still say
"<1%" without rounding it to nothing.

PUBLIC COUNTS. A gallery post and its copies are on the public post page,
so the gallery payload keeps `posts`, `decks` and `copies`.
"""

from calculatorapi.rankings_snapshots import RANKED_RACES

# Above zero but under half a percent: the client's "<1%".
SMALL = 0.001
# Races for a win rate's third confidence mark (the second is RANKED_RACES,
# the first the payload's `min_races`).
SOLID_RACES = 300


def share(part, whole):
    """`part` of `whole`, 0 to 1, to the whole percent (SMALL for a sliver)."""
    if not whole or not part:
        return 0
    value = part / whole
    return round(value, 2) if value >= 0.005 else SMALL


def team_shares(counted):
    """A count_teams() result as shares: an uma (and each strategy she runs)
    of every team, a card of every member's deck, a borrow of the card's own
    decks, a trio of every team."""
    teams, decks = counted["teams"], counted["decks"]
    return {
        "umas": [
            {
                "uma": row["uma"],
                "share": share(row["teams"], teams),
                "styles": {style: share(members, teams) for style, members in row["styles"].items()},
            }
            for row in counted["umas"]
        ],
        "cards": [
            {"card": row["card"], "share": share(row["decks"], decks), "borrowed": share(row["borrows"], row["decks"])}
            for row in counted["cards"]
        ],
        "trios": [{"umas": row["umas"], "share": share(row["teams"], teams)} for row in counted.get("trios", [])],
    }


def gallery_shares(payload):
    """The PvP page's Rankings tab: the posts' lists as shares, the public
    counts kept (module docstring)."""
    return {
        "generated_at": payload["generated_at"],
        "posts": payload["posts"],
        "decks": payload["decks"],
        **team_shares({**payload, "teams": payload["posts"]}),
        "copied": payload["copied"],
        "previous": payload["previous"],
    }


def run_shares(runs):
    """CM Runs as win rates, never races: each row from `min_races` with its
    `rate`, its confidence `marks` (1 from min_races, 2 from RANKED_RACES, 3
    from SOLID_RACES) and whether it is `ranked` (RANKED_RACES or more). In
    order: the ranked rows best rate first (more races breaking a tie), then
    the rest most races first. The client draws them as sent."""
    shown = [row for row in runs["rows"] if row["races"] >= max(runs["min_races"], 1)]
    ranked = sorted(
        (row for row in shown if row["races"] >= RANKED_RACES),
        key=lambda row: (-row["wins"] / row["races"], -row["races"], row["uma"], row["style"]),
    )
    rest = sorted(
        (row for row in shown if row["races"] < RANKED_RACES),
        key=lambda row: (-row["races"], -row["wins"] / row["races"], row["uma"], row["style"]),
    )

    def marks(races):
        return 3 if races >= SOLID_RACES else 2 if races >= RANKED_RACES else 1

    return [
        {
            "uma": row["uma"], "style": row["style"], "rate": share(row["wins"], row["races"]),
            "marks": marks(row["races"]), "ranked": row["races"] >= RANKED_RACES,
        }
        for row in ranked + rest
    ]


def pvp_shares(payload):
    """The Rankings page's PvP tab: what players build, as shares of every
    saved team, and what wins, as rates."""
    return {
        "generated_at": payload["generated_at"],
        "built": team_shares(payload["built"]),
        "runs": run_shares(payload["runs"]),
        "previous": payload["previous"],
    }


def pull_plan_shares(payload):
    """The Pull plans tab: a banner's planners as a share of everyone with a
    plan, its spread as shares of the planners with a sane figure, "also
    planning" as a share of its own planners, and each month's share of
    every planned pull. Medians and averages are per planner, so they stay;
    a banner's total pulls goes (total over median is roughly its planners)."""
    everyone = payload["planners"]
    window = ("type", "unit", "id", "name", "start_date", "end_date", "predicted", "status")

    def banner(row):
        spread = row["spread"]
        sane = sum(spread["planners"]) if spread else 0
        return {
            **{key: row[key] for key in window},
            "share": share(row["planners"], everyone),
            "average": row["average"],
            "median": row["median"],
            "spread": (
                {"edges": spread["edges"], "shares": [share(people, sane) for people in spread["planners"]]}
                if spread else None
            ),
            "also": [
                {
                    "type": other["type"], "id": other["id"], "name": other["name"],
                    "share": share(other["planners"], row["planners"]),
                }
                for other in row["also"]
            ],
        }

    pulls = sum(month["total_pulls"] for month in payload["demand"])
    return {
        "generated_at": payload["generated_at"],
        "banners": [banner(row) for row in payload["banners"]],
        "demand": [
            {"month": month["month"], "share": share(month["total_pulls"], pulls)} for month in payload["demand"]
        ],
        "previous": payload["previous"],
    }


def player_shares(payload):
    """The Players tab: everything out of the players who use the calculator
    (`engaged`, never sent), a rank's rows out of the people who set it, a
    selector's pickers out of its buyers and a pick out of its pickers."""
    engaged = payload["engaged"]
    return {
        "generated_at": payload["generated_at"],
        "favourites": [{"uma": row["uma"], "share": share(row["people"], engaged)} for row in payload["favourites"]],
        "ranks": [
            {
                "label": rank["label"],
                "share": share(rank["people"], engaged),
                "rows": [{"name": row["name"], "share": share(row["people"], rank["people"])} for row in rank["rows"]],
            }
            for rank in payload["ranks"]
        ],
        "income": [
            {
                "key": row["key"], "label": row["label"],
                "share": share(row["people"], engaged), "default_on": row["default_on"],
            }
            for row in payload["income"]
        ],
        "budget": [{"name": row["name"], "carats": row["carats"], "pulls": row["pulls"]} for row in payload["budget"]],
        "selectors": [
            {
                "campaign": row["campaign"], "label": row["label"],
                "buyers": share(row["buyers"], engaged), "picked": share(row["picked"], row["buyers"]),
                # A pick is {"uma": id} or {"card": id}, with how many picked it.
                "top": [
                    {**{key: value for key, value in pick.items() if key != "people"},
                     "share": share(pick["people"], row["picked"])}
                    for pick in row["top"]
                ],
                "status": row["status"], "end_date": row["end_date"],
            }
            for row in payload["selectors"]
        ],
        "previous": payload["previous"],
    }
