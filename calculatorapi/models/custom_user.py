from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from .club_rank import ClubRank
from .champions_meeting_rank import ChampionsMeetingRank
from .team_trials_rank import TeamTrialsRank
from .league_of_heroes_rank import LeagueOfHeroesRank


class CustomUser(AbstractUser):
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
    # Additional fields for the user profile
    club_rank = models.ForeignKey(
        ClubRank, on_delete=models.SET_NULL, null=True, blank=True
    )
    champions_meeting_rank = models.ForeignKey(
        ChampionsMeetingRank, on_delete=models.SET_NULL, null=True, blank=True
    )
    team_trials_rank = models.ForeignKey(
        TeamTrialsRank, on_delete=models.SET_NULL, null=True, blank=True
    )
    league_of_heroes_rank = models.ForeignKey(
        LeagueOfHeroesRank, on_delete=models.SET_NULL, null=True, blank=True
    )
    sr_shards = models.IntegerField(default=0)
    sr_crystals = models.IntegerField(default=0)
    ssr_shards = models.IntegerField(default=0)
    ssr_crystals = models.IntegerField(default=0)
    daily_carat = models.BooleanField(default=False)
    training_pass = models.BooleanField(default=False)
    # Approximates the sheet's "Misc Earnings" (gifts, team trials, careers):
    # a flat monthly carat estimate. On by default to match the source sheet,
    # which is the reference the projection is calibrated against.
    misc_earnings = models.BooleanField(default=True)
    # Monthly shop tickets: the game lets you buy 4 uma + 4 support gacha
    # tickets every month with a currency not tracked here, so when enabled the
    # projection simply credits those tickets monthly at no carat cost. On by
    # default alongside the other three projection toggles.
    monthly_shop_tickets = models.BooleanField(default=True)
    # Discounted paid pulls: a once-per-day option to spend 50 (instead of 150)
    # PAID carats on a single pull. Only usable while paid carats remain.
    discounted_paid_pulls = models.BooleanField(default=True)
    # Full-price paid pulls: whether paid carats may be spent normally (150 per
    # pull) on banners. On by default so paid carats keep counting toward pulls,
    # matching the historical behavior of a single merged carat pool.
    full_price_paid_pulls = models.BooleanField(default=True)
    # Include campaign purchases in the projection: when off (the default), a
    # user's planned pack/selector purchases are budgeting-only and change no
    # estimate anywhere. Off by default so enabling the feature never silently
    # moves anyone's existing numbers.
    include_purchases_in_projection = models.BooleanField(default=False)
    # Webstore bonus: the webstore sells the same packs with extra carats. The
    # rate is per-pack (see AnniversaryEventProduct.webstore_multiplier); this
    # only says whether to apply it. The pack's own carats are PAID; the bonus on
    # top is granted as FREE carats, so it never enlarges the paid balance that
    # funds step-ups. The split itself is computed client-side —
    # frontend/src/utils/campaignPurchases.ts.
    webstore_bonus = models.BooleanField(default=False)
    current_carat = models.IntegerField(default=0)
    current_paid_carat = models.IntegerField(default=0)
    uma_ticket = models.IntegerField(default=0)
    support_ticket = models.IntegerField(default=0)
    # Selector tickets are NOT gacha tickets. uma_ticket/support_ticket above are
    # each worth one pull and are spent by the pull strategy; a selector instead
    # takes a specific card outright and never funds a pull. Keeping them in
    # separate fields is what stops the projection inflating max pulls.
    #
    # These two are the user's CURRENT holdings, treated as unrestricted (no JP
    # cutoff). Tickets projected from campaigns carry their campaign's cutoff.
    uma_selector_ticket = models.IntegerField(default=0)
    support_selector_ticket = models.IntegerField(default=0)

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
