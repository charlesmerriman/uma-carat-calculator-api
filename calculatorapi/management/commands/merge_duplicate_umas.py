"""
Merges duplicate Uma rows that describe the same outfit into one.

WHAT WENT WRONG: when the banner backfill met a rerun banner ("Seiun Sky (Rerun) +
Narita Brian (Rerun)") it created a second Uma row named "Seiun Sky (Rerun)" instead
of linking the banner to the existing "Seiun Sky". The two rows share the image, so
they are visibly the same card, but everything keyed on the uma (banner links,
oshis, step-up picks, selector targets, CM recommendations) is split between them.

WHY IT MATTERS NOW: the skills work gives Uma a unique `game_id`, backfilled from
the number the image filename starts with. Two rows with one image would trip that
constraint mid-migration. This command runs first so it never does. Production
(checked 2026-09-16 through GET /umas) has no such pairs left, so there it reports
nothing and writes nothing; local databases and the fixtures still hold five.

HOW A DUPLICATE IS RECOGNISED: two Uma rows whose image filenames start with the
same six-digit card id (`102001-Seiun-Sky.png`, optionally behind a `<rarity>-`
prefix as the newer uploads have; `game_id_from_image` on the model). Same id,
same outfit. Once `Uma.game_id` is populated this is the same id, but the
command keeps reading the filename so it works on a database that has not
been backfilled yet, which is exactly when it is needed. The row that KEEPS is the
one whose name does not end in "(Rerun)"; if the group has no such row, or more
than one, it is reported and left alone. Rows without an image are never grouped.

HOW A MERGE WORKS: every row on every model that points at the duplicate is
re-pointed at the kept uma, then the duplicate is deleted. The FKs are discovered
from Uma's reverse relations, so a model added later is merged too rather than
silently cascaded away. Where re-pointing would collide with a row the kept uma
already has (the same banner listing both, the same person picking both as
oshis) the duplicate's row is dropped, since it says nothing the kept row does not.

Idempotent: a second run finds no pairs and does nothing.

Usage:
    python manage.py merge_duplicate_umas --dry-run   # report only
    python manage.py merge_duplicate_umas             # prompts
    python manage.py merge_duplicate_umas --no-input  # scripted

Run against PRODUCTION with --dry-run first, as with every data command.
"""

import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from calculatorapi import public_payload_cache
from calculatorapi.models import (
    ChampionsMeetingUmaRecommendation,
    Uma,
    UmasOnUmaBanner,
)
from calculatorapi.models.uma import game_id_from_image

CONFIRM_PHRASE = "merge"

RERUN_SUFFIX = re.compile(r"\s*\(rerun\)\s*$", re.IGNORECASE)

# Junction models with NO unique constraint covering the uma, so the database
# would happily hold the same banner or meeting twice after a re-point. For
# these the collision check is explicit: the named field, plus the uma, must not
# already exist on the kept row. Every other relation has a real constraint and
# lets IntegrityError decide.
UNCONSTRAINED_JUNCTIONS = {
    UmasOnUmaBanner: "banner_uma",
    ChampionsMeetingUmaRecommendation: "champions_meeting",
}


class Command(BaseCommand):
    help = "Merge duplicate Uma rows (the '(Rerun)' copies) into the original."

    # What `merge_duplicate_support_cards` overrides: the same merge over a
    # different card model. Everything below reads these three, never Uma.
    model = Uma
    noun = "uma"
    unconstrained_junctions = UNCONSTRAINED_JUNCTIONS

    @staticmethod
    def image_game_id(image_name):
        """The card id an image filename starts with, or None."""
        return game_id_from_image(image_name)

    def relations(self):
        """
        Every (model, fk_field_name) that points at the card model, discovered
        at runtime.

        Reverse relations rather than a hand-written list so a model added later
        is merged too. M2M entries are skipped: `BannerUma.umas` goes through
        UmasOnUmaBanner, whose own FK is already in this list.
        """
        return [
            (relation.related_model, relation.field.name)
            for relation in self.model._meta.related_objects  # pylint: disable=protected-access
            if not relation.many_to_many
        ]

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Skip the confirmation prompt (for scripted runs).",
        )

    def handle(self, *args, **options):
        planned, problems = self._plan()
        self._report(planned, problems, options["dry_run"])

        if not planned:
            self.stdout.write(self.style.SUCCESS("\nNothing to merge."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run: nothing written."))
            return

        if not options["no_input"]:
            typed = input(f'\nType "{CONFIRM_PHRASE}" to apply: ')
            if typed.strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        moved, dropped = self._write(planned)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nMerged {len(planned)} {self.noun}(s): re-pointed {moved} row(s), "
                f"dropped {dropped} row(s) the kept {self.noun} already had."
            )
        )

    def _plan(self):
        """
        Group umas by image id and pick the survivor of each group.

        Returns (planned, problems): planned is a list of (kept, duplicate)
        pairs; problems are groups this command will not decide for itself.
        """
        groups = defaultdict(list)
        for uma in self.model.objects.order_by("pk"):
            game_id = self.image_game_id(uma.image.name if uma.image else None)
            if game_id is not None:
                groups[game_id].append(uma)

        planned, problems = [], []
        for game_id, umas in sorted(groups.items()):
            if len(umas) < 2:
                continue

            originals = [uma for uma in umas if not RERUN_SUFFIX.search(uma.name)]
            names = ", ".join(f"'{uma.name}' (pk={uma.pk})" for uma in umas)
            if len(originals) != 1:
                reason = (
                    "none is the original" if not originals
                    else f"{len(originals)} rows lack the (Rerun) suffix"
                )
                problems.append(f"image id {game_id}: {names}; {reason}, left alone")
                continue

            kept = originals[0]
            for duplicate in umas:
                if duplicate.pk != kept.pk:
                    planned.append((kept, duplicate))

        return planned, problems

    def _write(self, planned):
        """
        All of it in one transaction: a half-merged uma would look like two
        different umas that each own part of the history.
        """
        moved = dropped = 0
        with transaction.atomic():
            for kept, duplicate in planned:
                for model, field_name in self.relations():
                    rows = model.objects.filter(**{field_name: duplicate})
                    for row in rows:
                        if self._repoint(row, field_name, kept, model):
                            moved += 1
                        else:
                            row.delete()
                            dropped += 1

                # Nothing may still cascade from this delete. If something
                # does, self.relations() missed a relation, and that is a bug
                # to fix rather than data to lose.
                leftovers = [
                    model.__name__
                    for model, field_name in self.relations()
                    if model.objects.filter(**{field_name: duplicate}).exists()
                ]
                if leftovers:
                    raise RuntimeError(
                        f"rows still point at '{duplicate.name}' (pk={duplicate.pk}) "
                        f"on {', '.join(leftovers)}; aborting, nothing written"
                    )
                duplicate.delete()

        # The merged rows are served by /calculator-data, and bulk writes do not
        # fire the per-row signals that would otherwise drop the cache.
        public_payload_cache.invalidate()
        return moved, dropped

    def _repoint(self, row, field_name, kept, model):
        """
        Point one row at the kept uma. Returns False when the kept uma already
        has an equivalent row, in which case the caller drops this one.
        """
        partner_field = self.unconstrained_junctions.get(model)
        if partner_field is not None:
            partner = getattr(row, partner_field)
            if model.objects.filter(**{partner_field: partner, field_name: kept}).exists():
                return False

        setattr(row, field_name, kept)
        # A savepoint, so a unique-constraint clash (the person already picked
        # the kept uma as an oshi, say) rolls back only this one UPDATE and the
        # surrounding transaction stays usable.
        try:
            with transaction.atomic():
                row.save(update_fields=[field_name])
        except IntegrityError:
            return False
        return True

    def _report(self, planned, problems, dry_run):
        verb = "Would merge" if dry_run else "Merging"
        if planned:
            self.stdout.write(f"\n{verb} {len(planned)} duplicate(s):")
            for kept, duplicate in planned:
                references = sum(
                    model.objects.filter(**{field_name: duplicate}).count()
                    for model, field_name in self.relations()
                )
                self.stdout.write(
                    f"    pk={duplicate.pk:<5} '{duplicate.name}' -> "
                    f"pk={kept.pk} '{kept.name}'  ({references} row(s) point at it)"
                )

        if problems:
            self.stdout.write(self.style.WARNING(f"\nLeft alone: {len(problems)} group(s)"))
            for problem in problems:
                self.stdout.write(f"    {problem}")
            self.stdout.write(
                "Rename the copy so it ends in (Rerun), or fix the image, and re-run."
            )
