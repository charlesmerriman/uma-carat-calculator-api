"""
The picture Discord and X show when a Timeline banner's link is pasted:
GET /banner/<id>/preview.jpg (views/banner_share.py).

WHAT IT SHOWS (owner, 2026-11-25)
---------------------------------
A branded card in the family of the plan's and the guide's pictures: the
brand strip, "Timeline banner", who the banner features and its dates, over
the banner's own art blurred and shaded as the backdrop. Unlike those two,
the art is ALSO drawn sharp at the right: the art is the banner, and
Daptrius's request was to see it (board post 1574926713618432050). Banner art
is 16:9 (1024x576), so the box is 16:9 and nothing is cropped from it.

banner_share.py decides what goes on it, as a Card, from catalogue data only:
the same title and dates as the page's tags.

RENDERED ON REQUEST, NEVER STORED
---------------------------------
As the other pictures (plan_preview.py, guide_preview.py): drawn when a
crawler first asks, kept in the "gallery-previews" cache, never written to
storage (default storage is the production Spaces bucket even locally). The
URL carries Card.version, a hash of everything drawn plus LAYOUT, so a banner
whose names, dates or art change gets a new address and the old one can stay
`immutable`. Art that can't be read leaves the plain background and no box,
never an error.
"""

import hashlib
from dataclasses import dataclass

from django.core.cache import caches
from PIL import ImageDraw

from calculatorapi import share_card
from calculatorapi.share_card import HEIGHT, MARGIN, STRIP_HEIGHT, WIDTH

# Which drawing this is; part of Card.version. Raise it when the drawing changes.
LAYOUT = "1"
CACHE_SECONDS = 7 * 24 * 60 * 60

KICKER_SIZE = 28
HEADLINE_SIZE = 46
HEADLINE_LINES = 4
WHEN_SIZE = 30
NOTE_SIZE = 26
# The sharp art at the right, 16:9 like the banner art itself.
ART_WIDTH = 520
ART_HEIGHT = ART_WIDTH * 9 // 16
ART_GAP = 40
# The words' column: what the art leaves, or the whole width with no art.
TEXT_WIDTH_BESIDE_ART = WIDTH - MARGIN * 2 - ART_WIDTH - ART_GAP
TEXT_WIDTH_ALONE = WIDTH - MARGIN * 2
# The backdrop as the plan's and the guide's: blurred, then shaded nearly
# solid behind the words at the left. A little lighter at the right, where
# the sharp art covers most of it anyway.
ART_BLUR = 6
SHADE_COLOUR = (12, 14, 20)
SHADE_LEFT, SHADE_RIGHT = 242, 200


@dataclass(frozen=True)
class Card:
    """What a banner's picture says. Built by views/banner_share.card()."""

    headline: str  # "Taiki Shuttle (Camping), Yamanin Zephyr and TM Opera O"
    when: str = ""  # "2027/2/12 through 2027/2/22"
    estimated: bool = False  # dates predicted from the JP schedule
    art: object = None  # the BannerTimeline's image FieldFile, or None

    @property
    def version(self):
        art_name = getattr(self.art, "name", "") or ""
        drawn = repr((LAYOUT, self.headline, self.when, self.estimated, art_name))
        return hashlib.sha1(drawn.encode()).hexdigest()[:10]


def render(card):
    """JPEG bytes for a banner's card. Pure: no cache, no request."""
    backdrop = share_card.cover(card.art, ART_BLUR) if card.art is not None else None
    sharp = share_card.tile(card.art, ART_WIDTH, ART_HEIGHT) if backdrop is not None else None
    if backdrop is None:
        image = share_card.background()
    else:
        image = backdrop
        share_card.shade(image, SHADE_COLOUR, SHADE_LEFT, SHADE_RIGHT)
    share_card.brand_strip(image, right="Banner Timeline")
    draw = ImageDraw.Draw(image)

    room = HEIGHT - STRIP_HEIGHT
    if sharp is not None:
        # Centred in the room under the strip, against the right margin.
        image.paste(sharp, (WIDTH - MARGIN - ART_WIDTH, STRIP_HEIGHT + (room - ART_HEIGHT) // 2))
    text_width = TEXT_WIDTH_BESIDE_ART if sharp is not None else TEXT_WIDTH_ALONE

    headline = share_card.wrap(card.headline, HEADLINE_SIZE, text_width, HEADLINE_LINES, weight=700)
    when = share_card.wrap(card.when, WHEN_SIZE, text_width, 2) if card.when else []
    # The block's height, so it can sit centred in the room under the strip.
    height = KICKER_SIZE + 20 + len(headline) * round(HEADLINE_SIZE * 1.15)
    height += 24 + len(when) * round(WHEN_SIZE * 1.3) if when else 0
    height += 8 + NOTE_SIZE if when and card.estimated else 0
    y = STRIP_HEIGHT + (room - height) // 2

    share_card.draw_text(draw, (MARGIN, y), "Timeline banner", KICKER_SIZE, share_card.MUTED)
    y += KICKER_SIZE + 20
    for line in headline:
        share_card.draw_text(draw, (MARGIN, y), line, HEADLINE_SIZE, share_card.INK, weight=700)
        y += round(HEADLINE_SIZE * 1.15)
    if when:
        y += 24
        for line in when:
            share_card.draw_text(draw, (MARGIN, y), line, WHEN_SIZE, share_card.BRAND, weight=500)
            y += round(WHEN_SIZE * 1.3)
        if card.estimated:
            # The Timeline's "Estimated" badge, in words (frontend PredictedBadge.tsx).
            y += 8
            share_card.draw_text(draw, (MARGIN, y), "Estimated from the JP schedule", NOTE_SIZE, share_card.MUTED)
    return share_card.to_jpeg(image)


def preview_bytes(banner_id, card):
    """The banner's picture, from the cache or freshly drawn."""
    key = f"banner-preview:{banner_id}:{card.version}"
    previews = caches["gallery-previews"]
    image = previews.get(key)
    if image is None:
        image = render(card)
        previews.set(key, image, CACHE_SECONDS)
    return image
