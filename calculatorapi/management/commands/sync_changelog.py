"""
Writes calculatorapi/data/changelog.yaml into the ChangelogEntry table.

The changelog is the one piece of site content that describes the code, so it
lives in the repo beside it rather than only in the database: an entry is
written in the same pull request as the work it announces, gets reviewed with
it, and ships when it ships. This command is what closes that loop.

Production runs it on every deploy, in the service's run_command next to
`migrate` (see backend/.do/app.yaml). There is no other route to the production
database — the app's database is an App Platform *dev* database with no external
endpoint — so a deploy is how a changelog entry reaches the live site.

Usage:
    python manage.py sync_changelog --dry-run     # report, write nothing
    python manage.py sync_changelog               # write
    python manage.py sync_changelog --strict      # exit 1 if the file is bad

What it touches, and what it does not:

    Entries are matched on ChangelogEntry.key. The file OWNS an entry whose key
    it lists: title, version, date and the whole list of change lines are
    replaced with what is written here, so an admin edit to a managed entry
    survives only until the next deploy.

    Entries with no key were written by hand in the admin. They are never read,
    written or deleted here. Dropping an entry from the file likewise does not
    delete it — it only stops being managed, because a deploy silently deleting
    published content is a worse failure than a stale line.

IT EXITS 0 ON A BAD FILE unless --strict is passed. This runs on the path that
starts the web service: a typo in a patch note must not be able to keep the API
from booting. Problems are reported on stdout and NOTHING is written when the
file does not validate — read the deploy log rather than trusting the exit
status. --strict inverts that for local checks and the test suite, where a
non-zero exit is the entire point.
"""

import datetime
from pathlib import Path

import yaml
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.core.validators import validate_slug
from django.db import transaction

from calculatorapi import changelog_discord, discord_messages
from calculatorapi.models import ChangelogChange, ChangelogEntry

# Default source file, resolved from the app directory so it does not depend on
# the working directory a deploy happens to invoke the command from.
DEFAULT_PATH = Path(settings.BASE_DIR) / "calculatorapi" / "data" / "changelog.yaml"

VALID_CATEGORIES = {choice for choice, _ in ChangelogChange.CATEGORY_CHOICES}

# Mirrors of the model's max_lengths. Checked up front so an over-long line is
# reported as a readable message naming the entry, rather than surfacing as a
# database error partway through a write.
MAX_KEY = 100
MAX_TITLE = 255
MAX_VERSION = 50
MAX_TEXT = 500


class ChangelogFileError(Exception):
    """The file could not be read or did not validate. Carries every problem
    found, not just the first — one round of fixes should be enough."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s) in the changelog file")


def _parse_date(raw):
    """YAML gives a `date` for a bare 2026-09-01 and a `str` for a quoted one.
    Accept both so quoting is a style choice rather than a trap."""
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    if isinstance(raw, str):
        return datetime.date.fromisoformat(raw.strip())
    raise ValueError(f"expected a date, got {type(raw).__name__}")


def _validate_changes(label, raw_changes):
    """Validate one entry's change lines, returning (changes, problems).

    Each line is a one-key mapping whose key is the category: `- added: "..."`.
    The single-pair shape is what keeps the file readable AND preserves display
    order, which a dict of category -> list would not.
    """
    problems = []
    changes = []

    if not isinstance(raw_changes, list):
        return changes, [f"{label}: 'changes' must be a list"]

    for line_no, line in enumerate(raw_changes, start=1):
        where = f"{label}, change {line_no}"
        if not isinstance(line, dict) or len(line) != 1:
            problems.append(
                f'{where}: expected a single "category: text" pair, e.g. - added: "..."'
            )
            continue
        (category, text), = line.items()
        if category not in VALID_CATEGORIES:
            problems.append(
                f"{where}: unknown category '{category}' "
                f"(expected one of {', '.join(sorted(VALID_CATEGORIES))})"
            )
        elif not text or not isinstance(text, str):
            problems.append(f"{where}: text is required and must be a string")
        elif len(text) > MAX_TEXT:
            problems.append(f"{where}: text is over {MAX_TEXT} characters")
        else:
            changes.append((category, text.strip()))

    return changes, problems


def _validate_key(label, key, seen_keys):
    """Validate the entry's identity field, returning its problems."""
    problems = []
    try:
        validate_slug(key)
    except ValidationError:
        problems.append(
            f"{label}: 'key' must be a slug (letters, numbers, hyphens, underscores)"
        )
    if len(key) > MAX_KEY:
        problems.append(f"{label}: 'key' is over {MAX_KEY} characters")
    if key in seen_keys:
        problems.append(f"{label}: duplicate key — keys identify entries and must be unique")
    return problems


def _validate_entry(item, index, seen_keys):
    """Validate one entry, returning (entry_or_None, problems).

    Collects every problem rather than stopping at the first, so one round of
    fixes is enough. Returns None for the entry when it cannot be built at all.
    """
    # Name the entry by key where we can and by position where we cannot, so a
    # problem is findable in the file either way.
    label = f"entry {index + 1}"
    if not isinstance(item, dict):
        return None, [f"{label}: expected a mapping, got {type(item).__name__}"]

    key = item.get("key")
    if isinstance(key, str):
        label = f'entry "{key}"'
    if not key or not isinstance(key, str):
        return None, [f"{label}: 'key' is required and must be a string"]

    problems = _validate_key(label, key, seen_keys)
    if any("duplicate key" in problem for problem in problems):
        return None, problems

    title = item.get("title")
    if not title or not isinstance(title, str):
        problems.append(f"{label}: 'title' is required and must be a string")
        title = ""
    elif len(title) > MAX_TITLE:
        problems.append(f"{label}: 'title' is over {MAX_TITLE} characters")

    # A missing version is the common case (no badge), so absent and empty both
    # mean the same thing and neither is an error.
    version = item.get("version") or ""
    if not isinstance(version, str):
        problems.append(f"{label}: 'version' must be a string")
        version = ""
    elif len(version) > MAX_VERSION:
        problems.append(f"{label}: 'version' is over {MAX_VERSION} characters")

    date = None
    try:
        date = _parse_date(item.get("date"))
    except (ValueError, TypeError) as exc:
        problems.append(f"{label}: 'date' must be an ISO date like 2026-09-01 ({exc})")

    changes, change_problems = _validate_changes(label, item.get("changes") or [])
    problems.extend(change_problems)

    unknown = set(item) - {"key", "title", "version", "date", "changes"}
    if unknown:
        # Loud rather than ignored: a misspelled field would otherwise mean a
        # silently missing version badge or date.
        problems.append(f"{label}: unknown field(s) {', '.join(sorted(unknown))}")

    if date is None:
        return None, problems
    return (
        {"key": key, "title": title, "version": version, "date": date, "changes": changes},
        problems,
    )


def load_entries(path=DEFAULT_PATH):
    """Read and validate the file, returning normalised entry dicts.

    Raises ChangelogFileError listing every problem found. Exported (rather than
    being a method) so the test suite can validate the shipped file without
    running a write — which is what keeps a broken changelog off a deploy.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChangelogFileError([f"{path} does not exist"]) from exc
    except yaml.YAMLError as exc:
        raise ChangelogFileError([f"{path} is not valid YAML: {exc}"]) from exc

    # A file holding only comments parses to None. That is a legitimate "no
    # managed entries" state, not an error.
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ChangelogFileError(
            [f"{path} must hold a list of entries, got {type(raw).__name__}"]
        )

    problems = []
    entries = []
    seen_keys = set()
    for index, item in enumerate(raw):
        entry, entry_problems = _validate_entry(item, index, seen_keys)
        problems.extend(entry_problems)
        if entry is not None:
            seen_keys.add(entry["key"])
            entries.append(entry)

    if problems:
        raise ChangelogFileError(problems)
    return entries


class Command(BaseCommand):
    help = "Write calculatorapi/data/changelog.yaml into the changelog table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=str(DEFAULT_PATH),
            help="Source file to read (defaults to calculatorapi/data/changelog.yaml).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero if the file does not validate (for local checks and CI).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        try:
            entries = load_entries(options["file"])
        except ChangelogFileError as exc:
            self.stdout.write(self.style.ERROR("Changelog file did not validate; nothing written."))
            for problem in exc.problems:
                self.stdout.write(f"  - {problem}")
            if options["strict"]:
                # Only --strict may fail. See the module docstring.
                raise SystemExit(1) from exc
            return

        created = updated = unchanged = 0
        updated_keys = []
        for entry in entries:
            action = self._sync_entry(entry, dry_run=dry_run)
            if action == "created":
                created += 1
                verb = "would create" if dry_run else "created"
                self.stdout.write(self.style.SUCCESS(f"  {verb} {entry['key']} — {entry['title']}"))
            elif action == "updated":
                updated += 1
                updated_keys.append(entry["key"])
                verb = "would update" if dry_run else "updated"
                self.stdout.write(self.style.WARNING(f"  {verb} {entry['key']} — {entry['title']}"))
            else:
                unchanged += 1

        prefix = "Dry run: " if dry_run else ""
        self.stdout.write(
            f"{prefix}{len(entries)} managed entr{'y' if len(entries) == 1 else 'ies'}: "
            f"{created} created, {updated} updated, {unchanged} unchanged."
        )

        # Discord's #changelog follows the table: new entries post, rewritten
        # ones edit their post (changelog_discord). Silent while the bot token
        # or channel id is unset; a Discord failure is logged, never raised,
        # because this command sits on the path that boots the API.
        if not dry_run:
            self._announce(updated_keys)

    def _announce(self, updated_keys):
        if not discord_messages.is_configured(settings.DISCORD_CHANGELOG_CHANNEL_ID):
            return
        posted, edited = changelog_discord.announce(updated_keys)
        self.stdout.write(f"Discord #changelog: {posted} to post, {edited} to edit.")

    @transaction.atomic
    def _sync_entry(self, entry, dry_run):
        """Upsert one entry and return "created", "updated" or "unchanged".

        Change lines are replaced wholesale rather than reconciled row by row.
        The file is the authority on the whole list, and a delete-then-create is
        both simpler and immune to the ordering collisions a per-row update runs
        into when a line moves position.
        """
        existing = ChangelogEntry.objects.filter(key=entry["key"]).first()

        if existing is not None:
            current = (existing.title, existing.version, existing.date)
            current_changes = [(c.category, c.text) for c in existing.changes.all()]
            if current == (entry["title"], entry["version"], entry["date"]) and \
                    current_changes == entry["changes"]:
                return "unchanged"

        if dry_run:
            return "created" if existing is None else "updated"

        obj, was_created = ChangelogEntry.objects.update_or_create(
            key=entry["key"],
            defaults={
                "title": entry["title"],
                "version": entry["version"],
                "date": entry["date"],
            },
        )
        obj.changes.all().delete()
        ChangelogChange.objects.bulk_create([
            ChangelogChange(entry=obj, category=category, text=text, order=order)
            for order, (category, text) in enumerate(entry["changes"])
        ])
        return "created" if was_created else "updated"
