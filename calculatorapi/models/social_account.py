from django.db import models
from .custom_user import CustomUser


class SocialAccount(models.Model):
    """Links a CustomUser to an identity held at Google, Discord or Patreon.

    This is the whole personal footprint of a non-staff account. Sign-in goes
    through the provider (see calculatorapi/oauth.py), which verifies the
    password on their side and hands us back ONE opaque number. The scopes --
    "openid" for Google, "identify" for Discord, "identity" for Patreon -- are
    the narrowest that yield an id, and oauth.py reads exactly that one value
    from each response, so no email, display name or picture is ever stored
    here by accident. (A provider picture WAS stored for one unshipped day,
    2026-09-12 to 2026-09-13; the column was dropped before it ever reached
    production, and the account picture is now a supporter perk -- see
    models/user_oshi.py.)

    A row can arrive two ways, and they are NOT the same operation:

      SIGN-IN  (views/social_auth.py) is AllowAny and CREATES an account when
               the identity is unknown.
      LINKING  (views/account_linking.py) is IsAuthenticated and must NEVER
               create one -- it attaches an identity to the account already
               signed in.

    They are separate views on purpose. Folding them into one behind a mode
    flag puts an account-creation path one branching mistake away from an
    account-takeover path.

    A Patreon row here says only "this person can sign in with Patreon". It says
    NOTHING about whether they are a paying patron -- that is PatreonSupporter's
    job, kept separate because the two have independent lifecycles: most patrons
    have no account here, and most accounts have no pledge.
    """

    PROVIDER_GOOGLE = "google"
    PROVIDER_DISCORD = "discord"
    PROVIDER_PATREON = "patreon"
    PROVIDER_CHOICES = [
        (PROVIDER_GOOGLE, "Google"),
        (PROVIDER_DISCORD, "Discord"),
        (PROVIDER_PATREON, "Patreon"),
    ]

    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        # A FK (rather than columns on CustomUser) costs nothing now and leaves
        # room to let one user link both Google and Discord later without a
        # migration. Today the sign-in view only ever creates one row per user.
        related_name="social_accounts",
    )
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    # The provider's permanent, opaque id for this person: Google's `sub` claim
    # or Discord's user `id`. Stable across sign-ins (that is what makes a
    # returning user resolve to the same account) and scoped to our app, so it
    # cannot be cross-referenced against any other service. Stored raw rather
    # than hashed: it identifies nobody without the provider's own database,
    # and hashing would strand every account if DJANGO_SECRET_KEY ever rotated.
    # 255 chars because OpenID Connect permits a `sub` up to that length, though
    # Google's are ~21 digits and Discord's snowflakes ~19.
    subject_id = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        verbose_name = "Social Account"
        verbose_name_plural = "Social Accounts"
        constraints = [
            # Enforces "same provider account -> same user" in the database
            # rather than trusting the view's get_or_create to race correctly.
            # Scoped to (provider, subject_id) because ids are only unique
            # within a provider -- Google and Discord could collide otherwise.
            models.UniqueConstraint(
                fields=["provider", "subject_id"],
                name="unique_provider_subject",
            )
        ]

    def __str__(self):
        # Deliberately renders the generated handle and provider only -- never
        # subject_id, so it cannot leak into admin logs or error messages.
        return f"{self.user.username} - {self.get_provider_display()}"
