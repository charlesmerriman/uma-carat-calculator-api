from rest_framework import serializers


class IncomeLedgerRowSerializer(serializers.Serializer):
    """
    Wire format for one `calculatorapi.ledger` row.

    A plain Serializer over dicts rather than a ModelSerializer: ledger rows are
    derived facts assembled across three models, not instances of any one of
    them. Declaring the shape explicitly is also what makes the contract with
    `frontend/src/types/ledger.ts` reviewable in one place.

    `date` is the instant the reward lands. `throughout_end` is the end of the
    curve a `carats_throughout` pool decays over (null on rows that carry none);
    the buffer between an event's end and its banner's end is already removed —
    see calculatorapi/ledger.py.

    The amount columns are always present and always integers, on every row and
    every kind, so the client never guards on shape. Race rows carry zeros: what
    a placement pays depends on the user's rank, which only the client knows.
    `LedgerSerializerTests` pins this field list against `ledger.AMOUNT_FIELDS`.
    """

    date = serializers.DateTimeField()
    kind = serializers.CharField()
    source_id = serializers.IntegerField()
    name = serializers.CharField()
    is_predicted = serializers.BooleanField()
    throughout_end = serializers.DateTimeField(allow_null=True)
    # cm_number / loh_number on race rows, null on event rows. Picks which
    # "from event #N" row a rank pays (frontend raceRewardAmount).
    event_number = serializers.IntegerField(allow_null=True)
    # The instants a race event pays, one per payout day, the last equal to
    # `date` (race_payouts.py). Empty on event rows.
    payouts = serializers.ListField(child=serializers.DateTimeField())
    # When a race event pays the rank's tickets and its shards, each one of
    # `payouts` (race_payouts.ITEM_PAYOUT_DAYS). Null on event rows.
    tickets_at = serializers.DateTimeField(allow_null=True)
    shards_at = serializers.DateTimeField(allow_null=True)

    carats = serializers.IntegerField()
    carats_throughout = serializers.IntegerField()
    uma_tickets = serializers.IntegerField()
    support_tickets = serializers.IntegerField()
    ssr_shards = serializers.IntegerField()
    ssr_crystals = serializers.IntegerField()
    sr_shards = serializers.IntegerField()
    sr_crystals = serializers.IntegerField()
