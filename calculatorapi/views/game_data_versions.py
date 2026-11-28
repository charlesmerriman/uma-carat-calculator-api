"""
GET / POST /game-data/versions: the daily masterdata check's state, for the
check only (scripts/masterdata_check.py, a GitHub Action).

AUTHENTICATION IS A SHARED SECRET, NOT A USER, exactly like /patreon/sync and
for the same reasons (views/patreon_supporters.py): the caller is a GitHub
Action with no account here, and the key authorises these two calls and
nothing else. While GAME_DATA_SYNC_SECRET is unset the route 404s; a wrong
key is a 403 that says nothing about why; the key is compared in constant time.

WHAT A STOLEN KEY CAN DO: add version rows and post a line to #calc-ops per
new hash. It cannot mark a version live (that is the admin's, later a
Discord button's), and nothing here touches game content.
"""

import hmac

from django.conf import settings
from django.http import Http404
from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from calculatorapi import game_data_versions
from calculatorapi.models import GameDataRegion, GameDataVersion

SYNC_HEADER = "X-Game-Data-Sync-Key"
# One run posts at most the versions it re-read plus the new numbers it found:
# a handful. The cap only stops a runaway caller filling the table.
MAX_FINDINGS = 50


class GameDataSyncThrottle(AnonRateThrottle):
    """A daily job plus manual re-runs, not whatever a leaked key attempts."""

    scope = "game_data_sync"


class FindingSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    region = serializers.ChoiceField(choices=GameDataRegion.choices)
    version = serializers.IntegerField(min_value=1)
    master_size = serializers.IntegerField(min_value=1)
    master_hash = serializers.RegexField(r"^[0-9a-f]{32}$")


class FindingsSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    findings = FindingSerializer(many=True, allow_empty=True, max_length=MAX_FINDINGS)


def _versions():
    return [game_data_versions.serialize(row) for row in GameDataVersion.objects.all()]


@api_view(["GET", "POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([GameDataSyncThrottle])
def game_data_versions_view(request):
    """GET: every known version and its status. POST {"findings": [...]}: record
    what the CDN showed, report what's new or revised, return both."""
    expected = getattr(settings, "GAME_DATA_SYNC_SECRET", "")
    if not expected:
        raise Http404
    if not hmac.compare_digest(request.headers.get(SYNC_HEADER, ""), expected):
        return Response({"detail": "Not permitted."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        return Response({"versions": _versions()})

    body = FindingsSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    changes = game_data_versions.record_findings(body.validated_data["findings"])
    return Response({"changes": changes, "versions": _versions()})
