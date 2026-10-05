"""
Creates the daily legend races page's row from its seed file, and nothing else.

get_or_create by slug, so an existing row (an editor's words, or a fresh
database where 0058 already seeded every page in site_content_seed.PAGES) is
left alone. It calls seed_page() and NOT the shared seed(): seed() also walks
the FAQ, and would bring back every FAQ item an editor has deleted in prod.

The reverse is a no-op. The row is content once it exists, and 0073's reverse
leaves the table in place.
"""

from django.db import migrations

from calculatorapi import site_content_seed


def seed_daily_legend_races_page(apps, schema_editor):
    site_content_seed.seed_page(
        apps.get_model("calculatorapi", "SitePage"), "daily-legend-races"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("calculatorapi", "0073_daily_legend_races"),
    ]

    operations = [
        migrations.RunPython(seed_daily_legend_races_page, migrations.RunPython.noop),
    ]
