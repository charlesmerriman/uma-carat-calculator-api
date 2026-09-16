"""Umas as rows: the duplicate merge that runs before game ids arrive."""

from io import StringIO

from django.core.management import call_command

from calculatorapi.models import (
    AnniversaryEventProduct,
    ChampionsMeetingUmaRecommendation,
    Uma,
    UmasOnUmaBanner,
    UserOshi,
    UserPlannedPurchase,
    UserStepUpSelection,
)
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.factories import (
    make_anniversary_event,
    make_champions_meeting,
    make_step_up_banner,
    make_uma_banner,
    make_user,
)


def merge(*flags):
    """Run the command scripted and hand back what it printed."""
    out = StringIO()
    call_command("merge_duplicate_umas", "--no-input", *flags, stdout=out)
    return out.getvalue()


class MergeDuplicateUmasTests(CalculatorTestCase):
    """
    The scenario the command was written for: a rerun banner's backfill made a
    second "Seiun Sky (Rerun)" row sharing the original's image.
    """

    def setUp(self):
        self.original = Uma.objects.create(
            name="Seiun Sky", image="umas/102001-Seiun-Sky.png",
        )
        self.rerun = Uma.objects.create(
            name="Seiun Sky (Rerun)", image="umas/102001-Seiun-Sky.png",
        )
        # A different outfit of the same character: a different card id, so
        # it must never be pulled into the merge.
        self.ballroom = Uma.objects.create(
            name="Seiun Sky (Ballroom)", image="umas/102002-Seiun-Sky-Ballroom.png",
        )
        self.debut_banner = make_uma_banner(name="Seiun Sky")
        self.rerun_banner = make_uma_banner(name="Seiun Sky (Rerun)")
        UmasOnUmaBanner.objects.create(banner_uma=self.debut_banner, uma=self.original)
        UmasOnUmaBanner.objects.create(banner_uma=self.rerun_banner, uma=self.rerun)

    def test_relinks_the_rerun_banner_and_deletes_the_copy(self):
        merge()

        self.assertFalse(Uma.objects.filter(pk=self.rerun.pk).exists())
        self.assertTrue(Uma.objects.filter(pk=self.original.pk).exists())
        self.assertTrue(Uma.objects.filter(pk=self.ballroom.pk).exists())
        self.assertEqual(
            list(self.rerun_banner.umas.values_list("pk", flat=True)), [self.original.pk],
        )
        self.assertEqual(self.original.umasonumabanner_set.count(), 2)

    def test_drops_the_link_when_the_banner_already_lists_the_original(self):
        # The join has no unique constraint, so a blind re-point would show
        # the same uma twice on the tile.
        UmasOnUmaBanner.objects.create(banner_uma=self.rerun_banner, uma=self.original)

        out = merge()

        self.assertEqual(
            UmasOnUmaBanner.objects.filter(
                banner_uma=self.rerun_banner, uma=self.original,
            ).count(),
            1,
        )
        self.assertIn("dropped 1 row(s)", out)

    def test_dry_run_reports_and_writes_nothing(self):
        out = merge("--dry-run")

        self.assertIn("Would merge 1 duplicate(s)", out)
        self.assertIn("Seiun Sky (Rerun)", out)
        self.assertTrue(Uma.objects.filter(pk=self.rerun.pk).exists())
        self.assertEqual(self.rerun_banner.umas.get(), self.rerun)

    def test_second_run_finds_nothing(self):
        merge()
        out = merge()

        self.assertIn("Nothing to merge", out)
        self.assertEqual(Uma.objects.count(), 2)

    def test_prefix_of_the_newer_upload_style_is_the_same_id(self):
        # Newer uploads carry the star rarity in front: `3-102001-...`. Same
        # card id, so still the same outfit.
        self.rerun.image = "umas/3-102001-Seiun-Sky.png"
        self.rerun.save()

        merge()

        self.assertFalse(Uma.objects.filter(pk=self.rerun.pk).exists())

    def test_umas_without_an_image_are_never_grouped(self):
        Uma.objects.create(name="(All)")
        Uma.objects.create(name="(All) 2")

        out = merge()

        self.assertIn("Merged 1 uma(s)", out)
        self.assertEqual(Uma.objects.filter(name__startswith="(All)").count(), 2)

    def test_two_originals_sharing_an_image_are_left_alone(self):
        # Neither row says it is the copy, so the command cannot pick a
        # survivor. It reports the pair for an editor instead of guessing.
        self.rerun.name = "Seiun Sky (Summer)"
        self.rerun.save()

        out = merge()

        self.assertIn("Left alone: 1 group(s)", out)
        self.assertIn("2 rows lack the (Rerun) suffix", out)
        self.assertEqual(Uma.objects.count(), 3)

    def test_group_of_only_copies_is_left_alone(self):
        self.original.name = "Seiun Sky (rerun)"
        self.original.save()

        out = merge()

        self.assertIn("none is the original", out)
        self.assertEqual(Uma.objects.count(), 3)


class MergeDuplicateUmasRelationTests(CalculatorTestCase):
    """
    Every other model that points at an uma follows it to the kept row. These
    are the rows a CASCADE delete would otherwise silently take with the copy.
    """

    def setUp(self):
        self.original = Uma.objects.create(
            name="Narita Brian", image="umas/101601-Narita-Brian.png",
        )
        self.rerun = Uma.objects.create(
            name="Narita Brian (Rerun)", image="umas/101601-Narita-Brian.png",
        )
        self.user = make_user("merge_user")

    def test_oshi_follows_the_merge(self):
        UserOshi.objects.create(user=self.user, uma=self.rerun, position=0)

        merge()

        self.assertEqual(self.user.oshis.get().uma, self.original)

    def test_oshi_is_dropped_when_the_person_already_picked_the_original(self):
        # unique (user, uma) would reject the re-point. The copy's row is
        # dropped and the person keeps the original at its own position.
        UserOshi.objects.create(user=self.user, uma=self.original, position=0)
        UserOshi.objects.create(user=self.user, uma=self.rerun, position=1)

        out = merge()

        self.assertEqual(list(self.user.oshis.values_list("uma_id", "position")),
                         [(self.original.pk, 0)])
        self.assertIn("dropped 1 row(s)", out)

    def test_step_up_selection_follows_the_merge(self):
        step_up = make_step_up_banner(card_type="uma")
        UserStepUpSelection.objects.create(
            user=self.user, banner_step_up=step_up, slot=1, uma=self.rerun,
        )

        merge()

        self.assertEqual(UserStepUpSelection.objects.get().uma, self.original)

    def test_planned_purchase_target_follows_the_merge(self):
        event = make_anniversary_event()
        selector = AnniversaryEventProduct.objects.create(
            anniversary_event=event, product_type="uma_selector",
            name="Uma Selector", usd_cost=21, paid_carat_amount=1500,
        )
        purchase = UserPlannedPurchase.objects.create(
            user=self.user, product=selector, quantity=1, target_uma=self.rerun,
        )

        merge()

        purchase.refresh_from_db()
        # SET_NULL on this FK: without the re-point the target would simply
        # vanish from the person's plan.
        self.assertEqual(purchase.target_uma, self.original)

    def test_champions_meeting_recommendation_follows_and_never_doubles(self):
        cm = make_champions_meeting()
        ChampionsMeetingUmaRecommendation.objects.create(uma=self.original, champions_meeting=cm)
        ChampionsMeetingUmaRecommendation.objects.create(uma=self.rerun, champions_meeting=cm)
        other_cm = make_champions_meeting(name="Other CM", cm_number=2)
        ChampionsMeetingUmaRecommendation.objects.create(uma=self.rerun, champions_meeting=other_cm)

        merge()

        self.assertEqual(
            sorted(
                ChampionsMeetingUmaRecommendation.objects.filter(uma=self.original)
                .values_list("champions_meeting_id", flat=True)
            ),
            sorted([cm.pk, other_cm.pk]),
        )
        self.assertEqual(ChampionsMeetingUmaRecommendation.objects.count(), 2)
