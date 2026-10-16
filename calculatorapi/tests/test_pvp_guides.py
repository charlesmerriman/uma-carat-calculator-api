"""The PvP guide: constraints, GET /pvp-guides/<kind>/<id>, has_guide, the admin, the sheet loader
and the track green filler."""
# One module per feature area, and this area has the admin page, the endpoint and
# the sheet loader in it.
# pylint: disable=too-many-lines

import csv
import tempfile
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache, pvp_guides, pvp_sheet, pvp_track_skills
from calculatorapi.management.commands import load_sheet_pvp_guides as loader
from calculatorapi.models.skill import skill_families
from calculatorapi.models import (
    CalculationConstants,
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
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
        PvpTeamComp.objects.create(guide=self.guide, text="2 Front + 1 Pace", note="61% in sims")

        body = self.client.get(url("cm", self.cm.id)).json()

        self.assertEqual(set(body), {
            "event_type", "event", "is_published", "updated_at", "scenario", "style_ranking",
            "short_description", "meta_analysis", "team_comps", "stat_configs",
            "uma_recommendations", "skill_recommendations", "style_notes", "decks",
            "skill_mentions",
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
        # The deck's cards are on the guide page, nested under the deck.
        self.assertContains(res, "Kitasan Black")
        self.assertContains(res, 'name="front-decks-0-slots-TOTAL_FORMS"')

    def test_the_list_shows_what_is_filled(self):
        res = self.client.get(reverse("admin:calculatorapi_pvpguide_changelist"))
        self.assertContains(res, '<span class="pvp-chip">Front</span>')
        self.assertNotContains(res, '<span class="pvp-chip">Late</span>')

    def test_the_add_page_has_no_tables(self):
        res = self.client.get(reverse("admin:calculatorapi_pvpguide_add"))
        self.assertNotContains(res, "TOTAL_FORMS")
        self.assertContains(res, "Pick the event and save")

    def test_the_event_page_links_to_the_guide(self):
        cm = self.guide.champions_meeting
        res = self.client.get(reverse("admin:calculatorapi_championsmeeting_change", args=(cm.pk,)))
        self.assertContains(res, "Open the guide")
        self.assertContains(res, reverse("admin:calculatorapi_pvpguide_change", args=(self.guide.pk,)))
        other = make_champions_meeting(name="No Guide Cup", cm_number=99)
        res = self.client.get(reverse("admin:calculatorapi_championsmeeting_change", args=(other.pk,)))
        self.assertContains(res, "Write a guide for this event")
        self.assertContains(res, f"?event=cm-{other.pk}")

    def test_the_autocomplete_carries_pictures(self):
        res = self.client.get(reverse("admin:autocomplete"), {
            "term": "Kitasan", "app_label": "calculatorapi",
            "model_name": "pvpdeckslot", "field_name": "support_card",
        })
        self.assertEqual(res.status_code, 200)
        row = res.json()["results"][0]
        self.assertEqual(row["text"], "Kitasan Black")
        self.assertNotIn("picture", row)  # no image on this card

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
        with patch.object(pvp_sheet, "CUP_TABS", ("Aquarius Cup",)):
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


class SkillMentionTests(CalculatorTestCase):
    """[[Skill name]] in guide text: refused when unknown, resolved by the API."""

    def setUp(self):
        self.cm = make_champions_meeting()
        make_skill("Groundwork", 100061)
        make_skill("Final Push", 100062)
        make_skill("Not Yet", 900002, description="")

    def test_an_unknown_mention_is_refused_with_a_suggestion(self):
        guide = PvpGuide(champions_meeting=self.cm, short_description="Take [[Grondwork]] first.")
        with self.assertRaises(ValidationError) as caught:
            guide.clean()
        message = str(caught.exception.message_dict["short_description"])
        self.assertIn("[[Grondwork]]", message)
        self.assertIn("Groundwork", message)

    def test_a_skill_not_on_global_cannot_be_mentioned(self):
        with self.assertRaises(ValidationError):
            PvpTeamComp(text="2 Front", note="with [[Not Yet]]").clean()

    def test_known_mentions_pass_ignoring_case(self):
        PvpGuide(champions_meeting=self.cm, short_description="[[groundwork]] then [[Final Push]]").clean()

    def test_the_api_resolves_every_mention_once(self):
        guide = PvpGuide.objects.create(
            champions_meeting=self.cm, is_published=True,
            short_description="[[Groundwork]] first.",
        )
        PvpStyleNote.objects.create(guide=guide, style="front", body="Then [[final push]].")
        deck = PvpDeck.objects.create(guide=guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=deck, slot=1, note="for [[Groundwork]]")
        body = APIClient().get(url("cm", self.cm.id)).json()
        self.assertEqual(set(body["skill_mentions"]), {"groundwork", "final push"})
        self.assertEqual(body["skill_mentions"]["final push"]["name"], "Final Push")
        self.assertEqual(body["skill_mentions"]["groundwork"]["description"], "Does a thing.")

    def test_a_mention_that_went_stale_is_left_out(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm, is_published=True,
                                        short_description="[[Gone Skill]]")
        self.assertEqual(guide.short_description, "[[Gone Skill]]")
        body = APIClient().get(url("cm", self.cm.id)).json()
        self.assertEqual(body["skill_mentions"], {})


class FormFields(HTMLParser):
    """Every field of the page's main form, as the browser would post it,
    leaving out the "Add another" template rows."""

    def __init__(self):
        super().__init__()
        self.data = {}
        self._select = None
        self._textarea = None
        self._select_chosen = False

    @staticmethod
    def _skip(name):
        return not name or "__prefix__" in name

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get("name")
        if tag == "input":
            if self._skip(name):
                return
            kind = attrs.get("type", "text")
            if kind in ("checkbox", "radio") and "checked" not in attrs:
                return
            if kind == "submit":
                return
            # A ticked checkbox with no value attribute posts "on".
            default = "on" if kind in ("checkbox", "radio") else ""
            self.data[name] = attrs.get("value", default)
        elif tag == "select" and not self._skip(name):
            self._select = name
            self._select_chosen = False
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in attrs:
            self.data[self._select] = attrs.get("value", "")
            self._select_chosen = True
        elif tag == "option" and self._select and not self._select_chosen:
            # A select with nothing marked posts its first option.
            self.data[self._select] = attrs.get("value", "")
        elif tag == "textarea" and not self._skip(name):
            self._textarea = name
            self.data[name] = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea] += data


def form_data(response):
    parser = FormFields()
    parser.feed(response.content.decode())
    return parser.data


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class GuideTabsTests(CalculatorTestCase):
    """The guide page: one tab per style, each row saved to its tab's style."""

    def setUp(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        self.guide = PvpGuide.objects.create(champions_meeting=make_champions_meeting())
        self.groundwork = make_skill("Groundwork", 100061)
        self.push = make_skill("Final Push", 100062)
        self.card = SupportCard.objects.create(name="Kitasan Black")
        PvpSkillRecommendation.objects.create(guide=self.guide, style="front",
                                              category="acceleration", skill=self.groundwork)

    def change_url(self):
        return reverse("admin:calculatorapi_pvpguide_change", args=(self.guide.pk,))

    def page_data(self):
        return form_data(self.client.get(self.change_url()))

    def test_every_tab_has_its_tables_and_the_general_skills_sit_on_the_guide_tab(self):
        res = self.client.get(self.change_url())
        self.assertEqual(res.status_code, 200)
        for style in ("runaway", "front", "pace", "late", "end"):
            for stem in ("uma_recommendations", "style_notes", "skill_recommendations",
                         "decks"):
                self.assertContains(res, f'name="{style}-{stem}-TOTAL_FORMS"')
        self.assertContains(res, 'name="everyone-skill_recommendations-TOTAL_FORMS"')
        self.assertContains(res, "General skills (every position)")
        self.assertContains(res, 'id="front-group"')
        # The saved row's style rides along hidden, fixed to its tab.
        self.assertContains(res, 'name="front-skill_recommendations-0-style" value="front"')

    def test_a_row_saves_to_its_tab_s_style_whatever_was_posted(self):
        data = self.page_data()
        data.update({
            "late-skill_recommendations-TOTAL_FORMS": "1",
            "late-skill_recommendations-0-style": "front",  # tampered: the tab wins
            "late-skill_recommendations-0-skill": str(self.push.id),
            "late-skill_recommendations-0-category": "acceleration",
            "late-skill_recommendations-0-tier": "1",
            "late-skill_recommendations-0-note": "Take [[Groundwork]] too",
            "late-skill_recommendations-0-order": "0",
            "_continue": "1",
            "_pvp_tab": "late",
        })
        res = self.client.post(self.change_url(), data)
        self.assertEqual(res.status_code, 302, res.content.decode()[:3000])
        # Save and continue lands back on the tab that was open.
        self.assertTrue(res["Location"].endswith("#late"), res["Location"])
        rows = sorted(self.guide.skill_recommendations.values_list("style", "skill__name", "tier"))
        self.assertEqual(rows, [("front", "Groundwork", None), ("late", "Final Push", 1)])

    def test_a_new_deck_saves_with_its_cards_and_blank_numbered_rows_are_skipped(self):
        data = self.page_data()
        prefix = "front-decks"
        data.update({
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-0-style": "front",
            f"{prefix}-0-label": "Standard",
            f"{prefix}-0-note": "",
            f"{prefix}-0-order": "0",
            f"{prefix}-0-guide": str(self.guide.id),
            f"{prefix}-0-slots-TOTAL_FORMS": "6",
            f"{prefix}-0-slots-INITIAL_FORMS": "0",
            f"{prefix}-0-slots-MIN_NUM_FORMS": "0",
            f"{prefix}-0-slots-MAX_NUM_FORMS": "1000",
        })
        for index in range(6):
            data.update({
                f"{prefix}-0-slots-{index}-slot": str(index + 1),
                f"{prefix}-0-slots-{index}-order": "0",
                f"{prefix}-0-slots-{index}-support_card": "",
                f"{prefix}-0-slots-{index}-note": "",
            })
        data[f"{prefix}-0-slots-0-support_card"] = str(self.card.id)
        data[f"{prefix}-0-slots-0-note"] = "2SP"
        data[f"{prefix}-0-slots-5-note"] = "or any speed"
        data[f"{prefix}-0-slots-5-is_borrow"] = "on"
        res = self.client.post(self.change_url(), data)
        self.assertEqual(res.status_code, 302, res.content.decode()[:3000])
        deck = self.guide.decks.get()
        self.assertEqual((deck.style, deck.label), ("front", "Standard"))
        slots = list(deck.slots.values_list("slot", "support_card__name", "note", "is_borrow"))
        self.assertEqual(slots, [(1, "Kitasan Black", "2SP", False), (6, None, "or any speed", True)])

    def test_two_borrow_ticks_in_one_deck_are_refused_and_the_tab_shows_the_error(self):
        deck = PvpDeck.objects.create(guide=self.guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=self.card, is_borrow=True)
        data = self.page_data()
        data["front-decks-0-slots-1-note"] = "borrowed too"
        data["front-decks-0-slots-1-is_borrow"] = "on"
        res = self.client.post(self.change_url(), data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Only one slot in a deck can be the borrowed one")
        self.assertContains(res, 'class="pvp-tab-errors"')
        self.assertEqual(deck.slots.count(), 1)

    def test_a_borrowed_slot_s_alternative_is_the_same_borrow(self):
        # The sheet loader ticks Borrow on a slot's card AND its "or"
        # alternative: one borrowed slot, so it saves, touched or not.
        deck = PvpDeck.objects.create(guide=self.guide, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=deck, slot=6, support_card=self.card, is_borrow=True)
        PvpDeckSlot.objects.create(deck=deck, slot=6, order=1, note="or any speed", is_borrow=True)
        data = self.page_data()
        self.assertEqual(self.client.post(self.change_url(), data).status_code, 302)
        data["front-decks-0-slots-1-note"] = "or any stamina"
        self.assertEqual(self.client.post(self.change_url(), data).status_code, 302)
        # A second borrowed SLOT is still refused, against untouched rows too.
        data["front-decks-0-slots-2-note"] = "borrowed too"
        data["front-decks-0-slots-2-is_borrow"] = "on"
        res = self.client.post(self.change_url(), data)
        self.assertContains(res, "Only one slot in a deck can be the borrowed one")

    def _fill(self, start, stop):
        """One more row in every table for each number in the range, and a
        card in that slot number of every deck."""
        for number in range(start, stop):
            uma = Uma.objects.create(name=f"Uma {number}")
            PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=uma,
                                                order=number)
            skill = make_skill(f"Skill {number}", 100200 + number)
            PvpSkillRecommendation.objects.create(guide=self.guide, style="pace",
                                                  category="speed", skill=skill, order=number)
            PvpStatConfig.objects.create(guide=self.guide, style="everyone", speed=str(number),
                                         order=number)
            PvpTeamComp.objects.create(guide=self.guide, text=f"Comp {number}", order=number)
            for deck in self.guide.decks.all():
                PvpDeckSlot.objects.create(deck=deck, slot=number, support_card=self.card)

    def test_the_page_and_an_unchanged_save_ask_nothing_per_row(self):
        # A row costs no query: the page (its pickers, "Other versions") and a
        # save that touched nothing (ChildRowForm) ask the database the same
        # whether a table holds one row or six. Decks are the exception, one
        # query each for their nested cards, so the deck count is held still.
        for style in ("front", "late"):
            PvpDeck.objects.create(guide=self.guide, style=style, label=f"{style} deck")
        self._fill(1, 2)
        # Warm the per-process caches (content types, the log entry's) first,
        # or the first page and the first save pay a lookup the next do not.
        self.assertEqual(self.client.post(self.change_url(), self.page_data()).status_code, 302)
        with CaptureQueriesContext(connection) as page_one:
            data = self.page_data()
        with CaptureQueriesContext(connection) as save_one:
            self.assertEqual(self.client.post(self.change_url(), data).status_code, 302)
        self._fill(2, 7)
        with CaptureQueriesContext(connection) as page_six:
            data = self.page_data()
        self.assertEqual(len(page_one), len(page_six))
        with CaptureQueriesContext(connection) as save_six:
            self.assertEqual(self.client.post(self.change_url(), data).status_code, 302)
        self.assertEqual(len(save_one), len(save_six))
        self.assertEqual(PvpDeckSlot.objects.count(), 12)

    def test_a_changed_row_is_validated_and_an_untouched_one_is_left_alone(self):
        maru = Uma.objects.create(name="Maruzensky")
        oguri = Uma.objects.create(name="Oguri Cap")
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=maru, order=0)
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=oguri, order=1)
        # A row written past the form's checks (here a mention of no skill)
        # does not block a save that leaves it alone...
        PvpTeamComp.objects.create(guide=self.guide, text="2 Front", note="[[Nope]]")
        data = self.page_data()
        self.assertEqual(self.client.post(self.change_url(), data).status_code, 302)
        # ...a changed row is checked in full: the same uma twice for a style...
        data["front-uma_recommendations-1-uma"] = str(maru.id)
        res = self.client.post(self.change_url(), data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "already exists")
        # ...and touching the bad note is what surfaces its mention.
        data = self.page_data()
        data["team_comps-0-note"] = "[[Nope]] still"
        res = self.client.post(self.change_url(), data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No skill on global has this name")

    def test_a_filled_guide_posts_past_django_s_default_field_limit_and_still_saves(self):
        # One Save posts every table of the guide. A filled guide runs past the
        # 1000 fields Django allows by default (DATA_UPLOAD_MAX_NUMBER_FIELDS),
        # and prod answered a bare 400 (TooManyFieldsSent) before the view ran
        # on 2026-10-15. settings.py raises the limit; this round-trips a page
        # shaped like a real guide so that it stays raised.
        for index, style in enumerate(("runaway", "front", "pace", "late", "end")):
            for number in range(3):
                deck = PvpDeck.objects.create(guide=self.guide, style=style,
                                              label=f"Deck {number}", order=number)
                for slot in range(1, 7):
                    PvpDeckSlot.objects.create(deck=deck, slot=slot, support_card=self.card)
            for number in range(8):
                skill = make_skill(f"{style} skill {number}", 100100 + index * 8 + number)
                PvpSkillRecommendation.objects.create(guide=self.guide, style=style,
                                                      category="acceleration", skill=skill)
        data = self.page_data()
        # The page must still be the shape that broke, or this proves nothing.
        self.assertGreater(len(data), 1000)
        res = self.client.post(self.change_url(), data)
        self.assertEqual(res.status_code, 302, res.content.decode()[:3000])

    def test_an_editor_can_open_the_page(self):
        call_command("create_content_editor_group", stdout=StringIO())
        editor = make_user(username="editor", is_staff=True)
        editor.groups.add(Group.objects.get(name="Content editors"))
        self.client.force_login(editor)
        self.assertEqual(self.client.get(self.change_url()).status_code, 200)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class GuideEventFieldTests(CalculatorTestCase):
    """One Event dropdown stands in for the two FK pickers."""

    def setUp(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        self.cm = make_champions_meeting(name="Aquarius Cup", cm_number=10)
        self.loh = make_league_of_heroes(name="LoH 3")

    def add_url(self):
        return reverse("admin:calculatorapi_pvpguide_add")

    def test_the_event_page_link_preselects_the_event(self):
        res = self.client.get(self.add_url(), {"event": f"cm-{self.cm.pk}"})
        self.assertContains(res, f'<option value="cm-{self.cm.pk}" selected>')

    def test_adding_sets_the_right_fk(self):
        res = self.client.post(self.add_url(), {
            "event": f"loh-{self.loh.pk}", "scenario": "", "style_ranking": "",
            "short_description": "", "meta_analysis": "", "admin_comments": "",
            "_continue": "1",
        })
        self.assertEqual(res.status_code, 302, res.content.decode()[:3000])
        guide = PvpGuide.objects.get()
        self.assertEqual((guide.champions_meeting_id, guide.league_of_heroes_id), (None, self.loh.pk))

    def test_an_event_with_a_guide_is_not_offered_and_is_refused(self):
        PvpGuide.objects.create(champions_meeting=self.cm)
        res = self.client.get(self.add_url())
        self.assertNotContains(res, f'value="cm-{self.cm.pk}"')
        self.assertContains(res, f'value="loh-{self.loh.pk}"')
        res = self.client.post(self.add_url(), {
            "event": f"cm-{self.cm.pk}", "scenario": "", "style_ranking": "",
            "short_description": "", "meta_analysis": "", "admin_comments": "",
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(PvpGuide.objects.count(), 1)

    def test_an_existing_guide_still_shows_its_own_event(self):
        guide = PvpGuide.objects.create(champions_meeting=self.cm)
        res = self.client.get(reverse("admin:calculatorapi_pvpguide_change", args=(guide.pk,)))
        self.assertContains(res, f'<option value="cm-{self.cm.pk}" selected>')


class CopyGuideTests(CalculatorTestCase):
    """copy_guide(): the next cup's guide starts from the last one."""

    def setUp(self):
        self.source = PvpGuide.objects.create(
            champions_meeting=make_champions_meeting(name="Old", cm_number=1),
            style_ranking="Front > End", short_description="Old words",
        )
        self.target = PvpGuide.objects.create(
            champions_meeting=make_champions_meeting(name="New", cm_number=2),
            short_description="My own words",
        )
        self.uma = Uma.objects.create(name="Maruzensky")
        self.card = SupportCard.objects.create(name="Kitasan Black")
        PvpUmaRecommendation.objects.create(guide=self.source, style="front", uma=self.uma, stars=5)
        PvpStyleNote.objects.create(guide=self.source, style="front", body="Go early.")
        deck = PvpDeck.objects.create(guide=self.source, style="front", label="Standard", order=3)
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=self.card, note="2SP")
        PvpDeckSlot.objects.create(deck=deck, slot=6, note="borrow", is_borrow=True)
        PvpStatConfig.objects.create(guide=self.source, style="everyone", speed="1200")

    def test_copies_every_section_and_keeps_what_the_target_had(self):
        PvpUmaRecommendation.objects.create(guide=self.target, style="front", uma=self.uma, stars=2)
        copied = pvp_guides.copy_guide(self.source, self.target)
        self.assertEqual(copied["uma_recommendations"], 0)  # already there, kept at 2 stars
        self.assertEqual(self.target.uma_recommendations.get().stars, 2)
        self.assertEqual(copied["decks"], 1)
        deck = self.target.decks.get()
        self.assertEqual(list(deck.slots.values_list("slot", "note")), [(1, "2SP"), (6, "borrow")])
        self.assertEqual(self.target.style_notes.get().body, "Go early.")
        self.assertEqual(self.target.stat_configs.get().speed, "1200")
        self.target.refresh_from_db()
        self.assertEqual(self.target.short_description, "My own words")  # never overwritten
        self.assertEqual(self.target.style_ranking, "Front > End")  # blank, so filled
        # The source is untouched.
        self.assertEqual(self.source.decks.count(), 1)

    def test_only_the_chosen_sections(self):
        copied = pvp_guides.copy_guide(self.source, self.target, ["decks"])
        self.assertEqual(copied, {"decks": 1})
        self.assertEqual(self.target.uma_recommendations.count(), 0)

    def test_refuses_itself(self):
        with self.assertRaises(ValueError):
            pvp_guides.copy_guide(self.source, self.source)

    def test_the_dialog_action_copies_and_sends_the_page_back(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        action_url = reverse("admin:calculatorapi_pvpguide_copy_from_guide", args=(self.target.pk,))
        res = self.client.get(action_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Copy from another guide")
        res = self.client.post(action_url, {
            "_form_submitted": "1", "source": str(self.source.pk), "sections": ["decks", "overview"],
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["HX-Redirect"], reverse("admin:calculatorapi_pvpguide_change", args=(self.target.pk,)))
        self.assertEqual(self.target.decks.count(), 1)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class LoadFromSheetButtonTests(CalculatorTestCase):
    """The admin's "Load from the sheet": preview reports, apply writes."""

    def setUp(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        self.cm = make_champions_meeting(name="Gemini", cm_number=14)
        self.guide = PvpGuide.objects.create(champions_meeting=self.cm, style_ranking="Mine")
        Uma.objects.create(name="Seiun Sky")
        SupportCard.objects.create(name="Narita Top Road", game_id=30086)

    def url(self):
        return reverse("admin:calculatorapi_pvpguide_load_from_sheet", args=(self.guide.pk,))

    @staticmethod
    def fake_reader(tab):
        block = SheetLoaderTests.import_block(SheetLoaderTests)
        if tab == pvp_sheet.IMPORT_TAB:
            return block
        return [["CM #99"]]  # a cup tab for some other event

    def test_the_page_renders(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Preview only")

    def test_preview_reports_and_writes_nothing(self):
        with patch.object(pvp_sheet, "read_tab", self.fake_reader):
            res = self.client.post(self.url(), {"preview": "on"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Preview, nothing was saved")
        self.assertContains(res, pvp_sheet.IMPORT_TAB)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.style_ranking, "Mine")
        self.assertEqual(self.guide.decks.count(), 0)

    def test_apply_replaces_the_sections(self):
        PvpStatConfig.objects.create(guide=self.guide, style="everyone", speed="old")
        with patch.object(pvp_sheet, "read_tab", self.fake_reader):
            res = self.client.post(self.url(), {})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Done")
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.style_ranking, "End >= Front")
        self.assertEqual(self.guide.stat_configs.get().stamina, "578*")
        self.assertEqual(self.guide.decks.count(), 1)
        self.assertFalse(self.guide.is_published)  # the switch is the editor's

    def test_an_event_the_sheet_lacks_is_a_form_error(self):
        other = PvpGuide.objects.create(champions_meeting=make_champions_meeting(name="X", cm_number=77))
        with patch.object(pvp_sheet, "read_tab", self.fake_reader):
            res = self.client.post(
                reverse("admin:calculatorapi_pvpguide_load_from_sheet", args=(other.pk,)), {"preview": "on"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "has no guide for CM #77")


def make_family(name, group_id, condition, gold=None):
    """A general skill family as the game ships it: ○, ◎ and ×, plus an
    optional gold skill, all sharing one condition. Returns the ◎."""
    base = group_id * 10
    make_skill(f"{name} ○", base + 2, group_id=group_id, tier=1, condition=condition)
    pick = make_skill(f"{name} ◎", base + 1, group_id=group_id, tier=2, condition=condition)
    make_skill(f"{name} ×", base + 3, group_id=group_id, tier=-1, condition=condition)
    if gold:
        make_skill(gold, base + 4, group_id=group_id, tier=3, rarity=2, condition=condition)
    return pick


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class TrackGreenSkillTests(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """"Fill track green skills": the event's track fields, matched against
    each skill's own game condition."""

    def setUp(self):
        self.right = make_family("Right-Handed", 20001, "rotation==1", gold="Right-Handed Demon")
        self.left = make_family("Left-Handed", 20002, "rotation==2")
        self.tokyo = make_family("Tokyo Racecourse", 20003, "track_id==10006")
        self.nakayama = make_family("Nakayama Racecourse", 20004, "track_id==10005")
        self.standard = make_family("Standard Distance", 20013, "is_basis_distance==1")
        self.firm = make_family("Firm Conditions", 20015, "ground_condition==1")
        self.wet = make_family(
            "Wet Conditions", 20016, "ground_condition==2@ground_condition==3@ground_condition==4",
        )
        self.spring = make_family("Spring Runner", 20017, "season==1@season==5")
        self.winter = make_family("Winter Runner", 20020, "season==4")
        self.sunny = make_family("Sunny Days", 20021, "weather==1")
        self.sharp = make_family("Sharp Turns", 20224, "track_id==10001@track_id==10005")
        self.muddy = make_family(
            "Muddy", 20234, "ground_type==2&ground_condition==3@ground_type==2&ground_condition==4",
        )
        # One-offs with a track condition but no ○/◎ pair, and a skill whose
        # condition reads more than the track.
        make_skill("Unchanging", 300021, group_id=30002, tier=1, rarity=2, condition="season==4")
        make_skill("Medium Race Enthusiast", 1200011, condition="distance_type==3&ground_type==1")
        make_skill("Corner Adept ◎", 200351, group_id=20035, tier=2, condition="corner!=0&rotation==1")
        make_skill("Corner Adept ○", 200352, group_id=20035, tier=1, condition="corner!=0&rotation==1")
        # Announced but not released: no condition yet.
        make_skill("Kyoto Racecourse ◎", 200061, group_id=20006, tier=2)

        # make_champions_meeting is Tokyo, Turf, Long, 2400m, Good, Spring, Sunny, Right.
        self.cm = make_champions_meeting(name="Taurus", cm_number=30)
        self.guide = PvpGuide.objects.create(champions_meeting=self.cm)

    def suggested(self):
        return pvp_track_skills.suggest(self.guide)

    def test_matches_each_track_field_and_adds_the_double_circle(self):
        result = self.suggested()
        self.assertEqual(
            result["add"],
            [self.right, self.tokyo, self.standard, self.wet, self.spring, self.sunny],
        )
        self.assertEqual(result["already"], [])
        self.assertEqual(result["problems"], [])

    def test_conditions_with_alternatives_and_joined_clauses(self):
        self.cm.track, self.cm.surface_type, self.cm.track_condition = "Nakayama", "Dirt", "Heavy"
        self.cm.season, self.cm.length, self.cm.direction = "Winter", "2500m", "Left-Handed"
        self.cm.save()
        self.assertEqual(
            self.suggested()["add"],
            [self.left, self.nakayama, self.wet, self.winter, self.sunny, self.sharp, self.muddy],
        )

    def test_a_track_with_no_racecourse_skill_is_reported(self):
        for track in ("Longchamp", "Toyko"):
            with self.subTest(track=track):
                self.cm.track = track
                self.cm.save()
                result = self.suggested()
                self.assertNotIn(self.tokyo, result["add"])
                self.assertIn(self.right, result["add"])
                self.assertIn(f'Track is "{track}"', result["problems"][0])

    def test_an_unreadable_field_skips_only_its_skills(self):
        # The live data has season and weather swapped on one event.
        self.cm.season, self.cm.weather = "Cloudy", "Fall"
        self.cm.save()
        result = self.suggested()
        self.assertEqual(len(result["problems"]), 2)
        self.assertIn('Season is "Cloudy"', result["problems"][0])
        for skill in (self.spring, self.winter, self.sunny):
            self.assertNotIn(skill, result["add"])
        self.assertIn(self.tokyo, result["add"])

    def test_a_family_the_guide_names_in_any_version_is_left_alone(self):
        tokyo_white = Skill.objects.get(name="Tokyo Racecourse ○")
        PvpSkillRecommendation.objects.create(
            guide=self.guide, style="front", category="speed", skill=tokyo_white,
        )
        result = self.suggested()
        self.assertNotIn(self.tokyo, result["add"])
        self.assertEqual(result["already"], [self.tokyo])

    def test_fill_appends_to_everyone_green_and_is_safe_twice(self):
        existing = make_skill("Maverick ◎", 200501, tier=2)
        PvpSkillRecommendation.objects.create(
            guide=self.guide, style="everyone", category="green", skill=existing, order=4,
        )
        added = pvp_track_skills.fill(self.guide)
        self.assertEqual(len(added), 6)
        rows = list(
            self.guide.skill_recommendations.filter(style="everyone", category="green")
            .order_by("order").values_list("skill__name", "order")
        )
        self.assertEqual(rows[0], ("Maverick ◎", 4))
        self.assertEqual(rows[1], ("Right-Handed ◎", 5))
        self.assertEqual(rows[-1], ("Sunny Days ◎", 10))
        self.assertEqual(pvp_track_skills.fill(self.guide), [])

    def test_the_admin_page_previews_then_adds(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        action_url = reverse("admin:calculatorapi_pvpguide_fill_track_greens", args=(self.guide.pk,))
        res = self.client.get(action_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Will add (6)")
        self.assertContains(res, "Tokyo Racecourse ◎")
        self.assertEqual(self.guide.skill_recommendations.count(), 0)
        res = self.client.post(action_url)
        self.assertRedirects(
            res, reverse("admin:calculatorapi_pvpguide_change", args=(self.guide.pk,)),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.guide.skill_recommendations.count(), 6)

    def test_parse_condition_refuses_what_it_cannot_read(self):
        self.assertIsNone(pvp_track_skills.parse_condition("phase_random==2&order_rate>50x"))
        self.assertEqual(
            pvp_track_skills.parse_condition("a==1&b>=2@c!=3"),
            [[("a", "==", 1), ("b", ">=", 2)], [("c", "!=", 3)]],
        )


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class SkillFamilyTests(CalculatorTestCase):
    """A skill's other versions (white, gold, evolved) are derived from the
    game data: same group, plus evolves_from, never the × penalty row, never a
    skill not on global."""

    def setUp(self):
        self.white = make_skill("Corner Adept ○", 200011, group_id=2001, tier=1, rarity=1)
        self.gold = make_skill("Corner Adept ◎", 200012, group_id=2001, tier=2, rarity=2)
        self.penalty = make_skill("Corner Adept ×", 200013, group_id=2001, tier=-1, rarity=1)
        # An evolved form, pointing at the gold; its own group id is new.
        self.evolved = make_skill("Corner Virtuoso", 900200012, group_id=9001, tier=1, rarity=6,
                                  evolves_from=self.gold)
        # Another group's gold, not on global yet: never shown.
        self.other_white = make_skill("Straightaway Adept ○", 200021, group_id=2002, tier=1)
        make_skill("Straightaway Adept ◎", 200022, group_id=2002, tier=2, rarity=2, description="")
        self.lone = make_skill("Groundwork", 200031, group_id=2003, tier=1)

    def test_white_sees_gold_and_evolved_in_order(self):
        families = skill_families([self.white, self.evolved, self.other_white, self.lone])
        self.assertEqual([row.name for row in families[self.white.id]],
                         ["Corner Adept ◎", "Corner Virtuoso"])
        self.assertEqual([row.name for row in families[self.evolved.id]],
                         ["Corner Adept ○", "Corner Adept ◎"])
        self.assertEqual(families[self.other_white.id], [])  # the gold is not on global
        self.assertEqual(families[self.lone.id], [])
        self.assertEqual(skill_families([]), {})

    def test_the_penalty_version_is_never_a_version(self):
        self.assertNotIn(self.penalty, self.gold.family())
        self.assertEqual([row.version_label for row in self.gold.family()], ["○ white", "evolved"])

    def test_one_query_for_any_number_of_skills(self):
        with CaptureQueriesContext(connection) as one:
            skill_families([self.white])
        with CaptureQueriesContext(connection) as four:
            skill_families([self.white, self.evolved, self.other_white, self.lone])
        self.assertEqual(len(one), len(four))

    def test_the_api_carries_every_version_on_rows_and_mentions(self):
        cm = make_champions_meeting()
        guide = PvpGuide.objects.create(champions_meeting=cm, is_published=True,
                                        short_description="Take [[Corner Virtuoso]] when you can.")
        PvpSkillRecommendation.objects.create(guide=guide, style="front", category="speed",
                                              skill=self.white, tier=1)
        body = APIClient().get(url("cm", cm.id)).json()
        skill = body["skill_recommendations"][0]["skill"]
        self.assertEqual((skill["version"], skill["tier"], skill["is_evolved"]), ("○ white", 1, False))
        self.assertEqual(
            [(row["name"], row["version"], row["tier"], row["is_evolved"]) for row in skill["versions"]],
            [("Corner Adept ◎", "◎ gold", 2, False), ("Corner Virtuoso", "evolved", 1, True)],
        )
        mention = body["skill_mentions"]["corner virtuoso"]
        self.assertEqual((mention["version"], mention["is_evolved"]), ("evolved", True))
        self.assertEqual([row["name"] for row in mention["versions"]], ["Corner Adept ○", "Corner Adept ◎"])

    def test_the_admin_picker_and_the_skills_table_name_the_version(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        res = self.client.get(reverse("admin:autocomplete"), {
            "term": "Corner Adept", "app_label": "calculatorapi",
            "model_name": "pvpskillrecommendation", "field_name": "skill",
        })
        versions = {row["text"]: row["version"] for row in res.json()["results"]}
        self.assertEqual(versions["Corner Adept ◎ (200012)"], "◎ gold")
        guide = PvpGuide.objects.create(champions_meeting=make_champions_meeting())
        PvpSkillRecommendation.objects.create(guide=guide, style="front", category="speed",
                                              skill=self.white)
        res = self.client.get(reverse("admin:calculatorapi_pvpguide_change", args=(guide.pk,)))
        self.assertContains(res, "◎ gold: Corner Adept ◎, evolved: Corner Virtuoso")
        res = self.client.get(reverse("admin:calculatorapi_pvpguide_skill_names"), {"q": "Corner"})
        names = {row["name"]: row["version"] for row in res.json()["results"]}
        self.assertEqual(names["Corner Adept ○"], "○ white")


class ProseLinkerTests(CalculatorTestCase):
    """The loader's auto-linking of skill names in the sheet's prose."""

    def setUp(self):
        for game_id, name in enumerate(
            ("Final Push", "Straightaway Acceleration", "Restless", "Runaway", "Acceleration",
             "Corner Adept ○"), start=100100):
            make_skill(name, game_id)

    def test_links_whole_names_and_skips_the_stoplist(self):
        linker = loader.ProseLinker()
        text = "1. Seiun 2. final push, then Straightaway Acceleration. Restless runaway, restless."
        self.assertEqual(
            linker.link(text),
            "1. Seiun 2. [[Final Push]], then [[Straightaway Acceleration]]. [[Restless]] runaway, restless.",
        )
        self.assertEqual(linker.find(text), ["Final Push", "Straightaway Acceleration", "Restless"])

    def test_leaves_existing_links_and_long_results_alone(self):
        linker = loader.ProseLinker()
        self.assertEqual(linker.link("[[Final Push]] first"), "[[Final Push]] first")
        self.assertEqual(linker.link("Final Push", max_length=8), "Final Push")
