"""
Deck limit breaks written as diamonds, the community sheet's way.

The sheet writes the limit break a deck's card should have as four diamonds, ◆
for each break and ◇ for the rest, then "+" or "++" for "or better", on the "or"
row under the card: "◆◆◆◇ + or spd" is "this card at 3 LB or better, or any
Speed card". The site keeps the number as PvpDeckSlot.limit_break (0 to 4) and
draws the game's own diamonds on the card (frontend FramedCard).

ONLY THE DIAMOND PART IS READ. The rest of the note stays exactly as written
(owner, 2026-11-02: "if the notes consist more than just the diamond, only the
diamond part is converted and the rest of the note stays"), so "◆◆◆◇ + or spd"
leaves "or spd" and "◇◇◇◇ + or R" leaves "or R".

Used by the sheet loader (pvp_sheet.py), so a fresh "Load from the sheet" fills
the field, and by `manage.py convert_deck_limit_breaks`, which moves the notes
already in the database once.
"""

import re

# Four diamonds, then any "+" after them (with or without a space): the whole
# diamond part, wherever it sits in the note.
DIAMONDS = re.compile(r"[◆◇]{4}(?:\s*\++)?")


def split_limit_break(text):
    """(limit break or None, the note without its diamond part).

    `None` and the text unchanged when the note holds no diamonds. The note
    keeps everything else, with the gap the diamonds leave closed up.
    """
    match = DIAMONDS.search(text)
    if match is None:
        return None, text
    rest = f"{text[:match.start()]} {text[match.end():]}"
    return match.group(0).count("◆"), re.sub(r"\s+", " ", rest).strip()
