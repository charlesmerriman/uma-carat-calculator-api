from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework import status
from calculatorapi.models import (
    BannerTimeline, BannerSupport, BannerUma, BannerStepUp,
)
from .uma import UmaSerializer
from .support_card import SupportCardSerializer
from .mixins import EffectiveDateMixin, EventTypeMixin
from ..banner_rate_ups import split_rate_ups


class BannerTimelineSerializer(EffectiveDateMixin, serializers.ModelSerializer):
    class Meta:
        model = BannerTimeline
        fields = (
            "id", "name", "banner_category",
            "start_date", "end_date", "is_predicted",
            "jp_start_date", "jp_end_date", "global_start_date", "global_end_date",
            "schedule_offset_days", "applied_offset_days",
            "image",
        )


def serialize_rate_ups(banner, context, with_recommendation=False):
    """(support_cards, other_rate_ups) for one support banner: the SSRs, and its
    SR and R rate-ups with their resolved `display` (calculatorapi/banner_rate_ups.py).

    BARE .all() ON PURPOSE -- do not "optimise" this to
    .select_related('support_card'). Calling select_related() on a related
    manager builds a NEW queryset, which bypasses the prefetch cache the view
    filled and re-queries once per banner (an N+1). The join lives in the
    Prefetch() in views/calculator.py instead; the two must stay paired.
    """
    ssrs, others = split_rate_ups(banner.supportsonsupportbanner_set.all())

    def card(link):
        # Context is forwarded so the nested card can resolve first_jp_date
        # from the request-wide map; a plain call doesn't inherit it the way a
        # declared nested field would.
        data = SupportCardSerializer(link.support_card, context=context).data
        if with_recommendation:
            data["recommendation"] = link.recommendation
        return data

    support_cards = [card(link) for link in ssrs]
    other_rate_ups = [{**card(link), "display": display} for link, display in others]
    return support_cards, other_rate_ups


def cached_rate_ups(banner, context, with_recommendation=False):
    """serialize_rate_ups, once per banner object, for the two fields that read it."""
    cached = getattr(banner, "_rate_up_split", None)
    if cached is None:
        cached = serialize_rate_ups(banner, context, with_recommendation)
        banner._rate_up_split = cached  # pylint: disable=protected-access
    return cached


class BannerSupportNestedSerializer(serializers.ModelSerializer):
    # The SSRs, drawn as tiles; the SR and R rate-ups beside them, each with
    # how the Timeline shows it (named in the row, a picture, or hidden).
    support_cards = serializers.SerializerMethodField()
    other_rate_ups = serializers.SerializerMethodField()

    class Meta:
        model = BannerSupport
        # is_recommended gives this panel its SSR treatment on the Timeline.
        fields = ("id", "name", "free_pulls", "is_recommended", "admin_comments",
                  "support_cards", "other_rate_ups")

    def get_support_cards(self, obj):
        return cached_rate_ups(obj, self.context, with_recommendation=True)[0]

    def get_other_rate_ups(self, obj):
        return cached_rate_ups(obj, self.context, with_recommendation=True)[1]


class BannerUmaNestedSerializer(serializers.ModelSerializer):
    umas = serializers.SerializerMethodField()

    class Meta:
        model = BannerUma
        # is_recommended gives this panel its SSR treatment on the Timeline.
        fields = ("id", "name", "free_pulls", "is_recommended", "admin_comments", "umas")

    def get_umas(self, obj):
        result = []
        # Bare .all() for the same reason as serialize_rate_ups above: the
        # select_related belongs in the view's Prefetch(), not here.
        for junction in obj.umasonumabanner_set.all():
            # See serialize_rate_ups above on why context is forwarded here.
            uma_data = UmaSerializer(junction.uma, context=self.context).data
            uma_data['recommendation'] = junction.recommendation
            result.append(uma_data)
        return result


class BannerStepUpSummarySerializer(serializers.ModelSerializer):
    """Just enough of a step-up to draw a chip on the Timeline.

    Deliberately not BannerStepUpSerializer: that one nests the whole
    banner_timeline, which is the very row this is attached to, so using it here
    would send each timeline back inside itself. The Timeline only needs to say
    "this campaign runs 2 star-3 and 3 SSR step-ups"; the full records are
    already sent under banner_step_up_data for the calculator.
    """

    class Meta:
        model = BannerStepUp
        fields = ("id", "name", "card_type", "banner_count")


class BannerTimelineForViewingSerializer(EventTypeMixin, EffectiveDateMixin,
                                         serializers.ModelSerializer):
    # Only this serializer is tagged, not BannerTimelineSerializer above — the
    # tag exists for the merged timeline array, which is fed exclusively by
    # /calculator-data's banner_timeline_data.
    event_type_value = "banner_timeline"

    banner_umas = BannerUmaNestedSerializer(source="uma_banners", many=True, read_only=True)
    banner_supports = BannerSupportNestedSerializer(source="support_banners", many=True, read_only=True)
    anniversary_event = serializers.SerializerMethodField()
    # Step-ups attach to the campaign PART they run in, so this lands on exactly
    # one timeline row per campaign with no extra filtering -- see the FK's
    # related_name on BannerStepUp.
    banner_step_ups = BannerStepUpSummarySerializer(
        source="step_up_banners", many=True, read_only=True
    )

    class Meta:
        model = BannerTimeline
        fields = (
            "id", "name", "event_type", "banner_category",
            "start_date", "end_date", "is_predicted",
            "jp_start_date", "jp_end_date", "global_start_date", "global_end_date",
            "schedule_offset_days", "applied_offset_days",
            "image", "banner_umas", "banner_supports", "anniversary_event",
            "banner_step_ups",
        )

    def get_anniversary_event(self, obj):
        """The campaign this banner is a part of, if any, plus which part it is.

        Deliberately a flat summary rather than the full AnniversaryEventSerializer:
        the timeline only needs enough to draw the attached strip, and the same
        campaign is already sent in full (with its products) under
        anniversary_event_data. Nesting it here would repeat the whole catalogue
        once per part.

        A banner in more than one campaign is not meaningful, so the first link
        wins rather than the field becoming a list for a case that never occurs.
        """
        link = next(iter(obj.anniversary_links.all()), None)
        if link is None:
            return None
        event = link.anniversary_event
        return {
            "id": event.id,
            "name": event.name,
            "event_type": event.event_type,
            "accent_label": event.accent_label,
            "image": event.image.url if event.image else None,
            "part_number": link.part_number,
        }


class BannerTimelineViewSet(ViewSet):
    def list(self, request):
        banner_timelines = BannerTimeline.objects.all().order_by("global_start_date")
        serializer = BannerTimelineSerializer(banner_timelines, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        try:
            banner_timeline = BannerTimeline.objects.get(pk=pk)
            serializer = BannerTimelineSerializer(banner_timeline)
            return Response(serializer.data)
        except BannerTimeline.DoesNotExist:
            return Response({"message": "Banner Timeline not found."}, status=status.HTTP_404_NOT_FOUND)
