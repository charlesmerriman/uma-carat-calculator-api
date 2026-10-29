from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework import serializers, status, permissions
from calculatorapi.models import ChangelogEntry, ChangelogChange


class ChangelogChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangelogChange
        fields = ("id", "category", "text", "order")


class ChangelogEntrySerializer(serializers.ModelSerializer):
    # Nested changes are read-only here; they're authored via the admin inline.
    # Ordering comes from ChangelogChange.Meta.ordering.
    changes = ChangelogChangeSerializer(many=True, read_only=True)

    class Meta:
        model = ChangelogEntry
        fields = ("id", "title", "version", "date", "changes")


class ChangelogEntryViewSet(ViewSet):
    """The public changelog, read-only by construction.

    Entries are authored in calculatorapi/data/changelog.yaml and synced on
    deploy, or typed in the admin. The API's own create/update/destroy went
    on 2026-10-15 together with the staff token login that was their only
    key: a bearer token could edit the changelog around the admin's second
    factor and its history. No write ACTIONS, not merely gated ones.
    """
    permission_classes = [permissions.AllowAny]

    def list(self, request):
        # The changes in ONE query, not one per entry: every /app load asks
        # for this, and with ~30 entries it was 9% of the server's time at the
        # 2026-10-29 peak. The prefetch keeps ChangelogChange's own ordering.
        entries = ChangelogEntry.objects.prefetch_related("changes").order_by("-date", "-id")
        serializer = ChangelogEntrySerializer(entries, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        try:
            entry = ChangelogEntry.objects.get(pk=pk)
        except ChangelogEntry.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = ChangelogEntrySerializer(entry)
        return Response(serializer.data)
