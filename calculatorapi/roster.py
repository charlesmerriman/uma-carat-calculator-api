"""The operations on what a player owns. Every /roster view goes through these.

What a row is, and the rule that hand edits win: models/roster.py. The wire
shape and input validation: views/roster.py.
"""

from django.db import transaction

from calculatorapi.models import (
    CustomUser,
    OwnedSupportCard,
    OwnedUma,
    SupportCard,
    TrainedUmaSource,
    Uma,
)
from calculatorapi.trained_umas import game_time, strip_other_players


class RosterRefused(Exception):
    """A write the rules refuse. The message is shown to the player as is."""


def owned_umas(user):
    """The player's umas. `game_data` deferred: only an export would read it."""
    return OwnedUma.objects.filter(user=user).defer("game_data")


def owned_cards(user):
    return OwnedSupportCard.objects.filter(user=user).defer("game_data")


def _check_ids(model, ids, what):
    if model.objects.filter(id__in=set(ids)).count() != len(set(ids)):
        raise RosterRefused(f"One of those {what} isn't on the site any more.")
    if len(set(ids)) != len(ids):
        raise RosterRefused(f"The same {what[:-1]} is in there twice.")


def _edit(model, user, field, changes, values):
    """Apply grid edits to one table. Each change is {<field>: id, owned, ...};
    `values` names the columns a change may set. The row becomes `manual`
    (where its values last came from); the next import still overwrites it."""
    wanted = {f"{field}__in": [change[field] for change in changes]}
    rows = {getattr(row, f"{field}_id"): row for row in model.objects.filter(user=user, **wanted)}
    gone = [rows[c[field]].id for c in changes if not c["owned"] and c[field] in rows]
    model.objects.filter(id__in=gone).delete()
    for change in changes:
        if not change["owned"]:
            continue
        row = rows.get(change[field]) or model(user=user, **{f"{field}_id": change[field]})
        row.source = TrainedUmaSource.MANUAL
        for column in values:
            if column in change:
                setattr(row, column, change[column])
        row.save()


def edit(user, *, umas=(), support_cards=()):
    """Grid edits, both tables, one transaction: a refused change writes nothing."""
    _check_ids(Uma, [c["uma"] for c in umas], "umas")
    _check_ids(SupportCard, [c["support_card"] for c in support_cards], "cards")
    with transaction.atomic():
        _edit(OwnedUma, user, "uma", umas, ("stars", "talent_level"))
        _edit(OwnedSupportCard, user, "support_card", support_cards, ("limit_break", "spare_copies"))


def _import(model, user, field, items, values):
    """Replace one table with the file's rows: write each over what is there
    (hand edits included) and remove every row the file lacks."""
    rows = {getattr(row, f"{field}_id"): row for row in model.objects.filter(user=user)}
    in_file = {item[field] for item in items}
    removed = [row.id for key, row in rows.items() if key not in in_file]
    model.objects.filter(id__in=removed).delete()
    fresh, changed = [], []
    for item in items:
        row = rows.get(item[field])
        if row is None:
            row = model(user=user, source=TrainedUmaSource.IMPORT, **{f"{field}_id": item[field]})
            fresh.append(row)
        else:
            changed.append(row)
        for column in values:
            setattr(row, column, item[column])
        raw = item.get("game_data")
        # These files name only the player today; stripped anyway, by the
        # veterans' rule, so a future field naming someone else never lands.
        row.game_data = strip_other_players(raw)[0] if isinstance(raw, dict) else None
        row.obtained_at = game_time(raw.get("create_time")) if isinstance(raw, dict) else None
        row.source = TrainedUmaSource.IMPORT
    model.objects.bulk_create(fresh)
    model.objects.bulk_update(changed, [*values, "game_data", "obtained_at", "source"])
    return {"added": len(fresh), "updated": len(changed), "removed": len(removed)}


def import_roster(user, *, umas=None, support_cards=None):
    """Write a game export's umas and/or cards. Each list given REPLACES that
    table (models/roster.py); a list not given (None) leaves its table alone,
    so dropping only the cards file never empties the umas. Counts per table
    written, {added, updated, removed}."""
    _check_ids(Uma, [i["uma"] for i in umas or []], "umas")
    _check_ids(SupportCard, [i["support_card"] for i in support_cards or []], "cards")
    counts = {}
    with transaction.atomic():
        # Two imports at once run one after the other (trained_umas.import_veterans).
        CustomUser.objects.select_for_update().get(pk=user.pk)
        if umas is not None:
            counts["umas"] = _import(OwnedUma, user, "uma", umas, ("stars", "talent_level"))
        if support_cards is not None:
            counts["support_cards"] = _import(
                OwnedSupportCard, user, "support_card", support_cards, ("limit_break", "spare_copies")
            )
    return counts


def clear(user, kind):
    """Start over: remove every owned uma ("umas") or card ("support_cards")."""
    model = OwnedUma if kind == "umas" else OwnedSupportCard
    model.objects.filter(user=user).delete()
