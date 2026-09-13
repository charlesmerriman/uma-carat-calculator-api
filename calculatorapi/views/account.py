"""
GET    /account — who the caller is, and what they are entitled to.
DELETE /account — remove the caller's account and everything that is theirs.

WHY THIS IS ITS OWN ROUTE
-------------------------
Until now the SPA decided "is someone signed in?" by asking whether a token
string was sitting in localStorage. That answers a question about the BROWSER,
not about the account: it cannot say who the user is, and it cannot say whether
they are entitled to anything. Every feature that needs a real answer — a
supporter badge in the navbar, suppressing ads for supporters, gating a
supporter-only feature — needs one place to ask, on every route.

It is deliberately NOT a key on /calculator-data, for two reasons:

  * That response is already the largest the API serves, and the answer is
    needed on the home page, the FAQ and the changelog — none of which fetch
    calculator data at all.
  * Everything in it but four user-scoped keys is served out of a shared
    process-wide cache (see public_payload_cache.py). Entitlement must never
    be answerable from a cache keyed on anything but the requesting user.

Same reasoning that gives GET /supporters its own route rather than a corner of
the calculator payload.

WHAT IT DELIBERATELY DOES NOT RETURN
------------------------------------
No `subject_id`, for any provider. That is the opaque per-provider id the whole
sign-in design exists to avoid spreading around (see models/social_account.py),
and being behind IsAuthenticated is not on its own what keeps it off the wire —
the serializer's EXPLICIT FIELD LIST is. That list plays exactly the role
PatreonSupporterSerializer's does for the supporter email: it is the one thing
standing between a private column and a response body. Add a field to it only
with a reason that survives being written down.

The avatar IS returned, and only here. `avatar_url` is the one profile attribute
the account holds (models/social_account.py), it belongs to the caller, and this
endpoint answers only to the caller. It appears twice: per linked provider, so
the account page can show which picture came from where, and once at the top
level as THE avatar for the navbar — the one from the provider most recently
signed in with, so it follows the login the person actually uses. Nothing
public (GET /supporters, the thank-you list) ever carries an avatar.

SUPPORTER STATUS IS DERIVED ON EVERY REQUEST
--------------------------------------------
`supporter` is computed from the linked PatreonSupporter row each time, never
read from a flag on the account — see calculatorapi/benefits.py for why that
matters more than it looks like it should.

`benefits` is a list of keys rather than a set of booleans so the client never
has to reimplement the tier arithmetic: whether "ad_free" needs any paid tier or
a specific one is decided in one place on the server, and adding a benefit later
does not change the response SHAPE, only its contents.

See patreon-accounts-plan.md, Phase 2.

DELETING AN ACCOUNT
-------------------
Self-serve, on the same route, because an account that holds no email has no
other way to ask. See _delete_account for exactly what goes and what stays —
the short version is that everything the PERSON entered goes with them, and
the two rows that are about someone else's records (their feedback, their
Patreon pledge) lose only their pointer to the account.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from calculatorapi import benefits
from calculatorapi.models import SocialAccount


class LinkedProviderSerializer(serializers.ModelSerializer):
    """One linked identity, reduced to what a UI needs to draw a row.

    `provider` is the raw stored value ("google", "discord"), not its display
    label: the client keys off it to decide what is already linked, and how to
    spell it for a human is a presentation decision that belongs in the SPA.
    """

    # created_at is a DateTimeField, and DRF's DateField REFUSES to coerce a
    # datetime rather than silently dropping a timezone — so the conversion is
    # explicit here. `localdate` resolves against settings.TIME_ZONE, which is
    # UTC for this project, matching every other date the API emits.
    #
    # A date rather than a timestamp because the exact minute someone linked an
    # account is a detail about a person that no screen needs — the same
    # reasoning that keeps PatreonSupporter.patron_since a DateField.
    linked_at = serializers.SerializerMethodField()

    class Meta:
        model = SocialAccount
        # THE PRIVACY BOUNDARY — read the module docstring before adding to it.
        # `subject_id` and the internal row id are absent deliberately;
        # `avatar_url` is present deliberately (see the docstring).
        fields = ["provider", "linked_at", "avatar_url"]

    def get_linked_at(self, obj):
        return timezone.localdate(obj.created_at)


def _current_avatar_url(linked):
    """The avatar to show in the navbar, or None.

    The picture from the provider the person most recently SIGNED IN with, so
    the avatar follows the login they actually use: someone who joined with
    Google and now always signs in with Discord sees their Discord picture. A
    provider they only ever linked (never signed in through) has no
    last_login_at, so its creation time stands in. Providers with no picture
    are skipped rather than winning the tie with an empty string.
    """
    with_avatar = [row for row in linked if row.avatar_url]
    if not with_avatar:
        return None
    newest = max(with_avatar, key=lambda row: row.last_login_at or row.created_at)
    return newest.avatar_url


def _supporter_block(user):
    """The caller's Patreon entitlement.

    When there is no entitlement the block carries `is_supporter` and NOTHING
    ELSE — no null tier fields, no empty benefits list. A null tier name sitting
    next to `is_supporter: false` is an invitation for a client to render an
    empty badge or read the absence of a tier as a tier.

    NOTE WHAT IS ABSENT even when they ARE a supporter: no display name, no
    email, no `patreon_user_id`, no supporter row id. The account owner has no
    use for any of it, and it is the same field-list discipline the linked
    providers are under above.
    """
    supporter = benefits.entitled_supporter(user)
    if supporter is None:
        return {"is_supporter": False}
    return {
        "is_supporter": True,
        # The tier NAME, which is what a badge shows. Not `order` — that is an
        # internal display rank the client has no business doing maths on; the
        # server has already done that maths to produce `benefits`.
        "tier": supporter.tier.name,
        "benefits": benefits.benefit_keys_for(supporter),
    }


def _delete_account(user):
    """Delete `user` and everything that is theirs. 204, or 403 for staff.

    WHAT GOES: the CustomUser row and, by cascade, its API token, its
    SocialAccount rows (avatar URLs included) and every planned banner,
    purchase and step-up selection. Signing in again with the same provider
    creates a fresh, empty account — the (provider, subject_id) pair is gone,
    so nothing resolves back to this one. There is no undo and no recovery
    path: we hold no email to send anything to, by design, which is why the
    account page makes the person type a confirmation before it sends this.

    WHAT STAYS, with its pointer cleared:
      * Feedback they sent (user FK is SET_NULL). The report is still useful
        to the site and identifies nobody on its own.
      * Their PatreonSupporter row (linked_user is SET_NULL). It is a fact
        about a pledge, not about this account: it keeps its publication
        consent and pledge date exactly as it does on an unlink, a lapse or a
        purge. That table is not ours to delete from.
    Both are the model's on_delete rules doing the work, so this function
    cannot drift from them; the tests pin the outcome rather than the code.

    STAFF ARE REFUSED. Their accounts carry a real password and admin
    permissions and are managed in the admin, where a deletion is deliberate
    and logged. A route reachable with a bearer token should not be able to
    remove an administrator.
    """
    if user.is_staff:
        return Response(
            {"error": "Staff accounts are managed in the admin."},
            status=status.HTTP_403_FORBIDDEN,
        )
    with transaction.atomic():
        user.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET", "DELETE"])
@permission_classes([permissions.IsAuthenticated])
def account_detail(request):
    """The signed-in user's account summary, or — on DELETE — its removal.

    401 for anonymous callers, which is what makes this usable as the client's
    source of truth: a token that has expired or been revoked server-side gets a
    401 here, so the SPA finds out and can drop it, rather than trusting a
    string in localStorage that stopped meaning anything.

    Staff are not a special case. They sign in with a password and so hold no
    SocialAccount rows at all, which simply makes `linked_providers` empty —
    a correct answer, not an error, and worth a test so it stays that way.
    """
    if request.method == "DELETE":
        return _delete_account(request.user)

    user = request.user

    # Evaluated once: the serializer and _current_avatar_url both walk it.
    linked = list(SocialAccount.objects.filter(user=user).order_by("created_at"))

    return Response(
        {
            # The generated handle ("user_a3f9c1"), never a real name — social
            # sign-in deliberately never learns one. Included so the account
            # page has something to show and two accounts can be told apart.
            "username": user.username,
            # null, not "", when there is no picture: the client draws its own
            # fallback on null and would try to load "" as an image.
            "avatar_url": _current_avatar_url(linked),
            "linked_providers": LinkedProviderSerializer(linked, many=True).data,
            "supporter": _supporter_block(user),
        }
    )
