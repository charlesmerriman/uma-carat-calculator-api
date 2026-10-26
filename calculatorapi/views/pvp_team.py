"""
GET    /pvp-teams        every team the caller holds, for every event.
POST   /pvp-teams        create one: {kind, event, name?, members?, notes?}.
                         Refused at PVP_TEAM_CAP_PER_EVENT for that event.
GET    /pvp-teams/<id>   one team.
PUT    /pvp-teams/<id>   replace its members and cards: {members, notes?}.
                         `notes` left out means "unchanged".
PATCH  /pvp-teams/<id>   rename it and/or pin it: {name?, is_focus?}.
DELETE /pvp-teams/<id>   delete it.

All IsAuthenticated. A guest keeps teams on the device
(frontend services/guestPvpTeamStore.ts) and never calls these. Every id goes
through pvp_teams.get_owned_team(): somebody else's team is a 404 identical to
one that does not exist.

A team on the wire, both ways (catalogue ids only, never embedded objects; the
client draws them from GET /pvp-catalogue):

    {id, kind: "cm" | "loh", event, name, is_focus, notes: [{text, color}],
     members: [{position, uma, style, trained_uma, cards: [{slot, support_card}]}],
     updated_at, post}    (post: the gallery post's public id, or null; out only)

A refusal is a 400 with {"error": "<one sentence for the player>"}; the builder
shows it as a toast. Never through PATCH /calculator-data: teams save on their
own routes (the client auto-saves them), so a refused team cannot take a
calculator save down with it.
"""

from django.db import transaction
from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import pvp_teams, trained_umas
from calculatorapi.models import (
    PVP_DECK_SIZE,
    PVP_NOTE_CAP,
    PVP_NOTE_MAX_LENGTH,
    PVP_TEAM_SIZE,
    PvpNoteColor,
    PvpStyle,
    PvpTeam,
)

_NOT_FOUND = {"error": "Team not found"}

# Every style but "everyone", which only a guide row uses.
_MEMBER_STYLES = [choice for choice in PvpStyle.choices if choice[0] != PvpStyle.EVERYONE]


# ── Input ────────────────────────────────────────────────────────────────────

class NoteSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One sticky note. Kept as typed, spaces and line breaks included."""
    text = serializers.CharField(
        max_length=PVP_NOTE_MAX_LENGTH, allow_blank=True, trim_whitespace=False
    )
    color = serializers.ChoiceField(choices=PvpNoteColor.choices)


class _CardInput(serializers.Serializer):  # pylint: disable=abstract-method
    slot = serializers.IntegerField(min_value=1, max_value=PVP_DECK_SIZE)
    support_card = serializers.IntegerField(min_value=1)


class _MemberInput(serializers.Serializer):  # pylint: disable=abstract-method
    position = serializers.IntegerField(min_value=0, max_value=PVP_TEAM_SIZE - 1)
    uma = serializers.IntegerField(min_value=1)
    style = serializers.ChoiceField(choices=_MEMBER_STYLES)
    cards = _CardInput(many=True, required=False, default=list)
    # The veteran that is this uma (R2), or null. Left out = none.
    trained_uma = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)

    def validate_cards(self, cards):
        if len(cards) > PVP_DECK_SIZE:
            raise serializers.ValidationError(f"A deck holds {PVP_DECK_SIZE} cards.")
        if len({card["slot"] for card in cards}) != len(cards):
            raise serializers.ValidationError("Two cards are in the same slot.")
        if len({card["support_card"] for card in cards}) != len(cards):
            # The borrow slot included: the game refuses a borrowed copy of a
            # card already in the deck.
            raise serializers.ValidationError("A deck can't hold the same card twice.")
        return cards


def _validate_members(members):
    if len(members) > PVP_TEAM_SIZE:
        raise serializers.ValidationError(f"A team has {PVP_TEAM_SIZE} umas.")
    if len({member["position"] for member in members}) != len(members):
        raise serializers.ValidationError("Two umas are in the same place on the team.")
    return members


class _ReplaceInput(serializers.Serializer):  # pylint: disable=abstract-method
    members = _MemberInput(many=True)
    notes = NoteSerializer(many=True, required=False, max_length=PVP_NOTE_CAP)

    def validate_members(self, members):
        return _validate_members(members)


class _CreateInput(_ReplaceInput):  # pylint: disable=abstract-method
    kind = serializers.ChoiceField(choices=list(pvp_teams.EVENT_FOR_KIND))
    event = serializers.IntegerField(min_value=1)
    name = serializers.CharField(max_length=40, allow_blank=True, required=False, default="")
    members = _MemberInput(many=True, required=False, default=list)


class _PatchInput(serializers.Serializer):  # pylint: disable=abstract-method
    name = serializers.CharField(max_length=40, allow_blank=True, required=False)
    is_focus = serializers.BooleanField(required=False)


def _first_error(errors):
    """DRF's nested error dict -> the first message in it, for {"error": ...}.
    The builder shows one sentence; the field path is not useful to a player."""
    while isinstance(errors, (dict, list)):
        if not errors:
            return "That team couldn't be saved."
        errors = next(iter(errors.values())) if isinstance(errors, dict) else errors[0]
    return str(errors)


def _bad_request(message):
    return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


# ── Output ───────────────────────────────────────────────────────────────────

def serialize_team(team):
    """One team on the wire. Reads `members` and their `cards` through
    whatever the caller prefetched (pvp_teams.owned_teams)."""
    return {
        "id": team.id,
        "kind": team.kind,
        "event": team.event_id,
        "name": team.name,
        "is_focus": team.is_focus,
        "notes": team.notes,
        "members": [
            {
                "position": member.position,
                "uma": member.uma_id,
                "style": member.style,
                "trained_uma": member.trained_uma_id,
                "cards": [
                    {"slot": card.slot, "support_card": card.support_card_id}
                    for card in member.cards.all()
                ],
            }
            for member in team.members.all()
        ],
        "updated_at": team.updated_at,
        # The public id of the gallery post copied from this team, or null.
        # So My team can say "Posted" on the right team and link to it. At
        # most one: a post is per event, and posting another team for the
        # same event moves the post's source to that team.
        "post": next((post.public_id for post in team.gallery_posts.all()), None),
    }


def _fresh(team):
    """Re-read one team with its rows prefetched, after a write."""
    return pvp_teams.owned_teams(team.user).get(id=team.id)


# ── Routes ───────────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([permissions.IsAuthenticated])
def pvp_team_list(request):
    if request.method == "GET":
        return Response(
            [serialize_team(team) for team in pvp_teams.owned_teams(request.user)]
        )

    payload = _CreateInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    data = payload.validated_data
    try:
        team = pvp_teams.create_team(
            request.user,
            kind=data["kind"],
            event_id=data["event"],
            name=data["name"],
            members=data["members"],
            notes=data.get("notes", []),
        )
    except (pvp_teams.TeamRefused, trained_umas.VeteranRefused) as refusal:
        return _bad_request(str(refusal))
    return Response(serialize_team(_fresh(team)), status=status.HTTP_201_CREATED)


def _replace(request, team):
    payload = _ReplaceInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    data = payload.validated_data
    try:
        pvp_teams.replace_team(team, members=data["members"], notes=data.get("notes"))
    except (pvp_teams.TeamRefused, trained_umas.VeteranRefused) as refusal:
        return _bad_request(str(refusal))
    return Response(serialize_team(_fresh(team)))


def _update(request, team):
    payload = _PatchInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    data = payload.validated_data
    # Both in one transaction, so a failing pin cannot leave a rename behind.
    with transaction.atomic():
        if "name" in data:
            team.name = data["name"]
            team.save(update_fields=["name", "updated_at"])
        if "is_focus" in data:
            pvp_teams.set_focus(team, data["is_focus"])
    return Response(serialize_team(_fresh(team)))


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def pvp_team_detail(request, team_id):
    try:
        team = pvp_teams.get_owned_team(request.user, team_id)
    except PvpTeam.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(serialize_team(_fresh(team)))
    if request.method == "PUT":
        return _replace(request, team)
    if request.method == "PATCH":
        return _update(request, team)
    team.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
