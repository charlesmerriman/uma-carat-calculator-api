"""
Listing helpers for the media bucket (DigitalOcean Spaces).

These power the admin's "choose an existing image" picker: a content editor
picks a file that is *already* in the Space instead of re-uploading one.

Why this can be so simple — an ``ImageField`` never stores an image. It stores
the **bucket key** as a plain string (``"umas/Special Week.png"``), and
``S3Boto3Storage`` turns that key into a CDN URL on read. So "use an existing
image" is just writing a different string into the column: no upload, no copy,
no new model, no migration. ``scripts/link_missing_banner_images.py`` already
does exactly this in bulk; the picker is the interactive version.

This module is deliberately pure-ish logic (storage + cache only, no forms, no
views, no ORM queries), mirroring the predictions.py / analytics/ / oauth.py
split. Its consumers are admin_image_picker.py (widget + form) and
views/admin_images.py (the JSON endpoint).
"""

import logging

from django.apps import apps
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import models

logger = logging.getLogger(__name__)

# Extensions the picker will offer. Anything else in the bucket is ignored so
# a stray .txt or a directory marker can't be assigned to an image field.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")

# Listing the bucket is a network round trip, and an admin page would otherwise
# make one on every render. Five minutes keeps the picker responsive while
# staying fresh enough; an upload busts the folder's entry immediately (see
# SpacesImagePickerMixin.save_model) and the modal has a manual Refresh button.
CACHE_TTL_SECONDS = 300
CACHE_KEY_PREFIX = "image-library:"


def normalize_prefix(prefix):
    """
    Coerce a folder name into the exact form used as a key prefix.

    ``upload_to`` values are written as ``"umas/"`` but tolerate ``"umas"`` and
    ``"/umas/"``; normalizing here means callers (and the ?prefix= query param)
    can be sloppy without producing two cache entries for one folder.
    """
    prefix = (prefix or "").strip().strip("/")
    return f"{prefix}/" if prefix else ""


def image_prefixes():
    """
    Every folder an image may legitimately live in, derived from the models.

    This doubles as the allow-list for the JSON endpoint and for key
    validation, so the picker can never be used to enumerate or point at an
    arbitrary path in the bucket. Deriving it from ``upload_to`` rather than
    hardcoding means a new ImageField is covered automatically.
    """
    prefixes = set()
    for model in apps.get_app_config("calculatorapi").get_models():
        for field in model._meta.get_fields():
            # upload_to may be a callable in Django; every one of ours is a
            # plain string, and a callable has no single listable folder.
            if isinstance(field, models.ImageField) and isinstance(field.upload_to, str):
                prefixes.add(normalize_prefix(field.upload_to))
    prefixes.discard("")
    return frozenset(prefixes)


def list_images(prefix, force_refresh=False):
    """
    Images sitting in ``prefix``, newest listing cached for CACHE_TTL_SECONDS.

    Returns a list of ``{"key", "name", "url"}`` dicts sorted by name. Returns
    ``[]`` rather than raising if the bucket can't be reached — the picker is
    an enhancement, and a credentials or network problem must degrade the admin
    to upload-only instead of 500ing the whole change form.
    """
    prefix = normalize_prefix(prefix)
    if not prefix:
        return []

    cache_key = f"{CACHE_KEY_PREFIX}{prefix}"
    if force_refresh:
        cache.delete(cache_key)
    else:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        _directories, filenames = default_storage.listdir(prefix)
    except Exception:  # pylint: disable=broad-except
        # boto3 raises a wide family here (ClientError, NoCredentialsError,
        # EndpointConnectionError, ParamValidationError...) and FileSystemStorage
        # raises FileNotFoundError. Catching narrowly would mean chasing that
        # list forever for a failure mode where the answer is always the same:
        # show no library, keep the upload field working.
        logger.warning("Could not list media bucket folder %r", prefix, exc_info=True)
        return []  # deliberately not cached, so the next render retries

    images = [
        {
            "key": f"{prefix}{filename}",
            "name": filename,
            # For S3Storage with custom_domain + querystring_auth=False this is
            # pure string building against the CDN host — no extra API calls.
            "url": default_storage.url(f"{prefix}{filename}"),
        }
        for filename in filenames
        if filename.lower().endswith(IMAGE_EXTENSIONS)
    ]
    images.sort(key=lambda image: image["name"].lower())

    cache.set(cache_key, images, CACHE_TTL_SECONDS)
    return images


def invalidate(prefix):
    """Drop the cached listing for one folder (called after a new upload)."""
    cache.delete(f"{CACHE_KEY_PREFIX}{normalize_prefix(prefix)}")


def listing_is_cached(prefix):
    """
    Whether a *successful* listing for ``prefix`` is currently cached.

    Lets callers tell "this folder is genuinely empty" apart from "we couldn't
    reach the bucket", which are otherwise both an empty list: only successful
    listings are ever cached (see the failure path in ``list_images``).
    """
    return cache.get(f"{CACHE_KEY_PREFIX}{normalize_prefix(prefix)}") is not None


def is_valid_key(key):
    """
    Whether ``key`` is safe to write into an ImageField.

    Checks shape, not existence: it must sit under a known image folder, carry
    an image extension, and contain no traversal. Existence is intentionally
    not checked — that would cost a HEAD request per save, and a key pointing
    at a deleted file degrades to a broken thumbnail, exactly as it already
    can when a fixture references a since-removed image.
    """
    if not key or key.startswith("/") or ".." in key or "//" in key:
        return False
    if not key.lower().endswith(IMAGE_EXTENSIONS):
        return False
    return any(key.startswith(prefix) for prefix in image_prefixes())
