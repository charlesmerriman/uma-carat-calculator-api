"""
The numbers for the Discord bot's daily digest (GET /bot/digest), posted to
the team's private #calc-ops (discord-bot-plan.md, Step 7).

AGGREGATES ONLY. Discord is a third party that keeps what it is sent, so this
returns counts and nothing that names or points at a person: no display
names, no handles, no ids. A test holds it to that.

It reads the same report the admin dashboard shows (cache.get_report), so the
digest and the dashboard can never disagree, and building that report keeps
the day's history snapshot too, exactly as opening the dashboard would. The
few figures the report doesn't hold (yesterday's sign-ups, the gallery's last
24 hours) are counted here directly.

"Yesterday" is the last whole UTC day, the site's one timezone.
"""

import datetime

from django.utils import timezone

from ..models import GalleryComment, GalleryPost, GalleryReport
from .cache import get_report
from .people import non_staff_users, saved_since

AVERAGE_DAYS = 7


def _visits(daily, day):
    row = daily.get(day)
    return {"visitors": row["unique_visitors"] if row else 0, "page_views": row["page_views"] if row else 0}


def digest():
    """The digest's figures, as plain numbers."""
    report = get_report()
    now = timezone.now()
    today = timezone.localdate()
    yesterday = today - datetime.timedelta(days=1)
    daily = {row["date"]: row for row in report["daily_visits"]}
    week = [yesterday - datetime.timedelta(days=offset) for offset in range(AVERAGE_DAYS)]
    users = non_staff_users()
    day_ago = now - datetime.timedelta(days=1)
    return {
        "date": yesterday.isoformat(),
        "visits": {
            "yesterday": _visits(daily, yesterday),
            "day_before": _visits(daily, yesterday - datetime.timedelta(days=1)),
            # Days with no visit row count as zero, so a quiet day pulls the average down.
            "average_7_days": round(sum(_visits(daily, day)["visitors"] for day in week) / AVERAGE_DAYS, 1),
        },
        "accounts": {
            "total": report["total_users"],
            "new_yesterday": users.filter(date_joined__date=yesterday).count(),
            "new_day_before": users.filter(date_joined__date=yesterday - datetime.timedelta(days=1)).count(),
            "new_last_7_days": users.filter(date_joined__gte=now - datetime.timedelta(days=AVERAGE_DAYS)).count(),
        },
        "saved_last_7_days": users.filter(saved_since(now - datetime.timedelta(days=AVERAGE_DAYS))).count(),
        "active_supporters": sum(row["active"] for row in report["supporters"]),
        "gallery_last_24_hours": {
            "posts": GalleryPost.objects.filter(published_at__gte=day_ago).count(),
            "comments": GalleryComment.objects.filter(created_at__gte=day_ago).count(),
            "reports": GalleryReport.objects.filter(created_at__gte=day_ago).count(),
        },
        # Items waiting for a moderator, not reports: three reports on one
        # post are one decision.
        "open_report_items": len({
            (report.post_id, report.comment_id)
            for report in GalleryReport.objects.filter(resolved_at__isnull=True).only("post_id", "comment_id")
        }),
    }
