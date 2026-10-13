"""GET /pvp-catalogue: what it carries, what it leaves out, and its cache."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from calculatorapi import pvp_catalogue_cache
from calculatorapi.models import (
    Plan,
    Skill,
    SupportCard,
    SupportCardSkill,
    Uma,
    UmaSkill,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_user


def make_skill(name, game_id, description="Does a thing."):
    return Skill.objects.create(
        name=name, game_id=game_id, description=description,
        rarity=1, group_id=game_id // 10, tier=1, icon_id=10011,
    )


class CatalogueTests(CalculatorTestCase):

    def setUp(self):
        self.on_global = make_skill("Groundwork", 200011)
        self.jp_only = make_skill("Not Yet", 200021, description="")
        self.uma = Uma.objects.create(
            name="Maruzensky", game_id=100401, image="umas/100401.png",
            apt_front=8, apt_long=4,
        )
        Uma.objects.create(name="No Picture", game_id=100501)
        Uma.objects.create(name="(All)", image="umas/all.png")
        self.card = SupportCard.objects.create(
            name="Kitasan Black", game_id=30028, image="support_cards/30028.png",
            card_type="speed",
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
        ):
            self.fetch()
            write()
            self.assertIsNone(pvp_catalogue_cache.read())

    def test_a_plan_save_keeps_it(self):
        self.fetch()
        Plan.objects.create(user=make_user(), name="Main")
        self.assertIsNotNone(pvp_catalogue_cache.read())
