"""
The daily masterdata check's state: GET/POST /game-data/versions, the
#calc-ops reports, and the admin's "Mark live" control
(calculatorapi/game_data_versions.py, models/game_data_version.py).
"""

from unittest import mock

from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse

from calculatorapi.models import DiscordMessage, GameDataStatus, GameDataVersion
from calculatorapi.tests.base import PLAIN_TEST_STORAGES, CalculatorTestCase
from calculatorapi.tests.factories import make_user
from calculatorapi.tests.test_discord_messages import discord_answer

KEY = "test-game-data-key"
HEADER = {"HTTP_X_GAME_DATA_SYNC_KEY": KEY}
URL = "/game-data/versions"


def finding(version, size=5738552, digest="82" * 16, region="global"):
    return {"region": region, "version": version, "master_size": size, "master_hash": digest}


def row(version, status=GameDataStatus.NEW, digest="82" * 16):
    return GameDataVersion.objects.create(version=version, master_size=1, master_hash=digest, status=status)


@override_settings(GAME_DATA_SYNC_SECRET=KEY)
class GameDataRouteTests(CalculatorTestCase):
    def post(self, findings, **headers):
        return self.client.post(URL, {"findings": findings}, content_type="application/json",
                                **(headers or HEADER))

    @override_settings(GAME_DATA_SYNC_SECRET="")
    def test_the_route_does_not_exist_while_the_secret_is_unset(self):
        self.assertEqual(self.client.get(URL, **HEADER).status_code, 404)

    def test_a_wrong_or_missing_key_is_refused(self):
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.client.get(URL, HTTP_X_GAME_DATA_SYNC_KEY="nope").status_code, 403)
        self.assertEqual(self.post([finding(1)], HTTP_X_GAME_DATA_SYNC_KEY="nope").status_code, 403)
        self.assertFalse(GameDataVersion.objects.exists())

    def test_get_lists_every_version_with_its_status(self):
        row(10008795, GameDataStatus.LIVE)
        row(10008800)
        versions = self.client.get(URL, **HEADER).json()["versions"]
        self.assertEqual([(v["version"], v["status"]) for v in versions],
                         [(10008800, "new"), (10008795, "live")])

    def test_post_records_new_revised_and_unchanged(self):
        row(10008795, GameDataStatus.LIVE, digest="aa" * 16)
        GameDataVersion.objects.filter(version=10008795).update(master_size=5738552)
        row(10008790, GameDataStatus.OLD)
        GameDataVersion.objects.filter(version=10008790).update(master_size=5738552)
        response = self.post([
            finding(10008795, digest="aa" * 16),   # unchanged
            finding(10008790, digest="bb" * 16),   # revised
            finding(10008800),                     # new
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c["change"] for c in response.json()["changes"]],
                         ["unchanged", "revised", "new"])
        revised = GameDataVersion.objects.get(version=10008790)
        self.assertEqual(revised.master_hash, "bb" * 16)
        self.assertIsNotNone(revised.revised_at)
        # Nothing the check posts ever changes a status: that is a person's.
        self.assertEqual(revised.status, GameDataStatus.OLD)
        self.assertEqual(GameDataVersion.objects.get(version=10008800).status, GameDataStatus.NEW)

    def test_a_malformed_finding_is_refused_whole(self):
        response = self.post([finding(10008800), finding(10008805, digest="not-hex")])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(GameDataVersion.objects.exists())


@override_settings(GAME_DATA_SYNC_SECRET=KEY, DISCORD_BOT_TOKEN="test-token",
                   DISCORD_OPS_CHANNEL_ID="1562200271222866020")
class GameDataReportTests(CalculatorTestCase):
    def post(self, findings):
        with mock.patch("requests.request", return_value=discord_answer()) as call:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(URL, {"findings": findings}, content_type="application/json", **HEADER)
        return [c.kwargs["json"]["content"] for c in call.call_args_list]

    def test_a_new_version_is_reported_once(self):
        sent = self.post([finding(10008800)])
        self.assertEqual(len(sent), 1)
        self.assertIn("New Global masterdata on the CDN: **10008800**. Not marked live.", sent[0])
        self.assertIn("/admin/calculatorapi/gamedataversion/", sent[0])
        self.assertEqual(self.post([finding(10008800)]), [])

    def test_each_revision_is_reported_once_and_says_whether_it_is_live(self):
        self.post([finding(10008795)])
        GameDataVersion.objects.get(version=10008795).mark_live()
        sent = self.post([finding(10008795, digest="cc" * 16)])
        self.assertEqual(len(sent), 1)
        self.assertIn("**10008795** (live) was revised on the CDN. The daily check opens a snapshot PR.", sent[0])
        self.assertEqual(self.post([finding(10008795, digest="cc" * 16)]), [])
        self.assertTrue(DiscordMessage.objects.filter(key=f"game-data:global:10008795:{'cc' * 16}").exists())

    @override_settings(DISCORD_OPS_CHANNEL_ID="")
    def test_no_channel_still_records_and_posts_nothing(self):
        self.assertEqual(self.post([finding(10008800)]), [])
        self.assertTrue(GameDataVersion.objects.filter(version=10008800).exists())


class MarkLiveTests(CalculatorTestCase):
    def test_marking_live_retires_the_old_live_and_undecided_versions_below(self):
        old_live = row(10008790, GameDataStatus.LIVE)
        skipped = row(10008792)
        ignored = row(10008793, GameDataStatus.IGNORED)
        target = row(10008795)
        staged = row(10008800)
        target.mark_live(by="Zac (admin)")
        statuses = dict(GameDataVersion.objects.values_list("version", "status"))
        self.assertEqual(statuses, {
            old_live.version: "old", skipped.version: "old", ignored.version: "ignored",
            target.version: "live", staged.version: "new",
        })
        self.assertEqual(GameDataVersion.objects.get(version=10008795).status_changed_by, "Zac (admin)")

    def test_the_database_allows_one_live_version_per_region(self):
        row(10008790, GameDataStatus.LIVE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            row(10008795, GameDataStatus.LIVE)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class GameDataAdminTests(CalculatorTestCase):
    def setUp(self):
        user = make_user("ops", is_staff=True)
        user.is_superuser = True
        user.save()
        self.client.force_login(user)
        self.changelist = reverse("admin:calculatorapi_gamedataversion_changelist")

    def test_the_list_renders_and_has_no_add_button(self):
        row(10008795)
        response = self.client.get(self.changelist)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10008795")
        self.assertEqual(self.client.get(reverse("admin:calculatorapi_gamedataversion_add")).status_code, 403)

    def test_mark_live_takes_exactly_one_row(self):
        a, b = row(10008795), row(10008800)
        self.client.post(self.changelist, {"action": "mark_live", "_selected_action": [a.pk, b.pk]})
        self.assertFalse(GameDataVersion.objects.filter(status=GameDataStatus.LIVE).exists())
        self.client.post(self.changelist, {"action": "mark_live", "_selected_action": [a.pk]})
        live = GameDataVersion.objects.get(status=GameDataStatus.LIVE)
        self.assertEqual(live.version, 10008795)
        self.assertEqual(live.status_changed_by, "ops (admin)")

    def test_ignore(self):
        a = row(10008800)
        self.client.post(self.changelist, {"action": "mark_ignored", "_selected_action": [a.pk]})
        self.assertEqual(GameDataVersion.objects.get(pk=a.pk).status, GameDataStatus.IGNORED)
