"""
A support banner's rate-up cards, split the way every reader needs them
(notes/banner-sr-r-plan.md).

TWO LISTS. A banner's `support_cards` in the API are the SSRs, the cards the
site has always drawn (on the Timeline, in a planner row's art, behind a
selector's "oldest featured card"). Its SR and R rate-ups travel beside them as
`other_rate_ups`, each with the `display` worked out here. So a reader that
draws, and every reader written later, sees only SSRs unless it asks for more;
the readers that want every card (the odds, a card's release date, search) ask
the client's `allRateUps()`.

THE ONE PLACE `display` IS RESOLVED. `SupportsOnSupportBanner.timeline_display`
is stored as the editor's choice, `auto` unless they changed it, and answered
here each time the payload is built, never written back. So an SSR an editor
adds to a banner later changes what its SRs do, with nothing to re-run.

AT MOST ONE SR PICTURE, AND ONLY BESIDE ONE SSR (owner, 2026-11-27: "If
there's 1 SSR and multiple SRs, then only one SR card should show. More than
two will typically break the CSS"). The support panel is laid out for two
tiles, so this is a cap, not a default: an editor's `picture` can't break it.

    an R                                     named   (an R is never drawn)
    an SR, the banner has two SSRs or more    named   ("treated as a R card and
                                                      listed below", owner 2026-11-27)
    on a banner with one SSR (or none):
      the SR an editor set to `picture`       picture (the first such one)
      else the banner's first `auto` SR       picture (Daptrius, 2026-11-20; the
                                                      first in the game's rate-up order)
      every other SR                          named

An editor's `named` and `hidden` always win. A `picture` that the cap leaves
out (a second one, or on a two-SSR banner, or on an R) reads as `named`.
`display` only changes the Timeline: the odds, the Encyclopedia and the PvP
tools use every card.

A card's rarity: the game's value, 1 R, 2 SR, 3 SSR. An unknown one (a card
with no game id) counts as an SSR, as the odds have always read it.
"""

from calculatorapi.models import SupportCardRarity, TimelineDisplay

# SRs before Rs in `other_rate_ups`: the row lists them in this order.
_ORDER = {SupportCardRarity.SR: 0, SupportCardRarity.R: 1}


def is_ssr(card):
    return card.rarity not in (SupportCardRarity.SR, SupportCardRarity.R)


def _pictured(others, ssr_count):
    """The one SR link drawn as a picture, or None. See the module docstring."""
    if ssr_count >= 2:
        return None
    srs = [link for link in others if link.support_card.rarity == SupportCardRarity.SR]
    for wanted in (TimelineDisplay.PICTURE, TimelineDisplay.AUTO):
        for link in srs:
            if link.timeline_display == wanted:
                return link
    return None


def split_rate_ups(links):
    """([SSR link], [(SR or R link, display)]) from a banner's links.

    `links` is the banner's prefetched `supportsonsupportbanner_set.all()`
    with `support_card` joined (a bare .all(), so the prefetch is used).
    """
    links = list(links)
    ssrs = [link for link in links if is_ssr(link.support_card)]
    others = sorted(
        (link for link in links if not is_ssr(link.support_card)),
        key=lambda link: _ORDER[link.support_card.rarity],  # stable: banner order within a rarity
    )
    pictured = _pictured(others, len(ssrs))

    def display(link):
        if link is pictured:
            return TimelineDisplay.PICTURE
        if link.timeline_display == TimelineDisplay.HIDDEN:
            return TimelineDisplay.HIDDEN
        return TimelineDisplay.NAMED

    return ssrs, [(link, display(link)) for link in others]
