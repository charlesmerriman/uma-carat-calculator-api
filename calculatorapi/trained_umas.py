"""The operations on a player's veterans. Every /trained-umas view goes through these,
and so do the team and run saves that link one.

The pvp_teams.py split: ownership and the write rules live here, so the
Collection page, the import and the links from My team and CM Runs share one
set of checks. What a veteran is: models/trained_uma.py. The wire shape and
input validation: views/trained_uma.py.
"""

from django.db import transaction
from django.utils import timezone

from calculatorapi.models import (
    APTITUDE_KEYS,
    STAT_FIELDS,
    TRAINED_UMA_CAP,
    TRAINED_UMA_MEMO_MAX_LENGTH,
    CustomUser,
    PvpRunRunner,
    Skill,
    TrainedUma,
    TrainedUmaSkill,
    TrainedUmaSource,
    Uma,
)


class VeteranRefused(Exception):
    """A write the rules refuse. The message is shown to the player as is
    (.claude/rules/copy.md)."""


_CAP_MESSAGE = f"You can keep up to {TRAINED_UMA_CAP} veterans. Delete some you no longer use first."


def owned(user):
    """Every veteran `user` holds, newest first, with skills in one prefetch.
    `game_data` is deferred: about 7 KB each, and only one veteran's page reads it."""
    return TrainedUma.objects.filter(user=user).defer("game_data").prefetch_related("skills")


def strip_other_players(game_data):
    """(cleaned copy, is_borrowed) of one veteran's raw game object.

    Keeps everything the game gave, the player's own account id and memo
    included (owner, 2026-10-26), but removes OTHER players' account ids at
    any depth: a key ending in `viewer_id` whose value is another account
    (the borrowed veteran's owner, a borrowed parent's owner). Any trainer
    name goes too; the veteran file carries none today, but a name is never
    stored. The browser strips the same before upload; this is the server's
    own guarantee, whatever the client sent.
    """
    own = game_data.get("viewer_id")

    def clean(value):
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if "trainer_name" not in key
                and not (key.endswith("viewer_id") and item not in (0, None, own))
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    owner = game_data.get("owner_viewer_id")
    borrowed = game_data.get("use_type") == 1 or owner not in (0, None, own)
    return clean(game_data), borrowed


def get_owned(user, veteran_id):
    """The veteran with this id IF it is `user`'s, else TrainedUma.DoesNotExist.
    Somebody else's and one that does not exist are the same error."""
    try:
        return TrainedUma.objects.get(id=int(veteran_id), user=user)
    except (TypeError, ValueError) as exc:
        raise TrainedUma.DoesNotExist from exc


def check_links(user, rows):
    """{id: TrainedUma} for the veterans `rows` link, or VeteranRefused.

    `rows` are team members or run runners as the views validated them, each
    {"uma": id, "trained_uma": id or None}. A link must be the player's own
    veteran (somebody else's id reads as gone, the same as a deleted one) and
    of the row's own outfit: a veteran is one outfit, so an Oguri Cap
    (Christmas) veteran cannot stand in for the original.
    """
    wanted = {row["trained_uma"] for row in rows if row.get("trained_uma")}
    if not wanted:
        return {}
    found = {veteran.id: veteran for veteran in TrainedUma.objects.filter(user=user, id__in=wanted)}
    if len(found) != len(wanted):
        raise VeteranRefused("That veteran isn't in your collection any more.")
    for row in rows:
        veteran = found.get(row.get("trained_uma"))
        if veteran is not None and veteran.uma_id != row["uma"]:
            raise VeteranRefused("A veteran can only run as her own outfit.")
    return found


def _check_catalogue(umas, skill_ids):
    """VeteranRefused for an uma or a skill the site does not have."""
    if Uma.objects.filter(id__in=set(umas)).count() != len(set(umas)):
        raise VeteranRefused("One of those umas isn't on the site any more.")
    if Skill.objects.filter(id__in=set(skill_ids)).count() != len(set(skill_ids)):
        raise VeteranRefused("One of those skills isn't on the site any more.")


def _apply(veteran, data):
    """The validated stats and aptitudes onto the row (not saved)."""
    for stat in STAT_FIELDS:
        if stat in data:
            setattr(veteran, stat, data[stat])
    if "apt" in data:
        for key in APTITUDE_KEYS:
            setattr(veteran, f"apt_{key}", data["apt"].get(key))


def _skill_rows(veteran, skills):
    return [TrainedUmaSkill(trained_uma=veteran, skill_id=entry["skill"], level=entry["level"]) for entry in skills]


def create_manual(user, data):
    """A hand-entered veteran. VeteranRefused at the cap or for an unknown uma or skill."""
    _check_catalogue([data["uma"]], [entry["skill"] for entry in data["skills"]])
    with transaction.atomic():
        # Locks the account row for the count-then-create (pvp_teams.create_team).
        CustomUser.objects.select_for_update().get(pk=user.pk)
        if TrainedUma.objects.filter(user=user).count() >= TRAINED_UMA_CAP:
            raise VeteranRefused(_CAP_MESSAGE)
        veteran = TrainedUma(user=user, uma_id=data["uma"], label=data["label"], source=TrainedUmaSource.MANUAL)
        _apply(veteran, data)
        veteran.save()
        TrainedUmaSkill.objects.bulk_create(_skill_rows(veteran, data["skills"]))
    return veteran


def update(veteran, data):
    """Change a veteran. A veteran from the game keeps the game's numbers, so
    only its label may change; a hand-entered one may change anything but its
    uma (a different uma is a different veteran: add a new one)."""
    if veteran.source == TrainedUmaSource.IMPORT and set(data) - {"label"}:
        raise VeteranRefused("A veteran from the game keeps the game's numbers. You can change its label.")
    if "uma" in data and data["uma"] != veteran.uma_id:
        raise VeteranRefused("A veteran can't change uma. Add a new one instead.")
    if "skills" in data:
        _check_catalogue([], [entry["skill"] for entry in data["skills"]])
    with transaction.atomic():
        if "label" in data:
            veteran.label = data["label"]
        _apply(veteran, data)
        veteran.save()
        if "skills" in data:
            veteran.skills.all().delete()
            TrainedUmaSkill.objects.bulk_create(_skill_rows(veteran, data["skills"]))
    return veteran


def _apply_import(veteran, item, now):
    """One import item onto its row (not saved): the numbers, and the raw game
    object with other players stripped, its memo and its borrowed flag."""
    veteran.uma_id = item["uma"]
    veteran.rank_score = item["rank_score"]
    raw = item.get("game_data")
    if raw is not None:
        veteran.game_data, veteran.is_borrowed = strip_other_players(raw)
        memo = raw.get("memo")
        veteran.memo = memo[:TRAINED_UMA_MEMO_MAX_LENGTH] if isinstance(memo, str) else ""
    veteran.imported_at = now
    # bulk_update skips auto_now, so updated_at is set by hand.
    veteran.updated_at = now
    veteran.missing_from_import = False
    _apply(veteran, item)


def import_veterans(user, items):
    """Write a game export's veterans: update the ones already imported (by
    the game's id), add the new ones, and remove the ones the game no longer
    has, EXCEPT any a logged run links. Each item's `game_data` (the raw game
    object) is kept after strip_other_players, with its memo and borrowed flag. Those are kept and flagged
    `missing_from_import`, so a re-import never strips stats off CM history.
    Hand-entered veterans are never touched. Labels survive a re-import.

    Refused as a whole (nothing written) past the cap: hand-entered ones, the
    kept ones and the file's together. The page trims the file first, so this
    is the backstop.

    Returns {"created", "updated", "removed", "kept"} counts.
    """
    keys = [item["import_key"] for item in items]
    if len(set(keys)) != len(keys):
        raise VeteranRefused("The same veteran is in that file twice.")
    _check_catalogue(
        [item["uma"] for item in items],
        [entry["skill"] for item in items for entry in item["skills"]],
    )
    now = timezone.now()
    with transaction.atomic():
        CustomUser.objects.select_for_update().get(pk=user.pk)
        imported = {
            veteran.import_key: veteran
            for veteran in TrainedUma.objects.filter(user=user, source=TrainedUmaSource.IMPORT)
        }
        gone = [veteran for key, veteran in imported.items() if key not in set(keys)]
        linked = set(
            PvpRunRunner.objects.filter(trained_uma__in=gone).values_list("trained_uma_id", flat=True)
        )
        kept = [veteran for veteran in gone if veteran.id in linked]
        removed = [veteran for veteran in gone if veteran.id not in linked]
        manual = TrainedUma.objects.filter(user=user, source=TrainedUmaSource.MANUAL).count()
        if manual + len(kept) + len(items) > TRAINED_UMA_CAP:
            raise VeteranRefused(_CAP_MESSAGE)

        TrainedUma.objects.filter(id__in=[veteran.id for veteran in removed]).delete()
        TrainedUma.objects.filter(id__in=[veteran.id for veteran in kept]).update(missing_from_import=True)

        fresh, changed = [], []
        for item in items:
            veteran = imported.get(item["import_key"])
            if veteran is None:
                veteran = TrainedUma(user=user, source=TrainedUmaSource.IMPORT, import_key=item["import_key"])
                fresh.append(veteran)
            else:
                changed.append(veteran)
            _apply_import(veteran, item, now)
        TrainedUma.objects.bulk_create(fresh)
        TrainedUma.objects.bulk_update(
            changed,
            ["uma", "rank_score", "imported_at", "missing_from_import", "updated_at",
             "game_data", "memo", "is_borrowed", *STAT_FIELDS, *[f"apt_{key}" for key in APTITUDE_KEYS]],
        )
        # Skills are replaced whole: two queries for the lot, not two per veteran.
        by_key = {veteran.import_key: veteran for veteran in [*fresh, *changed]}
        TrainedUmaSkill.objects.filter(trained_uma__in=changed).delete()
        TrainedUmaSkill.objects.bulk_create(
            row for item in items for row in _skill_rows(by_key[item["import_key"]], item["skills"])
        )
    return {"created": len(fresh), "updated": len(changed), "removed": len(removed), "kept": len(kept)}
