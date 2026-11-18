"""GET /encyclopedia/umas/<game_id>, /encyclopedia/cards/<game_id> and /encyclopedia/index."""

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from calculatorapi.models import SkillEvolutionCondition, SupportCard, SupportCardSkill, Uma, UmaSkill
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import RELEASED_ON_GLOBAL
from calculatorapi.tests.test_skills_api import (
    client_for, early_access_readers, editor_client, make, skill_queries,
)

SPECIAL_WEEK, SPECIAL_WEEK_SUMMER, SILENCE = 100101, 100102, 100201
SPE_CARD, SPE_SR, SIRIUS = 30001, 20001, 30081


def uma(game_id, name, **fields):
    return Uma.objects.create(game_id=game_id, name=name, image=f"umas/{game_id}.png", **fields)


def card(game_id, name, **fields):
    return SupportCard.objects.create(game_id=game_id, name=name, image=f"support_cards/{game_id}.png",
                                      **fields)


# The pages build image URLs from stored names. CI has no bucket configured, so
# the S3 storage raises there; plain file storage answers anywhere.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class EncyclopediaFixture(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    def setUp(self):
        self.client = editor_client()
        self.spe = uma(
            SPECIAL_WEEK, "Special Week", title="[Special Dreamer]", jp_name="スペシャルウィーク",
            jp_title="[スペシャルドリーマー]", rarity=3, running_style=3, apt_turf=7, base_speed=83,
            growth_stamina=20, global_release_at=RELEASED_ON_GLOBAL, purpose="Great late surger.",
            admin_comments="Editors only.",
            star_stats=[{"star": 3, "speed": 83, "stamina": 88, "power": 98, "guts": 90, "wit": 91}],
        )
        self.summer = uma(SPECIAL_WEEK_SUMMER, "Special Week (Summer)")
        uma(SILENCE, "Silence Suzuka")
        self.unique = make(100011, name="Shooting Star", rarity=5)
        self.gold = make(200011, name="Gold Skill", rarity=2, tier=2, cost=180)
        self.evolved = make(100101111, name="Evolved Star", rarity=6, evolves_from=self.gold)
        self.hidden = make(200999, name="", translated_name="", jp_name="")
        for skill, source, level in ((self.gold, "awakening", 3), (self.unique, "unique", None),
                                     (self.evolved, "evolved", 3), (self.hidden, "innate", None)):
            UmaSkill.objects.create(uma=self.spe, skill=skill, source=source, level=level)

        self.ssr = card(SPE_CARD, "Special Week", title="[The Brightest Star]", rarity=3,
                        card_type="guts", character_id=1001, jp_name="スペシャルウィーク")
        card(SPE_SR, "Special Week", rarity=2, card_type="speed", character_id=1001)
        self.sirius = card(SIRIUS, "Team Sirius", rarity=3, card_type="group", character_id=1002,
                           group_members=["Special Week", "Silence Suzuka"])
        card(31000, "Best SSR Speed", rarity=3, card_type="speed", is_placeholder=True)
        SupportCardSkill.objects.create(support_card=self.ssr, skill=self.gold, source="hint")


class GateTests(EncyclopediaFixture):
    """Open to everyone since 2026-11-18 (notes/encyclopedia-open-plan.md)."""

    def test_everyone_reads_every_route(self):
        for label, user in early_access_readers().items():
            client = client_for(user)
            for path in (f"/encyclopedia/umas/{SPECIAL_WEEK}", f"/encyclopedia/cards/{SPE_CARD}",
                         "/encyclopedia/card-effects", "/encyclopedia/index"):
                self.assertEqual(client.get(path).status_code, 200, (label, path))

    def test_an_unknown_or_unshown_id_is_a_404(self):
        uma(800101, "Tazuna Hayakawa")  # an NPC: not a trainee, so no page
        for path in ("/encyclopedia/umas/999999", "/encyclopedia/umas/800101",
                     "/encyclopedia/cards/31000", "/encyclopedia/cards/99999"):
            self.assertEqual(self.client.get(path).status_code, 404, path)


class UmaPageTests(EncyclopediaFixture):
    def page(self):
        return self.client.get(f"/encyclopedia/umas/{SPECIAL_WEEK}").json()

    def test_the_facts_and_never_the_editors_notes(self):
        body = self.page()
        self.assertEqual((body["id"], body["game_id"], body["title"]), (self.spe.id, SPECIAL_WEEK, "[Special Dreamer]"))
        self.assertEqual((body["jp_name"], body["jp_title"]), ("スペシャルウィーク", "[スペシャルドリーマー]"))
        self.assertEqual((body["apt"]["turf"], body["base"]["speed"], body["growth"]["stamina"]), (7, 83, 20))
        self.assertEqual(body["star_stats"][0]["stamina"], 88)
        self.assertEqual(body["purpose"], "Great late surger.")
        self.assertNotIn("admin_comments", body)
        self.assertNotIn("Editors only.", str(body))

    def test_she_wears_her_own_colours_and_her_unique_says_what_it_does(self):
        """The redesign: the game's pair for character 1001, and the unique skill's text for its plate."""
        self.unique.description = "Greatly increase velocity."
        self.unique.save()
        body = self.page()
        self.assertEqual(body["colours"], ["#EE6DCB", "#FFDEF9"])
        unique, gold = body["skills"][0], body["skills"][1]
        self.assertEqual(unique["description"], "Greatly increase velocity.")
        self.assertNotIn("description", gold)

    def test_a_character_the_files_dont_cover_has_no_colours(self):
        uma(199901, "Nobody Yet")
        self.assertIsNone(self.client.get("/encyclopedia/umas/199901").json()["colours"])

    def test_skills_in_the_games_order_and_only_shown_ones(self):
        skills = self.page()["skills"]
        self.assertEqual([(row["name"], row["source"]) for row in skills], [
            ("Shooting Star", "unique"), ("Gold Skill", "awakening"), ("Evolved Star", "evolved"),
        ])
        gold, evolved = skills[1], skills[2]
        self.assertEqual((gold["level"], gold["cost"]), (3, 180))
        self.assertEqual(evolved["evolves_from"], 200011)
        self.assertEqual((gold["conditions"], evolved["conditions"]), ([], []))

    def test_evolutions_come_in_release_order_global_first(self):
        """Daptrius, board, 2026-11-17: a JP-only evolution named earlier in the
        alphabet sat above a global one. Global first, then the game id, which
        numbers an outfit's evolutions of one rank in the order they came out."""
        later = make(100101121, name="Aim Front Row Center!", rarity=6, evolves_from=self.gold,
                     availability="jp")
        UmaSkill.objects.create(uma=self.spe, skill=later, source="evolved", level=3)
        evolved = [row["name"] for row in self.page()["skills"] if row["source"] == "evolved"]
        self.assertEqual(evolved, ["Evolved Star", "Aim Front Row Center!"])

    def test_an_evolution_carries_its_unlock_conditions(self):
        SkillEvolutionCondition.objects.create(
            skill=self.evolved, game_id=10010101, num=1, sub_num=1, timing_type=1, count_type=1,
            text="Attain at least 600 Stamina", jp_text="条件",
        )
        SkillEvolutionCondition.objects.create(
            skill=self.evolved, game_id=10010102, num=2, sub_num=1, timing_type=1, count_type=1, jp_text="スキルを3個",
        )
        evolved = self.page()["skills"][2]
        self.assertEqual([(row["text"], row["is_japanese"]) for row in evolved["conditions"]], [
            ("Attain at least 600 Stamina", False), ("スキルを3個", True),
        ])

    def test_relatives_other_outfits_own_cards_and_group_cards(self):
        body = self.page()
        self.assertEqual([row["game_id"] for row in body["outfits"]], [SPECIAL_WEEK_SUMMER])
        # Her own two cards and the group card that lists her; never the placeholder.
        self.assertEqual([row["game_id"] for row in body["cards"]], [SPE_SR, SPE_CARD, SIRIUS])
        # Each card row has the frameless art's slot, so the page can frame it (None until uploaded).
        self.assertTrue(all("image_borderless" in row for row in body["cards"]))

    def test_an_alternate_outfit_finds_the_same_cards(self):
        body = self.client.get(f"/encyclopedia/umas/{SPECIAL_WEEK_SUMMER}").json()
        self.assertEqual([row["game_id"] for row in body["outfits"]], [SPECIAL_WEEK])
        self.assertEqual([row["game_id"] for row in body["cards"]], [SPE_SR, SPE_CARD, SIRIUS])

    def test_a_jp_only_outfit_has_a_page(self):
        jp = uma(100103, "Special Week (New Year)", apt_turf=7)
        body = self.client.get(f"/encyclopedia/umas/{jp.game_id}").json()
        self.assertEqual((body["on_global"], body["apt"]["turf"], body["rarity"]), (False, 7, None))


class CardPageTests(EncyclopediaFixture):
    def test_the_card_its_skills_and_its_people(self):
        body = self.client.get(f"/encyclopedia/cards/{SPE_CARD}").json()
        self.assertEqual((body["card_type"], body["rarity"], body["jp_name"]), ("guts", 3, "スペシャルウィーク"))
        self.assertEqual([(row["name"], row["source"], row["cost"]) for row in body["skills"]],
                         [("Gold Skill", "hint", 180)])
        self.assertEqual([row["game_id"] for row in body["outfits"]], [SPECIAL_WEEK, SPECIAL_WEEK_SUMMER])
        self.assertEqual([row["game_id"] for row in body["cards"]], [SPE_SR, SIRIUS])

    def test_a_card_wears_its_characters_colours_and_a_group_card_none(self):
        self.assertEqual(self.client.get(f"/encyclopedia/cards/{SPE_CARD}").json()["colours"], ["#EE6DCB", "#FFDEF9"])
        self.assertIsNone(self.client.get(f"/encyclopedia/cards/{SIRIUS}").json()["colours"])

    def test_a_group_card_lists_its_members_outfits(self):
        body = self.client.get(f"/encyclopedia/cards/{SIRIUS}").json()
        self.assertEqual([row["game_id"] for row in body["outfits"]],
                         [SPECIAL_WEEK, SPECIAL_WEEK_SUMMER, SILENCE])
        self.assertEqual(body["cards"], [])


class TranslationCreditTests(EncyclopediaFixture):
    """notes/encyclopedia-jp-fills-plan.md, PR 3: a title Global (or an editor) hasn't given is
    UmaTL's, and a page showing any UmaTL text says so (`uses_translation`), for the credit."""

    def test_a_global_page_uses_no_translation(self):
        self.assertFalse(self.client.get(f"/encyclopedia/umas/{SPECIAL_WEEK}").json()["uses_translation"])
        self.assertFalse(self.client.get(f"/encyclopedia/cards/{SPE_CARD}").json()["uses_translation"])

    def test_umatls_title_shows_only_while_the_title_is_blank(self):
        SupportCard.objects.filter(pk=self.ssr.pk).update(translated_title="[Not Shown]")
        Uma.objects.filter(pk=self.summer.pk).update(translated_title="[Summer Title]")
        body = self.client.get(f"/encyclopedia/cards/{SPE_CARD}").json()
        self.assertEqual(body["title"], "[The Brightest Star]")
        # Her summer outfit's row shows UmaTL's title, so this page credits UmaTL too.
        summer = next(row for row in body["outfits"] if row["game_id"] == SPECIAL_WEEK_SUMMER)
        self.assertEqual((summer["title"], summer["title_translated"]), ("[Summer Title]", True))
        self.assertTrue(body["uses_translation"])
        page = self.client.get(f"/encyclopedia/umas/{SPECIAL_WEEK_SUMMER}").json()
        self.assertEqual((page["title"], page["uses_translation"]), ("[Summer Title]", True))

    def test_umatls_unique_sentence_and_race_name_are_credited(self):
        jp = card(30400, "Kitasan Black", rarity=3, card_type="speed", unique_effect={
            "level": 1, "effects": [{"type": 101, "value": 80, "params": [3, 1, 0, 0]}],
            "translated_text": "Speed Bonus (Friendship 80+)"})
        body = self.client.get(f"/encyclopedia/cards/{jp.game_id}").json()
        self.assertEqual((body["unique_effect"]["text"], body["uses_translation"]),
                         ("Speed Bonus (Friendship 80+)", True))
        runner = uma(100701, "Gold Ship", career_objectives=[
            {"turn": 40, "kind": 1, "race": "A New Race", "grade": 100, "top": 3, "translated": True}])
        self.assertTrue(self.client.get(f"/encyclopedia/umas/{runner.game_id}").json()["uses_translation"])


class IndexTests(EncyclopediaFixture):
    """The web's build writes a document and a sitemap entry per id listed here."""

    def test_it_lists_exactly_the_pages_that_exist(self):
        uma(800101, "Tazuna Hayakawa")  # an NPC: not a trainee, so no page
        body = client_for(None).get("/encyclopedia/index").json()
        self.assertEqual(body["umas"], [SPECIAL_WEEK, SPECIAL_WEEK_SUMMER, SILENCE])
        # The placeholder card (31000) is a stand-in, never a page.
        self.assertEqual(body["cards"], [SPE_SR, SPE_CARD, SIRIUS])
        # The nameless skill (200999) is one Skill.objects.shown() leaves out.
        self.assertEqual(body["skills"], [100011, 200011, 100101111])

    def test_every_listed_id_answers_and_no_other_does(self):
        body = client_for(None).get("/encyclopedia/index").json()
        for kind, route in (("umas", "/encyclopedia/umas"), ("cards", "/encyclopedia/cards"),
                            ("skills", "/skills")):
            for game_id in body[kind]:
                self.assertEqual(self.client.get(f"{route}/{game_id}").status_code, 200, (kind, game_id))
        self.assertEqual(self.client.get("/skills/200999").status_code, 404)

    def test_a_new_row_joins_it(self):
        self.client.get("/encyclopedia/index")
        uma(100301, "Tokai Teio")
        self.assertIn(100301, self.client.get("/encyclopedia/index").json()["umas"])


class CacheTests(EncyclopediaFixture):
    def test_a_page_costs_a_bounded_number_of_queries(self):
        with CaptureQueriesContext(connection) as queries:
            self.client.get(f"/encyclopedia/umas/{SPECIAL_WEEK}")
        self.assertLessEqual(len(queries), 10)

    def test_cached_until_a_source_changes(self):
        path = f"/encyclopedia/umas/{SPECIAL_WEEK}"
        self.client.get(path)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(path)
        self.assertEqual(skill_queries(queries), [])
        # A save to any source moves the generation: the next read rebuilds.
        self.gold.name = "Renamed Gold"
        self.gold.save()
        self.assertIn("Renamed Gold", [row["name"] for row in self.client.get(path).json()["skills"]])
