from django.db import models

from .club_rank import ClubRank
from .champions_meeting_rank import ChampionsMeetingRank
from .team_trials_rank import TeamTrialsRank
from .league_of_heroes_rank import LeagueOfHeroesRank


class GameStats(models.Model):
    """The stats block: everything the projection needs to know about ONE game
    account. Ranks, the income toggles, and the current balances.

    ABSTRACT, AND INHERITED TWICE
    -----------------------------
    CustomUser carries these columns for the person's own account, as it always
    has. IncomeProfile (models/income_profile.py) carries the same columns for a
    plan that reads its own numbers instead, which is how one person plans for
    several game accounts. Defining the fields once here is what keeps the two
    from drifting apart: a field added to this class exists on both, the
    shared serializer base (views/user.py GameStatsSerializer) lists it once,
    and plans.attach_income_profile() copies it without being told.

    Moving the fields off CustomUser into this base changed NO column: the
    definitions are the originals, verbatim. `makemigrations --check` must stay
    quiet for CustomUser after any edit here; if it does not, the definition
    drifted, and the fix is the definition, never a migration on the user table.

    Nothing here is personal data: purge_user_pii leaves it alone on both models.
    """

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
    # How many of each the player actually buys from the shop each month, for
    # someone who buys fewer or more than the assumed amount. NULL means "the
    # default", which is why these are nullable instead of defaulting to 4: the
    # default is an admin-editable constant
    # (CalculationConstants.monthly_shop_*_tickets), and a stored 4 would stop
    # following it the day an editor changes it. The ceiling is its sibling
    # constant, monthly_shop_*_tickets_max.
    #
    # Deliberately NOT validated against that ceiling here. The client clamps
    # to it when it reads these (frontend shopTicketsPerMonth), so a count
    # stored before an editor lowers the cap keeps saving instead of 400ing a
    # PATCH that carries the person's whole stats block with it.
    # They only apply while monthly_shop_tickets above is on.
    shop_uma_tickets_bought = models.PositiveSmallIntegerField(null=True, blank=True)
    shop_support_tickets_bought = models.PositiveSmallIntegerField(null=True, blank=True)
    # Spend gacha tickets on planned banners: when off, tickets are never used
    # to pay for a planned pull. They keep accumulating as a reserve (for
    # banners outside the plan) and drop out of "Max Pulls". On by default,
    # which is the behavior every plan had before this toggle existed.
    spend_tickets_on_banners = models.BooleanField(default=True)
    # Discounted paid pulls: a once-per-day option to spend 50 (instead of 150)
    # PAID carats on a single pull. Only usable while paid carats remain.
    discounted_paid_pulls = models.BooleanField(default=True)
    # Full-price paid pulls: whether paid carats may be spent normally (150 per
    # pull) on banners. On by default so paid carats keep counting toward pulls,
    # matching the historical behavior of a single merged carat pool.
    full_price_paid_pulls = models.BooleanField(default=True)
    # Daily pulls on the banners OUTSIDE the plan, for a player who takes the
    # 50-carat daily pull on every banner whether or not they want its cards.
    # Each banner the plan has no counted row on (an excluded row counts as
    # none) spends 50 PAID carats a day from today to the end of the plan's
    # farthest banner, while paid carats last. One switch per banner kind,
    # because every banner has its own daily pull. They never touch the rows
    # the plan does have: those follow discounted_paid_pulls above. Off by
    # default, so nobody's projection moves until they opt in.
    daily_pulls_other_uma_banners = models.BooleanField(default=False)
    daily_pulls_other_support_banners = models.BooleanField(default=False)
    # Include campaign purchases in the projection: when off (the default), a
    # user's planned pack/selector purchases are budgeting-only and change no
    # estimate anywhere. Off by default so enabling the feature never silently
    # moves anyone's existing numbers.
    include_purchases_in_projection = models.BooleanField(default=False)
    # Webstore bonus: the webstore sells the same packs with extra carats. The
    # rate is per-pack (see AnniversaryEventProduct.webstore_multiplier); this
    # only says whether to apply it. The pack's own carats are PAID; the bonus on
    # top is granted as FREE carats, so it never enlarges the paid balance that
    # funds step-ups. The split itself is computed client-side:
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
    # These two are the account's CURRENT holdings, treated as unrestricted (no
    # JP cutoff). Tickets projected from campaigns carry their campaign's cutoff.
    uma_selector_ticket = models.IntegerField(default=0)
    support_selector_ticket = models.IntegerField(default=0)
    # Race events the player will NOT take part in, so the projection credits
    # nothing for them: {"champions_meeting": [ids], "league_of_heroes": [ids]},
    # keyed by the income ledger's row kinds (ledger.KIND_*), ids being the
    # events' pks. Empty (the default) means "taking part in everything", which
    # is how every plan projected before this existed.
    #
    # On the stats block, not the plan, because it is a fact about the game
    # account's income, the same kind of fact as champions_meeting_rank: an alt
    # account on its own IncomeProfile skips its own events.
    #
    # JSON rather than a table of (owner, event) rows: as a column it rides
    # everything the stats block already has (stats_target, the profile copy,
    # the guest store, guest migration, auto-save) instead of needing its own
    # reconcile. The cost is no FK: an event an editor deletes leaves a dead id,
    # which the client ignores because no ledger row carries it. The shape is
    # checked by GameStatsSerializer.validate_skipped_race_events.
    skipped_race_events = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    @classmethod
    def field_names(cls):
        """The stat column names, for copying one stats block into another
        (plans.attach_income_profile). Concrete fields only: the two rows'
        ids, timestamps and owners are not stats."""
        return [f.name for f in GameStats._meta.get_fields() if f.concrete]
