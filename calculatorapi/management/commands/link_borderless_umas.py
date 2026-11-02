"""
The older name for `link_borderless_art --kind umas`, kept because the docs,
the notes and the habit all say it. Everything lives in link_borderless_art.py.

Usage:
    python manage.py link_borderless_umas --dry-run
    python manage.py link_borderless_umas
"""

from django.core.management.base import BaseCommand

from calculatorapi.management.commands.link_borderless_art import link


class Command(BaseCommand):
    help = "Set Uma.image_borderless from game_id for every uma that has none yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        link("umas", options["dry_run"], self.stdout, self.style)
