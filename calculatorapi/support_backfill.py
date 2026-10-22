"""
Shared pieces for the commands that attach missing support cards to banners.

Two commands need the same three things — read the pipeline's master CSV, turn
a " + "-joined name list into SupportCard rows, and refuse rather than guess
when a name doesn't resolve. They live here so the two cannot drift apart.

MATCHING CAME FROM THE OLD FIXTURE PIPELINE. `normalize`, `ALIASES` and the
date tiers below were first written in the scripts that generated fixtures from
the same CSV (`build_missing_banners.py`, `fix_banner_support_links.py`). Those
scripts were deleted on 2026-10-22, since their fixtures reached no database
once seeding was removed, so this module is now the only copy: there is nothing
left to keep in step with.

WHY NAMES ALONE ARE AMBIGUOUS: SupportCard.name is not unique, by design — see
the model's own note that "many characters have 2-3 support cards sharing the
exact same name (different rarities/reprints)". Production holds four rows
named exactly "Grass Wonder". `game_id` is the real identity, and the CSV
carries only names, so a collision has to be resolved by rule.

THE OLD RULE WAS LOWEST game_id, AND IT WAS WRONG. game_id encodes rarity —
1xxxx is R, 2xxxx SR, 3xxxx SSR — so "lowest" picked the R variant every single
time a character had one. That is never right: gacha banners feature SSRs, and
an R card is not obtainable from a banner at all. It put 20 R cards on the JP
launch banner in production (see the `fix_support_card_variants` command, which
repairs the damage this rule caused).

THE RULE NOW: among same-named SSR rows, pick by date against the banner's own
JP start, in two tiers:

  Tier 1 (debut) — exactly one variant whose source release_ja IS the banner's
  JP start date. The card launched on this banner.

  Tier 2 (rerun) — no debut match, so among variants released on or before that
  date (a banner cannot feature a card that does not exist yet), take the most
  recent, but only if it leads the runner-up by MIN_RERUN_MARGIN_DAYS. A tighter
  gap means two cards launched close together and there is no honest way to say
  which one a later banner is reusing.

Anything neither tier settles is reported as unresolved, never guessed at. The
fallbacks are narrow and each has a reason: one candidate needs no rule; no SSR
row at all falls back to the old lowest-id behaviour (a name whose rows predate
the game_id backfill still has to resolve to something); and no release date
for any candidate falls back to the lowest SSR, which is at least always a
card a banner could actually feature.

ONE GUARD SITS AHEAD OF ALL OF IT. If the source says a card debuted on this
banner's date and that game_id is not in the database, everything below is
refused. Otherwise tier 2 happily supplies the character's most recent older
reprint, which is indistinguishable from a correct answer in the output and
wrong in the database. Every 2026 banner in this project's local database hits
that case, because the cards are newer than the local snapshot.

WHY NOT FUZZY MATCHING: a similarity check offered "Hishi Miracle" as the
second-best match for "K.S. Miracle". Those are different cards. Normalization
plus an explicit alias table is as far as this goes; an unresolved name is
reported, never guessed at.
"""

import csv
import json
import os
import re
from collections import defaultdict
from datetime import date

from django.conf import settings

from calculatorapi.models import SupportCard

# The master timeline: one row per global banner, with its featured cards by name.
# Tracked in git and present on the deployed container, so a command may read it
# at runtime in production.
TIMELINE_MASTER = os.path.join(
    settings.BASE_DIR, "scripts", "data", "timeline_master.csv"
)

# Release dates per game_id. Same tree, same guarantees — tracked in git and
# deployed, so the date tiers work in production. It is the pipeline's snapshot
# of the reference game data, which is why release_ja is trustworthy here and
# `first_jp_date` (derived from whatever banners we happen to have linked) is
# not: deriving the fix from the data being fixed would be circular.
SUPPORT_CARDS_SOURCE = os.path.join(
    settings.BASE_DIR, "scripts", "data", "support_cards_source.json"
)

# game_id encodes rarity: 1xxxx R, 2xxxx SR, 3xxxx SSR.
SSR_MIN_GAME_ID = 30000

# How far the best rerun candidate must lead the runner-up to be believed.
MIN_RERUN_MARGIN_DAYS = 14

# Parsed source data, keyed by the path it came from so a test that patches
# SUPPORT_CARDS_SOURCE is not served the real file's cache.
_source_cache = {}

# The sheet's "Banner Type" code for a race-prep support rerun: one uma (usually)
# alongside ten support cards. See BannerCategory in models/banner_timeline.py.
RACE_PREP_BANNER_TYPE = "2"

# Sheet spellings that differ from the card names, keyed by NORMALIZED name.
#
# "K.S. Miracle" needs no entry: normalize() drops the periods, so it and the
# card's own "K.S.Miracle" both collapse to "k s miracle".
ALIASES = {
    "tamano cross": "tamamo cross",
    "tazuna": "tazuna hayakawa",
}


def split_names(joined):
    """The CSV's " + "-joined lists, as a list of trimmed names."""
    return [part.strip() for part in (joined or "").split(" + ") if part.strip()]


def normalize(value):
    """Lowercase, drop punctuation and embedded support game ids, squash spaces."""
    value = value.lower()
    value = re.sub(r"[.'’♡♥❤♪&]", " ", value)
    value = re.sub(r"[-_]", " ", value)
    value = re.sub(r"\b3\d{4}\b", " ", value)  # drop embedded support game ids
    return re.sub(r"\s+", " ", value).strip()


def strip_rerun(value):
    """The name without its "(Rerun)" suffix."""
    return re.sub(r"\s*\(rerun\)\s*", " ", value, flags=re.I).strip()


def _load_source():
    """
    Parse the snapshot once into (release_ja by game_id, SSR ids by name).

    Cached; the file cannot change while a command runs.
    """
    cached = _source_cache.get(SUPPORT_CARDS_SOURCE)
    if cached is not None:
        return cached

    releases, by_name = {}, defaultdict(list)
    with open(SUPPORT_CARDS_SOURCE, encoding="utf-8") as handle:
        for card in json.load(handle):
            game_id = card.get("id")
            raw = card.get("release_ja")
            released = None
            if raw:
                try:
                    released = date.fromisoformat(raw)
                except ValueError:
                    # A malformed date is worth ignoring rather than crashing a
                    # backfill: the card loses its date tier and falls back.
                    released = None
            if released is not None:
                releases[game_id] = released

            name = card.get("name_en")
            if name and (game_id or 0) >= SSR_MIN_GAME_ID:
                by_name[normalize(name)].append((game_id, released))

    cached = (releases, dict(by_name))
    _source_cache[SUPPORT_CARDS_SOURCE] = cached
    return cached


def release_dates():
    """game_id -> release_ja, as dates."""
    return _load_source()[0]


def clear_source_cache(path=None):
    """
    Forget a parsed snapshot. Only tests need this — they rewrite the file
    between assertions, which the cache would otherwise hide.
    """
    if path is None:
        _source_cache.clear()
    else:
        _source_cache.pop(path, None)


def missing_debut_game_ids(cards, jp_start):
    """
    Source game_ids that debuted on `jp_start` and are absent from the database.

    This is the guard against a confident wrong answer. Without it, a banner
    whose real card is absent from the database falls through to the rerun tier
    and gets linked to that character's most recent older reprint — which looks
    exactly like a correct answer in the output. Refusing instead surfaces the
    real problem, which is that an editor has to add the missing card.

    Returns the ids rather than a bool so callers can name them: "add 30293"
    is an actionable report, "unresolved" is a puzzle.
    """
    if not cards or jp_start is None:
        return []

    _, ssr_by_name = _load_source()
    present = {card.game_id for card in cards}
    return sorted(
        game_id
        for game_id, released in ssr_by_name.get(normalize(cards[0].name), ())
        if released == jp_start and game_id not in present
    )


def _pick_by_date(dated, jp_start):
    """Tiers 1 and 2 over [(release_ja, card)]. Returns a card, or None."""
    debut = [card for released, card in dated if released == jp_start]
    if len(debut) == 1:
        return debut[0]

    # Sorted by (date, game_id) so two cards sharing a release date still order
    # deterministically — the margin check below then rejects the pair anyway.
    preceding = sorted(
        (pair for pair in dated if pair[0] <= jp_start),
        key=lambda pair: (pair[0], pair[1].game_id),
        reverse=True,
    )
    if not preceding:
        return None  # every candidate postdates the banner
    if len(preceding) > 1:
        gap = (preceding[0][0] - preceding[1][0]).days
        if gap < MIN_RERUN_MARGIN_DAYS:
            return None  # too close together to tell apart
    return preceding[0][1]


def build_support_index():
    """
    Normalized name -> every SupportCard row sharing it.

    Built once per run rather than queried per name: 290 links across 85 names
    would otherwise be 290 round trips. Returns ALL variants, because which one
    is right depends on the banner — see resolve_variant.
    """
    index = defaultdict(list)
    for card in SupportCard.objects.all().only("id", "name", "game_id"):
        index[normalize(card.name)].append(card)
    return index


def resolve_variant(cards, jp_start):
    """
    Pick which of several same-named cards a banner starting `jp_start` features.

    Returns a SupportCard, or None when no tier gives a confident answer. See
    the module docstring for the rule and why each fallback exists.
    """
    if not cards or missing_debut_game_ids(cards, jp_start):
        return None

    if len(cards) == 1:
        return cards[0]

    ssr = [card for card in cards if (card.game_id or 0) >= SSR_MIN_GAME_ID]
    if not ssr:
        # Nothing here is banner-eligible. Rather than refuse outright, keep the
        # old behaviour for this case: these are rows that predate the game_id
        # backfill, not R cards masquerading as SSRs.
        return min(cards, key=lambda card: card.game_id or 0)
    if len(ssr) == 1:
        return ssr[0]

    releases = release_dates()
    dated = [(releases[card.game_id], card) for card in ssr if releases.get(card.game_id)]
    if jp_start is None or not dated:
        # No date signal to work with. The lowest SSR is a guess, but unlike the
        # old rule it is at least a card a banner could feature.
        return min(ssr, key=lambda card: card.game_id)

    return _pick_by_date(dated, jp_start)


def resolve_support_cards(names, jp_start=None, index=None):
    """
    Map names to SupportCard rows for a banner starting `jp_start`.

    Returns (found, missing), `found` in input order. Tries the name as given
    and then with "(Rerun)" stripped, applying the alias table to each — the
    same two-step the pipeline uses. A name that resolves to a set of variants
    but no confident one among them counts as MISSING, so a caller that skips
    incomplete batches keeps doing so rather than linking a guess.

    `jp_start` is optional only so a caller with no banner in hand can still ask
    the name question; pass it whenever there is a banner, or the date tiers are
    skipped and an ambiguous name falls back to the lowest SSR.

    Looks at every SupportCard, not just those already on a banner: production
    holds 315 cards attached to nothing, and they are invisible to the public
    API. Resolving against the API alone would wrongly report them missing.
    """
    if index is None:
        index = build_support_index()

    found, missing = [], []
    for name in names:
        card = None
        for candidate in (name, strip_rerun(name)):
            key = normalize(candidate)
            key = ALIASES.get(key, key)
            variants = index.get(key)
            if variants:
                card = resolve_variant(variants, jp_start)
                break
        if card:
            found.append(card)
        else:
            missing.append(name)
    return found, missing


def read_timeline_master(banner_type=None):
    """
    Rows of the master CSV, optionally filtered to one "Banner Type" code.

    The header repeats "Global Start Date" twice, so csv.DictReader collapses
    the pair and the second wins. Nothing here reads that column — matching is
    on JP start date, which is stable — but don't add a dependency on it
    without fixing the header first.
    """
    with open(TIMELINE_MASTER, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if banner_type is not None:
        rows = [row for row in rows if row["Banner Type"] == banner_type]
    return rows
