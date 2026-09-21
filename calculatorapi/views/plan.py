from rest_framework import serializers

from calculatorapi.models import Plan


class PlanSerializer(serializers.ModelSerializer):
    """A plan as its owner sees it in the switcher: what it is called and
    whether it is the one open. Its banner rows travel separately, as
    `user_planned_banner_data`, so listing five plans never serializes five
    plans' worth of nested banners.

    The field list is the whitelist for BOTH directions. `user` is not on it:
    the owner is always the caller, set by the view, and a body can neither
    read nor name anybody else. `is_active` is read-only here because making a
    plan active also has to clear the previous one (plans.set_active_plan) --
    a plain field write would trip one_active_plan_per_user instead.

    `income_profile_id` tells the switcher which plans read their own stats
    (null: the account's). Read-only, and NOT a writable FK: a body must not be
    able to name a profile at all. Attaching is `separate_income` on the PATCH
    route, which creates the profile itself, and pointing a plan at an
    existing profile by id is a later feature with its own ownership check.
    """

    income_profile_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Plan
        fields = ("id", "name", "is_active", "income_profile_id", "updated_at")
        read_only_fields = ("id", "is_active", "updated_at")

    def validate_name(self, value):
        # CharField already trims the ends and rejects blank. This collapses
        # runs of spaces INSIDE the name too, so "My   plan" and "My plan" do
        # not sit in the switcher looking like two different plans.
        name = " ".join(value.split())
        if not name:
            raise serializers.ValidationError("A plan needs a name.")
        return name
