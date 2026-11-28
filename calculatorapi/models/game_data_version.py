"""One row per game data version the daily check has seen on the game's CDN.

WHY A TABLE
-----------
The CDN has no listing and nothing on it says which version the game runs
(notes/masterdata-cdn-plan.md). Upcoming versions sit there days before release
(10008800 went up about a week before 2026-12-01), and a snapshot taken from
one marks its skills as on Global early. So which version is LIVE is a
person's choice, and this table is where it is made: an admin action ("Mark
live"), later a Discord button. The daily check (scripts/masterdata_check.py,
a GitHub Action) reads it, and only the live version ever becomes a snapshot PR.

WHAT IT IS NOT
--------------
Not game content. Nothing here reaches a player, and nothing imports from it:
game data still arrives only as a committed snapshot that a deploy imports
(CLAUDE.md, "nothing loads content INTO prod"). It is ops state, PRIVATE in
content_snapshot, and ignored by the /calculator-data cache.

THE STATUSES
------------
new      seen on the CDN, nobody has decided yet. Reported once.
live     what the game runs. At most one per region (a database constraint).
old      a live version that a newer one replaced, or a version below the
         live one that was never live. The check stops re-reading it.
ignored  a person said "not this one" (a staged build that was pulled, say).

A REVISION is the same version number whose master.mdb changed on the CDN
(new size or hash). `master_size` / `master_hash` always hold the latest seen;
`revised_at` says when they last changed.
"""

from django.db import models, transaction
from django.utils import timezone


class GameDataRegion(models.TextChoices):
    GLOBAL = "global", "Global"
    # Not fetched yet: the JP CDN's root manifest URL is unknown (the plan's
    # add-on C). The column exists so JP needs no migration when it is.
    JP = "jp", "JP"


class GameDataStatus(models.TextChoices):
    NEW = "new", "New"
    LIVE = "live", "Live"
    OLD = "old", "Old"
    IGNORED = "ignored", "Ignored"


class GameDataVersion(models.Model):
    region = models.CharField(max_length=8, choices=GameDataRegion.choices, default=GameDataRegion.GLOBAL)
    # The number in the CDN's root manifest URL, e.g. 10008795.
    version = models.PositiveIntegerField()
    # The `master.mdb.lz4` entry in the master category manifest: its packed
    # size and its 16 bytes in hex. What changes when a version is revised.
    master_size = models.PositiveBigIntegerField()
    master_hash = models.CharField(max_length=32)
    status = models.CharField(max_length=8, choices=GameDataStatus.choices, default=GameDataStatus.NEW)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    # When the check last saw it on the CDN (every run, for a version it re-reads).
    checked_at = models.DateTimeField(default=timezone.now)
    # When the size or hash last changed. Null: never revised since first seen.
    revised_at = models.DateTimeField(null=True, blank=True)
    # When and by whom the status last changed by hand ("Zac (admin)").
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ("region", "-version")
        verbose_name = "Game data version"
        verbose_name_plural = "Game data versions"
        constraints = [
            models.UniqueConstraint(fields=("region", "version"), name="unique_game_data_version"),
            # The pin: two live versions would leave the check unable to say
            # which one a snapshot PR should come from.
            models.UniqueConstraint(
                fields=("region",), condition=models.Q(status="live"),
                name="one_live_game_data_version_per_region",
            ),
        ]

    def __str__(self):
        return f"{self.get_region_display()} {self.version} ({self.get_status_display().lower()})"

    def mark_live(self, by=""):
        """Make this THE live version of its region. The previous live one, and
        any undecided version below this one, become old (the game moved past
        them). Ignored rows stay ignored."""
        now = timezone.now()
        with transaction.atomic():
            GameDataVersion.objects.filter(region=self.region).exclude(pk=self.pk).filter(
                models.Q(status=GameDataStatus.LIVE)
                | models.Q(status=GameDataStatus.NEW, version__lt=self.version)
            ).update(status=GameDataStatus.OLD, status_changed_at=now, status_changed_by=by)
            self.status = GameDataStatus.LIVE
            self.status_changed_at = now
            self.status_changed_by = by
            self.save(update_fields=["status", "status_changed_at", "status_changed_by"])
            # The skills this version first defined turn global now (owner,
            # 2026-11-28). Imported here: the service imports this model.
            from calculatorapi.game_data_versions import apply_live_version  # pylint: disable=import-outside-toplevel
            return apply_live_version(self.region, self.version)

    def mark_ignored(self, by=""):
        """Set aside: the check stops re-reading it and no PR is ever made from it."""
        self.status = GameDataStatus.IGNORED
        self.status_changed_at = timezone.now()
        self.status_changed_by = by
        self.save(update_fields=["status", "status_changed_at", "status_changed_by"])
