"""
The picture Discord and X show when a guide's link is pasted:
GET /guide/<kind>-<id>/preview.jpg (views/guide_share.py).

WHAT IT SHOWS (owner, 2026-10-24, guide-visuals-plan-2.md release D)
--------------------------------------------------------------------
The event's own art as the backdrop, blurred and shaded, the brand strip with
"CM 21" at its end, then "PvP guide", the cup's name and the course. An open
guide adds its style ranking (each style in its colour) and its stat targets;
a locked one (pvp_gate.py), a draft or an event with no guide adds the dates
instead, so the picture never shows what the gate holds back.

guide_share.py decides what goes on it, as a Card: the guide's own facts,
never its prose, and nothing about who wrote it.

RENDERED ON REQUEST, NEVER STORED
---------------------------------
As the other pictures (plan_preview.py, gallery_preview.py): drawn when a
crawler first asks, kept in the "gallery-previews" cache, never written to
storage. The URL carries Card.version, a hash of everything drawn plus LAYOUT,
so an edited guide gets a new address and the old one can stay `immutable`.

THE RANKING'S MARKS
-------------------
The site draws "Pace ≫ End"; the picture's fonts have no ≫ or ≥ (Outfit is a
Latin cut, and the DejaVu cut has no maths signs), so the picture says
"Pace » End › Late": » for a clear gap, › for ahead, = for level.
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
COURSE_SIZE = 32
RANKING_SIZE = 46
STAT_LABEL_SIZE = 28
STAT_VALUE_SIZE = 36
WHEN_SIZE = 32
TEXT_WIDTH = WIDTH - MARGIN * 2
# The backdrop as a shared plan's (plan_preview.py): blurred, then shaded
# nearly solid behind the words at the left.
ART_BLUR = 5
SHADE_COLOUR = (12, 14, 20)
SHADE_LEFT, SHADE_RIGHT = 242, 175

# Hakuraku's running-style colours, the dark-theme values the site uses
# (frontend/src/index.css, --color-style-*).
STYLE_COLOURS = {
    "runaway": (178, 102, 255),
    "front": (94, 152, 231),
    "pace": (164, 219, 34),
    "late": (253, 222, 52),
    "end": (255, 178, 49),
}
# The guide's marks as the picture draws them (see the module docstring).
PICTURE_MARKS = {">>": "»", ">": "›", ">=": "=", "=": "="}


@dataclass(frozen=True)
class Card:
    """What a guide's picture says. Built by views/guide_share.card()."""

    strip: str  # "CM 21"
    headline: str  # "Capricorn Cup"
    course: str  # "Chukyo 1200m · Sprint · Turf · Left"
    # ((mark before it or "", word, style or None), ...): empty for a locked guide.
    ranking: tuple = ()
    # (("Speed", "1600"), ...): empty for a locked guide.
    stats: tuple = ()
    when: str = ""  # "2026/10/31 through 2026/11/6", on a locked guide's card
    art: object = None  # the event's image FieldFile, or None

    @property
    def version(self):
        art_name = getattr(self.art, "name", "") or ""
        drawn = repr((LAYOUT, self.strip, self.headline, self.course, self.ranking, self.stats, self.when, art_name))
        return hashlib.sha1(drawn.encode()).hexdigest()[:10]


def _ranking_width(ranking, size):
    width = 0
    for mark, word, _ in ranking:
        if mark:
            width += share_card.text_width(f" {PICTURE_MARKS.get(mark, mark)} ", size)
        width += share_card.text_width(word, size, weight=700)
    return width


def _draw_ranking(draw, y, ranking):
    """The ranking on one line, each style in its colour, shrunk to fit."""
    size = RANKING_SIZE
    while size > 30 and _ranking_width(ranking, size) > TEXT_WIDTH:
        size -= 2
    x = MARGIN
    for mark, word, style in ranking:
        if mark:
            x += share_card.draw_text(draw, (x, y), f" {PICTURE_MARKS.get(mark, mark)} ", size, share_card.MUTED)
        x += share_card.draw_text(draw, (x, y), word, size, STYLE_COLOURS.get(style, share_card.INK), weight=700)
    return size


def _stats_width(stats, scale):
    gap = round(34 * scale)
    return sum(
        share_card.text_width(f"{label} ", round(STAT_LABEL_SIZE * scale), weight=500)
        + share_card.text_width(value, round(STAT_VALUE_SIZE * scale), weight=700)
        for label, value in stats
    ) + gap * (len(stats) - 1)


def _draw_stats(draw, y, stats):
    """"Speed 1600   Stamina 350 …" on one line, shrunk to fit."""
    scale = 1.0
    while scale > 0.6 and _stats_width(stats, scale) > TEXT_WIDTH:
        scale -= 0.05
    label_size, value_size, gap = round(STAT_LABEL_SIZE * scale), round(STAT_VALUE_SIZE * scale), round(34 * scale)
    x = MARGIN
    for label, value in stats:
        # Labels sit on the values' baseline (their tops a little lower).
        label_y = y + (value_size - label_size) - 2
        x += share_card.draw_text(draw, (x, label_y), f"{label} ", label_size, share_card.MUTED, weight=500)
        x += share_card.draw_text(draw, (x, y), value, value_size, share_card.INK, weight=700) + gap
    return value_size


def render(card):
    """JPEG bytes for a guide's card. Pure: no cache, no request."""
    art = share_card.cover(card.art, ART_BLUR) if card.art is not None else None
    if art is None:
        image = share_card.background()
    else:
        image = art
        share_card.shade(image, SHADE_COLOUR, SHADE_LEFT, SHADE_RIGHT)
    share_card.brand_strip(image, right=card.strip)
    draw = ImageDraw.Draw(image)

    headline = share_card.fit(card.headline, HEADLINE_SIZE, TEXT_WIDTH, weight=700)
    course = share_card.fit(card.course, COURSE_SIZE, TEXT_WIDTH) if card.course else ""
    # The block's height, so it can sit centred in the room under the strip.
    height = KICKER_SIZE + 22 + round(HEADLINE_SIZE * 1.12)
    height += 10 + COURSE_SIZE if course else 0
    height += 40 + RANKING_SIZE if card.ranking else 0
    height += 30 + STAT_VALUE_SIZE if card.stats else 0
    height += 30 + WHEN_SIZE if card.when else 0
    y = STRIP_HEIGHT + (HEIGHT - STRIP_HEIGHT - height) // 2

    share_card.draw_text(draw, (MARGIN, y), "PvP guide", KICKER_SIZE, share_card.MUTED)
    y += KICKER_SIZE + 22
    share_card.draw_text(draw, (MARGIN, y), headline, HEADLINE_SIZE, share_card.INK, weight=700)
    y += round(HEADLINE_SIZE * 1.12)
    if course:
        y += 10
        share_card.draw_text(draw, (MARGIN, y), course, COURSE_SIZE, share_card.MUTED, weight=500)
        y += COURSE_SIZE
    if card.ranking:
        y += 40
        y += _draw_ranking(draw, y, card.ranking)
    if card.stats:
        y += 30
        y += _draw_stats(draw, y, card.stats)
    if card.when:
        y += 30
        when = share_card.fit(card.when, WHEN_SIZE, TEXT_WIDTH)
        share_card.draw_text(draw, (MARGIN, y), when, WHEN_SIZE, share_card.BRAND)
    return share_card.to_jpeg(image)


def preview_bytes(ref, card):
    """The guide's picture, from the cache or freshly drawn."""
    key = f"guide-preview:{ref}:{card.version}"
    previews = caches["gallery-previews"]
    image = previews.get(key)
    if image is None:
        image = render(card)
        previews.set(key, image, CACHE_SECONDS)
    return image
