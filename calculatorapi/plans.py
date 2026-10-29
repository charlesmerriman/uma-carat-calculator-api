"""The operations on a user's pull plans. Every view goes through these.

Kept out of the views on purpose. Sharing a plan is copy_plan() plus a
visibility rule (copy_shared_plan, called from views/plan_public.py), and a
later "browse published plans" page will be the same again, so the copy has to
be callable from places that are not the /plans routes. The ownership check
lives here for the opposite reason: it must be impossible to reach a plan by id
without passing through it.

What a plan is, and what it must never hold: models/plan.py.
"""

from django.db import IntegrityError, transaction
from django.db.models import F

from calculatorapi.models import (
    CustomUser,
    DEFAULT_PLAN_NAME,
    GameStats,
    IncomeProfile,
    PLAN_CAP,
    Plan,
    UserPlannedBanner,
    UserPlannedPurchase,
)
from calculatorapi.public_ids import generate_public_id


class PlanCapReached(Exception):
    """The account already holds PLAN_CAP plans, so another cannot be created."""


def generate_unique_public_id():
    """Generate a public ID that is not already assigned to a plan.

    Every path that creates a plan, or resets its link, passes this explicitly
    rather than leaning on the field default, because the default cannot see
    the table: a collision there would surface as an IntegrityError on the
    create instead of a quiet second draw here. At 62^8 ids the loop almost
    never runs twice; the unique constraint is still the backstop for two
    requests drawing the same id at the same instant.
    """
    public_id = generate_public_id()
    while Plan.objects.filter(public_id=public_id).exists():
        public_id = generate_public_id()
    return public_id


def get_active_plan(user):
    """The plan the calculator opens on. Creates the account's first if needed.

    This is what makes "every account has at least one plan" true without a
    signal or a change to the sign-up path: a new social sign-up, a
    createsuperuser account and a user the backfill skipped (no planned rows)
    all get their first plan here, the first time anything asks for it.

    Also repairs the one inconsistent state that can exist: plans but none
    active. The partial unique constraint forbids TWO active plans, not zero,
    and a delete or a crash between two writes could leave zero. The oldest
    plan is promoted, so the answer is stable rather than arbitrary.
    """
    plan = _find_or_create_active_plan(user)
    _adopt_planless_rows(user, plan)
    return plan


def _find_or_create_active_plan(user):
    plan = Plan.objects.filter(user=user, is_active=True).first()
    if plan is not None:
        return plan

    oldest = Plan.objects.filter(user=user).first()  # Meta.ordering: oldest first
    if oldest is not None:
        oldest.is_active = True
        oldest.save(update_fields=["is_active", "updated_at"])
        return oldest

    try:
        # Its own savepoint, so losing the race below does not poison a
        # transaction the caller already has open (PATCH /calculator-data).
        with transaction.atomic():
            return Plan.objects.create(
                user=user,
                name=DEFAULT_PLAN_NAME,
                is_active=True,
                public_id=generate_unique_public_id(),
            )
    except IntegrityError:
        # Two first requests at once (the app fires its prefetch and the real
        # fetch close together). one_active_plan_per_user let exactly one
        # through; the loser reads the winner's row.
        return Plan.objects.get(user=user, is_active=True)


def _adopt_planless_rows(user, plan):
    """TRANSITIONAL (multi-plan, release 1 of 2): delete with release 2.

    `git push origin master` runs migrate while the OLD code is still serving.
    For those seconds, an autosave handled by the old code writes planned rows
    with a `user` and no `plan` -- after the backfill has already run, so it
    never sees them. Every read filters on `plan`, so such a row would simply
    vanish from its owner's calculator until release 2 swept it up.

    One UPDATE that almost always matches nothing, in exchange for nobody
    losing the banner they added during a deploy. Release 2 makes `plan`
    NOT NULL, after which no such row can exist and this goes away.
    """
    UserPlannedBanner.objects.filter(user=user, plan__isnull=True).update(plan=plan)


def get_owned_plan(user, plan_id):
    """The plan with this id IF it belongs to `user`, else Plan.DoesNotExist.

    THE ownership check. A view must never write `Plan.objects.get(id=...)`
    itself: the `user=` filter is what stops one account reading or, far worse,
    overwriting another's rows, and a lookup that forgets it fails silently.
    Somebody else's plan and a plan that does not exist are deliberately the
    same error, so a caller cannot probe which ids are taken.

    A non-numeric id (a client bug, or someone poking) is the same "no such
    plan" rather than a 500 from int().
    """
    try:
        return Plan.objects.get(id=int(plan_id), user=user)
    except (TypeError, ValueError) as exc:
        raise Plan.DoesNotExist from exc


def lock_plan(plan):
    """Hold `plan`'s row lock until the caller's transaction ends.

    Saving a plan's rows and deleting the plan both touch the same two
    tables, in opposite orders: the save writes the banner rows, then stamps
    `plan.updated_at`; the delete takes the banner rows (CASCADE), then the
    plan. On Postgres two requests doing that at once can each hold what the
    other wants next, and Postgres kills one with "deadlock detected" (a 500
    on an auto-save, 2026-10-28). Both paths call this FIRST, before any row
    is written, so the second one waits for the first to finish instead.

    Raises Plan.DoesNotExist when the plan is gone by the time the lock is
    granted: the request waited on a delete. The caller treats it like any
    other missing plan.

    Must run inside transaction.atomic(). SQLite has no row locks, so there
    (and in the test suite) this is only the existence check.
    """
    locked = Plan.objects.select_for_update().filter(pk=plan.pk).values_list("pk", flat=True)
    if not list(locked):
        raise Plan.DoesNotExist


def get_shared_plan(public_id):
    """The plan behind a share link, or Plan.DoesNotExist.

    THE visibility check for the public routes, as get_owned_plan() is the
    ownership check for the owner's. A plan whose sharing is off and an id
    that was never issued (or was replaced by Reset link) are deliberately the
    same error, so nobody can probe which ids belong to a private plan.
    """
    return Plan.objects.select_related("income_profile", "user").get(
        public_id=public_id, is_shared=True
    )


def stats_target(plan):
    """WHOSE STATS a plan is projected against: its income profile if it has
    one, else its owner's own row. Both carry the same columns
    (models/game_stats.py), so a caller reads and writes the result the same
    way either way; views/income_profile.py stats_serializer() picks the
    serializer. This is the only place the choice is made: every read of
    `user_stats_data` and every save of it goes through here, so a plan can
    never read one block and write the other.
    """
    return plan.income_profile or plan.user


def purchase_scope(plan):
    """WHOSE PURCHASES a plan sees: the filter (and the save kwargs) that
    narrows UserPlannedPurchase to the rows of the stats block this plan reads.
    `income_profile` is None for a plan on the account's own stats, which
    Django filters as IS NULL, so the account's purchases and each profile's
    are disjoint sets under one owner. The companion of stats_target(): a
    plan's purchases follow its stats, never its rows, and this is the only
    place that decides, so a plan can never read one block's purchases and
    write another's. Every read and reconcile of `user_planned_purchase_data`
    goes through here.
    """
    return {"user": plan.user, "income_profile": plan.income_profile}


def attach_income_profile(plan, *, seed_from=None):
    """Give `plan` its own stats: a new IncomeProfile seeded from whatever the
    plan reads today, then pointed at. No-op if it already has one.

    Seeding from the current target rather than from defaults is what the
    person expects: they turn this on for their alt account's plan and edit
    the numbers that differ, instead of re-entering ranks and toggles from
    scratch. GameStats.field_names() drives the copy, so a field added to the
    stats block is copied without anyone remembering this function exists.

    The planned purchases are copied for the same reason: they belong to the
    stats block (models/user_planned_purchase.py), and turning the toggle on
    must not change what is on screen. Without the copy the Selectors page
    would empty and the projection would move the moment it was flipped.

    `seed_from` is another PLAN whose block (its stats AND its purchases,
    through stats_target() and purchase_scope()) seeds the new profile
    instead, possibly someone else's: copy_shared_plan() passes the shared
    plan when its owner chose to share their resources. Only values and
    catalogue ids are read from it. The new profile and its purchase rows
    belong to `plan.user` from the moment they exist, so nothing points back
    at the sharer and later edits on either side stay their own.
    """
    if plan.income_profile_id is not None:
        return plan
    seed_plan = seed_from if seed_from is not None else plan
    source = stats_target(seed_plan)
    values = {name: getattr(source, name) for name in GameStats.field_names()}
    # Read BEFORE the pointer moves: the scope is the block the seed plan
    # reads today, and after an attach to that same plan it would be the new,
    # empty profile.
    purchases = list(UserPlannedPurchase.objects.filter(**purchase_scope(seed_plan)))
    with transaction.atomic():
        profile = IncomeProfile.objects.create(user=plan.user, **values)
        UserPlannedPurchase.objects.bulk_create(
            [
                UserPlannedPurchase(
                    user=plan.user,
                    income_profile=profile,
                    product_id=row.product_id,
                    quantity=row.quantity,
                    target_uma_id=row.target_uma_id,
                    target_support_id=row.target_support_id,
                )
                for row in purchases
            ]
        )
        plan.income_profile = profile
        plan.save(update_fields=["income_profile", "updated_at"])
    return plan


def detach_income_profile(plan):
    """Send `plan` back to its owner's own stats. The profile is deleted only
    if no other plan still points at it (a Duplicate shares the pointer), and
    its purchases go with it (CASCADE, see models/user_planned_purchase.py)."""
    profile = plan.income_profile
    if profile is None:
        return plan
    with transaction.atomic():
        plan.income_profile = None
        plan.save(update_fields=["income_profile", "updated_at"])
        _drop_profile_if_unused(profile)
    return plan


def _drop_profile_if_unused(profile):
    """Delete a profile nothing points at any more. Called after a detach and
    after a plan delete, so the table never accumulates orphans nobody can
    reach. `Plan.income_profile` is SET_NULL, so nothing here can take rows."""
    if profile is not None and not profile.plans.exists():
        profile.delete()


def set_active_plan(plan):
    """Make `plan` its owner's active plan.

    Two writes in one transaction, the clear FIRST: one_active_plan_per_user is
    checked per statement, so activating before deactivating would trip it.
    update() rather than save() on the clear because it may match zero rows or
    one and the instances are not in hand.
    """
    with transaction.atomic():
        Plan.objects.filter(user=plan.user, is_active=True).exclude(
            id=plan.id
        ).update(is_active=False)
        if not plan.is_active:
            plan.is_active = True
            plan.save(update_fields=["is_active", "updated_at"])
    return plan


def create_plan(user, name, *, copy_from=None):
    """A new plan for `user`: blank, or a copy of `copy_from`'s rows.

    Raises PlanCapReached at the cap. The cap is checked HERE and only here,
    on creation. It never blocks a save to an existing plan and never deletes
    one, so an account over the cap (were the cap ever lowered) keeps working
    and simply cannot add more -- the same "never reject a save the person
    could make before" rule the oshi slots follow.

    The new plan is not made active. Whether to switch to it is the caller's
    decision, and for a copy taken from someone else it usually is not.
    """
    # get_active_plan first, so an account that has never loaded the calculator
    # still ends up with its Main plan as well as the one being created, and
    # the new plan is never silently the only (and inactive) one.
    get_active_plan(user)

    with transaction.atomic():
        _refuse_at_cap(user)
        if copy_from is not None:
            return copy_plan(copy_from, owner=user, name=name)
        return Plan.objects.create(
            user=user, name=name, public_id=generate_unique_public_id()
        )


def _refuse_at_cap(user):
    """Raise PlanCapReached if `user` already holds PLAN_CAP plans. Must run
    INSIDE the caller's transaction, before its create.

    Locks the account row for the count-then-create, or two simultaneous
    POSTs both read "4" and both create a fifth. PostgreSQL honours the lock;
    SQLite (dev) ignores it and serialises writes anyway. Shared by
    create_plan() and copy_shared_plan(), the two ways an account gains a plan.
    """
    CustomUser.objects.select_for_update().get(pk=user.pk)
    if Plan.objects.filter(user=user).count() >= PLAN_CAP:
        raise PlanCapReached()


def copy_plan(source, *, owner, name, carry_notes=None):
    """Copy `source`'s banner rows into a NEW plan owned by `owner`.

    `owner` need not be `source.user`. A Duplicate is the same account; a copy
    taken through a share link (copy_shared_plan) is this same call across two
    accounts, which is why it takes the owner rather than assuming it. It works
    across accounts because a plan holds nothing about its author
    (models/plan.py): a row is a catalogue FK, two counts and two card ids, all
    of which mean the same thing to anyone.
    The row's `note` is the exception and is blanked across accounts, unless
    `carry_notes` says otherwise (below).

    The copy is a new, PRIVATE plan whoever makes it: a new public_id, sharing
    off, both share options off, copy_count 0 (the model defaults, left alone
    on purpose). Sharing the original does not share the copy.

    `carry_notes`: None applies the rule above (kept within one account,
    blanked across). True or False overrides it, which is how a sharer's
    `share_notes` choice reaches the copy. An explicit argument rather than a
    mode flag, so the caller states the one thing it decided.

    Does NOT check the cap or ownership of `source`. Both are the caller's
    job -- create_plan() does the first, get_owned_plan() the second -- so that
    copy_shared_plan() can apply its own rule for reading the source (the plan
    is shared) without this function growing a mode flag.
    """
    same_owner = owner.pk == source.user_id
    # Notes are the author's private text, the one thing on a row that is NOT
    # "a catalogue FK and two counts". Same default rule as the income_profile
    # pointer below: they survive a Duplicate inside one account and are
    # blanked the moment a copy changes owner, so a shared plan never leaks
    # them unless its owner turned share_notes on.
    keep_notes = same_owner if carry_notes is None else carry_notes
    with transaction.atomic():
        new_plan = Plan.objects.create(
            user=owner,
            name=name,
            public_id=generate_unique_public_id(),
            # The pointer stays only within one account: a Duplicate of "my
            # alt's plan" should read my alt's numbers too. Across accounts it
            # MUST be dropped. The profile is the author's facts, and a plan
            # that reaches another person carries nothing of its author.
            income_profile=source.income_profile if same_owner else None,
        )
        UserPlannedBanner.objects.bulk_create(
            [
                UserPlannedBanner(
                    plan=new_plan,
                    # TRANSITIONAL: `user` is still NOT NULL until release 2
                    # drops it. See models/user_planned_banner.py.
                    user=owner,
                    banner_uma_id=row.banner_uma_id,
                    banner_support_id=row.banner_support_id,
                    banner_step_up_id=row.banner_step_up_id,
                    number_of_pulls=row.number_of_pulls,
                    reserved_copies=row.reserved_copies,
                    note=row.note if keep_notes else "",
                    # Catalogue ids, so they mean the same thing to anyone.
                    primary_card=row.primary_card,
                    second_card=row.second_card,
                    primary_target=row.primary_target,
                    # Choices about the row: they mean the same thing to anyone.
                    tickets_to_use=row.tickets_to_use,
                    paid_carat_use=row.paid_carat_use,
                    excluded_from_projection=row.excluded_from_projection,
                    # A fact about the AUTHOR'S game account (pulls they made),
                    # so it follows the notes' rule, never the choices'.
                    pulls_already_made=row.pulls_already_made if same_owner else 0,
                )
                for row in source.banners.all()
            ]
        )
    return new_plan


def copy_shared_plan(source, *, owner, name):
    """`owner` takes a copy of the SHARED plan `source` into their account.

    The caller has already established that `source.is_shared` is on (the
    public routes 404 otherwise); this function does not re-check it, the same
    split as copy_plan() leaving ownership to get_owned_plan().

    Raises PlanCapReached at the cap, like create_plan(). The copy is created
    INACTIVE, like every new plan; the client activates it.

    What crosses: the rows, always. The notes only if the sharer turned
    share_notes on. Their resources (stats AND planned purchases, one block)
    only if they turned share_stats on, and then as a NEW income profile the
    copier owns, seeded from the sharer's block (attach_income_profile's
    seed_from), never as a pointer to it. The plan name never (the copier
    names the copy). Then the source's copy_count goes up by one, in the same
    transaction, so a refused copy is never counted.

    The owner opening their own link and copying gets an ordinary Duplicate
    and NO count: the number is meant to say how many other people took the
    plan, and a sharer clicking their own link a few times would inflate it.
    """
    if owner.pk == source.user_id:
        return create_plan(owner, name, copy_from=source)

    # As in create_plan(): an account that has never opened the calculator
    # still ends up with its Main plan as well as the copy.
    get_active_plan(owner)
    with transaction.atomic():
        _refuse_at_cap(owner)
        new_plan = copy_plan(
            source, owner=owner, name=name, carry_notes=source.share_notes
        )
        if source.share_stats:
            attach_income_profile(new_plan, seed_from=source)
        count_copy(source)
    return new_plan


def count_copy(source):
    """One more copy of `source` was taken through its share link.

    An F() expression, so two copies at the same instant both count (a read,
    add one, save would let the second overwrite the first). update() rather
    than save() also leaves `updated_at` alone: being copied is not an edit,
    and the landing card shows that date as "Updated".
    Called by copy_shared_plan() and by the anonymous copy route.
    """
    Plan.objects.filter(pk=source.pk).update(copy_count=F("copy_count") + 1)


def reset_public_id(plan):
    """Give `plan` a new share id. The old link stops working for good, and
    nothing else changes: sharing stays on or off as it was, and the count is
    kept, since the copies already taken still happened."""
    plan.public_id = generate_unique_public_id()
    plan.save(update_fields=["public_id", "updated_at"])
    return plan


def delete_plan(plan):
    """Delete `plan` and its rows. Returns the owner's active plan afterwards.

    Deleting the ACTIVE plan leaves the account with none active, and
    get_active_plan() then promotes the oldest survivor, which for nearly
    everyone is their Main plan -- a predictable place to land.

    Deleting an account's LAST plan is not refused here: get_active_plan()
    would simply start a fresh Main plan, so nothing breaks. Whether to OFFER
    that is a product decision and lives in the view (views/plan_routes.py
    refuses it).
    """
    user = plan.user
    profile = plan.income_profile
    with transaction.atomic():
        try:
            lock_plan(plan)
        except Plan.DoesNotExist:
            # Another delete of this plan finished while this one waited (a
            # double click). Nothing is left to delete.
            return get_active_plan(user)
        plan.delete()  # CASCADE takes its UserPlannedBanner rows
        _drop_profile_if_unused(profile)
        return get_active_plan(user)
