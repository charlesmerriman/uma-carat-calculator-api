"""Comments on a PvP guide, one thread per section, and thumbs-up on any
comment (guide-comments-plan.md, option A: a comment's parent is a post OR a
guide)."""
from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi import gallery, moderation_cards
from calculatorapi.models import GalleryComment, GalleryCommentVote, PvpGuide
from calculatorapi.tests.base import PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client
from calculatorapi.tests.test_gallery import GalleryFixture


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class GuideCommentTests(GalleryFixture):

    def setUp(self):
        super().setUp()
        self.guide = PvpGuide.objects.create(champions_meeting=self.cm, is_published=True)
        self.url = f"/pvp-guides/cm/{self.cm.id}/comments"
        self.reader = self.person()

    def client_for(self, user):
        client, _ = auth_client(user)
        return client

    def say(self, user, body, section="", parent=None):
        data = {"body": body, "section": section, **({"parent": parent} if parent else {})}
        return self.client_for(user).post(self.url, data, format="json")

    def test_each_section_is_its_own_thread(self):
        self.assertEqual(self.say(self.reader, "Why Pace first?", "pace").status_code, 201)
        self.assertEqual(self.say(self.reader, "Great overview").status_code, 201)
        pace = APIClient().get(self.url, {"section": "pace"}).json()
        whole = APIClient().get(self.url).json()
        self.assertEqual([c["body"] for c in pace["results"]], ["Why Pace first?"])
        self.assertEqual([c["body"] for c in whole["results"]], ["Great overview"])
        # A guide's comment has no team author to mark and no re-post to predate.
        self.assertFalse(pace["results"][0]["is_author"])
        self.assertFalse(pace["results"][0]["earlier_version"])
        # The guide carries the counts, so the tabs need no thread fetch.
        guide = APIClient().get(f"/pvp-guides/cm/{self.cm.id}").json()
        self.assertEqual(guide["comment_counts"], {"pace": 1, "": 1})

    def test_a_reply_stays_in_its_section(self):
        top = self.say(self.reader, "Question", "front").json()
        self.assertEqual(self.say(self.person(), "Answer", "front", top["id"]).status_code, 201)
        # The same comment id from another section is not a parent there.
        self.assertEqual(self.say(self.person(), "Wrong place", "pace", top["id"]).status_code, 400)

    def test_sections_are_the_running_styles_and_the_whole_guide(self):
        self.assertEqual(self.say(self.reader, "Hi", "everyone").status_code, 400)
        self.assertEqual(APIClient().get(self.url, {"section": "nope"}).status_code, 400)
        self.assertEqual(self.say(self.reader, "Runaway?", "runaway").status_code, 201)

    def test_a_draft_or_missing_guide_has_no_comments(self):
        PvpGuide.objects.filter(pk=self.guide.pk).update(is_published=False)
        self.assertEqual(APIClient().get(self.url).status_code, 404)
        self.assertEqual(self.say(self.reader, "Hi").status_code, 404)
        self.assertEqual(APIClient().get("/pvp-guides/loh/999/comments").status_code, 404)
        self.assertEqual(APIClient().get(f"/pvp-guides/xx/{self.cm.id}/comments").status_code, 404)

    def test_commenting_needs_what_the_gallery_needs(self):
        self.assertEqual(APIClient().post(self.url, {"body": "Hi"}, format="json").status_code, 401)
        nameless = self.person()
        nameless.display_name = ""
        nameless.save()
        self.assertEqual(self.say(nameless, "Hi").status_code, 400)

    def test_a_comment_has_exactly_one_parent(self):
        post = self.post(self.person())
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryComment.objects.create(post=post, guide=self.guide, author=self.reader, body="Both")
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryComment.objects.create(author=self.reader, body="Neither")
        # A post's comment never carries a guide section.
        with self.assertRaises(IntegrityError), transaction.atomic():
            GalleryComment.objects.create(post=post, section="front", author=self.reader, body="Odd")

    def test_staff_see_where_a_guide_comment_is(self):
        comment = gallery.add_comment(self.reader, None, "Hello", guide=self.guide, section="late")
        self.assertEqual(comment.link_path(), f"/app/pvp?event=cm-{self.cm.id}&style=late")
        self.assertIn("guide, Late", comment.where())
        overview = gallery.add_comment(self.reader, None, "Hello all", guide=self.guide)
        self.assertEqual(overview.link_path(), f"/app/pvp?event=cm-{self.cm.id}")
        # The moderation card links to the guide's section, not to a post.
        self.assertIn(f"/app/pvp?event=cm-{self.cm.id}&style=late", moderation_cards._links(comment))  # pylint: disable=protected-access


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class CommentVoteTests(GalleryFixture):

    def setUp(self):
        super().setUp()
        self.author = self.person()
        self.post_row = self.post(self.author)
        self.commenter = self.person()
        self.comment = gallery.add_comment(self.commenter, self.post_row, "Nice team")
        self.vote_url = f"/gallery/comments/{self.comment.pk}/vote"
        self.thread_url = f"/gallery/posts/{self.post_row.public_id}/comments"

    def client_for(self, user):
        client, _ = auth_client(user)
        return client

    def test_a_thumbs_up_counts_once_and_comes_back_off(self):
        voter = self.client_for(self.person())
        self.assertEqual(voter.put(self.vote_url).json(), {"score": 1, "my_vote": "up"})
        self.assertEqual(voter.put(self.vote_url).json()["score"], 1)
        self.client_for(self.author).put(self.vote_url)
        top = voter.get(self.thread_url).json()["results"][0]
        self.assertEqual((top["score"], top["my_vote"]), (2, "up"))
        # A guest sees the score, never who voted.
        stranger = APIClient().get(self.thread_url).json()["results"][0]
        self.assertEqual((stranger["score"], stranger["my_vote"]), (2, None))
        self.assertEqual(voter.delete(self.vote_url).json(), {"score": 1, "my_vote": "none"})

    def test_a_thumbs_down_switches_and_floors_at_zero(self):
        voter = self.client_for(self.person())
        voter.put(self.vote_url)
        down = voter.put(self.vote_url, {"value": "down"}, format="json")
        self.assertEqual(down.json(), {"score": 0, "my_vote": "down"})
        self.assertEqual(GalleryCommentVote.objects.count(), 1)

    def test_a_downvoted_comment_moves_to_the_end(self):
        later = gallery.add_comment(self.person(), self.post_row, "Second")
        self.client_for(self.person()).put(self.vote_url, {"value": "down"}, format="json")
        ids = [entry["id"] for entry in APIClient().get(self.thread_url).json()["results"]]
        self.assertEqual(ids, [later.pk, self.comment.pk])

    def test_not_on_your_own_comment_and_not_signed_out(self):
        self.assertEqual(self.client_for(self.commenter).put(self.vote_url).status_code, 403)
        self.assertEqual(APIClient().put(self.vote_url).status_code, 401)

    def test_a_hidden_comment_cannot_be_voted_on(self):
        GalleryComment.objects.filter(pk=self.comment.pk).update(is_hidden=True, hidden_reason="moderator")
        self.assertEqual(self.client_for(self.person()).put(self.vote_url).status_code, 404)

    def test_blocked_voters_do_not_count_and_erasing_an_account_takes_its_votes(self):
        voter = self.person()
        self.client_for(voter).put(self.vote_url)
        voter.publishing_blocked = True
        voter.save()
        self.assertEqual(APIClient().get(self.thread_url).json()["results"][0]["score"], 0)
        gallery.erase_users([voter])
        self.assertFalse(GalleryCommentVote.objects.exists())
