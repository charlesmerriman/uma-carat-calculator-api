"""
A PvP guide as a whole: where it lives on the site, what it still lacks, and
copying one guide's sections into another.

Pure model logic, no admin imports, so the admin (admin_pvp.py), its template
tag (templatetags/pvp_admin.py) and the tests all read one definition of the
tabs and the sections.
"""

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Prefetch

from .models import (
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
    PvpSkillRecommendation,
    PvpUmaRecommendation,
)
from .models.pvp_guide import STYLE_ORDER, PvpStyle

# The tabs of the guide page. "general" is unfold's own name for the tab that
# holds a change form's fieldsets, so the Guide tab borrows it and the five
# style tabs sit beside it. A style tab shows what the site's tab of the same
# name shows; "Everyone" rows (stats, general skills, team comps) are on the
# Guide tab.
GUIDE_TAB = "general"
STYLE_TABS = tuple(
    (style.value, style.label) for style in STYLE_ORDER if style != PvpStyle.EVERYONE
)
TABS = ((GUIDE_TAB, "Guide"), *STYLE_TABS)
TAB_KEYS = frozenset(key for key, _ in TABS)

# The sections "Copy from another guide" offers, in the order the site shows
# them. The key is the related name on PvpGuide, except "overview", which is
# the guide row's own text fields.
SECTIONS = (
    ("overview", "Overview text and style ranking"),
    ("stat_configs", "Recommended stats"),
    ("team_comps", "Team comps"),
    ("uma_recommendations", "Recommended umas"),
    ("style_notes", "Advice per style"),
    ("skill_recommendations", "Recommended skills"),
    ("decks", "Example decks and their cards"),
)
SECTION_KEYS = tuple(key for key, _ in SECTIONS)
OVERVIEW_FIELDS = ("scenario", "style_ranking", "short_description", "meta_analysis")

# The per-style tables, by the name a visitor would use for them.
STYLE_TABLES = (
    ("umas", "uma_recommendations"),
    ("advice", "style_notes"),
    ("skills", "skill_recommendations"),
    ("decks", "decks"),
)


def guide_queryset():
    """The guide with every section prefetched: a fixed number of queries
    however many rows each section has."""
    return PvpGuide.objects.select_related(
        "champions_meeting", "league_of_heroes", "scenario",
    ).prefetch_related(
        "stat_configs",
        Prefetch(
            "uma_recommendations",
            queryset=PvpUmaRecommendation.objects.select_related("uma"),
        ),
        # Layer three of the on_global rule (models/pvp_guide.py): a skill an
        # editor picked that has since lost its description stays in the
        # database but never reaches the site.
        Prefetch(
            "skill_recommendations",
            queryset=PvpSkillRecommendation.objects.filter(
                skill__description__regex=r"\S"
            ).select_related("skill"),
        ),
        "style_notes",
        Prefetch(
            "decks",
            queryset=PvpDeck.objects.prefetch_related(
                Prefetch(
                    "slots",
                    queryset=PvpDeckSlot.objects.select_related("support_card")
                    .order_by("slot", "order", "id"),
                ),
            ),
        ),
        "team_comps",
    )


def event_ref(guide):
    """The "cm-12" / "loh-7" tag the site's PvP page takes in ?event=."""
    if guide.champions_meeting_id:
        return f"cm-{guide.champions_meeting_id}"
    return f"loh-{guide.league_of_heroes_id}"


def site_url(guide):
    """Where the guide shows on the site: a draft for signed-in staff, the
    page everyone sees once published."""
    return f"{settings.FRONTEND_URL}/app/pvp?event={event_ref(guide)}"


def styles_present(guide):
    """{style: {table name, ...}} for every style that has at least one row.

    Reads the related managers, so a queryset that prefetched them pays no
    extra queries (the guide list does); a single guide pays five.
    """
    found = {}
    for name, related in STYLE_TABLES:
        for row in getattr(guide, related).all():
            if row.style != PvpStyle.EVERYONE:
                found.setdefault(row.style, set()).add(name)
    return found


def styles_with_content(guide):
    """The styles that have anything at all, in display order."""
    present = styles_present(guide)
    return [key for key, _ in STYLE_TABS if key in present]


def missing_summary(guide):
    """Short sentences about what the guide does not have yet.

    Shown under the tabs so an editor sees the gaps without opening every
    tab. Skills are not nagged about: many of the sheet's guides have none.
    """
    sentences = []
    if not guide.stat_configs.exists():
        sentences.append("No recommended stats yet.")
    if not (guide.style_ranking or guide.short_description or guide.meta_analysis):
        sentences.append("No overview text yet.")

    present = styles_present(guide)
    empty = [label for key, label in STYLE_TABS if key not in present]
    for key, label in STYLE_TABS:
        tables = present.get(key)
        if not tables:
            continue
        gaps = [name for name in ("umas", "advice", "decks") if name not in tables]
        if gaps:
            sentences.append(f"{label}: no {', no '.join(gaps)}.")
    if empty and len(empty) == len(STYLE_TABS):
        sentences.append("No style has anything yet. Pick a tab and add rows.")
    elif empty:
        sentences.append(f"Nothing yet for {', '.join(empty)}.")
    return sentences


def _next_order(manager):
    current = manager.aggregate(top=Max("order"))["top"]
    return 0 if current is None else current + 1


def _copy_row(row, **changes):
    """A new unsaved row with the same column values, except `changes`."""
    row.pk = None
    row.id = None
    row._state.adding = True  # pylint: disable=protected-access
    for name, value in changes.items():
        setattr(row, name, value)
    return row


@transaction.atomic
def copy_guide(source, target, sections=SECTION_KEYS):  # pylint: disable=too-many-locals,too-many-branches
    """Copy the chosen sections of `source` into `target`, adding to what the
    target already has.

    Rows that would collide with a unique constraint (the same uma for a
    style, the same skill for a style and category, a style that already has
    advice) are skipped, never overwritten.
    Overview text is copied only into blank fields. Copied rows go after the
    target's own, in the source's order. Returns {section: rows copied}.

    This is what lets the next cup's guide start from the last one instead
    of being typed again.
    """
    if source.pk == target.pk:
        raise ValueError("A guide cannot be copied into itself.")
    wanted = set(sections)
    copied = {}

    if "overview" in wanted:
        filled = 0
        for name in OVERVIEW_FIELDS:
            if not getattr(target, name) and getattr(source, name):
                setattr(target, name, getattr(source, name))
                filled += 1
        if filled:
            target.save(update_fields=[*OVERVIEW_FIELDS, "updated_at"])
        copied["overview"] = filled

    # Section -> the columns that make a row unique within a guide (None:
    # any number of rows).
    simple = {
        "stat_configs": None,
        "team_comps": None,
        "uma_recommendations": ("style", "uma_id"),
        "skill_recommendations": ("style", "category", "skill_id"),
    }
    for section, unique in simple.items():
        if section not in wanted:
            continue
        manager = getattr(target, section)
        taken = set(manager.values_list(*unique)) if unique else set()
        order = _next_order(manager)
        count = 0
        for row in getattr(source, section).order_by("order", "id"):
            key = tuple(getattr(row, name) for name in unique) if unique else None
            if unique and key in taken:
                continue
            _copy_row(row, guide=target, order=order).save()
            order += 1
            count += 1
        copied[section] = count

    if "style_notes" in wanted:
        taken = set(target.style_notes.values_list("style", flat=True))
        count = 0
        for row in source.style_notes.order_by("id"):
            if row.style in taken:
                continue
            _copy_row(row, guide=target).save()
            count += 1
        copied["style_notes"] = count

    if "decks" in wanted:
        order = _next_order(target.decks)
        count = 0
        for deck in source.decks.order_by("order", "id").prefetch_related("slots"):
            slots = list(deck.slots.all())
            new_deck = _copy_row(deck, guide=target, order=order)
            new_deck.save()
            for slot in slots:
                _copy_row(slot, deck=new_deck).save()
            order += 1
            count += 1
        copied["decks"] = count

    return copied


def guide_for_copy_choices(exclude_pk=None):
    """Guides an editor may copy from, newest event first, as (pk, label)."""
    rows = (
        PvpGuide.objects.select_related("champions_meeting", "league_of_heroes")
        .exclude(pk=exclude_pk)
        .order_by("-updated_at")
    )
    return [(guide.pk, str(guide)) for guide in rows]


__all__ = [
    "GUIDE_TAB", "STYLE_TABS", "TABS", "TAB_KEYS", "SECTIONS", "SECTION_KEYS",
    "guide_queryset", "event_ref", "site_url", "styles_present", "styles_with_content",
    "missing_summary", "copy_guide", "guide_for_copy_choices",
]
