"""Give every account that already has planned banners a "Main plan" to hold them.

0066 added Plan and a NULLABLE UserPlannedBanner.plan. This fills it: one
active plan per user who has any planned row, with all of that user's rows
pointed at it. Nothing the user sees changes -- the plan they had is now a
named one.

Users with NO planned rows are left alone on purpose. plans.get_active_plan()
creates their first plan the next time they load the calculator, which is the
same path a brand-new sign-up takes, so there is one way a first plan comes to
exist rather than two.

Safe to run twice: a user who already owns a plan is skipped, and only rows
whose plan is still NULL are ever touched.
"""

from django.db import migrations

# A copy, not an import of models.plan.DEFAULT_PLAN_NAME: this migration must
# keep doing what it did on the day it ran even if that constant changes.
MAIN_PLAN_NAME = "Main plan"


def backfill_main_plans(apps, schema_editor):
    # pylint: disable=unused-argument
    Plan = apps.get_model("calculatorapi", "Plan")
    UserPlannedBanner = apps.get_model("calculatorapi", "UserPlannedBanner")

    # Every user holding at least one row that no plan owns yet.
    user_ids = set(
        UserPlannedBanner.objects.filter(plan__isnull=True)
        .values_list("user_id", flat=True)
    )
    if not user_ids:
        return

    # A user who somehow has a plan already (a rerun, or rows written by new
    # code) keeps it; their stray rows join their ACTIVE plan below instead of
    # getting a second "Main plan".
    existing = dict(
        Plan.objects.filter(user_id__in=user_ids, is_active=True)
        .values_list("user_id", "id")
    )
    Plan.objects.bulk_create(
        [
            Plan(user_id=user_id, name=MAIN_PLAN_NAME, is_active=True)
            for user_id in sorted(user_ids - set(existing))
        ]
    )
    # Re-read rather than trusting bulk_create to hand back primary keys, which
    # it does on PostgreSQL but not on every backend.
    plan_by_user = dict(
        Plan.objects.filter(user_id__in=user_ids, is_active=True)
        .values_list("user_id", "id")
    )

    # One UPDATE per user. UserPlannedBanner is user-owned data and is not part
    # of the /calculator-data public cache, so bypassing post_save with
    # update() needs no public_payload_cache.invalidate().
    for user_id, plan_id in plan_by_user.items():
        UserPlannedBanner.objects.filter(
            user_id=user_id, plan__isnull=True
        ).update(plan_id=plan_id)


def remove_plans(apps, schema_editor):
    # pylint: disable=unused-argument
    Plan = apps.get_model("calculatorapi", "Plan")
    UserPlannedBanner = apps.get_model("calculatorapi", "UserPlannedBanner")

    # ORDER MATTERS. The FK is CASCADE and Django enforces that in Python, so
    # deleting a plan first would take every planned banner down with it.
    # Detach the rows, THEN delete the plans.
    UserPlannedBanner.objects.update(plan=None)
    Plan.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("calculatorapi", "0066_plan"),
    ]

    operations = [
        migrations.RunPython(backfill_main_plans, remove_plans),
    ]
