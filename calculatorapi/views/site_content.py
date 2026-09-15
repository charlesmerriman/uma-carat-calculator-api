"""
GET /site-content: the admin-editable pages and the FAQ, in one response.

Public and read-only. The frontend build fetches it once to bake the words
into the prerendered HTML, and a loaded page fetches it once after hydration
to pick up anything edited since the last build. Tens of kilobytes, a handful
of rows, so it is served straight from the database with no cache. Writes are
admin-only and happen through the Django admin, never here.
"""

from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi.models import FaqCategory, FaqItem, SitePage


class SitePageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SitePage
        fields = ("slug", "title", "meta_description", "body", "updated_at")


class FaqItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FaqItem
        fields = ("slug", "question", "answer", "show_on_homepage")


class FaqCategorySerializer(serializers.ModelSerializer):
    # Items arrive ordered by FaqItem.Meta.ordering through the prefetch below.
    items = FaqItemSerializer(many=True, read_only=True)

    class Meta:
        model = FaqCategory
        fields = ("slug", "title", "items")


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def site_content(request):
    pages = SitePage.objects.all()
    categories = FaqCategory.objects.prefetch_related("items")
    return Response({
        "pages": SitePageSerializer(pages, many=True).data,
        "faq": FaqCategorySerializer(categories, many=True).data,
    })
