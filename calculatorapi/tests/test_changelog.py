"""The changelog: the public endpoint, the committed YAML file, and the command that syncs it."""

import datetime
import os
import shutil
import tempfile
from io import StringIO
from unittest import mock

import requests

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi.management.commands.sync_changelog import (
    load_entries as load_changelog_entries,
)
from calculatorapi import changelog_discord
from calculatorapi.models import ChangelogEntry, ChangelogChange, DiscordMessage
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


@override_settings(DISCORD_BOT_TOKEN="test-token", DISCORD_CHANGELOG_CHANNEL_ID="2000")
class ChangelogDiscordTests(CalculatorTestCase):
    """`sync_changelog` posts the week's new entries to #changelog, once each,
    and edits a post when the file rewrites its entry."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.path = os.path.join(self.directory, 'changelog.yaml')
        self.today = timezone.now().date()

    def _write(self, title='Fresh release', day=None, version='v3.0'):
        day = day or self.today
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(
                '- key: fresh\n'
                f'  date: {day.isoformat()}\n'
                f'  title: "{title}"\n'
                f'  version: "{version}"\n'
                '  changes:\n'
                '    - added: "A new thing."\n'
                '    - fixed: "An old bug."\n'
            )

    def _run(self):
        """Run the sync with Discord faked. Returns (output, the fake)."""
        answer = mock.Mock(status_code=200, ok=True)
        answer.json.return_value = {"id": "777"}
        out = StringIO()
        with mock.patch("requests.request", return_value=answer) as call:
            with self.captureOnCommitCallbacks(execute=True):
                call_command('sync_changelog', file=self.path, stdout=out)
        return out.getvalue(), call

    def test_a_new_entry_posts_once(self):
        self._write()
        output, call = self._run()
        self.assertIn('Discord #changelog: 1 to post, 0 to edit.', output)
        call.assert_called_once()
        self.assertEqual(call.call_args.args[0], "POST")
        entry = ChangelogEntry.objects.get(key='fresh')
        self.assertEqual(
            DiscordMessage.objects.get(key=f'changelog:{entry.id}').message_id, '777',
        )
        # Every later deploy runs the sync again; none of them posts again.
        _, call = self._run()
        call.assert_not_called()

    def test_pings_nobody_by_default(self):
        self._write()
        _, call = self._run()
        body = call.call_args.kwargs['json']
        self.assertNotIn('content', body)
        self.assertEqual(body['allowed_mentions'], {'parse': [], 'roles': []})

    def test_pings_the_role_on_the_first_post_only(self):
        self._write()
        with self.settings(DISCORD_CHANGELOG_PING_ROLE_ID='3000'):
            _, call = self._run()
            body = call.call_args.kwargs['json']
            self.assertEqual(body['content'], '<@&3000>')
            # That role and nothing else may notify.
            self.assertEqual(body['allowed_mentions'], {'parse': [], 'roles': ['3000']})
            self._write(title='Fresh release, fixed')
            _, call = self._run()
        # An edit leaves the ping line alone rather than pinging again.
        self.assertNotIn('content', call.call_args.kwargs['json'])

    def test_silent_without_the_settings(self):
        self._write()
        for token, channel in (("", "2000"), ("test-token", "")):
            with self.settings(DISCORD_BOT_TOKEN=token, DISCORD_CHANGELOG_CHANNEL_ID=channel):
                output, call = self._run()
            call.assert_not_called()
            self.assertNotIn('Discord', output)

    def test_a_rewritten_entry_edits_its_post(self):
        self._write()
        self._run()
        self._write(title='Fresh release, fixed')
        output, call = self._run()
        self.assertIn('0 to post, 1 to edit', output)
        method, url = call.call_args.args
        self.assertEqual(method, "PATCH")
        self.assertTrue(url.endswith('/channels/2000/messages/777'))
        self.assertEqual(call.call_args.kwargs['json']['embeds'][0]['title'], 'v3.0: Fresh release, fixed')

    def test_history_never_posts(self):
        # The first deploy with a token must not flood the channel.
        self._write(day=self.today - datetime.timedelta(days=changelog_discord.POST_WINDOW_DAYS + 1))
        ChangelogEntry.objects.create(title='Old, by hand', date=datetime.date(2026, 1, 1))
        _, call = self._run()
        call.assert_not_called()

    def test_nothing_before_the_start_date(self):
        # Released last week and announced by hand before the bot went live.
        self._write(day=self.today - datetime.timedelta(days=2))
        with self.settings(DISCORD_CHANGELOG_START_DATE=self.today.isoformat()):
            _, call = self._run()
            call.assert_not_called()
            ChangelogEntry.objects.create(title='Released today', date=self.today)
            _, call = self._run()
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs['json']['embeds'][0]['title'], 'Released today')

    def test_an_unreadable_start_date_posts_nothing(self):
        self._write()
        with self.settings(DISCORD_CHANGELOG_START_DATE='20 October'), \
                self.assertLogs('calculatorapi.changelog_discord', 'WARNING'):
            _, call = self._run()
        call.assert_not_called()

    def test_a_recent_hand_written_entry_posts(self):
        ChangelogEntry.objects.create(title='Written in the admin', date=self.today)
        self._write(day=self.today - datetime.timedelta(days=30))
        _, call = self._run()
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs['json']['embeds'][0]['title'], 'Written in the admin')

    def test_a_failed_post_is_retried_by_the_next_deploy(self):
        self._write()
        with mock.patch("requests.request", side_effect=requests.ConnectionError), \
                self.assertLogs("calculatorapi.discord_messages", "WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                call_command('sync_changelog', file=self.path, stdout=StringIO())
        self.assertFalse(DiscordMessage.objects.exists())
        _, call = self._run()
        call.assert_called_once()

    def test_dry_run_never_posts(self):
        self._write()
        with mock.patch("requests.request") as call:
            with self.captureOnCommitCallbacks(execute=True):
                call_command('sync_changelog', file=self.path, dry_run=True, stdout=StringIO())
        call.assert_not_called()

    def test_the_embed(self):
        self._write()
        self._run()
        embed = changelog_discord.build_embed(ChangelogEntry.objects.get(key='fresh'))
        self.assertEqual(embed['title'], 'v3.0: Fresh release')
        self.assertEqual(embed['description'], '**Added:** A new thing.\n**Fixed:** An old bug.')
        self.assertTrue(embed['url'].endswith('/changelog'))
        day = self.today
        self.assertEqual(
            embed['footer']['text'], f'{day.year}/{day.month}/{day.day} · {changelog_discord.VOICE_THANKS}',
        )

    def test_no_version_no_prefix(self):
        self._write(version='')
        self._run()
        embed = changelog_discord.build_embed(ChangelogEntry.objects.get(key='fresh'))
        self.assertEqual(embed['title'], 'Fresh release')

    def test_a_long_entry_is_cut_at_a_line(self):
        entry = ChangelogEntry.objects.create(title='Huge', date=self.today)
        ChangelogChange.objects.bulk_create([
            ChangelogChange(entry=entry, category='added', text='x' * 490, order=i)
            for i in range(12)
        ])
        description = changelog_discord.build_embed(entry)['description']
        self.assertLessEqual(len(description), 4096)
        self.assertTrue(description.endswith('more on the changelog'))
        # Every kept line is whole.
        self.assertTrue(all(line.endswith('x') for line in description.split('\n')[:-1]))
