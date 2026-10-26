"""
GET    /roster          what the caller owns: {umas: [...], support_cards: [...]}.
PATCH  /roster          grid edits: {umas?: [change], support_cards?: [change]}.
POST   /roster/import   a game export's umas and/or cards; plain or gzipped JSON.
DELETE /roster/umas     start over: every owned uma removed.
DELETE /roster/support-cards   the same for cards.

All IsAuthenticated, account only (the Collection page shows a guest a
sign-in card). Rules: roster.py; what a row is: models/roster.py.

On the wire (catalogue ids only):

    uma:  {uma, stars, talent_level, source, obtained_at}
    card: {support_card, limit_break, spare_copies, source, obtained_at}

A PATCH change is {uma | support_card, owned: bool, ...the columns to set};
`owned: false` removes the row. Every change in one request applies or none
does.

An import item is a row's columns plus `game_data` (the game's whole object).
Each list sent REPLACES that table, hand edits included, and removes what the
file lacks (owner, 2026-10-26); a list left out is untouched. The answer counts
{added, updated, removed} per table sent and returns the roster.

A refusal is a 400 with {"error": "<one sentence for the player>"}.
"""

import json

from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.response import Response

from calculatorapi import roster
from calculatorapi.models import (
    ROSTER_LIMIT_BREAK_MAX,
    ROSTER_SPARE_COPIES_MAX,
    ROSTER_STARS_MAX,
    TRAINED_UMA_GAME_DATA_MAX_BYTES,
)
from calculatorapi.views.pvp_team import _first_error
from calculatorapi.views.trained_uma import GzipJSONParser

# Far past the catalogue (a few hundred umas, several hundred cards): it only
# stops a crafted request, never a real collection.
_LIST_MAX = 5000


def _stars():
    return serializers.IntegerField(min_value=1, max_value=ROSTER_STARS_MAX, allow_null=True, required=False)


def _small_game_data(game_data):
    if game_data is not None and len(json.dumps(game_data)) > TRAINED_UMA_GAME_DATA_MAX_BYTES:
        raise serializers.ValidationError("One item in that file is too large to keep.")
    return game_data


# ── Input ────────────────────────────────────────────────────────────────────

class _UmaChange(serializers.Serializer):  # pylint: disable=abstract-method
    uma = serializers.IntegerField(min_value=1)
    owned = serializers.BooleanField()
    stars = _stars()
    talent_level = _stars()


class _CardChange(serializers.Serializer):  # pylint: disable=abstract-method
    support_card = serializers.IntegerField(min_value=1)
    owned = serializers.BooleanField()
    limit_break = serializers.IntegerField(min_value=0, max_value=ROSTER_LIMIT_BREAK_MAX, required=False)
    spare_copies = serializers.IntegerField(min_value=0, max_value=ROSTER_SPARE_COPIES_MAX, required=False)


class _EditInput(serializers.Serializer):  # pylint: disable=abstract-method
    umas = _UmaChange(many=True, required=False, default=list, max_length=_LIST_MAX)
    support_cards = _CardChange(many=True, required=False, default=list, max_length=_LIST_MAX)


class _UmaItem(serializers.Serializer):  # pylint: disable=abstract-method
    uma = serializers.IntegerField(min_value=1)
    stars = _stars()
    talent_level = _stars()
    game_data = serializers.DictField(required=False, allow_null=True, default=None)

    def validate_game_data(self, game_data):
        return _small_game_data(game_data)


class _CardItem(serializers.Serializer):  # pylint: disable=abstract-method
    support_card = serializers.IntegerField(min_value=1)
    limit_break = serializers.IntegerField(min_value=0, max_value=ROSTER_LIMIT_BREAK_MAX)
    spare_copies = serializers.IntegerField(min_value=0, max_value=ROSTER_SPARE_COPIES_MAX, required=False, default=0)
    game_data = serializers.DictField(required=False, allow_null=True, default=None)

    def validate_game_data(self, game_data):
        return _small_game_data(game_data)


class _ImportInput(serializers.Serializer):  # pylint: disable=abstract-method
    # No default: a list left out must stay out, so its table is not replaced
    # with nothing (roster.import_roster).
    umas = _UmaItem(many=True, required=False, max_length=_LIST_MAX)
    support_cards = _CardItem(many=True, required=False, max_length=_LIST_MAX)


def _bad_request(message):
    return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


# ── Output ───────────────────────────────────────────────────────────────────

def serialize_roster(user):
    """The whole roster. Two queries, game_data left out."""
    return {
        "umas": [
            {"uma": row.uma_id, "stars": row.stars, "talent_level": row.talent_level,
             "source": row.source, "obtained_at": row.obtained_at}
            for row in roster.owned_umas(user)
        ],
        "support_cards": [
            {"support_card": row.support_card_id, "limit_break": row.limit_break,
             "spare_copies": row.spare_copies, "source": row.source, "obtained_at": row.obtained_at}
            for row in roster.owned_cards(user)
        ],
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@api_view(["GET", "PATCH"])
@permission_classes([permissions.IsAuthenticated])
def roster_detail(request):
    if request.method == "PATCH":
        payload = _EditInput(data=request.data)
        if not payload.is_valid():
            return _bad_request(_first_error(payload.errors))
        try:
            roster.edit(request.user, **payload.validated_data)
        except roster.RosterRefused as refusal:
            return _bad_request(str(refusal))
    return Response(serialize_roster(request.user))


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([GzipJSONParser])
def roster_import(request):
    payload = _ImportInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    try:
        counts = roster.import_roster(request.user, **payload.validated_data)
    except roster.RosterRefused as refusal:
        return _bad_request(str(refusal))
    return Response({**counts, "roster": serialize_roster(request.user)})


@api_view(["DELETE"])
@permission_classes([permissions.IsAuthenticated])
def roster_clear(request, kind):
    """Start over on one list. `kind` is "umas" or "support-cards" (the URL's spelling)."""
    if kind not in ("umas", "support-cards"):
        return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
    roster.clear(request.user, "umas" if kind == "umas" else "support_cards")
    return Response(serialize_roster(request.user))
