"""PvpRun.team: the My team team an unraced ticket follows (models/pvp_run.py,
notes/runs-follow-team-plan.md). Schema only; 0132 fills it in, in a migration
of its own (rules: never update rows and ALTER one table in one migration)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('calculatorapi', '0130_race_course'),
    ]

    operations = [
        migrations.AddField(
            model_name='pvprun',
            name='team',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='runs', to='calculatorapi.pvpteam',
            ),
        ),
    ]
