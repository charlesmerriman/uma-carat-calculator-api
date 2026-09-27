import secrets
import string


PUBLIC_ID_LENGTH = 8
PUBLIC_ID_ALPHABET = string.ascii_letters + string.digits


def generate_public_id():
    """Generate one URL-friendly, non-sequential public plan identifier."""
    return "".join(
        secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(PUBLIC_ID_LENGTH)
    )