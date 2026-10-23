"""
GET /pvp-guides/<kind>/<event_id>: one event's PvP guide, every section.

Public and read-only; editors write guides in the admin. `kind` is "cm" or
"loh", the same short tags the frontend's ?event=cm-12 links use.

NOT CACHED, on the /umas and /site-content reasoning: one guide row plus a
fixed number of prefetches (a test pins the count), tens of kilobytes, and
fetched when someone opens the PvP page or follows a Timeline link, never on
/app load. If it ever runs hot, R2's catalogue cache module is the shape to
copy. No throttle: editorial content, nothing about a person.

DRAFTS. An unpublished guide is a 404 with the same body as an unknown event,
so the response never says whether a draft exists. A signed-in editor (staff:
pvp_guide_editing.can_edit) gets the draft,
flagged `is_published: false`, so they can preview it on the site. The editor
itself reads and writes guides through views/pvp_guide_editor.py.

Umas, cards and skills are embedded as small objects rather than ids: the
client's banner data only knows cards that were on a banner, and skills are not
on the client at all yet.
"""

from django.db.models import Count, Exists, OuterRef
from django.db.models.functions import Lower
from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import pvp_guide_editing
from calculatorapi.pvp_guides import guide_queryset
from calculatorapi.models import (
    GalleryComment,
    ChampionsMeeting,
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
    PvpSkillRecommendation,
    PvpStatConfig,
    PvpStyleNote,
    PvpTeamComp,
    PvpUmaRecommendation,
    Scenario,
    Skill,
    SupportCard,
    Uma,
    UmaSkill,
    UmaSkillSource,
)
from calculatorapi.models.pvp_guide import STYLE_RANK, find_skill_mentions
from calculatorapi.models.skill import skill_families

# The URL's kind -> the guide's FK to that event.
EVENT_FIELD_FOR_KIND = {
    "cm": "champions_meeting_id",
    "loh": "league_of_heroes_id",
}

NOT_FOUND = {"error": "Guide not found"}


def annotate_has_guide(queryset):
    """Add `has_guide` to a ChampionsMeeting or LeagueOfHeroes queryset: does
    the event have a PUBLISHED guide? Drives the Timeline card's "PvP guide"
    link and the PvP page's dropdown star. A draft counts as no guide.

    One EXISTS subquery, so it costs no extra round trip. The race serializers
    read it with getattr(..., False), so an unannotated queryset says False
    rather than raising.
    """
    field = "champions_meeting" if queryset.model is ChampionsMeeting else "league_of_heroes"
    published = PvpGuide.objects.filter(is_published=True, **{field: OuterRef("pk")})
    return queryset.annotate(has_guide=Exists(published))


# ── Embedded catalogue objects ───────────────────────────────────────────────

class _ScenarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scenario
        fields = ("id", "name", "image")


class _UmaSerializer(serializers.ModelSerializer):
    # The borderless cut (Uma.portrait): the site draws every uma inside a
    # frame of its own (frontend FramedUma). `image` stays the bordered art.
    portrait = serializers.ImageField(read_only=True)

    class Meta:
        model = Uma
        fields = ("id", "name", "image", "portrait")


class _SupportCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportCard
        fields = ("id", "name", "image", "card_type", "rarity")


class _SkillVersionSerializer(serializers.ModelSerializer):
    """Another version of a recommended skill: its white, gold or evolved
    form. `version` is the label a chip shows ("○ white", "◎ white",
    "◎ gold", "evolved"); `tier` is the game's group_rate behind it. The
    colour is `rarity`'s: a ○/◎ pair's ◎ is tier 2 and its gold tier 3."""

    version = serializers.CharField(source="version_label")
    is_evolved = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = ("id", "name", "description", "image", "rarity", "tier", "version", "is_evolved")

    def get_is_evolved(self, obj):
        return obj.evolves_from_id is not None


class _SkillSerializer(serializers.ModelSerializer):
    # "Lead the Pack (Maruzensky)": the outfit whose unique this skill is, or
    # null. The sheet writes acceleration skills this way. Read from a dict the
    # view builds in one query (context["unique_of"]), not per skill.
    unique_of = serializers.SerializerMethodField()
    # The skill's own version (label, tier, evolved flag) and its other
    # versions (white, gold, evolved) from models.skill.skill_families, one
    # query for the guide (context["versions"]). A guide names ONE version per
    # row; the client shows the rest beside it. The team builder counts a
    # source that teaches the pick or a version that covers it: a card that
    # teaches the gold teaches the white too (owner, 2026-10-14), never the
    # reverse, and the ○ and ◎ of one white count as one (owner, 2026-10-16).
    version = serializers.CharField(source="version_label")
    is_evolved = serializers.SerializerMethodField()
    versions = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = (
            "id", "name", "description", "image", "rarity", "unique_of",
            "version", "tier", "is_evolved", "versions",
        )

    def get_unique_of(self, obj):
        return self.context["unique_of"].get(obj.id)

    def get_is_evolved(self, obj):
        return obj.evolves_from_id is not None

    def get_versions(self, obj):
        rows = self.context.get("versions", {}).get(obj.id, [])
        return _SkillVersionSerializer(rows, many=True, context=self.context).data


# ── The guide's sections ─────────────────────────────────────────────────────

class _StatConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PvpStatConfig
        fields = ("style", "label", "speed", "stamina", "power", "guts", "wit", "footnote")


class _UmaRecommendationSerializer(serializers.ModelSerializer):
    uma = _UmaSerializer(read_only=True)

    class Meta:
        model = PvpUmaRecommendation
        fields = ("style", "stars", "role", "note", "uma")


class _SkillRecommendationSerializer(serializers.ModelSerializer):
    skill = _SkillSerializer(read_only=True)

    class Meta:
        model = PvpSkillRecommendation
        fields = ("style", "category", "tier", "order", "note", "skill")


class _StyleNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PvpStyleNote
        fields = ("style", "body")


class _DeckSlotSerializer(serializers.ModelSerializer):
    support_card = _SupportCardSerializer(read_only=True)

    class Meta:
        model = PvpDeckSlot
        fields = ("slot", "order", "is_borrow", "note", "support_card")


class _DeckSerializer(serializers.ModelSerializer):
    # Arrive in (slot, order) through the prefetch's queryset ordering.
    slots = _DeckSlotSerializer(many=True, read_only=True)

    class Meta:
        model = PvpDeck
        fields = ("id", "style", "label", "note", "is_highlighted", "slots")


class _TeamCompSerializer(serializers.ModelSerializer):
    class Meta:
        model = PvpTeamComp
        fields = ("text", "note")


# ── Ordering ─────────────────────────────────────────────────────────────────
# By style in STYLE_ORDER (Everyone, Runaway, Front, Pace, Late, End), done in
# Python because the database would sort the style strings alphabetically.
# Within a style each section has its own rule.

def _by_style(row):
    return STYLE_RANK.get(row.style, len(STYLE_RANK))


def _stat_key(row):
    return (_by_style(row), row.order, row.id)


def _uma_key(row):
    # Most stars first; the editor's order breaks ties, then the name.
    return (_by_style(row), -row.stars, row.order, row.uma.name)


def _skill_key(row):
    # Tier 0 first, untiered rows last.
    return (_by_style(row), row.tier is None, row.tier or 0, row.order, row.id)


def _ordered_key(row):
    return (_by_style(row), row.order, row.id)


def _unique_of(skill_ids):
    """{skill id: outfit name} for the skills that are some outfit's unique.

    One query for the whole guide. If two outfits ever share a unique, the
    lower uma id wins, so the answer is stable between requests.
    """
    rows = (
        # Outfits on global only: a JP outfit's unique stays unnamed on the
        # site until the site labels JP content (skills-plan-3.md, step 4).
        UmaSkill.objects.filter(
            skill_id__in=skill_ids, source=UmaSkillSource.UNIQUE, uma__is_on_global=True,
        )
        .select_related("uma")
        .order_by("uma_id")
    )
    owners = {}
    for row in rows:
        owners.setdefault(row.skill_id, row.uma.name)
    return owners


def _guide_texts(guide):
    """Every editor-written text in the guide: where [[skill]] mentions live."""
    yield guide.short_description
    yield guide.meta_analysis
    for row in guide.stat_configs.all():
        yield row.footnote
    for row in guide.uma_recommendations.all():
        yield row.note
    for row in guide.skill_recommendations.all():
        yield row.note
    for row in guide.style_notes.all():
        yield row.body
    for deck in guide.decks.all():
        yield deck.note
        for slot in deck.slots.all():
            yield slot.note
    for row in guide.team_comps.all():
        yield row.text
        yield row.note


def _mentioned_skills(guide):
    """The skills the guide's text mentions as [[name]], keyed by lower-case name.

    One query, matched ignoring case against skills on global only (the same
    rule the admin's save check uses), so a mention that has since gone stale
    simply drops out and the client renders its name as plain text.
    """
    names = {name.lower() for text in _guide_texts(guide) for name in find_skill_mentions(text)}
    if not names:
        return []
    return list(
        Skill.objects.on_global()
        .annotate(lower_name=Lower("name"))
        .filter(lower_name__in=names)
        .order_by("game_id")
    )


def serialize_guide(guide, request=None):
    """The response body for one guide. `request` makes image URLs absolute."""
    skill_rows = sorted(guide.skill_recommendations.all(), key=_skill_key)
    mentioned = _mentioned_skills(guide)
    skills = [row.skill for row in skill_rows] + mentioned
    context = {
        "request": request,
        "unique_of": _unique_of([skill.id for skill in skills]),
        "versions": skill_families(skills),
    }
    # The first skill of a name wins (lowest game id), so a duplicate name in
    # the game data resolves the same way on every request.
    mentions = {}
    for skill in mentioned:
        mentions.setdefault(skill.lower_name, _SkillSerializer(skill, context=context).data)

    def many(serializer, rows):
        return serializer(rows, many=True, context=context).data

    event_id = guide.champions_meeting_id or guide.league_of_heroes_id
    return {
        "event_type": guide.event_type,
        "event": event_id,
        "is_published": guide.is_published,
        "updated_at": serializers.DateTimeField().to_representation(guide.updated_at),
        "scenario": (
            _ScenarioSerializer(guide.scenario, context=context).data
            if guide.scenario_id else None
        ),
        "style_ranking": guide.style_ranking,
        "short_description": guide.short_description,
        "meta_analysis": guide.meta_analysis,
        "team_comps": many(_TeamCompSerializer, guide.team_comps.all()),
        "stat_configs": many(
            _StatConfigSerializer, sorted(guide.stat_configs.all(), key=_stat_key)
        ),
        "uma_recommendations": many(
            _UmaRecommendationSerializer,
            sorted(guide.uma_recommendations.all(), key=_uma_key),
        ),
        "skill_recommendations": many(_SkillRecommendationSerializer, skill_rows),
        "style_notes": many(
            _StyleNoteSerializer, sorted(guide.style_notes.all(), key=_by_style)
        ),
        "decks": many(_DeckSerializer, sorted(guide.decks.all(), key=_ordered_key)),
        # {lower-case name: skill} for every [[name]] in the guide's text.
        "skill_mentions": mentions,
        # {section: visible comments}, "" for the whole guide, so the tabs can
        # show counts without fetching every thread (views/gallery.py).
        "comment_counts": _comment_counts(guide),
    }


def _comment_counts(guide):
    """Visible comments per section: not hidden, not "[deleted]", not by a
    blocked account, the rule a post's comment count keeps."""
    rows = (
        GalleryComment.objects.filter(guide=guide, is_hidden=False, is_deleted=False)
        .exclude(author__publishing_blocked=True)
        .values("section").annotate(n=Count("id")).values_list("section", "n")
    )
    return dict(rows)


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def pvp_guide_detail(request, kind, event_id):
    field = EVENT_FIELD_FOR_KIND.get(kind)
    if field is None:
        return Response(NOT_FOUND, status=404)

    guide = guide_queryset().filter(**{field: event_id}).first()
    # A draft answers exactly like a missing guide unless the caller may edit
    # it (staff: pvp_guide_editing.can_edit).
    if guide is None or (not guide.is_published and not pvp_guide_editing.can_edit(request.user, guide)):
        return Response(NOT_FOUND, status=404)

    return Response(serialize_guide(guide, request))
