"""
A shared plan's link: GET /plan/<public_id>, a tiny HTML page of link-preview
tags that sends people on to the landing card at /app/<public_id>.

NOT UNDER /api, for the same reason as a gallery post's /post/<id> (see
views/gallery_share.py, which this mirrors): Discord's and X's crawlers do not
run JavaScript, and the static site serves /app/<id> from the one spa.html
shell, so every plan used to unfurl as the plain site. An ingress rule in
backend/.do/app.yaml sends /plan to this app. The share face copies this
address; an older /app/<id> link still works, it just keeps the plain preview.

WHAT THE PREVIEW SAYS (owner's call, 2026-10-16)
------------------------------------------------
- The pull and step totals (plan_public.plan_totals) and how many banners.
- The first few banner names: catalogue data.
- The plan's notes, ONLY when the owner turned "Include my notes" on, trimmed
  to NOTES_MAX_CHARS. Anyone holding the link could already read them by
  taking a copy; what is new is that Discord and X keep their own copy of a
  preview after the owner turns notes off or resets the link. The share face
  says so beside the switch.
- The picture (plan_preview.py, owner 2026-10-19): the brand strip, the
  totals and the first banner names drawn over the first banner's art, the
  same facts as the tags. It was the raw art until then, a game promo that
  said nothing about the plan. Notes never go on it.
Never the plan name, the plan id, the owner's display name or anything else
about the owner: the same list plan_public.py keeps out of the summary.

A plan that is not shared, or an id that does not exist, gets the site's own
tags (one answer for both, like plans.get_shared_plan's 404) and is still sent
to /app/<id>, where the card says the plan isn't shared. A 200 rather than a
404, so an old link still unfurls as the site.

Opening this page creates and counts nothing: crawlers fetch it on every paste.
"""

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from calculatorapi import plan_preview, plans
from calculatorapi.models import Plan
from calculatorapi.share_card import HEIGHT, WIDTH
from calculatorapi.views.gallery import GalleryThrottle
from calculatorapi.views.gallery_share import IMMUTABLE, SITE_DESCRIPTION, SITE_NAME
from calculatorapi.views.plan_public import plan_totals, rows_in_order

# How many banner names the description lists before "and N more".
NAMES_SHOWN = 3
# The notes' share of the description. Discord shows roughly 350 characters
# of a description; the totals and names take the first hundred or so.
NOTES_MAX_CHARS = 200
OPEN_LABEL = "Open this plan on Uma Musume Carat Calculator"


class PlanShareThrottle(GalleryThrottle):
    """The share page. High, like the gallery's: Discord and X crawl from a
    few shared addresses, and every paste of a link is a fetch. Counted in the
    "gallery" cache with the gallery's counters (GalleryThrottle explains), so
    a burst of pastes cannot push /calculator-data out of "default"."""

    scope = "plan_share"


def _site(path=""):
    return f"{settings.FRONTEND_URL}{path}"


def _plural(count, one, many):
    return f"{count:,} {one if count == 1 else many}"


def _default_context(public_id):
    return {
        "title": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "share_url": _site(f"/plan/{public_id}"),
        "app_url": _site(f"/app/{public_id}"),
        "image_url": _site("/social-card.png"),
        "image_type": "image/png",
        "image_width": 512,
        "image_height": 512,
        "image_alt": "The Uma Musume Carat Calculator umagon mascot.",
        "twitter_card": "summary",
        "open_label": OPEN_LABEL,
    }


def _counts(rows, pulls, steps):
    """'230 pulls on 4 banners', with the steps when there are any. A step is
    not converted to pulls (plan_totals says why)."""
    counts = [_plural(pulls, "pull", "pulls")] if pulls or not steps else []
    if steps:
        counts.append(_plural(steps, "step-up step", "step-up steps"))
    return f"{' and '.join(counts)} on {_plural(len(rows), 'banner', 'banners')}"


def _title(rows, pulls, steps):
    """'Shared pull plan: 230 pulls on 4 banners'."""
    return f"Shared pull plan: {_counts(rows, pulls, steps)}" if rows else "Shared pull plan"


def _banner_list(rows):
    """'T.M. Opera, Oguri Cap (support), Mihono Bourbon and 2 more' (no full
    stop: the picture prints it as a line). Support banners are named after
    their card, which is often an uma's name too."""
    names = []
    for row in rows:
        banner = row.banner_target
        if banner is None:
            continue
        names.append(f"{banner.name} (support)" if row.banner_support_id else banner.name)
    if not names:
        return ""
    shown, rest = names[:NAMES_SHOWN], len(names) - NAMES_SHOWN
    if rest > 0:
        return f"{', '.join(shown)} and {rest} more"
    if len(shown) == 1:
        return shown[0]
    return f"{', '.join(shown[:-1])} and {shown[-1]}"


def _banner_names(rows):
    """The description's sentence: _banner_list with its full stop."""
    names = _banner_list(rows)
    return f"{names}." if names else ""


def _notes(rows):
    """The plan's notes in planner order, each on one line, joined and cut
    at a word to NOTES_MAX_CHARS. Empty unless the caller checked
    share_notes."""
    notes = [" ".join(row.note.split()) for row in rows if row.note.strip()]
    text = "; ".join(notes)
    if len(text) <= NOTES_MAX_CHARS:
        return text
    cut = text[:NOTES_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ;,.")
    return f"{cut}…"


def _art(rows):
    """The image of the first banner in the plan with a picture, or None. A
    step-up's own image wins over its campaign part's, as on the card."""
    for row in rows:
        banner = row.banner_target
        if banner is None:
            continue
        image = getattr(banner, "image", None) or banner.banner_timeline.image
        if image:
            return image
    return None


def card(rows, pulls, steps):
    """What the plan's picture says (plan_preview.py): the tags' facts, never
    the notes."""
    return plan_preview.Card(
        headline=_counts(rows, pulls, steps) if rows else "No banners in it yet",
        names=_banner_list(rows),
        art=_art(rows),
    )


def share_context(plan):
    """The tags for one shared plan (see the module docstring for the rule)."""
    rows = rows_in_order(plan)
    pulls, steps = plan_totals(rows)
    context = _default_context(plan.public_id)

    parts = [_banner_names(rows)] if rows else ["No banners in it yet."]
    notes = _notes(rows) if plan.share_notes else ""
    if notes:
        parts.append(f"Notes: {notes}")
    else:
        parts.append("Copy it to see how it works out with your own carats.")
    title = _title(rows, pulls, steps)
    context.update(
        title=title,
        description=" ".join(part for part in parts if part),
        image_url=_site(f"/plan/{plan.public_id}/preview.jpg?v={card(rows, pulls, steps).version}"),
        image_type="image/jpeg",
        image_width=WIDTH,
        image_height=HEIGHT,
        image_alt=title,
        twitter_card="summary_large_image",
    )
    return context


def share_page(request, public_id):
    if not PlanShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    try:
        plan = plans.get_shared_plan(public_id)
    except Plan.DoesNotExist:
        context = _default_context(public_id)
    else:
        context = share_context(plan)
    return render(request, "share/link_preview.html", context)


def share_preview(request, public_id):
    """The plan's picture. A plan that is not shared, or an unknown id, is a
    404: there is no picture of the site to fall back to here, and the page's
    own tags already point those at the site's card."""
    if not PlanShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    try:
        plan = plans.get_shared_plan(public_id)
    except Plan.DoesNotExist:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    rows = rows_in_order(plan)
    pulls, steps = plan_totals(rows)
    current = card(rows, pulls, steps)
    asked = request.GET.get("v")
    if asked and asked != current.version:
        # A picture from before the plan changed. Send the crawler to the
        # current one rather than caching it under the old address forever.
        return HttpResponseRedirect(f"/plan/{plan.public_id}/preview.jpg?v={current.version}")
    response = HttpResponse(plan_preview.preview_bytes(plan.public_id, current), content_type="image/jpeg")
    response["Cache-Control"] = IMMUTABLE
    return response
