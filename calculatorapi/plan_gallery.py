"""
Community plans: the pull plan kind of gallery post (notes/pull-plan-database-plan.md).

The gallery's shared layer (gallery.py, models/gallery.py) already handles any
post: votes, comments, reports, views, moderation, erasure. This module holds
what only a plan post has: who may use it, what posting one freezes, how the
feed is ranked and filtered, and what a copy brings.

WHO, FOR NOW: EARLY ACCESS
--------------------------
can_use() is the one gate (the owner, 2026-11-20: supporters first, everyone in
a later release). The feed, posting, copying and reading a plan post all ask
it and refuse on the server; the page never decides alone. Opening it to
everyone is this one function.

WHAT A POST FREEZES
-------------------
snapshot_from_plan(): the plan's rows through SharedPlannedBannerSerializer,
the share link's own whitelist (catalogue ids and the row's choices; never
pulls_already_made, never an id of the plan or the person), with `note` only
when the author ticked "Include my notes". Plus, per row, the banner's end
date when it was posted (`ends`), so a row can show the banners still ahead
without working dates out on every request. And, only when the author ticked
"Include my income", `income`: the fields in POSTED_INCOME_FIELDS and no
others.

INCOME IS NOT RESOURCES (the owner, 2026-11-20)
-----------------------------------------------
"when I said resources, I meant their income, not their literal current
resources, as there's no reason for any person to have the exact same amount
of carats or tickets or crystals as someone else." So a post can show what
makes a plan affordable (ranks and income settings, which the page turns into
"average a month" with the Calculator's own engine) and never a balance.
POSTED_INCOME_FIELDS is exactly what the Calculator's Average Monthly Income
tiles read (frontend useAverageMonthlyIncome). A test pins every GameStats
field to one side, so a new field cannot slip onto a post unnoticed.

THE FEED: RANKED ONCE A MINUTE, FILTERED PER REQUEST
----------------------------------------------------
The order is the same for every visitor; only "my vote" differs. So each sort
(New, and Top for Today, This week, This month, All time) is worked out at
most once a minute and remembered in the "gallery" cache as a list of
entries: a post's id with its four filter values. A request filters that list
in memory and loads only its own page. Filters never multiply the remembered
lists, and a filter is never a query.

The remembered list decides ORDER only. Every page is fetched through
gallery.visible_posts(), so a post hidden, blocked, taken down or erased since
the list was made drops out at once; the page may come back a row short for
up to a minute, never with something it shouldn't show. Publishing forgets
the lists, so an author sees their own post straight away.

Worked example: 60 people open Top this week in the same minute and there are
3,000 plans. Ranked per request: 60 ranking queries, 180,000 posts loaded.
Remembered: 1 ranking query, and 60 pages of 20 posts.

TOP BY WINDOW (Henry's leaderboards)
------------------------------------
Top counts the points (ups minus downs, every vote equal) a plan RECEIVED inside
a rolling window: the last 1, 7 or 30 days, or ever. A month-old plan people
are upvoting this week belongs in this week's Top. Ties go to the newer post,
then the higher id. (Waiting on Henry: whether "daily" might mean plans
posted that day instead. Received is the default.)
"""

import datetime
import hashlib

from django.core.cache import caches
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from calculatorapi import benefits, gallery, gallery_moderation, gallery_ranking, moderation_cards, plans
from calculatorapi.models import (
    GALLERY_CAPTION_MAX_LENGTH,
    GALLERY_STRATEGY_MAX_LENGTH,
    PLAN_STRATEGIES,
    BannerStepUp,
    BannerSupport,
    BannerTimeline,
    BannerUma,
    CustomUser,
    GalleryPost,
    GalleryPostKind,
    PlanSpending,
    PlanSweat,
    Scenario,
)
from calculatorapi.models.pvp_guide import PvpStyle
from calculatorapi.predictions import build_effective_date_maps, planned_effective_start, scenario_effective_dates
from calculatorapi.views.income_profile import stats_serializer
from calculatorapi.views.plan_public import SharedPlannedBannerSerializer, rows_in_order

REFUSED_TEXT = "Community plans are open to supporters first."

# Live plan posts one person may have at once: the plan cap's number. A team
# post is limited to one per event; a plan has no event, so without a count
# one person could fill the feed.
PLAN_POST_CAP = 5
# A note is shown beside a name once posted, so it is held to a comment's
# kind of length and goes through the same filters.
PLAN_NOTE_MAX_LENGTH = 300
# How many banners a feed row draws, and the link preview's picture.
LEAD_ROWS = 4
PREVIEW_ROWS = 5

FEED_CACHE_SECONDS = 60
SORT_TOP, SORT_NEW = "top", "new"
SORTS = (SORT_TOP, SORT_NEW)
# Top's windows, in days; None is all time.
TOP_WINDOWS = {"day": 1, "week": 7, "month": 30, "all": None}
DEFAULT_WINDOW = "week"

# What each spending level means, shown when someone hovers it (Henry's own
# words, 2026-11-20). A label the author picks, never read from purchases.
SPENDING_MEANINGS = {
    PlanSpending.F2P: "No purchases",
    PlanSpending.MINNOW: "Daily Carat Pack only",
    PlanSpending.DOLPHIN: "Selector packs only",
    PlanSpending.ORCA: "Discounted packs only",
    PlanSpending.WHALE: "Limitless",
}

# The stats a post may show when its author ticks "Include my income": what
# the Calculator's Average Monthly Income tiles read, so the page can show the
# same five numbers from them. Everything else in GameStats is a balance (what
# someone holds) or a spending choice, and never leaves an account this way.
POSTED_INCOME_FIELDS = (
    "club_rank",
    "team_trials_rank",
    "champions_meeting_rank",
    "league_of_heroes_rank",
    "daily_carat",
    "training_pass",
    "misc_earnings",
    "custom_misc_earnings_monthly",
    "monthly_shop_tickets",
    "shop_uma_tickets_bought",
    "shop_support_tickets_bought",
)

_BANNER_KEYS = ("banner_uma", "banner_support", "banner_step_up")
_BANNER_MODELS = {"banner_uma": BannerUma, "banner_support": BannerSupport, "banner_step_up": BannerStepUp}


def can_use(user):
    """Whether `user` may read, post or copy Community plans right now."""
    return benefits.early_access(user)


def can_open(post, viewer):
    """Whether `viewer` may reach this post through any route: read it, vote,
    comment, report, count a view, or open its author's card. Every post
    route asks this (views/gallery.py, views/profile_card.py), so a plan post
    is early access first everywhere, not only on its detail. A team post is
    anyone's; a plan's author always reaches their own (a lapsed supporter can
    still read and take down what they posted)."""
    if not post.is_plan or can_use(viewer):
        return True
    return bool(viewer and viewer.is_authenticated and viewer.pk == post.author_id)


# ── Posting ──────────────────────────────────────────────────────────────────


def posted_income(target):
    """{field: value} of the income fields only, from a stats block
    (plans.stats_target's answer), in the shape the client's stats use."""
    data = stats_serializer(target).data
    return {field: data[field] for field in POSTED_INCOME_FIELDS}


def _banner_name(row):
    banner = row.banner_target
    return banner.name if banner is not None else "a banner"


def _check_notes(rows):
    """GalleryRefused naming the banner, for the first note that may not be
    posted. Nothing is cut short for the author: they fix it or untick."""
    for row in rows:
        note = row.note.strip()
        if not note:
            continue
        if len(note) > PLAN_NOTE_MAX_LENGTH:
            raise gallery.GalleryRefused(
                f"Keep the note on {_banner_name(row)} to {PLAN_NOTE_MAX_LENGTH} characters.", field="notes"
            )
        try:
            gallery_moderation.check_text(note)
        except gallery_moderation.TextRefused as refusal:
            raise gallery.GalleryRefused(
                f"Check the note on {_banner_name(row)}. {refusal}", field="notes"
            ) from refusal


def range_for(rows, emap):
    """The scenario a plan runs up to: the newest one that has started by the
    end of its last counted banner, from the site's own dates (the ones the
    Timeline shows). None when its banners have no dates or no scenario has
    started by then. Worked out, never typed, so it can't be gamed."""
    ends = [
        entry["end_date"]
        for entry in (planned_effective_start(row, emap) for row in rows if not row.excluded_from_projection)
        if entry is not None and entry.get("end_date") is not None
    ]
    if not ends:
        return None
    last_end = max(ends)
    best, best_start = None, None
    for scenario in Scenario.objects.filter(banner_timeline__isnull=False):
        start = scenario_effective_dates(scenario, emap)["start_date"]
        if start is not None and start <= last_end and (best_start is None or start > best_start):
            best, best_start = scenario, start
    return best


def snapshot_from_plan(plan, *, include_notes, include_income):
    """(snapshot, range) for posting `plan`. See the module docstring for
    what a snapshot holds. GalleryRefused when there is nothing to post or a
    note may not be posted."""
    emap = build_effective_date_maps()[BannerTimeline]
    rows = rows_in_order(plan, emap)
    if not any(not row.excluded_from_projection for row in rows):
        raise gallery.GalleryRefused("Add a banner to this plan before posting it.")
    if include_notes:
        _check_notes(rows)
    data = SharedPlannedBannerSerializer(rows, many=True, context={"share_notes": include_notes}).data
    ends = []
    for row in rows:
        entry = planned_effective_start(row, emap)
        end = entry.get("end_date") if entry else None
        ends.append(end.isoformat() if end is not None else None)
    snapshot = {"rows": [dict(row) for row in data], "ends": ends}
    if include_income:
        snapshot["income"] = posted_income(plans.stats_target(plan))
    return snapshot, range_for(rows, emap)


def _clean_choice(value, choices, *, field, message):
    """A pick from a fixed list, or "" for "not said"."""
    if value in (None, ""):
        return ""
    if value not in choices:
        raise gallery.GalleryRefused(message, field=field)
    return value


def clean_strategies(value):
    """The ticked running styles as the stored string, in PLAN_STRATEGIES'
    order: ["end", "front"] becomes "front,end"."""
    if value in (None, ""):
        return ""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise gallery.GalleryRefused("Pick running styles from Front, Pace, Late and End.", field="strategies")
    chosen = set(value)
    if not chosen <= set(PLAN_STRATEGIES):
        raise gallery.GalleryRefused("Pick running styles from Front, Pace, Late and End.", field="strategies")
    return ",".join(style for style in PLAN_STRATEGIES if style in chosen)


def _preview_version(snapshot):
    """A short hash of the banners the preview picture can draw, so a re-post
    that changes them gets a new picture URL (as a team's does)."""
    drawn = "|".join(
        f"{key}:{row[key]}" for row in snapshot["rows"] for key in _BANNER_KEYS if row.get(key)
    )
    return hashlib.sha1(drawn.encode("utf-8")).hexdigest()[:12]


def publish_plan(user, plan, *, caption="", strategy="", spending="", sweat="", strategies=None,  # pylint: disable=too-many-arguments,too-many-locals
                 include_notes=False, include_income=False, confirm_public_identity=False):
    """Post `plan` to Community plans, or update its post. Returns (post, created).

    The title (`caption`) is required; the description (`strategy`, the
    gallery's column name) and the three filter picks are optional. Notes and
    income go only when their flag is literally True: posting them is the
    author's choice every time, never a share link's settings. A re-post keeps
    the link, the comments and the votes, and moves `content_updated_at`
    (the "Edited" mark) only when what readers see of the plan changed: its
    rows or its income. A hidden post stays hidden.
    """
    if not can_use(user):
        raise gallery.GalleryRefused(REFUSED_TEXT, status=403)
    if confirm_public_identity is not True:
        raise gallery.GalleryRefused("Tick the box to agree to what will show with your plan.")
    gallery.check_may_publish(user)
    if plan.user_id != user.pk:
        # The view resolves the plan through plans.get_owned_plan; this is
        # the backstop for any other caller.
        raise gallery.GalleryRefused("That plan isn't yours.", status=403)
    caption = gallery.clean_text(caption, field="caption", label="title", limit=GALLERY_CAPTION_MAX_LENGTH)
    if not caption:
        raise gallery.GalleryRefused("Give your plan a title.", field="caption")
    strategy = gallery.clean_text(strategy, field="strategy", label="description", limit=GALLERY_STRATEGY_MAX_LENGTH)
    spending = _clean_choice(
        spending, PlanSpending.values, field="spending", message="Pick a spending level from the list."
    )
    sweat = _clean_choice(sweat, PlanSweat.values, field="sweat", message="Pick a sweat level from the list.")
    strategies = clean_strategies(strategies)
    snapshot, plan_range = snapshot_from_plan(
        plan, include_notes=include_notes is True, include_income=include_income is True
    )
    now = timezone.now()
    fields = {
        "caption": caption,
        "strategy": strategy,
        "plan_snapshot": snapshot,
        "plan_spending": spending,
        "plan_sweat": sweat,
        "plan_strategies": strategies,
        "plan_range": plan_range,
        "preview_version": _preview_version(snapshot),
        "content_updated_at": now,
    }
    with transaction.atomic():
        # Locks the author's row, so two quick posts cannot both pass the cap
        # (the idiom plans._refuse_at_cap uses).
        CustomUser.objects.select_for_update().get(pk=user.pk)
        post = GalleryPost.objects.select_for_update().filter(author=user, source_plan=plan).first()
        if post is None:
            posted = GalleryPost.objects.filter(author=user, kind=GalleryPostKind.PLAN).count()
            if posted >= PLAN_POST_CAP:
                raise gallery.GalleryRefused(
                    f"You can have up to {PLAN_POST_CAP} plans posted. Take one down to post another."
                )
            post = GalleryPost.objects.create(
                public_id=gallery.generate_unique_post_id(),
                author=user,
                kind=GalleryPostKind.PLAN,
                source_plan=plan,
                published_at=now,
                **fields,
            )
            created = True
        else:
            before = post.plan_snapshot or {}
            if before.get("rows") == snapshot["rows"] and before.get("income") == snapshot.get("income"):
                del fields["content_updated_at"]
            for name, value in fields.items():
                setattr(post, name, value)
            post.save(update_fields=list(fields))
            created = False
    forget_ranking()
    if not created:
        # A reported post that is re-posted shows its new words on its card.
        moderation_cards.refresh(post)
    return post, created


def posted_plan_ids(user):
    """{plan id: post public id} for `user`'s posted plans, for the plan menu."""
    return dict(
        GalleryPost.objects.filter(author=user, source_plan__isnull=False).values_list("source_plan_id", "public_id")
    )


# ── Reading a post ───────────────────────────────────────────────────────────


def known_banners(snapshots):
    """{"banner_uma": {ids}, ...}: the banners these snapshots name that are
    still in the catalogue. Three queries for a whole page at most, none when
    the page has no plan post."""
    wanted = {key: set() for key in _BANNER_KEYS}
    for snapshot in snapshots:
        for row in snapshot.get("rows", []):
            for key in _BANNER_KEYS:
                if row.get(key):
                    wanted[key].add(row[key])
    return {
        key: set(_BANNER_MODELS[key].objects.filter(id__in=ids).values_list("id", flat=True)) if ids else set()
        for key, ids in wanted.items()
    }


def _is_known(row, known):
    return any(row.get(key) in known[key] for key in _BANNER_KEYS if row.get(key))


def rows_for_display(snapshot, known):
    """The snapshot's rows whose banner is still in the catalogue, in order.
    A banner taken out of the catalogue is dropped, as a team's uma is."""
    return [row for row in snapshot.get("rows", []) if _is_known(row, known)]


def summary(rows):
    """(row count, pulls, steps). `number_of_pulls` is STEPS on a step-up row,
    so the two are summed apart (plan_public.plan_totals' rule), and a row the
    author left out of their projection is left out of the totals."""
    counted = [row for row in rows if not row.get("excluded_from_projection")]
    pulls = sum(row["number_of_pulls"] for row in counted if not row.get("banner_step_up"))
    steps = sum(row["number_of_pulls"] for row in counted if row.get("banner_step_up"))
    return len(rows), pulls, steps


def lead_rows(snapshot, known, count=LEAD_ROWS, now=None):
    """The banners a row draws: the first `count` still ahead (their end
    after `now`, from the dates frozen at posting), else the first `count`."""
    now = now or timezone.now()
    rows = snapshot.get("rows", [])
    ends = snapshot.get("ends") or [None] * len(rows)
    shown = [row for row in rows if _is_known(row, known)]
    ahead = [
        row for row, end in zip(rows, ends)
        if _is_known(row, known) and end is not None and datetime.datetime.fromisoformat(end) > now
    ]
    picked = (ahead or shown)[:count]
    return [{key: row.get(key) for key in _BANNER_KEYS} for row in picked]


def strategies_of(post):
    """The post's running styles as a list, in PLAN_STRATEGIES' order."""
    return [style for style in post.plan_strategies.split(",") if style]


# ── The feed ─────────────────────────────────────────────────────────────────


def _cache_key(sort, window):
    return f"plan-feed:{sort}:{window if sort == SORT_TOP else 'all'}"


def forget_ranking():
    """Drop every remembered order, so the next request ranks afresh."""
    keys = [_cache_key(SORT_TOP, window) for window in TOP_WINDOWS] + [_cache_key(SORT_NEW, None)]
    caches["gallery"].delete_many(keys)


def _rank(sort, window, now):
    """The remembered list: entries in order, and the scenarios they reach."""
    posts = gallery.visible_posts().filter(kind=GalleryPostKind.PLAN)
    if sort == SORT_TOP:
        days = TOP_WINDOWS[window]
        since = now - datetime.timedelta(days=days) if days is not None else None
        posts = gallery_ranking.with_points(posts, since=since).annotate(
            points=F("ups") - F("downs")
        ).order_by("-points", "-published_at", "-id")
    else:
        posts = posts.order_by("-published_at", "-id")
    entries = list(posts.values_list("id", "plan_spending", "plan_sweat", "plan_strategies", "plan_range_id"))
    reached = {entry[4] for entry in entries if entry[4] is not None}
    # Release order, by the launch banner's JP date: the same order the
    # site's predicted dates give, without building the date maps per minute.
    ranges = list(
        Scenario.objects.filter(id__in=reached)
        .order_by("banner_timeline__jp_start_date", "id")
        .values_list("id", "name")
    )
    return {"entries": entries, "ranges": ranges}


def ranked(sort, window, now=None):
    """The remembered list for this sort, worked out at most once a minute."""
    key = _cache_key(sort, window)
    bundle = caches["gallery"].get(key)
    if bundle is None:
        bundle = _rank(sort, window, now or timezone.now())
        caches["gallery"].set(key, bundle, FEED_CACHE_SECONDS)
    return bundle


def filtered(bundle, *, range_id=None, strategies=(), spending="", sweat=""):
    """The entries that match. Spending and sweat: that value. Strategies: ANY
    of the ticked styles. Range: plans that run up to that scenario or an
    earlier one. An unset filter matches everything; a post that left a field
    blank matches only when that filter is unset. In memory, never a query."""
    order = {scenario_id: index for index, (scenario_id, _) in enumerate(bundle["ranges"])}
    limit = order.get(range_id) if range_id is not None else None
    wanted = set(strategies)
    matches = []
    for entry in bundle["entries"]:
        _, entry_spending, entry_sweat, entry_strategies, entry_range = entry
        if spending and entry_spending != spending:
            continue
        if sweat and entry_sweat != sweat:
            continue
        if wanted and not wanted & set(entry_strategies.split(",")):
            continue
        if limit is not None and (entry_range is None or order[entry_range] > limit):
            continue
        matches.append(entry)
    return matches


def filter_choices(bundle):
    """Every choice the sidebar offers, with its label: the page never
    hard-codes one. Range offers only the scenarios some visible plan reaches."""
    return {
        "spending": [
            {"value": value, "label": label, "meaning": SPENDING_MEANINGS[value]}
            for value, label in PlanSpending.choices
        ],
        "sweat": [{"value": value, "label": label} for value, label in PlanSweat.choices],
        "strategies": [{"value": style, "label": PvpStyle(style).label} for style in PLAN_STRATEGIES],
        "ranges": [{"value": scenario_id, "label": name} for scenario_id, name in bundle["ranges"]],
    }
