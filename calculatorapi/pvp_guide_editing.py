"""
Who may edit a PvP guide on the site, and the writes the site's editor makes.

WHO (owner, 2026-10-21): every STAFF account, and nobody else. Through ONE
function, `can_edit`, which GET /account reads for the `guide_editor` flag
and every editor route checks before writing. Never a separate column, and
no permission path: a non-staff account holding `change_pvpguide` (the
"Content editors" group) does NOT edit on the site (the owner's call the
same day, replacing an earlier "or the permission"). Supporters writing
guides of their own is a later, separate gallery feature with its own
tables and its own rules, not a second branch here.

WHY THIS IS NOT THE ADMIN'S GATE. The admin's pages sit behind a password and
a second factor (admin_site.py). This is a content write route on the API's
token, and a token comes only from a Discord, Google or Patreon sign-in (the
provider's own second factor; nothing on the API takes a password). It is
held to three conditions, which the next content write route must meet too:
one function decides from the account's own staff flag, every write leaves a
revision with its author (pvp_guide_revisions.py), and the route touches the
eight guide tables and nothing else.
"""

from django.db import IntegrityError, transaction

from . import benefits, public_payload_cache, pvp_guide_document, pvp_guide_revisions, pvp_guides
from .models import ChampionsMeeting, LeagueOfHeroes, PvpGuide, PvpGuideRevisionSource, PvpGuideVisibility

# The URL's / body's kind -> the guide's FK to that event (pvp_teams' idiom).
EVENT_FOR_KIND = {
    "cm": (ChampionsMeeting, "champions_meeting"),
    "loh": (LeagueOfHeroes, "league_of_heroes"),
}


class GuideRefused(Exception):
    """A write the rules refuse. The message is shown to the editor as is."""


def sees_skills_preview(user):
    """
    Whether `user` sees the skills work and the Encyclopedia: GET /skills, the
    uma and card pages, JP-only skills on guides, and the readable text and
    sources the hover card draws.

    Shelved for the guide editors from 2026-10-23; since the Encyclopedia
    (owner, 2026-11-12: "supporters/staff only first, to be enabled for all
    later") it is the site's early-access rule, the one Rankings uses: any
    paid tier, staff, or an account ticked "early access". Everyone else gets
    guides exactly as before the work (global skills only). Opening it to
    everyone is this one function: return True (notes/encyclopedia-plan.md,
    "Opening it to everyone").
    """
    return benefits.early_access(user)


def can_edit(user, guide=None):  # pylint: disable=unused-argument
    """Whether `user` may edit guides on the site: an active staff account.
    `guide` is unused; it is the seam a later feature (supporters' own guides)
    would read ownership from, and the public route's draft rule passes it
    already."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return bool(user.is_staff)


def resolve_event(kind, event_id):
    """(the event, the FK name), or GuideRefused."""
    try:
        model, field = EVENT_FOR_KIND[kind]
    except KeyError as exc:
        raise GuideRefused("Pick a Champions Meeting or a League of Heroes event.") from exc
    event = model.objects.filter(pk=event_id).first()
    if event is None:
        raise GuideRefused("That event isn't on the site.")
    return event, field


def create_guide(user, *, kind, event_id, scenario_id=None, copy_from=None):
    """A new draft guide for an event with none, optionally started from
    another guide's every section. Returns the guide."""
    event, field = resolve_event(kind, event_id)
    source = None
    if copy_from is not None:
        source = PvpGuide.objects.filter(pk=copy_from).first()
        if source is None:
            raise GuideRefused("The guide to copy from isn't on the site.")
    try:
        with transaction.atomic():
            guide = PvpGuide.objects.create(**{field: event}, scenario_id=scenario_id)
            if source is not None:
                pvp_guides.copy_guide(source, guide)
    except IntegrityError as exc:
        raise GuideRefused("This event already has a guide.") from exc
    pvp_guide_revisions.record(
        guide, user, PvpGuideRevisionSource.COPY if source else PvpGuideRevisionSource.SITE,
    )
    return guide


def save_document(guide, user, data, source=PvpGuideRevisionSource.SITE):
    """Write a validated document and record it. The caller has checked the
    version and run pvp_guide_document.errors()."""
    try:
        with transaction.atomic():
            pvp_guide_document.write(guide, data)
    except IntegrityError as exc:
        # Past the checks in errors(): the constraints caught something the
        # list above does not know yet. One sentence, never a 500.
        raise GuideRefused("The guide couldn't be saved as it is. Check the rows and try again.") from exc
    pvp_guide_revisions.record(guide, user, source)


def set_visibility(guide, user, visibility, tools_public=False):
    """Who may read the guide, and whether My team and the Gallery open to
    everyone with it (owner, 2026-11-02, guide-visibility-plan.md).

    The switch is refused on anything but Public, never quietly dropped, so a
    client that sends it there learns its mistake. The one write the public
    payload cares about (has_guide, guide_visibility and guide_tools_public
    on both race arrays), so it invalidates by hand: update() raises no
    signal.

    The revision says PUBLISH when the guide ends up out of draft and
    UNPUBLISH when it goes back to Private, so "changed since it was
    published" keeps comparing against the last time it went out."""
    if visibility not in PvpGuideVisibility.values:
        raise GuideRefused("Pick Private, Supporters or Public.")
    if tools_public and visibility != PvpGuideVisibility.PUBLIC:
        raise GuideRefused("My team and the Gallery can only be opened on a Public guide.")
    is_published = visibility != PvpGuideVisibility.PRIVATE
    # is_published is TRANSITIONAL (models/pvp_guide.py): kept in step until
    # the column goes.
    PvpGuide.objects.filter(pk=guide.pk).update(
        visibility=visibility, tools_public=tools_public, is_published=is_published,
    )
    guide.visibility, guide.tools_public, guide.is_published = visibility, tools_public, is_published
    pvp_guide_revisions.record(
        guide, user,
        PvpGuideRevisionSource.PUBLISH if is_published else PvpGuideRevisionSource.UNPUBLISH,
    )
    public_payload_cache.invalidate()


def delete_guide(guide):
    """Delete a DRAFT. A guide anyone but staff can read is refused: make it
    Private first, so a live guide never vanishes on one click."""
    if guide.is_out_of_draft:
        raise GuideRefused("Make the guide private before deleting it.")
    guide.delete()


__all__ = [
    "EVENT_FOR_KIND", "GuideRefused", "can_edit", "resolve_event",
    "create_guide", "save_document", "set_visibility", "delete_guide",
]
