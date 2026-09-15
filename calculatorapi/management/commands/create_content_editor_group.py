"""
Creates (or refreshes) the "Content editors" permission group.

Members of this group can add/change/delete/view all game content — banners,
umas, support cards, events, Champions Meetings, League of Heroes events, and
the rank/income tables — but see nothing else in the admin: user accounts,
saved plans, tokens, and auth models stay invisible (the admin hides models a
user has no permissions for).

Idempotent: safe to run repeatedly. `permissions.set(...)` replaces the
group's permission list wholesale, so rerunning after adding a model here
converges to exactly the intended set.

Usage:
    python manage.py create_content_editor_group

Run once locally and once in production (DigitalOcean API component's
Console tab) before assigning the group to the client's account.
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from calculatorapi.models import (
    Uma, SupportCard,
    BannerTimeline, BannerUma, BannerSupport, BannerStepUp,
    UmasOnUmaBanner, SupportsOnSupportBanner,
    GameEvent,
    ChampionsMeeting, ChampionsMeetingUmaRecommendation,
    LeagueOfHeroes,
    ChangelogEntry, ChangelogChange,
    ClubRank, TeamTrialsRank, ChampionsMeetingRank, LeagueOfHeroesRank,
    AnniversaryEvent, AnniversaryEventBanner, AnniversaryEventProduct,
    Scenario,
    PatreonTier, PatreonSupporter,
    SitePage, FaqCategory, FaqItem,
)

GROUP_NAME = "Content editors"

# Everything a content editor manages. The three join models are edited via
# inlines, but inline saves still require permissions on the join model itself.
CONTENT_MODELS = [
    Uma, SupportCard,
    BannerTimeline, BannerUma, BannerSupport, BannerStepUp,
    UmasOnUmaBanner, SupportsOnSupportBanner,
    GameEvent,
    ChampionsMeeting, ChampionsMeetingUmaRecommendation,
    LeagueOfHeroes,
    ChangelogEntry, ChangelogChange,
    ClubRank, TeamTrialsRank, ChampionsMeetingRank, LeagueOfHeroesRank,
    # Campaigns are content: an editor sets up an anniversary's packs and
    # selectors. UserPlannedPurchase is deliberately absent — it's user data.
    AnniversaryEvent, AnniversaryEventBanner, AnniversaryEventProduct,
    # A scenario is content too: an editor names it, points it at its launch
    # banner, and adds the art whenever the art exists.
    Scenario,
    # The Patreon thank-you list. Included because deciding who is thanked, and
    # by what name, is exactly the editorial judgement this group exists for —
    # and the sidebar links it ungated, like the Changelog. Note that `add`
    # permission also unlocks the CSV import page, which is intended: the
    # importer cannot publish a name, so the consent decision stays a separate
    # tick either way.
    PatreonTier, PatreonSupporter,
    # The About page, the carat income guide and the FAQ. Pages are change-only
    # in the admin whatever the permissions say (SitePageAdmin refuses add and
    # delete); FAQ questions and categories are fully editable.
    SitePage, FaqCategory, FaqItem,
]


class Command(BaseCommand):
    help = f'Create or refresh the "{GROUP_NAME}" group with content permissions.'

    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(name=GROUP_NAME)

        # Look permissions up through ContentType — permission primary keys
        # differ between databases, so they must never be hardcoded.
        content_types = ContentType.objects.get_for_models(*CONTENT_MODELS).values()
        permissions = Permission.objects.filter(content_type__in=content_types)

        group.permissions.set(permissions)

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f'{verb} group "{GROUP_NAME}" with {permissions.count()} permissions '
            f"across {len(CONTENT_MODELS)} models."
        ))
