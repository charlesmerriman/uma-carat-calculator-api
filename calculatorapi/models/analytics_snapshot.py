from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class AnalyticsSnapshot(models.Model):
    """One row per day: the admin analytics report as it stood that day.

    Every figure on the analytics page except traffic is a snapshot of the
    database right now, so on its own the page cannot say whether anything is
    growing. These rows are its memory: the page compares today against the
    nearest row 30 days back, and lists the first row of each month as History.

    Written by calculatorapi/analytics/snapshots.py, at most once a day: the
    first time the report is rebuilt on a new day (any staff visit to /admin/
    or the analytics page), and by `manage.py snapshot_analytics` on every
    deploy. Never edited after that.

    Aggregates only, like the report it copies: counts, percentages, averages,
    banner names. No row can be traced to a person.

    Deliberately NOT registered in the Django admin, for the same reason as
    DailyVisit: it is reporting output, and a hand-edited history is worse than
    none. Listed in content_snapshot.PRIVATE_MODELS, so pull_prod_content never
    copies it.
    """

    # UTC calendar day the report was BUILT on (its generated_at), which is
    # not always the day it was written: a report cached at 23:58 belongs to
    # the day it describes. Unique, so the database referees a race between two
    # requests that both find today's row missing.
    date = models.DateField(unique=True)
    # The report's cache key, e.g. "analytics:report:v2". The dict's shape
    # changes between releases; a reader can tell which shape it holds, and
    # snapshots.figure() reads every stored number defensively for that reason.
    shape = models.CharField(max_length=40)
    # The report dict minus the parts that have their own history (the traffic
    # lists) or are derived from these rows (the comparison and History). Dates
    # are stored as ISO strings by the encoder; nothing reads them back.
    report = models.JSONField(encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Analytics Snapshot"
        verbose_name_plural = "Analytics Snapshots"
        # Newest first: the readers want the recent end.
        ordering = ["-date"]

    def __str__(self):
        return f"Analytics snapshot {self.date}"
