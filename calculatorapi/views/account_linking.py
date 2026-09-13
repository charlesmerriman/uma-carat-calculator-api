"""
Attach and detach provider identities on an ALREADY SIGNED-IN account.

  GET    /account/link/<provider>/start     -> {"authorize_url": ..., "state": ...}
  POST   /account/link/<provider>/complete  -> the linked provider row
  DELETE /account/link/<provider>           -> 204

WHY THIS IS NOT A FLAG ON THE SIGN-IN VIEW
------------------------------------------
views/social_auth.py is AllowAny and CREATES AN ACCOUNT when it meets an
identity it does not recognise. These endpoints are IsAuthenticated and must
NEVER create one — they bind a new identity to the account already in hand.

Two opposite security postures over the same OAuth machinery. Folded into one
view behind a `mode` parameter, a single mis-evaluated branch turns "link my
Patreon" into "sign me in as whoever owns this Patreon", which is account
takeover. Separate doors, one lock mechanism underneath.

THE STATE IS BOUND TO THE USER, AND THAT IS THE WHOLE DEFENCE
-------------------------------------------------------------
A sign-in `state` proves only "this flow started here". For linking that is not
enough. Without a user binding an attacker could start a link on their own
account, keep the state, and get a victim's browser to complete it while the
victim is signed in — attaching the ATTACKER's Patreon identity to the VICTIM's
account. The attacker could then sign in as the victim forever.

So the signed payload carries `u`, the user id it was minted for, and completion
refuses a state belonging to anyone else.

The salt is also different from the sign-in one. That is not decoration: a
sign-in state carries no `u` at all, so if this view accepted sign-in salts a
state with no user binding would sail straight through the check above. Django's
signing namespaces by salt, so a state minted for one purpose cannot be verified
for the other.

ONE IDENTITY PER PROVIDER PER ACCOUNT
-------------------------------------
Linking a second Google (or Patreon) identity to an account that already has one
is refused rather than stored. The database would happily hold both — the unique
constraint is on (provider, subject_id), not (user, provider) — but "which of
your two Google logins do you want to remove?" is a question no screen here
wants to ask, and DELETE /account/link/google would become ambiguous. Unlink,
then link the other one.
"""

import logging
import secrets

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from calculatorapi import benefits, oauth, patreon_api
from calculatorapi.admin_patreon_import import apply_patreon_import
from calculatorapi.models import PatreonCredentials, SocialAccount
from calculatorapi.views.account import LinkedProviderSerializer

# The redirect-URI allowlist check is IMPORTED, not reimplemented. It is the
# security boundary that stops these endpoints mailing authorization codes to
# any address a caller names, and a second copy would be free to drift from the
# first — at which point one of the two doors is an open redirector.
from calculatorapi.views.social_auth import _resolve_redirect_uri  # pylint: disable=protected-access

# Deliberately NOT views.social_auth.STATE_SALT — see the module docstring.
LINK_STATE_SALT = "calculatorapi.account-link-state"

# One generic message for every failure that involves the provider exchange, for
# the same reason the sign-in view has one: distinguishing "bad state" from "bad
# code" only helps someone probing the endpoint.
GENERIC_LINK_ERROR = {"error": "Could not complete linking. Please try again."}

logger = logging.getLogger(__name__)


class AccountLinkThrottle(UserRateThrottle):
    """Caps how fast one ACCOUNT can start link flows.

    Scoped to the user rather than the IP because the route is authenticated.
    Each accepted call mints a state and sends someone to a provider, so this
    caps outbound work rather than guarding a public write.

    Applied to COMPLETE as well as start, because completing a Patreon link can
    trigger a full member fetch against Patreon (see
    _resolve_patreon_entitlement) — the one place a single user action reaches
    a third-party API more than once.
    """

    scope = "account_link"



def _issue_link_state(provider, redirect_uri, user):
    """Sign a state binding this attempt to a provider, a redirect URI AND a user."""
    return signing.dumps(
        {
            "p": provider,
            "n": secrets.token_urlsafe(16),
            "r": redirect_uri,
            # The binding that makes this safe. Checked on completion.
            "u": user.pk,
        },
        salt=LINK_STATE_SALT,
    )


def _load_link_state(state, provider, user):
    """Verify a returned link state, or None if it fails any check."""
    if not state:
        return None
    try:
        payload = signing.loads(
            state,
            salt=LINK_STATE_SALT,
            max_age=settings.OAUTH_STATE_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        # Covers SignatureExpired, which subclasses it.
        return None
    if payload.get("p") != provider:
        return None
    # A state minted for someone else must not complete against this account,
    # even though the caller is properly authenticated as themselves.
    if payload.get("u") != user.pk:
        return None
    return payload


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([AccountLinkThrottle])
def account_link_start(request, provider):
    """Hand the SPA a consent URL for attaching `provider` to this account."""
    if not oauth.is_supported(provider):
        return Response({"error": "Unknown provider"}, status=status.HTTP_404_NOT_FOUND)

    redirect_uri, redirect_error = _resolve_redirect_uri(request)
    if redirect_error is not None:
        return redirect_error

    state = _issue_link_state(provider, redirect_uri, request.user)
    try:
        authorize_url = oauth.build_authorize_url(provider, state, redirect_uri)
    except oauth.OAuthError:
        # Missing client id/secret — a deployment fault, not user error.
        return Response(
            {"error": "Linking is unavailable right now."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"authorize_url": authorize_url, "state": state})


def _owned_elsewhere_response(provider):
    return Response(
        {"error": f"That {provider} account is already linked to a different account."},
        status=status.HTTP_409_CONFLICT,
    )


def _attach_identity(user, provider, identity):
    """Bind a verified provider identity to `user`, or explain why not.

    Split out from the view so that view reads as the three things it does —
    validate, exchange, attach — and so every way an attach can be refused sits
    in one place next to its reasoning.

    NEVER creates a CustomUser. That is the invariant separating linking from
    sign-in, and it is one `create` call away from being violated.
    """
    subject_id = identity.subject_id
    owner = SocialAccount.objects.filter(provider=provider, subject_id=subject_id).first()
    if owner is not None:
        if owner.user_id == user.pk:
            # Already linked — a double submit, or a refreshed callback.
            # Idempotent rather than an error: nothing is wrong. The avatar is
            # still refreshed, because the provider was just consulted and
            # this is the same "follow the current picture" rule sign-in uses.
            if owner.avatar_url != identity.avatar_url:
                owner.avatar_url = identity.avatar_url
                owner.save(update_fields=["avatar_url"])
            return Response(LinkedProviderSerializer(owner).data, status=status.HTTP_200_OK)
        # Someone else owns this identity, and we do NOT move it. Reassigning
        # would let anyone who can complete a consent screen strip another
        # account of its sign-in method. Merging two accounts is a real feature
        # and a far larger one — see patreon-accounts-plan.md.
        return _owned_elsewhere_response(provider)

    if SocialAccount.objects.filter(user=user, provider=provider).exists():
        return Response(
            {"error": f"This account already has a {provider} login linked."},
            status=status.HTTP_409_CONFLICT,
        )

    try:
        with transaction.atomic():
            link = SocialAccount.objects.create(
                user=user,
                provider=provider,
                subject_id=subject_id,
                avatar_url=identity.avatar_url,
            )
    except IntegrityError:
        # Lost a race against a concurrent completion of the same identity. The
        # unique constraint is what actually decides, so answer from the row
        # that won rather than from what we read a moment ago.
        winner = SocialAccount.objects.filter(
            provider=provider, subject_id=subject_id
        ).first()
        if winner is not None and winner.user_id == user.pk:
            return Response(LinkedProviderSerializer(winner).data, status=status.HTTP_200_OK)
        return _owned_elsewhere_response(provider)

    return Response(LinkedProviderSerializer(link).data, status=status.HTTP_201_CREATED)


def _refresh_supporters_from_patreon():
    """Run the real sync now, best effort. True if it completed.

    The FOURTH caller of the one reconcile, and a thin one like the other three
    (the management command, the admin button, POST /patreon/sync): fetch rows
    with `patreon_api.fetch_members`, hand them to `apply_patreon_import`. What
    a sync MEANS still lives in one place; this only decides when to run one.

    `deactivate_missing=False`, unlike the scheduled run. Retiring lapsed
    patrons is the daily job's business — a user action should only ever be
    able to ADD what it came for.

    Every failure is swallowed. The link itself has already succeeded by the
    time this runs, so Patreon being unreachable must not turn that into an
    error response; the daily sync will pick the same patron up.
    """
    # Checked before the call rather than caught after it. With no token there
    # is nothing to attempt, and letting it fail would write a traceback into
    # the log on every link — indistinguishable from a real outage, and the
    # normal state of a dev machine that has no Patreon credentials.
    if not PatreonCredentials.load().is_configured:
        return False
    try:
        rows = patreon_api.fetch_members()
    except patreon_api.PatreonApiError:
        logger.warning("Inline Patreon refresh failed", exc_info=True)
        return False
    apply_patreon_import(rows, deactivate_missing=False)
    return True


def _resolve_patreon_entitlement(user, patreon_user_id):
    """Make a brand-new supporter a supporter NOW, not tomorrow morning.

    Two steps, cheapest first:

      1. A local match against the supporters table. This is the common case —
         the daily sync already knows about anyone who pledged before today —
         and it costs one indexed query.
      2. Only if that finds nothing: run the real sync inline. Someone who
         pledged and linked within the same minute is invisible to step 1
         through no fault of their own, and "come back tomorrow" is a poor
         answer for a person who has just paid.

    Step 2 goes through the SAME producer and the SAME reconcile as every other
    sync — `fetch_members` into `apply_patreon_import` — rather than reading
    this user's membership from their own OAuth token. Reading their token
    would need a wider Patreon scope, would arrive without the display name a
    supporter row requires, and would be a second path into entitlement that
    the one-reconcile rule exists to prevent.
    """
    if benefits.link_supporter_to_user(user, patreon_user_id) is not None:
        return
    if _refresh_supporters_from_patreon():
        # The reconcile attaches the row itself, having just met a patron whose
        # id now has a SocialAccount. Repeated here so this function's contract
        # does not depend on that staying true.
        benefits.link_supporter_to_user(user, patreon_user_id)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([AccountLinkThrottle])
def account_link_complete(request, provider):
    """Redeem the code and attach the identity to the signed-in account.

    201 when the link is made, 200 when it was already there (completing twice
    is not an error worth surfacing), 409 when the identity belongs elsewhere.

    NOTHING here creates a CustomUser. That is the invariant separating this
    view from social_auth.
    """
    if not oauth.is_supported(provider):
        return Response({"error": "Unknown provider"}, status=status.HTTP_404_NOT_FOUND)

    code = request.data.get("code")
    state_payload = _load_link_state(request.data.get("state"), provider, request.user)
    if not code or state_payload is None:
        return Response(GENERIC_LINK_ERROR, status=status.HTTP_400_BAD_REQUEST)

    # The URI the flow actually started with, recovered from the SIGNED state
    # rather than the request body — a value the browser could edit between the
    # two halves would be worthless as a binding. Same rule as sign-in.
    redirect_uri = state_payload.get("r") or settings.OAUTH_REDIRECT_URI

    try:
        identity = oauth.exchange_code(provider, code, redirect_uri)
    except oauth.OAuthError:
        # Generic to the client, detailed in the log -- see the same handler in
        # social_auth.py for why both halves are deliberate.
        logger.warning("OAuth link failed for %s", provider, exc_info=True)
        return Response(GENERIC_LINK_ERROR, status=status.HTTP_400_BAD_REQUEST)

    response = _attach_identity(request.user, provider, identity)

    # Only on a successful attach, and only for Patreon: a 409 means the
    # identity belongs to someone else, and resolving entitlement off it would
    # be reading a pledge that is not this user's.
    if provider == SocialAccount.PROVIDER_PATREON and response.status_code < 400:
        _resolve_patreon_entitlement(request.user, identity.subject_id)

    return response


@api_view(["DELETE"])
@permission_classes([permissions.IsAuthenticated])
def account_link_delete(request, provider):
    """Detach `provider` from this account. 204 on success.

    REFUSES TO REMOVE THE LAST WAY IN. An ordinary account has an unusable
    password — sign-in goes through a provider or not at all — so unlinking the
    only one would lock its owner out of their own saved plan permanently, with
    no reset flow to recover through (we hold no email to send one to, by
    design). Staff are exempt because their password genuinely works.

    Enforced here rather than by hiding the button: a client-side check is a
    suggestion, and this one has to be a rule.

    Unlinking Patreon also drops supporter entitlement, because entitlement is
    derived from the link. The SUPPORTER ROW SURVIVES with its publication
    consent and pledge date intact — they are still a patron, they have just
    taken their account off it. Identical treatment to a lapse; this table is
    not ours to delete from.
    """
    if not oauth.is_supported(provider):
        return Response({"error": "Unknown provider"}, status=status.HTTP_404_NOT_FOUND)

    link = SocialAccount.objects.filter(user=request.user, provider=provider).first()
    if link is None:
        return Response(
            {"error": f"No {provider} login is linked to this account."},
            status=status.HTTP_404_NOT_FOUND,
        )

    remaining = (
        SocialAccount.objects.filter(user=request.user).exclude(pk=link.pk).count()
    )
    if remaining == 0 and not request.user.has_usable_password():
        return Response(
            {
                "error": (
                    "That is the only way to sign in to this account. "
                    "Link another provider first."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    link.delete()
    if provider == SocialAccount.PROVIDER_PATREON:
        benefits.unlink_supporter(request.user)
    return Response(status=status.HTTP_204_NO_CONTENT)
