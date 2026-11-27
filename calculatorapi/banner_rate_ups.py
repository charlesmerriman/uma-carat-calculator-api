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

THE ONE PLACE `auto` IS RESOLVED. `SupportsOnSupportBanner.timeline_display`
is stored as the editor's choice, `auto` unless they changed it, and answered
here each time the payload is built, never written back. So an SSR an editor
adds to a banner later changes what its SRs do, with nothing to re-run:

    an R                                    named   (an R is never drawn)
    the banner's only SR, beside one SSR     picture (Daptrius, 2026-11-20: "only 1
      (or none)                                      SSR rate up and only 1 SR rate up")
    an SR, the banner has two SSRs or more   named   ("treated as a R card and
                                                     listed below", owner 2026-11-27)
    an SR among several on the banner        named   (Narita Top Road, 1 SSR + 3 SRs:
                                                     three pictures would crowd one SSR)

An editor's `named`, `picture` or `hidden` wins, except `picture` on an R
(the admin refuses it; a stray one reads as `named`). `display` only changes
the Timeline: the odds, the Encyclopedia and the PvP tools use every card.

A card's rarity: the game's value, 1 R, 2 SR, 3 SSR. An unknown one (a card
with no game id) counts as an SSR, as the odds have always read it.
"""

from calculatorapi.models import SupportCardRarity, TimelineDisplay

# SRs before Rs in `other_rate_ups`: the row lists them in this order.
_ORDER = {SupportCardRarity.SR: 0, SupportCardRarity.R: 1}


def is_ssr(card):
    return card.rarity not in (SupportCardRarity.SR, SupportCardRarity.R)


def resolve_display(link, ssr_count, sr_count):
    """What an SR or R link does on the Timeline: named, picture or hidden."""
    chosen = link.timeline_display
    is_r = link.support_card.rarity == SupportCardRarity.R
    if chosen == TimelineDisplay.AUTO:
        if is_r or ssr_count >= 2 or sr_count != 1:
            return TimelineDisplay.NAMED
        return TimelineDisplay.PICTURE
    if chosen == TimelineDisplay.PICTURE and is_r:
        return TimelineDisplay.NAMED
    return chosen


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
    sr_count = sum(1 for link in others if link.support_card.rarity == SupportCardRarity.SR)
    return ssrs, [(link, resolve_display(link, len(ssrs), sr_count)) for link in others]
