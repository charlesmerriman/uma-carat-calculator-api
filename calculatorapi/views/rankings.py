"""
The Rankings page's routes (/app/rankings, notes/rankings-page-plan.md),
one per tab:

  GET /rankings/guides                     every pick in the published PvP guides
  GET /rankings/gallery/<kind>/<event_id>  one event's gallery posts, counted
                                           (the PvP page's Rankings tab)
  GET /rankings/pull-plans                 the banners players plan to pull
                                           on, and when (behind the Raptive
                                           gate, below)
  GET /rankings/pvp/<kind>/<event_id>      one event's saved teams, counted,
                                           and CM Runs win rates
  GET /rankings/players                    favourite umas, ranks, income
                                           options, selector picks

EARLY ACCESS ONLY (owner, 2026-10-27: "Patreon only for now"). Every route
checks benefits.early_access on the server; a guest gets 401, anyone else
403. The page and its navbar item are drawn only for early access too, but
that is presentation: this is the gate.

SHARES, NEVER A COUNT OF PEOPLE (owner, 2026-10-28). The payloads below are
built with counts, which the week's snapshot needs; what is sent is
rankings_shares.py's version of each, percentages only.

HOURLY (rankings_cache.py). Each payload is built at most once an hour,
stamped with `generated_at`, and never invalidated: the page says how old it
is. Gzipped (`gzip_page`): the rows repeat their keys, so ~280 KB of JSON is
~12 KB on the wire. The site has no GZipMiddleware; /calculator-data gzips
its own.

GUIDES: ROWS, NOT COUNTS. The client does the counting, so the sheet's tick
boxes (card type, scenario, strategy, CM / LoH, deck tier) and the event
window are instant, and the rules for what counts live in one place,
utils/guideRankings.ts. The server sends each guide's event, its scenario
and its rows, nothing it would have to agree with the client about:

  cards  one per deck slot that holds a card: the card, the deck's id (so
         the client can count decks, for a card's share), style and tier
         (below), and its role: "main", "borrow" (the
         borrowed card, counted like any other: owner), or "alt" (an "or
         Kitasan Black" alternative, shown but not counted)
  umas   one per starred uma: the uma, the style, the stars

A deck's tier comes from its label. The guides keep Beginner, Standard and
Whale for every style, and most guides add an honorable mention deck, which
the sheet's "Include" filter switches on its own. Everything else is a
one-off deck named for its idea ("xoguri pace", "runaway suzuka"): about 20
of 650 decks on prod, 2026-10-27. Those are "other".

Never sent: placeholder cards ("Your best SSR Speed card" is no card to
pull), note-only slots ("Any Wit"), and drafts. Past events are sent; the
client picks the window.
"""

from collections import Counter, defaultdict

from django.db.models import F, Q
from django.views.decorators.gzip import gzip_page
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import benefits, gallery, pvp_guide_editing, pvp_teams
from calculatorapi.analytics.banners import (
    active_rows, banner_popularity, demand_calendar, favourite_umas, step_up_popularity,
)
from calculatorapi.analytics.income_settings import income_settings, rank_distributions, selector_purchases
from calculatorapi.analytics.people import engaged_subset, non_staff_users
from calculatorapi.models import (
    BannerTimeline, PvpDeckSlot, PvpGuide, PvpRunRace, PvpRunRunner, PvpTeamCard, PvpTeamMember,
    PvpUmaRecommendation, Scenario, SupportCard, UserPlannedPurchase,
)
from calculatorapi.predictions import build_effective_date_maps
from calculatorapi.rankings_cache import cached_payload
from calculatorapi.rankings_shares import gallery_shares, player_shares, pull_plan_shares, pvp_shares
from calculatorapi.rankings_figures import budget_by_class, pull_plan_figures, selector_windows

TIERS = ("beginner", "standard", "whale")
# Exact three-uma teams listed under "Trios players build most".
TOP_TRIOS = 10
# Posts listed under the PvP page's "Most copied posts".
TOP_COPIED = 3
# A gallery member's borrowed card sits in slot 6 (gallery.snapshot_from_team).
BORROW_SLOT = 6
# Matched as the label's START, any case: prod has "Honorable mention",
# "Honorable mentions" and "Honorable mention deck".
HONORABLE = "honorable mention"

REFUSED = {"error": "Rankings are open to supporters first."}


def deck_tier(label):
    """A deck's label as a tier: "beginner" / "standard" / "whale" /
    "honorable", else "other"."""
    word = label.strip().lower()
    if word in TIERS:
        return word
    return "honorable" if word.startswith(HONORABLE) else "other"


def slot_role(is_borrow, order):
    """An alternative (order 1 and up) first, a borrowed card's included."""
    if order > 0:
        return "alt"
    return "borrow" if is_borrow else "main"


def guides_payload():
    """{"scenarios": [{"id", "name"}], "guides": [{"kind", "event",
    "scenario", "cards": [...], "umas": [...]}]}. The scenarios are the ones
    some published guide names, in release order (JP's, the order the
    sheet's filter lists them); the guides in no particular order. Four
    queries whatever the guide count."""
    guides = {}
    for guide_id, cm_id, loh_id, scenario_id in PvpGuide.objects.filter(is_published=True).values_list(
        "id", "champions_meeting_id", "league_of_heroes_id", "scenario_id"
    ):
        guides[guide_id] = {
            "kind": "cm" if cm_id else "loh",
            "event": cm_id or loh_id,
            "scenario": scenario_id,
            "cards": [],
            "umas": [],
        }
    _add_cards(guides)
    _add_umas(guides)
    return {"scenarios": _scenarios_in_use(guides), "guides": list(guides.values())}


def _add_cards(guides):
    slots = (
        PvpDeckSlot.objects.filter(
            deck__guide__is_published=True,
            support_card__isnull=False,
            support_card__is_placeholder=False,
        )
        .order_by("deck__guide_id", "deck__order", "deck_id", "slot", "order")
        .values_list(
            "deck__guide_id", "deck_id", "deck__style", "deck__label", "is_borrow", "order", "support_card_id",
        )
    )
    for guide_id, deck_id, style, label, is_borrow, order, card_id in slots:
        guides[guide_id]["cards"].append({
            "card": card_id,
            "deck": deck_id,
            "style": style,
            "tier": deck_tier(label),
            "role": slot_role(is_borrow, order),
        })


def _add_umas(guides):
    umas = (
        PvpUmaRecommendation.objects.filter(guide__is_published=True)
        .order_by("guide_id", "order", "id")
        .values_list("guide_id", "style", "uma_id", "stars")
    )
    for guide_id, style, uma_id, stars in umas:
        guides[guide_id]["umas"].append({"uma": uma_id, "style": style, "stars": stars})


def _scenarios_in_use(guides):
    used = {guide["scenario"] for guide in guides.values()} - {None}
    return list(
        Scenario.objects.filter(id__in=used)
        .order_by(F("banner_timeline__jp_start_date").asc(nulls_last=True), "id")
        .values("id", "name")
    )


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_guides(request):
    if not benefits.early_access(request.user):
        return Response(REFUSED, status=403)
    return Response(cached_payload("guides", guides_payload))


def count_teams(teams):
    """Teams counted: {"teams", "decks", "umas": [{"uma", "teams", "styles":
    {style: members}}], "cards": [{"card", "decks", "borrows"}]}, most first.

    `teams` is a list of teams, each a list of members shaped like a gallery
    snapshot's: {"uma", "style", "cards": [{"slot", "support_card"}]}. Shared
    by the gallery (posted teams) and the PvP tab (saved teams), so the two
    count by one rule:

      - an uma once per team with her (a team never repeats a character),
        with how many members ran each strategy;
      - a card once per member's deck holding it, the borrowed card (slot 6)
        included (the owner's rule for the guides, kept here) and counted
        apart in `borrows`;
      - placeholder cards ("your best SSR Speed card") never: they are no
        card to pull;
      - a team with no member is no team.

    `teams` is every team counted (an uma's share is out of it), `decks`
    every member (a card's share). Ties go to the lower id. One query.

    `trios`: the exact three-uma teams built most, any styles, as sorted id
    triples, the top TOP_TRIOS (the redesign's "Trios players build most").
    """
    teams = [team for team in teams if team]
    card_ids = {card["support_card"] for team in teams for member in team for card in member["cards"]}
    placeholders = set(
        SupportCard.objects.filter(id__in=card_ids, is_placeholder=True).values_list("id", flat=True)
    )
    umas, styles = Counter(), defaultdict(Counter)
    decks, borrows = Counter(), Counter()
    trios = Counter()
    members = 0
    for team in teams:
        team_umas = {member["uma"] for member in team}
        for uma in team_umas:
            umas[uma] += 1
        if len(team_umas) == 3:
            trios[tuple(sorted(team_umas))] += 1
        for member in team:
            members += 1
            styles[member["uma"]][member["style"]] += 1
            for card in member["cards"]:
                if card["support_card"] in placeholders:
                    continue
                decks[card["support_card"]] += 1
                if card["slot"] == BORROW_SLOT:
                    borrows[card["support_card"]] += 1
    return {
        "teams": len(teams),
        "decks": members,
        "umas": [
            {"uma": uma, "teams": count, "styles": dict(styles[uma])}
            for uma, count in _most_first(umas)
        ],
        "cards": [
            {"card": card, "decks": count, "borrows": borrows[card]}
            for card, count in _most_first(decks)
        ],
        "trios": [{"umas": list(trio), "teams": count} for trio, count in _most_first(trios)[:TOP_TRIOS]],
    }


def gallery_payload(event):
    """One event's visible gallery posts, counted (count_teams); `posts` is
    its `teams`. COUNTED HERE, unlike the guides: the tab has no filters, so
    the client needs totals, and a few hundred posts' snapshots stay on the
    server. Two queries."""
    counted = count_teams(gallery.visible_posts().filter(**event).values_list("team_snapshot", flat=True))
    counted["posts"] = counted.pop("teams")
    counted["copied"] = most_copied(event)
    return counted


def most_copied(event):
    """The event's TOP_COPIED most copied visible posts, as {"public_id",
    "caption", "copies", "umas"}: what the post page already shows anyone,
    minus the poster's name (the client links to the post, which carries it).
    A post nobody copied isn't listed. One query."""
    return [
        {"public_id": public_id, "caption": caption, "copies": copies, "umas": [member["uma"] for member in snapshot]}
        for public_id, caption, copies, snapshot in (
            gallery.visible_posts().filter(copy_count__gt=0, **event)
            .order_by("-copy_count", "id")
            .values_list("public_id", "caption", "copy_count", "team_snapshot")[:TOP_COPIED]
        )
    ]


def _most_first(counter):
    """(id, count), most first, ties by id so the order never depends on the posts'."""
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_gallery(request, kind, event_id):
    if not benefits.early_access(request.user):
        return Response(REFUSED, status=403)
    try:
        event = pvp_teams.resolve_event(kind, event_id)
    except pvp_teams.TeamRefused:
        return Response({"error": "Event not found"}, status=404)
    return Response(cached_payload(f"gallery:{kind}:{event_id}", lambda: gallery_payload(event), gallery_shares))


def sees_pull_plans(user):
    """Whether `user` sees the Pull plans tab: the guide editors only, until
    the owner says Raptive (the ad network) has approved the site. Henry,
    2026-10-27: the pull-plan tables stay "hidden until we get raptive
    approval (similar to skills tab)". The client's twin is
    hooks/useRankingsPullPlans.ts; un-gating is both: return True here (the
    early-access check still applies) and there."""
    return pvp_guide_editing.can_edit(user)


def pull_plans_payload():
    """The admin analytics' Popular banners tables as one list, plus the
    demand calendar: {"banners": [...], "demand": [...]}.

    One row per banner anyone plans: `type` ("uma" / "support" / "step_up"),
    its id, name and banner window (start, end, predicted, status), and
    `planners`, `total`, `average`, `median`. A step-up row's figures are
    STEPS (`unit`), never pulls (rules file step-up.md), so the client never
    adds them to a pull figure. Staff are left out and only active plans
    count (analytics.banners.active_rows); a row with an impossible pull
    count still counts as a planner but not in the figures (`SANE_MAX_*`).

    `planners` at the top is everyone with any banner in their active plan,
    each once: the denominator of a row's "N% of planners" (the redesign's
    Pull plans tab, notes/rankings-redesign-plan.md). Each row also carries
    `spread` and `also` (rankings_figures.pull_plan_figures).
    """
    timeline_dates = build_effective_date_maps()[BannerTimeline]

    def window(row):
        return {key: row[key] for key in ("start_date", "end_date", "predicted", "status")}

    banners = [
        {
            "type": banner_type, "unit": "pulls", "id": row["id"], "name": row["name"], **window(row),
            "planners": row["planners"], "total": row["total_pulls"],
            "average": row["avg_pulls"], "median": row["median_pulls"],
        }
        for banner_type, fk_name in (("uma", "banner_uma"), ("support", "banner_support"))
        for row in banner_popularity(fk_name, timeline_dates)
    ] + [
        {
            "type": "step_up", "unit": "steps", "id": row["id"], "name": row["name"], **window(row),
            "planners": row["planners"], "total": row["total_steps"],
            "average": row["avg_steps"], "median": row["median_steps"],
        }
        for row in step_up_popularity(timeline_dates)
        # Picked cards but planned no steps: nothing to rank.
        if row["planners"] > 0
    ]
    names = {(row["type"], row["id"]): row["name"] for row in banners}
    live = {(row["type"], row["id"]) for row in banners if row["status"] != "ended"}
    figures = pull_plan_figures(live, names)
    empty = {"spread": None, "also": []}
    for row in banners:
        row.update(figures.get((row["type"], row["id"]), empty))
    return {
        "planners": active_rows().values("user_id").distinct().count(),
        "banners": banners,
        "demand": demand_calendar(timeline_dates),
    }


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_pull_plans(request):
    if not (benefits.early_access(request.user) and sees_pull_plans(request.user)):
        return Response(REFUSED, status=403)
    return Response(cached_payload("pull-plans", pull_plans_payload, pull_plan_shares))


# A win rate under this many races is noise; the client says "not enough
# runs yet" for it rather than drawing 100% from one lucky ticket.
MIN_RACES = 20


def saved_teams(event):
    """Every non-staff saved team for `event`, as count_teams() reads them.
    Saved, not only posted: what players BUILD (owner, 2026-10-27; the
    privacy policy says totals count saved teams). Two queries."""
    lookup = {f"team__{field}": value for field, value in event.items()}
    members = {}
    teams = defaultdict(list)
    for member_id, team_id, uma_id, style in (
        PvpTeamMember.objects.filter(team__user__is_staff=False, **lookup)
        .order_by("team_id", "position")
        .values_list("id", "team_id", "uma_id", "style")
    ):
        members[member_id] = {"uma": uma_id, "style": style, "cards": []}
        teams[team_id].append(members[member_id])
    card_lookup = {f"member__{key}": value for key, value in lookup.items()}
    for member_id, slot, card_id in (
        PvpTeamCard.objects.filter(member__team__user__is_staff=False, **card_lookup)
        .values_list("member_id", "slot", "support_card_id")
    ):
        members[member_id]["cards"].append({"slot": slot, "support_card": card_id})
    return list(teams.values())


def run_results(event):
    """CM Runs, totalled per uma and strategy: {"players", "races", "rows":
    [{"uma", "style", "races", "wins"}]}, most races first.

    A runner RAN every race of her ticket that has a result (a race with no
    row isn't raced yet), and WON the ones whose winner is her spot. So a
    ticket's three runners share its races, and their wins add up to the
    ticket's wins. Skipped tickets never count; staff never do. `min_races`
    is the floor under which the client shows no rate. Two queries.
    """
    lookup = {f"run__{field}": value for field, value in event.items()}
    base = {"run__user__is_staff": False, "run__skipped": False, **lookup}
    races = defaultdict(list)
    for run_id, winner in PvpRunRace.objects.filter(**base).values_list("run_id", "winner"):
        races[run_id].append(winner)
    totals = defaultdict(lambda: {"races": 0, "wins": 0})
    players = set()
    for run_id, user_id, position, uma_id, style in (
        PvpRunRunner.objects.filter(**base).values_list("run_id", "run__user_id", "position", "uma_id", "style")
    ):
        if not races[run_id]:
            continue
        players.add(user_id)
        row = totals[(uma_id, style)]
        row["races"] += len(races[run_id])
        row["wins"] += sum(1 for winner in races[run_id] if winner == position)
    rows = [{"uma": uma, "style": style, **counts} for (uma, style), counts in totals.items()]
    rows.sort(key=lambda row: (-row["races"], row["uma"], row["style"]))
    return {
        "players": len(players),
        "races": sum(len(results) for results in races.values()),
        "min_races": MIN_RACES,
        "rows": rows,
    }


def pvp_payload(event):
    """The Rankings page's PvP tab for one event: what players build (every
    saved team, count_teams) and what wins (CM Runs, run_results)."""
    return {"built": count_teams(saved_teams(event)), "runs": run_results(event)}


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_pvp(request, kind, event_id):
    if not benefits.early_access(request.user):
        return Response(REFUSED, status=403)
    try:
        event = pvp_teams.resolve_event(kind, event_id)
    except pvp_teams.TeamRefused:
        return Response({"error": "Event not found"}, status=404)
    return Response(cached_payload(f"pvp:{kind}:{event_id}", lambda: pvp_payload(event), pvp_shares))


# The income ranks the Players tab shows, by the label rank_distributions gives them.
PLAYER_RANKS = ("Team Trials", "Club Rank")
# Picks listed under each selector.
TOP_PICKS = 5


def selector_picks():
    """{product id: [{"uma"|"card": id, "people"}]}, the cards and umas people
    plan to take with each campaign selector, most first, at most TOP_PICKS.
    Distinct people, staff out, quantity 1 or more. One query."""
    picks = defaultdict(Counter)
    seen = set()
    for user_id, product_id, uma_id, card_id in (
        UserPlannedPurchase.objects.filter(
            user__is_staff=False, quantity__gte=1,
        ).filter(Q(target_uma__isnull=False) | Q(target_support__isnull=False))
        .values_list("user_id", "product_id", "target_uma_id", "target_support_id")
    ):
        target = ("uma", uma_id) if uma_id else ("card", card_id)
        # One person planning the same pick for two stats blocks is one person.
        if (user_id, product_id, target) in seen:
            continue
        seen.add((user_id, product_id, target))
        picks[product_id][target] += 1
    return {
        product_id: [
            {target[0]: target[1], "people": people}
            for target, people in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:TOP_PICKS]
        ]
        for product_id, counter in picks.items()
    }


def players_payload():
    """The Players tab: four panels, from the admin analytics, among ENGAGED
    accounts (people who use the calculator; analytics.people.engaged_q),
    staff out.

      favourites  the most-picked favourite umas (id, people)
      ranks       Team Trials and club rank: people per rank among those who
                  set one (the "Not set" row is left out, and the share is out
                  of the people who set it)
      income      each income option: people with it on, of `engaged`, and
                  whether a new account starts with it on (`default_on`)
      budget      per Team Trials class: people and the median carats held and
                  pulls planned (rankings_figures.budget_by_class)
      selectors   each campaign selector: buyers, how many picked a card, the
                  top picks, and whether it is on sale (`status`, `end_date`)
    """
    engaged = engaged_subset(non_staff_users())
    engaged_count = engaged.count()

    ranks = []
    for distribution in rank_distributions(engaged, engaged_count):
        if distribution["label"] not in PLAYER_RANKS:
            continue
        rows = [row for row in distribution["rows"] if row["name"] != "Not set"]
        set_count = sum(row["count"] for row in rows)
        ranks.append({
            "label": distribution["label"],
            "people": set_count,
            "rows": [{"name": row["name"], "people": row["count"]} for row in rows],
        })

    picks = selector_picks()
    windows = selector_windows()
    selectors = selector_purchases(engaged_count, engaged_count)["selector_purchases"]
    return {
        "engaged": engaged_count,
        "favourites": [{"uma": row["uma_id"], "people": row["people"]} for row in favourite_umas()],
        "ranks": ranks,
        "income": [
            {"key": row["key"], "label": row["label"], "people": row["users_on"], "default_on": row["default"] == "On"}
            for row in income_settings(engaged, engaged_count)
        ],
        "budget": budget_by_class(engaged),
        "selectors": [
            {
                "campaign": row["campaign"], "label": row["label"], "buyers": row["count"],
                "picked": row["picked"], "top": picks.get(row["id"], []),
                **windows.get(row["id"], {"status": "upcoming", "end_date": None}),
            }
            for row in selectors
        ],
    }


@gzip_page
@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def rankings_players(request):
    if not benefits.early_access(request.user):
        return Response(REFUSED, status=403)
    return Response(cached_payload("players", players_payload, player_shares))
