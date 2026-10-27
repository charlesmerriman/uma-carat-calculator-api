"""The Rankings page's routes: who may read them, what they send, and the
hourly cache (views/rankings.py, rankings_cache.py)."""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi.models import (
    GalleryPost, GalleryPostKind, PvpDeck, PvpDeckSlot, PvpGuide, PvpRun, PvpRunRace, PvpRunRunner, PvpTeam,
    PvpTeamCard, PvpTeamMember, PvpUmaRecommendation, SupportCard, Uma, UserOshi, UserPlannedBanner,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client, make_champions_meeting, make_league_of_heroes, make_scenario, make_step_up_banner, make_timeline,
    make_uma_banner, make_user,
)
from calculatorapi.views.rankings import deck_tier, guides_payload

URL = "/rankings/guides"


class RankingsFixture(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    def setUp(self):
        self.cm = make_champions_meeting(name="Aquarius Cup", cm_number=22)
        self.loh = make_league_of_heroes(name="LoH 2")
        now = timezone.now()
        # Created newest first, so release order is not id order.
        self.newer = make_scenario(name="Trackblazer", banner_timeline=make_timeline(jp_start_date=now))
        self.older = make_scenario(
            name="Grand Live", banner_timeline=make_timeline(jp_start_date=now - timedelta(days=400)),
        )
        make_scenario(name="Unused")
        self.kitasan = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
        self.fine = SupportCard.objects.create(name="Fine Motion", card_type="wit", rarity=3)
        self.any_speed = SupportCard.objects.create(
            name="SSR Speed", card_type="speed", rarity=3, is_placeholder=True,
        )
        self.maru = Uma.objects.create(name="Maruzensky")

        self.guide = PvpGuide.objects.create(champions_meeting=self.cm, scenario=self.newer, is_published=True)
        deck = PvpDeck.objects.create(guide=self.guide, style="front", label=" whale ")
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=self.kitasan)
        PvpDeckSlot.objects.create(deck=deck, slot=1, order=1, support_card=self.fine)
        PvpDeckSlot.objects.create(deck=deck, slot=2, note="Any Wit")
        PvpDeckSlot.objects.create(deck=deck, slot=3, support_card=self.any_speed)
        PvpDeckSlot.objects.create(deck=deck, slot=6, is_borrow=True, support_card=self.fine)
        self.deck = deck
        mention = PvpDeck.objects.create(guide=self.guide, style="pace", label="Honorable mention deck", order=1)
        PvpDeckSlot.objects.create(deck=mention, slot=1, support_card=self.kitasan)
        self.mention = mention
        one_off = PvpDeck.objects.create(guide=self.guide, style="pace", label="Xoguri pace", order=2)
        PvpDeckSlot.objects.create(deck=one_off, slot=1, support_card=self.fine)
        self.one_off = one_off
        PvpUmaRecommendation.objects.create(guide=self.guide, style="front", uma=self.maru, stars=3)

        older = PvpGuide.objects.create(league_of_heroes=self.loh, scenario=self.older, is_published=True)
        PvpUmaRecommendation.objects.create(guide=older, style="end", uma=self.maru, stars=1)

        draft = PvpGuide.objects.create(
            champions_meeting=make_champions_meeting(name="Draft Cup", cm_number=23), is_published=False,
        )
        hidden = PvpDeck.objects.create(guide=draft, style="front", label="Standard")
        PvpDeckSlot.objects.create(deck=hidden, slot=1, support_card=self.kitasan)


class AccessTests(RankingsFixture):
    def test_a_guest_is_asked_to_sign_in(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_a_plain_account_is_refused(self):
        client, _ = auth_client(make_user("plain"))
        response = client.get(URL)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("guides", response.json())

    def test_staff_and_a_granted_account_may_read(self):
        granted = make_user("tester")
        granted.early_access = True
        granted.save(update_fields=["early_access"])
        for user in (make_user("staff", is_staff=True), granted):
            client, _ = auth_client(user)
            self.assertEqual(client.get(URL).status_code, 200, user.username)


class DeckTierTests(CalculatorTestCase):
    def test_labels(self):
        cases = {
            "Whale": "whale",
            " beginner ": "beginner",
            "Honorable mention": "honorable",
            "Honorable Mentions": "honorable",
            "honorable mention deck": "honorable",
            "Xoguri pace": "other",
            "": "other",
        }
        for label, tier in cases.items():
            self.assertEqual(deck_tier(label), tier, label)


class PayloadTests(RankingsFixture):
    def setUp(self):
        super().setUp()
        self.client, _ = auth_client(make_user("staff", is_staff=True))

    def guide_for(self, body, kind):
        return next(guide for guide in body["guides"] if guide["kind"] == kind)

    def test_published_guides_only_with_their_rows(self):
        body = self.client.get(URL).json()
        self.assertEqual(len(body["guides"]), 2)
        guide = self.guide_for(body, "cm")
        self.assertEqual((guide["event"], guide["scenario"]), (self.cm.id, self.newer.id))
        whale, mention, one_off = self.deck.id, self.mention.id, self.one_off.id
        self.assertEqual(guide["cards"], [
            {"card": self.kitasan.id, "deck": whale, "style": "front", "tier": "whale", "role": "main"},
            {"card": self.fine.id, "deck": whale, "style": "front", "tier": "whale", "role": "alt"},
            {"card": self.fine.id, "deck": whale, "style": "front", "tier": "whale", "role": "borrow"},
            {"card": self.kitasan.id, "deck": mention, "style": "pace", "tier": "honorable", "role": "main"},
            {"card": self.fine.id, "deck": one_off, "style": "pace", "tier": "other", "role": "main"},
        ])
        self.assertEqual(guide["umas"], [{"uma": self.maru.id, "style": "front", "stars": 3}])

    def test_scenarios_in_use_in_release_order(self):
        body = self.client.get(URL).json()
        self.assertEqual(body["scenarios"], [
            {"id": self.older.id, "name": "Grand Live"},
            {"id": self.newer.id, "name": "Trackblazer"},
        ])

    def test_a_guide_without_a_scenario_sends_null(self):
        PvpGuide.objects.filter(pk=self.guide.pk).update(scenario=None)
        guide = self.guide_for(self.client.get(URL).json(), "cm")
        self.assertIsNone(guide["scenario"])

    def test_gzipped_when_asked(self):
        response = self.client.get(URL, HTTP_ACCEPT_ENCODING="gzip, deflate")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Encoding"], "gzip")

    def test_no_placeholder_and_no_note_only_slot(self):
        cards = self.guide_for(self.client.get(URL).json(), "cm")["cards"]
        self.assertNotIn(self.any_speed.id, [row["card"] for row in cards])
        self.assertTrue(all(row["card"] for row in cards))

    def test_four_queries_whatever_the_guide_count(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                guides_payload()
            return len(queries)

        before = count()
        cm = make_champions_meeting(name="Piscis Cup", cm_number=24)
        guide = PvpGuide.objects.create(champions_meeting=cm, scenario=self.older, is_published=True)
        deck = PvpDeck.objects.create(guide=guide, style="end", label="Beginner")
        PvpDeckSlot.objects.create(deck=deck, slot=1, support_card=self.kitasan)
        PvpUmaRecommendation.objects.create(guide=guide, style="end", uma=self.maru, stars=1)
        self.assertEqual(count(), before)
        self.assertEqual(before, 4)


class HourlyCacheTests(RankingsFixture):
    """Built once, stamped, and NOT invalidated by a guide edit: hourly
    staleness is the owner's decision (rankings_cache.py)."""

    def test_stamped_and_served_from_the_cache_until_it_expires(self):
        client, _ = auth_client(make_user("staff", is_staff=True))
        first = client.get(URL).json()
        self.assertIn("generated_at", first)

        PvpUmaRecommendation.objects.filter(guide=self.guide).delete()
        with CaptureQueriesContext(connection) as queries:
            second = client.get(URL).json()
        self.assertEqual(second, first)
        # Only the account's own reads: the payload's four never run.
        self.assertFalse(any("pvpdeckslot" in query["sql"].lower() for query in queries))


class GalleryRankingsTests(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """GET /rankings/gallery/<kind>/<id>: one event's posted teams, counted."""

    def setUp(self):
        self.cm = make_champions_meeting(name="Libra Cup", cm_number=21)
        self.oguri = Uma.objects.create(name="Oguri Cap")
        self.maru = Uma.objects.create(name="Maruzensky")
        self.light = SupportCard.objects.create(name="Light Hello", card_type="friend", rarity=3)
        self.kitasan = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
        self.any_speed = SupportCard.objects.create(
            name="SSR Speed", card_type="speed", rarity=3, is_placeholder=True,
        )
        self.post(make_user("a"), [
            self.member(self.oguri, "pace", [(1, self.light), (2, self.any_speed), (6, self.kitasan)]),
            self.member(self.maru, "front", [(1, self.light)]),
        ])
        self.post(make_user("b"), [self.member(self.oguri, "end", [(1, self.kitasan)])])
        blocked = make_user("blocked")
        blocked.publishing_blocked = True
        blocked.save(update_fields=["publishing_blocked"])
        self.post(blocked, [self.member(self.maru, "front", [(1, self.kitasan)])])
        self.url = f"/rankings/gallery/cm/{self.cm.id}"
        self.client, _ = auth_client(make_user("staff", is_staff=True))

    @staticmethod
    def member(uma, style, cards):
        return {"position": 0, "uma": uma.id, "style": style,
                "cards": [{"slot": slot, "support_card": card.id} for slot, card in cards]}

    def post(self, author, snapshot):
        GalleryPost.objects.create(
            author=author, kind=GalleryPostKind.PVP_TEAM, champions_meeting=self.cm, team_snapshot=snapshot,
        )

    def test_counts_visible_posts_umas_and_cards(self):
        body = self.client.get(self.url).json()
        self.assertEqual((body["posts"], body["decks"]), (2, 3))
        self.assertEqual(body["umas"], [
            {"uma": self.oguri.id, "teams": 2, "styles": {"pace": 1, "end": 1}},
            {"uma": self.maru.id, "teams": 1, "styles": {"front": 1}},
        ])
        self.assertEqual(body["cards"], [
            {"card": self.light.id, "decks": 2, "borrows": 0},
            {"card": self.kitasan.id, "decks": 2, "borrows": 1},
        ])
        self.assertIn("generated_at", body)

    def test_early_access_only_and_a_real_event(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        plain, _ = auth_client(make_user("plain"))
        self.assertEqual(plain.get(self.url).status_code, 403)
        self.assertEqual(self.client.get("/rankings/gallery/cm/9999").status_code, 404)
        self.assertEqual(self.client.get(f"/rankings/gallery/xx/{self.cm.id}").status_code, 404)


class PullPlansTests(CalculatorTestCase):
    """GET /rankings/pull-plans: the admin's banner tables with a median, behind
    the Raptive gate (guide editors only, views/rankings.sees_pull_plans)."""

    URL = "/rankings/pull-plans"

    def setUp(self):
        self.uma = make_uma_banner(name="Gentildonna")
        self.step_up = make_step_up_banner(name="Anniversary Step-Up")
        for name, pulls in (("a", 100), ("b", 150), ("c", 1000)):
            UserPlannedBanner.objects.create(user=make_user(name), banner_uma=self.uma, number_of_pulls=pulls)
        UserPlannedBanner.objects.create(user=make_user("d"), banner_step_up=self.step_up, number_of_pulls=4)
        UserPlannedBanner.objects.create(
            user=make_user("staffer", is_staff=True), banner_uma=self.uma, number_of_pulls=5,
        )
        self.editor, _ = auth_client(make_user("editor", is_staff=True))

    def test_banners_with_a_median_and_steps_kept_apart(self):
        body = self.editor.get(self.URL).json()
        uma = next(row for row in body["banners"] if row["type"] == "uma")
        self.assertEqual(
            (uma["id"], uma["name"], uma["unit"], uma["planners"], uma["total"], uma["median"]),
            (self.uma.id, "Gentildonna", "pulls", 3, 1250, 150),
        )
        self.assertAlmostEqual(uma["average"], 416.7)
        step_up = next(row for row in body["banners"] if row["type"] == "step_up")
        self.assertEqual((step_up["unit"], step_up["total"], step_up["median"]), ("steps", 4, 4))
        self.assertIn("demand", body)
        self.assertIn("generated_at", body)

    def test_behind_the_raptive_gate(self):
        tester = make_user("tester")
        tester.early_access = True
        tester.save(update_fields=["early_access"])
        for user in (make_user("plain"), tester):
            client, _ = auth_client(user)
            self.assertEqual(client.get(self.URL).status_code, 403, user.username)
        self.assertEqual(APIClient().get(self.URL).status_code, 401)


class PvpRankingsTests(CalculatorTestCase):  # pylint: disable=too-many-instance-attributes
    """GET /rankings/pvp/<kind>/<id>: every saved team (not only posted ones),
    and CM Runs win rates, staff out."""

    def setUp(self):
        self.cm = make_champions_meeting(name="Libra Cup", cm_number=21)
        self.oguri = Uma.objects.create(name="Oguri Cap")
        self.maru = Uma.objects.create(name="Maruzensky")
        self.light = SupportCard.objects.create(name="Light Hello", card_type="friend", rarity=3)
        self.player = make_user("player")
        self.team(self.player, [(self.oguri, "pace", [(1, self.light), (6, self.light)]), (self.maru, "front", [])])
        self.team(make_user("other"), [(self.oguri, "end", [(1, self.light)])])
        self.team(make_user("empty"), [])
        self.team(make_user("staffer", is_staff=True), [(self.maru, "front", [(1, self.light)])])
        self.url = f"/rankings/pvp/cm/{self.cm.id}"
        self.client, _ = auth_client(make_user("staff", is_staff=True))

    def team(self, user, members):
        team = PvpTeam.objects.create(user=user, champions_meeting=self.cm)
        for position, (uma, style, cards) in enumerate(members):
            member = PvpTeamMember.objects.create(team=team, position=position, uma=uma, style=style)
            for slot, card in cards:
                PvpTeamCard.objects.create(member=member, slot=slot, support_card=card)

    def log_run(self, user, winners, skipped=False, ticket=1):
        run = PvpRun.objects.create(
            user=user, champions_meeting=self.cm, stage="day", day=1, ticket=ticket, skipped=skipped,
        )
        PvpRunRunner.objects.create(run=run, position=0, uma=self.oguri, style="pace")
        PvpRunRunner.objects.create(run=run, position=1, uma=self.maru, style="front")
        for race, winner in enumerate(winners, start=1):
            PvpRunRace.objects.create(run=run, race=race, winner=winner)

    def test_counts_every_saved_team(self):
        built = self.client.get(self.url).json()["built"]
        self.assertEqual((built["teams"], built["decks"]), (2, 3))
        self.assertEqual(built["umas"][0], {"uma": self.oguri.id, "teams": 2, "styles": {"pace": 1, "end": 1}})
        self.assertEqual(built["cards"], [{"card": self.light.id, "decks": 3, "borrows": 1}])

    def test_win_rates_per_uma_and_strategy(self):
        self.log_run(self.player, [0, None, 1])
        self.log_run(self.player, [0, 0, 0], skipped=True, ticket=2)
        self.log_run(make_user("staffer2", is_staff=True), [0, 0, 0])
        runs = self.client.get(self.url).json()["runs"]
        self.assertEqual((runs["players"], runs["races"], runs["min_races"]), (1, 3, 20))
        self.assertEqual(runs["rows"], [
            {"uma": self.oguri.id, "style": "pace", "races": 3, "wins": 1},
            {"uma": self.maru.id, "style": "front", "races": 3, "wins": 1},
        ])

    def test_early_access_only_and_a_real_event(self):
        plain, _ = auth_client(make_user("plain"))
        self.assertEqual(plain.get(self.url).status_code, 403)
        self.assertEqual(self.client.get("/rankings/pvp/cm/9999").status_code, 404)


class PlayersRankingsTests(CalculatorTestCase):
    """GET /rankings/players: the four panels, staff out, ranks without "Not set"."""

    URL = "/rankings/players"

    def test_the_four_panels(self):
        uma = Uma.objects.create(name="Special Week")
        fan = make_user("fan")
        UserOshi.objects.create(user=fan, uma=uma, position=0)
        UserOshi.objects.create(user=make_user("staffer", is_staff=True), uma=uma, position=0)
        client, _ = auth_client(make_user("staff", is_staff=True))
        body = client.get(self.URL).json()
        self.assertEqual(body["favourites"], [{"uma": uma.id, "people": 1}])
        self.assertEqual([rank["label"] for rank in body["ranks"]], ["Team Trials", "Club Rank"])
        for rank in body["ranks"]:
            self.assertNotIn("Not set", [row["name"] for row in rank["rows"]])
            self.assertEqual(rank["people"], sum(row["people"] for row in rank["rows"]))
        self.assertIn("daily_carat", [row["key"] for row in body["income"]])
        self.assertIn("selectors", body)

    def test_early_access_only(self):
        plain, _ = auth_client(make_user("plain"))
        self.assertEqual(plain.get(self.URL).status_code, 403)
