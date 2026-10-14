"""The gallery layer: posts, the ranking and its supporter weights, the public
identity block, votes, views, copies and the text filters.

The routes are tested in test_pvp_gallery.py; this module tests the rules the
routes call (gallery.py, gallery_ranking.py, public_identity.py,
gallery_moderation.py), so a rule is pinned once however many routes use it.
"""

import datetime
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi import benefits, gallery, gallery_moderation, gallery_ranking, public_identity
from calculatorapi.models import (
    BlockedTerm,
    GalleryComment,
    GalleryHiddenReason,
    GalleryPost,
    GalleryPostKind,
    GalleryReport,
    GalleryReportReason,
    GalleryVote,
    PatreonSupporter,
    PatreonTier,
    PvpTeam,
    PvpTeamCard,
    PvpTeamMember,
    SupportCard,
    Uma,
    UserOshi,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_champions_meeting, make_league_of_heroes, make_user

# The prod tiers' orders (benefits.py): Senior Class 1, Classic Class 2,
# Junior Class 3.
SENIOR, CLASSIC, JUNIOR = "Senior Class", "Classic Class", "Junior Class"


class GalleryFixture(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """An event, three tiers, umas of three characters plus a second outfit,
    and a full deck of cards plus one spare."""

    def setUp(self):
        self.cm = make_champions_meeting()
        self.tiers = {
            SENIOR: PatreonTier.objects.create(name=SENIOR, order=1),
            CLASSIC: PatreonTier.objects.create(name=CLASSIC, order=2),
            JUNIOR: PatreonTier.objects.create(name=JUNIOR, order=3),
        }
        self.umas = [
            Uma.objects.create(name=name, game_id=game_id, image=f"umas/{game_id}.png")
            for name, game_id in (
                ("Maruzensky", 100401),
                ("Oguri Cap", 100601),
                ("Gold Ship", 100701),
                ("Oguri Cap (Christmas)", 100602),
            )
        ]
        self.cards = [
            SupportCard.objects.create(
                name=f"Card {n}", game_id=30000 + n, image=f"support_cards/{30000 + n}.png"
            )
            for n in range(1, 8)
        ]
        self._user_count = 0

    def person(self, tier=None, *, display_name=None, active=True):
        """An account with a display name, and a pledge on `tier` if given."""
        self._user_count += 1
        user = make_user(f"person{self._user_count}")
        user.display_name = display_name or f"Trainer{self._user_count}"
        user.save()
        if tier:
            PatreonSupporter.objects.create(
                display_name=f"patron{self._user_count}",
                patreon_user_id=f"p{self._user_count}",
                linked_user=user,
                tier=self.tiers[tier],
                is_active=active,
            )
        return user

    def team(self, user, umas=None, *, cards=6, event=None):
        """A team on `event` (the CM by default), every slot filled."""
        event = event or {"champions_meeting": self.cm}
        team = PvpTeam.objects.create(user=user, **event)
        for position, uma in enumerate(umas or self.umas[:3]):
            member = PvpTeamMember.objects.create(
                team=team, position=position, uma=uma, style="front"
            )
            for slot, card in enumerate(self.cards[:cards], start=1):
                PvpTeamCard.objects.create(member=member, slot=slot, support_card=card)
        return team

    def post(self, user, **kwargs):
        """A published post by `user`, made through the real publish path."""
        post, _ = gallery.publish_team(
            user, self.team(user, **kwargs), confirm_public_identity=True
        )
        return post


class ModelConstraintTests(GalleryFixture):

    def test_a_pvp_post_needs_exactly_one_event(self):
        user = self.person()
        loh = make_league_of_heroes()
        for fields in ({}, {"champions_meeting": self.cm, "league_of_heroes": loh}):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    GalleryPost.objects.create(
                        author=user, kind=GalleryPostKind.PVP_TEAM, **fields
                    )

    def test_one_post_per_person_per_event_in_the_database(self):
        user = self.person()
        GalleryPost.objects.create(author=user, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryPost.objects.create(
                author=user, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm,
                public_id="abcdefgh",
            )

    def test_a_hidden_post_says_why(self):
        user = self.person()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryPost.objects.create(
                author=user, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm,
                is_hidden=True,
            )

    def test_a_report_has_exactly_one_target(self):
        user = self.person()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryReport.objects.create(reporter=user, reason=GalleryReportReason.SPAM)

    def test_one_vote_per_person_per_post(self):
        post = self.post(self.person())
        voter = self.person()
        GalleryVote.objects.create(post=post, user=voter)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryVote.objects.create(post=post, user=voter)


class LadderTests(GalleryFixture):

    def supporter(self, tier, active=True):
        return PatreonSupporter(tier=self.tiers[tier], is_active=active)

    def test_vote_weights_by_tier(self):
        self.assertEqual(benefits.vote_weight_for(None), 1)
        self.assertEqual(benefits.vote_weight_for(self.supporter(JUNIOR)), 2)
        self.assertEqual(benefits.vote_weight_for(self.supporter(CLASSIC)), 3)
        self.assertEqual(benefits.vote_weight_for(self.supporter(SENIOR)), 4)

    def test_gallery_looks_by_tier(self):
        self.assertIsNone(benefits.gallery_style_for(None))
        self.assertEqual(benefits.gallery_style_for(self.supporter(JUNIOR)), "rose")
        self.assertEqual(benefits.gallery_style_for(self.supporter(CLASSIC)), "sr")
        self.assertEqual(benefits.gallery_style_for(self.supporter(SENIOR)), "ssr")

    def test_the_sql_weight_agrees_with_the_ladder(self):
        """gallery_ranking builds its SQL from the ladder; a lapsed pledge, an
        untiered row and no row at all must all read as the free weight."""
        author = self.person()
        post = self.post(author)
        cases = {
            "none": (self.person(), 1),
            "junior": (self.person(JUNIOR), 2),
            "classic": (self.person(CLASSIC), 3),
            "senior": (self.person(SENIOR), 4),
            "lapsed": (self.person(SENIOR, active=False), 1),
        }
        untiered = self.person()
        PatreonSupporter.objects.create(display_name="untiered", linked_user=untiered, tier=None)
        cases["untiered"] = (untiered, 1)
        for label, (voter, weight) in cases.items():
            with self.subTest(label):
                GalleryVote.objects.all().delete()
                GalleryVote.objects.create(post=post, user=voter)
                ranked = gallery_ranking.ranked(GalleryPost.objects.all(), "top")
                self.assertEqual(ranked[0].vote_points, weight)
                entitled = benefits.entitled_supporter(voter)
                self.assertEqual(benefits.vote_weight_for(entitled), weight)


class RankingTests(GalleryFixture):
    """The worked example from gallery_ranking.py's docstring, exactly."""

    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        free_voters = [self.person() for _ in range(12)]
        classic_voter = self.person(CLASSIC)
        senior_voter = self.person(SENIOR)
        loh = make_league_of_heroes()
        # One event per post so each author can post once; the ranking does
        # not care which event a post is on.
        events = [{"champions_meeting": self.cm}] + [
            {"league_of_heroes": event} for event in (loh, make_league_of_heroes("L2"),
                                                       make_league_of_heroes("L3"),
                                                       make_league_of_heroes("L4"))
        ]
        spec = {
            # name: (author tier, posted on day, voters)
            "A": (None, 1, free_voters[:12]),
            "B": (SENIOR, 5, free_voters[:3] + [classic_voter]),
            "C": (None, 6.5, free_voters[:4]),
            "D": (JUNIOR, 7, []),
            "E": (None, 2, free_voters[:5] + [senior_voter]),
        }
        self.posts = {}
        for (name, (tier, day, voters)), event in zip(spec.items(), events):
            post = self.post(self.person(tier), event=event)
            when = self.now - datetime.timedelta(days=7 - day)
            GalleryPost.objects.filter(pk=post.pk).update(
                published_at=when, content_updated_at=when
            )
            for voter in voters:
                GalleryVote.objects.create(post=post, user=voter)
            self.posts[post.pk] = name

    def order(self, sort):
        ranked = gallery_ranking.ranked(GalleryPost.objects.all(), sort, now=self.now)
        return "".join(self.posts[post.pk] for post in ranked), ranked

    def test_hot(self):
        order, ranked = self.order("hot")
        self.assertEqual(order, "BCAED")
        hot = {self.posts[post.pk]: round(post.hot, 2) for post in ranked}
        self.assertEqual(hot, {"A": 3.25, "B": 6.30, "C": 4.45, "D": 2.0, "E": 3.15})

    def test_top_breaks_ties_by_the_newer_post(self):
        order, ranked = self.order("top")
        self.assertEqual(order, "ABECD")
        self.assertEqual([post.points for post in ranked], [13, 10, 10, 5, 2])

    def test_new(self):
        self.assertEqual(self.order("new")[0], "DCBEA")

    def test_rows_count_people_not_points(self):
        _, ranked = self.order("top")
        counts = {self.posts[post.pk]: post.upvote_count for post in ranked}
        self.assertEqual(counts, {"A": 12, "B": 4, "C": 4, "D": 0, "E": 6})

    def test_a_lapse_takes_the_head_start_away(self):
        b = GalleryPost.objects.get(pk=next(pk for pk, n in self.posts.items() if n == "B"))
        PatreonSupporter.objects.filter(linked_user=b.author).update(is_active=False)
        _, ranked = self.order("top")
        self.assertEqual(next(p.points for p in ranked if p.pk == b.pk), 7)

    def test_a_blocked_voter_does_not_count(self):
        a = next(pk for pk, n in self.posts.items() if n == "A")
        voter = GalleryVote.objects.filter(post_id=a).first().user
        voter.publishing_blocked = True
        voter.save()
        _, ranked = self.order("top")
        row = next(post for post in ranked if post.pk == a)
        self.assertEqual((row.points, row.upvote_count), (12, 11))


class PosterBlockTests(GalleryFixture):

    def test_exactly_the_public_keys(self):
        block = public_identity.poster_block(self.person(SENIOR, display_name="Mooncake"))
        self.assertEqual(set(block), {"display_name", "portrait", "tier_name", "gallery_style"})
        self.assertEqual(block["display_name"], "Mooncake")
        self.assertEqual((block["tier_name"], block["gallery_style"]), (SENIOR, "ssr"))

    def test_badge_off_hides_tier_and_look(self):
        user = self.person(CLASSIC)
        user.show_supporter_badge = False
        user.save()
        block = public_identity.poster_block(user)
        self.assertEqual((block["tier_name"], block["gallery_style"]), (None, None))

    def test_no_pledge_or_a_lapse_shows_no_tier(self):
        for user in (self.person(), self.person(JUNIOR, active=False)):
            block = public_identity.poster_block(user)
            self.assertEqual((block["tier_name"], block["gallery_style"]), (None, None))

    def test_portrait_is_the_first_favourite_with_art(self):
        user = self.person()
        UserOshi.objects.create(user=user, uma=self.umas[1], position=0)
        self.assertTrue(public_identity.poster_block(user)["portrait"].endswith("umas/100601.png"))
        self.assertIsNone(public_identity.poster_block(self.person())["portrait"])

    def test_many_at_once_matches_one_at_a_time(self):
        users = [self.person(SENIOR), self.person(), self.person(JUNIOR)]
        blocks = public_identity.poster_blocks(users + [None, users[0]])
        for user in users:
            self.assertEqual(blocks[user.pk], public_identity.poster_block(user))


class PublishTests(GalleryFixture):

    def publish(self, user, team, **kwargs):
        kwargs.setdefault("confirm_public_identity", True)
        return gallery.publish_team(user, team, **kwargs)

    def assertRefused(self, status, call, *args, **kwargs):  # pylint: disable=invalid-name
        with self.assertRaises(gallery.GalleryRefused) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)
        return caught.exception.message

    def test_the_consent_flag_must_be_literally_true(self):
        user = self.person()
        for flag in (False, "true", 1, None):
            with self.subTest(flag=flag):
                self.assertRefused(400, self.publish, user, self.team(user), confirm_public_identity=flag)

    def test_needs_a_display_name(self):
        user = self.person()
        user.display_name = ""
        user.save()
        message = self.assertRefused(400, self.publish, user, self.team(user))
        self.assertEqual(message, "Set a display name first.")

    def test_a_blocked_account_cannot_post(self):
        user = self.person()
        user.publishing_blocked = True
        user.save()
        self.assertRefused(403, self.publish, user, self.team(user))

    def test_only_your_own_team(self):
        self.assertRefused(403, self.publish, self.person(), self.team(self.person()))

    def test_the_team_must_be_complete(self):
        user = self.person()
        self.assertRefused(400, self.publish, user, self.team(user, umas=self.umas[:2]))
        PvpTeam.objects.all().delete()
        self.assertRefused(400, self.publish, user, self.team(user, cards=5))

    def test_two_outfits_of_one_character_are_refused(self):
        user = self.person()
        team = self.team(user, umas=[self.umas[0], self.umas[1], self.umas[3]])
        self.assertRefused(400, self.publish, user, team)

    def test_caption_filters(self):
        user = self.person()
        BlockedTerm.objects.create(term="Rude")
        for caption in ("x" * 141, "join https://spam.example", "so RUDE", "discord.gg/abc"):
            with self.subTest(caption=caption):
                self.assertRefused(400, self.publish, user, self.team(user), caption=caption)
                PvpTeam.objects.all().delete()

    def test_strategy_filters_name_the_strategy(self):
        user = self.person()
        BlockedTerm.objects.create(term="Rude")
        for strategy in ("x" * 2001, "see https://spam.example", "so RUDE"):
            with self.subTest(strategy=strategy):
                with self.assertRaises(gallery.GalleryRefused) as caught:
                    self.publish(user, self.team(user), strategy=strategy)
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(caught.exception.field, "strategy")
                PvpTeam.objects.all().delete()

    def test_the_strategy_is_trimmed_and_keeps_its_line_breaks(self):
        user = self.person()
        post, _ = self.publish(user, self.team(user), strategy="  Line one\n\nLine two  ")
        self.assertEqual(post.strategy, "Line one\n\nLine two")
        post, _ = self.publish(user, self.team(user), strategy="x" * 2000)
        self.assertEqual(len(post.strategy), 2000)

    def test_changing_only_the_words_keeps_the_votes_and_the_place(self):
        user = self.person()
        team = self.team(user)
        post, _ = self.publish(user, team, caption="Old", strategy="Old plan")
        GalleryVote.objects.create(post=post, user=self.person())
        before = post.content_updated_at
        again, created = self.publish(user, team, caption="New", strategy="New plan")
        self.assertFalse(created)
        self.assertEqual((again.caption, again.strategy), ("New", "New plan"))
        self.assertEqual(again.votes.count(), 1)
        self.assertEqual(again.content_updated_at, before)
        # One card is a different team: the votes were for the old one.
        PvpTeamCard.objects.filter(member__team=team, slot=6).update(support_card=self.cards[6])
        changed, _ = self.publish(user, team, caption="New", strategy="New plan")
        self.assertEqual(changed.votes.count(), 0)
        self.assertGreater(changed.content_updated_at, before)

    def test_the_snapshot_is_frozen(self):
        user = self.person()
        team = self.team(user)
        post, created = self.publish(user, team, caption="  Runaway blocker  ")
        self.assertTrue(created)
        self.assertEqual(post.caption, "Runaway blocker")
        self.assertEqual([m["uma"] for m in post.team_snapshot], [u.id for u in self.umas[:3]])
        self.assertEqual(len(post.team_snapshot[0]["cards"]), 6)
        team.members.filter(position=2).update(style="late")
        post.refresh_from_db()
        self.assertEqual(post.team_snapshot[2]["style"], "front")

    def test_posting_again_replaces_the_copy_and_clears_the_votes(self):
        user = self.person()
        post, _ = self.publish(user, self.team(user))
        GalleryVote.objects.create(post=post, user=self.person())
        before = post.content_updated_at
        version = post.preview_version
        other = self.team(user, umas=[self.umas[2], self.umas[1], self.umas[0]])
        again, created = self.publish(user, other)
        self.assertFalse(created)
        self.assertEqual(again.public_id, post.public_id)
        self.assertEqual(GalleryPost.objects.count(), 1)
        self.assertEqual(again.votes.count(), 0)
        self.assertGreater(again.content_updated_at, before)
        self.assertNotEqual(again.preview_version, version)
        self.assertEqual(again.source_team, other)

    def test_a_card_only_change_keeps_the_preview_version(self):
        user = self.person()
        team = self.team(user)
        post, _ = self.publish(user, team)
        PvpTeamCard.objects.filter(member__team=team, slot=6).update(support_card=self.cards[6])
        again, _ = self.publish(user, team)
        self.assertEqual(again.preview_version, post.preview_version)

    def test_reposting_does_not_unhide(self):
        user = self.person()
        post, _ = self.publish(user, self.team(user))
        GalleryPost.objects.filter(pk=post.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.MODERATOR
        )
        again, _ = self.publish(user, self.team(user))
        self.assertTrue(again.is_hidden)


class ReadTests(GalleryFixture):

    def test_hidden_and_blocked_posts_are_not_visible(self):
        visible = self.post(self.person())
        hidden = self.post(self.person())
        GalleryPost.objects.filter(pk=hidden.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS
        )
        blocked_author = self.person()
        blocked = self.post(blocked_author)
        blocked_author.publishing_blocked = True
        blocked_author.save()
        self.assertEqual(list(gallery.visible_posts()), [visible])
        for post in (hidden, blocked):
            with self.assertRaises(GalleryPost.DoesNotExist):
                gallery.get_post(post.public_id)
            self.assertEqual(gallery.get_post(post.public_id, post.author), post)

    def test_a_snapshot_drops_what_left_the_catalogue(self):
        post = self.post(self.person())
        self.cards[5].delete()
        self.umas[2].delete()
        shown = gallery.snapshot_for_display(post.team_snapshot)
        self.assertEqual([m["uma"] for m in shown], [self.umas[0].id, self.umas[1].id])
        self.assertEqual(len(shown[0]["cards"]), 5)


class VoteTests(GalleryFixture):

    def test_votes_are_idempotent_and_reversible(self):
        post = self.post(self.person())
        voter = self.person()
        gallery.vote(voter, post)
        gallery.vote(voter, post)
        self.assertEqual(gallery.upvote_count(post), 1)
        gallery.unvote(voter, post)
        gallery.unvote(voter, post)
        self.assertEqual(gallery.upvote_count(post), 0)

    def test_not_on_your_own_post_and_not_when_blocked(self):
        author = self.person()
        post = self.post(author)
        blocked = self.person()
        blocked.publishing_blocked = True
        blocked.save()
        for user in (author, blocked):
            with self.assertRaises(gallery.GalleryRefused) as caught:
                gallery.vote(user, post)
            self.assertEqual(caught.exception.status, 403)


class ViewAndCopyTests(GalleryFixture):

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.author = self.person()
        self.post_row = self.post(self.author)

    def request(self, user=None, ip="203.0.113.5", agent="Mozilla/5.0"):
        request = self.factory.post("/", REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)
        request.user = user or _Anonymous()
        return request

    def views(self):
        self.post_row.refresh_from_db()
        return self.post_row.view_count

    def test_once_per_viewer_per_day(self):
        viewer = self.person()
        self.assertTrue(gallery.record_view(self.request(viewer), self.post_row))
        self.assertFalse(gallery.record_view(self.request(viewer), self.post_row))
        self.assertTrue(gallery.record_view(self.request(), self.post_row))
        self.assertFalse(gallery.record_view(self.request(), self.post_row))
        self.assertTrue(gallery.record_view(self.request(ip="198.51.100.9"), self.post_row))
        self.assertEqual(self.views(), 3)

    def test_not_the_author_and_not_a_crawler(self):
        self.assertFalse(gallery.record_view(self.request(self.author), self.post_row))
        bot = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"
        self.assertFalse(gallery.record_view(self.request(agent=bot), self.post_row))
        self.assertEqual(self.views(), 0)

    def test_a_copy_counts_unless_it_is_the_authors(self):
        team = gallery.copy_snapshot(self.request(self.person()), self.post_row)
        gallery.copy_snapshot(self.request(self.author), self.post_row)
        self.post_row.refresh_from_db()
        self.assertEqual(self.post_row.copy_count, 1)
        self.assertEqual(len(team), 3)


class _Anonymous:  # pylint: disable=too-few-public-methods
    is_authenticated = False
    pk = None


class TextFilterTests(CalculatorTestCase):

    def refused(self, text):
        try:
            gallery_moderation.check_text(text)
        except gallery_moderation.TextRefused as refusal:
            return str(refusal)
        return None

    def test_links_are_refused(self):
        for text in ("see http://x.io", "HTTPS://X.IO", "www.example.com", "discord.gg/abc",
                     "discord.com/invite/abc", "dsc.gg/abc"):
            with self.subTest(text=text):
                self.assertEqual(self.refused(text), gallery_moderation.LINKS_REFUSED)
        for text in ("v1.12 is out", "Mr.C.B. on the turf", "Pace 2.0"):
            with self.subTest(text=text):
                self.assertIsNone(self.refused(text))

    def test_blocked_terms_whole_word_by_default(self):
        BlockedTerm.objects.create(term="Ass")
        self.assertEqual(self.refused("what an ASS"), gallery_moderation.TERMS_REFUSED)
        self.assertIsNone(self.refused("a class act, with good passing"))

    def test_blocked_terms_anywhere(self):
        BlockedTerm.objects.create(term="scam", match="anywhere")
        self.assertEqual(self.refused("SCAMMERS here"), gallery_moderation.TERMS_REFUSED)

    def test_look_alikes_and_invisible_characters_are_folded(self):
        BlockedTerm.objects.create(term="bad")
        for text in ("ＢＡＤ team", "b\u200bad team", "B\u00adAD"):
            with self.subTest(text=text):
                self.assertEqual(self.refused(text), gallery_moderation.TERMS_REFUSED)

    def test_terms_are_stored_normalised(self):
        self.assertEqual(BlockedTerm.objects.create(term="  ＢＡＤ ").term, "bad")


class CommentTests(GalleryFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person()
        self.post_row = self.post(self.author)
        self.commenter = self.person()

    def test_needs_a_name_and_not_blocked(self):
        nameless = self.person()
        nameless.display_name = ""
        nameless.save()
        blocked = self.person()
        blocked.publishing_blocked = True
        blocked.save()
        for user, status in ((nameless, 400), (blocked, 403)):
            with self.assertRaises(gallery.GalleryRefused) as caught:
                gallery.add_comment(user, self.post_row, "Nice team")
            self.assertEqual(caught.exception.status, status)

    def test_text_rules(self):
        BlockedTerm.objects.create(term="rude")
        for body in ("", "   ", "x" * 501, "so rude", "see www.example.com"):
            with self.subTest(body=body):
                with self.assertRaises(gallery.GalleryRefused):
                    gallery.add_comment(self.commenter, self.post_row, body)

    def test_the_same_text_twice_in_a_day_is_refused(self):
        gallery.add_comment(self.commenter, self.post_row, "Great team")
        other = self.post(self.person())
        with self.assertRaises(gallery.GalleryRefused) as caught:
            gallery.add_comment(self.commenter, other, "Great team")
        self.assertEqual(caught.exception.message, "You've already posted that comment.")

    def test_one_level_of_replies(self):
        top = gallery.add_comment(self.commenter, self.post_row, "Top")
        reply = gallery.add_comment(self.author, self.post_row, "Reply", top.pk)
        deeper = gallery.add_comment(self.person(), self.post_row, "Reply to the reply", reply.pk)
        self.assertEqual(reply.parent, top)
        self.assertEqual(deeper.parent, top)

    def test_a_reply_to_a_comment_on_another_post_is_refused(self):
        elsewhere = gallery.add_comment(self.commenter, self.post(self.person()), "Elsewhere")
        with self.assertRaises(gallery.GalleryRefused):
            gallery.add_comment(self.author, self.post_row, "Reply", elsewhere.pk)

    def test_edit_marks_it_edited(self):
        comment = gallery.add_comment(self.commenter, self.post_row, "First")
        gallery.edit_comment(comment, "Second")
        comment.refresh_from_db()
        self.assertEqual((comment.body, comment.edited_at is not None), ("Second", True))

    def test_delete_with_and_without_replies(self):
        lonely = gallery.add_comment(self.commenter, self.post_row, "Lonely")
        gallery.delete_comment(lonely)
        self.assertFalse(GalleryComment.objects.filter(pk=lonely.pk).exists())
        answered = gallery.add_comment(self.commenter, self.post_row, "Answered")
        gallery.add_comment(self.author, self.post_row, "Answer", answered.pk)
        gallery.delete_comment(answered)
        answered.refresh_from_db()
        self.assertEqual((answered.is_deleted, answered.body, answered.author), (True, "", None))

    def test_the_thread_shows_states_to_the_right_people(self):
        visible = gallery.add_comment(self.commenter, self.post_row, "Visible")
        hidden = gallery.add_comment(self.person(), self.post_row, "Hidden, no replies")
        GalleryComment.objects.filter(pk=hidden.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS
        )
        removed = gallery.add_comment(self.person(), self.post_row, "Removed, with a reply")
        gallery.add_comment(self.author, self.post_row, "A reply", removed.pk)
        GalleryComment.objects.filter(pk=removed.pk).update(
            is_hidden=True, hidden_reason=GalleryHiddenReason.MODERATOR
        )
        states = {c.pk: state for c, state, _ in gallery.thread(self.post_row)}
        self.assertEqual(states, {visible.pk: "visible", removed.pk: "removed"})
        own = {c.pk: state for c, state, _ in gallery.thread(self.post_row, hidden.author)}
        self.assertEqual(own[hidden.pk], "under_review")


class ReportTests(GalleryFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person()
        self.post_row = self.post(self.author)

    def report(self, user=None, **target):
        target = target or {"post": self.post_row}
        return gallery.report(user or self.person(), reason=GalleryReportReason.SPAM, **target)

    def test_not_your_own_and_not_twice(self):
        with self.assertRaises(gallery.GalleryRefused):
            self.report(self.author)
        reporter = self.person()
        self.report(reporter)
        with self.assertRaises(gallery.GalleryRefused) as caught:
            self.report(reporter)
        self.assertEqual(caught.exception.status, 409)

    def test_three_people_hide_it(self):
        self.report()
        self.report()
        self.post_row.refresh_from_db()
        self.assertFalse(self.post_row.is_hidden)
        self.report()
        self.post_row.refresh_from_db()
        self.assertEqual((self.post_row.is_hidden, self.post_row.hidden_reason), (True, "reports"))

    def test_comments_hide_too(self):
        comment = gallery.add_comment(self.person(), self.post_row, "Spam spam")
        for _ in range(3):
            self.report(comment=comment)
        comment.refresh_from_db()
        self.assertTrue(comment.is_hidden)

    def test_keep_restores_and_closes(self):
        for _ in range(3):
            self.report()
        moderator = make_user("mod", is_staff=True)
        gallery_moderation.keep(self.post_row, moderator)
        self.post_row.refresh_from_db()
        self.assertFalse(self.post_row.is_hidden)
        self.assertFalse(self.post_row.reports.filter(resolved_at__isnull=True).exists())
        self.assertEqual(set(self.post_row.reports.values_list("resolution", flat=True)), {"kept"})

    def test_remove_and_block(self):
        self.report()
        moderator = make_user("mod", is_staff=True)
        gallery_moderation.block_author(self.post_row, moderator)
        self.post_row.refresh_from_db()
        self.author.refresh_from_db()
        self.assertEqual(self.post_row.hidden_reason, "moderator")
        self.assertTrue(self.author.publishing_blocked)
        self.assertEqual(self.post_row.reports.get().resolved_by, moderator)

    def test_the_alert_is_off_when_blank_and_quiet_about_people(self):
        with self.settings(MODERATION_WEBHOOK_URL=""), mock.patch("requests.post") as post:
            with self.captureOnCommitCallbacks(execute=True):
                self.report()
        post.assert_not_called()
        with self.settings(MODERATION_WEBHOOK_URL="https://hook.example/x"), \
                mock.patch("requests.post") as post:
            with self.captureOnCommitCallbacks(execute=True):
                comment = gallery.add_comment(self.person(display_name="Snitch"), self.post_row, "secret text")
                self.report(comment=comment)
        post.assert_called_once()
        sent = post.call_args.kwargs["json"]["content"]
        self.assertIn("reported", sent)
        self.assertNotIn("secret text", sent)
        self.assertNotIn("Snitch", sent)

    def test_a_failing_webhook_never_fails_the_report(self):
        with self.settings(MODERATION_WEBHOOK_URL="https://hook.example/x"), \
                mock.patch("requests.post", side_effect=requests.ConnectionError):
            with self.captureOnCommitCallbacks(execute=True):
                self.report()
        self.assertEqual(GalleryReport.objects.count(), 1)


class EraseTests(GalleryFixture):

    def test_erase_users(self):
        leaving = self.person()
        staying = self.person()
        their_post = self.post(leaving)
        other_post = self.post(staying)
        GalleryVote.objects.create(post=other_post, user=leaving)
        lonely = gallery.add_comment(leaving, other_post, "Lonely")
        answered = gallery.add_comment(leaving, other_post, "Answered")
        reply_kept = gallery.add_comment(staying, other_post, "Kept reply", answered.pk)
        their_reply = gallery.add_comment(leaving, other_post, "Their reply", answered.pk)
        filed = gallery.report(leaving, reason=GalleryReportReason.OTHER, post=other_post)

        gallery.erase_users([leaving])

        self.assertFalse(GalleryPost.objects.filter(pk=their_post.pk).exists())
        self.assertFalse(GalleryVote.objects.filter(user=leaving).exists())
        self.assertFalse(GalleryComment.objects.filter(pk__in=[lonely.pk, their_reply.pk]).exists())
        answered.refresh_from_db()
        self.assertEqual((answered.is_deleted, answered.body, answered.author), (True, "", None))
        self.assertTrue(GalleryComment.objects.filter(pk=reply_kept.pk).exists())
        filed.refresh_from_db()
        self.assertIsNone(filed.reporter)

    def test_account_deletion_erases_first(self):
        leaving = self.person()
        other_post = self.post(self.person())
        answered = gallery.add_comment(leaving, other_post, "Answered")
        gallery.add_comment(self.person(), other_post, "Reply", answered.pk)
        client = APIClient()
        client.force_authenticate(leaving)
        self.assertEqual(client.delete("/account").status_code, 204)
        answered.refresh_from_db()
        self.assertEqual((answered.body, answered.is_deleted), ("", True))

    def test_purge_erases_the_gallery(self):
        leaving = self.person()
        self.post(leaving)
        call_command("purge_user_pii", "--no-input", stdout=StringIO())
        self.assertFalse(GalleryPost.objects.exists())
