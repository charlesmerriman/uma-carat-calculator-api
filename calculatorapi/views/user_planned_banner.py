from rest_framework import serializers
from calculatorapi.models import (
    UserPlannedBanner, BannerUma, BannerSupport, BannerStepUp,
)
from .banner_uma import BannerUmaSerializer
from .banner_support import BannerSupportSerializer
from .banner_step_up import BannerStepUpSerializer


class UserPlannedBannerSerializer(serializers.ModelSerializer):
    banner_uma = serializers.PrimaryKeyRelatedField(
        queryset=BannerUma.objects.all(), required=False, allow_null=True
    )
    banner_support = serializers.PrimaryKeyRelatedField(
        queryset=BannerSupport.objects.all(), required=False, allow_null=True
    )
    banner_step_up = serializers.PrimaryKeyRelatedField(
        queryset=BannerStepUp.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = UserPlannedBanner
        fields = (
            "id",
            "user",
            "plan",
            "number_of_pulls",
            "reserved_copies",
            "banner_uma",
            "banner_support",
            "banner_step_up",
        )
        # Neither is writable from a row body. Which plan a row lands in comes
        # from the PATCH's top-level plan_id, resolved through
        # plans.get_owned_plan() -- a writable `plan` here would let a row name
        # somebody else's plan and skip that check entirely.
        read_only_fields = ("user", "plan")

    def to_representation(self, instance):
        # For GET requests, return nested objects
        representation = super().to_representation(instance)
        if instance.banner_uma:
            representation["banner_uma"] = BannerUmaSerializer(
                instance.banner_uma, context=self.context
            ).data
        if instance.banner_support:
            representation["banner_support"] = BannerSupportSerializer(
                instance.banner_support, context=self.context
            ).data
        if instance.banner_step_up:
            representation["banner_step_up"] = BannerStepUpSerializer(
                instance.banner_step_up, context=self.context
            ).data
        return representation

    TARGET_FIELDS = ("banner_uma", "banner_support", "banner_step_up")

    def validate(self, attrs):
        """Exactly one target, checked across all three FKs.

        Mirrors the model's exactly_one_banner_target check constraint. Both
        exist on purpose: the constraint is the guarantee, this is the readable
        400 the client gets instead of a 500 from the database.

        On a PARTIAL update the incoming body may name only some of the fields,
        so unmentioned ones fall back to what the instance already holds —
        otherwise PATCHing just `number_of_pulls` would read as "no target
        provided" and be rejected.
        """
        chosen = [
            field for field in self.TARGET_FIELDS
            if attrs.get(
                field,
                getattr(self.instance, field, None) if self.instance else None,
            )
        ]

        if not chosen:
            raise serializers.ValidationError(
                "One of banner_uma, banner_support or banner_step_up must be provided."
            )
        if len(chosen) > 1:
            raise serializers.ValidationError(
                f"Only one banner target may be set; got {', '.join(chosen)}."
            )

        return attrs
