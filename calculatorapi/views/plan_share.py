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
- The first banner with art, as the picture.
Never the plan name, the plan id, the owner's display name or anything else
about the owner: the same list plan_public.py keeps out of the summary.

A plan that is not shared, or an id that does not exist, gets the site's own
tags (one answer for both, like plans.get_shared_plan's 404) and is still sent
to /app/<id>, where the card says the plan isn't shared. A 200 rather than a
404, so an old link still unfurls as the site.

Opening this page creates and counts nothing: crawlers fetch it on every paste.
"""

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import HttpResponse
from django.shortcuts import render

from calculatorapi import plans
from calculatorapi.models import Plan
from calculatorapi.views.gallery import GalleryThrottle
from calculatorapi.views.gallery_share import SITE_DESCRIPTION, SITE_NAME
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


def _title(rows, pulls, steps):
    """'Shared pull plan: 230 pulls on 4 banners', with the steps when there
    are any. A step is not converted to pulls (plan_totals says why)."""
    if not rows:
        return "Shared pull plan"
    counts = [_plural(pulls, "pull", "pulls")] if pulls or not steps else []
    if steps:
        counts.append(_plural(steps, "step-up step", "step-up steps"))
    return f"Shared pull plan: {' and '.join(counts)} on {_plural(len(rows), 'banner', 'banners')}"


def _banner_names(rows):
    """'T.M. Opera, Oguri Cap (support), Mihono Bourbon and 2 more.' Support
    banners are named after their card, which is often an uma's name too."""
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
        return f"{', '.join(shown)} and {rest} more."
    if len(shown) == 1:
        return f"{shown[0]}."
    return f"{', '.join(shown[:-1])} and {shown[-1]}."


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
    """(url, alt) of the first banner in the plan with a picture, or None.
    A step-up's own image wins over its campaign part's, as on the card."""
    for row in rows:
        banner = row.banner_target
        if banner is None:
            continue
        image = getattr(banner, "image", None) or banner.banner_timeline.image
        if image:
            url = default_storage.url(image.name)
            # Spaces gives an absolute URL; local file storage gives /media/...
            return (_site(url) if url.startswith("/") else url), banner.name
    return None


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
    context.update(
        title=_title(rows, pulls, steps),
        description=" ".join(part for part in parts if part),
    )

    art = _art(rows)
    if art is not None:
        url, alt = art
        # Uploaded art: its type and size are not known here, so those two
        # tags are left out (the template skips them when blank).
        context.update(
            image_url=url, image_alt=alt, image_type="", image_width=None,
            image_height=None, twitter_card="summary_large_image",
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
