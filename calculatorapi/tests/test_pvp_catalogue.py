"""GET /pvp-catalogue: what it carries, what it leaves out, and its cache."""

import datetime
import importlib

from django.apps import apps as django_apps
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from calculatorapi import pvp_catalogue_cache
from calculatorapi.models import (
    Plan,
    Skill,
    SkillAlias,
    SupportCard,
    SupportCardSkill,
    Uma,
    UmaKind,
    UmaSkill,
)
from calculatorapi.models.uma import uma_kind
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import RELEASED_ON_GLOBAL, make_user


def make_skill(name, game_id, description="Does a thing.", availability="global"):
    return Skill.objects.create(
        name=name, game_id=game_id, description=description, availability=availability,
        rarity=1, group_id=game_id // 10, tier=1, icon_id=10011,
    )


# Picture URLs go through default_storage, which is Spaces unless told
# otherwise; CI has no bucket, so it would fail there and pass locally.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class CatalogueTests(CalculatorTestCase):

    def setUp(self):
        self.on_global = make_skill("Groundwork", 200011)
        self.jp_only = make_skill("Not Yet", 200021, availability="jp")
        self.uma = Uma.objects.create(
            name="Maruzensky", game_id=100401, image="umas/100401.png",
            apt_front=8, apt_long=4, global_release_at=RELEASED_ON_GLOBAL,
        )
        Uma.objects.create(name="No Picture", game_id=100501)
        Uma.objects.create(name="(All)", image="umas/all.png")
        self.card = SupportCard.objects.create(
            name="Kitasan Black", game_id=30028, image="support_cards/30028.png",
            card_type="speed", global_release_at=RELEASED_ON_GLOBAL,
        )
        SupportCard.objects.create(name="Blank Card", game_id=30029)
        UmaSkill.objects.create(uma=self.uma, skill=self.on_global, source="unique", level=1)
        UmaSkill.objects.create(uma=self.uma, skill=self.jp_only, source="innate")
        SupportCardSkill.objects.create(support_card=self.card, skill=self.on_global, source="hint")
        SupportCardSkill.objects.create(support_card=self.card, skill=self.jp_only, source="event")

    def fetch(self):
        response = APIClient().get("/pvp-catalogue")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_trainees_only_but_favourites_keep_the_pictures(self):
        """A casual outfit and an NPC are pictures, not racers: out of every PvP
        picker, still on offer as a favourite (GET /umas)."""
        Uma.objects.create(name="Special Week (Casual)", game_id=901001, image="umas/901001.png")
        Uma.objects.create(name="Happy Meek (NPC)", game_id=200101, image="umas/200101.png")
        Uma.objects.create(name="Tazuna Hayakawa (NPC)", game_id=800101, image="umas/800101.png")
        self.assertEqual([uma["name"] for uma in self.fetch()["umas"]], ["Maruzensky"])
        favourites = {uma["name"] for uma in APIClient().get("/umas").json()}
        self.assertTrue({"Special Week (Casual)", "Happy Meek (NPC)", "Tazuna Hayakawa (NPC)"} <= favourites)

    def test_kind_comes_from_the_game_id(self):
        kinds = {game_id: uma_kind(game_id) for game_id in (None, 100401, 199999, 200101, 800101, 901001)}
        self.assertEqual(kinds, {
            None: UmaKind.TRAINEE, 100401: UmaKind.TRAINEE, 199999: UmaKind.TRAINEE,
            200101: UmaKind.NPC, 800101: UmaKind.NPC, 901001: UmaKind.CASUAL,
        })

    def test_shape(self):
        body = self.fetch()
        self.assertEqual([uma["name"] for uma in body["umas"]], ["Maruzensky"])
        uma = body["umas"][0]
        self.assertEqual(uma["character_id"], 1004)
        self.assertFalse(uma["is_variant"])
        self.assertEqual(uma["apt"]["front"], 8)
        self.assertEqual(uma["apt"]["long"], 4)
        self.assertIsNone(uma["apt"]["dirt"])
        self.assertTrue(uma["image"].endswith("umas/100401.png"))
        self.assertEqual([card["name"] for card in body["support_cards"]], ["Kitasan Black"])
        self.assertEqual(body["support_cards"][0]["card_type"], "speed")
        self.assertEqual(body["support_cards"][0]["group_members"], [])

    def test_a_card_says_when_it_came_out(self):
        self.assertEqual(self.fetch()["support_cards"][0]["released_at"], "2025-07-01T00:00:00+00:00")

    def test_a_card_dated_ahead_is_not_out_and_shows_no_date(self):
        # The client's 2099 "not released here" mark, or a card shipped early.
        SupportCard.objects.filter(pk=self.card.pk).update(
            global_release_at=datetime.datetime(2099, 1, 1, tzinfo=datetime.timezone.utc)
        )
        pvp_catalogue_cache.invalidate()
        card = self.fetch()["support_cards"][0]
        self.assertEqual((card["released_at"], card["on_global"], card["skills"]), (None, False, []))

    def test_a_card_sends_its_borderless_art_or_null(self):
        # Never a fallback to `image`: the bordered picture has a frame of its
        # own, and the deck draws it unframed (models/support_card.py).
        self.assertIsNone(self.fetch()["support_cards"][0]["image_borderless"])
        SupportCard.objects.filter(pk=self.card.pk).update(
            image_borderless="support_cards_borderless/30028-Kitasan-Black-spd.webp"
        )
        pvp_catalogue_cache.invalidate()
        card = self.fetch()["support_cards"][0]
        self.assertTrue(card["image_borderless"].endswith("support_cards_borderless/30028-Kitasan-Black-spd.webp"))
        self.assertTrue(card["image"].endswith("support_cards/30028.png"))

    def test_a_placeholder_says_so(self):
        SupportCard.objects.create(
            name="SSR Speed", game_id=31000, image="support_cards/31000.png",
            card_type="speed", rarity=3, is_placeholder=True,
        )
        flags = {card["name"]: card["placeholder"] for card in self.fetch()["support_cards"]}
        self.assertEqual(flags, {"Kitasan Black": False, "SSR Speed": True})

    def test_the_placeholder_migration_ticks_only_exact_matches(self):
        migration = importlib.import_module("calculatorapi.migrations.0121_support_card_placeholder")
        exact = SupportCard.objects.create(name="SSR Speed", game_id=31000)
        renamed = SupportCard.objects.create(name="Any SSR Speed", game_id=32000)
        migration.tick_placeholders(django_apps, None)
        exact.refresh_from_db()
        renamed.refresh_from_db()
        self.assertTrue(exact.is_placeholder)
        self.assertFalse(renamed.is_placeholder)
        self.assertFalse(SupportCard.objects.get(name="Kitasan Black").is_placeholder)

    def test_a_group_card_carries_its_umas(self):
        SupportCard.objects.create(
            name="Team Sirius", game_id=30081, image="support_cards/30081.png",
            card_type="group", group_members=["Silence Suzuka", "Special Week"],
        )
        cards = {card["name"]: card for card in self.fetch()["support_cards"]}
        self.assertEqual(cards["Team Sirius"]["group_members"], ["Silence Suzuka", "Special Week"])

    def test_on_global_is_the_imported_flag_not_having_links(self):
        # A JP card has skill links now (from the JP snapshot), and is still
        # not on global; its links never reach the builder.
        jp_only = SupportCard.objects.create(
            name="JP Card", game_id=30200, image="support_cards/30200.png",
        )
        SupportCardSkill.objects.create(support_card=jp_only, skill=self.on_global, source="hint")
        body = self.fetch()
        flags = {card["name"]: card["on_global"] for card in body["support_cards"]}
        self.assertEqual(flags, {"Kitasan Black": True, "JP Card": False})
        self.assertTrue(body["umas"][0]["on_global"])
        jp_card = next(card for card in body["support_cards"] if card["name"] == "JP Card")
        self.assertEqual(jp_card["skills"], [])

    def test_skills_not_on_global_are_left_out_with_their_links(self):
        body = self.fetch()
        self.assertEqual([skill["name"] for skill in body["skills"]], ["Groundwork"])
        self.assertEqual(
            body["umas"][0]["skills"],
            [{"skill": self.on_global.id, "source": "unique", "level": 1}],
        )
        self.assertEqual(
            body["support_cards"][0]["skills"],
            [{"skill": self.on_global.id, "source": "hint"}],
        )

    def test_a_skill_carries_every_other_name_for_search(self):
        Skill.objects.filter(pk=self.on_global.pk).update(translated_name="Ground Work", jp_name="下準備")
        SkillAlias.objects.create(skill=self.on_global, name="Foundations")  # GameTora's, say
        SkillAlias.objects.create(skill=self.on_global, name="Groundwork")  # its shown name: left out
        [skill] = self.fetch()["skills"]
        self.assertEqual(skill["name"], "Groundwork")
        self.assertEqual(skill["other_names"], ["Ground Work", "Foundations", "下準備"])

    def test_a_second_fetch_is_served_from_the_cache(self):
        self.fetch()
        self.assertIsNotNone(pvp_catalogue_cache.read())
        with CaptureQueriesContext(connection) as queries:
            self.fetch()
        self.assertEqual(len(queries), 0)

    def test_a_catalogue_write_drops_it(self):
        for write in (
            lambda: self.on_global.save(update_fields=["name"]),
            lambda: self.uma.save(update_fields=["name"]),
            self.card.save,
            lambda: UmaSkill.objects.filter(source="innate").delete(),
            lambda: SkillAlias.objects.create(skill=self.on_global, name="Another Name"),
        ):
            self.fetch()
            write()
            self.assertIsNone(pvp_catalogue_cache.read())

    def test_a_plan_save_keeps_it(self):
        self.fetch()
        Plan.objects.create(user=make_user(), name="Main")
        self.assertIsNotNone(pvp_catalogue_cache.read())
