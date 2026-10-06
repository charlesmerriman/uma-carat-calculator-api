"""One-time backfill of the rate-up inputs 0077 added, for the banners that need them.

Runs once, on the deploy that ships the client's per-card rate-up rule. Without it
the 34 select banners would read as ten cards sharing 3% (0.3% each), worse than the
flat 0.75% they showed before, until someone got to the admin. The values come from
the global client's own gacha table (master.mdb `gacha_available`, read 2026-10-06):
a select banner's two picks are 0.75% each, and the two anime-collab doubles of
2025-07-16 gave each card 0.5%.

It only FILLS BLANKS, which is what keeps it inside the "nothing loads content into
prod" rule (see CLAUDE.md, "Safety & data"): a value an editor already set is never
overwritten, nothing is created or deleted, and a banner that does not match exactly
is left alone. On a database without these banners (local, tests) it does nothing.

queryset.update() fires no post_save, so it does not invalidate the public payload
cache. On a deploy that does not matter: the run command is `migrate && gunicorn`,
and the new process starts with an empty in-memory cache.
"""

import datetime
from decimal import Decimal

from django.db import migrations

SELECT_BANNER_SUFFIX = "10 Select 2"
SELECT_BANNER_PICKS = 2

COLLAB_RATE = Decimal("0.005")
COLLAB_UMA_BANNER = "Tokai Teio (Anime) + Mejiro Mcqueen (Anime)"
COLLAB_SUPPORT_BANNER = "Kitasan Black + Satono Diamond"
# Pinned to the day as well as the name: a rerun could reuse either name and would
# not share the collab's rate.
COLLAB_DAY_START = datetime.datetime(2025, 7, 16, tzinfo=datetime.timezone.utc)
COLLAB_DAY_END = COLLAB_DAY_START + datetime.timedelta(days=1)


def backfill_rate_up_inputs(apps, schema_editor):  # pylint: disable=unused-argument
    """Set rate_up_picks on select banners and the collab overrides, blanks only."""
    banner_support = apps.get_model("calculatorapi", "BannerSupport")
    umas_on_banner = apps.get_model("calculatorapi", "UmasOnUmaBanner")
    supports_on_banner = apps.get_model("calculatorapi", "SupportsOnSupportBanner")

    banner_support.objects.filter(
        name__endswith=SELECT_BANNER_SUFFIX, rate_up_picks__isnull=True,
    ).update(rate_up_picks=SELECT_BANNER_PICKS)

    umas_on_banner.objects.filter(
        banner_uma__name=COLLAB_UMA_BANNER,
        banner_uma__banner_timeline__global_start_date__gte=COLLAB_DAY_START,
        banner_uma__banner_timeline__global_start_date__lt=COLLAB_DAY_END,
        rate_override__isnull=True,
    ).update(rate_override=COLLAB_RATE)

    supports_on_banner.objects.filter(
        banner_support__name=COLLAB_SUPPORT_BANNER,
        banner_support__banner_timeline__global_start_date__gte=COLLAB_DAY_START,
        banner_support__banner_timeline__global_start_date__lt=COLLAB_DAY_END,
        rate_override__isnull=True,
    ).update(rate_override=COLLAB_RATE)


class Migration(migrations.Migration):

    dependencies = [
        ("calculatorapi", "0078_planned_banner_odds_cards"),
    ]

    operations = [
        # No reverse: rolling 0077 back drops the columns anyway, and undoing a
        # fill-blanks pass would also wipe any value an editor set afterwards.
        migrations.RunPython(backfill_rate_up_inputs, migrations.RunPython.noop),
    ]
