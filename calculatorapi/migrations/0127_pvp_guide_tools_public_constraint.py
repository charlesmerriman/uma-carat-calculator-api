"""
The tools_public check (guide-visibility-plan.md): My team and the Gallery
open to everyone only on a Public guide.

Its own migration, so its own transaction, on purpose. 0126 updates the guide
rows (CM 21's twice), and a row written twice in one transaction leaves
PostgreSQL's deferred foreign-key checks pending until that transaction
commits; an ALTER TABLE on the same table before then fails with "cannot ALTER
TABLE ... because it has pending trigger events". The 2026-11-02 deploy failed
on exactly that when this constraint sat at the end of 0126 (SQLite, which the
tests use, has no such rule). Django's docs give the same advice: keep data
changes and schema changes on one table in separate migrations on PostgreSQL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('calculatorapi', '0126_pvp_guide_visibility'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='pvpguide',
            constraint=models.CheckConstraint(condition=models.Q(('tools_public', False), ('visibility', 'public'), _connector='OR'), name='tools_public_only_when_public', violation_error_message='My team and the Gallery can only be opened on a Public guide.'),
        ),
    ]
