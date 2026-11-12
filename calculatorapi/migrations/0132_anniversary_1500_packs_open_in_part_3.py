"""Fill-blanks: an anniversary's 1500 Carat Pack goes on sale with Part 3.

Daptrius, 2026-11-12 (board post 1567668424887509263): "The only things that are
consistently in part 3 are 1500 carat packs. They are in part 3 in almost all
anniversaries." The one exception, the 2.5th Anniversary, sells it with Part 2
(Daptrius, same post, message 1570537919566057493), so it is left out. The
owner chose to set this here, on deploy (notes/campaign-purchase-parts-plan.md).

It sets `opens_with_part` to 3 only where it is still blank, only on products
named exactly "1500 Carat Pack", only on `anniversary` campaigns that have a
Part 3 linked and are not named in EXCEPTIONS (where there is none, Part 3 would fall back to the main part
anyway, so the row is left blank rather than holding a value that means
nothing). It creates and deletes nothing, and is a no-op on a database without
those rows. Its own migration, apart from 0131's column (rules: never update
rows and ALTER one table in one migration).
"""

from django.db import migrations

PACK_NAME = "1500 Carat Pack"
PART = 3
# Anniversaries whose 1500 pack goes on sale with Part 2 after all, by exact name.
EXCEPTIONS = ("2.5th Anniversary",)


def set_part_3(apps, schema_editor):
    AnniversaryEventProduct = apps.get_model("calculatorapi", "AnniversaryEventProduct")
    AnniversaryEventProduct.objects.filter(
        name=PACK_NAME,
        opens_with_part__isnull=True,
        anniversary_event__event_type="anniversary",
        anniversary_event__banner_links__part_number=PART,
    ).exclude(anniversary_event__name__in=EXCEPTIONS).update(opens_with_part=PART)


def unset_part_3(apps, schema_editor):
    """Back to blank, but only the rows this migration could have set."""
    AnniversaryEventProduct = apps.get_model("calculatorapi", "AnniversaryEventProduct")
    AnniversaryEventProduct.objects.filter(
        name=PACK_NAME, opens_with_part=PART, anniversary_event__event_type="anniversary",
    ).update(opens_with_part=None)


class Migration(migrations.Migration):

    dependencies = [
        ('calculatorapi', '0131_anniversaryeventproduct_opens_with_part'),
    ]

    operations = [
        migrations.RunPython(set_part_3, unset_part_3),
    ]
