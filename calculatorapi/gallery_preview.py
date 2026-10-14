"""
The picture Discord and X show when a gallery post's link is pasted.

THE TEAM, AND ONLY THE TEAM
---------------------------
Three uma portraits side by side, each with its running style under it, on a
plain background. No names, no poster, no caption, no cards, no wordmark (the
owner, 2026-10-14). Keeping the poster off it matters more than it looks:
Discord and X copy the image into their own caches, and those copies outlive a
take-down, an account deletion and a purge. A picture of three umas says
nothing about the person who posted them.

RENDERED ON REQUEST, NEVER STORED
---------------------------------
Pillow draws it when a crawler first asks, in about a tenth of a second plus
three portrait reads, and the bytes are kept in the "gallery-previews" cache
(settings.CACHES). Never written to storage: default storage is the production
Spaces bucket even on a developer's machine, and a preview is cheap to draw
again. The URL carries GalleryPost.preview_version, a hash of the umas and
their styles, so a re-post that changes the team gets a new URL and the
crawlers fetch it afresh.

A portrait that cannot be read becomes the grey silhouette rather than an
error: a crawler that gets a failure caches a card with no picture at all.
"""

import io
import logging

from django.core.cache import caches
from PIL import Image, ImageDraw, ImageFont

from calculatorapi.models import PvpStyle, Uma

logger = logging.getLogger(__name__)

# The large-card size both Discord and X use (1.91:1).
WIDTH, HEIGHT = 1200, 630
PORTRAIT = 360
LABEL_GAP = 28
LABEL_SIZE = 48
# The portraits and their labels as one block, centred top to bottom.
PORTRAIT_TOP = (HEIGHT - (PORTRAIT + LABEL_GAP + LABEL_SIZE)) // 2
# The site's dark surfaces (gray-800 to gray-900), top to bottom.
BACKGROUND_TOP = (31, 41, 55)
BACKGROUND_BOTTOM = (17, 24, 39)
LABEL_COLOUR = (243, 244, 246)
SILHOUETTE_COLOUR = (75, 85, 99)
JPEG_QUALITY = 85
CACHE_SECONDS = 7 * 24 * 60 * 60

STYLE_LABELS = dict(PvpStyle.choices)


def _background():
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


def _silhouette():
    """The grey head and shoulders the site draws for a missing picture."""
    image = Image.new("RGBA", (PORTRAIT, PORTRAIT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    head = PORTRAIT // 4
    centre = PORTRAIT // 2
    draw.ellipse(
        [centre - head, PORTRAIT // 6, centre + head, PORTRAIT // 6 + 2 * head],
        fill=SILHOUETTE_COLOUR,
    )
    draw.ellipse(
        [PORTRAIT // 8, PORTRAIT // 2 + head // 2, PORTRAIT - PORTRAIT // 8, PORTRAIT + PORTRAIT // 3],
        fill=SILHOUETTE_COLOUR,
    )
    return image


def _portrait(uma):
    """The uma's portrait art fitted inside the square, or the silhouette."""
    if uma is None or not uma.portrait:
        return _silhouette()
    try:
        with uma.portrait.open("rb") as handle:
            art = Image.open(io.BytesIO(handle.read()))
            art.load()
    except Exception as error:  # pylint: disable=broad-exception-caught
        # Any storage or decoding failure: a crawler must never get a 500.
        # One line, not a traceback: the cause is in the message.
        logger.warning("Preview portrait for uma %s could not be read: %r", uma.pk, error)
        return _silhouette()
    art = art.convert("RGBA")
    art.thumbnail((PORTRAIT, PORTRAIT), Image.Resampling.LANCZOS)
    return art


def render(snapshot):  # pylint: disable=too-many-locals
    """JPEG bytes for a post's team snapshot. Pure: no cache, no request."""
    image = _background()
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=LABEL_SIZE)
    umas = Uma.objects.in_bulk([member["uma"] for member in snapshot])
    members = [member for member in snapshot if member["uma"] in umas] or snapshot
    column = WIDTH // max(len(members), 1)
    for index, member in enumerate(members):
        centre = column * index + column // 2
        art = _portrait(umas.get(member["uma"]))
        left = centre - art.width // 2
        top = PORTRAIT_TOP + (PORTRAIT - art.height) // 2
        image.paste(art, (left, top), art)
        label = STYLE_LABELS.get(member["style"], member["style"].title())
        width = draw.textlength(label, font=font)
        draw.text(
            (centre - width / 2, PORTRAIT_TOP + PORTRAIT + LABEL_GAP),
            label,
            font=font,
            fill=LABEL_COLOUR,
        )
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def preview_bytes(post):
    """The post's preview, from the cache or freshly drawn."""
    key = f"gallery-preview:{post.public_id}:{post.preview_version}"
    previews = caches["gallery-previews"]
    image = previews.get(key)
    if image is None:
        image = render(post.team_snapshot)
        previews.set(key, image, CACHE_SECONDS)
    return image
