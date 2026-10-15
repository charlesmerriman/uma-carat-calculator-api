"""
The PvP guide admin: one page per guide, one tab per running style.

WHY ONE TAB PER STYLE
---------------------
The site shows a guide one running style at a time, and the sheet the authors
write in lays its columns out per style. The admin used to be laid out per
table instead: six flat lists on the guide page, each row with a style
dropdown, the skills on a page of their own and every deck's six cards on a
page of its own (fourteen decks per guide). Here the page has a **Guide** tab
(event, published, scenario, overview text, the stats table, team comps, the
general skills) and a tab per style (Runaway, Front, Pace, Late, End), each
holding exactly what the site's tab of that name shows: umas, the advice
paragraph, skills, the example decks with their cards.
Nothing on a style tab asks which style it is for. One form, one Save.

HOW A TAB KNOWS ITS STYLE
-------------------------
Every child table gets one inline class per style from `for_style()`: the
queryset is filtered to the style, the form hides the `style` field and
fixes its value (`FixedStyleForm`), and the formset gets its own prefix
(`front-decks`, `late-uma_recommendations`), because six formsets of one
model would otherwise all post under the same name. The fix lives on the
FORM, not in the formset's `_construct_form`: Django builds the "Add another"
template row through `empty_form`, which bypasses `_construct_form`, so an
added row would post an empty style.

HOW THE TABS ARE DRAWN
----------------------
unfold keeps the open tab in `activeTab` on <body> (persisted in the URL hash,
restored on load) and its inline wrappers show a plain inline when
`activeTab == 'general'`. The two wrapper templates under
templates/admin/calculatorapi/pvpguide/ show an inline when `activeTab` equals
the inline's `tab_group` instead, and the change form template draws the nav
and groups the inlines by tab. The Guide tab is "general", unfold's name for
the fieldsets' tab. A hidden `_pvp_tab` input travels with every POST so
"Save and continue editing" comes back to the same tab (the browser never
sends the hash).

DECKS AND THEIR CARDS
---------------------
unfold's ModelAdmin already includes NestedInlinesModelAdminMixin: a stacked
deck inline declares `inlines = (DeckSlotInline,)` and each deck renders its
cards as a table under it, with its own "Add another". A new deck comes with
six blank rows already numbered 1 to 6 (DeckSlotFormSet.initial); an untouched
blank row is not saved.

PICTURES IN THE PICKERS, AND NO QUERY PER ROW
---------------------------------------------
PictureAutocompleteJsonView (routed at admin/autocomplete/ before the admin's
own) adds each result's image URL to the autocomplete JSON, and
PictureAutocompleteSelect puts the already chosen option's image on its
<option>. static/admin/pvp-pickers.js draws both in select2.

Django's picker fetches the chosen option by pk every time it renders, to
label it, and a filled guide renders a few hundred pickers. Here a row's
picker is handed the object the row already holds: PicturePickersMixin loads
the picks with the rows (select_related) and KnownRowForm passes each one to
its picker, so rendering a saved row asks the database nothing. The same
object answers the form field on a save when the posted value is the one
the row holds (KnownChoiceField).

WHAT A SAVE VALIDATES
---------------------
The rows that changed, and every new row. Django never saves a row the
editor left alone (BaseModelFormSet.save_existing_objects), yet validates it
all the same, at five or six queries a row: the id and the picked object
fetched again, the foreign key checked, the unique constraint, the check
constraints. ChildRowForm switches that off for an untouched saved row with
Django's own `empty_permitted`, which makes full_clean() return before any
check when has_changed() is false. A save of a full guide went from about
1,200 queries to under 100, and the page from about 300 to about 70.
"""

# unfold's ModelAdmin has a deep hierarchy, and every inline here is one
# class per style by design.
# pylint: disable=too-many-ancestors,too-many-lines

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.autocomplete import AutocompleteJsonView
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ValidationError
from django.db.models import Count, F, Prefetch
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import formats
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import action
from unfold.forms import BaseDialogForm, PaginationInlineFormSet
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminSelectWidget,
)

from . import public_payload_cache, pvp_sheet
from .models import (
    ChampionsMeeting,
    LeagueOfHeroes,
    PvpDeck,
    PvpDeckSlot,
    PvpGuide,
    PvpSkillRecommendation,
    PvpStatConfig,
    PvpStyleNote,
    PvpTeamComp,
    PvpUmaRecommendation,
    Skill,
)
from .models.pvp_guide import HIGHLIGHT_HELP, MARKDOWN_HELP, PvpSkillCategory, PvpStyle
from .models.skill import skill_families
from .pvp_guides import (
    GUIDE_TAB,
    SECTION_KEYS,
    SECTIONS,
    STYLE_TABS,
    TAB_KEYS,
    copy_guide,
    guide_for_copy_choices,
    missing_summary,
    site_url,
    styles_with_content,
)

TABULAR = "admin/calculatorapi/pvpguide/tabular.html"
STACKED = "admin/calculatorapi/pvpguide/stacked.html"
HINT = "admin/calculatorapi/pvpguide/_inline_hint.html"

MEDIA_JS = ("admin/vendor/marked.min.js", "admin/pvp-guide-editor.js")
MEDIA_CSS = {"all": ("admin/pvp-guide.css",)}


# ── Pictures in the pickers ──────────────────────────────────────────────────

def picture_url(obj):
    """The image URL of an uma, card or skill, or "" without one."""
    image = getattr(obj, "image", None)
    return image.url if image else ""


class PictureAutocompleteJsonView(AutocompleteJsonView):
    """Django's autocomplete JSON, plus each result's picture.

    Routed at admin/autocomplete/ ahead of the admin's own pattern (urls.py),
    so every autocomplete on the site reads it. A model without an image
    field is unchanged.
    """

    def serialize_result(self, obj, to_field_name):
        result = super().serialize_result(obj, to_field_name)
        url = picture_url(obj)
        if url:
            result["picture"] = url
        # A skill's version ("○ white", "◎ gold", "evolved"), so an editor
        # picks the one the guide means.
        if isinstance(obj, Skill):
            result["version"] = obj.version_label
        return result


class PictureAutocompleteSelect(AutocompleteSelect):
    """Django's picker, with the picture of the option already chosen, and no
    query for a row whose pick came with the row.

    The dropdown's pictures come from the JSON above; the chosen option is
    rendered server side, so it carries its picture as a data attribute.
    static/admin/pvp-pickers.js draws both.

    Django's widget looks the chosen option up by pk on every render, to
    label it. `known_object` is that object when the form already has it
    (KnownRowForm sets it from the row), and then the render asks nothing. A
    value the row does not hold (a posted change, a new row) is looked up as
    before, once, for the label and the picture together.
    """

    known_object = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pictures = {}
        self._versions = {}

    def _chosen(self, to_field, selected):
        """The objects behind the selected values: the row's own when that is
        what is selected, else one lookup."""
        known = self.known_object
        if known is not None and selected == {str(getattr(known, to_field))}:
            return [known]
        if not selected:
            return []
        return self.choices.queryset.using(self.db).filter(**{f"{to_field}__in": selected})

    def optgroups(self, name, value, attr=None):
        # Django's AutocompleteMixin.optgroups, with the lookup routed through
        # _chosen() and the pictures read in the same pass instead of a
        # second lookup.
        default = (None, [], 0)
        groups = [default]
        has_selected = False
        selected_choices = {
            str(v) for v in value if str(v) not in self.choices.field.empty_values
        }
        if not self.is_required and not self.allow_multiple_selected:
            default[1].append(self.create_option(name, "", "", False, 0))
        remote_model_opts = self.field.remote_field.model._meta  # pylint: disable=protected-access
        to_field_name = getattr(
            self.field.remote_field, "field_name", remote_model_opts.pk.attname
        )
        to_field_name = remote_model_opts.get_field(to_field_name).attname
        self._pictures = {}
        self._versions = {}
        for obj in self._chosen(to_field_name, selected_choices):
            option_value = getattr(obj, to_field_name)
            self._pictures[str(option_value)] = picture_url(obj)
            if isinstance(obj, Skill):
                self._versions[str(option_value)] = obj.version_label
            selected = str(option_value) in value and (
                has_selected is False or self.allow_multiple_selected
            )
            has_selected |= selected
            default[1].append(self.create_option(
                name, option_value, self.choices.field.label_from_instance(obj),
                selected_choices, len(default[1]),
            ))
        return groups

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        url = self._pictures.get(str(value))
        if url:
            option["attrs"]["data-picture"] = url
        version = self._versions.get(str(value))
        if version:
            option["attrs"]["data-version"] = version
        return option

    @property
    def media(self):
        # ONE list, not parent + ours: Media's merge only orders files that
        # share a list, and the script must run after select2 and before
        # autocomplete.js initialises the widgets.
        parent = super().media
        return forms.Media(
            js=[*parent._js, "admin/pvp-pickers.js"],  # pylint: disable=protected-access
            css={**parent._css, "all": [*parent._css.get("all", []), *MEDIA_CSS["all"]]},  # pylint: disable=protected-access
        )


class KnownChoiceField(forms.ModelChoiceField):
    """A picker's form field that answers from the row's own object when the
    posted value is the one the row holds, instead of fetching it by pk."""

    known = None

    def to_python(self, value):
        known = self.known
        if known is not None and value not in self.empty_values:
            if str(getattr(known, self.to_field_name or "pk")) == str(value):
                return known
        return super().to_python(value)


class KnownRowForm(forms.ModelForm):
    """Hands each picker the object its row already holds, loaded with the row
    (PicturePickersMixin.get_queryset), so rendering the row's pickers and
    validating a pick that did not change ask the database nothing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        row = self.instance
        if row.pk is None:
            return
        for name, field in self.fields.items():
            if not isinstance(field, KnownChoiceField):
                continue
            model_field = row._meta.get_field(name)  # pylint: disable=protected-access
            if not model_field.is_cached(row):
                continue
            known = model_field.get_cached_value(row)
            if known is None:
                continue
            field.known = known
            # The admin wraps the picker in RelatedFieldWidgetWrapper (the
            # add and change buttons beside it); the picker is its .widget.
            widget = getattr(field.widget, "widget", field.widget)
            if isinstance(widget, PictureAutocompleteSelect):
                widget.known_object = known


class PicturePickersMixin:
    """For an inline whose rows pick an uma, a card, a skill or a banner.

    Every `autocomplete_fields` FK gets PictureAutocompleteSelect, the rows
    are loaded with their picks and their parent (which a row's __str__
    names), and KnownRowForm hands each picker its pick, so a page of saved
    rows renders and re-validates with no query per row.
    """

    form = KnownRowForm

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name in self.get_autocomplete_fields(request) and "widget" not in kwargs:
            kwargs["widget"] = PictureAutocompleteSelect(
                db_field, self.admin_site, using=kwargs.get("using")
            )
            kwargs.setdefault("form_class", KnownChoiceField)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        related = list(self.get_autocomplete_fields(request))
        parent = next(
            (
                field.name
                for field in self.model._meta.fields  # pylint: disable=protected-access
                if field.many_to_one and field.related_model is self.parent_model
            ),
            None,
        )
        if parent:
            related.append(parent)
        # select_related() with NO names follows every foreign key: never call
        # it with an empty list.
        return queryset.select_related(*related) if related else queryset


# ── Forms ────────────────────────────────────────────────────────────────────

class MentionAwareForm(forms.ModelForm):
    """Marks the text boxes the editor script enhances: `[[` suggestions on
    every field that may mention a skill (the model's MENTION_FIELDS), and a
    Markdown preview on the long ones. static/admin/pvp-guide-editor.js."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in getattr(self._meta.model, "MENTION_FIELDS", ()):  # pylint: disable=no-member
            field = self.fields.get(name)
            if field is None:
                continue
            field.widget.attrs["data-pvp-mentions"] = "1"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["data-pvp-markdown"] = "1"


class ChildRowForm(KnownRowForm, MentionAwareForm):
    """A row of one of the guide's tables.

    A saved row the editor left alone is not validated again: see "WHAT A
    SAVE VALIDATES" in the module docstring. `empty_permitted` is Django's
    own switch for that: full_clean() returns before any check when it is
    set and has_changed() is false. A row that changed, and every new row,
    is validated in full.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and self.instance.pk is not None:
            self.empty_permitted = True


class FixedStyleForm(ChildRowForm):
    """A child row whose style is decided by the tab it is on.

    The field stays in the form (hidden) so the unique checks that include
    `style` still run, and clean_style() returns the tab's style whatever was
    posted.
    """

    fixed_style = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["style"]
        field.widget = forms.HiddenInput()
        field.initial = self.fixed_style
        # A blank row must count as unchanged until the editor fills it, and
        # has_changed() reads self.initial (built from the unsaved instance,
        # whose style is ""), not field.initial.
        self.initial["style"] = self.fixed_style
        if self.instance.pk is None:
            self.instance.style = self.fixed_style

    def clean_style(self):
        return self.fixed_style


# unfold renders a required choice field with no blank option, so an untouched
# blank row would post the first choice and count as changed (and then fail on
# its empty picker). These forms give such fields a blank first choice.
BLANK_STYLE = [("", "Pick a style"), *PvpStyle.choices]
BLANK_CATEGORY = [("", "Pick a category"), *PvpSkillCategory.choices]
STAR_CHOICES = [(n, "★" * n) for n in range(1, 6)]
TIER_CHOICES = [
    ("", "No tier"),
    (0, "T0 (take first)"),
    (1, "T1"),
    (2, "T2"),
    (3, "T3"),
    (4, "T4"),
]
SLOT_CHOICES = [(n, f"Slot {n}") for n in range(1, 7)]
ALTERNATIVE_CHOICES = [
    (0, "No, this is the card"),
    (1, "Alternative 1"),
    (2, "Alternative 2"),
    (3, "Alternative 3"),
]


class StatConfigForm(ChildRowForm):
    style = forms.ChoiceField(choices=BLANK_STYLE, widget=UnfoldAdminSelectWidget)

    class Meta:
        model = PvpStatConfig
        fields = "__all__"


class UmaForm(ChildRowForm):
    stars = forms.TypedChoiceField(
        coerce=int, choices=STAR_CHOICES, initial=3, widget=UnfoldAdminSelectWidget,
    )

    class Meta:
        model = PvpUmaRecommendation
        fields = "__all__"


class SkillForm(ChildRowForm):
    category = forms.ChoiceField(choices=BLANK_CATEGORY, widget=UnfoldAdminSelectWidget)
    tier = forms.TypedChoiceField(
        coerce=int,
        empty_value=None,
        required=False,
        choices=TIER_CHOICES,
        widget=UnfoldAdminSelectWidget,
        help_text="Leave on \"No tier\" if the guide has no tiers.",
    )

    class Meta:
        model = PvpSkillRecommendation
        fields = "__all__"


class DeckForm(ChildRowForm):
    class Meta:
        model = PvpDeck
        fields = "__all__"
        # Drag order instead of a number; unfold's sort writes the hidden input.
        widgets = {"order": forms.HiddenInput}


class DeckSlotForm(ChildRowForm):
    slot = forms.TypedChoiceField(coerce=int, choices=SLOT_CHOICES, widget=UnfoldAdminSelectWidget)
    order = forms.TypedChoiceField(
        coerce=int,
        choices=ALTERNATIVE_CHOICES,
        initial=0,
        widget=UnfoldAdminSelectWidget,
        label="Alternative",
        help_text="An alternative shows under the slot's card as \"or\".",
    )

    class Meta:
        model = PvpDeckSlot
        fields = "__all__"
        labels = {"support_card": "Card", "is_borrow": "Borrow"}
        help_texts = {"is_borrow": "", "slot": ""}


def missing_slots(deck):
    """The slot numbers a deck has no card for yet (all six for a new one).

    Reads deck.slots, so a deck that came with its slots prefetched
    (DeckInline.get_queryset) answers without a query. It is asked twice per
    deck, by DeckSlotInline.get_extra and by the formset below.
    """
    if deck is None or deck.pk is None:
        return list(range(1, 7))
    taken = {slot.slot for slot in deck.slots.all() if slot.order == 0}
    return [number for number in range(1, 7) if number not in taken]


class DeckSlotFormSet(PaginationInlineFormSet):
    """A deck's cards: blank rows arrive numbered, and one borrowed slot at most."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "initial", [{"slot": number} for number in missing_slots(kwargs.get("instance"))]
        )
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        # One borrowed SLOT, not one ticked row: the card in a slot and an
        # "or" alternative under it are the same borrow, and the sheet loader
        # ticks both. A saved row the editor left alone has no cleaned_data
        # (ChildRowForm skips its validation), so it is read off the row.
        borrowed = set()
        for form in self.forms:
            if form.has_changed():
                data = getattr(form, "cleaned_data", None) or {}
                if data.get("DELETE"):
                    continue
                is_borrow, slot = data.get("is_borrow"), data.get("slot")
            elif form.instance.pk is not None:
                is_borrow, slot = form.instance.is_borrow, form.instance.slot
            else:
                continue
            if is_borrow and slot is not None:
                borrowed.add(slot)
        if len(borrowed) > 1:
            raise ValidationError(
                "Only one slot in a deck can be the borrowed one. Untick Borrow on the others."
            )


# ── Inlines ──────────────────────────────────────────────────────────────────

class GuideInline:  # pylint: disable=too-few-public-methods
    """What every table on the guide page shares: the tab it sits on and a
    one-line hint above it (the wrapper templates read both).

    On the per-style tables the hidden `style` field goes LAST in `fields`:
    unfold draws the drag handle in the first column's cell, and a hidden
    first column would take the handle with it. `hide_title` drops the
    caption unfold writes over every saved row (the row's __str__), which
    only repeats what the row shows.
    """

    tab_group = GUIDE_TAB
    hint = ""
    template_before = HINT
    extra = 1
    hide_title = True


class StatConfigInline(GuideInline, TabularInline):
    model = PvpStatConfig
    form = StatConfigForm
    template = TABULAR
    fields = ("style", "label", "speed", "stamina", "power", "guts", "wit", "footnote", "order")
    ordering = ("order", "id")
    ordering_field = "order"
    hide_ordering_field = True
    verbose_name = "stat row"
    verbose_name_plural = "Recommended stats"
    hint = (
        "One row per style, and one more for each variant (give it a label like "
        "\"901* + 1 Gold\"). Everyone means the whole team. A blank stat shows as a "
        "dash. Drag the handle to reorder."
    )


class TeamCompInline(GuideInline, TabularInline):
    model = PvpTeamComp
    form = ChildRowForm
    template = TABULAR
    fields = ("text", "note", "order")
    ordering = ("order", "id")
    ordering_field = "order"
    hide_ordering_field = True
    verbose_name = "team comp"
    verbose_name_plural = "Team comps"
    hint = "Team shapes like \"2 Front + 1 Pace\", with a note if one is needed."


class UmaInline(GuideInline, PicturePickersMixin, TabularInline):
    model = PvpUmaRecommendation
    form = UmaForm
    template = TABULAR
    fields = ("uma", "stars", "role", "note", "order", "style")
    autocomplete_fields = ("uma",)
    ordering = ("order", "id")
    ordering_field = "order"
    hide_ordering_field = True
    verbose_name = "uma"
    verbose_name_plural = "Recommended umas"
    hint = (
        "Search by name. Role is a short tag shown on the picture (\"Runaway blocker\"); "
        "the note shows when someone hovers the uma. " + HIGHLIGHT_HELP
    )


class StyleNoteInline(GuideInline, StackedInline):
    model = PvpStyleNote
    form = ChildRowForm
    template = STACKED
    fields = ("body", "style")
    extra = 1
    max_num = 1
    hide_title = True
    verbose_name = "advice"
    verbose_name_plural = "Advice for this style"
    hint = "The paragraph shown under the umas, like the sheet's \"Rec. Skills\" text. " + MARKDOWN_HELP


class SkillInline(GuideInline, PicturePickersMixin, TabularInline):
    model = PvpSkillRecommendation
    form = SkillForm
    template = TABULAR
    fields = ("skill", "other_versions", "category", "tier", "note", "order", "style")
    readonly_fields = ("other_versions",)
    autocomplete_fields = ("skill",)
    ordering = ("tier", "order", "id")
    ordering_field = "order"
    hide_ordering_field = True
    verbose_name = "skill"
    verbose_name_plural = "Recommended skills"
    hint = (
        "Only skills already on global can be picked. Pick the version the guide "
        "means (the picker says white, gold or evolved); the site shows the other "
        "versions beside it, and a team that has the gold counts for a white pick "
        "(a card teaching the gold teaches the white too). Pick a category, and a "
        "tier if the guide has tiers (T0 is take first). Drag to reorder within a tier."
    )

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)

        class SkillFormSet(formset):  # pylint: disable=too-few-public-methods
            """Looks the rows' skill families up once, for the "Other
            versions" column, instead of once per row."""

            families_attached = False

            def get_queryset(self):
                rows = super().get_queryset()
                if not self.families_attached:
                    self.families_attached = True
                    # Evaluates the queryset, which the formset does anyway.
                    families = skill_families([row.skill for row in rows if row.skill_id])
                    for row in rows:
                        row.family_rows = families.get(row.skill_id, [])
                return rows

        return SkillFormSet

    @admin.display(description="Other versions")
    def other_versions(self, obj):
        """The skill's white, gold and evolved forms (models.skill.skill_families),
        so the editor sees what the row covers before saving."""
        if obj is None or obj.pk is None or obj.skill_id is None:
            return "–"
        rows = getattr(obj, "family_rows", None)
        if rows is None:
            rows = obj.skill.family()
        if not rows:
            return "none"
        return ", ".join(f"{row.version_label}: {row.name}" for row in rows)


class DeckSlotInline(PicturePickersMixin, TabularInline):
    """A deck's cards, nested under the deck on the guide page."""
    model = PvpDeckSlot
    hide_title = True
    form = DeckSlotForm
    formset = DeckSlotFormSet
    fields = ("slot", "order", "support_card", "is_borrow", "note")
    autocomplete_fields = ("support_card",)
    ordering = ("slot", "order", "id")
    verbose_name = "card"
    verbose_name_plural = "Cards"

    def get_extra(self, request, obj=None, **kwargs):
        # obj is the deck here: unfold hands a nested inline its parent row,
        # an unsaved one for the "Add another deck" template.
        return len(missing_slots(obj))


class DeckInline(GuideInline, StackedInline):
    model = PvpDeck
    form = DeckForm
    template = STACKED
    # A stacked row's title is where its drag handle and delete live, so it
    # stays; PvpDeck.get_inline_title keeps it to the label and style.
    hide_title = False
    fields = ("label", "note", "order", "style")
    ordering = ("order", "id")
    ordering_field = "order"
    hide_ordering_field = True
    extra = 0
    inlines = (DeckSlotInline,)
    verbose_name = "deck"
    verbose_name_plural = "Example decks"

    def get_queryset(self, request):
        # The slots come with the decks: missing_slots() reads them for the
        # pre-numbered blank rows, twice per deck, and this answers both.
        return super().get_queryset(request).prefetch_related("slots")
    hint = (
        "Each deck has a label (\"Beginner\", \"Standard\", \"Whale\") and six cards. "
        "Tick Borrow on the borrowed card. For an \"or\" under a card, add a row with "
        "the same slot number and Alternative 1. A row with no card and a note like "
        "\"or any speed\" is fine. Drag a deck's handle to reorder the decks."
    )


class StyleInlineMixin:
    """The per-style copy of a child inline: its rows only, its own prefix."""

    fixed_style = None
    formset_prefix = None

    def get_queryset(self, request):
        return super().get_queryset(request).filter(style=self.fixed_style)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        prefix = self.formset_prefix

        class StyleFormSet(formset):  # pylint: disable=too-few-public-methods
            @classmethod
            def get_default_prefix(cls):
                return prefix

        return StyleFormSet


def for_style(base, style, tab=None, **overrides):
    """The inline `base`, showing only `style` rows on tab `tab` (the style's
    own tab unless told otherwise). See the module docstring."""
    stem = base.model._meta.get_field("guide").remote_field.get_accessor_name()  # pylint: disable=protected-access
    form = type(f"{base.form.__name__}{style.capitalize()}", (FixedStyleForm, base.form), {
        "fixed_style": style,
    })
    attrs = {
        "fixed_style": style,
        "formset_prefix": f"{style}-{stem}",
        "tab_group": tab or style,
        "form": form,
        **overrides,
    }
    return type(f"{base.__name__}{style.capitalize()}", (StyleInlineMixin, base), attrs)


GUIDE_INLINES = (
    StatConfigInline,
    TeamCompInline,
    for_style(
        SkillInline, "everyone", tab=GUIDE_TAB,
        verbose_name_plural="General skills (every position)",
        hint=(
            "Skills every position should take. The site shows them inside each "
            "style's recommended skills, beside that style's own picks. The sheet's "
            "\"Green\" and \"Debuffs\" groups go here with that category."
        ),
    ),
)

STYLE_INLINES = tuple(
    inline
    for style, _ in STYLE_TABS
    for inline in (
        for_style(UmaInline, style),
        for_style(StyleNoteInline, style),
        for_style(SkillInline, style),
        for_style(DeckInline, style),
    )
)


# ── The guide form ───────────────────────────────────────────────────────────

def event_choices(current=None):
    """Grouped choices for the Event dropdown: every event without a guide,
    newest first, plus `current` (the guide's own event) so an existing guide
    can still show it."""
    groups = []
    for label, model, kind in (
        ("Champions Meetings", ChampionsMeeting, "cm"),
        ("League of Heroes", LeagueOfHeroes, "loh"),
    ):
        rows = model.objects.filter(pvp_guide__isnull=True)
        if current is not None and isinstance(current, model):
            rows = rows | model.objects.filter(pk=current.pk)
        choices = [
            (f"{kind}-{event.pk}", str(event))
            for event in rows.order_by("-jp_start_date", "-id")
        ]
        if choices:
            groups.append((label, choices))
    return groups


class PvpGuideForm(MentionAwareForm):
    """One Event dropdown in place of the two FK pickers.

    The model keeps two nullable OneToOnes (models/pvp_guide.py says why); an
    editor should not have to know that. clean_event() turns "cm-12" back into
    the right FK and refuses an event that already has another guide, a check
    the form would otherwise skip because the FKs are not form fields.
    """

    event = forms.ChoiceField(
        label="Event",
        widget=UnfoldAdminSelectWidget,
        help_text="Each event has one guide. Events that already have one are not listed.",
    )

    class Meta:
        model = PvpGuide
        fields = (
            "scenario", "is_published", "style_ranking", "short_description",
            "meta_analysis", "admin_comments",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.event if self.instance.pk else None
        self.fields["event"].choices = event_choices(current)
        if current is not None:
            kind = "cm" if self.instance.champions_meeting_id else "loh"
            self.fields["event"].initial = f"{kind}-{current.pk}"

    def clean_event(self):
        value = self.cleaned_data["event"]
        kind, _, pk = value.partition("-")
        model = {"cm": ChampionsMeeting, "loh": LeagueOfHeroes}.get(kind)
        event = model.objects.filter(pk=pk).first() if model and pk.isdigit() else None
        if event is None:
            raise ValidationError("Pick an event.")
        other = PvpGuide.objects.filter(**{f"{self._field_for(kind)}": event}).exclude(pk=self.instance.pk)
        if other.exists():
            raise ValidationError("That event already has a guide. Open it from the list instead.")
        return kind, event

    @staticmethod
    def _field_for(kind):
        return "champions_meeting" if kind == "cm" else "league_of_heroes"

    def clean(self):
        cleaned = super().clean()
        picked = cleaned.get("event")
        if picked:
            kind, event = picked
            self.instance.champions_meeting = event if kind == "cm" else None
            self.instance.league_of_heroes = event if kind == "loh" else None
        return cleaned


# ── Copy from another guide ──────────────────────────────────────────────────

class CopyGuideForm(BaseDialogForm):
    """The dialog behind "Copy from another guide"."""

    source = forms.ChoiceField(label="Copy from", widget=UnfoldAdminSelectWidget)
    sections = forms.MultipleChoiceField(
        label="Sections to copy",
        choices=SECTIONS,
        initial=list(SECTION_KEYS),
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text=(
            "Rows are added to what this guide already has; nothing is overwritten. "
            "A row that is already here (the same uma for a style, say) is skipped."
        ),
    )

    def __init__(self, request, object_id=None, *args, **kwargs):  # pylint: disable=keyword-arg-before-vararg
        super().__init__(request, object_id, *args, **kwargs)
        self.fields["source"].choices = guide_for_copy_choices(exclude_pk=object_id)


class EventKindFilter(admin.SimpleListFilter):
    """Champions Meeting or League of Heroes, read off which FK is set."""
    title = "event kind"
    parameter_name = "kind"

    def lookups(self, request, model_admin):
        return (("cm", "Champions Meeting"), ("loh", "League of Heroes"))

    def queryset(self, request, queryset):
        if self.value() == "cm":
            return queryset.filter(champions_meeting__isnull=False)
        if self.value() == "loh":
            return queryset.filter(league_of_heroes__isnull=False)
        return queryset


# ── The guide admin ──────────────────────────────────────────────────────────

@admin.register(PvpGuide)
class PvpGuideAdmin(ModelAdmin):
    """One guide, every section, on one page. See the module docstring."""

    form = PvpGuideForm
    change_form_template = "admin/calculatorapi/pvpguide/change_form.html"
    warn_unsaved_form = True

    list_display = (
        "event_name", "kind", "starts", "scenario", "filled", "deck_count",
        "is_published", "updated_at",
    )
    list_display_links = ("event_name",)
    list_editable = ("is_published",)
    list_filter = ("is_published", EventKindFilter, "scenario")
    list_select_related = ("champions_meeting", "league_of_heroes", "scenario")
    search_fields = ("champions_meeting__name", "league_of_heroes__name")
    autocomplete_fields = ("scenario",)
    actions = ("publish_selected", "unpublish_selected")
    actions_detail = ("open_on_site", "copy_from_guide", "load_from_sheet")
    readonly_fields = ("site_link", "updated_at")
    fieldsets = (
        (None, {
            "fields": ("event", "scenario", "is_published", "site_link"),
        }),
        ("Overview", {
            "fields": ("style_ranking", "short_description", "meta_analysis"),
        }),
        ("Notes for editors", {
            "classes": ("collapse",),
            "fields": ("admin_comments", "updated_at"),
        }),
    )
    inlines = GUIDE_INLINES + STYLE_INLINES

    class Media:
        js = MEDIA_JS
        css = MEDIA_CSS

    # The add page is the event and the overview only; the tables need a
    # saved guide to hang off, and twenty-eight empty tables would bury the
    # one dropdown that matters.
    def get_inlines(self, request, obj):
        return () if obj is None else self.inlines

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _starts=Coalesce(
                    F("champions_meeting__global_start_date"),
                    F("league_of_heroes__global_start_date"),
                ),
                _jp_starts=Coalesce(
                    F("champions_meeting__jp_start_date"),
                    F("league_of_heroes__jp_start_date"),
                ),
                _deck_count=Count("decks", distinct=True),
            )
            .prefetch_related(
                Prefetch("uma_recommendations", PvpUmaRecommendation.objects.only("id", "guide_id", "style")),
                Prefetch("style_notes", PvpStyleNote.objects.only("id", "guide_id", "style")),
                Prefetch("skill_recommendations", PvpSkillRecommendation.objects.only("id", "guide_id", "style")),
                Prefetch("decks", PvpDeck.objects.only("id", "guide_id", "style")),
            )
        )

    def get_ordering(self, request):
        # Next cup first. An annotation, so it lives here rather than in
        # `ordering`, which the admin checks against model fields.
        return (F("_starts").desc(nulls_last=True), "-id")

    # ── List columns ──

    @admin.display(description="Event")
    def event_name(self, obj):
        return str(obj)

    @admin.display(description="Kind")
    def kind(self, obj):
        return "CM" if obj.champions_meeting_id else "LoH"

    @admin.display(description="Starts", ordering="_starts")
    def starts(self, obj):
        if obj._starts:  # pylint: disable=protected-access
            return formats.date_format(obj._starts, "DATE_FORMAT")  # pylint: disable=protected-access
        if obj._jp_starts:  # pylint: disable=protected-access
            return format_html(
                '<span title="Global dates not confirmed yet; this is the JP date">{} (JP)</span>',
                formats.date_format(obj._jp_starts, "DATE_FORMAT"),  # pylint: disable=protected-access
            )
        return "–"

    @admin.display(description="Filled")
    def filled(self, obj):
        styles = styles_with_content(obj)
        if not styles:
            return "nothing yet"
        return format_html_join(
            " ", '<span class="pvp-chip">{}</span>',
            ((label,) for key, label in STYLE_TABS if key in styles),
        )

    @admin.display(description="Decks", ordering="_deck_count")
    def deck_count(self, obj):
        return obj._deck_count  # pylint: disable=protected-access

    # ── Form extras ──

    @admin.display(description="On the site")
    def site_link(self, obj):
        if obj is None or obj.pk is None:
            return "Save the guide first. Then it can be opened on the site."
        state = (
            "Published, everyone sees it."
            if obj.is_published
            else "Draft: only signed-in staff see it on the site."
        )
        return format_html(
            '{} <a href="{}" target="_blank" rel="noopener">Open on the site</a>',
            state, site_url(obj),
        )

    def render_change_form(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, request, context, add=False, change=False, form_url="", obj=None,
    ):
        # The line under the tabs. Set here, where the view hands over the
        # guide it already fetched, rather than in change_view, which would
        # fetch it (and its four prefetches) a second time.
        if obj is not None:
            context["missing"] = missing_summary(obj)
        return super().render_change_form(request, context, add, change, form_url, obj)

    # "Save and continue editing" lands back on the tab the editor was on.
    # The hidden _pvp_tab input is bound to unfold's activeTab.
    def _keep_tab(self, request, response):
        tab = request.POST.get("_pvp_tab", "")
        if (
            "_continue" in request.POST
            and tab in TAB_KEYS
            and tab != GUIDE_TAB
            and isinstance(response, HttpResponseRedirect)
        ):
            response["Location"] += f"#{tab}"
        return response

    def response_add(self, request, obj, post_url_continue=None):
        return self._keep_tab(request, super().response_add(request, obj, post_url_continue))

    def response_change(self, request, obj):
        return self._keep_tab(request, super().response_change(request, obj))

    # ── Bulk actions ──

    @admin.action(description="Publish selected guides")
    def publish_selected(self, request, queryset):
        count = queryset.update(is_published=True)
        # update() raises no signals; the Timeline's "PvP guide" link reads a
        # cached has_guide, so the cache is dropped by hand.
        public_payload_cache.invalidate()
        self.message_user(request, f"Published {count} guide(s).", messages.SUCCESS)

    @admin.action(description="Unpublish selected guides (back to draft)")
    def unpublish_selected(self, request, queryset):
        count = queryset.update(is_published=False)
        public_payload_cache.invalidate()
        self.message_user(request, f"Unpublished {count} guide(s).", messages.SUCCESS)

    # ── Detail actions (the buttons at the top of the page) ──

    @action(
        description="Open on the site",
        url_path="open-on-site",
        icon="open_in_new",
        attrs={"target": "_blank", "rel": "noopener"},
        permissions=["view"],
    )
    def open_on_site(self, request, object_id):
        guide = get_object_or_404(PvpGuide, pk=object_id)
        return redirect(site_url(guide))

    @action(
        description="Copy from another guide",
        url_path="copy-from",
        icon="content_copy",
        permissions=["change"],
        dialog={
            "title": "Copy from another guide",
            "description": (
                "Start this guide from another one. The rows are copied into this "
                "guide and can be edited after."
            ),
            "form_class": CopyGuideForm,
            "form_submit_text": "Copy",
        },
    )
    def copy_from_guide(self, request, form, object_id):
        target = get_object_or_404(PvpGuide, pk=object_id)
        source = get_object_or_404(PvpGuide, pk=form.cleaned_data["source"])
        copied = copy_guide(source, target, form.cleaned_data["sections"])
        public_payload_cache.invalidate()
        total = sum(copied.values())
        self.message_user(
            request,
            f"Copied {total} row(s) from {source}. Check each tab, then save.",
            messages.SUCCESS,
        )
        # The dialog posts with htmx; a plain redirect would be followed inside
        # the dialog. HX-Redirect sends the whole page instead.
        url = reverse("admin:calculatorapi_pvpguide_change", args=(target.pk,))
        return HttpResponse(status=200, headers={"HX-Redirect": url})

    @action(
        description="Load from the sheet",
        url_path="load-from-sheet",
        icon="cloud_download",
        permissions=["change"],
    )
    def load_from_sheet(self, request, object_id):
        """Fill this guide from the community sheet's block for its event.

        Preview first, then apply. See pvp_sheet.py for what is read; this
        is an editor's action through the admin for one event, the same
        standing as typing the rows in, not a seed path.
        """
        guide = get_object_or_404(PvpGuide, pk=object_id)
        summary = None
        was_preview = True
        if request.method == "POST":
            form = pvp_sheet.LoadFromSheetForm(request.POST)
            if form.is_valid():
                was_preview = form.cleaned_data["preview"]
                try:
                    summary = pvp_sheet.load_guide_from_sheet(guide, preview=was_preview)
                except pvp_sheet.SheetError as exc:
                    form.add_error(None, str(exc))
                else:
                    if not was_preview:
                        public_payload_cache.invalidate()
                        self.message_user(
                            request,
                            f"Loaded the sheet's \"{summary['source']}\" into {guide}. "
                            "Check each tab.",
                            messages.SUCCESS,
                        )
        else:
            form = pvp_sheet.LoadFromSheetForm()
        context = {
            **self.admin_site.each_context(request),
            "title": f"Load from the sheet: {guide}",
            "opts": self.model._meta,  # pylint: disable=protected-access
            "original": guide,
            "form": form,
            "summary": summary,
            "was_preview": was_preview,
        }
        return TemplateResponse(
            request, "admin/calculatorapi/pvpguide/load_from_sheet.html", context,
        )

    # ── Extra routes ──

    def get_urls(self):
        return [
            path(
                "skill-names/",
                self.admin_site.admin_view(self.skill_names_view),
                name="calculatorapi_pvpguide_skill_names",
            ),
            *super().get_urls(),
        ]

    def skill_names_view(self, request):
        """Names for the `[[` suggestions in the text boxes: skills on global
        whose name contains the typed text, with their icons."""
        term = request.GET.get("q", "").strip()
        rows = Skill.objects.on_global()
        if term:
            rows = rows.filter(name__icontains=term)
        results = [
            {"name": skill.name, "picture": picture_url(skill), "version": skill.version_label}
            for skill in rows.order_by("name")[:12]
        ]
        return JsonResponse({"results": results})


# ── The link from an event's page ────────────────────────────────────────────

class PvpGuideLinkMixin:  # pylint: disable=too-few-public-methods
    """The "PvP guide" line on the Champions Meeting and League of Heroes
    pages: open the guide, or start one with the event already picked."""

    @admin.display(description="PvP guide")
    def pvp_guide_link(self, obj):
        if obj is None or obj.pk is None:
            return "Save the event first."
        try:
            guide = obj.pvp_guide
        except PvpGuide.DoesNotExist:
            guide = None
        if guide is not None:
            return format_html(
                '<a href="{}">Open the guide</a> ({})',
                reverse("admin:calculatorapi_pvpguide_change", args=(guide.pk,)),
                "published" if guide.is_published else "draft, staff only",
            )
        kind = "cm" if isinstance(obj, ChampionsMeeting) else "loh"
        return format_html(
            '<a href="{}?event={}-{}">Write a guide for this event</a>',
            reverse("admin:calculatorapi_pvpguide_add"), kind, obj.pk,
        )


PVP_GUIDE_FIELDSET = (
    "PvP guide", {
        "fields": ("pvp_guide_link",),
        "description": (
            "Recommended stats, umas, skills and decks for this event live in its "
            "PvP guide, written per running style."
        ),
    },
)
