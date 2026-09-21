"""IncomeProfile's serializer, and the one place that picks a stats serializer
for whatever plans.stats_target() returned."""

from calculatorapi.models import IncomeProfile
from calculatorapi.views.user import GameStatsSerializer, UserStatsSerializer


class IncomeProfileSerializer(GameStatsSerializer):
    """A profile's stats. Only the stats: `user`, ids and timestamps are not
    the client's business, and the shape must match UserStatsSerializer's
    exactly so a plan switch can hand the client either without it knowing."""

    class Meta(GameStatsSerializer.Meta):
        model = IncomeProfile


def stats_serializer(target, **kwargs):
    """The right stats serializer for `target`, which is whatever
    plans.stats_target(plan) returned: a CustomUser or an IncomeProfile.
    `kwargs` pass straight through (`data=`, `partial=`) so this works for
    reading and for saving alike. Callers never branch on the type themselves;
    this is the single isinstance."""
    cls = (IncomeProfileSerializer if isinstance(target, IncomeProfile)
           else UserStatsSerializer)
    return cls(target, **kwargs)
