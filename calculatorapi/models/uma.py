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


class Rarity(models.IntegerChoices):
    """An outfit's initial star count. The number IS the game's value."""
    ONE = 1, "★1"
    TWO = 2, "★2"
    THREE = 3, "★3"


class RunningStyle(models.IntegerChoices):
    """The game's `running_style` numbering, kept as-is so the import is a copy."""
    FRONT = 1, "Front runner"
    PACE = 2, "Pace chaser"
    LATE = 3, "Late surger"
    END = 4, "End closer"


class Aptitude(models.IntegerChoices):
    """Aptitude grades on the game's 1..8 scale, stored as the game stores them."""
    G = 1, "G"
    F = 2, "F"
    E = 3, "E"
    D = 4, "D"
    C = 5, "C"
    B = 6, "B"
    A = 7, "A"
    S = 8, "S"


# Columns the game-data import owns on Uma, in the order the admin shows them.
# Everything here is filled by `manage.py import_game_data` from the committed
# snapshot and is nullable so "not imported yet" is distinguishable from a
# real value (a growth bonus of 0 is real).
def _game_int(help_text, choices=None):
    return models.PositiveSmallIntegerField(
        null=True, blank=True, choices=choices, help_text=help_text,
    )


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
    # A second art asset, not a replacement: the same card art without the
    # rarity border. Rendered by the oshi picker and the account picture, via
    # `portrait` below; attached to the row (matched on game_id) by
    # `manage.py link_borderless_umas`.
    # Its own folder in the Space, so the picker lists the two sets apart.
    image_borderless = models.ImageField(
        upload_to="umas_borderless/",
        blank=True,
        null=True,
        verbose_name="borderless image",
        help_text="The same art without the rarity border. Optional.",
    )
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
    # one -- see calculatorapi/eligibility.py. The ★ half is `rarity`, further
    # down with the rest of the game data, read through `is_three_star`.
    is_time_limited = models.BooleanField(
        default=False,
        help_text=(
            "Only obtainable during a limited window, so selector tickets and "
            "step-ups can never take them. Hides this uma from every selector "
            "picker and stops a selector funding a banner it is featured on."
        ),
    )
    # ---- Game data, imported. See the note above _game_int. ----
    title = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="The outfit's title from the game, e.g. \"[Special Dreamer]\".",
    )
    rarity = _game_int(
        "Initial star count. Selectors and step-ups only grant ★3 umas, so ★1 "
        "and ★2 are hidden from the pickers. Blank counts as ★3. Imported for "
        "umas on global; set it by hand for one that is not there yet.",
        choices=Rarity.choices,
    )
    running_style = _game_int("Default strategy.", choices=RunningStyle.choices)
    apt_turf = _game_int("Turf aptitude.", choices=Aptitude.choices)
    apt_dirt = _game_int("Dirt aptitude.", choices=Aptitude.choices)
    apt_short = _game_int("Short distance aptitude.", choices=Aptitude.choices)
    apt_mile = _game_int("Mile aptitude.", choices=Aptitude.choices)
    apt_medium = _game_int("Medium distance aptitude.", choices=Aptitude.choices)
    apt_long = _game_int("Long distance aptitude.", choices=Aptitude.choices)
    apt_front = _game_int("Front runner aptitude.", choices=Aptitude.choices)
    apt_pace = _game_int("Pace chaser aptitude.", choices=Aptitude.choices)
    apt_late = _game_int("Late surger aptitude.", choices=Aptitude.choices)
    apt_end = _game_int("End closer aptitude.", choices=Aptitude.choices)
    base_speed = _game_int("Speed at the initial star count.")
    base_stamina = _game_int("Stamina at the initial star count.")
    base_power = _game_int("Power at the initial star count.")
    base_guts = _game_int("Guts at the initial star count.")
    base_wit = _game_int("Wit at the initial star count.")
    growth_speed = _game_int("Speed growth bonus, percent.")
    growth_stamina = _game_int("Stamina growth bonus, percent.")
    growth_power = _game_int("Power growth bonus, percent.")
    growth_guts = _game_int("Guts growth bonus, percent.")
    growth_wit = _game_int("Wit growth bonus, percent.")

    @property
    def character_id(self):
        """The game's character id: an outfit id is `<character><outfit>`."""
        return self.game_id // 100 if self.game_id else None

    @property
    def portrait(self):
        """The art to draw when the uma stands for a PERSON: the borderless cut
        if there is one, else the bordered card art.

        The oshi picker and the account picture crop the art into a circle,
        where the rarity border only shows as clipped corners. Falling back
        keeps every uma pickable while the borderless set has gaps (a new
        outfit gets its bordered art first). One property so GET /umas and
        GET /account cannot disagree about which file a pick shows.
        """
        return self.image_borderless or self.image

    @property
    def is_three_star(self):
        """Can a selector or step-up grant this uma, as far as stars go?

        Derived from `rarity` rather than stored. An unknown rarity counts as
        ★3 on purpose: an uma nobody has data for yet stays selectable, which
        is what the old default-ticked "Is ★3" box did. /calculator-data still
        sends this under the same name, so the client never saw the change.
        """
        return self.rarity is None or self.rarity == Rarity.THREE

    def __str__(self):
        return f"{self.name}"
