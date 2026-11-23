"""
LOCAL ONLY: fills Community plans with fake players' posted plans, so the page
can be built and looked at before anyone real posts.

Ten made-up accounts, three of them on supporter tiers (when the local
database has the tiers) so the gallery looks show. Each posts one plan built
from the banners still ahead, through the real publish path
(plan_gallery.publish_plan), so every rule a real post meets applies. The
posts carry the four filter values in different mixes, some with notes and
some with income, and votes cast over the last month so Top's four windows
and New come out in different orders, plus a few comments.

WHY IT CAN NEVER REACH PRODUCTION
---------------------------------
Its first act is content_snapshot.require_local_database(): anything but
SQLite is refused, the guard seed_gallery_demo and pull_prod_content use.
Nothing runs it on deploy. The accounts have unusable passwords and no linked
provider, so nobody can sign in as them.

WHAT IT OWNS
------------
Accounts whose username starts with `demoplan_` and supporter rows whose
`patreon_user_id` starts with `demoplan-`. A plain run removes what an earlier
run made first (deleting the accounts cascades their plans, posts, votes and
comments), so running it twice gives the same result.

Usage:
    python manage.py seed_plan_gallery_demo            # replace the demo plans
    python manage.py seed_plan_gallery_demo --clear    # only remove them
"""

import datetime
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from calculatorapi import gallery, plan_gallery, plans
from calculatorapi.content_snapshot import require_local_database
from calculatorapi.models import (
    BannerSupport,
    BannerTimeline,
    BannerUma,
    ChampionsMeetingRank,
    ClubRank,
    CustomUser,
    GalleryPost,
    GalleryVote,
    LeagueOfHeroesRank,
    PatreonSupporter,
    PatreonTier,
    TeamTrialsRank,
    UserPlannedBanner,
    VoteValue,
)
from calculatorapi.predictions import build_effective_date_maps

USERNAME_PREFIX = "demoplan_"
PATREON_ID_PREFIX = "demoplan-"

# (display name, tier name or None, title, spending, sweat, strategies,
#  banners, notes?, income?, days since posting)
CAST = (
    ("CaratSaver", "Senior Class", "F2P road to the anniversary",
     "f2p", "semi_comp", ["pace", "late"], 13, True, True, 8),
    ("TicketHoarder", "Classic Class", "Supports first, umas later",
     "minnow", "casual", ["front"], 9, False, False, 5),
    ("SparkChaser", None, "One copy of everything for Champions Meeting",
     "whale", "comp", ["end"], 21, True, False, 12),
    ("QuietPlanner", None, "Skip until the anniversary",
     "f2p", "casual", [], 4, False, False, 2),
    ("PaidCaratPal", "Junior Class", "Step-ups only, the paid carat plan",
     "dolphin", "semi_comp", ["late", "end"], 6, False, True, 3),
    ("FreshTrainer", None, "New player, first six months",
     "f2p", "casual", ["front", "pace"], 11, True, False, 20),
    ("SummerWaiter", None, "Saving for the summer outfits",
     "f2p", "comp", ["pace"], 7, False, False, 0.2),
    ("MLBorBust", None, "Every support MLB, the whale route",
     "whale", "no_life", ["front", "end"], 24, False, True, 15),
    ("TwoAQuarter", None, "Two umas a quarter and nothing else",
     "orca", "semi_comp", ["late"], 8, False, False, 6),
    ("PityPlanner", None, "Anniversary step-up plus one pity",
     "minnow", "comp", [], 5, False, False, 1),
)
NOTES = (
    "Stop at one copy. She is only here for the next Champions Meeting.",
    "Go to MLB. This card carries every deck until spring.",
    "Skip if the anniversary tickets cover it.",
)
ABOUT = (
    "Free carats only. I skip almost everything until the anniversary, then go to pity on both "
    "banners. The support banner in December is the one exception: that card is worth it for every deck."
)
COMMENTS = ("Nice plan, copying it.", "Would you skip the December support banner?", "Same budget as me, thanks.")


class Command(BaseCommand):
    help = "LOCAL ONLY: fill Community plans with fake players' plans for development."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Only remove the demo plans.")

    def handle(self, *args, **options):
        require_local_database()
        with transaction.atomic():
            removed = clear_demo_data()
            self.stdout.write(f"Removed {removed} demo plan accounts.")
            if options["clear"]:
                return
            made = seed(random.Random(2026))
        self.stdout.write(self.style.SUCCESS(f"Posted {made} demo plans."))


def clear_demo_data():
    """Delete everything an earlier run made. Returns how many accounts went."""
    users = CustomUser.objects.filter(username__startswith=USERNAME_PREFIX)
    count = users.count()
    GalleryPost.objects.filter(author__in=users).delete()
    PatreonSupporter.objects.filter(patreon_user_id__startswith=PATREON_ID_PREFIX).delete()
    users.delete()
    return count


def upcoming_banners(now):
    """Uma and support banners still ahead, in date order, alternating kinds
    as a real plan tends to."""
    emap = build_effective_date_maps()[BannerTimeline]
    ahead = {
        timeline_id for timeline_id, entry in emap.items()
        if entry and entry.get("end_date") and entry["end_date"] > now
    }

    def start(banner):
        return emap[banner.banner_timeline_id]["start_date"]

    umas = sorted(BannerUma.objects.filter(banner_timeline_id__in=ahead), key=start)
    supports = sorted(BannerSupport.objects.filter(banner_timeline_id__in=ahead), key=start)
    if not umas or not supports:
        raise CommandError("There are no upcoming banners locally. Run pull_prod_content first.")
    return sorted(umas + supports, key=start)


def _account(index, name, tier_name):
    user = CustomUser.objects.create_user(username=f"{USERNAME_PREFIX}{index}", password=None)
    user.set_unusable_password()
    user.display_name = name
    user.early_access = True
    user.club_rank = ClubRank.objects.order_by("-id").first()
    user.team_trials_rank = TeamTrialsRank.objects.order_by("-id").first()
    user.champions_meeting_rank = ChampionsMeetingRank.objects.order_by("id").first()
    user.league_of_heroes_rank = LeagueOfHeroesRank.objects.order_by("id").first()
    user.daily_carat = index % 2 == 0
    user.save()
    tier = PatreonTier.objects.filter(name=tier_name).first() if tier_name else None
    if tier is not None:
        PatreonSupporter.objects.create(
            linked_user=user, display_name=f"demo patron {index}", patreon_user_id=f"{PATREON_ID_PREFIX}{index}",
            email=f"{USERNAME_PREFIX}{index}@example.invalid", is_active=True, tier=tier,
        )
    return user


def seed(rng):  # pylint: disable=too-many-locals
    """Make the accounts and their posts. Returns how many posts."""
    now = timezone.now()
    banners = upcoming_banners(now)
    authors, posts = [], []
    for index, (name, tier, title, spending, sweat, strategies, count, notes, income, age) in enumerate(CAST):
        user = _account(index, name, tier)
        plan = plans.get_active_plan(user)
        for position, banner in enumerate(banners[:count]):
            kind = "banner_uma" if isinstance(banner, BannerUma) else "banner_support"
            UserPlannedBanner.objects.create(
                plan=plan, user=user, number_of_pulls=rng.choice((0, 133, 200, 200, 400)) if kind == "banner_uma"
                else rng.choice((0, 200, 400, 600)),
                note=NOTES[position] if notes and position < len(NOTES) else "", **{kind: banner},
            )
        post, _ = plan_gallery.publish_plan(
            user, plan, caption=title, strategy=ABOUT if index % 3 == 0 else "", spending=spending, sweat=sweat,
            strategies=strategies, include_notes=notes, include_income=income, confirm_public_identity=True,
        )
        published = now - datetime.timedelta(days=age)
        GalleryPost.objects.filter(pk=post.pk).update(
            published_at=published, content_updated_at=published,
            view_count=rng.randint(20, 1300), copy_count=rng.randint(0, 200),
        )
        authors.append(user)
        posts.append((post, age))
    # Votes from the other demo accounts, cast at times spread over each
    # post's life, so Today, This week, This month and All time differ.
    for post, age in posts:
        for voter in authors:
            if voter.pk == post.author_id or rng.random() < 0.35:
                continue
            value = VoteValue.DOWN if rng.random() < 0.12 else VoteValue.UP
            vote = GalleryVote.objects.create(post=post, user=voter, value=value)
            cast = now - datetime.timedelta(days=rng.uniform(0, age))
            GalleryVote.objects.filter(pk=vote.pk).update(created_at=cast)
        for body in rng.sample(COMMENTS, rng.randint(0, len(COMMENTS))):
            try:
                gallery.add_comment(rng.choice([user for user in authors if user.pk != post.author_id]), post, body)
            except gallery.GalleryRefused:
                # The same words from the same account twice in a day: the
                # gallery's own spam rule, so that comment is skipped.
                continue
    plan_gallery.forget_ranking()
    return len(posts)
