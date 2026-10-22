"""
A PvP guide as ONE document: the shape the site's editor reads and writes.

The nine tables stay the truth and the admin page keeps editing them row by
row. This module is the other way in: the whole guide as one JSON document,
validated as a whole, written as a whole in one transaction, and recorded as
a whole in PvpGuideRevision. The three halves:

  serialize(guide)        the tables -> the document (catalogue IDS, never the
                          embedded objects GET /pvp-guides sends; the client
                          draws them from /pvp-catalogue)
  DocumentInput, errors() the document -> a list of {path, message}, the path
                          being where in the document the problem is
                          ("decks[2].slots[5].note"), so the editor can mark
                          the field and count it on the tab
  write(guide, data)      the document -> the tables, in one transaction

ORDER IS POSITION. The client never sends `order`; every `order` column is
written from the array index, which is also how the site reads them. The one
exception is a deck slot's `order` (0 = the card, 1+ = an alternative), which
is a fact about the slot, not a sort key.

REPLACE, NOT PATCH. Every child row the document omits is deleted and what it
holds is written (the plan reconcile's rule). Leaf rows carry no ids and are
rewritten. A deck keeps its id when the document names one: nothing else
references deck ids, but a stable id keeps React keys steady and makes two
revisions comparable deck by deck.

NO SIGNAL ON A SAVE. The guide row is written with update(), not save(): the
site autosaves every couple of seconds while someone types, and PvpGuide is
NOT in public_payload_cache._IRRELEVANT_MODELS (its is_published feeds
has_guide), so a save() would drop the whole /calculator-data payload on every
autosave, the 2026-09-23 outage's shape. Publishing, which is the one change
the payload cares about, invalidates on purpose (pvp_guide_editing.publish).

NO full_clean() PER ROW. The admin's lesson (admin.md, "A save validates
what changed"): five queries a row. The checks below cost one query per
catalogue table for the whole document, uniqueness is sets in Python, and
the [[mentions]] of every text are checked in ONE query. The database
constraints stay as the backstop; an IntegrityError past them is a 400 with
one sentence, never a 500.
"""

from collections import Counter

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
    PvpSkillCategory,
    PvpSkillRecommendation,
    PvpStatConfig,
    PvpStyle,
    PvpStyleNote,
    PvpTeamComp,
    PvpUmaRecommendation,
    Scenario,
    Skill,
    SupportCard,
    Uma,
)
from .models.pvp_guide import find_skill_mentions, mention_error, unknown_skill_mentions

# The sections, in the document's order. Keys are the related names on
# PvpGuide, which is also what pvp_guides.SECTIONS and copy_guide use.
SECTIONS = (
    "stat_configs",
    "team_comps",
    "uma_recommendations",
    "skill_recommendations",
    "style_notes",
    "decks",
)

STYLES = [choice for choice, _ in PvpStyle.choices]
ROW_STYLES = [choice for choice in STYLES if choice != PvpStyle.EVERYONE]
CATEGORIES = [choice for choice, _ in PvpSkillCategory.choices]

DECK_SLOTS = 6

# Error copy. Shown beside the field, so written for the editor.
NOT_IN_CATALOGUE = "Pick one from the list."
SKILL_NOT_ON_GLOBAL = "That skill is not on global yet."
SLOT_NEEDS_SOMETHING = 'Pick a card, or write a note like "or any speed".'
ONE_BORROW = "Only one slot can be the borrowed card."
DUPLICATE_SLOT = "Two rows share slot {slot}."


def _max(model, field):
    return model._meta.get_field(field).max_length  # pylint: disable=protected-access


def _text(model, field, **kwargs):
    """A CharField of the model column's own length, blank unless said."""
    kwargs.setdefault("allow_blank", True)
    kwargs.setdefault("required", False)
    kwargs.setdefault("default", "")
    return serializers.CharField(max_length=_max(model, field), **kwargs)


# ── The document, incoming ───────────────────────────────────────────────────

class _StatInput(serializers.Serializer):
    style = serializers.ChoiceField(choices=STYLES)
    label = _text(PvpStatConfig, "label")
    speed = _text(PvpStatConfig, "speed")
    stamina = _text(PvpStatConfig, "stamina")
    power = _text(PvpStatConfig, "power")
    guts = _text(PvpStatConfig, "guts")
    wit = _text(PvpStatConfig, "wit")
    footnote = _text(PvpStatConfig, "footnote")


class _TeamCompInput(serializers.Serializer):
    text = _text(PvpTeamComp, "text", allow_blank=False, required=True, default=serializers.empty)
    note = _text(PvpTeamComp, "note")


class _UmaInput(serializers.Serializer):
    style = serializers.ChoiceField(choices=ROW_STYLES)
    uma = serializers.IntegerField(min_value=1)
    stars = serializers.IntegerField(min_value=1, max_value=5, required=False, default=3)
    role = _text(PvpUmaRecommendation, "role")
    note = _text(PvpUmaRecommendation, "note")


class _SkillInput(serializers.Serializer):
    style = serializers.ChoiceField(choices=STYLES)
    category = serializers.ChoiceField(choices=CATEGORIES)
    tier = serializers.IntegerField(min_value=0, max_value=4, allow_null=True, required=False, default=None)
    skill = serializers.IntegerField(min_value=1)
    note = _text(PvpSkillRecommendation, "note")


class _StyleNoteInput(serializers.Serializer):
    style = serializers.ChoiceField(choices=STYLES)
    body = serializers.CharField(allow_blank=False)


class _SlotInput(serializers.Serializer):
    slot = serializers.IntegerField(min_value=1, max_value=DECK_SLOTS)
    order = serializers.IntegerField(min_value=0, required=False, default=0)
    is_borrow = serializers.BooleanField(required=False, default=False)
    support_card = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    note = _text(PvpDeckSlot, "note")


class _DeckInput(serializers.Serializer):
    id = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    style = serializers.ChoiceField(choices=STYLES)
    label = _text(PvpDeck, "label", allow_blank=False, required=True, default=serializers.empty)
    note = _text(PvpDeck, "note")
    # Optional, so a revision saved before the field existed restores unboxed.
    is_highlighted = serializers.BooleanField(required=False, default=False)
    slots = _SlotInput(many=True)


class DocumentInput(serializers.Serializer):
    """The whole guide. Every section is required, so a client that forgot
    one is told rather than having that section wiped."""
    scenario = serializers.IntegerField(min_value=1, allow_null=True)
    style_ranking = _text(PvpGuide, "style_ranking", required=True, default=serializers.empty)
    short_description = serializers.CharField(allow_blank=True)
    meta_analysis = serializers.CharField(allow_blank=True)
    admin_comments = serializers.CharField(allow_blank=True)
    stat_configs = _StatInput(many=True)
    team_comps = _TeamCompInput(many=True)
    uma_recommendations = _UmaInput(many=True)
    skill_recommendations = _SkillInput(many=True)
    style_notes = _StyleNoteInput(many=True)
    decks = _DeckInput(many=True)


class DocumentPut(DocumentInput):
    """A save: the document plus the version it started from."""
    version = serializers.IntegerField(min_value=0)


# ── Errors as paths ──────────────────────────────────────────────────────────

def flatten_errors(tree, prefix=""):
    """DRF's nested error structure -> [{"path", "message"}], one per message.

    A list of per-row dicts becomes `section[i]`, a field becomes `.field`,
    and `non_field_errors` lands on the parent path.
    """
    found = []
    if isinstance(tree, dict):
        for key, value in tree.items():
            if key == "non_field_errors":
                found.extend(flatten_errors(value, prefix))
            else:
                found.extend(flatten_errors(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(tree, list):
        if tree and all(isinstance(item, (dict, list)) for item in tree):
            for index, item in enumerate(tree):
                if item:
                    found.extend(flatten_errors(item, f"{prefix}[{index}]"))
        else:
            for message in tree:
                found.append({"path": prefix, "message": str(message)})
    else:
        found.append({"path": prefix, "message": str(tree)})
    return found


def _texts(data):
    """Every text an editor wrote, as (path, text), where [[mentions]] live."""
    for field in ("short_description", "meta_analysis", "admin_comments", "style_ranking"):
        yield field, data[field]
    for i, row in enumerate(data["stat_configs"]):
        yield f"stat_configs[{i}].footnote", row["footnote"]
    for i, row in enumerate(data["team_comps"]):
        yield f"team_comps[{i}].text", row["text"]
        yield f"team_comps[{i}].note", row["note"]
    for i, row in enumerate(data["uma_recommendations"]):
        yield f"uma_recommendations[{i}].note", row["note"]
    for i, row in enumerate(data["skill_recommendations"]):
        yield f"skill_recommendations[{i}].note", row["note"]
    for i, row in enumerate(data["style_notes"]):
        yield f"style_notes[{i}].body", row["body"]
    for i, deck in enumerate(data["decks"]):
        yield f"decks[{i}].note", deck["note"]
        for j, slot in enumerate(deck["slots"]):
            yield f"decks[{i}].slots[{j}].note", slot["note"]


def errors(data):  # pylint: disable=too-many-locals
    """The semantic problems of a shape-valid document, as [{path, message}].

    One query per catalogue table for the whole document, one for the
    scenario, one for every [[mention]] in it; everything else is sets.
    """
    found = []

    def add(path, message):
        found.append({"path": path, "message": message})

    if data["scenario"] is not None and not Scenario.objects.filter(pk=data["scenario"]).exists():
        add("scenario", NOT_IN_CATALOGUE)

    # The admin's rule (PvpUmaRecommendation.uma's limit_choices_to): any
    # uma but the "(All)" placeholder. Not Uma.pickable(), which also wants a
    # picture: a guide loaded before an uma's art arrived must still save.
    uma_ids = {row["uma"] for row in data["uma_recommendations"]}
    known_umas = dict(
        Uma.objects.exclude(name__iexact=Uma.ALL_PLACEHOLDER_NAME)
        .filter(id__in=uma_ids).values_list("id", "name")
    )
    seen = set()
    for i, row in enumerate(data["uma_recommendations"]):
        name = known_umas.get(row["uma"])
        if name is None:
            add(f"uma_recommendations[{i}].uma", NOT_IN_CATALOGUE)
            continue
        key = (row["style"], row["uma"])
        if key in seen:
            add(f"uma_recommendations[{i}].uma",
                f"{name} is already in {PvpStyle(row['style']).label}.")
        seen.add(key)

    skill_ids = {row["skill"] for row in data["skill_recommendations"]}
    known_skills = dict(Skill.objects.on_global().filter(id__in=skill_ids).values_list("id", "name"))
    seen = set()
    for i, row in enumerate(data["skill_recommendations"]):
        name = known_skills.get(row["skill"])
        if name is None:
            add(f"skill_recommendations[{i}].skill", SKILL_NOT_ON_GLOBAL)
            continue
        key = (row["style"], row["category"], row["skill"])
        if key in seen:
            add(f"skill_recommendations[{i}].skill",
                f"{name} is already in {PvpStyle(row['style']).label}'s "
                f"{PvpSkillCategory(row['category']).label} list.")
        seen.add(key)

    seen = set()
    for i, row in enumerate(data["style_notes"]):
        if row["style"] in seen:
            add(f"style_notes[{i}].style", f"{PvpStyle(row['style']).label} already has an advice box.")
        seen.add(row["style"])

    _deck_errors(data["decks"], add)

    # [[mentions]]: one query for every name in the document, then each
    # text's own unknown names in its own message (the admin's wording).
    mentions = {path: find_skill_mentions(text) for path, text in _texts(data)}
    unknown = {
        name.casefold()
        for name in unknown_skill_mentions({n for names in mentions.values() for n in names})
    }
    if unknown:
        for path, names in mentions.items():
            bad = [name for name in names if name.casefold() in unknown]
            if bad:
                add(path, mention_error(list(dict.fromkeys(bad))))

    return found


def _deck_errors(decks, add):
    """The deck rules: a known card, a card or a note, one row per slot and
    order, one borrowed SLOT (a borrowed card and its "or" alternative are
    the same borrow, as DeckSlotFormSet.clean counts them)."""
    card_ids = {slot["support_card"] for deck in decks for slot in deck["slots"]} - {None}
    known_cards = set(SupportCard.objects.filter(id__in=card_ids).values_list("id", flat=True))
    for i, deck in enumerate(decks):
        slots_seen = Counter()
        borrowed = set()
        for j, slot in enumerate(deck["slots"]):
            path = f"decks[{i}].slots[{j}]"
            card = slot["support_card"]
            if card is not None and card not in known_cards:
                add(f"{path}.support_card", NOT_IN_CATALOGUE)
            if card is None and not slot["note"].strip():
                add(path, SLOT_NEEDS_SOMETHING)
            slots_seen[(slot["slot"], slot["order"])] += 1
            if slot["is_borrow"]:
                borrowed.add(slot["slot"])
        for (number, _order), count in slots_seen.items():
            if count > 1:
                add(f"decks[{i}]", DUPLICATE_SLOT.format(slot=number))
        if len(borrowed) > 1:
            add(f"decks[{i}]", ONE_BORROW)


# ── The document, outgoing ───────────────────────────────────────────────────

def serialize(guide):
    """The tables -> the document. Reads the related managers, so a guide
    from pvp_guides.guide_queryset() costs no extra queries. Rows come
    out in the order the tables keep them (`order`, then id), which is the
    order the editor shows and the order it will send back."""
    return {
        "id": guide.id,
        "version": guide.version,
        "event_type": guide.event_type,
        "event": guide.champions_meeting_id or guide.league_of_heroes_id,
        "is_published": guide.is_published,
        "updated_at": serializers.DateTimeField().to_representation(guide.updated_at),
        "scenario": guide.scenario_id,
        "style_ranking": guide.style_ranking,
        "short_description": guide.short_description,
        "meta_analysis": guide.meta_analysis,
        "admin_comments": guide.admin_comments,
        "stat_configs": [
            {
                "style": row.style, "label": row.label, "speed": row.speed,
                "stamina": row.stamina, "power": row.power, "guts": row.guts,
                "wit": row.wit, "footnote": row.footnote,
            }
            for row in _ordered(guide.stat_configs.all())
        ],
        "team_comps": [
            {"text": row.text, "note": row.note} for row in _ordered(guide.team_comps.all())
        ],
        "uma_recommendations": [
            {"style": row.style, "uma": row.uma_id, "stars": row.stars, "role": row.role, "note": row.note}
            for row in _ordered(guide.uma_recommendations.all())
        ],
        "skill_recommendations": [
            {
                "style": row.style, "category": row.category, "tier": row.tier,
                "skill": row.skill_id, "note": row.note,
            }
            for row in _ordered(guide.skill_recommendations.all())
        ],
        "style_notes": [
            {"style": row.style, "body": row.body}
            for row in sorted(guide.style_notes.all(), key=lambda row: row.id)
        ],
        "decks": [
            {
                "id": deck.id,
                "style": deck.style,
                "label": deck.label,
                "note": deck.note,
                "is_highlighted": deck.is_highlighted,
                "slots": [
                    {
                        "slot": slot.slot, "order": slot.order, "is_borrow": slot.is_borrow,
                        "support_card": slot.support_card_id, "note": slot.note,
                    }
                    for slot in sorted(deck.slots.all(), key=lambda s: (s.slot, s.order, s.id))
                ],
            }
            for deck in _ordered(guide.decks.all())
        ],
    }


def _ordered(rows):
    return sorted(rows, key=lambda row: (row.order, row.id))


def counts(document):
    """Rows per section, for a revision's list line."""
    return {section: len(document[section]) for section in SECTIONS}


# ── The document -> the tables ───────────────────────────────────────────────

@transaction.atomic
def write(guide, data):
    """Replace every section of `guide` with `data` (a validated document) and
    bump its version. The caller records the revision and, if publishing
    changed, invalidates the public payload. Fixed query count whatever the
    row count."""
    PvpGuide.objects.filter(pk=guide.pk).update(
        scenario_id=data["scenario"],
        style_ranking=data["style_ranking"],
        short_description=data["short_description"],
        meta_analysis=data["meta_analysis"],
        admin_comments=data["admin_comments"],
        updated_at=timezone.now(),
    )

    guide.stat_configs.all().delete()
    PvpStatConfig.objects.bulk_create([
        PvpStatConfig(guide=guide, order=order, **row)
        for order, row in enumerate(data["stat_configs"])
    ])

    guide.team_comps.all().delete()
    PvpTeamComp.objects.bulk_create([
        PvpTeamComp(guide=guide, order=order, **row)
        for order, row in enumerate(data["team_comps"])
    ])

    guide.uma_recommendations.all().delete()
    PvpUmaRecommendation.objects.bulk_create([
        PvpUmaRecommendation(
            guide=guide, order=order, style=row["style"], uma_id=row["uma"],
            stars=row["stars"], role=row["role"], note=row["note"],
        )
        for order, row in enumerate(data["uma_recommendations"])
    ])

    guide.skill_recommendations.all().delete()
    PvpSkillRecommendation.objects.bulk_create([
        PvpSkillRecommendation(
            guide=guide, order=order, style=row["style"], category=row["category"],
            tier=row["tier"], skill_id=row["skill"], note=row["note"],
        )
        for order, row in enumerate(data["skill_recommendations"])
    ])

    guide.style_notes.all().delete()
    PvpStyleNote.objects.bulk_create([
        PvpStyleNote(guide=guide, **row) for row in data["style_notes"]
    ])

    _write_decks(guide, data["decks"])


def _write_decks(guide, decks):
    """Decks keep their id when the document names one they own; every slot
    is rewritten."""
    existing = {deck.id: deck for deck in guide.decks.all()}
    wanted_ids = {deck["id"] for deck in decks if deck["id"] in existing}
    guide.decks.exclude(id__in=wanted_ids).delete()
    PvpDeckSlot.objects.filter(deck__guide=guide).delete()

    kept = []
    new = []
    for order, deck in enumerate(decks):
        if deck["id"] in existing:
            row = existing[deck["id"]]
            row.style, row.label, row.note, row.order = deck["style"], deck["label"], deck["note"], order
            row.is_highlighted = deck["is_highlighted"]
            kept.append(row)
        else:
            new.append(PvpDeck(
                guide=guide, order=order, style=deck["style"], label=deck["label"], note=deck["note"],
                is_highlighted=deck["is_highlighted"],
            ))
    if kept:
        PvpDeck.objects.bulk_update(kept, ["style", "label", "note", "is_highlighted", "order"])
    # bulk_create fills the new rows' ids (SQLite and PostgreSQL both
    # return them), so the slots below can point at them.
    created = iter(PvpDeck.objects.bulk_create(new))

    slots = []
    for deck in decks:
        row = existing[deck["id"]] if deck["id"] in existing else next(created)
        for slot in deck["slots"]:
            slots.append(PvpDeckSlot(
                deck=row, slot=slot["slot"], order=slot["order"], is_borrow=slot["is_borrow"],
                support_card_id=slot["support_card"], note=slot["note"],
            ))
    PvpDeckSlot.objects.bulk_create(slots)


# A deck's id is matched against this guide's own decks only, so a document
# naming another guide's deck id simply creates a new deck.
__all__ = [
    "SECTIONS", "DocumentInput", "DocumentPut", "flatten_errors", "errors",
    "serialize", "counts", "write",
]
