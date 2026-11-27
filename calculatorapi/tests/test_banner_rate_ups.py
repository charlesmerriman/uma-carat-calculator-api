"""SR and R rate-ups on support banners: how each one shows on the Timeline
("auto" and an editor's choice), the payload's two lists, the support card
constants, the admin's rule for an R, and the deploy's import.
notes/banner-sr-r-plan.md."""

import datetime
import json
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError

from calculatorapi.banner_rate_ups import split_rate_ups
from calculatorapi.models import (
    CalculationConstants, SupportCard, SupportsOnSupportBanner, TimelineDisplay,
)

from .base import CalculatorTestCase
from .factories import make_support_banner, make_timeline
from .test_skills import import_game_data, write_snapshot

UTC = datetime.timezone.utc


def card(game_id, name=None):
    """A support card; its rarity comes from the id's first digit (1 R, 2 SR, 3 SSR)."""
    return SupportCard.objects.create(name=name or f"Card {game_id}", game_id=game_id)


def link(banner, support_card, display=TimelineDisplay.AUTO):
    return SupportsOnSupportBanner.objects.create(
        banner_support=banner, support_card=support_card, timeline_display=display,
    )


def displays(banner):
    """{card game id: display} for the banner's SRs and Rs, as the payload resolves them."""
    _, others = split_rate_ups(banner.supportsonsupportbanner_set.select_related("support_card"))
    return {row.support_card.game_id: display for row, display in others}


class AutomaticDisplayTests(CalculatorTestCase):
    """The table in banner_rate_ups.py, row by row."""

    def setUp(self):
        self.banner = make_support_banner()

    def test_an_r_is_listed(self):
        link(self.banner, card(30001))
        link(self.banner, card(10001))
        self.assertEqual(displays(self.banner), {10001: "named"})

    def test_the_only_sr_beside_one_ssr_gets_its_picture(self):
        link(self.banner, card(30001))
        link(self.banner, card(20001))
        link(self.banner, card(10001))
        self.assertEqual(displays(self.banner), {20001: "picture", 10001: "named"})

    def test_an_sr_beside_two_ssrs_is_listed_like_an_r(self):
        link(self.banner, card(30001))
        link(self.banner, card(30002))
        link(self.banner, card(20001))
        self.assertEqual(displays(self.banner), {20001: "named"})

    def test_one_ssr_with_several_srs_pictures_only_the_first(self):
        # The owner, 2026-11-27: "only one SR card should show"; the panel is
        # laid out for two tiles.
        link(self.banner, card(30001))
        for game_id in (20001, 20002, 20003):
            link(self.banner, card(game_id))
        self.assertEqual(displays(self.banner), {20001: "picture", 20002: "named", 20003: "named"})

    def test_an_editors_picture_takes_the_one_slot(self):
        link(self.banner, card(30001))
        link(self.banner, card(20001))
        link(self.banner, card(20002), TimelineDisplay.PICTURE)
        self.assertEqual(displays(self.banner), {20001: "named", 20002: "picture"})

    def test_two_pictures_still_draw_one(self):
        link(self.banner, card(30001))
        link(self.banner, card(20001), TimelineDisplay.PICTURE)
        link(self.banner, card(20002), TimelineDisplay.PICTURE)
        self.assertEqual(displays(self.banner), {20001: "picture", 20002: "named"})

    def test_an_ssr_added_later_changes_the_answer(self):
        link(self.banner, card(30001))
        link(self.banner, card(20001))
        self.assertEqual(displays(self.banner), {20001: "picture"})
        link(self.banner, card(30002))
        self.assertEqual(displays(self.banner), {20001: "named"})

    def test_an_editors_named_and_hidden_win(self):
        link(self.banner, card(30001))
        link(self.banner, card(20001), TimelineDisplay.NAMED)
        link(self.banner, card(20002), TimelineDisplay.HIDDEN)
        link(self.banner, card(10001), TimelineDisplay.HIDDEN)
        self.assertEqual(displays(self.banner), {20001: "named", 20002: "hidden", 10001: "hidden"})

    def test_no_picture_beside_two_ssrs_even_when_set(self):
        link(self.banner, card(30001))
        link(self.banner, card(30002))
        link(self.banner, card(20001), TimelineDisplay.PICTURE)
        self.assertEqual(displays(self.banner), {20001: "named"})

    def test_a_stray_picture_on_an_r_reads_as_listed(self):
        link(self.banner, card(10001), TimelineDisplay.PICTURE)
        self.assertEqual(displays(self.banner), {10001: "named"})

    def test_srs_come_before_rs(self):
        link(self.banner, card(10001))
        link(self.banner, card(30001))
        link(self.banner, card(20001))
        self.assertEqual(list(displays(self.banner)), [20001, 10001])

    def test_the_admin_refuses_a_picture_on_an_r(self):
        row = SupportsOnSupportBanner(
            banner_support=self.banner, support_card=card(10001), timeline_display=TimelineDisplay.PICTURE,
        )
        with self.assertRaises(ValidationError):
            row.full_clean()


class PayloadTests(CalculatorTestCase):
    """support_cards stays the SSRs; the rest travel as other_rate_ups."""

    def setUp(self):
        self.timeline = make_timeline()
        self.banner = make_support_banner(self.timeline)
        link(self.banner, card(30001, "SSR One"))
        link(self.banner, card(20001, "SR One"))
        link(self.banner, card(10001, "R One"))

    def test_calculator_data_splits_the_cards(self):
        data = self.client.get("/calculator-data").json()
        banner = next(b for b in data["banner_support_data"] if b["id"] == self.banner.id)
        self.assertEqual([c["name"] for c in banner["support_cards"]], ["SSR One"])
        self.assertEqual(
            [(c["name"], c["rarity"], c["display"]) for c in banner["other_rate_ups"]],
            [("SR One", 2, "picture"), ("R One", 1, "named")],
        )
        # The card's own fields ride along, for the odds and the row's links.
        self.assertEqual(banner["other_rate_ups"][0]["game_id"], 20001)
        self.assertIn("first_jp_date", banner["other_rate_ups"][0])

    def test_the_timeline_splits_them_the_same_way(self):
        data = self.client.get("/calculator-data").json()
        row = next(t for t in data["banner_timeline_data"] if t["id"] == self.timeline.id)
        banner = row["banner_supports"][0]
        self.assertEqual([c["name"] for c in banner["support_cards"]], ["SSR One"])
        self.assertEqual([c["display"] for c in banner["other_rate_ups"]], ["picture", "named"])

    def test_the_support_constants_are_numbers(self):
        constants = self.client.get("/calculator-data").json()["calculation_constants"]
        self.assertEqual(constants["support_rate_up_rate_2"], 0.0225)
        self.assertEqual(constants["support_rate_up_pool_2"], 0.03)
        self.assertEqual(constants["support_rate_up_rate_1"], 0.0375)
        self.assertEqual(constants["support_rate_up_pool_1"], 0.05)
        # The uma side is untouched by them.
        self.assertEqual(
            float(CalculationConstants.objects.get().rate_up_rate_1), constants["rate_up_rate_1"],
        )


def gacha(gacha_id, start, rate_ups):
    return {"id": gacha_id, "start": start, "end": None,
            "rate_ups": [{"card": c, "rarity": int(str(c)[0]), "odds": 7500} for c in rate_ups]}


class RateUpImportTests(CalculatorTestCase):
    """The deploy's import: adds the SR and R links a gacha names, nothing else."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.global_dir = Path(tmp.name) / "global"
        self.jp_dir = Path(tmp.name) / "jp"
        for directory in (self.global_dir, self.jp_dir):
            directory.mkdir()
            write_snapshot(directory, [])
            for name in ("evolutions",):
                (directory / f"{name}.json").write_text(
                    json.dumps({"outfit": [], "scenario": [], "succession": []}), encoding="utf-8",
                )
        # A JP day: the timeline's jp_start_date holds it at 22:00 UTC, the
        # gacha opens at noon Japan time that day.
        self.timeline = make_timeline(
            jp_start_date=datetime.datetime(2023, 4, 19, 22, 0, tzinfo=UTC),
            jp_end_date=datetime.datetime(2023, 4, 28, 22, 0, tzinfo=UTC),
        )
        self.banner = make_support_banner(self.timeline, "Symboli Rudolf")
        self.ssr = card(30037, "Symboli Rudolf")
        link(self.banner, self.ssr)
        self.sr = card(20061, "Gold Ship")
        self.r = card(10091, "Katsuragi Ace")

    def write(self, global_gachas=(), jp_gachas=()):
        (self.global_dir / "support_gachas.json").write_text(json.dumps(list(global_gachas)), encoding="utf-8")
        (self.jp_dir / "support_gachas.json").write_text(json.dumps(list(jp_gachas)), encoding="utf-8")

    def run_import(self):
        return import_game_data(self.global_dir, "--jp-snapshot", str(self.jp_dir))

    def linked(self):
        return set(self.banner.supportsonsupportbanner_set.values_list("support_card__game_id", flat=True))

    def test_adds_the_srs_and_rs_by_jp_day_and_ssrs(self):
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30037, 20061, 10091])])
        out = self.run_import()
        self.assertEqual(self.linked(), {30037, 20061, 10091})
        self.assertIn("adding 2 link(s)", out)
        added = SupportsOnSupportBanner.objects.get(banner_support=self.banner, support_card=self.sr)
        self.assertEqual(added.timeline_display, TimelineDisplay.AUTO)

    def test_a_second_run_adds_nothing(self):
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30037, 20061])])
        self.run_import()
        out = self.run_import()
        self.assertEqual(SupportsOnSupportBanner.objects.filter(banner_support=self.banner).count(), 2)
        self.assertIn("already linked 1", out)

    def test_an_editors_choice_and_the_ssr_are_left_alone(self):
        existing = link(self.banner, self.sr, TimelineDisplay.HIDDEN)
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30037, 20061])])
        self.run_import()
        existing.refresh_from_db()
        self.assertEqual(existing.timeline_display, TimelineDisplay.HIDDEN)
        self.assertEqual(SupportsOnSupportBanner.objects.filter(support_card=self.ssr).count(), 1)

    def test_global_start_to_the_minute_wins(self):
        global_start = datetime.datetime(2026, 1, 9, 22, 0, tzinfo=UTC)
        self.timeline.global_start_date = global_start
        self.timeline.save()
        self.write(global_gachas=[gacha(30161, global_start.isoformat(), [30037, 10091])])
        self.run_import()
        self.assertEqual(self.linked(), {30037, 10091})

    def test_a_banner_with_other_ssrs_is_not_matched(self):
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30038, 20061])])
        out = self.run_import()
        self.assertEqual(self.linked(), {30037})
        self.assertIn("no banner with the same day and SSRs for 1 gacha(s): 30161", out)

    def test_a_banner_merging_two_gachas_takes_each_ones_rate_ups(self):
        # The site's banner holds two SSRs; JP ran them as two gachas, and the
        # one with the SR names only its own SSR (Mejiro Ramonu, 2026-12-01).
        link(self.banner, card(30137, "Progenitors & Guides"))
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30037, 20061])])
        self.run_import()
        self.assertEqual(self.linked(), {30037, 30137, 20061})

    def test_two_banners_that_both_hold_the_ssrs_are_left_alone(self):
        other = make_support_banner(self.timeline, "Rerun")
        link(other, self.ssr)
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30037, 20061])])
        out = self.run_import()
        self.assertEqual(self.linked(), {30037})
        self.assertIn("for 1 gacha(s): 30161", out)

    def test_a_card_with_no_row_is_reported_not_created(self):
        self.write(jp_gachas=[gacha(30161, "2023-04-19T03:00:00+00:00", [30037, 20099])])
        out = self.run_import()
        self.assertFalse(SupportCard.objects.filter(game_id=20099).exists())
        self.assertIn("card 20099 (gacha 30161) has no row", out)
