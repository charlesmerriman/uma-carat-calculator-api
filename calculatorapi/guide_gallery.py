"""
Community guides: players' own PvP guides, the third kind of gallery post
(notes/community-guides-plan.md).

The gallery's shared layer (gallery.py, models/gallery.py) already handles any
post: votes, comments, reports, views, moderation, erasure. This module holds
what only a guide has: who may use it, the working copy (its checks, its caps,
where it starts), posting it, the event's list, and drawing a posted guide in
the reader's own shape.

WHO, FOR NOW: EARLY ACCESS (the owner, 2026-11-27: "Patreon-gated feature, at
least at the beginning"; decision 1: everyone with early access reads AND
writes). Two functions, so reading can open to everyone on a later day while
writing stays with supporters, with no second code path added that day:

    can_use(user)    read the list and the guides, vote, comment, report
    can_write(user)  start, save and post a guide of one's own

Every route that reaches a guide post asks gallery_access.can_open, which
asks can_use for this kind; the page never decides alone.

NOT pvp_guide_editing.can_edit. That function decides who edits the SITE's
guides (every staff account and nobody else, the owner, 2026-10-21) and also
opens Private guides and the Edit guide tab. A player's guide is that
player's own data, like a team or a plan, and becomes public only by posting,
through the gallery's rules (opt-in, filters, reports, erasure).

THE DOCUMENT
------------
A working copy holds the site editor's document (pvp_guide_document.py), its
CONTENT keys only: no editor notes, no "under construction" banner, no
visibility. It is checked on every save the way a site guide is (shape,
catalogue ids, duplicates, deck rules, [[mentions]]) plus the caps below, and
never filtered: it is private until posted. Posting runs EVERY word of it
through the gallery's text filters (links refused), naming the field at
fault by its document path so the editor shows it beside the box.

THE READER'S SHAPE, FROM THE SERVER
-----------------------------------
A posted guide is drawn by the same component as the site's guide, from the
same JSON the site's guide route sends, built by the same function
(views/pvp_guide.serialize_guide) fed an in-memory stand-in for the guide
(_Standin). So a skill's other versions, its owner, its JP-only tag, its
plain-English text, where to get it and the UmaTL credit all come out exactly
as they do on the site's guide, decided per reader (sees_skills_preview).
The alternative, rebuilding that shape in the browser from the catalogue,
would have dropped the UmaTL flag (an invariant) and every JP-only skill.
"""

import copy
import json
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from calculatorapi import (
    benefits, gallery, gallery_moderation, gallery_ranking, guide_access, moderation_cards,
    pvp_guide_document, pvp_guide_editing,
)
from calculatorapi.models import (
    COMMUNITY_GUIDE_TITLE_MAX_LENGTH,
    ChampionsMeeting,
    CustomUser,
    GalleryPost,
    GalleryPostKind,
    PvpCommunityGuide,
    Scenario,
    Skill,
    SupportCard,
    Uma,
)
from calculatorapi.community_guide_document import CONTENT_KEYS, content_of, empty_content, public_texts
from calculatorapi.pvp_guides import guide_queryset

REFUSED_TEXT = "Community guides are open to supporters first."

# Guides one person may hold for one event (the owner, 2026-11-27, decision 4:
# several, capped at five). Counted on the working copies, each of which has
# at most one post, so it caps the posts too.
COMMUNITY_GUIDE_CAP = 5
CAP_REFUSED = f"You can have {COMMUNITY_GUIDE_CAP} guides for one event. Delete one to write another."

# Caps, from the largest guide on the site (CM 22, 2026-11-27: 26 umas, 112
# skills, 16 decks of 7.5 rows on average, 7 team comps, 2 stat rows, 369
# characters of meta analysis, 29.6 KB as a document) with room to spare. The
# site's own guides keep none. The last one exists because the rows alone can
# pass it (250 skill rows are ~22 KB, 30 decks of 24 rows ~79 KB): a guide
# cannot sit at every cap at once.
ROW_CAPS = {
    "uma_recommendations": 60,
    "skill_recommendations": 250,
    "decks": 30,
    "team_comps": 15,
    "stat_configs": 15,
}
DECK_ROW_CAP = 24
TEXT_CAPS = {"meta_analysis": 6000, "short_description": 500}
STYLE_NOTE_CAP = 4000
DOCUMENT_BYTES_CAP = 100_000
TOO_LONG = "This guide is too long to save. Take something out."

# How many umas a list row draws, most stars first.
LEAD_UMAS = 3
# The most guides one event's list sends at once.
FEED_LIMIT_MAX = 50
FEED_SORTS = (gallery_ranking.SORT_TOP, gallery_ranking.SORT_NEW)

# A refusal shown to the writer: gallery.GalleryRefused (a sentence, a field,
# a status), so the views answer every refusal in one shape.
GuideRefused = gallery.GalleryRefused


def can_use(user):
    """Whether `user` may read community guides right now."""
    return benefits.early_access(user)


def can_write(user):
    """Whether `user` may start, save or post a guide of their own right now.
    A blocked account keeps reading but cannot write; a lapsed supporter keeps
    their guides and posts (a lapse never deletes data) and writes again when
    the pledge comes back."""
    return can_use(user) and not user.publishing_blocked


def _check_may_write(user):
    if not can_write(user):
        raise GuideRefused(REFUSED_TEXT, status=403)


# ── The document ─────────────────────────────────────────────────────────────


def cap_errors(data):
    """[{path, message}] for every cap a validated document passes."""
    found = []
    for section, cap in ROW_CAPS.items():
        if len(data[section]) > cap:
            found.append({"path": section, "message": f"A guide holds up to {cap} of these."})
    for index, deck in enumerate(data["decks"]):
        if len(deck["slots"]) > DECK_ROW_CAP:
            found.append({"path": f"decks[{index}]", "message": f"A deck holds up to {DECK_ROW_CAP} cards and notes."})
    for field, cap in TEXT_CAPS.items():
        if len(data[field]) > cap:
            found.append({"path": field, "message": f"Keep this to {cap:,} characters."})
    for index, note in enumerate(data["style_notes"]):
        if len(note["body"]) > STYLE_NOTE_CAP:
            found.append({
                "path": f"style_notes[{index}].body", "message": f"Keep this to {STYLE_NOTE_CAP:,} characters.",
            })
    if not found and len(json.dumps(content_of(data))) > DOCUMENT_BYTES_CAP:
        found.append({"path": "", "message": TOO_LONG})
    return found


def validate(body):
    """(content, []) for a body that may be saved, or (None, [{path, message}]).

    The site editor's own checks (pvp_guide_document: DocumentInput and
    errors()), with the private parts set aside: a player's guide has no
    editor notes and no banner, whatever the body says, then the caps."""
    body = {key: value for key, value in dict(body).items() if key in CONTENT_KEYS}
    payload = pvp_guide_document.DocumentInput(
        data={**body, "admin_comments": "", "under_construction": False}
    )
    if not payload.is_valid():
        return None, pvp_guide_document.flatten_errors(payload.errors)
    data = payload.validated_data
    problems = cap_errors(data) or pvp_guide_document.errors(data)
    if problems:
        return None, problems
    return content_of(data), []


# ── Working copies ───────────────────────────────────────────────────────────


def resolve_event(kind, event_id):
    """(the event, its field on a guide) or GuideRefused."""
    try:
        return pvp_guide_editing.resolve_event(kind, event_id)
    except pvp_guide_editing.GuideRefused as refusal:
        raise GuideRefused(str(refusal)) from refusal


def owned_guide(user, guide_id):
    """The caller's own working copy, or None (somebody else's reads the same)."""
    return (
        PvpCommunityGuide.objects.filter(pk=guide_id, author=user)
        .select_related("champions_meeting", "league_of_heroes").first()
    )


def mine(user, kind=None, event_id=None):
    """The caller's working copies, for one event or all, oldest first, with
    their posts."""
    guides = PvpCommunityGuide.objects.filter(author=user).select_related(
        "champions_meeting", "league_of_heroes",
    ).prefetch_related("gallery_posts")
    if kind is not None:
        _, field = pvp_guide_editing.EVENT_FOR_KIND[kind]
        guides = guides.filter(**{f"{field}_id": event_id})
    return list(guides)


def _starting_content(user, event, field, start):
    """Where a guide starts: empty, the event's site guide (Henry, 2026-11-27:
    "allow people to copy from my guides like I can"), or another of the
    writer's own guides for the same event."""
    if start == "empty":
        return empty_content()
    if start == "official":
        guide = guide_queryset().filter(**{field: event}).first()
        if guide is None or not guide_access.can_read(user, guide):
            raise GuideRefused("This event has no site guide to start from.")
        return content_of(pvp_guide_document.serialize(guide))
    source = PvpCommunityGuide.objects.filter(pk=start, author=user, **{field: event}).first()
    if source is None:
        raise GuideRefused("That isn't one of your guides for this event.")
    return content_of(source.document)


def _clean_title(title):
    title = (title or "").strip()
    if len(title) > COMMUNITY_GUIDE_TITLE_MAX_LENGTH:
        raise GuideRefused(f"Keep the title to {COMMUNITY_GUIDE_TITLE_MAX_LENGTH} characters.", field="title")
    return title


def create_guide(user, *, kind, event_id, title="", start="empty"):
    """A new working copy for an event, at most COMMUNITY_GUIDE_CAP per person
    per event. The count is taken under a lock on the person's own row, so
    two quick clicks cannot both pass it."""
    _check_may_write(user)
    event, field = resolve_event(kind, event_id)
    title = _clean_title(title)
    content = _starting_content(user, event, field, start)
    with transaction.atomic():
        CustomUser.objects.select_for_update().filter(pk=user.pk).first()
        if PvpCommunityGuide.objects.filter(author=user, **{field: event}).count() >= COMMUNITY_GUIDE_CAP:
            raise GuideRefused(CAP_REFUSED)
        return PvpCommunityGuide.objects.create(author=user, **{field: event}, title=title, document=content)


class Stale(Exception):
    """A save or start-over from an older version than the stored one."""

    def __init__(self, guide):
        super().__init__("This guide was saved somewhere else since you opened it.")
        self.guide = guide


def _write(guide, version, *, content, title=None):
    """Store a new document (and title), refusing a stale version. Locked for
    the compare-and-write, as the site editor's save is."""
    with transaction.atomic():
        current = PvpCommunityGuide.objects.select_for_update().get(pk=guide.pk)
        if version != current.version:
            raise Stale(current)
        current.document = content
        if title is not None:
            current.title = title
        current.version += 1
        current.save()
    return current


def save(guide, body):
    """A save from the editor: {version, title?, ...document}. Returns the
    stored guide; GuideRefused with `errors` for a document the checks refuse,
    Stale for an older version."""
    _check_may_write(guide.author)
    version = body.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise GuideRefused("A save names the version it started from.", field="version")
    title = _clean_title(body["title"]) if "title" in body else None
    content, problems = validate(body)
    if problems:
        refusal = GuideRefused(problems[0]["message"])
        refusal.errors = problems
        raise refusal
    return _write(guide, version, content=content, title=title)


def start_over(guide, version, start):
    """Replace the working copy's document with a starting point (the More
    menu). Its post, if any, is untouched until the writer posts again."""
    _check_may_write(guide.author)
    field = "champions_meeting" if guide.champions_meeting_id else "league_of_heroes"
    if start in ("empty", guide.pk):
        raise GuideRefused("Pick the site's guide or another of your guides.")
    content = _starting_content(guide.author, guide.event, field, start)
    return _write(guide, version, content=content)


def delete_guide(guide):
    """Delete a working copy. Refused while its post is up, so a live guide
    post always has the working copy it came from."""
    if guide.gallery_posts.exists():
        raise GuideRefused("Take the post down first.")
    guide.delete()


def post_of(guide):
    """The guide's post, or None. Uses a prefetch when there is one."""
    return next(iter(guide.gallery_posts.all()), None)


def document_payload(guide):
    """A working copy as the editor reads it: the site editor's document
    shape (so its components need no second type), plus what only a player's
    guide has: its title and its post."""
    content = content_of(guide.document)
    post = post_of(guide)
    return {
        "id": guide.id,
        "version": guide.version,
        "event_type": "champions_meeting" if guide.champions_meeting_id else "league_of_heroes",
        "event": guide.event_id,
        "kind": guide.kind,
        "title": guide.title,
        # The editor's shape has these; a working copy is always private and
        # has neither a banner nor editor notes.
        "visibility": "private",
        "tools_public": False,
        "under_construction": False,
        "admin_comments": "",
        "updated_at": serializers.DateTimeField().to_representation(guide.updated_at),
        **content,
        "post": post.public_id if post else None,
        "changed_since_posted": bool(post) and (post.guide_snapshot != content or post.caption != guide.title),
    }


def list_row(guide):
    """A working copy in "My guides": what it is, and whether it is posted."""
    post = post_of(guide)
    content = content_of(guide.document)
    return {
        "id": guide.id,
        "kind": guide.kind,
        "event": guide.event_id,
        "title": guide.title,
        "version": guide.version,
        "updated_at": guide.updated_at,
        "post": post.public_id if post else None,
        "changed_since_posted": bool(post) and (post.guide_snapshot != content or post.caption != guide.title),
    }


# ── Posting ──────────────────────────────────────────────────────────────────


def publish(user, guide, *, confirm_public_identity=False):
    """Post the working copy, or update its post. Returns (post, created).

    The gallery's rules: the person's own agreement to show their name,
    picture and tier (`confirm_public_identity` literally True), a display
    name, not blocked. Then every word through the text filters, a refusal
    naming its document path. The post is a frozen copy: an update keeps its
    link, votes and comments; it reads "Edited" and older comments read "On
    an earlier version" when the guide or its title changed (the gallery's
    re-post rule). A hidden post stays hidden: re-posting is no way out of
    moderation.
    """
    if confirm_public_identity is not True:
        raise GuideRefused("Tick the box to agree to what will show with your guide.")
    gallery.check_may_publish(user)
    _check_may_write(user)
    title = gallery.clean_text(guide.title, field="title", label="title", limit=COMMUNITY_GUIDE_TITLE_MAX_LENGTH)
    if not title:
        raise GuideRefused("Give your guide a title first.", field="title")
    content = content_of(guide.document)
    for path, text in public_texts(content):
        try:
            gallery_moderation.check_text(text)
        except gallery_moderation.TextRefused as refusal:
            raise GuideRefused(str(refusal), field=path) from refusal
    event = (
        {"champions_meeting_id": guide.champions_meeting_id}
        if guide.champions_meeting_id
        else {"league_of_heroes_id": guide.league_of_heroes_id}
    )
    now = timezone.now()
    with transaction.atomic():
        post = GalleryPost.objects.select_for_update().filter(source_guide=guide).first()
        if post is None:
            post = GalleryPost.objects.create(
                public_id=gallery.generate_unique_post_id(),
                author=user,
                kind=GalleryPostKind.PVP_GUIDE,
                caption=title,
                guide_snapshot=content,
                source_guide=guide,
                published_at=now,
                content_updated_at=now,
                **event,
            )
            return post, True
        fields = {"caption": title, "guide_snapshot": content}
        if post.caption != title or post.guide_snapshot != content:
            fields["content_updated_at"] = now
        for name, value in fields.items():
            setattr(post, name, value)
        post.save(update_fields=list(fields))
    moderation_cards.refresh(post)
    return post, False


# ── Reading ──────────────────────────────────────────────────────────────────


def feed(event_filter, sort):
    """One event's visible guide posts in `sort` order (Top or New), with
    `ups` and `downs` for serialize_posts."""
    posts = gallery.visible_posts().filter(kind=GalleryPostKind.PVP_GUIDE, **event_filter).select_related(
        "author", "champions_meeting", "league_of_heroes",
    )
    return gallery_ranking.ranked(posts, sort)


def known_umas(snapshots):
    """The uma ids in these guides that are still in the catalogue: one query
    for a page of rows."""
    ids = {row["uma"] for snapshot in snapshots for row in snapshot.get("uma_recommendations", [])}
    return set(Uma.objects.filter(id__in=ids).values_list("id", flat=True))


def lead_umas(snapshot, known):
    """The umas a list row draws: most stars first, then the writer's order,
    each uma once, only those still in the catalogue."""
    rows = sorted(
        enumerate(snapshot.get("uma_recommendations", [])), key=lambda item: (-item[1]["stars"], item[0])
    )
    lead = []
    for _, row in rows:
        if row["uma"] in known and row["uma"] not in lead:
            lead.append(row["uma"])
        if len(lead) == LEAD_UMAS:
            break
    return lead


class _Rows(list):
    """A list that answers .all(), so serialize_guide reads it like a related manager."""

    def all(self):
        return self


def _rows(items):
    """Rows with the position as `order` and `id`, unless a row has its own:
    a deck slot's `order` says what it is (0 the card, 1 and up an "or"
    alternative), and a deck keeps its id."""
    return _Rows(
        SimpleNamespace(**{"order": index, "id": index + 1, **item}) for index, item in enumerate(items)
    )


def _standin(content, event, updated_at):
    """An in-memory guide serialize_guide can draw: the document's rows with
    the catalogue objects they name, a fixed number of queries. A row whose
    uma or skill has left the catalogue (or that a guide may no longer show)
    drops out, as a stale row does on the site; a card that left becomes an
    empty slot, which drops out too unless it carries a note."""
    umas = Uma.objects.in_bulk({row["uma"] for row in content["uma_recommendations"]})
    skills = Skill.objects.shown().in_bulk({row["skill"] for row in content["skill_recommendations"]})
    card_ids = {slot["support_card"] for deck in content["decks"] for slot in deck["slots"]} - {None}
    cards = SupportCard.objects.in_bulk(card_ids)
    scenario = Scenario.objects.filter(pk=content["scenario"]).first() if content["scenario"] else None
    is_cm = isinstance(event, ChampionsMeeting)

    decks = _Rows()
    for index, deck in enumerate(content["decks"]):
        slots = [
            {**slot, "support_card": cards.get(slot["support_card"])}
            for slot in deck["slots"]
            if cards.get(slot["support_card"]) is not None or slot["note"].strip()
        ]
        decks.append(SimpleNamespace(**{
            "order": index, **deck, "slots": _rows(sorted(slots, key=lambda s: (s["slot"], s["order"]))),
        }))
    return SimpleNamespace(
        champions_meeting_id=event.pk if is_cm else None,
        league_of_heroes_id=None if is_cm else event.pk,
        event_type="champions_meeting" if is_cm else "league_of_heroes",
        # Drawn as a published guide: no "Private" banner over a player's post.
        visibility="public",
        tools_public=False,
        is_out_of_draft=True,
        updated_at=updated_at,
        scenario=scenario,
        scenario_id=scenario.pk if scenario else None,
        style_ranking=content["style_ranking"],
        short_description=content["short_description"],
        meta_analysis=content["meta_analysis"],
        under_construction=False,
        stat_configs=_rows(content["stat_configs"]),
        team_comps=_rows(content["team_comps"]),
        uma_recommendations=_rows(
            {**row, "uma": umas[row["uma"]], "uma_id": row["uma"]}
            for row in content["uma_recommendations"] if row["uma"] in umas
        ),
        skill_recommendations=_rows(
            {**row, "skill": skills[row["skill"]], "skill_id": row["skill"]}
            for row in content["skill_recommendations"] if row["skill"] in skills
        ),
        style_notes=_rows(content["style_notes"]),
        decks=decks,
    )


def render(content, event, updated_at, request):
    """A guide's document in the reader's own shape: exactly what
    GET /pvp-guides/<kind>/<event> sends for the site's guide, decided for
    this request's reader. No comment counts: a guide post has one thread,
    the post's."""
    # Imported here: views/pvp_guide is a view module, and importing it at the
    # top would tie this service's import to the whole view layer.
    from calculatorapi.views.pvp_guide import serialize_guide  # pylint: disable=import-outside-toplevel
    return serialize_guide(_standin(copy.deepcopy(content), event, updated_at), request, comment_counts=False)


def render_post(post, request):
    """A guide post's frozen guide, drawn for this reader."""
    return render(content_of(post.guide_snapshot), post.event, post.content_updated_at, request)


def render_working_copy(guide, request):
    """The writer's own working copy, drawn the way it will read once posted
    (the editor's "View as a reader" and its preview)."""
    return render(content_of(guide.document), guide.event, guide.updated_at, request)
