"""
Attaches the missing featured umas to the JP launch banner (2021-02-24).

The launch banner exists in production as a bare BannerTimeline row: it has a
name listing nine units, but no BannerUma, no BannerSupport and no image. Since
the featured-card links are what put a unit into the selector pool, the nine
BASE launch units are currently unreachable there — only their costume variants
(Special Week (Summer), Tokai Teio (Anime), ...) can be picked. An uma selector
ticket cannot select base Special Week, Tokai Teio, Oguri Cap and so on at all.

This is a LINKING job, not a creation job. All nine Uma records already exist;
they are simply not attached to any banner. The command creates one BannerUma
against the launch timeline and links the nine through UmasOnUmaBanner.

Two deliberate choices:

  - The timeline is found by its JP start date, not by primary key. The row is
    present in production and absent from local databases, so its id is not
    portable.

  - The unit list is parsed from the timeline's own `name`, which is the sheet's
    " + "-joined concatenation. Hardcoding nine names here would let this drift
    from the row it repairs.

Support cards were originally skipped here, on the grounds that the source
Google Sheet's Timeline tab does not reach back to 2021-02-24 so the launch
banner's support side was unknowable. That was the wrong place to look:
timeline_master.csv — the file support_backfill.py already reads, tracked in
git and deployed alongside the app — carries all twenty of them for this row.
They are linked here now.

Idempotent, and the two halves are independent: whichever of the uma and
support banners is missing gets created, the other is reported and left alone.
That matters because the uma half was applied to production before the support
half existed, so the second run must complete the row rather than see a uma
banner and exit.

Usage:
    python manage.py repair_launch_banner --dry-run   # report only
    python manage.py repair_launch_banner             # prompts
    python manage.py repair_launch_banner --no-input  # scripted

Run against PRODUCTION from the DigitalOcean API component's Console tab, and
--dry-run there first — local databases do not contain this row.
"""

from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from calculatorapi.models import (
    BannerSupport,
    BannerTimeline,
    BannerUma,
    SupportsOnSupportBanner,
    Uma,
    UmasOnUmaBanner,
)
from calculatorapi.support_backfill import (
    read_timeline_master,
    resolve_support_cards,
    split_names,
)

CONFIRM_PHRASE = "repair"

LAUNCH_JP_DATE = date(2021, 2, 24)

# Same reasoning as the race-prep batches: twenty " + "-joined names is not a
# usable label in the planner's banner dropdown.
LAUNCH_SUPPORT_NAME = "Launch Support"


class Command(BaseCommand):
    help = "Link the nine featured umas to the JP launch banner (2021-02-24)."

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
        timeline = BannerTimeline.objects.filter(
            jp_start_date__date=LAUNCH_JP_DATE
        ).first()

        if timeline is None:
            self.stdout.write(
                self.style.ERROR(
                    f"No banner timeline starts {LAUNCH_JP_DATE}. Nothing to repair. "
                    "(Expected on a local database — this row exists in production only.)"
                )
            )
            return

        self.stdout.write(f"Found timeline id={timeline.pk}: {timeline.name}\n")

        if timeline.uma_banners.exists() and timeline.support_banners.exists():
            self.stdout.write(
                self.style.SUCCESS(
                    "It already has both a uma and a support banner — nothing to do."
                )
            )
            return

        umas = self._collect_umas(timeline)
        supports = self._collect_supports(timeline)

        if not umas and not supports:
            self.stdout.write(self.style.ERROR("\nNothing to link."))
            return

        self.stdout.write(
            f"\nWould link {len(umas)} uma(s) and {len(supports)} support card(s) "
            f"on timeline {timeline.pk}."
        )

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run — nothing written."))
            return

        if not options["no_input"]:
            typed = input(f'\nType "{CONFIRM_PHRASE}" to apply: ')
            if typed.strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        self._write(timeline, umas, supports)

    def _collect_umas(self, timeline):
        """The featured umas, parsed from the timeline's own " + "-joined name."""
        if timeline.uma_banners.exists():
            self.stdout.write("Uma banner already attached — leaving it alone.")
            return []

        wanted = [part.strip() for part in timeline.name.split(" + ") if part.strip()]
        self.stdout.write(f"Parsed {len(wanted)} unit name(s) from the row name.")

        found, missing = [], []
        for name in wanted:
            uma = Uma.objects.filter(name__iexact=name).first()
            (found if uma else missing).append(uma or name)

        self.stdout.write(f"\nMatched {len(found)} Uma record(s):")
        for uma in found:
            self.stdout.write(f"    id={uma.pk:<5} {uma.name}")

        if missing:
            # Reported rather than created: an unmatched name usually means a
            # spelling drift between the row name and the Uma record, and
            # creating a duplicate would make that worse.
            self.stdout.write(
                self.style.WARNING(f"\nNo Uma record for {len(missing)} name(s):")
            )
            for name in missing:
                self.stdout.write(f"    {name}")
            self.stdout.write(
                "Fix these in admin first — the command will not create Uma rows."
            )
        return found

    def _collect_supports(self, timeline):
        """
        The featured support cards, from the pipeline's master CSV.

        Unlike a race-prep batch this links whatever resolves rather than
        skipping the row wholesale: the launch banner is a one-off, and
        nineteen of twenty cards is materially better than none.
        """
        if timeline.support_banners.exists():
            self.stdout.write("\nSupport banner already attached — leaving it alone.")
            return []

        rows = [
            row
            for row in read_timeline_master()
            if row["JP Start Date"] == LAUNCH_JP_DATE.isoformat()
        ]
        if not rows:
            self.stdout.write(
                self.style.WARNING(
                    f"\nNo {LAUNCH_JP_DATE} row in the master CSV — skipping supports."
                )
            )
            return []

        # The JP date matters as much as the names: twenty of these characters
        # have both an R and an SSR card of the same name, and the resolver
        # needs the banner's date to tell which SSR debuted here. The first run
        # of this command predated that rule and linked all twenty R variants
        # into production — `fix_support_card_variants` repairs those rows.
        names = split_names(rows[0]["Banner Support"])
        found, missing = resolve_support_cards(names, LAUNCH_JP_DATE)
        self.stdout.write(f"\nMatched {len(found)} of {len(names)} support card(s).")

        if missing:
            self.stdout.write(
                self.style.WARNING(f"No SupportCard for: {', '.join(missing)}")
            )
            self.stdout.write(
                "The rest are still linked; add these in admin and re-run."
            )
        return found

    def _write(self, timeline, umas, supports):
        """Both halves in one transaction, so the row never lands half-built."""
        with transaction.atomic():
            if umas:
                banner = BannerUma.objects.create(
                    banner_timeline=timeline,
                    name=timeline.name,
                    free_pulls=0,
                )
                UmasOnUmaBanner.objects.bulk_create(
                    [UmasOnUmaBanner(banner_uma=banner, uma=uma) for uma in umas]
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\nCreated uma banner id={banner.pk} with {len(umas)} uma(s)."
                    )
                )

            if supports:
                support_banner = BannerSupport.objects.create(
                    banner_timeline=timeline,
                    name=LAUNCH_SUPPORT_NAME,
                    free_pulls=0,
                )
                SupportsOnSupportBanner.objects.bulk_create(
                    [
                        SupportsOnSupportBanner(
                            banner_support=support_banner, support_card=card
                        )
                        for card in supports
                    ]
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created support banner id={support_banner.pk} with "
                        f"{len(supports)} card(s)."
                    )
                )
