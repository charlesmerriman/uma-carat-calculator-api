from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from .game_stats import GameStats


class CustomUser(GameStats, AbstractUser):
    # ACCOUNT PREFERENCE -- the one thing a person can choose about how their
    # account looks to THEMSELVES. Set through PATCH /account and served only by
    # GET /account (views/account.py); it never reaches any public route. Not
    # provider data: it does not touch the OAuth scopes or oauth.Identity.
    #
    # The picture is NOT here. Supporters pick "oshis" (models/user_oshi.py)
    # and the first one is their picture; free accounts have none.
    #
    # display_name sits BESIDE the generated `user_xxxxxx` handle, never in
    # place of it. The handle is the row's identity in the admin and in every
    # __str__, and nothing can change it; the name is what the person wants to
    # be called. UNIQUE, case-insensitively, among non-blank names (the
    # constraint in Meta): display names will be visible to other users through
    # future features (decided 2026-09-13), so nobody may take a name someone
    # else already goes by. It IS personal data in a way the handle is not, so
    # purge_user_pii blanks it. The serializer also refuses a name that matches
    # any account's HANDLE, so a chosen "user_b7e2d0" cannot impersonate one.
    display_name = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text=(
            "A name the person chose for themselves. Blank means they use the "
            "handle. Unique among non-blank names, ignoring case."
        ),
    )
    # The 25 stat fields (ranks, income toggles, balances) come from GameStats.
    # They are THIS ACCOUNT'S numbers. A plan can read an IncomeProfile's set
    # instead (models/income_profile.py); plans.stats_target() decides which.

    # No Meta needed: AbstractUser already sets verbose_name "user" / "users".

    class Meta(AbstractUser.Meta):
        constraints = [
            # Two people may not go by the same name, whatever the case. Partial
            # over non-blank names because "" is the default and most rows hold
            # it. Lower() rather than a collation so SQLite (dev) and PostgreSQL
            # (prod) agree. The serializer checks first for a friendly 400; this
            # is the backstop against a race.
            models.UniqueConstraint(
                Lower("display_name"),
                name="unique_display_name_ci",
                condition=~Q(display_name=""),
            ),
        ]

    def __str__(self):
        return self.username
