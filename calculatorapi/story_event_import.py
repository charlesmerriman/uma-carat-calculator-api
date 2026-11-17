"""
The story event half of import_game_data: the snapshots' story_events.json
into StoryEvent and StoryEventCard rows. notes/story-events-plan.md.

TWO FILES, ONE EVENT LIST. Global's snapshot holds the events Global has run,
with English names and Global's instants. JP's holds every event, the ones
still to come included, with JP's instants. An event is matched across the two
by the game's id, which both clients share.

WHAT THE IMPORT OWNS, AND WHAT IT NEVER TOUCHES (the model's docstring has
the why):

  writes, every run    game_id, name (Global's), jp_name, the five instants,
                       and a free card the game lists that has no row yet
  writes while unset   banner_timeline (matched by start, below), so an
                       editor's correction sticks
  writes while         the five rewards. Global's figures, else JP's. Once an
  rewards_edited off   editor changes one, the team's figure wins
  never                image, working_name, a card's must_get, note and order,
                       and it never deletes a row or a card

A value the snapshots don't have (Global's dates on an event Global hasn't
run) is left as it is rather than blanked.

THE BANNER MATCH. An event runs with its banner, so it is placed by start:
Global's start to the minute when Global has run it (21 of 22, checked
2026-11-17; the launch event opened at 00:30 and its banners at 22:00), else
JP's start on the same JP calendar day as a banner's `jp_start_date` (57 of
57). A banner's `jp_start_date` holds the JP day at 22:00 UTC, so the event's
JP instant is read in Japan time. Two banners on one day are one Timeline card
already, so the lower id is as good as the other.
"""

import datetime

from calculatorapi.models import BannerTimeline, StoryEvent, StoryEventCard, SupportCard

JST = datetime.timezone(datetime.timedelta(hours=9))

REWARD_COLUMNS = {
    "carats": "carat_amount",
    "uma_tickets": "uma_ticket_amount",
    "support_tickets": "support_ticket_amount",
    "ssr_shards": "ssr_shard_amount",
    "sr_shards": "sr_shard_amount",
}


def _instant(text):
    return datetime.datetime.fromisoformat(text) if text else None


def merge_events(global_events, jp_events):
    """{game id: what the snapshots say about it}, Global's first."""
    merged = {}
    for event in jp_events:
        merged[event["id"]] = {
            "name": "", "jp_name": event["name"],
            "global_start_at": None, "global_end_at": None, "global_claim_end_at": None,
            "jp_start_at": _instant(event["start"]), "jp_end_at": _instant(event["end"]),
            "rewards": event["rewards"], "cards": event["cards"],
        }
    for event in global_events:
        entry = merged.setdefault(event["id"], {
            "jp_name": "", "jp_start_at": None, "jp_end_at": None,
        })
        entry.update({
            "name": event["name"],
            "global_start_at": _instant(event["start"]),
            "global_end_at": _instant(event["end"]),
            "global_claim_end_at": _instant(event["claim_end"]),
            # Global's own figures and cards win over JP's for an event both have.
            "rewards": event["rewards"], "cards": event["cards"],
        })
    return merged


class BannerMatcher:  # pylint: disable=too-few-public-methods
    """Finds the banner an event runs with, from every banner's two starts."""

    def __init__(self):
        self.by_global = {}
        self.by_jp_day = {}
        for banner_id, global_start, jp_start in BannerTimeline.objects.order_by("id").values_list(
            "id", "global_start_date", "jp_start_date",
        ):
            if global_start is not None:
                self.by_global.setdefault(global_start, banner_id)
            if jp_start is not None:
                day = jp_start.astimezone(datetime.timezone.utc).date()
                self.by_jp_day.setdefault(day, banner_id)

    def match(self, global_start, jp_start):
        if global_start is not None and global_start in self.by_global:
            return self.by_global[global_start]
        if jp_start is not None:
            return self.by_jp_day.get(jp_start.astimezone(JST).date())
        return None


GAME_COLUMNS = ("name", "jp_name", "global_start_at", "global_end_at", "global_claim_end_at",
                "jp_start_at", "jp_end_at")


class _Planner:  # pylint: disable=too-few-public-methods
    """Compares one event at a time with the database, collecting the plan.
    A class only so the lookups built once are shared by every event."""

    def __init__(self):
        self.rows = {row.game_id: row for row in StoryEvent.objects.all()}
        self.card_pks = dict(SupportCard.objects.exclude(game_id=None).values_list("game_id", "pk"))
        self.linked = set(StoryEventCard.objects.values_list(
            "story_event__game_id", "support_card__game_id"))
        self.matcher = BannerMatcher()
        self.plan = {
            "label": "story event", "story": True, "changed": [], "created": [],
            "new_cards": [], "missing": [], "unmatched": [], "unchanged": 0, "mismatched": [],
        }

    def add(self, game_id, event):
        # A value the snapshots don't have is left as it is, never blanked.
        values = {column: event[column] for column in GAME_COLUMNS
                  if event.get(column) not in (None, "")}
        rewards = {REWARD_COLUMNS[key]: amount for key, amount in event["rewards"].items()}
        row = self.rows.get(game_id)
        banner_id = None
        if row is None or row.banner_timeline_id is None:
            banner_id = self.matcher.match(event.get("global_start_at"), event.get("jp_start_at"))
            if banner_id is None:
                self.plan["unmatched"].append(game_id)
        if row is None:
            self.plan["created"].append(
                StoryEvent(game_id=game_id, banner_timeline_id=banner_id, **values, **rewards))
        else:
            diff = {column: value for column, value in values.items()
                    if getattr(row, column) != value}
            if not row.rewards_edited:
                diff.update({column: value for column, value in rewards.items()
                             if getattr(row, column) != value})
            if banner_id is not None:
                diff["banner_timeline_id"] = banner_id
            if diff:
                self.plan["changed"].append((row, diff))
            else:
                self.plan["unchanged"] += 1
        self._add_cards(game_id, event["cards"])

    def _add_cards(self, game_id, card_game_ids):
        for order, card_game_id in enumerate(card_game_ids):
            if (game_id, card_game_id) in self.linked:
                continue
            if card_game_id not in self.card_pks:
                self.plan["missing"].append(
                    f"event {game_id}: support card {card_game_id} has no row; add it and re-run")
                continue
            self.plan["new_cards"].append((game_id, self.card_pks[card_game_id], order))


def plan_story_events(global_events, jp_events):
    """What a run would change, without writing anything. See the module docstring."""
    planner = _Planner()
    for game_id, event in sorted(merge_events(global_events, jp_events).items()):
        planner.add(game_id, event)
    return planner.plan


def report_lines(plan, dry_run):
    """The import's report for this plan, as (text, is_warning) pairs."""
    verb = "would update" if dry_run else "updating"
    lines = [(
        f"\nstory events: {verb} {len(plan['changed'])}, "
        f"{'would create' if dry_run else 'creating'} {len(plan['created'])}, "
        f"{'would add' if dry_run else 'adding'} {len(plan['new_cards'])} free card(s), "
        f"already current {plan['unchanged']}", False,
    )]
    lines += [(f"    {row.game_id} {str(row)[:40]:<40} {', '.join(sorted(diff))}", False)
              for row, diff in plan["changed"]]
    if plan["unmatched"]:
        lines.append((f"    no banner found for {len(plan['unmatched'])}: "
                      f"{', '.join(map(str, plan['unmatched']))} (set it in the admin)", True))
    lines += [(f"    {line}", True) for line in plan["missing"]]
    return lines


def write_story_events(plan):
    """Apply a plan from plan_story_events. Runs inside the import's transaction."""
    for row, diff in plan["changed"]:
        for column, value in diff.items():
            setattr(row, column, value)
        row.save(update_fields=list(diff))
    for event in plan["created"]:
        event.save()
    if plan["new_cards"]:
        event_pks = dict(StoryEvent.objects.values_list("game_id", "pk"))
        StoryEventCard.objects.bulk_create(
            [StoryEventCard(story_event_id=event_pks[game_id], support_card_id=card_pk, order=order)
             for game_id, card_pk, order in plan["new_cards"]],
            ignore_conflicts=True,
        )
    return len(plan["changed"]) + len(plan["created"]) + len(plan["new_cards"])
