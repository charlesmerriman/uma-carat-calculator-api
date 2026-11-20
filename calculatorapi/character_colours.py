"""
Each character's two colours, as the game's own files give them.

calculatorapi/data/character_colours.json is {character id: [main, sub]}: the
game's chara_data.ui_color_main and ui_color_sub, the pair it paints her
training screens in (Oguri Cap: #3A7AD2 blue and #ECE7E7 pale grey). The
Encyclopedia paints her pages with them (notes/encyclopedia-redesign-plan.md,
in the workspace's notes repo).

WHY A FILE AND NOT TWO COLUMNS. The colours belong to a CHARACTER, and there is
no character row: every outfit and support card of hers would carry a copy.
Nothing filters by them and no editor should change them, so they stay what
they are, a slice of the game files: scripts/extract_master_snapshot.py writes
it from both clients' snapshots (global's pair wins), and a refresh is a diff
anyone can read. The same shape as support_effect_types.json.

A value ends up in a style attribute, and the JP half comes from a teammate's
export, so the file is checked on load: "#" and six hex digits, or the whole
table is refused.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

COLOURS_FILE = Path(__file__).resolve().parent / "data" / "character_colours.json"
HEX_COLOUR = re.compile(r"^#[0-9A-F]{6}$")


@lru_cache(maxsize=1)
def table():
    """{character id as text: [main, sub]}, as the catalogue sends it."""
    colours = json.loads(COLOURS_FILE.read_text(encoding="utf-8"))
    for character, pair in colours.items():
        if not (character.isdigit() and len(pair) == 2 and all(HEX_COLOUR.match(value) for value in pair)):
            raise ValueError(f"character_colours.json: {character!r}: {pair!r} is not two hex colours")
    return colours


def colours_for(character_id):
    """[main, sub] for one of the game's character ids, or None when the files
    have no pair for her yet (a character newer than the last JP export)."""
    if not character_id:
        return None
    pair = table().get(str(character_id))
    return list(pair) if pair else None
