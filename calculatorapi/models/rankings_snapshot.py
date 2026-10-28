from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class RankingsSnapshot(models.Model):
    """One Rankings payload as it stood at the start of the week, so the page
    can show rank movement (notes/rankings-redesign-plan.md step 5).

    One row per cache name ("guides", "pvp:cm:21", ...), replaced by the first
    rebuild after Monday 00:00 UTC (rankings_snapshots.py). A table rather
    than the cache: the rankings cache is LocMem, emptied by every deploy, so
    a week-old copy kept there would rarely live a week. Private (analytics
    built from people's plans); never leaves production.
    """

    name = models.CharField(max_length=100, unique=True)
    generated_at = models.DateTimeField()
    # DjangoJSONEncoder: a payload carries datetimes (a banner's dates), which
    # DRF writes on the wire as ISO strings and plain json can't store.
    payload = models.JSONField(encoder=DjangoJSONEncoder)

    def __str__(self):
        return f"{self.name} ({self.generated_at:%Y-%m-%d})"
