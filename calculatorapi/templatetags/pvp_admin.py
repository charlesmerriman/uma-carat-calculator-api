"""
Template tags for the PvP guide admin page (admin_pvp.py).

`pvp_tabs` turns the page's inline formsets into the tab strip: one entry per
tab (pvp_guides.TABS) with how many saved rows it holds and, after a failed
save, how many errors, so an editor sees which tab refused without opening
each one.
"""

from django import template

from calculatorapi.pvp_guides import GUIDE_TAB, TABS

register = template.Library()


def _formset_errors(formset):
    """Errors in a formset and in any formsets nested under its rows (a
    deck's cards sit under the deck)."""
    count = formset.total_error_count()
    for form in formset.forms:
        for nested in getattr(form, "nested_formsets", ()):
            count += nested.formset.total_error_count()
    return count


@register.simple_tag(takes_context=True)
def pvp_tabs(context):
    """{"items": [{key, label, count, errors}], "first_error": key or ""}."""
    counts = {key: 0 for key, _ in TABS}
    errors = dict(counts)
    for inline in context.get("inline_admin_formsets") or ():
        group = getattr(inline.opts, "tab_group", GUIDE_TAB)
        if group not in counts:
            continue
        counts[group] += inline.formset.initial_form_count()
        errors[group] += _formset_errors(inline.formset)
    adminform = context.get("adminform")
    if adminform is not None:
        errors[GUIDE_TAB] += len(adminform.form.errors)
    items = [
        {"key": key, "label": label, "count": counts[key], "errors": errors[key]}
        for key, label in TABS
    ]
    first_error = next((item["key"] for item in items if item["errors"]), "")
    return {"items": items, "first_error": first_error}
