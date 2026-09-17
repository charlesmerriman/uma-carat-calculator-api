import re

from django.db import migrations, models


# A frozen copy of calculatorapi.models.uma.IMAGE_GAME_ID. Inlined rather than
# imported so that a later change to the helper cannot change what this
# migration did when it ran.
IMAGE_GAME_ID = re.compile(r"^(?:\d-)?(\d{6})-")


def backfill_game_ids(apps, schema_editor):
    """
    Fill Uma.game_id from the image filename, which has always started with it.

    Tolerant on purpose: a row whose image has no id, or whose id another row
    already took, is left null and reported. The (Rerun) duplicates that
    `merge_duplicate_umas` folds away would otherwise make this migration fail
    on any database where it has not been run yet, and a failed migration on
    deploy is far worse than a null an editor can fill in.
    """
    del schema_editor  # unused; the signature is RunPython's
    Uma = apps.get_model("calculatorapi", "Uma")

    taken = set()
    skipped = []
    for uma in Uma.objects.order_by("pk"):
        filename = (uma.image.name if uma.image else "").rsplit("/", 1)[-1]
        match = IMAGE_GAME_ID.match(filename)
        if not match:
            skipped.append(f"pk={uma.pk} '{uma.name}': no id in image '{filename}'")
            continue
        game_id = int(match.group(1))
        if game_id in taken:
            skipped.append(
                f"pk={uma.pk} '{uma.name}': id {game_id} already used by another row "
                "(run merge_duplicate_umas)"
            )
            continue
        taken.add(game_id)
        uma.game_id = game_id
        uma.save(update_fields=["game_id"])

    if skipped:
        print(f"\n0059: {len(skipped)} uma(s) left without a game_id:")
        for line in skipped:
            print(f"    {line}")


class Migration(migrations.Migration):
    """
    Uma gets the game's own card id, the join key for everything the skills
    work imports from the game data. Backfilled from the image filename.
    """

    dependencies = [
        ("calculatorapi", "0058_site_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="uma",
            name="game_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Numeric card id from the game data (e.g. 102001). The first six "
                    "digits of the image filename. Editors rarely need to touch this."
                ),
                null=True,
                unique=True,
            ),
        ),
        # Reverse is a no-op: RemoveField above drops the column and its data.
        migrations.RunPython(backfill_game_ids, migrations.RunPython.noop),
    ]
