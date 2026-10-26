"""
GET    /pvp-runs/<kind>/<event_id>                      every run the caller logged for the event.
PUT    /pvp-runs/<kind>/<event_id>/day/<d>/ticket/<t>   write one ticket whole (creates it).
DELETE /pvp-runs/<kind>/<event_id>/day/<d>/ticket/<t>   clear it.
PUT    /pvp-runs/<kind>/<event_id>/final                write the final whole (CM only).
DELETE /pvp-runs/<kind>/<event_id>/final                clear it.

All IsAuthenticated, and account only by design: a guest sees a sign-in card
in the CM Runs view and never calls these. A run is addressed by its slot, not
an id (pvp_runs.py says why), and every query is scoped to request.user, so
nobody else's runs are reachable at all.

A run on the wire (catalogue ids only; the client draws them from
GET /pvp-catalogue):

    {kind: "cm" | "loh", event, stage: "day" | "final", day, ticket,
     finals_group: "" | "A" | "B", placement: null | 1..3, note,
     runners: [{position, uma, style}], races: [{race, winner}], updated_at}

A PUT body is the same minus kind, event, stage, day, ticket and updated_at
(the URL says those). `winner` is the spot (a runner's position) of the uma
that won, or null for a loss; a race not in the list has not been run yet.

An unknown kind, event, day or ticket is a 404; a refused write is a 400 with
{"error": "<one sentence for the player>"}, shown as a toast.
"""

from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import pvp_runs, pvp_teams
from calculatorapi.models import (
    PVP_RUN_NOTE_MAX_LENGTH,
    PVP_RUN_RACES,
    PVP_TEAM_SIZE,
    PvpFinalsGroup,
)
from calculatorapi.views.pvp_team import _MEMBER_STYLES, _first_error

_NOT_FOUND = {"error": "That ticket isn't in this event."}


# ── Input ────────────────────────────────────────────────────────────────────

class _RunnerInput(serializers.Serializer):  # pylint: disable=abstract-method
    position = serializers.IntegerField(min_value=0, max_value=PVP_TEAM_SIZE - 1)
    uma = serializers.IntegerField(min_value=1)
    style = serializers.ChoiceField(choices=_MEMBER_STYLES)


class _RaceInput(serializers.Serializer):  # pylint: disable=abstract-method
    race = serializers.IntegerField(min_value=1, max_value=PVP_RUN_RACES)
    winner = serializers.IntegerField(min_value=0, max_value=PVP_TEAM_SIZE - 1, allow_null=True)


class _RunInput(serializers.Serializer):  # pylint: disable=abstract-method
    runners = _RunnerInput(many=True, required=False, default=list)
    races = _RaceInput(many=True, required=False, default=list)
    finals_group = serializers.ChoiceField(
        choices=PvpFinalsGroup.choices, allow_blank=True, required=False, default=""
    )
    placement = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    note = serializers.CharField(
        max_length=PVP_RUN_NOTE_MAX_LENGTH, allow_blank=True, required=False, default=""
    )

    def validate_runners(self, runners):
        if len(runners) > PVP_TEAM_SIZE:
            raise serializers.ValidationError(f"A ticket is run by {PVP_TEAM_SIZE} umas.")
        if len({runner["position"] for runner in runners}) != len(runners):
            raise serializers.ValidationError("Two umas are in the same spot.")
        return runners


# ── Output ───────────────────────────────────────────────────────────────────

def serialize_run(run):
    """One run on the wire. Reads `runners` and `races` through whatever the
    caller prefetched (pvp_runs.runs_for_event)."""
    return {
        "kind": run.kind,
        "event": run.event_id,
        "stage": run.stage,
        "day": run.day,
        "ticket": run.ticket,
        "finals_group": run.finals_group,
        "placement": run.placement,
        "note": run.note,
        "runners": [
            {"position": runner.position, "uma": runner.uma_id, "style": runner.style}
            for runner in run.runners.all()
        ],
        "races": [{"race": race.race, "winner": race.winner} for race in run.races.all()],
        "updated_at": run.updated_at,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def pvp_run_list(request, kind, event_id):
    try:
        runs = pvp_runs.runs_for_event(request.user, kind, event_id)
    except pvp_teams.TeamRefused as refusal:
        return Response({"error": str(refusal)}, status=status.HTTP_404_NOT_FOUND)
    return Response([serialize_run(run) for run in runs])


def _write_or_clear(request, kind, event_id, day=None, ticket=None):
    """PUT or DELETE one slot. `day` and `ticket` None mean the final."""
    try:
        if request.method == "DELETE":
            pvp_runs.clear_run(request.user, kind, event_id, day=day, ticket=ticket)
            return Response(status=status.HTTP_204_NO_CONTENT)

        payload = _RunInput(data=request.data)
        if not payload.is_valid():
            return Response(
                {"error": _first_error(payload.errors)}, status=status.HTTP_400_BAD_REQUEST
            )
        # Checked before the write so an unknown event is a 404, not a 400.
        pvp_runs.check_slot(kind, day, ticket)
        pvp_teams.resolve_event(kind, event_id)
    except pvp_runs.NoSuchSlot:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
    except pvp_teams.TeamRefused as refusal:
        return Response({"error": str(refusal)}, status=status.HTTP_404_NOT_FOUND)

    try:
        run = pvp_runs.write_run(
            request.user, kind, event_id, day=day, ticket=ticket, data=payload.validated_data
        )
    except pvp_runs.RunRefused as refusal:
        return Response({"error": str(refusal)}, status=status.HTTP_400_BAD_REQUEST)
    # Re-read with the prefetch, so the response is exactly what a GET returns.
    fresh = pvp_runs.runs_for_event(request.user, kind, event_id).get(id=run.id)
    return Response(serialize_run(fresh))


@api_view(["PUT", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def pvp_run_ticket(request, kind, event_id, day, ticket):
    return _write_or_clear(request, kind, event_id, day=day, ticket=ticket)


@api_view(["PUT", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def pvp_run_final(request, kind, event_id):
    return _write_or_clear(request, kind, event_id)
