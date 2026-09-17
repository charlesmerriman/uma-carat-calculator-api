from rest_framework import permissions, serializers, viewsets
from calculatorapi.models import Uma
from .mixins import FirstJpDateMixin


class UmaSerializer(FirstJpDateMixin, serializers.ModelSerializer):
    context_key = "uma_first_jp_dates"
    # Not a column any more: Uma.is_three_star is a property over `rarity`
    # (unknown rarity counts as ★3). Declared because ModelSerializer only
    # maps model FIELDS by itself; the wire name and values are unchanged.
    is_three_star = serializers.BooleanField(read_only=True)

    class Meta:
        model = Uma
        fields = (
            "id",
            "name",
            "image",
            "admin_comments",
            # Public and rendered: the Timeline tile's hover overlay.
            "purpose",
            "first_jp_date",
            # The intrinsic selector gates. Sent on every uma because the
            # client filters both pickers and the projection's selector
            # funding by them -- see frontend/src/utils/selectorTickets.ts.
            "is_time_limited",
            "is_three_star",
        )


class UmaOptionSerializer(serializers.ModelSerializer):
    """One row of GET /umas: what a picker needs to draw a tile, and nothing else.

    Not UmaSerializer above. That one carries the selector gates, the Timeline
    overlay text and `admin_comments` (an editors' scratch field that
    /calculator-data has always exposed); a route that exists so a person can
    pick a picture has no use for any of it, and a new public route is the
    right moment to stop the comments leaking further.
    """

    # Still called `image` on the wire, but it is the uma's portrait: the
    # borderless art when the row has it, the bordered art when it does not
    # (Uma.portrait). A picker tile is a circle, and the border does not
    # survive the crop.
    image = serializers.ImageField(source="portrait", read_only=True)

    class Meta:
        model = Uma
        fields = ("id", "name", "image")


class UmaViewSet(viewsets.ReadOnlyModelViewSet):  # pylint: disable=too-many-ancestors
    """GET /umas — the uma catalogue as picker options. Public.

    Exists for the oshi picker on /account. That page sits outside the
    calculator provider, so the catalogue in /calculator-data is not in the
    client there, and fetching the largest payload the API serves to fill a
    picker would be the wrong trade — this is three fields a row.

    Only umas WITH a picture, because a pick has to render, and by name
    because that is how a person scans a few hundred tiles. Not cached: it is
    one indexed query, and the public payload cache's one-process caveat
    (public_payload_cache.py) is not worth inheriting for it.
    """

    permission_classes = [permissions.AllowAny]
    serializer_class = UmaOptionSerializer
    queryset = (
        Uma.objects.exclude(image="").exclude(image__isnull=True).order_by("name", "id")
    )

    def get_serializer_context(self):
        # No "request" in the context, matching every serializer behind
        # /calculator-data: with it, DRF's ImageField wraps the storage URL in
        # request.build_absolute_uri(), which behind the prod reverse proxy
        # names the wrong (internal, http) host. The storage already knows
        # its own public URL. See the note in views/calculator.py.
        context = super().get_serializer_context()
        context.pop("request", None)
        return context
