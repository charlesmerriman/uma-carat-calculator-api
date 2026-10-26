"""The operations on a player's PvP teams. Every /pvp-teams view goes through these.

The plans.py split, for the same reasons: the ownership check lives here so a
team cannot be reached by id without passing through it, and the write rules
(the cap, the replace, the character rule, the pin) live here so R2b's drawer
and R3's "copy to my teams" reuse them rather than re-deriving them in a view.

What a team is: models/pvp_team.py. The wire shape and the input validation:
views/pvp_team.py.
"""

from django.db import transaction

from calculatorapi.models import (
    ChampionsMeeting,
    CustomUser,
    LeagueOfHeroes,
    PVP_TEAM_CAP_PER_EVENT,
    SupportCard,
    PvpTeam,
    PvpTeamCard,
    PvpTeamMember,
    Uma,
)

# The URL's / body's kind -> (the event model, the team's FK to it).
EVENT_FOR_KIND = {
    "cm": (ChampionsMeeting, "champions_meeting"),
    "loh": (LeagueOfHeroes, "league_of_heroes"),
}


class TeamRefused(Exception):
    """A write the rules refuse. The message is shown to the player as is, so
    it is written as copy (.claude/rules/copy.md), not as a developer error."""


def get_owned_team(user, team_id):
    """The team with this id IF it belongs to `user`, else PvpTeam.DoesNotExist.

    THE ownership check, as plans.get_owned_plan() is for plans. Somebody
    else's team and a team that does not exist are the same error, so a caller
    cannot probe which ids are taken. A non-numeric id is the same error too.
    """
    try:
        return PvpTeam.objects.get(id=int(team_id), user=user)
    except (TypeError, ValueError) as exc:
        raise PvpTeam.DoesNotExist from exc


def owned_teams(user):
    """Every team `user` holds, with members, cards and any gallery post
    copied from it, in three prefetches."""
    return (
        PvpTeam.objects.filter(user=user)
        .prefetch_related("members__cards", "gallery_posts")
    )


def resolve_event(kind, event_id):
    """{fk_name: event} for a team's event, or TeamRefused when there is no
    such event. `kind` is "cm" or "loh"."""
    if kind not in EVENT_FOR_KIND:
        raise TeamRefused("Pick a Champions Meeting or League of Heroes event.")
    model, field = EVENT_FOR_KIND[kind]
    try:
        return {field: model.objects.get(id=int(event_id))}
    except (TypeError, ValueError, model.DoesNotExist) as exc:
        raise TeamRefused("That event isn't on the site any more.") from exc


def create_team(user, *, kind, event_id, name="", members=(), notes=()):  # pylint: disable=too-many-arguments
    """A new team for one event. Raises TeamRefused at the per-event cap or
    when `members` break a rule.

    The cap is checked HERE and only here, on creation, like PLAN_CAP: a save
    to a team that already exists is never refused for it.
    """
    event = resolve_event(kind, event_id)
    with transaction.atomic():
        # Locks the account row for the count-then-create, so two quick
        # "New team" clicks cannot both read 4 and both make a fifth.
        # PostgreSQL honours it; SQLite serialises writes anyway.
        CustomUser.objects.select_for_update().get(pk=user.pk)
        if PvpTeam.objects.filter(user=user, **event).count() >= PVP_TEAM_CAP_PER_EVENT:
            raise TeamRefused(
                f"You can keep up to {PVP_TEAM_CAP_PER_EVENT} teams for one event."
            )
        team = PvpTeam.objects.create(user=user, name=name, notes=list(notes), **event)
        _write_members(team, members)
    return team


def replace_team(team, *, members, notes=None):
    """Replace `team`'s members and cards, and its notes when given.

    Delete-all-then-create in one transaction, with no nested ids on the wire:
    the client sends the whole team as it should be, and nothing about which
    row was which can collide (the lesson of the step-up picks). A refused
    write leaves the team exactly as it was.

    `notes=None` leaves the notes alone, so a client that only edits cards
    cannot wipe them by leaving the key out.
    """
    with transaction.atomic():
        team.members.all().delete()  # CASCADE takes the cards
        _write_members(team, members)
        if notes is not None:
            team.notes = list(notes)
        # Saved even with no column changed, so updated_at says "edited now".
        team.save(update_fields=["notes", "updated_at"])
    return team


def check_lineup(uma_ids):
    """{id: Uma} for a lineup's umas, or TeamRefused for one the game refuses:
    an uma not on the site, the same uma twice, or two outfits of one character.

    Shared with pvp_runs.py, so a logged ticket's lineup obeys the same rules
    as the team it was copied from.
    """
    umas = Uma.objects.in_bulk(uma_ids)
    if len(umas) != len(set(uma_ids)):
        raise TeamRefused("One of those umas isn't on the site any more.")
    if len(set(uma_ids)) != len(uma_ids):
        raise TeamRefused("A team can't have the same uma twice.")
    characters = [umas[uma_id].character_id for uma_id in uma_ids]
    known = [character for character in characters if character is not None]
    if len(set(known)) != len(known):
        raise TeamRefused("A team can't have two outfits of the same uma.")
    return umas


def _write_members(team, members):
    """Create the member and card rows for already-shaped `members`.

    The serializer has checked each row on its own (positions, slots, styles,
    duplicates inside one deck). What only the whole team can say is checked
    here: the same uma twice, and two outfits of one character.
    """
    umas = check_lineup([member["uma"] for member in members])
    card_ids = {card["support_card"] for member in members for card in member["cards"]}
    if SupportCard.objects.filter(id__in=card_ids).count() != len(card_ids):
        raise TeamRefused("One of those support cards isn't on the site any more.")

    for member in members:
        row = PvpTeamMember.objects.create(
            team=team,
            position=member["position"],
            uma=umas[member["uma"]],
            style=member["style"],
        )
        PvpTeamCard.objects.bulk_create(
            PvpTeamCard(member=row, slot=card["slot"], support_card_id=card["support_card"])
            for card in member["cards"]
        )


def set_focus(team, focused):
    """Pin `team` as its owner's drawer focus, or unpin it.

    Pinning clears any other pin FIRST, in the same transaction:
    one_focus_pvp_team_per_user is checked per statement, so setting before
    clearing would trip it (plans.set_active_plan, same idiom). Unpinning
    leaves the account with no pin, which is allowed: the drawer then shows
    the team for the running or next event.
    """
    with transaction.atomic():
        if focused:
            PvpTeam.objects.filter(user=team.user, is_focus=True).exclude(
                id=team.id
            ).update(is_focus=False)
        if team.is_focus != focused:
            team.is_focus = focused
            team.save(update_fields=["is_focus", "updated_at"])
    return team
