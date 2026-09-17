from django.db import models
from django.db.models import Q

from .custom_user import CustomUser

# The most plans one account can hold. A flat number for everyone (decided
# 2026-09-17), not a supporter ladder. Enforced only when a plan is CREATED
# (see calculatorapi/plans.py): it never rejects a save to a plan that already
# exists and never deletes one, so lowering it later strands nobody's data.
PLAN_CAP = 5

# What the backfill migration names everyone's existing plan, and what
# plans.get_active_plan() names the first plan of an account that has none.
# The migration carries its own copy of this string on purpose: a migration
# must keep meaning what it meant on the day it ran, even if this changes.
DEFAULT_PLAN_NAME = "Main plan"


class Plan(models.Model):
    """One named pull plan: a list of UserPlannedBanner rows, and nothing else.

    A PLAN HOLDS CHOICES. THE ACCOUNT HOLDS FACTS.
    ----------------------------------------------
    Carats, tickets, ranks, the income toggles, planned purchases and step-up
    picks all stay on the account (CustomUser and its own collections). A plan
    carries only which banners to pull on and how hard. The numbers on screen
    are therefore always the VIEWER'S: the same plan projected for two people
    gives two different answers, which is the point.

    That split is what makes a plan portable. A later feature lets a player
    publish a plan and another player take it, and a plan that never held
    anything about its author needs no stripping when it is copied and cannot
    leak what someone holds or spends. Do not add a field here that describes
    the person rather than the plan.

    OWNERSHIP
    ---------
    A planned row belongs to a plan, and the plan belongs to a user, so "whose
    row is this" is always `row.plan.user`. Views resolve a plan id through
    plans.get_owned_plan() and nothing else; that one function is the
    ownership check for every plan route.

    ONE ACTIVE PLAN PER ACCOUNT
    ---------------------------
    `is_active` marks the plan the calculator opens on. A boolean on the
    person's OWN row rather than a CustomUser.active_plan FK, because that FK
    would be circular and could be pointed at somebody else's plan; this
    cannot. The partial unique constraint below is the backstop against two
    active plans, the same idiom as CustomUser's unique_display_name_ci.
    Every account always has at least one plan: plans.get_active_plan()
    creates the first on demand.

    Not a training `Scenario` (models/scenario.py), which is game content.
    """

    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="plans"
    )
    # Private to its owner today. If plans are ever published, the public title
    # should be asked for at publish time rather than reusing this.
    name = models.CharField(max_length=40)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Oldest first, so the list reads in the order the person made them and
        # the backfilled "Main plan" stays on top. `id` breaks a created_at tie
        # (the backfill creates many plans in the same instant).
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="one_active_plan_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.name}"
