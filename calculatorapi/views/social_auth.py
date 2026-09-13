"""
Sign-in endpoints for Google / Discord / Patreon.

Two steps, matching the OAuth2 authorization-code flow in calculatorapi/oauth.py:

  GET  /auth/<provider>/start  -> {"authorize_url": ..., "state": ...}
  POST /auth/social            -> {"token": ...}

The SPA sends the browser to `authorize_url`, the provider bounces it back to
the frontend's /auth/callback with a one-time `code`, and the SPA posts that
code here. The response shape {"token": ...} deliberately matches the existing
password login so the frontend stores it identically.
"""

import logging
import secrets

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.authtoken.models import Token

from calculatorapi import benefits, oauth
from calculatorapi.models import CustomUser, SocialAccount

logger = logging.getLogger(__name__)

# Namespaces the signature so a state string cannot be repurposed as, or forged
# from, any other value signed with the same SECRET_KEY.
STATE_SALT = "calculatorapi.social-auth-state"

# Generated handles look like "user_a3f9c1". Deliberately NOT derived from the
# provider's username or name -- that is exactly the personal data this whole
# feature exists to avoid holding.
USERNAME_PREFIX = "user_"
USERNAME_RANDOM_BYTES = 3
MAX_USERNAME_ATTEMPTS = 5

# One generic message for every rejection. Distinguishing "bad state" from "bad
# code" would only help someone probing the endpoint.
GENERIC_AUTH_ERROR = {"error": "Could not complete sign in. Please try again."}


def _issue_state(provider, redirect_uri):
    """Sign a short-lived state token binding this login attempt to a provider
    AND to the redirect URI it started with.

    Guards against login CSRF: without it, an attacker could pre-start a login
    with their own account and trick a victim's browser into completing it,
    silently signing the victim into the attacker's account. `signing.dumps`
    uses a TimestampSigner, so `loads(max_age=...)` enforces expiry as well as
    authenticity. The random nonce keeps two states issued in the same second
    from being identical; the frontend also stores a copy in sessionStorage and
    compares it on return, which an attacker cannot write to.
    """
    return signing.dumps(
        # "r" is the redirect URI. It rides along in the SIGNED state rather
        # than being re-sent by the client at completion time, because the token
        # exchange must repeat it byte-for-byte -- and a value the browser could
        # edit between the two halves of the flow would be worthless as a
        # binding. The signature is what makes it trustworthy on the way back.
        {"p": provider, "n": secrets.token_urlsafe(16), "r": redirect_uri},
        salt=STATE_SALT,
    )


def _load_state(state, provider):
    """Verify a returned state and hand back its payload, or None if it fails.

    Returns the payload rather than a bool so the caller can recover the
    redirect URI sealed inside it.
    """
    if not state:
        return None
    try:
        payload = signing.loads(
            state,
            salt=STATE_SALT,
            max_age=settings.OAUTH_STATE_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        # Covers SignatureExpired too (it subclasses BadSignature).
        return None
    # A state minted for Google must not be replayable against Discord.
    if payload.get("p") != provider:
        return None
    return payload


def _resolve_redirect_uri(request):
    """Pick the redirect URI for this login: (uri, error_response).

    No `redirect_uri` parameter -> the canonical one. That is what the deployed
    SPA sends and what every client sent before this parameter existed, so the
    default path is unchanged.

    A parameter -> it must appear verbatim in the server-side allowlist. THIS IS
    THE SECURITY BOUNDARY of the whole feature. Honouring an arbitrary value
    would make this endpoint an open redirector that mails authorization codes
    to whatever address the caller named, so an unlisted URI is refused outright
    rather than quietly falling back to the default (which would send the user
    somewhere they did not expect and look like a broken login).
    """
    requested = request.query_params.get("redirect_uri")
    if not requested:
        return settings.OAUTH_REDIRECT_URI, None
    if requested not in settings.OAUTH_ALLOWED_REDIRECT_URIS:
        # Deliberately does not echo the rejected value back to the caller.
        return None, Response(
            {"error": "That redirect URI is not allowed for this deployment."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return requested, None


def _create_anonymous_user():
    """Create a CustomUser carrying no personal data at all.

    Blank email/first/last name and an unusable password: there is no password
    to check because sign-in always goes through the provider. The retry loop
    handles the (vanishingly unlikely) case of two random handles colliding --
    the DB's unique constraint on username is what actually decides.
    """
    for _ in range(MAX_USERNAME_ATTEMPTS):
        username = USERNAME_PREFIX + secrets.token_hex(USERNAME_RANDOM_BYTES)
        user = CustomUser(username=username, email="", first_name="", last_name="")
        user.set_unusable_password()
        try:
            with transaction.atomic():
                user.save()
            return user
        except IntegrityError:
            continue
    raise RuntimeError("Could not generate a unique username")


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def social_auth_start(request, provider):
    """Hand the SPA the provider consent URL to redirect the browser to."""
    if not oauth.is_supported(provider):
        return Response(
            {"error": "Unknown provider"}, status=status.HTTP_404_NOT_FOUND
        )
    redirect_uri, redirect_error = _resolve_redirect_uri(request)
    if redirect_error is not None:
        return redirect_error

    state = _issue_state(provider, redirect_uri)
    try:
        authorize_url = oauth.build_authorize_url(provider, state, redirect_uri)
    except oauth.OAuthError:
        # Only reachable when the client id/secret env vars are missing, which
        # is a deployment fault rather than anything the user did.
        return Response(
            {"error": "Sign in is unavailable right now."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"authorize_url": authorize_url, "state": state})


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
def social_auth_complete(request):
    """Redeem the one-time code and return an API token for the matching user."""
    provider = request.data.get("provider")
    code = request.data.get("code")
    state = request.data.get("state")

    if not oauth.is_supported(provider):
        return Response(
            {"error": "Unknown provider"}, status=status.HTTP_404_NOT_FOUND
        )
    state_payload = _load_state(state, provider)
    if not code or state_payload is None:
        return Response(GENERIC_AUTH_ERROR, status=status.HTTP_400_BAD_REQUEST)

    # The URI this login actually started with. Not re-checked against the
    # allowlist: by this point the browser is already back, so the value is no
    # longer a redirect target -- it is only the string the provider requires us
    # to repeat. Re-checking would also break a login that was in flight while
    # the allowlist changed. A state predating this field (one issued by the
    # previous release, mid-deploy) has no "r" and correctly falls back to the
    # canonical URI, which is what it was started with.
    redirect_uri = state_payload.get("r") or settings.OAUTH_REDIRECT_URI

    try:
        identity = oauth.exchange_code(provider, code, redirect_uri)
    except oauth.OAuthError:
        # Expired/replayed code, provider outage, or a redirect_uri mismatch.
        # The CLIENT gets one generic message -- distinguishing these would only
        # help someone probing the endpoint -- but the detail must not vanish
        # entirely, or a genuinely broken provider integration is invisible in
        # production and indistinguishable from users mistyping their password.
        # OAuthError carries no token or code, only status codes and shape
        # complaints, so it is safe to log.
        logger.warning("OAuth sign-in failed for %s", provider, exc_info=True)
        return Response(GENERIC_AUTH_ERROR, status=status.HTTP_400_BAD_REQUEST)

    subject_id = identity.subject_id

    # get_or_create on (provider, subject_id) is what makes a returning user
    # resolve to their existing account -- and the DB's unique constraint is
    # what guarantees it even if two sign-ins race.
    try:
        with transaction.atomic():
            social, created = SocialAccount.objects.select_related("user").get_or_create(
                provider=provider,
                subject_id=subject_id,
                defaults={"user": _create_anonymous_user},
            )
    except IntegrityError:
        # Lost a race against a concurrent first sign-in; the row now exists.
        social = SocialAccount.objects.select_related("user").get(
            provider=provider, subject_id=subject_id
        )
        created = False

    social.last_login_at = timezone.now()
    social.save(update_fields=["last_login_at"])

    # Someone signing in with Patreon may already be a known patron. This is a
    # LOCAL lookup only — no request to Patreon — because sign-in is the hot
    # path and most people signing in are not patrons. The link endpoint, which
    # is a deliberate "give me my benefits" action, is where it is worth asking
    # Patreon directly; here a new patron simply waits for the daily sync.
    if provider == SocialAccount.PROVIDER_PATREON:
        benefits.link_supporter_to_user(social.user, subject_id)

    token, _ = Token.objects.get_or_create(user=social.user)
    return Response(
        {"token": token.key},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
