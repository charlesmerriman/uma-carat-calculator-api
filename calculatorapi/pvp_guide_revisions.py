"""
Recording a PvP guide's saves (models/pvp_guide_revision.py).

`record(guide, user, source)` is called by every path that writes a guide,
after the write: the site's editor, the admin page, the three actions,
publish and unpublish, a restore. It serializes the guide as it now is,
bumps `PvpGuide.version` (the concurrency token the site's editor sends
back), coalesces an autosave into the previous one, and prunes.

COALESCING. A site save by the same author within COALESCE_WINDOW of the
previous revision, when that one is also a site save of theirs, replaces it
(same number, new document and time). A different author, a different
source, or a longer pause starts a new row. So two people editing at once
each get their own rows, in order.

PRUNING. The last KEEP rows stay, plus every PUBLISH row (what "changed
since it was published" compares against). Pruning deletes, never
renumbers, so a number named in a link stays the same revision.

The version bump goes through update(), never save(): see the "NO SIGNAL
ON A SAVE" note in pvp_guide_document.py.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import pvp_guide_document, pvp_guides
from .models import PvpGuide, PvpGuideRevision, PvpGuideRevisionSource

COALESCE_WINDOW = timedelta(minutes=10)
KEEP = 40


def fresh(guide):
    """The guide re-read with every section prefetched, after a write."""
    return pvp_guides.guide_queryset().get(pk=guide.pk)


@transaction.atomic
def record(guide, user, source):
    """Bump the guide's version and write (or coalesce into) a revision.
    Returns the revision. `guide` is refreshed in place with the new version."""
    PvpGuide.objects.filter(pk=guide.pk).update(version=F("version") + 1)
    guide.refresh_from_db(fields=["version"])
    document = pvp_guide_document.serialize(fresh(guide))
    author = user if user is not None and user.is_authenticated else None
    now = timezone.now()

    latest = PvpGuideRevision.objects.filter(guide=guide).order_by("-number").first()
    if _coalesces(latest, author, source, now):
        latest.document = document
        latest.counts = pvp_guide_document.counts(document)
        latest.saved_at = now
        latest.save(update_fields=["document", "counts", "saved_at"])
        return latest

    revision = PvpGuideRevision.objects.create(
        guide=guide,
        number=(latest.number + 1) if latest else 1,
        document=document,
        author=author,
        source=source,
        counts=pvp_guide_document.counts(document),
        saved_at=now,
    )
    _prune(guide)
    return revision


def _coalesces(latest, author, source, now):
    """A site save by the same person, soon after their last site save."""
    if source != PvpGuideRevisionSource.SITE or latest is None or author is None:
        return False
    if latest.source != PvpGuideRevisionSource.SITE or latest.author_id != author.pk:
        return False
    return now - latest.saved_at <= COALESCE_WINDOW


def _prune(guide):
    keep = list(
        PvpGuideRevision.objects.filter(guide=guide)
        .order_by("-number")
        .values_list("number", flat=True)[:KEEP]
    )
    (
        PvpGuideRevision.objects.filter(guide=guide)
        .exclude(number__in=keep)
        .exclude(source=PvpGuideRevisionSource.PUBLISH)
        .delete()
    )


def listing(guide, viewer=None):
    """The revisions, newest first, as the editor's list draws them: the
    author's HANDLE (never a display name) and whether it is the viewer."""
    rows = PvpGuideRevision.objects.filter(guide=guide).select_related("author").order_by("-number")
    return [
        {
            "number": row.number,
            "saved_at": row.saved_at,
            "by": row.author.username if row.author else None,
            "is_you": viewer is not None and row.author_id == viewer.pk,
            "source": row.source,
            "counts": row.counts,
        }
        for row in rows
    ]


__all__ = ["COALESCE_WINDOW", "KEEP", "fresh", "record", "listing"]
