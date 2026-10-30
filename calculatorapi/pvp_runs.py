"""The operations on a player's logged CM / LoH results. Every /pvp-runs view goes through these.

The pvp_teams.py split: the ownership scoping and the write rules live here, so
the CM Runs view, a later Collection "CM history" tab and a replay import all
write through the same checks.

A run is addressed by its SLOT (event, then day and ticket, or "the final"),
never by a row id. The CM Runs view auto-saves a ticket on every dropdown
change, and the first save creates the row: keyed by slot, two quick saves
write the same row instead of racing to create two.

What a run is: models/pvp_run.py. The wire shape and input validation:
views/pvp_run.py.
"""

from django.db import IntegrityError, transaction

from calculatorapi import pvp_teams, trained_umas
from calculatorapi.models import (
    PVP_FINAL_PLACEMENTS,
    PVP_FINAL_RACES,
    PVP_RUN_DAYS,
    PVP_RUN_RACES,
    PVP_RUN_TICKETS,
    PvpRun,
    PvpRunRace,
    PvpRunRunner,
    PvpRunStage,
)

# Refusals share pvp_teams' exception (the lineup rules raise it), so a view
# catches one class. Its message is shown to the player as is (copy.md).
RunRefused = pvp_teams.TeamRefused


class NoSuchSlot(Exception):
    """A day, ticket or final the event kind does not have. A 404: the client
    never builds one, so it is a wrong URL, not a player's mistake."""


def check_slot(kind, day=None, ticket=None):
    """NoSuchSlot unless (day, ticket) exists for `kind`; both None means the
    final, which only a Champions Meeting has so far."""
    if kind not in PVP_RUN_DAYS:
        raise NoSuchSlot()
    if day is None and ticket is None:
        if kind != "cm":
            raise NoSuchSlot()
        return
    if not 1 <= day <= PVP_RUN_DAYS[kind] or not 1 <= ticket <= PVP_RUN_TICKETS[kind]:
        raise NoSuchSlot()


def _slot_filter(user, event, day, ticket):
    """The lookup for one slot's row. `event` is resolve_event()'s {fk: event}."""
    if day is None:
        return {"user": user, "stage": PvpRunStage.FINAL, **event}
    return {"user": user, "stage": PvpRunStage.DAY, "day": day, "ticket": ticket, **event}


def runs_for_event(user, kind, event_id):
    """Every run `user` logged for the event, with runners and races, in two
    prefetches. TeamRefused for an event that does not exist."""
    event = pvp_teams.resolve_event(kind, event_id)
    return PvpRun.objects.filter(user=user, **event).prefetch_related("runners", "races")


def all_runs(user):
    """Every run `user` logged, in every event, with runners and races: the
    Collection's CM history. A player logs a few hundred a year at most, so
    one unpaged list is fine. Grouped by event (by id, not by date: the client
puts the events in date order from /calculator-data)."""
    return (
        PvpRun.objects.filter(user=user)
        .prefetch_related("runners", "races")
        .order_by("champions_meeting_id", "league_of_heroes_id", "stage", "day", "ticket")
    )


def write_run(user, kind, event_id, *, day=None, ticket=None, data):  # pylint: disable=too-many-arguments
    """Write one slot whole, creating the row on the first save. `data` is the
    view's validated body: runners, races, finals_group, placement, note,
    skipped.

    Delete-then-create for the runners and races, in one transaction, like
    pvp_teams.replace_team: the client sends the ticket as it should be. A
    refused write leaves the slot exactly as it was.
    """
    check_slot(kind, day, ticket)
    event = pvp_teams.resolve_event(kind, event_id)
    is_final = day is None
    runners, races = data["runners"], data["races"]

    _check_races(races, is_final, {runner["position"] for runner in runners})
    umas = pvp_teams.check_lineup([runner["uma"] for runner in runners])
    veterans = trained_umas.check_links(user, runners)
    if not is_final and (data.get("finals_group") or data.get("placement") is not None):
        raise RunRefused("Only the final has a group and a placement.")
    placement = data.get("placement")
    if placement is not None and not 1 <= placement <= PVP_FINAL_PLACEMENTS:
        raise RunRefused("A final places you 1st, 2nd or 3rd.")
    # Skipped means "the races not listed were never run" (models/pvp_run.py),
    # so a ticket with every race listed has nothing left to skip. Un-skipping
    # is the client's: a PUT without the flag (the player logged a race after
    # all, or undid it).
    skipped = data.get("skipped", False)
    if skipped and is_final:
        raise RunRefused("Only a day's ticket can be skipped.")
    if skipped and len(races) >= PVP_RUN_RACES:
        raise RunRefused(f"All {PVP_RUN_RACES} races are logged, so none were skipped.")

    with transaction.atomic():
        run = _get_or_create(_slot_filter(user, event, day, ticket))
        run.runners.all().delete()
        run.races.all().delete()
        PvpRunRunner.objects.bulk_create(
            PvpRunRunner(
                run=run,
                position=runner["position"],
                uma=umas[runner["uma"]],
                style=runner["style"],
                trained_uma=veterans.get(runner.get("trained_uma")),
            )
            for runner in runners
        )
        PvpRunRace.objects.bulk_create(
            PvpRunRace(run=run, race=race["race"], winner=race["winner"]) for race in races
        )
        run.finals_group = data.get("finals_group", "")
        run.placement = placement
        run.note = data.get("note", "")
        run.skipped = skipped
        # Saved even with no column changed, so updated_at says "edited now".
        run.save(update_fields=["finals_group", "placement", "note", "skipped", "updated_at"])
    return run


def _get_or_create(lookup):
    """The slot's row, created if it is new.

    Two first saves of one ticket can still arrive together (two tabs). The
    loser of the insert hits the slot's unique constraint; the savepoint keeps
    the surrounding transaction usable, and it reads the winner's row instead.
    """
    try:
        with transaction.atomic():
            run, _ = PvpRun.objects.get_or_create(**lookup)
    except IntegrityError:
        run = PvpRun.objects.get(**lookup)
    return run


def _check_races(races, is_final, positions):
    """The rules only the whole ticket can check: how many races, each number
    once, and a winner that names a spot somebody ran in."""
    most = PVP_FINAL_RACES if is_final else PVP_RUN_RACES
    numbers = [race["race"] for race in races]
    if len(races) > most or any(number > most for number in numbers):
        raise RunRefused(
            "A final is one race." if is_final else f"A ticket is {PVP_RUN_RACES} races."
        )
    if len(set(numbers)) != len(numbers):
        raise RunRefused("The same race is in there twice.")
    if any(race["winner"] is not None and race["winner"] not in positions for race in races):
        raise RunRefused("A race was won by an empty spot. Pick the uma first.")


def clear_run(user, kind, event_id, *, day=None, ticket=None):
    """Delete one slot's row. Clearing a slot that was never logged is fine:
    the view answers 204 either way, so a double tap is harmless."""
    check_slot(kind, day, ticket)
    event = pvp_teams.resolve_event(kind, event_id)
    PvpRun.objects.filter(**_slot_filter(user, event, day, ticket)).delete()
