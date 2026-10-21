"""The bot API (views/bot_api.py): the Discord worker's way in. Locked by a
shared secret, decides through the same functions as the admin, records a
Discord moderator by name without ever making an account, and tells a
moderator nothing about a person beyond a display name and a handle."""

import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from calculatorapi import gallery, gallery_moderation
from calculatorapi.models import (
    BlockedTerm,
    CustomUser,
    GalleryHiddenReason,
    GalleryReportReason,
    GalleryReportResolution,
    SocialAccount,
)
from calculatorapi.tests.test_gallery import GalleryFixture

KEY = "bot-test-key"
MODERATOR = "oguri_fan (123456789012345678)"


@override_settings(BOT_API_SECRET=KEY)
class BotApiTests(GalleryFixture):  # pylint: disable=too-many-public-methods

    def setUp(self):
        super().setUp()
        self.author = self.person(display_name="SpeedySuzuka")
        self.post_row = self.post(self.author)
        self.reporter = self.person(display_name="MooncakeMile")

    def report(self, user=None, **target):
        target = target or {"post": self.post_row}
        return gallery.report(
            user or self.reporter, reason=GalleryReportReason.SPAM, note="posted five times", **target
        )

    def call(self, method, name, body=None, *, key=KEY, **kwargs):
        headers = {"HTTP_X_BOT_KEY": key} if key is not None else {}
        url = reverse(name, kwargs=kwargs)
        if method == "get":
            return self.client.get(url, **headers)
        return self.client.post(url, json.dumps(body or {}), content_type="application/json", **headers)

    def moderate(self, action, kind="post", ref=None, moderator=MODERATOR):
        return self.call(
            "post", "bot-moderate", {"action": action, "moderator": moderator},
            kind=kind, ref=ref or self.post_row.public_id,
        )

    # ── The lock ──

    def test_every_route_404s_while_the_secret_is_unset(self):
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.call("get", "bot-reports").status_code, 404)
            self.assertEqual(self.moderate("keep").status_code, 404)

    def test_a_missing_or_wrong_key_is_refused(self):
        for key in (None, "", "bot-test-kez", KEY + "x"):
            with self.subTest(key=key):
                self.assertEqual(self.call("get", "bot-reports", key=key).status_code, 403)

    # ── Reading ──

    def test_reports_lists_open_items_newest_first(self):
        self.report()
        other = self.post(self.person())
        self.report(post=other)
        self.report(self.person(), post=other)
        items = self.call("get", "bot-reports").json()["items"]
        self.assertEqual([item["ref"] for item in items], [other.public_id, self.post_row.public_id])
        self.assertEqual(items[0]["open_reports"], 2)
        self.assertNotIn("MooncakeMile", json.dumps(items))

    def test_a_decided_item_leaves_the_list(self):
        self.report()
        gallery_moderation.keep(self.post_row, None, discord_moderator=MODERATOR)
        self.assertEqual(self.call("get", "bot-reports").json()["items"], [])

    def test_the_item_carries_names_and_handles_only(self):
        self.reporter.email = "mooncake@example.com"
        self.reporter.save()
        SocialAccount.objects.create(user=self.reporter, provider="discord", subject_id="81234567890")
        self.report()
        body = self.call("get", "bot-item", kind="post", ref=self.post_row.public_id).json()
        self.assertEqual(body["author"], {"display_name": "SpeedySuzuka", "handle": self.author.username})
        self.assertEqual(
            body["open_reports"][0]["reporter"],
            {"display_name": "MooncakeMile", "handle": self.reporter.username},
        )
        self.assertEqual(body["open_reports"][0]["note"], "posted five times")
        text = json.dumps(body)
        for private in ("mooncake@example.com", "81234567890", "discord", str(self.reporter.pk) + ","):
            self.assertNotIn(private, text)
        self.assertNotIn('"id"', text)

    def test_unknown_items_404(self):
        self.assertEqual(self.call("get", "bot-item", kind="post", ref="nope1234").status_code, 404)
        self.assertEqual(self.call("get", "bot-item", kind="comment", ref="abc").status_code, 404)
        self.assertEqual(self.call("get", "bot-item", kind="team", ref="1").status_code, 404)

    # ── Deciding ──

    def test_keep_records_the_discord_moderator_and_no_account(self):
        self.report()
        accounts = CustomUser.objects.count()
        response = self.moderate("keep")
        self.assertEqual(response.status_code, 200)
        report = self.post_row.reports.get()
        self.assertEqual(report.resolution, GalleryReportResolution.KEPT)
        self.assertEqual(report.resolved_by_discord, MODERATOR)
        self.assertIsNone(report.resolved_by)
        self.assertEqual(CustomUser.objects.count(), accounts)
        self.assertEqual(response.json()["item"]["decided"]["by"], "oguri_fan (Discord)")

    def test_remove_hides_the_post(self):
        self.report()
        self.assertEqual(self.moderate("remove").status_code, 200)
        self.post_row.refresh_from_db()
        self.assertTrue(self.post_row.is_hidden)
        self.assertEqual(self.post_row.hidden_reason, GalleryHiddenReason.MODERATOR)

    def test_block_blocks_the_author(self):
        self.report()
        self.assertEqual(self.moderate("block").status_code, 200)
        self.author.refresh_from_db()
        self.assertTrue(self.author.publishing_blocked)

    def test_a_comment_is_named_by_its_row_id(self):
        comment = gallery.add_comment(self.person(), self.post_row, "Nice team")
        self.report(comment=comment)
        self.assertEqual(self.moderate("remove", kind="comment", ref=str(comment.pk)).status_code, 200)
        comment.refresh_from_db()
        self.assertTrue(comment.is_hidden)

    def test_a_second_click_is_told_who_got_there_first(self):
        self.report()
        self.moderate("keep")
        response = self.moderate("remove", moderator="someone_else (223456789012345678)")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["handled_by"], "oguri_fan (Discord)")
        self.assertEqual(response.json()["resolution"], GalleryReportResolution.KEPT)
        self.post_row.refresh_from_db()
        self.assertFalse(self.post_row.is_hidden)

    def test_nothing_reported_is_a_409(self):
        response = self.moderate("keep")
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.json()["handled_by"])

    def test_bad_bodies_are_refused(self):
        self.report()
        self.assertEqual(self.moderate("delete").status_code, 400)
        for moderator in ("", "oguri_fan", "oguri_fan (not-a-number)", "x" * 90 + " (123456)"):
            with self.subTest(moderator=moderator):
                self.assertEqual(self.moderate("keep", moderator=moderator).status_code, 400)
        self.assertTrue(self.post_row.reports.get().is_open)

    def test_a_decision_edits_the_card(self):
        self.report()
        with self.settings(DISCORD_BOT_TOKEN="t", DISCORD_MOD_QUEUE_CHANNEL_ID="4000"):
            with mock.patch("calculatorapi.moderation_cards.refresh") as refresh:
                self.moderate("keep")
        refresh.assert_called_once()

    # ── Undo, for a misclick ──

    def undo(self, kind="post", ref=None):
        return self.call(
            "post", "bot-undo", {"moderator": MODERATOR}, kind=kind, ref=ref or self.post_row.public_id
        )

    def test_undo_a_remove_shows_it_and_reopens_its_reports(self):
        self.report()
        self.moderate("remove")
        response = self.undo()
        self.assertEqual(response.status_code, 200)
        self.post_row.refresh_from_db()
        self.assertFalse(self.post_row.is_hidden)
        report = self.post_row.reports.get()
        self.assertTrue(report.is_open)
        self.assertEqual((report.resolution, report.resolved_by_discord), ("", ""))
        self.assertEqual(len(response.json()["item"]["open_reports"]), 1)

    def test_undo_a_block_lifts_the_block_too(self):
        self.report()
        self.moderate("block")
        self.assertEqual(self.post_row.reports.get().resolution, GalleryReportResolution.BLOCKED)
        self.assertEqual(self.undo().status_code, 200)
        self.author.refresh_from_db()
        self.assertFalse(self.author.publishing_blocked)

    def test_undo_a_remove_leaves_an_unrelated_block_alone(self):
        self.report()
        self.moderate("remove")
        self.author.publishing_blocked = True
        self.author.save()
        self.undo()
        self.author.refresh_from_db()
        self.assertTrue(self.author.publishing_blocked)

    def test_undo_a_keep_hides_it_again_if_enough_people_reported(self):
        for _ in range(gallery_moderation.AUTO_HIDE_REPORTS):
            self.report(self.person())
        self.moderate("keep")
        self.undo()
        self.post_row.refresh_from_db()
        self.assertTrue(self.post_row.is_hidden)
        self.assertEqual(self.post_row.hidden_reason, GalleryHiddenReason.REPORTS)

    def test_undo_reaches_back_one_decision_only(self):
        self.report()
        self.moderate("keep")
        self.report(self.person())
        self.moderate("remove")
        self.undo()
        reports = self.post_row.reports.order_by("created_at")
        self.assertEqual([report.is_open for report in reports], [False, True])
        self.assertEqual(reports[0].resolution, GalleryReportResolution.KEPT)

    def test_nothing_to_undo_is_a_409(self):
        self.assertEqual(self.undo().status_code, 409)
        self.report()
        self.assertEqual(self.undo().status_code, 409)
        self.moderate("keep")
        self.assertEqual(self.undo().status_code, 200)
        self.assertEqual(self.undo().status_code, 409)

    # ── Unblock and blocked terms ──

    def test_unblock_lifts_the_block(self):
        self.report()
        self.moderate("block")
        response = self.call(
            "post", "bot-unblock", {"moderator": MODERATOR}, kind="post", ref=self.post_row.public_id
        )
        self.assertEqual(response.status_code, 200)
        self.author.refresh_from_db()
        self.assertFalse(self.author.publishing_blocked)
        self.assertFalse(response.json()["item"]["author_blocked"])
        # The item stays removed.
        self.assertTrue(response.json()["item"]["is_hidden"])

    def test_unblock_without_a_block_is_a_409(self):
        response = self.call(
            "post", "bot-unblock", {"moderator": MODERATOR}, kind="post", ref=self.post_row.public_id
        )
        self.assertEqual(response.status_code, 409)

    def test_a_blocked_term_is_added_normalised_once(self):
        response = self.call(
            "post", "bot-blocked-term", {"term": "ＢＡＤ", "match": "anywhere", "moderator": MODERATOR}
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(BlockedTerm.objects.get().term, "bad")
        again = self.call("post", "bot-blocked-term", {"term": "bad", "moderator": MODERATOR})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(BlockedTerm.objects.count(), 1)

    def test_a_blocked_term_needs_a_term_and_a_known_match(self):
        for body in ({"term": ""}, {"term": "x" * 61}, {"term": "bad", "match": "fuzzy"}):
            with self.subTest(body=body):
                response = self.call("post", "bot-blocked-term", {**body, "moderator": MODERATOR})
                self.assertEqual(response.status_code, 400)
        self.assertFalse(BlockedTerm.objects.exists())
