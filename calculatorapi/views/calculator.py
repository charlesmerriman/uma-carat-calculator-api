import json

from rest_framework.viewsets import ViewSet
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework import permissions, status
from django.db import transaction
from django.db.models import Prefetch
from calculatorapi import plans, public_payload_cache
from calculatorapi.eligibility import build_first_jp_date_maps
from calculatorapi.ledger import (
    KIND_CHAMPIONS_MEETING,
    KIND_LEAGUE_OF_HEROES,
    build_income_ledger,
)
from calculatorapi.predictions import (
    build_anniversary_event_date_map,
    build_effective_date_maps,
    build_game_event_date_map,
    build_scenario_date_map,
    effective_sort_key,
    planned_effective_start,
)
from calculatorapi.models import (
    CalculationConstants, Plan,
    ClubRank, TeamTrialsRank, ChampionsMeetingRank, LeagueOfHeroesRank,
    UserPlannedBanner, UserPlannedPurchase, UserStepUpSelection,
    BannerUma, BannerSupport, BannerStepUp,
    ChampionsMeeting, LeagueOfHeroes, GameEvent, BannerTimeline,
    AnniversaryEvent, Scenario,
    UmasOnUmaBanner, SupportsOnSupportBanner,
)
from calculatorapi.views.rank_viewsets import (
    ClubRankSerializer,
    TeamTrialsRankSerializer,
    ChampionsMeetingRankSerializer,
    LeagueOfHeroesRankSerializer,
)
from calculatorapi.views.user_planned_banner import UserPlannedBannerSerializer
from calculatorapi.views.user import UserStatsSerializer
from calculatorapi.views.banner_uma import BannerUmaSerializer
from calculatorapi.views.banner_support import BannerSupportSerializer
from calculatorapi.views.banner_step_up import BannerStepUpSerializer
from calculatorapi.views.champions_meeting import ChampionsMeetingSerializer
from calculatorapi.views.league_of_heroes import LeagueOfHeroesSerializer
from calculatorapi.views.game_event import GameEventSerializer
from calculatorapi.views.banner_timeline import BannerTimelineForViewingSerializer
from calculatorapi.views.anniversary_event import AnniversaryEventSerializer
from calculatorapi.views.scenario import ScenarioSerializer
from calculatorapi.views.ledger import IncomeLedgerRowSerializer
from calculatorapi.views.calculation_constants import CalculationConstantsSerializer
from calculatorapi.views.user_planned_purchase import UserPlannedPurchaseSerializer
from calculatorapi.views.user_step_up_selection import UserStepUpSelectionSerializer
from calculatorapi.views.plan import PlanSerializer


def _replace_user_rows(rows, *, model, serializer_class, user, missing_message,
                       context=None, plan=None):
    # pylint: disable=too-many-arguments
    # Every one after `rows` is keyword-only and names a distinct part of the
    # contract below; bundling them into a config object would hide which
    # collection is being reconciled at each call site.
    """Reconcile one of the PATCH body's user-owned collections against the DB.

    The contract every such collection shares:

      * key absent from the body (rows is None) -> leave the collection alone
      * []                                      -> delete every row
      * row carrying an id                      -> partial update, 404 if the
                                                   id isn't this user's
      * row without an id                       -> create, owned by this user

    `plan` narrows the collection from "this user's rows" to "this PLAN's
    rows". Only planned banners pass it; purchases and step-up selections
    belong to the account and leave it None. It is not optional in spirit for
    banners: the delete below removes every row the body did not name, so
    reconciling plan A's body against ALL of the user's rows would wipe plans
    B through E. The caller resolves it through plans.get_owned_plan(), so a
    plan that reaches here is already known to be this user's.

    `context` is handed to every row's serializer. Collections that validate a
    row against something outside it (step-up selections against the JP cutoff,
    and against what the user already had stored) pass their shared lookups in
    that way rather than rebuilding them per row.

    Returns an error Response to bail out with, or None on success. The caller
    runs inside transaction.atomic(), so returning early rolls back whatever
    earlier collections had already written -- a half-saved plan is worse than
    a rejected one.

    NOTE for collections with a UNIQUE constraint: sending rows WITHOUT ids
    makes this a delete-all-then-create, which is collision-free. Sending ids
    updates rows one at a time, so moving a value between two rows (a card from
    slot 3 to slot 4) transiently duplicates it and trips the constraint.
    UserStepUpSelection relies on the id-less form.
    """
    if rows is None:
        return None

    # One scope for the delete, the lookup and the save, so the three can
    # never disagree about which rows this body is allowed to touch.
    # TRANSITIONAL: a planned banner still carries `user` beside `plan` until
    # release 2 drops the column -- see models/user_planned_banner.py.
    scope = {"user": user} if plan is None else {"user": user, "plan": plan}

    incoming_ids = [row["id"] for row in rows if "id" in row]
    model.objects.filter(**scope).exclude(id__in=incoming_ids).delete()

    for row in rows:
        row_id = row.get("id")
        if row_id:
            try:
                # An id from ANOTHER of this user's plans is "not found" too:
                # a body is reconciled against one plan, never across them.
                instance = model.objects.get(id=row_id, **scope)
            except model.DoesNotExist:
                return Response({"error": missing_message},
                                status=status.HTTP_404_NOT_FOUND)
            serializer = serializer_class(
                instance, data=row, partial=True, context=context or {}
            )
        else:
            serializer = serializer_class(data=row, context=context or {})

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(**scope)

    return None


def _planned_banner_kind_rank(planned_banner):
    """Tie-break for two planned rows sharing a start date.

    A campaign routinely opens several banners on one instant, and the plan is
    sorted by resolved start date alone -- so tied rows fall back to whatever
    order the queryset returned them in (effectively primary key, i.e. the order
    the user happened to add them). The client re-sorts on every edit, so
    without a matching rank here the sheet appears to reshuffle itself the next
    time the user reloads.

    The order is Uma -> Support -> Step-Up Uma -> Step-Up Support. The step-up
    split reads `BannerStepUp.card_type`, which is why this cannot be expressed
    as `Meta.ordering` or an `order_by`: the discriminator lives one FK away on
    whichever of the three targets is set, and the primary key is a resolved
    date map that is computed in Python anyway.

    MUST stay in step with `plannedBannerOrderRank` in
    `frontend/src/utils/bannerHelpers.ts`.
    """
    if planned_banner.banner_uma_id is not None:
        return 0
    if planned_banner.banner_support_id is not None:
        return 1
    if planned_banner.banner_step_up_id is not None:
        return 2 if planned_banner.banner_step_up.card_type == "uma" else 3
    # Unreachable while exactly_one_banner_target holds, but a row with no
    # target sorts last rather than raising.
    return 4


def _stored_selection_pairs(user):
    """{(banner_step_up_id, "uma"|"support", card_id)} the user already had saved.

    The grandfathering set for step-up selection eligibility. One values_list
    query; a card FK that has gone null (its card was deleted) contributes
    nothing, which is right -- there is no pairing left to grandfather.
    """
    pairs = set()
    rows = UserStepUpSelection.objects.filter(user=user).values_list(
        "banner_step_up_id", "uma_id", "support_id"
    )
    for step_up_id, uma_id, support_id in rows:
        if uma_id is not None:
            pairs.add((step_up_id, "uma", uma_id))
        elif support_id is not None:
            pairs.add((step_up_id, "support", support_id))
    return pairs


def _build_user_context():
    """The three lookups the user-scoped collections need, and nothing else.

    A signed-in request still has to order its planned banners by resolved
    banner date and its planned purchases by resolved campaign date, and to
    resolve selector eligibility on the cards nested inside a planned row --
    none of which the cached public bytes carry in usable form.

    _build_public_payload() derives the same three while assembling the
    catalogue and passes its own down; this exists for the cache-HIT path,
    where none of that work has happened. Six queries against a payload that
    would otherwise cost hundreds.
    """
    date_maps = build_effective_date_maps()
    emap = date_maps[BannerTimeline]
    # banner_links only: build_anniversary_event_date_map spans a campaign's
    # banner "Parts" to date it, and reads nothing off products.
    anniversary_emap = build_anniversary_event_date_map(
        AnniversaryEvent.objects.prefetch_related("banner_links"), emap
    )
    uma_first_jp_dates, support_first_jp_dates = build_first_jp_date_maps()
    return emap, anniversary_emap, {
        "uma_first_jp_dates": uma_first_jp_dates,
        "support_first_jp_dates": support_first_jp_dates,
    }


def serialize_planned_banners(plan, *, emap, card_context):
    """One plan's banner rows, date-sorted and serialized.

    Shared by GET /calculator-data (the active plan) and GET /plans/<id> (the
    plan being switched to), so a row looks identical whichever route served
    it and the client needs one type for both.
    """
    planned_banners = sorted(
        UserPlannedBanner.objects.filter(plan=plan).select_related(
            "banner_uma__banner_timeline",
            "banner_support__banner_timeline",
            "banner_step_up__banner_timeline",
            "banner_step_up__anniversary_event",
        # UserPlannedBannerSerializer nests BannerUma/BannerSupportSerializer,
        # so a signed-in user pays the same M2M N+1 as the catalogue does --
        # once per planned row rather than once per banner, but same cause.
        ).prefetch_related("banner_uma__umas", "banner_support__support_cards"),
        key=lambda pb: (
            effective_sort_key(planned_effective_start(pb, emap)),
            _planned_banner_kind_rank(pb),
        ),
    )
    return UserPlannedBannerSerializer(
        planned_banners, many=True,
        # No "request" in context on purpose: with it, DRF's ImageField
        # emits absolute URLs via request.build_absolute_uri(), which
        # behind the prod reverse proxy point at the wrong (internal/http)
        # host and break the nested banner images. Every other serializer
        # omits request and emits relative /media/... URLs that the
        # frontend/ingress resolves correctly — keep this one consistent.
        context={"effective_dates": emap, **card_context},
    ).data


def _build_user_payload(user, *, emap, anniversary_emap, card_context):
    """The user-owned half of the response, serialized, for merging over the
    cached guest payload. Every key here MUST also appear in
    _build_public_payload()'s response dict with its guest value, or a signed-in
    response and a guest one would carry different keys.

    Planned banners are the ACTIVE PLAN's. Purchases, step-up selections and
    stats belong to the account and are the same whichever plan is open --
    see models/plan.py for why the line falls there.
    """
    # Creates the account's first plan if it has none, so everything below can
    # assume one exists. A write on a GET, deliberately: see get_active_plan.
    active_plan = plans.get_active_plan(user)

    # Ordered by their campaign's resolved start so the planner renders
    # chronologically without re-deriving dates client-side.
    planned_purchases = sorted(
        UserPlannedPurchase.objects.filter(user=user).select_related(
            "product__anniversary_event"
        ),
        key=lambda pp: effective_sort_key(
            anniversary_emap.get(pp.product.anniversary_event_id)
        ),
    )
    # Ordered by the model's own Meta.ordering (banner, then slot) so the client
    # can render the ten slots without re-sorting. Cards are joined for nothing
    # here -- only ids are serialized -- so this stays a single query.
    step_up_selections = UserStepUpSelection.objects.filter(user=user)

    return {
        # Read AFTER get_active_plan so a first plan it just created is listed.
        "user_plans": PlanSerializer(
            Plan.objects.filter(user=user), many=True
        ).data,
        "active_plan_id": active_plan.id,
        "user_planned_banner_data": serialize_planned_banners(
            active_plan, emap=emap, card_context=card_context
        ),
        "user_planned_purchase_data": UserPlannedPurchaseSerializer(
            planned_purchases, many=True
        ).data,
        "user_step_up_selection_data": UserStepUpSelectionSerializer(
            step_up_selections, many=True
        ).data,
        "user_stats_data": UserStatsSerializer(user).data,
    }


class CalculatorViewSet(ViewSet):
    def get_permissions(self):
        # GET serves mostly reference data, so guests may read it; the PATCH
        # writes to request.user's rows and stays account-only.
        if self.action == "get_calculator_data":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    @action(detail=False, methods=["get"], url_path="calculator-data")
    def get_calculator_data(self, request):
        """The app's hot route: everything under /app blocks on it.

        Served from calculatorapi/public_payload_cache.py, because the expensive
        part of this response is identical for every visitor -- the catalogue
        below serializes ~900 nested cards twice over and none of it is
        per-user. On a cache hit neither branch runs a single catalogue query:
        a guest is answered straight from the cached JSON, and a signed-in user
        has only their own user-scoped keys read and merged over it.
        """
        cached_json = public_payload_cache.read()
        if cached_json is None:
            payload = self._build_public_payload()
            public_payload_cache.store(JSONRenderer().render(payload))
        else:
            # ~4ms for the megabyte, against the ~2s the cache just saved. A
            # guest could be answered with those bytes verbatim via HttpResponse
            # and skip even this, but that costs the endpoint its DRF Response
            # -- and with it .data, content negotiation, and one uniform shape
            # across both branches -- to save single-digit milliseconds.
            payload = json.loads(cached_json)

        if request.user.is_authenticated:
            emap, anniversary_emap, card_context = _build_user_context()
            payload.update(
                _build_user_payload(
                    request.user,
                    emap=emap,
                    anniversary_emap=anniversary_emap,
                    card_context=card_context,
                )
            )

        return Response(payload, status=status.HTTP_200_OK)

    def _build_public_payload(self):
        """Assemble the visitor-independent half of /calculator-data.

        Runs only on a cache miss. Everything it touches is content an admin
        edits, never anything about the requesting user -- which is what lets
        the result be shared by everyone until a write invalidates it.
        """
        # pylint: disable=too-many-locals
        #
        # This endpoint's job IS to assemble the whole payload: every local is a
        # named section of the response, and most carry a comment explaining
        # which anchor resolved its dates. Splitting it to satisfy a locals
        # count would scatter that reasoning across helpers that each run once,
        # in order, and would touch the app's most-used public route for no
        # behavioural gain.
        club_rank_data = ClubRank.objects.all()
        team_trials_rank_data = TeamTrialsRank.objects.all()
        champions_meeting_rank_data = ChampionsMeetingRank.objects.all()
        league_of_heroes_rank_data = LeagueOfHeroesRank.objects.all()

        # Resolve every timeline's effective (confirmed-or-predicted) global
        # dates once. Banner ordering now keys off these resolved dates rather
        # than a DB column, so we sort in Python (the sets are small). Champions
        # Meetings and League of Heroes events get their own maps — each content
        # type predicts against its own anchor, so rows are never mixed.
        # Resolved together in one call because schedule offsets DO span content
        # types (one shared calendar) — see predictions.apply_schedule_offsets.
        date_maps = build_effective_date_maps()
        emap = date_maps[BannerTimeline]
        cm_emap = date_maps[ChampionsMeeting]
        loh_emap = date_maps[LeagueOfHeroes]

        # prefetch_related on the M2M is what keeps this ONE query for every
        # card rather than one per banner: BannerUmaSerializer nests
        # `umas`/`support_cards`, so without it DRF walks the relation per row
        # and the endpoint costs ~340 extra round trips. Cheap on SQLite,
        # ~6ms each against the networked prod Postgres.
        banner_uma_data = sorted(
            BannerUma.objects.select_related("banner_timeline").prefetch_related("umas"),
            key=lambda b: effective_sort_key(emap.get(b.banner_timeline_id)),
        )
        banner_support_data = sorted(
            BannerSupport.objects.select_related("banner_timeline").prefetch_related(
                "support_cards"
            ),
            key=lambda b: effective_sort_key(emap.get(b.banner_timeline_id)),
        )
        # Sorted through the same emap as its two peers — a step-up dates itself
        # off an ordinary BannerTimeline (its campaign's Part 2), so it needs no
        # prediction of its own. anniversary_event is joined for the cutoff the
        # serializer folds in.
        banner_step_up_data = sorted(
            BannerStepUp.objects.select_related(
                "banner_timeline", "anniversary_event"
            ),
            key=lambda b: effective_sort_key(emap.get(b.banner_timeline_id)),
        )
        # Campaigns own no dates — resolved by spanning the BannerTimeline parts
        # they link to, reusing the emap above rather than predicting afresh.
        anniversary_event_data = AnniversaryEvent.objects.prefetch_related(
            "banner_links", "products"
        )
        anniversary_emap = build_anniversary_event_date_map(
            anniversary_event_data, emap
        )
        anniversary_event_data = sorted(
            anniversary_event_data,
            key=lambda ev: effective_sort_key(anniversary_emap.get(ev.id)),
        )
        # Scenarios borrow their launch banner's START and nothing else — they
        # have no end date at all, so they resolve through their own map rather
        # than the campaign one. select_related is not needed: the resolver reads
        # banner_timeline_id off the row and looks it up in the emap.
        scenario_data = Scenario.objects.all()
        scenario_emap = build_scenario_date_map(scenario_data, emap)
        scenario_data = sorted(
            scenario_data,
            key=lambda sc: effective_sort_key(scenario_emap.get(sc.id)),
        )

        # Selector eligibility keys off each card's earliest JP banner. Built
        # once here and handed to every serializer that nests a card, because
        # resolving it per card would be an N+1 across the whole catalogue.
        uma_first_jp_dates, support_first_jp_dates = build_first_jp_date_maps()
        card_context = {
            "uma_first_jp_dates": uma_first_jp_dates,
            "support_first_jp_dates": support_first_jp_dates,
        }

        events_data = GameEvent.objects.select_related("banner_timeline").all()
        # GameEvent has no dates of its own — resolved via the BannerTimeline
        # emap already built above (reusing it, not a new anchor computation).
        game_event_emap = build_game_event_date_map(events_data, emap)
        events_data = sorted(
            events_data,
            key=lambda ge: effective_sort_key(game_event_emap.get(ge.id)),
        )
        champions_meeting_data = sorted(
            ChampionsMeeting.objects.all(),
            key=lambda cm: effective_sort_key(cm_emap.get(cm.id)),
        )
        league_of_heroes_event_data = sorted(
            LeagueOfHeroes.objects.all(),
            key=lambda loh: effective_sort_key(loh_emap.get(loh.id)),
        )
        banner_timeline_data = sorted(
            BannerTimeline.objects.prefetch_related(
                # The nested serializers walk the JUNCTION rows (they need
                # `recommendation`, which lives on the through model), so it is
                # the junction sets that must be prefetched -- prefetching
                # "uma_banners" alone leaves the cards themselves an N+1.
                # select_related("uma") rides along inside the Prefetch so the
                # card arrives on the same query; it CANNOT go in the serializer,
                # because select_related() on a related manager builds a fresh
                # queryset and silently bypasses this cache.
                # -> BannerUmaNestedSerializer.get_umas in views/banner_timeline.py
                Prefetch(
                    "uma_banners__umasonumabanner_set",
                    queryset=UmasOnUmaBanner.objects.select_related("uma"),
                ),
                Prefetch(
                    "support_banners__supportsonsupportbanner_set",
                    queryset=SupportsOnSupportBanner.objects.select_related(
                        "support_card"
                    ),
                ),
                # Prefetched so the attached-campaign strip costs no extra query
                # per banner (195 rows would otherwise be 195 lookups).
                "anniversary_links__anniversary_event",
                # Same reason: the step-up chip would otherwise be one lookup
                # per timeline row.
                "step_up_banners",
            ),
            key=lambda t: effective_sort_key(emap.get(t.id)),
        )

        # The income ledger: every reward instant on one flat, date-sorted
        # timeline, which the projection queries for cumulative totals instead of
        # accruing income window by window. Built from the querysets and date maps
        # already in hand above — no extra queries, and no prediction of its own.
        income_ledger = build_income_ledger(
            game_events=events_data,
            game_event_emap=game_event_emap,
            race_sources=(
                (KIND_CHAMPIONS_MEETING, champions_meeting_data, cm_emap),
                (KIND_LEAGUE_OF_HEROES, league_of_heroes_event_data, loh_emap),
            ),
        )

        response = {
            "club_rank_data": ClubRankSerializer(club_rank_data, many=True).data,
            "team_trials_rank_data": TeamTrialsRankSerializer(team_trials_rank_data, many=True).data,
            "champions_meeting_rank_data": ChampionsMeetingRankSerializer(champions_meeting_rank_data, many=True).data,
            "league_of_heroes_rank_data": LeagueOfHeroesRankSerializer(league_of_heroes_rank_data, many=True).data,
            "banner_uma_data": BannerUmaSerializer(
                banner_uma_data, many=True,
                context={"effective_dates": emap, **card_context}
            ).data,
            "banner_support_data": BannerSupportSerializer(
                banner_support_data, many=True,
                context={"effective_dates": emap, **card_context}
            ).data,
            "banner_step_up_data": BannerStepUpSerializer(
                banner_step_up_data, many=True,
                context={"effective_dates": emap}
            ).data,
            # The guest values. A signed-in request overwrites all of them from
            # _build_user_payload() after this dict comes back out of the cache;
            # they are spelled out here so the CACHED bytes are already a
            # complete, correct guest response and can be returned untouched.
            # A guest has one unnamed in-memory plan, hence no list and no id.
            "user_plans": [],
            "active_plan_id": None,
            "user_planned_banner_data": [],
            "user_planned_purchase_data": [],
            "user_step_up_selection_data": [],
            "anniversary_event_data": AnniversaryEventSerializer(
                anniversary_event_data, many=True,
                context={"effective_dates": anniversary_emap}
            ).data,
            "scenario_data": ScenarioSerializer(
                scenario_data, many=True,
                context={"effective_dates": scenario_emap}
            ).data,
            "champions_meeting_data": ChampionsMeetingSerializer(
                champions_meeting_data, many=True, context={"effective_dates": cm_emap}
            ).data,
            "league_of_heroes_event_data": LeagueOfHeroesSerializer(
                league_of_heroes_event_data, many=True, context={"effective_dates": loh_emap}
            ).data,
            "events_data": GameEventSerializer(
                events_data, many=True, context={"effective_dates": game_event_emap}
            ).data,
            "income_ledger": IncomeLedgerRowSerializer(income_ledger, many=True).data,
            # Every tunable number the projection uses. Served on each request so
            # an admin edit takes effect on the next page load without a deploy.
            "calculation_constants": CalculationConstantsSerializer(
                CalculationConstants.load()
            ).data,
            "user_stats_data": None,
            "banner_timeline_data": BannerTimelineForViewingSerializer(
                banner_timeline_data, many=True,
                context={"effective_dates": emap, **card_context}
            ).data,
        }

        return response

    @action(detail=False, methods=["patch"], url_path="calculator-data")
    def update_calculator_data(self, request):
        # Wrap everything in a transaction so a mid-update failure doesn't leave
        # stats saved but banners half-updated (or vice versa).
        #
        # set_rollback is load-bearing: `return`ing a Response from inside an
        # atomic block exits the context manager NORMALLY, so Django commits.
        # Without this the rejected half of a partly-invalid PATCH would be
        # rolled back but the accepted half would persist -- which is exactly
        # the state this transaction exists to prevent.
        with transaction.atomic():
            error = self._apply_updates(request)
            if error is not None:
                transaction.set_rollback(True)
                return error

        return Response({"message": "Data updated successfully"}, status=status.HTTP_200_OK)

    @staticmethod
    def _apply_updates(request):
        """Apply every section of the PATCH body. Returns an error Response or None.

        Order matters only in that stats are cheapest to validate; all three
        sections stand or fall together via the caller's transaction.
        """
        user = request.user

        # WHICH PLAN the banner rows belong to. The body says; the server never
        # guesses for a client that knows about plans. _replace_user_rows
        # deletes every row the body does not name, and auto-save fires five
        # seconds after the last edit -- so "whatever plan is active when the
        # save arrives" would let someone who edits plan A and switches to B
        # inside that window have A's rows written over B's.
        #
        # An ABSENT plan_id means the active plan. That exists for one case: a
        # tab left open across the deploy that introduced plans. Its bundle has
        # never heard of them and the account then has exactly one.
        plan_id = request.data.get("plan_id")
        try:
            plan = (
                plans.get_active_plan(user) if plan_id is None
                else plans.get_owned_plan(user, plan_id)
            )
        except Plan.DoesNotExist:
            return Response({"error": "Plan not found"},
                            status=status.HTTP_404_NOT_FOUND)

        user_stats_data = request.data.get("user_stats_data")
        if user_stats_data:
            serializer = UserStatsSerializer(user, data=user_stats_data, partial=True)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            serializer.save()

        # Snapshotted BEFORE any collection is written, because
        # _replace_user_rows deletes the user's existing selections before
        # recreating them -- read it afterwards and every pick would look new.
        # See UserStepUpSelectionSerializer._validate_eligibility for why
        # grandfathering the stored set is what stops a corrected cutoff from
        # 400ing an untouched plan.
        selection_context = None
        if request.data.get("user_step_up_selection_data") is not None:
            selection_context = {
                "stored_pairs": _stored_selection_pairs(user),
                "first_jp_dates": build_first_jp_date_maps(),
            }

        # Only banners are scoped to the plan. Purchases and selections are the
        # account's, the same under every plan, so they reconcile against all
        # of the user's rows exactly as before.
        collections = (
            ("user_planned_banner_data", UserPlannedBanner,
             UserPlannedBannerSerializer, "Banner not found", None, plan),
            ("user_planned_purchase_data", UserPlannedPurchase,
             UserPlannedPurchaseSerializer, "Purchase not found", None, None),
            ("user_step_up_selection_data", UserStepUpSelection,
             UserStepUpSelectionSerializer, "Step-up selection not found",
             selection_context, None),
        )
        for key, model, serializer_class, missing_message, context, scope_plan \
                in collections:
            error = _replace_user_rows(
                request.data.get(key),
                model=model,
                serializer_class=serializer_class,
                user=user,
                missing_message=missing_message,
                context=context,
                plan=scope_plan,
            )
            if error is not None:
                return error

        # auto_now only fires on save(), and the rows above were written through
        # their own serializers. Stamp the plan so "last edited" means its rows.
        if request.data.get("user_planned_banner_data") is not None:
            plan.save(update_fields=["updated_at"])

        return None
