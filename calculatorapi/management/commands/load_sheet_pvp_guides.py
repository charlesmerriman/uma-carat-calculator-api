"""
LOCAL ONLY: fills PvP guides from the community Google Sheet, for testing the
PvP page against real content.

The parser and the writer live in calculatorapi/pvp_sheet.py, shared with the
admin's "Load from the sheet" button (one guide at a time, anywhere). This
command loads EVERY guide the sheet holds, so it refuses any database that is
not SQLite (content_snapshot.require_local_database), the same guard as
pull_prod_content: nothing loads content INTO production unattended
(CLAUDE.md, "Safety & data").

Usage:
    python manage.py load_sheet_pvp_guides --dry-run      # parse and report only
    python manage.py load_sheet_pvp_guides                # create guides for events with none
    python manage.py load_sheet_pvp_guides --replace      # delete and reload sheet events' guides
    python manage.py load_sheet_pvp_guides --from-dir DIR # read <Tab name>.csv files instead of fetching
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from calculatorapi import public_payload_cache, pvp_sheet
from calculatorapi.content_snapshot import require_local_database
from calculatorapi.models import PvpGuide
# Re-exported for the tests, which read the parser through this module.
from calculatorapi.pvp_sheet import (  # noqa: F401  pylint: disable=unused-import
    IMPORT_TAB,
    ProseLinker,
    SheetError,
    SkillMatcher,
    normalise,
    parse_cup_tab,
    parse_import_range,
)


class Command(BaseCommand):
    help = "LOCAL ONLY: fill PvP guides from the community Google Sheet, for testing."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Parse and report; write nothing.")
        parser.add_argument("--replace", action="store_true",
                            help="Delete and reload the guides of events the sheet covers.")
        parser.add_argument("--from-dir",
                            help="Read '<Tab name>.csv files from this folder instead of fetching.")

    def handle(self, *args, **options):
        require_local_database()
        from_dir = options["from_dir"]

        def reader(tab):
            return pvp_sheet.read_tab(tab, from_dir=from_dir)

        try:
            guides = pvp_sheet.parse_all(reader)
        except pvp_sheet.SheetError as exc:
            raise CommandError(str(exc)) from exc

        importer = pvp_sheet.SheetImporter()
        created = skipped = 0
        with transaction.atomic():
            for parsed in guides:
                if self.write(importer, parsed, options["replace"]):
                    created += 1
                else:
                    skipped += 1
            if options["dry_run"]:
                transaction.set_rollback(True)

        public_payload_cache.invalidate()
        self.report(importer, created, skipped, options["dry_run"])

    @staticmethod
    def write(importer, parsed, replace):
        field_name, event = pvp_sheet.event_for(parsed)
        if event is None:
            importer.unmatched["events"].append(f"{parsed.kind.upper()} #{parsed.number}")
            return False
        existing = PvpGuide.objects.filter(**{field_name: event}).first()
        if existing and not replace:
            return False
        if existing:
            existing.delete()
        guide = PvpGuide.objects.create(**{field_name: event}, is_published=True)
        importer.write(
            parsed, guide,
            note=(
                f"Imported from the sheet's '{parsed.source}' tab by load_sheet_pvp_guides "
                f"on {timezone.now():%Y-%m-%d}, for local testing."
            ),
        )
        return True

    def report(self, importer, created, skipped, dry_run):
        verb = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {created} guide(s); left {skipped} alone "
            "(event already has a guide, or no such event here)."
        ))
        self.stdout.write(f"Acceleration picks taken from the sheet's text: {importer.accel_picks}.")
        for label, values in importer.unmatched_report():
            self.stdout.write(self.style.WARNING(f"{label} ({len(values)}): {', '.join(values)}"))
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run: nothing written."))
