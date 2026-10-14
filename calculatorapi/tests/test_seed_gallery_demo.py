"""seed_gallery_demo: the LOCAL-ONLY fake gallery players."""

import datetime
import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from calculatorapi import benefits, gallery_ranking, public_identity
from calculatorapi.management.commands import seed_gallery_demo
from calculatorapi.models import (
    CustomUser,
    GalleryComment,
    GalleryPost,
    GalleryReport,
    PatreonSupporter,
    PatreonTier,
    SupportCard,
    Uma,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import make_champions_meeting, make_user


# Picture URLs go through default_storage, which is Spaces unless told
# otherwise; CI has no bucket, so it would fail there and pass locally.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class SeedGalleryDemoTests(CalculatorTestCase):

    def setUp(self):
        now = timezone.now()
        self.previous = make_champions_meeting(
            "Earlier CM", cm_number=1,
            global_start_date=now - datetime.timedelta(days=30),
            global_end_date=now - datetime.timedelta(days=23),
        )
        self.current = make_champions_meeting("Current CM", cm_number=2)
        for order, name in enumerate(("Senior Class", "Classic Class", "Junior Class"), start=1):
            PatreonTier.objects.create(name=name, order=order)
        for n in range(1, 6):
            Uma.objects.create(name=f"Uma {n}", game_id=100001 + n * 100, image=f"umas/{n}.png")
        for n in range(1, 9):
            SupportCard.objects.create(name=f"Card {n}", game_id=30000 + n, image=f"cards/{n}.png")
        # Real data that the command must never touch.
        self.real = make_user("realperson")
        self.real_supporter = PatreonSupporter.objects.create(
            display_name="Real patron", patreon_user_id="real-1", linked_user=self.real,
            tier=PatreonTier.objects.get(order=1),
        )

    def seed(self, *args):
        out = StringIO()
        call_command("seed_gallery_demo", *args, stdout=out)
        return out.getvalue()

    def demo_users(self):
        return CustomUser.objects.filter(username__startswith=seed_gallery_demo.USERNAME_PREFIX)

    def test_refuses_anything_but_sqlite(self):
        with mock.patch("calculatorapi.content_snapshot.connection") as connection:
            connection.vendor = "postgresql"
            with self.assertRaises(CommandError):
                self.seed()
        self.assertFalse(self.demo_users().exists())

    def test_twelve_accounts_most_without_a_pledge(self):
        self.seed()
        users = list(self.demo_users())
        self.assertEqual(len(users), 12)
        self.assertEqual(sum(1 for user in users if not benefits.is_supporter(user)), 8)
        looks = {public_identity.poster_block(user)["gallery_style"] for user in users}
        self.assertEqual(looks, {"ssr", "sr", "rose", None})
        self.assertFalse(any(user.has_usable_password() for user in users))

    def test_every_post_is_a_legal_team(self):
        self.seed()
        posts = GalleryPost.objects.all()
        self.assertEqual(posts.filter(champions_meeting=self.current).count(), 12)
        self.assertEqual(posts.filter(champions_meeting=self.previous).count(), 3)
        for post in posts:
            umas = Uma.objects.in_bulk([member["uma"] for member in post.team_snapshot])
            self.assertEqual(len(umas), 3)
            self.assertEqual(len({uma.character_id for uma in umas.values()}), 3)
            for member in post.team_snapshot:
                self.assertEqual([card["slot"] for card in member["cards"]], [1, 2, 3, 4, 5, 6])
                self.assertEqual(len({card["support_card"] for card in member["cards"]}), 6)

    def test_the_three_sorts_disagree_and_every_case_shows(self):
        self.seed()
        posts = GalleryPost.objects.filter(champions_meeting=self.current)
        firsts = {gallery_ranking.ranked(posts, sort)[0].pk for sort in gallery_ranking.SORTS}
        self.assertEqual(len(firsts), 3)
        comments = GalleryComment.objects.all()
        self.assertTrue(comments.filter(is_deleted=True, replies__isnull=False).exists())
        self.assertTrue(comments.filter(is_hidden=True).exists())
        self.assertTrue(comments.filter(edited_at__isnull=False).exists())
        self.assertEqual(GalleryReport.objects.filter(resolved_at__isnull=True).count(), 3)
        moon = posts.get(author__display_name="MooncakeMile")
        self.assertTrue(comments.filter(post=moon, created_at__lt=moon.content_updated_at).exists())

    def test_running_twice_gives_the_same_gallery(self):
        def gallery_state():
            return sorted(
                (post.author.display_name, post.champions_meeting_id, json.dumps(post.team_snapshot))
                for post in GalleryPost.objects.select_related("author")
            )

        self.seed()
        first = gallery_state()
        self.seed()
        self.assertEqual(self.demo_users().count(), 12)
        self.assertEqual(gallery_state(), first)

    def test_clear_removes_only_the_demo_data(self):
        self.seed()
        self.seed("--clear")
        self.assertFalse(self.demo_users().exists())
        self.assertFalse(GalleryPost.objects.exists())
        self.assertFalse(PatreonSupporter.objects.filter(patreon_user_id__startswith="demo-").exists())
        self.real_supporter.refresh_from_db()
        self.assertEqual(self.real_supporter.linked_user, self.real)

    def test_missing_tiers_mean_no_pledges(self):
        PatreonTier.objects.all().delete()
        output = self.seed()
        self.assertIn("no pledge", output)
        self.assertFalse(PatreonSupporter.objects.filter(patreon_user_id__startswith="demo-").exists())
