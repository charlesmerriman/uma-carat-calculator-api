"""
The gallery's moderation pages: the report queue, posts, comments and the
blocked-terms list. Imported by admin.py so it registers.

THE QUEUE IS GalleryReportAdmin. Open reports first, each row naming the item,
how many open reports it has and whether it is hidden. Three actions work on
the ITEMS behind the selected reports, not on the reports themselves, and
every one closes the item's open reports with the moderator's name:

    Keep          it is fine: show it again
    Remove        hide it for good (kept in the database, so reversible)
    Block author  Remove, and stop its author posting anywhere in the gallery

The same three are on the post and comment pages, for something spotted
without a report. They all go through gallery_moderation, which is also what
the API's auto-hide uses, so a decision made here and one made by reports
leave the same state behind.

Nothing here can be ADDED by hand: posts, comments, votes and reports come
from people, through the API.
"""

from django.contrib import admin, messages
from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from calculatorapi import gallery_moderation
from calculatorapi.models import BlockedTerm, GalleryComment, GalleryPost, GalleryReport, GalleryVote


def _apply(modeladmin, request, items, decision, done):
    """Run one moderation decision over distinct items and say how many."""
    seen = set()
    for item in items:
        key = (type(item), item.pk)
        if item is None or key in seen:
            continue
        seen.add(key)
        decision(item, request.user)
    modeladmin.message_user(request, f"{done}: {len(seen)}.", messages.SUCCESS)


class _ModerationActions:
    """Keep / Remove / Block author for a page whose rows ARE the items."""

    actions = ("keep_items", "remove_items", "block_authors")

    @admin.action(description="Keep: show again and close their reports")
    def keep_items(self, request, queryset):
        _apply(self, request, queryset, gallery_moderation.keep, "Kept")

    @admin.action(description="Remove: hide and close their reports")
    def remove_items(self, request, queryset):
        _apply(self, request, queryset, gallery_moderation.remove, "Removed")

    @admin.action(description="Block author: remove, and stop them posting")
    def block_authors(self, request, queryset):
        _apply(self, request, queryset, gallery_moderation.block_author, "Removed and blocked")


class GalleryVoteInline(TabularInline):
    """Who upvoted a post. Admin only, never on a public route: it is here to
    spot a vote brigade (one batch of new accounts voting together)."""

    model = GalleryVote
    fields = ("user", "created_at")
    readonly_fields = fields
    extra = 0
    can_delete = True
    verbose_name_plural = "Upvotes (admin only)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GalleryPost)
class GalleryPostAdmin(_ModerationActions, ModelAdmin):  # pylint: disable=too-many-ancestors
    list_display = (
        "public_id", "author_name", "event", "caption", "view_count", "copy_count",
        "is_hidden", "content_updated_at",
    )
    list_filter = ("is_hidden", "hidden_reason", "kind")
    list_select_related = ("author", "champions_meeting", "league_of_heroes")
    search_fields = ("public_id", "caption", "strategy", "author__display_name", "author__username")
    ordering = ("-content_updated_at",)
    readonly_fields = (
        "public_id", "author", "kind", "caption", "strategy", "published_at", "content_updated_at",
        "champions_meeting", "league_of_heroes", "team_snapshot", "preview_version",
        "source_team", "view_count", "copy_count", "is_hidden", "hidden_reason", "hidden_at",
    )
    fields = readonly_fields
    inlines = (GalleryVoteInline,)

    def has_add_permission(self, request):
        return False

    @admin.display(description="Author", ordering="author__display_name")
    def author_name(self, obj):
        return obj.author.display_name or obj.author.username


@admin.register(GalleryComment)
class GalleryCommentAdmin(_ModerationActions, ModelAdmin):  # pylint: disable=too-many-ancestors
    list_display = ("short_body", "author_name", "post", "is_hidden", "is_deleted", "created_at")
    list_filter = ("is_hidden", "hidden_reason", "is_deleted")
    list_select_related = ("author", "post__author")
    search_fields = ("body", "author__display_name", "author__username", "post__public_id")
    ordering = ("-created_at",)
    readonly_fields = (
        "post", "author", "parent", "body", "created_at", "edited_at", "is_hidden",
        "hidden_reason", "hidden_at", "is_deleted",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    @admin.display(description="Comment")
    def short_body(self, obj):
        return "[deleted]" if obj.is_deleted else obj.body[:80]

    @admin.display(description="Author")
    def author_name(self, obj):
        if obj.author is None:
            return "[deleted]"
        return obj.author.display_name or obj.author.username


@admin.register(GalleryReport)
class GalleryReportAdmin(ModelAdmin):
    """The moderation queue. See the module docstring."""

    list_display = (
        "created_at", "target_link", "reason", "note", "open_on_item", "item_hidden",
        "status",
    )
    list_filter = ("reason", "resolution")
    list_select_related = ("post", "comment__post", "reporter", "resolved_by")
    search_fields = ("note", "post__public_id", "comment__body")
    readonly_fields = (
        "reporter", "post", "comment", "reason", "note", "created_at", "resolved_at",
        "resolved_by", "resolved_by_discord", "resolution",
    )
    fields = readonly_fields
    actions = ("keep_items", "remove_items", "block_authors")

    # Open reports first (no resolved_at), then the most recently decided;
    # newest first within each.
    ordering = (F("resolved_at").desc(nulls_first=True), "-created_at")

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        # "Open reports on it", counted in the list query rather than once
        # per row. A report names a post or a comment, so one of the two
        # counts is always zero.
        return super().get_queryset(request).annotate(
            _open_on_post=Count(
                "post__reports", distinct=True,
                filter=Q(post__reports__resolved_at__isnull=True),
            ),
            _open_on_comment=Count(
                "comment__reports", distinct=True,
                filter=Q(comment__reports__resolved_at__isnull=True),
            ),
        )

    @staticmethod
    def _item(report):
        return report.post or report.comment

    @admin.display(description="Reported")
    def target_link(self, obj):
        item = self._item(obj)
        if obj.post_id:
            url = reverse("admin:calculatorapi_gallerypost_change", args=[item.pk])
            return format_html('<a href="{}">Post {}</a>', url, item.public_id)
        url = reverse("admin:calculatorapi_gallerycomment_change", args=[item.pk])
        text = "[deleted]" if item.is_deleted else item.body[:60]
        return format_html('<a href="{}">Comment: {}</a>', url, text)

    @admin.display(description="Open reports on it")
    def open_on_item(self, obj):
        return obj._open_on_post if obj.post_id else obj._open_on_comment  # pylint: disable=protected-access

    @admin.display(description="Hidden", boolean=True)
    def item_hidden(self, obj):
        return self._item(obj).is_hidden

    @admin.display(description="Status")
    def status(self, obj):
        if obj.resolved_at is None:
            return "Open"
        if obj.resolved_by_discord:
            who = f"{obj.resolved_by_discord} on Discord"
        else:
            who = obj.resolved_by.username if obj.resolved_by else "someone"
        return f"{obj.get_resolution_display()} by {who}"

    @admin.action(description="Keep the reported items: show again, close reports")
    def keep_items(self, request, queryset):
        _apply(self, request, (self._item(report) for report in queryset), gallery_moderation.keep, "Kept")

    @admin.action(description="Remove the reported items: hide, close reports")
    def remove_items(self, request, queryset):
        _apply(self, request, (self._item(report) for report in queryset), gallery_moderation.remove, "Removed")

    @admin.action(description="Block the authors: remove, and stop them posting")
    def block_authors(self, request, queryset):
        _apply(
            self, request, (self._item(report) for report in queryset),
            gallery_moderation.block_author, "Removed and blocked",
        )


@admin.register(BlockedTerm)
class BlockedTermAdmin(ModelAdmin):
    """Words and phrases refused in captions, comments and display names.
    "Whole word" is the safe default; "Anywhere" also catches the term inside
    longer words, so keep it for terms that never occur innocently."""

    list_display = ("term", "match", "created_at")
    list_filter = ("match",)
    search_fields = ("term",)
