"""
Store a freshly uploaded picture as lossless WebP.

Daptrius exports the layered uma art as PNG (notes/layered-uma-art-plan.md).
A foreground is almost all transparent, yet the PNGs average 185 KB; the same
pixels as lossless WebP are about 10 KB, and a framed uma loads one of each.
Lossless, never lossy: lossy WebP halves the colour resolution and greys the
one-pixel coloured edges the art is drawn with.

Only a NEW upload is converted. A file already in storage (one the link
command pointed at, or one an editor picked with "Browse library") is
committed, and is left exactly as it is: converting it would mean downloading
it from the Space and uploading a second copy on every save.
"""

import io
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from PIL import Image


def as_webp(field_file):
    """Swap an uncommitted upload on `field_file` for its lossless WebP, in place.

    Call it from the model's save() before super().save(): the FileField then
    stores the WebP under the same name with a .webp ending. Anything that is
    not a fresh upload, is already WebP, or can't be read as an image is left
    alone (Django's own ImageField validation already refused a non-image in
    the admin form).
    """
    if not field_file or getattr(field_file, "_committed", True):
        return
    name = PurePosixPath(field_file.name)
    if name.suffix.lower() == ".webp":
        return
    try:
        field_file.open("rb")
        field_file.seek(0)
        picture = Image.open(field_file)
        picture.load()
    except (OSError, ValueError):
        return
    buffer = io.BytesIO()
    # RGBA keeps the transparency both layers depend on, whatever mode the
    # export used (a palette PNG would otherwise lose it).
    picture.convert("RGBA").save(buffer, "WEBP", lossless=True)
    # save=False: the caller is already inside the model's save().
    field_file.save(name.with_suffix(".webp").name, ContentFile(buffer.getvalue()), save=False)
