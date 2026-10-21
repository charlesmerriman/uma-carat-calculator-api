"""
One saved state of a PvP guide: the whole guide as a document, who saved it
and from where.

WHY A DOCUMENT PER SAVE, NOT A HISTORY PER TABLE
-------------------------------------------------
A guide is nine tables; a save changes rows in several of them at once. What
an editor wants back is "the guide as it was at 14:02", not a per-row
history to reassemble, so the row holds the guide's document (the same JSON
the site's editor reads and writes: pvp_guide_document.py) and a restore is
a save of that document. django-simple-history was considered and rejected
for the same reason (pvp-guide-builder-plan.md, "Trade-offs").

WHAT WRITES ONE
---------------
Every path that writes a guide, through pvp_guide_revisions.record: the
site's editor, the admin page (PvpGuideAdmin.save_related), the three
actions (copy, sheet, track greens), publish and unpublish, a restore. So the
two editors share one history. The site's autosaves are COALESCED: a save by
the same person within ten minutes of their last site save replaces that row
rather than adding one, so an afternoon of typing is a handful of rows.

PRIVATE (content_snapshot.PRIVATE_MODELS): it names who saved. Off the public
payload cache's radar too (public_payload_cache._IRRELEVANT_MODELS): a
revision is written on every autosave, and the 2026-09-23 lesson applies.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from .pvp_guide import PvpGuide


class PvpGuideRevisionSource(models.TextChoices):
    """Where a save came from. `PUBLISH` rows survive the per-guide cap: they
    are what "changed since it was published" compares against."""
    SITE = "site", "The site's editor"
    ADMIN = "admin", "The admin page"
    SHEET = "sheet", "Load from the sheet"
    COPY = "copy", "Copy from another guide"
    TRACK_GREENS = "track_greens", "Fill track green skills"
    RESTORE = "restore", "Restore of an earlier revision"
    PUBLISH = "publish", "Published"
    UNPUBLISH = "unpublish", "Unpublished"


class PvpGuideRevision(models.Model):
    guide = models.ForeignKey(PvpGuide, on_delete=models.CASCADE, related_name="revisions")
    # 1, 2, 3 per guide. Coalescing keeps a number while its row is replaced.
    number = models.PositiveIntegerField()
    # The guide as pvp_guide_document.serialize wrote it at that moment.
    document = models.JSONField()
    # Who saved. SET_NULL: the history outlives an account; the handle is all
    # the list ever shows (never a display name).
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source = models.CharField(max_length=20, choices=PvpGuideRevisionSource.choices)
    # Rows per section at the time, so the list can say "22 umas, 14 decks"
    # without opening the document.
    counts = models.JSONField(default=dict, blank=True)
    saved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-number",)
        verbose_name = "PvP guide revision"
        verbose_name_plural = "PvP guide revisions"
        constraints = [
            models.UniqueConstraint(fields=["guide", "number"], name="unique_pvp_guide_revision"),
        ]

    def __str__(self):
        return f"{self.guide} #{self.number} ({self.get_source_display()})"
