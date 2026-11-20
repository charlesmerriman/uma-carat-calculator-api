"""
The other free card events' half of import_game_data: the snapshots'
free_card_events.json into StoryEvent rows of kind `main_story`, `anniversary`
and `special`, with their free cards and umas. notes/story-events-plan-2.md.

The story event half (story_event_import.py) is the model; the differences:

THE KEY is (kind, game id), never the id alone: main story part 7 and story
event 7 are different things (`one_story_event_per_kind_and_game_id`).

WHAT THE IMPORT OWNS, AND WHAT IT NEVER TOUCHES

  writes, every run    name (Global's), jp_name, the four instants, and a free
                       card or uma the game lists that has no row yet
  writes while unset   banner_timeline (placed by start, below), so an editor's
                       correction sticks
  never                image, working_name, a reward's must_get, note and
                       order, the reward amounts (the game files don't state
                       them for these kinds; they stay 0 unless an editor fills
                       them), and it never deletes a row or a reward. An event
                       an editor made by hand has no game id and is never seen.

PLACEMENT. A story event starts with its banner, so story_event_import's two
steps (Global's minute, else JP's day) place all 57. These kinds don't always
start with one: the 0.5th anniversary's missions opened mid-banner, and so did
the April fan event. So, in order (checked 2026-11-20 against the live banners,
22 of 22 under the banner the old sheet shows them with):

  1. a banner starting at Global's minute                     (7 of the 22)
  2. the Global banner RUNNING at that minute                  (2)
  3. a banner starting on JP's day                             (11)
  4. the banner running on JP that day
  5. no start in either client: a main story part the game has
     had since launch (the client dates parts 1 and 2 to 2017)
     goes under the first Global banner                        (2)

Two banners on one start are one Timeline card already, so the lower id is as
good as the other.
"""

import datetime

from calculatorapi.models import BannerTimeline, StoryEvent, StoryEventCard, SupportCard, Uma
from calculatorapi.story_event_import import JST, BannerMatcher

GAME_COLUMNS = ("name", "jp_name", "global_start_at", "global_end_at", "jp_start_at", "jp_end_at")
KINDS = ("main_story", "anniversary", "special")


def _instant(text):
    return datetime.datetime.fromisoformat(text) if text else None


def merge_free_card_events(global_events, jp_events):
    """{(kind, game id): what the snapshots say about it}, Global's first.

    JP's name is Japanese and goes to `jp_name` only. Global's rewards win for
    an event both clients have, as for story events."""
    merged = {}
    for event in jp_events:
        merged[(event["kind"], event["id"])] = {
            "name": "", "jp_name": event["name"],
            "global_start_at": None, "global_end_at": None,
            "jp_start_at": _instant(event["start"]), "jp_end_at": _instant(event["end"]),
            "rewards": event["rewards"],
        }
    for event in global_events:
        entry = merged.setdefault((event["kind"], event["id"]), {
            "jp_name": "", "jp_start_at": None, "jp_end_at": None,
        })
        entry.update({
            "name": event["name"],
            "global_start_at": _instant(event["start"]),
            "global_end_at": _instant(event["end"]),
            "rewards": event["rewards"],
        })
    return merged


class RunningBannerMatcher(BannerMatcher):  # pylint: disable=too-few-public-methods
    """BannerMatcher's two steps, then the banner running at the moment, then
    the launch banner for a part with no start at all (the module docstring)."""

    def __init__(self):
        super().__init__()
        self.windows = list(BannerTimeline.objects.order_by("id").values_list(
            "id", "global_start_date", "global_end_date", "jp_start_date", "jp_end_date",
        ))
        firsts = [(start, banner_id) for banner_id, start, *_ in self.windows if start is not None]
        self.launch = min(firsts)[1] if firsts else None

    def match_event(self, global_start, jp_start):
        found = self.match(global_start, jp_start)
        if found is not None:
            return found, "starts with"
        if global_start is not None:
            found = self._running(global_start, 1, 2)
            if found is not None:
                return found, "running on Global"
        if jp_start is not None:
            # A banner's jp_start_date holds its JP day at 22:00 UTC, so the
            # JP day is compared at that hour.
            day = jp_start.astimezone(JST).date()
            moment = datetime.datetime.combine(day, datetime.time(22), datetime.timezone.utc)
            found = self._running(moment, 3, 4)
            if found is not None:
                return found, "running on JP"
        if global_start is None and jp_start is None:
            return self.launch, "launch"
        return None, None

    def _running(self, moment, start_column, end_column):
        """The latest-starting banner whose window holds `moment` (lowest id on a tie).

        Only banners with both ends: one with no recorded end would hold every
        later moment and swallow events that belong elsewhere."""
        holding = [
            (row[start_column], -row[0]) for row in self.windows
            if row[start_column] is not None and row[end_column] is not None
            and row[start_column] <= moment <= row[end_column]
        ]
        return -max(holding)[1] if holding else None


class _Planner:  # pylint: disable=too-few-public-methods
    """Compares one event at a time with the database, collecting the plan."""

    def __init__(self):
        self.rows = {
            (row.kind, row.game_id): row
            for row in StoryEvent.objects.filter(kind__in=KINDS, game_id__isnull=False)
        }
        self.pks = {
            "card": dict(SupportCard.objects.exclude(game_id=None).values_list("game_id", "pk")),
            "uma": dict(Uma.objects.exclude(game_id=None).values_list("game_id", "pk")),
        }
        self.linked = {
            (kind, game_id, "card", card) for kind, game_id, card in StoryEventCard.objects.filter(
                story_event__kind__in=KINDS, support_card__isnull=False,
            ).values_list("story_event__kind", "story_event__game_id", "support_card__game_id")
        } | {
            (kind, game_id, "uma", uma) for kind, game_id, uma in StoryEventCard.objects.filter(
                story_event__kind__in=KINDS, uma__isnull=False,
            ).values_list("story_event__kind", "story_event__game_id", "uma__game_id")
        }
        self.matcher = RunningBannerMatcher()
        self.plan = {
            "label": "free card events", "free_events": True, "changed": [], "created": [],
            "new_cards": [], "missing": [], "unmatched": [], "unchanged": 0, "placed": [],
        }

    def add(self, key, event):
        kind, game_id = key
        # A value the snapshots don't have is left as it is, never blanked.
        values = {column: event[column] for column in GAME_COLUMNS
                  if event.get(column) not in (None, "")}
        row = self.rows.get(key)
        banner_id = None
        if row is None or row.banner_timeline_id is None:
            banner_id, rule = self.matcher.match_event(
                event.get("global_start_at"), event.get("jp_start_at"))
            if banner_id is None:
                self.plan["unmatched"].append(f"{kind} {game_id}")
            else:
                self.plan["placed"].append((key, banner_id, rule))
        if row is None:
            self.plan["created"].append(
                StoryEvent(kind=kind, game_id=game_id, banner_timeline_id=banner_id, **values))
        else:
            diff = {column: value for column, value in values.items()
                    if getattr(row, column) != value}
            if banner_id is not None:
                diff["banner_timeline_id"] = banner_id
            if diff:
                self.plan["changed"].append((row, diff))
            else:
                self.plan["unchanged"] += 1
        self._add_rewards(key, event["rewards"])

    def _add_rewards(self, key, rewards):
        kind, game_id = key
        for order, reward in enumerate(rewards):
            reward_type, reward_game_id = reward["type"], reward["id"]
            if (kind, game_id, reward_type, reward_game_id) in self.linked:
                continue
            pk = self.pks[reward_type].get(reward_game_id)
            if pk is None:
                what = "support card" if reward_type == "card" else "uma"
                self.plan["missing"].append(
                    f"{kind} {game_id}: {what} {reward_game_id} has no row; add it and re-run")
                continue
            self.plan["new_cards"].append((key, reward_type, pk, order))


def plan_free_card_events(global_events, jp_events):
    """What a run would change, without writing anything. See the module docstring."""
    planner = _Planner()
    for key, event in sorted(merge_free_card_events(global_events, jp_events).items()):
        planner.add(key, event)
    return planner.plan


def report_lines(plan, dry_run):
    """The import's report for this plan, as (text, is_warning) pairs."""
    lines = [(
        f"\nfree card events (main story, anniversary, special): "
        f"{'would update' if dry_run else 'updating'} {len(plan['changed'])}, "
        f"{'would create' if dry_run else 'creating'} {len(plan['created'])}, "
        f"{'would add' if dry_run else 'adding'} {len(plan['new_cards'])} free reward(s), "
        f"already current {plan['unchanged']}", False,
    )]
    lines += [(f"    {row.kind} {row.game_id} {str(row)[:40]:<40} {', '.join(sorted(diff))}", False)
              for row, diff in plan["changed"]]
    lines += [(f"    {kind} {game_id} placed under banner {banner_id} ({rule})", False)
              for (kind, game_id), banner_id, rule in plan["placed"]]
    if plan["unmatched"]:
        lines.append((f"    no banner found for {len(plan['unmatched'])}: "
                      f"{', '.join(plan['unmatched'])} (set it in the admin)", True))
    lines += [(f"    {line}", True) for line in plan["missing"]]
    return lines


def write_free_card_events(plan):
    """Apply a plan from plan_free_card_events. Runs inside the import's transaction."""
    for row, diff in plan["changed"]:
        for column, value in diff.items():
            setattr(row, column, value)
        row.save(update_fields=list(diff))
    for event in plan["created"]:
        event.save()
    if plan["new_cards"]:
        event_pks = {
            (kind, game_id): pk for kind, game_id, pk in StoryEvent.objects.filter(
                kind__in=KINDS, game_id__isnull=False,
            ).values_list("kind", "game_id", "pk")
        }
        StoryEventCard.objects.bulk_create(
            [StoryEventCard(story_event_id=event_pks[key], order=order,
                            **{"support_card_id" if reward_type == "card" else "uma_id": pk})
             for key, reward_type, pk, order in plan["new_cards"]],
            ignore_conflicts=True,
        )
    return len(plan["changed"]) + len(plan["created"]) + len(plan["new_cards"])
