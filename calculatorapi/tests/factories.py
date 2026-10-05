"""Builders shared across test modules: model rows, UTC datetimes, a signed-in client, a fake response."""

# Builders and request helpers take one parameter per field a test can vary.
# pylint: disable=too-many-arguments,too-many-positional-arguments

import datetime

from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from calculatorapi.predictions import PREDICTION_FACTOR
from calculatorapi.models import (
    CustomUser,
    ClubRank, TeamTrialsRank, ChampionsMeetingRank, LeagueOfHeroesRank,
    BannerTimeline, BannerUma, BannerSupport, BannerStepUp,
    ChampionsMeeting, LeagueOfHeroes, GameEvent, Scenario,
    DailyLegendRaceRelease, DailyLegendRaceUma,
    AnniversaryEvent, AnniversaryEventBanner, AnniversaryEventProduct,
)


def make_ranks():
    """Create one of each rank type with zero income — minimal valid FKs for CustomUser."""
    club = ClubRank.objects.create(name='None', income_amount=0)
    tt = TeamTrialsRank.objects.create(name='None', income_amount=0)
    cm = ChampionsMeetingRank.objects.create(name='None', income_amount=0)
    loh = LeagueOfHeroesRank.objects.create(name='None', income_amount=0)
    return club, tt, cm, loh


def make_user(username='testuser', password='testpass123', is_staff=False):
    """Create a CustomUser with all required FK ranks set.

    `is_staff` matters for /login, which is staff-only now that ordinary
    accounts sign in through Google/Discord.
    """
    club, tt, cm, loh = make_ranks()
    return CustomUser.objects.create_user(
        username=username,
        password=password,
        email=f'{username}@test.com',
        first_name='Test',
        last_name='User',
        is_staff=is_staff,
        club_rank=club,
        team_trials_rank=tt,
        champions_meeting_rank=cm,
        league_of_heroes_rank=loh,
    )


def make_timeline(name='Test Timeline', jp_start_date=None, jp_end_date=None,
                  global_start_date=None, global_end_date=None,
                  schedule_offset_days=0):
    """Create a BannerTimeline. By default it's a CONFIRMED global banner
    (now → now+30d) so existing tests keep resolving real dates; pass jp_*/
    global_* explicitly to build predicted (global-null) timelines."""
    now = timezone.now()
    if global_start_date is None and global_end_date is None and \
            jp_start_date is None and jp_end_date is None:
        global_start_date = now
        global_end_date = now + datetime.timedelta(days=30)
    return BannerTimeline.objects.create(
        name=name,
        jp_start_date=jp_start_date,
        jp_end_date=jp_end_date,
        global_start_date=global_start_date,
        global_end_date=global_end_date,
        schedule_offset_days=schedule_offset_days,
    )


def make_uma_banner(timeline=None, name='Test Uma Banner'):
    return BannerUma.objects.create(
        banner_timeline=timeline or make_timeline(),
        name=name,
    )


def make_support_banner(timeline=None, name='Test Support Banner'):
    return BannerSupport.objects.create(
        banner_timeline=timeline or make_timeline(),
        name=name,
    )


def make_step_up_banner(event=None, timeline=None, name='Test Step-Up',
                        card_type='support', banner_count=3, order=0):
    """Create a BannerStepUp, wiring a campaign and a Part if none is given.

    The two FKs must agree (the timeline has to be one of the campaign's parts),
    so building one implies building the other -- which is exactly the coupling
    BannerStepUp.clean() enforces.
    """
    if timeline is None:
        timeline = make_timeline(name=f'{name} Window')
    if event is None:
        event = make_anniversary_event(name=f'{name} Campaign', parts=(timeline,))
    return BannerStepUp.objects.create(
        anniversary_event=event, banner_timeline=timeline, name=name,
        card_type=card_type, banner_count=banner_count, order=order,
    )


def make_champions_meeting(name='Test CM', cm_number=1, jp_start_date=None,
                           jp_end_date=None, global_start_date=None,
                           global_end_date=None, schedule_offset_days=0):
    """Create a ChampionsMeeting. Defaults to a CONFIRMED global meeting
    (now → now+7d); pass jp_*/global_* explicitly for predicted rows. Track and
    stat fields are filler — they don't affect date resolution."""
    now = timezone.now()
    if global_start_date is None and global_end_date is None and \
            jp_start_date is None and jp_end_date is None:
        global_start_date = now
        global_end_date = now + datetime.timedelta(days=7)
    return ChampionsMeeting.objects.create(
        name=name, cm_number=cm_number,
        jp_start_date=jp_start_date, jp_end_date=jp_end_date,
        global_start_date=global_start_date, global_end_date=global_end_date,
        schedule_offset_days=schedule_offset_days,
        track='Tokyo', surface_type='Turf', distance='Long', length='2400m',
        track_condition='Good', season='Spring', weather='Sunny', direction='Right',
        speed_recommendation=0, stamina_recommendation=0, power_recommendation=0,
        guts_recommendation=0, wit_recommendation=0,
    )


def make_league_of_heroes(name='Test LoH', jp_start_date=None, jp_end_date=None,
                          global_start_date=None, global_end_date=None,
                          schedule_offset_days=0, loh_number=0):
    """Create a LeagueOfHeroes event. Defaults to a CONFIRMED global event
    (now → now+7d); pass jp_*/global_* explicitly for predicted rows."""
    now = timezone.now()
    if global_start_date is None and global_end_date is None and \
            jp_start_date is None and jp_end_date is None:
        global_start_date = now
        global_end_date = now + datetime.timedelta(days=7)
    return LeagueOfHeroes.objects.create(
        name=name, loh_number=loh_number,
        jp_start_date=jp_start_date, jp_end_date=jp_end_date,
        global_start_date=global_start_date, global_end_date=global_end_date,
        schedule_offset_days=schedule_offset_days,
    )


def make_game_event(name='Test Event', banner_timeline=None, **reward_fields):
    """Create a GameEvent, optionally linked to a BannerTimeline. Dates are
    always derived from banner_timeline (or null when unlinked) — GameEvent
    has no date fields of its own. Reward amounts (carat_amount,
    carats_throughout, etc.) can be passed as kwargs; they default to 0."""
    return GameEvent.objects.create(name=name, banner_timeline=banner_timeline, **reward_fields)


def make_scenario(name='Test Scenario', banner_timeline=None, image=None):
    """Create a Scenario, optionally linked to its launch BannerTimeline.

    A scenario's start comes from that banner and it has NO end date at all --
    it stays playable after release, so there is nothing for an end to mean.
    `image` is routinely None: scenarios get entered before their art exists.
    """
    return Scenario.objects.create(
        name=name, banner_timeline=banner_timeline, image=image,
    )


def make_daily_legend_race(name='Test Release', banner_timeline=None,
                           offset_days=0, umas=()):
    """Create a DailyLegendRaceRelease and link `umas` to it.

    Start-only like a scenario: the date is the banner's start plus
    `offset_days`, and there is no end. Unlinked (banner_timeline=None) is the
    normal state for a batch entered before the timeline reaches it.
    """
    release = DailyLegendRaceRelease.objects.create(
        name=name, banner_timeline=banner_timeline, offset_days=offset_days,
    )
    for uma in umas:
        DailyLegendRaceUma.objects.create(release=release, uma=uma)
    return release


def make_anniversary_event(name='Test Anniversary', event_type='anniversary',
                           jp_cutoff_date=None, parts=(), products=()):
    """Create an AnniversaryEvent, its banner-part links and its products.

    `parts` is an iterable of BannerTimeline (linked as Part 1, 2, ... in order).
    `products` is an iterable of kwargs dicts for AnniversaryEventProduct.
    Dates come entirely from the linked parts -- the event has none of its own.
    """
    event = AnniversaryEvent.objects.create(
        name=name, event_type=event_type, jp_cutoff_date=jp_cutoff_date,
    )
    for index, timeline in enumerate(parts, start=1):
        AnniversaryEventBanner.objects.create(
            anniversary_event=event, banner_timeline=timeline, part_number=index,
        )
    for product_kwargs in products:
        AnniversaryEventProduct.objects.create(anniversary_event=event, **product_kwargs)
    return event


def auth_client(user):
    """Return an APIClient already authenticated as `user`."""
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client, token


_UTC = datetime.timezone.utc


def _dt(y, m, d, hour=0, minute=0, second=0):
    """A UTC datetime. Time-of-day defaults to midnight — most fixtures only
    care about the calendar day — but is available for the cases that turn on
    it, e.g. a confirmed global window running 22:00 -> 21:59:59."""
    return datetime.datetime(y, m, d, hour, minute, second, tzinfo=_UTC)


def _predicted(anchor_global_start, jp_gap_days, offset_days=0):
    """Predicted global start for a row whose JP start is `jp_gap_days` after
    the anchor's, plus any schedule offset already applied.

    Derived from PREDICTION_FACTOR rather than hardcoded, so retuning the factor
    doesn't mean rewriting every expectation in the suite. The one deliberate
    exception is test_fixed_anchor_worked_example, which pins concrete numbers
    on purpose — that's what makes it a worked example."""
    return (anchor_global_start
            + datetime.timedelta(days=jp_gap_days) * PREDICTION_FACTOR
            + datetime.timedelta(days=offset_days))


def _iso(value):
    """Format a datetime exactly as DRF's DateTimeField renders it on the wire."""
    text = value.isoformat()
    return text[:-6] + 'Z' if text.endswith('+00:00') else text


class FakeResponse:  # pylint: disable=too-few-public-methods
    """Stands in for a requests.Response in the client tests."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload
