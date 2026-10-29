"""
Saving one user collection from a PATCH /calculator-data body: planned banners,
planned purchases and step-up picks, each reconciled against what is stored
(views/calculator.py hands each one here).

The contract is the auto-save's: an absent key leaves a collection alone, `[]`
deletes it, a row with an id updates that row, a row without one is created.
Most of this module is about doing LESS on a typical save, where every row but
one arrives exactly as stored (2026-10-29, when saves were ~45% of the server's
time at the daily peak): an unchanged row is skipped (_row_is_unchanged), and
an id-less body that would recreate exactly the stored rows is skipped whole
(_idless_body_is_unchanged).
"""

from collections import Counter

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response


def _writable_columns(serializer_class, context):
    """Pair each writable field of `serializer_class` with the column it writes.

    Returns ({body key: (serializer field, model column)}, {writable keys that
    write no single column}). _row_is_unchanged can only vouch for the first
    kind; a key of the second kind always takes the full save path.

    Built from a throwaway serializer once per collection per request rather
    than cached at module scope: bound DRF fields are not meant to be shared
    across threads, and building them costs well under a millisecond.
    """
    model = serializer_class.Meta.model
    by_name = {column.name: column for column in model._meta.concrete_fields}
    comparable, opaque = {}, set()
    for key, field in serializer_class(context=context or {}).fields.items():
        if field.read_only:
            # `id`, and whatever the scope writes (`user`, `plan`): the
            # serializer ignores these keys in a body, so they compare as equal.
            continue
        column = by_name.get(field.source)
        if column is None:
            opaque.add(key)
        else:
            comparable[key] = (field, column)
    return comparable, opaque


def _as_pk(model, value):
    """`value` as `model`'s primary key, or None if it can't be one.

    The stored rows are keyed by int, and a JSON body could carry "12". None
    reads as "not found", the same 404 a foreign id gets (it used to be a 500).
    """
    try:
        return model._meta.pk.to_python(value)
    except (DjangoValidationError, TypeError, ValueError):
        return None


def _row_is_unchanged(instance, row, columns):
    """Would saving `row` over `instance` write exactly what is stored?

    Only keys the body carries are compared (the save is partial, so an absent
    key leaves its column alone either way). A plain value is parsed the way
    the serializer would parse it, validators included, so a malformed value
    still reaches the serializer and gets its 400 even on a row that otherwise
    looks untouched. A relation is compared by id WITHOUT the lookup the
    serializer makes, which is the point: that lookup is a query per field
    per row. An id that names a since-deleted target and is already stored
    therefore passes, like any grandfathered value.

    Anything this can't vouch for answers False, and False is always safe: the
    row takes the same validate-and-save path every row took before.

    Not run here: the serializer's validate() and validate_<field>() methods.
    The banner and purchase serializers have no validate_<field>(), and their
    validate() only checks (it never rewrites attrs), which is what makes
    "equal to stored" mean "the save is a no-op". A serializer that grows a
    transforming validate_<field>() or validate() must revisit this.
    """
    comparable, opaque = columns
    for key, value in row.items():
        if key in opaque:
            return False
        if key not in comparable:
            continue
        incoming = _incoming_value(*comparable[key], value)
        if incoming is _UNPARSEABLE or incoming != getattr(instance, comparable[key][1].attname):
            return False
    return True


# What _incoming_value answers for a value it can't vouch for.
_UNPARSEABLE = object()


def _incoming_value(field, column, value):
    """`value` parsed the way the serializer's `field` would store it in
    `column`, or _UNPARSEABLE. A relation is its target's id, parsed without
    the lookup the serializer would make (see _row_is_unchanged)."""
    try:
        if column.is_relation:
            # DRF's PrimaryKeyRelatedField refuses a boolean pk, and a null
            # where the field disallows one.
            if isinstance(value, bool) or (value is None and not field.allow_null):
                return _UNPARSEABLE
            return None if value is None else column.target_field.to_python(value)
        return field.run_validation(value)
    except (DRFValidationError, DjangoValidationError, TypeError, ValueError):
        return _UNPARSEABLE


def _idless_body_is_unchanged(rows, stored, columns):
    """Would an id-less body (the step-up picks) recreate exactly what is stored?

    Such a body is a delete-all-then-create (replace_user_rows says why), so
    an untouched set of picks used to be deleted and recreated on every
    auto-save: a lookup per reference per pick, every insert, and Postgres's
    foreign-key checks on each (pg_stat_statements, October: 5.9M step-up
    lookups). Equal as a MULTISET of rows, compared field by field the way
    _row_is_unchanged compares one row, means the recreate would change
    nothing, so the collection is left alone.

    Only when EVERY row carries every writable column: a row that leaves one
    out would be recreated with that column's default, which a comparison
    against the stored value can't see. Anything else answers False, and False
    is always safe: the body takes the delete-and-recreate path as before.
    """
    comparable, opaque = columns
    keys = sorted(comparable)
    if len(rows) != len(stored):
        return False
    incoming = Counter()
    for row in rows:
        if any(key in opaque for key in row) or set(row) - {"id"} != set(keys):
            return False
        values = tuple(_incoming_value(*comparable[key], row[key]) for key in keys)
        if _UNPARSEABLE in values:
            return False
        incoming[values] += 1
    kept = Counter(
        tuple(getattr(instance, comparable[key][1].attname) for key in keys) for instance in stored
    )
    return incoming == kept


def replace_user_rows(rows, *, model, serializer_class, scope, missing_message,
                      context=None):
    # pylint: disable=too-many-arguments
    # Every one after `rows` is keyword-only and names a distinct part of the
    # contract below; bundling them into a config object would hide which
    # collection is being reconciled at each call site.
    """Reconcile one of the PATCH body's user-owned collections against the DB.

    The contract every such collection shares:

      * key absent from the body (rows is None) -> leave the collection alone
      * []                                      -> delete every row
      * row carrying an id                      -> partial update, 404 if the
                                                   id isn't inside `scope`
      * row without an id                       -> create, inside `scope`

    `scope` is the filter that says WHICH rows this body owns, and always
    carries the user. It is one dict for the delete, the lookup and the save,
    so the three can never disagree. Each collection narrows differently:
    banners to the named plan (`plan`), purchases to the stats block that
    plan reads (`income_profile`, None for the account's own; see
    plans.purchase_scope), step-up selections to the account alone. The
    narrowing is not optional in spirit: the delete below removes every row
    the body did not name, so reconciling plan A's body against ALL of the
    user's banner rows would wipe plans B through E, and reconciling one
    profile's purchases against the account's would do the same. The caller
    resolves the plan through plans.get_owned_plan(), so a scope that reaches
    here is already known to be this user's.

    `context` is handed to every row's serializer. Collections that validate a
    row against something outside it (step-up selections against the JP cutoff,
    and against what the user already had stored) pass their shared lookups in
    that way rather than rebuilding them per row.

    Returns an error Response to bail out with, or None on success. The caller
    runs inside transaction.atomic(), so returning early rolls back whatever
    earlier collections had already written -- a half-saved plan is worse than
    a rejected one.

    NOTE for collections with a UNIQUE constraint: sending rows WITHOUT ids
    makes this a delete-all-then-create, which is collision-free. Sending ids
    updates rows one at a time, so moving a value between two rows (a card from
    slot 3 to slot 4) transiently duplicates it and trips the constraint.
    UserStepUpSelection relies on the id-less form.

    A row carrying an id that arrives EXACTLY as stored is skipped: no
    validation, no write. Auto-save sends the whole plan after every edit, so
    on a typical save every row but one is unchanged, and re-validating and
    rewriting each cost four queries per row (133 for a 30-row plan, measured
    2026-10-29, when this was ~45% of the server's time at the daily peak).
    See _row_is_unchanged for what "exactly" means. Skipping an untouched
    row's validation is the grandfathering every collection already wants:
    a rule that tightened after the row was saved must not 400 a plan its
    owner never touched (UserPlannedPurchaseSerializer._pairing_is_unchanged).
    An id-less body that would recreate exactly the stored rows is skipped
    whole, for the same reasons (_idless_body_is_unchanged).
    """
    if rows is None:
        return None

    incoming_ids = [row["id"] for row in rows if "id" in row]
    # An id-less body describing exactly the stored rows changes nothing: one
    # read instead of a delete and a create per row.
    if rows and not incoming_ids:
        if _idless_body_is_unchanged(
            rows, list(model.objects.filter(**scope)), _writable_columns(serializer_class, context)
        ):
            return None
    model.objects.filter(**scope).exclude(id__in=incoming_ids).delete()

    # What the delete left is exactly the rows this body names and owns:
    # read them in ONE query, where looking each up cost one query per row.
    # An id-less body (step-up selections) has nothing left to read.
    stored, columns = {}, None
    if incoming_ids:
        stored = {instance.pk: instance for instance in model.objects.filter(**scope)}
        columns = _writable_columns(serializer_class, context)

    for row in rows:
        row_id = row.get("id")
        if row_id:
            # An id from ANOTHER of this user's plans is "not found" too:
            # a body is reconciled against one plan, never across them.
            instance = stored.get(_as_pk(model, row_id))
            if instance is None:
                return Response({"error": missing_message},
                                status=status.HTTP_404_NOT_FOUND)
            if _row_is_unchanged(instance, row, columns):
                continue
            serializer = serializer_class(
                instance, data=row, partial=True, context=context or {}
            )
        else:
            serializer = serializer_class(data=row, context=context or {})

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(**scope)

    return None
