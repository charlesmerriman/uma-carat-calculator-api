from django.db import models

from .custom_user import CustomUser
from .uma import Uma

# The most oshis any account can hold: what the top Patreon tier grants.
# benefits.OSHI_SLOT_LADDER's top rung must equal this (benefits.py asserts
# it at import), and PATCH /account refuses a longer list outright, so the
# `position` column below never exceeds OSHI_SLOT_CAP - 1.
OSHI_SLOT_CAP = 5


class UserOshi(models.Model):
    """One of the umas a Patreon supporter picked as their "oshi" (favourite).

    WHAT IT IS FOR. Supporters get a small ordered list of favourite umas, sized
    by tier (1, 3 or 5 -- see benefits.OSHI_SLOT_LADDER), and the FIRST one is
    their picture in the navbar and on the account page. Free accounts have no
    picture; the perk IS the picture. Written only by PATCH /account, which
    replaces the whole list and renumbers positions from 0, so "the first one"
    is always position 0 among the rows that exist.

    WHY A TABLE AND NOT A COLUMN. The list is up to five long and ordered, and
    a future feature will show a supporter's oshis publicly (decided
    2026-09-13), so it needs to be joinable from the supporter side --
    `related_name="oshis"` rather than the "+" the old avatar_uma FK had.

    ENTITLEMENT IS NOT STORED HERE. Nothing on this row says whether the pick
    is currently covered by a pledge. A lapse or a downgrade keeps every row
    (the same "a lapse never deletes data" rule /calculator-data follows for
    premium rows) and GET /account reports how many of them the current tier
    covers; the view decides what to show and PATCH decides what may be added.

    NOT PERSONAL DATA. A uma pick is the site's own art, so purge_user_pii
    leaves these rows alone, exactly as it left avatar_uma. When the future
    public route reads them it must join through PatreonSupporter.linked_user
    and honour is_public, and never carry display_name or the handle alongside.

    CASCADE on the uma, not SET_NULL: a row with no uma is nothing, and a
    dangling slot would become a blank public tile later. The remaining rows
    keep their positions; the view reads "first by position", so a gap is fine.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name="oshis")
    uma = models.ForeignKey(Uma, on_delete=models.CASCADE, related_name="oshi_of")
    # 0-based rank in the person's list. 0 is their picture.
    position = models.PositiveSmallIntegerField()

    class Meta:
        verbose_name = "User Oshi"
        verbose_name_plural = "User Oshis"
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(fields=["user", "position"], name="unique_oshi_position"),
            # The same uma cannot fill two slots.
            models.UniqueConstraint(fields=["user", "uma"], name="unique_oshi_uma"),
        ]

    def __str__(self):
        return f"{self.user.username} - #{self.position + 1} {self.uma.name}"
