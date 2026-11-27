"""
Sets BannerTimeline.banner_category on rows that can be classified from their
own data.

Only ONE category is safe to derive automatically:

  golden_week_revival — more than 2 featured umas AND zero support cards.

That combination is structural rather than coincidental. The source sheet's
Golden Week block physically overwrites its support columns (AH/AI) with umas,
so a revival row has nowhere to record a support card; the supports for that
window live on the separate, concurrently-running standard banner. No other
category shares the shape.

The other two are deliberately NOT auto-applied:

  race_prep_support — 29 rows, each 1 uma + 10 supports. Set by
    `backfill_race_prep_supports`, not here: the category comes from the master
    CSV's "Banner Type" column, which is authoritative, and that command writes
    it in the same transaction as the cards so the two cannot disagree. Before
    that backfill these rows have no support cards at all, which makes them
    indistinguishable from an ordinary one-uma banner — so there is nothing for
    this command to detect either way.

  rerun — the sheet codes only 2, but our data holds more banners whose name
    carries "(Rerun)" because we track reruns the sheet skipped. Name matching
    would therefore disagree with the sheet on purpose, so these are only
    REPORTED unless you pass --include-reruns.

Idempotent: rows already holding the target category are left alone and
reported as unchanged, so re-running is a no-op.

Usage:
    python manage.py classify_banner_categories --dry-run   # report only
    python manage.py classify_banner_categories             # prompts
    python manage.py classify_banner_categories --no-input  # scripted
    python manage.py classify_banner_categories --include-reruns

Run against PRODUCTION from the DigitalOcean API component's Console tab.
Production content is admin-edited and has diverged from both the fixtures and
any local database, so always --dry-run there first and read the list.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from calculatorapi.models import BannerTimeline, BannerCategory

CONFIRM_PHRASE = "classify"


class Command(BaseCommand):
    help = "Set banner_category on timelines that can be classified from their own data."

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
        parser.add_argument(
            "--include-reruns",
            action="store_true",
            help='Also classify banners whose name contains "(Rerun)".',
        )

    def handle(self, *args, **options):
        # Counting through the join tables rather than the M2M managers keeps
        # this one query instead of one per timeline.
        timelines = BannerTimeline.objects.annotate(
            uma_count=Count("uma_banners__umasonumabanner", distinct=True),
            # SSRs only: SR and R rate-ups ride along on banners of every kind.
            support_count=Count(
                "support_banners__supportsonsupportbanner", distinct=True,
                filter=~Q(support_banners__supportsonsupportbanner__support_card__rarity__in=(1, 2)),
            ),
        )

        revivals = timelines.filter(uma_count__gt=2, support_count=0)
        planned = [(t, BannerCategory.GOLDEN_WEEK_REVIVAL) for t in revivals]

        rerun_matches = timelines.filter(
            Q(name__icontains="(rerun)")
        ).exclude(pk__in=[t.pk for t, _ in planned])

        if options["include_reruns"]:
            planned += [(t, BannerCategory.RERUN) for t in rerun_matches]

        changes = [(t, c) for t, c in planned if t.banner_category != c]
        unchanged = [(t, c) for t, c in planned if t.banner_category == c]

        self.stdout.write(f"Scanned {timelines.count()} banner timelines.\n")

        self._report("Would set" if options["dry_run"] else "Setting", changes)
        if unchanged:
            self.stdout.write(
                self.style.SUCCESS(f"\nAlready correct: {len(unchanged)} row(s).")
            )

        if not options["include_reruns"] and rerun_matches:
            self.stdout.write(
                f'\nRerun candidates (NOT applied — pass --include-reruns): {rerun_matches.count()}'
            )
            for t in rerun_matches:
                self.stdout.write(f"    id={t.pk}  {t.name[:70]}")

        # Surfaces the rows still carrying no support cards. Once
        # backfill_race_prep_supports has run this should be down to the
        # revivals (structurally support-free) plus genuinely unfilled rows.
        empty = timelines.filter(support_count=0, uma_count__lte=2).count()
        self.stdout.write(
            f"\nFYI {empty} timeline(s) have no support cards at all — the "
            "revivals are legitimately among them; the rest are unfilled rows "
            "(see backfill_race_prep_supports)."
        )

        if not changes:
            self.stdout.write(self.style.SUCCESS("\nNothing to change."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run — nothing written."))
            return

        if not options["no_input"]:
            typed = input(f'\nType "{CONFIRM_PHRASE}" to apply: ')
            if typed.strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        with transaction.atomic():
            for timeline, category in changes:
                timeline.banner_category = category
                timeline.save(update_fields=["banner_category"])

        self.stdout.write(self.style.SUCCESS(f"\nUpdated {len(changes)} row(s)."))

    def _report(self, verb, changes):
        if not changes:
            return
        self.stdout.write(f"\n{verb} {len(changes)} row(s):")
        for timeline, category in changes:
            jp = timeline.jp_start_date.date() if timeline.jp_start_date else "no JP date"
            self.stdout.write(
                f"    id={timeline.pk:<5} {jp}  "
                f"{timeline.banner_category} -> {category}   {timeline.name[:52]}"
            )
