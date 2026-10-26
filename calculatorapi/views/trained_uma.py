"""
GET    /trained-umas          every veteran the caller holds, newest first.
POST   /trained-umas          add one by hand: {uma, label?, speed..wit, apt?, skills?}.
GET    /trained-umas/<id>     one veteran.
PATCH  /trained-umas/<id>     change it (a game veteran: its label only).
DELETE /trained-umas/<id>     delete it; a team or run linking it keeps its uma.
POST   /trained-umas/import   write a game export: {items: [...]}; see trained_umas.import_veterans.

All IsAuthenticated, and account only by design: the Collection page shows a
guest a sign-in card. Every id goes through trained_umas.get_owned(), so
somebody else's veteran is a 404 identical to one that does not exist.

A veteran on the wire (catalogue ids only; the client draws names from
GET /pvp-catalogue):

    {id, uma, label, speed, stamina, power, guts, wit,
     apt: {turf, dirt, short, mile, medium, long, front, pace, late, end},
     rank_score, source: "import" | "manual", import_key, missing_from_import,
     memo, is_borrowed, has_game_data, skills: [{skill, level}], created_at, updated_at}

plus `game_data` (the raw game object, or null) on GET /trained-umas/<id> only:
it is about 7 KB a veteran, so the list leaves it out.

`apt` values are the game's 1..8 (G..S), null when not known. An import item is
the same minus id, label, source, memo, is_borrowed and the dates, plus
`import_key` (the game's trained_chara_id) and `game_data` (the veteran's whole
game object; the server takes the memo and the borrowed flag from it and strips
other players' account ids again: trained_umas.strip_other_players).

THE IMPORT MAY BE GZIPPED. A long-time account's raw veterans run to megabytes
(256 were 1.9 MB) against Django's 2.5 MB body limit; gzipped they were 90 KB.
The page sends `Content-Encoding: gzip`; GzipJSONParser unpacks it, refusing
anything that unpacks past IMPORT_MAX_BYTES (a zip bomb).

A refusal is a 400 with {"error": "<one sentence for the player>"}.
"""

import io
import json
import zlib

from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from calculatorapi import trained_umas
from calculatorapi.models import (
    STAT_FIELDS,
    TRAINED_UMA_CAP,
    TRAINED_UMA_GAME_DATA_MAX_BYTES,
    TRAINED_UMA_LABEL_MAX_LENGTH,
    TRAINED_UMA_SKILL_CAP,
    TRAINED_UMA_STAT_MAX,
    TrainedUma,
)
from calculatorapi.views.pvp_team import _first_error

_NOT_FOUND = {"error": "Veteran not found"}

# The most an import may unpack to: a full account of raw veterans with room
# to spare (the owner's 256 were 1.9 MB). Past it the request is refused
# before any JSON is parsed.
IMPORT_MAX_BYTES = 24 * 1024 * 1024


class GzipJSONParser(JSONParser):  # pylint: disable=too-few-public-methods
    """JSON, or gzipped JSON when the request says `Content-Encoding: gzip`.

    Unpacked in a bounded step (zlib's max_length), so a few kilobytes that
    would expand to gigabytes stop at IMPORT_MAX_BYTES and never reach memory.
    """

    def parse(self, stream, media_type=None, parser_context=None):
        request = (parser_context or {}).get("request")
        encoding = request.META.get("HTTP_CONTENT_ENCODING", "") if request is not None else ""
        if encoding.lower() != "gzip":
            return super().parse(stream, media_type, parser_context)
        unpacker = zlib.decompressobj(wbits=31)  # 31: a gzip header and trailer
        try:
            raw = unpacker.decompress(stream.read(), IMPORT_MAX_BYTES + 1)
        except zlib.error as exc:
            raise ParseError("That import couldn't be read.") from exc
        if len(raw) > IMPORT_MAX_BYTES or not unpacker.eof:
            raise ParseError("That import is too large.")
        return super().parse(io.BytesIO(raw), media_type, parser_context)


# ── Input ────────────────────────────────────────────────────────────────────

def _aptitude_field():
    return serializers.IntegerField(min_value=1, max_value=8, allow_null=True, required=False, default=None)


class _AptInput(serializers.Serializer):  # pylint: disable=abstract-method
    """The ten aptitudes (models.APTITUDE_KEYS), each 1..8 or null."""
    turf = _aptitude_field()
    dirt = _aptitude_field()
    short = _aptitude_field()
    mile = _aptitude_field()
    medium = _aptitude_field()
    long = _aptitude_field()
    front = _aptitude_field()
    pace = _aptitude_field()
    late = _aptitude_field()
    end = _aptitude_field()


class _SkillInput(serializers.Serializer):  # pylint: disable=abstract-method
    skill = serializers.IntegerField(min_value=1)
    level = serializers.IntegerField(min_value=1, max_value=10, required=False, default=1)


def _validate_skills(skills):
    if len(skills) > TRAINED_UMA_SKILL_CAP:
        raise serializers.ValidationError(f"A veteran holds up to {TRAINED_UMA_SKILL_CAP} skills.")
    if len({entry["skill"] for entry in skills}) != len(skills):
        raise serializers.ValidationError("The same skill is in there twice.")
    return skills


def _stat_field(**extra):
    return serializers.IntegerField(min_value=0, max_value=TRAINED_UMA_STAT_MAX, **extra)


class _VeteranInput(serializers.Serializer):  # pylint: disable=abstract-method
    uma = serializers.IntegerField(min_value=1)
    label = serializers.CharField(
        max_length=TRAINED_UMA_LABEL_MAX_LENGTH, allow_blank=True, required=False, default=""
    )
    speed = _stat_field()
    stamina = _stat_field()
    power = _stat_field()
    guts = _stat_field()
    wit = _stat_field()
    apt = _AptInput(required=False, default=dict)
    skills = _SkillInput(many=True, required=False, default=list)

    def validate_skills(self, skills):
        return _validate_skills(skills)


class _PatchInput(serializers.Serializer):  # pylint: disable=abstract-method
    """Every field optional; only what is sent changes."""
    uma = serializers.IntegerField(min_value=1, required=False)
    label = serializers.CharField(max_length=TRAINED_UMA_LABEL_MAX_LENGTH, allow_blank=True, required=False)
    speed = _stat_field(required=False)
    stamina = _stat_field(required=False)
    power = _stat_field(required=False)
    guts = _stat_field(required=False)
    wit = _stat_field(required=False)
    apt = _AptInput(required=False)
    skills = _SkillInput(many=True, required=False)

    def validate_skills(self, skills):
        return _validate_skills(skills)


class _ImportItem(_VeteranInput):  # pylint: disable=abstract-method
    import_key = serializers.IntegerField(min_value=1)
    rank_score = serializers.IntegerField(min_value=0, allow_null=True, required=False, default=None)
    # The veteran's whole game object (models/trained_uma.py, "ALL OF IT IS KEPT").
    game_data = serializers.DictField(required=False, allow_null=True, default=None)

    def validate_game_data(self, game_data):
        if game_data is not None and len(json.dumps(game_data)) > TRAINED_UMA_GAME_DATA_MAX_BYTES:
            raise serializers.ValidationError("One veteran in that file is too large to keep.")
        return game_data


class _ImportInput(serializers.Serializer):  # pylint: disable=abstract-method
    items = _ImportItem(many=True, max_length=TRAINED_UMA_CAP)


def _bad_request(message):
    return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)


# ── Output ───────────────────────────────────────────────────────────────────

def serialize_veteran(veteran, with_game_data=False):
    """One veteran on the wire. Reads `skills` through whatever was prefetched.
    `game_data` only when asked (one veteran's GET): the list defers it."""
    return {
        "id": veteran.id,
        "uma": veteran.uma_id,
        "label": veteran.label,
        **{stat: getattr(veteran, stat) for stat in STAT_FIELDS},
        "apt": veteran.aptitudes(),
        "rank_score": veteran.rank_score,
        "source": veteran.source,
        "import_key": veteran.import_key,
        "missing_from_import": veteran.missing_from_import,
        "memo": veteran.memo,
        "is_borrowed": veteran.is_borrowed,
        # From the source, not the column: the list defers game_data, and
        # reading it here would load every row's 7 KB one by one.
        "has_game_data": veteran.source == "import",
        "skills": [{"skill": row.skill_id, "level": row.level} for row in veteran.skills.all()],
        "created_at": veteran.created_at,
        "updated_at": veteran.updated_at,
        **({"game_data": veteran.game_data} if with_game_data else {}),
    }


def _fresh(user, veteran_id):
    return trained_umas.owned(user).get(id=veteran_id)


# ── Routes ───────────────────────────────────────────────────────────────────

@api_view(["GET", "POST"])
@permission_classes([permissions.IsAuthenticated])
def trained_uma_list(request):
    if request.method == "GET":
        return Response([serialize_veteran(veteran) for veteran in trained_umas.owned(request.user)])

    payload = _VeteranInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    try:
        veteran = trained_umas.create_manual(request.user, payload.validated_data)
    except trained_umas.VeteranRefused as refusal:
        return _bad_request(str(refusal))
    return Response(serialize_veteran(_fresh(request.user, veteran.id)), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def trained_uma_detail(request, veteran_id):
    try:
        veteran = trained_umas.get_owned(request.user, veteran_id)
    except TrainedUma.DoesNotExist:
        return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        full = TrainedUma.objects.prefetch_related("skills").get(id=veteran.id)
        return Response(serialize_veteran(full, with_game_data=True))
    if request.method == "DELETE":
        veteran.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    payload = _PatchInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    try:
        trained_umas.update(veteran, payload.validated_data)
    except trained_umas.VeteranRefused as refusal:
        return _bad_request(str(refusal))
    return Response(serialize_veteran(_fresh(request.user, veteran.id)))


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@parser_classes([GzipJSONParser])
def trained_uma_import(request):
    payload = _ImportInput(data=request.data)
    if not payload.is_valid():
        return _bad_request(_first_error(payload.errors))
    try:
        counts = trained_umas.import_veterans(request.user, payload.validated_data["items"])
    except trained_umas.VeteranRefused as refusal:
        return _bad_request(str(refusal))
    return Response(
        {**counts, "veterans": [serialize_veteran(veteran) for veteran in trained_umas.owned(request.user)]}
    )
