"""The admin-editable pages and FAQ: the seed, the endpoint, the admin, and the rebuild button."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from calculatorapi import digitalocean_api, site_content_seed
from calculatorapi.models import CustomUser, FaqCategory, FaqItem, SitePage
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import FakeResponse, make_user


class SeedTests(CalculatorTestCase):
    """The migration's seed step: rows exist after migrate, and a rerun changes nothing."""

    def test_migrate_seeded_every_page_and_faq_row(self):
        # The test database was built by running the migrations, so the seed
        # has already run once here.
        self.assertEqual(
            set(SitePage.objects.values_list("slug", flat=True)),
            {"about", "carat-income-guide"},
        )
        categories = list(FaqCategory.objects.all())
        self.assertEqual(
            [c.slug for c in categories],
            ["using-the-calculator", "the-numbers", "account-and-privacy", "about-the-site"],
        )
        self.assertEqual(FaqItem.objects.count(), 16)

    def test_seed_body_matches_the_file(self):
        page = SitePage.objects.get(slug="about")
        expected = next(r for r in site_content_seed.load_pages() if r["slug"] == "about")
        self.assertEqual(page.body, expected["body"])
        self.assertEqual(page.title, "About")
        self.assertTrue(page.meta_description)

    def test_seed_rerun_creates_nothing_and_keeps_edits(self):
        page = SitePage.objects.get(slug="about")
        page.body = "Edited in the admin.\n"
        page.save()
        item = FaqItem.objects.get(slug="do-i-need-an-account")
        item.answer = "Edited answer."
        item.save()

        site_content_seed.seed(SitePage, FaqCategory, FaqItem)

        self.assertEqual(SitePage.objects.count(), 2)
        self.assertEqual(FaqItem.objects.count(), 16)
        self.assertEqual(SitePage.objects.get(slug="about").body, "Edited in the admin.\n")
        self.assertEqual(FaqItem.objects.get(slug="do-i-need-an-account").answer, "Edited answer.")

    def test_seed_files_have_no_em_dashes(self):
        # The copy rule applies to text in the repo. Text the team writes in
        # the admin is theirs and is not checked.
        for row in site_content_seed.load_pages():
            self.assertNotIn("—", row["body"], row["slug"])
        for category in site_content_seed.load_faq():
            for item in category["items"]:
                self.assertNotIn("—", item["answer"], item["slug"])
                self.assertNotIn("—", item["question"], item["slug"])


class EndpointTests(CalculatorTestCase):
    """GET /site-content: public, one response, ordered as the admin orders it."""

    def get(self):
        return self.client.get(reverse("site-content"))

    def test_is_public_and_carries_both_halves(self):
        res = self.get()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.json()), {"pages", "faq"})

    def test_page_shape(self):
        pages = {p["slug"]: p for p in self.get().json()["pages"]}
        self.assertEqual(set(pages), {"about", "carat-income-guide"})
        about = pages["about"]
        self.assertEqual(
            set(about), {"slug", "title", "meta_description", "body", "updated_at"}
        )
        self.assertTrue(about["body"].startswith("The Uma Musume Carat Calculator"))

    def test_faq_shape_and_ordering(self):
        faq = self.get().json()["faq"]
        self.assertEqual(
            [c["slug"] for c in faq],
            ["using-the-calculator", "the-numbers", "account-and-privacy", "about-the-site"],
        )
        first = faq[0]
        self.assertEqual(set(first), {"slug", "title", "items"})
        self.assertEqual(
            set(first["items"][0]), {"slug", "question", "answer", "show_on_homepage"}
        )
        self.assertEqual(first["items"][0]["slug"], "what-does-this-calculator-actually-do")

    def test_ordering_follows_order_fields_not_ids(self):
        # Move the last category to the front and the last question of the
        # first category to the top of it.
        FaqCategory.objects.filter(slug="using-the-calculator").update(order=9)
        FaqCategory.objects.filter(slug="about-the-site").update(order=0)
        FaqItem.objects.filter(slug="what-does-this-calculator-actually-do").update(order=9)
        FaqItem.objects.filter(slug="what-do-the-colours-on-the-pulls").update(order=0)

        faq = self.get().json()["faq"]
        self.assertEqual(faq[0]["slug"], "about-the-site")
        using = next(c for c in faq if c["slug"] == "using-the-calculator")
        self.assertEqual(using["items"][0]["slug"], "what-do-the-colours-on-the-pulls")

    def test_show_on_homepage_flags_the_seeded_three(self):
        faq = self.get().json()["faq"]
        flagged = [i["slug"] for c in faq for i in c["items"] if i["show_on_homepage"]]
        self.assertEqual(
            flagged,
            [
                "do-i-need-an-account",
                "why-does-this-not-match-what-i",
                "is-this-site-affiliated-with-cygames",
            ],
        )

    def test_an_admin_edit_is_served_at_once(self):
        page = SitePage.objects.get(slug="about")
        page.body = "New words.\n"
        page.save()
        pages = {p["slug"]: p for p in self.get().json()["pages"]}
        self.assertEqual(pages["about"]["body"], "New words.\n")

    def test_writes_are_refused(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                res = getattr(self.client, method)(reverse("site-content"))
                self.assertEqual(res.status_code, 405)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class AdminTests(CalculatorTestCase):
    """The Pages and FAQ admin pages render, and Pages is change-only."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = CustomUser.objects.create_superuser(username="boss", password="x")

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_pages_render(self):
        page = SitePage.objects.get(slug="about")
        category = FaqCategory.objects.first()
        for url in (
            reverse("admin:calculatorapi_sitepage_changelist"),
            reverse("admin:calculatorapi_sitepage_change", args=[page.pk]),
            reverse("admin:calculatorapi_faqcategory_changelist"),
            reverse("admin:calculatorapi_faqcategory_change", args=[category.pk]),
            reverse("admin:calculatorapi_faqcategory_add"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_pages_changelist_carries_the_rebuild_button(self):
        res = self.client.get(reverse("admin:calculatorapi_sitepage_changelist"))
        self.assertContains(res, "Rebuild website")
        self.assertContains(res, reverse("admin:calculatorapi_sitepage_rebuild_website"))

    def test_pages_refuse_add_and_delete_even_for_a_superuser(self):
        page = SitePage.objects.get(slug="about")
        self.assertEqual(
            self.client.get(reverse("admin:calculatorapi_sitepage_add")).status_code, 403
        )
        res = self.client.post(
            reverse("admin:calculatorapi_sitepage_delete", args=[page.pk]), {"post": "yes"}
        )
        self.assertEqual(res.status_code, 403)
        self.assertTrue(SitePage.objects.filter(slug="about").exists())

    def test_content_editors_get_the_new_models(self):
        call_command("create_content_editor_group", stdout=StringIO())
        codenames = set(
            Group.objects.get(name="Content editors")
            .permissions.values_list("codename", flat=True)
        )
        for codename in (
            "change_sitepage", "view_sitepage",
            "add_faqcategory", "change_faqcategory", "delete_faqcategory",
            "add_faqitem", "change_faqitem", "delete_faqitem",
        ):
            self.assertIn(codename, codenames)


@override_settings(STORAGES=PLAIN_TEST_STORAGES, DO_API_TOKEN="tok", DO_APP_ID="app-1")
class RebuildButtonTests(CalculatorTestCase):
    """The "Rebuild website" view: who may press it, and the three outcomes."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = make_user("editor", is_staff=True)
        cls.visitor = make_user("visitor")

    def url(self):
        return reverse("admin:calculatorapi_sitepage_rebuild_website")

    def messages_of(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_non_staff_is_sent_to_the_admin_login(self):
        self.client.force_login(self.visitor)
        with patch("calculatorapi.digitalocean_api.requests.request") as request:
            res = self.client.post(self.url())
        self.assertEqual(res.status_code, 302)
        self.assertIn(reverse("admin:login"), res["Location"])
        request.assert_not_called()

    def test_get_does_not_rebuild(self):
        self.client.force_login(self.staff)
        with patch("calculatorapi.digitalocean_api.requests.request") as request:
            res = self.client.get(self.url())
        self.assertRedirects(res, reverse("admin:index"))
        request.assert_not_called()

    @override_settings(DO_API_TOKEN="", DO_APP_ID="")
    def test_unconfigured_explains_instead_of_failing(self):
        self.client.force_login(self.staff)
        with patch("calculatorapi.digitalocean_api.requests.request") as request:
            res = self.client.post(self.url(), follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("not set up on this server", " ".join(self.messages_of(res)))
        request.assert_not_called()

    def test_in_progress_deployment_stops_a_second_one(self):
        self.client.force_login(self.staff)
        in_progress = FakeResponse({"deployments": [{"id": "d1", "phase": "BUILDING"}]})
        with patch(
            "calculatorapi.digitalocean_api.requests.request", return_value=in_progress
        ) as request:
            res = self.client.post(self.url(), follow=True)
        self.assertIn("already running", " ".join(self.messages_of(res)))
        # One GET to look, no POST to start.
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")

    def test_success_posts_a_forced_build(self):
        self.client.force_login(self.staff)
        responses = [
            FakeResponse({"deployments": [{"id": "d0", "phase": "ACTIVE"}]}),
            FakeResponse({"deployment": {"id": "d2"}}, status_code=201),
        ]
        with patch(
            "calculatorapi.digitalocean_api.requests.request", side_effect=responses
        ) as request:
            res = self.client.post(self.url(), follow=True)
        self.assertIn("Rebuild started", " ".join(self.messages_of(res)))
        self.assertEqual(request.call_count, 2)
        method, url = request.call_args_list[1].args
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{digitalocean_api.API_BASE}/apps/app-1/deployments")
        self.assertEqual(request.call_args_list[1].kwargs["json"], {"force_build": True})
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer tok"
        )

    def test_api_error_is_a_message_not_a_500(self):
        self.client.force_login(self.staff)
        with patch(
            "calculatorapi.digitalocean_api.requests.request",
            return_value=FakeResponse(None, status_code=401),
        ):
            res = self.client.post(self.url(), follow=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn("DigitalOcean answered 401", " ".join(self.messages_of(res)))

    def test_no_deployments_yet_counts_as_idle(self):
        with patch(
            "calculatorapi.digitalocean_api.requests.request",
            return_value=FakeResponse({"deployments": []}),
        ):
            self.assertFalse(digitalocean_api.deployment_in_progress())
