"""GET /skills and GET /skills/<game_id>, and the skill fields a guide now sends."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from calculatorapi.models import (
    PatreonSupporter, PatreonTier, PvpGuide, PvpSkillRecommendation, Skill, SkillAlias,
    SkillEvolutionCondition, SupportCard, SupportCardSkill, Uma, UmaSkill,
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
    """Staff: one of the early-access readers, and the guide editors."""
    client = APIClient()
    client.force_authenticate(make_user("editor", is_staff=True))
    return client


def early_access_readers():
    """{label: user} for the five kinds of reader the Encyclopedia gate tells
    apart (notes/encyclopedia-plan.md, "Supporters and staff first"). None is
    the guest."""
    tier = PatreonTier.objects.create(name="Junior Class", order=10)
    supporter = make_user("supporter")
    PatreonSupporter.objects.create(display_name="Patron", patreon_user_id="7", tier=tier,
                                    is_active=True, linked_user=supporter)
    granted = make_user("tester")
    granted.early_access = True
    granted.save(update_fields=["early_access"])
    return {"guest": None, "plain": make_user("plain"), "supporter": supporter,
            "staff": make_user("staff", is_staff=True), "granted": granted}


def client_for(user):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


# What each reader gets from a gated route: a guest is asked to sign in, an
# account without early access is refused, and the three ways in read it.
EXPECTED_STATUS = {"guest": 401, "plain": 403, "supporter": 200, "staff": 200, "granted": 200}

# The tables a page is built from. A cached hit reads none of them; the only
# query it makes is the early-access check's pledge lookup, worked out on every
# request by rule (patreon.md: entitlement is never stored).
BUILD_TABLES = ("calculatorapi_skill", "calculatorapi_umaskill", "calculatorapi_supportcardskill",
                "calculatorapi_uma\"", "calculatorapi_supportcard\"")


def skill_queries(queries):
    """The captured queries that touched a table a page is built from."""
    return [query["sql"] for query in queries.captured_queries
            if any(table in query["sql"] for table in BUILD_TABLES)]


class SkillsGateTests(CalculatorTestCase):
    """Supporters and staff first (owner, 2026-11-12): the early-access rule."""

    def test_each_reader_gets_its_answer(self):
        make(201351, name="Gourmand")
        for label, user in early_access_readers().items():
            client = client_for(user)
            for path in ("/skills", "/skills/201351"):
                self.assertEqual(client.get(path).status_code, EXPECTED_STATUS[label], (label, path))

    def test_a_lapsed_pledge_loses_it_on_the_next_request(self):
        make(201351, name="Gourmand")
        supporter = early_access_readers()["supporter"]
        client = client_for(supporter)
        self.assertEqual(client.get("/skills").status_code, 200)
        PatreonSupporter.objects.filter(linked_user=supporter).update(is_active=False)
        self.assertEqual(client.get("/skills").status_code, 403)

    def test_a_guide_shows_a_player_global_skills_only(self):
        cm = make_champions_meeting()
        guide = PvpGuide.objects.create(champions_meeting=cm, visibility="public",
                                        short_description="[[Fan Skill]] and [[Gourmand]]")
        fan = make(200999, name="", translated_name="Fan Skill", availability="jp")
        gourmand = make(201351, name="Gourmand")
        for skill in (fan, gourmand):
            PvpSkillRecommendation.objects.create(guide=guide, style="front", category="speed", skill=skill)
        body = APIClient().get(url("cm", cm.id)).json()
        self.assertEqual([row["skill"]["name"] for row in body["skill_recommendations"]], ["Gourmand"])
        self.assertEqual(set(body["skill_mentions"]), {"gourmand"})
        # Early access sees both: staff, and now supporters too.
        readers = early_access_readers()
        for label in ("staff", "supporter"):
            body = client_for(readers[label]).get(url("cm", cm.id)).json()
            self.assertEqual(len(body["skill_recommendations"]), 2, label)
            self.assertEqual(set(body["skill_mentions"]), {"fan skill", "gourmand"}, label)


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

    def test_lists_every_skill_by_the_name_a_player_reads(self):
        rows = self.client.get("/skills").json()
        # A skill with no global or UmaTL name is listed by its Japanese one,
        # after the English names (owner and Daptrius, 2026-11-12).
        self.assertEqual([row["name"] for row in rows],
                         ["Bring on the Banquet!", "Fan Skill", "Gourmand", "名前だけ"])
        fan = rows[1]
        self.assertEqual((fan["availability"], fan["uses_translation"]), ("jp", True))
        nameless = rows[3]
        self.assertEqual((nameless["game_id"], nameless["uses_translation"]), (self.nameless.game_id, False))
        self.assertEqual(nameless["other_names"], [])

    def test_every_other_name_is_there_for_search(self):
        gourmand = next(row for row in self.client.get("/skills").json() if row["name"] == "Gourmand")
        self.assertEqual(gourmand["other_names"], ["Glutton", "Old Gourmand", "食いしん坊"])
        self.assertFalse(gourmand["uses_translation"])

    def test_cached_until_a_skill_changes(self):
        self.client.get("/skills")
        with CaptureQueriesContext(connection) as queries:
            self.client.get("/skills")
        self.assertEqual(skill_queries(queries), [])
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

    def test_a_japanese_name_skill_has_a_page_and_an_unknown_one_is_a_404(self):
        make(200998, name="", availability="jp", jp_name="名前だけ")
        self.assertEqual(self.client.get("/skills/200998").json()["name"], "名前だけ")
        make(200997, name="", availability="jp")  # no name at all: still hidden
        self.assertEqual(self.client.get("/skills/200997").status_code, 404)
        self.assertEqual(self.client.get("/skills/1").status_code, 404)

    def test_a_page_is_cached_and_dropped_when_a_source_changes(self):
        self.client.get(f"/skills/{self.gourmand.game_id}")
        with CaptureQueriesContext(connection) as queries:
            self.client.get(f"/skills/{self.gourmand.game_id}")
        self.assertEqual(skill_queries(queries), [])
        SupportCard.objects.filter(name="Kitasan Black").first().delete()
        body = self.client.get(f"/skills/{self.gourmand.game_id}").json()
        self.assertEqual(body["sources"]["cards"], [])


class GuideSkillFieldsTests(CalculatorTestCase):
    """What a guide's skill chip now carries, for the hover card."""

    def test_a_jp_only_skill_reaches_the_guide_tagged_and_credited(self):
        cm = make_champions_meeting()
        guide = PvpGuide.objects.create(champions_meeting=cm, visibility="public")
        fan = make(200999, name="", description="", translated_name="Fan Skill",
                   translated_description="Fan text.", availability="jp", effects=SPEED)
        week = Uma.objects.create(name="Special Week (New)", game_id=100105)
        UmaSkill.objects.create(uma=week, skill=fan, source="unique", level=3)
        PvpSkillRecommendation.objects.create(guide=guide, style="front", category="speed", skill=fan)

        [row] = editor_client().get(url("cm", cm.id)).json()["skill_recommendations"]
        skill = row["skill"]
        self.assertEqual((skill["name"], skill["description"]), ("Fan Skill", "Fan text."))
        self.assertEqual((skill["availability"], skill["uses_translation"]), ("jp", True))
        self.assertEqual(skill["readable"]["slots"][0]["effects"][0]["text"], "Velocity +0.15")
        self.assertEqual(skill["sources"], {
            "umas": [{"name": "Special Week (New)", "source": "unique", "level": 3, "on_global": False}],
            "uma_count": 1, "cards": [], "card_count": 0,
        })
        # A JP outfit's unique is named now that the site labels JP content.
        self.assertEqual(skill["unique_of"], "Special Week (New)")


class SkillOwnersAndTypeTests(CalculatorTestCase):
    """Cost, type, owners and the unique / inherited pair (encyclopedia PR 3)."""

    def setUp(self):
        self.client = editor_client()
        spe = Uma.objects.create(name="Special Week", game_id=100101)
        unique = make(100011, name="Shooting Star", rarity=5, icon_id=0, cost=None)
        make(900011, name="Shooting Star", rarity=1, icon_id=20013, cost=200)
        self.gold = make(200011, name="Gold Skill", rarity=2, icon_id=20012, cost=180)
        evolved = make(100101111, name="Evolved Star", rarity=6, icon_id=20016, evolves_from=self.gold)
        make(200501, name="Passive", icon_id=10011, cost=70)
        make(200601, name="Odd", icon_id=99991)
        UmaSkill.objects.create(uma=spe, skill=unique, source="unique")
        UmaSkill.objects.create(uma=spe, skill=evolved, source="evolved", level=3)

    def rows(self):
        return {row["game_id"]: row for row in self.client.get("/skills").json()}

    def test_cost_and_type_from_the_icon_family(self):
        rows = self.rows()
        self.assertEqual((rows[200011]["cost"], rows[200011]["type"]), (180, "velocity"))
        self.assertEqual((rows[100011]["cost"], rows[100011]["type"]), (None, "unique"))
        self.assertEqual(rows[200501]["type"], "stat")
        # A family nobody placed is "other", never a guess.
        self.assertEqual(rows[200601]["type"], "other")

    def test_a_unique_and_its_copy_name_their_uma_and_each_other(self):
        rows = self.rows()
        self.assertEqual((rows[100011]["owners"], rows[100011]["pair"]), (["Special Week"], 900011))
        self.assertEqual((rows[900011]["owners"], rows[900011]["pair"]), (["Special Week"], 100011))
        self.assertEqual((rows[200011]["owners"], rows[200011]["pair"]), ([], None))
        self.assertEqual(self.client.get("/skills/900011").json()["pair"]["game_id"], 100011)

    def test_an_evolution_names_its_uma(self):
        detail = self.client.get("/skills/200011").json()
        self.assertEqual([(row["name"], row["owners"]) for row in detail["evolution"]["into"]],
                         [("Evolved Star", ["Special Week"])])

    def test_a_cards_rarity_and_type_for_hide_r(self):
        card = SupportCard.objects.create(name="Kitasan Black", game_id=10028, rarity=1, card_type="speed")
        SupportCardSkill.objects.create(support_card=card, skill=self.gold, source="hint")
        [row] = self.client.get("/skills/200011").json()["sources"]["cards"]
        self.assertEqual((row["rarity"], row["card_type"]), (1, "speed"))
