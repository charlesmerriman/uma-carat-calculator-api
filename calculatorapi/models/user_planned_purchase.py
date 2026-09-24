from django.core.exceptions import ValidationError
from django.db import models

from .anniversary_event_product import AnniversaryEventProduct
from .custom_user import CustomUser
from .income_profile import IncomeProfile
from .support_card import SupportCard
from .uma import Uma


class UserPlannedPurchase(models.Model):
    """A purchase the user intends to make at a campaign.

    Mirrors UserPlannedBanner: user-owned, replaced wholesale by the
    /calculator-data PATCH upsert, and using the same "at most one of two
    nullable FKs" check-constraint idiom for its target.

    `target_uma` / `target_support` record which card a selector will be spent
    on. Both stay null for carat packs, and for a selector the user has not
    decided on yet — so unlike UserPlannedBanner's constraint this one permits
    neither being set.

    WHOSE PURCHASES: THE STATS BLOCK'S, NOT THE PLAN'S
    --------------------------------------------------
    A purchase is a fact about what the person will spend, so it never lives
    on a Plan (models/plan.py). It belongs to one of the owner's stats blocks:
    `income_profile` null means the account's own purchases, set means the
    purchases of that IncomeProfile. A plan reaches them through the same
    pointer it reads its stats through, and plans.purchase_scope() is the only
    place that decides which. `user` stays the owner and the ownership check
    either way; a profile's purchases always belong to the profile's owner.

    CASCADE on the profile, deliberately: turning "separate resources" off
    already deletes an unshared profile's stats, and its purchases go with
    them. SET_NULL would fold them into the account's list, and a product
    planned on both sides would become two rows for one product, one of which
    the Selectors page cannot show but the projection still credits.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    income_profile = models.ForeignKey(
        IncomeProfile,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="planned_purchases",
    )
    product = models.ForeignKey(AnniversaryEventProduct, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    target_uma = models.ForeignKey(
        Uma,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    target_support = models.ForeignKey(
        SupportCard,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(target_uma__isnull=True)
                    | models.Q(target_support__isnull=True)
                ),
                name="at_most_one_selector_target",
            )
        ]
        verbose_name = "planned purchase"
        verbose_name_plural = "planned purchases"

    def clean(self):
        if self.target_uma and self.target_support:
            raise ValidationError("Cannot set both target_uma and target_support.")
        # The API can never produce this (the scope comes from an owned plan);
        # this is the backstop for the admin form.
        if (
            self.income_profile_id is not None
            and self.income_profile.user_id != self.user_id
        ):
            raise ValidationError(
                "A purchase's income profile must belong to the same user."
            )

    def __str__(self):
        return f"{self.user.username} - {self.product.name} x{self.quantity}"
