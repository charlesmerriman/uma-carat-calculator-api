"""
A guide's link: GET /guide/<kind>-<id> ("cm-21", the event's database id, as
the PvP page's own ?event= names it), a tiny HTML page of link-preview tags
that sends people on to /app/pvp?event=cm-21. A ?style= passes through, so a
link copied on the Pace tab opens on Pace. Its picture is
/guide/<kind>-<id>/preview.jpg (guide_preview.py).

NOT UNDER /api, like a post's /post/<id> and a plan's /plan/<id> (see
views/gallery_share.py): Discord's and X's crawlers run no JavaScript, and
the static site serves /app/pvp?event=… from one prerendered page, so every
guide unfurled as the plain site. An ingress rule in backend/.do/app.yaml
sends /guide to this app. The page's "Copy link" copies this address; an
older /app/pvp?event=… link still works, with the plain preview.

WHAT THE PREVIEW SAYS (owner, 2026-10-24, guide-visuals-plan-2.md release D)
---------------------------------------------------------------------------
- A published guide everyone may read today (pvp_gate.guide_is_open): the
  facts only, never the guide's prose. Its style ranking, its stat targets
  (the "everyone" row, else the first) and the course; the picture says the
  same over the event's art.
- A guide the early-access gate still holds back, a draft, or an event with
  no guide yet: the event alone (its name, course and dates), so a pasted
  link never gives away what the gate keeps for supporters.
- An address that names no event: the site's own tags, a 200 (like /plan).
Never an editor's name, a comment, or anything else about a person.

Opening this page creates and counts nothing: crawlers fetch it on every paste.
"""

import re

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from calculatorapi import guide_preview, pvp_gate
from calculatorapi.models import PvpGuide
from calculatorapi.predictions import build_effective_date_maps
from calculatorapi.share_card import HEIGHT, WIDTH
from calculatorapi.views.gallery import GalleryThrottle
from calculatorapi.views.gallery_share import IMMUTABLE, SITE_DESCRIPTION, SITE_NAME
from calculatorapi.views.pvp_guide import EVENT_FIELD_FOR_KIND

REF = re.compile(r"^(cm|loh)-(\d+)$")
# What ?style= may carry through to the page: its running styles and "all".
STYLES = ("runaway", "front", "pace", "late", "end", "all")
OPEN_LABEL = "Open this guide on Uma Musume Carat Calculator"
# A ranking's words that name a running style (frontend pvpGuide.ts RANKING_WORDS).
RANKING_WORDS = {
    "runaway": "runaway",
    "front": "front",
    "front runner": "front",
    "pace": "pace",
    "pace chaser": "pace",
    "late": "late",
    "late surger": "late",
    "end": "end",
    "end closer": "end",
}
# The marks as the description writes them; the picture has its own (guide_preview.py).
TEXT_MARKS = {">>": "≫", ">": ">", ">=": "≥", "=": "="}
STAT_LABELS = (("speed", "Speed"), ("stamina", "Stamina"), ("power", "Power"), ("guts", "Guts"), ("wit", "Wit"))
# A course value the admin leaves unknown.
UNKNOWN = {"", "tbd"}


class GuideShareThrottle(GalleryThrottle):
    """The share page and its picture. High, like the gallery's and the
    plan's: Discord and X crawl from a few shared addresses. Counted in the
    "gallery" cache (GalleryThrottle explains why not "default")."""

    scope = "guide_share"


def _site(path=""):
    return f"{settings.FRONTEND_URL}{path}"


def _default_context():
    return {
        "title": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "share_url": _site("/app/pvp"),
        "app_url": _site("/app/pvp"),
        "image_url": _site("/social-card.png"),
        "image_type": "image/png",
        "image_width": 512,
        "image_height": 512,
        "image_alt": "The Uma Musume Carat Calculator umagon mascot.",
        "twitter_card": "summary",
        "open_label": OPEN_LABEL,
    }


def _lookup(ref):
    """(kind, event) for "cm-21", or None when it names no event."""
    match = REF.match(ref)
    if not match:
        return None
    kind, event_id = match.group(1), int(match.group(2))
    event = pvp_gate.EVENT_MODELS[kind].objects.filter(pk=event_id).first()
    return (kind, event) if event else None


def _style(request):
    style = request.GET.get("style", "")
    return style if style in STYLES else ""


def split_event_name(name):
    """("Champions Meeting 21", "Capricorn Cup"), as the PvP page's header
    splits it (splitEventName); ("", name) when it doesn't start that way."""
    match = re.match(r"^((?:Champions Meeting|League of Heroes)\s+\d+)\s+(.+)$", name.strip(), re.IGNORECASE)
    return (match.group(1), match.group(2)) if match else ("", name.strip())


def short_name(kind, event):
    """ "CM 21" / "LoH 4", as the site shortens an event (shortEventName)."""
    return f"CM {event.cm_number}" if kind == "cm" else f"LoH {event.loh_number}"


def _known(value):
    value = (value or "").strip()
    return value if value.lower() not in UNKNOWN else ""


def _course_parts(event):
    """("Chukyo 1200m", ["Sprint", "Turf", "Left"]): the place and length,
    then the rest the admin knows ("TBD" and blanks left out)."""
    length = _known(event.length)
    if length.isdigit():
        length = f"{length}m"
    where = " ".join(part for part in (_known(event.track), length) if part)
    rest = [part for part in (_known(event.distance), _known(event.surface_type), _known(event.direction)) if part]
    return where, rest


def course_line(event):
    """ "Chukyo 1200m · Sprint · Turf · Left", for the picture."""
    where, rest = _course_parts(event)
    return " · ".join(part for part in (where, *rest) if part)


def course_sentence(event):
    """ "Chukyo 1200m Sprint, Turf, Left", for the description."""
    where, rest = _course_parts(event)
    if where and rest:
        return ", ".join([f"{where} {rest[0]}", *rest[1:]])
    return ", ".join(part for part in (where, *rest) if part)


def ranking_steps(text):
    """The guide's style_ranking as ((mark before or "", word, style or None), ...),
    as the frontend's parseStyleRanking reads it."""
    steps, mark = [], ""
    for part in re.split(r"(>>|>=|>|=)", text or ""):
        word = part.strip()
        if word in TEXT_MARKS:
            mark = word
            continue
        if not word:
            continue
        steps.append((mark if steps else "", word, RANKING_WORDS.get(word.lower())))
        mark = ""
    return tuple(steps)


def ranking_sentence(steps):
    """ "Pace ≫ End > Late > Front"."""
    return " ".join(f"{TEXT_MARKS[mark]} {word}" if mark else word for mark, word, _ in steps)


def stat_rows(guide):
    """(the "everyone" row's (label, value) pairs, else the first row's; how
    many other rows there are)."""
    rows = list(guide.stat_configs.all())
    if not rows:
        return (), 0
    row = next((each for each in rows if each.style == "everyone"), rows[0])
    pairs = tuple((label, getattr(row, field).strip()) for field, label in STAT_LABELS if getattr(row, field).strip())
    return pairs, len(rows) - 1


def _day(moment):
    """2026/10/31: the site's date format (formatDate), in UTC."""
    return f"{moment.year}/{moment.month}/{moment.day}"


def _dates(maps, kind, event):
    """ "2026/10/31 through 2026/11/6", the effective dates, or ""."""
    dates = maps[pvp_gate.EVENT_MODELS[kind]].get(event.id) or {}
    start, end = dates.get("start_date"), dates.get("end_date")
    if not start:
        return ""
    return f"{_day(start)} through {_day(end)}" if end else _day(start)


def _published_guide(kind, event):
    return (
        PvpGuide.objects.filter(is_published=True, **{EVENT_FIELD_FOR_KIND[kind]: event.id})
        .prefetch_related("stat_configs")
        .first()
    )


def _facts(kind, event):
    """Everything the page and the picture say, decided once: (open guide or
    None, card)."""
    maps = build_effective_date_maps()
    guide = _published_guide(kind, event)
    if guide is not None and not pvp_gate.guide_is_open(kind, event, maps=maps):
        guide = None
    _, title = split_event_name(event.name)
    art = event.image or None
    if guide is None:
        card = guide_preview.Card(
            strip=short_name(kind, event), headline=title, course=course_line(event),
            when=_dates(maps, kind, event), art=art,
        )
        return None, card
    stats, _ = stat_rows(guide)
    card = guide_preview.Card(
        strip=short_name(kind, event), headline=title, course=course_line(event),
        ranking=ranking_steps(guide.style_ranking), stats=stats, art=art,
    )
    return guide, card


def share_context(kind, event, style):
    guide, card = _facts(kind, event)
    ref = f"{kind}-{event.id}"
    context = _default_context()
    if guide is None:
        title = event.name
        parts = [course_sentence(event), card.when]
    else:
        title = f"{card.headline} PvP guide"
        stats, more = stat_rows(guide)
        stats_text = ", ".join(f"{label} {value}" for label, value in stats)
        if stats_text and more:
            stats_text += f" (and {more} more stat {'row' if more == 1 else 'rows'})"
        parts = [ranking_sentence(card.ranking), stats_text, course_sentence(event)]
    context.update(
        title=title,
        description=" ".join(f"{part}." for part in parts if part),
        share_url=_site(f"/guide/{ref}" + (f"?style={style}" if style else "")),
        app_url=_site(f"/app/pvp?event={ref}" + (f"&style={style}" if style else "")),
        image_url=_site(f"/guide/{ref}/preview.jpg?v={card.version}"),
        image_type="image/jpeg",
        image_width=WIDTH,
        image_height=HEIGHT,
        image_alt=title,
        twitter_card="summary_large_image",
    )
    return context


def share_page(request, ref):
    if not GuideShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    found = _lookup(ref)
    context = share_context(*found, _style(request)) if found else _default_context()
    return render(request, "share/link_preview.html", context)


def share_preview(request, ref):
    """The guide's picture. An address that names no event is a 404: the
    page's own tags already point those at the site's card."""
    if not GuideShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    found = _lookup(ref)
    if not found:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    kind, event = found
    _, card = _facts(kind, event)
    asked = request.GET.get("v")
    if asked and asked != card.version:
        # A picture from before the guide changed: send the crawler to the
        # current one rather than caching it under the old address forever.
        return HttpResponseRedirect(f"/guide/{kind}-{event.id}/preview.jpg?v={card.version}")
    response = HttpResponse(guide_preview.preview_bytes(f"{kind}-{event.id}", card), content_type="image/jpeg")
    response["Cache-Control"] = IMMUTABLE
    return response
