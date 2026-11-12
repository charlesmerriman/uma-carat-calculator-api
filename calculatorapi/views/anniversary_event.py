from rest_framework import serializers

from calculatorapi.models import AnniversaryEvent, AnniversaryEventProduct

from .mixins import AnniversaryEventDateMixin

# A product's `opens_at`, formatted as every other date on the wire.
_DT = serializers.DateTimeField()


class AnniversaryEventProductSerializer(serializers.ModelSerializer):
    """One purchasable line on a campaign — a carat pack or a selector.

    Money and multipliers are emitted as JSON numbers rather than DRF's default
    Decimal-as-string, so the frontend can do arithmetic on them without parsing
    every value at the point of use. Precision is not a concern at two decimal
    places and these magnitudes.
    """

    usd_cost = serializers.DecimalField(
        max_digits=7, decimal_places=2, coerce_to_string=False
    )
    webstore_multiplier = serializers.DecimalField(
        max_digits=3, decimal_places=2, coerce_to_string=False
    )
    # The campaign-level cutoff already folded in, so the client never has to
    # reimplement the fallback. The raw override stays available as
    # `jp_cutoff_date_override` for the admin-facing case.
    jp_cutoff_date = serializers.DateField(
        source="effective_jp_cutoff_date", read_only=True
    )
    jp_cutoff_date_override = serializers.DateField(
        source="jp_cutoff_date", read_only=True
    )

    class Meta:
        model = AnniversaryEventProduct
        fields = (
            "id",
            "product_type",
            "name",
            "usd_cost",
            "paid_carat_amount",
            "webstore_multiplier",
            "max_quantity",
            "jp_cutoff_date",
            "jp_cutoff_date_override",
            "opens_with_part",
            "order",
        )


class AnniversaryEventSerializer(AnniversaryEventDateMixin, serializers.ModelSerializer):
    """A campaign, with its dates resolved from the banner parts it spans.

    A GameEventDateMixin subclass (not EffectiveDateMixin) because this model
    owns no global_start_date/global_end_date to fall back to — without a context
    map it fails safe to null dates. The map comes from
    predictions.build_anniversary_event_date_map.

    Three dates, not two: `start_date`/`end_date` are the campaign's whole
    window, while `main_start_date` is when the event it is named after actually
    begins. See AnniversaryEventDateMixin.
    """

    products = AnniversaryEventProductSerializer(many=True, read_only=True)
    banner_parts = serializers.SerializerMethodField()

    class Meta:
        model = AnniversaryEvent
        fields = (
            "id",
            "name",
            "event_type",
            "jp_cutoff_date",
            "image",
            "accent_label",
            "start_date",
            "main_start_date",
            "end_date",
            "is_predicted",
            "applied_offset_days",
            "products",
            "banner_parts",
        )

    def to_representation(self, instance):
        """Each product gains `opens_at`: the instant its paid carats credit.
        The start of the part it opens with (`opens_with_part`) when that part
        is linked and dated, else the campaign's `main_start_date`, so an
        editor's typo never drops a purchase from the projection."""
        data = super().to_representation(instance)
        part_starts = self._entry(instance).get("part_starts", {})
        for product, row in zip(instance.products.all(), data["products"]):
            start = part_starts.get(product.opens_with_part)
            row["opens_at"] = _DT.to_representation(start) if start is not None else data["main_start_date"]
        return data

    def get_banner_parts(self, obj):
        """The banner timelines this campaign spans, as ids + part numbers.

        Ids only, not nested banners: the frontend already holds every
        BannerTimeline in banner_timeline_data and can join on id, so nesting
        them here would duplicate a large payload for no gain.
        """
        return [
            {
                "banner_timeline": link.banner_timeline_id,
                "part_number": link.part_number,
            }
            for link in obj.banner_links.all()
        ]
