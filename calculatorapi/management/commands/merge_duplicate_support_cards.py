"""
Merges duplicate SupportCard rows that describe the same card into one.

The support-card twin of `merge_duplicate_umas`, and the same code: read that
command's docstring for what went wrong, how a merge works and why it is safe
to re-run. Only what differs is here.

WHY IT EXISTS: the banner backfill made a "Daiichi Ruby (Rerun)" row next to the
real card. The copy has NO `game_id` (the real row holds it, and the column is
unique), so `import_game_data` can never reach it and nothing keyed on the id
ever will.

HOW A DUPLICATE IS RECOGNISED: as for umas, by the card id the image filename
starts with (`support_cards/30114-Daiichi-Ruby-pwr.png`); a support card id has
five digits where an outfit id has six. Matching on the NAME would not work:
several real cards share one name (Daiichi Ruby has an R and two SSRs), and only
the image says which of them the copy is a copy of. The row that keeps is the
one whose name does not end in "(Rerun)".

Usage:
    python manage.py merge_duplicate_support_cards --dry-run   # report only
    python manage.py merge_duplicate_support_cards             # prompts
    python manage.py merge_duplicate_support_cards --no-input  # scripted

Run against PRODUCTION with --dry-run first, as with every data command.
"""

import re

from calculatorapi.management.commands import merge_duplicate_umas
from calculatorapi.models import SupportCard, SupportsOnSupportBanner

# `support_cards/30114-Daiichi-Ruby-pwr.png`. Anchored at the start, and the
# hyphen after the fifth digit keeps a six-digit run from matching.
IMAGE_GAME_ID = re.compile(r"^(\d{5})-")


class Command(merge_duplicate_umas.Command):
    help = "Merge duplicate SupportCard rows (the '(Rerun)' copies) into the original."

    model = SupportCard
    noun = "support card"
    # The banner junction has no unique constraint over (banner, card), so the
    # collision check has to be explicit. See the note in merge_duplicate_umas.
    unconstrained_junctions = {SupportsOnSupportBanner: "banner_support"}

    @staticmethod
    def image_game_id(image_name):
        if not image_name:
            return None
        match = IMAGE_GAME_ID.match(image_name.rsplit("/", 1)[-1])
        return int(match.group(1)) if match else None
