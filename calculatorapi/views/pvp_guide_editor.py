"""
The site's guide editor: a PvP guide read and written as one document.

GET    /pvp-guides/editable                   the guides the caller may edit, and the
                                               events that have no guide yet
POST   /pvp-guides                            {kind, event, scenario?, copy_from?} -> 201 the document
GET    /pvp-guides/<id>/document              the document (pvp_guide_document.serialize)
PUT    /pvp-guides/<id>/document              {version, ...document} -> {document, guide}
                                               400 {error, errors: [{path, message}]}
                                               409 {error, current} when the guide moved on
PATCH  /pvp-guides/<id>                       {is_published} -> the document
DELETE /pvp-guides/<id>                       204; a published guide is refused (400)
GET    /pvp-guides/<id>/revisions             [{number, saved_at, by, is_you, source, counts}]
GET    /pvp-guides/<id>/revisions/<n>         {number, source, saved_at, document}
POST   /pvp-guides/<id>/actions/copy-from     {source, sections?, preview?}
POST   /pvp-guides/<id>/actions/sheet         {preview?}
POST   /pvp-guides/<id>/actions/track-greens  {preview?}

All IsAuthenticated and then pvp_guide_editing.can_edit. A guide the caller
may not edit, and one that does not exist, are the same 404 as the public
route's (nobody learns which ids are guides). The list and create routes
answer 403 to a signed-in account that is not an editor.

The document is pvp_guide_document.py's; the reader's shape that rides
along with every write (`guide`) is views.pvp_guide.serialize_guide, so the
client primes the page's cache without a second request. The actions apply
to the SAVED guide (the client flushes its autosave first) and answer the
document, so the client replaces its own copy with the result.
"""

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from calculatorapi import (
    pvp_guide_document,
    pvp_guide_editing,
    pvp_guide_revisions,
    pvp_guides,
    pvp_sheet,
    pvp_track_skills,
)
from calculatorapi.models import (
    ChampionsMeeting,
    LeagueOfHeroes,
    PvpGuide,
    PvpGuideRevision,
    PvpGuideRevisionSource,
)
from calculatorapi.views.pvp_catalogue import image_url
from calculatorapi.views.pvp_guide import NOT_FOUND, guide_queryset, serialize_guide

NOT_AN_EDITOR = {"error": "Only guide editors can do that."}


class EditorSaveThrottle(UserRateThrottle):
    """Autosave at a two-second pause cannot reach this; a stuck client is capped."""
    scope = "guide_editor"


class EditorActionThrottle(UserRateThrottle):
    """Copy, sheet and track greens: a preview-then-apply loop, a few an hour."""
    scope = "guide_editor_action"


# ── Lookups ──────────────────────────────────────────────────────────────────

def _editable_guide(request, guide_id):
    """The guide, prefetched, or None when it does not exist or the caller
    may not edit it (one answer for both)."""
    guide = guide_queryset().filter(pk=guide_id).first()
    if guide is None or not pvp_guide_editing.can_edit(request.user, guide):
        return None
    return guide


def _not_found():
    return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)


def _refused(message):
    """A refusal with no field to point at: the same two keys as _invalid,
    with an empty path, so the client reads one shape."""
    return Response(
        {"error": message, "errors": [{"path": "", "message": message}]},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _invalid(error_list):
    """400 with every problem and its path, plus the first one as `error`
    for a client that shows one sentence."""
    return Response(
        {"error": error_list[0]["message"], "errors": error_list},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _answer(guide, request):
    """The document and the reader's shape, after a write."""
    fresh = pvp_guide_revisions.fresh(guide)
    return {
        "document": pvp_guide_document.serialize(fresh),
        "guide": serialize_guide(fresh, request),
    }


# ── The list ─────────────────────────────────────────────────────────────────

def _event_rows(model, kind):
    """Events of one kind with no guide, newest first (global start, else JP)."""
    rows = (
        model.objects.filter(pvp_guide__isnull=True)
        .annotate(_starts=Coalesce(F("global_start_date"), F("jp_start_date")))
        .order_by(F("_starts").desc(nulls_last=True), "-id")
    )
    return [{"kind": kind, "event": row.id, "name": str(row)} for row in rows]


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def pvp_guide_editable(request):
    if not pvp_guide_editing.can_edit(request.user):
        return Response(NOT_AN_EDITOR, status=status.HTTP_403_FORBIDDEN)
    guides = (
        PvpGuide.objects.select_related("champions_meeting", "league_of_heroes")
        .prefetch_related("uma_recommendations", "style_notes", "skill_recommendations", "decks")
        .order_by("-updated_at", "-id")
    )
    return Response({
        "guides": [
            {
                "id": guide.id,
                "kind": "cm" if guide.champions_meeting_id else "loh",
                "event": guide.champions_meeting_id or guide.league_of_heroes_id,
                "name": str(guide),
                "is_published": guide.is_published,
                "version": guide.version,
                "updated_at": guide.updated_at,
                "filled": pvp_guides.styles_with_content(guide),
            }
            for guide in guides
        ],
        "events": _event_rows(ChampionsMeeting, "cm") + _event_rows(LeagueOfHeroes, "loh"),
    })


# ── Create ───────────────────────────────────────────────────────────────────

class _CreateInput(serializers.Serializer):  # pylint: disable=abstract-method
    kind = serializers.ChoiceField(choices=list(pvp_guide_editing.EVENT_FOR_KIND))
    event = serializers.IntegerField(min_value=1)
    scenario = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    copy_from = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([EditorActionThrottle])
def pvp_guide_create(request):
    if not pvp_guide_editing.can_edit(request.user):
        return Response(NOT_AN_EDITOR, status=status.HTTP_403_FORBIDDEN)
    payload = _CreateInput(data=request.data)
    if not payload.is_valid():
        return _invalid(pvp_guide_document.flatten_errors(payload.errors))
    data = payload.validated_data
    try:
        guide = pvp_guide_editing.create_guide(
            request.user, kind=data["kind"], event_id=data["event"],
            scenario_id=data["scenario"], copy_from=data["copy_from"],
        )
    except pvp_guide_editing.GuideRefused as refusal:
        return _refused(str(refusal))
    return Response(
        pvp_guide_document.serialize(pvp_guide_revisions.fresh(guide)),
        status=status.HTTP_201_CREATED,
    )


# ── The document ─────────────────────────────────────────────────────────────

def _save(request, guide):
    payload = pvp_guide_document.DocumentPut(data=request.data)
    if not payload.is_valid():
        return _invalid(pvp_guide_document.flatten_errors(payload.errors))
    data = payload.validated_data
    with transaction.atomic():
        # Locked for the compare-and-write: two saves of one guide queue
        # here, and the second sees the first's version (a no-op on SQLite,
        # a row lock on PostgreSQL).
        current = PvpGuide.objects.select_for_update().get(pk=guide.pk)
        if data["version"] != current.version:
            return Response(
                {
                    "error": "Someone saved this guide since you opened it.",
                    "current": pvp_guide_document.serialize(pvp_guide_revisions.fresh(guide)),
                },
                status=status.HTTP_409_CONFLICT,
            )
        problems = pvp_guide_document.errors(data)
        if problems:
            return _invalid(problems)
        try:
            pvp_guide_editing.save_document(current, request.user, data)
        except pvp_guide_editing.GuideRefused as refusal:
            return _refused(str(refusal))
    return Response(_answer(guide, request))


@api_view(["GET", "PUT"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([EditorSaveThrottle])
def pvp_guide_document_view(request, guide_id):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    if request.method == "GET":
        return Response(pvp_guide_document.serialize(guide))
    return _save(request, guide)


class _PublishInput(serializers.Serializer):  # pylint: disable=abstract-method
    is_published = serializers.BooleanField()


@api_view(["PATCH", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def pvp_guide_editor_detail(request, guide_id):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    if request.method == "DELETE":
        try:
            pvp_guide_editing.delete_guide(guide)
        except pvp_guide_editing.GuideRefused as refusal:
            return _refused(str(refusal))
        return Response(status=status.HTTP_204_NO_CONTENT)
    payload = _PublishInput(data=request.data)
    if not payload.is_valid():
        return _invalid(pvp_guide_document.flatten_errors(payload.errors))
    pvp_guide_editing.publish(guide, request.user, payload.validated_data["is_published"])
    return Response(pvp_guide_document.serialize(pvp_guide_revisions.fresh(guide)))


# ── Revisions ────────────────────────────────────────────────────────────────

@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def pvp_guide_revision_list(request, guide_id):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    return Response(pvp_guide_revisions.listing(guide, request.user))


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def pvp_guide_revision_detail(request, guide_id, number):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    revision = PvpGuideRevision.objects.filter(guide=guide, number=number).first()
    if revision is None:
        return Response({"error": "That revision isn't there any more."}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        "number": revision.number,
        "source": revision.source,
        "saved_at": revision.saved_at,
        "document": revision.document,
    })


# ── Actions ──────────────────────────────────────────────────────────────────

class _PreviewInput(serializers.Serializer):  # pylint: disable=abstract-method
    preview = serializers.BooleanField(required=False, default=True)


class _CopyInput(_PreviewInput):  # pylint: disable=abstract-method
    source = serializers.IntegerField(min_value=1)
    sections = serializers.MultipleChoiceField(
        choices=pvp_guides.SECTION_KEYS, required=False, default=set(pvp_guides.SECTION_KEYS),
    )


def _action_payload(serializer, request):
    payload = serializer(data=request.data)
    if not payload.is_valid():
        return None, _invalid(pvp_guide_document.flatten_errors(payload.errors))
    return payload.validated_data, None


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([EditorActionThrottle])
def pvp_guide_copy_from(request, guide_id):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    data, error = _action_payload(_CopyInput, request)
    if error:
        return error
    source = PvpGuide.objects.filter(pk=data["source"]).first()
    if source is None or source.pk == guide.pk:
        return _refused("Pick another guide to copy from.")
    sections = [key for key in pvp_guides.SECTION_KEYS if key in data["sections"]]
    with transaction.atomic():
        copied = pvp_guides.copy_guide(source, guide, sections)
        if data["preview"]:
            transaction.set_rollback(True)
            return Response({"counts": copied})
    pvp_guide_revisions.record(guide, request.user, PvpGuideRevisionSource.COPY)
    return Response({"counts": copied, **_answer(guide, request)})


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([EditorActionThrottle])
def pvp_guide_load_sheet(request, guide_id):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    data, error = _action_payload(_PreviewInput, request)
    if error:
        return error
    try:
        summary = pvp_sheet.load_guide_from_sheet(guide, preview=data["preview"])
    except pvp_sheet.SheetError as exc:
        return _refused(str(exc))
    if data["preview"]:
        return Response({"summary": summary})
    pvp_guide_revisions.record(guide, request.user, PvpGuideRevisionSource.SHEET)
    return Response({"summary": summary, **_answer(guide, request)})


def _skill_row(skill):
    return {
        "id": skill.id,
        "name": skill.name,
        "version": skill.version_label,
        "image": image_url(skill.image.name if skill.image else ""),
    }


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([EditorActionThrottle])
def pvp_guide_track_greens(request, guide_id):
    guide = _editable_guide(request, guide_id)
    if guide is None:
        return _not_found()
    data, error = _action_payload(_PreviewInput, request)
    if error:
        return error
    if data["preview"]:
        suggestion = pvp_track_skills.suggest(guide)
        return Response({
            "add": [_skill_row(skill) for skill in suggestion["add"]],
            "already": [_skill_row(skill) for skill in suggestion["already"]],
            "problems": suggestion["problems"],
        })
    added = pvp_track_skills.fill(guide)
    pvp_guide_revisions.record(guide, request.user, PvpGuideRevisionSource.TRACK_GREENS)
    return Response({"added": [_skill_row(skill) for skill in added], **_answer(guide, request)})
