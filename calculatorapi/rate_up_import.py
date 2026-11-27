"""
The rate-up half of import_game_data: the snapshots' support_gachas.json into
the SR and R links of the site's support banners (notes/banner-sr-r-plan.md).

WHY THE DEPLOY DOES IT (the owner, 2026-11-27): an SR or R on rate-up is game
data, like a story event's free cards, and there are about 175 of them. The
links land with `timeline_display` "auto", so the site lists every one of them
straight away (calculatorapi/banner_rate_ups.py says how) and an editor only
steps in to change one.

WHAT THE IMPORT OWNS, AND WHAT IT NEVER TOUCHES:

  adds        an SR or R link the gacha names and the banner lacks
  never       removes a link, touches an SSR link, changes a link that exists
              (so an editor's `timeline_display`, recommendation or rate
              override stays), creates a banner or creates a card

TWO FILES, ONE GACHA LIST. Global's snapshot holds the gachas it has run,
with Global's instants; JP's holds every one, with JP's. The two clients share
gacha ids, so a gacha is read once with both starts.

THE BANNER MATCH, in two steps:

  1. The timeline. Global's start to the minute when Global has run the gacha,
     else JP's start on the same JP calendar day as a timeline's
     `jp_start_date` (story_event_import's rule: that column holds the JP day
     at 22:00 UTC, so the gacha's instant is read in Japan time). The launch
     gacha opened at 00:30 and its banners at 22:00, so a minute that finds
     nothing falls back to the JP day.
  2. The banner on it whose SSRs are exactly the gacha's SSR rate-ups. Two
     support banners can share a day; their SSRs tell them apart. A banner
     whose SSRs an editor hasn't entered yet doesn't match, and a select
     banner's file lists placeholders: both are reported, never guessed.

A card the site has no row for is reported too (the card half of the import
creates cards from support_cards.json, so this is rare).
"""

import datetime

from calculatorapi.models import BannerSupport, BannerTimeline, SupportCard, SupportsOnSupportBanner

JST = datetime.timezone(datetime.timedelta(hours=9))
SSR = 3


def _instant(text):
    return datetime.datetime.fromisoformat(text) if text else None


def merge_gachas(global_gachas, jp_gachas):
    """{gacha id: {global_start, jp_start, rate_ups}}, Global's rate-ups first."""
    merged = {}
    for gacha in jp_gachas:
        merged[gacha["id"]] = {"global_start": None, "jp_start": _instant(gacha["start"]),
                               "rate_ups": gacha["rate_ups"]}
    for gacha in global_gachas:
        entry = merged.setdefault(gacha["id"], {"jp_start": None})
        entry.update({"global_start": _instant(gacha["start"]), "rate_ups": gacha["rate_ups"]})
    return merged


class _Banners:  # pylint: disable=too-few-public-methods
    """Every support banner, findable by its timeline's two starts and its SSRs."""

    def __init__(self):
        self.by_global = {}
        self.by_jp_day = {}
        for timeline_id, global_start, jp_start in BannerTimeline.objects.values_list(
            "id", "global_start_date", "jp_start_date",
        ):
            if global_start is not None:
                self.by_global.setdefault(global_start, []).append(timeline_id)
            if jp_start is not None:
                day = jp_start.astimezone(datetime.timezone.utc).date()
                self.by_jp_day.setdefault(day, []).append(timeline_id)
        ssrs = {}
        for banner_id, game_id, rarity in SupportsOnSupportBanner.objects.values_list(
            "banner_support_id", "support_card__game_id", "support_card__rarity",
        ):
            if rarity in (None, SSR) and game_id is not None:
                ssrs.setdefault(banner_id, set()).add(game_id)
        self.on_timeline = {}
        for banner_id, timeline_id in BannerSupport.objects.values_list("id", "banner_timeline_id"):
            self.on_timeline.setdefault(timeline_id, []).append((banner_id, frozenset(ssrs.get(banner_id, ()))))

    def _with_ssrs(self, timeline_ids, wanted):
        return sorted({banner_id for timeline_id in timeline_ids
                       for banner_id, ssrs in self.on_timeline.get(timeline_id, ()) if ssrs == wanted})

    def match(self, gacha):
        """The one banner this gacha ran as, or None."""
        wanted = frozenset(card["card"] for card in gacha["rate_ups"] if card["rarity"] == SSR)
        if not wanted:
            return None
        tries = []
        if gacha["global_start"] is not None:
            tries.append(self.by_global.get(gacha["global_start"], ()))
        if gacha["jp_start"] is not None:
            tries.append(self.by_jp_day.get(gacha["jp_start"].astimezone(JST).date(), ()))
        for timeline_ids in tries:
            found = self._with_ssrs(timeline_ids, wanted)
            if len(found) == 1:
                return found[0]
        return None


def plan_rate_ups(global_gachas, jp_gachas):
    """What a run would add, without writing anything. See the module docstring."""
    plan = {"label": "rate-ups", "rate_ups": True, "changed": [], "created": [],
            "unmatched": [], "missing": [], "unchanged": 0}
    gachas = merge_gachas(global_gachas, jp_gachas)
    if not gachas:
        return plan
    banners = _Banners()
    card_pks = dict(SupportCard.objects.exclude(game_id=None).values_list("game_id", "pk"))
    linked = set(SupportsOnSupportBanner.objects.values_list("banner_support_id", "support_card_id"))
    for gacha_id, gacha in sorted(gachas.items()):
        banner_id = banners.match(gacha)
        if banner_id is None:
            start = gacha["jp_start"] or gacha["global_start"]
            plan["unmatched"].append(f"{gacha_id} ({start.astimezone(JST).date() if start else 'no date'})")
            continue
        for card in gacha["rate_ups"]:
            if card["rarity"] == SSR:
                continue
            card_pk = card_pks.get(card["card"])
            if card_pk is None:
                plan["missing"].append(f"card {card['card']} (gacha {gacha_id}) has no row")
                continue
            if (banner_id, card_pk) in linked:
                plan["unchanged"] += 1
                continue
            linked.add((banner_id, card_pk))
            plan["created"].append(SupportsOnSupportBanner(banner_support_id=banner_id, support_card_id=card_pk))
    return plan


def report_lines(plan, dry_run):
    """The import's report for this plan, as (text, is_warning) pairs."""
    lines = [(
        f"\nSR and R rate-ups: {'would add' if dry_run else 'adding'} {len(plan['created'])} "
        f"link(s), already linked {plan['unchanged']}", False,
    )]
    if plan["unmatched"]:
        lines.append((
            f"    no banner with the same day and SSRs for {len(plan['unmatched'])} gacha(s): "
            f"{', '.join(plan['unmatched'])}", True,
        ))
    lines += [(f"    {line}", True) for line in plan["missing"]]
    return lines


def write_rate_ups(plan):
    """Apply a plan from plan_rate_ups. Runs inside the import's transaction;
    the import drops the payload cache after it (a bulk create fires no signal)."""
    SupportsOnSupportBanner.objects.bulk_create(plan["created"])
    return len(plan["created"])
