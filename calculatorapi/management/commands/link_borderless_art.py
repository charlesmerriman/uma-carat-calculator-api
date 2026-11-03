"""
Points every uma or support card without a borderless image at its file in the
media storage.

The borderless art sits in the storage one folder per kind, one file per row,
each filename starting with the row's game id:

    umas   umas_borderless/114901-Phalaenopsis.png            six-digit outfit id
    cards  support_cards_borderless/30024-Oguri-Cap-pwr.webp  five-digit card id

scripts/upload_borderless_umas.py and scripts/upload_borderless_cards.py put
them there. This lists the kind's folder once, reads the id off each filename,
and sets `image_borderless` on the row with that `game_id`.

A row that already has a borderless image is left alone, so a hand pick in the
admin survives every re-run. Both kinds of leftover are reported, because each
one is something to chase: a file whose id matches no row (a typo in the
filename, or an outfit or card the site has no row for yet), and a row with a
game id and no file (art not uploaded yet).

Cards run on every deploy (the run command in .do/app.yaml), so a new card's
art links itself once its file is in the Space. Placeholder cards ("your best
SSR Speed card") have a made-up id and never have a file, so they are skipped.

Usage:
    python manage.py link_borderless_art --kind cards --dry-run
    python manage.py link_borderless_art --kind cards
    python manage.py link_borderless_umas    # the older name, umas only
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction

from calculatorapi import pvp_catalogue_cache
from calculatorapi.models import SupportCard, Uma

# A deploy log should stay readable while a whole set waits for its upload.
REPORT_LIMIT = 20


@dataclass(frozen=True)
class Kind:
    noun: str
    folder: str
    # The id's digits and then anything that is not one more digit, so a
    # five-digit card file never matches the six-digit uma pattern or back.
    leading_game_id: re.Pattern
    # The rows that should have a file: every one with a game id.
    rows: Callable


KINDS = {
    "umas": Kind(
        "uma", "umas_borderless", re.compile(r"^(\d{6})(?!\d)"),
        lambda: Uma.objects.exclude(game_id__isnull=True),
    ),
    "cards": Kind(
        "card", "support_cards_borderless", re.compile(r"^(\d{5})(?!\d)"),
        lambda: SupportCard.objects.exclude(game_id__isnull=True).exclude(is_placeholder=True),
    ),
}


def files_by_game_id(kind):
    """Map game_id -> storage path for every file in the kind's folder.

    One listing call, not one `exists()` per row: against the Space that is a
    single paginated request instead of hundreds of round trips.
    """
    try:
        _folders, filenames = default_storage.listdir(kind.folder)
    except FileNotFoundError:
        # Local file storage raises when the folder was never created; the
        # Space just returns an empty listing. Same meaning either way.
        return {}
    found = {}
    # Sorted so that if two files ever share an id, which one wins is stable.
    for filename in sorted(filenames):
        match = kind.leading_game_id.match(filename)
        if match:
            found.setdefault(int(match.group(1)), f"{kind.folder}/{filename}")
    return found


def _listed(items):
    """A comma list, cut short past REPORT_LIMIT with a count of the rest."""
    shown = ", ".join(items[:REPORT_LIMIT])
    rest = len(items) - REPORT_LIMIT
    return f"{shown} and {rest} more" if rest > 0 else shown


def link(kind_name, dry_run, stdout, style):
    """The whole run for one kind. Shared with the link_borderless_umas alias."""
    kind = KINDS[kind_name]
    try:
        files = files_by_game_id(kind)
    except Exception as error:  # pylint: disable=broad-except
        # This runs in the deploy's run command, before gunicorn: a Space that
        # can't be listed right now skips the link, never the deploy (the rule
        # snapshot_analytics follows). The next deploy links whatever waited.
        stdout.write(style.WARNING(f"Link skipped, {kind.folder}/ could not be listed: {error!r}"))
        return
    rows = {row.game_id: row for row in kind.rows()}

    planned = [
        (row, files[game_id])
        for game_id, row in sorted(rows.items())
        if game_id in files and not row.image_borderless
    ]
    without_file = [row for game_id, row in sorted(rows.items()) if game_id not in files]
    without_row = [path for game_id, path in sorted(files.items()) if game_id not in rows]

    verb = "Would link" if dry_run else "Linking"
    stdout.write(f"{verb} {len(planned)} {kind.noun}(s) from {len(files)} file(s) in {kind.folder}/.")
    if without_row:
        stdout.write(style.WARNING(
            f"{len(without_row)} file(s) match no {kind.noun}: {_listed(without_row)}"
        ))
    if without_file:
        stdout.write(style.WARNING(
            f"{len(without_file)} {kind.noun}(s) have no borderless file: "
            f"{_listed([f'{row.name} ({row.game_id})' for row in without_file])}"
        ))

    if dry_run or not planned:
        return

    with transaction.atomic():
        for row, path in planned:
            row.image_borderless = path
            row.save(update_fields=["image_borderless"])
    # The saves above fire the signals; a writer still drops the catalogue it
    # feeds itself, so a later switch to bulk_update cannot go stale (the rule
    # link_skill_images follows).
    pvp_catalogue_cache.invalidate()
    stdout.write(style.SUCCESS(f"Linked {len(planned)} {kind.noun}(s)."))


class Command(BaseCommand):
    help = "Set image_borderless from game_id for every uma or support card that has none yet."

    def add_arguments(self, parser):
        parser.add_argument("--kind", choices=sorted(KINDS), required=True)
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        link(options["kind"], options["dry_run"], self.stdout, self.style)
