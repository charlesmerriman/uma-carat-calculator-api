"""
OAuth2 authorization-code flow for Google, Discord and Patreon sign-in.

Ordinary users never give us a password. The provider verifies who they are and
hands back one opaque, provider-scoped id (Google's `sub`, Discord's user `id`,
Patreon's user `id`) and, since 2026-09-12, the URL of their profile picture.
Those two values are ALL that is persisted -- see models/social_account.py.

The flow, end to end:

1. `build_authorize_url()` produces the provider's consent-screen URL. The SPA
   sends the browser there.
2. The provider bounces the browser back to OAUTH_REDIRECT_URI with a one-time
   `code`. That code is worthless on its own: it is single-use, expires in
   seconds, and cannot be redeemed without our client secret.
3. `exchange_code()` redeems it server-to-server (no browser involved, secret
   never leaves this process) and returns an `Identity`.

This module is pure provider logic -- no Django views, no ORM -- mirroring the
split used by predictions.py and analytics.py. Everything that can go wrong
raises OAuthError so the view can collapse the lot into one generic 400 rather
than leaking provider internals to the client.

PRIVACY NOTE: the scopes below are the narrowest that yield a subject id AND an
avatar, and each extractor reads exactly those two values from what comes back:

  Google   "openid profile"   "profile" also puts name, given/family name and
                              locale in the id_token; only `sub` and `picture`
                              are read. "openid" alone carries no picture, and
                              that is the one reason "profile" is requested.
  Discord  "identify"         id/username/avatar hash/etc.; only `id` and
                              `avatar` are read. Unchanged since sign-in shipped.
  Patreon  "identity"         a JSON:API user resource; the sparse fieldset asks
                              for `thumb_url` and nothing else, so the response
                              carries the id, the avatar, and no other attribute.

None of them sends an email address, so none can be stored here by accident.
The avatar is the ONE profile attribute this project holds -- the account owner
asked to see it in their own navbar (2026-09-12) -- and everything else in each
response is dropped before it leaves this module. Do not widen these scopes or
the extractors without changing the privacy policy first: it promises this
exact list.

In particular: Patreon puts the email behind a SEPARATE "identity[email]" scope.
Never request it. Adding it would put an address in the response for every
person who signs in, which is the exact thing this design exists to avoid.
"""

import base64
import binascii
import json
import re
import time
from typing import NamedTuple
from urllib.parse import urlencode

import requests
from django.conf import settings

# Providers can be slow, but a hung request would tie up a gunicorn worker.
HTTP_TIMEOUT_SECONDS = 10

GOOGLE = "google"
DISCORD = "discord"
PATREON = "patreon"

# Matches SocialAccount.avatar_url's max_length. A provider URL is well under
# this; the cap exists so an unexpected payload cannot fail the row save.
AVATAR_URL_MAX_LENGTH = 500

DISCORD_CDN = "https://cdn.discordapp.com"
# Discord avatar hashes are hex, with an "a_" prefix for animated ones. The
# hash is interpolated into a URL, so anything else is refused rather than
# passed through.
_DISCORD_AVATAR_HASH = re.compile(r"(a_)?[0-9a-fA-F]+")

# The ONLY user attribute the Patreon identity call asks for. See
# _patreon_identity for why the fieldset can be neither absent nor empty.
PATREON_USER_FIELDS = "thumb_url"


class OAuthError(Exception):
    """Any failure during the OAuth exchange (network, provider, or malformed
    response). Deliberately carries no provider detail toward the client."""


class Identity(NamedTuple):
    """What exchange_code() hands back about the person who just consented.

    A NamedTuple rather than a bare tuple so a call site cannot swap the two
    fields, and rather than a dict so it cannot quietly grow a third: adding
    one means editing this class, which is the review moment the privacy
    boundary needs. Everything else a provider returns dies in the extractor.
    """

    # The provider's permanent, opaque id for this person.
    subject_id: str
    # An https URL on the provider's own CDN, or "" when they have no picture
    # (or the provider sent something that is not a usable URL).
    avatar_url: str


def _google_config():
    return {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # "openid" yields `sub`. "profile" is what makes Google add `picture`
        # to the id_token -- it also adds the name and locale, which
        # _google_identity reads past. Still no "email": that is a third,
        # separate scope, and asking for it would hand us an address for every
        # person who signs in.
        "scope": "openid profile",
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "extra_authorize_params": {},
    }


def _discord_config():
    return {
        "authorize_url": "https://discord.com/oauth2/authorize",
        "token_url": "https://discord.com/api/oauth2/token",
        # "identify" returns id/username/avatar but NOT email (that would need
        # the separate "email" scope). We read `id` and the avatar hash.
        "scope": "identify",
        "client_id": settings.DISCORD_OAUTH_CLIENT_ID,
        "client_secret": settings.DISCORD_OAUTH_CLIENT_SECRET,
        "extra_authorize_params": {},
    }


def _patreon_config():
    return {
        "authorize_url": "https://www.patreon.com/oauth2/authorize",
        "token_url": "https://www.patreon.com/api/oauth2/token",
        # "identity" returns the user resource WITHOUT the email -- that lives
        # behind "identity[email]", which we must never ask for. Entitlement is
        # deliberately NOT read from this token (no "identity.memberships"):
        # the creator-side sync is the one path into it. See
        # views/account_linking.py.
        "scope": "identity",
        "client_id": settings.PATREON_OAUTH_CLIENT_ID,
        # NOT settings.PATREON_CLIENT_SECRET -- that is the creator app used by
        # patreon_api.py for the member sync. See the settings comment.
        "client_secret": settings.PATREON_OAUTH_CLIENT_SECRET,
        "extra_authorize_params": {},
    }


# Built per call rather than at import time so @override_settings works in tests.
_PROVIDER_BUILDERS = {
    GOOGLE: _google_config,
    DISCORD: _discord_config,
    PATREON: _patreon_config,
}

SUPPORTED_PROVIDERS = tuple(_PROVIDER_BUILDERS)


def is_supported(provider):
    """True if `provider` is one we can sign in with. Lets the view 404 an
    unknown provider before any work happens."""
    return provider in _PROVIDER_BUILDERS


def get_config(provider):
    if not is_supported(provider):
        raise OAuthError(f"Unsupported provider: {provider}")
    config = _PROVIDER_BUILDERS[provider]()
    if not config["client_id"] or not config["client_secret"]:
        # Misconfiguration, not user error -- surfaced as a 500 by the view so a
        # missing env var in production is loud rather than looking like a
        # rejected login.
        raise OAuthError(f"{provider} OAuth credentials are not configured")
    return config


def build_authorize_url(provider, state, redirect_uri=None):
    """The provider's consent-screen URL to send the browser to.

    `redirect_uri` overrides the canonical OAUTH_REDIRECT_URI. The caller is
    responsible for having validated it against settings.OAUTH_ALLOWED_REDIRECT_URIS
    FIRST -- this function does no checking, because an unvalidated value here
    would leak authorization codes to any address a client cared to name.
    Whatever is used must be repeated verbatim in exchange_code().
    """
    config = get_config(provider)
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri or settings.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
        **config["extra_authorize_params"],
    }
    return f"{config['authorize_url']}?{urlencode(params)}"


def _post_token_request(config, code, redirect_uri=None):
    """Redeem the one-time code for provider tokens. Server-to-server: this is
    the only place the client secret is used, and it never touches the browser."""
    payload = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        # Must match the value sent to the authorize endpoint exactly; providers
        # treat this as part of the code's binding, not as a redirect target.
        # That is why the view carries the chosen URI through the signed state
        # rather than recomputing it here -- a login started against one address
        # must finish against the same one, even if the default has changed.
        "redirect_uri": redirect_uri or settings.OAUTH_REDIRECT_URI,
    }
    try:
        response = requests.post(
            config["token_url"],
            data=payload,
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise OAuthError("Token request failed") from exc

    if response.status_code != 200:
        # Usually an expired/replayed code or a redirect_uri mismatch. The body
        # can echo request details, so it is not forwarded to the client.
        raise OAuthError(f"Token endpoint returned {response.status_code}")

    try:
        return response.json()
    except ValueError as exc:
        raise OAuthError("Token endpoint returned malformed JSON") from exc


def _decode_jwt_payload(token):
    """Decode a JWT's payload WITHOUT verifying its signature.

    That is normally a serious mistake, but it is the approach Google documents
    for this specific flow: the token did not arrive via the browser, it came
    straight back from Google's token endpoint over TLS in exchange for our
    client secret. There is no untrusted middleman whose tampering a signature
    check would catch -- anyone able to forge this response could already
    intercept TLS. The caller still validates iss/aud/exp below, which is what
    actually catches a misconfigured client id.

    If this ever starts accepting an id_token from any other source (e.g. one
    posted by the frontend), this MUST become a verifying parse instead.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise OAuthError("Malformed id_token")
    try:
        # JWTs use base64url without padding; b64decode needs it, and an
        # over-long pad is ignored, so always appending "==" is safe.
        decoded = base64.urlsafe_b64decode(parts[1] + "==")
        return json.loads(decoded)
    except (ValueError, binascii.Error) as exc:
        raise OAuthError("Could not decode id_token payload") from exc


def _clean_avatar_url(value):
    """A provider-supplied avatar URL, or "" if it is not one we will store.

    The value ends up in an <img src> on the account owner's own page and in a
    500-character column, so it must be an https URL of sane length. A missing
    picture is the normal case for a fresh Discord account and is not an error:
    "" simply means "no picture", and the client draws its own fallback.
    """
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value.lower().startswith("https://") or len(value) > AVATAR_URL_MAX_LENGTH:
        return ""
    return value


def _google_identity(config, token_data):
    id_token = token_data.get("id_token")
    if not id_token:
        raise OAuthError("Google response contained no id_token")

    claims = _decode_jwt_payload(id_token)

    # Cheap sanity checks that catch a misconfigured client id or a token minted
    # for a different app entirely.
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise OAuthError("Unexpected id_token issuer")
    if claims.get("aud") != config["client_id"]:
        raise OAuthError("id_token was not issued for this client")
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int) or expires_at <= time.time():
        raise OAuthError("id_token is expired or has no expiry")

    subject_id = claims.get("sub")
    if not subject_id:
        raise OAuthError("id_token contained no subject")

    # `picture` is here because of the "profile" scope, which also puts
    # `name`, `given_name`, `family_name` and `locale` into these claims. None
    # of those is read, logged or returned -- `claims` goes out of scope on
    # this line, and Identity has no field to carry them in.
    return Identity(str(subject_id), _clean_avatar_url(claims.get("picture")))


def _discord_avatar_url(user_id, avatar_hash):
    """Discord sends an avatar HASH, not a URL; the CDN path is documented and
    stable. A null hash means no custom avatar. Discord's default silhouettes
    are deliberately NOT substituted for it -- "" lets the client draw its own
    fallback, which reads as "no picture" rather than as a Discord logo."""
    if not isinstance(avatar_hash, str) or not _DISCORD_AVATAR_HASH.fullmatch(avatar_hash):
        return ""
    # size=128 is the smallest power of two comfortably above a 2x-density
    # navbar avatar. An animated hash ("a_" prefix) still serves a static PNG.
    return f"{DISCORD_CDN}/avatars/{user_id}/{avatar_hash}.png?size=128"


def _discord_identity(_config, token_data):
    access_token = token_data.get("access_token")
    if not access_token:
        raise OAuthError("Discord response contained no access_token")

    try:
        response = requests.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise OAuthError("Discord profile request failed") from exc

    if response.status_code != 200:
        raise OAuthError(f"Discord profile endpoint returned {response.status_code}")

    try:
        profile = response.json()
    except ValueError as exc:
        raise OAuthError("Discord profile endpoint returned malformed JSON") from exc

    subject_id = profile.get("id")
    if not subject_id:
        raise OAuthError("Discord profile contained no id")
    subject_id = str(subject_id)
    # Everything else in `profile` (username, global_name, banner, ...) is
    # intentionally dropped here and never stored or logged.
    return Identity(subject_id, _discord_avatar_url(subject_id, profile.get("avatar")))


def _patreon_identity(_config, token_data):
    """The caller's Patreon user id and avatar, from the JSON:API identity resource.

    The shape is Patreon's documented one -- {"data": {"type": "user",
    "id": "...", "attributes": {...}}} -- and was confirmed against the live API
    by a real sign-in on 2026-09-12, after the fieldset fix below.

    THE FIELDSET IS NOT OPTIONAL AND MUST NOT BE EMPTY. It was `fields[user]=`
    (empty) from 2026-09-09 to 2026-09-10, and Patreon answered every sign-in
    with a flat HTTP 400 -- the same parameter took the supporter sync down at
    the same time, since patreon_api.py asks for the sideloaded user the same
    way. A JSON:API sparse fieldset has to name at least one attribute. Until
    2026-09-12 it named a throwaway boolean (`hide_pledges`); now it names
    `thumb_url`, the avatar, which is the one attribute this project actually
    wants. Dropping the parameter is the other wrong fix -- with no fieldset at
    all Patreon sends its DEFAULT user attributes (full name, vanity URL, social
    handles), which is personal data we have no use for and no wish to receive.

    The extractor reads `data.id` and `data.attributes.thumb_url` and drops the
    rest, the same discipline _discord_identity applies to the username.
    """
    access_token = token_data.get("access_token")
    if not access_token:
        raise OAuthError("Patreon response contained no access_token")

    try:
        response = requests.get(
            "https://www.patreon.com/api/oauth2/v2/identity",
            headers={"Authorization": f"Bearer {access_token}"},
            # Exactly one attribute. NOT "" (Patreon 400s) and NOT absent
            # (Patreon sends the whole default profile). See the docstring.
            params={"fields[user]": PATREON_USER_FIELDS},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise OAuthError("Patreon identity request failed") from exc

    if response.status_code != 200:
        raise OAuthError(f"Patreon identity endpoint returned {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise OAuthError("Patreon identity endpoint returned malformed JSON") from exc

    data = payload.get("data")
    # A JSON:API resource object, not a list -- /identity describes exactly one
    # user (the token's owner). Anything else means the shape changed, and
    # guessing at it would be how a wrong id gets bound to an account.
    if not isinstance(data, dict):
        raise OAuthError("Patreon identity response had no user resource")

    subject_id = data.get("id")
    if not subject_id:
        raise OAuthError("Patreon identity resource contained no id")

    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}
    return Identity(str(subject_id), _clean_avatar_url(attributes.get("thumb_url")))


_IDENTITY_EXTRACTORS = {
    GOOGLE: _google_identity,
    DISCORD: _discord_identity,
    PATREON: _patreon_identity,
}


def exchange_code(provider, code, redirect_uri=None):
    """Redeem a one-time authorization code and return the person's Identity:
    the provider's opaque subject id and their avatar URL ("" if none).
    Raises OAuthError on every failure path.

    `redirect_uri` must be the SAME value build_authorize_url() was given, or
    the provider rejects the exchange.
    """
    config = get_config(provider)
    token_data = _post_token_request(config, code, redirect_uri)
    return _IDENTITY_EXTRACTORS[provider](config, token_data)
