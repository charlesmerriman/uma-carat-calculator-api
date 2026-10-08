"""
Which tables a content snapshot may carry, and where one may be loaded.

A content snapshot copies production's admin-authored content DOWN to a local
development database (`manage.py pull_prod_content`). Data only ever moves in
that direction: the old seeding script was deleted because `loaddata` upserts
by primary key, and one run against production would overwrite live admin
edits with a stale file. Two rules here keep that from coming back:

1. `CONTENT_MODELS` is an include list. A snapshot holds these tables and
   nothing else, so a new table holding accounts, tokens or anything about a
   person stays out of every snapshot until someone deliberately lists it.
   An exclude list would fail the other way: forget to update it and the new
   table leaks. `tests/test_content_snapshot.py` fails when a model is in
   neither list, so the decision cannot be skipped.
2. `require_local_database()` is called before anything is fetched or written.
   It refuses every database that is not SQLite, and production is PostgreSQL.
"""

from django.apps import apps
from django.core.management.base import CommandError
from django.db import connection

# Content: authored in the admin (or imported from game files) and the same
# for every visitor. Model class names, all in the `calculatorapi` app.
CONTENT_MODELS = (
    # Rank tables and constants
    "ClubRank",
    "TeamTrialsRank",
    "ChampionsMeetingRank",
    "LeagueOfHeroesRank",
    "CalculationConstants",
    # Characters, cards and skills
    "Uma",
    "SupportCard",
    "Skill",
    "UmaSkill",
    "SupportCardSkill",
    # The timeline and everything hanging off it
    "BannerTimeline",
    "BannerUma",
    "BannerSupport",
    "BannerStepUp",
    "UmasOnUmaBanner",
    "SupportsOnSupportBanner",
    "ChampionsMeeting",
    "ChampionsMeetingUmaRecommendation",
    "LeagueOfHeroes",
    "GameEvent",
    "Scenario",
    "DailyLegendRaceRelease",
    "DailyLegendRaceUma",
    "AnniversaryEvent",
    "AnniversaryEventBanner",
    "AnniversaryEventProduct",
    # Pages, FAQ and patch notes
    "SitePage",
    "FaqCategory",
    "FaqItem",
    "ChangelogEntry",
    "ChangelogChange",
    # Tier NAMES only. The supporters themselves are private, below.
    "PatreonTier",
)

# Private: about a person, an account, a credential or site traffic. Never
# leaves production. Listed so the test can tell "decided" from "forgotten".
PRIVATE_MODELS = (
    "CustomUser",
    "SocialAccount",
    "IncomeProfile",
    "Plan",
    "UserPlannedBanner",
    "UserPlannedPurchase",
    "UserStepUpSelection",
    "UserOshi",
    "PatreonSupporter",
    "PatreonCredentials",
    "DailyVisit",
    "MonthlyVisit",
    "VisitorHash",
)


def content_models():
    """The content model classes, in the order listed above."""
    return [apps.get_model("calculatorapi", name) for name in CONTENT_MODELS]


def content_labels():
    """`calculatorapi.Uma`-style labels, the form `dumpdata` takes."""
    return [f"calculatorapi.{name}" for name in CONTENT_MODELS]


def require_local_database():
    """Raise unless the default database is a local SQLite file.

    Checked on the live connection rather than on `DATABASE_URL`, so it holds
    however the settings came to point where they point.
    """
    if connection.vendor != "sqlite":
        raise CommandError(
            f"Refusing to run: the default database is {connection.vendor}, not SQLite. "
            "A content snapshot is only ever loaded into a local development database."
        )
