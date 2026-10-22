"""The site's guide editor (views/pvp_guide_editor.py): who may edit, a guide as one
document, its checks, the version handshake, publishing, revisions and the actions."""
# One module per feature area; the editor has a lot of surface.
# pylint: disable=too-many-lines,too-many-public-methods

import copy
import datetime
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache, pvp_guide_document, pvp_guide_revisions, pvp_sheet
from calculatorapi.models import (
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
    PvpGuideRevision,
    PvpSkillRecommendation,
    PvpStatConfig,
    PvpStyleNote,
    PvpTeamComp,
    PvpUmaRecommendation,
    SupportCard,
    Uma,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    auth_client,
    make_champions_meeting,
    make_league_of_heroes,
    make_scenario,
    make_user,
)
from calculatorapi.tests.test_pvp_guides import SheetLoaderTests, form_data, make_family, make_skill


def editor_client(username="editor", is_staff=True):
    """A signed-in API client for a (staff, by default) editor."""
    user = make_user(username=username, is_staff=is_staff)
    client, _ = auth_client(user)
    return client, user


def content_editor_client(username="henry"):
    """A NON-staff account in the "Content editors" group: the permission way in."""
    call_command("create_content_editor_group", stdout=StringIO())
    user = make_user(username=username, is_staff=False)
    user.groups.add(Group.objects.get(name="Content editors"))
    client, _ = auth_client(user)
    return client, user


def build_guide(published=False):
    """A guide with one row in every section, the way test_full_shape builds one."""
    cm = make_champions_meeting(name="Aquarius Cup", cm_number=10)
    scenario = make_scenario(name="Grand Live")
    guide = PvpGuide.objects.create(
        champions_meeting=cm, scenario=scenario, is_published=published,
        style_ranking="Front >> End", short_description="Mile on turf.",
        meta_analysis="Go early.", admin_comments="private",
    )
    uma = Uma.objects.create(name="Maruzensky")
    other_uma = Uma.objects.create(name="Seiun Sky")
    card = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
    skill = make_skill("Lead the Pack", 100011)
    groundwork = make_skill("Groundwork", 100061)
    PvpStatConfig.objects.create(guide=guide, style="everyone", label="Minimum", speed="1100", stamina="578*")
    PvpTeamComp.objects.create(guide=guide, text="2 Front + 1 Pace", note="61% in sims")
    PvpUmaRecommendation.objects.create(guide=guide, style="front", uma=uma, stars=5, note="Use as runaway")
    PvpSkillRecommendation.objects.create(guide=guide, style="front", category="acceleration", tier=0, skill=skill)
    PvpStyleNote.objects.create(guide=guide, style="front", body="**Go early.**")
    deck = PvpDeck.objects.create(guide=guide, style="front", label="Standard")
    PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=card, note="Gold + Link")
    PvpDeckSlot.objects.create(deck=deck, slot=1, order=1, note="or any speed")
    PvpDeckSlot.objects.create(deck=deck, slot=6, is_borrow=True, support_card=card)
    return SimpleNamespace(
        cm=cm, scenario=scenario, guide=guide, uma=uma, other_uma=other_uma, card=card,
        skill=skill, groundwork=groundwork, deck=deck,
    )


def doc_url(guide):
    return f"/pvp-guides/{guide.pk}/document"


def put_body(document, **changes):
    """A PUT body from a GET's document: the version it carries, the fields
    the server takes, and `changes` on top."""
    body = {key: copy.deepcopy(value) for key, value in document.items()
            if key not in ("id", "event_type", "event", "is_published", "updated_at")}
    body.update(changes)
    return body


# ── Who may edit ─────────────────────────────────────────────────────────────

class EditorPermissionTests(CalculatorTestCase):
    """Staff accounts edit; everyone else gets the public 404, and never
    learns which ids are guides. The content-editor permission alone does
    not count (owner, 2026-10-21): the feature is staff only until
    supporters' own guides arrive as a feature of their own."""

    def setUp(self):
        self.fixture = build_guide()
        self.guide = self.fixture.guide

    def test_a_visitor_is_asked_to_sign_in(self):
        self.assertEqual(APIClient().get(doc_url(self.guide)).status_code, 401)

    def test_a_plain_account_gets_the_public_404_and_a_403_on_the_list(self):
        client, _ = editor_client("plain", is_staff=False)
        res = client.get(doc_url(self.guide))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json(), {"error": "Guide not found"})
        self.assertEqual(client.get("/pvp-guides/editable").status_code, 403)
        self.assertEqual(client.post("/pvp-guides", {"kind": "cm", "event": 1}).status_code, 403)
        for path in ("/revisions", "/actions/copy-from", "/actions/sheet", "/actions/track-greens"):
            with self.subTest(path=path):
                method = client.get if path == "/revisions" else client.post
                self.assertEqual(method(f"/pvp-guides/{self.guide.pk}{path}").status_code, 404)

    def test_early_access_alone_does_not_edit(self):
        client, user = editor_client("tester", is_staff=False)
        user.early_access = True
        user.save()
        self.assertEqual(client.get(doc_url(self.guide)).status_code, 404)
        self.assertIs(client.get("/account").json()["guide_editor"], False)

    def test_every_staff_account_may_edit(self):
        plain_staff, _ = editor_client("staff")
        superuser_client, superuser = editor_client("root")
        superuser.is_superuser = True
        superuser.save()
        for name, client in (("staff", plain_staff), ("superuser", superuser_client)):
            with self.subTest(who=name):
                self.assertEqual(client.get(doc_url(self.guide)).status_code, 200)
                account = client.get("/account").json()
                self.assertIs(account["guide_editor"], True)
                self.assertIs(account["early_access"], True)

    def test_the_permission_alone_does_not_edit(self):
        """A non-staff account in the Content editors group, and a non-staff
        superuser, are plain accounts to the editor."""
        content, _ = content_editor_client("henry")
        superuser_client, superuser = editor_client("root", is_staff=False)
        superuser.is_superuser = True
        superuser.save()
        for name, client in (("content editor", content), ("non-staff superuser", superuser_client)):
            with self.subTest(who=name):
                self.assertEqual(client.get(doc_url(self.guide)).status_code, 404)
                self.assertEqual(client.get("/pvp-guides/editable").status_code, 403)
                self.assertIs(client.get("/account").json()["guide_editor"], False)
                # The public route's draft rule is the same function.
                self.assertEqual(client.get(f"/pvp-guides/cm/{self.fixture.cm.id}").status_code, 404)

    def test_the_flag_cannot_be_set_through_patch_account(self):
        client, user = editor_client("plain", is_staff=False)
        res = client.patch("/account", {"guide_editor": True, "display_name": "Rhondal"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIs(res.json()["guide_editor"], False)
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_an_inactive_editor_is_out(self):
        client, user = editor_client("gone")
        user.is_active = False
        user.save()
        self.assertIn(client.get(doc_url(self.guide)).status_code, (401, 403, 404))


# ── The document ─────────────────────────────────────────────────────────────

class DocumentRoundTripTests(CalculatorTestCase):
    """GET the document, PUT it back: ids not objects, order from position,
    decks keep their ids, the reader's shape rides along."""

    def setUp(self):
        self.client, self.user = editor_client()
        self.fixture = build_guide()
        self.guide = self.fixture.guide

    def test_the_document_shape(self):
        body = self.client.get(doc_url(self.guide)).json()
        self.assertEqual(set(body), {
            "id", "version", "event_type", "event", "is_published", "updated_at", "scenario",
            "style_ranking", "short_description", "meta_analysis", "admin_comments",
            "stat_configs", "team_comps", "uma_recommendations", "skill_recommendations",
            "style_notes", "decks",
        })
        self.assertEqual(body["version"], 0)
        self.assertEqual(body["scenario"], self.fixture.scenario.id)
        self.assertEqual(body["admin_comments"], "private")
        self.assertEqual(body["uma_recommendations"], [
            {"style": "front", "uma": self.fixture.uma.id, "stars": 5, "role": "", "note": "Use as runaway"},
        ])
        self.assertEqual(body["skill_recommendations"][0]["skill"], self.fixture.skill.id)
        deck = body["decks"][0]
        self.assertEqual(deck["id"], self.fixture.deck.id)
        self.assertEqual([(s["slot"], s["order"], s["support_card"]) for s in deck["slots"]],
                         [(1, 0, self.fixture.card.id), (1, 1, None), (6, 0, self.fixture.card.id)])

    def test_put_unchanged_keeps_every_row_and_bumps_the_version(self):
        before = self.client.get(f"/pvp-guides/cm/{self.fixture.cm.id}").json()
        document = self.client.get(doc_url(self.guide)).json()
        res = self.client.put(doc_url(self.guide), put_body(document), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(set(res.json()), {"document", "guide"})
        self.assertEqual(res.json()["document"]["version"], 1)
        after = res.json()["guide"]
        before.pop("updated_at")
        after.pop("updated_at")
        self.assertEqual(before, after)
        self.assertEqual(self.guide.decks.get().id, self.fixture.deck.id)
        self.assertEqual(PvpDeckSlot.objects.filter(deck__guide=self.guide).count(), 3)

    def test_order_is_the_array_position_and_a_sent_order_is_ignored(self):
        document = self.client.get(doc_url(self.guide)).json()
        umas = [
            {"style": "front", "uma": self.fixture.other_uma.id, "stars": 3, "role": "RA", "note": "", "order": 99},
            document["uma_recommendations"][0],
        ]
        res = self.client.put(doc_url(self.guide), put_body(document, uma_recommendations=umas), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        rows = list(self.guide.uma_recommendations.order_by("order").values_list("uma__name", "order"))
        self.assertEqual(rows, [("Seiun Sky", 0), ("Maruzensky", 1)])

    def test_a_deck_gold_box_saves_and_reaches_the_reader(self):
        document = self.client.get(doc_url(self.guide)).json()
        self.assertIs(document["decks"][0]["is_highlighted"], False)
        boxed = {**document["decks"][0], "is_highlighted": True}
        # A deck sent without the key (a revision saved before the field
        # existed) saves unboxed.
        plain = {"id": None, "style": "pace", "label": "Honorable Mentions", "note": "", "slots": []}
        res = self.client.put(doc_url(self.guide), put_body(document, decks=[boxed, plain]), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(list(self.guide.decks.order_by("order").values_list("is_highlighted", flat=True)), [True, False])
        reader = self.client.get(f"/pvp-guides/cm/{self.fixture.cm.id}").json()
        self.assertEqual([deck["is_highlighted"] for deck in reader["decks"]], [True, False])

    def test_decks_keep_their_id_when_named_and_are_replaced_otherwise(self):
        document = self.client.get(doc_url(self.guide)).json()
        kept = document["decks"][0]
        kept["label"] = "Renamed"
        kept["slots"] = kept["slots"][:1]
        new = {"id": None, "style": "pace", "label": "Whale", "note": "",
               "slots": [{"slot": 2, "support_card": self.fixture.card.id}]}
        res = self.client.put(doc_url(self.guide), put_body(document, decks=[new, kept]), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        decks = list(self.guide.decks.order_by("order").values_list("id", "label", "style", "order"))
        self.assertEqual(decks[1], (self.fixture.deck.id, "Renamed", "front", 1))
        self.assertEqual(decks[0][1:], ("Whale", "pace", 0))
        self.assertEqual(PvpDeckSlot.objects.filter(deck_id=self.fixture.deck.id).count(), 1)
        # The new deck's defaults: order 0, no borrow, a card with no note.
        slot = PvpDeckSlot.objects.get(deck_id=decks[0][0])
        self.assertEqual((slot.slot, slot.order, slot.is_borrow, slot.note), (2, 0, False, ""))
        # Omitting every deck deletes them all.
        document = res.json()["document"]
        res = self.client.put(doc_url(self.guide), put_body(document, decks=[]), format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.guide.decks.count(), 0)
        # Another guide's deck id is not this guide's: it makes a new deck.
        other = PvpGuide.objects.create(league_of_heroes=make_league_of_heroes())
        foreign = PvpDeck.objects.create(guide=other, style="end", label="Theirs")
        document = res.json()["document"]
        stolen = {"id": foreign.id, "style": "end", "label": "Mine", "note": "", "slots": []}
        res = self.client.put(doc_url(self.guide), put_body(document, decks=[stolen]), format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(other.decks.get().label, "Theirs")
        self.assertEqual(self.guide.decks.get().label, "Mine")

    def test_the_overview_and_the_scenario_are_written(self):
        document = self.client.get(doc_url(self.guide)).json()
        res = self.client.put(doc_url(self.guide), put_body(
            document, scenario=None, style_ranking="End >> Front",
            short_description="Take [[Groundwork]] first.", admin_comments="note to self",
        ), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.guide.refresh_from_db()
        self.assertIsNone(self.guide.scenario_id)
        self.assertEqual(self.guide.style_ranking, "End >> Front")
        self.assertEqual(self.guide.admin_comments, "note to self")
        self.assertIn("groundwork", res.json()["guide"]["skill_mentions"])

    def test_a_save_does_not_drop_the_public_payload(self):
        APIClient().get("/calculator-data")
        self.assertIsNotNone(public_payload_cache.read())
        document = self.client.get(doc_url(self.guide)).json()
        self.client.put(doc_url(self.guide), put_body(document), format="json")
        self.assertIsNotNone(public_payload_cache.read(), "an autosave dropped the payload")

    def test_the_editable_list(self):
        loh = make_league_of_heroes(name="LoH 3", loh_number=3)
        res = self.client.get("/pvp-guides/editable")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual([g["id"] for g in body["guides"]], [self.guide.id])
        row = body["guides"][0]
        self.assertEqual((row["kind"], row["event"], row["is_published"], row["version"]),
                         ("cm", self.fixture.cm.id, False, 0))
        self.assertEqual(row["filled"], ["front"])
        self.assertEqual(body["events"], [{"kind": "loh", "event": loh.id, "name": str(loh)}])


class CreateAndDeleteTests(CalculatorTestCase):
    def setUp(self):
        self.client, self.user = editor_client()
        self.loh = make_league_of_heroes(name="LoH 3", loh_number=3)

    def test_create_a_draft(self):
        res = self.client.post("/pvp-guides", {"kind": "loh", "event": self.loh.id}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        guide = PvpGuide.objects.get(league_of_heroes=self.loh)
        self.assertEqual((body["id"], body["event_type"], body["event"], body["is_published"]),
                         (guide.id, "league_of_heroes", self.loh.id, False))
        self.assertEqual(body["version"], 1)
        self.assertEqual(guide.revisions.get().source, "site")

    def test_create_from_another_guide(self):
        source = build_guide().guide
        res = self.client.post("/pvp-guides", {"kind": "loh", "event": self.loh.id, "copy_from": source.id},
                               format="json")
        self.assertEqual(res.status_code, 201, res.content)
        guide = PvpGuide.objects.get(league_of_heroes=self.loh)
        self.assertEqual(guide.uma_recommendations.count(), 1)
        self.assertEqual(guide.decks.count(), 1)
        self.assertEqual(guide.short_description, "Mile on turf.")
        self.assertEqual(guide.revisions.get().source, "copy")

    def test_create_refusals(self):
        PvpGuide.objects.create(league_of_heroes=self.loh)
        cases = (
            ({"kind": "loh", "event": self.loh.id}, "already has a guide"),
            ({"kind": "loh", "event": 999}, "isn't on the site"),
            ({"kind": "loh", "event": self.loh.id + 1, "copy_from": 999}, "isn't on the site"),
        )
        for body, words in cases:
            with self.subTest(body=body):
                res = self.client.post("/pvp-guides", body, format="json")
                self.assertEqual(res.status_code, 400)
                self.assertIn(words, res.json()["error"])
        res = self.client.post("/pvp-guides", {"kind": "cup", "event": 1}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["errors"][0]["path"], "kind")

    def test_delete_a_draft_but_never_a_published_guide(self):
        guide = PvpGuide.objects.create(league_of_heroes=self.loh, is_published=True)
        res = self.client.delete(f"/pvp-guides/{guide.pk}")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Unpublish", res.json()["error"])
        guide.is_published = False
        guide.save()
        self.assertEqual(self.client.delete(f"/pvp-guides/{guide.pk}").status_code, 204)
        self.assertFalse(PvpGuide.objects.filter(pk=guide.pk).exists())


# ── Checks ───────────────────────────────────────────────────────────────────

class ValidationTests(CalculatorTestCase):
    """Every refusal names where in the document it is, and nothing is written."""

    def setUp(self):
        self.client, self.user = editor_client()
        self.fixture = build_guide()
        self.guide = self.fixture.guide
        self.document = self.client.get(doc_url(self.guide)).json()

    def refuse(self, **changes):
        res = self.client.put(doc_url(self.guide), put_body(self.document, **changes), format="json")
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertEqual(body["error"], body["errors"][0]["message"])
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.version, 0, "a refused save wrote something")
        return {(e["path"], e["message"]) for e in body["errors"]}

    def uma(self, **fields):
        row = {"style": "front", "uma": self.fixture.uma.id, "stars": 3, "role": "", "note": ""}
        row.update(fields)
        return row

    def test_a_missing_section_is_told_not_wiped(self):
        body = put_body(self.document)
        del body["decks"]
        res = self.client.put(doc_url(self.guide), body, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["errors"][0]["path"], "decks")
        self.assertEqual(self.guide.decks.count(), 1)

    def test_uma_rows(self):
        errors = self.refuse(uma_recommendations=[self.uma(uma=999)])
        self.assertIn(("uma_recommendations[0].uma", pvp_guide_document.NOT_IN_CATALOGUE), errors)
        errors = self.refuse(uma_recommendations=[self.uma(), self.uma(stars=4)])
        self.assertIn(("uma_recommendations[1].uma", "Maruzensky is already in Front."), errors)
        paths = {p for p, _ in self.refuse(uma_recommendations=[self.uma(style="everyone")])}
        self.assertIn("uma_recommendations[0].style", paths)
        paths = {p for p, _ in self.refuse(uma_recommendations=[self.uma(stars=6)])}
        self.assertIn("uma_recommendations[0].stars", paths)
        placeholder = Uma.objects.create(name=Uma.ALL_PLACEHOLDER_NAME)
        errors = self.refuse(uma_recommendations=[self.uma(uma=placeholder.id)])
        self.assertIn(("uma_recommendations[0].uma", pvp_guide_document.NOT_IN_CATALOGUE), errors)

    def test_skill_rows(self):
        jp_only = make_skill("JP Only", 900001, description="")
        row = {"style": "front", "category": "speed", "tier": None, "skill": jp_only.id, "note": ""}
        errors = self.refuse(skill_recommendations=[row])
        self.assertIn(("skill_recommendations[0].skill", pvp_guide_document.SKILL_NOT_ON_GLOBAL), errors)
        twice = dict(row, skill=self.fixture.skill.id)
        errors = self.refuse(skill_recommendations=[twice, dict(twice, tier=2)])
        self.assertIn(
            ("skill_recommendations[1].skill", "Lead the Pack is already in Front's Speed list."), errors,
        )
        paths = {p for p, _ in self.refuse(skill_recommendations=[dict(twice, tier=5)])}
        self.assertIn("skill_recommendations[0].tier", paths)
        paths = {p for p, _ in self.refuse(skill_recommendations=[dict(twice, category="magic")])}
        self.assertIn("skill_recommendations[0].category", paths)

    def test_advice_once_per_style(self):
        notes = [{"style": "front", "body": "a"}, {"style": "front", "body": "b"}]
        errors = self.refuse(style_notes=notes)
        self.assertIn(("style_notes[1].style", "Front already has an advice box."), errors)
        paths = {p for p, _ in self.refuse(style_notes=[{"style": "front", "body": ""}])}
        self.assertIn("style_notes[0].body", paths)

    def test_deck_rows(self):
        card = self.fixture.card.id

        def deck(slots):
            return [{"id": None, "style": "front", "label": "D", "note": "", "slots": slots}]

        errors = self.refuse(decks=deck([{"slot": 1, "support_card": None, "note": "  "}]))
        self.assertIn(("decks[0].slots[0]", pvp_guide_document.SLOT_NEEDS_SOMETHING), errors)
        errors = self.refuse(decks=deck([{"slot": 1, "support_card": card, "is_borrow": True},
                                         {"slot": 2, "support_card": card, "is_borrow": True}]))
        self.assertIn(("decks[0]", pvp_guide_document.ONE_BORROW), errors)
        errors = self.refuse(decks=deck([{"slot": 3, "support_card": card}, {"slot": 3, "support_card": card}]))
        self.assertIn(("decks[0]", "Two rows share slot 3."), errors)
        errors = self.refuse(decks=deck([{"slot": 1, "support_card": 999}]))
        self.assertIn(("decks[0].slots[0].support_card", pvp_guide_document.NOT_IN_CATALOGUE), errors)
        paths = {p for p, _ in self.refuse(decks=deck([{"slot": 7, "support_card": card}]))}
        self.assertIn("decks[0].slots[0].slot", paths)
        unlabelled = [{"id": None, "style": "front", "label": "", "note": "", "slots": []}]
        paths = {p for p, _ in self.refuse(decks=unlabelled)}
        self.assertIn("decks[0].label", paths)
        # A borrowed slot and its "or" alternative are one borrow.
        ok = deck([{"slot": 6, "support_card": card, "is_borrow": True},
                   {"slot": 6, "order": 1, "note": "or any speed", "is_borrow": True}])
        res = self.client.put(doc_url(self.guide), put_body(self.document, decks=ok), format="json")
        self.assertEqual(res.status_code, 200, res.content)

    def test_mentions_name_a_skill_on_global(self):
        errors = self.refuse(short_description="Take [[Groundwrk]] first")
        path, message = next(iter(errors))
        self.assertEqual(path, "short_description")
        self.assertIn("No skill on global has this name: [[Groundwrk]]", message)
        self.assertIn("did you mean Groundwork", message)
        decks = copy.deepcopy(self.document["decks"])
        decks[0]["slots"][0]["note"] = "[[Nothing Here]]"
        errors = self.refuse(decks=decks)
        self.assertEqual({p for p, _ in errors}, {"decks[0].slots[0].note"})
        # Known names pass, whatever their case.
        body = put_body(self.document, meta_analysis="[[groundwork]]")
        res = self.client.put(doc_url(self.guide), body, format="json")
        self.assertEqual(res.status_code, 200, res.content)

    def test_lengths_and_the_scenario(self):
        paths = {p for p, _ in self.refuse(style_ranking="x" * 121)}
        self.assertIn("style_ranking", paths)
        paths = {p for p, _ in self.refuse(team_comps=[{"text": "x" * 121, "note": ""}])}
        self.assertIn("team_comps[0].text", paths)
        errors = self.refuse(scenario=999)
        self.assertIn(("scenario", pvp_guide_document.NOT_IN_CATALOGUE), errors)

    def test_a_constraint_past_the_checks_is_a_sentence_not_a_500(self):
        with patch.object(pvp_guide_document, "errors", return_value=[]):
            errors = self.refuse(uma_recommendations=[self.uma(), self.uma()])
        self.assertEqual({p for p, _ in errors}, {""})
        self.assertIn("couldn't be saved", next(iter(errors))[1])


# ── Versions ─────────────────────────────────────────────────────────────────

class ConcurrencyTests(CalculatorTestCase):
    def setUp(self):
        self.client, self.user = editor_client("one")
        self.other, _ = editor_client("two")
        self.fixture = build_guide()
        self.guide = self.fixture.guide

    def test_a_save_over_a_newer_version_is_refused_with_the_current_document(self):
        mine = self.client.get(doc_url(self.guide)).json()
        theirs = self.other.get(doc_url(self.guide)).json()
        res = self.other.put(doc_url(self.guide), put_body(theirs, style_ranking="Theirs"), format="json")
        self.assertEqual(res.status_code, 200)
        res = self.client.put(doc_url(self.guide), put_body(mine, style_ranking="Mine"), format="json")
        self.assertEqual(res.status_code, 409)
        body = res.json()
        self.assertEqual(body["error"], "Someone saved this guide since you opened it.")
        self.assertEqual(body["current"]["version"], 1)
        self.assertEqual(body["current"]["style_ranking"], "Theirs")
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.style_ranking, "Theirs")
        # Keep mine: resend over the current version.
        res = self.client.put(doc_url(self.guide), put_body(body["current"], style_ranking="Mine"), format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["document"]["version"], 2)

    def test_the_version_is_required(self):
        document = self.client.get(doc_url(self.guide)).json()
        body = put_body(document)
        del body["version"]
        res = self.client.put(doc_url(self.guide), body, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["errors"][0]["path"], "version")


# ── Publish ──────────────────────────────────────────────────────────────────

class PublishTests(CalculatorTestCase):
    def setUp(self):
        self.client, self.user = editor_client()
        self.fixture = build_guide()
        self.guide = self.fixture.guide

    def test_publish_and_unpublish(self):
        visitor = APIClient()
        public = f"/pvp-guides/cm/{self.fixture.cm.id}"
        visitor.get("/calculator-data")
        self.assertEqual(visitor.get(public).status_code, 404)

        res = self.client.patch(f"/pvp-guides/{self.guide.pk}", {"is_published": True}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIs(res.json()["is_published"], True)
        self.assertEqual(res.json()["version"], 1)
        self.assertIsNone(public_payload_cache.read(), "publishing kept a stale has_guide")
        self.assertEqual(visitor.get(public).status_code, 200)
        self.assertEqual(self.guide.revisions.get().source, "publish")

        visitor.get("/calculator-data")
        res = self.client.patch(f"/pvp-guides/{self.guide.pk}", {"is_published": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(public_payload_cache.read())
        self.assertEqual(visitor.get(public).status_code, 404)
        self.assertEqual(list(self.guide.revisions.values_list("source", flat=True)), ["unpublish", "publish"])

    def test_the_body_is_checked(self):
        res = self.client.patch(f"/pvp-guides/{self.guide.pk}", {"is_published": "maybe"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["errors"][0]["path"], "is_published")


# ── Revisions ────────────────────────────────────────────────────────────────

@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class RevisionTests(CalculatorTestCase):
    def setUp(self):
        self.client, self.user = editor_client("one")
        self.other, self.other_user = editor_client("two")
        self.fixture = build_guide()
        self.guide = self.fixture.guide
        self.url = f"/pvp-guides/{self.guide.pk}/revisions"

    def save(self, client, **changes):
        document = client.get(doc_url(self.guide)).json()
        res = client.put(doc_url(self.guide), put_body(document, **changes), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["document"]

    def test_site_saves_by_one_person_coalesce_and_another_person_starts_a_row(self):
        self.save(self.client, style_ranking="One")
        self.save(self.client, style_ranking="Two")
        rows = self.client.get(self.url).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["number"], rows[0]["by"], rows[0]["is_you"], rows[0]["source"]),
                         (1, "one", True, "site"))
        self.assertEqual(rows[0]["counts"]["decks"], 1)
        self.assertEqual(self.client.get(f"{self.url}/1").json()["document"]["style_ranking"], "Two")

        self.save(self.other, style_ranking="Three")
        rows = self.client.get(self.url).json()
        self.assertEqual([(r["number"], r["by"], r["is_you"]) for r in rows], [(2, "two", False), (1, "one", True)])
        # Back to the first editor after the other: a new row again.
        self.save(self.client, style_ranking="Four")
        self.assertEqual([r["number"] for r in self.client.get(self.url).json()], [3, 2, 1])

    def test_a_pause_longer_than_the_window_starts_a_row(self):
        self.save(self.client, style_ranking="One")
        PvpGuideRevision.objects.update(
            saved_at=PvpGuideRevision.objects.get().saved_at - pvp_guide_revisions.COALESCE_WINDOW
            - datetime.timedelta(seconds=1),
        )
        self.save(self.client, style_ranking="Two")
        self.assertEqual([r["number"] for r in self.client.get(self.url).json()], [2, 1])

    def test_the_list_shows_the_handle_never_the_display_name(self):
        self.user.display_name = "Rhondal"
        self.user.save()
        self.save(self.client)
        self.assertEqual(self.client.get(self.url).json()[0]["by"], "one")
        self.assertNotIn("Rhondal", self.client.get(self.url).content.decode())

    def test_pruning_keeps_the_last_rows_and_every_publish(self):
        with patch.object(pvp_guide_revisions, "KEEP", 2):
            self.save(self.client, style_ranking="One")
            self.client.patch(f"/pvp-guides/{self.guide.pk}", {"is_published": True}, format="json")
            for label in ("Two", "Three", "Four"):
                self.save(self.other, style_ranking=label)
                self.save(self.client, style_ranking=label + "!")
        numbers = [r["number"] for r in self.client.get(self.url).json()]
        rows = {r["number"]: r["source"] for r in self.client.get(self.url).json()}
        self.assertEqual(numbers[:2], [8, 7])
        self.assertEqual(rows[2], "publish")
        self.assertEqual(sorted(rows), [2, 7, 8])
        res = self.client.get(f"{self.url}/1")
        self.assertEqual(res.status_code, 404)

    def test_restore_is_a_save_of_an_old_document(self):
        self.save(self.client, style_ranking="Keep this")
        self.save(self.other, style_ranking="Oops", uma_recommendations=[])
        old = self.client.get(f"{self.url}/1").json()["document"]
        current = self.client.get(doc_url(self.guide)).json()
        res = self.client.put(doc_url(self.guide), put_body(old, version=current["version"]), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.style_ranking, "Keep this")
        self.assertEqual(self.guide.uma_recommendations.count(), 1)

    def test_an_admin_save_records_a_revision_too(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client = self.client_class()
        self.client.force_login(admin)
        change_url = reverse("admin:calculatorapi_pvpguide_change", args=(self.guide.pk,))
        data = form_data(self.client.get(change_url))
        data["style_ranking"] = "From the admin"
        res = self.client.post(change_url, data)
        self.assertEqual(res.status_code, 302, res.content.decode()[:2000])
        row = self.guide.revisions.get()
        self.assertEqual((row.source, row.author_id, row.document["style_ranking"]),
                         ("admin", admin.id, "From the admin"))
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.version, 1)
        # Ticking Published on the page is recorded as a publish.
        data["is_published"] = "on"
        self.client.post(change_url, data)
        self.assertEqual(self.guide.revisions.order_by("-number").first().source, "publish")

    def test_the_admin_bulk_action_publishes_through_the_same_path(self):
        admin = make_user(username="admin", is_staff=True)
        admin.is_superuser = True
        admin.save()
        self.client = self.client_class()
        self.client.force_login(admin)
        APIClient().get("/calculator-data")
        res = self.client.post(
            reverse("admin:calculatorapi_pvpguide_changelist"),
            {"action": "publish_selected", "_selected_action": [self.guide.pk]},
        )
        self.assertEqual(res.status_code, 302)
        self.guide.refresh_from_db()
        self.assertTrue(self.guide.is_published)
        self.assertEqual(self.guide.revisions.get().source, "publish")
        self.assertIsNone(public_payload_cache.read())


# ── Actions ──────────────────────────────────────────────────────────────────

@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class ActionTests(CalculatorTestCase):
    def setUp(self):
        self.client, self.user = editor_client()
        self.fixture = build_guide()
        self.guide = self.fixture.guide

    def action(self, name, **body):
        return self.client.post(f"/pvp-guides/{self.guide.pk}/actions/{name}", body, format="json")

    def test_copy_from_previews_then_applies(self):
        target = PvpGuide.objects.create(league_of_heroes=make_league_of_heroes())
        url = f"/pvp-guides/{target.pk}/actions/copy-from"
        res = self.client.post(url, {"source": self.guide.pk}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["counts"]["decks"], 1)
        self.assertEqual(target.decks.count(), 0)
        self.assertEqual(target.revisions.count(), 0)

        res = self.client.post(url, {"source": self.guide.pk, "preview": False, "sections": ["decks", "overview"]},
                               format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(set(res.json()), {"counts", "document", "guide"})
        self.assertEqual(target.decks.count(), 1)
        self.assertEqual(target.uma_recommendations.count(), 0)
        self.assertEqual(res.json()["document"]["short_description"], "Mile on turf.")
        self.assertEqual(target.revisions.get().source, "copy")

        res = self.client.post(url, {"source": target.pk}, format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(url, {"source": 999}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_load_from_the_sheet(self):
        Uma.objects.create(name="Seiun Sky (Sheet)")
        SupportCard.objects.create(name="Narita Top Road", game_id=30086)
        self.fixture.cm.cm_number = 14
        self.fixture.cm.save()

        def fake_reader(tab):
            if tab == pvp_sheet.IMPORT_TAB:
                return SheetLoaderTests.import_block(SheetLoaderTests)
            return [["CM #99"]]

        with patch.object(pvp_sheet, "read_tab", fake_reader):
            res = self.action("sheet")
            self.assertEqual(res.status_code, 200, res.content)
            self.assertEqual(set(res.json()), {"summary"})
            self.assertIs(res.json()["summary"]["preview"], True)
            self.guide.refresh_from_db()
            self.assertEqual(self.guide.style_ranking, "Front >> End")

            res = self.action("sheet", preview=False)
            self.assertEqual(res.status_code, 200, res.content)
            self.assertEqual(set(res.json()), {"summary", "document", "guide"})
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.style_ranking, "End >= Front")
        self.assertEqual(res.json()["document"]["decks"][0]["label"], "Beginner")
        self.assertEqual(self.guide.revisions.get().source, "sheet")

        self.fixture.cm.cm_number = 77
        self.fixture.cm.save()
        with patch.object(pvp_sheet, "read_tab", fake_reader):
            res = self.action("sheet")
        self.assertEqual(res.status_code, 400)
        self.assertIn("no guide for CM #77", res.json()["error"])

    def test_track_greens(self):
        right = make_family("Right-Handed", 20001, "rotation==1")
        make_family("Left-Handed", 20002, "rotation==2")
        res = self.action("track-greens")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual([s["name"] for s in body["add"]], ["Right-Handed ○"])
        self.assertEqual(body["add"][0]["version"], "○ white")
        self.assertEqual(body["already"], [])
        self.assertEqual(self.guide.skill_recommendations.count(), 1)

        res = self.action("track-greens", preview=False)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual([s["id"] for s in res.json()["added"]], [right.id])
        rows = res.json()["document"]["skill_recommendations"]
        self.assertEqual((rows[-1]["style"], rows[-1]["category"], rows[-1]["skill"]), ("everyone", "green", right.id))
        self.assertEqual(self.guide.revisions.get().source, "track_greens")
        res = self.action("track-greens")
        self.assertEqual([s["name"] for s in res.json()["already"]], ["Right-Handed ○"])


# ── Cost ─────────────────────────────────────────────────────────────────────

class QueryCountTests(CalculatorTestCase):
    def test_a_save_costs_the_same_whatever_the_guide_holds(self):
        client, _ = editor_client("one")
        # The second save is another editor's, so both saves write a revision
        # row and prune, instead of the second coalescing into the first.
        other, _ = editor_client("two")
        fixture = build_guide()
        guide = fixture.guide
        document = client.get(doc_url(guide)).json()
        # The same KINDS of write on both sides (a mention to check, a deck to
        # insert, a deck to keep), so only the row counts differ.
        document["short_description"] = "Take [[Groundwork]] first."
        document["decks"].append({"id": None, "style": "pace", "label": "Deck", "note": "",
                                  "slots": [{"slot": 1, "support_card": fixture.card.id}]})
        with CaptureQueriesContext(connection) as small:
            res = client.put(doc_url(guide), put_body(document), format="json")
        self.assertEqual(res.status_code, 200, res.content)

        document = res.json()["document"]
        umas = [Uma.objects.create(name=f"Uma {i}") for i in range(12)]
        skills = [make_skill(f"Skill {i}", 200000 + i) for i in range(12)]
        document["uma_recommendations"] += [
            {"style": style, "uma": uma.id, "stars": 3, "role": "", "note": "[[Groundwork]]"}
            for uma in umas for style in ("front", "pace", "late", "end")
        ]
        document["skill_recommendations"] += [
            {"style": "everyone", "category": "green", "tier": 1, "skill": skill.id, "note": ""}
            for skill in skills
        ]
        document["decks"] += [
            {"id": None, "style": "late", "label": f"Deck {i}", "note": "",
             "slots": [{"slot": n, "support_card": fixture.card.id} for n in range(1, 7)]}
            for i in range(6)
        ]
        with CaptureQueriesContext(connection) as large:
            res = other.put(doc_url(guide), put_body(document), format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(guide.decks.count(), 8)
        self.assertEqual(len(small), len(large), "a save costs a query per row")


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class CatalogueFieldsTests(CalculatorTestCase):
    """Plain file storage: CI's default storage is the Spaces bucket, which
    cannot build a URL for the pictures these rows name."""

    def test_the_catalogue_carries_game_ids_and_skill_versions(self):
        Uma.objects.create(name="Maruzensky", game_id=100101, image="umas/m.png")
        SupportCard.objects.create(name="Kitasan Black", game_id=30086, image="cards/k.png")
        make_skill("Groundwork", 100061)
        body = APIClient().get("/pvp-catalogue").json()
        self.assertEqual(body["umas"][0]["game_id"], 100101)
        self.assertEqual(body["support_cards"][0]["game_id"], 30086)
        skill = body["skills"][0]
        self.assertEqual((skill["game_id"], skill["version"]), (100061, "○ white"))
