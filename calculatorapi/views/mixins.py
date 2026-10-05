from rest_framework import serializers


# One shared DateTimeField instance to reproduce DRF's exact ISO wire format
# (e.g. "2025-06-26T22:00:00Z") when serializing resolved (Python-computed) dates.
_DT = serializers.DateTimeField()


class _ResolvedDateMixin(serializers.Serializer):
    """
    Emits RESOLVED dates under the existing `start_date`/`end_date` field
    names, plus an `is_predicted` flag, by looking up this row's id in the
    `effective_dates` map passed via serializer context. Keying by id (not
    queryset position) is what keeps every serialization path consistent for
    a given row.

    `applied_offset_days` reports how many days of accumulated schedule offset
    are already baked into the dates above (see predictions.apply_schedule_offsets).
    It's diagnostic only — the dates are complete without it — but a cascading
    rule is near-impossible to debug from the outside without it.

    Subclasses provide `_fallback(obj)` for the no-context-map case — the
    shape differs per model (some models can read their own confirmed date
    fields as a fallback, others can't), so it's not implemented here.
    """

    start_date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()
    is_predicted = serializers.SerializerMethodField()
    applied_offset_days = serializers.SerializerMethodField()

    def _entry(self, obj):
        emap = self.context.get("effective_dates")
        if emap is not None and obj.id in emap:
            return emap[obj.id]
        return self._fallback(obj)

    def _fallback(self, obj):
        raise NotImplementedError

    def get_start_date(self, obj):
        value = self._entry(obj)["start_date"]
        return _DT.to_representation(value) if value is not None else None

    def get_end_date(self, obj):
        value = self._entry(obj)["end_date"]
        return _DT.to_representation(value) if value is not None else None

    def get_is_predicted(self, obj):
        return self._entry(obj)["is_predicted"]

    def get_applied_offset_days(self, obj):
        return self._entry(obj)["applied_offset_days"]


class EffectiveDateMixin(_ResolvedDateMixin):
    """
    Model-agnostic: reused by BannerTimeline, ChampionsMeeting, and
    LeagueOfHeroes serializers. The fallback reads `obj.global_start_date`/
    `obj.global_end_date`, so any model using those field names is safe when
    serialized standalone (no map).
    """

    def _fallback(self, obj):
        # No map means no prediction, which also means no offset was applied.
        return {
            "start_date": obj.global_start_date,
            "end_date": obj.global_end_date,
            "is_predicted": False,
            "applied_offset_days": 0,
        }


class EventTypeMixin(serializers.Serializer):
    """
    Emits a constant `event_type` tag so the timeline's three content types form
    a real discriminated union on the wire.

    The frontend merges BannerTimeline, ChampionsMeeting and LeagueOfHeroes into
    one sorted array and has to narrow each element back to its own type. It used
    to do that structurally ("track" in event), which silently breaks the moment
    two models' field sets converge — exactly what happened when LeagueOfHeroes
    gained ChampionsMeeting's course details. A tag the backend owns can't drift.

    Subclasses set `event_type_value`; it must stay in sync with the string
    literals on the matching TypeScript interfaces in frontend/src/types/.
    """

    event_type = serializers.SerializerMethodField()

    #: Overridden per serializer. None here would be a bug, not a valid tag.
    event_type_value = None

    # The instance is deliberately ignored: the tag identifies the *serializer*,
    # not anything about the row.
    def get_event_type(self, _obj):
        return self.event_type_value


class FirstJpDateMixin(serializers.Serializer):
    """
    Emits `first_jp_date` — the earliest JP banner this card appeared on, which
    is the key selector eligibility is judged on (see calculatorapi/eligibility.py).

    It reads a prebuilt {id: date} map from serializer context rather than
    annotating the queryset, because these serializers are nested several levels
    deep inside banners and planned banners; an annotation would have to be
    threaded through every one of those querysets, and a per-object lookup would
    be an N+1 across every card on every banner. One map, built once per request
    by build_first_jp_date_maps(), covers all of them.

    Emits null when no map is in context (standalone use, e.g. the admin) or when
    the card has never appeared on a banner. Consumers must treat null as
    "unknown", not "ancient" — eligibility.is_eligible() refuses it under a real
    cutoff for exactly that reason.

    Subclasses set `context_key`, since umas and support cards get separate maps.
    """

    first_jp_date = serializers.SerializerMethodField()

    #: Overridden per serializer.
    context_key = None

    def get_first_jp_date(self, obj):
        value = (self.context.get(self.context_key) or {}).get(obj.id)
        return _DT.to_representation(value) if value is not None else None


class GameEventDateMixin(_ResolvedDateMixin):
    """
    GameEvent has no global_start_date/global_end_date of its own — its dates
    come entirely from its linked BannerTimeline via a context map built by
    build_game_event_date_map/build_game_event_confirmed_date_map. If ever
    serialized without that context, fail safe to unresolved dates rather
    than raising (there's no meaningful per-object fallback to compute here).
    """

    def _fallback(self, obj):
        return {"start_date": None, "end_date": None, "is_predicted": False,
                "applied_offset_days": 0}


class AnniversaryEventDateMixin(GameEventDateMixin):
    """
    GameEventDateMixin plus `main_start_date` — the start of the part that IS the
    campaign's event, as opposed to `start_date`, which is when the campaign
    first opens.

    The two differ for anniversaries, which run a Part 1 of login rewards before
    the anniversary proper (predictions._main_part explains the selection). Both
    are on the wire because they answer different questions: consumers placing a
    landmark on a calendar, or crediting a purchase, want main_start_date;
    consumers describing the campaign's whole window want start_date/end_date.

    Inherits the no-context-map fallback and extends it with the new key, so a
    standalone serialization still emits every field the type promises.
    """

    main_start_date = serializers.SerializerMethodField()

    def get_main_start_date(self, obj):
        value = self._entry(obj)["main_start_date"]
        return _DT.to_representation(value) if value is not None else None

    def _fallback(self, obj):
        return {**super()._fallback(obj), "main_start_date": None}


class StartInstantDateMixin(_ResolvedDateMixin):
    """
    A single dated instant borrowed from a BannerTimeline, with no end — used by
    Scenario and DailyLegendRaceRelease, the two models whose dates are a start
    and nothing else.

    `end_date` is REMOVED from the wire rather than emitted as a permanent null.
    A field that is structurally always null invites a consumer to render a range
    that doesn't exist, and a scenario genuinely has no end (it stays available
    forever once released). Reassigning an inherited declared field to None is
    DRF's own idiom for dropping it: SerializerMetaclass skips any base-class
    declared field whose name is redefined in the subclass. That also means
    `end_date` must NOT appear in a subclass's Meta.fields, or DRF raises.

    The inherited get_end_date is dead here and live for the other two mixins;
    it stays on _ResolvedDateMixin.

    Fails safe to unresolved dates when serialized with no context map, as
    GameEventDateMixin does — there's no per-object fallback to compute.
    """

    end_date = None

    def _fallback(self, obj):
        return {"start_date": None, "end_date": None, "is_predicted": False,
                "applied_offset_days": 0}
