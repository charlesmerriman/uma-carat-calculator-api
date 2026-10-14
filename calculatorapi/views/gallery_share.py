"""
A gallery post's permanent link, and the picture its link preview shows.

GET /post/<public_id>               a tiny HTML page: link-preview tags, then on
                                    to the post in the app
GET /post/<public_id>/preview.jpg   the preview picture (gallery_preview.py)

NOT UNDER /api. These are what people paste in Discord and on X, so they live
at https://umacaratcalculator.com/post/<id>: an ingress rule in
backend/.do/app.yaml sends /post to this app, the way it sends /admin.

WHY THE SERVER BUILDS THIS PAGE
-------------------------------
Discord's and X's crawlers do not run JavaScript. They fetch the URL and read
the tags in the HTML they get. The site itself is static, so its /app/pvp page
carries the same tags for every post; a preview of ONE team needs HTML built
for that team. Everyone gets the same page (no user-agent sniffing): crawlers
keep the tags, and a person's browser runs the one-line script and moves on,
with `location.replace` so the back button skips this page.

WHAT THE PREVIEW SAYS
---------------------
Generated text only: the event and the uma names. Never the caption, never
the display name. Discord and X keep their own copy of a preview long after a
post is taken down, so it must carry nothing a person wrote or is known by.

A hidden, blocked, taken-down or unknown post gets the site's own tags and is
sent to the gallery, where the app says the team isn't available. A 200 rather
than a 404, so an old link still unfurls as the site.

`/post` must never be disallowed in robots.txt: X's crawler obeys it. The
`noindex` tag is what keeps these pages out of search results.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from calculatorapi import gallery, gallery_preview
from calculatorapi.models import ChampionsMeeting, GalleryPost, Uma
from calculatorapi.views.gallery import GalleryThrottle

SITE_NAME = "Uma Musume Carat Calculator"
SITE_DESCRIPTION = (
    "A carat planner and banner timeline for Uma Musume. See every upcoming banner "
    "and how many carats and tickets you will have when it arrives."
)
IMMUTABLE = "public, max-age=31536000, immutable"


class GalleryShareThrottle(GalleryThrottle):
    """The share page and its picture. High, because Discord and X crawl from
    a few shared addresses, and every paste of a link is a fetch."""

    scope = "gallery_share"


def _throttled(request):
    return not GalleryShareThrottle().allow_request(request, None)


def _site(path=""):
    return f"{settings.FRONTEND_URL}{path}"


def _app_url(public_id, post=None):
    params = {"view": "gallery", "post": public_id}
    if post is not None:
        params = {"event": f"{post.event_kind}-{post.event.pk}", **params}
    return _site(f"/app/pvp?{urlencode(params)}")


def _default_context(public_id):
    return {
        "title": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "share_url": _site(f"/post/{public_id}"),
        "app_url": _app_url(public_id),
        "image_url": _site("/social-card.png"),
        "image_type": "image/png",
        "image_width": 512,
        "image_height": 512,
        "image_alt": "The Uma Musume Carat Calculator umagon mascot.",
        "twitter_card": "summary",
    }


def _uma_names(post):
    umas = Uma.objects.in_bulk([member["uma"] for member in post.team_snapshot])
    return [
        umas[member["uma"]].name
        for member in sorted(post.team_snapshot, key=lambda member: member["position"])
        if member["uma"] in umas
    ]


def share_context(post):
    """The tags for one available post. Generated text only (see above)."""
    names = _uma_names(post)
    if not names:
        return _default_context(post.public_id)
    event_kind = "Champions Meeting" if isinstance(post.event, ChampionsMeeting) else "League of Heroes"
    team = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
    return {
        "title": f"{post.event.name} team: {', '.join(names)}",
        "description": (
            f"A {event_kind} team from the PvP gallery. Open it to see the support "
            "cards, upvotes and comments."
        ),
        "share_url": _site(f"/post/{post.public_id}"),
        "app_url": _app_url(post.public_id, post),
        "image_url": _site(f"/post/{post.public_id}/preview.jpg?v={post.preview_version}"),
        "image_type": "image/jpeg",
        "image_width": gallery_preview.WIDTH,
        "image_height": gallery_preview.HEIGHT,
        "image_alt": team,
        "twitter_card": "summary_large_image",
    }


def _available(public_id):
    try:
        return gallery.get_post(public_id)
    except GalleryPost.DoesNotExist:
        return None


def share_page(request, public_id):
    if _throttled(request):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    post = _available(public_id)
    context = share_context(post) if post is not None else _default_context(public_id)
    return render(request, "gallery/share.html", context)


def share_preview(request, public_id):
    if _throttled(request):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    post = _available(public_id)
    if post is None:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    asked = request.GET.get("v")
    if asked and asked != post.preview_version:
        # An old version's URL, from a preview cached before a re-post. Send
        # the crawler to the current picture rather than caching it under
        # the old address forever.
        return HttpResponseRedirect(
            f"/post/{post.public_id}/preview.jpg?v={post.preview_version}"
        )
    response = HttpResponse(gallery_preview.preview_bytes(post), content_type="image/jpeg")
    response["Cache-Control"] = IMMUTABLE
    return response
