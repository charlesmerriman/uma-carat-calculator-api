"""The Rankings page's routes: who may read them, what they send, and the
hourly cache (views/rankings.py, rankings_cache.py)."""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi.models import (
    GalleryPost, GalleryPostKind, PvpDeck, PvpDeckSlot, PvpGuide, PvpRun, PvpRunRace, PvpRunRunner, PvpTeam,
    PvpTeamCard, PvpTeamMember, PvpUmaRecommendation, SupportCard, TeamTrialsRank, Uma, UserOshi,
    UserPlannedBanner, UserPlannedPurchase,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client, make_anniversary_event, make_champions_meeting, make_league_of_heroes, make_scenario,
    make_step_up_banner, make_support_banner, make_timeline, make_uma_banner, make_user,
)
from calculatorapi.rankings_public import limited
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
    def test_a_guest_gets_the_top_three_counted_on_the_server(self):
        body = APIClient().get(URL).json()
        self.assertTrue(body["limited"])
        # No rows to count: the server's own count, every box ticked, three at most.
        self.assertNotIn("guides", body)
        self.assertNotIn("previous", body)
        self.assertLessEqual(len(body["cards"]), 3)
        self.assertLessEqual(len(body["umas"]), 3)
        self.assertEqual(set(body["totals"]), {"guides", "decks", "scenarios"})

    def test_a_plain_account_gets_the_same_cut(self):
        client, _ = auth_client(make_user("plain"))
        body = client.get(URL).json()
        self.assertTrue(body["limited"])
        self.assertNotIn("guides", body)

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

    def test_most_copied_posts_in_copy_order_never_a_hidden_one(self):
        posts = list(GalleryPost.objects.order_by("id"))
        GalleryPost.objects.filter(id=posts[0].id).update(copy_count=3, caption="Pace core")
        GalleryPost.objects.filter(id=posts[1].id).update(copy_count=7)
        # The blocked author's post is the most copied, and still never listed.
        GalleryPost.objects.filter(id=posts[2].id).update(copy_count=99)
        copied = self.client.get(self.url).json()["copied"]
        self.assertEqual([post["public_id"] for post in copied], [posts[1].public_id, posts[0].public_id])
        self.assertEqual(copied[1], {
            "public_id": posts[0].public_id, "caption": "Pace core", "copies": 3,
            "umas": [self.oguri.id, self.maru.id],
        })

    def test_anyone_else_gets_the_top_three_and_a_real_event(self):
        body = APIClient().get(self.url).json()
        self.assertTrue(body["limited"])
        self.assertEqual((body["posts"], body["decks"]), (2, 3))
        self.assertLessEqual(len(body["umas"]), 3)
        self.assertNotIn("copied", body)
        self.assertEqual(self.client.get("/rankings/gallery/cm/9999").status_code, 404)
        self.assertEqual(self.client.get(f"/rankings/gallery/xx/{self.cm.id}").status_code, 404)


class PullPlansTests(CalculatorTestCase):
    """GET /rankings/pull-plans: the admin's banner tables with a median, behind
    the Raptive gate (guide editors only, views/rankings.sees_pull_plans)."""

    URL = "/rankings/pull-plans"

    def setUp(self):
        self.uma = make_uma_banner(name="Gentildonna")
        self.step_up = make_step_up_banner(name="Anniversary Step-Up")
        self.users = {}
        for name, pulls in (("a", 100), ("b", 150), ("c", 1000)):
            self.users[name] = make_user(name)
            UserPlannedBanner.objects.create(user=self.users[name], banner_uma=self.uma, number_of_pulls=pulls)
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
        # Everyone with a banner planned, each once, staff out: a, b, c and d.
        self.assertEqual(body["planners"], 4)
        self.assertIn("demand", body)
        self.assertIn("generated_at", body)

    def test_spread_sums_to_the_planners_and_steps_keep_their_own_buckets(self):
        body = self.editor.get(self.URL).json()
        uma = next(row for row in body["banners"] if row["type"] == "uma")
        # a 100, b 150, c 1000: the 100 to 199 bucket holds two, 300 and up one.
        self.assertEqual(uma["spread"], {"edges": [0, 50, 100, 200, 300], "planners": [0, 0, 2, 0, 1]})
        self.assertEqual(sum(uma["spread"]["planners"]), uma["planners"])
        step_up = next(row for row in body["banners"] if row["type"] == "step_up")
        self.assertEqual(step_up["spread"], {"edges": [0, 2, 3, 4, 5], "planners": [0, 0, 0, 1, 0]})

    def test_also_planning_lists_the_other_banners_never_the_banner_itself(self):
        support = make_support_banner(name="Kitasan Black")
        for name in ("a", "b"):
            UserPlannedBanner.objects.create(user=self.users[name], banner_support=support, number_of_pulls=50)
        body = self.editor.get(self.URL).json()
        uma = next(row for row in body["banners"] if row["type"] == "uma")
        self.assertEqual(
            uma["also"], [{"type": "support", "id": support.id, "name": "Kitasan Black", "planners": 2}],
        )
        self.assertNotIn(("uma", self.uma.id), [(row["type"], row["id"]) for row in uma["also"]])

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

    def test_trios_count_exact_three_uma_teams_once_each(self):
        kitasan = Uma.objects.create(name="Kitasan Black")
        for name in ("t1", "t2"):
            self.team(make_user(name), [(self.oguri, "pace", []), (self.maru, "front", []), (kitasan, "end", [])])
        built = self.client.get(self.url).json()["built"]
        # Two-uma teams make no trio.
        self.assertEqual(built["trios"], [{"umas": sorted([self.oguri.id, self.maru.id, kitasan.id]), "teams": 2}])

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

    def test_anyone_else_gets_the_top_three_ranked_and_a_real_event(self):
        self.log_run(self.player, [0, None, 1])
        self.assertEqual(len(self.client.get(self.url).json()["runs"]["rows"]), 2)
        plain, _ = auth_client(make_user("plain"))
        body = plain.get(self.url).json()
        self.assertTrue(body["limited"])
        self.assertNotIn("previous", body)
        self.assertEqual(body["built"]["teams"], 2)
        self.assertLessEqual(len(body["built"]["umas"]), 3)
        # Three races: under the ranked floor, so no place to show, so not sent.
        self.assertEqual(body["runs"]["rows"], [])
        self.assertEqual(body["runs"]["races"], 3)
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

    def test_budget_by_class_is_medians_and_never_one_person(self):
        class6 = TeamTrialsRank.objects.create(name="Class 6", income_amount=900)
        class5 = TeamTrialsRank.objects.create(name="Class 5", income_amount=600)
        banner = make_uma_banner(name="Gentildonna")
        for name, rank, carats, pulls in (
            ("a", class6, 10_000, 100), ("b", class6, 20_000, 200),
            ("c", class6, 30_000, 0), ("d", class6, 40_000, 300),
            ("lone", class5, 99_999, 999),
        ):
            user = make_user(name)
            user.team_trials_rank = rank
            user.current_carat = carats
            user.save(update_fields=["team_trials_rank", "current_carat"])
            if pulls:
                UserPlannedBanner.objects.create(user=user, banner_uma=banner, number_of_pulls=pulls)
        client, _ = auth_client(make_user("staff", is_staff=True))
        budget = client.get(self.URL).json()["budget"]
        # An even count: the median of 10k, 20k, 30k and 40k is 25k. Pulls among the
        # three who plan any: 200. Class 5's one person is never shown.
        self.assertEqual(budget, [{"name": "Class 6", "people": 4, "carats": 25_000, "pulls": 200}])

    def test_income_options_say_where_a_new_account_starts(self):
        client, _ = auth_client(make_user("staff", is_staff=True))
        income = {row["key"]: row for row in client.get(self.URL).json()["income"]}
        self.assertTrue(income["misc_earnings"]["default_on"])
        self.assertFalse(income["daily_carat"]["default_on"])

    def test_a_selector_says_whether_it_is_on_sale(self):
        now = timezone.now()
        windows = {
            "on sale": (now - timedelta(days=2), now + timedelta(days=5)),
            "past": (now - timedelta(days=40), now - timedelta(days=10)),
            "upcoming": (now + timedelta(days=10), now + timedelta(days=20)),
        }
        for name, (start, end) in windows.items():
            timeline = make_timeline(name=name, global_start_date=start, global_end_date=end)
            event = make_anniversary_event(name=name, event_type="campaign", parts=[timeline], products=[{
                "product_type": "uma_selector", "name": f"{name} selector", "usd_cost": 10, "paid_carat_amount": 0,
            }])
            UserPlannedPurchase.objects.create(
                user=make_user(f"buyer {name}"), product=event.products.get(), quantity=1,
            )
        client, _ = auth_client(make_user("staff", is_staff=True))
        selectors = {row["campaign"]: row for row in client.get(self.URL).json()["selectors"]}
        self.assertEqual(
            {name: row["status"] for name, row in selectors.items()},
            {"on sale": "on_sale", "past": "past", "upcoming": "upcoming"},
        )
        self.assertIsNotNone(selectors["on sale"]["end_date"])

    def test_anyone_else_gets_the_band_and_three_favourites(self):
        umas = [Uma.objects.create(name=f"Uma {index}") for index in range(5)]
        for index, uma in enumerate(umas):
            UserOshi.objects.create(user=make_user(f"fan {index}"), uma=uma, position=0)
        body = APIClient().get(self.URL).json()
        self.assertTrue(body["limited"])
        self.assertEqual(len(body["favourites"]), 3)
        self.assertEqual([row["key"] for row in body["income"]], ["daily_carat"])
        self.assertEqual((body["budget"], body["selectors"]), ([], []))
        self.assertLessEqual(len(body["ranks"]), 1)


class PublicCutTests(CalculatorTestCase):
    """rankings_public.limited: the top three by the client's own rules."""

    def setUp(self):
        for card in (1, 2, 3, 4, 9):
            SupportCard.objects.create(id=card, name=f"Card {card}", card_type="speed", rarity=3)

    def test_guides_count_borrows_in_alternatives_out_three_at_most(self):
        def row(card, deck, role="main"):
            return {"card": card, "deck": deck, "style": "pace", "tier": "standard", "role": role}
        payload = {"generated_at": "x", "scenarios": [], "guides": [{
            "kind": "cm", "event": 1, "scenario": 5,
            "cards": [row(1, 1), row(1, 2, "borrow"), row(2, 1), row(3, 2), row(4, 3), row(9, 3, "alt")],
            "umas": [{"uma": 7, "style": "pace", "stars": 3}, {"uma": 8, "style": "end", "stars": 1}],
        }]}
        cut = limited("guides", payload)
        self.assertEqual(cut["totals"], {"guides": 1, "decks": 3, "scenarios": 1})
        self.assertEqual(cut["cards"][0], {"card": 1, "decks": 2, "borrows": 1, "share": 2 / 3})
        self.assertEqual([entry["card"] for entry in cut["cards"]], [1, 2, 3])
        self.assertEqual([(entry["uma"], entry["share"]) for entry in cut["umas"]], [(7, 0.5), (8, 0.5)])

    def test_a_card_with_no_type_is_left_off_as_the_page_does(self):
        SupportCard.objects.filter(id=1).update(card_type="")
        payload = {"generated_at": "x", "scenarios": [], "guides": [{
            "kind": "cm", "event": 1, "scenario": None, "umas": [],
            "cards": [{"card": 1, "deck": deck, "style": "pace", "tier": "standard", "role": "main"} for deck in (1, 2)]
            + [{"card": 2, "deck": 1, "style": "pace", "tier": "standard", "role": "main"}],
        }]}
        cut = limited("guides", payload)
        self.assertEqual([entry["card"] for entry in cut["cards"]], [2])
        # Its decks still count toward every share.
        self.assertEqual(cut["totals"]["decks"], 2)

    def test_pull_plans_have_no_cut(self):
        with self.assertRaises(ValueError):
            limited("pull-plans", {"generated_at": "x"})
