"""
Admin configuration, organized for a non-technical content editor.

Layout of this file:
  1. Site branding
  2. Shared helpers (image thumbnails)
     Image *upload* fields additionally get a "Choose from library" button,
     which lets an editor reuse a file already in the media bucket instead of
     re-uploading it. That machinery lives in admin_image_picker.py; admins
     opt in by mixing in SpacesImagePickerMixin (no other configuration).
  3. Inlines (children edited on their parent's page)
  4. Game content admins (what the "Content editors" group manages)
  5. Rank / income tables
  6. User data admins (owner-only; hidden from content editors by permissions)
  7. Calculation constants (the projection's tunable numbers, one singleton row)
  8. Patreon supporters (the public thank-you list, plus its CSV importer)
  9. Site content (the About page, the carat income guide and the FAQ, plus
     the "Rebuild website" button that re-bakes them into the public site)
 10. Security (django-axes's sign-in log and lockout counters, read-only;
     the two-factor devices themselves are inlines on the user page, and
     the login flow is calculatorapi/admin_site.py)

The join models (UmasOnUmaBanner, SupportsOnSupportBanner, UmaSkill,
SupportCardSkill, DailyLegendRaceUma) and every PvP guide child are
deliberately NOT registered top-level — they are edited through inlines on
their parent pages. The "Content editors" group (see
management/commands/create_content_editor_group.py) still needs permissions on
them for the inlines to save.

The PvP guide admin (one page per guide, one tab per running style, the decks
with their cards nested) lives in admin_pvp.py, imported below so it registers
with the site. The two event admins here only link to it (pvp_guide_link).
"""

# unfold's ModelAdmin has a deeper class hierarchy than pylint's default
# max-parents of 7 — every admin in this file trips it, so disable once here.
# pylint: disable=too-many-ancestors

# One admin per file would scatter nine cohesive sections across nine modules
# for no reader's benefit — the section index in the docstring above is how you
# navigate this, and it works. That said, this file passed 1000 lines with the
# Patreon section, so splitting the *content* admins (section 4) out into
# admin_content.py is worth doing as its own change.
# pylint: disable=too-many-lines

from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Count, F, Min, Q
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
# django-unfold themes the admin, but only for admins that inherit its base
# classes — a plain admin.ModelAdmin would render unstyled under unfold's
# templates. Hence every ModelAdmin/TabularInline below extends unfold's.
from axes.models import AccessAttempt, AccessLog
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.overrides import FORMFIELD_OVERRIDES_INLINE
from unfold.widgets import UnfoldAdminExpandableTextareaWidget

from .admin_image_picker import SpacesImagePickerMixin
# Importing admin_pvp registers the PvP guide admin; the mixin and fieldset
# put the "PvP guide" link on the two event pages.
from .admin_pvp import PVP_GUIDE_FIELDSET, PicturePickersMixin, PvpGuideLinkMixin
# Importing admin_gallery registers the gallery's moderation pages.
from . import admin_gallery  # noqa: F401  pylint: disable=unused-import
from . import digitalocean_api, patreon_api, skill_text
from .admin_patreon_import import (
    PatreonCsvImportForm,
    PatreonSyncForm,
    apply_patreon_import,
    parse_patreon_csv,
)
from .predictions import GAME_EVENT_END_DATE_BUFFER
from .models import (
    CustomUser, Uma, Skill, SkillAlias, SkillEvolutionCondition, UmaSkill, SupportCardSkill,
    SupportCard, UserPlannedBanner,
    Plan, IncomeProfile,
    TeamTrialsRank, ClubRank, ChampionsMeetingRank, LeagueOfHeroesRank,
    ChampionsMeetingRankReward, LeagueOfHeroesRankReward,
    BannerTimeline, BannerUma, BannerSupport, BannerStepUp,
    ChampionsMeeting,
    SupportsOnSupportBanner, UmasOnUmaBanner,
    GameEvent, LeagueOfHeroes, Scenario,
    DailyLegendRaceRelease, DailyLegendRaceUma,
    ChangelogEntry, ChangelogChange,
    SocialAccount,
    AnniversaryEvent, AnniversaryEventBanner, AnniversaryEventProduct,
    UserPlannedPurchase,
    UserStepUpSelection,
    CalculationConstants,
    PatreonTier, PatreonSupporter, PatreonCredentials,
    UserOshi,
    SitePage, FaqCategory, FaqItem,
)

# ── 1. Site branding ─────────────────────────────────────────────────────────

admin.site.site_header = "Uma Calculator Admin"
admin.site.site_title = "Uma Calculator Admin"
admin.site.index_title = "Content management"

# Custom index adds a "Reports" box linking to the analytics dashboard
# (templates/admin/custom_index.html extends the default index).
admin.site.index_template = "admin/custom_index.html"

# django-otp registers plain ModelAdmins for its device models when its plugins
# load (they sit before this app in INSTALLED_APPS). Plain admins render
# unstyled under unfold, and a device only means anything beside its owner, so
# they come off the site and the inlines on CustomUserAdmin (section 6) take
# over. The guard keeps this safe if a plugin ever stops registering them.
for _device_model in (TOTPDevice, StaticDevice):
    if admin.site.is_registered(_device_model):
        admin.site.unregister(_device_model)


# ── 2. Shared helpers ────────────────────────────────────────────────────────

class ImagePreviewMixin:  # pylint: disable=too-few-public-methods
    """Adds a small image thumbnail for models with an `image` field."""

    @admin.display(description="Preview")
    def image_preview(self, obj):
        # Guard: .url raises if no file is attached.
        if obj.image:
            return format_html(
                '<img src="{}" style="height: 48px; border-radius: 4px;" />',
                obj.image.url,
            )
        return "—"


# ── 3. Inlines ───────────────────────────────────────────────────────────────

class BannerUmaInline(TabularInline):
    """Uma banners listed on their timeline; click through to edit the umas."""
    model = BannerUma
    # is_recommended is here as well as on the banner's own page so an editor
    # can tick it from the timeline they are already editing.
    fields = ("name", "free_pulls", "is_recommended")
    show_change_link = True  # renders a "Change" link to the banner's own page
    extra = 0


class BannerSupportInline(TabularInline):
    """Support card banners listed on their timeline."""
    model = BannerSupport
    fields = ("name", "free_pulls", "is_recommended")
    show_change_link = True
    extra = 0


# The recommendation box on a featured card starts at Django's default ten rows,
# which made every card on a banner page a tall block an editor had to scroll
# past. Unfold's expandable textarea starts at two rows and grows with what is
# typed (CSS field-sizing), so short notes stay short and long ones still fit.
# Spread over unfold's inline defaults: setting formfield_overrides replaces
# them outright, which would unstyle every other field on these inlines.
COMPACT_TEXT_OVERRIDES = {
    **FORMFIELD_OVERRIDES_INLINE,
    models.TextField: {"widget": UnfoldAdminExpandableTextareaWidget},
}


class UmaOnBannerInline(PicturePickersMixin, TabularInline):
    """The umas featured on an uma banner, edited on the banner page."""
    model = UmasOnUmaBanner
    autocomplete_fields = ("uma",)  # searchable picker instead of a 200-item dropdown
    formfield_overrides = COMPACT_TEXT_OVERRIDES
    extra = 1


class SupportOnBannerInline(PicturePickersMixin, TabularInline):
    """The support cards featured on a support banner."""
    model = SupportsOnSupportBanner
    autocomplete_fields = ("support_card",)
    formfield_overrides = COMPACT_TEXT_OVERRIDES
    extra = 1


class UmaSkillInline(PicturePickersMixin, TabularInline):
    """The skills an outfit carries, on the uma's page. Filled by the import."""
    model = UmaSkill
    autocomplete_fields = ("skill",)
    fields = ("skill", "source", "level", "notes")
    extra = 0


class SupportCardSkillInline(PicturePickersMixin, TabularInline):
    """The skills a support card gives, by hint or event. Filled by the import."""
    model = SupportCardSkill
    autocomplete_fields = ("skill",)
    fields = ("skill", "source", "notes")
    extra = 0


class SkillAliasInline(TabularInline):
    """Older names of a skill, so a guide that wrote one still links. Added by
    the import whenever it replaces a name; an editor may add one too."""
    model = SkillAlias
    fields = ("name",)
    extra = 0


class SkillEvolutionConditionInline(TabularInline):
    """What unlocks an evolved skill. Game data, replaced by every import, so
    read-only here."""
    model = SkillEvolutionCondition
    fields = ("game_id", "text", "translated_text", "jp_text", "num", "sub_num",
              "timing_type", "count_type")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ChangelogChangeInline(TabularInline):
    """The individual change lines of a changelog entry, edited on its page."""
    model = ChangelogChange
    fields = ("order", "category", "text")
    extra = 1


class AnniversaryEventBannerInline(PicturePickersMixin, TabularInline):
    """The banner "Parts" a campaign spans, edited on the campaign's page.

    Editing the link here rather than on BannerTimeline keeps that shared model
    free of campaign fields, and puts every part of a campaign on one screen —
    which is also where its date range comes from.
    """
    model = AnniversaryEventBanner
    fields = ("part_number", "banner_timeline")
    autocomplete_fields = ("banner_timeline",)
    ordering = ("part_number",)
    extra = 1


class DailyLegendRaceUmaInline(PicturePickersMixin, TabularInline):
    """The umas in one daily legend race batch.

    No order column: the site sorts a batch by star count, then by name. An
    uma already in another batch is refused with a form error, from the
    database's one-release-per-uma constraint.
    """
    model = DailyLegendRaceUma
    fields = ("uma",)
    autocomplete_fields = ("uma",)
    extra = 1


class AnniversaryEventProductInline(TabularInline):
    """The carat packs and selectors a campaign sells."""
    model = AnniversaryEventProduct
    fields = (
        "order", "product_type", "name", "usd_cost", "paid_carat_amount",
        "webstore_multiplier", "max_quantity", "jp_cutoff_date",
    )
    ordering = ("order",)
    extra = 1


# ── 4. Game content ──────────────────────────────────────────────────────────

class GlobalDatesFilter(admin.SimpleListFilter):
    """
    Sidebar filter on the timeline list: has the global run been confirmed?
    Mirrors the app's own logic — a timeline without a global_start_date is
    served to users with dates *predicted* from the JP schedule.
    """
    title = "global dates"
    parameter_name = "global_dates"

    def lookups(self, request, model_admin):
        return (
            ("confirmed", "Confirmed"),
            ("predicted", "Predicted (awaiting confirmation)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "confirmed":
            return queryset.filter(global_start_date__isnull=False)
        if self.value() == "predicted":
            return queryset.filter(global_start_date__isnull=True)
        return queryset


class GlobalDatesStatusMixin:  # pylint: disable=too-few-public-methods
    """
    Adds a Confirmed/Predicted status badge column, shared by every JP-first
    content admin (banners, Champions Meetings, League of Heroes). A row with a
    global_start_date is confirmed; without one the app predicts its dates from
    the JP schedule.
    """

    @admin.display(description="Status", ordering="global_start_date")
    def global_dates_status(self, obj):
        # Inline styles (not admin CSS classes) so the badge renders the same
        # under any admin theme.
        if obj.global_start_date:
            color, background, label = "#166534", "#dcfce7", "Confirmed"
        else:
            color, background, label = "#92400e", "#fef3c7", "Predicted"
        return format_html(
            '<span style="color: {}; background: {}; padding: 2px 8px; '
            'border-radius: 9999px; font-weight: 600;">{}</span>',
            color, background, label,
        )


class ScheduleOffsetFilter(admin.SimpleListFilter):
    """
    Sidebar filter: which rows are currently carrying a schedule offset? An
    offset shifts every later date across all three content types, so "what is
    moving the calendar right now" is a question editors need a fast answer to.
    """
    title = "schedule offset"
    parameter_name = "schedule_offset"

    def lookups(self, request, model_admin):
        return (
            ("set", "Has an offset"),
            ("none", "No offset"),
        )

    def queryset(self, request, queryset):
        if self.value() == "set":
            return queryset.exclude(schedule_offset_days=0)
        if self.value() == "none":
            return queryset.filter(schedule_offset_days=0)
        return queryset


class ScheduleOffsetMixin:  # pylint: disable=too-few-public-methods
    """
    Adds an Offset column, shared by every JP-first content admin. Offsets only
    apply to rows whose global dates are still predicted, so a value sitting on
    a confirmed row is flagged as inactive rather than silently looking live.
    """

    @admin.display(description="Offset", ordering="schedule_offset_days")
    def schedule_offset_display(self, obj):
        days = obj.schedule_offset_days
        if not days:
            return "—"
        label = f"{days:+d}d"
        if obj.global_start_date:
            # Confirmed rows ignore the offset entirely (predictions.py) — the
            # confirmed date already reflects reality.
            return format_html('<span style="opacity: 0.5;">{} (inactive)</span>', label)
        return format_html(
            '<span style="color: #1e3a8a; background: #dbeafe; padding: 2px 8px; '
            'border-radius: 9999px; font-weight: 600;">{}</span>',
            label,
        )


# Shared so the three content admins can't drift apart on the wording of a
# field whose blast radius is the entire calendar.
SCHEDULE_OFFSET_FIELDSET = (
    "Schedule offset (advanced)", {
        "fields": ("schedule_offset_days",),
        "description": (
            "<strong>This is not a local edit.</strong> It pushes this row "
            "<em>and every later banner, Champions Meeting and League of Heroes "
            "event</em> forward by this many days, and stacks with any other "
            "offset. Use it only when global has actually delayed its schedule, "
            "so that every predicted date after the delay stays right. "
            "It has no effect once this row's global dates are confirmed."
        ),
    },
)

@admin.register(BannerTimeline)
class BannerTimelineAdmin(GlobalDatesStatusMixin, ScheduleOffsetMixin, ImagePreviewMixin,
                          SpacesImagePickerMixin, ModelAdmin):
    list_display = ("name", "banner_category", "jp_start_date", "global_start_date",
                    "global_end_date", "global_dates_status", "schedule_offset_display")
    list_filter = ("banner_category", GlobalDatesFilter, ScheduleOffsetFilter)
    date_hierarchy = "global_start_date"
    ordering = ("-global_start_date",)
    search_fields = ("name",)  # also powers the autocomplete on banner admins
    readonly_fields = ("image_preview",)
    inlines = (BannerUmaInline, BannerSupportInline)
    # Editors always fill the JP dates; global dates only once the banner is
    # confirmed (they're left blank until then, and the app predicts them).
    fieldsets = (
        (None, {"fields": ("name", "banner_category", "image", "image_preview")}),
        ("JP server dates (always known)", {"fields": ("jp_start_date", "jp_end_date")}),
        ("Global server dates (fill when confirmed)", {"fields": ("global_start_date", "global_end_date")}),
        SCHEDULE_OFFSET_FIELDSET,
    )


class PlannedByColumnMixin:  # pylint: disable=too-few-public-methods
    """
    Adds a sortable "Planned by" column: how many users have this banner in
    their pull plan. Counting happens in SQL (one annotation, no N+1) and the
    `ordering=` mapping is what makes the column header sortable.
    """

    def get_queryset(self, request):
        # Reverse FK from UserPlannedBanner (no related_name → default name).
        # DISTINCT USERS, not rows: an account can hold several plans, and the
        # same banner in three of them is still one person. The column says
        # "users", so that is what it counts.
        #
        # ACTIVE PLANS ONLY, the same rule as the analytics dashboard's
        # "Planners" column (analytics._banner_popularity), so the two figures
        # agree for the same banner. The spare plans are what-ifs: a banner
        # someone keeps only in "What if I skip X" is not one they plan to
        # pull on. The isnull half is TRANSITIONAL (multi-plan, release 1 of
        # 2): a row the old code wrote during the deploy window has no plan
        # yet and is still a real row. Goes with release 2.
        active = (
            Q(userplannedbanner__plan__is_active=True)
            | Q(userplannedbanner__plan__isnull=True)
        )
        return super().get_queryset(request).annotate(
            planned_count=Count(
                "userplannedbanner__user", distinct=True, filter=active))

    @admin.display(description="Planned by", ordering="planned_count")
    def planned_by(self, obj):
        return f"{obj.planned_count} user{'' if obj.planned_count == 1 else 's'}"


@admin.register(BannerUma)
class BannerUmaAdmin(PlannedByColumnMixin, ModelAdmin):
    list_display = ("name", "banner_timeline", "free_pulls", "is_recommended", "planned_by")
    list_filter = ("is_recommended",)
    # Tickable straight from the list. Deliberately NOT a "Mark as recommended"
    # bulk action: those are written with queryset.update(), which fires no
    # post_save, so public_payload_cache would keep serving the old flag until
    # its TTL ran out. list_editable saves each changed row individually.
    list_editable = ("is_recommended",)
    list_select_related = ("banner_timeline",)
    ordering = ("-banner_timeline__global_start_date",)
    search_fields = ("name",)
    autocomplete_fields = ("banner_timeline",)
    inlines = (UmaOnBannerInline,)


@admin.register(BannerSupport)
class BannerSupportAdmin(PlannedByColumnMixin, ModelAdmin):
    list_display = ("name", "banner_timeline", "free_pulls", "is_recommended", "planned_by")
    list_filter = ("is_recommended",)
    # See BannerUmaAdmin on why this is list_editable rather than a bulk action.
    list_editable = ("is_recommended",)
    list_select_related = ("banner_timeline",)
    ordering = ("-banner_timeline__global_start_date",)
    search_fields = ("name",)
    autocomplete_fields = ("banner_timeline",)
    inlines = (SupportOnBannerInline,)


@admin.register(BannerStepUp)
class BannerStepUpAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    """A Select Step-Up banner, sold during a campaign for paid carats only.

    Unlike its uma/support peers there is no card inline: the player picks their
    own 10 cards from the back catalogue, bounded by the campaign's JP cutoff,
    so there is nothing to list here.
    """

    list_display = ("name", "anniversary_event", "card_type", "banner_count",
                    "max_steps", "banner_timeline")
    list_filter = ("card_type", "anniversary_event")
    list_select_related = ("anniversary_event", "banner_timeline")
    search_fields = ("name",)
    ordering = ("anniversary_event", "order")
    autocomplete_fields = ("banner_timeline",)
    readonly_fields = ("image_preview",)
    fieldsets = (
        (None, {"fields": ("name", "anniversary_event", "banner_timeline", "order")}),
        ("Ladder", {
            "description": (
                "Card type picks which pool this draws from and how its odds are "
                "labelled. Banner count is how many of this type the campaign "
                "sells — the ceiling on steps is five times that. The step COSTS "
                "are shared across every step-up and live in Calculation "
                "constants, not here."
            ),
            "fields": ("card_type", "banner_count"),
        }),
        ("Artwork", {"fields": ("image", "image_preview")}),
        ("Notes", {"fields": ("admin_comments",)}),
    )

    @admin.display(description="Max steps")
    def max_steps(self, obj):
        return obj.max_steps

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Offer only the selected campaign's own parts for banner_timeline.

        The model's clean() is the real guard; this keeps an editor from having
        to hunt through ~200 timelines to find one of the three that are valid.
        Falls back to the full list while adding, when there is no campaign to
        filter by yet.
        """
        if db_field.name == "banner_timeline":
            step_up_id = request.resolver_match.kwargs.get("object_id")
            if step_up_id:
                step_up = BannerStepUp.objects.filter(pk=step_up_id).first()
                if step_up:
                    kwargs["queryset"] = BannerTimeline.objects.filter(
                        anniversary_links__anniversary_event=step_up.anniversary_event_id
                    ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# Shared by the two card admins so the wording that separates a PUBLIC note
# from the editors' own can't drift between them.
PURPOSE_FIELDSET = (
    "Shown to players", {
        "fields": ("purpose",),
        "description": (
            "<strong>Public.</strong> One short line on what this card is for — "
            "\"Great pace parent\", \"Great for front runners\". Players see it when "
            "they hover over or tap the card's picture on the Timeline; leave it "
            "blank and nothing shows. 100 characters at most, so it fits on the "
            "smallest card. Notes meant for other editors go in Admin comments."
        ),
    },
)

# "Empty" lists every card still missing a purpose, so an editor can work
# through them.
PURPOSE_FILTER = ("purpose", admin.EmptyFieldListFilter)


@admin.register(Uma)
class UmaAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    inlines = (UmaSkillInline,)
    list_display = ("image_preview", "name", "game_id", "is_time_limited", "rarity")
    list_display_links = ("name",)
    list_filter = ("is_time_limited", "rarity", PURPOSE_FILTER)
    ordering = ("name",)
    # required: autocomplete source for banner inlines. `=game_id` is an exact
    # match, so typing an id finds one row rather than every name containing it.
    search_fields = ("name", "=game_id")
    readonly_fields = ("image_preview", "borderless_preview", "is_on_global")
    fieldsets = (
        (None, {"fields": (
            "name", "game_id", "image", "image_preview",
            "image_borderless", "borderless_preview", "admin_comments",
        )}),
        PURPOSE_FIELDSET,
        ("Selector availability", {
            "fields": ("is_time_limited", "rarity"),
            "description": (
                "A time-limited uma, or one that is ★1 or ★2, is hidden from "
                "selector and step-up pickers, and a selector ticket cannot pay "
                "for a banner it is featured on. Both are independent of the "
                "campaign cutoff dates. Rarity comes from the game-data import "
                "for umas on global, which overwrites an edit here; set it by "
                "hand only for an uma that is not on global yet. Blank counts "
                "as ★3."
            ),
        }),
        ("Game data", {
            "classes": ("collapse",),
            "description": (
                "Filled by the game-data import and overwritten by the next one, "
                "so edits here do not stick. Fix the source instead."
            ),
            "fields": (
                "is_on_global", "title", "running_style",
                ("apt_turf", "apt_dirt"),
                ("apt_short", "apt_mile", "apt_medium", "apt_long"),
                ("apt_front", "apt_pace", "apt_late", "apt_end"),
                ("base_speed", "base_stamina", "base_power", "base_guts", "base_wit"),
                ("growth_speed", "growth_stamina", "growth_power", "growth_guts", "growth_wit"),
            ),
        }),
    )


    @admin.display(description="Borderless preview")
    def borderless_preview(self, obj):
        if obj.image_borderless:
            return format_html(
                '<img src="{}" style="height: 48px; border-radius: 4px;" />',
                obj.image_borderless.url,
            )
        return "—"


def _phrases_html(phrases, joiner):
    """Readable phrases, with a raw one (the game's own code) in a code box."""
    return format_html_join(
        joiner, "{}",
        ((format_html("<code>{}</code>", item["text"]) if item["raw"] else item["text"],)
         for item in phrases),
    )


def _condition_html(condition):
    """skill_text's {"all", "any"} as one line: all of these, and one of those."""
    parts = [_phrases_html(condition["all"], ", ")] if condition["all"] else []
    if condition["any"]:
        parts.append(format_html("and one of: {}", format_html_join(
            " <b>or</b> ", "({})", ((_phrases_html(alt, ", "),) for alt in condition["any"]),
        )))
    return format_html_join(", ", "{}", ((part,) for part in parts))


class OnGlobalFilter(admin.SimpleListFilter):
    """Skills by whether a player may be shown them. The rule is SkillQuerySet's;
    the `availability` filter beside it says where the game has each one."""
    title = "shown to players"
    parameter_name = "on_global"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "Not yet"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.on_global()
        if self.value() == "no":
            return queryset.not_on_global()
        return queryset


@admin.register(Skill)
class SkillAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    """
    Skills are imported, so this page is mostly for looking things up and for
    the two editor-owned columns (image, admin comments). "Versions" answers
    "is this the gold version of something" from group_id without a second table.
    """
    inlines = (SkillAliasInline, SkillEvolutionConditionInline)
    list_display = (
        "image_preview", "label", "game_id", "rarity", "tier", "cost", "versions",
        "availability", "on_global",
    )
    list_display_links = ("label",)
    list_filter = ("availability", "rarity", "tier", OnGlobalFilter)
    ordering = ("name",)
    # Every name a skill has had, so an editor can find it by any of them.
    search_fields = (
        "name", "translated_name", "jp_name", "aliases__name", "=game_id", "=group_id",
    )
    readonly_fields = (
        "image_preview", "versions", "on_global", "availability",
        "translated_name", "translated_description", "jp_name", "jp_description",
        "effects", "evolution_scenario", "readable", "sources",
    )
    autocomplete_fields = ("evolves_from",)
    list_select_related = ("evolves_from",)
    fieldsets = (
        (None, {"fields": ("name", "game_id", "image", "image_preview", "admin_comments")}),
        ("Text", {
            "description": (
                "Global's English comes first. Where the game has this skill is "
                "\"availability\", set by the import: a skill only announced for "
                "global (no icon id yet) may be used by guides already, and its "
                "rarity and tier below are guesses you may correct until it "
                "releases. A JP-only skill stays here in the admin and is never "
                "shown to players."
            ),
            "fields": ("description", "description_detailed", "availability", "on_global"),
        }),
        ("In plain English", {
            "description": (
                "Built from the game data below, the way the site will show it. A grey "
                "code box is a part nobody has confirmed the meaning of yet."
            ),
            "fields": ("readable", "sources"),
        }),
        ("Translation and Japanese", {
            "classes": ("collapse",),
            "description": (
                "UmaTL's English and the JP client's text, from the import. A player "
                "sees the translation only while global has no text."
            ),
            "fields": ("translated_name", "translated_description", "jp_name", "jp_description"),
        }),
        ("Versions", {
            "description": (
                "The white, gold and × versions of one effect share a group id. "
                "Evolved skills point at the skill they evolved from."
            ),
            "fields": ("rarity", "group_id", "tier", "versions", "evolves_from"),
        }),
        ("Game data", {
            "classes": ("collapse",),
            "description": "Filled by the game-data import and overwritten by the next one.",
            "fields": ("icon_id", "cost", "precondition", "condition", "effects",
                       "evolution_scenario"),
        }),
    )

    @admin.display(description="Shown", boolean=True)
    def on_global(self, obj):
        return obj.is_on_global

    @admin.display(description="When it activates, and what it does")
    def readable(self, obj):
        if obj.pk is None:
            return "–"
        described = skill_text.describe(obj, skill_text.context_from_database())
        rows = []
        if described["wit_check"]:
            rows.append(format_html("<p>{}</p>", "Rolls against Wit before it fires."))
        for number, slot in enumerate(described["slots"], start=1):
            label = f"Part {number}: " if len(described["slots"]) > 1 else ""
            if slot["after"]["all"] or slot["after"]["any"]:
                rows.append(format_html("<p><b>{}Only after</b> {}</p>", label,
                                        _condition_html(slot["after"])))
            rows.append(format_html("<p><b>{}When</b> {}</p>", label,
                                    _condition_html(slot["when"]) or "any time"))
            timing = [text for text in (slot["duration"], slot["cooldown"]) if text]
            rows.append(format_html(
                "<p><b>{}Does</b> {}{}</p>", label,
                _phrases_html(slot["effects"], "; "),
                f" ({', '.join(timing)})" if timing else "",
            ))
        return format_html_join("", "{}", ((row,) for row in rows))

    @admin.display(description="Where it comes from")
    def sources(self, obj):
        if obj.pk is None:
            return "–"
        lines = []
        for row in obj.umas.select_related("uma").order_by("source", "uma__name"):
            level = f", level {row.level}" if row.level is not None else ""
            lines.append(format_html(
                '<a href="{}">{}</a> ({}{})',
                reverse("admin:calculatorapi_uma_change", args=[row.uma_id]),
                row.uma.name, row.get_source_display().lower(), level,
            ))
        for row in obj.support_cards.select_related("support_card").order_by("source"):
            lines.append(format_html(
                '<a href="{}">{}</a> ({})',
                reverse("admin:calculatorapi_supportcard_change", args=[row.support_card_id]),
                row.support_card.name, row.get_source_display().lower(),
            ))
        if obj.evolves_from_id:
            lines.append(format_html("Evolves from {}", obj.evolves_from.admin_label))
        for evolution in obj.evolutions.all():
            lines.append(format_html("Evolves into {}", evolution.admin_label))
        if not lines:
            return "–"
        return format_html_join("", "<div>{}</div>", ((line,) for line in lines))

    @admin.display(description="Name", ordering="name")
    def label(self, obj):
        # A JP-only row has no global name; it is listed by its Japanese one.
        return obj.admin_label

    def get_changelist_instance(self, request):
        # "Versions" names each row's siblings. One query for the page's rows
        # rather than one per row (siblings() is a query each): the page's
        # rows are swapped for a list carrying their siblings, which is what
        # the template iterates. The change page's column takes the one query.
        changelist = super().get_changelist_instance(request)
        rows = list(changelist.result_list)
        by_group = {}
        for skill in Skill.objects.filter(group_id__in={row.group_id for row in rows}).order_by("-tier"):
            by_group.setdefault(skill.group_id, []).append(skill)
        for row in rows:
            row.sibling_rows = [
                skill for skill in by_group.get(row.group_id, []) if skill.pk != row.pk
            ]
        changelist.result_list = rows
        return changelist

    @admin.display(description="Versions")
    def versions(self, obj):
        """The other tiers of this effect, plus the evolution link when set."""
        if obj.pk is None:
            return "—"
        siblings = getattr(obj, "sibling_rows", None)
        if siblings is None:
            siblings = obj.siblings()
        # version_label, not the tier's display: tier 2 is a white ◎ in some
        # groups, and the gold above it (tier 3) has no display at all.
        parts = [
            f"{sibling.version_label} {sibling.name} ({sibling.game_id})"
            for sibling in siblings
        ]
        if obj.evolves_from_id:
            parts.append(f"evolved from {obj.evolves_from.name} ({obj.evolves_from.game_id})")
        return ", ".join(parts) if parts else "—"


@admin.register(SupportCard)
class SupportCardAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    inlines = (SupportCardSkillInline,)
    list_display = ("image_preview", "name", "game_id", "card_type", "rarity")
    list_display_links = ("name",)
    list_filter = ("card_type", "rarity", "is_placeholder", PURPOSE_FILTER)
    ordering = ("name",)
    search_fields = ("name", "=game_id")  # required: autocomplete source for banner inlines
    readonly_fields = ("image_preview", "is_on_global")
    # Explicit since purpose arrived: Django's every-field default form had
    # nowhere to say that one of these boxes is public.
    fieldsets = (
        (None, {"fields": ("name", "game_id", "image", "image_preview", "is_placeholder",
                           "admin_comments")}),
        PURPOSE_FIELDSET,
        ("Game data", {
            "classes": ("collapse",),
            "description": (
                "Filled by the game-data import and overwritten by the next one, "
                "so edits here do not stick. Fix the source instead."
            ),
            "fields": ("is_on_global", "title", "card_type", "rarity", "character_id",
                       "group_members"),
        }),
    )


@admin.register(GameEvent)
class GameEventAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    list_display = (
        "name", "banner_timeline", "confirmed_start_date", "confirmed_end_date",
        "carat_amount", "carats_throughout",
    )
    date_hierarchy = "banner_timeline__global_start_date"
    ordering = ("-banner_timeline__global_start_date",)
    search_fields = ("name",)
    autocomplete_fields = ("banner_timeline",)
    list_select_related = ("banner_timeline",)
    readonly_fields = ("image_preview",)
    fieldsets = (
        (None, {"fields": ("name", "banner_timeline", "image", "image_preview")}),
        ("Rewards", {"fields": (
            "carat_amount", "carats_throughout",
            "uma_ticket_amount", "support_ticket_amount",
            "ssr_shard_amount", "sr_shard_amount",
            "ssr_crystal_amount", "sr_crystal_amount",
        )}),
    )

    @admin.display(description="Start date")
    def confirmed_start_date(self, obj):
        # Admin-display only, no prediction math -- just the linked banner's
        # own confirmed date, same rule GameEventSerializer's confirmed-only
        # variant uses for the standalone /events route.
        if obj.banner_timeline_id is None:
            return "—"
        return obj.banner_timeline.global_start_date or "—"

    @admin.display(description="End date")
    def confirmed_end_date(self, obj):
        if obj.banner_timeline_id is None or obj.banner_timeline.global_end_date is None:
            return "—"
        return obj.banner_timeline.global_end_date + GAME_EVENT_END_DATE_BUFFER


@admin.register(Scenario)
class ScenarioAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    """A training scenario — a new, optional way to play the game.

    No date fields to edit, and no end date to edit anywhere: a scenario takes
    its start from its launch banner and never ends. It stays available forever
    once released, so there is nothing for an end date to mean. The list column
    shows the banner's confirmed start with no prediction math — same rule
    GameEventAdmin uses.

    No inline: this is a single FK, not a parts table like a campaign's.
    """
    list_display = ("name", "banner_timeline", "confirmed_start_date")
    ordering = ("-banner_timeline__global_start_date",)
    search_fields = ("name",)
    autocomplete_fields = ("banner_timeline",)
    list_select_related = ("banner_timeline",)
    readonly_fields = ("image_preview",)
    fieldsets = (
        (None, {"fields": ("name", "image", "image_preview")}),
        ("Launch", {
            "fields": ("banner_timeline",),
            "description": (
                "The scenario's start date is taken from this banner. A "
                "scenario has no end date — it stays playable after release."
            ),
        }),
    )

    @admin.display(description="Start date")
    def confirmed_start_date(self, obj):
        if obj.banner_timeline_id is None:
            return "—"
        return obj.banner_timeline.global_start_date or "—"


@admin.register(DailyLegendRaceRelease)
class DailyLegendRaceReleaseAdmin(ModelAdmin):
    """A batch of umas joining the daily legend races.

    Like a scenario, a batch has a start and no end, taken from a banner. It
    also has a day offset, for batches that land a few days after that banner.
    The list shows the banner's confirmed start plus the offset, with no
    prediction maths (same rule as ScenarioAdmin), and how the batch shows on
    the site: dated, tentative (no banner yet), or not at all (no banner and
    no umas).

    No image on the form. The model still has the column, but nothing on the
    site draws it since a batch became a pill on its banner's Timeline card,
    and an upload box that does nothing is a trap for an editor. To bring it
    back, add "image" to the first fieldset and the two image mixins
    (ImagePreviewMixin, SpacesImagePickerMixin) to the bases.
    """
    list_display = (
        "name", "banner_timeline", "offset_days", "confirmed_start_date",
        "uma_count", "on_the_site",
    )
    # Newest batch first, by the banner's JP date. NOT the global date: a
    # predicted banner has none, and most batches are years out, so sorting on
    # it left nearly every row tied on NULL and the list fell back to name
    # order (1.5th, 1st, 2.5th, 2nd...). Every banner has a JP date today and
    # the two calendars run in the same order. The global date is the second
    # key only for a banner that might one day have no JP date; the two are
    # never mixed into one key, because they are years apart. Unlinked batches
    # go last, stated outright because Postgres and SQLite disagree on where a
    # descending sort puts NULL.
    ordering = (
        F("banner_timeline__jp_start_date").desc(nulls_last=True),
        F("banner_timeline__global_start_date").desc(nulls_last=True),
        "name",
    )
    search_fields = ("name",)
    autocomplete_fields = ("banner_timeline",)
    list_select_related = ("banner_timeline",)
    inlines = [DailyLegendRaceUmaInline]
    fieldsets = (
        (None, {"fields": ("name",)}),
        ("Date", {
            "fields": ("banner_timeline", "offset_days"),
            "description": (
                "The batch's date is this banner's start date, plus the offset. "
                "Pick the banner the batch arrives with, usually the "
                "anniversary's last part. If the timeline has no banner for "
                "the batch yet, leave it blank: the Legend Races tab then "
                "lists the batch as tentative, with no date, and the Timeline "
                "leaves it out. A batch has no end date: once umas join the "
                "daily races, they stay."
            ),
        }),
    )

    def get_queryset(self, request):
        # Counted in the list query, so the column costs nothing per row.
        return super().get_queryset(request).annotate(_uma_count=Count("uma_links"))

    @admin.display(description="Start date")
    def confirmed_start_date(self, obj):
        if obj.banner_timeline_id is None or obj.banner_timeline.global_start_date is None:
            return "—"
        return obj.banner_timeline.global_start_date + timedelta(days=obj.offset_days)

    @admin.display(description="Umas", ordering="_uma_count")
    def uma_count(self, obj):
        return obj._uma_count  # pylint: disable=protected-access

    @admin.display(description="On the site")
    def on_the_site(self, obj):
        # Three answers, matching _build_public_payload. A batch with a banner
        # shows with that banner's date, predicted or confirmed (every banner
        # on the timeline has one). Without a banner it shows as tentative, as
        # long as it has umas to show. With neither it is a draft.
        if obj.banner_timeline_id is not None:
            return "Yes, with a date"
        if obj._uma_count:  # pylint: disable=protected-access
            return "Tentative, no date"
        return "Hidden: no banner or umas"


@admin.register(AnniversaryEvent)
class AnniversaryEventAdmin(ImagePreviewMixin, SpacesImagePickerMixin, ModelAdmin):
    """A campaign that sells carat packs and grants selector tickets.

    No date fields to edit: a campaign's dates come from the banner parts on the
    inline below. The list columns show the confirmed span of those parts, with
    no prediction math — same rule GameEventAdmin uses.
    """
    list_display = (
        "name", "event_type", "part_count", "confirmed_start_date",
        "jp_cutoff_date", "product_count",
    )
    list_filter = ("event_type",)
    search_fields = ("name",)
    readonly_fields = ("image_preview",)
    inlines = (AnniversaryEventBannerInline, AnniversaryEventProductInline)
    fieldsets = (
        (None, {"fields": ("name", "event_type", "image", "image_preview")}),
        ("Selectors", {
            "fields": ("jp_cutoff_date", "accent_label"),
            "description": (
                "The cutoff applies to every selector on this campaign unless a "
                "product overrides it. Leave blank for no restriction."
            ),
        }),
    )

    def get_queryset(self, request):
        # Counts as SQL aggregates so the changelist doesn't fire two queries
        # per row for the columns below.
        return super().get_queryset(request).annotate(
            _part_count=Count("banner_links", distinct=True),
            _product_count=Count("products", distinct=True),
            # The earliest confirmed part start, for "Starts" (Min ignores the
            # join's repeats, so it sits beside the distinct counts).
            _starts=Min("banner_links__banner_timeline__global_start_date"),
        )

    @admin.display(description="Parts", ordering="_part_count")
    def part_count(self, obj):
        return obj._part_count  # pylint: disable=protected-access

    @admin.display(description="Products", ordering="_product_count")
    def product_count(self, obj):
        return obj._product_count  # pylint: disable=protected-access

    @admin.display(description="Starts", ordering="_starts")
    def confirmed_start_date(self, obj):
        return obj._starts or "—"  # pylint: disable=protected-access


@admin.register(ChampionsMeeting)
class ChampionsMeetingAdmin(PvpGuideLinkMixin, GlobalDatesStatusMixin, ScheduleOffsetMixin, ImagePreviewMixin,
                            SpacesImagePickerMixin, ModelAdmin):
    list_display = ("name", "cm_number", "jp_start_date", "global_start_date",
                    "global_end_date", "global_dates_status", "schedule_offset_display")
    list_filter = (GlobalDatesFilter, ScheduleOffsetFilter)
    date_hierarchy = "global_start_date"
    ordering = ("-global_start_date",)
    search_fields = ("name",)
    readonly_fields = ("image_preview", "pvp_guide_link")
    # Editors always fill the JP dates; global dates only once the meeting is
    # confirmed (they're left blank until then, and the app predicts them).
    fieldsets = (
        (None, {
            "fields": ("name", "cm_number", "image", "image_preview"),
        }),
        ("JP server dates (always known)", {
            "fields": ("jp_start_date", "jp_end_date"),
        }),
        ("Global server dates (fill when confirmed)", {
            "fields": ("global_start_date", "global_end_date"),
        }),
        SCHEDULE_OFFSET_FIELDSET,
        ("Track details", {
            "fields": ("track", "surface_type", "distance", "length",
                       "track_condition", "season", "weather", "direction"),
        }),
        PVP_GUIDE_FIELDSET,
    )


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(ModelAdmin):
    list_display = ("title", "version", "date", "source")
    date_hierarchy = "date"
    ordering = ("-date",)
    search_fields = ("title",)
    inlines = (ChangelogChangeInline,)
    fieldsets = (
        (None, {
            "fields": ("title", "version", "date"),
        }),
        # Separated because it is not really content: it is the switch between an
        # entry written here and one written in the repo. See the field help text
        # and calculatorapi/management/commands/sync_changelog.py.
        ("Managed entries", {
            "fields": ("key",),
        }),
    )

    @admin.display(description="Source", ordering="key")
    def source(self, obj):
        """Which of the two authoring routes owns this entry.

        Worth a column: an editor who changes a "Repo file" entry here will see
        their edit reverted by the next deploy, and nothing else on the page
        would tell them why.
        """
        return "Repo file" if obj.key else "Written here"


@admin.register(LeagueOfHeroes)
class LeagueOfHeroesAdmin(PvpGuideLinkMixin, GlobalDatesStatusMixin, ScheduleOffsetMixin, ImagePreviewMixin,
                          SpacesImagePickerMixin, ModelAdmin):
    list_display = ("name", "loh_number", "jp_start_date", "global_start_date",
                    "global_end_date", "global_dates_status", "schedule_offset_display")
    list_filter = (GlobalDatesFilter, ScheduleOffsetFilter)
    date_hierarchy = "global_start_date"
    ordering = ("-global_start_date",)
    search_fields = ("name",)
    readonly_fields = ("image_preview", "pvp_guide_link")
    # Mirrors ChampionsMeetingAdmin above section for section — the two content
    # types now hold the same data and render through the same timeline card, so
    # they should be the same page to edit. (This previously omitted the schedule
    # offset fieldset even though the model and ScheduleOffsetMixin both had the
    # field, which left it uneditable.)
    #
    # Editors always fill the JP dates; global dates only once the event is
    # confirmed (they're left blank until then, and the app predicts them).
    fieldsets = (
        (None, {
            "fields": ("name", "loh_number", "image", "image_preview"),
        }),
        ("JP server dates (always known)", {
            "fields": ("jp_start_date", "jp_end_date"),
        }),
        ("Global server dates (fill when confirmed)", {
            "fields": ("global_start_date", "global_end_date"),
        }),
        SCHEDULE_OFFSET_FIELDSET,
        ("Track details", {
            "fields": ("track", "surface_type", "distance", "length",
                       "track_condition", "season", "weather", "direction"),
        }),
        PVP_GUIDE_FIELDSET,
    )


# ── 5. Rank / income tables ──────────────────────────────────────────────────

# `list_editable` turns the changelist into an editable grid: every rank's
# amounts can be updated on one screen with a single Save. The `name` column
# stays non-editable because it is the link to the detail page (a Django
# requirement: editable fields can't be in list_display_links).

@admin.register(ClubRank)
class ClubRankAdmin(ModelAdmin):
    list_display = ("name", "income_amount")
    list_editable = ("income_amount",)
    ordering = ("income_amount",)


@admin.register(TeamTrialsRank)
class TeamTrialsRankAdmin(ModelAdmin):
    list_display = ("name", "income_amount")
    list_editable = ("income_amount",)
    ordering = ("income_amount",)


# What a rank pays from a later event on (models/race_rank_reward.py), edited
# on the rank's own page. Only the changes need a row: before the first one,
# the rank pays its own amount.

class ChampionsMeetingRankRewardInline(TabularInline):
    model = ChampionsMeetingRankReward
    fields = ("from_event_number", "income_amount", "day_amounts")
    extra = 1


class LeagueOfHeroesRankRewardInline(TabularInline):
    model = LeagueOfHeroesRankReward
    fields = ("from_event_number", "income_amount", "day_amounts")
    extra = 1


@admin.register(ChampionsMeetingRank)
class ChampionsMeetingRankAdmin(ModelAdmin):
    # sort_order is editable here so content editors control the dropdown order
    # directly (placements don't sort logically by income). Lower = higher up.
    list_display = ("name", "sort_order", "income_amount", "uma_ticket_amount",
                    "support_ticket_amount", "ssr_shard_amount", "sr_shard_amount")
    list_editable = ("sort_order", "income_amount", "uma_ticket_amount",
                     "support_ticket_amount", "ssr_shard_amount", "sr_shard_amount")
    ordering = ("sort_order",)
    inlines = [ChampionsMeetingRankRewardInline]


@admin.register(LeagueOfHeroesRank)
class LeagueOfHeroesRankAdmin(ModelAdmin):
    list_display = ("name", "income_amount", "uma_ticket_amount",
                    "support_ticket_amount", "ssr_shard_amount", "sr_shard_amount")
    list_editable = ("income_amount", "uma_ticket_amount",
                     "support_ticket_amount", "ssr_shard_amount", "sr_shard_amount")
    ordering = ("income_amount",)
    inlines = [LeagueOfHeroesRankRewardInline]


class UserOshiInline(TabularInline):
    """A supporter's oshis, in order; the first is their picture. Read-only
    apart from removal: the picks are the person's own, and the list is
    entitlement-checked by PATCH /account, which this form would bypass."""
    model = UserOshi
    fields = ("position", "uma")
    readonly_fields = ("position", "uma")
    extra = 0
    can_delete = True
    verbose_name = "Oshi"
    verbose_name_plural = "Oshis (the first is their picture)"


# ── 6. User data (owner-only) ────────────────────────────────────────────────

# The stats block in the order it reads best on a form: ranks, toggles,
# balances. One tuple for BOTH places it appears, the user's own stats and an
# IncomeProfile's, because the two models share the columns (models/game_stats.py)
# and should look the same to whoever edits them.
CALCULATOR_STATS_FIELDS = (
    "club_rank", "team_trials_rank",
    "champions_meeting_rank", "league_of_heroes_rank",
    "daily_carat", "training_pass", "misc_earnings", "custom_misc_earnings_monthly",
    "monthly_shop_tickets", "shop_uma_tickets_bought", "shop_support_tickets_bought",
    "spend_tickets_on_banners", "discounted_paid_pulls", "full_price_paid_pulls",
    "daily_pulls_other_uma_banners", "daily_pulls_other_support_banners",
    "include_purchases_in_projection", "webstore_bonus",
    "current_carat", "current_paid_carat",
    "uma_ticket", "support_ticket",
    "uma_selector_ticket", "support_selector_ticket",
    "ssr_crystals", "sr_crystals", "ssr_shards", "sr_shards",
)


class _ReadOnlyDeviceInline(TabularInline):
    """A staff account's two-factor devices, listed read-only on its user
    page. Nothing is typed in here: an authenticator's secret is minted on the
    setup page and the backup codes on the settings page, both by the person
    themselves (calculatorapi/admin_site.py). DELETE is the one action, and it
    is the lost-phone procedure: with no confirmed authenticator left, the
    person's next login walks them through setting up a new one."""
    extra = 0
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class TOTPDeviceInline(_ReadOnlyDeviceInline):
    model = TOTPDevice
    verbose_name = "Authenticator app"
    verbose_name_plural = "Authenticator app (two-factor)"
    fields = ("name", "confirmed", "created_at", "last_used_at")
    readonly_fields = fields


class StaticDeviceInline(_ReadOnlyDeviceInline):
    model = StaticDevice
    verbose_name = "Backup codes"
    verbose_name_plural = "Backup codes (two-factor)"
    fields = ("name", "codes_left", "created_at", "last_used_at")
    readonly_fields = fields

    @admin.display(description="Unused codes")
    def codes_left(self, obj):
        return obj.token_set.count()


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin, ModelAdmin):
    """
    Django's stock UserAdmin (proper password handling, permission editors)
    extended with a collapsed section for the calculator's stat fields.
    """
    # Unfold's variants of the auth forms — same behavior, themed widgets.
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "display_name", "is_staff", "early_access", "date_joined")
    # UserAdmin's defaults plus the early-access switch, so "who has it" is
    # one click rather than a scroll through every account.
    list_filter = ("is_staff", "is_superuser", "is_active", "early_access", "groups")
    actions = ("grant_early_access", "revoke_early_access")
    # UserAdmin's default searches email and the real-name fields, which
    # ordinary accounts never hold. The handle and the chosen name are the
    # two things a person can quote from their account page.
    search_fields = ("username", "display_name")
    inlines = (UserOshiInline,)
    readonly_fields = ("show_supporter_badge",)
    # Ordinary accounts sign in through Google/Discord and deliberately hold no
    # email or name (see models/social_account.py), so those fields are dropped
    # from the form rather than sitting there inviting someone to fill them in.
    # Staff still need a password, which is why UserAdmin's auth fieldset stays.
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        # Editable, not read-only: it is the person's own choice, set from
        # their account page, and the one reason to touch it here is to
        # blank a display name that should not stand. Their oshis (the
        # picture) are the inline below.
        ("Profile", {
            "fields": ("display_name",),
            "description": (
                "What the person chose on their account page. Shown to them, "
                "and beside anything they post or comment in the gallery."
            ),
        }),
        ("Permissions", {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        # The owner's grant of early access: the third way in beside a live
        # pledge and staff (benefits.early_access_for). A field here and not a
        # Group, because it is a site entitlement rather than an admin
        # permission, and a group costs a query on every request. Its own
        # fieldset, directly under Permissions, so the eye lands on it before
        # it lands on is_staff.
        ("Early access", {
            "fields": ("early_access",),
            "description": (
                "Opens features that are still in early access to this account, "
                "the same as a live pledge or staff. Use this for testers instead "
                "of making them staff: staff can sign in to this admin and skip "
                "every supporter limit. It gives nothing else a supporter gets."
            ),
        }),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
        # The gallery's posting ban, and the person's own badge switch (shown,
        # not editable: it is their choice, made on their account page).
        ("Moderation", {
            "fields": ("publishing_blocked", "show_supporter_badge"),
            "description": (
                "Blocking stops this account posting, commenting, voting or "
                "reporting in the gallery and hides what it already posted. "
                "Unblocking brings it all back."
            ),
        }),
    ) + (
        ("Calculator stats", {
            "classes": ("collapse",),
            "fields": CALCULATOR_STATS_FIELDS,
        }),
    )

    def get_inlines(self, request, obj=None):
        # The two-factor rows only mean something on an account that can sign
        # in to this admin; an ordinary account's page stays as it was.
        if obj is not None and obj.is_staff:
            return (*self.inlines, TOTPDeviceInline, StaticDeviceInline)
        return self.inlines

    def _set_early_access(self, request, queryset, value, verb):
        """Flip the switch on every selected account that is not already
        there, and write a history row for each. The change form logs through
        the admin's own machinery; a bulk action has to say so itself, or the
        account's History page stays silent about who granted what. One save
        per row rather than a queryset update() for the same reason, and
        because the list is a handful of testers, never thousands."""
        changed = 0
        for user in queryset.exclude(early_access=value):
            user.early_access = value
            user.save(update_fields=["early_access"])
            self.log_change(request, user, f"{verb} early access.")
            changed += 1
        self.message_user(
            request, f"{verb} early access for {changed} account(s).", messages.SUCCESS
        )

    @admin.action(description="Grant early access to selected users")
    def grant_early_access(self, request, queryset):
        self._set_early_access(request, queryset, True, "Granted")

    @admin.action(description="Revoke early access from selected users")
    def revoke_early_access(self, request, queryset):
        self._set_early_access(request, queryset, False, "Revoked")


@admin.register(SocialAccount)
class SocialAccountAdmin(ModelAdmin):
    """Read-only view of which provider each account signs in with.

    Fully read-only on purpose: these rows are created by the sign-in flow, and
    hand-editing a provider/subject pair would either lock someone out of their
    plan or hand their plan to somebody else. Deletion is allowed so an account
    can be unlinked on request.
    """
    list_display = ("user", "provider", "created_at", "last_login_at")
    list_filter = ("provider",)
    search_fields = ("user__username",)
    list_select_related = ("user",)
    # subject_id is searchable by nobody and shown to nobody — it is the one
    # identifying value we hold, and the admin has no reason to surface it.
    fields = ("user", "provider", "created_at", "last_login_at")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    list_display = (
        "user", "name", "is_active", "income_profile", "banner_count", "updated_at",
    )
    list_filter = ("is_active",)
    list_select_related = ("user", "income_profile__user")
    search_fields = ("user__username", "name")
    readonly_fields = ("created_at", "updated_at")
    # A profile's __str__ carries its owner's handle, so a plan pointed at the
    # wrong person's profile reads wrong at a glance. Ownership is enforced by
    # the API (plans.py), not by this form; the raw id keeps the picker from
    # listing every profile in the database.
    raw_id_fields = ("income_profile",)

    def get_queryset(self, request):
        # Annotated once for the whole page rather than one COUNT per row.
        return super().get_queryset(request).annotate(
            banner_count=Count("banners"))

    @admin.display(description="Banners", ordering="banner_count")
    def banner_count(self, obj):
        return obj.banner_count


@admin.register(IncomeProfile)
class IncomeProfileAdmin(ModelAdmin):
    """A second stats block of an account, read by the plans that point at it.
    Same fieldset as the user's own stats, on purpose: it IS the same block."""
    list_display = ("user", "plan_count", "purchase_count", "updated_at")
    list_select_related = ("user",)
    search_fields = ("user__username",)
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)
    fieldsets = (
        (None, {"fields": ("user", "created_at", "updated_at")}),
        ("Calculator stats", {"fields": CALCULATOR_STATS_FIELDS}),
    )

    def get_queryset(self, request):
        # distinct=True on both: two reverse joins in one query multiply each
        # other's rows, so without it a profile with 2 plans and 3 purchases
        # would count 6 of each.
        return super().get_queryset(request).annotate(
            plan_count=Count("plans", distinct=True),
            purchase_count=Count("planned_purchases", distinct=True),
        )

    @admin.display(description="Plans", ordering="plan_count")
    def plan_count(self, obj):
        return obj.plan_count

    @admin.display(description="Purchases", ordering="purchase_count")
    def purchase_count(self, obj):
        return obj.purchase_count


@admin.register(UserPlannedBanner)
class UserPlannedBannerAdmin(ModelAdmin):
    list_display = (
        "user", "plan", "banner_uma", "banner_support", "number_of_pulls",
        "reserved_copies",
    )
    # plan__user because Plan.__str__ prints its owner's handle; without it the
    # new column costs one user lookup per row.
    list_select_related = ("user", "plan__user", "banner_uma", "banner_support")
    search_fields = ("user__username",)
    # A row's note is the owner's private text. Staff have no reason to read
    # it, so it is kept off the change form as well as the list.
    exclude = ("note",)


@admin.register(UserPlannedPurchase)
class UserPlannedPurchaseAdmin(ModelAdmin):
    """`income_profile` blank = the account's own purchases; set = the
    purchases of that profile, seen only by the plans pointing at it."""
    list_display = ("user", "income_profile", "product", "quantity", "selector_target")
    list_filter = ("product__product_type", "product__anniversary_event")
    list_select_related = (
        "user", "income_profile__user", "product__anniversary_event",
        "target_uma", "target_support",
    )
    search_fields = ("user__username",)
    raw_id_fields = ("income_profile",)

    @admin.display(description="Selector target")
    def selector_target(self, obj):
        return obj.target_uma or obj.target_support or "—"


@admin.register(UserStepUpSelection)
class UserStepUpSelectionAdmin(ModelAdmin):
    """Read-mostly: users own these, and the app writes them through
    /calculator-data. Here so support can see what someone actually picked."""

    list_display = ("user", "banner_step_up", "slot", "selected_card", "is_target")
    list_filter = ("is_target", "banner_step_up__card_type", "banner_step_up")
    list_select_related = ("user", "banner_step_up", "uma", "support")
    search_fields = ("user__username", "uma__name", "support__name")

    @admin.display(description="Card")
    def selected_card(self, obj):
        return obj.card or "—"


# Group is registered by django.contrib.auth with a plain ModelAdmin;
# re-register it on unfold's base class so it picks up the theme.
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


# ── 7. Calculation constants ─────────────────────────────────────────────────

@admin.register(CalculationConstants)
class CalculationConstantsAdmin(ModelAdmin):
    """
    The one page holding every tunable number the carat projection uses.

    SINGLETON BEHAVIOUR. There is exactly one row, so the usual list → add →
    edit flow is wrong here: adding is refused once the row exists, deleting is
    refused outright, and the changelist redirects straight to the row's edit
    page so "Calculation constants" in the sidebar opens the form directly.

    BLAST RADIUS. Every field on this page changes the numbers shown to every
    user on their next page load, with no deploy and no review. A bad value is
    indistinguishable from a bug in the projection. The model's validators catch
    the obviously-wrong (negatives, an out-of-range prediction factor); nothing
    catches a plausible-but-wrong figure, so treat this page as production data.
    """

    fieldsets = (
        ("Daily income", {
            "fields": ("daily_base_carats", "weekly_bonus_carats"),
        }),
        ("Packs & passes", {
            "fields": (
                "daily_carat_pack_per_day",
                "daily_carat_pack_paid_carats",
                "daily_carat_pack_cycle_days",
                "training_pass_start_date",
                "training_pass_monthly_free_carats",
                "training_pass_monthly_paid_carats",
                "monthly_base_reward",
                "training_pass_free_uma_tickets",
                "training_pass_free_support_tickets",
                "training_pass_paid_bonus_uma_tickets",
                "training_pass_paid_bonus_support_tickets",
                "training_pass_paid_ssr_shards",
            ),
        }),
        ("Login campaigns & gifts", {
            "fields": (
                "misc_earnings_monthly",
                "misc_earnings_delay_days",
                "fifty_day_login_carats",
                "fifty_day_login_cycle_days",
                "valentines_carats", "valentines_month", "valentines_day",
                "white_day_carats", "white_day_month", "white_day_day",
                "monthly_shop_uma_tickets",
                "monthly_shop_support_tickets",
                "monthly_shop_uma_tickets_max",
                "monthly_shop_support_tickets_max",
                "monthly_shop_restock_day",
            ),
        }),
        ("Pull costs & uncap", {
            "fields": (
                "pull_cost_carats",
                "discounted_pull_cost_carats",
                "shards_per_crystal",
            ),
        }),
        ("Step-up banners", {
            "description": (
                "The Select Step-Up cost ladder. The five step costs repeat every "
                "five steps, so they describe a ladder of any length. The target "
                "rate is DERIVED from the pool size (~3% total rate across 10 "
                "selected cards) — it is not an independent dial."
            ),
            "fields": (
                "step_up_cost_step_1",
                "step_up_cost_step_2",
                "step_up_cost_step_3",
                "step_up_cost_step_4",
                "step_up_cost_step_5",
                "step_up_pulls_per_step",
                "step_up_target_rate",
                "step_up_max_rounds",
            ),
        }),
        ("Rate-up rates", {
            "description": (
                "The chance of pulling one rate-up card on an ordinary banner, by rarity. "
                "Each card gets its rarity's rate, unless more cards share the rate-up than "
                "the pool allows: then the pool is split between them (20 SSRs sharing 3% "
                "is 0.15% each). Decimals: 0.0075 is 0.75%. A single banner that breaks "
                "the rule is fixed on that banner's page, with 'rate override' or "
                "'rate-up picks', not here."
            ),
            "fields": (
                "rate_up_rate_3",
                "rate_up_pool_3",
                "rate_up_rate_2",
                "rate_up_pool_2",
                "rate_up_rate_1",
                "rate_up_pool_1",
            ),
        }),
        ("Event carat decay curve", {
            "description": (
                "Governs how an event's 'carats throughout' pool is front-loaded "
                "across its run. Changing these moves every event's contribution "
                "at once — retune against the parity harness, not by eye."
            ),
            "fields": (
                "throughout_end_offset_days",
                "throughout_filter_grace_days",
                "throughout_decay_k",
                "throughout_decay_linear_slope",
            ),
        }),
        ("Global date prediction", {
            "description": (
                "How unconfirmed Global dates are predicted from the JP schedule. "
                "These move BANNER DATES, not just income."
            ),
            "fields": ("prediction_factor", "game_event_end_buffer_days"),
        }),
        ("PvP milestones", {
            "description": (
                "The key moments of every Champions Meeting and League of Heroes, "
                "in hours after the event's start date (negative means before it). "
                "Round 1 starts at the start date. The PvP page shows a date for "
                "each one that is filled in."
            ),
            "fields": (
                "cm_registration_opens_hours",
                "cm_round_two_hours",
                "cm_finals_registration_closes_hours",
                "cm_finals_hours",
                "loh_registration_opens_hours",
                "loh_round_two_hours",
                "loh_finals_registration_closes_hours",
                "loh_finals_hours",
            ),
        }),
    )

    def has_add_permission(self, request):
        # The row is created on first read by CalculationConstants.load(), so
        # "add" is only ever reachable before that has happened.
        return not CalculationConstants.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Skip the one-row list and open the form."""
        constants = CalculationConstants.load()
        return redirect(
            reverse("admin:calculatorapi_calculationconstants_change",
                    args=(constants.pk,))
        )


# ── 8. Patreon supporters ────────────────────────────────────────────────────

@admin.register(PatreonTier)
class PatreonTierAdmin(ModelAdmin):
    """Pledge tiers, ordered by hand.

    `order` is list_editable so the whole ladder can be renumbered on one
    screen — which is the only thing anyone ever wants to do here.
    """

    list_display = ("name", "order", "supporter_count")
    list_editable = ("order",)
    ordering = ("order", "name")
    search_fields = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_supporter_count=Count("supporters"))

    @admin.display(description="Supporters", ordering="_supporter_count")
    def supporter_count(self, obj):
        return obj._supporter_count  # pylint: disable=protected-access


@admin.register(PatreonSupporter)
class PatreonSupporterAdmin(ModelAdmin):
    """The public thank-you list.

    The important column here is "Show name publicly", which is off by default
    and is the only thing that puts a name on the website. It is `list_editable`
    so the whole list can be reviewed and cleared in one pass, and it is the one
    field the CSV importer will never touch.

    `email` is shown here and NOWHERE else. It is what distinguishes two rows
    whose display names collide, or the old and new row left behind when a
    patron renames themselves on Patreon. It is not on the public serializer and
    must not be added to it.

    THE ACCOUNT LINK IS READ-ONLY. `linked_user` is what grants supporter
    entitlement on the website, and `patreon_user_id` is what the sync matches
    on. Both are machine-derived — the sync sets them, and the person themselves
    sets or clears them by linking their Patreon login. Making either editable
    would put "who gets the thing they paid for" behind a form with no review,
    and hand-editing the id would silently reassign one patron's entitlement to
    another. `tier` and `is_active` stay editable because THOSE are editorial
    judgements about a pledge, not identity.
    """

    change_list_template = "admin/calculatorapi/patreonsupporter/change_list.html"

    list_display = (
        "display_name", "email", "tier", "is_public", "is_active",
        "has_website_account", "patron_since",
    )
    list_editable = ("is_public", "is_active")
    list_filter = ("is_public", "is_active", "tier")
    ordering = ("tier__order", "display_name")
    # Searching by email is the fast way to find the row for a patron who wrote
    # in, when the name they signed the message with isn't the one on the row.
    search_fields = ("display_name", "email")
    autocomplete_fields = ("tier",)
    list_select_related = ("tier",)
    readonly_fields = ("patreon_user_id", "linked_user")

    @admin.display(boolean=True, description="Website account")
    def has_website_account(self, obj):
        """Whether this patron has linked a site login — i.e. whether they can
        actually receive supporter benefits. The commonest support question is
        'I\'m pledging but the site doesn\'t know', and this column answers it
        at a glance without opening the row."""
        return obj.linked_user_id is not None

    fieldsets = (
        (None, {
            "description": (
                "The email is for identification inside the admin only. It is never "
                "sent to the website, and it is not a mailing list — see the privacy "
                "note on the model before using it for anything else."
            ),
            "fields": ("display_name", "email", "tier"),
        }),
        ("Publication", {
            "description": (
                "A supporter is counted anonymously until 'Show name publicly' is "
                "ticked. Only tick it for a name they chose to be thanked by — the "
                "Patreon export's Name column is often a real billing name."
            ),
            "fields": ("is_public", "is_active", "patron_since"),
        }),
        ("Website account", {
            "description": (
                "Read-only. Filled automatically when this patron signs in with "
                "Patreon or connects it to an existing account — that is what lets "
                "them see supporter-only features. Empty is normal: most patrons "
                "never make an account here. Supporters added from a CSV have no "
                "Patreon ID and can never be linked, which is a reason to prefer "
                "the API sync."
            ),
            "fields": ("linked_user", "patreon_user_id"),
        }),
    )

    def get_urls(self):
        # Extra admin views, registered under this model's URL namespace so they
        # inherit the changelist's own permission check via admin_view().
        return [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="calculatorapi_patreonsupporter_import_csv",
            ),
            path(
                "sync-patreon/",
                self.admin_site.admin_view(self.sync_patreon_view),
                name="calculatorapi_patreonsupporter_sync_patreon",
            ),
            *super().get_urls(),
        ]

    def import_csv_view(self, request):
        """Upload a Patreon members export and reconcile it against the table."""
        # Same gate as adding a row by hand: this view creates supporters.
        if not self.has_add_permission(request):
            return redirect(reverse("admin:calculatorapi_patreonsupporter_changelist"))

        summary = None
        was_dry_run = False
        if request.method == "POST":
            form = PatreonCsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    rows = parse_patreon_csv(form.cleaned_data["csv_file"])
                except ValueError as exc:
                    form.add_error("csv_file", str(exc))
                else:
                    was_dry_run = form.cleaned_data["dry_run"]
                    summary = apply_patreon_import(
                        rows,
                        deactivate_missing=form.cleaned_data["deactivate_missing"],
                        dry_run=was_dry_run,
                    )
                    if not was_dry_run:
                        self.message_user(
                            request,
                            f"Imported {len(rows)} row(s): "
                            f"{len(summary['created'])} added, "
                            f"{len(summary['deactivated'])} deactivated. "
                            "No names were published — tick 'Show name publicly' to do that.",
                        )
        else:
            form = PatreonCsvImportForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Import Patreon CSV",
            "opts": self.model._meta,  # pylint: disable=protected-access
            "form": form,
            "summary": summary,
            "was_dry_run": was_dry_run,
        }
        return TemplateResponse(
            request, "admin/calculatorapi/patreonsupporter/import_csv.html", context
        )

    def sync_patreon_view(self, request):
        """Fetch the member list from the Patreon API and reconcile it.

        The same two steps as `sync_patreon_supporters`, and the same two steps
        as the scheduled endpoint — fetch rows, hand them to
        `apply_patreon_import`. Only the trigger differs. Nothing here decides
        what a sync means, which is what keeps the three in agreement.

        Gated on `has_add_permission` like the CSV view: a sync creates
        supporters, so it needs the same permission adding one by hand does.
        Notably it does NOT need access to the credentials, which live on an
        unregistered model precisely so no admin page can read them.
        """
        if not self.has_add_permission(request):
            return redirect(reverse("admin:calculatorapi_patreonsupporter_changelist"))

        credentials = PatreonCredentials.load()
        summary = None
        was_dry_run = False
        error = None

        if request.method == "POST":
            form = PatreonSyncForm(request.POST)
            if form.is_valid():
                was_dry_run = form.cleaned_data["dry_run"]
                try:
                    rows = patreon_api.fetch_members(credentials)
                except patreon_api.PatreonApiError as exc:
                    error = str(exc)
                    if not was_dry_run:
                        credentials.last_sync_error = error
                        credentials.save(update_fields=["last_sync_error"])
                else:
                    summary = apply_patreon_import(
                        rows,
                        deactivate_missing=form.cleaned_data["deactivate_missing"],
                        dry_run=was_dry_run,
                    )
                    if not was_dry_run:
                        credentials.last_synced_at = timezone.now()
                        credentials.last_sync_error = ""
                        credentials.save(
                            update_fields=["last_synced_at", "last_sync_error"]
                        )
                        self.message_user(
                            request,
                            f"Synced {len(rows)} member(s) from Patreon: "
                            f"{len(summary['created'])} added, "
                            f"{len(summary['deactivated'])} deactivated. "
                            "No names were published — tick 'Show name publicly' to do that.",
                        )
        else:
            form = PatreonSyncForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Sync from Patreon",
            "opts": self.model._meta,  # pylint: disable=protected-access
            "form": form,
            "summary": summary,
            "was_dry_run": was_dry_run,
            "error": error,
            "is_configured": credentials.is_configured,
            "last_synced_at": credentials.last_synced_at,
            # Suppressed once this run has produced its own error, so the page
            # shows one failure rather than the same message twice.
            "last_sync_error": "" if error else credentials.last_sync_error,
        }
        return TemplateResponse(
            request, "admin/calculatorapi/patreonsupporter/sync_patreon.html", context
        )


# ── 9. Site content ──────────────────────────────────────────────────────────

@admin.register(SitePage)
class SitePageAdmin(ModelAdmin):
    """The prose pages: About and the carat income guide.

    CHANGE ONLY. A page exists because the frontend has a route for it, and a
    route is code, so there is nothing an editor could add here that would
    appear on the site, and deleting a row would leave a live route with no
    words. Same posture as CalculationConstants: the rows exist; you edit them.

    The words a visitor sees update the moment a page is saved (the site
    fetches this content after it loads). Search engines see the copy baked
    into the site at its last build, which is what the "Rebuild website"
    button on the changelist is for. See rebuild_website_view.
    """

    change_list_template = "admin/calculatorapi/sitepage/change_list.html"

    list_display = ("title", "slug", "updated_at")
    readonly_fields = ("slug", "updated_at")
    fieldsets = (
        (None, {
            "description": (
                "Saving changes the page for visitors straight away. Search engines "
                "keep seeing the old text until the site is rebuilt, so when you "
                "are done editing for the day press \"Rebuild website\" on the "
                "Pages list. Terms and the Privacy Policy are not here on purpose: "
                "they describe what the site does, so they change with the code."
            ),
            "fields": ("slug", "title", "meta_description"),
        }),
        ("Text", {
            "description": (
                "Markdown. A blank line starts a new paragraph, ## starts a "
                "section, **bold**, *italic*, and a link is [the words](/faq). "
                "Links to other pages on this site start with a slash."
            ),
            "fields": ("body", "updated_at"),
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        # Registered under this model's namespace so admin_view() applies the
        # usual staff check; the view then gates on is_staff explicitly too.
        return [
            path(
                "rebuild-website/",
                self.admin_site.admin_view(self.rebuild_website_view),
                name="calculatorapi_sitepage_rebuild_website",
            ),
            *super().get_urls(),
        ]

    def rebuild_website_view(self, request):
        """Start a production rebuild so the baked pages pick up the edits.

        Gated on `is_staff` alone, not on a model permission: every staff
        account may press it, whatever it can edit, because a rebuild changes
        no content. It only republishes what is already saved. POST only; a
        GET just lands back on the list.

        The DigitalOcean call and its two guards (not configured, already
        running) live in digitalocean_api. Both surface here as a message
        rather than an error page: an editor should never see a 500 for
        pressing a button whose worst case is "not right now".
        """
        # Where to land afterwards. The list needs view permission on Pages,
        # which a staff account allowed to press this button may not have, so
        # such an account goes back to the dashboard with the message instead.
        back = (
            reverse("admin:calculatorapi_sitepage_changelist")
            if self.has_view_permission(request)
            else reverse("admin:index")
        )
        if not request.user.is_staff or request.method != "POST":
            return redirect(back)
        try:
            digitalocean_api.trigger_rebuild()
        except digitalocean_api.RebuildError as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
        else:
            self.message_user(
                request,
                "Rebuild started. The site updates in about 5 to 10 minutes.",
            )
        return redirect(back)


class FaqItemInline(StackedInline):
    """The questions in a category, edited on the category's page.

    Stacked rather than tabular: an answer is a few paragraphs of markdown,
    and a tabular row is too narrow to write one in.
    """
    model = FaqItem
    fields = ("order", "question", "slug", "answer", "show_on_homepage")
    ordering = ("order", "id")
    extra = 0


@admin.register(FaqCategory)
class FaqCategoryAdmin(ModelAdmin):
    """The FAQ: categories with their questions inline.

    Fully editable, unlike the pages above. A question's slug is its anchor on
    the FAQ page and is linked to from elsewhere on the site, so the field's
    help text asks editors not to change it once published.
    """

    list_display = ("title", "slug", "order", "question_count")
    list_editable = ("order",)
    ordering = ("order", "id")
    inlines = (FaqItemInline,)
    fieldsets = (
        (None, {
            "description": (
                "Each category is a section on the FAQ page, with its questions "
                "underneath. Tick \"Show on homepage\" on a question to put it in "
                "the short FAQ teaser on the homepage. Edits are live as soon as "
                "you save; press \"Rebuild website\" on the Pages list when you are "
                "done so search engines see them too."
            ),
            "fields": ("title", "slug", "order"),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_question_count=Count("items"))

    @admin.display(description="Questions", ordering="_question_count")
    def question_count(self, obj):
        return obj._question_count  # pylint: disable=protected-access


# ═══════════════════════════════════════════════════════════════════════════
# 10. Security: django-axes's tables, read-only
# ═══════════════════════════════════════════════════════════════════════════
# axes keeps two tables. AccessAttempt is the live lockout counter, one row per
# (username, address) pair with recent wrong passwords; AccessLog is one row
# per successful sign-in, with its sign-out time. Both are written by axes and
# only ever READ here (its own admins are switched off in settings:
# AXES_ENABLE_ADMIN). Deleting an AccessAttempt row IS the unlock, which is why
# delete stays on. Owner-only in practice: the content editors group holds no
# axes permission, so the Security sidebar group hides from them.
# The posted form data axes stores (post_data) is left off both pages on
# purpose: there is nothing to learn from it that the row does not already say.


class _ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AccessAttempt)
class AccessAttemptAdmin(_ReadOnlyAdmin):
    """Who is locked out, or on their way to it. A row is deleted by axes when
    the cool-off passes, or by hand here to unlock someone early."""
    list_display = ("username", "ip_address", "failures_since_start", "attempt_time")
    search_fields = ("username", "ip_address")
    ordering = ("-attempt_time",)
    fields = ("username", "ip_address", "failures_since_start", "attempt_time",
              "path_info", "user_agent")
    readonly_fields = fields
    actions = ("delete_selected",)


@admin.register(AccessLog)
class AccessLogAdmin(_ReadOnlyAdmin):
    """Every successful admin sign-in: who, from where, when, and when they
    signed out (blank while the session is still open)."""
    list_display = ("username", "ip_address", "attempt_time", "logout_time")
    search_fields = ("username", "ip_address")
    ordering = ("-attempt_time",)
    fields = ("username", "ip_address", "attempt_time", "logout_time", "path_info", "user_agent")
    readonly_fields = fields
