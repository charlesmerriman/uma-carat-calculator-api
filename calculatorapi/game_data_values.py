"""
The Uma columns one snapshot outfit implies, global's or the JP client's.

Read by `manage.py import_game_data`, which decides which rows take them (its
docstring has the order of authority). Kept apart so the command stays about
planning and writing; game_data_sources.py is the other half, what the
secondary sources say.
"""

from django.core.management.base import CommandError

# snapshot key -> Uma column, for the blocks that copy straight across.
APTITUDE_COLUMNS = {
    "turf": "apt_turf", "dirt": "apt_dirt",
    "short": "apt_short", "mile": "apt_mile", "medium": "apt_medium", "long": "apt_long",
    "front": "apt_front", "pace": "apt_pace", "late": "apt_late", "end": "apt_end",
}
STAT_NAMES = ("speed", "stamina", "power", "guts", "wit")
RUNNING_STYLES = {"front": 1, "pace": 2, "late": 3, "end": 4}


def star_stats(card):
    """
    Uma.star_stats for one snapshot outfit: the five stats at each star level
    the game lists, lowest star first. Aptitudes are left out on purpose: they
    are the same at every star in both clients (checked 2026-11-12, all 272
    outfits), so the apt_* columns already say them once.
    """
    return [
        {"star": star["star"], **{stat: star["stats"][stat] for stat in STAT_NAMES}}
        for star in sorted(card["stars"], key=lambda star: star["star"])
    ]


def uma_values(card):
    """The Uma column values one snapshot outfit implies."""
    initial = next(
        (star for star in card["stars"] if star["star"] == card["default_rarity"]), None,
    )
    if initial is None:
        raise CommandError(
            f"card {card['id']} has no per-star row for its initial rarity "
            f"{card['default_rarity']}; the snapshot is inconsistent"
        )
    values = {
        "title": card["title"],
        "rarity": card["default_rarity"],
        "running_style": RUNNING_STYLES[card["running_style"]],
    }
    for key, column in APTITUDE_COLUMNS.items():
        values[column] = initial["aptitude"][key]
    for stat in STAT_NAMES:
        values[f"base_{stat}"] = initial["stats"][stat]
        values[f"growth_{stat}"] = card["growth"][stat]
    values["star_stats"] = star_stats(card)
    return values


# What a JP-only outfit must NOT take from the JP client, though uma_values
# reads it:
#   rarity  feeds Uma.is_three_star, so selectors and the rate-up odds. Filling
#           it from JP would move both on unreleased umas; that belongs to
#           those rules, not to a reference page (encyclopedia-plan.md). An
#           editor sets it by hand, as before.
#   title   the English title is the editors' until global ships one; JP's
#           goes to jp_title instead (game_data_sources.jp_names).
JP_SKIPPED_COLUMNS = ("rarity", "title")


def jp_uma_values(card):
    """The Uma columns a JP-only outfit takes from the JP snapshot."""
    values = uma_values(card)
    for column in JP_SKIPPED_COLUMNS:
        del values[column]
    return values


def objective_values(objectives, uma_game_ids):
    """
    {outfit game id: {"career_objectives": goals}} from objectives.json, for
    every outfit whose character has a route. The game keys goals by
    character (game id // 100), so each outfit of one character gets the same
    list. From the GLOBAL client only: its race names are Global's English,
    which is why an outfit not yet out on global may take them too.
    """
    by_character = {row["chara_id"]: row["goals"] for row in objectives}
    return {
        game_id: {"career_objectives": by_character[game_id // 100]}
        for game_id in uma_game_ids
        if game_id // 100 in by_character
    }


def merged(*sources):
    """{game id: {column: value}} dicts combined per id, later ones adding columns."""
    result = {}
    for source in sources:
        for game_id, values in source.items():
            result.setdefault(game_id, {}).update(values)
    return result
