from django.core.validators import MinValueValidator
from django.db import models

from calculatorapi.race_payouts import DayAmountsChecks, day_amounts_field

from .champions_meeting_rank import ChampionsMeetingRank
from .league_of_heroes_rank import LeagueOfHeroesRank


class RaceRankReward(DayAmountsChecks, models.Model):
    """What a CM or LoH rank pays from one event number on.

    The game has raised these payouts over time, keyed to the event's number
    (CM 30, LoH 6, ...), so one amount per rank undercounts every later event.
    Each row says "from event #N, this rank pays X carats". An event pays the
    row with the LARGEST `from_event_number` at or below its own number, and
    the rank's own `income_amount` when no row reaches that low. So a rank with
    no rows pays exactly what it always did, and only the changes need a row.

    Why "from #N" and not a from-to range: the source tables are already
    written as from-numbers (#1, #2-5, #6-13...), and "largest from wins"
    needs no overlap check, only `unique (rank, from_event_number)`.

    Carats only. Tickets and shards stay on the rank, as they do in the source
    sheet. Abstract so CM and LoH each get their own table and their own inline
    on the rank's admin page, the way the two rank models are kept apart.
    """

    from_event_number = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        verbose_name="from event #",
        help_text="From this event number on, this rank pays the carats beside it. "
                  "Before the first row, it pays the amount above.",
    )
    income_amount = models.IntegerField(
        validators=[MinValueValidator(0)],
        verbose_name="carats",
    )
    # How this era's carats arrive over the event (race_payouts.py). Blank:
    # the rank's own split, scaled to this total by the client.
    day_amounts = day_amounts_field()

    class Meta:
        abstract = True
        # Ascending, which is the order the client reads them in and the order
        # prefetch_related hands them over.
        ordering = ["from_event_number"]

    def __str__(self):
        # `rank` is declared by each concrete subclass (the two FKs differ).
        rank = self.rank  # pylint: disable=no-member
        return f"{rank} from #{self.from_event_number}: {self.income_amount}"


class ChampionsMeetingRankReward(RaceRankReward):
    RACE_KIND = "champions_meeting"

    rank = models.ForeignKey(
        ChampionsMeetingRank, on_delete=models.CASCADE, related_name="rewards"
    )

    class Meta(RaceRankReward.Meta):
        verbose_name = "reward from a later meeting"
        verbose_name_plural = "rewards from a later meeting"
        constraints = [
            models.UniqueConstraint(
                fields=["rank", "from_event_number"],
                name="unique_cm_rank_reward_from",
            ),
        ]


class LeagueOfHeroesRankReward(RaceRankReward):
    RACE_KIND = "league_of_heroes"

    rank = models.ForeignKey(
        LeagueOfHeroesRank, on_delete=models.CASCADE, related_name="rewards"
    )

    class Meta(RaceRankReward.Meta):
        verbose_name = "reward from a later league"
        verbose_name_plural = "rewards from a later league"
        constraints = [
            models.UniqueConstraint(
                fields=["rank", "from_event_number"],
                name="unique_loh_rank_reward_from",
            ),
        ]
