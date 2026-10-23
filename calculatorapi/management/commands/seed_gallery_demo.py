"""
LOCAL ONLY: fills the PvP gallery with fake players, so it can be built and
looked at before anyone real posts.

Twelve made-up accounts, eight of them with no live pledge, each post a legal
team for the event the PvP page opens on (the running one, else the next).
Three of them also post on the event before it, to show the gallery is per
event. The posts are spread over the last ten days and carry votes, views,
copies and comments arranged so that every case the gallery has to draw shows
up somewhere:

    * all three supporter looks (ssr, sr, rose), a supporter who turned the
      badge off, and a lapsed supporter who shows as a free account
    * Hot, Top and New in three different orders
    * a reply by the post's author ("Author"), an edited comment, a deleted
      comment with replies ("[deleted]"), a comment hidden by three reports (an
      open item in the moderation queue), and comments made before the post
      was updated ("On an earlier version")

WHY IT CAN NEVER REACH PRODUCTION
---------------------------------
Its first act is content_snapshot.require_local_database(), the guard
load_sheet_pvp_guides and pull_prod_content use: anything but SQLite is
refused. Nothing runs it on deploy. The accounts cannot be signed in to: they
have unusable passwords and no linked provider.

WHAT IT OWNS, AND HOW IT CLEANS UP
----------------------------------
Accounts whose username starts with `demo_`, and Patreon supporter rows whose
`patreon_user_id` starts with `demo-`. Deleting the accounts cascades their
teams, posts, votes and favourites. This is the one place a supporter row is
ever deleted, and only because these rows are fake and local; the rule that
the supporter table is not ours to delete from is about real patrons.

A plain run clears what an earlier run made first, so running it twice gives
the same result. `pull_prod_content` replaces the event rows the posts point
at, so run this again after a pull.

Usage:
    python manage.py seed_gallery_demo            # replace the demo data
    python manage.py seed_gallery_demo --clear    # only remove it
    python manage.py seed_gallery_demo --seed 7   # a different, repeatable shuffle
"""

import datetime
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from calculatorapi import gallery, gallery_ranking, pvp_teams
from calculatorapi.content_snapshot import require_local_database
from calculatorapi.models import (
    ChampionsMeeting,
    CustomUser,
    GalleryComment,
    GalleryHiddenReason,
    GalleryPost,
    GalleryReport,
    GalleryReportReason,
    GalleryVote,
    LeagueOfHeroes,
    PatreonSupporter,
    PatreonTier,
    PvpDeckSlot,
    PvpGuide,
    PvpStyle,
    SupportCard,
    Uma,
    UserOshi,
)
from calculatorapi.predictions import build_effective_date_maps

USERNAME_PREFIX = "demo_"
PATREON_ID_PREFIX = "demo-"

SENIOR, CLASSIC, JUNIOR = "Senior Class", "Classic Class", "Junior Class"

# (display name, tier, lapsed, badge shown, days since posting, upvotes)
# The days and votes are what make Hot, Top and New disagree: CarrotRun is old
# and has the most votes (Top), NightRacer is recent and well voted (Hot),
# GateNine posted minutes ago with none (New).
CAST = (
    ("MooncakeMile", SENIOR, False, True, 2.0, 5),
    ("TurfTactician", CLASSIC, False, True, 4.5, 7),
    ("GateNine", JUNIOR, False, True, 0.05, 0),
    ("LateSurger", JUNIOR, False, False, 6.0, 3),
    ("PaceKeeper", CLASSIC, True, True, 8.0, 6),
    ("CarrotRun", None, False, True, 9.5, 11),
    ("NightRacer", None, False, True, 1.0, 6),
    ("BlueRibbon", None, False, True, 3.0, 4),
    ("HarukaPace", None, False, True, 7.0, 2),
    ("StaminaGoblin", None, False, True, 0.5, 2),
    ("FinalCorner", None, False, True, 5.0, 5),
    ("DirtDash", None, False, True, 10.0, 1),
)
# Who also posts on the event before, with how many votes.
PREVIOUS_EVENT_POSTS = (("CarrotRun", 3), ("TurfTactician", 2), ("BlueRibbon", 1))

CAPTIONS = (
    "Runaway blocker and two pace, holds up on the long straight",
    "Budget build, one SSR per deck",
    "End closer carry for the final corner",
    "Safe pace core for round 1",
    "Two late chasers behind a front blocker",
    "Stamina heavy, built for this course",
    "",
    "My finals team, went 4-1 in round 2",
    "Front plus two end, a bit greedy but fun",
    "Copy of a JP team adjusted for our card pool",
    "Recovery stacked so nobody runs dry",
    "First CM, feedback welcome",
)

STYLES = (PvpStyle.FRONT, PvpStyle.PACE, PvpStyle.LATE, PvpStyle.END)


class Command(BaseCommand):
    help = "LOCAL ONLY: fill the PvP gallery with fake players for development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear", action="store_true", help="Only remove the demo data."
        )
        parser.add_argument(
            "--seed", type=int, default=2026, help="Shuffle seed (default 2026)."
        )

    def handle(self, *args, **options):
        require_local_database()
        with transaction.atomic():
            removed = clear_demo_data()
            self.stdout.write(f"Removed {removed} demo accounts.")
            if options["clear"]:
                return
            summary = Seeder(random.Random(options["seed"]), self.stdout).run()
        self.stdout.write(self.style.SUCCESS(summary))


def clear_demo_data():
    """Delete everything an earlier run made. Returns how many accounts went."""
    demo_users = CustomUser.objects.filter(username__startswith=USERNAME_PREFIX)
    count = demo_users.count()
    # Posts first: their comments, votes and reports go with them, so no
    # comment by a demo account is left behind as "[deleted]".
    GalleryPost.objects.filter(author__in=demo_users).delete()
    PatreonSupporter.objects.filter(patreon_user_id__startswith=PATREON_ID_PREFIX).delete()
    demo_users.delete()
    return count


def event_window(now):
    """(the event the PvP page opens on, the event before it) as model rows.

    The frontend's defaultPvpEvent over the same dates: the event running now,
    else the next, else the last; CMs and LoHs ordered together by start.
    """
    maps = build_effective_date_maps((ChampionsMeeting, LeagueOfHeroes))
    events = []
    for model in (ChampionsMeeting, LeagueOfHeroes):
        rows = model.objects.in_bulk(list(maps[model]))
        for pk, entry in maps[model].items():
            if entry and entry.get("start_date") and pk in rows:
                events.append((entry["start_date"], entry.get("end_date"), rows[pk]))
    if not events:
        raise CommandError("There are no Champions Meetings or League of Heroes events locally.")
    events.sort(key=lambda event: event[0])
    index = next(
        (i for i, (start, end, _) in enumerate(events) if end and start <= now <= end),
        None,
    )
    if index is None:
        index = next((i for i, (start, _, _) in enumerate(events) if start > now), len(events) - 1)
    previous = events[index - 1][2] if index > 0 else None
    return events[index][2], previous


def event_fields(event):
    """The kind and id pvp_teams.create_team takes, and the filter for a guide."""
    if isinstance(event, ChampionsMeeting):
        return "cm", {"champions_meeting": event}
    return "loh", {"league_of_heroes": event}


class TeamPool:  # pylint: disable=too-few-public-methods
    """What one event's demo teams are built from: the event's guide when it
    has one (its recommended umas, with their styles, and the cards in its
    decks), else the catalogue's umas and cards that are on global."""

    def __init__(self, event):
        _, fields = event_fields(event)
        guide = PvpGuide.objects.filter(**fields).first()
        self.umas = []
        self.deck_cards = {}
        if guide:
            self.umas = [
                (rec.style, rec.uma)
                for rec in guide.uma_recommendations.select_related("uma")
                if rec.uma.image and rec.style in STYLES + (PvpStyle.RUNAWAY,)
            ]
            for slot in (
                PvpDeckSlot.objects.filter(deck__guide=guide, order=0, support_card__isnull=False)
                .select_related("deck")
                .order_by("deck__order", "deck_id", "slot")
            ):
                cards = self.deck_cards.setdefault(slot.deck.style, [])
                if slot.support_card_id not in cards:
                    cards.append(slot.support_card_id)
        if len({uma.character_id for _, uma in self.umas}) < 3:
            self.umas = [(None, uma) for uma in _on_global_umas()]
        self.cards = sorted({card for cards in self.deck_cards.values() for card in cards})
        if len(self.cards) < 6:
            self.cards = _on_global_cards()
        if len(self.cards) < 6 or len({uma.character_id for _, uma in self.umas}) < 3:
            raise CommandError(
                f"Not enough umas or cards to build a legal team for {event}. "
                "Load content first (pull_prod_content)."
            )

    def members(self, rng):
        """Three umas of three different characters, each with a style and a
        full deck: its style's guide cards first, the rest from the pool."""
        chosen, characters = [], set()
        for style, uma in rng.sample(self.umas, len(self.umas)):
            if uma.character_id in characters:
                continue
            chosen.append((style or rng.choice(STYLES), uma))
            characters.add(uma.character_id)
            if len(chosen) == 3:
                break
        members = []
        for position, (style, uma) in enumerate(chosen):
            deck = list(self.deck_cards.get(style, []))[:4]
            spare = [card for card in rng.sample(self.cards, len(self.cards)) if card not in deck]
            cards = (deck + spare)[:6]
            members.append({
                "position": position,
                "uma": uma.id,
                "style": style,
                "cards": [{"slot": slot, "support_card": card} for slot, card in enumerate(cards, start=1)],
            })
        return members


def _on_global_umas():
    """Pictured umas, the ones on global (they have skills) when there are any."""
    umas = Uma.pickable()
    on_global = umas.filter(is_on_global=True)
    return list(on_global if on_global.exists() else umas)


def _on_global_cards():
    cards = SupportCard.objects.all()
    on_global = cards.filter(is_on_global=True)
    return list((on_global if on_global.exists() else cards).values_list("id", flat=True))


class Seeder:  # pylint: disable=too-few-public-methods
    """One run of the demo data. Every random choice goes through `rng`, so a
    seed gives the same gallery every time."""

    def __init__(self, rng, stdout):
        self.rng = rng
        self.stdout = stdout
        self.now = timezone.now()
        self.users = {}
        self.posts = {}

    def run(self):  # pylint: disable=too-many-locals
        event, previous = event_window(self.now)
        tiers = {tier.name: tier for tier in PatreonTier.objects.filter(name__in=(SENIOR, CLASSIC, JUNIOR))}
        missing = {SENIOR, CLASSIC, JUNIOR} - set(tiers)
        if missing:
            self.stdout.write(
                f"No local tier named {', '.join(sorted(missing))}: those accounts get no pledge."
            )
        favourites = list(Uma.pickable())
        for name, tier, lapsed, badge, *_ in CAST:
            self.users[name] = self._account(name, tiers.get(tier), lapsed, badge, favourites)

        pool = TeamPool(event)
        for index, (name, _, _, _, days, votes) in enumerate(CAST):
            self.posts[name] = self._post(name, event, pool, days, votes, CAPTIONS[index])
        previous_count = 0
        if previous is not None:
            previous_pool = TeamPool(previous)
            for name, votes in PREVIOUS_EVENT_POSTS:
                self._post(name, previous, previous_pool, 12 + votes, votes, "Last event's team")
                previous_count += 1

        comments = self._comments()
        votes = GalleryVote.objects.filter(post__author__in=self.users.values()).count()
        kind, _ = event_fields(event)
        orders = self._orders(event)
        return (
            f"{len(self.users)} demo accounts, {len(self.posts)} posts on {event} and "
            f"{previous_count} on {previous}, {votes} votes, {comments} comments.\n{orders}\n"
            f"Open http://localhost:5173/app/pvp?event={kind}-{event.id}&view=gallery"
        )

    def _account(self, name, tier, lapsed, badge, favourites):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        user = CustomUser(
            username=f"{USERNAME_PREFIX}{name.lower()}",
            email="",
            first_name="",
            last_name="",
            display_name=name,
            show_supporter_badge=badge,
        )
        user.set_unusable_password()
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError as error:
            raise CommandError(
                f"A local account already goes by \"{name}\". Rename it, then run this again."
            ) from error
        if favourites:
            UserOshi.objects.create(user=user, uma=self.rng.choice(favourites), position=0)
        if tier is not None:
            PatreonSupporter.objects.create(
                display_name=name,
                patreon_user_id=f"{PATREON_ID_PREFIX}{name.lower()}",
                linked_user=user,
                tier=tier,
                is_active=not lapsed,
            )
        return user

    def _post(self, name, event, pool, days, votes, caption):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        user = self.users[name]
        kind, _ = event_fields(event)
        team = pvp_teams.create_team(
            user, kind=kind, event_id=event.id, members=pool.members(self.rng)
        )
        post, _ = gallery.publish_team(user, team, caption=caption, confirm_public_identity=True)
        when = self.now - datetime.timedelta(days=days)
        others = [other for other in self.users.values() if other.pk != user.pk]
        for voter in self.rng.sample(others, min(votes, len(others))):
            GalleryVote.objects.create(post=post, user=voter, created_at=when)
        GalleryPost.objects.filter(pk=post.pk).update(
            published_at=when,
            content_updated_at=when,
            view_count=25 + votes * 30 + self.rng.randint(0, 40),
            copy_count=votes // 2 + self.rng.randint(0, 2),
        )
        post.refresh_from_db()
        return post

    def _comment(self, post, author, body, hours_after, parent=None, **fields):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        return GalleryComment.objects.create(
            post=post,
            author=self.users[author] if author else None,
            parent=parent,
            body=body,
            created_at=post.content_updated_at + datetime.timedelta(hours=hours_after),
            **fields,
        )

    def _comments(self):  # pylint: disable=too-many-locals
        """The scripted threads. Returns how many comments were made."""
        carrot, night, turf, moon = (self.posts[name] for name in (
            "CarrotRun", "NightRacer", "TurfTactician", "MooncakeMile"))

        # A busy thread, with the author answering and one edited comment.
        first = self._comment(
            carrot, "NightRacer", "This carried me through round 1, thanks for posting it.", 3)
        self._comment(
            carrot, "CarrotRun",
            "Glad it worked! The pace slot is the one to swap if you're short on stamina.", 5, parent=first)
        swap = self._comment(
            carrot, "TurfTactician", "Would swapping the borrow for a second recovery card be worth it here?", 8)
        self._comment(
            carrot, "CarrotRun",
            "I tried it and it was close. Speed felt short on the last straight though.", 9, parent=swap)
        self._comment(carrot, "GateNine", "Same question, I kept running out at the final corner.", 12, parent=swap)
        self._comment(
            carrot, "BlueRibbon", "What stats are you hitting on the front runner?", 20,
            edited_at=carrot.content_updated_at + datetime.timedelta(hours=21),
        )
        self._comment(carrot, "DirtDash", "Clean team. I'd copy it if I had the wit card.", 30)

        # A comment deleted by its author after people replied to it.
        gone = self._comment(night, None, "", 2, is_deleted=True)
        self._comment(night, "HarukaPace", "Not sure what they meant, the deck looks fine to me.", 4, parent=gone)
        self._comment(night, "NightRacer", "Yeah, I'm keeping it as is.", 5, parent=gone)
        self._comment(night, "MooncakeMile", "Two late chasers is brave on this course. Did it pay off?", 7)
        self._comment(night, "StaminaGoblin", "Copied it, works well for me.", 9)

        # A comment three people reported, hidden until a moderator decides.
        reported = self._comment(
            turf, "DirtDash", "worst team I've seen, uninstall", 6,
            is_hidden=True, hidden_reason=GalleryHiddenReason.REPORTS, hidden_at=self.now,
        )
        for reporter, reason in (("GateNine", GalleryReportReason.HARASSMENT),
                                 ("FinalCorner", GalleryReportReason.HARASSMENT),
                                 ("LateSurger", GalleryReportReason.OTHER)):
            GalleryReport.objects.create(reporter=self.users[reporter], comment=reported, reason=reason)
        self._comment(turf, "FinalCorner", "Solid for round 1. I'd want more power for the finals.", 10)
        self._comment(turf, "TurfTactician", "Fair, I'll post my finals version once I've tested it.", 12)

        # Comments on the version before an update: MooncakeMile re-posted a
        # day after posting, so these two read "On an earlier version". (A real
        # re-post clears the votes; the five here stand for the new version.)
        self._comment(moon, "CarrotRun", "Nice, though I'd drop the second stamina card.", -20)
        self._comment(moon, "PaceKeeper", "Agreed, that slot wants speed.", -18)
        updated = moon.content_updated_at + datetime.timedelta(hours=24)
        GalleryPost.objects.filter(pk=moon.pk).update(
            published_at=moon.content_updated_at - datetime.timedelta(days=1), content_updated_at=updated
        )
        moon.refresh_from_db()
        self._comment(moon, "LateSurger", "The new deck looks much better.", 2)

        # A few one-off comments so most posts show a count.
        for author, target, body in (
            ("CarrotRun", "GateNine", "Nice first post!"),
            ("HarukaPace", "FinalCorner", "Is two end closers too many here?"),
            ("FinalCorner", "FinalCorner", "Maybe, but they don't get boxed in as much."),
            ("BlueRibbon", "DirtDash", "Old but still solid."),
            ("GateNine", "PaceKeeper", "Thanks, copying this one."),
            ("NightRacer", "StaminaGoblin", "Love the recovery stack."),
        ):
            post = self.posts[target]
            self._comment(post, author, body, 1)
        return GalleryComment.objects.filter(post__author__in=self.users.values()).count()

    def _orders(self, event):
        """The first three of each sort, so the run says what to expect."""
        _, fields = event_fields(event)
        posts = gallery.visible_posts().filter(**fields)
        names = {user.pk: name for name, user in self.users.items()}
        lines = []
        for sort in gallery_ranking.SORTS:
            top = gallery_ranking.ranked(posts, sort, now=self.now)[:3]
            lines.append(f"  {sort:>3}: {', '.join(names.get(post.author_id, '?') for post in top)}")
        return "First three by sort:\n" + "\n".join(lines)
