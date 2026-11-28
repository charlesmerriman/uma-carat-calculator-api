"""
The admin's manual control for the daily masterdata check: which game data
version is live. Imported by admin.py so it registers.

Rows come only from the check (POST /game-data/versions), so there is no Add
button and every column is read-only. A person changes a status through the
two actions, which go through the model's own mark_live() / mark_ignored() so
"one live version per region" is kept in one place.
"""

from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from calculatorapi.models import GameDataVersion


def _who(request):
    return f"{request.user.get_username()} (admin)"


@admin.register(GameDataVersion)
class GameDataVersionAdmin(ModelAdmin):
    list_display = ("version", "region", "status", "master_size", "short_hash",
                    "first_seen_at", "revised_at", "checked_at")
    list_filter = ("region", "status")
    actions = ("mark_live", "mark_ignored")
    readonly_fields = ("region", "version", "status", "master_size", "master_hash",
                       "first_seen_at", "checked_at", "revised_at",
                       "status_changed_at", "status_changed_by")
    fieldsets = (
        (None, {
            "fields": ("region", "version", "status"),
            "description": (
                "Mark a version live once the game is running it. The daily check "
                "makes a snapshot PR only from the live version, so a staged one "
                "never ships early. Select a row in the list and use the actions."
            ),
        }),
        ("On the CDN", {"fields": ("master_size", "master_hash", "first_seen_at",
                                   "checked_at", "revised_at")}),
        ("Last change", {"fields": ("status_changed_at", "status_changed_by")}),
    )

    @admin.display(description="Hash")
    def short_hash(self, obj):
        return obj.master_hash[:12]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Mark live (the game runs this version)",
                  permissions=["change"])
    def mark_live(self, request, queryset):
        rows = list(queryset[:2])
        if len(rows) != 1:
            self.message_user(request, "Select exactly one version to mark live.", messages.ERROR)
            return
        released, held = rows[0].mark_live(by=_who(request))
        skills = f" {released} skill(s) now on Global." if released else ""
        skills += f" {held} skill(s) from newer versions held back." if held else ""
        self.message_user(request, f"{rows[0]} is now the live version.{skills}", messages.SUCCESS)

    @admin.action(description="Ignore", permissions=["change"])
    def mark_ignored(self, request, queryset):
        for row in queryset:
            row.mark_ignored(by=_who(request))
        self.message_user(request, f"Ignored {queryset.count()} version(s).", messages.SUCCESS)
