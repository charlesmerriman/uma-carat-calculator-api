"""The share id a plan's public link carries (`Plan.public_id`).

Its own module, with no model imports, so the model's field default and the
migration that backfills existing rows can both import it without a cycle.
The collision check needs the database and lives in
plans.generate_unique_public_id().
"""

import secrets
import string


PUBLIC_ID_LENGTH = 8
# Letters and digits only, so an id survives being pasted into chat apps and
# URL bars untouched. 62^8 is about 2 x 10^14 ids: unguessable for a link that
# also has to be switched on (Plan.is_shared) before it answers anything.
PUBLIC_ID_ALPHABET = string.ascii_letters + string.digits


def generate_public_id():
    """Generate one URL-friendly, non-sequential public plan identifier."""
    return "".join(
        secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(PUBLIC_ID_LENGTH)
    )
