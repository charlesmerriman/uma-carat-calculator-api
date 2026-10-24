"""
The drawing kit for link-preview pictures: the large-card size Discord and X
use, the site's dark background, the brand strip along the top, and text in
the site's own face. Used by gallery_preview.py (a gallery post's team) and
plan_preview.py (a shared plan).

THE BRAND STRIP (owner, 2026-10-19, design polish item 26b)
-----------------------------------------------------------
Every picture opens with the site's name, "Uma" in the brand gold and the rest
in white as the wordmark is drawn on the site, and optionally a short line at
the other end (the event's name on a post). Before it, a post's picture was
three umas on navy with nothing to say where it came from.

FONTS (calculatorapi/fonts/README.md)
-------------------------------------
Outfit, the wordmark's face, for everything it covers, and a small cut of
DejaVu Sans Bold for the few characters catalogue names use that it does not
(★ in the ★3 step-ups, ♡ in some support banners). Which font draws a
character is decided from Outfit's published ranges, OUTFIT_RANGES: Pillow
has no per-glyph fallback, and a missing glyph draws a blank box.
"""

import io
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# The large-card size both Discord and X use (1.91:1).
WIDTH, HEIGHT = 1200, 630
JPEG_QUALITY = 85

# The site's dark surfaces (gray-800 to gray-900), top to bottom.
BACKGROUND_TOP = (31, 41, 55)
BACKGROUND_BOTTOM = (17, 24, 39)
BRAND = (230, 210, 138)  # --color-brand, #E6D28A
INK = (243, 244, 246)
MUTED = (163, 170, 184)

# The strip: a darker band the full width of the card.
STRIP_HEIGHT = 84
STRIP_FILL = (10, 12, 18, 200)
MARGIN = 56
WORDMARK_SIZE = 34

FONTS = Path(__file__).resolve().parent / "fonts"
OUTFIT = FONTS / "Outfit-latin-wght.woff2"
FALLBACK = FONTS / "DejaVuSans-Bold-symbols.ttf"

# The code points Outfit's Latin file covers, from the fontsource package's
# unicode.json ("latin"). Anything else is drawn with FALLBACK.
OUTFIT_RANGES = (
    (0x0000, 0x00FF), (0x0131, 0x0131), (0x0152, 0x0153), (0x02BB, 0x02BC),
    (0x02C6, 0x02C6), (0x02DA, 0x02DA), (0x02DC, 0x02DC), (0x0304, 0x0304),
    (0x0308, 0x0308), (0x0329, 0x0329), (0x2000, 0x206F), (0x20AC, 0x20AC),
    (0x2122, 0x2122), (0x2191, 0x2191), (0x2193, 0x2193), (0x2212, 0x2212),
    (0x2215, 0x2215), (0xFEFF, 0xFEFF), (0xFFFD, 0xFFFD),
)


def _in_outfit(char):
    point = ord(char)
    return any(low <= point <= high for low, high in OUTFIT_RANGES)


@lru_cache(maxsize=32)
def _font(size, weight, fallback):
    """A font at a size, cached: loading a font file is the slow part. Outfit
    is a variable font, so the weight is an axis setting on the one file."""
    if fallback:
        return ImageFont.truetype(str(FALLBACK), size)
    font = ImageFont.truetype(str(OUTFIT), size)
    font.set_variation_by_axes([weight])
    return font


def _runs(text):
    """The text cut into runs that one font can draw: [(run, fallback?)]."""
    runs = []
    for char in text:
        fallback = not _in_outfit(char)
        if runs and runs[-1][1] == fallback:
            runs[-1] = (runs[-1][0] + char, fallback)
        else:
            runs.append((char, fallback))
    return runs


def text_width(text, size, weight=600):
    """How wide `text` draws, in pixels, across however many fonts it needs."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return sum(
        probe.textlength(run, font=_font(size, weight, fallback)) for run, fallback in _runs(text)
    )


def draw_text(draw, xy, text, size, fill, *, weight=600):  # pylint: disable=too-many-arguments
    """Draw `text` with its top-left at `xy`, switching fonts per run. Returns
    the width drawn, so a caller can place what follows it."""
    x, y = xy
    for run, fallback in _runs(text):
        font = _font(size, weight, fallback)
        draw.text((x, y), run, font=font, fill=fill)
        x += draw.textlength(run, font=font)
    return x - xy[0]


def fit(text, size, max_width, weight=600):
    """`text`, cut at a word and given an ellipsis if it would draw wider than
    `max_width`. A name is never cut mid-word unless it is one long word."""
    if text_width(text, size, weight) <= max_width:
        return text
    words = text.split(" ")
    while len(words) > 1:
        words.pop()
        candidate = " ".join(words).rstrip(",") + "…"
        if text_width(candidate, size, weight) <= max_width:
            return candidate
    cut = text
    while cut and text_width(cut + "…", size, weight) > max_width:
        cut = cut[:-1]
    return cut + "…"


def wrap(text, size, max_width, max_lines, weight=600):
    """`text` broken into at most `max_lines` lines that fit `max_width`, the
    last one ellipsized if the text runs past it."""
    lines, current = [], ""
    words = text.split(" ")
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if text_width(candidate, size, weight) <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            rest = " ".join([current, *words[index + 1:]])
            lines.append(fit(rest, size, max_width, weight))
            return lines
    lines.append(current)
    return [fit(line, size, max_width, weight) for line in lines[:max_lines]]


def background():
    """The site's dark gradient, top to bottom."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND_BOTTOM)
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        mix = y / (HEIGHT - 1)
        colour = tuple(
            round(top + (bottom - top) * mix)
            for top, bottom in zip(BACKGROUND_TOP, BACKGROUND_BOTTOM)
        )
        draw.line([(0, y), (WIDTH, y)], fill=colour)
    return image


def brand_strip(image, right=""):
    """The band along the top: the wordmark at the left and `right` (the
    event, on a post) at the other end, cut to the room it has."""
    band = Image.new("RGBA", (WIDTH, STRIP_HEIGHT), STRIP_FILL)
    image.paste(band, (0, 0), band)
    draw = ImageDraw.Draw(image)
    top = (STRIP_HEIGHT - WORDMARK_SIZE) // 2 - 4
    width = draw_text(draw, (MARGIN, top), "Uma", WORDMARK_SIZE, BRAND)
    width += draw_text(draw, (MARGIN + width, top), " Carat Calculator", WORDMARK_SIZE, INK)
    if right:
        room = WIDTH - MARGIN * 2 - width - 48
        label = fit(right, 26, room, weight=500)
        label_width = text_width(label, 26, weight=500)
        draw_text(draw, (WIDTH - MARGIN - label_width, top + 6), label, 26, MUTED, weight=500)


def cover(art, blur):
    """An uploaded picture (a FieldFile) scaled to cover the card, cropped to
    its centre and blurred by `blur`, for a backdrop; None when it can't be
    read. Used by plan_preview.py (a banner's art) and guide_preview.py (an
    event's)."""
    try:
        with art.open("rb") as handle:
            picture = Image.open(io.BytesIO(handle.read()))
            picture.load()
    except Exception as error:  # pylint: disable=broad-exception-caught
        # Any storage or decoding failure: a crawler must never get a 500.
        logger.warning("Preview art %s could not be read: %r", getattr(art, "name", art), error)
        return None
    picture = picture.convert("RGB")
    scale = max(WIDTH / picture.width, HEIGHT / picture.height)
    size = (round(picture.width * scale), round(picture.height * scale))
    picture = picture.resize(size, Image.Resampling.LANCZOS)
    left, top = (size[0] - WIDTH) // 2, (size[1] - HEIGHT) // 2
    return picture.crop((left, top, left + WIDTH, top + HEIGHT)).filter(ImageFilter.GaussianBlur(blur))


def shade(image, colour, left, right):
    """Darken a backdrop left to right, from alpha `left` to `right`, so white
    words stay readable on any of it."""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), colour + (0,))
    draw = ImageDraw.Draw(layer)
    for x in range(WIDTH):
        alpha = round(left + (right - left) * x / (WIDTH - 1))
        draw.line([(x, 0), (x, HEIGHT)], fill=colour + (alpha,))
    image.paste(layer, (0, 0), layer)


def to_jpeg(image):
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
