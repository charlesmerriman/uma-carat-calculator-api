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

A COMMUNITY PLAN POST (views/gallery_share.py) uses the same card with
`tiles`: its first banners' art in a row under the words (Henry, 2026-11-20:
"a preview image showcasing the first 5 banners"), on the plain background,
and "Community plan" over the headline. Still only the counts and catalogue
names: never the post's title, its words, or its author.
"""

import hashlib
from dataclasses import dataclass

from django.core.cache import caches
from PIL import ImageDraw

from calculatorapi import share_card
from calculatorapi.share_card import HEIGHT, MARGIN, STRIP_HEIGHT, WIDTH

# Which drawing this is; part of Card.version. Raise it when the drawing changes.
LAYOUT = "1"
# A post's banner row: up to TILES_SHOWN pictures, 2:1 like banner art.
TILES_SHOWN = 5
TILE_GAP = 16
TILE_WIDTH = (WIDTH - MARGIN * 2 - TILE_GAP * (TILES_SHOWN - 1)) // TILES_SHOWN
TILE_HEIGHT = TILE_WIDTH // 2
TILE_GAP_ABOVE = 34
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


# The words a plan's link and picture share, used by views/plan_share.py (a
# shared plan) and views/gallery_share.py (a Community plans post). Here,
# below both, so neither has to import the other.
# How many banner names the description lists before "and N more".
NAMES_SHOWN = 3


def _plural(count, one, many):
    return f"{count:,} {one if count == 1 else many}"


def plan_counts(rows, pulls, steps):
    """'230 pulls on 4 banners', with the steps when there are any. A step is
    not converted to pulls (plan_totals says why)."""
    counts = [_plural(pulls, "pull", "pulls")] if pulls or not steps else []
    if steps:
        counts.append(_plural(steps, "step-up step", "step-up steps"))
    return f"{' and '.join(counts)} on {_plural(len(rows), 'banner', 'banners')}"


def banner_list(rows):
    """'T.M. Opera, Oguri Cap (support), Mihono Bourbon and 2 more' (no full
    stop: the picture prints it as a line). Support banners are named after
    their card, which is often an uma's name too."""
    names = []
    for row in rows:
        banner = row.banner_target
        if banner is None:
            continue
        names.append(f"{banner.name} (support)" if row.banner_support_id else banner.name)
    if not names:
        return ""
    shown, rest = names[:NAMES_SHOWN], len(names) - NAMES_SHOWN
    if rest > 0:
        return f"{', '.join(shown)} and {rest} more"
    if len(shown) == 1:
        return shown[0]
    return f"{', '.join(shown[:-1])} and {shown[-1]}"


@dataclass(frozen=True)
class Card:
    """What a plan's picture says. Built by plan_share.card()."""

    headline: str
    names: str
    art: object = None  # the first banner's image FieldFile, or None
    kicker: str = "Shared pull plan"
    # A Community plan post's first banners' images (FieldFiles), drawn in a
    # row; empty for a shared plan's link, which keeps its backdrop instead.
    tiles: tuple = ()

    @property
    def version(self):
        art_name = getattr(self.art, "name", "") or ""
        tile_names = ",".join(getattr(tile, "name", "") or "" for tile in self.tiles)
        drawn = "|".join((LAYOUT, self.kicker, self.headline, self.names, art_name, tile_names))
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
    # With a banner row there is room for one line of names, not two.
    names = share_card.wrap(card.names, NAMES_SIZE, TEXT_WIDTH, 1 if card.tiles else 2) if card.names else []
    tiles = [picture for picture in (share_card.tile(art, TILE_WIDTH, TILE_HEIGHT)
                                     for art in card.tiles[:TILES_SHOWN]) if picture is not None]
    # The block's height, so it can sit centred in the room under the strip.
    height = (
        KICKER_SIZE + 22
        + len(headline) * round(HEADLINE_SIZE * 1.12)
        + (18 + len(names) * round(NAMES_SIZE * 1.3) if names else 0)
        + (TILE_GAP_ABOVE + TILE_HEIGHT if tiles else 0)
    )
    y = STRIP_HEIGHT + (HEIGHT - STRIP_HEIGHT - height) // 2
    share_card.draw_text(draw, (MARGIN, y), card.kicker, KICKER_SIZE, share_card.MUTED)
    y += KICKER_SIZE + 22
    for line in headline:
        share_card.draw_text(draw, (MARGIN, y), line, HEADLINE_SIZE, share_card.INK, weight=700)
        y += round(HEADLINE_SIZE * 1.12)
    if names:
        y += 18
        for line in names:
            share_card.draw_text(draw, (MARGIN, y), line, NAMES_SIZE, share_card.BRAND)
            y += round(NAMES_SIZE * 1.3)
    if tiles:
        y += TILE_GAP_ABOVE
        for index, picture in enumerate(tiles):
            image.paste(picture, (MARGIN + index * (TILE_WIDTH + TILE_GAP), y))
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
