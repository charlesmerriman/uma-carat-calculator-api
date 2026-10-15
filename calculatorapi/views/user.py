from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import serializers
from calculatorapi.models import CustomUser as User
from calculatorapi.models import ClubRank, TeamTrialsRank, ChampionsMeetingRank, LeagueOfHeroesRank


class GameStatsSerializer(serializers.ModelSerializer):
    """The stats block (models/game_stats.py) in the shape the client stores as
    `user_stats_data`. Abstract in the same sense as the model: subclasses set
    Meta.model and nothing else, so an account's own stats and an
    IncomeProfile's serialize identically and the client cannot tell which it
    was handed. Which one a plan reads is plans.stats_target()'s decision;
    stats_serializer() (views/income_profile.py) picks the subclass for it.
    """
    club_rank = serializers.PrimaryKeyRelatedField(
        required=False, allow_null=True, queryset=ClubRank.objects.all()
    )
    team_trials_rank = serializers.PrimaryKeyRelatedField(
        queryset=TeamTrialsRank.objects.all(), required=False, allow_null=True
    )
    champions_meeting_rank = serializers.PrimaryKeyRelatedField(
        queryset=ChampionsMeetingRank.objects.all(), required=False, allow_null=True
    )
    league_of_heroes_rank = serializers.PrimaryKeyRelatedField(
        queryset=LeagueOfHeroesRank.objects.all(), required=False, allow_null=True
    )

    class Meta:
        fields = [
            "current_carat", "current_paid_carat", "uma_ticket", "support_ticket",
            "uma_selector_ticket", "support_selector_ticket",
            "daily_carat", "training_pass", "misc_earnings",
            "monthly_shop_tickets", "shop_uma_tickets_bought", "shop_support_tickets_bought",
            "spend_tickets_on_banners", "discounted_paid_pulls", "full_price_paid_pulls",
            "include_purchases_in_projection", "webstore_bonus",
            "sr_shards", "sr_crystals", "ssr_shards", "ssr_crystals",
            "club_rank", "team_trials_rank", "champions_meeting_rank", "league_of_heroes_rank",
        ]


class UserStatsSerializer(GameStatsSerializer):
    """The account's OWN stats: what a plan without an income profile reads."""

    class Meta(GameStatsSerializer.Meta):
        model = User


# Public sign-up was removed when ordinary accounts moved to Google/Discord
# (see views/social_auth.py), and the staff password login went on 2026-10-15:
# it handed a bearer token to anyone with a staff password, around the admin's
# second factor (calculatorapi/admin_site.py), and nothing called it. There is
# deliberately no register endpoint and no login endpoint here: the only way to
# create an account is through a provider, which is what keeps emails, names
# and passwords out of this database, and the only way to sign in as staff is
# /admin. What remains is the logout every kind of account uses, and the stats
# serializers above.


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def user_logout(request):
    request.user.auth_token.delete()
    return Response({"message": "Successfully logged out"}, status=status.HTTP_200_OK)
