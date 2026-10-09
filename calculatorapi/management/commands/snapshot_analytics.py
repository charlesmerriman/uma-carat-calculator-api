"""
Keeps today's analytics snapshot, unless one already exists.

The admin writes the day's snapshot itself the first time the report is rebuilt
on a new day (see calculatorapi/analytics/snapshots.py). This command is the
second writer: it runs in the deploy chain (.do/app.yaml run_command), so every
release leaves a point in the history even in a week when nobody opened the
admin.

Idempotent: a second run on the same day writes nothing and says so.

NEVER FAILS A DEPLOY. Any error building the report is printed and the command
still exits 0, the same contract sync_changelog keeps. A missing day of history
costs nothing; an API that will not boot because of a reporting query costs the
whole site. The analytics page shows the same error to whoever opens it next.

Usage:
    python manage.py snapshot_analytics
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from calculatorapi.analytics.report import build_analytics_report
from calculatorapi.analytics.snapshots import ensure_today
from calculatorapi.models import AnalyticsSnapshot


class Command(BaseCommand):
    help = "Keep today's analytics snapshot, unless one already exists."

    def handle(self, *args, **options):
        today = timezone.localdate()
        # Checked before building: the report is a few dozen queries, and on
        # most deploys someone has already opened the admin today.
        if AnalyticsSnapshot.objects.filter(date=today).exists():
            self.stdout.write(
                f"A snapshot for {today} already exists; nothing written.")
            return

        try:
            written = ensure_today(build_analytics_report())
        except Exception as error:  # pylint: disable=broad-except
            self.stdout.write(self.style.WARNING(
                f"Snapshot skipped, the report failed to build: {error!r}"))
            return

        if written:
            self.stdout.write(self.style.SUCCESS(
                f"Kept the analytics snapshot for {today}."))
        else:
            # Another writer (a staff page load) won the race in between.
            self.stdout.write(
                f"A snapshot for {today} already exists; nothing written.")
