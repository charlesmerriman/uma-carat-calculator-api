"""
A FAQ question's link: GET /help/<slug>, a tiny HTML page of link-preview
tags that sends people on to /faq#<slug>.

WHY A PAGE OF ITS OWN
---------------------
A question is linked as /faq#<slug>, and the #fragment never leaves the
browser: Discord's and X's crawlers fetch /faq and get the one page's tags,
the same generic blurb for every question (Daptrius's request, board post
1574926713618432050). Only a path can name the question to a server.

WHY /help AND NOT /faq/<slug>
-----------------------------
App Platform's ingress matches path PREFIXES only, and /faq itself is a
prerendered page on the static site (dist/faq.html). A /faq rule sending
/faq/<slug> here would take the FAQ page with it. An ingress rule in
backend/.do/app.yaml sends /help to this app; the site has no route of
its own under it (checked 2026-11-25). Each question's "Copy link" on the FAQ page copies this
address; an older /faq#<slug> link still works, with the plain preview.

WHAT THE PREVIEW SAYS
---------------------
The question as the title and the start of its answer as the description,
the markdown taken out and cut at a word. Both are the team's own words from
the admin (Site content -> FAQ), already public on /faq, passed through
as written. The picture is the site's card.

A slug that names no question gets the FAQ page's own tags, a 200 like
/post, /plan and /guide (owner, 2026-11-25), and a person is still sent on
to the FAQ.

Opening this page creates and counts nothing: crawlers fetch it on every paste.
"""

import re

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

from calculatorapi.models import FaqItem
from calculatorapi.views.gallery import GalleryThrottle
from calculatorapi.views.gallery_share import SITE_NAME

OPEN_LABEL = "Read this answer on Uma Musume Carat Calculator"
# The FAQ page's own description (frontend components/info/Faq.tsx), for the
# tags of a slug that names no question.
FAQ_DESCRIPTION = (
    "How the Uma Musume carat calculator works, where its numbers come from, whether you "
    "need an account, and what it does with your data."
)
# Discord shows about 350 characters of a description before it cuts.
ANSWER_MAX_CHARS = 300

# Markdown to plain text, as much of it as the FAQ uses (react-markdown with
# GFM, plus the guide's ==highlight==; MarkdownContent.tsx). Order matters:
# images before links (an image is a link with a "!"), links before emphasis.
_MARKDOWN = (
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),     # ![alt](src) -> alt
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),      # [text](href) -> text
    (re.compile(r"`([^`]*)`"), r"\1"),                  # `code` -> code
    (re.compile(r"(\*\*|__|==|~~)(.+?)\1"), r"\2"),     # **bold**, __bold__, ==mark==, ~~strike~~
    (re.compile(r"(?<![\w*])[*_]([^*_\n]+)[*_](?![\w*])"), r"\1"),  # *em*, _em_
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE), ""),            # # Heading
    (re.compile(r"^\s{0,3}>\s?", re.MULTILINE), ""),                 # > quote
    (re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE), ""),     # - item, 1. item
)


class FaqShareThrottle(GalleryThrottle):
    """The share page. High, like the other share pages: Discord and X crawl
    from a few shared addresses. Counted in the "gallery" cache
    (GalleryThrottle explains why not "default")."""

    scope = "faq_share"


def _site(path=""):
    return f"{settings.FRONTEND_URL}{path}"


def plain_text(markdown):
    """The answer as one line of plain text: the marks taken out, the
    paragraphs and list items run together with spaces."""
    text = markdown or ""
    for pattern, replacement in _MARKDOWN:
        text = pattern.sub(replacement, text)
    return " ".join(text.split())


def excerpt(text, limit=ANSWER_MAX_CHARS):
    """`text` whole when it fits, else cut at the last word inside `limit`
    with an ellipsis, as a plan's notes are (views/plan_share.py)."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ;,.:")
    return f"{cut}…"


def _default_context():
    return {
        "title": f"FAQ | {SITE_NAME}",
        "description": FAQ_DESCRIPTION,
        "share_url": _site("/faq"),
        "app_url": _site("/faq"),
        "image_url": _site("/social-card.png"),
        "image_type": "image/png",
        "image_width": 512,
        "image_height": 512,
        "image_alt": "The Uma Musume Carat Calculator umagon mascot.",
        "twitter_card": "summary",
        "open_label": OPEN_LABEL,
    }


def share_context(item):
    context = _default_context()
    context.update(
        title=item.question,
        description=excerpt(plain_text(item.answer)) or FAQ_DESCRIPTION,
        share_url=_site(f"/help/{item.slug}"),
        app_url=_site(f"/faq#{item.slug}"),
    )
    return context


def share_page(request, slug):
    """The tags, or for a slug naming no question the FAQ's own (a 200, so
    an old link still unfurls as the FAQ and still opens it)."""
    if not FaqShareThrottle().allow_request(request, None):
        return HttpResponse("Too many requests", status=429, content_type="text/plain")
    item = FaqItem.objects.filter(slug=slug).first()
    context = share_context(item) if item is not None else _default_context()
    return render(request, "share/link_preview.html", context)
