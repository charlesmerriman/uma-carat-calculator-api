"""Share links for plans: the five Plan columns behind "copy this plan".

Its own migration, never an edit to 0066_plan. Django records each migration
by NAME as applied, and production ran 0066 on 2026-09-17, so columns added to
0066 after that day would never be created there and every plan read would
fail. A fresh test database runs the edited file and passes, which is why that
mistake is invisible to the suite and to `makemigrations --check`.

public_id is added in three steps because a unique column cannot be added to
a table that already has rows with a single default: every existing plan
would get the same value from one call. So: add it nullable, give each row its
own id, then make it unique and required.
"""

import secrets
import string

from django.db import migrations, models

import calculatorapi.public_ids

# A frozen copy of calculatorapi/public_ids.py on the day this ran. A migration
# must keep meaning what it meant, even if the live generator changes later.
_ALPHABET = string.ascii_letters + string.digits
_LENGTH = 8


def _new_id(taken):
    """A fresh id not in `taken`. The column is all NULL while this runs, so
    the set of ids handed out so far is the whole collision check."""
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        if candidate not in taken:
            return candidate


def backfill_public_ids(apps, schema_editor):
    """One unique id per existing plan."""
    Plan = apps.get_model("calculatorapi", "Plan")
    taken = set()
    for plan in Plan.objects.filter(public_id__isnull=True).iterator():
        plan.public_id = _new_id(taken)
        taken.add(plan.public_id)
        # update_fields, so updated_at is not bumped on every plan in the
        # table: the landing card shows it as "Updated", and a deploy is not
        # an update to anybody's plan.
        plan.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ('calculatorapi', '0082_visit_landing_and_referrer'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='public_id',
            field=models.CharField(editable=False, max_length=8, null=True),
        ),
        # Reverse is a noop: unapplying this migration drops the column anyway.
        migrations.RunPython(backfill_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='plan',
            name='public_id',
            field=models.CharField(
                default=calculatorapi.public_ids.generate_public_id,
                editable=False,
                max_length=8,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='plan',
            name='is_shared',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='plan',
            name='share_stats',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='plan',
            name='share_notes',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='plan',
            name='copy_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
