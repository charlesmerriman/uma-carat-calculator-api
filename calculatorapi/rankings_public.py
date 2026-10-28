"""
The Rankings page for everyone (notes/rankings-redesign-plan.md step 6):
anyone without early access, guests included, gets each payload cut to the
top three of each ranked list, `"limited": true`, with every figure the
tab's band shows kept. Early access gets the whole payload as before.

Cut from the hourly cached payload on each request, so nothing is built
twice. What a cut leaves out:

  - every row past the third of each list;
  - GUIDES' rows: the client counts the full payload under its filters, so a
    limited reader gets the server's own count with every box ticked instead
    (the top three cards and umas with their figures, and the band's totals);
  - `previous`: last week's order would show more than the top three;
  - whatever a tab draws beyond its lists (ranks past the band's one figure,
    income options past the band's one, budgets, selectors, most copied
    posts), which are for supporters.

Pull plans are not cut: that tab stays behind the Raptive gate (guide
editors) until the owner lifts it, whoever is asking.
"""

from collections import defaultdict

from calculatorapi.models import SupportCard

# Rows a limited reader gets per list.
TOP = 3
# A win rate takes a place from this many races (the client's RANKED_RACES).
RANKED_RACES = 100
# The income option the Players band reads.
DAILY_CARAT = "daily_carat"
# The rank the Players band reads its top class from.
TEAM_TRIALS = "Team Trials"


def limited(name, payload):
    """`payload` (built for `name`, the rankings cache name) cut for a reader
    without early access."""
    base = {"generated_at": payload["generated_at"], "limited": True}
    if name == "guides":
        return {**base, **_guides(payload)}
    if name == "players":
        return {**base, **_players(payload)}
    if name.startswith("pvp:"):
        return {**base, "built": _counted(payload["built"]), "runs": _runs(payload["runs"])}
    if name.startswith("gallery:"):
        return {**base, **_counted(payload, teams="posts")}
    raise ValueError(f"No public cut for {name}")


def _counted(counted, teams="teams"):
    """A count_teams() result cut to its top three umas, cards and trios."""
    return {
        teams: counted[teams],
        "decks": counted["decks"],
        "umas": counted["umas"][:TOP],
        "cards": counted["cards"][:TOP],
        "trios": counted.get("trios", [])[:TOP],
    }


def _runs(runs):
    """CM Runs cut to the top three RANKED rows (100 races and up), best rate
    first: a thinner row has no place to show."""
    ranked = [row for row in runs["rows"] if row["races"] >= RANKED_RACES]
    ranked.sort(key=lambda row: (-row["wins"] / row["races"], -row["races"]))
    return {"players": runs["players"], "races": runs["races"], "min_races": runs["min_races"], "rows": ranked[:TOP]}


def _players(payload):
    """Favourites cut to three, and only the two figures the band reads: the
    biggest Team Trials class, and the Daily Carat Pack."""
    trials = next((rank for rank in payload["ranks"] if rank["label"] == TEAM_TRIALS), None)
    ranks = []
    if trials and trials["rows"]:
        ranks.append({**trials, "rows": [max(trials["rows"], key=lambda row: row["people"])]})
    return {
        "engaged": payload["engaged"],
        "favourites": payload["favourites"][:TOP],
        "ranks": ranks,
        "income": [row for row in payload["income"] if row["key"] == DAILY_CARAT],
        "budget": [],
        "selectors": [],
    }


def _guides(payload):
    """The guides counted on the server with every box ticked, by the client's
    rules (utils/guideRankings.ts), the top three of each list with the band's
    totals."""
    guides = payload["guides"]
    # The page drops a card it can't type (its card-type boxes can't place it),
    # so the count here does too, or a guest's top three would differ from a
    # supporter's.
    ids = {row["card"] for guide in guides for row in guide["cards"]}
    typed = set(
        SupportCard.objects.filter(id__in=ids).exclude(card_type__isnull=True).exclude(card_type="")
        .values_list("id", flat=True)
    )
    cards, every_deck = _guide_cards(guides, typed)
    return {
        "totals": {
            "guides": len(guides),
            "decks": len(every_deck),
            "scenarios": len({guide["scenario"] for guide in guides} - {None}),
        },
        "cards": [{**card, "share": card["decks"] / len(every_deck)} for card in cards[:TOP]],
        "umas": _guide_umas(guides)[:TOP],
    }


def _guide_cards(guides, typed):
    """([{"card", "decks", "borrows"}] most decks first, every deck counted): a
    card once per deck it is the card or the borrowed card in, alternatives
    out, cards outside `typed` left off (their decks still count). Ties: more
    events first, then first seen."""
    every_deck = set()
    decks, borrows, events, seen = defaultdict(set), defaultdict(int), defaultdict(set), []
    for guide in guides:
        for row in guide["cards"]:
            every_deck.add(row["deck"])
            if row["role"] == "alt" or row["card"] not in typed:
                continue
            if row["card"] not in decks:
                seen.append(row["card"])
            decks[row["card"]].add(row["deck"])
            events[row["card"]].add((guide["kind"], guide["event"]))
            borrows[row["card"]] += row["role"] == "borrow"
    ranked = sorted(seen, key=lambda card: (-len(decks[card]), -len(events[card]), seen.index(card)))
    return [{"card": card, "decks": len(decks[card]), "borrows": borrows[card]} for card in ranked], every_deck


def _guide_umas(guides):
    """[{"uma", "picks", "share"}] most picks first: an uma once per guide and
    style that recommends her, her share of every guide-and-style."""
    picks, events, sections, seen = defaultdict(int), defaultdict(set), set(), []
    for index, guide in enumerate(guides):
        for row in guide["umas"]:
            sections.add((index, row["style"]))
            if row["uma"] not in picks:
                seen.append(row["uma"])
            picks[row["uma"]] += 1
            events[row["uma"]].add((guide["kind"], guide["event"]))
    ranked = sorted(seen, key=lambda uma: (-picks[uma], -len(events[uma]), seen.index(uma)))
    return [{"uma": uma, "picks": picks[uma], "share": picks[uma] / len(sections)} for uma in ranked]
