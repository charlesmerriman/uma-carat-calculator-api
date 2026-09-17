"""
Points every Uma without a borderless image at its file in the media storage.

The borderless art sits in the storage under `umas_borderless/`, one file per
outfit, each filename starting with the outfit's game id
(`114901-Phalaenopsis.png`); scripts/upload_borderless_umas.py puts it there.
This lists that folder once, reads the id off each filename, and sets
`Uma.image_borderless` on the row with that `game_id`.

A row that already has a borderless image is left alone, so a hand pick in the
admin survives every re-run. Both kinds of leftover are reported, because each
one is something to chase: a file whose id matches no uma (a typo in the
filename, or an outfit the site has no row for yet), and an uma with a game id
and no file (art the artist has not delivered).

Usage:
    python manage.py link_borderless_umas --dry-run
    python manage.py link_borderless_umas
"""

import re

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction

from calculatorapi.models import Uma

ART_FOLDER = "umas_borderless"
# Six digits and then anything that is not a seventh: an uma outfit's game id.
LEADING_GAME_ID = re.compile(r"^(\d{6})(?!\d)")


def files_by_game_id():
    """Map game_id -> storage path for every file in the borderless folder.

    One listing call, not one `exists()` per uma: against the Space that is a
    single paginated request instead of ~270 round trips.
    """
    try:
        _folders, filenames = default_storage.listdir(ART_FOLDER)
    except FileNotFoundError:
        # Local file storage raises when the folder was never created; the
        # Space just returns an empty listing. Same meaning either way.
        return {}
    found = {}
    # Sorted so that if two files ever share an id, which one wins is stable.
    for filename in sorted(filenames):
        match = LEADING_GAME_ID.match(filename)
        if match:
            found.setdefault(int(match.group(1)), f"{ART_FOLDER}/{filename}")
    return found


class Command(BaseCommand):
    help = "Set Uma.image_borderless from game_id for every uma that has none yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        files = files_by_game_id()
        umas = {uma.game_id: uma for uma in Uma.objects.exclude(game_id__isnull=True)}

        planned = [
            (uma, files[game_id])
            for game_id, uma in sorted(umas.items())
            if game_id in files and not uma.image_borderless
        ]
        without_file = [uma for game_id, uma in sorted(umas.items()) if game_id not in files]
        without_uma = [path for game_id, path in sorted(files.items()) if game_id not in umas]

        verb = "Would link" if options["dry_run"] else "Linking"
        self.stdout.write(f"{verb} {len(planned)} uma(s) from {len(files)} file(s) in {ART_FOLDER}/.")
        if without_uma:
            self.stdout.write(self.style.WARNING(
                f"{len(without_uma)} file(s) match no uma: {', '.join(without_uma)}"
            ))
        if without_file:
            self.stdout.write(self.style.WARNING(
                f"{len(without_file)} uma(s) have no borderless file: "
                f"{', '.join(f'{uma.name} ({uma.game_id})' for uma in without_file)}"
            ))

        if options["dry_run"] or not planned:
            return

        with transaction.atomic():
            for uma, path in planned:
                uma.image_borderless = path
                uma.save(update_fields=["image_borderless"])
        self.stdout.write(self.style.SUCCESS(f"Linked {len(planned)} uma(s)."))
