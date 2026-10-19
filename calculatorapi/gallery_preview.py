"""
The picture Discord and X show when a gallery post's link is pasted.

THE TEAM, THE SITE AND THE EVENT
--------------------------------
Three uma portraits side by side, each with its running style under it, under
the site's brand strip with the event's name (share_card.brand_strip). No
names, no poster, no caption, no cards. The owner chose "the team, and only the
team" on 2026-10-14 and added the strip on 2026-10-19 (design polish item 26b):
the site's name and the event are catalogue text, not anything a person wrote.
Keeping the poster off it matters more than it looks: Discord and X copy the
image into their own caches, and those copies outlive a take-down, an account
deletion and a purge. A picture of three umas says nothing about the person
who posted them.

RENDERED ON REQUEST, NEVER STORED
---------------------------------
Pillow draws it when a crawler first asks, in about a tenth of a second plus
three portrait reads, and the bytes are kept in the "gallery-previews" cache
(settings.CACHES). Never written to storage: default storage is the production
Spaces bucket even on a developer's machine, and a preview is cheap to draw
again. The URL carries image_version(): GalleryPost.preview_version, a hash
of the umas and their styles, plus LAYOUT, so a re-post that changes the team
gets a new URL, and so does every post when this drawing changes. The picture
is served `immutable`, so a new design never reaches a crawler under an old
address.

A portrait that cannot be read becomes the grey silhouette rather than an
error: a crawler that gets a failure caches a card with no picture at all.
"""

import io
import logging

from django.core.cache import caches
from PIL import Image, ImageDraw

from calculatorapi import share_card
from calculatorapi.models import PvpStyle, Uma
from calculatorapi.share_card import HEIGHT, STRIP_HEIGHT, WIDTH

logger = logging.getLogger(__name__)

# Which drawing this is. Part of every picture's URL (image_version), so
# raising it sends every crawler to the new picture; raise it whenever the
# drawing changes. 2: the brand strip (2026-10-19).
LAYOUT = "2"
PORTRAIT = 340
LABEL_GAP = 22
LABEL_SIZE = 44
# The portraits and their labels as one block, centred in the room the brand
# strip leaves.
PORTRAIT_TOP = STRIP_HEIGHT + (HEIGHT - STRIP_HEIGHT - (PORTRAIT + LABEL_GAP + LABEL_SIZE)) // 2
SILHOUETTE_COLOUR = (75, 85, 99)
CACHE_SECONDS = 7 * 24 * 60 * 60

STYLE_LABELS = dict(PvpStyle.choices)


def image_version(post):
    """The version part of the picture's URL: the team's hash and the layout."""
    return f"{post.preview_version}.{LAYOUT}"


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


def render(snapshot, event_name=""):  # pylint: disable=too-many-locals
    """JPEG bytes for a post's team snapshot under the brand strip, with the
    event's name in it. Pure: no cache, no request."""
    image = share_card.background()
    share_card.brand_strip(image, right=event_name)
    draw = ImageDraw.Draw(image)
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
        width = share_card.text_width(label, LABEL_SIZE)
        share_card.draw_text(
            draw,
            (centre - width / 2, PORTRAIT_TOP + PORTRAIT + LABEL_GAP),
            label,
            LABEL_SIZE,
            share_card.INK,
        )
    return share_card.to_jpeg(image)


def preview_bytes(post):
    """The post's preview, from the cache or freshly drawn."""
    key = f"gallery-preview:{post.public_id}:{image_version(post)}"
    previews = caches["gallery-previews"]
    image = previews.get(key)
    if image is None:
        image = render(post.team_snapshot, post.event.name)
        previews.set(key, image, CACHE_SECONDS)
    return image
