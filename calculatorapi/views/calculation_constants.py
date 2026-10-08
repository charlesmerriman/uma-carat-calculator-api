from rest_framework import serializers

from calculatorapi.models import CalculationConstants


class CalculationConstantsSerializer(serializers.ModelSerializer):
    """
    The calculation constants, served to the frontend on every
    `/calculator-data` request.

    The decimal fields are emitted as NUMBERS rather than DRF's default decimal
    strings. The client feeds them straight into arithmetic, and `"0.664" * 2`
    is a silent NaN in JavaScript rather than an error — so the coercion belongs
    here, once, not at every call site.

    Every DecimalField on the model needs a line below; there is a test that
    fails if one is added without one, because the omission is otherwise
    invisible until a NaN turns up somewhere far away.

    `id` is excluded: there is only ever one row, and exposing its pk invites a
    client to think otherwise.
    """

    throughout_decay_k = serializers.FloatField()
    throughout_decay_linear_slope = serializers.FloatField()
    prediction_factor = serializers.FloatField()
    step_up_target_rate = serializers.FloatField()
    rate_up_rate_3 = serializers.FloatField()
    rate_up_pool_3 = serializers.FloatField()
    rate_up_rate_2 = serializers.FloatField()
    rate_up_pool_2 = serializers.FloatField()
    rate_up_rate_1 = serializers.FloatField()
    rate_up_pool_1 = serializers.FloatField()

    class Meta:
        model = CalculationConstants
        exclude = ("id",)
