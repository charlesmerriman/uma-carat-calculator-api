"""
"Is this uma outfit or support card out on Global?", the one rule.

Owner, 2026-11-14: "on global" means RELEASED on global, by whatever brings
it into the game, never just "the global client holds it". The client ships
outfits and cards ahead of their release, and until this module the tag read
the client alone, so it ran ahead of the game when the snapshot was fresh and
behind it when the snapshot was stale (Daptrius, 2026-11-13: Daitaku Helios
and K.S.Miracle read "JP only" ten days after their Nov 4 banner).

An outfit or card is out on Global once EITHER has happened:

1. A CONFIRMED global banner for it has started: its BannerTimeline's
   `global_start_date` has passed. A predicted date never counts (a null
   `global_start_date` is a prediction, predictions.py): a guess is not a
   release. This half needs no snapshot refresh, so the tag turns over on
   release day as soon as an editor has entered the banner.
2. The game's own release date has passed: `global_release_at`, which
   import_game_data writes from the global client (a support card's own
   `start_date`; an outfit's first gacha or main story reward). This is what
   releases everything on no banner: the launch roster, event-reward cards,
   Rs and SRs, and story-reward outfits like [Ruler of Japan] Special Week.

Both are compared with NOW, when the request is served, never stored as a
boolean: a stored flag could only change at a deploy. The pages that read it
are cached for at most the caches' TTLs (5 minutes for the skill pages and the
PvP catalogue, 30 for a guide's public payload), which is the most the tag can
lag a release.

Readers: a queryset of rows takes `annotate_umas()` / `annotate_cards()`
(each row gets `.on_global`); a payload over many rows reached through other
tables builds `released_umas()` / `released_cards()` once and tests
membership or filters with `__in`; one row asks `uma_is_released()` /
`card_is_released()`. Never compare `global_release_at` or banner dates by
hand somewhere else. `game_release_passed()` is only for showing the game's
date itself, never for the tag.
"""

from django.db.models import BooleanField, ExpressionWrapper, Q
from django.utils import timezone

from calculatorapi.models import SupportCard, SupportsOnSupportBanner, Uma, UmasOnUmaBanner


def released_uma_q(now=None):
    """The Q over Uma for outfits out on Global by `now` (default: this instant)."""
    now = now or timezone.now()
    on_started_banner = UmasOnUmaBanner.objects.filter(
        banner_uma__banner_timeline__global_start_date__lte=now,
    ).values("uma_id")
    return Q(global_release_at__lte=now) | Q(pk__in=on_started_banner)


def released_card_q(now=None):
    """The Q over SupportCard for cards out on Global by `now` (default: this instant)."""
    now = now or timezone.now()
    on_started_banner = SupportsOnSupportBanner.objects.filter(
        banner_support__banner_timeline__global_start_date__lte=now,
    ).values("support_card_id")
    return Q(global_release_at__lte=now) | Q(pk__in=on_started_banner)


def released_umas(now=None):
    """The ids of every outfit out on Global by `now`, as a set. One query."""
    return set(Uma.objects.filter(released_uma_q(now)).values_list("id", flat=True))


def released_cards(now=None):
    """The ids of every support card out on Global by `now`, as a set. One query."""
    return set(SupportCard.objects.filter(released_card_q(now)).values_list("id", flat=True))


def annotate_umas(queryset, now=None):
    """`queryset` (of Uma) with `.on_global` on every row, in the same query."""
    return queryset.annotate(on_global=ExpressionWrapper(released_uma_q(now), output_field=BooleanField()))


def annotate_cards(queryset, now=None):
    """`queryset` (of SupportCard) with `.on_global` on every row, in the same query."""
    return queryset.annotate(on_global=ExpressionWrapper(released_card_q(now), output_field=BooleanField()))


def uma_is_released(uma, now=None):
    """Is this one outfit out on Global by `now`? One query."""
    return Uma.objects.filter(released_uma_q(now), pk=uma.pk).exists()


def card_is_released(card, now=None):
    """Is this one support card out on Global by `now`? One query."""
    return SupportCard.objects.filter(released_card_q(now), pk=card.pk).exists()


def game_release_passed(released_at, now=None):
    """
    The game's own date, for SHOWING it (a card page's Global date, the
    Collection's sort): the date when it has passed, else None. A date still
    ahead is the client's, and may be its 2099 "not released here" mark, so it
    is never shown. Not the tag: that is the rule above, banners included.
    """
    if released_at is None or released_at > (now or timezone.now()):
        return None
    return released_at
