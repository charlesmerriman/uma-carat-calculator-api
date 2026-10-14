"""
How a gallery is ordered: Hot, Top and New.

POINTS
------
    points = the author's own vote weight
           + the weight of every upvote, from voters who are not blocked

A vote's weight comes from the voter's pledge (benefits.VOTE_WEIGHT_LADDER):
1 with no pledge, then 2 / 3 / 4 for the three tiers, read as the pledge is NOW,
never as it was when they voted. The author's own weight is the post's head
start, the way Reddit counts your own post as upvoted: a Senior Class post
starts on 4, a free one on 1.

Why a head start and not a multiplier: doubling a supporter's points would let
money compound with popularity (at 50 real votes it would beat a free post
with 99). A fixed head start of up to +3 decides a lot at 2 votes and almost
nothing at 50, so supporters get seen sooner and the community still decides
which team is best.

HOT
---
    hot = points x 0.5 ^ (age in days / HOT_HALF_LIFE_DAYS)

A post's points halve every 3 days. Reddit's hot ranking has the same shape
with a half-life of about 3 hours 45 minutes, which suits thousands of posts an
hour; a CM gallery gets a few a day over a two-to-three-week window, so 3 days
keeps a good post near the top for about a week.

Worked example, day 7 of an event (the ranking test uses these numbers):

    post  author     posted   upvotes             points          x age   hot
    A     no pledge  day 1    12 free             1 + 12 = 13     0.25    3.25
    B     Senior     day 5    3 free + 1 Classic  4 + 3 + 3 = 10  0.63    6.30
    C     no pledge  day 6.5  4 free              1 + 4 = 5       0.89    4.45
    D     Junior     day 7    none                2               1.00    2.00
    E     no pledge  day 2    5 free + 1 Senior   1 + 5 + 4 = 10  0.31    3.15

    Hot: B C A E D    Top: A B E C D    New: D C B E A

Age runs from `content_updated_at`, the moment the CURRENT version of the post
went up: a re-post clears its votes (gallery.publish_team), so its points are
about the new version and are aged from it. New orders by the same field.

Views and comments are not ranked on. Views mostly measure where a post
already sits (everyone sees the top of Hot), so ranking on them is a feedback
loop, and they are the easiest number to inflate. A comment can be criticism.
An upvote is the one signal that means "this is good".

SCALE
-----
Points come from ONE query per page of a gallery (with_points), and Hot is
computed in Python: one event's gallery is tens to a few hundred posts. If a
gallery ever holds thousands, note that ordering by points x 0.5^(age/h) is the
same order as log2(points) + days_since_epoch / h, which does not depend on
"now" and so could be stored and indexed. A stored value would go stale when a
pledge lapses, so it is not worth it before then.
"""

from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from calculatorapi.benefits import ANY_PAID_TIER, FREE_VOTE_WEIGHT, VOTE_WEIGHT_LADDER

HOT_HALF_LIFE_DAYS = 3

SORT_HOT = "hot"
SORT_TOP = "top"
SORT_NEW = "new"
SORTS = (SORT_HOT, SORT_TOP, SORT_NEW)


def weight_expression(user_path):
    """A SQL expression for the vote weight of the user reached through
    `user_path` ("author", or "votes__user").

    Built FROM VOTE_WEIGHT_LADDER, rung by rung, so the database and
    benefits.vote_weight_for read one table and cannot disagree (a test checks
    every tier). The three conditions are benefits.entitled_supporter's: a
    linked supporter row that is active and has a tier. No row at all is a
    LEFT JOIN of NULLs, which matches no rung and gets the free weight.
    """
    supporter = f"{user_path}__patreon_supporter"
    entitled = Q(**{f"{supporter}__is_active": True, f"{supporter}__tier__isnull": False})
    whens = []
    for threshold, weight in VOTE_WEIGHT_LADDER:
        condition = entitled
        if threshold is not ANY_PAID_TIER:
            condition = condition & Q(**{f"{supporter}__tier__order__lte": threshold})
        whens.append(When(condition, then=Value(weight)))
    return Case(*whens, default=Value(FREE_VOTE_WEIGHT), output_field=IntegerField())


# Votes that count: from an account that is not blocked. The isnull clause
# matters as much: a post with no votes still joins one all-NULL row, and
# without it that row would be summed as one free vote.
_COUNTED_VOTE = Q(votes__isnull=False, votes__user__publishing_blocked=False)


def with_points(posts):
    """Annotate a GalleryPost queryset with `upvote_count`, `vote_points`
    and `author_weight`. `points` is their sum, read through points()."""
    return posts.annotate(
        upvote_count=Count("votes", filter=_COUNTED_VOTE),
        vote_points=Coalesce(
            Sum(weight_expression("votes__user"), filter=_COUNTED_VOTE),
            Value(0),
        ),
        author_weight=weight_expression("author"),
    )


def points(post):
    """The post's points. Needs a post from with_points()."""
    return post.author_weight + post.vote_points


def hot_score(post_points, updated_at, now):
    """points x 0.5 ^ (age in days / half-life). A post dated in the future
    (clock skew, or demo data) counts as brand new rather than gaining."""
    age_days = max((now - updated_at).total_seconds(), 0) / 86400
    return post_points * 0.5 ** (age_days / HOT_HALF_LIFE_DAYS)


def ranked(posts, sort, now=None):
    """The posts of an annotated queryset as a list, in `sort` order.

    Each post gets `points` and `hot` attributes. Ties go to the newer
    version, then the higher id, so the order is total and a page boundary
    never shuffles between two requests.
    """
    now = now or timezone.now()
    rows = list(with_points(posts))
    for post in rows:
        post.points = points(post)
        post.hot = hot_score(post.points, post.content_updated_at, now)

    def newer(post):
        return (post.content_updated_at, post.id)

    if sort == SORT_NEW:
        return sorted(rows, key=newer, reverse=True)
    if sort == SORT_TOP:
        return sorted(rows, key=lambda post: (post.points, *newer(post)), reverse=True)
    return sorted(rows, key=lambda post: (post.hot, *newer(post)), reverse=True)
