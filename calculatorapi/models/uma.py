import re

from django.db import models

# The six-digit card id at the front of an uma image filename
# (`umas/102001-Seiun-Sky.png`). The optional leading `<digit>-` is the star
# rarity some newer uploads carry (`3-111801-Admire-Groove.png`); it is not part
# of the id. Anchored at the start so a stray six-digit run elsewhere in a name
# can never be mistaken for the id.
IMAGE_GAME_ID = re.compile(r"^(?:\d-)?(\d{6})-")


def game_id_from_image(image_name):
    """The card id an uma image filename starts with, as an int, or None."""
    if not image_name:
        return None
    match = IMAGE_GAME_ID.match(image_name.rsplit("/", 1)[-1])
    return int(match.group(1)) if match else None


class Uma(models.Model):
    name = models.CharField(max_length=255)
    # The game's own id for this outfit: character `1020` outfit `01` is
    # `102001`, so `game_id // 100` is the character. Unique because it is the
    # join key for everything imported from the game data (skills, aptitudes,
    # base stats); nullable because placeholder rows like "(All)" have none.
    # Backfilled by 0059 from the image filename, which has always started
    # with it, and written by scripts/add_missing_umas.py for new rows.
    game_id = models.PositiveIntegerField(
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Numeric card id from the game data (e.g. 102001). The first six "
            "digits of the image filename. Editors rarely need to touch this."
        ),
    )
    image = models.ImageField(upload_to="umas/", blank=True, null=True)
    admin_comments = models.TextField(blank=True, null=True, help_text="Notes for editors.")

    # PUBLIC, and rendered -- unlike admin_comments above, which nothing on the
    # site displays. Shown as an overlay when a player hovers or focuses the uma's
    # art on the Timeline. It describes the uma itself ("Great pace parent"), so it
    # reads the same on every banner; advice about one particular banner belongs
    # on the UmasOnUmaBanner.recommendation junction instead.
    #
    # Capped at 100 because the overlay has to fit the narrowest tile the Timeline
    # draws. CharField rather than TextField so the admin form and the column both
    # enforce the cap; default "" rather than null so "no purpose" has exactly one
    # representation.
    purpose = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Shown publicly when a player hovers this uma's art on the Timeline, "
            "e.g. \"Great pace parent.\" Leave blank to show nothing. Notes for "
            "other editors go in Admin comments instead."
        ),
    )

    # Two INTRINSIC selector gates, stored because neither is derivable from
    # banner data: a time-limited unit and a non-★3 unit both appear on ordinary
    # banners and look exactly like a selectable unit from here. They are
    # independent of the JP cutoff and bite even under an unrestricted (null)
    # one -- see calculatorapi/eligibility.py.
    is_time_limited = models.BooleanField(
        default=False,
        help_text=(
            "Only obtainable during a limited window, so selector tickets and "
            "step-ups can never take them. Hides this uma from every selector "
            "picker and stops a selector funding a banner it is featured on."
        ),
    )
    is_three_star = models.BooleanField(
        default=True,
        verbose_name="Is ★3",
        help_text=(
            "Uncheck for ★1/★2 units. Selectors and step-ups only grant ★3 "
            "umas, so anything unchecked here is hidden from the pickers."
        ),
    )

    def __str__(self):
        return f"{self.name}"
