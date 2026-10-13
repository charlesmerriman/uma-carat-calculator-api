"""
Creates the PvP guides page's row from its seed file, and nothing else.

The 0074 pattern: get_or_create by slug through seed_page(), never the shared
seed(), which also walks the FAQ and would bring back every FAQ item an editor
has deleted in prod. The reverse is a no-op; the row is content once it exists.
"""

from django.db import migrations

from calculatorapi import site_content_seed


def seed_pvp_guides_page(apps, schema_editor):
    site_content_seed.seed_page(apps.get_model("calculatorapi", "SitePage"), "pvp-guides")


class Migration(migrations.Migration):

    dependencies = [
        ("calculatorapi", "0084_pvp_guide"),
    ]

    operations = [
        migrations.RunPython(seed_pvp_guides_page, migrations.RunPython.noop),
    ]
