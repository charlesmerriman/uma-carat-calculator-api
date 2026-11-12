"""CM Runs tickets follow their My team team until they are raced
(models/pvp_run.py, notes/runs-follow-team-plan.md, owner 2026-11-12)."""

import importlib

from django.apps import apps as django_apps

from calculatorapi.models import PvpRun, PvpTeam, TrainedUma
from calculatorapi.tests.factories import auth_client, make_user
from calculatorapi.tests.test_pvp_runs import PvpRunFixture
from calculatorapi.tests.test_trained_umas import STATS


class FollowTeamTests(PvpRunFixture):

    def setUp(self):
        super().setUp()
        self.team = self.make_team("Team 1", self.oguri, self.maruzensky, self.gold_ship)

    def make_team(self, name, *umas):
        response = self.client.post("/pvp-teams", {
            "kind": "cm", "event": self.cm.id, "name": name,
            "members": [{"position": i, "uma": uma.id, "style": "front", "cards": []} for i, uma in enumerate(umas)],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return PvpTeam.objects.get(id=response.json()["id"])

    def fill(self, ticket, team=None, umas=None, **body):
        """A ticket filled from `team` (its lineup), as the client sends it."""
        team = team or self.team
        umas = umas or [member.uma for member in team.members.order_by("position")]
        response = self.put(self.ticket_url(ticket=ticket), runners=self.runners(*umas), team=team.id, **body)
        self.assertEqual(response.status_code, 200, response.data)
        return response.json()

    def edit_team(self, *umas, team=None):
        team = team or self.team
        return self.client.put(f"/pvp-teams/{team.id}", {
            "members": [{"position": i, "uma": uma.id, "style": "front", "cards": []} for i, uma in enumerate(umas)],
        }, format="json")

    def lineup(self, ticket):
        run = PvpRun.objects.get(user=self.user, day=1, ticket=ticket)
        return [runner.uma_id for runner in run.runners.order_by("position")]

    def test_the_plans_worked_example(self):
        """Team 1 swaps one uma (Oguri Cap out, Oguri Cap (Christmas) in): the
        two unraced tickets follow, the two raced ones keep what they ran with."""
        self.fill(1, races=self.races(0, 1, None, 2, 0))          # 5 races logged
        self.fill(2, races=self.races(0, None))                    # 2 races logged
        self.fill(3)                                               # nothing logged
        self.fill(4)
        response = self.edit_team(self.oguri_xmas, self.maruzensky, self.gold_ship)
        self.assertEqual(response.json()["tickets_followed"], 2)
        ran = [self.oguri.id, self.maruzensky.id, self.gold_ship.id]
        now = [self.oguri_xmas.id, self.maruzensky.id, self.gold_ship.id]
        self.assertEqual([self.lineup(n) for n in (1, 2, 3, 4)], [ran, ran, now, now])

    def test_another_teams_tickets_are_left_alone(self):
        other = self.make_team("Plan B", self.gold_ship, self.maruzensky)
        self.fill(1, team=other)
        self.edit_team(self.oguri_xmas, self.maruzensky, self.gold_ship)
        self.assertEqual(self.lineup(1), [self.gold_ship.id, self.maruzensky.id])

    def test_a_race_save_without_the_key_keeps_the_link(self):
        """An older client sends no `team`: logging a race must not drop it."""
        self.fill(1)
        response = self.put(self.ticket_url(), runners=self.runners(self.oguri, self.maruzensky, self.gold_ship))
        self.assertEqual(response.json()["team"], self.team.id)

    def test_a_ticket_that_no_longer_holds_the_team_lets_go(self):
        self.fill(1)
        response = self.put(self.ticket_url(), runners=self.runners(self.gold_ship, self.maruzensky))
        self.assertIsNone(response.json()["team"])
        self.edit_team(self.oguri_xmas, self.maruzensky, self.gold_ship)
        self.assertEqual(self.lineup(1), [self.gold_ship.id, self.maruzensky.id])

    def test_a_veteran_picked_in_runs_goes_to_the_team_and_its_other_tickets(self):
        veteran = TrainedUma.objects.create(user=self.user, uma=self.oguri, source="manual", **STATS)
        self.fill(1)
        self.fill(2)
        runners = self.runners(self.oguri, self.maruzensky, self.gold_ship)
        runners[0]["trained_uma"] = veteran.id
        response = self.put(self.ticket_url(ticket=1), runners=runners)
        self.assertEqual(response.json()["team"], self.team.id)
        self.assertEqual(self.team.members.get(position=0).trained_uma_id, veteran.id)
        second = PvpRun.objects.get(user=self.user, day=1, ticket=2)
        self.assertEqual(second.runners.get(position=0).trained_uma_id, veteran.id)

    def test_a_veteran_fixed_on_a_raced_ticket_stays_on_that_ticket(self):
        veteran = TrainedUma.objects.create(user=self.user, uma=self.oguri, source="manual", **STATS)
        self.fill(1, races=self.races(0))
        runners = self.runners(self.oguri, self.maruzensky, self.gold_ship)
        runners[0]["trained_uma"] = veteran.id
        response = self.put(self.ticket_url(ticket=1), runners=runners, races=self.races(0))
        self.assertIsNone(response.json()["team"])
        self.assertIsNone(self.team.members.get(position=0).trained_uma_id)

    def test_someone_elses_team_is_refused(self):
        stranger, _ = auth_client(make_user("stranger"))
        response = self.put(self.ticket_url(), client=stranger, team=self.team.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "That team isn't one of yours for this event.")

    def test_deleting_the_team_keeps_the_tickets(self):
        self.fill(1)
        self.client.delete(f"/pvp-teams/{self.team.id}")
        run = PvpRun.objects.get(user=self.user, day=1, ticket=1)
        self.assertIsNone(run.team_id)
        self.assertEqual(self.lineup(1), [self.oguri.id, self.maruzensky.id, self.gold_ship.id])


class BackfillTests(PvpRunFixture):
    """Migration 0132: link only on one exact match, unraced, event not over."""

    def setUp(self):
        super().setUp()
        self.backfill = importlib.import_module(
            "calculatorapi.migrations.0132_link_unraced_runs_to_teams"
        ).link_unraced_tickets

    def team(self, *umas, name="Team"):
        team = PvpTeam.objects.create(user=self.user, champions_meeting=self.cm, name=name)
        for i, uma in enumerate(umas):
            team.members.create(position=i, uma=uma, style="front")
        return team

    def ticket(self, number, *umas, races=()):
        self.put(self.ticket_url(ticket=number), runners=self.runners(*umas), races=list(races))
        return PvpRun.objects.get(user=self.user, day=1, ticket=number)

    def run_backfill(self):
        self.backfill(django_apps, None)

    def test_links_exact_unique_matches_only(self):
        team = self.team(self.oguri, self.maruzensky)
        exact = self.ticket(1, self.oguri, self.maruzensky)
        raced = self.ticket(2, self.oguri, self.maruzensky, races=self.races(0))
        other = self.ticket(3, self.gold_ship, self.maruzensky)
        self.run_backfill()
        for run in (exact, raced, other):
            run.refresh_from_db()
        self.assertEqual((exact.team_id, raced.team_id, other.team_id), (team.id, None, None))

    def test_two_matching_teams_link_neither(self):
        self.team(self.oguri, self.maruzensky, name="A")
        self.team(self.oguri, self.maruzensky, name="B")
        run = self.ticket(1, self.oguri, self.maruzensky)
        self.run_backfill()
        run.refresh_from_db()
        self.assertIsNone(run.team_id)
