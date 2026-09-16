"""
Fills the game-data columns on Uma and SupportCard from the committed snapshot.

THE SOURCE is scripts/data/master_snapshot/*.json, written by
scripts/extract_master_snapshot.py from the global game client's own database.
It is global-only by construction, so a JP-only row in our database is simply
never matched and never touched.

WHAT IT WRITES, and what it leaves alone:

  Uma          title, rarity, running_style, the ten apt_* grades, the five
               base_* stats at the initial star count, the five growth_*
               bonuses, and `is_three_star` (= rarity is 3), which the selector
               pickers read. Matched on `game_id`.
  SupportCard  card_type, character_id, title. Matched on `game_id`.

  Never: name, image, admin_comments, purpose, is_time_limited. Those are the
  editors'. Never creates an Uma or SupportCard either: a card needs art, and a
  row created here would sit in every picker with a broken image. A snapshot id
  with no row is reported so an editor can add it through the admin, image and
  all, and the next run fills it in.

It is an overwrite, not a merge: the game's value wins on every column above,
every run. That is what makes it idempotent and what the admin's "Game data"
fieldset warns about.

SANITY CHECK: the game's character name must appear in our row's name
("Oguri Cap" in "[Get Lots of Hugs for Me] Oguri Cap" or "Oguri Cap (Summer)").
A miss almost always means the row's game_id is wrong (a mislabeled image), so
the row is reported and skipped rather than overwritten with another card's
numbers.

Usage:
    python manage.py import_game_data --dry-run    # report only
    python manage.py import_game_data              # prompts
    python manage.py import_game_data --no-input   # scripted
    python manage.py import_game_data --snapshot path/to/dir

Run against PRODUCTION with --dry-run first, read it, then --apply. The rows it
touches are served by /calculator-data, so it drops that cache when done.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from calculatorapi import public_payload_cache
from calculatorapi.models import Rarity, SupportCard, Uma

CONFIRM_PHRASE = "import"

DEFAULT_SNAPSHOT = Path(__file__).resolve().parents[3] / "scripts" / "data" / "master_snapshot"

# snapshot key -> Uma column, for the blocks that copy straight across.
APTITUDE_COLUMNS = {
    "turf": "apt_turf", "dirt": "apt_dirt",
    "short": "apt_short", "mile": "apt_mile", "medium": "apt_medium", "long": "apt_long",
    "front": "apt_front", "pace": "apt_pace", "late": "apt_late", "end": "apt_end",
}
STAT_NAMES = ("speed", "stamina", "power", "guts", "wit")
RUNNING_STYLES = {"front": 1, "pace": 2, "late": 3, "end": 4}


def load_snapshot(directory):
    """The snapshot files this command reads, as parsed JSON."""
    directory = Path(directory)
    if not directory.is_dir():
        raise CommandError(f"snapshot directory not found: {directory}")
    files = {}
    for name in ("cards", "support_cards"):
        path = directory / f"{name}.json"
        if not path.is_file():
            raise CommandError(f"snapshot file missing: {path}")
        files[name] = json.loads(path.read_text(encoding="utf-8"))
    return files


def uma_values(card):
    """The Uma column values one snapshot outfit implies."""
    initial = next(
        (star for star in card["stars"] if star["star"] == card["default_rarity"]), None,
    )
    if initial is None:
        raise CommandError(
            f"card {card['id']} has no per-star row for its initial rarity "
            f"{card['default_rarity']}; the snapshot is inconsistent"
        )
    values = {
        "title": card["title"],
        "rarity": card["default_rarity"],
        "running_style": RUNNING_STYLES[card["running_style"]],
        "is_three_star": card["default_rarity"] == Rarity.THREE,
    }
    for key, column in APTITUDE_COLUMNS.items():
        values[column] = initial["aptitude"][key]
    for stat in STAT_NAMES:
        values[f"base_{stat}"] = initial["stats"][stat]
        values[f"growth_{stat}"] = card["growth"][stat]
    return values


def support_values(card):
    """The SupportCard column values one snapshot support card implies."""
    return {
        "title": card["title"],
        "card_type": card["card_type"],
        "character_id": card["chara_id"],
    }


def _letters(text):
    """Lowercase letters and digits only, so "TM Opera O" and "T.M. Opera O" agree."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def name_matches(row_name, chara_name):
    """Our row names always contain the character's name; a miss is a wrong game_id."""
    return _letters(chara_name) in _letters(row_name)


class Command(BaseCommand):
    help = "Fill Uma and SupportCard game-data columns from the committed master snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--snapshot", default=DEFAULT_SNAPSHOT,
            help="Directory holding the snapshot JSON (default: scripts/data/master_snapshot).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--no-input", action="store_true",
            help="Skip the confirmation prompt (for scripted runs).",
        )

    def handle(self, *args, **options):
        snapshot = load_snapshot(options["snapshot"])

        plans = [
            self._plan("uma", Uma, snapshot["cards"], uma_values),
            self._plan("support card", SupportCard, snapshot["support_cards"], support_values),
        ]
        self._report(plans, options["dry_run"])

        if not any(plan["changed"] for plan in plans):
            self.stdout.write(self.style.SUCCESS("\nNothing to write."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run: nothing written."))
            return

        if not options["no_input"]:
            typed = input(f'\nType "{CONFIRM_PHRASE}" to apply: ')
            if typed.strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        written = self._write(plans)
        self.stdout.write(self.style.SUCCESS(f"\nUpdated {written} row(s)."))

    @staticmethod
    def _plan(label, model, cards, values_for):
        """
        Match every snapshot card to a row and work out what would change.

        Rows are matched on game_id in one query; the loop then only touches
        Python objects, so a dry run is as cheap as it looks.
        """
        by_game_id = {row.game_id: row for row in model.objects.exclude(game_id__isnull=True)}
        changed, unchanged, missing, mismatched = [], 0, [], []

        for card in cards:
            row = by_game_id.get(card["id"])
            if row is None:
                missing.append(f"{card['id']}  {card['name']}")
                continue
            if not name_matches(row.name, card["chara_name"]):
                mismatched.append(
                    f"pk={row.pk} '{row.name}' has game_id {card['id']}, which the game "
                    f"says is '{card['name']}'; check the id"
                )
                continue
            values = values_for(card)
            diff = {
                column: value for column, value in values.items()
                if getattr(row, column) != value
            }
            if diff:
                changed.append((row, diff))
            else:
                unchanged += 1

        return {
            "label": label, "changed": changed, "unchanged": unchanged,
            "missing": missing, "mismatched": mismatched,
        }

    @staticmethod
    def _write(plans):
        """
        Every row in one transaction, so a crash halfway leaves the columns as
        they were rather than half of one import and half of the last.
        """
        written = 0
        with transaction.atomic():
            for plan in plans:
                for row, diff in plan["changed"]:
                    for column, value in diff.items():
                        setattr(row, column, value)
                    row.save(update_fields=list(diff))
                    written += 1
        # Per-row saves already fire the invalidation signal, but the invariant
        # is that a bulk writer drops the cache itself, so this does too.
        public_payload_cache.invalidate()
        return written

    def _report(self, plans, dry_run):
        verb = "Would update" if dry_run else "Updating"
        for plan in plans:
            label = plan["label"]
            self.stdout.write(
                f"\n{label}s: {verb.lower()} {len(plan['changed'])}, "
                f"already current {plan['unchanged']}, "
                f"not in database {len(plan['missing'])}, "
                f"name mismatch {len(plan['mismatched'])}"
            )
            for row, diff in plan["changed"]:
                columns = ", ".join(sorted(diff))
                self.stdout.write(f"    pk={row.pk:<5} {row.name[:40]:<40} {columns}")
            if plan["missing"]:
                self.stdout.write(self.style.WARNING(
                    f"  Not in database ({len(plan['missing'])}); add through the admin, "
                    "with an image, and re-run:"
                ))
                for line in plan["missing"]:
                    self.stdout.write(f"    {line}")
            if plan["mismatched"]:
                self.stdout.write(self.style.WARNING(
                    f"  Name mismatch ({len(plan['mismatched'])}); left alone:"
                ))
                for line in plan["mismatched"]:
                    self.stdout.write(f"    {line}")
