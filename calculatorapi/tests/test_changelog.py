"""The changelog: the public endpoint, the committed YAML file, and the command that syncs it."""

import datetime
import os
import shutil
import tempfile
from io import StringIO

from django.core.management import call_command
from rest_framework.test import APIClient

from calculatorapi.management.commands.sync_changelog import (
    load_entries as load_changelog_entries,
)
from calculatorapi.models import ChangelogEntry, ChangelogChange
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_user, auth_client


class ChangelogEndpointTests(CalculatorTestCase):
    """The public /changelog endpoint lists entries newest-first with nested,
    ordered changes; writes stay admin-only."""

    def setUp(self):
        # Two entries out of date order so we can assert sorting.
        older = ChangelogEntry.objects.create(
            title='Initial release', version='v1.0',
            date=datetime.date(2026, 7, 1),
        )
        newer = ChangelogEntry.objects.create(
            title='Changelog added', version='v1.1',
            date=datetime.date(2026, 7, 16),
        )
        # Create changes out of `order` so we can assert they come back sorted.
        ChangelogChange.objects.create(
            entry=newer, category=ChangelogChange.CHANGED,
            text='Faster banner sorting.', order=2,
        )
        ChangelogChange.objects.create(
            entry=newer, category=ChangelogChange.ADDED,
            text='Changelog page.', order=0,
        )
        self.newer, self.older = newer, older

    def test_list_is_public_and_newest_first(self):
        res = APIClient().get('/changelog')
        self.assertEqual(res.status_code, 200)
        titles = [e['title'] for e in res.json()]
        self.assertEqual(titles, ['Changelog added', 'Initial release'])

    def test_nested_changes_are_ordered(self):
        res = APIClient().get('/changelog')
        newest = res.json()[0]
        # Sorted by ChangelogChange.Meta.ordering ("order", "id").
        self.assertEqual(
            [c['order'] for c in newest['changes']], [0, 2]
        )
        self.assertEqual(
            [c['category'] for c in newest['changes']], ['added', 'changed']
        )

    def test_retrieve_is_public(self):
        res = APIClient().get(f'/changelog/{self.newer.id}')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['version'], 'v1.1')

    def test_the_route_is_read_only(self):
        # No write actions at all, not merely gated ones: a staff token used
        # to be able to create entries here, around the admin and its history.
        staff_client, _ = auth_client(make_user('staffer', is_staff=True))
        for client in (APIClient(), staff_client):
            res = client.post('/changelog', {'title': 'x', 'date': '2026-07-17'})
            self.assertEqual(res.status_code, 405)
        self.assertEqual(staff_client.delete(f'/changelog/{self.newer.id}').status_code, 405)


class ShippedChangelogFileTests(CalculatorTestCase):
    """The committed changelog.yaml must always validate.

    This is the guard that keeps a broken file off production. `sync_changelog`
    runs on the path that starts the web service and deliberately exits 0 on a
    file it cannot read, so without this test a mistake would go unnoticed until
    someone spotted the changelog page missing an entry.
    """

    def test_shipped_file_validates(self):
        entries = load_changelog_entries()
        self.assertTrue(entries, 'changelog.yaml should hold at least one entry')

    def test_shipped_entries_are_complete(self):
        for entry in load_changelog_entries():
            self.assertTrue(entry['key'])
            self.assertTrue(entry['title'])
            self.assertIsInstance(entry['date'], datetime.date)


class SyncChangelogCommandTests(CalculatorTestCase):
    """`sync_changelog` writes the file's entries and nothing else."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.path = os.path.join(self.directory, 'changelog.yaml')

    def _write(self, text):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(text)

    def _run(self, **opts):
        out = StringIO()
        call_command('sync_changelog', file=self.path, stdout=out, **opts)
        return out.getvalue()

    ONE_ENTRY = (
        '- key: open-beta\n'
        '  date: 2026-09-01\n'
        '  title: "Open beta"\n'
        '  version: "v1.0"\n'
        '  changes:\n'
        '    - added: "First line."\n'
        '    - fixed: "Second line."\n'
    )

    def test_creates_entry_with_changes_in_file_order(self):
        self._write(self.ONE_ENTRY)
        self._run()
        entry = ChangelogEntry.objects.get(key='open-beta')
        self.assertEqual(entry.title, 'Open beta')
        self.assertEqual(entry.version, 'v1.0')
        self.assertEqual(entry.date, datetime.date(2026, 9, 1))
        # order is assigned from position, which is what the API sorts on.
        self.assertEqual(
            [(c.category, c.text, c.order) for c in entry.changes.all()],
            [('added', 'First line.', 0), ('fixed', 'Second line.', 1)],
        )

    def test_second_run_is_a_no_op(self):
        self._write(self.ONE_ENTRY)
        self._run()
        output = self._run()
        # Idempotence matters: this runs on every container start, not just on
        # a deploy that changed the file.
        self.assertIn('0 created, 0 updated, 1 unchanged', output)
        self.assertEqual(ChangelogEntry.objects.count(), 1)
        self.assertEqual(ChangelogChange.objects.count(), 2)

    def test_edited_entry_is_rewritten_and_stale_lines_removed(self):
        self._write(self.ONE_ENTRY)
        self._run()
        self._write(
            '- key: open-beta\n'
            '  date: 2026-09-02\n'
            '  title: "Open beta, renamed"\n'
            '  changes:\n'
            '    - changed: "Only line now."\n'
        )
        self._run()
        entry = ChangelogEntry.objects.get(key='open-beta')
        self.assertEqual(entry.title, 'Open beta, renamed')
        self.assertEqual(entry.date, datetime.date(2026, 9, 2))
        self.assertEqual(entry.version, '')
        # Change lines are replaced wholesale, so a deleted line really goes.
        self.assertEqual(
            [(c.category, c.text) for c in entry.changes.all()],
            [('changed', 'Only line now.')],
        )

    def test_hand_written_entries_are_untouched(self):
        # An entry authored in the admin has no key and must survive a sync
        # unchanged — the file is not authoritative over the whole table.
        manual = ChangelogEntry.objects.create(
            title='Written in the admin', date=datetime.date(2026, 8, 1),
        )
        ChangelogChange.objects.create(
            entry=manual, category=ChangelogChange.ADDED, text='Kept.',
        )
        self._write(self.ONE_ENTRY)
        self._run()
        manual.refresh_from_db()
        self.assertEqual(manual.title, 'Written in the admin')
        self.assertEqual(manual.changes.count(), 1)
        self.assertEqual(ChangelogEntry.objects.count(), 2)

    def test_two_hand_written_entries_can_coexist(self):
        # `key` is unique, so an unkeyed entry must store NULL rather than "" —
        # otherwise the second one written in the admin is an IntegrityError.
        ChangelogEntry.objects.create(title='One', date=datetime.date(2026, 8, 1))
        ChangelogEntry.objects.create(title='Two', date=datetime.date(2026, 8, 2))
        self.assertEqual(ChangelogEntry.objects.filter(key__isnull=True).count(), 2)

    def test_dry_run_writes_nothing(self):
        self._write(self.ONE_ENTRY)
        output = self._run(dry_run=True)
        self.assertIn('would create open-beta', output)
        self.assertEqual(ChangelogEntry.objects.count(), 0)

    def test_invalid_file_writes_nothing_and_exits_zero(self):
        # The deploy path. A bad patch note must not stop the API booting, so
        # the command reports and returns rather than raising.
        self._write('- key: "not a slug!"\n  date: nonsense\n')
        output = self._run()
        self.assertIn('did not validate', output)
        self.assertEqual(ChangelogEntry.objects.count(), 0)

    def test_strict_exits_non_zero_on_an_invalid_file(self):
        # The local/CI path, where a silent pass is the failure mode.
        self._write('- key: dupe\n  title: A\n  date: 2026-01-01\n'
                    '- key: dupe\n  title: B\n  date: 2026-01-02\n')
        with self.assertRaises(SystemExit):
            self._run(strict=True)
        self.assertEqual(ChangelogEntry.objects.count(), 0)

    def test_unknown_category_is_rejected(self):
        self._write('- key: k\n  title: T\n  date: 2026-01-01\n'
                    '  changes:\n    - invented: "nope"\n')
        output = self._run()
        self.assertIn("unknown category 'invented'", output)
        self.assertEqual(ChangelogEntry.objects.count(), 0)
