from django.db import models

from calculatorapi.race_payouts import DayAmountsChecks, day_amounts_field


class ChampionsMeetingRank(DayAmountsChecks, models.Model):
    RACE_KIND = "champions_meeting"

    name = models.CharField(max_length=255, null=False)
    income_amount = models.IntegerField()
    uma_ticket_amount = models.IntegerField(default=0)
    support_ticket_amount = models.IntegerField(default=0)
    ssr_shard_amount = models.IntegerField(default=0)
    sr_shard_amount = models.IntegerField(default=0)
    # How `income_amount` arrives over the event (race_payouts.py). Blank:
    # all of it at the finals, as before day-by-day payouts existed.
    day_amounts = day_amounts_field()
    # Explicit display order. Placements don't sort logically by income
    # (a top-league Third pays fewer carats than a Group B 1st) or by pk
    # (new placements were appended, not inserted), so ordering is its own
    # editor-controllable field rather than a side effect of another column.
    sort_order = models.IntegerField(default=0)

    class Meta:
        # Proper-noun casing for the admin.
        verbose_name = "Champions Meeting rank"
        verbose_name_plural = "Champions Meeting ranks"
        # Applied to every unordered .all() (e.g. the /calculator-data payload
        # that feeds the rank dropdown). `id` tiebreaks so equal/blank
        # sort_orders stay deterministic instead of relying on DB row order.
        ordering = ["sort_order", "id"]

    def __str__(self):
        return str(self.name)
