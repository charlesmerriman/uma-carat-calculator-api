"""GET /profile-cards/*: what a person's card shows, and every way it stays private."""

from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi import gallery
from calculatorapi.models import (
    GalleryComment, GalleryHiddenReason, GalleryPost, GalleryPostKind, PatreonSupporter, PatreonTier,
    Plan, PvpGuide, Uma, UserOshi, UserPlannedBanner,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    make_champions_meeting, make_step_up_banner, make_uma_banner, make_user,
)
from calculatorapi.views.profile_card import POSTS_SHOWN


def keys_in(value):
    """Every dict key anywhere in a payload."""
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in keys_in(child)}
    if isinstance(value, list):
        return {key for child in value for key in keys_in(child)}
    return set()


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class ProfileCardTests(CalculatorTestCase):  # pylint: disable=too-many-public-methods

    def setUp(self):
        self.guest = APIClient()
        self.cm = make_champions_meeting(name="Capricorn Cup", cm_number=21)
        self.author = make_user("user_author1")
        self.author.display_name = "Moonlit Trainer"
        self.author.save(update_fields=["display_name"])
        self.post = self.make_post(caption="Pace core")

    def make_post(self, author=None, caption="", cm=None):
        return GalleryPost.objects.create(
            author=author or self.author, kind=GalleryPostKind.PVP_TEAM,
            champions_meeting=cm or self.cm, caption=caption,
        )

    @staticmethod
    def hide(model, row):
        model.objects.filter(pk=row.pk).update(is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS)

    def card(self, post=None):
        return self.guest.get(f"/profile-cards/post/{(post or self.post).public_id}")

    def comment_card(self, comment):
        return self.guest.get(f"/profile-cards/comment/{comment.pk}")

    def favorite(self, position, name):
        uma = Uma.objects.create(name=name, image=f"umas/{position}.png")
        UserOshi.objects.create(user=self.author, uma=uma, position=position)
        return uma

    def make_supporter(self):
        tier = PatreonTier.objects.create(name="Senior Class", order=1)
        PatreonSupporter.objects.create(
            linked_user=self.author, display_name="x", email="a@example.invalid", is_active=True, tier=tier,
        )

    def test_the_whole_shape_and_nothing_about_the_account(self):
        self.favorite(0, "Oguri Cap")
        body = self.card().json()
        self.assertEqual(set(body), {"favorites", "posts", "plans"})
        self.assertEqual(set(body["posts"][0]), {"public_id", "caption", "score", "event"})
        raw = self.card().content.decode()
        self.assertNotIn("user_author1", raw)
        self.assertNotIn("a@example", raw)
        # The gallery's rule: "label", never "name", so a team's name can't slip out.
        self.assertNotIn("name", keys_in(body))
        self.assertFalse({"user", "user_id", "username", "handle", "email", "plan"} & keys_in(body))

    def test_a_guest_can_open_a_card(self):
        self.assertEqual(self.card().status_code, 200)

    def test_every_favorite_the_tier_covers_in_order(self):
        self.make_supporter()
        first = self.favorite(0, "Oguri Cap")
        second = self.favorite(1, "Gold Ship")
        favorites = self.card().json()["favorites"]
        self.assertEqual([row["uma"] for row in favorites], [first.id, second.id])
        self.assertEqual(favorites[0]["label"], "Oguri Cap")

    def test_a_favorite_past_the_slots_stays_private(self):
        # No pledge: one slot, so a lapsed supporter's second pick isn't shown.
        self.favorite(0, "Oguri Cap")
        self.favorite(1, "Gold Ship")
        self.assertEqual([row["label"] for row in self.card().json()["favorites"]], ["Oguri Cap"])

    def test_a_favorite_with_no_art_is_left_off(self):
        uma = Uma.objects.create(name="No Art", image="")
        UserOshi.objects.create(user=self.author, uma=uma, position=0)
        self.assertEqual(self.card().json()["favorites"], [])

    def test_their_visible_posts_newest_first_and_capped(self):
        other = make_user("user_other1")
        self.make_post(author=other, caption="Not theirs")
        hidden = self.make_post(cm=make_champions_meeting(name="Hidden", cm_number=30), caption="Hidden")
        self.hide(GalleryPost, hidden)
        newest = self.make_post(cm=make_champions_meeting(name="Newer", cm_number=22), caption="Newest")
        posts = self.card().json()["posts"]
        self.assertEqual([row["public_id"] for row in posts], [newest.public_id, self.post.public_id])
        self.assertEqual(posts[1]["event"], {"kind": "cm", "id": self.cm.id, "label": "Capricorn Cup"})

        for number in range(40, 40 + POSTS_SHOWN):
            self.make_post(cm=make_champions_meeting(name=f"CM {number}", cm_number=number))
        self.assertEqual(len(self.card().json()["posts"]), POSTS_SHOWN)

    def test_plans_stay_off_until_the_person_turns_them_on(self):
        plan = Plan.objects.filter(user=self.author).first() or Plan.objects.create(user=self.author, name="Secret")
        plan.is_shared = True
        plan.save(update_fields=["is_shared"])
        self.assertEqual(self.card().json()["plans"], [])

    def test_the_switch_lists_shared_plans_only_with_totals_only(self):
        self.author.show_shared_plans_on_card = True
        self.author.save(update_fields=["show_shared_plans_on_card"])
        shared = Plan.objects.create(user=self.author, name="Secret plan name", is_shared=True)
        Plan.objects.create(user=self.author, name="Private plan")
        UserPlannedBanner.objects.create(
            user=self.author, plan=shared, banner_uma=make_uma_banner(), number_of_pulls=200, reserved_copies=0,
        )
        UserPlannedBanner.objects.create(
            user=self.author, plan=shared, banner_step_up=make_step_up_banner(), number_of_pulls=4, reserved_copies=0,
        )
        response = self.card()
        self.assertEqual(response.json()["plans"], [
            {"public_id": shared.public_id, "row_count": 2, "pull_count": 200, "step_count": 4},
        ])
        self.assertNotIn("Secret plan name", response.content.decode())
        self.assertNotIn("Private plan", response.content.decode())

    def test_a_score_never_reads_below_zero(self):
        self.assertEqual(self.card().json()["posts"][0]["score"], 0)

    def test_a_blocked_author_has_no_card(self):
        unknown = self.guest.get("/profile-cards/post/nothere1")
        self.author.publishing_blocked = True
        self.author.save(update_fields=["publishing_blocked"])
        self.assertEqual(self.card().status_code, 404)
        self.assertEqual(self.card().json(), unknown.json())

    def test_a_hidden_post_has_no_card(self):
        self.hide(GalleryPost, self.post)
        self.assertEqual(self.card().status_code, 404)

    def test_erasing_an_account_takes_the_card_with_it(self):
        gallery.erase_users([self.author])
        self.assertEqual(self.card().status_code, 404)

    def test_a_comment_opens_its_writers_card(self):
        writer = make_user("user_writer1")
        comment = GalleryComment.objects.create(post=self.post, author=writer, body="Nice")
        self.make_post(author=writer, caption="Writer's team")
        posts = self.comment_card(comment).json()["posts"]
        self.assertEqual([row["caption"] for row in posts], ["Writer's team"])

    def test_a_comment_on_a_guide_opens_a_card(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm, is_published=True)
        comment = GalleryComment.objects.create(guide=guide, author=self.author, body="Thanks")
        self.assertEqual(self.comment_card(comment).status_code, 200)

    def test_a_comment_nobody_can_see_has_no_card(self):
        comment = GalleryComment.objects.create(post=self.post, author=self.author, body="Nice")
        unknown = self.guest.get("/profile-cards/comment/999999")
        self.hide(GalleryComment, comment)
        self.assertEqual(self.comment_card(comment).status_code, 404)
        GalleryComment.objects.filter(pk=comment.pk).update(is_hidden=False, hidden_reason="", is_deleted=True)
        self.assertEqual(self.comment_card(comment).json(), unknown.json())

    def test_a_comment_on_a_hidden_post_has_no_card(self):
        writer = make_user("user_writer2")
        comment = GalleryComment.objects.create(post=self.post, author=writer, body="Nice")
        self.hide(GalleryPost, self.post)
        self.assertEqual(self.comment_card(comment).status_code, 404)

    def test_a_comment_on_an_unpublished_guide_has_no_card(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm, is_published=False)
        comment = GalleryComment.objects.create(guide=guide, author=self.author, body="Draft")
        self.assertEqual(self.comment_card(comment).status_code, 404)

    def test_the_switch_is_the_persons_own(self):
        client = APIClient()
        client.force_authenticate(self.author)
        self.assertIs(client.get("/account").data["show_shared_plans_on_card"], False)
        response = client.patch("/account", {"show_shared_plans_on_card": True}, format="json")
        self.assertIs(response.data["show_shared_plans_on_card"], True)
