from django.db import models

from .custom_user import CustomUser
from .game_stats import GameStats


class IncomeProfile(GameStats):
    """A second stats block for an account: the numbers of ANOTHER game account
    the same person plays. A Plan may point at one (Plan.income_profile) and is
    then projected against these ranks, toggles and balances instead of the
    account's own.

    WHY A SEPARATE ROW AND NOT COLUMNS ON THE PLAN
    ----------------------------------------------
    "A plan holds choices, the account holds facts" (models/plan.py) is what
    lets a plan be copied between accounts later without leaking what its
    author holds. Putting stats on the plan would break that. Putting them on
    their own row, owned by the account, keeps it: the plan carries a POINTER,
    and plans.copy_plan() drops the pointer whenever the copy changes owner.

    `user` is kept even though every plan that points here already knows its
    owner. It is the ownership check (a plan may only point at its own owner's
    profile), the CASCADE that takes profiles with a deleted account, and the
    admin's list column.

    Two plans of one account may share a profile. v1 has no picker; sharing
    happens through Duplicate, which keeps the pointer. A profile nobody points
    at any more is deleted by plans._drop_profile_if_unused(), so the table
    never accumulates orphans.

    No name in v1 (decided 2026-09-21): a profile is only ever reached through
    the plans that use it, so a name would have nowhere to appear yet.
    """

    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="income_profiles"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")

    def __str__(self):
        return f"{self.user.username} - profile {self.pk}"
