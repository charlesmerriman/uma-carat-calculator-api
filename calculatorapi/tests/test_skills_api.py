"""GET /skills and GET /skills/<game_id>, and the skill fields a guide now sends."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from calculatorapi.models import (
    PvpGuide, PvpSkillRecommendation, Skill, SkillAlias, SkillEvolutionCondition, SupportCard,
    SupportCardSkill, Uma, UmaSkill,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import make_user
from calculatorapi.tests.test_pvp_guides import make_champions_meeting, url

SPEED = [{"precondition": "", "condition": "phase_random==1", "duration": 30000,
          "cooldown": 5000000, "abilities": [{"type": 27, "value": 1500, "value_usage": 1,
                                              "target_type": 1, "target_value": 0}]}]


def make(game_id, **fields):
    values = {"name": f"Skill {game_id}", "description": "Does a thing.", "rarity": 1,
              "group_id": game_id // 10, "tier": 1, "icon_id": 10011}
    values.update(fields)
    return Skill.objects.create(game_id=game_id, **values)


def editor_client():
    """The skills work is shelved to guide editors (staff) for now."""
    client = APIClient()
    client.force_authenticate(make_user("editor", is_staff=True))
    return client


class SkillsShelvedTests(CalculatorTestCase):
    """Shelved (owner, 2026-10-23): nobody but a guide editor knows it exists."""

    def test_the_routes_are_a_404_to_everyone_else(self):
        make(201351, name="Gourmand")
        for client in (APIClient(), APIClient()):
            self.assertEqual(client.get("/skills").status_code, 404)
            self.assertEqual(client.get("/skills/201351").status_code, 404)
            client.force_authenticate(make_user(f"player{id(client)}"))
            self.assertEqual(client.get("/skills").status_code, 404)

    def test_a_guide_shows_a_player_global_skills_only(self):
        cm = make_champions_meeting()
        guide = PvpGuide.objects.create(champions_meeting=cm, is_published=True,
                                        short_description="[[Fan Skill]] and [[Gourmand]]")
        fan = make(200999, name="", translated_name="Fan Skill", availability="jp")
        gourmand = make(201351, name="Gourmand")
        for skill in (fan, gourmand):
            PvpSkillRecommendation.objects.create(guide=guide, style="front", category="speed", skill=skill)
        body = APIClient().get(url("cm", cm.id)).json()
        self.assertEqual([row["skill"]["name"] for row in body["skill_recommendations"]], ["Gourmand"])
        self.assertEqual(set(body["skill_mentions"]), {"gourmand"})
        # A guide editor sees both, as the site's preview of the shelved work.
        body = editor_client().get(url("cm", cm.id)).json()
        self.assertEqual(len(body["skill_recommendations"]), 2)
        self.assertEqual(set(body["skill_mentions"]), {"fan skill", "gourmand"})


class SkillListTests(CalculatorTestCase):
    def setUp(self):
        self.client = editor_client()
        self.gourmand = make(201351, name="Gourmand", rarity=2, tier=2, translated_name="Glutton",
                             jp_name="食いしん坊")
        self.announced = make(110681, name="Bring on the Banquet!", availability="announced")
        self.fan = make(200999, name="", description="", translated_name="Fan Skill",
                        translated_description="Fan text.", availability="jp")
        self.nameless = make(200998, name="", availability="jp", jp_name="名前だけ")
        SkillAlias.objects.create(skill=self.gourmand, name="Old Gourmand")

    def test_lists_every_skill_with_an_english_name_by_that_name(self):
        rows = self.client.get("/skills").json()
        self.assertEqual([row["name"] for row in rows],
                         ["Bring on the Banquet!", "Fan Skill", "Gourmand"])
        fan = rows[1]
        self.assertEqual((fan["availability"], fan["uses_translation"]), ("jp", True))
        self.assertNotIn(self.nameless.game_id, [row["game_id"] for row in rows])

    def test_every_other_name_is_there_for_search(self):
        gourmand = next(row for row in self.client.get("/skills").json() if row["name"] == "Gourmand")
        self.assertEqual(gourmand["other_names"], ["Glutton", "Old Gourmand", "食いしん坊"])
        self.assertFalse(gourmand["uses_translation"])

    def test_cached_until_a_skill_changes(self):
        self.client.get("/skills")
        with CaptureQueriesContext(connection) as queries:
            self.client.get("/skills")
        self.assertEqual(len(queries), 0)
        self.announced.name = "Renamed"
        self.announced.save()
        self.assertIn("Renamed", [row["name"] for row in self.client.get("/skills").json()])


class SkillDetailTests(CalculatorTestCase):
    def setUp(self):
        self.client = editor_client()
        self.gourmand = make(201351, name="Gourmand", rarity=2, tier=2)
        self.ruler = make(100101111, name="Gluttonous Ruler", rarity=6, group_id=10010111,
                          availability="announced", evolves_from=self.gourmand,
                          effects=SPEED, activate_lot=1)
        SkillEvolutionCondition.objects.create(
            skill=self.ruler, game_id=10010101, num=1, sub_num=1, timing_type=1, count_type=1,
            text="Attain at least 600 Stamina", translated_text="Reach 600 Stamina.", jp_text="条件",
        )
        SkillEvolutionCondition.objects.create(
            skill=self.ruler, game_id=10010102, num=2, sub_num=1, timing_type=1, count_type=1,
            jp_text="スキルを3個",
        )
        week = Uma.objects.create(name="Special Week", game_id=100101, is_on_global=True)
        jp_week = Uma.objects.create(name="Special Week (New)", game_id=100105)
        UmaSkill.objects.create(uma=jp_week, skill=self.gourmand, source="awakening", level=3)
        UmaSkill.objects.create(uma=week, skill=self.gourmand, source="awakening", level=3)
        UmaSkill.objects.create(uma=week, skill=self.ruler, source="evolved", level=3)
        card = SupportCard.objects.create(name="Kitasan Black", game_id=30028, is_on_global=True)
        SupportCardSkill.objects.create(support_card=card, skill=self.gourmand, source="hint")

    def test_one_skill_in_full(self):
        body = self.client.get(f"/skills/{self.ruler.game_id}").json()
        self.assertEqual((body["name"], body["version"], body["availability"]),
                         ("Gluttonous Ruler", "evolved", "announced"))
        self.assertEqual(body["readable"]["slots"][0]["when"]["all"][0]["text"],
                         "At a random moment mid-race")
        self.assertTrue(body["readable"]["wit_check"])
        self.assertEqual(body["evolution"]["from"]["name"], "Gourmand")
        self.assertEqual(body["evolution"]["conditions"], [
            {"text": "Attain at least 600 Stamina", "uses_translation": False, "is_japanese": False},
            {"text": "スキルを3個", "uses_translation": False, "is_japanese": True},
        ])
        self.assertEqual([(row["name"], row["source"], row["level"]) for row in body["sources"]["umas"]],
                         [("Special Week", "evolved", 3)])

    def test_sources_put_what_is_on_global_first(self):
        body = self.client.get(f"/skills/{self.gourmand.game_id}").json()
        self.assertEqual([(row["name"], row["on_global"]) for row in body["sources"]["umas"]],
                         [("Special Week", True), ("Special Week (New)", False)])
        self.assertEqual([row["name"] for row in body["sources"]["cards"]], ["Kitasan Black"])
        self.assertEqual([row["name"] for row in body["evolution"]["into"]], ["Gluttonous Ruler"])

    def test_a_hidden_or_unknown_skill_is_a_404(self):
        make(200998, name="", availability="jp", jp_name="名前だけ")
        self.assertEqual(self.client.get("/skills/200998").status_code, 404)
        self.assertEqual(self.client.get("/skills/1").status_code, 404)

    def test_a_page_is_cached_and_dropped_when_a_source_changes(self):
        self.client.get(f"/skills/{self.gourmand.game_id}")
        with CaptureQueriesContext(connection) as queries:
            self.client.get(f"/skills/{self.gourmand.game_id}")
        self.assertEqual(len(queries), 0)
        SupportCard.objects.filter(name="Kitasan Black").first().delete()
        body = self.client.get(f"/skills/{self.gourmand.game_id}").json()
        self.assertEqual(body["sources"]["cards"], [])


class GuideSkillFieldsTests(CalculatorTestCase):
    """What a guide's skill chip now carries, for the hover card."""

    def test_a_jp_only_skill_reaches_the_guide_tagged_and_credited(self):
        cm = make_champions_meeting()
        guide = PvpGuide.objects.create(champions_meeting=cm, is_published=True)
        fan = make(200999, name="", description="", translated_name="Fan Skill",
                   translated_description="Fan text.", availability="jp", effects=SPEED)
        week = Uma.objects.create(name="Special Week (New)", game_id=100105)
        UmaSkill.objects.create(uma=week, skill=fan, source="unique", level=3)
        PvpSkillRecommendation.objects.create(guide=guide, style="front", category="speed", skill=fan)

        [row] = editor_client().get(url("cm", cm.id)).json()["skill_recommendations"]
        skill = row["skill"]
        self.assertEqual((skill["name"], skill["description"]), ("Fan Skill", "Fan text."))
        self.assertEqual((skill["availability"], skill["uses_translation"]), ("jp", True))
        self.assertEqual(skill["readable"]["slots"][0]["effects"][0]["text"], "Velocity +0.15 m/s")
        self.assertEqual(skill["sources"], {
            "umas": [{"name": "Special Week (New)", "source": "unique", "level": 3, "on_global": False}],
            "uma_count": 1, "cards": [], "card_count": 0,
        })
        # A JP outfit's unique is named now that the site labels JP content.
        self.assertEqual(skill["unique_of"], "Special Week (New)")
