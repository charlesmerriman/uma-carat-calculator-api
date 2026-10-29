from rest_framework import serializers

from calculatorapi.eligibility import (
    build_first_jp_date_maps,
    selection_refusal_reason,
)
from calculatorapi.models import (
    AnniversaryEventProduct,
    SupportCard,
    Uma,
    UserPlannedPurchase,
)


class UserPlannedPurchaseSerializer(serializers.ModelSerializer):
    """A purchase the user plans to make at a campaign.

    Mirrors UserPlannedBannerSerializer: PK-writable relations on the way in,
    ids on the way out. Unlike planned banners this does NOT nest the product on
    read — the frontend already holds every campaign and its products in
    anniversary_event_data, so an id is enough to join on and nesting would
    duplicate the catalogue once per planned row.
    """

    product = serializers.PrimaryKeyRelatedField(
        queryset=AnniversaryEventProduct.objects.all()
    )
    target_uma = serializers.PrimaryKeyRelatedField(
        queryset=Uma.objects.all(), required=False, allow_null=True
    )
    target_support = serializers.PrimaryKeyRelatedField(
        queryset=SupportCard.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = UserPlannedPurchase
        fields = (
            "id",
            "user",
            "product",
            "quantity",
            "target_uma",
            "target_support",
        )
        read_only_fields = ("user",)

    def validate(self, attrs):
        # On a PATCH only the changed keys arrive, so fall back to the instance
        # for anything absent -- otherwise partial updates would validate against
        # a half-empty picture and let an ineligible pairing through.
        instance = self.instance
        product = attrs.get("product") or getattr(instance, "product", None)
        target_uma = attrs.get(
            "target_uma", getattr(instance, "target_uma", None)
        )
        target_support = attrs.get(
            "target_support", getattr(instance, "target_support", None)
        )

        if target_uma and target_support:
            raise serializers.ValidationError(
                "Cannot provide both target_uma and target_support."
            )

        if product is None:
            return attrs

        # A carat pack has nothing to select, so a target on one is a client bug
        # rather than a harmless extra field -- reject it rather than silently
        # storing something no reader will ever look at.
        if not product.grants_selector and (target_uma or target_support):
            raise serializers.ValidationError(
                "A carat pack cannot have a selector target."
            )
        if product.product_type == "uma_selector" and target_support:
            raise serializers.ValidationError(
                "A uma selector cannot target a support card."
            )
        if product.product_type == "support_selector" and target_uma:
            raise serializers.ValidationError(
                "A support selector cannot target an uma."
            )

        # Only what the user is actually doing gets checked against the cutoff;
        # a pairing already sitting on this row is left alone. See
        # _pairing_is_unchanged for why that distinction has to exist.
        if not self._pairing_is_unchanged(product, target_uma, target_support):
            self._validate_target_eligibility(product, target_uma, target_support)
        return attrs

    def _pairing_is_unchanged(self, product, target_uma, target_support):
        """Is this row's (product, target) exactly what is already stored?

        Cutoffs are reference data that legitimately moves. Most campaigns
        carried no cutoff at all until 0034 backfilled one, and null reads as
        UNRESTRICTED -- so their pickers offered every card ever released, and
        users saved picks that were perfectly legal at the time. Editors are
        expected to keep correcting these in the admin as real dates surface.

        Re-checking an untouched row would therefore reject a plan the user
        never changed. And because the client saves the whole plan in one atomic
        PATCH, that rejection is not confined to the offending pick: it 400s the
        entire request, so a single stale selector target locks the account out
        of saving its stats and banners too, with no way back except deleting
        the pick.

        Grandfathering the stored pairing keeps the backstop pointed at what it
        was written for -- a stale client persisting a NEW ineligible pick --
        while never letting a shared-data correction brick an individual's
        plan. Product is part of the identity on purpose: moving the same target
        onto a different campaign is a different pairing, and gets re-checked
        against that campaign's cutoff.
        """
        instance = self.instance
        if instance is None:
            return False
        return (
            product == instance.product
            and target_uma == instance.target_uma
            and target_support == instance.target_support
        )

    def _validate_target_eligibility(self, product, target_uma, target_support):
        """Reject a target this selector cannot actually grant.

        The client filters its picker by the same rules, so this is the backstop
        rather than the primary gate -- but a stale client holding a campaign
        whose cutoff has since been corrected would otherwise persist a plan the
        game will not honour.

        Note there is NO early return on a null cutoff. Null means the TEMPORAL
        gate is unrestricted, not that the pick is unconditionally legal: a
        time-limited or non-★3 uma is refused by every selector there is.
        """
        target = target_uma or target_support
        if target is None:
            return

        cutoff = product.effective_jp_cutoff_date
        # The date is only consulted by the temporal gate, so an unrestricted
        # cutoff skips building the maps entirely.
        first_jp = None
        if cutoff is not None:
            # /calculator-data passes the cached maps (first_jp_date_maps());
            # anything else that saves a purchase builds them here.
            uma_dates, support_dates = self.context.get("first_jp_dates") or build_first_jp_date_maps()
            dates = uma_dates if target_uma else support_dates
            first_jp = dates.get(target.id)

        refusal = selection_refusal_reason(target, first_jp, cutoff, "this selector")
        if refusal:
            raise serializers.ValidationError(refusal)
