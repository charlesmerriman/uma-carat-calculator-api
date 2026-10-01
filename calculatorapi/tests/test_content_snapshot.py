"""The content snapshot: what it may carry, where it may load, and the console driver."""

import gzip
import json
import os
import tempfile
from io import StringIO
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from calculatorapi.content_snapshot import (
    CONTENT_MODELS,
    PRIVATE_MODELS,
    content_labels,
    content_models,
)
from calculatorapi.management.commands import pull_prod_content
from calculatorapi.models import ClubRank, Plan
from .base import CalculatorTestCase


def write_snapshot(folder, rows):
    # Gzipped, because that is the form pull_prod_content hands over.
    path = os.path.join(folder, "snapshot.json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as snapshot:
        json.dump(rows, snapshot)
    return path


def dump(*labels):
    """dumpdata's rows for the given models, as parsed JSON."""
    out = StringIO()
    call_command("dumpdata", *labels, stdout=out)
    return json.loads(out.getvalue())


class ClassificationTests(CalculatorTestCase):
    def test_every_model_is_either_content_or_private(self):
        # The test that makes the include list safe to rely on: a new model
        # fails here until someone decides which side it is on.
        every = {model.__name__ for model in apps.get_app_config("calculatorapi").get_models()}
        self.assertEqual(set(CONTENT_MODELS) | set(PRIVATE_MODELS), every)
        self.assertEqual(set(CONTENT_MODELS) & set(PRIVATE_MODELS), set())

    def test_content_never_points_at_a_private_table(self):
        # A snapshot row with a foreign key to a table that is not in the
        # snapshot could not be loaded, and would say something about a person.
        content = set(content_models())
        for model in content:
            for field in model._meta.get_fields():
                if field.is_relation and field.concrete:
                    self.assertIn(
                        field.related_model, content,
                        f"{model.__name__}.{field.name} points outside the content list",
                    )


class LoadContentSnapshotTests(CalculatorTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def load(self, rows):
        call_command("load_content_snapshot", write_snapshot(self.folder, rows), stdout=StringIO())

    def test_refuses_a_database_that_is_not_sqlite(self):
        ClubRank.objects.create(name="Local", income_amount=1)
        path = write_snapshot(self.folder, [])
        with mock.patch("calculatorapi.content_snapshot.connection") as connection:
            connection.vendor = "postgresql"
            with self.assertRaisesMessage(CommandError, "not SQLite"):
                call_command("load_content_snapshot", path)
        self.assertTrue(ClubRank.objects.filter(name="Local").exists())

    def test_refuses_a_file_carrying_a_private_table(self):
        get_user_model().objects.create(username="someone")
        ClubRank.objects.create(name="Local", income_amount=1)
        rows = dump("calculatorapi.CustomUser")
        with self.assertRaisesMessage(CommandError, "calculatorapi.customuser"):
            self.load(rows)
        self.assertTrue(ClubRank.objects.filter(name="Local").exists())

    def test_replaces_content_rather_than_merging(self):
        ClubRank.objects.all().delete()
        ClubRank.objects.create(name="From production", income_amount=1)
        rows = dump("calculatorapi.ClubRank")
        ClubRank.objects.all().delete()
        ClubRank.objects.create(name="Local only", income_amount=2)

        self.load(rows)

        self.assertEqual(
            list(ClubRank.objects.values_list("name", flat=True)), ["From production"]
        )

    def test_local_accounts_and_their_rows_survive(self):
        user = get_user_model().objects.create(username="local")
        plans_before = Plan.objects.filter(user=user).count()
        self.load([])
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertEqual(Plan.objects.filter(user=user).count(), plans_before)

    def test_a_local_row_left_dangling_rolls_the_whole_load_back(self):
        # The account's rank is content; a snapshot without it would leave the
        # account pointing at nothing.
        rank = ClubRank.objects.create(name="Local", income_amount=1)
        get_user_model().objects.create(username="ranked", club_rank=rank)

        with self.assertRaisesMessage(CommandError, "Nothing was loaded"):
            self.load([])

        self.assertTrue(ClubRank.objects.filter(pk=rank.pk).exists())


class ConsoleDriverTests(CalculatorTestCase):
    """`run_in_console` against a local shell standing in for the remote one."""

    def test_reports_the_exit_code_of_the_typed_line(self):
        sentinel = pull_prod_content.SENTINEL
        with mock.patch.object(pull_prod_content, "SHELL_QUIET_SECONDS", 0.5):
            # `echo ready` gives the driver the "printed, then quiet" it waits for.
            argv = ["sh", "-c", "echo ready; exec sh"]
            status, output = pull_prod_content.run_in_console(argv, f"echo hello; echo {sentinel}")
            self.assertEqual(status, 0)
            self.assertIn("hello", output)

            status, _ = pull_prod_content.run_in_console(argv, f"(exit 3); echo {sentinel}")
            self.assertEqual(status, 3)

    def test_the_remote_line_dumps_only_content_and_always_cleans_up(self):
        line = pull_prod_content.remote_command_line("snapshots/x.json.gz")
        dumped = line.split(" -o ", maxsplit=1)[0].split("dumpdata ", maxsplit=1)[1].split()
        self.assertEqual(dumped, content_labels())
        self.assertIn("'ACL': 'private'", line)
        self.assertIn(f"rm -f {pull_prod_content.REMOTE_FILE}", line)
        # A terminal in canonical mode drops input lines past 4096 bytes.
        self.assertLess(len(line), 4000)


class PullProdContentGuardTests(CalculatorTestCase):
    def test_refuses_before_contacting_production(self):
        with mock.patch("calculatorapi.content_snapshot.connection") as connection, \
                mock.patch.object(pull_prod_content, "run_in_console") as console:
            connection.vendor = "postgresql"
            with self.assertRaisesMessage(CommandError, "not SQLite"):
                call_command("pull_prod_content", app_id="x")
        console.assert_not_called()
