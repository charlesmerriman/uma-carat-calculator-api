"""
The picture Discord and X show when a shared plan's link is pasted:
GET /plan/<public_id>/preview.jpg (views/plan_share.py).

WHAT IT SHOWS (owner, 2026-10-19, design polish item 26b)
---------------------------------------------------------
The brand strip, "Shared pull plan", the totals ("460 pulls on 4 banners") and
the first banner names, over the plan's first banner art, dimmed so the words
read. Before it the picture WAS that art: a game promo, Korean text and all,
that said nothing about the plan or the site.

It says exactly what the page's own tags already say, and plan_share.py
decides that, passing it in as a Card: never the notes, never the plan's name,
nothing about the owner. Discord and X keep their own copy of a picture after
the owner stops sharing, so the rule is the tags' rule (see plan_share.py).

RENDERED ON REQUEST, NEVER STORED
---------------------------------
As a post's picture (gallery_preview.py): drawn when a crawler first asks, kept
in the "gallery-previews" cache, never written to storage, whose default is
the production Spaces bucket even on a developer's machine. The URL carries
Card.version, a hash of everything drawn plus LAYOUT, so an edited plan gets a
new picture under a new address and the old one can stay `immutable`.

Art that cannot be read leaves the plain background rather than an error: a
crawler that gets a failure caches a card with no picture at all.
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

KICKER_SIZE = 30
HEADLINE_SIZE = 66
NAMES_SIZE = 34
TEXT_WIDTH = WIDTH - MARGIN * 2
# The art is a backdrop, not a second picture: blurred, then shaded (alpha
# nearly solid behind the words at the left, lighter at the right edge where
# the art shows). Banner art is a game promo with its own lettering, and at
# full strength that lettering fought the card's words.
ART_BLUR = 5
SHADE_COLOUR = (12, 14, 20)
SHADE_LEFT, SHADE_RIGHT = 242, 175


@dataclass(frozen=True)
class Card:
    """What a plan's picture says. Built by plan_share.card()."""

    headline: str
    names: str
    art: object = None  # the first banner's image FieldFile, or None

    @property
    def version(self):
        art_name = getattr(self.art, "name", "") or ""
        drawn = "|".join((LAYOUT, self.headline, self.names, art_name))
        return hashlib.sha1(drawn.encode()).hexdigest()[:10]


def render(card):
    """JPEG bytes for a plan's card. Pure: no cache, no request."""
    art = share_card.cover(card.art, ART_BLUR) if card.art is not None else None
    if art is None:
        image = share_card.background()
    else:
        image = art
        share_card.shade(image, SHADE_COLOUR, SHADE_LEFT, SHADE_RIGHT)
    share_card.brand_strip(image)
    draw = ImageDraw.Draw(image)

    headline = share_card.wrap(card.headline, HEADLINE_SIZE, TEXT_WIDTH, 2, weight=700)
    names = share_card.wrap(card.names, NAMES_SIZE, TEXT_WIDTH, 2) if card.names else []
    # The block's height, so it can sit centred in the room under the strip.
    height = (
        KICKER_SIZE + 22
        + len(headline) * round(HEADLINE_SIZE * 1.12)
        + (18 + len(names) * round(NAMES_SIZE * 1.3) if names else 0)
    )
    y = STRIP_HEIGHT + (HEIGHT - STRIP_HEIGHT - height) // 2
    share_card.draw_text(draw, (MARGIN, y), "Shared pull plan", KICKER_SIZE, share_card.MUTED)
    y += KICKER_SIZE + 22
    for line in headline:
        share_card.draw_text(draw, (MARGIN, y), line, HEADLINE_SIZE, share_card.INK, weight=700)
        y += round(HEADLINE_SIZE * 1.12)
    if names:
        y += 18
        for line in names:
            share_card.draw_text(draw, (MARGIN, y), line, NAMES_SIZE, share_card.BRAND)
            y += round(NAMES_SIZE * 1.3)
    return share_card.to_jpeg(image)


def preview_bytes(public_id, card):
    """The plan's picture, from the cache or freshly drawn."""
    key = f"plan-preview:{public_id}:{card.version}"
    previews = caches["gallery-previews"]
    image = previews.get(key)
    if image is None:
        image = render(card)
        previews.set(key, image, CACHE_SECONDS)
    return image
