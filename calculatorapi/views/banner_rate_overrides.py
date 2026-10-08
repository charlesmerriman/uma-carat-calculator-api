"""The per-card rate overrides a banner serializer sends alongside its cards.

A featured card's override lives on the THROUGH row (UmasOnUmaBanner /
SupportsOnSupportBanner), because the same card can carry a different rate on
another banner. The banner serializers nest the cards through the M2M, which
never sees the through row, so the overrides travel as a separate map instead:
`{card id: rate}`, holding only the cards that have one.
"""


def rate_overrides(through_rows, card_id_field):
    """`{card id: override as a float}` for the rows that set one.

    `through_rows` must be prefetched by the caller (`<through>_set`), or this
    costs a query per banner. Floats, not DRF's decimal strings, for the same
    reason as CalculationConstantsSerializer: the client does arithmetic on it.
    """
    return {
        getattr(row, card_id_field): float(row.rate_override)
        for row in through_rows.all()
        if row.rate_override is not None
    }
