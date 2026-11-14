from django.db import models


class SupportCardType(models.TextChoices):
    """The stat a support card trains, or friend / group for the ones that train none."""
    SPEED = "speed", "Speed"
    STAMINA = "stamina", "Stamina"
    POWER = "power", "Power"
    GUTS = "guts", "Guts"
    WIT = "wit", "Wit"
    FRIEND = "friend", "Friend"
    GROUP = "group", "Group"


class SupportCardRarity(models.IntegerChoices):
    """R / SR / SSR. The number IS the game's value, and the card id's first digit."""
    R = 1, "R"
    SR = 2, "SR"
    SSR = 3, "SSR"


def support_rarity_from_game_id(game_id):
    """The rarity a support card id encodes in its first digit, or None.

    Every support card id starts with 1, 2 or 3 for R, SR, SSR (10001, 20012,
    30024), which holds for cards that are not on global yet too. The import
    writes the game's own `rarity` for the cards it knows; this is the fallback
    that covers the rest.
    """
    if not game_id:
        return None
    first_digit = int(str(game_id)[0])
    return first_digit if first_digit in SupportCardRarity.values else None


class SupportCard(models.Model):
    name = models.CharField(max_length=255)
    game_id = models.PositiveIntegerField(
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Numeric card id from the reference game data (e.g. 30024). "
            "Anchors this card's image filename in the DO Space."
        ),
    )
    # When the card comes out on Global, by the game's own table: written by
    # the import only, from the global client's start_date (clamped to
    # Global's launch), and None for a card the client doesn't hold. It may be
    # AHEAD of today: the client ships cards before their release, and a few
    # JP-only ones dated 2099, its "not released here" mark.
    #
    # One of the two halves of "on global" (calculatorapi/global_release.py):
    # the card is out once this has passed OR a confirmed global banner for it
    # has started. Read through that module, never compared here and there.
    # It also serves the Collection's Release date sort for cards on no banner.
    #
    # NEVER SELECTOR ELIGIBILITY. A selector judges a card on its first JP
    # banner (eligibility.py), and a card on no banner staying unknown there
    # is what keeps these cards out of every selector. Filling that gap from
    # this column would offer them. It stays off /calculator-data for the same
    # reason.
    global_release_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text=(
            "When the card comes out on Global, by the game's own data (may be ahead "
            "of today). Set by the game-data import only. Selectors never read it."
        ),
    )
    image = models.ImageField(upload_to="support_cards/", blank=True, null=True)
    # The card's twin of Uma.image_borderless: the game's own card art with no
    # rarity border and no type icon (the game's texture), which the site
    # frames itself (frontend FramedCard, from Daptrius's overlays). Attached
    # to the row (matched on game_id) by `manage.py link_borderless_art
    # --kind cards`. Its own folder in the Space, so the image picker lists the
    # two sets apart.
    #
    # Unlike an uma there is no `portrait` fallback to `image`: the bordered
    # picture already carries a frame and icon, so it can't go inside the
    # site's. The API sends this field as it is (null when missing: a
    # placeholder, or a new card before its texture is uploaded), and the
    # frontend draws `image` unframed for those.
    image_borderless = models.ImageField(
        upload_to="support_cards_borderless/",
        blank=True,
        null=True,
        verbose_name="borderless image",
        help_text="The same art without the rarity border and type icon. Optional.",
    )
    admin_comments = models.TextField(blank=True, null=True, help_text="Notes for editors.")
    # Public, rendered as the Timeline tile's hover overlay -- the support-card
    # twin of Uma.purpose; see the note there for the cap and the "" default.
    # Card-level ("Great for front runners"); advice about one particular banner
    # belongs on the SupportsOnSupportBanner.recommendation junction instead.
    purpose = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Shown publicly when a player hovers this card's art on the Timeline, "
            "e.g. \"Great for front runners.\" Leave blank to show nothing. Notes "
            "for other editors go in Admin comments instead."
        ),
    )

    # Henry and Daptrius, 2026-10-23 (owner's yes 2026-10-27): a stand-in for
    # "your best SSR Speed card", for a team shared before the player knows
    # which card. Not a game card: its game_id is made up (31000 is SSR
    # Speed), so the import never touches it. The team builder treats it as
    # always out, a deck may hold it twice (views/pvp_team.py), and it counts
    # toward nothing that ranks cards.
    is_placeholder = models.BooleanField(
        default=False,
        verbose_name="placeholder",
        help_text=(
            "A stand-in for \"your best card of this type and rarity\". Always "
            "pickable in My team, and a deck may hold it twice."
        ),
    )

    # ---- Game data, filled by `manage.py import_game_data`. ----
    card_type = models.CharField(
        max_length=10,
        blank=True,
        default="",
        choices=SupportCardType.choices,
        help_text="What the card trains. Blank until imported.",
    )
    rarity = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        choices=SupportCardRarity.choices,
        help_text="R, SR or SSR. Blank only for a card with no game id.",
    )
    # The game's character id, a plain number rather than a FK to Uma: a
    # character is not an outfit, so "this character's outfits" is
    # Uma.objects.filter(game_id__range=(id * 100, id * 100 + 99)).
    character_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="The game's character id (e.g. 1006 for Oguri Cap). Not an uma row.",
    )
    title = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="The card's title from the game, e.g. \"[Get Lots of Hugs for Me]\".",
    )
    # A group card's umas, by name, in the game's order. Each one counts as
    # this card's uma in a deck, the way an ordinary card counts as its own
    # (the team builder's rules, frontend utils/pvpTeams.ts). Names rather than
    # character ids because the builder matches cards to umas by name: ~300
    # cards not on global yet have no character id to match against.
    group_members = models.JSONField(
        default=list,
        blank=True,
        help_text="The umas a group card stands for, e.g. Team Sirius's six. Empty for every other card.",
    )
    # The JP client's name and title, as on Uma. A group card's name is the
    # group's (チーム＜シリウス＞), since the JP snapshot names it that way.
    jp_name = models.CharField(
        max_length=100, blank=True, default="", verbose_name="JP name",
        help_text="The card's name in the JP client.",
    )
    jp_title = models.CharField(
        max_length=100, blank=True, default="", verbose_name="JP title",
        help_text="The card's title in the JP client.",
    )
    # UmaTL's English title (scripts/data/umatl/), written by the import for
    # every id the JP snapshot holds. Shown only while `title` is blank: Global's
    # title and an editor's both win, and the page credits UmaTL when it shows
    # (`uses_translated_title`). Never read by an AI feature (UmaTL's terms).
    translated_title = models.CharField(
        max_length=100, blank=True, default="",
        help_text="UmaTL's English title, shown only while title is blank. Import-owned.",
    )
    # The card's training effects and its unique effect, as the game's tables
    # state them (scripts/extract_master_snapshot.py, extract_support_effects):
    # written by import_game_data, read whole with the card and never edited
    # by hand. calculatorapi/support_effects.py turns them into the page's
    # table. JSON, not tables of their own: nothing asks SQL "which cards have
    # Race Bonus", and the day something does is the day to add one.
    effects = models.JSONField(
        blank=True, default=list,
        help_text='Each effect\'s value at the levels the game names: [{"type": 1, "levels": {"1": 10, "25": 15}}].',
    )
    unique_effect = models.JSONField(
        blank=True, null=True,
        help_text="The unique effect: the level that unlocks it, its typed effects, and the game's sentence for it.",
    )

    @property
    def shown_title(self):
        """The title a reader sees: Global's or an editor's, else UmaTL's."""
        return self.title or self.translated_title

    @property
    def uses_translated_title(self):
        """Does the shown title come from UmaTL (so the page credits it)?"""
        return not self.title and bool(self.translated_title)

    def save(self, *args, **kwargs):
        # A card an editor adds by hand, e.g. one that is not on global yet,
        # never meets the import, so its rarity is read off the id here.
        if self.rarity is None:
            self.rarity = support_rarity_from_game_id(self.game_id)
        super().save(*args, **kwargs)

    def __str__(self):
        # Many characters have 2-3 support cards sharing the exact same name
        # (different rarities/reprints) - appending game_id keeps admin
        # autocomplete/search results unambiguous.
        return f"{self.name} ({self.game_id})" if self.game_id else self.name
