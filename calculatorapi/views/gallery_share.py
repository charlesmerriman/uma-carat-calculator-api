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

A COMMUNITY PLAN POST says what a shared plan's link says (views/plan_share.py):
its totals and its first banners' catalogue names, and its picture shows its
first five banners (plan_preview.py, Henry's ask). Never its title, its words,
its notes, its income or its author, for the same reason as above. It opens
the post on /app/plans.

`/post` must never be disallowed in robots.txt: X's crawler obeys it. The
`noindex` tag is what keeps these pages out of search results.
"""

from types import SimpleNamespace
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from calculatorapi import gallery, gallery_preview, plan_gallery, plan_preview
from calculatorapi.models import BannerStepUp, BannerSupport, BannerUma, ChampionsMeeting, GalleryPost, Uma
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
    if post is not None and post.is_plan:
        return _site(f"/app/plans?{urlencode({'post': public_id})}")
    params = {"view": "gallery", "post": public_id}
    if post is not None:
        params = {"event": f"{post.event_kind}-{post.event.pk}", **params}
    return _site(f"/app/pvp?{urlencode(params)}")


def _plan_rows(post):
    """The post's rows as plan_share's helpers read rows (`banner_target`,
    `banner_support_id`), with the banners fetched in three queries."""
    known = plan_gallery.known_banners([post.plan_snapshot])
    rows = plan_gallery.rows_for_display(post.plan_snapshot, known)
    banners = {
        key: model.objects.select_related("banner_timeline").in_bulk(
            [row[key] for row in rows if row.get(key)]
        )
        for key, model in (("banner_uma", BannerUma), ("banner_support", BannerSupport),
                           ("banner_step_up", BannerStepUp))
    }
    shaped = [
        SimpleNamespace(
            banner_target=next(banners[key].get(row[key]) for key in banners if row.get(key)),
            banner_support_id=row.get("banner_support"),
        )
        for row in rows
    ]
    lead = plan_gallery.lead_rows(post.plan_snapshot, known, count=plan_gallery.PREVIEW_ROWS)
    lead_banners = [next(banners[key].get(row[key]) for key in banners if row.get(key)) for row in lead]
    return rows, shaped, lead_banners


def plan_card(post):
    """A Community plan post's picture: its totals and names, and its first
    five banners (the ones still ahead first, as its feed row)."""
    rows, shaped, lead = _plan_rows(post)
    _, pulls, steps = plan_gallery.summary(rows)
    tiles = tuple(
        art for art in (getattr(banner, "image", None) or banner.banner_timeline.image for banner in lead) if art
    )
    return plan_preview.Card(
        headline=plan_preview.plan_counts(shaped, pulls, steps) if rows else "No banners in it",
        names=plan_preview.banner_list(shaped),
        kicker="Pull Plan Library",
        tiles=tiles,
    ), shaped, pulls, steps


def _plan_context(post):
    """The tags for a Community plan post: counts and catalogue names only."""
    card, shaped, pulls, steps = plan_card(post)
    names = plan_preview.banner_list(shaped)
    title = f"Pull Plan Library: {plan_preview.plan_counts(shaped, pulls, steps)}" if shaped else "Pull Plan Library"
    context = _default_context(post.public_id)
    context.update(
        title=title,
        description=" ".join(part for part in (
            f"{names}." if names else "",
            "Open it to see every banner, then copy it into your own plans.",
        ) if part),
        app_url=_app_url(post.public_id, post),
        image_url=_site(f"/post/{post.public_id}/preview.jpg?v={card.version}"),
        image_type="image/jpeg",
        image_width=plan_preview.WIDTH,
        image_height=plan_preview.HEIGHT,
        image_alt=title,
        twitter_card="summary_large_image",
        open_label="Open this plan on Uma Musume Carat Calculator",
    )
    return context


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
        "open_label": "Open this team on Uma Musume Carat Calculator",
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
    if post.is_plan:
        return _plan_context(post)
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
        "image_url": _site(f"/post/{post.public_id}/preview.jpg?v={gallery_preview.image_version(post)}"),
        "image_type": "image/jpeg",
        "image_width": gallery_preview.WIDTH,
        "image_height": gallery_preview.HEIGHT,
        "image_alt": team,
        "twitter_card": "summary_large_image",
        "open_label": "Open this team on Uma Musume Carat Calculator",
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
    return render(request, "share/link_preview.html", context)


def share_preview(request, public_id):
    if _throttled(request):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    post = _available(public_id)
    if post is None:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    if post.is_plan:
        return _plan_preview(request, post)
    asked = request.GET.get("v")
    current = gallery_preview.image_version(post)
    if asked and asked != current:
        # An old version's URL, from a preview cached before a re-post or a
        # change of drawing. Send the crawler to the current picture rather
        # than caching it under the old address forever.
        return HttpResponseRedirect(f"/post/{post.public_id}/preview.jpg?v={current}")
    response = HttpResponse(gallery_preview.preview_bytes(post), content_type="image/jpeg")
    response["Cache-Control"] = IMMUTABLE
    return response


def _plan_preview(request, post):
    """A Community plan post's picture, versioned like a team's: an old
    version's URL redirects to the current one."""
    card = plan_card(post)[0]
    asked = request.GET.get("v")
    if asked and asked != card.version:
        return HttpResponseRedirect(f"/post/{post.public_id}/preview.jpg?v={card.version}")
    # "post-" so a post's picture and a shared plan's never share a cache key.
    response = HttpResponse(plan_preview.preview_bytes(f"post-{post.public_id}", card), content_type="image/jpeg")
    response["Cache-Control"] = IMMUTABLE
    return response
