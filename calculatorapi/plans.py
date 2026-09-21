"""The operations on a user's pull plans. Every view goes through these.

Kept out of the views on purpose. A later feature lets players publish a plan
and take somebody else's, and both are copy_plan() plus a visibility rule, so
the copy has to be callable from places that are not the /plans routes. The
ownership check lives here for the opposite reason: it must be impossible to
reach a plan by id without passing through it.

What a plan is, and what it must never hold: models/plan.py.
"""

from django.db import IntegrityError, transaction

from calculatorapi.models import (
    CustomUser,
    DEFAULT_PLAN_NAME,
    GameStats,
    IncomeProfile,
    PLAN_CAP,
    Plan,
    UserPlannedBanner,
)


class PlanCapReached(Exception):
    """The account already holds PLAN_CAP plans, so another cannot be created."""


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
                user=user, name=DEFAULT_PLAN_NAME, is_active=True
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


def attach_income_profile(plan):
    """Give `plan` its own stats: a new IncomeProfile seeded from whatever the
    plan reads today, then pointed at. No-op if it already has one.

    Seeding from the current target rather than from defaults is what the
    person expects: they turn this on for their alt account's plan and edit
    the numbers that differ, instead of re-entering ranks and toggles from
    scratch. GameStats.field_names() drives the copy, so a field added to the
    stats block is copied without anyone remembering this function exists.
    """
    if plan.income_profile_id is not None:
        return plan
    source = stats_target(plan)
    values = {name: getattr(source, name) for name in GameStats.field_names()}
    with transaction.atomic():
        plan.income_profile = IncomeProfile.objects.create(user=plan.user, **values)
        plan.save(update_fields=["income_profile", "updated_at"])
    return plan


def detach_income_profile(plan):
    """Send `plan` back to its owner's own stats. The profile is deleted only
    if no other plan still points at it (a Duplicate shares the pointer)."""
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
        # Lock the account row for the count-then-create, or two simultaneous
        # POSTs both read "4" and both create a fifth. PostgreSQL honours the
        # lock; SQLite (dev) ignores it and serialises writes anyway.
        CustomUser.objects.select_for_update().get(pk=user.pk)
        if Plan.objects.filter(user=user).count() >= PLAN_CAP:
            raise PlanCapReached()
        if copy_from is not None:
            return copy_plan(copy_from, owner=user, name=name)
        return Plan.objects.create(user=user, name=name)


def copy_plan(source, *, owner, name):
    """Copy `source`'s banner rows into a NEW plan owned by `owner`.

    `owner` need not be `source.user`. Today it always is ("Duplicate"); the
    publish and take features are this same call across two accounts, which is
    why it takes the owner rather than assuming it. It works across accounts
    because a plan holds nothing about its author (models/plan.py): a row is a
    catalogue FK and two counts, all of which mean the same thing to anyone.
    The row's `note` is the exception and is blanked across accounts.

    Does NOT check the cap or ownership of `source`. Both are the caller's
    job -- create_plan() does the first, get_owned_plan() the second -- so that
    a future "take a published plan" can apply a different rule for reading
    the source without this function growing a mode flag.
    """
    # Notes are the author's private text, the one thing on a row that is NOT
    # "a catalogue FK and two counts". Same rule as the income_profile pointer
    # below: they survive a Duplicate inside one account and are blanked the
    # moment a copy changes owner, so a published plan never leaks them.
    same_owner = owner.pk == source.user_id
    with transaction.atomic():
        new_plan = Plan.objects.create(
            user=owner,
            name=name,
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
                    note=row.note if same_owner else "",
                )
                for row in source.banners.all()
            ]
        )
    return new_plan


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
        plan.delete()  # CASCADE takes its UserPlannedBanner rows
        _drop_profile_if_unused(profile)
        return get_active_plan(user)
