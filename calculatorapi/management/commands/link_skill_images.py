"""
Points every Skill without an image at its icon file in the media storage.

Skill icons are generic: 63 files cover 718 skills, keyed by `Skill.icon_id`.
Once the files sit in the storage under `skills/<icon_id>.png` (see
scripts/fetch_skill_icons.py), this sets `Skill.image` to that path for every
row whose image is empty. A row an editor already pointed somewhere is left
alone, so a hand-picked icon survives every re-run.

Each path is checked against the storage before it is written: a skill whose
icon file is missing is reported, not linked to a broken URL.

Usage:
    python manage.py link_skill_images --dry-run
    python manage.py link_skill_images
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction

from calculatorapi.models import Skill

ICON_FOLDER = "skills"


def icon_path(icon_id):
    return f"{ICON_FOLDER}/{icon_id}.png"


class Command(BaseCommand):
    help = "Set Skill.image from icon_id for every skill that has no image yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        unlinked = Skill.objects.filter(image="") | Skill.objects.filter(image__isnull=True)
        unlinked = unlinked.order_by("icon_id", "game_id")

        # One storage round trip per icon, not per skill: 63 checks, not 718.
        present = {}
        planned, missing = [], []
        for skill in unlinked:
            path = icon_path(skill.icon_id)
            if skill.icon_id not in present:
                present[skill.icon_id] = default_storage.exists(path)
            (planned if present[skill.icon_id] else missing).append(skill)

        verb = "Would link" if options["dry_run"] else "Linking"
        self.stdout.write(f"{verb} {len(planned)} skill(s) across {sum(present.values())} icon(s).")
        if missing:
            absent = sorted({skill.icon_id for skill in missing})
            self.stdout.write(self.style.WARNING(
                f"{len(missing)} skill(s) left alone: {len(absent)} icon file(s) not in "
                f"storage: {', '.join(icon_path(icon_id) for icon_id in absent)}"
            ))

        if options["dry_run"] or not planned:
            return

        with transaction.atomic():
            for skill in planned:
                skill.image = icon_path(skill.icon_id)
                skill.save(update_fields=["image"])
        self.stdout.write(self.style.SUCCESS(f"Linked {len(planned)} skill(s)."))
