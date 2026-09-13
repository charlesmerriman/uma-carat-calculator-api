"""
Strips personal data from non-staff accounts.

Ordinary accounts now sign in through Google/Discord and store nothing but an
opaque provider id (see models/social_account.py). This command retires the
personal data collected by the old password-based sign-up, so the promise holds
for rows that already exist rather than only for new ones.

For every user with is_staff=False it:
  - blanks email / first_name / last_name
  - blanks display_name, the name they chose on their account page. A chosen
    name is personal data in a way the generated handle is not. The oshis they
    picked (models/user_oshi.py) are the site's own art and are left alone.
  - replaces the password hash with Django's unusable-password marker
  - deletes their API token, so any key still sitting in a browser's
    localStorage stops working immediately

Staff accounts are untouched — they still sign in with a password to reach
/admin and the analytics dashboard.

SUPPORTER LINKS ARE ALWAYS CLEARED, flag or no flag. This command's job is to
make an account unreachable, and a `PatreonSupporter.linked_user` pointing at a
purged account would outlive it — granting entitlement to a login nobody can
perform, and keeping a pointer at a person the purge was meant to disconnect.
The supporter ROW survives untouched, exactly as it does when someone unlinks
by hand; only the link to the account goes.

PATREON SUPPORTERS ARE NOT ACCOUNTS, and their DATA is left alone unless you
pass --include-patreon. PatreonSupporter.email is the admin's only way to tell two
supporters apart, so wiping it is a deliberate act, not a side effect of the
legacy account purge this command exists for. The flag is here so there IS a
lever — a takedown request, or decommissioning the Patreon integration — rather
than because a routine run should pull it. It blanks the email and nothing else:
the rows, the tiers and the publication decisions survive.

IRREVERSIBLE. There is no backup of the blanked values, and afterwards those
accounts cannot be logged into at all (their plans stay in the database but
become unreachable). This is the accepted consequence of the migration; see
the project plan.

Usage:
    python manage.py purge_user_pii --dry-run   # report only, changes nothing
    python manage.py purge_user_pii             # prompts for confirmation
    python manage.py purge_user_pii --no-input  # for scripted/CI runs
    python manage.py purge_user_pii --include-patreon   # also blank supporter emails

Run it ONCE in production (DigitalOcean API component's Console tab) after the
social-login deploy has gone out and been verified.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from rest_framework.authtoken.models import Token

from calculatorapi.models import CustomUser, PatreonSupporter

CONFIRM_PHRASE = "purge"

# Blanked rather than nulled: AbstractUser declares the first three as non-null
# CharFields with blank=True, and display_name follows the same shape, so ""
# is the correct empty value for all four.
PII_FIELDS = ["email", "first_name", "last_name", "display_name"]


class Command(BaseCommand):
    help = "Blank email/name/password on all non-staff accounts (irreversible)."

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
            "--include-patreon",
            action="store_true",
            help=(
                "Also blank the email on every Patreon supporter. Off by default: "
                "that field is the admin's only way to tell supporters apart."
            ),
        )

    def handle(self, *args, **options):
        targets = CustomUser.objects.filter(is_staff=False)
        total = targets.count()

        include_patreon = options["include_patreon"]
        linked_supporters = PatreonSupporter.objects.filter(
            linked_user__in=targets
        ).count()
        # Counted whether or not the flag is set, so a dry run always shows what
        # the flag WOULD reach.
        patreon_with_email = PatreonSupporter.objects.exclude(email="").count()

        if not total and not (include_patreon and patreon_with_email):
            self.stdout.write(self.style.SUCCESS("No non-staff accounts found; nothing to do."))
            return

        # Counted before the write so the summary can show what was actually
        # holding data, not just how many rows were touched.
        with_pii = targets.exclude(email="", first_name="", last_name="").count()
        with_password = sum(1 for user in targets.only("password") if user.has_usable_password())
        token_count = Token.objects.filter(user__in=targets).count()

        self.stdout.write(f"Non-staff accounts:        {total}")
        self.stdout.write(f"  holding email/name:      {with_pii}")
        self.stdout.write(
            f"  holding a display name:  {targets.exclude(display_name='').count()}"
        )
        self.stdout.write(f"  holding a usable password: {with_password}")
        self.stdout.write(f"  API tokens to delete:    {token_count}")
        self.stdout.write(f"  Patreon links to clear:  {linked_supporters}")
        self.stdout.write(
            self.style.WARNING(
                f"Staff accounts left untouched: {CustomUser.objects.filter(is_staff=True).count()}"
            )
        )
        if include_patreon:
            self.stdout.write(
                f"Patreon supporter emails to blank: {patreon_with_email}"
            )
        elif patreon_with_email:
            self.stdout.write(
                f"Patreon supporters holding an email: {patreon_with_email} "
                "(left alone — pass --include-patreon to blank them too)"
            )

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("\nDry run — no changes written."))
            return

        if not options["no_input"]:
            self.stdout.write(self.style.ERROR(
                "\nThis cannot be undone. Affected accounts will no longer be able to sign in."
            ))
            answer = input(f'Type "{CONFIRM_PHRASE}" to continue: ')
            if answer.strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.WARNING("Aborted; nothing was changed."))
                return

        # One transaction so a failure part-way through cannot leave some rows
        # scrubbed and others still holding data.
        with transaction.atomic():
            # Tokens go first: if the run fails after this point the worst case
            # is that people are signed out, not that stale keys survive a
            # partial scrub.
            Token.objects.filter(user__in=targets).delete()

            users = list(targets)
            for user in users:
                user.email = ""
                user.first_name = ""
                user.last_name = ""
                user.display_name = ""
                # Writes the "!" marker; check_password() then rejects every
                # input, including "!" itself.
                user.set_unusable_password()

            CustomUser.objects.bulk_update(users, PII_FIELDS + ["password"])

            # The provider rows stay untouched: they are the (provider,
            # subject_id) pairs that make a returning sign-in resolve to this
            # account, and the pair identifies nobody without the provider's
            # own database.

            # Always, not behind --include-patreon: this severs a link, it does
            # not touch supporter data. Leaving it would keep entitlement alive
            # on an account that can no longer be signed into.
            PatreonSupporter.objects.filter(linked_user__in=targets).update(
                linked_user=None
            )

            if include_patreon:
                # Blanked, not deleted: the supporter still needs thanking, and
                # their publication decision must survive.
                PatreonSupporter.objects.exclude(email="").update(email="")

        self.stdout.write(self.style.SUCCESS(
            f"\nPurged {len(users)} account(s) and deleted {token_count} token(s)."
        ))
        if linked_supporters:
            self.stdout.write(self.style.SUCCESS(
                f"Cleared {linked_supporters} Patreon supporter link(s). "
                "The supporter rows themselves are unchanged."
            ))
        if include_patreon:
            self.stdout.write(self.style.SUCCESS(
                f"Blanked the email on {patreon_with_email} Patreon supporter(s)."
            ))
