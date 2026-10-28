from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class RankingsPayload(models.Model):
    """The latest build of one Rankings payload, as sent (shares only), so a
    deploy or restart serves the last build instead of making a visitor wait
    for a new one (rankings_cache.py, notes/rankings-speed-plan.md PR 3).

    One row per cache KEY ("rankings:pull-plans:v4"): the key carries the
    shape version, so a version bump leaves the old rows unread. Written only
    by a rebuild, at most every half day per key. Analytics built from
    people's plans: private, never leaves production, not in the admin.
    """

    key = models.CharField(max_length=150, unique=True)
    generated_at = models.DateTimeField()
    # DjangoJSONEncoder: a payload carries datetimes (a banner's dates).
    payload = models.JSONField(encoder=DjangoJSONEncoder)

    def __str__(self):
        return f"{self.key} ({self.generated_at:%Y-%m-%d %H:%M})"
