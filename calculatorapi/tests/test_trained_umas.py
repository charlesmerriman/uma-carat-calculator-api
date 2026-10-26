"""Veterans: the model's rules, the /trained-umas routes, the import, and the links from teams and runs."""

import gzip
import json
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi import gallery, public_payload_cache, trained_umas
from calculatorapi.views import trained_uma as trained_uma_view
from calculatorapi.models import (
    PvpRunRunner,
    PvpTeam,
    PvpTeamMember,
    TrainedUma,
    TrainedUmaSkill,
)
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import auth_client, make_champions_meeting, make_user
from calculatorapi.tests.test_pvp_catalogue import make_skill
from calculatorapi.tests.test_pvp_teams import make_card, make_uma

STATS = {"speed": 1200, "stamina": 800, "power": 1000, "guts": 400, "wit": 900}


class VeteranFixture(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes

    def setUp(self):
        self.user = make_user("trainer")
        self.client, _ = auth_client(self.user)
        self.oguri = make_uma("Oguri Cap", 100601)
        self.oguri_xmas = make_uma("Oguri Cap (Christmas)", 100602)
        self.maruzensky = make_uma("Maruzensky", 100401)
        self.skill = make_skill("Corner Adept", 200331)
        self.other_skill = make_skill("Straightaway Adept", 200341)
        self.cm = make_champions_meeting()

    def add(self, client=None, **body):
        body.setdefault("uma", self.oguri.id)
        for stat, value in STATS.items():
            body.setdefault(stat, value)
        return (client or self.client).post("/trained-umas", body, format="json")

    def item(self, key, uma=None, **extra):
        """One import item, as the page sends it after reading the game's file."""
        return {
            "import_key": key, "uma": (uma or self.oguri).id, **STATS, "rank_score": 12000,
            "apt": {"turf": 7, "mile": 8}, "skills": [{"skill": self.skill.id, "level": 2}], **extra,
        }

    def run_import(self, *items):
        return self.client.post("/trained-umas/import", {"items": list(items)}, format="json")

    def team_body(self, *members):
        return {"kind": "cm", "event": self.cm.id, "members": list(members)}


class ModelTests(VeteranFixture):

    def test_an_import_needs_its_key_and_a_hand_entry_has_none(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrainedUma.objects.create(user=self.user, uma=self.oguri, source="import", **STATS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrainedUma.objects.create(user=self.user, uma=self.oguri, source="manual", import_key=5, **STATS)

    def test_one_veteran_per_game_id_per_account(self):
        TrainedUma.objects.create(user=self.user, uma=self.oguri, source="import", import_key=5, **STATS)
        TrainedUma.objects.create(user=make_user("rival"), uma=self.oguri, source="import", import_key=5, **STATS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrainedUma.objects.create(user=self.user, uma=self.oguri, source="import", import_key=5, **STATS)

    def test_an_aptitude_is_one_to_eight_or_unknown(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TrainedUma.objects.create(user=self.user, uma=self.oguri, source="manual", apt_turf=9, **STATS)


class ByHandTests(VeteranFixture):

    def test_add_returns_the_veteran_as_sent(self):
        response = self.add(label="CM 21 Oguri", apt={"turf": 8, "long": None}, skills=[{"skill": self.skill.id}])
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(
            (body["uma"], body["label"], body["source"], body["import_key"]),
            (self.oguri.id, "CM 21 Oguri", "manual", None),
        )
        self.assertEqual({stat: body[stat] for stat in STATS}, STATS)
        self.assertEqual(body["apt"]["turf"], 8)
        self.assertIsNone(body["apt"]["long"])
        self.assertEqual(body["skills"], [{"skill": self.skill.id, "level": 1}])

    def test_refusals(self):
        cases = [
            {"speed": 2001},
            {"apt": {"turf": 9}},
            {"uma": 9999},
            {"skills": [{"skill": 9999}]},
            {"skills": [{"skill": self.skill.id}, {"skill": self.skill.id}]},
            {"label": "x" * 41},
        ]
        for body in cases:
            with self.subTest(body=body):
                response = self.add(**body)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("error", response.json())
        self.assertFalse(TrainedUma.objects.exists())

    def test_there_is_no_cap(self):
        """Owner, 2026-10-26: no limit. 300 is past every cap this route ever had."""
        TrainedUma.objects.bulk_create(
            TrainedUma(user=self.user, uma=self.oguri, source="manual", **STATS) for _ in range(300)
        )
        self.assertEqual(self.add().status_code, 201)

    def test_edit_changes_what_is_sent_and_replaces_skills(self):
        veteran_id = self.add(skills=[{"skill": self.skill.id}]).json()["id"]
        response = self.client.patch(
            f"/trained-umas/{veteran_id}",
            {"speed": 1100, "skills": [{"skill": self.other_skill.id, "level": 3}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["speed"], 1100)
        self.assertEqual(response.json()["stamina"], STATS["stamina"])
        self.assertEqual(response.json()["skills"], [{"skill": self.other_skill.id, "level": 3}])

    def test_a_veteran_cannot_change_uma(self):
        veteran_id = self.add().json()["id"]
        response = self.client.patch(f"/trained-umas/{veteran_id}", {"uma": self.maruzensky.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_someone_elses_veteran_is_a_404(self):
        veteran_id = self.add().json()["id"]
        rival, _ = auth_client(make_user("rival"))
        self.assertEqual(rival.get(f"/trained-umas/{veteran_id}").status_code, 404)
        self.assertEqual(rival.delete(f"/trained-umas/{veteran_id}").status_code, 404)
        self.assertEqual(rival.get("/trained-umas").json(), [])

    def test_guests_cannot_reach_any_of_it(self):
        guest = APIClient()
        self.assertEqual(guest.get("/trained-umas").status_code, 401)
        self.assertEqual(self.add(client=guest).status_code, 401)


class ImportTests(VeteranFixture):

    def test_a_first_import_adds_every_veteran(self):
        response = self.run_import(self.item(12), self.item(13, uma=self.maruzensky))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual({key: body[key] for key in ("created", "updated", "removed", "kept")},
                         {"created": 2, "updated": 0, "removed": 0, "kept": 0})
        veteran = TrainedUma.objects.get(import_key=12)
        self.assertEqual((veteran.source, veteran.rank_score, veteran.apt_mile), ("import", 12000, 8))
        self.assertEqual(list(veteran.skills.values_list("skill_id", "level")), [(self.skill.id, 2)])

    def test_a_re_import_updates_in_place_keeps_labels_and_never_touches_hand_entries(self):
        self.run_import(self.item(12))
        veteran = TrainedUma.objects.get(import_key=12)
        self.client.patch(f"/trained-umas/{veteran.id}", {"label": "Main Oguri"}, format="json")
        manual_id = self.add().json()["id"]

        body = self.run_import(self.item(12, speed=1150, skills=[{"skill": self.other_skill.id, "level": 1}])).json()
        self.assertEqual((body["created"], body["updated"]), (0, 1))
        veteran.refresh_from_db()
        self.assertEqual((veteran.id, veteran.speed, veteran.label), (veteran.id, 1150, "Main Oguri"))
        self.assertEqual(list(veteran.skills.values_list("skill_id", flat=True)), [self.other_skill.id])
        self.assertTrue(TrainedUma.objects.filter(id=manual_id).exists())

    def test_a_veteran_gone_from_the_game_is_removed_unless_a_logged_run_links_it(self):
        self.run_import(self.item(12), self.item(13, uma=self.maruzensky))
        linked = TrainedUma.objects.get(import_key=12)
        put = self.client.put(
            f"/pvp-runs/cm/{self.cm.id}/day/1/ticket/1",
            {"runners": [{"position": 0, "uma": self.oguri.id, "style": "pace", "trained_uma": linked.id}]},
            format="json",
        )
        self.assertEqual(put.status_code, 200, put.content)

        body = self.run_import(self.item(14)).json()
        self.assertEqual(
            {key: body[key] for key in ("created", "removed", "kept")}, {"created": 1, "removed": 1, "kept": 1}
        )
        linked.refresh_from_db()
        self.assertTrue(linked.missing_from_import)
        self.assertFalse(TrainedUma.objects.filter(import_key=13).exists())
        self.assertEqual(PvpRunRunner.objects.get().trained_uma_id, linked.id)

        # Back in the next file: no longer flagged.
        self.run_import(self.item(12), self.item(14))
        linked.refresh_from_db()
        self.assertFalse(linked.missing_from_import)

    def test_an_import_has_no_count_limit(self):
        response = self.run_import(*[self.item(key) for key in range(1, 301)])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(TrainedUma.objects.filter(source="import").count(), 300)

    def test_a_game_veteran_keeps_the_games_numbers(self):
        self.run_import(self.item(12))
        veteran = TrainedUma.objects.get(import_key=12)
        self.assertEqual(self.client.patch(f"/trained-umas/{veteran.id}", {"speed": 1}, format="json").status_code, 400)
        relabel = self.client.patch(f"/trained-umas/{veteran.id}", {"label": "Ace"}, format="json")
        self.assertEqual(relabel.status_code, 200)

    def test_the_same_game_id_twice_is_refused(self):
        self.assertEqual(self.run_import(self.item(12), self.item(12)).status_code, 400)


class LinkTests(VeteranFixture):

    def member(self, uma, veteran=None, position=0):
        return {"position": position, "uma": uma.id, "style": "pace", "cards": [], "trained_uma": veteran}

    def test_a_team_member_links_a_veteran_of_her_own_outfit(self):
        veteran_id = self.add().json()["id"]
        response = self.client.post(
            "/pvp-teams", self.team_body(self.member(self.oguri, veteran_id)), format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["members"][0]["trained_uma"], veteran_id)

    def test_a_link_to_another_outfit_or_someone_elses_veteran_is_refused(self):
        veteran_id = self.add().json()["id"]
        rival, _ = auth_client(make_user("rival"))
        for client, member in [
            (self.client, self.member(self.oguri_xmas, veteran_id)),
            (rival, self.member(self.oguri, veteran_id)),
        ]:
            with self.subTest(member=member):
                response = client.post(
                    "/pvp-teams", {"kind": "cm", "event": self.cm.id, "members": [member]}, format="json"
                )
                self.assertEqual(response.status_code, 400, response.content)

    def test_deleting_a_veteran_keeps_the_uma_on_teams_and_runs(self):
        veteran_id = self.add().json()["id"]
        self.client.post("/pvp-teams", self.team_body(self.member(self.oguri, veteran_id)), format="json")
        self.client.put(
            f"/pvp-runs/cm/{self.cm.id}/day/1/ticket/1",
            {"runners": [{"position": 0, "uma": self.oguri.id, "style": "pace", "trained_uma": veteran_id}]},
            format="json",
        )
        self.assertEqual(self.client.delete(f"/trained-umas/{veteran_id}").status_code, 204)
        self.assertIsNone(PvpTeamMember.objects.get().trained_uma_id)
        self.assertEqual(PvpRunRunner.objects.get().uma_id, self.oguri.id)
        self.assertIsNone(PvpRunRunner.objects.get().trained_uma_id)


@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class LifecycleTests(VeteranFixture):

    def test_veteran_writes_keep_the_public_payload(self):
        APIClient().get("/calculator-data")
        veteran_id = self.add().json()["id"]
        self.run_import(self.item(12))
        self.client.delete(f"/trained-umas/{veteran_id}")
        self.assertIsNotNone(public_payload_cache.read(), "a veteran save dropped /calculator-data")

    def test_deleting_the_account_deletes_its_veterans(self):
        self.add(skills=[{"skill": self.skill.id}])
        self.assertEqual(self.client.delete("/account").status_code, 204)
        self.assertFalse(TrainedUma.objects.exists())
        self.assertFalse(TrainedUmaSkill.objects.exists())

    def test_a_gallery_post_never_carries_a_veteran(self):
        """A post is a plan, not a brag sheet: the snapshot names umas and cards only."""
        veteran_id = self.add().json()["id"]
        cards = [make_card(f"Card {n}", 30000 + n) for n in range(1, 7)]
        deck = [{"slot": slot, "support_card": card.id} for slot, card in enumerate(cards, start=1)]
        members = [
            {"position": position, "uma": uma.id, "style": "pace", "cards": deck,
             "trained_uma": veteran_id if uma == self.oguri else None}
            for position, uma in enumerate([self.oguri, self.maruzensky, make_uma("Gold Ship", 100701)])
        ]
        response = self.client.post("/pvp-teams", self.team_body(*members), format="json")
        team_id = response.json()["id"]
        snapshot = gallery.snapshot_from_team(PvpTeam.objects.get(id=team_id))
        self.assertTrue(all("trained_uma" not in member for member in snapshot))


OWN = 311943848596
OTHER = 206074645533


def game_object(**extra):
    """One veteran's raw game object, in umadump's shape, trimmed."""
    return {
        "viewer_id": OWN, "trained_chara_id": 12, "card_id": 100601, "memo": "Ace", "use_type": 0,
        "owner_viewer_id": 0, "factor_info_array": [{"factor_id": 101, "level": 3}],
        "succession_chara_array": [
            {"position_id": 10, "card_id": 100901, "owner_viewer_id": OWN},
            {"position_id": 20, "card_id": 100101, "owner_viewer_id": OTHER, "owner_trainer_name": "Someone"},
        ],
        "race_result_list": [{"turn": 12, "result_rank": 1}],
        **extra,
    }


class RawGameDataTests(VeteranFixture):
    # DRF's client.generic returns a response; pylint reads Django's stub as a request.
    # pylint: disable=no-member

    def test_strip_keeps_your_id_and_drops_other_players_at_any_depth(self):
        cleaned, borrowed = trained_umas.strip_other_players(game_object())
        self.assertFalse(borrowed)
        self.assertEqual(cleaned["viewer_id"], OWN)
        self.assertEqual(cleaned["succession_chara_array"][0]["owner_viewer_id"], OWN)
        self.assertNotIn("owner_viewer_id", cleaned["succession_chara_array"][1])
        self.assertNotIn("owner_trainer_name", cleaned["succession_chara_array"][1])
        self.assertNotIn(str(OTHER), json.dumps(cleaned))
        # Everything else is kept as given.
        self.assertEqual(cleaned["factor_info_array"], [{"factor_id": 101, "level": 3}])
        self.assertEqual(cleaned["race_result_list"], [{"turn": 12, "result_rank": 1}])

    def test_a_borrowed_veteran_is_kept_and_flagged_without_its_owner(self):
        cleaned, borrowed = trained_umas.strip_other_players(game_object(use_type=1, owner_viewer_id=OTHER))
        self.assertTrue(borrowed)
        self.assertNotIn("owner_viewer_id", cleaned)

    def test_an_import_keeps_the_raw_object_memo_and_flag_but_never_another_players_id(self):
        response = self.run_import(self.item(12, game_data=game_object(use_type=1, owner_viewer_id=OTHER)))
        self.assertEqual(response.status_code, 200, response.content)
        veteran = TrainedUma.objects.get(import_key=12)
        self.assertEqual((veteran.memo, veteran.is_borrowed), ("Ace", True))
        self.assertEqual(veteran.game_data["viewer_id"], OWN)
        self.assertNotIn(str(OTHER), json.dumps(veteran.game_data))

        listed = self.client.get("/trained-umas").json()[0]
        self.assertNotIn("game_data", listed)
        self.assertEqual((listed["memo"], listed["is_borrowed"], listed["has_game_data"]), ("Ace", True, True))
        detail = self.client.get(f"/trained-umas/{veteran.id}").json()
        self.assertEqual(detail["game_data"]["race_result_list"], [{"turn": 12, "result_rank": 1}])

    def test_a_veteran_too_large_to_keep_is_refused(self):
        huge = game_object(race_result_list=[{"turn": n, "note": "x" * 100} for n in range(1000)])
        self.assertEqual(self.run_import(self.item(12, game_data=huge)).status_code, 400)
        self.assertFalse(TrainedUma.objects.exists())

    def gzipped(self, body):
        return self.client.generic(
            "POST", "/trained-umas/import", gzip.compress(json.dumps(body).encode()),
            content_type="application/json", HTTP_CONTENT_ENCODING="gzip",
        )

    def test_a_gzipped_import_is_read(self):
        response = self.gzipped({"items": [self.item(12, game_data=game_object())]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(TrainedUma.objects.get().memo, "Ace")

    def test_a_broken_or_oversized_gzip_is_refused(self):
        broken = self.client.generic(
            "POST", "/trained-umas/import", b"not gzip", content_type="application/json", HTTP_CONTENT_ENCODING="gzip"
        )
        self.assertEqual(broken.status_code, 400)
        with mock.patch.object(trained_uma_view, "IMPORT_MAX_BYTES", 100):
            self.assertEqual(self.gzipped({"items": [self.item(12)]}).status_code, 400)
        self.assertFalse(TrainedUma.objects.exists())

    def test_a_cross_origin_page_may_send_a_gzipped_import(self):
        preflight = self.client.options(
            "/trained-umas/import",
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-encoding,content-type,authorization",
        )
        self.assertIn("content-encoding", preflight.headers.get("access-control-allow-headers", ""))

    def test_the_list_is_newest_trained_first_after_one_import(self):
        """One import saves every row at the same instant; the game's time orders them."""
        self.run_import(
            self.item(1, game_data=game_object(trained_chara_id=1, create_time="2025-08-01 10:00:00")),
            self.item(2, game_data=game_object(trained_chara_id=2, create_time="2026-09-01 10:00:00")),
            self.item(3, game_data=game_object(trained_chara_id=3, create_time="2025-12-01 10:00:00")),
        )
        listed = self.client.get("/trained-umas").json()
        self.assertEqual([veteran["import_key"] for veteran in listed], [2, 3, 1])
        self.assertEqual(listed[0]["trained_at"], "2026-09-01T10:00:00Z")
