"""
Staff-only analytics dashboard, served inside the Django admin.

The view itself has no auth logic: it is wrapped with
``admin.site.admin_view()`` in calculatorproject/urls.py, which redirects
anonymous and non-staff users to the admin login and marks responses
never-cache. All numbers come from calculatorapi.analytics — this module
only renders them (HTML page or CSV download), and both renderers are one loop
over analytics.tables, so a new section needs no change here.
"""

import csv

from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from calculatorapi.analytics import csv_rows, get_report, page_tables


def analytics_dashboard(request):
    """Render the analytics snapshot; ``?format=csv`` downloads it instead.

    Both read the cached report (analytics.get_report), so the CSV is the page
    the person just looked at. ``?refresh=1`` is the page's "Refresh now" link:
    it rebuilds the report, then redirects to the plain URL so a reload or a
    bookmark of the refresh link does not rebuild on every visit.
    """
    if request.GET.get("refresh") == "1":
        get_report(refresh=True)
        return redirect(request.path)
    report = get_report()
    if request.GET.get("format") == "csv":
        return _csv_response(report)
    context = {
        # each_context() supplies the admin chrome (site header, nav
        # sidebar, user tools) so the template renders like a native
        # admin page.
        **admin.site.each_context(request),
        "title": "Analytics",
        "report": report,
        "tables": page_tables(report),
    }
    return render(request, "admin/analytics.html", context)


def _csv_response(report):
    """
    Serialize the report into a single sectioned CSV.

    One file (section title row, header row, data rows, blank line) keeps
    the download simple to open in Google Sheets/Excel. History no longer
    depends on keeping these files: the server stores a daily snapshot and the
    History section carries it (analytics/snapshots.py).
    """
    today = timezone.localdate().isoformat()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="analytics-{today}.csv"'
    writer = csv.writer(response)

    writer.writerow(["Uma Calculator Analytics",
                     f"generated {report['generated_at']:%Y-%m-%d %H:%M}"])
    writer.writerow([])
    writer.writerows(csv_rows(report))
    return response
