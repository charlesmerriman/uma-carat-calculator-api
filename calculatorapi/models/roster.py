"""
What a player owns in the game: umas (outfits) and support cards, for the
Collection page's Umas and Support cards tabs (notes/pvp-planner-plan.md, R2,
"Roster"). Not veterans: owning an outfit is not having trained her
(models/trained_uma.py).

A FACT ABOUT THE PERSON, NEVER A CHOICE: on the account, one row per uma or
card, never on a Plan or a PvpTeam.

TWO SOURCES, AND HAND EDITS WIN (owner, 2026-10-26)
---------------------------------------------------
`manual`: ticked on the Collection grid (the path for phone players).
`import`: read from the player's own umadump export, in their browser.
An import ADDS what is new and UPDATES what it wrote before; it never touches a
row the player set by hand, and it never deletes (the game never takes a card
or an uma away). Editing an imported row by hand makes it `manual`, so later
imports leave it alone (roster.py).

ALL OF IT IS KEPT, LIKE A VETERAN: an imported row keeps the game's whole object
in `game_data` (the player's own account id included; these files name no other
player), with the columns the site reads promoted beside it.

PRIVATE: content_snapshot.PRIVATE_MODELS, public_payload_cache._IRRELEVANT_MODELS.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .custom_user import CustomUser
from .support_card import SupportCard
from .trained_uma import TrainedUmaSource
from .uma import Uma

# The game's ranges: an outfit's stars and potential (talent) run 1..5, a
# card's limit break 0..4 (4 is MLB).
ROSTER_STARS_MAX = 5
ROSTER_LIMIT_BREAK_MAX = 4
# Spare copies (the game's `stock`): the owner's file holds 0 or 1; the ceiling
# only stops abuse.
ROSTER_SPARE_COPIES_MAX = 99


class OwnedUma(models.Model):
    """One outfit the player owns."""

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="owned_umas")
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="+")
    # NULL when ticked by hand without saying: owning her is the fact, stars a detail.
    stars = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(ROSTER_STARS_MAX)]
    )
    talent_level = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(ROSTER_STARS_MAX)]
    )
    source = models.CharField(max_length=6, choices=TrainedUmaSource.choices)
    game_data = models.JSONField(null=True, blank=True)
    # When the game says she joined the account (`create_time`), if imported.
    obtained_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(fields=["user", "uma"], name="one_owned_uma_per_outfit"),
            models.CheckConstraint(
                condition=Q(stars__isnull=True) | Q(stars__gte=1, stars__lte=ROSTER_STARS_MAX),
                name="owned_uma_stars_in_range",
            ),
            models.CheckConstraint(
                condition=Q(talent_level__isnull=True) | Q(talent_level__gte=1, talent_level__lte=ROSTER_STARS_MAX),
                name="owned_uma_talent_in_range",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} owns {self.uma}"


class OwnedSupportCard(models.Model):
    """One support card the player owns."""

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="owned_support_cards")
    support_card = models.ForeignKey(SupportCard, on_delete=models.CASCADE, related_name="+")
    limit_break = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(ROSTER_LIMIT_BREAK_MAX)]
    )
    # Copies held beyond the one in use (the game's `stock`), for limit breaking.
    spare_copies = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(ROSTER_SPARE_COPIES_MAX)]
    )
    source = models.CharField(max_length=6, choices=TrainedUmaSource.choices)
    game_data = models.JSONField(null=True, blank=True)
    obtained_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(fields=["user", "support_card"], name="one_owned_copy_per_card"),
            models.CheckConstraint(
                condition=Q(limit_break__lte=ROSTER_LIMIT_BREAK_MAX), name="owned_card_limit_break_in_range"
            ),
            models.CheckConstraint(
                condition=Q(spare_copies__lte=ROSTER_SPARE_COPIES_MAX), name="owned_card_spares_in_range"
            ),
        ]

    def __str__(self):
        return f"{self.user.username} owns {self.support_card}"
