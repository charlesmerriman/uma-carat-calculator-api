"""Support card variants: which card a banner features, the backfill, and the repair command."""

import csv
import datetime
import json
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.utils import timezone

from calculatorapi import support_backfill
from calculatorapi.models import (
    Uma, SupportCard,
    BannerTimeline, BannerUma, BannerSupport,
    UmasOnUmaBanner, SupportsOnSupportBanner,
    BannerCategory,
)
from calculatorapi.tests.base import CalculatorTestCase


class SupportVariantResolutionTests(CalculatorTestCase):
    """
    Which of several same-named SupportCard rows a banner actually features.

    Split from SupportBackfillTests because these exercise the resolver alone —
    no CSV, no commands, no database beyond the cards themselves.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.source_path = os.path.join(self.tmp, 'support_cards_source.json')
        patcher = patch.object(
            support_backfill, 'SUPPORT_CARDS_SOURCE', self.source_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._write_source({})

    def _write_source(self, releases, name=None):
        """releases: {game_id: "YYYY-MM-DD"} — the shape resolve_variant reads."""
        with open(self.source_path, 'w', encoding='utf-8') as handle:
            json.dump(
                [{'id': game_id, 'release_ja': released, 'name_en': name}
                 for game_id, released in releases.items()],
                handle)
        support_backfill.clear_source_cache(self.source_path)

    def _cards(self, *names):
        for name in names:
            SupportCard.objects.get_or_create(name=name)

    # ── resolve_support_cards ────────────────────────────────────────────────

    def test_resolves_names_case_insensitively(self):
        self._cards('Super Creek')
        found, missing = support_backfill.resolve_support_cards(['super creek'])
        self.assertEqual([c.name for c in found], ['Super Creek'])
        self.assertEqual(missing, [])

    def test_resolves_the_known_spelling_differences(self):
        # "K.S. Miracle" needs no alias — normalize() drops the periods so it
        # and the card's own "K.S.Miracle" collapse together. The other two are
        # genuine aliases, shared with the fixture pipeline.
        self._cards('K.S.Miracle', 'Tamamo Cross', 'Tazuna Hayakawa')
        found, missing = support_backfill.resolve_support_cards(
            ['K.S. Miracle', 'Tamano Cross', 'Tazuna'])
        self.assertEqual(missing, [])
        self.assertEqual(
            sorted(c.name for c in found),
            ['K.S.Miracle', 'Tamamo Cross', 'Tazuna Hayakawa'])

    def test_resolves_a_rerun_token_to_the_base_card(self):
        self._cards('Super Creek')
        found, missing = support_backfill.resolve_support_cards(['Super Creek (Rerun)'])
        self.assertEqual([c.name for c in found], ['Super Creek'])
        self.assertEqual(missing, [])

    def test_duplicate_names_fall_back_to_the_lowest_ssr_without_dates(self):
        # SupportCard.name is deliberately not unique — production holds four
        # rows named "Grass Wonder" (different rarities/reprints). With no
        # release date for any of them there is no honest way to choose, so the
        # rule degrades to the lowest SSR: still a guess, but always a card a
        # banner could actually feature.
        SupportCard.objects.create(name='Grass Wonder', game_id=30200)
        wanted = SupportCard.objects.create(name='Grass Wonder', game_id=30100)
        SupportCard.objects.create(name='Grass Wonder', game_id=30300)

        found, missing = support_backfill.resolve_support_cards(['Grass Wonder'])

        self.assertEqual(missing, [])
        self.assertEqual([c.pk for c in found], [wanted.pk])

    def test_never_resolves_to_an_r_card_when_an_ssr_exists(self):
        # THE BUG THIS RULE REPLACED: the old "lowest game_id" wins picked the
        # R variant every time, because 1xxxx sorts under 3xxxx. It put twenty
        # R cards on the production launch banner.
        SupportCard.objects.create(name='Special Week', game_id=10001)
        wanted = SupportCard.objects.create(name='Special Week', game_id=30001)

        found, _ = support_backfill.resolve_support_cards(['Special Week'])

        self.assertEqual([c.pk for c in found], [wanted.pk])

    def test_a_real_ssr_beats_a_null_game_id(self):
        # The old rule sorted a null game_id as 0, which made it beat every real
        # card. A row with no game_id cannot be shown to be banner-eligible, so
        # a known SSR is strictly the better answer.
        SupportCard.objects.create(name='Nameless', game_id=None)
        wanted = SupportCard.objects.create(name='Nameless', game_id=30001)

        found, _ = support_backfill.resolve_support_cards(['Nameless'])

        self.assertEqual([c.pk for c in found], [wanted.pk])

    def test_a_name_with_no_game_ids_at_all_still_resolves(self):
        # Rows predating the game_id backfill must keep working — refusing them
        # would turn a working batch into a skipped one.
        wanted = SupportCard.objects.create(name='Ancient', game_id=None)
        SupportCard.objects.create(name='Ancient', game_id=None)

        found, missing = support_backfill.resolve_support_cards(['Ancient'])

        self.assertEqual(missing, [])
        self.assertEqual([c.pk for c in found], [wanted.pk])

    # ── resolve_variant's date tiers ─────────────────────────────────────────

    def test_picks_the_ssr_that_debuted_on_the_banner(self):
        SupportCard.objects.create(name='Agnes Digital', game_id=30085)
        wanted = SupportCard.objects.create(name='Agnes Digital', game_id=30297)
        self._write_source({30085: '2022-02-08', 30297: '2026-04-30'})

        found, _ = support_backfill.resolve_support_cards(
            ['Agnes Digital'], datetime.date(2026, 4, 30))

        self.assertEqual([c.pk for c in found], [wanted.pk])

    def test_a_rerun_takes_the_most_recent_card_that_already_existed(self):
        # No card debuts on a rerun's date, so tier 2 decides: the newest
        # variant released on or before it.
        SupportCard.objects.create(name='Nice Nature', game_id=30054)
        wanted = SupportCard.objects.create(name='Nice Nature', game_id=30138)
        SupportCard.objects.create(name='Nice Nature', game_id=30239)
        self._write_source(
            {30054: '2021-08-20', 30138: '2023-03-29', 30239: '2025-01-31'})

        found, _ = support_backfill.resolve_support_cards(
            ['Nice Nature'], datetime.date(2024, 6, 1))

        self.assertEqual([c.pk for c in found], [wanted.pk])

    def test_refuses_two_candidates_released_too_close_together(self):
        # Inside the 14-day margin there is no confident answer, and a wrong
        # link here silently moves the card's selector eligibility.
        SupportCard.objects.create(name='Twins', game_id=30100)
        SupportCard.objects.create(name='Twins', game_id=30101)
        self._write_source({30100: '2024-01-01', 30101: '2024-01-08'})

        found, missing = support_backfill.resolve_support_cards(
            ['Twins'], datetime.date(2024, 6, 1))

        self.assertEqual(found, [])
        self.assertEqual(missing, ['Twins'])

    def test_refuses_when_the_card_that_debuted_here_is_not_in_the_database(self):
        # THE TRAP the guard exists for. The source says 30293 launched on this
        # banner, but only the older reprint is in the database — so tier 2
        # would answer 30248, which reads as a confident correct answer and is
        # not. Every 2026 banner locally is in exactly this state.
        SupportCard.objects.create(name='Daring Tact', game_id=10120)
        SupportCard.objects.create(name='Daring Tact', game_id=30248)
        self._write_source(
            {30248: '2025-04-10', 30293: '2026-03-30'}, name='Daring Tact')

        found, missing = support_backfill.resolve_support_cards(
            ['Daring Tact'], datetime.date(2026, 3, 30))

        self.assertEqual(found, [])
        self.assertEqual(missing, ['Daring Tact'])

    def test_accepts_the_debut_card_once_it_exists(self):
        # The other half of the guard: adding the row unblocks the same call.
        SupportCard.objects.create(name='Daring Tact', game_id=30248)
        wanted = SupportCard.objects.create(name='Daring Tact', game_id=30293)
        self._write_source(
            {30248: '2025-04-10', 30293: '2026-03-30'}, name='Daring Tact')

        found, _ = support_backfill.resolve_support_cards(
            ['Daring Tact'], datetime.date(2026, 3, 30))

        self.assertEqual([c.pk for c in found], [wanted.pk])

    def test_refuses_candidates_that_all_postdate_the_banner(self):
        # A banner cannot feature a card that does not exist yet.
        SupportCard.objects.create(name='Future', game_id=30100)
        SupportCard.objects.create(name='Future', game_id=30200)
        self._write_source({30100: '2026-01-01', 30200: '2026-06-01'})

        found, missing = support_backfill.resolve_support_cards(
            ['Future'], datetime.date(2025, 1, 1))

        self.assertEqual(found, [])
        self.assertEqual(missing, ['Future'])

    def test_reports_an_unknown_name_rather_than_guessing(self):
        # The guard against fuzzy matching: a similarity check offered
        # "Hishi Miracle" as the runner-up for "K.S. Miracle".
        self._cards('Hishi Miracle')
        found, missing = support_backfill.resolve_support_cards(['K.S. Miracle'])
        self.assertEqual(found, [])
        self.assertEqual(missing, ['K.S. Miracle'])

    def test_resolves_a_card_that_is_on_no_banner(self):
        # Such a card is invisible to the public API, so the command must look
        # at SupportCard directly or it would wrongly report it missing.
        self._cards('Orphan Card')
        found, _ = support_backfill.resolve_support_cards(['Orphan Card'])
        self.assertEqual([c.name for c in found], ['Orphan Card'])


class SupportBackfillTests(CalculatorTestCase):
    """
    The race-prep support backfill and the launch banner's support half.

    Both read timeline_master.csv, so these tests point the module at a
    temporary CSV of their own rather than depending on the real file's
    contents — which change whenever a banner is added.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmp, 'timeline_master.csv')
        patcher = patch.object(support_backfill, 'TIMELINE_MASTER', self.csv_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Same treatment for the release-date source the variant tiers read.
        # The module caches parsed source data per path, so pointing at a temp
        # file both isolates these tests and keeps the real file's cache intact.
        self.source_path = os.path.join(self.tmp, 'support_cards_source.json')
        source_patcher = patch.object(
            support_backfill, 'SUPPORT_CARDS_SOURCE', self.source_path)
        source_patcher.start()
        self.addCleanup(source_patcher.stop)
        self._write_source({})

        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write_csv(self, rows):
        """rows: (jp_start, banner_type, uma, supports-joined)."""
        with open(self.csv_path, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['JP Start Date', 'Banner Type', 'Banner Uma', 'Banner Support'])
            writer.writerows(rows)

    def _write_source(self, releases, name=None):
        """releases: {game_id: "YYYY-MM-DD"} — the shape resolve_variant reads."""
        with open(self.source_path, 'w', encoding='utf-8') as handle:
            json.dump(
                [{'id': game_id, 'release_ja': released, 'name_en': name}
                 for game_id, released in releases.items()],
                handle)
        # Drop any parse cached from an earlier write in the same test.
        support_backfill.clear_source_cache(self.source_path)

    def _timeline(self, name, jp_start, **kwargs):
        return BannerTimeline.objects.create(
            name=name,
            jp_start_date=timezone.make_aware(datetime.datetime(*jp_start)),
            jp_end_date=timezone.make_aware(datetime.datetime(*jp_start) +
                                            datetime.timedelta(days=10)),
            **kwargs)

    def _cards(self, *names):
        for name in names:
            SupportCard.objects.get_or_create(name=name)

    # ── backfill_race_prep_supports ──────────────────────────────────────────

    def test_attaches_the_support_cards_and_sets_the_category(self):
        timeline = self._timeline('Satono Crown', (2023, 12, 11))
        self._cards('Super Creek', 'Tokai Teio')
        self._write_csv([('2023-12-11', '2', 'Satono Crown', 'Super Creek + Tokai Teio')])

        call_command('backfill_race_prep_supports', '--no-input')

        timeline.refresh_from_db()
        banner = timeline.support_banners.get()
        self.assertEqual(banner.name, 'Race Prep Support')
        self.assertEqual(
            sorted(banner.support_cards.values_list('name', flat=True)),
            ['Super Creek', 'Tokai Teio'])
        self.assertEqual(timeline.banner_category, BannerCategory.RACE_PREP_SUPPORT)

    def test_ignores_rows_that_are_not_race_prep(self):
        timeline = self._timeline('Ordinary', (2023, 12, 11))
        self._cards('Super Creek')
        self._write_csv([('2023-12-11', '1', 'Ordinary', 'Super Creek')])

        call_command('backfill_race_prep_supports', '--no-input')

        timeline.refresh_from_db()
        self.assertFalse(timeline.support_banners.exists())
        self.assertEqual(timeline.banner_category, BannerCategory.STANDARD)

    def test_dry_run_writes_nothing(self):
        timeline = self._timeline('Satono Crown', (2023, 12, 11))
        self._cards('Super Creek')
        self._write_csv([('2023-12-11', '2', 'Satono Crown', 'Super Creek')])

        call_command('backfill_race_prep_supports', '--dry-run')

        timeline.refresh_from_db()
        self.assertFalse(timeline.support_banners.exists())
        self.assertEqual(timeline.banner_category, BannerCategory.STANDARD)

    def test_is_idempotent(self):
        self._timeline('Satono Crown', (2023, 12, 11))
        self._cards('Super Creek')
        self._write_csv([('2023-12-11', '2', 'Satono Crown', 'Super Creek')])

        call_command('backfill_race_prep_supports', '--no-input')
        call_command('backfill_race_prep_supports', '--no-input')

        self.assertEqual(BannerSupport.objects.count(), 1)
        self.assertEqual(SupportsOnSupportBanner.objects.count(), 1)

    def test_skips_a_whole_banner_when_one_card_is_unknown(self):
        # Half a batch is worse than none: it would look complete in the UI
        # while quietly under-representing the banner.
        timeline = self._timeline('Satono Crown', (2023, 12, 11))
        self._cards('Super Creek')
        self._write_csv([('2023-12-11', '2', 'Satono Crown', 'Super Creek + Nonesuch')])

        call_command('backfill_race_prep_supports', '--no-input')

        timeline.refresh_from_db()
        self.assertFalse(timeline.support_banners.exists())
        self.assertEqual(timeline.banner_category, BannerCategory.STANDARD)

    def test_never_creates_support_cards(self):
        self._timeline('Satono Crown', (2023, 12, 11))
        self._write_csv([('2023-12-11', '2', 'Satono Crown', 'Nonesuch')])

        call_command('backfill_race_prep_supports', '--no-input')

        self.assertEqual(SupportCard.objects.count(), 0)

    def test_survives_a_csv_row_with_no_matching_timeline(self):
        self._cards('Super Creek')
        self._write_csv([('2023-12-11', '2', 'Ghost', 'Super Creek')])

        call_command('backfill_race_prep_supports', '--no-input')

        self.assertEqual(BannerSupport.objects.count(), 0)

    # ── repair_launch_banner, support half ───────────────────────────────────

    def test_launch_repair_links_supports_alongside_umas(self):
        self._timeline('Special Week + Tokai Teio', (2021, 2, 24))
        Uma.objects.create(name='Special Week')
        Uma.objects.create(name='Tokai Teio')
        self._cards('Super Creek', 'Tazuna Hayakawa')
        self._write_csv([('2021-02-24', '-2', '', 'Super Creek + Tazuna')])

        call_command('repair_launch_banner', '--no-input')

        timeline = BannerTimeline.objects.get()
        self.assertEqual(timeline.uma_banners.get().umas.count(), 2)
        support = timeline.support_banners.get()
        self.assertEqual(support.name, 'Launch Support')
        self.assertEqual(
            sorted(support.support_cards.values_list('name', flat=True)),
            ['Super Creek', 'Tazuna Hayakawa'])

    def test_launch_repair_completes_a_row_that_already_has_its_umas(self):
        # Production is in exactly this state: the uma half was applied before
        # the support half existed, so an early return would strand it.
        timeline = self._timeline('Special Week', (2021, 2, 24))
        uma = Uma.objects.create(name='Special Week')
        banner = BannerUma.objects.create(banner_timeline=timeline, name='Special Week')
        UmasOnUmaBanner.objects.create(banner_uma=banner, uma=uma)
        self._cards('Super Creek')
        self._write_csv([('2021-02-24', '-2', '', 'Super Creek')])

        call_command('repair_launch_banner', '--no-input')

        self.assertEqual(timeline.uma_banners.count(), 1)
        self.assertEqual(timeline.support_banners.get().support_cards.count(), 1)

    def test_launch_repair_links_what_it_can_when_a_card_is_missing(self):
        # Unlike a race-prep batch, the launch banner is a one-off: 19 of 20
        # cards is materially better than nothing, and the gap is reported.
        self._timeline('Special Week', (2021, 2, 24))
        Uma.objects.create(name='Special Week')
        self._cards('Super Creek')
        self._write_csv([('2021-02-24', '-2', '', 'Super Creek + Grass Wonder')])

        call_command('repair_launch_banner', '--no-input')

        support = BannerTimeline.objects.get().support_banners.get()
        self.assertEqual(
            list(support.support_cards.values_list('name', flat=True)), ['Super Creek'])

    def test_launch_repair_links_the_ssr_not_the_r(self):
        # The regression guard for what put 20 R cards into production. Both
        # rarities of the same name exist, exactly as they do in prod.
        self._timeline('Special Week', (2021, 2, 24))
        Uma.objects.create(name='Special Week')
        SupportCard.objects.create(name='Special Week', game_id=10001)
        wanted = SupportCard.objects.create(name='Special Week', game_id=30001)
        self._write_source({10001: '2021-02-24', 30001: '2021-02-24'})
        self._write_csv([('2021-02-24', '-2', '', 'Special Week')])

        call_command('repair_launch_banner', '--no-input')

        support = BannerTimeline.objects.get().support_banners.get()
        self.assertEqual(
            list(support.support_cards.values_list('pk', flat=True)), [wanted.pk])


class FixSupportCardVariantsTests(CalculatorTestCase):
    """
    The repair for banner links pointing at an R card instead of its SSR.

    These build the production shape deliberately: a banner whose linked card
    shares its name with a higher-rarity card released the same day.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.source_path = os.path.join(self.tmp, 'support_cards_source.json')
        patcher = patch.object(
            support_backfill, 'SUPPORT_CARDS_SOURCE', self.source_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._write_source({10001: '2021-02-24', 30001: '2021-02-24'})

    def _write_source(self, releases, name=None):
        with open(self.source_path, 'w', encoding='utf-8') as handle:
            json.dump(
                [{'id': game_id, 'release_ja': released, 'name_en': name}
                 for game_id, released in releases.items()],
                handle)
        support_backfill.clear_source_cache(self.source_path)

    def _banner(self, jp_start=(2021, 2, 24), name='Launch Support'):
        timeline = BannerTimeline.objects.create(
            name='Launch',
            jp_start_date=timezone.make_aware(datetime.datetime(*jp_start)),
            jp_end_date=timezone.make_aware(
                datetime.datetime(*jp_start) + datetime.timedelta(days=7)))
        return BannerSupport.objects.create(banner_timeline=timeline, name=name)

    def _link(self, banner, card):
        return SupportsOnSupportBanner.objects.create(
            banner_support=banner, support_card=card)

    def test_repoints_an_r_link_to_the_ssr(self):
        banner = self._banner()
        r_card = SupportCard.objects.create(name='Special Week', game_id=10001)
        ssr = SupportCard.objects.create(name='Special Week', game_id=30001)
        link = self._link(banner, r_card)

        call_command('fix_support_card_variants', '--no-input')

        link.refresh_from_db()
        self.assertEqual(link.support_card_id, ssr.pk)

    def test_leaves_ssr_links_alone(self):
        banner = self._banner()
        ssr = SupportCard.objects.create(name='Special Week', game_id=30001)
        link = self._link(banner, ssr)

        call_command('fix_support_card_variants', '--no-input')

        link.refresh_from_db()
        self.assertEqual(link.support_card_id, ssr.pk)

    def test_is_idempotent(self):
        # What makes it safe to leave wired up as a POST_DEPLOY job.
        banner = self._banner()
        r_card = SupportCard.objects.create(name='Special Week', game_id=10001)
        ssr = SupportCard.objects.create(name='Special Week', game_id=30001)
        self._link(banner, r_card)

        call_command('fix_support_card_variants', '--no-input')
        call_command('fix_support_card_variants', '--no-input')

        self.assertEqual(
            list(banner.support_cards.values_list('pk', flat=True)), [ssr.pk])

    def test_dry_run_writes_nothing(self):
        banner = self._banner()
        r_card = SupportCard.objects.create(name='Special Week', game_id=10001)
        SupportCard.objects.create(name='Special Week', game_id=30001)
        link = self._link(banner, r_card)

        call_command('fix_support_card_variants', '--dry-run')

        link.refresh_from_db()
        self.assertEqual(link.support_card_id, r_card.pk)

    def test_drops_the_r_link_when_the_banner_already_has_the_ssr(self):
        # Re-pointing would otherwise show the card twice on the tile; there is
        # no unique constraint on the join to stop it.
        banner = self._banner()
        r_card = SupportCard.objects.create(name='Special Week', game_id=10001)
        ssr = SupportCard.objects.create(name='Special Week', game_id=30001)
        self._link(banner, r_card)
        self._link(banner, ssr)

        call_command('fix_support_card_variants', '--no-input')

        self.assertEqual(
            list(banner.support_cards.values_list('pk', flat=True)), [ssr.pk])

    def test_leaves_a_link_alone_when_no_ssr_exists(self):
        # A LINKING job: it must not invent the missing card.
        banner = self._banner()
        r_card = SupportCard.objects.create(name='Orphan R', game_id=10099)
        link = self._link(banner, r_card)

        out = StringIO()
        call_command('fix_support_card_variants', '--no-input', stdout=out)

        link.refresh_from_db()
        self.assertEqual(link.support_card_id, r_card.pk)
        self.assertIn('Orphan R', out.getvalue())
        self.assertEqual(SupportCard.objects.filter(name='Orphan R').count(), 1)

    def test_leaves_a_link_alone_when_the_debut_ssr_is_missing(self):
        # The production risk this command must not take: quietly "repairing"
        # a 2026 banner onto a 2025 reprint because the real card was never
        # added. It reports instead, and the row stays as it was.
        banner = self._banner(jp_start=(2026, 3, 30), name='Daring Tact')
        r_card = SupportCard.objects.create(name='Daring Tact', game_id=10120)
        SupportCard.objects.create(name='Daring Tact', game_id=30248)
        self._write_source(
            {30248: '2025-04-10', 30293: '2026-03-30'}, name='Daring Tact')
        link = self._link(banner, r_card)

        out = StringIO()
        call_command('fix_support_card_variants', '--no-input', stdout=out)

        link.refresh_from_db()
        self.assertEqual(link.support_card_id, r_card.pk)
        # Names the card to add, rather than just declaring defeat.
        self.assertIn('30293', out.getvalue())

    def test_reports_a_link_whose_card_has_no_game_id(self):
        banner = self._banner()
        card = SupportCard.objects.create(name='Unknown Rarity', game_id=None)
        link = self._link(banner, card)

        out = StringIO()
        call_command('fix_support_card_variants', '--no-input', stdout=out)

        link.refresh_from_db()
        self.assertEqual(link.support_card_id, card.pk)
        self.assertIn('no game_id', out.getvalue())


class ImportGameDataSupportCardTests(CalculatorTestCase):
    """`import_game_data` filling the SupportCard columns. The Uma half is in test_umas."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.card = SupportCard.objects.create(name="Oguri Cap", game_id=30024)

    def _snapshot(self, **overrides):
        card = {
            "id": 30024, "chara_id": 1006, "chara_name": "Oguri Cap",
            "name": "[Get Lots of Hugs for Me] Oguri Cap", "title": "[Get Lots of Hugs for Me]",
            "rarity": 3, "card_type": "stamina", "skill_set_id": 9081006,
        }
        card.update(overrides)
        directory = Path(self.tmp.name)
        (directory / "skills.json").write_text("[]", encoding="utf-8")
        (directory / "cards.json").write_text("[]", encoding="utf-8")
        (directory / "support_cards.json").write_text(json.dumps([card]), encoding="utf-8")
        return directory

    def _run(self, directory, *flags):
        out = StringIO()
        call_command(
            "import_game_data", "--no-input", "--snapshot", str(directory), *flags, stdout=out,
        )
        return out.getvalue()

    def test_fills_type_character_and_title(self):
        self._run(self._snapshot())

        self.card.refresh_from_db()
        self.assertEqual(self.card.card_type, "stamina")
        self.assertEqual(self.card.character_id, 1006)
        self.assertEqual(self.card.title, "[Get Lots of Hugs for Me]")
        self.assertEqual(self.card.name, "Oguri Cap")

    def test_friend_and_group_types_round_trip(self):
        group = SupportCard.objects.create(name="Heirs to the Throne", game_id=30067)
        directory = self._snapshot(
            id=30067, chara_id=1017, chara_name="Heirs to the Throne",
            name="[Esteemed and Adored] Heirs to the Throne", card_type="group",
        )

        self._run(directory)

        group.refresh_from_db()
        self.assertEqual(group.card_type, "group")

    def test_name_mismatch_is_left_alone(self):
        self._run(self._snapshot(chara_name="Special Week"))

        self.card.refresh_from_db()
        self.assertEqual(self.card.card_type, "")
        self.assertIsNone(self.card.character_id)

    def test_dry_run_writes_nothing(self):
        out = self._run(self._snapshot(), "--dry-run")

        self.card.refresh_from_db()
        self.assertEqual(self.card.card_type, "")
        self.assertIn("would update 1", out)
