"""
The community sheet's PvP guides, read into this site's guide tables.

Two callers, one parser:

- `load_sheet_pvp_guides` (LOCAL ONLY, management/commands/) fills every
  guide the sheet holds into a local SQLite database, for testing the PvP
  page against real content. It refuses any other database.
- The admin's "Load from the sheet" button (admin_pvp.PvpGuideAdmin
  .load_from_sheet) fills ONE guide, the one an editor is looking at, in
  production or anywhere else. That is an editor's action through the admin
  for one event, the same standing as typing the rows in (CLAUDE.md, "Safety
  & data": nothing loads content into prod unattended; an editor pressing a
  button for one event is an admin edit). It previews first.

WHAT IT READS
-------------
Two formats, the same two the PvpGuide model was designed around:

1. `PvP ImportRange`: the sheet's mirror of the upstream "PvP Data Sheet". One
   47-row block per event (CM #14 onward, LoH #1 onward), four styles side by
   side in column groups starting at B, I, P and W. Cards are keyed
   "30086 Narita Top Road CM #11" (game id first), umas "Seiun Sky CM #2".
   Gives: scenario, stats, short description, meta analysis, style ranking,
   starred umas, per-style "Rec. Accel" / "Rec. Skills" prose, decks with
   borrow slots, alternates and red highlights. Its honourable mentions
   block is skipped: the site has no such section (an editor who wants one
   adds a deck for it).
2. The per-cup tabs Henry writes now (`Virgo Cup`, `Capricorn Cup`,
   `Aquarius Cup`): a stat table, acceleration skills per style, tiered
   recommended skills, team comps and "Henry's Team". No decks. Their layout
   moves from tab to tab, so they are read by anchor text ("Recommended
   Stats", "Acceleration", "Recommended Skills", "Recommended Team Comp")
   rather than fixed rows. When an event is in both, the cup tab wins: it is
   the newer format.

Skills are matched to Skill rows by name (○/◎ variants, the sheet's
"Name (other name)" form, then a close-match fallback). Anything that does not
match is reported rather than guessed.
"""

# A spreadsheet parser: long, branchy functions that follow the sheet's layout
# cell by cell read better than the same logic split to satisfy a counter.
# pylint: disable=too-many-locals,too-many-branches,too-many-statements
# pylint: disable=too-many-instance-attributes,too-few-public-methods

import csv
import difflib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests
from django import forms
from django.db import transaction
from django.utils import timezone
from unfold.widgets import UnfoldBooleanWidget

from calculatorapi.models import (
    ChampionsMeeting,
    LeagueOfHeroes,
    PvpDeck,
    PvpDeckSlot,
    PvpSkillRecommendation,
    PvpStatConfig,
    PvpStyleNote,
    PvpTeamComp,
    PvpUmaRecommendation,
    Scenario,
    Skill,
    SupportCard,
    Uma,
)


class SheetError(Exception):
    """The sheet could not be read, or does not hold what was asked for.
    Shown to an editor as a form error, never as a 500."""


WORKBOOK = "100t3hnYl5Qm2UR8RtPlH-8Xd9KQbBlxEdXUOIR4d394"
IMPORT_TAB = "PvP ImportRange"
CUP_TABS = ("Virgo Cup", "Capricorn Cup", "Aquarius Cup")

# ImportRange: the first column of each style's six-column group.
GROUPS = {"front": 1, "pace": 8, "late": 15, "end": 22}
STYLE_WORDS = {
    "runaway": "runaway", "front": "front", "pace": "pace", "late": "late",
    "end": "end", "everyone": "everyone", "all": "everyone",
}
EVENT_HEADER = re.compile(r"^(CM|LoH)\s*#(\d+)", re.IGNORECASE)
# The "CM #N" tail is optional: the placeholders ("12305 Wit") have none.
CARD_CELL = re.compile(r"^(\d{5})\s+(.+?)(?:\s+(?:CM|LoH)\s*#\d+)?\s*$", re.IGNORECASE)
UMA_CELL = re.compile(r"^(.+?)\s+(?:CM|LoH)\s*#\d+\s*$", re.IGNORECASE)
# "12305 Wit": the data sheet's stand-ins for "any card of this type".
PLACEHOLDER_IDS = range(12300, 12310)


def tab_url(tab):
    from urllib.parse import quote  # pylint: disable=import-outside-toplevel
    return (
        f"https://docs.google.com/spreadsheets/d/{WORKBOOK}/gviz/tq"
        f"?tqx=out:csv&headers=0&sheet={quote(tab)}"
    )


def cell(rows, r, c):
    """rows[r][c] stripped, or "" past the edge."""
    if 0 <= r < len(rows) and 0 <= c < len(rows[r]):
        return rows[r][c].strip()
    return ""


def normalise(name):
    """A matching key: no accents, no punctuation (but ○ and ◎ and brackets
    stay), lower case, one space. "T.M. Opera O" and "TM Opera O" meet, as do
    "Playtime's Over!" and "Playtime is Over"."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch)).casefold()
    name = re.sub(r"[^\w\s○◎()/]", "", name)
    return re.sub(r"\s+", " ", name).strip()


@dataclass
class ParsedGuide:
    """Everything one event's guide holds, before it touches the database."""
    kind: str
    number: int
    source: str
    scenario: str = ""
    style_ranking: str = ""
    short_description: str = ""
    meta_analysis: str = ""
    stats: list = field(default_factory=list)       # dicts for PvpStatConfig
    umas: list = field(default_factory=list)        # (style, name, stars, role)
    style_notes: dict = field(default_factory=dict) # style -> markdown
    accel_text: dict = field(default_factory=dict)  # style -> the raw "Rec. Accel" line
    decks: list = field(default_factory=list)       # dicts with "slots"
    skills: list = field(default_factory=list)      # (style, category, tier, text, note)
    comps: list = field(default_factory=list)       # (text, note)


# ── ImportRange ──────────────────────────────────────────────────────────────

def _highlight(note, card_present):
    """The sheet's red text, as this site's ==highlight==."""
    if note:
        return f"=={note}=="
    return "==Must have==" if card_present else ""


def _row_has(rows, r, cols):
    return any(cell(rows, r, c) for c in cols)


def _is_red_row(rows, r, cols):
    values = [cell(rows, r, c) for c in cols]
    return cell(rows, r, 31) == "Highlight" or (
        any(v == "red" for v in values) and all(v in ("", "red") for v in values)
    )


def _card_or_placeholder(text):
    """(game_id or None, placeholder text or None) for one card cell."""
    match = CARD_CELL.match(text)
    if not match:
        return None, text or None
    game_id = int(match.group(1))
    if game_id in PLACEHOLDER_IDS:
        return None, f"Any {match.group(2)}"
    return game_id, None


def _parse_card_rows(rows, header_row, first_col, limit=6):
    """Cards, notes, red flags and alternates under a deck header.

    Returns (slots, footnotes): slots is a list of six dicts or None, one per
    column; footnotes is any "*..." line found under the block.
    """
    cols = list(range(first_col, first_col + limit))
    r = header_row + 1
    # Cards: the first row under the header with anything in this group.
    while r < header_row + 4 and not _row_has(rows, r, cols):
        r += 1
    card_row = r
    r += 1
    while r < card_row + 3 and not _row_has(rows, r, cols):
        r += 1
    notes_row = r
    red_row = notes_row + 1 if _is_red_row(rows, notes_row + 1, cols) else None
    alt_row = (red_row or notes_row) + 1

    slots, footnotes = [], []
    for index, c in enumerate(cols):
        text = cell(rows, card_row, c)
        game_id, placeholder = _card_or_placeholder(text)
        note = cell(rows, notes_row, c)
        if placeholder and note and note.casefold() != placeholder.casefold():
            note = f"{placeholder}: {note}"
        elif placeholder:
            note = placeholder
        if red_row is not None and cell(rows, red_row, c) == "red":
            note = _highlight(note, game_id is not None)
        alt = cell(rows, alt_row, c)
        if alt.startswith("*"):
            footnotes.append(alt)
            alt = ""
        if game_id is None and not note:
            slots.append(None)
            continue
        slots.append({"slot": index + 1, "game_id": game_id, "note": note, "alt": alt})
    # A footnote can also sit one row lower (LoH #6's Palmer line).
    for c in cols:
        below = cell(rows, alt_row + 1, c)
        if below.startswith("*"):
            footnotes.append(below)
    return slots, footnotes


def _deck_label(text):
    """("Beginner", "") from "BEGINNER - FRONT"; ("Weird deck", "18 power")
    from "WEIRD DECK - 18 power"."""
    head, _, rest = (part.strip() for part in text.partition("-"))
    label = head.capitalize() if head.isupper() else head
    note = "" if rest.casefold() in STYLE_WORDS else rest
    return label[:60] or "Deck", note


def parse_import_range(rows):
    """One ParsedGuide per event block in the PvP ImportRange tab."""
    starts = [
        (i, EVENT_HEADER.match(cell(rows, i, 1)))
        for i in range(len(rows))
        if EVENT_HEADER.match(cell(rows, i, 1))
    ]
    guides = []
    for n, (start, match) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else min(start + 47, len(rows))
        block = rows[start:end]
        guide = ParsedGuide(
            kind="cm" if match.group(1).upper() == "CM" else "loh",
            number=int(match.group(2)),
            source=IMPORT_TAB,
            scenario=cell(block, 1, 1),
            meta_analysis=cell(block, 0, 23),
            short_description=cell(block, 2, 9),
            style_ranking=cell(block, 3, 24),
        )
        _import_stats(block, guide)
        _import_body(block, guide)
        guides.append(guide)
    return guides


def _import_stats(block, guide):
    values = [cell(block, 1, c) for c in range(9, 14)]
    clean = ["" if v == "-" else v[:20] for v in values]
    if any(clean):
        guide.stats.append(dict(zip(("speed", "stamina", "power", "guts", "wit"), clean),
                                style="everyone", label="", footnote=""))
    # Up to two stamina/guts variants beside the main row.
    for stamina_col, guts_col, label in ((15, 17, cell(block, 0, 16)), (19, 20, "")):
        stamina, guts = cell(block, 1, stamina_col), cell(block, 1, guts_col)
        if stamina in ("", "-") and guts in ("", "-"):
            continue
        guide.stats.append({
            "style": "everyone",
            "label": re.sub(r"\s+", " ", label).strip()[:60] or "Variant",
            "speed": "", "power": "", "wit": "",
            "stamina": "" if stamina == "-" else stamina[:20],
            "guts": "" if guts == "-" else guts[:20],
            "footnote": "",
        })


def _import_body(block, guide):
    accel = skills_prose = None
    for r in range(len(block)):
        first = cell(block, r, 1)
        if first.startswith("Recommended Front"):
            names = r + 1
            while names < r + 4 and not _row_has(block, names, range(1, 28)):
                names += 1
            if cell(block, names, 1) == "Rec. Accel":
                continue  # an event with no starred umas yet
            for style, g in GROUPS.items():
                for c in range(g, g + 6):
                    name = cell(block, names, c)
                    if not name or name == "N/A":
                        continue
                    stars_text = cell(block, names + 1, c)
                    role = re.search(r"\(([^)]*)\)", stars_text)
                    uma = UMA_CELL.match(name)
                    guide.umas.append((
                        style,
                        uma.group(1) if uma else name,
                        max(1, min(5, stars_text.count("★"))),
                        role.group(1).strip()[:40] if role else "",
                    ))
        elif first == "Rec. Accel":
            accel = r
        elif first == "Rec. Skills":
            skills_prose = r
        for style, g in GROUPS.items():
            title = cell(block, r, g)
            if title and cell(block, r, g + 5) == "Borrow Slot":
                label, note = _deck_label(title)
                slots, footnotes = _parse_card_rows(block, r, g)
                if footnotes:
                    note = " ".join(filter(None, [note, *footnotes]))
                guide.decks.append({
                    "style": style, "label": label, "note": note[:300],
                    "slots": [s for s in slots if s],
                })

    for style, g in GROUPS.items():
        parts = []
        if accel is not None and cell(block, accel, g + 1):
            parts.append(f"**Acceleration:** {cell(block, accel, g + 1)}")
            guide.accel_text[style] = cell(block, accel, g + 1)
        if skills_prose is not None and cell(block, skills_prose, g + 1):
            parts.append(f"**Skills:** {cell(block, skills_prose, g + 1)}")
        if parts:
            guide.style_notes[style] = "\n\n".join(parts)


# ── Per-cup tabs ─────────────────────────────────────────────────────────────

def _find(rows, text, col=None):
    """(row, col) of the first cell equal to text, or (None, None)."""
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if value.strip() == text and (col is None or c == col):
                return r, c
    return None, None


def parse_cup_tab(rows, tab):
    """One ParsedGuide from a per-cup tab like "Aquarius Cup"."""
    match = re.match(r"CM\s*#(\d+)", cell(rows, 0, 0))
    if not match:
        raise SheetError(f"{tab}: no 'CM #N' in A1.")
    guide = ParsedGuide(kind="cm", number=int(match.group(1)), source=tab)
    _cup_stats(rows, guide)
    _cup_accel(rows, guide)
    _cup_skills(rows, guide)
    _cup_comps(rows, guide)
    return guide


def _cup_stats(rows, guide):
    top, col = _find(rows, "Recommended Stats")
    if top is None:
        return
    label = ""
    for r in range(top + 1, len(rows)):
        text = cell(rows, r, col)
        if text in ("Recommended Strategy", "Recommended", "Recommended Team Comp"):
            if text == "Recommended Strategy":
                guide.style_ranking = cell(rows, r + 1, col)[:120]
            break
        style = STYLE_WORDS.get(text.casefold())
        if style and any(cell(rows, r, c) for c in range(col + 1, col + 6)):
            values = [cell(rows, r, c)[:20] for c in range(col + 1, col + 6)]
            guide.stats.append(dict(zip(("speed", "stamina", "power", "guts", "wit"), values),
                                    style=style, label=label[:60], footnote=""))
        elif text.startswith("*") and guide.stats:
            footnote = guide.stats[-1]["footnote"]
            guide.stats[-1]["footnote"] = f"{footnote} {text}".strip()[:200]
        elif text:
            label = text


def _skill_cells(rows, r, start_col):
    return [(c, cell(rows, r, c)) for c in range(start_col, len(rows[r])) if cell(rows, r, c)]


def _cup_accel(rows, guide):
    top, col = _find(rows, "Acceleration")
    stop, _ = _find(rows, "Recommended Skills")
    if top is None:
        return
    for r in range(top, stop or len(rows)):
        style = STYLE_WORDS.get(cell(rows, r, col).casefold())
        if not style:
            continue
        for order, (_, text) in enumerate(_skill_cells(rows, r, col + 2)):
            guide.skills.append((style, "acceleration", None, text, order))


GROUP_TARGETS = {
    "green": ("everyone", "green"),
    "everyone": ("everyone", "other"),
    "front": ("front", "other"),
    "pace": ("pace", "other"),
    "late": ("late", "other"),
    "end": ("end", "other"),
    "debuffs": ("everyone", "debuff"),
}


def _cup_skills(rows, guide):
    top, col = _find(rows, "Recommended Skills")
    if top is None:
        return
    target, tier, order = None, None, 0
    for r in range(top + 1, len(rows)):
        group = cell(rows, r, col).casefold()
        target = GROUP_TARGETS.get(group, target)
        tier_text = cell(rows, r, col + 1)
        if tier_text.startswith("Tier"):
            number = re.match(r"Tier\s*(\d)\b(?!,)", tier_text)
            tier = int(number.group(1)) if number and int(number.group(1)) <= 4 else None
            continue
        if target is None:
            continue
        cells = _skill_cells(rows, r, col + 2)
        footnote = next((text for _, text in cells if text.startswith("*")), "")
        for _, text in cells:
            if text.startswith("*"):
                continue
            note = footnote if text.endswith("*") else ""
            guide.skills.append((target[0], target[1], tier, text, order, note))
            order += 1


def _cup_comps(rows, guide):
    top, col = _find(rows, "Recommended Team Comp")
    if top is None:
        return
    r = top + 1
    while r < len(rows):
        text = cell(rows, r, col)
        if text.startswith(("With ", "Total")):
            break
        if text == "Henry's Team":
            r += 1
            while r < len(rows) and not cell(rows, r, col):
                r += 1
            styles = [cell(rows, r, c) for c in range(col, col + 3) if cell(rows, r, c)]
            if styles:
                guide.comps.append((" + ".join(styles), "Henry's Team"))
        elif text:
            if (text, "") not in guide.comps:
                guide.comps.append((text[:120], ""))
        r += 1


# ── Matching ─────────────────────────────────────────────────────────────────

class SkillMatcher:
    """Name -> Skill, tolerant of the sheet's spellings."""

    def __init__(self):
        self.by_name = {}
        # On-global first, then the lowest game id, so a duplicate name picks a
        # stable, showable row.
        for skill in Skill.objects.on_global().order_by("game_id"):
            self.by_name.setdefault(normalise(skill.name), skill)
        self.names = list(self.by_name)

    def _one(self, name):
        key = normalise(name)
        if not key:
            return None
        if key in self.by_name:
            return self.by_name[key]
        # A name that doesn't say which version ("Sunny Day ○/◎", "Firm
        # Conditions") is the ○ first, the version "Fill track green skills"
        # adds (owner, 2026-10-17), so a guide loaded from the sheet and one
        # filled by the button name the same skill. A cell that says ◎ still
        # gets the ◎: an exact name returns above.
        if "○/◎" in key or "◎/○" in key:
            base = key.replace("○/◎", "").replace("◎/○", "").strip()
            for suffix in (" ○", " ◎", ""):
                if base + suffix in self.by_name:
                    return self.by_name[base + suffix]
            key = base
        for suffix in (" ○", " ◎"):
            if key + suffix in self.by_name:
                return self.by_name[key + suffix]
        # Close spellings ("Sunny Day" for "Sunny Days ○"), ○ first.
        for candidate in (key + " ○", key + " ◎", key):
            close = difflib.get_close_matches(candidate, self.names, n=1, cutoff=0.88)
            if close:
                return self.by_name[close[0]]
        return None

    def match(self, text):
        """(skill or None, note) for one sheet cell like
        "Early Lead (Taking The Lead)" or "Firm Condition ○/◎"."""
        text = re.sub(r"\s+", " ", text).strip()
        if "~" in text:
            # "Front~End Runner Savvy" means four skills, not one; leave it
            # to the unmatched list rather than pick Front Runner Savvy.
            return None, ""
        outside = re.sub(r"\([^)]*\)", "", text).strip()
        inside = " ".join(re.findall(r"\(([^)]*)\)", text)).strip()
        # Trailing words like "TAIKI ONLY" or "Front & Pace Only" are advice.
        advice = ""
        tail = re.search(r"\s+([A-Z][A-Za-z&~ ]*\b(?:ONLY|Only|only))$", outside)
        if tail:
            advice, outside = tail.group(1), outside[: tail.start()].strip()
        skill = self._one(outside)
        if skill is None and inside and not inside.lower().startswith("order"):
            skill = self._one(inside)
            inside = outside if skill else inside
        note = " ".join(part for part in (inside, advice) if part)
        return skill, note[:200]


# One-word skill names that are ordinary words in PvP prose: a style, a
# scenario, plain English. Never auto-linked; an editor can still write
# [[Focus]] by hand.
LINK_STOPLIST = {
    "runaway", "trackblazer", "focus", "relax", "eager", "pressure", "restart",
    "fighter", "rational", "comeback", "elated", "pumped", "aspire", "murmur",
    "tether", "moxie", "superstan",
    # "Acceleration" is a skill too, but prose (and the style note's own
    # "Acceleration:" label) means the idea.
    "acceleration",
}


class ProseLinker:
    """Wraps skill names found in prose as [[Skill name]], so they link.

    Only names without the ○ / ◎ / × markers (prose never writes those), and
    only whole words. One-word names must match their exact capitalisation
    ("Restless", not "restless"); longer names match ignoring case. Longest
    names first, so "Straightaway Acceleration" is not read as two skills.
    Text already inside [[...]] is left alone.
    """

    def __init__(self):
        names = {}
        for skill in Skill.objects.on_global().order_by("game_id"):
            name = skill.name.strip()
            if re.search(r"[○◎×]", name) or len(name) < 5 or name.casefold() in LINK_STOPLIST:
                continue
            names.setdefault(name.casefold(), name)
        self.canonical = names
        ordered = sorted(names.values(), key=len, reverse=True)
        multi = [re.escape(name) for name in ordered if " " in name]
        single = [re.escape(name) for name in ordered if " " not in name]
        self.multi = re.compile(r"(?<![\w\[])(" + "|".join(multi) + r")(?![\w\]])", re.IGNORECASE) if multi else None
        self.single = re.compile(r"(?<![\w\[])(" + "|".join(single) + r")(?![\w\]])") if single else None

    def find(self, text):
        """The canonical skill names in a text, in order of appearance."""
        hits = []
        for pattern in (self.multi, self.single):
            if pattern is None:
                continue
            for match in pattern.finditer(text or ""):
                hits.append((match.start(), self.canonical[match.group(1).casefold()]))
        # A short name inside a longer one already found is not a second hit.
        hits.sort()
        result, last_end = [], -1
        for start, name in hits:
            if start >= last_end:
                result.append(name)
                last_end = start + len(name)
        return result

    def link(self, text, max_length=None):
        """The text with each found name wrapped in [[...]]. Unchanged when the
        result would pass `max_length` (a column's limit)."""
        if not text:
            return text
        pieces = re.split(r"(\[\[[^\]]*\]\])", text)
        for index, piece in enumerate(pieces):
            if piece.startswith("[["):
                continue
            for pattern in (self.multi, self.single):
                if pattern is None:
                    continue
                piece = pattern.sub(lambda m: f"[[{self.canonical[m.group(1).casefold()]}]]", piece)
                # The second pattern must not re-enter what the first wrapped.
                piece = re.sub(r"\[\[\[\[([^\]]*)\]\]\]\]", r"[[\1]]", piece)
            pieces[index] = piece
        linked = "".join(pieces)
        # A name inside a name the first pass already wrapped ("[[Final [[Push]]]]").
        linked = re.sub(r"\[\[([^\[\]]*)\[\[([^\]]*)\]\]([^\[\]]*)\]\]", r"[[\1\2\3]]", linked)
        if max_length is not None and len(linked) > max_length:
            return text
        return linked


def _uma_lookup():
    table = {}
    for uma in Uma.objects.order_by("id"):
        table.setdefault(normalise(uma.name), uma)
    return table




# ── Writing a parsed guide ───────────────────────────────────────────────────

# Which event a ParsedGuide is for.
def event_for(parsed):
    if parsed.kind == "cm":
        return "champions_meeting", ChampionsMeeting.objects.filter(cm_number=parsed.number).first()
    return "league_of_heroes", LeagueOfHeroes.objects.filter(loh_number=parsed.number).first()


def read_tab(tab, from_dir=None):
    """One tab as rows of cells: from `<Tab name>.csv` in `from_dir`, or
    fetched from the public sheet."""
    if from_dir:
        path = Path(from_dir) / f"{tab}.csv"
        text = path.read_text(encoding="utf-8")
    else:
        try:
            response = requests.get(tab_url(tab), timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SheetError(f"Could not read the sheet's \"{tab}\" tab: {exc}") from exc
        text = response.content.decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def parse_all(reader):
    """Every guide the sheet holds, ImportRange blocks first, then the cup tabs."""
    guides = parse_import_range(reader(IMPORT_TAB))
    for tab in CUP_TABS:
        guides.append(parse_cup_tab(reader(tab), tab))
    return guides


class SheetImporter:
    """Writes ParsedGuides into guide rows, remembering what did not match."""

    def __init__(self):
        self.unmatched = {"umas": set(), "cards": set(), "skills": set(), "events": []}
        self.linker = ProseLinker()
        self.matcher = SkillMatcher()
        self.accel_picks = 0

    def write(self, parsed, guide, *, note=""):
        """Fill `guide` (an existing row) from `parsed`: the overview fields
        are overwritten and every child table is replaced. Returns the rows
        written per section."""
        link = self.linker.link
        scenario = (
            Scenario.objects.filter(name__iexact=parsed.scenario).first()
            if parsed.scenario else None
        )
        guide.scenario = scenario
        guide.style_ranking = parsed.style_ranking[:120]
        guide.short_description = link(parsed.short_description)
        guide.meta_analysis = link(parsed.meta_analysis)
        if note:
            guide.admin_comments = (
                f"{guide.admin_comments}\n{note}".strip()
                + (f" Scenario in the sheet: {parsed.scenario}." if parsed.scenario and not scenario else "")
            )
        guide.save()
        for related in ("stat_configs", "uma_recommendations", "style_notes", "decks",
                        "skill_recommendations", "team_comps"):
            getattr(guide, related).all().delete()

        counts = {}
        for order, row in enumerate(parsed.stats):
            PvpStatConfig.objects.create(
                guide=guide, order=order, **{**row, "footnote": link(row["footnote"], 200)}
            )
        counts["stats"] = len(parsed.stats)

        umas = _uma_lookup()
        seen = set()
        counts["umas"] = 0
        for order, (style, name, stars, role) in enumerate(parsed.umas):
            uma = umas.get(normalise(name))
            if uma is None:
                self.unmatched["umas"].add(name)
                continue
            if (style, uma.id) in seen:
                continue
            seen.add((style, uma.id))
            PvpUmaRecommendation.objects.create(
                guide=guide, style=style, uma=uma, stars=stars, role=role, order=order,
            )
            counts["umas"] += 1

        for style, body in parsed.style_notes.items():
            PvpStyleNote.objects.create(guide=guide, style=style, body=link(body))
        counts["advice"] = len(parsed.style_notes)

        cards = {card.game_id: card for card in SupportCard.objects.exclude(game_id=None)}
        for order, deck in enumerate(parsed.decks):
            row = PvpDeck.objects.create(guide=guide, style=deck["style"], label=deck["label"],
                                         note=link(deck["note"], 300), order=order)
            for slot in deck["slots"]:
                card = cards.get(slot["game_id"]) if slot["game_id"] else None
                note_text = slot["note"]
                if slot["game_id"] and card is None:
                    self.unmatched["cards"].add(slot["game_id"])
                    note_text = note_text or f"Card {slot['game_id']}"
                if card is None and not note_text:
                    continue
                PvpDeckSlot.objects.create(
                    deck=row, slot=slot["slot"], order=0, is_borrow=slot["slot"] == 6,
                    support_card=card, note=link(note_text[:120], 120),
                )
                if slot["alt"]:
                    PvpDeckSlot.objects.create(
                        deck=row, slot=slot["slot"], order=1, is_borrow=slot["slot"] == 6,
                        note=link(slot["alt"][:120], 120),
                    )
        counts["decks"] = len(parsed.decks)

        matcher = self.matcher
        seen = set()
        counts["skills"] = 0
        for entry in parsed.skills:
            style, category, tier, text, order = entry[:5]
            extra = entry[5] if len(entry) > 5 else ""
            skill, note_text = matcher.match(text)
            if skill is None:
                self.unmatched["skills"].add(text)
                continue
            if (style, category, skill.id) in seen:
                continue
            seen.add((style, category, skill.id))
            PvpSkillRecommendation.objects.create(
                guide=guide, style=style, category=category, tier=tier, skill=skill,
                note=" ".join(filter(None, [note_text, extra]))[:200], order=order,
            )
            counts["skills"] += 1

        for style, text in parsed.accel_text.items():
            for order, name in enumerate(self.linker.find(text)):
                skill = matcher.by_name.get(normalise(name))
                if skill is None or (style, "acceleration", skill.id) in seen:
                    continue
                seen.add((style, "acceleration", skill.id))
                PvpSkillRecommendation.objects.create(
                    guide=guide, style=style, category="acceleration", skill=skill, order=order,
                )
                self.accel_picks += 1
                counts["skills"] += 1

        for order, (text, note_text) in enumerate(parsed.comps):
            PvpTeamComp.objects.create(guide=guide, text=text, note=note_text, order=order)
        counts["team comps"] = len(parsed.comps)
        return counts

    def unmatched_report(self):
        """[(label, [names])] for everything that matched nothing, for a
        report or a page."""
        report = []
        for key, label in (("events", "Events not in this database"),
                           ("umas", "Umas not matched"),
                           ("cards", "Card game ids not in this database"),
                           ("skills", "Skills not matched")):
            values = self.unmatched[key]
            if values:
                report.append((label, sorted(str(value) for value in values)))
        return report


# ── The admin's "Load from the sheet" ────────────────────────────────────────

class LoadFromSheetForm(forms.Form):
    preview = forms.BooleanField(
        required=False,
        initial=True,
        label="Preview only",
        widget=UnfoldBooleanWidget,
        help_text=(
            "Read the sheet and report what would be written, without saving. "
            "Untick to replace this guide's sections with the sheet's."
        ),
    )


def parsed_for_guide(guide, reader=None):
    """The sheet's guide for this event, the cup tab's if there is one, else
    the ImportRange block. Raises SheetError when the sheet has neither."""
    event = guide.event
    kind = "cm" if guide.champions_meeting_id else "loh"
    number = event.cm_number if kind == "cm" else event.loh_number
    found = [
        parsed for parsed in parse_all(reader or read_tab)
        if parsed.kind == kind and parsed.number == number
    ]
    if not found:
        raise SheetError(
            f"The sheet has no guide for {kind.upper()} #{number}. The ImportRange tab "
            f"and the cup tabs ({', '.join(CUP_TABS)}) were read."
        )
    cup = [parsed for parsed in found if parsed.source != IMPORT_TAB]
    return (cup or found)[0]


def load_guide_from_sheet(guide, *, preview=True, reader=None):
    """Fill one guide from the sheet. With `preview`, everything is rolled
    back and only the report comes back. Returns the summary the page shows."""
    parsed = parsed_for_guide(guide, reader)
    importer = SheetImporter()
    with transaction.atomic():
        counts = importer.write(
            parsed, guide,
            note=f"Loaded from the sheet's \"{parsed.source}\" tab on {timezone.now():%Y-%m-%d}.",
        )
        if preview:
            transaction.set_rollback(True)
    return {
        "source": parsed.source,
        "counts": counts,
        "unmatched": importer.unmatched_report(),
        "accel_picks": importer.accel_picks,
        "preview": preview,
    }
