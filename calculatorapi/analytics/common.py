"""Helpers and bounds shared by every section module in this package."""


# ── Sanity bounds ────────────────────────────────────────────────────────────
# Ceilings above which a stored number stops being an answer and starts being
# someone finding out what the field does.
#
# These are ANALYTICS-ONLY, and deliberately NOT validation. The API accepts any
# value on purpose: a user is free to sandbox "what if I had a billion carats"
# and watch their own projection respond, and that is a legitimate thing to want
# from a calculator. These bounds decide only what counts as a DATA POINT on
# this page — nobody's saved plan is touched, rejected or rewritten.
#
# Both sit orders of magnitude above any real answer, so what they exclude is
# unambiguous rather than merely unusual. A cautious ceiling would be the wrong
# trade: wrongly dropping a genuine whale biases the report quietly, while a
# ceiling this high can only catch values that were never answers at all.
#
#   pulls    — pity is 200 and MLB of a five-copy card is ~1,000 pulls, so
#              2,000 is ten pity copies budgeted for one banner: double what
#              maxing out a banner costs, and still a number someone could
#              plausibly mean.
#   resource — 10,000,000 carats is ~66,000 pulls' worth, and the same ceiling
#              is generous past absurdity for tickets, crystals and shards.
#
# What prompted them: the client sanitiser caps typed input at nine digits
# (frontend NumberField.sanitise), so a user leaning on a digit key lands on
# exactly 999,999,999. That one value, on one account, was adding ~169,000 to
# every resource mean and turning a 145-avg banner into a 447,572 one.
SANE_MAX_PULLS = 2_000
SANE_MAX_RESOURCE = 10_000_000


def pct(part, whole):
    """Percentage rounded to one decimal; 0.0 when the denominator is empty."""
    return round(part / whole * 100, 1) if whole else 0.0
