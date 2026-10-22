from django.db import models

from calculatorapi.race_payouts import DayAmountsChecks, day_amounts_field


class LeagueOfHeroesRank(DayAmountsChecks, models.Model):
    RACE_KIND = "league_of_heroes"

    name = models.CharField(max_length=255)
    income_amount = models.IntegerField()
    uma_ticket_amount = models.IntegerField(default=0)
    support_ticket_amount = models.IntegerField(default=0)
    ssr_shard_amount = models.IntegerField(default=0)
    sr_shard_amount = models.IntegerField(default=0)
    # How `income_amount` arrives over the event (race_payouts.py). Blank:
    # all of it on the last day, as before day-by-day payouts existed.
    day_amounts = day_amounts_field()

    class Meta:
        # Proper-noun casing for the admin.
        verbose_name = "League of Heroes rank"
        verbose_name_plural = "League of Heroes ranks"

    def __str__(self):
        return str(self.name) if self.name else "Unnamed Rank"
