"""The Rankings page's routes: who may read them, what they send, and the
half-day cache (views/rankings.py, rankings_cache.py)."""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from calculatorapi.models import (
    GalleryPost, GalleryPostKind, PvpDeck, PvpDeckSlot, PvpGuide, PvpRun, PvpRunRace, PvpRunRunner, PvpTeam,
    PvpTeamCard, PvpTeamMember, PvpUmaRecommendation, RankingsSnapshot, SupportCard, TeamTrialsRank, Uma, UserOshi,
    UserPlannedBanner, UserPlannedPurchase,
)
from calculatorapi.rankings_shares import SMALL, run_shares, share
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    auth_client, make_anniversary_event, make_champions_meeting, make_league_of_heroes, make_scenario,
    make_step_up_banner, make_support_banner, make_timeline, make_uma_banner, make_user,
)
from calculatorapi.views.rankings import deck_tier, guides_payload

URL = "/rankings/guides"


def full_deck(filler, *cards, borrow=None):
    """Six (slot, card) pairs: `cards` from slot 1, then `filler` (a placeholder
    fills a slot and is never counted), `borrow` in slot 6."""
    owned = [*cards, *[filler] * (5 - len(cards))]
    return [*enumerate(owned, start=1), (6, borrow or filler)]


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


class HalfDayCacheTests(RankingsFixture):
    """Built once, stamped, and NOT invalidated by a guide edit: half-day
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
        self.haru = Uma.objects.create(name="Haru Urara")
        self.rice = Uma.objects.create(name="Rice Shower")
        self.light = SupportCard.objects.create(name="Light Hello", card_type="friend", rarity=3)
        self.kitasan = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
        self.any_speed = SupportCard.objects.create(
            name="SSR Speed", card_type="speed", rarity=3, is_placeholder=True,
        )
        self.post(make_user("a"), [
            self.member(self.oguri, "pace", full_deck(self.any_speed, self.light, borrow=self.kitasan)),
            self.member(self.maru, "front", full_deck(self.any_speed, self.light)),
            self.member(self.haru, "late", full_deck(self.any_speed)),
        ])
        self.post(make_user("b"), [
            self.member(self.oguri, "end", full_deck(self.any_speed, self.kitasan)),
            self.member(self.rice, "pace", full_deck(self.any_speed)),
            self.member(self.haru, "late", full_deck(self.any_speed)),
        ])
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

    def test_visible_posts_umas_and_cards_as_shares(self):
        body = self.client.get(self.url).json()
        # Posts are public (the post page shows them), so their counts stay.
        self.assertEqual((body["posts"], body["decks"]), (2, 6))
        self.assertEqual(body["umas"], [
            {"uma": self.oguri.id, "share": 1.0, "styles": {"pace": 0.5, "end": 0.5}},
            {"uma": self.haru.id, "share": 1.0, "styles": {"late": 1.0}},
            {"uma": self.maru.id, "share": 0.5, "styles": {"front": 0.5}},
            {"uma": self.rice.id, "share": 0.5, "styles": {"pace": 0.5}},
        ])
        # A borrow is a share of the card's own decks.
        self.assertEqual(body["cards"], [
            {"card": self.light.id, "share": 0.33, "borrowed": 0},
            {"card": self.kitasan.id, "share": 0.33, "borrowed": 0.5},
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
            "umas": [self.oguri.id, self.maru.id, self.haru.id],
        })

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
        # Three of the four people with a banner planned (a, b, c and d; staff out).
        self.assertEqual(
            (uma["id"], uma["name"], uma["unit"], uma["share"], uma["median"]),
            (self.uma.id, "Gentildonna", "pulls", 0.75, 150),
        )
        self.assertAlmostEqual(uma["average"], 416.7)
        step_up = next(row for row in body["banners"] if row["type"] == "step_up")
        self.assertEqual((step_up["unit"], step_up["median"]), ("steps", 4))
        # Never a count: not everyone's, not a banner's planners or total pulls.
        self.assertNotIn("planners", body)
        self.assertFalse({"planners", "total"} & set(uma))
        self.assertIn("demand", body)
        self.assertIn("generated_at", body)

    def test_spread_is_shares_and_steps_keep_their_own_buckets(self):
        body = self.editor.get(self.URL).json()
        uma = next(row for row in body["banners"] if row["type"] == "uma")
        # a 100, b 150, c 1000: the 100 to 199 bucket holds two of three, 300 and up one.
        self.assertEqual(uma["spread"], {"edges": [0, 50, 100, 200, 300], "shares": [0, 0, 0.67, 0, 0.33]})
        step_up = next(row for row in body["banners"] if row["type"] == "step_up")
        self.assertEqual(step_up["spread"], {"edges": [0, 2, 3, 4, 5], "shares": [0, 0, 0, 1.0, 0]})

    def test_also_planning_lists_the_other_banners_never_the_banner_itself(self):
        support = make_support_banner(name="Kitasan Black")
        for name in ("a", "b"):
            UserPlannedBanner.objects.create(user=self.users[name], banner_support=support, number_of_pulls=50)
        body = self.editor.get(self.URL).json()
        uma = next(row for row in body["banners"] if row["type"] == "uma")
        self.assertEqual(
            uma["also"], [{"type": "support", "id": support.id, "name": "Kitasan Black", "share": 0.67}],
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
        self.haru = Uma.objects.create(name="Haru Urara")
        self.light = SupportCard.objects.create(name="Light Hello", card_type="friend", rarity=3)
        self.filler = SupportCard.objects.create(
            name="SSR Speed", card_type="speed", rarity=3, is_placeholder=True,
        )
        self.player = make_user("player")
        self.team(self.player, [
            (self.oguri, "pace", full_deck(self.filler, self.light)),
            (self.maru, "front", []),
            (self.haru, "late", full_deck(self.filler, borrow=self.light)),
        ])
        self.team(make_user("other"), [
            (self.oguri, "end", full_deck(self.filler, self.light)),
            (self.maru, "pace", full_deck(self.filler)),
            (self.haru, "late", full_deck(self.filler)),
        ])
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

    def test_every_finished_saved_team_as_shares(self):
        built = self.client.get(self.url).json()["built"]
        # Two teams of three, five full decks: never sent.
        self.assertFalse({"teams", "decks"} & set(built))
        self.assertEqual(built["umas"][0], {"uma": self.oguri.id, "share": 1.0, "styles": {"pace": 0.5, "end": 0.5}})
        self.assertEqual(built["cards"], [{"card": self.light.id, "share": 0.6, "borrowed": 0.33}])

    def test_half_built_teams_and_decks_stay_out(self):
        kitasan = SupportCard.objects.create(name="Kitasan Black", card_type="speed", rarity=3)
        rice = Uma.objects.create(name="Rice Shower")
        # A full deck on a team of two: the whole team is out.
        self.team(make_user("two"), [
            (self.oguri, "end", full_deck(self.filler, kitasan)),
            (rice, "late", full_deck(self.filler)),
        ])
        # A team of three counts its umas, but five cards are no full deck.
        self.team(make_user("five"), [
            (self.oguri, "end", full_deck(self.filler, kitasan)[:5]),
            (self.maru, "pace", []),
            (self.haru, "late", []),
        ])
        built = self.client.get(self.url).json()["built"]
        self.assertNotIn(rice.id, [row["uma"] for row in built["umas"]])
        self.assertEqual(built["umas"][1]["share"], 1.0)
        self.assertEqual([row["card"] for row in built["cards"]], [self.light.id])

    def test_trios_count_exact_three_uma_teams_once_each(self):
        kitasan = Uma.objects.create(name="Kitasan Black")
        for name in ("t1", "t2"):
            self.team(make_user(name), [(self.oguri, "pace", []), (self.maru, "front", []), (kitasan, "end", [])])
        built = self.client.get(self.url).json()["built"]
        self.assertEqual(built["trios"], [
            {"umas": sorted([self.oguri.id, self.maru.id, self.haru.id]), "share": 0.5},
            {"umas": sorted([self.oguri.id, self.maru.id, kitasan.id]), "share": 0.5},
        ])

    def test_win_rates_per_uma_and_strategy(self):
        # Five tickets of five races, 25 in all: Oguri wins 10, Maruzensky 5.
        for ticket in range(1, 6):
            self.log_run(self.player, [0, 0, 1, None, None], ticket=ticket)
        # A skipped ticket and a staff member's never count.
        self.log_run(make_user("skipper"), [0, 0, 0], skipped=True)
        self.log_run(make_user("staffer2", is_staff=True), [1, 1, 1])
        runs = self.client.get(self.url).json()["runs"]
        # Rates and how much to trust them, never races, wins or players. Under
        # RANKED_RACES, so unranked, with one mark.
        self.assertEqual(runs, [
            {"uma": self.oguri.id, "style": "pace", "rate": 0.4, "marks": 1, "ranked": False},
            {"uma": self.maru.id, "style": "front", "rate": 0.2, "marks": 1, "ranked": False},
        ])

    def test_a_win_rate_under_the_floor_is_never_sent(self):
        self.log_run(self.player, [0, None, 1])
        self.assertEqual(self.client.get(self.url).json()["runs"], [])

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
        self.assertEqual([row["uma"] for row in body["favourites"]], [uma.id])
        self.assertEqual(set(body["favourites"][0]), {"uma", "share"})
        self.assertEqual([rank["label"] for rank in body["ranks"]], ["Team Trials", "Club Rank"])
        for rank in body["ranks"]:
            self.assertNotIn("Not set", [row["name"] for row in rank["rows"]])
        self.assertNotIn("engaged", body)
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
        self.assertEqual(budget, [{"name": "Class 6", "carats": 25_000, "pulls": 200}])

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

    def test_early_access_only(self):
        plain, _ = auth_client(make_user("plain"))
        self.assertEqual(plain.get(self.URL).status_code, 403)


class SharesNeverCountsTests(CalculatorTestCase):
    """rankings_shares.py: no Rankings payload sends a count of people (owner,
    2026-10-28), and a share is a whole percent."""

    # Keys that held a count of people before the shares; none may come back.
    COUNT_KEYS = {
        "people", "planners", "teams", "races", "wins", "players", "engaged", "total", "borrows", "total_pulls",
    }

    def keys(self, value):
        if isinstance(value, dict):
            return set(value) | {key for item in value.values() for key in self.keys(item)}
        if isinstance(value, list):
            return {key for item in value for key in self.keys(item)}
        return set()

    def test_no_payload_sends_a_count_of_people(self):
        cm = make_champions_meeting(name="Libra Cup", cm_number=21)
        client, _ = auth_client(make_user("staff", is_staff=True))
        for url in (
            "/rankings/players", "/rankings/pull-plans", f"/rankings/pvp/cm/{cm.id}", f"/rankings/gallery/cm/{cm.id}",
        ):
            body = client.get(url).json()
            # `previous` is ids in rank order, and the gallery's posts are public.
            body.pop("previous", None)
            self.assertEqual(self.keys(body) & self.COUNT_KEYS, set(), url)

    def test_the_snapshot_keeps_the_counts(self):
        client, _ = auth_client(make_user("staff", is_staff=True))
        client.get("/rankings/players")
        self.assertIn("engaged", RankingsSnapshot.objects.get(name="players").payload)

    def test_a_share_is_a_whole_percent_and_a_sliver_is_never_nothing(self):
        self.assertEqual([share(1, 3), share(2, 3), share(0, 5), share(3, 0)], [0.33, 0.67, 0, 0])
        # 1 of 300 is 0.33%: sent as SMALL, which the page says as "<1%".
        self.assertEqual(share(1, 300), SMALL)

    def test_win_rates_ranked_from_100_races_the_thin_ones_under_and_marked(self):
        rows = [
            {"uma": 1, "style": "pace", "races": 400, "wins": 100},
            {"uma": 2, "style": "end", "races": 19, "wins": 19},
            {"uma": 3, "style": "front", "races": 44, "wins": 13},
            {"uma": 4, "style": "late", "races": 120, "wins": 36},
            {"uma": 5, "style": "pace", "races": 75, "wins": 15},
            {"uma": 6, "style": "end", "races": 100, "wins": 30},
        ]
        rated = run_shares({"min_races": 20, "rows": rows})
        # By rate from 100 races, more races breaking a tie; then 20 to 99 by
        # races (30% from 44 never outranks 25% from 400); 19 races never sent.
        self.assertEqual(
            [(row["uma"], row["rate"], row["marks"], row["ranked"]) for row in rated],
            [(4, 0.3, 2, True), (6, 0.3, 2, True), (1, 0.25, 3, True), (5, 0.2, 1, False), (3, 0.3, 1, False)],
        )
