"""
How a gallery is ordered: Hot, Top and New.

POINTS
------
    points = up votes - down votes, from voters who are not blocked

Every vote counts the same (the owner, 2026-10-30). Until then a supporter's
upvote counted 2 to 4 and a post started on its author's weight; both went
with downvotes. A post with no votes is on 0.

Points can be negative. The page never SHOWS less than 0 (Henry, 2026-10-26:
views/gallery.py floors the number), but the ranking uses the real points, so
a downvoted post sinks below every post on 0. Nothing is hidden for its score.

HOT
---
    hot = points x 0.5 ^ (age in days / HOT_HALF_LIFE_DAYS)

A post's points halve every 3 days. Reddit's hot ranking has the same shape
with a half-life of about 3 hours 45 minutes, which suits thousands of posts an
hour; a CM gallery gets a few a day over a two-to-three-week window, so 3 days
keeps a good post near the top for about a week. A negative post's hot score
is negative too and shrinks toward 0 with age, so an old pile-on slowly fades.

Worked example, day 7 of an event (the ranking test uses these numbers):

    post  posted   ups  downs  points  x age   hot
    A     day 1    12   0      12      0.25    3.00
    B     day 5    4    0      4       0.63    2.52
    C     day 6.5  4    1      3       0.89    2.67
    D     day 7    0    0      0       1.00    0.00
    E     day 2    6    9      -3      0.31    -0.94

    Hot: A C B D E    Top: A B C D E    New: D C B E A

Age runs from `published_at`, when the post FIRST went up, and New orders by
it too. A re-post keeps its votes (Henry, 2026-10-26), so aging it from the
re-post would hand a well-voted post a fresh start every time its team
changed.

Views and comments are not ranked on. Views mostly measure where a post
already sits (everyone sees the top of Hot), so ranking on them is a feedback
loop, and they are the easiest number to inflate. A comment can be criticism.
A vote is the one signal that means "this is good" (or isn't).

SCALE
-----
Points come from ONE query per page of a gallery (with_points), and Hot is
computed in Python: one event's gallery is tens to a few hundred posts. If a
gallery ever holds thousands, note that ordering by points x 0.5^(age/h) is the
same order as sign(points) x (log2|points| + days_since_epoch / h) for
nonzero points, which does not depend on "now" and so could be stored and
indexed.
"""

from django.db.models import Count, Q
from django.utils import timezone

from calculatorapi.models import VoteValue

HOT_HALF_LIFE_DAYS = 3

SORT_HOT = "hot"
SORT_TOP = "top"
SORT_NEW = "new"
SORTS = (SORT_HOT, SORT_TOP, SORT_NEW)

# Votes that count: from an account that is not blocked. The isnull clause
# matters as much: a post with no votes still joins one all-NULL row, and
# without it that row would be counted.
_COUNTED_VOTE = Q(votes__isnull=False, votes__user__publishing_blocked=False)


def with_points(posts):
    """Annotate a GalleryPost queryset with `ups` and `downs`, the counted
    votes each way. `points` is their difference, read through points()."""
    return posts.annotate(
        ups=Count("votes", filter=_COUNTED_VOTE & Q(votes__value=VoteValue.UP)),
        downs=Count("votes", filter=_COUNTED_VOTE & Q(votes__value=VoteValue.DOWN)),
    )


def points(post):
    """The post's points, which may be negative. Needs a post from with_points()."""
    return post.ups - post.downs


def hot_score(post_points, published_at, now):
    """points x 0.5 ^ (age in days / half-life). A post dated in the future
    (clock skew, or demo data) counts as brand new rather than gaining."""
    age_days = max((now - published_at).total_seconds(), 0) / 86400
    return post_points * 0.5 ** (age_days / HOT_HALF_LIFE_DAYS)


def ranked(posts, sort, now=None):
    """The posts of an annotated queryset as a list, in `sort` order.

    Each post gets `points` and `hot` attributes. Ties go to the newer post,
    then the higher id, so the order is total and a page boundary never
    shuffles between two requests.
    """
    now = now or timezone.now()
    rows = list(with_points(posts))
    for post in rows:
        post.points = points(post)
        post.hot = hot_score(post.points, post.published_at, now)

    def newer(post):
        return (post.published_at, post.id)

    if sort == SORT_NEW:
        return sorted(rows, key=newer, reverse=True)
    if sort == SORT_TOP:
        return sorted(rows, key=lambda post: (post.points, *newer(post)), reverse=True)
    return sorted(rows, key=lambda post: (post.hot, *newer(post)), reverse=True)
