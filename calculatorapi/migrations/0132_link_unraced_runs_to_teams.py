"""A fill-blanks backfill of PvpRun.team (0131): the My team team an unraced
ticket follows (models/pvp_run.py, notes/runs-follow-team-plan.md).

The backfill (owner, 2026-11-12) links a ticket only when ALL of these hold,
so it never guesses:

  * the ticket has no team yet (NULL; it only ever sets NULLs);
  * nothing is raced, skipped, placed or grouped on it (only those follow);
  * its event is not known to be over (no confirmed global end date in the
    past; an ended CM's unraced tickets have nothing left to follow);
  * its lineup, uma, style and veteran per spot, equals EXACTLY ONE of the
    player's teams for that event. Two matches, or none, leave it unlinked.

It creates and deletes nothing, and on a database without runs it does
nothing. Reversing it is a no-op; reversing 0131 drops the links with the column.
"""

from django.db import migrations
from django.utils import timezone


def link_unraced_tickets(apps, schema_editor):
    PvpRun = apps.get_model("calculatorapi", "PvpRun")
    PvpTeam = apps.get_model("calculatorapi", "PvpTeam")
    ChampionsMeeting = apps.get_model("calculatorapi", "ChampionsMeeting")
    LeagueOfHeroes = apps.get_model("calculatorapi", "LeagueOfHeroes")

    now = timezone.now()
    over_cms = set(ChampionsMeeting.objects.filter(global_end_date__lt=now).values_list("id", flat=True))
    over_lohs = set(LeagueOfHeroes.objects.filter(global_end_date__lt=now).values_list("id", flat=True))

    def lineup(rows):
        return tuple(sorted((row.position, row.uma_id, row.style, row.trained_uma_id) for row in rows))

    # Each player's teams per event, by lineup.
    teams = {}
    for team in PvpTeam.objects.prefetch_related("members"):
        key = (team.user_id, team.champions_meeting_id, team.league_of_heroes_id)
        teams.setdefault(key, []).append((team.id, lineup(team.members.all())))

    linked = []
    candidates = (
        PvpRun.objects.filter(
            team__isnull=True, races__isnull=True, skipped=False, placement__isnull=True, finals_group="",
        )
        .exclude(champions_meeting_id__in=over_cms)
        .exclude(league_of_heroes_id__in=over_lohs)
        .distinct()
        .prefetch_related("runners")
    )
    for run in candidates:
        mine = lineup(run.runners.all())
        if not mine:
            continue
        key = (run.user_id, run.champions_meeting_id, run.league_of_heroes_id)
        matches = [team_id for team_id, theirs in teams.get(key, []) if theirs == mine]
        if len(matches) == 1:
            run.team_id = matches[0]
            linked.append(run)
    PvpRun.objects.bulk_update(linked, ["team"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('calculatorapi', '0131_pvprun_team'),
    ]

    operations = [
        migrations.RunPython(link_unraced_tickets, migrations.RunPython.noop),
    ]
