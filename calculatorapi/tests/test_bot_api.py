"""The bot API (views/bot_api.py): the Discord worker's way in. Locked by a
shared secret, decides through the same functions as the admin, records a
Discord moderator by name without ever making an account, and tells a
moderator nothing about a person beyond a display name and a handle."""

import datetime
import json
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from calculatorapi import gallery, gallery_moderation
from calculatorapi.models import (
    AnalyticsSnapshot,
    BlockedTerm,
    CustomUser,
    DailyVisit,
    DiscordMessage,
    GalleryHiddenReason,
    GalleryReportReason,
    GalleryReportResolution,
    SocialAccount,
    TeamQueueEntry,
)
from calculatorapi.tests.test_gallery import GalleryFixture

KEY = "bot-test-key"
KEY_HEADER = KEY
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


@override_settings(BOT_API_SECRET=KEY)
class BotMessageTests(GalleryFixture):
    """The worker's never-twice records: claim, record, release."""

    KEY = "promote:1562205622429155540"

    def claim(self, key=KEY, channel_id="1562200270061043872"):
        return self.client.post(
            reverse("bot-message-claim"), json.dumps({"key": key, "channel_id": channel_id}),
            content_type="application/json", HTTP_X_BOT_KEY=KEY_HEADER,
        )

    def on(self, method, key=KEY, body=None):
        url = reverse("bot-message", kwargs={"key": key})
        if method == "get":
            return self.client.get(url, HTTP_X_BOT_KEY=KEY_HEADER)
        return getattr(self.client, method)(
            url, json.dumps(body or {}), content_type="application/json", HTTP_X_BOT_KEY=KEY_HEADER,
        )

    def test_a_claim_is_won_once(self):
        self.assertEqual(self.claim().status_code, 201)
        second = self.claim()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["message_id"], "")
        self.assertEqual(DiscordMessage.objects.count(), 1)

    def test_record_then_read(self):
        self.claim()
        self.assertEqual(self.on("patch", body={"message_id": "1562519911803388028"}).status_code, 200)
        self.assertEqual(self.on("get").json()["message_id"], "1562519911803388028")
        self.assertEqual(self.claim().json()["message_id"], "1562519911803388028")

    def test_release_only_while_in_flight(self):
        self.claim()
        self.assertEqual(self.on("delete").status_code, 204)
        self.assertEqual(self.claim().status_code, 201)
        self.on("patch", body={"message_id": "1562519911803388028"})
        self.assertEqual(self.on("delete").status_code, 409)
        self.assertTrue(DiscordMessage.objects.filter(key=self.KEY).exists())

    def test_the_site_keys_are_out_of_reach(self):
        DiscordMessage.objects.create(key="changelog:42", channel_id="1", message_id="2")
        DiscordMessage.objects.create(key="card:post:7", channel_id="1", message_id="3")
        for key in ("changelog:42", "card:post:7", "promote:abc", "promote:", "anything"):
            with self.subTest(key=key):
                self.assertEqual(self.on("get", key=key).status_code, 404)
                self.assertEqual(self.on("delete", key=key).status_code, 404)
        self.assertEqual(self.claim(key="changelog:43").status_code, 404)
        self.assertEqual(DiscordMessage.objects.count(), 2)

    def test_bad_ids_are_refused(self):
        self.assertEqual(self.claim(channel_id="general").status_code, 400)
        self.claim()
        self.assertEqual(self.on("patch", body={"message_id": "x"}).status_code, 400)

    def test_locked_like_every_bot_route(self):
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.claim().status_code, 404)
        response = self.client.post(reverse("bot-message-claim"), "{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)


@override_settings(BOT_API_SECRET=KEY)
class BotQueueTests(GalleryFixture):
    """The team board's ordered queue: always 1..N, changed only through these."""

    A, B, C, D = "1562205603294875771", "1562205611196686466", "1562205622429155540", "1562205629244899419"

    def send(self, name, body=None, **kwargs):
        return self.client.post(
            reverse(name, kwargs=kwargs), json.dumps(body or {}),
            content_type="application/json", HTTP_X_BOT_KEY=KEY_HEADER,
        )

    def queue(self):
        return self.client.get(reverse("bot-queue"), HTTP_X_BOT_KEY=KEY_HEADER).json()["thread_ids"]

    def fill(self, *ids):
        for thread_id in ids:
            self.send("bot-queue", {"thread_id": thread_id})

    def test_add_joins_the_bottom_once(self):
        self.assertEqual(self.send("bot-queue", {"thread_id": self.A}).status_code, 201)
        self.fill(self.B)
        self.assertEqual(self.send("bot-queue", {"thread_id": self.A}).status_code, 200)
        self.assertEqual(self.queue(), [self.A, self.B])
        self.assertEqual(list(TeamQueueEntry.objects.values_list("position", flat=True)), [1, 2])

    def test_remove_closes_the_gap(self):
        self.fill(self.A, self.B, self.C)
        url = reverse("bot-queue-entry", kwargs={"thread_id": self.B})
        response = self.client.delete(url, HTTP_X_BOT_KEY=KEY_HEADER)
        self.assertEqual(response.json()["thread_ids"], [self.A, self.C])
        self.assertEqual(list(TeamQueueEntry.objects.values_list("position", flat=True)), [1, 2])
        again = self.client.delete(url, HTTP_X_BOT_KEY=KEY_HEADER)
        self.assertEqual(again.status_code, 404)

    def test_move_to_top_middle_and_past_the_end(self):
        self.fill(self.A, self.B, self.C, self.D)
        self.send("bot-queue-move", {"position": 1}, thread_id=self.C)
        self.assertEqual(self.queue(), [self.C, self.A, self.B, self.D])
        self.send("bot-queue-move", {"position": 3}, thread_id=self.C)
        self.assertEqual(self.queue(), [self.A, self.B, self.C, self.D])
        self.send("bot-queue-move", {"position": 99}, thread_id=self.A)
        self.assertEqual(self.queue(), [self.B, self.C, self.D, self.A])

    def test_move_refuses_bad_positions_and_unqueued_posts(self):
        self.fill(self.A)
        self.assertEqual(self.send("bot-queue-move", {"position": 0}, thread_id=self.A).status_code, 400)
        self.assertEqual(self.send("bot-queue-move", {"position": "x"}, thread_id=self.A).status_code, 400)
        self.assertEqual(self.send("bot-queue-move", {"position": 1}, thread_id=self.B).status_code, 404)

    def test_sync_keeps_places_drops_the_gone_and_appends_the_new(self):
        self.fill(self.A, self.B, self.C)
        self.send("bot-queue-move", {"position": 1}, thread_id=self.C)
        response = self.send("bot-queue-sync", {"thread_ids": [self.A, self.D, self.C]})
        self.assertEqual(response.json()["thread_ids"], [self.C, self.A, self.D])

    def test_ids_must_be_discord_ids(self):
        self.assertEqual(self.send("bot-queue", {"thread_id": "abc"}).status_code, 404)
        self.assertEqual(self.send("bot-queue-sync", {"thread_ids": ["abc"]}).status_code, 400)
        self.assertEqual(self.send("bot-queue-sync", {"thread_ids": "x"}).status_code, 400)

    def test_locked(self):
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.client.get(reverse("bot-queue")).status_code, 404)
        self.assertEqual(self.client.get(reverse("bot-queue")).status_code, 403)


@override_settings(BOT_API_SECRET=KEY)
class BotMessageLookupTests(GalleryFixture):
    """Finding the public post a team copy came from, by the copy's id."""

    def find(self, message_id):
        return self.client.get(reverse("bot-message-claim"), {"message_id": message_id}, HTTP_X_BOT_KEY=KEY_HEADER)

    def test_finds_a_worker_record_by_its_message(self):
        DiscordMessage.objects.create(
            key="promote:1552020789509755194", channel_id="1", message_id="1562519911803388028",
        )
        body = self.find("1562519911803388028").json()
        self.assertEqual(body["key"], "promote:1552020789509755194")

    def test_never_a_site_record(self):
        DiscordMessage.objects.create(key="card:post:7", channel_id="1", message_id="1562519911803388028")
        self.assertEqual(self.find("1562519911803388028").status_code, 404)
        self.assertEqual(self.find("x").status_code, 404)

    def test_the_new_prefixes_can_be_claimed(self):
        for key in ("upnext:1562200271222866020", "weekly:20261026"):
            with self.subTest(key=key):
                response = self.client.post(
                    reverse("bot-message-claim"), json.dumps({"key": key, "channel_id": "1562200271222866020"}),
                    content_type="application/json", HTTP_X_BOT_KEY=KEY_HEADER,
                )
                self.assertEqual(response.status_code, 201)


@override_settings(BOT_API_SECRET=KEY)
class BotDigestTests(GalleryFixture):
    """Yesterday's numbers for #calc-ops: right, and nothing about a person."""

    def digest(self):
        response = self.client.get(reverse("bot-digest"), HTTP_X_BOT_KEY=KEY_HEADER)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_counts_yesterday_and_the_gallery(self):
        yesterday = timezone.now() - datetime.timedelta(days=1)
        joined = self.person(display_name="NewcomerNana")
        joined.date_joined = yesterday
        joined.save()
        DailyVisit.objects.create(date=yesterday.date(), page_views=30, unique_visitors=12)
        author = self.person(display_name="SpeedySuzuka")
        post_row = self.post(author)
        gallery.report(self.person(), reason=GalleryReportReason.SPAM, post=post_row)
        body = self.digest()
        self.assertEqual(body["date"], yesterday.date().isoformat())
        self.assertEqual(body["visits"]["yesterday"], {"visitors": 12, "page_views": 30})
        self.assertEqual(body["visits"]["average_7_days"], round(12 / 7, 1))
        self.assertEqual(body["accounts"]["new_yesterday"], 1)
        self.assertEqual(body["gallery_last_24_hours"], {"posts": 1, "comments": 0, "reports": 1})
        self.assertEqual(body["open_report_items"], 1)

    def test_nothing_names_or_points_at_a_person(self):
        author = self.person(display_name="SpeedySuzuka")
        reporter = self.person(display_name="MooncakeMile")
        gallery.report(reporter, reason=GalleryReportReason.SPAM, post=self.post(author))
        text = json.dumps(self.digest())
        for private in ("SpeedySuzuka", "MooncakeMile", author.username, "_id", "handle", "email"):
            self.assertNotIn(private, text)

        def numbers_only(value):
            if isinstance(value, dict):
                return all(numbers_only(v) for v in value.values())
            return isinstance(value, (int, float))

        body = self.digest()
        body.pop("date")
        self.assertTrue(numbers_only(body))

    def test_reading_it_keeps_todays_snapshot(self):
        self.digest()
        self.assertTrue(AnalyticsSnapshot.objects.filter(date=timezone.localdate()).exists())

    def test_locked(self):
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.client.get(reverse("bot-digest")).status_code, 404)


@override_settings(BOT_API_SECRET=KEY)
class WorkerKeyShapeTests(GalleryFixture):
    """Each worker prefix takes exactly its own shape of key."""

    def claim(self, key):
        return self.client.post(
            reverse("bot-message-claim"), json.dumps({"key": key, "channel_id": "1562200271222866020"}),
            content_type="application/json", HTTP_X_BOT_KEY=KEY_HEADER,
        ).status_code

    def test_shapes(self):
        for key, expected in (
            ("digest:20261021", 201),
            ("deploy:3b4871e4-29bb-4067-86b0-7c3ef2d986f9", 201),
            ("deploy:3554696", 404),
            ("weekly:2026102", 404),
            ("promote:abcdef", 404),
            ("upnext:", 404),
        ):
            with self.subTest(key=key):
                self.assertEqual(self.claim(key), expected)
