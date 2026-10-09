"""
Callbacks wired into django-unfold via the UNFOLD dict in settings.py.

  - environment_callback: the "Production"/"Local" badge in the admin header.
  - dashboard_callback:    headline stat cards on the admin landing page,
                           rendered by templates/admin/custom_index.html.

Both are referenced by dotted path in settings so they are only imported when
the admin renders — keeping settings.py free of app-model imports.
"""

from django.conf import settings

from .analytics import get_report


def environment_callback(request):  # pylint: disable=unused-argument
    """
    Header badge: [label, color]. Colors map to unfold's badge palette
    (info/success/warning/danger). Driven by DEBUG so the live admin is
    visually distinct from a local one.
    """
    if settings.DEBUG:
        return ["Local", "info"]
    return ["Production", "danger"]


def _since(comparison, key):
    """"+3 since 2026-09-09" for one tracked figure, or "" with nothing to
    compare against yet (see analytics.snapshots.comparison)."""
    delta = comparison["figures"][key]["delta"]
    if delta is None:
        return ""
    return f"{delta:+} since {comparison['since']:%Y-%m-%d}"


def dashboard_callback(request, context):  # pylint: disable=unused-argument
    """
    Inject headline KPI cards into the admin index context. Reuses the same
    aggregate report as the analytics dashboard (staff excluded, aggregates
    only — never per-user rows), and the same cached copy of it: this runs on
    every /admin/ load, so building the report here each time made the
    cheapest admin page the most expensive one.

    Wrapped defensively: the landing page must still render even if the report
    query fails (e.g. a half-migrated database), just without the cards.
    """
    try:
        report = get_report()
    except Exception:  # pylint: disable=broad-except
        context["kpi_cards"] = []
        return context

    # Adoption counts for the two paid products, looked up by label.
    paid = {row["label"]: row for row in report["paid_products"]}
    daily_carat = paid.get("Daily Carat Pack", {}).get("count", 0)
    training_pass = paid.get("Training Pass", {}).get("count", 0)

    # Most-planned uma banner (list is pre-sorted by planners desc).
    top_uma = report["popular_uma_banners"][0] if report["popular_uma_banners"] else None

    comparison = report["comparison"]
    engaged_since = _since(comparison, "engaged_users")
    carat_delta, pass_delta = (comparison["figures"][key]["delta"]
                               for key in ("daily_carat", "training_pass"))
    paid_since = ""
    if carat_delta is not None and pass_delta is not None:
        paid_since = (f"{carat_delta:+} / {pass_delta:+} "
                      f"since {comparison['since']:%Y-%m-%d}")

    context["kpi_cards"] = [
        {
            "title": "Total users",
            "value": report["total_users"],
            "footer": _since(comparison, "total_users"),
            "icon": "group",
        },
        {
            "title": "Active planners",
            "value": report["engaged_users"],
            "footer": ", ".join(filter(None, [
                f"{report['engaged_pct']}% of users", engaged_since])),
            "icon": "person_check",
        },
        {
            "title": "Daily Carat / Training Pass",
            "value": f"{daily_carat} / {training_pass}",
            "footer": paid_since or "paid-product adopters",
            "icon": "paid",
        },
        {
            "title": "Top planned banner",
            "value": top_uma["name"] if top_uma else "—",
            "footer": f"{top_uma['planners']} planners" if top_uma else "no plans yet",
            "icon": "trophy",
        },
    ]
    return context
