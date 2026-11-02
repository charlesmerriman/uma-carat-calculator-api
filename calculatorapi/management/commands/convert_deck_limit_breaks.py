"""
Moves the sheet-era diamond notes into PvpDeckSlot.limit_break, once.

The guides loaded from the community sheet carry a card's recommended limit
break as text on the "or" row under it ("◆◆◆◇ + or spd": this card at 3 LB or
better, or any Speed card). The site now keeps the number on the card's own
row and draws the game's diamonds (notes/support-card-art-plan.md, PRs 3 and
4). On 2026-11-02 prod held 134 such notes in 25 guides, all on note-only "or"
rows.

For each note holding a diamond group (deck_limit_breaks.split_limit_break):

  * the card it describes is the row's own card, else the slot's order-0 card;
  * that row's limit_break is set from the ◆ count, unless it already has one;
  * ONLY the diamond part leaves the note, the rest stays as written (owner,
    2026-11-02), and a note-only row with nothing left is deleted;
  * a note with no card to describe (a slot with no card) is listed for an
    editor and left alone.

Every guide it changes gets one revision (`source=limit_breaks`, no author)
through pvp_guide_revisions.record, the version bump included, so an editor
with the guide open gets the usual "changed elsewhere" refusal and reloads.
Not a data migration: it rewrites notes and deletes rows, which the
fill-blanks exception in CLAUDE.md doesn't cover. Running it twice finds
nothing the second time.

Usage (run on prod with the owner's go, dry run first):
    python manage.py convert_deck_limit_breaks --dry-run
    python manage.py convert_deck_limit_breaks
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from calculatorapi import pvp_guide_revisions
from calculatorapi.deck_limit_breaks import split_limit_break
from calculatorapi.models import PvpDeckSlot, PvpGuideRevisionSource


def plan():
    """What the run would do: ({guide: [(note row, card row, limit break,
    rest of the note)]}, [rows left alone with why]). Reads only."""
    rows = list(
        PvpDeckSlot.objects.filter(Q(note__contains="◆") | Q(note__contains="◇"))
        .select_related("deck__guide", "support_card")
        .order_by("deck__guide_id", "deck_id", "slot", "order")
    )
    # Each slot's card row, for the "or" rows that describe it: one query.
    main_rows = {
        (row.deck_id, row.slot): row
        for row in PvpDeckSlot.objects.filter(
            deck_id__in={row.deck_id for row in rows}, order=0,
        ).exclude(support_card=None).select_related("support_card")
    }
    changes, skipped = defaultdict(list), []
    targeted = set()
    for row in rows:
        limit_break, rest = split_limit_break(row.note)
        if limit_break is None:
            continue  # a stray diamond, not a group of four
        card_row = row if row.support_card_id else main_rows.get((row.deck_id, row.slot))
        if card_row is None:
            skipped.append((row, "no card in this slot"))
        elif card_row.limit_break is not None or card_row.pk in targeted:
            skipped.append((row, f"the card already has {card_row.limit_break} LB"))
        else:
            targeted.add(card_row.pk)
            changes[row.deck.guide].append((row, card_row, limit_break, rest))
    return changes, skipped


def describe(row):
    return f"{row.deck.guide} / {row.deck.label} / slot {row.slot}"


class Command(BaseCommand):
    help = "Move the diamond limit-break notes onto their cards' limit_break, once."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        changes, skipped = plan()
        total = sum(len(rows) for rows in changes.values())
        verb = "Would convert" if options["dry_run"] else "Converting"
        self.stdout.write(f"{verb} {total} note(s) in {len(changes)} guide(s).")
        for guide, rows in changes.items():
            self.stdout.write(f"  {guide}: {len(rows)}")
            for note_row, card_row, limit_break, rest in rows:
                after = f'"{rest}"' if rest else "row removed"
                self.stdout.write(
                    f'    {card_row.support_card.name}: {limit_break} LB  "{note_row.note}" -> {after}'
                )
        for row, why in skipped:
            self.stdout.write(self.style.WARNING(f'Left alone ({why}): {describe(row)} "{row.note}"'))

        if options["dry_run"] or not changes:
            return

        with transaction.atomic():
            for guide, rows in changes.items():
                for note_row, card_row, limit_break, rest in rows:
                    card_row.limit_break = limit_break
                    if note_row.pk == card_row.pk:
                        card_row.note = rest
                        card_row.save(update_fields=["limit_break", "note"])
                        continue
                    card_row.save(update_fields=["limit_break"])
                    if rest:
                        note_row.note = rest
                        note_row.save(update_fields=["note"])
                    else:
                        note_row.delete()
                pvp_guide_revisions.record(guide, None, PvpGuideRevisionSource.LIMIT_BREAKS)
        self.stdout.write(self.style.SUCCESS(f"Converted {total} note(s) in {len(changes)} guide(s)."))
