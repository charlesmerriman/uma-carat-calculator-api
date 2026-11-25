"""
A banner's link: GET /banner/<id> (the BannerTimeline's id, as the Timeline's
own ?focus=banner-<id> names it), a tiny HTML page of link-preview tags that
sends people on to /app/timeline?focus=banner-<id>.

NOT UNDER /api, like a post's /post/<id>, a plan's /plan/<id> and a guide's
/guide/<ref> (views/gallery_share.py explains why the server builds these):
Discord's and X's crawlers run no JavaScript, and the static site serves
/app/timeline?focus=… from one prerendered page whatever the query says, so
every banner unfurled as the plain Timeline. An ingress rule in
backend/.do/app.yaml sends /banner to this app. A Timeline banner card's
"Copy link" copies this address; an older /app/timeline?focus=banner-<id>
link still works, with the plain preview (Daptrius's request, board post
1574926713618432050).

WHAT THE PREVIEW SAYS
---------------------
Catalogue data only, all of it already public on the Timeline:
- the title is the banner window's name ("Taiki Shuttle (Camping) + Yamanin
  Zephyr"), which already names its umas;
- the description is its dates (the effective ones the Timeline shows,
  "estimated" when predicted from the JP schedule) and its support cards'
  names;
- the picture is the banner's own art, as uploaded. Daptrius: the art alone
  is right almost every time, so nothing is drawn over it (unlike the
  guide's and the plan's pictures). A banner with no art gets the site card.

An id that names no banner gets the site's own tags with a 404 status, and
a person is still sent on to the Timeline (see share_page).

Opening this page creates and counts nothing: crawlers fetch it on every paste.
"""

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

from calculatorapi.models import BannerTimeline
from calculatorapi.predictions import build_effective_date_maps
from calculatorapi.views.gallery import GalleryThrottle
from calculatorapi.views.gallery_share import SITE_DESCRIPTION, SITE_NAME

OPEN_LABEL = "Open this banner on Uma Musume Carat Calculator"
# Enough names for a support banner's usual three to five cards; a longer
# list is cut with "and N more" so the description stays one Discord line.
MAX_CARD_NAMES = 5


class BannerShareThrottle(GalleryThrottle):
    """The share page. High, like the gallery's and the guide's: Discord and
    X crawl from a few shared addresses. Counted in the "gallery" cache
    (GalleryThrottle explains why not "default")."""

    scope = "banner_share"


def _site(path=""):
    return f"{settings.FRONTEND_URL}{path}"


def app_path(banner_id):
    """The Timeline's own deep link to the card (frontend utils/timelineFocus.ts)."""
    return f"/app/timeline?focus=banner-{banner_id}"


def _default_context(banner_id=None):
    return {
        "title": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "share_url": _site(f"/banner/{banner_id}" if banner_id else "/app/timeline"),
        "app_url": _site("/app/timeline"),
        "image_url": _site("/social-card.png"),
        "image_type": "image/png",
        "image_width": 512,
        "image_height": 512,
        "image_alt": "The Uma Musume Carat Calculator umagon mascot.",
        "twitter_card": "summary",
        "open_label": OPEN_LABEL,
    }


def _day(moment):
    """2026/10/31: the site's date format (formatDate), in UTC, as the
    guide's preview writes it (views/guide_share.py)."""
    return f"{moment.year}/{moment.month}/{moment.day}"


def dates_sentence(entry):
    """ "2026/6/11 through 2026/6/20, estimated from the JP schedule", or ""
    for a banner with no dates yet. `entry` is the banner's row in the
    effective date map, the dates the Timeline itself shows."""
    start, end = (entry or {}).get("start_date"), (entry or {}).get("end_date")
    if not start:
        return ""
    when = f"{_day(start)} through {_day(end)}" if end else _day(start)
    # The Timeline's "Estimated" badge, in words (frontend PredictedBadge.tsx).
    return f"{when}, estimated from the JP schedule" if entry.get("is_predicted") else when


def card_names(banner):
    """The support cards on the window's support banners, in the admin's
    order, placeholders left out. Catalogue `name` only, never a translated
    column (UmaTL's text stays on the site, backend-models.md)."""
    names = []
    for support_banner in banner.support_banners.all():
        for link in support_banner.supportsonsupportbanner_set.all():
            card = link.support_card
            if not card.is_placeholder and card.name not in names:
                names.append(card.name)
    return names


def cards_sentence(names):
    """ "Support cards: A, B and C", "and N more" past MAX_CARD_NAMES."""
    if not names:
        return ""
    shown, rest = names[:MAX_CARD_NAMES], len(names) - MAX_CARD_NAMES
    if rest > 0:
        listed = f"{', '.join(shown)} and {rest} more"
    elif len(shown) > 1:
        listed = f"{', '.join(shown[:-1])} and {shown[-1]}"
    else:
        listed = shown[0]
    return f"{'Support card' if len(names) == 1 else 'Support cards'}: {listed}"


def _image_url(request, banner):
    """The banner art's public address. In production default storage is the
    Spaces CDN and `url` is already absolute; a test's file storage answers
    "/media/…", which a crawler could not follow, so that is made absolute."""
    url = banner.image.url
    return url if url.startswith(("http://", "https://")) else request.build_absolute_uri(url)


def _lookup(banner_id):
    return (
        BannerTimeline.objects.prefetch_related("support_banners__supportsonsupportbanner_set__support_card")
        .filter(pk=banner_id)
        .first()
    )


def share_context(request, banner):
    # Every model at once, never BannerTimeline alone: schedule offsets are
    # resolved across the whole calendar (predictions.py), so a one-model map
    # could put the banner on other dates than the Timeline does.
    entry = build_effective_date_maps()[BannerTimeline].get(banner.id)
    parts = [dates_sentence(entry), cards_sentence(card_names(banner))]
    context = _default_context(banner.id)
    context.update(
        title=banner.name,
        description=" ".join(f"{part}." for part in parts if part) or SITE_DESCRIPTION,
        app_url=_site(app_path(banner.id)),
        image_alt=banner.name,
    )
    if banner.image:
        # Uploaded art: its size isn't known without reading the file from
        # storage, so the type and size tags are left out (the template skips
        # them) and Discord reads the picture itself.
        context.update(
            image_url=_image_url(request, banner),
            image_type=None,
            image_width=None,
            image_height=None,
            twitter_card="summary_large_image",
        )
    return context


def share_page(request, banner_id):
    """The tags, or for an id naming no banner the site's own tags with a
    404 status. The 404 stops a crawler showing a preview of a banner that
    is gone; the page still carries the redirect, so a person who follows
    an old link lands on the Timeline rather than on an error."""
    if not BannerShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    banner = _lookup(banner_id)
    if banner is None:
        return render(request, "share/link_preview.html", _default_context(), status=404)
    return render(request, "share/link_preview.html", share_context(request, banner))
