"""The PvP guide: constraints, GET /pvp-guides/<kind>/<id>, has_guide, the admin, and the sheet loader."""

import csv
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.management.commands import load_sheet_pvp_guides as loader
from calculatorapi.models import (
    CalculationConstants,
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
    PvpHonorableMention,
    PvpSkillRecommendation,
    PvpStatConfig,
    PvpStyleNote,
    PvpTeamComp,
    PvpUmaRecommendation,
    Skill,
    SupportCard,
    Uma,
    UmaSkill,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    make_champions_meeting,
    make_league_of_heroes,
    make_scenario,
    make_user,
)

RECOMMENDATION_KEYS = (
    "speed_recommendation", "stamina_recommendation", "power_recommendation",
    "guts_recommendation", "wit_recommendation",
)


def make_skill(name, game_id, description="Does a thing.", **fields):
    defaults = {"rarity": 1, "group_id": game_id // 10, "tier": 1, "icon_id": 10011}
    defaults.update(fields)
    return Skill.objects.create(name=name, game_id=game_id, description=description, **defaults)


def url(kind, event_id):
    return f"/pvp-guides/{kind}/{event_id}"


class GuideModelTests(CalculatorTestCase):
    """The guide points at exactly one event, and its children keep their shape."""

    def setUp(self):
        self.cm = make_champions_meeting()
        self.loh = make_league_of_heroes()

    def test_a_guide_needs_exactly_one_event(self):
        for fields in ({}, {"champions_meeting": self.cm, "league_of_heroes": self.loh}):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(ValidationError):
                    PvpGuide(**fields).clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PvpGuide.objects.create(**fields)

    def test_one_guide_per_event(self):
        PvpGuide.objects.create(champions_meeting=self.cm)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpGuide.objects.create(champions_meeting=self.cm)

    def test_an_event_with_a_guide_cannot_be_deleted(self):
        PvpGuide.objects.create(league_of_heroes=self.loh)
        with self.assertRaises(ProtectedError):
            self.loh.delete()

    def test_a_slot_needs_a_card_or_a_note(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm)
        deck = PvpDeck.objects.create(guide=guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=deck, slot=1, note="or any speed")
        with self.assertRaises(ValidationError):
            PvpDeckSlot(deck=deck, slot=2).clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpDeckSlot.objects.create(deck=deck, slot=2)

    def test_a_recommended_uma_runs_one_style(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm)
        uma = Uma.objects.create(name="Maruzensky")
        with self.assertRaises(ValidationError):
            PvpUmaRecommendation(guide=guide, style="everyone", uma=uma).clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpUmaRecommendation.objects.create(guide=guide, style="everyone", uma=uma)

    def test_a_skill_not_on_global_is_refused(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm)
        jp_only = make_skill("JP Only", 900001, description="")
        with self.assertRaises(ValidationError):
            PvpSkillRecommendation(
                guide=guide, style="front", category="speed", skill=jp_only
            ).clean()


class GuideEndpointTests(CalculatorTestCase):
    """GET /pvp-guides/<kind>/<id>: what a visitor and an editor each get back."""

    def setUp(self):
        self.client = APIClient()
        self.cm = make_champions_meeting(name="Aquarius Cup", cm_number=10)
        self.guide = PvpGuide.objects.create(
            champions_meeting=self.cm,
            scenario=make_scenario(name="Grand Live"),
            style_ranking="Front >> Runaway >= End > Late > Pace",
            short_description="Mile on turf. ==Stamina 500 is enough==.",
            is_published=True,
            admin_comments="private",
        )

    def test_unknown_kind_and_unknown_event_are_404(self):
        for path in (url("pvp", self.cm.id), url("cm", self.cm.id + 999), url("loh", self.cm.id)):
            with self.subTest(path=path):
                res = self.client.get(path)
                self.assertEqual(res.status_code, 404)
                self.assertEqual(res.json(), {"error": "Guide not found"})

    def test_a_draft_is_404_for_a_visitor_with_the_same_body(self):
        self.guide.is_published = False
        self.guide.save()
        missing = self.client.get(url("cm", self.cm.id + 999))
        draft = self.client.get(url("cm", self.cm.id))
        self.assertEqual(draft.status_code, 404)
        self.assertEqual(draft.content, missing.content)

        self.client.force_authenticate(make_user())
        self.assertEqual(self.client.get(url("cm", self.cm.id)).status_code, 404)

    def test_staff_see_a_draft_flagged_as_one(self):
        self.guide.is_published = False
        self.guide.save()
        self.client.force_authenticate(make_user(is_staff=True))
        res = self.client.get(url("cm", self.cm.id))
        self.assertEqual(res.status_code, 200)
        self.assertIs(res.json()["is_published"], False)

    def test_league_of_heroes_guides_answer_on_loh(self):
        loh = make_league_of_heroes()
        PvpGuide.objects.create(league_of_heroes=loh, is_published=True)
        body = self.client.get(url("loh", loh.id)).json()
        self.assertEqual(body["event_type"], "league_of_heroes")
        self.assertEqual(body["event"], loh.id)

    def test_full_shape(self):
        uma = Uma.objects.create(name="Maruzensky")
        card = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
        skill = make_skill("Lead the Pack", 100011, rarity=5)
        UmaSkill.objects.create(uma=uma, skill=skill, source="unique")
        PvpStatConfig.objects.create(guide=self.guide, style="everyone", label="Minimum",
                                     speed="1100", stamina="578*")
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=uma, stars=5,
                                            note="Use as runaway")
        PvpSkillRecommendation.objects.create(guide=self.guide, style="front",
                                              category="acceleration", tier=0, skill=skill)
        PvpStyleNote.objects.create(guide=self.guide, style="front", body="**Go early.**")
        deck = PvpDeck.objects.create(guide=self.guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=card, note="Gold + Link")
        PvpDeckSlot.objects.create(deck=deck, slot=1, order=1, note="or any speed")
        PvpDeckSlot.objects.create(deck=deck, slot=6, is_borrow=True, support_card=card)
        PvpHonorableMention.objects.create(guide=self.guide, style="front", support_card=card,
                                           note="Stats + 2SP")
        PvpTeamComp.objects.create(guide=self.guide, text="2 Front + 1 Pace", note="61% in sims")

        body = self.client.get(url("cm", self.cm.id)).json()

        self.assertEqual(set(body), {
            "event_type", "event", "is_published", "updated_at", "scenario", "style_ranking",
            "short_description", "meta_analysis", "team_comps", "stat_configs",
            "uma_recommendations", "skill_recommendations", "style_notes", "decks",
            "honorable_mentions",
        })
        self.assertNotIn("admin_comments", body)
        self.assertEqual(body["event_type"], "champions_meeting")
        self.assertEqual(body["event"], self.cm.id)
        self.assertEqual(body["scenario"]["name"], "Grand Live")
        self.assertEqual(body["team_comps"], [{"text": "2 Front + 1 Pace", "note": "61% in sims"}])
        self.assertEqual(body["stat_configs"][0]["stamina"], "578*")
        self.assertEqual(body["stat_configs"][0]["power"], "")
        self.assertEqual(body["uma_recommendations"][0]["uma"]["name"], "Maruzensky")
        self.assertEqual(body["uma_recommendations"][0]["note"], "Use as runaway")
        rec = body["skill_recommendations"][0]
        self.assertEqual(rec["skill"]["name"], "Lead the Pack")
        self.assertEqual(rec["skill"]["description"], "Does a thing.")
        self.assertEqual(rec["skill"]["unique_of"], "Maruzensky")
        self.assertEqual(body["style_notes"], [{"style": "front", "body": "**Go early.**"}])

        slots = body["decks"][0]["slots"]
        self.assertEqual([(s["slot"], s["order"]) for s in slots], [(1, 0), (1, 1), (6, 0)])
        self.assertEqual(slots[0]["support_card"]["card_type"], "speed")
        self.assertIsNone(slots[1]["support_card"])
        self.assertEqual(slots[1]["note"], "or any speed")
        self.assertIs(slots[2]["is_borrow"], True)
        self.assertEqual(body["honorable_mentions"][0]["support_card"]["name"], "Kitasan Black")

    def test_sections_sort_by_style_then_their_own_rule(self):
        for style in ("end", "front", "everyone", "runaway"):
            PvpStatConfig.objects.create(guide=self.guide, style=style)
        low, high = Uma.objects.create(name="A"), Uma.objects.create(name="B")
        PvpUmaRecommendation.objects.create(guide=self.guide, style="pace", uma=low, stars=3)
        PvpUmaRecommendation.objects.create(guide=self.guide, style="pace", uma=high, stars=5)
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=low, stars=1)
        untiered = make_skill("Untiered", 100021)
        tier_two = make_skill("Tier two", 100031)
        tier_zero = make_skill("Tier zero", 100041)
        for skill, tier in ((untiered, None), (tier_two, 2), (tier_zero, 0)):
            PvpSkillRecommendation.objects.create(guide=self.guide, style="everyone",
                                                  category="green", tier=tier, skill=skill)

        body = self.client.get(url("cm", self.cm.id)).json()

        self.assertEqual([r["style"] for r in body["stat_configs"]],
                         ["everyone", "runaway", "front", "end"])
        self.assertEqual([(r["style"], r["uma"]["name"]) for r in body["uma_recommendations"]],
                         [("front", "A"), ("pace", "B"), ("pace", "A")])
        self.assertEqual([r["skill"]["name"] for r in body["skill_recommendations"]],
                         ["Tier zero", "Tier two", "Untiered"])

    def test_a_skill_that_left_global_is_not_shown(self):
        skill = make_skill("Gone", 100051)
        PvpSkillRecommendation.objects.create(guide=self.guide, style="front",
                                              category="speed", skill=skill)
        Skill.objects.filter(pk=skill.pk).update(description="  ")
        body = self.client.get(url("cm", self.cm.id)).json()
        self.assertEqual(body["skill_recommendations"], [])

    def test_query_count_does_not_grow_with_decks(self):
        card = SupportCard.objects.create(name="Card")

        def add_deck(label):
            deck = PvpDeck.objects.create(guide=self.guide, style="front", label=label)
            for slot in range(1, 7):
                PvpDeckSlot.objects.create(deck=deck, slot=slot, support_card=card)

        add_deck("One")
        with CaptureQueriesContext(connection) as one:
            self.client.get(url("cm", self.cm.id))
        for label in ("Two", "Three", "Four", "Five"):
            add_deck(label)
        with CaptureQueriesContext(connection) as five:
            res = self.client.get(url("cm", self.cm.id))
        self.assertEqual(len(res.json()["decks"]), 5)
        self.assertEqual(len(one), len(five))


class HasGuideTests(CalculatorTestCase):
    """has_guide on both race arrays: true only for a PUBLISHED guide."""

    def setUp(self):
        self.client = APIClient()
        self.cm_published = make_champions_meeting(name="Published", cm_number=1)
        self.cm_draft = make_champions_meeting(name="Draft", cm_number=2)
        self.cm_none = make_champions_meeting(name="None", cm_number=3)
        self.loh = make_league_of_heroes()
        PvpGuide.objects.create(champions_meeting=self.cm_published, is_published=True)
        PvpGuide.objects.create(champions_meeting=self.cm_draft)
        PvpGuide.objects.create(league_of_heroes=self.loh, is_published=True)

    def test_calculator_data_carries_has_guide(self):
        body = self.client.get("/calculator-data").json()
        flags = {cm["name"]: cm["has_guide"] for cm in body["champions_meeting_data"]}
        self.assertEqual(flags, {"Published": True, "Draft": False, "None": False})
        self.assertIs(body["league_of_heroes_event_data"][0]["has_guide"], True)

    def test_the_five_recommendation_keys_are_gone(self):
        body = self.client.get("/calculator-data").json()
        for event in body["champions_meeting_data"] + body["league_of_heroes_event_data"]:
            for key in RECOMMENDATION_KEYS:
                self.assertNotIn(key, event)

    def test_league_of_heroes_route_carries_has_guide(self):
        body = self.client.get("/leagueofheroes").json()
        self.assertIs(body[0]["has_guide"], True)
        for key in RECOMMENDATION_KEYS:
            self.assertNotIn(key, body[0])

    def test_publishing_drops_the_payload_and_a_child_save_keeps_it(self):
        self.client.get("/calculator-data")
        guide = PvpGuide.objects.get(champions_meeting=self.cm_published)
        deck = PvpDeck.objects.create(guide=guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=deck, slot=1, note="or any speed")
        PvpTeamComp.objects.create(guide=guide, text="3 Front")
        self.assertIsNotNone(public_payload_cache.read(), "a guide child dropped the cache")

        guide.is_published = False
        guide.save()
        self.assertIsNone(public_payload_cache.read(), "unpublishing kept a stale has_guide")
        flags = {cm["name"]: cm["has_guide"]
                 for cm in self.client.get("/calculator-data").json()["champions_meeting_data"]}
        self.assertIs(flags["Published"], False)


class MilestoneConstantsTests(CalculatorTestCase):
    def test_constants_carry_the_milestones(self):
        constants = APIClient().get("/calculator-data").json()["calculation_constants"]
        self.assertEqual(constants["cm_registration_opens_hours"], -72)
        self.assertEqual(constants["cm_finals_hours"], 120)
        self.assertIsNone(constants["loh_round_two_hours"])
        self.assertEqual(CalculationConstants.load().cm_round_two_hours, 48)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class GuideAdminTests(CalculatorTestCase):
    """The guide and deck pages render with every inline filled."""

    def setUp(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        cm = make_champions_meeting()
        self.guide = PvpGuide.objects.create(champions_meeting=cm, is_published=True)
        card = SupportCard.objects.create(name="Kitasan Black")
        PvpStatConfig.objects.create(guide=self.guide, style="everyone", speed="1100+")
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front",
                                            uma=Uma.objects.create(name="Maruzensky"))
        PvpSkillRecommendation.objects.create(guide=self.guide, style="front", category="speed",
                                              skill=make_skill("Lead the Pack", 100011))
        PvpStyleNote.objects.create(guide=self.guide, style="front", body="Go early.")
        PvpHonorableMention.objects.create(guide=self.guide, style="front", support_card=card)
        PvpTeamComp.objects.create(guide=self.guide, text="2 Front + 1 Pace")
        self.deck = PvpDeck.objects.create(guide=self.guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=self.deck, slot=1, support_card=card)

    def test_guide_pages_render(self):
        for name in ("changelist", "add"):
            with self.subTest(page=name):
                res = self.client.get(reverse(f"admin:calculatorapi_pvpguide_{name}"))
                self.assertEqual(res.status_code, 200)
        res = self.client.get(reverse("admin:calculatorapi_pvpguide_change", args=(self.guide.pk,)))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Standard")

    def test_deck_pages_render(self):
        for name in ("changelist", "add"):
            with self.subTest(page=name):
                res = self.client.get(reverse(f"admin:calculatorapi_pvpdeck_{name}"))
                self.assertEqual(res.status_code, 200)
        res = self.client.get(reverse("admin:calculatorapi_pvpdeck_change", args=(self.deck.pk,)))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Kitasan Black")

    def test_skill_autocomplete_offers_global_skills_only(self):
        make_skill("Global Skill", 100099)
        make_skill("JP Only Skill", 900001, description="")
        res = self.client.get(reverse("admin:autocomplete"), {
            "term": "Skill", "app_label": "calculatorapi",
            "model_name": "pvpskillrecommendation", "field_name": "skill",
        })
        self.assertEqual(res.status_code, 200)
        names = [row["text"] for row in res.json()["results"]]
        self.assertIn("Global Skill (100099)", names)
        self.assertNotIn("JP Only Skill (900001)", names)

    def test_the_event_pages_no_longer_offer_the_five_boxes(self):
        cm = self.guide.champions_meeting
        res = self.client.get(reverse("admin:calculatorapi_championsmeeting_change", args=(cm.pk,)))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, "speed_recommendation")


class SheetLoaderTests(CalculatorTestCase):
    """load_sheet_pvp_guides reads both sheet formats (from CSV files here)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.cm14 = make_champions_meeting(name="Gemini", cm_number=14)
        self.cm10 = make_champions_meeting(name="Aquarius", cm_number=10)
        Uma.objects.create(name="Seiun Sky")
        SupportCard.objects.create(name="Narita Top Road", game_id=30086)
        make_skill("Groundwork", 100061)
        make_skill("Sunny Days ◎", 100071)
        make_skill("Early Lead", 100081)

    def write(self, tab, rows):
        with open(Path(self.tmp.name) / f"{tab}.csv", "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)

    @staticmethod
    def grid(height, width=33):
        return [[""] * width for _ in range(height)]

    def import_block(self):
        rows = self.grid(47)
        rows[0][1], rows[0][23] = "CM #14 GEMINI CUP", "A front game."
        rows[1][9:14] = ["1200", "578*", "1100+", "1100+*", "1100+"]
        rows[3][24] = "End >= Front"
        rows[4][1] = "Recommended Front Runner"
        rows[6][1], rows[7][1] = "Seiun Sky CM #2", "★★★(RA)"
        rows[8][1], rows[8][2] = "Rec. Accel", "1. Seiun"
        rows[11][1], rows[11][6] = "BEGINNER - FRONT", "Borrow Slot"
        rows[12][1], rows[12][4] = "30086 Narita Top Road CM #11", "12305 Wit"
        rows[14][1] = "Stats + FCM*"
        rows[15][1], rows[15][31] = "red", "Highlight"
        rows[16][1] = "or any spd"
        return rows

    def cup_tab(self):
        rows = self.grid(20, 30)
        rows[0][0], rows[0][6], rows[0][14] = "CM #10", "Recommended Stats", "Acceleration"
        rows[1][14], rows[1][16] = "Front", "Groundwork"
        rows[3][6:12] = ["Runaway", "1200*", "578*", "1000+", "400+", "1000+"]
        rows[4][6] = "*Bonus Speed every 300 Speed."
        rows[5][6], rows[6][6] = "Recommended Strategy", "Front >> End"
        rows[8][14] = "Recommended Skills"
        rows[9][14], rows[9][15] = "Green", "Tier 1"
        rows[10][16], rows[10][18] = "Sunny Day ○/◎", "Early Lead (Taking The Lead)"
        rows[12][1] = "Recommended Team Comp"
        rows[13][1] = "2 Front + 1 Pace"
        rows[14][1] = "Henry's Team"
        rows[15][1:4] = ["Front", "Front", "End"]
        return rows

    def load(self, *args):
        self.write(loader.IMPORT_TAB, self.import_block())
        self.write("Aquarius Cup", self.cup_tab())
        with patch.object(loader, "CUP_TABS", ("Aquarius Cup",)):
            out = StringIO()
            call_command("load_sheet_pvp_guides", "--from-dir", self.tmp.name, *args, stdout=out)
        return out.getvalue()

    def test_reads_an_import_range_block(self):
        self.load()
        guide = PvpGuide.objects.get(champions_meeting=self.cm14)
        self.assertTrue(guide.is_published)
        self.assertEqual(guide.style_ranking, "End >= Front")
        self.assertEqual(guide.stat_configs.get().stamina, "578*")
        uma = guide.uma_recommendations.get()
        self.assertEqual((uma.uma.name, uma.stars, uma.role), ("Seiun Sky", 3, "RA"))
        self.assertIn("1. Seiun", guide.style_notes.get(style="front").body)
        slots = list(guide.decks.get().slots.order_by("slot", "order"))
        self.assertEqual(slots[0].support_card.game_id, 30086)
        self.assertEqual(slots[0].note, "==Stats + FCM*==")
        self.assertEqual((slots[1].slot, slots[1].order, slots[1].note), (1, 1, "or any spd"))
        self.assertEqual((slots[2].slot, slots[2].support_card, slots[2].note), (4, None, "Any Wit"))

    def test_reads_a_per_cup_tab(self):
        self.load()
        guide = PvpGuide.objects.get(champions_meeting=self.cm10)
        self.assertEqual(guide.style_ranking, "Front >> End")
        stats = guide.stat_configs.get()
        self.assertEqual((stats.style, stats.speed), ("runaway", "1200*"))
        self.assertIn("Bonus Speed", stats.footnote)
        accel = guide.skill_recommendations.get(category="acceleration")
        self.assertEqual((accel.style, accel.skill.name), ("front", "Groundwork"))
        green = {r.skill.name: r for r in guide.skill_recommendations.filter(category="green")}
        self.assertEqual(set(green), {"Sunny Days ◎", "Early Lead"})
        self.assertEqual(green["Early Lead"].tier, 1)
        self.assertEqual(green["Early Lead"].note, "Taking The Lead")
        self.assertEqual(list(guide.team_comps.values_list("text", "note")),
                         [("2 Front + 1 Pace", ""), ("Front + Front + End", "Henry's Team")])

    def test_keeps_existing_guides_unless_replace(self):
        PvpGuide.objects.create(champions_meeting=self.cm14, style_ranking="Mine")
        self.load()
        self.assertEqual(PvpGuide.objects.get(champions_meeting=self.cm14).style_ranking, "Mine")
        self.load("--replace")
        self.assertEqual(PvpGuide.objects.get(champions_meeting=self.cm14).style_ranking,
                         "End >= Front")

    def test_dry_run_writes_nothing(self):
        out = self.load("--dry-run")
        self.assertIn("Would create 2 guide(s)", out)
        self.assertFalse(PvpGuide.objects.exists())
