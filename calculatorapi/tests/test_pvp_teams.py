"""PvP teams: the model's constraints and the /pvp-teams routes."""

from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache, pvp_catalogue_cache
from calculatorapi.models import (
    PVP_TEAM_CAP_PER_EVENT,
    PvpTeam,
    PvpTeamCard,
    PvpTeamMember,
    SupportCard,
    Uma,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client,
    make_champions_meeting,
    make_league_of_heroes,
    make_user,
)


def make_uma(name, game_id):
    return Uma.objects.create(name=name, game_id=game_id, image=f"umas/{game_id}.png")


def make_card(name, game_id):
    return SupportCard.objects.create(
        name=name, game_id=game_id, image=f"support_cards/{game_id}.png"
    )


class PvpTeamFixture(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """Two events, a few umas (two outfits of one character), seven cards."""

    def setUp(self):
        self.user = make_user("trainer")
        self.client, _ = auth_client(self.user)
        self.cm = make_champions_meeting()
        self.loh = make_league_of_heroes()
        self.maruzensky = make_uma("Maruzensky", 100401)
        self.oguri = make_uma("Oguri Cap", 100601)
        self.oguri_xmas = make_uma("Oguri Cap (Christmas)", 100602)
        self.gold_ship = make_uma("Gold Ship", 100701)
        self.cards = [make_card(f"Card {n}", 30000 + n) for n in range(1, 8)]

    def deck(self, count=6):
        return [
            {"slot": slot, "support_card": card.id}
            for slot, card in enumerate(self.cards[:count], start=1)
        ]

    def members(self, *umas, cards=True):
        return [
            {
                "position": position,
                "uma": uma.id,
                "style": "front",
                "cards": self.deck() if cards else [],
            }
            for position, uma in enumerate(umas)
        ]

    def create(self, client=None, **body):
        body.setdefault("kind", "cm")
        body.setdefault("event", self.cm.id)
        return (client or self.client).post("/pvp-teams", body, format="json")


class ModelConstraintTests(PvpTeamFixture):

    def test_a_team_needs_exactly_one_event(self):
        for fields in ({}, {"champions_meeting": self.cm, "league_of_heroes": self.loh}):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PvpTeam.objects.create(user=self.user, **fields)

    def test_one_pin_per_account(self):
        PvpTeam.objects.create(user=self.user, champions_meeting=self.cm, is_focus=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpTeam.objects.create(user=self.user, league_of_heroes=self.loh, is_focus=True)

    def test_a_member_never_runs_everyone(self):
        team = PvpTeam.objects.create(user=self.user, champions_meeting=self.cm)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpTeamMember.objects.create(team=team, position=0, uma=self.oguri, style="everyone")

    def test_slot_out_of_range_is_refused(self):
        team = PvpTeam.objects.create(user=self.user, champions_meeting=self.cm)
        member = PvpTeamMember.objects.create(team=team, position=0, uma=self.oguri, style="pace")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpTeamCard.objects.create(member=member, slot=7, support_card=self.cards[0])


class CreateTests(PvpTeamFixture):

    def test_create_returns_the_team_as_sent(self):
        response = self.create(
            name="Aquarius",
            members=self.members(self.maruzensky, self.oguri),
            notes=[{"text": "Pull Kitasan", "color": "yellow"}],
        )
        self.assertEqual(response.status_code, 201, response.data)
        body = response.json()
        self.assertEqual(body["kind"], "cm")
        self.assertEqual(body["event"], self.cm.id)
        self.assertEqual(body["name"], "Aquarius")
        self.assertFalse(body["is_focus"])
        self.assertEqual(body["notes"], [{"text": "Pull Kitasan", "color": "yellow"}])
        self.assertEqual([m["uma"] for m in body["members"]], [self.maruzensky.id, self.oguri.id])
        self.assertEqual(body["members"][0]["cards"], self.deck())

    def test_a_blank_team_is_allowed(self):
        response = self.create(kind="loh", event=self.loh.id)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["members"], [])

    def test_an_unknown_event_is_refused(self):
        for body in ({"kind": "cm", "event": 9999}, {"kind": "tt", "event": self.cm.id}):
            with self.subTest(body=body):
                response = self.client.post("/pvp-teams", body, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())

    def test_the_cap_is_per_event_and_on_create_only(self):
        for _ in range(PVP_TEAM_CAP_PER_EVENT):
            self.assertEqual(self.create().status_code, 201)
        refused = self.create()
        self.assertEqual(refused.status_code, 400)
        self.assertIn(str(PVP_TEAM_CAP_PER_EVENT), refused.json()["error"])
        # Another event has its own count.
        self.assertEqual(self.create(kind="loh", event=self.loh.id).status_code, 201)
        # A team at the cap still saves.
        team = PvpTeam.objects.filter(champions_meeting=self.cm).first()
        response = self.client.put(
            f"/pvp-teams/{team.id}", {"members": self.members(self.oguri)}, format="json"
        )
        self.assertEqual(response.status_code, 200)

    def test_guests_cannot_save(self):
        response = APIClient().post(
            "/pvp-teams", {"kind": "cm", "event": self.cm.id}, format="json"
        )
        self.assertEqual(response.status_code, 401)


class RuleTests(PvpTeamFixture):
    """What a save may never hold, whether creating or replacing."""

    def assert_refused(self, members, fragment):
        response = self.create(members=members)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(fragment, response.json()["error"])
        self.assertFalse(PvpTeam.objects.exists())

    def test_two_outfits_of_one_character(self):
        self.assert_refused(self.members(self.oguri, self.oguri_xmas), "two outfits")

    def test_the_same_uma_twice(self):
        members = self.members(self.oguri, self.maruzensky)
        members[1]["uma"] = self.oguri.id
        self.assert_refused(members, "same uma twice")

    def test_the_same_card_twice_in_one_deck(self):
        members = self.members(self.oguri)
        members[0]["cards"][5]["support_card"] = self.cards[0].id   # the borrow slot too
        self.assert_refused(members, "same card twice")

    def test_two_cards_in_one_slot(self):
        members = self.members(self.oguri)
        members[0]["cards"][1]["slot"] = 1
        self.assert_refused(members, "same slot")

    def test_a_fourth_uma(self):
        members = self.members(self.oguri, self.maruzensky, self.gold_ship, cards=False)
        members.append({"position": 2, "uma": self.oguri_xmas.id, "style": "end"})
        self.assert_refused(members, "3 umas")

    def test_style_everyone(self):
        members = self.members(self.oguri)
        members[0]["style"] = "everyone"
        response = self.create(members=members)
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_card_or_uma(self):
        members = self.members(self.oguri)
        members[0]["cards"][0]["support_card"] = 99999
        self.assert_refused(members, "support cards")
        members = self.members(self.oguri)
        members[0]["uma"] = 99999
        self.assert_refused(members, "umas")

    def test_the_same_card_on_two_umas_is_fine(self):
        response = self.create(members=self.members(self.oguri, self.maruzensky))
        self.assertEqual(response.status_code, 201)

    def test_note_limits(self):
        cases = {
            "eleven notes": [{"text": "x", "color": "pink"}] * 11,
            "a long note": [{"text": "x" * 1001, "color": "pink"}],
            "an unknown colour": [{"text": "x", "color": "purple"}],
        }
        for label, notes in cases.items():
            with self.subTest(label):
                self.assertEqual(self.create(notes=notes).status_code, 400)
        ok = self.create(notes=[{"text": "  spaced\nout ", "color": "green"}] * 10)
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["notes"][0]["text"], "  spaced\nout ")


class DetailTests(PvpTeamFixture):

    def setUp(self):
        super().setUp()
        self.team_id = self.create(
            members=self.members(self.oguri),
            notes=[{"text": "keep me", "color": "blue"}],
        ).json()["id"]

    def test_put_replaces_members_and_keeps_notes_when_left_out(self):
        response = self.client.put(
            f"/pvp-teams/{self.team_id}",
            {"members": self.members(self.maruzensky, self.gold_ship, cards=False)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([m["uma"] for m in body["members"]], [self.maruzensky.id, self.gold_ship.id])
        self.assertEqual(body["notes"], [{"text": "keep me", "color": "blue"}])
        self.assertEqual(PvpTeamCard.objects.count(), 0)

    def test_put_with_notes_replaces_them(self):
        response = self.client.put(
            f"/pvp-teams/{self.team_id}", {"members": [], "notes": []}, format="json"
        )
        self.assertEqual(response.json()["notes"], [])

    def test_a_refused_put_changes_nothing(self):
        response = self.client.put(
            f"/pvp-teams/{self.team_id}",
            {"members": self.members(self.oguri, self.oguri_xmas)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        team = self.client.get(f"/pvp-teams/{self.team_id}").json()
        self.assertEqual([m["uma"] for m in team["members"]], [self.oguri.id])
        self.assertEqual(len(team["members"][0]["cards"]), 6)

    def test_patch_renames(self):
        response = self.client.patch(
            f"/pvp-teams/{self.team_id}", {"name": "Main"}, format="json"
        )
        self.assertEqual(response.json()["name"], "Main")

    def test_patch_moves_the_pin(self):
        other_id = self.create(kind="loh", event=self.loh.id).json()["id"]
        self.client.patch(f"/pvp-teams/{self.team_id}", {"is_focus": True}, format="json")
        self.client.patch(f"/pvp-teams/{other_id}", {"is_focus": True}, format="json")
        pinned = list(PvpTeam.objects.filter(is_focus=True).values_list("id", flat=True))
        self.assertEqual(pinned, [other_id])
        self.client.patch(f"/pvp-teams/{other_id}", {"is_focus": False}, format="json")
        self.assertFalse(PvpTeam.objects.filter(is_focus=True).exists())

    def test_delete(self):
        self.assertEqual(self.client.delete(f"/pvp-teams/{self.team_id}").status_code, 204)
        self.assertFalse(PvpTeam.objects.exists())

    def test_someone_elses_team_is_a_404(self):
        stranger, _ = auth_client(make_user("stranger"))
        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(stranger, method)(
                    f"/pvp-teams/{self.team_id}", {"members": []}, format="json"
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"error": "Team not found"})
        self.assertEqual(stranger.get("/pvp-teams").json(), [])
        self.assertTrue(PvpTeam.objects.filter(id=self.team_id).exists())

    def test_list_returns_every_event(self):
        self.create(kind="loh", event=self.loh.id)
        kinds = [team["kind"] for team in self.client.get("/pvp-teams").json()]
        self.assertEqual(kinds, ["cm", "loh"])


class LifecycleTests(PvpTeamFixture):

    def test_a_team_save_keeps_both_caches(self):
        APIClient().get("/calculator-data")
        APIClient().get("/pvp-catalogue")
        team_id = self.create(members=self.members(self.oguri)).json()["id"]
        self.client.put(f"/pvp-teams/{team_id}", {"members": []}, format="json")
        self.client.patch(f"/pvp-teams/{team_id}", {"is_focus": True}, format="json")
        self.client.delete(f"/pvp-teams/{team_id}")
        self.assertIsNotNone(public_payload_cache.read(), "a team save dropped /calculator-data")
        self.assertIsNotNone(pvp_catalogue_cache.read(), "a team save dropped the catalogue")

    def test_deleting_the_account_deletes_its_teams(self):
        self.create(members=self.members(self.oguri))
        self.assertEqual(self.client.delete("/account").status_code, 204)
        self.assertFalse(PvpTeam.objects.exists())
        self.assertFalse(PvpTeamMember.objects.exists())
