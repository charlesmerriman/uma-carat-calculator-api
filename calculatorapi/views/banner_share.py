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
- the title is who the banner features, worded as the Timeline card's own
  title (frontend featuredTitle): umas first, then support cards, each name
  once, "A, B and C", past three "A, B and 3 more". Never the row's `name`,
  which is an editor's label the card doesn't show (it can carry "(Rerun)"
  or a typo); that is only the fallback for a banner with nobody on it yet;
- the description is its dates (the effective ones the Timeline shows,
  "estimated" when predicted from the JP schedule), and when the title had
  to cut its list, everyone it features;
- the picture is a branded card (banner_preview.py, owner 2026-11-25): the
  brand strip, the same title and dates, and the banner's art, sharp at the
  right and blurred behind, in the family of the plan's and the guide's
  pictures. A banner with no art gets the card on the plain background.

An address that names no banner gets the site's own tags, a 200 like /post,
/plan and /guide (owner, 2026-11-25), and a person is still sent on to the
Timeline. Its picture is a 404: the page's tags point those at the site card.

Opening this page creates and counts nothing: crawlers fetch it on every paste.
"""

import re

from django.conf import settings
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from calculatorapi import banner_preview
from calculatorapi.banner_rate_ups import split_rate_ups
from calculatorapi.models import BannerTimeline, SupportsOnSupportBanner, TimelineDisplay, UmasOnUmaBanner
from calculatorapi.predictions import build_effective_date_maps
from calculatorapi.share_card import HEIGHT, WIDTH
from calculatorapi.views.gallery import GalleryThrottle
from calculatorapi.views.gallery_share import IMMUTABLE, SITE_DESCRIPTION, SITE_NAME

OPEN_LABEL = "Open this banner on Uma Musume Carat Calculator"
# A BannerTimeline id, as ?focus=banner-<id> carries it. Anything else names no banner.
REF = re.compile(r"^[1-9]\d{0,9}$")
# The title's list, as the card's (featuredTitle): three names, or two and a count.
TITLE_NAMES = 3
# The description's full list when the title was cut: a Golden Week revival
# features up to eleven, and Discord cuts a description at about 350 characters.
MAX_LISTED_NAMES = 12


class BannerShareThrottle(GalleryThrottle):
    """The share page and its picture. High, like the gallery's and the guide's: Discord and
    X crawl from a few shared addresses. Counted in the "gallery" cache
    (GalleryThrottle explains why not "default")."""

    scope = "banner_share"


def _site(path=""):
    return f"{settings.FRONTEND_URL}{path}"


def app_path(banner_id):
    """The Timeline's own deep link to the card (frontend utils/timelineFocus.ts)."""
    return f"/app/timeline?focus=banner-{banner_id}"


def _default_context(ref):
    return {
        "title": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "share_url": _site(f"/banner/{ref}"),
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


def when_text(entry):
    """ "2026/6/11 through 2026/6/20", or "" for a banner with no dates yet.
    `entry` is the banner's row in the effective date map, the dates the
    Timeline itself shows."""
    start, end = (entry or {}).get("start_date"), (entry or {}).get("end_date")
    if not start:
        return ""
    return f"{_day(start)} through {_day(end)}" if end else _day(start)


def dates_sentence(entry):
    """when_text, with ", estimated from the JP schedule" when predicted:
    the Timeline's "Estimated" badge in words (frontend PredictedBadge.tsx)."""
    when = when_text(entry)
    return f"{when}, estimated from the JP schedule" if when and entry.get("is_predicted") else when


def featured_names(banner):
    """Everyone the banner features, umas first, then support cards, each
    name once, in the order the Timeline card lists them (the API's: by
    junction row). Placeholder cards left out. Catalogue `name` only, never
    a translated column (UmaTL's text stays on the site, backend-models.md)."""
    names = []
    for uma_banner in banner.uma_banners.all():
        names.extend(link.uma.name for link in uma_banner.umasonumabanner_set.all())
    for support_banner in banner.support_banners.all():
        # The SSRs and any SR drawn as a picture, as the Timeline card draws
        # them; the SR and R rate-ups listed under it stay out of the preview.
        ssrs, others = split_rate_ups(support_banner.supportsonsupportbanner_set.all())
        drawn = ssrs + [link for link, display in others if display == TimelineDisplay.PICTURE]
        names.extend(link.support_card.name for link in drawn if not link.support_card.is_placeholder)
    return list(dict.fromkeys(names))


def _and_list(names):
    """ "A", "A and B", "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def featured_title(names):
    """The Timeline card's title (frontend timelineShared.ts featuredTitle),
    or None when nobody is announced yet."""
    if not names:
        return None
    if len(names) > TITLE_NAMES:
        return f"{', '.join(names[:TITLE_NAMES - 1])} and {len(names) - (TITLE_NAMES - 1)} more"
    return _and_list(names)


def featuring_sentence(names):
    """ "Featuring A, B, C and D", only when the title had to cut the list."""
    if len(names) <= TITLE_NAMES:
        return ""
    shown, rest = names[:MAX_LISTED_NAMES], len(names) - MAX_LISTED_NAMES
    listed = f"{', '.join(shown)} and {rest} more" if rest > 0 else _and_list(shown)
    return f"Featuring {listed}"


def _lookup(ref):
    """The banner "73" names, or None."""
    if not REF.match(ref):
        return None
    # Junction rows by id: the order the API sends them, so the names come
    # out in the card's order.
    return (
        BannerTimeline.objects.prefetch_related(
            Prefetch("uma_banners__umasonumabanner_set",
                     queryset=UmasOnUmaBanner.objects.select_related("uma").order_by("id")),
            Prefetch("support_banners__supportsonsupportbanner_set",
                     queryset=SupportsOnSupportBanner.objects.select_related("support_card").order_by("id")),
        )
        .filter(pk=int(ref))
        .first()
    )


def _facts(banner):
    """Everything the page and the picture say, decided once: (title, entry,
    names, card)."""
    # Every model at once, never BannerTimeline alone: schedule offsets are
    # resolved across the whole calendar (predictions.py), so a one-model map
    # could put the banner on other dates than the Timeline does.
    entry = build_effective_date_maps()[BannerTimeline].get(banner.id)
    names = featured_names(banner)
    title = featured_title(names) or banner.name
    card = banner_preview.Card(
        headline=title,
        when=when_text(entry),
        estimated=bool(entry and entry.get("is_predicted")),
        art=banner.image or None,
    )
    return title, entry, names, card


def share_context(banner):
    title, entry, names, card = _facts(banner)
    parts = [dates_sentence(entry), featuring_sentence(names)]
    context = _default_context(banner.id)
    context.update(
        title=title,
        description=" ".join(f"{part}." for part in parts if part) or SITE_DESCRIPTION,
        app_url=_site(app_path(banner.id)),
        image_url=_site(f"/banner/{banner.id}/preview.jpg?v={card.version}"),
        image_type="image/jpeg",
        image_width=WIDTH,
        image_height=HEIGHT,
        image_alt=title,
        twitter_card="summary_large_image",
    )
    return context


def share_page(request, ref):
    if not BannerShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    banner = _lookup(ref)
    context = share_context(banner) if banner is not None else _default_context(ref)
    return render(request, "share/link_preview.html", context)


def share_preview(request, ref):
    """The banner's picture. An address that names no banner is a 404: the
    page's own tags already point those at the site's card."""
    if not BannerShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    banner = _lookup(ref)
    if banner is None:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    card = _facts(banner)[3]
    asked = request.GET.get("v")
    if asked and asked != card.version:
        # A picture from before the banner changed: send the crawler to the
        # current one rather than caching it under the old address forever.
        return HttpResponseRedirect(f"/banner/{banner.id}/preview.jpg?v={card.version}")
    response = HttpResponse(banner_preview.preview_bytes(banner.id, card), content_type="image/jpeg")
    response["Cache-Control"] = IMMUTABLE
    return response
