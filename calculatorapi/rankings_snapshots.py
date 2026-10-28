"""
Rank movement for the Rankings page (notes/rankings-redesign-plan.md step 5):
each payload carries `previous`, what its lists looked like at the start of
the week, so the client can draw "▲2", "▼1" and "new" beside a row.

THE SNAPSHOT. One RankingsSnapshot per cache name, the payload as built by
the first rebuild after Monday 00:00 UTC. Movement is "this week, since
Monday": on Monday morning almost nothing has moved, by Sunday a week has.
At most seven days old, and it outlives a deploy (the rankings cache does
not). The first build ever for a name writes the snapshot and sends no
`previous` (there is nothing to compare with yet).

WHAT `previous` HOLDS. For "guides", the snapshot's guide rows: the client
counts them under the filters on screen, so movement follows the filters.
For every other payload only the ranked ids in order, per list, as the
client ranks them (ranked_lists below), and the snapshot's `generated_at`.

THE MONDAY POST. When a list worth pinning rotates (Guides overall, Pull
plans by planners, PvP umas for an event running now or in the next two
weeks), the week that just ended is compared with the new one and its three
biggest climbers go to the team's channel (DISCORD_RANKINGS_CHANNEL_ID),
once per list and ISO week (discord_messages.send keys). Prod has no
scheduler, so this rides whichever request rebuilds the list first.
"""

import datetime

from django.conf import settings
from django.utils import timezone

from calculatorapi import discord_messages
from calculatorapi.models import ChampionsMeeting, LeagueOfHeroes, RankingsSnapshot, SupportCard, Uma
from calculatorapi.predictions import build_effective_date_maps

# A win rate takes a place from this many races (the client's RANKED_RACES).
RANKED_RACES = 100
# Climbers listed in the Monday post, per list.
MOVERS = 3
# A PvP list is worth a post while its event runs or starts within this.
UPCOMING = datetime.timedelta(days=14)


def week_start(now):
    """Monday 00:00 UTC of `now`'s week."""
    day = now.astimezone(datetime.timezone.utc).date()
    monday = day - datetime.timedelta(days=day.weekday())
    return datetime.datetime.combine(monday, datetime.time.min, tzinfo=datetime.timezone.utc)


def _guide_cards(payload):
    """Guides overall as the client ranks it with every box ticked: a card
    once per deck it is the card or the borrowed card in, alternatives out,
    most decks first, then more events, then first seen."""
    decks, events, order = {}, {}, []
    for guide in payload.get("guides", []):
        for row in guide["cards"]:
            if row["role"] == "alt":
                continue
            card = row["card"]
            if card not in decks:
                decks[card], events[card] = set(), set()
                order.append(card)
            decks[card].add(row["deck"])
            events[card].add((guide["kind"], guide["event"]))
    return sorted(order, key=lambda card: (-len(decks[card]), -len(events[card]), order.index(card)))


def _win_order(runs):
    rows = [row for row in runs.get("rows", []) if row["races"] >= RANKED_RACES]
    rows.sort(key=lambda row: (-row["wins"] / row["races"], -row["races"]))
    return [f"{row['uma']}-{row['style']}" for row in rows]


def ranked_lists(name, payload):
    """{list: [ids in rank order]} for one payload, as the client ranks it."""
    if name == "guides":
        return {"cards": _guide_cards(payload)}
    if name == "pull-plans":
        banners = sorted(payload.get("banners", []), key=lambda row: -row["planners"])
        return {"banners": [f"{row['type']}-{row['id']}" for row in banners]}
    if name == "players":
        return {"favourites": [row["uma"] for row in payload.get("favourites", [])]}
    counted = payload.get("built", payload)
    lists = {
        "umas": [row["uma"] for row in counted.get("umas", [])],
        "cards": [row["card"] for row in counted.get("cards", [])],
    }
    if "runs" in payload:
        lists["runs"] = _win_order(payload["runs"])
    return lists


def previous_of(name, snapshot):
    """What the client gets as `previous` from a snapshot row."""
    if name == "guides":
        return {"generated_at": snapshot.payload["generated_at"], "guides": snapshot.payload.get("guides", [])}
    return {"generated_at": snapshot.payload["generated_at"], **ranked_lists(name, snapshot.payload)}


def attach_previous(name, payload, now=None):
    """Rotate `name`'s snapshot if it predates this week, post the movers when
    it does, and return `payload` with `previous` (None before the first
    snapshot existed)."""
    now = now or timezone.now()
    snapshot = RankingsSnapshot.objects.filter(name=name).first()
    if snapshot is None:
        RankingsSnapshot.objects.create(name=name, generated_at=now, payload=payload)
        return {**payload, "previous": None}
    if snapshot.generated_at < week_start(now):
        post_movers(name, ranked_lists(name, snapshot.payload), ranked_lists(name, payload), payload, now)
        snapshot.generated_at = now
        snapshot.payload = payload
        snapshot.save(update_fields=["generated_at", "payload"])
    return {**payload, "previous": previous_of(name, snapshot)}


def climbers(before, after, limit=MOVERS):
    """The `limit` biggest climbers into `after`'s top ten: (id, from place,
    to place), places 1-based, biggest climb first, then the higher place.
    An id new to the list isn't a climber."""
    moves = []
    for index, item in enumerate(after[:10]):
        if item in before and before.index(item) > index:
            moves.append((item, before.index(item) + 1, index + 1))
    moves.sort(key=lambda move: (move[2] - move[1], move[2]))
    return moves[:limit]


def _pvp_event_name(name, now):
    """The event's name for a "pvp:<kind>:<id>" list worth posting (running now,
    or starting within UPCOMING), else None."""
    _, kind, event_id = name.split(":")
    model = ChampionsMeeting if kind == "cm" else LeagueOfHeroes
    entry = build_effective_date_maps((model,))[model].get(int(event_id))
    if not entry or entry["start_date"] is None:
        return None
    if entry["start_date"] > now + UPCOMING or (entry["end_date"] is not None and now > entry["end_date"]):
        return None
    return model.objects.filter(id=event_id).values_list("name", flat=True).first()


def _list_to_post(name, payload, now):
    """(list key, title, {id: display name}) for a list the Monday post covers:
    Guides overall, Pull plans by planners, PvP umas for an event running now
    or soon. None for any other."""
    if name == "guides":
        return "cards", "Guides, overall", None
    if name == "pull-plans":
        labels = {f"{row['type']}-{row['id']}": row["name"] for row in payload.get("banners", [])}
        return "banners", "Pull plans, by planners", labels
    if name.startswith("pvp:"):
        event = _pvp_event_name(name, now)
        return ("umas", f"{event}: umas players build", None) if event else None
    return None


def post_movers(name, before, after, payload, now):
    """Last week's three biggest climbers for one list, to the team's channel,
    once per list and ISO week. Nothing without a channel, a list worth
    posting, or a climber."""
    if not settings.DISCORD_RANKINGS_CHANNEL_ID:
        return
    target = _list_to_post(name, payload, now)
    if target is None:
        return
    key, title, labels = target
    moves = climbers(before.get(key, []), after.get(key, []))
    if not moves:
        return
    if labels is None:
        model = SupportCard if key == "cards" else Uma
        labels = dict(model.objects.filter(id__in=[item for item, _, _ in moves]).values_list("id", "name"))
    # Keyed by the week that just ended, whichever day the first rebuild lands on.
    year, week, _ = (week_start(now) - datetime.timedelta(days=1)).isocalendar()
    lines = [f"**{labels.get(item, item)}**: #{was} to #{place}" for item, was, place in moves]
    discord_messages.send(
        f"rankings-movers:{year}-W{week:02d}:{name}",
        settings.DISCORD_RANKINGS_CHANNEL_ID,
        embed={"title": f"Climbers last week: {title}", "description": "\n".join(lines)},
    )
