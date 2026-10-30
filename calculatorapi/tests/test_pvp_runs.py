"""Logged CM / LoH results: the model's constraints and the /pvp-runs routes."""

from django.db import IntegrityError, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from calculatorapi import public_payload_cache
from calculatorapi.models import PvpRun, PvpRunRace, PvpRunRunner
from calculatorapi.tests.base import CalculatorTestCase, PLAIN_TEST_STORAGES
from calculatorapi.tests.factories import (
    auth_client,
    make_champions_meeting,
    make_league_of_heroes,
    make_user,
)
from calculatorapi.tests.test_pvp_teams import make_uma


class PvpRunFixture(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """Two events and four umas, two of them outfits of one character."""

    def setUp(self):
        self.user = make_user("trainer")
        self.client, _ = auth_client(self.user)
        self.cm = make_champions_meeting()
        self.loh = make_league_of_heroes()
        self.maruzensky = make_uma("Maruzensky", 100401)
        self.oguri = make_uma("Oguri Cap", 100601)
        self.oguri_xmas = make_uma("Oguri Cap (Christmas)", 100602)
        self.gold_ship = make_uma("Gold Ship", 100701)

    def runners(self, *umas):
        return [
            {"position": position, "uma": uma.id, "style": "front"}
            for position, uma in enumerate(umas)
        ]

    def ticket_url(self, day=1, ticket=1, kind="cm", event=None):
        event = event or (self.cm.id if kind == "cm" else self.loh.id)
        return f"/pvp-runs/{kind}/{event}/day/{day}/ticket/{ticket}"

    def final_url(self, kind="cm", event=None):
        return f"/pvp-runs/{kind}/{event or self.cm.id}/final"

    def put(self, url, client=None, **body):
        body.setdefault("runners", self.runners(self.maruzensky, self.oguri, self.gold_ship))
        return (client or self.client).put(url, body, format="json")

    def races(self, *winners):
        return [{"race": number, "winner": winner} for number, winner in enumerate(winners, start=1)]


class ModelConstraintTests(PvpRunFixture):

    def test_a_run_needs_exactly_one_event(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpRun.objects.create(user=self.user, stage="day", day=1, ticket=1)

    def test_a_day_needs_a_day_and_a_ticket(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpRun.objects.create(user=self.user, champions_meeting=self.cm, stage="day", day=1)

    def test_only_a_final_has_a_placement(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpRun.objects.create(
                user=self.user, champions_meeting=self.cm, stage="day", day=1, ticket=1, placement=1
            )

    def test_a_final_cannot_be_skipped(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpRun.objects.create(user=self.user, champions_meeting=self.cm, stage="final", skipped=True)

    def test_one_row_per_ticket_and_one_final(self):
        PvpRun.objects.create(user=self.user, champions_meeting=self.cm, stage="day", day=1, ticket=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpRun.objects.create(
                user=self.user, champions_meeting=self.cm, stage="day", day=1, ticket=1
            )
        PvpRun.objects.create(user=self.user, champions_meeting=self.cm, stage="final")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PvpRun.objects.create(user=self.user, champions_meeting=self.cm, stage="final")

    def test_the_same_slot_in_another_event_is_fine(self):
        PvpRun.objects.create(user=self.user, champions_meeting=self.cm, stage="day", day=1, ticket=1)
        PvpRun.objects.create(user=self.user, league_of_heroes=self.loh, stage="day", day=1, ticket=1)
        self.assertEqual(PvpRun.objects.count(), 2)


class WriteTests(PvpRunFixture):

    def test_put_creates_the_ticket_and_returns_it(self):
        response = self.put(self.ticket_url(day=2, ticket=3), races=self.races(0, None, 1))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            {key: body[key] for key in ("kind", "event", "stage", "day", "ticket")},
            {"kind": "cm", "event": self.cm.id, "stage": "day", "day": 2, "ticket": 3},
        )
        self.assertEqual([runner["uma"] for runner in body["runners"]],
                         [self.maruzensky.id, self.oguri.id, self.gold_ship.id])
        self.assertEqual(body["races"], self.races(0, None, 1))

    def test_a_second_put_replaces_the_same_row(self):
        self.put(self.ticket_url(), races=self.races(0))
        self.put(self.ticket_url(), races=self.races(2, 2), note="rain")
        run = PvpRun.objects.get()
        self.assertEqual([race.winner for race in run.races.all()], [2, 2])
        self.assertEqual(run.note, "rain")

    def test_a_lost_race_is_kept_apart_from_an_unraced_one(self):
        self.put(self.ticket_url(), races=self.races(None))
        self.assertEqual(PvpRunRace.objects.count(), 1)
        self.assertIsNone(PvpRunRace.objects.get().winner)

    def test_the_final_takes_a_group_and_a_placement(self):
        response = self.put(
            self.final_url(), races=self.races(1), finals_group="A", placement=1
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["stage"], body["day"], body["ticket"]), ("final", None, None))
        self.assertEqual((body["finals_group"], body["placement"]), ("A", 1))

    def test_loh_tickets_reach_day_six_ticket_five(self):
        self.assertEqual(self.put(self.ticket_url(day=6, ticket=5, kind="loh")).status_code, 200)

    def test_the_loh_top_96_keeps_a_team_a_placement_and_a_note(self):
        url = self.final_url(kind="loh", event=self.loh.id)
        response = self.client.put(url, {"placement": 42, "note": "Best one yet"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["stage"], response.json()["placement"]), ("final", 42))
        response = self.put(url, placement=42)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["runners"]), 3)
        self.assertEqual(self.client.put(url, {"placement": 97}, format="json").status_code, 400)
        self.assertEqual(
            self.client.put(url, {"placement": 1, "finals_group": "A"}, format="json").status_code, 400
        )
        self.assertEqual(
            self.client.put(url, {"races": [{"race": 1, "winner": None}]}, format="json").status_code, 400
        )

    def test_guests_cannot_save(self):
        self.assertEqual(self.put(self.ticket_url(), client=APIClient()).status_code, 401)


class SkipTests(PvpRunFixture):
    """A skip is the player's word that the races a ticket doesn't list never ran."""

    def test_a_whole_ticket_can_be_skipped(self):
        response = self.put(self.ticket_url(), runners=[], skipped=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["skipped"], response.json()["races"]), (True, []))

    def test_a_ticket_can_stop_after_some_races(self):
        self.put(self.ticket_url(), races=self.races(0, None), skipped=True)
        run = PvpRun.objects.get()
        self.assertTrue(run.skipped)
        self.assertEqual(run.races.count(), 2)

    def test_a_put_without_the_flag_undoes_it(self):
        # Slow typing, not a skip: the player logs race 3 after all.
        self.put(self.ticket_url(), races=self.races(0, None), skipped=True)
        self.put(self.ticket_url(), races=self.races(0, None, 1))
        self.assertFalse(PvpRun.objects.get().skipped)

    def test_a_run_says_it_is_not_skipped(self):
        self.assertIs(self.put(self.ticket_url()).json()["skipped"], False)


class RefusalTests(PvpRunFixture):

    def assert_refused(self, response, status=400):
        self.assertEqual(response.status_code, status, response.content)
        self.assertIn("error", response.json())
        self.assertFalse(PvpRun.objects.exists())

    def test_slots_outside_the_event_are_404(self):
        self.assert_refused(self.put(self.ticket_url(day=5)), 404)
        self.assert_refused(self.put(self.ticket_url(ticket=5)), 404)
        self.assert_refused(self.put(self.ticket_url(day=0)), 404)

    def test_an_unknown_event_or_kind_is_404(self):
        self.assert_refused(self.put(self.ticket_url(event=9999)), 404)
        self.assert_refused(self.client.put("/pvp-runs/tt/1/day/1/ticket/1", {}, format="json"), 404)

    def test_six_races(self):
        self.assert_refused(self.put(self.ticket_url(), races=self.races(0, 0, 0, 0, 0, 0)))

    def test_two_races_in_the_final(self):
        self.assert_refused(self.put(self.final_url(), races=self.races(0, 1)))

    def test_the_same_race_twice(self):
        self.assert_refused(self.put(
            self.ticket_url(), races=[{"race": 1, "winner": 0}, {"race": 1, "winner": 1}]
        ))

    def test_a_win_by_an_empty_spot(self):
        self.assert_refused(self.put(
            self.ticket_url(), runners=self.runners(self.oguri), races=self.races(2)
        ))

    def test_the_lineup_follows_the_team_rules(self):
        self.assert_refused(self.put(
            self.ticket_url(), runners=self.runners(self.oguri, self.oguri_xmas)
        ))
        self.assert_refused(self.put(
            self.ticket_url(), runners=self.runners(self.oguri, self.maruzensky, self.gold_ship, self.oguri_xmas)
        ))

    def test_a_placement_on_a_day_or_out_of_range(self):
        self.assert_refused(self.put(self.ticket_url(), placement=1))
        self.assert_refused(self.put(self.ticket_url(), finals_group="A"))
        self.assert_refused(self.put(self.final_url(), placement=4))

    def test_a_skipped_final(self):
        self.assert_refused(self.put(self.final_url(), skipped=True))

    def test_skipping_a_ticket_with_every_race_logged(self):
        self.assert_refused(self.put(self.ticket_url(), races=self.races(0, 0, 0, 0, 0), skipped=True))

    def test_a_refused_put_changes_nothing(self):
        self.put(self.ticket_url(), races=self.races(0, 1))
        response = self.put(self.ticket_url(), races=self.races(0, 0, 0, 0, 0, 0))
        self.assertEqual(response.status_code, 400)
        self.assertEqual([race.winner for race in PvpRunRace.objects.all()], [0, 1])

    def test_a_long_note(self):
        self.assert_refused(self.put(self.ticket_url(), note="x" * 121))


class ReadAndClearTests(PvpRunFixture):

    def test_list_returns_only_this_event_and_this_player(self):
        self.put(self.ticket_url(day=1, ticket=1))
        self.put(self.ticket_url(day=1, ticket=1, kind="loh"))
        self.put(self.final_url())
        other, _ = auth_client(make_user("rival"))
        self.put(self.ticket_url(day=1, ticket=2), client=other)

        runs = self.client.get(f"/pvp-runs/cm/{self.cm.id}").json()
        self.assertEqual([(run["stage"], run["ticket"]) for run in runs], [("day", 1), ("final", None)])

    def test_delete_clears_one_slot_and_is_idempotent(self):
        self.put(self.ticket_url(ticket=1))
        self.put(self.ticket_url(ticket=2))
        self.assertEqual(self.client.delete(self.ticket_url(ticket=1)).status_code, 204)
        self.assertEqual(self.client.delete(self.ticket_url(ticket=1)).status_code, 204)
        self.assertEqual(list(PvpRun.objects.values_list("ticket", flat=True)), [2])
        self.assertFalse(PvpRunRunner.objects.filter(run__ticket=1).exists())

    def test_someone_else_cannot_clear_your_ticket(self):
        self.put(self.ticket_url())
        other, _ = auth_client(make_user("rival"))
        other.delete(self.ticket_url())
        self.assertEqual(PvpRun.objects.count(), 1)


class HistoryTests(PvpRunFixture):
    """GET /pvp-runs: every run the caller logged, across events."""

    def test_every_event_and_the_final(self):
        self.put(self.ticket_url(day=1, ticket=1), races=self.races(0))
        self.put(self.final_url(), races=self.races(1), finals_group="A", placement=2)
        self.put(self.ticket_url(day=6, ticket=5, kind="loh"), races=self.races(None))
        response = self.client.get("/pvp-runs")
        self.assertEqual(response.status_code, 200, response.content)
        slots = {(run["kind"], run["event"], run["stage"]) for run in response.json()}
        self.assertEqual(slots, {
            ("cm", self.cm.id, "day"), ("cm", self.cm.id, "final"), ("loh", self.loh.id, "day"),
        })
        final = next(run for run in response.json() if run["stage"] == "final")
        self.assertEqual((final["finals_group"], final["placement"]), ("A", 2))

    def test_only_the_callers_runs(self):
        other, _ = auth_client(make_user("rival"))
        self.put(self.ticket_url(), client=other, races=self.races(0))
        self.assertEqual(self.client.get("/pvp-runs").json(), [])

    def test_runs_stay_grouped_by_event(self):
        second = make_champions_meeting()
        self.put(self.ticket_url(day=1, ticket=1), races=self.races(0))
        self.put(self.ticket_url(day=1, ticket=1, event=second.id), races=self.races(0))
        self.put(self.ticket_url(day=2, ticket=1), races=self.races(0))
        events = [run["event"] for run in self.client.get("/pvp-runs").json()]
        self.assertEqual(events, sorted(events))

    def test_guests_are_refused(self):
        self.assertEqual(APIClient().get("/pvp-runs").status_code, 401)


# Picture URLs go through default_storage; see test_pvp_teams.LifecycleTests.
@override_settings(STORAGES=PLAIN_TEST_STORAGES)
class LifecycleTests(PvpRunFixture):

    def test_a_run_save_keeps_the_public_payload(self):
        APIClient().get("/calculator-data")
        self.put(self.ticket_url(), races=self.races(0))
        self.client.delete(self.ticket_url())
        self.assertIsNotNone(public_payload_cache.read(), "a run save dropped /calculator-data")

    def test_editing_the_team_never_touches_a_run(self):
        team = self.client.post(
            "/pvp-teams",
            {"kind": "cm", "event": self.cm.id, "members": [
                {"position": 0, "uma": self.oguri.id, "style": "pace", "cards": []},
            ]},
            format="json",
        ).json()
        self.put(self.ticket_url(), runners=self.runners(self.oguri))
        self.client.delete(f"/pvp-teams/{team['id']}")
        self.assertEqual(PvpRunRunner.objects.get().uma, self.oguri)

    def test_deleting_the_account_deletes_its_runs(self):
        self.put(self.ticket_url(), races=self.races(0))
        self.assertEqual(self.client.delete("/account").status_code, 204)
        self.assertFalse(PvpRun.objects.exists())
        self.assertFalse(PvpRunRace.objects.exists())
