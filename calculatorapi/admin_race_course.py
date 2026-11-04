"""
The Race courses page: the course map's records (models/race_course.py), the
two buttons that fill them (admin_course_import.py), and the path swap.

Registered here and imported by admin.py for that side effect, like
admin_gallery, so admin.py doesn't grow another few hundred lines.

What an editor does here, in order (backend/docs/content-editing.md):
  1. "Import courses" with the bundle Claude builds from Daptrius's zips.
  2. "Link courses", which links every CM and LoH the JP snapshot knows.
  3. Per course, when needed: swap the path where the import had two to
     choose from, set "Later laps" on a course over one lap, or untick
     "Show map" to hide one.
"""

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin

from .admin_course_import import (
    CourseBundleImportForm,
    LinkCoursesForm,
    apply_course_bundle,
    apply_course_links,
    load_race_links,
    parse_course_bundle,
    plan_course_links,
)
from .models import RaceCourse

# Every Nth point is plenty for a thumbnail: 1,001 points become ~200.
PREVIEW_STEP = 5


def shape_svg(shape, size=180):
    """A small drawing of a stored shape, the finish marked in red. Admin only."""
    if not shape:
        return "No real shape (a straight course, or no path file): the schematic draws it."
    width = max(x for x, _ in shape) or 1
    height = max(y for _, y in shape) or 1
    stroke = max(width, height) / 90
    points = shape[::PREVIEW_STEP] + [shape[-1]]
    return format_html(
        '<svg viewBox="{} {} {} {}" width="{}" height="{}" role="img" aria-label="Course shape">'
        '<polyline points="{}" fill="none" stroke="currentColor" stroke-width="{}" '
        'stroke-linejoin="round"/><circle cx="{}" cy="{}" r="{}" fill="#dc2626"/></svg>',
        -stroke * 2, -stroke * 2, width + stroke * 4, height + stroke * 4, size, size,
        " ".join(f"{x},{y}" for x, y in points), stroke,
        shape[-1][0], shape[-1][1], stroke * 2.5,
    )


class RaceCourseForm(forms.ModelForm):
    """The course form, plus the swap between the two path files.

    The shapes themselves are never typed: the swap trades the stored shape
    for the candidate's and keeps the old one as the candidate, so swapping
    back is the same choice.
    """

    path_choice = forms.ChoiceField(
        label="Path file", required=False,
        help_text="Where the import found two path files (rail variants a few metres apart), "
                  "which one the map draws. The first is the import's choice.",
    )

    class Meta:
        model = RaceCourse
        # Never the shapes or the source record: those change only through the
        # import and the swap above.
        fields = ("racecourse", "region", "game_id", "track_id", "surface", "distance",
                  "variant", "direction", "corners", "straights", "slopes", "surface_changes",
                  "show_map", "later_laps")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        names = [self.instance.path_source, *self.instance.path_candidates] if self.instance.path_source else []
        if len(names) > 1:
            self.fields["path_choice"].choices = [(name, name) for name in names]
            self.fields["path_choice"].initial = self.instance.path_source
        else:
            del self.fields["path_choice"]

    def save(self, commit=True):
        course = super().save(commit=False)
        choice = self.cleaned_data.get("path_choice")
        if choice and choice != course.path_source and choice in course.path_candidates:
            candidates = dict(course.path_candidates)
            new_shape = candidates.pop(choice)
            candidates[course.path_source] = course.shape
            course.shape, course.path_source, course.path_candidates = new_shape, choice, candidates
        if commit:
            course.save()
        return course


@admin.register(RaceCourse)
class RaceCourseAdmin(ModelAdmin):
    form = RaceCourseForm
    list_display = ("__str__", "game_id", "has_shape", "flag_count", "show_map", "event_count")
    list_filter = ("region", "surface", "show_map", "racecourse")
    # Also what the events' "Course (the map)" autocomplete searches.
    search_fields = ("racecourse",)
    # The game's facts. A superuser may still correct one by hand.
    GAME_FACTS = ("region", "game_id", "track_id", "surface", "distance", "variant", "direction",
                  "corners", "straights", "slopes", "surface_changes")
    fieldsets = (
        (None, {"fields": ("racecourse", "region", "game_id", "track_id",
                           "surface", "distance", "variant", "direction")}),
        ("On the site", {"fields": ("show_map", "later_laps", "used_by")}),
        ("Shape", {"fields": ("shape_preview", "path_choice", "path_source", "candidate_previews")}),
        ("Course record", {"fields": ("corners", "straights", "slopes", "surface_changes")}),
        ("Import notes", {"fields": ("flag_list",)}),
    )

    def get_queryset(self, request):
        # One query for the whole list: the event counts are annotations,
        # never a lookup per row (backend-models.md).
        return super().get_queryset(request).annotate(
            _cms=Count("champions_meetings", distinct=True),
            _lohs=Count("league_of_heroes_events", distinct=True),
        )

    def get_readonly_fields(self, request, obj=None):
        fixed = ["used_by", "shape_preview", "path_source", "candidate_previews", "flag_list"]
        if request.user.is_superuser:
            return fixed
        return fixed + list(self.GAME_FACTS)

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None or len(obj.path_candidates) == 0:
            # Nothing to swap: drop the field and the empty candidates row.
            return [
                (name, {**opts, "fields": tuple(
                    f for f in opts["fields"] if f not in ("path_choice", "candidate_previews"))})
                for name, opts in fieldsets
            ]
        return fieldsets

    @admin.display(description="Real shape", boolean=True)
    def has_shape(self, obj):
        return obj.shape is not None

    @admin.display(description="Import notes")
    def flag_count(self, obj):
        return len(obj.flags) or ""

    @admin.display(description="Events")
    def event_count(self, obj):
        return (getattr(obj, "_cms", 0) or 0) + (getattr(obj, "_lohs", 0) or 0)

    def has_add_permission(self, request):
        # Rows come from "Import courses" only: a course typed by hand would
        # have no record behind it. The import checks the add PERMISSION.
        return False

    @admin.display(description="Used by")
    def used_by(self, obj):
        if obj.pk is None:
            return ""
        events = [*obj.champions_meetings.order_by("cm_number"),
                  *obj.league_of_heroes_events.order_by("loh_number")]
        if not events:
            return "No event yet."
        return format_html_join(", ", "{}", ((str(event),) for event in events))

    @admin.display(description="The map draws")
    def shape_preview(self, obj):
        return shape_svg(obj.shape)

    @admin.display(description="The other path file")
    def candidate_previews(self, obj):
        return format_html_join(
            "", '<div style="display:inline-block;margin-right:1rem"><div>{}</div>{}</div>',
            ((name, shape_svg(shape)) for name, shape in obj.path_candidates.items()),
        )

    @admin.display(description="What the import cleaned up")
    def flag_list(self, obj):
        if not obj.flags:
            return "Nothing."
        return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>", ((f,) for f in obj.flags)))

    # ── The two buttons ──────────────────────────────────────────────────────

    def get_urls(self):
        # Registered under this model's namespace so they go through
        # admin_view(), the one gate with the second factor (auth-privacy.md).
        return [
            path("import-courses/", self.admin_site.admin_view(self.import_courses_view),
                 name="calculatorapi_racecourse_import_courses"),
            path("link-courses/", self.admin_site.admin_view(self.link_courses_view),
                 name="calculatorapi_racecourse_link_courses"),
            *super().get_urls(),
        ]

    def _page(self, request, template, title, **context):
        return TemplateResponse(request, f"admin/calculatorapi/racecourse/{template}", {
            **self.admin_site.each_context(request),
            "title": title,
            "opts": self.model._meta,  # pylint: disable=protected-access
            **context,
        })

    def import_courses_view(self, request):
        """Upload a course bundle; preview, then apply."""
        if not request.user.has_perm("calculatorapi.add_racecourse"):
            return redirect(reverse("admin:calculatorapi_racecourse_changelist"))
        summary, was_dry_run = None, False
        form = CourseBundleImportForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                region, courses = parse_course_bundle(form.cleaned_data["bundle"])
            except ValueError as exc:
                form.add_error("bundle", str(exc))
            else:
                was_dry_run = form.cleaned_data["dry_run"]
                summary = apply_course_bundle(region, courses, dry_run=was_dry_run)
                if not was_dry_run:
                    self.message_user(
                        request,
                        f"Imported {len(courses)} courses: {len(summary['created'])} added, "
                        f"{len(summary['updated'])} updated. Next: Link courses.",
                    )
        return self._page(request, "import_courses.html", "Import courses",
                          form=form, summary=summary, was_dry_run=was_dry_run)

    def link_courses_view(self, request):
        """Link every CM and LoH to the course the game names; preview, then apply."""
        user = request.user
        if not (user.has_perm("calculatorapi.change_championsmeeting")
                and user.has_perm("calculatorapi.change_leagueofheroes")):
            return redirect(reverse("admin:calculatorapi_racecourse_changelist"))
        summary, was_dry_run, error = None, False, None
        form = LinkCoursesForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                links = load_race_links()
            except ValueError as exc:
                error = str(exc)
            else:
                was_dry_run = form.cleaned_data["dry_run"]
                summary = apply_course_links(
                    plan_course_links(links),
                    replace_different=form.cleaned_data["replace_different"],
                    dry_run=was_dry_run,
                )
                if not was_dry_run:
                    self.message_user(
                        request,
                        f"Linked {len(summary['linked']) + len(summary['replaced'])} events.",
                    )
        return self._page(request, "link_courses.html", "Link courses",
                          form=form, summary=summary, was_dry_run=was_dry_run, error=error)
