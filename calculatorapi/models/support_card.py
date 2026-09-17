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
    image = models.ImageField(upload_to="support_cards/", blank=True, null=True)
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
