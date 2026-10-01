"""
Replaces the LOCAL database's content tables with a content snapshot.

The file is a `dumpdata` export of the tables in
`content_snapshot.CONTENT_MODELS` (`.json` or `.json.gz`). You rarely run this
directly: `pull_prod_content` fetches a fresh snapshot from production and
then calls it.

Local only. It refuses any database that is not SQLite, and refuses a file
that carries a table outside the content list.

It replaces rather than merges: every content table is emptied, then the
snapshot is loaded, so local content ends up identical to the snapshot. Rows
that are not content (your local accounts, their plans and purchases) are
left alone. If one of them points at content the snapshot no longer has, the
whole load is rolled back and nothing changes.

Usage:
    python manage.py load_content_snapshot snapshot.json.gz
"""

import gzip
import json
from collections import Counter

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, connection, transaction

from calculatorapi import public_payload_cache
from calculatorapi.content_snapshot import (
    content_labels,
    content_models,
    require_local_database,
)


def read_snapshot(path):
    """Parse the snapshot into dumpdata's list of {"model", "pk", "fields"}."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as snapshot:
        return json.load(snapshot)


class Command(BaseCommand):
    help = "Replace the local database's content tables with a content snapshot."

    def add_arguments(self, parser):
        parser.add_argument("path", help="A dumpdata file: .json or .json.gz.")

    def handle(self, *args, **options):
        require_local_database()
        path = options["path"]

        try:
            rows = read_snapshot(path)
        except (OSError, ValueError) as error:
            raise CommandError(f"Could not read {path}: {error}") from error

        # dumpdata writes labels lowercased ("calculatorapi.uma").
        counts = Counter(row["model"] for row in rows)
        allowed = {label.lower() for label in content_labels()}
        unexpected = sorted(set(counts) - allowed)
        if unexpected:
            raise CommandError(
                "This file holds tables that are not content, so it is not a content "
                f"snapshot: {', '.join(unexpected)}. Nothing was loaded."
            )

        try:
            # One transaction: either the content is fully replaced or nothing
            # changes. SQLite checks Django's foreign keys at COMMIT, not per
            # statement, which is what lets the tables be emptied in any order
            # while other rows still point at them.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    for model in content_models():
                        # Plain DELETE, not queryset.delete(): that would
                        # cascade into the local accounts' plans and purchases,
                        # which should survive a refresh.
                        table = connection.ops.quote_name(model._meta.db_table)
                        cursor.execute(f"DELETE FROM {table}")
                if rows:
                    call_command("loaddata", path, verbosity=0)
                # loaddata checks only the tables it wrote. This checks every
                # table, which is what finds a local plan row left pointing at
                # a banner that production has since deleted.
                connection.check_constraints()
        except IntegrityError as error:
            raise CommandError(
                "Nothing was loaded. A local row points at content that is not in the "
                f"snapshot:\n  {error}\n"
                "Delete that row, or start from a fresh database (see the README, "
                '"Local content").'
            ) from error

        # The raw DELETEs above send no signals, and a bulk writer owns its own
        # invalidation. A runserver in another process still holds its copy
        # until the cache TTL runs out or it is restarted.
        public_payload_cache.invalidate()

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(rows)} rows into {len(counts)} content tables."
        ))
        missing = sorted(allowed - set(counts))
        if missing:
            self.stdout.write(self.style.WARNING(
                f"Empty in the snapshot, so now empty locally: {', '.join(missing)}"
            ))
