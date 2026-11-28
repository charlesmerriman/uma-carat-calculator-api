"""
A community guide's document, as data: which parts of a site guide's document
a player's guide keeps, an empty one, the stored or validated form as plain
JSON, every word a reader sees in it, and those words for a moderator.

No queries and no imports from the gallery, so both guide_gallery.py (which
posts through moderation_cards) and moderation_cards.py (which shows a guide's
words on a card) can read it without importing each other.
"""

import json

from calculatorapi import pvp_guide_document

# The parts of a site guide's document a player's guide keeps.
CONTENT_KEYS = (
    "scenario", "style_ranking", "short_description", "meta_analysis", *pvp_guide_document.SECTIONS,
)


def empty_content():
    """A guide with nothing in it yet."""
    return {
        "scenario": None, "style_ranking": "", "short_description": "", "meta_analysis": "",
        **{section: [] for section in pvp_guide_document.SECTIONS},
    }


def content_of(document):
    """The content keys of a stored or validated document, as plain JSON (a
    deep copy: validated data holds OrderedDicts, and a stored document must
    never be edited in place)."""
    content = {**empty_content(), **{key: document[key] for key in CONTENT_KEYS if key in document}}
    return _with_deck_ids(json.loads(json.dumps(content)))


def _with_deck_ids(content):
    """Every deck gets a positive id unique in this guide: the editor keys
    decks by it (dragging, undo), and a working copy has no table to give
    one. An id the client already has is kept."""
    used = set()
    for deck in content["decks"]:
        if isinstance(deck.get("id"), int) and deck["id"] > 0 and deck["id"] not in used:
            used.add(deck["id"])
        else:
            deck["id"] = None
    next_id = max(used, default=0) + 1
    for deck in content["decks"]:
        if deck["id"] is None:
            deck["id"] = next_id
            next_id += 1
    return content


def public_texts(content):
    """(path, text) for EVERY word a reader sees in a guide, for the filters.

    Wider than pvp_guide_document._texts (where mentions live): that one
    leaves out a stat row's label and cells, an uma's role and a deck's
    label, which a reader sees all the same."""
    for field in ("style_ranking", "short_description", "meta_analysis"):
        yield field, content[field]
    for i, row in enumerate(content["stat_configs"]):
        for field in ("label", "speed", "stamina", "power", "guts", "wit", "footnote"):
            yield f"stat_configs[{i}].{field}", row[field]
    for i, row in enumerate(content["team_comps"]):
        yield f"team_comps[{i}].text", row["text"]
        yield f"team_comps[{i}].note", row["note"]
    for i, row in enumerate(content["uma_recommendations"]):
        yield f"uma_recommendations[{i}].role", row["role"]
        yield f"uma_recommendations[{i}].note", row["note"]
    for i, row in enumerate(content["skill_recommendations"]):
        yield f"skill_recommendations[{i}].note", row["note"]
    for i, row in enumerate(content["style_notes"]):
        yield f"style_notes[{i}].body", row["body"]
    for i, deck in enumerate(content["decks"]):
        yield f"decks[{i}].label", deck["label"]
        yield f"decks[{i}].note", deck["note"]
        for j, slot in enumerate(deck["slots"]):
            yield f"decks[{i}].slots[{j}].note", slot["note"]


# ── Moderation ───────────────────────────────────────────────────────────────

# The most of a guide's text a moderation card carries.
CARD_TEXT_LIMIT = 1500


def card_text(post):
    """A guide's words for a moderator, in section order, cut to the card."""
    content = content_of(post.guide_snapshot or {})
    words = " / ".join(text.strip() for _, text in public_texts(content) if text.strip())
    if len(words) > CARD_TEXT_LIMIT:
        words = f"{words[:CARD_TEXT_LIMIT]} (and {len(words) - CARD_TEXT_LIMIT:,} more characters)"
    return words
