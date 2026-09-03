"""The view model and per-client state for `ui_app.py`.

`SlotView` is one flattened cell of the canvas; `PlannerState` is everything
one browser tab is looking at. Split out of the monolithic page module so a
widget module (cards, canvas, drawer) can depend on the *shape* of state
without depending on the `ui.page` handler that builds the DOM around it.
Nothing in this module makes a `ui.*` call — every method here either
computes and returns a value or mutates `self`, and the caller (still in
`ui_app.py` today) is what repaints in response. That split already held in
the monolith; this file just draws the module boundary where it already was.
"""

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import ValidationError

from freezer import FreezerItem
from planner import (
    LOCATION_RESTRICTION_PHRASES,
    MACRO_KEYS,
    NUTRIENT_KEYS,
    SUNDAY_PREP_REHEAT_MINUTES,
    TRAINING_NOTE_PREFIXES,
    ADHERENCE_EATEN,
    ADHERENCE_SKIPPED,
    ADHERENCE_STATUS_LABELS,
    ADHERENCE_SWAPPED,
    AdherenceEntry,
    CookEvent,
    Recipe,
    WeekPlan,
    WorkoutCompletion,
    TARGET_MODE_AUTO,
    TARGET_MODE_MACROS,
    TARGET_MODE_MANUAL,
    apply_training_adjustments,
    calculate_daily_targets,
    hydrate_dynamic_targets,
    day_multiplicity,
    inventory_entries,
    is_prepped_ahead,
    is_sunday_prepped,
    apply_preset_layer,
    resolve_preset_layer,
    load_app_config,
    meal_overrides_for,
    recipe_eligibility_error,
    resolve_planner_model,
    single_serving,
    split_targets,
    storage_spans,
    week_shape_errors,
    weeknight_prep_minutes,
    workout_session_id,
)
# `apply_training_adjustments` is its real owner; it is the tolerant "HH:MM"
# parse a drawer's free-text time field needs. Shared rather than
# reimplemented so the Today tab orders a day's sessions by the same clock
# reading that decides which meal gets the post-workout pin; a second parser
# is a second answer to "what time is `7:3o`?".
from planner import clock_minutes
import presets as preset_layer
from nutrition_engine import (
    ADAPTIVE_NO_LOGS,
    MIN_TREND_SPAN_DAYS,
    ADAPTIVE_NO_WEIGH_INS,
    ADAPTIVE_SHORT_SPAN,
    ADAPTIVE_TDEE_TOLERANCE,
    MIN_PROPOSAL_OCCURRENCES,
    PROPOSAL_DROP,
    TRAINING_PROPOSAL_NO_ACTIVITY,
    TRAINING_PROPOSAL_SHORT_HISTORY,
    AdaptiveTDEEStatus,
    ProposedSession,
    SessionMatch,
    TrainingScheduleProposal,
    estimate_session_burn_kcal,
    match_recorded_sessions,
    measure_adaptive_tdee,
    measure_weight_trend,
    propose_training_schedule,
    resolve_current_weight_kg,
)
from shopping import ShoppingList, aggregate_cook_events, apply_pantry
from repository import BIOMETRIC_SECTION_SOURCES, LocalJSONRepository, catalog_matches
from ui_theme import (
    ADHERENCE_MARK_ORDER,
    LINK_COLOURS,
    LINK_SOURCE_MEAL,
    LINK_TARGET_MEAL,
    MACRO_DETAIL_LABELS,
    STATUS_COOK,
    STATUS_LEFTOVER,
    STATUS_MISSING,
    STATUS_SKIP,
    SYNC_CHECKED,
    SYNC_FRESH_CURRENT,
    SYNC_FRESH_NEVER,
    SYNC_FRESH_STALE,
    SYNC_RECORDED,
    SYNC_SECTION_LABELS,
    SYNC_UNCHECKED,
    TRAINING_TYPE_LABELS,
    TRAINING_TYPES,
    WEEK_SEQUENCE,
    macro_band,
    pluralize,
)
from week import (
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    PIN_ORIGIN_USER,
    SlotSpec,
    WeekSpec,
    apply_week_shape,
    clear_cuisines,
    clear_styles,
    cook_day_index,
    day_date,
    default_week_spec,
    eaten_on,
    fridge_day_gaps,
    humanize,
    leftover_link_error,
    link_leftover,
    location_for,
    location_rule,
    meal_types,
    next_day_slot_id,
    parse_slot_id,
    pin_recipe,
    portions_for,
    resolve_prep_day,
    set_skip_estimate,
    slot_id,
    slot_label,
    shopping_windows,
    span_days,
    today_in_week,
    unlink_leftover,
    week_days,
)


@dataclass
class SlotView:
    """One cell of the canvas, flattened for rendering.

    The grid renders from these rather than branching on `WeekPlan` vs.
    `WeekSpec` at every label: a generated week and an un-generated one differ
    only in whether `recipe` is set, so both paths build the same shape and the
    card widget stays single-purpose.
    """

    day: str
    meal_type: str
    status: str
    title: str
    mode: str = MODE_SKIP
    style: str = ""
    cuisine: str = ""
    portions: int = 0
    prep_minutes: Optional[int] = None
    macros: Optional[dict] = None
    source_label: str = ""
    # The raw slot_id behind `source_label`, which is a display string ("Mon
    # dinner"). The unlink action needs the real id to report what the source
    # batch shrank to, and re-parsing a humanized label to get it back would
    # be a second, lossy encoding of something already known here.
    source_id: str = ""
    prep_badge: str = ""  # "fridge" | "freezer" | "" — see PREP_BADGE_STYLES
    prep_origin: str = ""  # tooltip: where in the Sunday prep timeline this came from
    recipe: object = None  # planner.Recipe, kept loose to avoid a hard import cycle
    # `SlotSpec.extra_portions` verbatim — 0 for every slot but a batch anchor
    # with spare portions declared. Carried here so a card can offer "Record
    # N to freezer" without re-deriving the spec; see `pending_freezer_surplus`.
    extra_portions: int = 0

    # --- leftover chain wiring ---
    # `chain` is the shared index of the cook-plus-its-leftovers group this
    # card belongs to; both ends of a link carry the same one, which is what
    # lets the card colour and the hover outline tie them together visually.
    chain: Optional[int] = None
    feeds: List[str] = field(default_factory=list)  # cook cards: who eats this
    link_target: str = ""  # slot id "Link to next lunch" would point at us
    link_error: str = ""  # why that link isn't available, if it isn't

    # Skipped slots only: the macros this meal is eaten out at, or None for a
    # meal genuinely not eaten. Distinct from `macros`, which is always a
    # recipe's — nothing was cooked here, so the two must not share a field or
    # the card would render an estimate as though a recipe backed it.
    skip_estimate: Optional[dict] = None

    @property
    def id(self) -> str:
        return slot_id(self.day, self.meal_type)

    @property
    def chain_colour(self) -> str:
        return LINK_COLOURS[(self.chain or 0) % len(LINK_COLOURS)]


@dataclass
class TrainingView:
    """One scheduled session, flattened for display.

    Sourced from `PlannerState.training_schedule` — the drawer's live copy —
    rather than from config, so a session added or retimed in the drawer shows
    on the Today tab immediately, the same "live preview of the next run"
    contract `targets_for` already honours for target edits.

    `is_rest` is kept as its own flag rather than left for the caller to infer
    from the type string, because a rest entry is genuinely different in kind:
    `apply_training_adjustments` skips it, so it expands no budget and pins no
    meal. It is worth showing (an explicitly scheduled rest day is
    information) but it must not read as a session that bought calories back.
    """

    day: str
    time: str
    type: str
    label: str
    duration_minutes: int
    burn_kcal: float
    is_rest: bool


@dataclass
class LocationView:
    """Where a day is spent and what that constrains.

    `meal_modes` is the location's `<meal_type>_mode` keys, and it is also the
    exact scope of `restrictions` — per `planner.build_location_note`, a
    location only constrains the meals it declares a mode for. Reusing that
    one key as the scope here, rather than deciding again in the UI which
    meals a restriction covers, is what keeps the badge on a card and the
    clause in the prompt from ever disagreeing about a Monday breakfast eaten
    at home before leaving for the office.
    """

    name: str
    restrictions: List[str] = field(default_factory=list)
    notes: str = ""
    max_prep_minutes: Optional[int] = None
    meal_modes: Dict[str, str] = field(default_factory=dict)
    # `<meal_type>_skip_estimate` for the meals this location skips — what is
    # eaten instead, per `week.apply_location_modes`, which stamps exactly this
    # onto the slot. Kept here rather than left for a caller to re-read off
    # `location_rules` because `meal_modes` above already comes off that rule
    # in this one place, and a second reader of the same entry is a second
    # chance to disagree with it about which meals a location actually skips.
    # Empty for every location that skips nothing, which is most of them.
    skip_estimates: Dict[str, dict] = field(default_factory=dict)

    def constrains(self, meal_type: str) -> bool:
        return meal_type in self.meal_modes

    @property
    def phrase_pairs(self) -> List[Tuple[str, str]]:
        """(tag, prose) for each restriction the model is actually told about.

        `LOCATION_RESTRICTION_PHRASES` rather than a humanized tag: the tag is
        a config token ("portable"), and the prose is what it actually means.
        A tag with no phrase is dropped here exactly as `build_location_note`
        drops it, so a tooltip can't claim a constraint the prompt never sent.

        Paired rather than left as two parallel lists precisely *because* of
        that drop: a caller wanting both — a chip labelled with the tag,
        hovering to the prose — cannot zip `restrictions` against a filtered
        `phrases` without silently pairing the wrong two the moment one tag
        goes unrecognised.
        """
        return [
            (tag, LOCATION_RESTRICTION_PHRASES[tag])
            for tag in self.restrictions
            if tag in LOCATION_RESTRICTION_PHRASES
        ]

    @property
    def phrases(self) -> List[str]:
        return [phrase for _, phrase in self.phrase_pairs]

    def brief(self, meal_type: str) -> str:
        """What `planner.build_location_note` tells the model about this meal.

        The same three conditions and the same prose, minus the "[Eaten at X:
        ...]" wrapper, since a card showing this already names the location
        beside it. "" when the location says nothing about this meal type, or
        says only that its mode changed — a mode already reshaped the grid in
        `week.apply_location_modes`, and restating it would describe the card
        back to the person looking at it.
        """
        if not self.constrains(meal_type):
            return ""
        return " ".join(
            part for part in ["; ".join(self.phrases), self.notes] if part
        )


@dataclass
class TrainingNote:
    """A `training_notes` entry, split into what it is and what it says.

    `kind` is "post" or "pre" and becomes the badge; `text` is the *reason*
    with the prefix and brackets taken off, which is the model's own sentence
    rather than a UI paraphrase of it. Splitting them is what stops a tooltip
    reading "POST-WORKOUT MEAL: ..." under a badge already saying
    POST-WORKOUT. The classification tests `planner.TRAINING_NOTE_PREFIXES`
    instead of matching the wording, so rewording the prompt can't silently
    drop the badge.
    """

    kind: str
    text: str


@dataclass
class DayContext:
    """Everything today's location and training say about a day's meals.

    One object built once per repaint rather than three lookups per card:
    `training_notes` is only reachable through `planning_config()`, which runs
    `apply_training_adjustments` over the whole week, and calling that four
    times to label four cards would be four copies of the same work.
    """

    location: Optional[LocationView]
    sessions: List[TrainingView]
    meal_notes: Dict[str, TrainingNote]

    @property
    def active_sessions(self) -> List[TrainingView]:
        return [session for session in self.sessions if not session.is_rest]

    @property
    def total_burn_kcal(self) -> float:
        return sum(session.burn_kcal for session in self.active_sessions)


def _now_iso() -> str:
    """The `marked_at` stamp both mark methods write.

    UTC and second-resolution, matching `ui_generation`'s rejection stamp —
    the two are adjacent records of "the user said something at this moment"
    and a reader comparing them should not have to reconcile two conventions.
    Nothing reads it back today; it exists so a future audit of a mark can
    tell a considered answer from a stray click, which needs the field to
    have been there all along.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MealAdherenceView:
    """One day's meal marks, and whether the day can carry any.

    **`date_iso` is what makes a day markable at all**, and it is legitimately
    absent: a `slot_id` is a weekday name (`"Thursday:dinner"`), which repeats
    every seven days, so without the plan's `week_start_date` there is no key
    to file a mark under and no way to read one back. That is the same
    pre-migration tolerance `logged_actuals_for` already draws for a plan
    generated before that field existed, reached from the other direction —
    there it costs a readout, here it costs the affordance.

    `planned` is the slots there is something to adhere to. A skipped slot is
    excluded deliberately: nothing was planned to be eaten, so "did you eat
    it" has no answer, and counting it in a denominator would make every week
    with a skipped snack read as permanently 3/4 adhered.
    """

    date_iso: Optional[str]
    statuses: Dict[str, str] = field(default_factory=dict)
    planned: Tuple[str, ...] = ()

    @property
    def markable(self) -> bool:
        return self.date_iso is not None

    def status_for(self, slot: str) -> Optional[str]:
        return self.statuses.get(slot)

    def label_for(self, slot: str) -> Optional[str]:
        status = self.statuses.get(slot)
        return ADHERENCE_STATUS_LABELS.get(status) if status else None

    @property
    def marked(self) -> int:
        return sum(1 for slot in self.planned if slot in self.statuses)

    @property
    def eaten(self) -> int:
        return sum(
            1 for slot in self.planned if self.statuses.get(slot) == ADHERENCE_EATEN
        )

    @property
    def summary(self) -> str:
        """One line for the day, or "" when there is nothing worth saying.

        Silent until something is marked, rather than printing "0 of 4
        marked" on every unmarked day — the whole week is unmarked until
        somebody starts, and a counter that reads zero on six days out of
        seven is a UI element announcing that a feature exists rather than
        reporting anything. Same "say nothing" default `context_strip` takes
        for a day with no location and no session.
        """
        if not self.planned or not self.marked:
            return ""
        parts = [f"{self.marked} of {len(self.planned)} marked"]
        if self.eaten != self.marked:
            # Only when the two differ: with every mark an "eaten", the second
            # half would restate the first.
            parts.append(f"{self.eaten} as planned")
        return " · ".join(parts)


@dataclass
class WorkoutMarkView:
    """One declared session, and the two independent ways it can be done.

    `recorded` comes from Garmin and `marked` from a person, and they are
    kept apart rather than folded into one boolean because only the second is
    stored — see `planner.WorkoutCompletion`. A session the watch saw needs
    no row and must never get one, so the button is offered only where
    `recorded` is False; `done` is what the tick actually keys off.

    The three `recorded_*` figures ride through from `SessionMatch` so the
    strip can say *what* the watch saw rather than only that it saw
    something — a 20-minute walk answering for a declared hour is exactly the
    case where the evidence matters more than the verdict.
    """

    session_id: str
    time: str
    session_type: str
    label: str
    recorded: bool
    marked: bool
    markable: bool
    recorded_start: Optional[str] = None
    recorded_minutes: Optional[float] = None
    recorded_kcal: Optional[float] = None

    @property
    def done(self) -> bool:
        return self.recorded or self.marked

    @property
    def source(self) -> str:
        """Which of the two answered, or "" for a session nothing has.

        Garmin wins when both say yes. That is not a preference between the
        sources so much as an ordering: a manual mark is only ever written
        for a session the watch had not recorded, so the pair can only
        co-occur when a later re-sync found the session after the fact — and
        at that point the watch's own record is the better evidence, and the
        stale manual row is the one that should stop being cited.
        """
        if self.recorded:
            return "garmin"
        return "manual" if self.marked else ""

    @property
    def detail(self) -> str:
        """What the watch recorded, in its own units, or "" for no recording."""
        if not self.recorded:
            return ""
        parts = [
            part
            for part in [
                self.recorded_start or "",
                f"{self.recorded_minutes:.0f} min" if self.recorded_minutes else "",
                f"{self.recorded_kcal:.0f} kcal" if self.recorded_kcal else "",
            ]
            if part
        ]
        return " · ".join(parts)


def meal_adherence_view(
    rows: List[dict],
    date_iso: Optional[str],
    planned: Tuple[str, ...],
) -> MealAdherenceView:
    """`adherence.json`'s `meals` rows for one date, as the view above.

    Pure and date-matched, never weekday-matched — the same distinction
    `PlannerState.logged_actuals_for` documents against
    `planner.logged_intake_for`. Last row wins for a duplicated key, matching
    that function: `_upsert_adherence` keeps one row per `date`+`slot_id`, so
    a second is only reachable in a hand-edited file where the later line is
    the edit.
    """
    if date_iso is None:
        return MealAdherenceView(date_iso=None, statuses={}, planned=planned)
    statuses = {}
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("date") or "")[:10] != date_iso:
            continue
        slot = str(row.get("slot_id") or "")
        status = str(row.get("status") or "")
        if slot and status:
            statuses[slot] = status
    return MealAdherenceView(date_iso=date_iso, statuses=statuses, planned=planned)


def workout_marks_view(
    sessions: List["TrainingView"],
    activity_log: list,
    rows: List[dict],
    date_iso: Optional[str],
) -> List[WorkoutMarkView]:
    """Each of `sessions`, against what the watch recorded and what was marked.

    The fold `nutrition_engine.match_recorded_sessions` deliberately does not
    do: that function is pure over two lists and knows nothing about storage,
    and this is where a stored manual mark is laid over its answer — the same
    layering `sync_status` and `adaptive_tdee_view` already use, where the
    engine measures and the view model decides what a reader is told.

    A day with no `date_iso` yields sessions that are neither recorded nor
    markable: without a real calendar date there is nothing to match the
    activity log against and no key to file a mark under, and reporting them
    as "not done" would state as fact something never actually checked.
    """
    if date_iso is None:
        return [
            WorkoutMarkView(
                session_id=workout_session_id(session.time, session.type),
                time=session.time,
                session_type=session.type,
                label=session.label,
                recorded=False,
                marked=False,
                markable=False,
            )
            for session in sessions
        ]

    matches = match_recorded_sessions(
        activity_log,
        [{"time": session.time, "type": session.type} for session in sessions],
        date_iso,
    )
    marked = {
        str(row.get("session_id") or "")
        for row in rows or []
        if isinstance(row, dict)
        and str(row.get("date") or "")[:10] == date_iso
        and row.get("completed")
    }
    return [
        WorkoutMarkView(
            session_id=match.session_id,
            time=session.time,
            session_type=session.type,
            label=session.label,
            recorded=match.recorded,
            marked=match.session_id in marked,
            markable=True,
            recorded_start=match.recorded_start,
            recorded_minutes=match.recorded_minutes,
            recorded_kcal=match.recorded_kcal,
        )
        for session, match in zip(sessions, matches)
    ]


@dataclass
class PendingChange:
    """One line of the staged-changes bar/review dialog's "N pending changes".

    Just a label — `pending_changes()` is the only producer, so there's
    nothing else a consumer needs to branch on. Kept as a real type rather
    than a bare string so a future consumer (grouping by category, say) has
    somewhere to add a field without every call site's tuple shape changing.
    """

    summary: str


def pantry_rows(config: dict) -> List[Dict[str, Any]]:
    """`inventory_to_clear` as the drawer's editable rows.

    One parse of that list, shared by `.load()` and `discard_pending_inputs`,
    so a seeded pantry and a discarded one cannot disagree about what the file
    said. `planner.inventory_entries` is the parser — the same one generation
    reads the list through, which is what keeps the drawer from showing a row
    the ledger would drop, or dropping one it would keep.

    Always rows, even for a config written before quantities existed: a bare
    string becomes `{"item": ..., "quantity_g": None}`, which
    `inventory_entries` reads straight back as the unquantified item it was.
    """
    return [
        {"item": name, "quantity_g": quantity}
        for name, quantity in inventory_entries(config)
    ]


# Which `PlannerState` field each config value seeds, and how to read it.
#
# One table rather than a list of kwargs in `.load()` and a second list in
# `set_preset`, because those two must not drift: a field seeded at load and
# forgotten on a pick change is a preset that *appears applied and is not* —
# the failure the whole preset layer is shaped to refuse. `model` is
# deliberately absent (it reads `models.json`, which no preset can touch) and
# so is `weight_kg` (a fact about the body, not the config).
PRESET_SEEDED_FIELDS = (
    ("target_modes", lambda config: dict(config["target_modes"])),
    ("week_start", lambda config: config["week_start_day"]),
    ("servings", lambda config: config["serving_rules"]["servings_per_meal"]),
    ("shop_days", lambda config: list(config["shopping"]["shop_days"])),
    ("pantry", pantry_rows),
    (
        "training_schedule",
        lambda config: [dict(session) for session in config["training_schedule"]],
    ),
    (
        "baseline_cuisine_share",
        lambda config: config["planning_rules"]["min_baseline_cuisine_share"],
    ),
)

# The config keys `week.default_week_spec` reads that nothing in
# `PRESET_SEEDED_FIELDS` already covers. A preset moving one of these changes
# which slots are cooked, so the cached preview grid has to be rebuilt — the
# same invalidation `_spec_shape` performs for the two it does cover.
PRESET_GRID_SHAPE_KEYS = ("week_defaults", "meal_types", "base_schedule", "location_rules")


# --------------------------------------------------------------------------
# The preset editor's field list (PROMPT-9)
# --------------------------------------------------------------------------
#
# One descriptor per editor-managed override. `PRESET_EDITOR_FIELDS` is the
# single authority the widget module, the preview and `save_preset` all read,
# so none can disagree about what "the editor manages" — which matters,
# because `save_preset` clears every managed path off a preset before writing
# the user's choices back, and a path it does not know about would be dropped
# on every save (or, if the widget drew one this list omits, silently
# preserved forever).
#
# Deliberately bounded to preset keys that already have a config home and a
# clean widget shape. design-01 §9.2 lists more — the prep-ceiling constants,
# the long-cook threshold (one number in four prose copies), the numbers
# welded into `DINNER_VARIETY_RULE`/`PORTION_DENSITY_GUARD`, the training
# constants, `meal_styles`, `meal_overrides` — but each of those needs a code
# change first and is filed in CHANGE-QUEUE.md items 7-9. Every ❌ row is a
# later release, exactly as PROMPT-7 intended. `week_shape` was one of them
# until Task 1.2d gave it a code home (`planner.week_shape_errors`/
# `week.apply_week_shape`) and its own list-of-records field below.
#
# Every field renders **unset** and may be ignored: an absent override means
# exactly today's behaviour (design-03 §4.1), which is what keeps the empty
# preset the identity and a preset from being forced to have an opinion about
# a key.

PRESET_FIELD_INT = "int"
PRESET_FIELD_FLOAT = "float"
PRESET_FIELD_INT_LIST = "int_list"        # a text field parsed to [int]
PRESET_FIELD_MULTI_INT = "multi_int"      # multi-select of ints
PRESET_FIELD_MULTI_STR = "multi_str"      # multi-select of catalog keys
PRESET_FIELD_ENUM_OBJECT = "enum_object"  # {sub-key: one of `choices`}
PRESET_FIELD_NUMBER_OBJECT = "number_object"  # {sub-key: a number}
PRESET_FIELD_DAY_CARBS = "day_carbs"      # one number per weekday, each its own leaf
# `{"batches": [...], "freezer_draws": [...]}`, replaced whole — Task 1.2d.
# Not an `enum_object`/`number_object`: each list holds independently
# addable/removable records, not a fixed set of sub-keys, so it draws through
# its own `ui_presets.render_week_shape` rather than `render_object`.
PRESET_FIELD_WEEK_SHAPE = "week_shape"

# The kinds whose override value is a single object leaf replaced whole —
# `save_preset`/`preview` treat them as one path, and the editor shows an
# "Override this" switch (off = not in `overrides`). `PRESET_FIELD_WEEK_SHAPE`
# shares the save/seed machinery (whole-leaf, switch-gated) but not the
# generic sub-key rendering — see `render_field`'s dispatch in `ui_presets.py`.
PRESET_OBJECT_KINDS = (PRESET_FIELD_ENUM_OBJECT, PRESET_FIELD_NUMBER_OBJECT, PRESET_FIELD_WEEK_SHAPE)


@dataclass(frozen=True)
class PresetField:
    """One editable dimension of a preset. `path` is the override leaf; the
    empty string marks `PRESET_FIELD_DAY_CARBS`, which expands to one
    `weekly_schedule.<day>.net_carbs_g` leaf per day at render time."""

    key: str
    path: str
    kind: str
    label: str
    help: str = ""
    advanced: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    # `PRESET_FIELD_MULTI_INT` / `PRESET_FIELD_ENUM_OBJECT` value set.
    choices: Tuple = ()
    # `PRESET_FIELD_NUMBER_OBJECT` / `PRESET_FIELD_ENUM_OBJECT` sub-keys, when
    # they are not read from config. "meal_types" means "one per meal type".
    subkeys: Tuple[str, ...] = ()
    subkey_source: str = "static"


PRESET_EDITOR_FIELDS: Tuple[PresetField, ...] = (
    PresetField(
        "allowed_nova_groups", "dietary_rules.allowed_nova_groups",
        PRESET_FIELD_MULTI_INT, "Processing levels allowed",
        "NOVA groups 1-4. Group 4 (ultra-processed) is always rejected whatever "
        "is ticked here; a comfort week might allow 4, a strict week only 1-2.",
        choices=(1, 2, 3, 4),
    ),
    PresetField(
        "active_diet_styles", "dietary_rules.active_diet_styles",
        PRESET_FIELD_MULTI_STR, "Diet styles",
        "Standing eating patterns (Mediterranean, Fast 800, ...) layered on top "
        "of whatever cuisine cooks each night. Soft guidance to the model.",
    ),
    PresetField(
        "week_defaults", "week_defaults", PRESET_FIELD_ENUM_OBJECT,
        "Which meals are cooked",
        "Per meal type: cook fresh, eat a leftover, or skip. \"Shakes and soups\" "
        "weeks live here.",
        choices=("cook", "leftover", "skip"), subkey_source="meal_types",
    ),
    PresetField(
        "meal_weights", "meal_weights", PRESET_FIELD_NUMBER_OBJECT,
        "Where the day's energy sits",
        "Each meal's share of the day's calories. The split is normalised over "
        "the meals actually cooked, so the numbers need not sum to 1.",
        minimum=0.0, maximum=1.0, step=0.05, subkey_source="meal_types",
    ),
    PresetField(
        "net_carbs_g", "", PRESET_FIELD_DAY_CARBS, "Net carbs per day",
        "The week's carb-cycling lever — passed straight into the engine and "
        "never recomputed, so fat absorbs the difference. Calories and protein "
        "are the block's job, not a preset's.",
    ),
    PresetField(
        "servings_per_meal", "serving_rules.servings_per_meal", PRESET_FIELD_INT,
        "People per meal",
        "Cooking for guests is a week, not a life change.",
        advanced=True, minimum=1, step=1,
    ),
    PresetField(
        "cuisine_block_pattern", "planning_rules.cuisine_block_pattern",
        PRESET_FIELD_INT_LIST, "Cuisine block pattern",
        "Contiguous days sharing one cuisine, scaled to the days actually "
        "cooked. \"4, 3\" is the default; \"7\" is one cuisine all week; seven "
        "1s is a different tradition every night.",
        advanced=True,
    ),
    PresetField(
        "min_baseline_cuisine_share", "planning_rules.min_baseline_cuisine_share",
        PRESET_FIELD_FLOAT, "Min. everyday-Western share",
        "Floor on how much of the week goes to plain roast-and-veg cuisines "
        "before the rest rotates freely. 0 turns the floor off.",
        advanced=True, minimum=0.0, maximum=1.0, step=0.05,
    ),
    PresetField(
        "favorite_breakfast_slots", "planning_rules.favorite_breakfast_slots",
        PRESET_FIELD_INT, "Favourite breakfasts",
        "How many mornings one saved favourite covers. The lazy/comfort dial.",
        advanced=True, minimum=0, step=1,
    ),
    PresetField(
        "favorite_dinner_slots", "planning_rules.favorite_dinner_slots",
        PRESET_FIELD_INT, "Favourite dinners",
        "How many dinners saved favourites may claim. 0 = invent every dinner; "
        "raising it past ~2 leaves no room for a cuisine block.",
        advanced=True, minimum=0, step=1,
    ),
    PresetField(
        "favorite_reuse_days", "planning_rules.favorite_reuse_days",
        PRESET_FIELD_NUMBER_OBJECT, "Favourite reuse windows",
        "Days before a saved favourite may be scheduled again, per meal type. "
        "Must stay under the 28-day history depth or the rule silently stops "
        "binding.",
        advanced=True, minimum=0, maximum=28, step=1,
        subkeys=("breakfast", "lunch", "dinner"),
    ),
    PresetField(
        "week_shape", "week_shape",
        PRESET_FIELD_WEEK_SHAPE, "Week shape",
        "Which dishes are batched ahead — a cook day plus the days that eat "
        "it — and which meals draw from the freezer. Off leaves the base "
        "config's own declaration; on replaces it whole, including an empty "
        "list (no automatic batching at all).",
    ),
)


def preset_field_subkeys(field: PresetField, base_config: dict) -> Tuple[str, ...]:
    """The sub-keys an object-valued field edits."""
    if field.subkey_source == "meal_types":
        return tuple(base_config.get("meal_types") or base_config.get("week_defaults") or ())
    return field.subkeys


def preset_editor_field_paths(base_config: dict) -> Tuple[str, ...]:
    """Every override leaf the editor manages, `PRESET_FIELD_DAY_CARBS`
    expanded. `save_preset` clears exactly these off a preset before writing
    the user's choices back, so a hand-added override path the editor never
    drew survives an edit untouched — the `training_schedule` escape hatch,
    per preset."""
    paths: List[str] = []
    for field in PRESET_EDITOR_FIELDS:
        if field.kind == PRESET_FIELD_DAY_CARBS:
            paths.extend(
                f"weekly_schedule.{day}.net_carbs_g"
                for day in base_config.get("weekly_schedule", {})
            )
        else:
            paths.append(field.path)
    return tuple(paths)


def _slim_targets(day_targets: dict) -> Dict[str, float]:
    """The four numbers the preview table shows, out of `calculate_daily_targets`."""
    return {key: day_targets[key] for key in ("calories", "protein_g", "net_carbs_g", "fiber_g")}


@dataclass(frozen=True)
class PresetCatalogRow:
    """One preset as the Settings editor lists it."""

    name: str
    label: str
    active: bool
    changes: List[str]


@dataclass(frozen=True)
class PresetCatalogView:
    rows: List[PresetCatalogRow]
    active: Optional[str]


@dataclass(frozen=True)
class PresetDayTargets:
    """One weekday's resolved macro targets, base beside preset, for the
    on-demand preview."""

    day: str
    base: Dict[str, float]
    preset: Dict[str, float]


@dataclass(frozen=True)
class PresetPreview:
    """What "here is the week this preset produces" resolves to — computed on
    a button, never live (design-03 §4.3: a live grid preview repaints the
    canvas per keystroke)."""

    ok: bool
    failures: List[str]
    changes: List[str]
    day_targets: List[PresetDayTargets]
    identical: bool


@dataclass(frozen=True)
class WeekShapePreview:
    """What a draft `week_shape` resolves to — Task 1.2d's own on-demand
    preview, a sibling of `PresetPreview` but over the batch/freezer-draw
    machinery (`planner.week_shape_errors` then `week.apply_week_shape`)
    rather than the whole config diff. Computed on a button, never live, and
    the applier half never runs at all when the shape isn't even coherent —
    `errors` names why, the same messages `AppConfig`/`save_preset` would
    raise/refuse on."""

    ok: bool
    errors: List[str]
    batch_anchors: Dict[str, Optional[str]]
    warnings: List[str]


@dataclass(frozen=True)
class PresetView:
    """The weekly pick, as the review dialog draws it.

    A view model rather than four `state.` reads at the widget, for the
    reason every other one here exists: the rules — that the baseline is the
    base config, that a no-op override produces no line, that a name with no
    label shows as its name — are worth testing, and a widget module is where
    logic goes to be untestable.
    """

    options: Dict[str, str]
    active: Optional[str]
    label: Optional[str]
    changes: List[str]

    @property
    def available(self) -> bool:
        """Whether there is anything to pick from.

        False on every checkout with no `presets.json`, which is the state
        the whole feature degrades to and the reason the control draws
        nothing at all rather than an empty select.
        """
        return bool(self.options)

    @property
    def summary(self) -> str:
        """One line saying what the pick changed, against the base config.

        "No changes from the base config" is a real answer and the one
        `default` gives — it is what makes that preset visibly data rather
        than a built-in wearing a costume.
        """
        if self.active is None:
            return "No preset — planning against config/ as it stands."
        if not self.changes:
            return "No changes from the base config."
        return " · ".join(self.changes)


@dataclass
class ShoppingWindowView:
    """One shopping trip, resolved once and read by everything that draws it.

    The rail's badge counted the whole week in a single
    `aggregate_cook_events` call while the drawer aggregated per window, so an
    ingredient bought on two trips was one line in the badge and two on
    screen — "Shopping (43)" over a drawer that never summed to 43. That is
    CLAUDE.md's "a number the UI displays and a number a run plans against
    must come from one call, not two", broken quietly on the display side,
    and one view is the whole fix: the badge sums `item_count` over exactly
    the windows the panel renders.
    """

    label: str
    days: List[str]
    events: List[CookEvent]
    shopping_list: Optional[ShoppingList]
    # Slot labels in this window whose generation failed, so the panel can say
    # why a short list is short. Keyed off `WeekPlan.failures`, which is per
    # slot_id rather than per day: one bad meal-type call can fail some of a
    # window's days without failing all of them.
    failed: List[str] = field(default_factory=list)

    @property
    def item_count(self) -> int:
        return len(self.shopping_list.items()) if self.shopping_list else 0


@dataclass(frozen=True)
class FreezerCaptureDefaults:
    """What "send to freezer" should pre-fill for one cook event.

    The one computation both capture routes read (`PlannerState.
    freezer_capture_defaults_for`, `record_freezer_surplus`), so a card
    button and a pending-surplus "Record" click cannot disagree about the
    label, the provenance or — the one that has bitten before, see
    `freezer_cooked_on` — the date.
    """

    label: str
    recipe_id: Optional[str]
    cooked_on: Optional[str]


def freezer_cooked_on(week_plan: WeekPlan, event: CookEvent, config: dict) -> Optional[str]:
    """The real calendar date `event` was cooked on, for a freezer capture.

    A prep-day batch anchors its *eating* slot on day 0 but was actually
    cooked the day before the week started — `design-04` §6a.2, the same
    off-by-one `storage_note`'s `keeps_for_days` paid for once already. This
    resolves the real weekday (`week.resolve_prep_day`, which may land on
    either of the two candidate days, not always exactly one day back) and
    turns it into an ISO date; every other cook event just dates off its own
    grid day (`week.day_date`).

    None when there is no real calendar to date against — a plan with no
    `week_start_date` (from before that field existed), or a prep-ahead event
    when neither candidate day has the hours for a session. Callers hand
    that back as "nothing to freeze against" rather than guessing a date.
    """
    if not week_plan.week_start_date:
        return None
    if not is_prepped_ahead(event, week_plan):
        return day_date(week_plan.week_start_date, week_plan.days, event.day)

    resolution = resolve_prep_day(week_plan.days, config)
    if resolution.day is None:
        return None
    start = datetime.fromisoformat(week_plan.week_start_date).date()
    # Exactly the two candidates `resolve_prep_day` itself considers — see
    # its own docstring: "walks backward ... over exactly the two days that
    # precede it". Matched by real date rather than by re-deriving the
    # weekday-name arithmetic `week._shift_day` already owns.
    for offset in (1, 2):
        candidate = start - timedelta(days=offset)
        if candidate.strftime("%A") == resolution.day:
            return candidate.isoformat()
    return None  # unreachable given resolve_prep_day's own contract


def freezer_capture_defaults(
    week_plan: WeekPlan, event: CookEvent, slot: SlotSpec, config: dict
) -> FreezerCaptureDefaults:
    return FreezerCaptureDefaults(
        label=event.recipe.name,
        recipe_id=slot.recipe_id,
        cooked_on=freezer_cooked_on(week_plan, event, config),
    )


def freezer_surplus_id(target_slot_id: str, cooked_on: str) -> str:
    """Deterministic id for a pending surplus lot's eventual freezer row.

    Keyed on the slot and its real cook date rather than a fresh uuid, so a
    second "Record" click on a still-pending card upserts the same row
    instead of writing a second one (`save_freezer_item` is a plain
    upsert-by-id — idempotency has to come from the id). The manual "send to
    freezer" card action does not use this: a person may genuinely freeze
    more of the same dish on a later occasion, and each of those really is a
    new lot, so it always mints a fresh one.
    """
    return f"surplus:{target_slot_id}:{cooked_on}"


@dataclass(frozen=True)
class FreezerSurplusView:
    """One cook event's declared spare portions, not yet in the freezer.

    `total_portions` is the whole batch — already `claim_count x
    servings_per_meal + extra_portions` (`week.portions_for`, carried
    verbatim as `CookEvent.portions`) — shown alongside `extra_portions` so
    the card reads as "3 meal(s) x 2 + 6 spare = 12 portions" rather than a
    bare 6 with nothing to place it against (`design-04` §6a.1).
    """

    slot_id: str
    label: str
    claim_count: int
    servings_per_meal: int
    extra_portions: int
    total_portions: int
    cooked_on: str

    @property
    def total_expression(self) -> str:
        return (
            f"{self.claim_count} meal(s) × {self.servings_per_meal} + "
            f"{self.extra_portions} spare = {self.total_portions} portions"
        )


@dataclass
class PlannerState:
    """Everything one browser tab is looking at.

    `week_plan` is None until a week has been generated at least once —
    `load_week_plan()` returning None is the documented cold start, not a
    failure, so the canvas falls back to previewing the default grid shape.
    """

    config: dict
    # The five merged core files *before* the preset layer, plus the presets
    # document itself. Both kept because a pick is a **re-layer, not a second
    # layer**: switching from one preset to another has to start from the
    # base again, or the outgoing preset's overrides survive on every leaf the
    # incoming one is silent about. `base_config` is also the only honest
    # baseline for the pick's diff — `default` is an ordinary row that may be
    # edited or deleted, where the base config cannot be, since it is the
    # thing presets layer over.
    base_config: dict = field(default_factory=dict)
    presets_config: dict = field(default_factory=dict)
    # models.json, loaded alongside config — the drawer's model select offers
    # `selectable_models(models_config)` and `.load()` uses
    # `models_config["meal_generation_model"]` as `model`'s starting value
    # until the select changes it for this session.
    models_config: dict = field(default_factory=dict)
    # The latest weigh-in (falling back to user_profile.current_weight_kg),
    # fetched once at `.load()` time — same "read once per page load, nothing
    # here writes biometrics.json" reasoning `ui_app.py` already applies to
    # its own Insights biometrics read. Feeds `estimate_burn`'s MET
    # calculation; None when neither source has a weight, which just means no
    # estimate is offered (see `estimate_burn`).
    weight_kg: Optional[float] = None
    week_plan: Optional[WeekPlan] = None
    # Which cached week is on screen — a key into WEEK_SELECTION_LABELS, and
    # the `week_identifier` threaded through every `load_week_plan`/
    # `save_week_plan` call below. Switching it is what `switch_week` does;
    # plain reloads/generation act on whichever value is already here.
    week_selection: str = "current"
    # Which of `WEEK_SEQUENCE`'s weeks actually have a plan on disk, and which
    # of those have a calendar span covering today. Both are facts about
    # storage rather than about this tab, and both are read by the day picker:
    # the first says whether a chevron at the edge of the week has anywhere to
    # go, the second is where its "Today" button goes back to once you have
    # stepped into another week and the loaded plan no longer has a today in
    # it. Seeded by `scan_cached_weeks` at load and kept current for the
    # *selected* week by `adopt_plan`, which is the only thing that changes
    # what is on disk under it.
    cached_weeks: Set[str] = field(default_factory=set)
    weeks_covering_today: Set[str] = field(default_factory=set)
    week_start: str = ""
    servings: int = 2
    shop_days: List[str] = field(default_factory=list)
    # Shopping-drawer display toggle only — never persisted and never fed to
    # `generate_week_plan`. True partitions the week into one window per cook
    # day (`shopping_windows(state.days, state.days)`) instead of the
    # configured `shop_days` trips; the underlying cook events and quantities
    # are identical either way, only the grouping changes.
    daily_shop_mode: bool = False
    # Which lines have been ticked off, as `(window label, item name)`.
    #
    # Still not persisted — `ui_shopping.py`'s docstring and
    # `architecture.md` both argue that storing ticks would be more state
    # able to disagree with `week_plan.json`, and that argument stands. What
    # did not stand is where they were living: in the DOM, inside a
    # `@ui.refreshable` registered on both `"plan"` and `"shopping_days"`, so
    # any edit that repainted wiped them **mid-shop**. "Not persisted across
    # sessions" and "cleared when you tick a box in another tab" are
    # different claims and only the first was ever decided. Here they are
    # per-client, die with the tab, and never reach `data/`.
    #
    # `aggregate_cook_events` combines by normalised name, so a name is
    # unique within one window's list and the pair is a stable key across
    # repaints. A tick for a line that no longer exists simply never renders.
    shopping_ticks: Set[Tuple[str, str]] = field(default_factory=set)
    # Real value is always set by `.load()` via `resolve_planner_model` —
    # this placeholder only exists because dataclasses require a default.
    model: str = ""
    focus: Optional[SlotView] = None
    edited: bool = False
    # Food already in the house, to be cooked through. Seeded from config's
    # `inventory_to_clear` and edited in the drawer; it reaches the model as a
    # priority, never a constraint (`planner.inventory_instruction`).
    #
    # `{"item": str, "quantity_g": float | None}` rows, the same shape
    # `training_schedule` uses and for the same reason: a list of records the
    # drawer edits field by field. Was a bare `List[str]`, which is what the
    # config file still accepts — a string entry normalises to a row with no
    # quantity here, and `planner.inventory_entries` reads it back as exactly
    # the unquantified item it was. Only a row carrying a `quantity_g` is
    # spent by the ledger; the rest are named every stage as they always were.
    pantry: List[Dict[str, Any]] = field(default_factory=list)
    # day -> partial macro dict, holding only what differs from config.json.
    # Storing the *difference* rather than a full copy is what lets the drawer
    # say which days are overridden and reset them one at a time, and means a
    # day nobody touched still follows the file if the file changes.
    target_overrides: Dict[str, dict] = field(default_factory=dict)
    # Which switchable macro targets are computed and which are read off
    # `weekly_schedule` — `planner.TargetModes`, seeded from profile.json at
    # load() and, unlike every other field in this block, **written back to
    # it** by `set_target_mode`. It is a standing setting rather than an
    # input to the next run: a toggle that reset on reload would answer
    # "where do these numbers come from" differently every time you looked.
    target_modes: Dict[str, str] = field(default_factory=dict)
    # The latest weigh-in and the full biometrics series, both fetched once
    # at load(). Held rather than discarded so `planning_config()` can run
    # `hydrate_dynamic_targets` — a *pure* function — without awaiting
    # storage from a synchronous method. That is what makes the telemetry
    # header a preview of the numbers the next run will actually aim at
    # instead of the stale ones in the file.
    latest_biometrics: Optional[dict] = None
    biometrics: Optional[dict] = None
    # The popup's picks for the *next* generation only — same "transient,
    # diff-based, never persisted" contract as target_overrides. An empty
    # list means "use config.json's list unchanged"; a non-empty pick
    # REPLACES the file's list.
    #
    # `bulk_prep_enabled`/`long_cook_enabled` lived here until Task 1.2d
    # retired them — batches are now `config["week_shape"]`'s own
    # declaration, edited in the preset editor (`ui_presets.py`) rather than
    # toggled per run, so there is no longer a staged field for them at all.
    cuisine_override: List[str] = field(default_factory=list)
    diet_style_override: List[str] = field(default_factory=list)
    # The popup's slider on `planning_rules.min_baseline_cuisine_share` — a
    # scalar, not a list, so unlike cuisine_override/diet_style_override there
    # is no "empty means use the file" state: it always feeds
    # `planning_config()` and is seeded from the file's own value at load() so
    # opening the popup previews the standing config rather than some other
    # default.
    baseline_cuisine_share: float = 0.5
    # Workout sessions ({day, time, type, duration_minutes, estimated_burn_kcal}).
    # Seeded from config's `training_schedule` and edited in the drawer; like
    # the pantry it is an input to the *next* run, folded into
    # `planning_config()` by `planner.apply_training_adjustments` and never
    # written back to config.json.
    training_schedule: List[dict] = field(default_factory=list)
    # Which day the day inspector is open for, if any. Held the same way as
    # `focus` is for the recipe detail dialog: one dialog reused for all seven
    # days, refreshable off this key rather than seven pre-built dialogs.
    # (This field used to back a per-day context-pipeline dialog that phase 3
    # of `ui-redesign.md` removed — repurposed here rather than left dead.)
    inspector_day: Optional[str] = None
    # Which day the Today tab is showing, or None for "follow today". None is
    # a distinct state rather than a copy of today's name: a tab left alone
    # should still be on the right day tomorrow, and storing the resolved name
    # would pin it to whichever day the page happened to load on. Cleared
    # rather than re-pointed when the user clicks the "today" reset, for the
    # same reason `target_overrides` drops a key that matches the file.
    selected_day: Optional[str] = None
    # A run holds this client for 30s-3min per meal type. The loop stays free
    # (planner dispatches each call to a thread), so the browser is still live
    # and perfectly able to click Generate again — this is the flag that says
    # no. Per-client, like everything else here: two tabs generating at once
    # would race to overwrite the same week_plan.json.
    generating: bool = False
    # Which single day a "Regenerate day" click is mid-flight for, if any.
    # Same purpose as `generating` but scoped to one day — guards against a
    # second regenerate click (this day or another) racing the first, and
    # against overlapping with a whole-week run, without needing a canvas
    # repaint to show it: the clicked button's own `loading` prop covers that.
    regenerating_day: Optional[str] = None
    # Same purpose as `regenerating_day` but scoped to one meal (one slot_id),
    # for the per-card "Regenerate meal" refresh icon — guards against a
    # second regenerate click (this meal, another meal, a whole day, or a
    # whole-week run) racing this one.
    regenerating_meal: Optional[str] = None

    # The recipe catalog (recipes_master.json) — every recipe ever favorited
    # or imported, record shape {id, content_key, recipe, is_favorite,
    # source, added_at, updated_at}. It is the single place recipe content
    # outlives the week it was generated in; `week_plan.json` is overwritten
    # every run. Loaded once at startup and kept in sync with disk by every
    # handler that mutates it — there is no re-read on repaint, since the
    # whole point is that a browser tab's list doesn't jump around under a
    # card the user is mid-click on.
    recipe_catalog: List[dict] = field(default_factory=list)
    # `meal_history.json` as it stood at page load — one entry per cooked day,
    # carrying `recipe_names`, and the only record anywhere of *when* a recipe
    # was actually eaten. It backs the Library table's "Last eaten" column and
    # nothing else. Read once and kept, exactly like `recipe_catalog` above:
    # the one thing that appends to it is a generation, which reloads it.
    history: List[dict] = field(default_factory=list)
    # `adherence.json`'s two lists — what actually happened to a planned meal,
    # and which sessions the watch missed but were done anyway. Read once at
    # `.load()` and then kept *in step by hand*: `mark_meal` and `mark_workout`
    # update this alongside their write, because unlike the catalog or the
    # history there is no reload between a mark and the repaint that has to
    # show it. Re-reading the file per repaint would be a disk read per
    # keystroke elsewhere on the page for a list only two surfaces consult.
    adherence: Dict[str, List[dict]] = field(default_factory=dict)
    # `freezer.json`'s declared lots, as plain dicts — `data/`, app-written,
    # like `adherence` above and kept in step by hand the same way:
    # `capture_freezer_item` and its callers (`send_to_freezer`,
    # `record_freezer_surplus`, `update_freezer_item`, `remove_freezer_item`)
    # update this alongside every write, because there is no reload between
    # a capture and the repaint that has to show it disappear from the
    # pending-surplus list or appear in the review dialog's row editor.
    freezer: List[Dict[str, Any]] = field(default_factory=list)
    catalog_search: str = ""
    # The full-screen catalog browser's own filters — separate from
    # catalog_search above so typing in one surface doesn't silently refilter
    # the other. "All" means no meal-type filter, matching the select's own
    # placeholder option.
    catalog_browser_search: str = ""
    catalog_browser_meal_type: str = "All"
    catalog_browser_favorites_only: bool = False
    # Which column the Library table is ordered by, and which way. Session
    # state like every other filter above it — a sort is a way of looking at
    # the catalog, not a fact about it, so it resets with the tab rather than
    # reaching `config/`. The default reproduces the card grid's own order
    # (see `CATALOG_SORT_COLUMNS`), so replacing the grid with a table moved
    # nothing on first paint.
    catalog_browser_sort: str = "favorite"
    catalog_browser_sort_desc: bool = False
    # Which card the swap modal is open for, and its in-progress filter/pick.
    # Held on state rather than as dialog-local variables so `swap_dialog_body`
    # can be a plain `@ui.refreshable` that reads state, the same pattern
    # `recipe_detail` uses for `state.focus`.
    swap_target: Optional[SlotView] = None
    swap_filter: Optional[str] = None
    swap_query: str = ""
    swap_selected_id: Optional[str] = None
    # Import dialog scratch text/toggle; cleared once a paste is successfully
    # turned into a catalog entry. `import_as_favorite` defaults off — an
    # import lands in the catalog but isn't favorited unless asked for.
    import_text: str = ""
    import_as_favorite: bool = False
    edit_catalog_id: Optional[str] = None
    edit_catalog_name: str = ""

    # The live week shape. Held rather than re-derived on every access because
    # it is now editable: rebuilding it per read would throw away the links the
    # user just made. `_spec_shape` records what it was derived from, so a
    # genuine change of week shape still rebuilds it.
    _spec: Optional[WeekSpec] = None
    _spec_shape: tuple = ()

    # `training_schedule` as `.load()` first read it from config.json — a
    # snapshot, not a live reference, so `pending_changes()` can tell an
    # edited/added/removed session apart from one still exactly as the file
    # left it, the same way `set_target` diffs an override against
    # `config["weekly_schedule"]`. Never touched after `.load()`.
    _original_training_schedule: List[dict] = field(default_factory=list)

    # `ProposedSession.key` for every schedule proposal waved away this
    # session. Deliberately session-local and deliberately not persisted: a
    # dismissal says "not now", where accepting says "this is my week", and
    # only the second is a standing fact worth a file. A proposal dismissed
    # today comes back next week if the watch is still recording it, which is
    # the honest behaviour — the evidence for it has grown, not gone.
    dismissed_proposals: Set[str] = field(default_factory=set)

    @classmethod
    async def load(cls, repository: LocalJSONRepository) -> "PlannerState":
        # `apply_preset_layer` lays the active preset over the merged files
        # and validates the result through `AppConfig` here, once, at startup
        # — the same call, in the same order, the CLI gets from
        # `load_config_with_models`. Every field below is then guaranteed
        # present with a real value, so this reads them directly instead of
        # each picking its own `.get(key, DEFAULT)` fallback.
        #
        # The *base* is kept beside the layered result rather than being
        # re-read when the pick changes: `set_preset` re-layers from it, and
        # the pick's diff line is measured against it.
        base_config = await repository.load_config()
        presets_config = await repository.load_presets_config()
        config = apply_preset_layer(base_config, presets_config)
        models_config = await repository.load_models_config()
        latest_biometrics = await repository.get_latest_biometrics()
        # The *series* as well as the latest row, for the same reason
        # `hydrate_config` reads both: `calculate_adaptive_tdee` measures
        # expenditure from the whole weigh-in and intake history, and
        # `planning_config()` now runs that same hydration to preview it.
        biometrics = await repository.load_biometrics()
        state = cls(
            config=config,
            base_config=base_config,
            presets_config=presets_config,
            models_config=models_config,
            latest_biometrics=latest_biometrics,
            biometrics=biometrics,
            weight_kg=resolve_current_weight_kg(config["user_profile"], latest_biometrics),
            model=resolve_planner_model(dict(config, models=models_config)),
            # The nine config-derived fields come from `PRESET_SEEDED_FIELDS`
            # rather than being spelled here, so `set_preset`'s re-seed reads
            # the same table this does and the two cannot drift.
            **{name: read(config) for name, read in PRESET_SEEDED_FIELDS},
        )
        state._original_training_schedule = [dict(session) for session in state.training_schedule]
        state.recipe_catalog = await repository.load_recipe_catalog()
        state.history = await repository.load_history()
        state.adherence = await repository.load_adherence()
        state.freezer = await repository.load_freezer()
        await state.reload_plan(repository)
        await state.scan_cached_weeks(repository)
        return state

    async def reload_plan(self, repository: LocalJSONRepository) -> None:
        """Pull the cached week off disk. Validation lives here, not in the
        repository, which deliberately deals in plain dicts.

        Acts on whichever week is already selected — `switch_week` is what
        changes that.
        """
        raw = await repository.load_week_plan(self.week_selection)
        self.adopt_plan(WeekPlan.model_validate(raw) if raw else None)

    async def switch_week(self, repository: LocalJSONRepository, target_week: str) -> None:
        """Change which cached week is on screen, loading it from disk.

        The load happens before `week_selection` is reassigned, same
        ordering as `reload_plan`: if it raises, the previous week is still
        the one showing rather than the state flipping to a selection whose
        plan never actually loaded. No generation call here, and no write
        either — this only ever reads what's already on disk.
        """
        raw = await repository.load_week_plan(target_week)
        self.week_selection = target_week
        self.adopt_plan(WeekPlan.model_validate(raw) if raw else None)

    def adopt_plan(self, plan: Optional[WeekPlan]) -> None:
        """Make `plan` the week on screen, discarding any unsaved edits.

        Both callers have just made disk the truth again — a reload read it, a
        generation wrote it — so the edited spec is stale by definition and the
        "edited — not saved" chip has to clear with it. Dropping `_spec` rather
        than rebuilding it here means the next `spec` read derives from the new
        plan, which is where that derivation already lives.
        """
        self.week_plan = plan
        self._spec = None
        self._spec_shape = ()
        self.edited = False
        # The selected week is the only one anything here can change — a
        # reload just read it, a generation just wrote it — so its two
        # entries are maintained where that happens rather than by re-probing
        # the disk. `scan_cached_weeks` answers for the others, once.
        for week_set, holds in (
            (self.cached_weeks, plan is not None),
            (self.weeks_covering_today, self._plan_covers_today(plan)),
        ):
            if holds:
                week_set.add(self.week_selection)
            else:
                week_set.discard(self.week_selection)

    def _known_weeks(self, scanned: Set[str], selection_holds: bool) -> Set[str]:
        """`scanned`, or what the loaded plan alone can vouch for if it is empty.

        `scan_cached_weeks` is what fills these, and it runs from `.load()`.
        A `PlannerState` built any other way — every test fixture, and
        anything constructed before the scan lands — has never probed disk,
        and an empty set there means "not asked yet", not "nothing exists".
        Reading it as the latter would silently disable both chevrons and the
        Today button on a state that has a perfectly good week loaded.

        Falling back to the selection makes an unscanned state behave exactly
        as this tab did before it could cross weeks: one week, clamped at both
        ends. The fallback can never over-claim, because the two facts it
        substitutes — a plan is loaded, and it has a column for today — are
        the same two the scan would record for that week anyway.
        """
        if scanned:
            return scanned
        return {self.week_selection} if selection_holds else set()

    def _plan_covers_today(self, plan: Optional["WeekPlan"]) -> bool:
        """Whether `plan` has a *column* for today, not merely a span over it.

        The same stricter test `week_covers_today` applies to the loaded plan,
        asked of an arbitrary one — `today_in_week` answers a question about
        dates while the grid is drawn from `self.days`, and it is the columns
        a picker can navigate to.
        """
        if plan is None:
            return False
        return (
            today_in_week(plan.week_start_date, plan.days, plan.generated_at) in self.days
        )

    async def scan_cached_weeks(self, repository: LocalJSONRepository) -> None:
        """Which of `WEEK_SEQUENCE`'s weeks exist on disk, and which holds today.

        One extra small read per page load, for the week that is not on
        screen. That buys the picker an *honest disabled state*: a chevron at
        the edge of the week can only be offered when there is something on
        the other side of it, which is the same standard the clamped version
        already set for itself ("a disabled chevron is the honest edge"). The
        alternative — spill first, discover the week is empty afterwards —
        strands the reader on a destination with no picker to step back with,
        since `viewed_day()` is None once there is no plan.

        A week whose file will not validate is treated as absent rather than
        raised: a corrupt `next` week must not take down a page load that was
        only ever asked to show `current`.
        """
        cached: Set[str] = set()
        covering: Set[str] = set()
        for week in WEEK_SEQUENCE:
            if week == self.week_selection:
                plan = self.week_plan
            else:
                raw = await repository.load_week_plan(week)
                try:
                    plan = WeekPlan.model_validate(raw) if raw else None
                except ValidationError:
                    plan = None
            if plan is None:
                continue
            cached.add(week)
            if self._plan_covers_today(plan):
                covering.add(week)
        self.cached_weeks = cached
        self.weeks_covering_today = covering

    @property
    def days(self) -> List[str]:
        return week_days(self.config, self.week_start)

    @property
    def meal_types(self) -> List[str]:
        return meal_types(self.config)

    def pantry_entries(self) -> List[Tuple[str, Optional[float]]]:
        """The staged pantry as `(name, grams or None)` pairs.

        `planner.inventory_entries` is the only parser of that list, per
        CLAUDE.md, so this hands it the staged rows rather than re-reading
        them: the drawer's editor, the generation ledger and the shopping
        list's subtraction then cannot disagree about what the pantry says.
        The *staged* rows and not `config["inventory_to_clear"]`, because a
        row typed into the drawer moments ago is the honest statement of
        what is in the house and is what the next run will be given.
        """
        return inventory_entries({"inventory_to_clear": self.pantry})

    def shopping_view(self) -> List[ShoppingWindowView]:
        """Every trip in this week, resolved once — the panel and the badge
        both read this and nothing else aggregates.

        The pantry comes off here rather than inside `aggregate_cook_events`
        because aggregation is a fact about the recipes and this is a fact
        about the house: the same list, asked two different questions. See
        `shopping.apply_pantry` for why it is render-time and stores nothing.
        """
        plan = self.week_plan
        if plan is None:
            return []
        window_days = self.days if self.daily_shop_mode else self.shop_days
        pantry = self.pantry_entries()
        views = []
        for window in shopping_windows(self.days, window_days):
            events = plan.events_on_days(window.days)
            shopping_list = None
            if events:
                shopping_list = apply_pantry(
                    aggregate_cook_events(events, window.days), pantry
                )
            views.append(
                ShoppingWindowView(
                    label=window.label,
                    days=list(window.days),
                    events=events,
                    shopping_list=shopping_list,
                    failed=[
                        slot_label(key)
                        for key in plan.failures
                        if parse_slot_id(key)[0] in window.days
                    ],
                )
            )
        return views

    def shopping_item_count(self) -> int:
        """How many lines the drawer will show, summed over its own windows."""
        return sum(view.item_count for view in self.shopping_view())

    def shopping_tick(self, window_label: str, name: str) -> bool:
        return (window_label, name) in self.shopping_ticks

    def set_shopping_tick(self, window_label: str, name: str, ticked: bool) -> None:
        key = (window_label, name)
        if ticked:
            self.shopping_ticks.add(key)
        else:
            self.shopping_ticks.discard(key)

    def today_day(self) -> Optional[str]:
        """Today's weekday name, if the loaded week's actual calendar span
        covers it — see `week.today_in_week`. None when there's no plan yet,
        or the loaded plan (a stale cache, or a "next" week not yet current)
        doesn't include today, so a "Today" view knows to say so rather than
        confidently rendering the wrong Thursday.
        """
        if self.week_plan is None:
            return None
        return today_in_week(
            self.week_plan.week_start_date, self.week_plan.days, self.week_plan.generated_at
        )

    def viewed_day(self) -> Optional[str]:
        """Which day the Today tab renders — the browsed one, or today.

        Falls back to the week's first day when today isn't in the loaded
        span, rather than refusing to render as `today_day()` alone did. Once
        the tab has a day picker, "this cached week doesn't cover today" is a
        note to print above a browsable week, not a reason to show nothing —
        the plan is still perfectly readable, it just isn't current.

        None means there is no plan at all, which is the one case with
        genuinely nothing to show.
        """
        if self.week_plan is None:
            return None
        days = self.days
        if self.selected_day in days:
            return self.selected_day
        return self.today_day() if self.week_covers_today() else (days[0] if days else None)

    def week_covers_today(self) -> bool:
        """Whether the loaded week has a column for today's actual date.

        Stricter than `today_day() is not None`, and deliberately so: that
        answers "does this plan's seven-day *span* contain today", which is a
        question about dates, while the grid is drawn from `self.days`. The
        two can disagree — a config whose `weekly_schedule` names fewer than
        seven days has a span wider than its columns — and it is the columns a
        day picker can actually navigate to.
        """
        return self.today_day() in self.days

    def viewing_today(self) -> bool:
        """Whether the day on screen is actually today's — false both when
        browsing away and when the loaded week has no today to be on."""
        return self.week_covers_today() and self.viewed_day() == self.today_day()

    def select_day(self, day: Optional[str]) -> None:
        """Browse to `day`, or pass None to go back to following today."""
        self.selected_day = day if day in self.days else None

    def browsable_timeline(self) -> List[Tuple[str, str]]:
        """Every `(week, day)` the picker can reach, in calendar order.

        The concatenation of each *cached* week's columns. It can be this
        simple only because `days` is derived from config rather than from the
        plan — both weeks are the same seven weekdays in the same rotation, so
        crossing from one to the next is an index step and never a re-read of
        anybody's day list.
        """
        return [
            (week, day)
            for week in WEEK_SEQUENCE
            if week in self._known_weeks(self.cached_weeks, self.week_plan is not None)
            for day in self.days
        ]

    def step_target(self, delta: int) -> Optional[Tuple[str, str]]:
        """Where stepping `delta` days lands: `(week, day)`, or None to stay put.

        The whole rule, kept pure so the picker's chevrons can ask it twice —
        once to decide whether to disable themselves, once to act — and so it
        is testable without a repository. `step_viewed_day` is the two-line
        application on top.

        **Stepping crosses into the adjacent cached week rather than clamping
        at Sunday**, which is what this used to do. The two objections
        recorded against crossing were an async load of the other plan and "a
        second control free to disagree with the header's week selector", and
        both are answered rather than dodged: the load is what
        `scan_cached_weeks` has already decided is worth doing, and there is
        no second control because the chevron *drives* the existing selector —
        `switch_week` is still the only thing that writes `week_selection`,
        and the header select repaints from it.

        Still clamped at the outer ends, and still for the original reason:
        beyond the last cached week there is genuinely nothing, and wrapping
        round to the first would pretend the calendar is a loop. None is what
        a clamp reads as here, so a caller cannot tell "did not move" apart
        from "must not move" — which is exactly what a disabled chevron wants.
        """
        day = self.viewed_day()
        if day is None or day not in self.days:
            return None
        timeline = self.browsable_timeline()
        here = (self.week_selection, day)
        if here not in timeline:
            return None
        landed = timeline[
            min(max(timeline.index(here) + delta, 0), len(timeline) - 1)
        ]
        return None if landed == here else landed

    async def step_viewed_day(self, repository: LocalJSONRepository, delta: int) -> None:
        """Apply `step_target`, loading the other cached week if it crosses.

        Async because crossing a week reads one from disk. Every within-week
        step still touches nothing but `selected_day`, so the common case
        awaits nothing.
        """
        target = self.step_target(delta)
        if target is None:
            return
        week, day = target
        if week != self.week_selection:
            await self.switch_week(repository, week)
        self.select_day(day)

    def today_week(self) -> Optional[str]:
        """The earliest cached week with a column for today, or None.

        What a "Today" reset goes back to. It has to be a fact about *disk*
        rather than about the loaded plan, or the reset button would
        disappear the moment stepping forward landed on a week that has no
        today in it — which is precisely when it is most wanted.
        """
        covering = self._known_weeks(self.weeks_covering_today, self.week_covers_today())
        return next((w for w in WEEK_SEQUENCE if w in covering), None)

    def today_is_reachable(self) -> bool:
        """Whether a "Today" reset would actually do something.

        Same "only offered when it would move you" test the clamped version
        applied to the loaded week, widened to the whole timeline.
        """
        home = self.today_week()
        if home is None:
            return False
        return home != self.week_selection or not self.viewing_today()

    async def go_to_today(self, repository: LocalJSONRepository) -> None:
        """Return to today, crossing back into its week if we have left it.

        Clears `selected_day` rather than re-pointing it at today's name — the
        "follow today" state is a distinct one, and storing the resolved name
        would pin the tab to whichever day the reset happened on.
        """
        home = self.today_week()
        if home is not None and home != self.week_selection:
            await self.switch_week(repository, home)
        self.select_day(None)

    def open_inspector(self, day: str) -> None:
        """Open the day inspector for `day` — see `ui_inspector.py`."""
        self.inspector_day = day if day in self.days else None

    def close_inspector(self) -> None:
        self.inspector_day = None

    def day_date_iso(self, day: str) -> Optional[str]:
        """The calendar date `day` falls on in the loaded week, or None.

        None for a plan generated before `week_start_date` existed — the same
        pre-migration tolerance `today_in_week` extends, and the reason
        `week.day_date` refuses to guess an anchor of its own. A caller with
        no date shows the weekday name alone rather than a plausible-looking
        wrong one.
        """
        plan = self.week_plan
        if plan is None or not plan.week_start_date or day not in plan.days:
            return None
        return day_date(plan.week_start_date, plan.days, day)

    def _shape(self) -> tuple:
        """What the spec is derived *from*; a change here invalidates edits.

        `servings` only counts for an un-generated week: a generated plan's
        portions come from `week_plan.servings_per_meal`, so nudging the
        drawer's people-per-meal must not silently discard links it can't
        affect.
        """
        plan = self.week_plan
        return (
            tuple(self.days),
            plan.generated_at if plan else None,
            None if plan else self.servings,
        )

    @property
    def spec(self) -> WeekSpec:
        """The week as a spec, whether or not it has been generated.

        A generated plan carries its own slots, so reusing them here means
        `portions_for` and friends work identically on both — the derived
        portion count in the preview is the same arithmetic that produced the
        real one.
        """
        shape = self._shape()
        if self._spec is None or self._spec_shape != shape:
            if self.week_plan:
                self._spec = WeekSpec(
                    days=self.days,
                    servings_per_meal=self.week_plan.servings_per_meal,
                    slots=self.week_plan.slots,
                )
            else:
                self._spec = default_week_spec(self.config, self.week_start, self.servings)
            self._spec_shape = shape
        return self._spec

    def apply_spec(self, spec: WeekSpec) -> None:
        """Adopt an edited spec as the week's live shape.

        The plan keeps its *own* copy of the slots and `day_slot_macros` walks
        those, so replacing only the spec would leave the telemetry header
        reporting the week as it was before the edit. Cook events are rescaled
        for the same reason: portions are derived from how many slots claim a
        cook (`week.portions_for`), so the batch — and its ingredient
        quantities — has to move with them.

        Nothing is written to disk. This is a local reshuffle of a week that
        has already been generated; `edited` is what tells the header to say
        so.
        """
        self._spec = spec
        self.edited = True

        plan = self.week_plan
        if plan is None:
            self._spec_shape = self._shape()
            return

        portions = portions_for(spec)
        claims = eaten_on(spec)

        def rescaled(event: CookEvent) -> CookEvent:
            if event.slot_id not in portions or event.portions <= 0:
                return event
            target = portions[event.slot_id]
            return event.model_copy(
                update={
                    "portions": target,
                    "eaten_by": list(claims.get(event.slot_id, [event.slot_id])),
                    # `self.config` threaded through so the storage note uses
                    # the configured `inventory_rules.storage_windows`.
                    # Omitting it silently fell back to
                    # week.DEFAULT_STORAGE_WINDOWS, so an edited config would
                    # disagree with the note on the card. The dish's own class
                    # comes off the recipe itself, inside `scale_to_servings`.
                    # `is_prepped_ahead` so a batch cooked in the prep
                    # session keeps counting its fridge days from the pan —
                    # `scale_to_servings` rewrites the storage note it wrote
                    # at generation, so without this a single grid edit puts
                    # the off-by-one back.
                    "recipe": event.recipe.scale_to_servings(
                        target,
                        span_days(
                            spec,
                            event.slot_id,
                            is_prepped_ahead(event, plan),
                        ),
                        self.config,
                    ),
                }
            )

        self.week_plan = plan.model_copy(
            update={
                "slots": list(spec.slots),
                # A slot that stopped being a cook keeps its event in the list,
                # orphaned: nothing resolves to it any more, and holding it
                # means the recipe is still there if the week is re-pointed.
                "cook_events": [rescaled(event) for event in plan.cook_events],
            }
        )
        self._spec_shape = self._shape()

    def link_to_next_lunch(self, source_id: str) -> Optional[str]:
        """Point the following day's lunch at this cook. Returns why not, or None.

        The whole "Macro Action": one click turns tomorrow's lunch into
        leftovers of tonight's dinner, which — because portions are derived —
        is also what increases tonight's batch. There is no portion number to
        set anywhere in this flow, by design.
        """
        spec = self.spec
        source = spec.by_id().get(source_id)
        if source is None:
            return "That meal isn't part of this week."

        target_id = next_day_slot_id(spec, source.day, LINK_TARGET_MEAL)
        if target_id is None:
            return f"{source.day} is the last day of the week — there's no next lunch."

        error = leftover_link_error(spec, target_id, source_id)
        if error:
            return error

        self.apply_spec(link_leftover(spec, target_id, source_id))
        return None

    def unlink_slot(self, target_id: str) -> Optional[str]:
        """Turn a leftover slot back into a cook. Returns why not, or None.

        The inverse of `link_to_next_lunch`, and the only way to undo one:
        clicking the link button again hits `leftover_link_error`'s
        repeat-click guard rather than toggling. Without this the grid could
        only ever accumulate links — `ui_generation.generate_week`'s own
        "unlink one, or turn off one of the two toggles" warning named an
        action the UI did not actually offer.

        `apply_spec` does the rest: portions are derived, so dropping a claim
        shrinks the source batch and rescales its cook event by the same
        linear arithmetic that grew it.
        """
        spec = self.spec
        slot = spec.by_id().get(target_id)
        if slot is None:
            return "That meal isn't part of this week."
        if slot.mode != MODE_LEFTOVER:
            return f"{slot_label(target_id)} isn't a leftover."

        self.apply_spec(unlink_leftover(spec, target_id))
        return None

    def default_skip_estimate(self, target_slot_id: str) -> dict:
        """What a skipped meal would have been briefed at, had it been cooked.

        The starting point for the estimate on a meal eaten out: "roughly what
        this slot is normally worth". It runs the day through `split_targets`
        with the skipped slot *added back* as a cook slot, so the number comes
        from the same weight normalisation generation would have used rather
        than from a flat quarter of the day — a snack and a dinner are not
        the same guess.

        It is only ever a default. `set_skip_estimate` takes whatever the user
        actually types, because the two cases this feature exists for pull in
        opposite directions: a restaurant dinner is usually well *above* the
        weighted share, and a missed meal is 0.
        """
        spec = self.spec
        slot = spec.by_id().get(target_slot_id)
        if slot is None:
            return {key: 0.0 for key in MACRO_KEYS}

        config = self.planning_config()
        # The skipped slot, temporarily a cook, so split_targets counts it in
        # the weight normalisation instead of redistributing its share.
        as_cook = slot.model_copy(update={"mode": MODE_COOK, "skip_estimate": None})
        day_slots = [
            as_cook if other.id == target_slot_id else other
            for other in spec.slots
            if other.day == slot.day
            and (other.mode == MODE_COOK or other.id == target_slot_id)
        ]
        budgets = split_targets(
            self.targets_for(slot.day),
            day_slots,
            day_multiplicity(spec, slot.day),
            config,
            meal_overrides_for(slot.day, config),
        )
        budget = budgets.get(target_slot_id)
        return (
            {key: round(float(budget[key]), 1) for key in MACRO_KEYS}
            if budget
            else {key: 0.0 for key in MACRO_KEYS}
        )

    def set_skip_estimate(
        self, target_slot_id: str, estimate: Optional[dict]
    ) -> Optional[str]:
        """Record (or clear) the macros a skipped meal is eaten out at.

        Returns why not, or None. Same shape as `link_to_next_lunch` and
        `swap_slot_with_favorite`: one sentence about the one slot clicked,
        state mutates only on success, and the caller repaints.
        """
        spec = self.spec
        slot = spec.by_id().get(target_slot_id)
        if slot is None:
            return "That meal isn't part of this week."
        if slot.mode != MODE_SKIP:
            return (
                f"{slot_label(target_slot_id)} isn't skipped — a cooked or "
                "leftover meal's macros come from its recipe."
            )
        if estimate is not None:
            missing = [key for key in MACRO_KEYS if key not in estimate]
            if missing:
                return f"Estimate is missing {', '.join(missing)}."
            if any(float(estimate[key]) < 0 for key in MACRO_KEYS):
                return "An estimate can't be negative."
            estimate = {key: float(estimate[key]) for key in MACRO_KEYS}

        self.apply_spec(set_skip_estimate(spec, target_slot_id, estimate))
        return None

    def recipe_pin_options(self, target_slot_id: str) -> List[dict]:
        """Catalog records this slot may be pinned to under the live config."""
        slot = self.spec.by_id().get(target_slot_id)
        if slot is None or slot.mode != MODE_COOK:
            return []
        config = self.planning_config()
        config = dict(config, storage_spans=storage_spans(self.spec, config))
        return [
            record
            for record in self.recipe_catalog
            if recipe_eligibility_error(record, slot, config) is None
        ]

    def pin_recipe_for_slot(
        self, target_slot_id: str, recipe_id: Optional[str]
    ) -> Optional[str]:
        """Stage a deliberate catalog recipe for one cook slot, or explain why not."""
        spec = self.spec
        slot = spec.by_id().get(target_slot_id)
        if slot is None:
            return "That meal is not part of this week."
        if slot.mode != MODE_COOK:
            return f"{slot_label(target_slot_id)} is not a cook slot."
        if not recipe_id:
            self._spec = pin_recipe(spec, target_slot_id, None)
            self._spec_shape = self._shape()
            return None

        record = next((item for item in self.recipe_catalog if item.get("id") == recipe_id), None)
        if record is None:
            return "That recipe is no longer in your catalog."
        config = self.planning_config()
        config = dict(config, storage_spans=storage_spans(spec, config))
        error = recipe_eligibility_error(record, slot, config)
        if error:
            return error
        try:
            # Validate and normalise here as well as at generation: refusal is
            # immediate, and a bookmarked batch never reaches the pin path as
            # a two-serving recipe that the portion-trim clamp cannot halve.
            single_serving(
                Recipe.model_validate(record.get("recipe"), context={"config": config})
            )
        except ValidationError as exc:
            return f"That catalog entry is not a usable recipe: {exc}"

        self._spec = pin_recipe(
            spec, target_slot_id, recipe_id, origin=PIN_ORIGIN_USER
        )
        self._spec_shape = self._shape()
        return None

    def shuffle_styles(self) -> None:
        """Blank the style/cuisine on every cook slot so the next generation
        re-rolls them from scratch.

        `week.clear_styles`/`week.clear_cuisines` are what `generate_week`
        now also applies unconditionally on every full-week run, so this
        manual drawer action is a preview of what the next Generate click
        would already do — useful when you want to see the re-roll without
        also spending an API call, or before a run that isn't going through
        `generate_week` at all. Touches only style/cuisine, not mode or
        leftover links, so `apply_spec` can go through its normal rescale
        path unchanged.
        """
        self.apply_spec(clear_cuisines(clear_styles(self.spec)))

    def swap_slot_with_favorite(self, target_slot_id: str, favorite_recipe: dict) -> Optional[str]:
        """Replace a cooked slot's recipe with a saved favorite. Returns why not, or None.

        Same shape as `link_to_next_lunch`: a single sentence about the one
        slot clicked, state only mutates on success, and the caller is the one
        that repaints (`refresh_all`) — this class stays free of `ui.*` calls,
        same as everywhere else in `PlannerState`.

        Resolved through `source` for a leftover slot, same as `slot_views`,
        because the recipe belongs to the cook event, not the eating slot —
        swapping a leftover's favorite has to change the meal everyone in that
        chain eats, not fork a second copy only this slot sees.
        """
        if self.week_plan is None:
            return "Generate a week before swapping in a favorite."

        spec = self.spec
        slot = spec.by_id().get(target_slot_id)
        if slot is None:
            return "That meal isn't part of this week."
        if slot.mode == MODE_SKIP:
            return f"{slot_label(target_slot_id)} is skipped — nothing to swap."

        source_id = slot.id if slot.mode == MODE_COOK else slot.source
        event = self.week_plan.by_slot().get(source_id or "")
        if event is None:
            return f"{slot_label(target_slot_id)} hasn't been generated yet — nothing to swap."

        try:
            recipe = Recipe.model_validate(favorite_recipe)
        except ValidationError as exc:
            return f"That favorite isn't a usable recipe: {exc}"

        # Favorites are expected in the same shape generation produces: one
        # serving. A favorite saved off an already-scaled batch card would
        # carry servings > 1, so it's normalised back to one serving first —
        # `scale_to_servings` below is what re-expands it to this slot's batch.
        if recipe.servings != 1:
            recipe = recipe.resize_by_factor(1 / recipe.servings).model_copy(
                update={"servings": 1}
            )

        # portions/keeps_for_days mirror `apply_spec`'s rescale: the batch
        # size is still derived from how many slots claim this cook, a
        # favorite swap doesn't change that.
        scaled = recipe.scale_to_servings(
            event.portions,
            keeps_for_days=span_days(
                spec, source_id, is_prepped_ahead(event, self.week_plan)
            ),
            config=self.config,
        )
        new_event = event.model_copy(update={"recipe": scaled})
        self.week_plan = self.week_plan.model_copy(
            update={
                "cook_events": [
                    new_event if e.slot_id == source_id else e
                    for e in self.week_plan.cook_events
                ]
            }
        )
        # No apply_spec here: the slot's mode/source didn't change, only the
        # cook event's recipe, which day_slot_macros and slot_views already
        # read live off week_plan.cook_events — no _spec rebuild needed.
        self.edited = True
        return None

    # ---- the Library table ------------------------------------------

    def catalog_rows(self) -> List["CatalogRow"]:
        """Every catalog entry passing the Library's filters, as table rows,
        in the order its column headers currently ask for.

        Filter, projection and sort are one call because a table is one
        answer: the widget module renders what it is handed rather than
        deciding any part of it, which is what makes the whole column set
        testable without a NiceGUI harness. `catalog_matches` is the same
        filter `/api/recipes` uses — see its note in `repository.py` for what
        happened the last time those two were written out separately.
        """
        return sort_catalog_rows(
            build_catalog_rows(
                [
                    entry
                    for entry in self.recipe_catalog
                    if catalog_matches(
                        entry,
                        favorites_only=self.catalog_browser_favorites_only,
                        meal_type=self.catalog_browser_meal_type,
                        search=self.catalog_browser_search,
                    )
                ],
                self.history,
            ),
            self.catalog_browser_sort,
            self.catalog_browser_sort_desc,
        )

    def sort_catalog_by(self, column: str) -> None:
        """Clicking a column header: the same column flips direction, a new
        column starts ascending. An unknown column is ignored rather than
        stored, so a header added to the table without a key in
        `CATALOG_SORT_COLUMNS` fails visibly (nothing happens) instead of
        silently resetting the order the next repaint.
        """
        if column not in CATALOG_SORT_COLUMNS:
            return
        if self.catalog_browser_sort == column:
            self.catalog_browser_sort_desc = not self.catalog_browser_sort_desc
        else:
            self.catalog_browser_sort = column
            self.catalog_browser_sort_desc = False

    def planning_config(self, *, ignore_overrides_for: Optional[str] = None) -> dict:
        """Config as the *next* generation will see it.

        The file on disk, plus everything the drawer can change: the model, the
        per-day target overrides, the pantry list and the training schedule.
        Assembled here rather than at the call site so one object carries all
        of it — `generate_week_plan`, `validate_week`, `split_targets` and
        `inventory_instruction` all read plain config, and each would
        otherwise need its own patch applied.

        `apply_training_adjustments` runs last, on top of the drawer's target
        overrides: a workout's burn stacks onto whatever the day target
        currently reads, edited or not, and its per-meal pin/notes have to be
        in place before `calculate_daily_targets`/`meal_overrides_for` read
        this config — that is what makes the telemetry header a live preview
        of a training edit rather than something only the next generation
        would show.

        Nothing here is written back to config.json — target/training/pantry
        overrides are meant to be "this week is different" and nothing writes
        them anywhere but `week_plan.json`'s own record of what a run used,
        the same way `save_grid` writes a grid edit without touching
        config.json either.
        """
        overrides = {
            day: values
            for day, values in self.target_overrides.items()
            if day != ignore_overrides_for
        }
        # Which (day, macro) pairs an override wrote, so hydration knows not
        # to replace them. Without this the fold below is invisible to
        # `hydrate_dynamic_targets`, which cannot tell an edited value from a
        # stale file one and overwrote both — the bug that made every
        # calorie/protein override a silent no-op.
        locks = {
            day: [key for key in values if key in TARGET_MODE_MACROS]
            for day, values in overrides.items()
        }
        schedule = {
            day: dict(day_config, **overrides.get(day, {}))
            for day, day_config in self.config["weekly_schedule"].items()
        }
        return hydrate_dynamic_targets(
            apply_training_adjustments(
                dict(
                    self.config,
                    weekly_schedule=schedule,
                    inventory_to_clear=[dict(item) for item in self.pantry],
                            openrouter_model=self.model,
                            training_schedule=[dict(session) for session in self.training_schedule],
                            # generate_week_plan/build_client/fit_recipe_to_budget etc.
                            # all read `config.get("models")` — see planner.py — so this
                            # is what lets a run resolve request_timeout_seconds and the
                            # base URL from models.json instead of the pre-models.json
                            # literals.
                            models=self.models_config,
                            # cuisine_override/diet_style_override REPLACE (not add to)
                            # config's own lists when non-empty — resolve_auto_choices'
                            # pick_cuisine_blocks and build_diet_style_rule already just
                            # read whatever's here, so overriding these two values is the
                            # entire cuisine/diet-style popup feature; no new algorithm.
                            cuisines=self.cuisine_override or self.config["cuisines"],
                            dietary_rules=dict(
                                self.config["dietary_rules"],
                                active_diet_styles=(
                                    self.diet_style_override
                                    or self.config["dietary_rules"]["active_diet_styles"]
                                ),
                            ),
                            # Same REPLACE-in-place approach as dietary_rules above: only
                            # this one key of planning_rules changes, so it's spread back
                            # in rather than rebuilt, and pick_cuisine_blocks reads it via
                            # planning_rule() exactly as it reads every other rule. `.get`
                            # rather than direct indexing — unlike dietary_rules above,
                            # not every hand-built config a test constructs carries this
                            # key, and planning_rule() already tolerates that same gap.
                            planning_rules=dict(
                                self.config.get("planning_rules") or {},
                                min_baseline_cuisine_share=self.baseline_cuisine_share,
                            ),
                            target_modes=dict(self.target_modes),
                            target_locks={day: keys for day, keys in locks.items() if keys},
                )
            ),
            self.latest_biometrics,
            biometrics=self.biometrics,
            # Silent: this runs on every repaint, and a per-keystroke
            # "dynamic targets: ..." line would bury the per-call generation
            # timing `logs/meals.log` exists for. The generation entry points
            # hydrate again with logging on.
            log=False,
        )

    def planned_targets(self, day: str) -> dict:
        """What the next run will actually aim at for `day`.

        Hydrated, so this is the engine's computed figure for any macro on
        `auto` — not `weekly_schedule`'s stated one. Before `planning_config`
        hydrated, this returned the file's numbers and the telemetry header
        was measuring the week against a target no run would ever use: a
        1000 kcal Thursday displayed against a plan generated for 1722.

        `fat_g` comes back derived, so the drawer shows the same figure the
        model will be told rather than one the UI computed its own way.
        """
        return calculate_daily_targets(day, self.planning_config())

    def baseline_targets(self, day: str) -> dict:
        """What `day` would aim at with none of its own overrides applied.

        The thing an override is a difference *from*, which is no longer
        `weekly_schedule`'s stated numbers: on `auto` it is whatever the
        engine computes. `set_target` compares against this so that typing
        the computed value back in clears the override rather than storing a
        no-op copy of it, and so the reset button can still undo itself.
        """
        return calculate_daily_targets(
            day, self.planning_config(ignore_overrides_for=day)
        )

    def set_target(self, day: str, key: str, value: float) -> None:
        """Record a review-dialog edit to one of a day's macro targets.

        A value equal to the day's *resolved* baseline clears that key
        instead of storing a no-op override, so "overridden" always means
        "differs from what this day would otherwise aim at". That is also
        what makes the reset button able to undo itself: it writes the
        baseline numbers back into the inputs, and the change events those
        fire land here and cancel out rather than re-creating the override.

        It compares against `baseline_targets`, not `weekly_schedule`. On a
        macro set to `auto` the file's number is inert — Thursday's stated
        1000 kcal against a computed 1722 — so diffing against it marked
        every day permanently overridden and made typing the real target
        look like an edit.
        """
        base = self.baseline_targets(day)
        override = dict(self.target_overrides.get(day, {}))
        if round(float(base.get(key, 0)), 1) == round(float(value), 1):
            override.pop(key, None)
        else:
            override[key] = float(value)

        if override:
            self.target_overrides[day] = override
        else:
            self.target_overrides.pop(day, None)

    def preset_view(self) -> PresetView:
        """The pick, its options and what it changed — one object, one read."""
        entries = preset_layer.preset_entries(self.presets_config)
        active = preset_layer.active_preset_name(self.presets_config)
        return PresetView(
            options={
                name: preset_layer.preset_label(self.presets_config, name)
                for name in entries
            },
            active=active,
            label=preset_layer.preset_label(self.presets_config, active),
            changes=preset_layer.preset_changes(self.base_config, self.presets_config, active),
        )

    async def set_preset(
        self, repository: LocalJSONRepository, name: Optional[str]
    ) -> None:
        """Choose this week's preset: re-layer the config, and save the pick.

        The **third** writer to `config/`, after `set_target_mode` and
        `accept_training_proposal`, and it passes the same test both do — a
        standing choice, not an input to one run. A pick that evaporated on
        reload would reintroduce the decision this whole arm exists to
        remove, since the default is meant to be last week's pick. It is a
        third *writer*, not a third caller of `save_config_keys`: that method
        raises on every key in this file, which is why `save_presets_config`
        exists.

        **Re-layered from `base_config`, never layered onto `self.config`.**
        Laying the incoming preset over the outgoing one's result would leave
        every leaf the new preset is silent about still carrying the old
        preset's opinion — a config nobody chose and no file describes.

        The re-seed is the subtle half. Nine `PlannerState` fields are
        *copies* of config values taken at load, so a preset moving one of
        them would otherwise change the config and not the control that
        displays it. They are re-seeded **only where the config value behind
        them actually moved**, which is what lets a pantry row or a training
        session typed a moment ago survive a pick that says nothing about
        either — while a preset that does have an opinion still wins.
        """
        presets_config = dict(self.presets_config)
        presets_config[preset_layer.ACTIVE_KEY] = name
        # Before anything is mutated or written: an unusable pick leaves the
        # session exactly as it was, rather than half-applied.
        config = apply_preset_layer(self.base_config, presets_config)

        self.presets_config = presets_config
        self._relayer(config)

        await repository.save_presets_config({preset_layer.ACTIVE_KEY: name})

    def _relayer(self, config: dict) -> None:
        """Swap in a freshly-layered config and re-seed the fields that copy
        from it — shared by `set_preset` (a new pick) and `save_preset` (an
        edit to the *active* preset), because both change what the active
        preset resolves to and neither may leave a `PlannerState` field
        displaying a value the config no longer holds.

        The re-seed is keyed on the config value *actually* moving, which is
        what lets a pantry row or training session typed a moment ago survive
        a re-layer that says nothing about it — while a preset that does have
        an opinion still wins.
        """
        previous = self.config
        self.config = config

        for field_name, read in PRESET_SEEDED_FIELDS:
            before, after = read(previous), read(config)
            if before == after:
                continue
            setattr(self, field_name, after)
            if field_name == "training_schedule":
                # Moves with it, or the staged bar reports a phantom edit for
                # every session the preset just seeded — the same trap
                # `accept_training_proposal` sidesteps when it writes to disk.
                self._original_training_schedule = [dict(session) for session in after]

        if any(previous.get(key) != config.get(key) for key in PRESET_GRID_SHAPE_KEYS):
            # Which slots are cooked has changed, so the cached preview grid
            # is stale. Only a week with no plan is actually rebuilt from
            # config — a generated one derives its spec from `week_plan.slots`
            # — so this costs no structural edit that has already been made.
            self._spec = None
            self._spec_shape = ()

    # ---- the preset editor (PROMPT-9) -------------------------------------

    def preset_catalog_view(self) -> PresetCatalogView:
        """Every preset, as the Settings editor lists it — label, the active
        mark, and one diff line per override that differs from the **base
        config** (never from the row named `default`, which is why deleting
        `default` leaves every other row's diff intact)."""
        entries = preset_layer.preset_entries(self.presets_config)
        active = preset_layer.active_preset_name(self.presets_config)
        rows = [
            PresetCatalogRow(
                name=name,
                label=preset_layer.preset_label(self.presets_config, name),
                active=(name == active),
                changes=preset_layer.preset_changes(
                    self.base_config, self.presets_config, name
                ),
            )
            for name in entries
        ]
        return PresetCatalogView(rows=rows, active=active)

    def _preset_entry(
        self, name: str, label: str, editor_overrides: dict, is_new: bool
    ) -> dict:
        """The preset dict the editor is about to write.

        The escape hatch, per preset: start from the existing `overrides`,
        drop **every** path the editor manages (an unset field means "not
        overridden"), then merge the user's choices back. A path the editor
        never drew — a hand-added `meal_styles.breakfast`, say — is not in the
        managed set, so it survives untouched. Any non-`label`/`overrides` key
        on the entry (a hand-added note) is carried through as well.
        """
        prior = (
            {}
            if is_new
            else dict(preset_layer.preset_entries(self.presets_config).get(name, {}))
        )
        prior_overrides = prior.get(preset_layer.OVERRIDES_KEY)
        prior_overrides = (
            dict(prior_overrides) if isinstance(prior_overrides, dict) else {}
        )
        managed = set(preset_editor_field_paths(self.base_config))
        kept = {p: v for p, v in prior_overrides.items() if p not in managed}
        merged = {**kept, **{p: v for p, v in editor_overrides.items() if p in managed}}

        other = {
            k: v
            for k, v in prior.items()
            if k not in (preset_layer.LABEL_KEY, preset_layer.OVERRIDES_KEY)
        }
        return {
            preset_layer.LABEL_KEY: (label or "").strip() or name,
            preset_layer.OVERRIDES_KEY: merged,
            **other,
        }

    def _preset_map(self) -> dict:
        """The `presets` map exactly as the file holds it — the raw dict, not
        `preset_entries`' filtered view, so a save round-trips every sibling
        verbatim (including one this code has never parsed)."""
        raw = self.presets_config.get(preset_layer.PRESETS_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    def _candidate_presets(self, name: str, entry: dict) -> dict:
        """A whole `presets.json` document with `entry` in place and `name`
        active, for `resolve_preset_layer` to check — every *other* preset
        round-tripped verbatim so a save cannot drop one."""
        return {
            preset_layer.ACTIVE_KEY: name,
            preset_layer.PRESETS_KEY: {**self._preset_map(), name: entry},
        }

    def preview_preset(
        self, *, name: str, label: str, editor_overrides: dict, is_new: bool
    ) -> PresetPreview:
        """What this preset resolves to, on demand — the diff against the base
        config and the resolved per-day macro targets beside it.

        Pure: no `repository`, no disk. `resolve_preset_layer` is the same
        check `save_preset` runs, so a preview that comes back clean is a
        preview of a preset that will save.
        """
        entry = self._preset_entry(name, label, editor_overrides, is_new)
        doc = self._candidate_presets(name, entry)
        config, failures = resolve_preset_layer(self.base_config, doc)
        if failures:
            return PresetPreview(
                ok=False,
                failures=[failure.message for failure in failures],
                changes=[],
                day_targets=[],
                identical=False,
            )
        changes = preset_layer.preset_changes(self.base_config, doc, name)
        base_cfg = load_app_config(self.base_config)
        day_targets = [
            PresetDayTargets(
                day=day,
                base=_slim_targets(calculate_daily_targets(day, base_cfg)),
                preset=_slim_targets(calculate_daily_targets(day, config)),
            )
            for day in base_cfg.get("weekly_schedule", {})
        ]
        return PresetPreview(
            ok=True,
            failures=[],
            changes=changes,
            day_targets=day_targets,
            identical=not changes,
        )

    def preview_week_shape(self, week_shape_draft: dict) -> WeekShapePreview:
        """Run the one shared validator/applier against `week_shape_draft` —
        Task 1.2d's "Preview" button, over the `week_shape` field's own
        draft (`{"batches": [...], "freezer_draws": [...]}`), never the
        whole preset.

        Same two functions the loader and `generate_and_store_week` use,
        in the same order: `planner.week_shape_errors` first — an
        incoherent draft (unknown meal type, a gap in `serves`, two records
        claiming one slot) never reaches the applier, exactly as it never
        reaches generation — then `week.apply_week_shape` over a throwaway
        spec built the same way `default_week_spec` always is.

        Pure and read-only: no `repository`, no disk, no model. It never
        touches `self.spec`/`self._spec` (no live-spec mutation — the week
        canvas has nothing to do with a preset edit), and any freezer
        reservation `apply_week_shape` makes is local to its own discarded
        return value, never written back to `self.freezer`.
        """
        config = dict(self.base_config, week_shape=week_shape_draft)
        spec = default_week_spec(config, self.week_start, self.servings)
        prep_day = resolve_prep_day(spec.days, config)
        errors = week_shape_errors(config, prep_day)
        if errors:
            return WeekShapePreview(ok=False, errors=errors, batch_anchors={}, warnings=[])
        application = apply_week_shape(spec, week_shape_draft, config, prep_day, self.freezer)
        return WeekShapePreview(
            ok=True,
            errors=[],
            batch_anchors=application.batch_anchors,
            warnings=application.warnings,
        )

    async def save_preset(
        self,
        repository: LocalJSONRepository,
        *,
        name: str,
        label: str,
        editor_overrides: dict,
        is_new: bool,
    ) -> List[str]:
        """Write one preset. Returns the failure messages, or `[]` on success.

        Validated through `resolve_preset_layer` — the loader's own check,
        returning `PresetFailure`s instead of raising — and **nothing is
        written when it fails** (design-03 §4.2: a UI that writes `config/`
        must validate before it saves, because this app's fail-loudly policy
        otherwise surfaces the mistake as a raise on the *next* start).

        The **fourth** writer to `config/`, and the second through
        `save_presets_config`; like the other three it persists a standing
        choice, and unlike them it can write arbitrary structure — which is
        the whole reason the validator exists.
        """
        name = (name or "").strip()
        if not name:
            return ["A preset needs a name."]
        existing = preset_layer.preset_entries(self.presets_config)
        if is_new and name in existing:
            return [f"A preset named '{name}' already exists."]
        if not is_new and name not in existing:
            return [f"No preset named '{name}' to edit."]

        entry = self._preset_entry(name, label, editor_overrides, is_new)
        doc = self._candidate_presets(name, entry)
        _config, failures = resolve_preset_layer(self.base_config, doc)
        if failures:
            return [failure.message for failure in failures]

        new_map = doc[preset_layer.PRESETS_KEY]
        await repository.save_presets_config({preset_layer.PRESETS_KEY: new_map})
        self.presets_config = {
            **self.presets_config,
            preset_layer.PRESETS_KEY: new_map,
        }
        if name == preset_layer.active_preset_name(self.presets_config):
            # Editing the active preset changes what it resolves to, so the
            # session's config and every field seeded from it must move too —
            # the same re-layer `set_preset` does for a new pick.
            self._relayer(apply_preset_layer(self.base_config, self.presets_config))
        return []

    async def delete_preset(
        self, repository: LocalJSONRepository, name: str
    ) -> List[str]:
        """Remove a preset. Returns failure messages, or `[]` on success.

        Deleting the **active** preset is refused: deletion must never
        silently change what the week plans against, and `active` on disk
        must always be absent, null, or the name of a preset that exists.
        Switch the weekly pick first.
        """
        active = preset_layer.active_preset_name(self.presets_config)
        if name == active:
            return ["Can't delete the active preset — switch the weekly pick first."]
        existing = self._preset_map()
        if name not in existing:
            return [f"No preset named '{name}'."]
        new_map = {key: value for key, value in existing.items() if key != name}
        await repository.save_presets_config({preset_layer.PRESETS_KEY: new_map})
        self.presets_config = {
            **self.presets_config,
            preset_layer.PRESETS_KEY: new_map,
        }
        return []

    async def set_target_mode(
        self, repository: LocalJSONRepository, macro: str, mode: str
    ) -> None:
        """Switch one macro between the engine's number and the file's, and save.

        The only thing in the UI besides generation that writes to `config/`,
        and deliberately so: this is a standing setting, not an input to the
        next run. Everything in `pending_changes()` is "this week is
        different" and is right to evaporate on reload; "protein is a number
        I choose" is not, and a toggle that reset every time the page loaded
        would be a worse answer to "where do these numbers come from" than
        the silence it replaced.

        Switching to `manual` seeds `weekly_schedule` from what the engine
        currently computes, rather than exposing whatever stale figure the
        file still holds. Those two had drifted a long way — a Thursday
        stated at 1000 kcal against a computed 1722 — and handing back the
        stale number as "your manual target" would look like the toggle had
        changed the plan rather than merely changed who decides it.
        """
        if macro not in TARGET_MODE_MACROS:
            raise ValueError(
                f"'{macro}' is not a switchable target. "
                f"Switchable: {list(TARGET_MODE_MACROS)}"
            )
        if mode not in (TARGET_MODE_AUTO, TARGET_MODE_MANUAL):
            raise ValueError(f"'{mode}' is not a target mode.")

        updates: dict = {}
        if mode == TARGET_MODE_MANUAL and self.target_modes.get(macro) != mode:
            # Read the computed figures *before* flipping the mode, or the
            # seed reads back the file value this is meant to replace.
            seeded = {day: self.planned_targets(day)[macro] for day in self.days}
            schedule = {
                day: dict(values, **({macro: seeded[day]} if day in seeded else {}))
                for day, values in self.config["weekly_schedule"].items()
            }
            self.config = dict(self.config, weekly_schedule=schedule)
            updates["weekly_schedule"] = schedule

        self.target_modes[macro] = mode
        self.config = dict(self.config, target_modes=dict(self.target_modes))
        updates["target_modes"] = dict(self.target_modes)
        await repository.save_config_keys(updates)

    def set_manual_target(
        self, day: str, macro: str, value: float
    ) -> None:
        """Edit a manual macro's stored per-day value, in memory.

        Distinct from `set_target`, which stages a transient override for the
        next run only. This changes the standing number a `manual` macro is
        read from, so it needs `save_manual_targets` to reach disk — kept
        apart so a half-typed digit doesn't write a file per keystroke.
        """
        schedule = {
            existing_day: dict(values)
            for existing_day, values in self.config["weekly_schedule"].items()
        }
        if day not in schedule:
            raise ValueError(f"'{day}' is not in weekly_schedule.")
        schedule[day][macro] = float(value)
        self.config = dict(self.config, weekly_schedule=schedule)

    async def save_manual_targets(self, repository: LocalJSONRepository) -> None:
        """Persist `weekly_schedule` as it currently stands."""
        await repository.save_config_keys(
            {"weekly_schedule": self.config["weekly_schedule"]}
        )

    def clear_targets(self, day: Optional[str] = None) -> None:
        """Drop one day's overrides, or the whole week's."""
        if day is None:
            self.target_overrides.clear()
        else:
            self.target_overrides.pop(day, None)

    def estimate_burn(self, session_type: str, duration_minutes: float) -> Optional[float]:
        """A MET-based default for a session's `estimated_burn_kcal`, or None.

        None-safe wrapper around `nutrition_engine.estimate_session_burn_kcal`
        — `self.weight_kg` is None on a fresh checkout with no weigh-in and no
        `current_weight_kg` set, and this degrades to "no estimate" rather
        than raising, the same tolerance `planned_targets` extends when the
        body isn't known yet.
        """
        if not self.weight_kg:
            return None
        return estimate_session_burn_kcal(session_type, duration_minutes, self.weight_kg)

    def add_training_session(self) -> None:
        """Append a new workout row with sane defaults, ready to edit in place."""
        default_type = TRAINING_TYPES[0]
        default_duration = 60
        self.training_schedule.append(
            {
                "day": self.days[0] if self.days else "Monday",
                "time": "07:00",
                "type": default_type,
                "duration_minutes": default_duration,
                # A real MET-derived starting point, not the flat 300 kcal
                # this used to hardcode — see CLAUDE.md's "Derive the
                # training burn". Falls back to that same flat guess only
                # when no weight is available to derive one from.
                "estimated_burn_kcal": self.estimate_burn(default_type, default_duration) or 300,
            }
        )

    def remove_training_session(self, index: int) -> None:
        if 0 <= index < len(self.training_schedule):
            self.training_schedule.pop(index)

    def training_proposals(self) -> TrainingScheduleProposal:
        """What Garmin's recorded activity suggests this week should be.

        Reads `activity_log` off the biometrics `.load()` already fetched, so
        it costs no I/O and can be called from a synchronous repaint — the
        same reason `planning_config()` keeps the weigh-in series around.

        Diffed against the **staged** `training_schedule`, not the file's, so
        a session accepted (or typed in by hand) a moment ago stops being
        proposed immediately rather than at the next page load. Dismissed
        proposals are filtered here rather than inside the engine: which
        suggestions this tab has waved away is a session fact, and
        `propose_training_schedule` is a pure function of stored data — the
        same line `PlannerState` already draws against the API's read routes.
        """
        biometrics = self.biometrics or {}
        proposal = propose_training_schedule(
            biometrics.get("activity_log") or [],
            self.training_schedule,
            date.today(),
            weight_kg=self.weight_kg,
            checked_through=(biometrics.get("sync_checkpoints") or {}).get("garmin"),
        )
        # A drop is an edit to the *file*, so it may only name a session the
        # file actually holds. Without this, a session typed into the drawer
        # moments ago — which Garmin has of course never recorded — is offered
        # for removal on the next repaint, which reads as the app arguing with
        # an edit still under the cursor. Additions keep diffing against the
        # staged list, so accepting one stops it being re-offered immediately.
        declared = {
            (session.get("day"), session.get("time"), session.get("type"))
            for session in self.config["training_schedule"]
        }
        kept = [
            session
            for session in proposal.proposals
            if session.key not in self.dismissed_proposals
            and (
                session.kind != PROPOSAL_DROP
                or (session.day, session.time, session.type) in declared
            )
        ]
        if len(kept) == len(proposal.proposals):
            return proposal
        return replace(proposal, proposals=kept)

    def dismiss_training_proposal(self, proposal: ProposedSession) -> None:
        """Wave one proposal away for the rest of this session. Writes nothing."""
        self.dismissed_proposals.add(proposal.key)

    async def accept_training_proposal(
        self, repository: LocalJSONRepository, proposal: ProposedSession
    ) -> None:
        """Apply one proposal to the declared schedule, and save it.

        **This persists**, and it is the second thing in the UI that writes to
        `config/` — `set_target_mode` was the first, on identical reasoning.
        `training_schedule` lives in `config/schedule.json` because it is the
        *standing* week, not an input to the next run: accepting "I train
        Thursday evenings now" and having it evaporate on reload would make
        the accept button a no-op with an animation, and the same proposal
        would be offered again forever. Everything else in the review dialog
        stays session-only and is right to.

        The change is applied to the **file's** list and to the staged one
        separately, rather than persisting whatever the drawer currently
        holds. Those two differ whenever something else is staged, and a
        click on "accept" must not quietly write out an unrelated half-typed
        session sitting two rows below it.

        `_original_training_schedule` moves with the file, so the staged bar
        reports no phantom change for the row that was just accepted while
        still reporting every genuine edit beside it.
        """
        declared = self._apply_proposal(self.config["training_schedule"], proposal)
        self.training_schedule = self._apply_proposal(self.training_schedule, proposal)
        self._original_training_schedule = [dict(session) for session in declared]
        self.config = dict(self.config, training_schedule=declared)
        self.dismissed_proposals.discard(proposal.key)
        await repository.save_config_keys({"training_schedule": declared})

    @staticmethod
    def _apply_proposal(schedule: List[dict], proposal: ProposedSession) -> List[dict]:
        """`schedule` with `proposal` added, or its matching session removed.

        Matched on day/time/type rather than on list position: the drawer's
        rows and the file's are two different lists by the time this runs, and
        an index into one means nothing in the other. A drop that matches
        nothing is a no-op — the session may already have been deleted by
        hand, which is the same outcome the click was asking for.
        """
        if proposal.kind == PROPOSAL_DROP:
            return [
                dict(session)
                for session in schedule
                if (
                    session.get("day"),
                    session.get("time"),
                    session.get("type"),
                )
                != (proposal.day, proposal.time, proposal.type)
            ]
        return [dict(session) for session in schedule] + [proposal.session()]

    def pending_changes(self) -> List[PendingChange]:
        """Everything staged for the next run that config.json doesn't know
        about yet — what the staged-changes bar counts and summarizes.

        Deliberately does **not** clear once a generation has used these
        values: `target_overrides`/`pantry`/`training_schedule` are never
        written back to config.json (see their field comments above), so a
        week just generated from an overridden Wednesday is still, honestly,
        a week generated from settings that disagree with the file — the
        next regenerate uses them again. Only the grid-edit entry
        (`edited`) actually clears after a generation, because saving *is*
        what makes the grid match disk.
        """
        changes: List[PendingChange] = []

        for day in self.days:
            override = self.target_overrides.get(day)
            if not override:
                continue
            # Against the day's resolved baseline, not `weekly_schedule` —
            # the same reason `set_target` diffs that way. On an `auto`
            # macro the file's number is inert, so measuring from it
            # reported "Thu +800 kcal" for an override that had moved the
            # day 78 kcal off what it was actually going to aim at.
            base = self.baseline_targets(day)
            # Calories first since it's the headline number; falling back to
            # whichever key the override actually touched keeps this honest
            # for a protein-only or carb-only edit.
            key = "calories" if "calories" in override else next(iter(override))
            delta = float(override[key]) - float(base.get(key, 0))
            unit = "kcal" if key == "calories" else "g"
            changes.append(PendingChange(f"{day[:3]} {delta:+.0f} {unit}"))

        original_by_signature = {
            (s.get("day"), s.get("time")): s for s in self._original_training_schedule
        }
        current_signatures = {(s.get("day"), s.get("time")) for s in self.training_schedule}
        for session in self.training_schedule:
            signature = (session.get("day"), session.get("time"))
            original = original_by_signature.get(signature)
            if original is None:
                label = TRAINING_TYPE_LABELS.get(session.get("type"), "session")
                changes.append(PendingChange(f"{session.get('day', '?')} {label} added"))
            elif original != session:
                changes.append(PendingChange(f"{session.get('day', '?')} training edited"))
        for signature, original in original_by_signature.items():
            if signature not in current_signatures:
                label = TRAINING_TYPE_LABELS.get(original.get("type"), "session")
                changes.append(PendingChange(f"{original.get('day', '?')} {label} removed"))

        if self.pantry:
            changes.append(PendingChange(f"{len(self.pantry)} pantry item(s)"))

        catalog_names = {
            record.get("id"): (record.get("recipe") or {}).get("name")
            for record in self.recipe_catalog
        }
        for slot in self.spec.cook_slots():
            if slot.recipe_id and slot.recipe_pin_origin == PIN_ORIGIN_USER:
                name = catalog_names.get(slot.recipe_id) or "recipe"
                changes.append(
                    PendingChange(f"{slot_label(slot.id, short=True)}: {name} pinned")
                )

        if self.edited:
            changes.append(PendingChange("grid edited"))

        return changes

    def discard_pending_inputs(self) -> None:
        """Reset target overrides, the training schedule and the pantry list
        back to what config.json/`.load()` gave them — the non-grid inputs in
        `pending_changes()` (targets, training, pantry and recipe pins).

        The staged-changes bar's "Discard pending changes" button pairs this
        with `reload_from_disk` (which handles the fourth quarter, grid
        edits, by re-reading `week_plan.json`): a button sitting right next
        to "Mon +700 kcal" has to actually make that line go away, not just
        the grid-edit part `reload_from_disk` alone ever touched. Unlike a
        *successful generation* — which deliberately leaves these three
        alone, see `pending_changes()`'s own docstring — this is the "give up
        on everything I've staged" action, so it is allowed to be the
        stronger of the two.
        """
        self.clear_targets()
        self.training_schedule = [dict(session) for session in self._original_training_schedule]
        self.pantry = pantry_rows(self.config)
        cleared = self.spec
        for slot in list(cleared.cook_slots()):
            if slot.recipe_id and slot.recipe_pin_origin == PIN_ORIGIN_USER:
                cleared = pin_recipe(cleared, slot.id, None)
        self._spec = cleared
        self._spec_shape = self._shape()

    def has_training(self, day: str) -> bool:
        """Whether `day` carries a session that actually buys calories back.

        Mirrors `apply_training_adjustments`' own filter — a `rest` entry, or
        any session logged at zero burn, expands no budget and pins no meal,
        so it must not read as one here either. That is the same distinction
        `TrainingView.is_rest` draws for the Today tab's context strip.

        Counting a rest day as training had two visible effects: the
        telemetry header drew an emerald ⚡ on an explicitly scheduled rest
        day, and — because `targets_for` branches on this — every day of a
        week with a rest entry took the live-preview path, which is how the
        stored plan's targets became unreachable from the header.
        """
        return any(
            session.get("day") == day
            and session.get("type") != "rest"
            and float(session.get("estimated_burn_kcal", 0) or 0) > 0
            for session in self.training_schedule
        )

    def training_for(self, day: str) -> List["TrainingView"]:
        """`day`'s sessions, earliest first.

        Deliberately reads nothing but `training_schedule`, so it costs a list
        scan rather than a `planning_config()`. `day_context` needs the whole
        config for its per-meal notes and is therefore built once per repaint;
        this is the half the Today tab's day picker calls for **all seven
        days** on that same repaint, to mark which ones train. Folding the two
        together would have made the picker seven `apply_training_adjustments`
        passes over the week.

        Ordered by `planner.clock_minutes` — the same tolerant "HH:MM" read
        that decides which meal gets the post-workout pin, so the strip can't
        order a day differently from the way its pin was chosen. The drawer
        appends a new session to the end of the list, so file order is not
        clock order.
        """
        return sorted(
            (
                TrainingView(
                    day=day,
                    time=str(session.get("time", "")),
                    type=str(session.get("type", "")),
                    label=TRAINING_TYPE_LABELS.get(
                        session.get("type"), humanize(str(session.get("type", "")))
                    ),
                    duration_minutes=int(session.get("duration_minutes") or 0),
                    burn_kcal=float(session.get("estimated_burn_kcal") or 0),
                    # Matches `apply_training_adjustments`' own filter, which
                    # drops a zero-burn session as surely as a typed rest day:
                    # both expand nothing, so both must read as rest rather
                    # than as a workout whose calories went missing.
                    is_rest=(
                        session.get("type") == "rest"
                        or float(session.get("estimated_burn_kcal") or 0) <= 0
                    ),
                )
                for session in self.training_schedule
                if session.get("day") == day
            ),
            key=lambda session: clock_minutes(session.time),
        )

    def training_edited_for(self, day: str) -> bool:
        """Whether this session's training edits have changed `day`'s schedule.

        Diffed against `_original_training_schedule`, the snapshot `.load()`
        takes — the same comparison `pending_changes` makes, so the staged
        bar and the telemetry marker can't disagree about whether a day was
        touched.
        """
        def sessions(schedule: List[dict]) -> List[tuple]:
            return sorted(
                tuple(sorted(session.items()))
                for session in schedule
                if session.get("day") == day
            )

        return sessions(self.training_schedule) != sessions(
            self._original_training_schedule
        )

    def target_is_staged(self, day: str) -> bool:
        """Whether `day` is being measured against a preview rather than the plan.

        True when something *this session staged* changes what the day would
        aim at — a target override, or an edit to that day's training. Both
        are deliberate acts whose whole point is seeing where the week is
        about to move to.

        It deliberately does **not** include "this day has a workout".
        Merely having a session scheduled is the config's standing state, not
        a staged change, and branching on it put six of seven days on the
        live preview while the seventh was measured against the stored plan —
        one row of figures silently computed two different ways. A new
        weigh-in moves the preview for every day at once, so letting it show
        on some days and not others reads as a plan that drifted off target
        on Monday and held on Thursday, which is not what happened.
        """
        return day in self.target_overrides or self.training_edited_for(day)

    def targets_for(self, day: str) -> dict:
        """The denominator the telemetry header measures a day against.

        A staged override — or a staged training edit — wins over the
        generated plan's own targets on purpose: the point of editing either
        before a run is to see how far the current week sits from where you
        are about to aim it, and without that the control would silently do
        nothing until the next generation.

        Otherwise a generated week is measured against what it was actually
        generated for, and an un-generated one against the live preview, so
        the header always has something to divide by. That split is what
        keeps a fresh weigh-in from making an untouched plan look like it
        missed: the body moved, the plan didn't, and re-generating is what
        reconciles them.
        """
        if self.target_is_staged(day):
            return self.planned_targets(day)
        if self.week_plan and day in self.week_plan.targets:
            return self.week_plan.targets[day]
        return self.planned_targets(day)

    def totals_for(self, day: str) -> dict:
        # NUTRIENT_KEYS so the empty-week shape matches what
        # `day_slot_macros` returns once a week exists — a header reading
        # fibre must not have to special-case the un-generated case.
        if not self.week_plan:
            return {key: 0.0 for key in NUTRIENT_KEYS}
        return self.week_plan.day_slot_macros(day)

    def logged_actuals_for(self, day: str) -> Optional[dict]:
        """The `daily_actuals` row Cronometer wrote for `day`'s date, or None.

        **Dated, not weekday-matched, which is why this is not
        `planner.logged_intake_for`.** That function answers the same
        question during *generation*, where a `SlotSpec` carries a weekday
        name and nothing else — so "Thursday" in a week being planned ahead
        is not the Thursday that was logged, and it refuses every day but
        today rather than subtract a meal from a day it was never eaten on.
        A loaded `WeekPlan` carries `week_start_date`, so here every column
        of the grid has a real calendar date and any of them can be matched.

        None for a plan generated before `week_start_date` existed
        (`day_date_iso` says so) and for a date nothing has been logged
        against — the same two answers, because a reader gets the planned
        figure alone in both cases. Last row wins, matching
        `logged_intake_for`: `_upsert_dated_entry` keeps one row per date, so
        a second is only possible in a hand-edited file where the later line
        is the edit.
        """
        iso = self.day_date_iso(day)
        if iso is None:
            return None
        rows = [
            row
            for row in ((self.biometrics or {}).get("daily_actuals") or [])
            if isinstance(row, dict) and str(row.get("date") or "")[:10] == iso
        ]
        return rows[-1] if rows else None

    def fibre_for(self, day: str) -> "FibreView":
        """What the day's recipes carry in fibre, against target, beside what
        was logged.

        The target comes from `targets_for` — the same call every other figure
        in the telemetry row divides by — so fibre reads the day's live preview
        or its stored plan on exactly the rule `target_is_staged` already
        applies to calories and protein. A plan generated before fibre had a
        target has no `fiber_g` in `week_plan.targets`, and gets the bare
        planned figure back, which is what the header printed before this
        existed.
        """
        logged = (self.logged_actuals_for(day) or {}).get("fiber_g")
        target = self.targets_for(day).get("fiber_g")
        return fibre_view(
            float(self.totals_for(day).get("fiber_g") or 0.0),
            float(logged) if isinstance(logged, (int, float)) else None,
            float(target) if isinstance(target, (int, float)) and target > 0 else None,
        )

    def meal_adherence_for(self, day: str) -> "MealAdherenceView":
        """What was marked against `day`'s meals — see `meal_adherence_view`.

        The planned set comes off `spec`, not off `week_plan.cook_events`, so
        a leftover counts and a slot whose generation failed does too: both
        are meals the week intends you to eat, and a failed one is exactly
        the case where "did you eat it" has an interesting answer. Only a
        skip is excluded, because nothing was planned there to adhere to.
        """
        planned = tuple(
            slot_id(slot.day, slot.meal_type)
            for slot in self.spec.slots
            if slot.day == day and slot.mode in (MODE_COOK, MODE_LEFTOVER)
        )
        return meal_adherence_view(
            (self.adherence or {}).get("meals") or [],
            self.day_date_iso(day),
            planned,
        )

    def workout_marks_for(self, day: str) -> List["WorkoutMarkView"]:
        """`day`'s real sessions, against the watch and against any manual mark.

        Rest is filtered here rather than in `workout_marks_view` or in
        `match_recorded_sessions`: `TrainingView.is_rest` is the one place
        that folds a typed `rest` and a zero-burn session together, and both
        of those are days there is nothing to have completed.
        """
        sessions = [s for s in self.training_for(day) if not s.is_rest]
        return workout_marks_view(
            sessions,
            (self.biometrics or {}).get("activity_log") or [],
            (self.adherence or {}).get("workouts") or [],
            self.day_date_iso(day),
        )

    async def mark_meal(
        self, repository: LocalJSONRepository, day: str, meal_type: str, status: str
    ) -> None:
        """Record — or clear — what happened to one planned meal.

        **Clicking the status a slot already carries clears it**, which is
        what makes three buttons a complete control rather than three
        one-way doors: without it a mis-click could be corrected to another
        status but never back to "nobody has said", and absence is a real
        answer here (see `clear_meal_adherence`).

        Persists immediately, unlike every grid edit, which stages until
        Save. A mark is not an input to the next run — nothing generates
        differently because of it — so there is nothing for a staged bar to
        stage, and a mark that vanished on reload would be a tick box that
        does nothing. That is the same test `set_target_mode` and
        `accept_training_proposal` pass, arrived at from the storage side
        rather than the config one.

        A day with no calendar date cannot be marked at all — the UI does not
        offer the buttons, and this refuses rather than filing the mark under
        a key nothing will read back.
        """
        date_iso = self.day_date_iso(day)
        if date_iso is None:
            return
        slot = slot_id(day, meal_type)
        rows = [
            row
            for row in ((self.adherence or {}).get("meals") or [])
            if not (row.get("date") == date_iso and row.get("slot_id") == slot)
        ]
        current = self.meal_adherence_for(day).status_for(slot)
        if current == status:
            self.adherence = dict(self.adherence or {}, meals=rows)
            await repository.clear_meal_adherence(date_iso, slot)
            return

        entry = AdherenceEntry(
            date=date_iso,
            slot_id=slot,
            status=status,
            marked_at=_now_iso(),
        ).model_dump()
        self.adherence = dict(self.adherence or {}, meals=rows + [entry])
        await repository.save_meal_adherence(entry)

    async def mark_workout(
        self, repository: LocalJSONRepository, day: str, mark: "WorkoutMarkView"
    ) -> None:
        """Toggle the manual "I did this" mark for one declared session.

        **Refuses a session the watch already recorded**, rather than merely
        not offering the button: `activity_log` is the answer for those, and
        a stored `completed` row beside it would be a second answer free to
        disagree the moment a re-sync changed one of them — the same
        one-question-one-source rule that keeps derived and stored apart
        throughout this app.
        """
        date_iso = self.day_date_iso(day)
        if date_iso is None or mark.recorded:
            return
        rows = [
            row
            for row in ((self.adherence or {}).get("workouts") or [])
            if not (
                row.get("date") == date_iso and row.get("session_id") == mark.session_id
            )
        ]
        if mark.marked:
            self.adherence = dict(self.adherence or {}, workouts=rows)
            await repository.clear_workout_completion(date_iso, mark.session_id)
            return

        entry = WorkoutCompletion(
            date=date_iso,
            session_id=mark.session_id,
            session_type=mark.session_type,
            completed=True,
            source="manual",
            marked_at=_now_iso(),
        ).model_dump()
        self.adherence = dict(self.adherence or {}, workouts=rows + [entry])
        await repository.save_workout_completion(entry)

    # ---- the declared freezer ledger ---------------------------------------
    # `design-04` §2: a confirmed list, not an inferred count. Every write
    # below persists immediately — like `mark_meal`/`mark_workout` above, a
    # capture that vanished on reload would be a control with no effect —
    # and every one of them funnels through `capture_freezer_item`, the one
    # place `freezer.FreezerItem` gets constructed and saved.

    async def capture_freezer_item(
        self,
        repository: LocalJSONRepository,
        *,
        label: str,
        portions: int,
        cooked_on: str,
        frozen_on: str,
        recipe: Optional[Recipe] = None,
        recipe_id: Optional[str] = None,
        id: Optional[str] = None,  # noqa: A002 - matches FreezerItem's own field name
    ) -> Optional[str]:
        """Snapshot one lot and persist it — the one write both capture
        routes share (`send_to_freezer`, `record_freezer_surplus`), plus the
        row editor's manual add, so none of them can disagree about dating,
        snapshots or ids. Returns why not, or None on success — the same
        convention `set_skip_estimate` and its siblings use.

        `recipe`, when given, supplies the freeze-time snapshot: `per_serving`
        is filtered to `MACRO_KEYS` because a recipe's own `per_serving_macros`
        also carries fibre, which `FreezerItem` does not accept. Omitted for
        a hand-declared item with nothing cooked behind it (`design-04`
        §2.2's third route) — the lot then has no macro snapshot, exactly the
        "neither" case that design section names.

        `id` omitted mints a fresh one (`FreezerItem`'s own default) — the
        manual card capture always wants a new lot. `record_freezer_surplus`
        passes a deterministic one instead, so a repeated click upserts
        rather than duplicating.
        """
        try:
            item = FreezerItem(
                **({"id": id} if id else {}),
                label=label.strip() or "Frozen portions",
                portions=int(portions),
                cooked_on=cooked_on,
                frozen_on=frozen_on,
                storage_class=recipe.storage_class if recipe is not None else None,
                per_serving=(
                    {
                        key: round(float(recipe.per_serving_macros[key]), 1)
                        for key in MACRO_KEYS
                    }
                    if recipe is not None
                    else None
                ),
                recipe_id=recipe_id,
            )
        except ValidationError as exc:
            return f"That isn't a usable freezer entry: {exc}"

        data = item.model_dump()
        await repository.save_freezer_item(data)
        self.freezer = [row for row in self.freezer if row.get("id") != data["id"]] + [data]
        return None

    def freezer_capture_defaults_for(
        self, target_slot_id: str
    ) -> Optional[FreezerCaptureDefaults]:
        """Pre-fill for the recipe-card "send to freezer" dialog, or None
        when there is nothing generated at that slot to capture."""
        plan = self.week_plan
        if plan is None:
            return None
        event = plan.by_slot().get(target_slot_id)
        slot = next((s for s in plan.slots if s.id == target_slot_id), None)
        if event is None or slot is None:
            return None
        return freezer_capture_defaults(plan, event, slot, self.config)

    async def send_to_freezer(
        self,
        repository: LocalJSONRepository,
        target_slot_id: str,
        *,
        portions: int,
        frozen_on: str,
        label: Optional[str] = None,
    ) -> Optional[str]:
        """The recipe-card "send to freezer" action.

        Label, recipe id and cook date come off the cook event itself — a
        capture of something already on screen, not a blank form — leaving
        only the count and the freeze date to state. Always mints a fresh
        id: unlike the surplus route's "Record", the same dish may
        genuinely be sent to the freezer more than once across different
        sessions, and each of those really is a new lot.
        """
        plan = self.week_plan
        if plan is None:
            return "Generate a week before sending anything to the freezer."
        event = plan.by_slot().get(target_slot_id)
        slot = next((s for s in plan.slots if s.id == target_slot_id), None)
        if event is None or slot is None:
            return f"{slot_label(target_slot_id)} hasn't been generated yet."
        cooked_on = freezer_cooked_on(plan, event, self.config)
        if cooked_on is None:
            return "This plan has no real calendar date to freeze against."
        return await self.capture_freezer_item(
            repository,
            label=label or event.recipe.name,
            portions=portions,
            cooked_on=cooked_on,
            frozen_on=frozen_on,
            recipe=event.recipe,
            recipe_id=slot.recipe_id,
        )

    def pending_freezer_surplus(self) -> List[FreezerSurplusView]:
        """Every cook event with declared spare portions not yet recorded.

        Reads `week_plan.slots` — the plan's own record of what actually
        generated — never the live, possibly-staged `self.spec`: a pending
        lot is a fact about a cook that happened, not about an edit in
        progress. Filters out anything already written to `data/freezer.json`
        (matched by `freezer_surplus_id`), which is what makes a recorded lot
        drop off this list the moment `record_freezer_surplus` succeeds
        rather than staying to invite a second write — a successful
        generation on its own writes zero rows here, per `design-04` §2.2's
        "not confirming is a legitimate end state".
        """
        plan = self.week_plan
        if plan is None:
            return []
        recorded_ids = {row.get("id") for row in self.freezer}
        slots_by_id = {slot.id: slot for slot in plan.slots}
        views: List[FreezerSurplusView] = []
        for event in plan.cook_events:
            slot = slots_by_id.get(event.slot_id)
            if slot is None or slot.extra_portions <= 0:
                continue
            cooked_on = freezer_cooked_on(plan, event, self.config)
            if cooked_on is None:
                continue
            if freezer_surplus_id(event.slot_id, cooked_on) in recorded_ids:
                continue
            views.append(
                FreezerSurplusView(
                    slot_id=event.slot_id,
                    label=event.recipe.name,
                    claim_count=len(event.eaten_by),
                    servings_per_meal=plan.servings_per_meal,
                    extra_portions=slot.extra_portions,
                    total_portions=event.portions,
                    cooked_on=cooked_on,
                )
            )
        return views

    def freezer_surplus_for(self, target_slot_id: str) -> Optional[FreezerSurplusView]:
        """One card's pending surplus, if it still has one — the gate for
        its "Record" pill."""
        return next(
            (view for view in self.pending_freezer_surplus() if view.slot_id == target_slot_id),
            None,
        )

    async def record_freezer_surplus(
        self, repository: LocalJSONRepository, target_slot_id: str
    ) -> Optional[str]:
        """The pending-surplus card's "Record" action.

        Snapshots the recipe now, at click time — never at generation, so a
        week that plans a surplus but is never confirmed here writes
        nothing. The portion count is the slot's declared spare amount;
        correcting a batch that came out smaller is a later edit through
        the freezer row editor (`update_freezer_item`), not a parameter
        here.

        The id is deterministic (`freezer_surplus_id`), not a fresh uuid, so
        a second click on the same still-pending card upserts the same row
        instead of writing a second lot — `capture_freezer_item`/
        `save_freezer_item` are both plain upserts, so the no-duplicate
        guarantee has to come from the id, not from a click guard here.
        """
        plan = self.week_plan
        if plan is None:
            return "Generate a week before recording a surplus."
        event = plan.by_slot().get(target_slot_id)
        slot = next((s for s in plan.slots if s.id == target_slot_id), None)
        if event is None or slot is None or slot.extra_portions <= 0:
            return f"{slot_label(target_slot_id)} has no declared surplus to freeze."
        cooked_on = freezer_cooked_on(plan, event, self.config)
        if cooked_on is None:
            return "This plan has no real calendar date to freeze against."
        return await self.capture_freezer_item(
            repository,
            id=freezer_surplus_id(target_slot_id, cooked_on),
            label=event.recipe.name,
            portions=slot.extra_portions,
            cooked_on=cooked_on,
            frozen_on=date.today().isoformat(),
            recipe=event.recipe,
            recipe_id=slot.recipe_id,
        )

    async def add_freezer_item(self, repository: LocalJSONRepository) -> Optional[str]:
        """Manual add, in the review dialog's row editor — food this app
        never cooked. No recipe behind it, so the row starts with no macro
        snapshot (`design-04` §2.2's "neither" case: whatever eventually
        draws on it contributes 0 and shows a visible shortfall, never a
        guess)."""
        today = date.today().isoformat()
        return await self.capture_freezer_item(
            repository, label="New item", portions=1, cooked_on=today, frozen_on=today
        )

    async def update_freezer_item(
        self, repository: LocalJSONRepository, item_id: str, **fields
    ) -> Optional[str]:
        """Edit one declared lot in place — the row editor's own field
        writes.

        Merges into the stored row rather than reconstructing from a recipe,
        so correcting a label or a count never touches a snapshot only a
        "send to freezer"/"Record" click may set — `FreezerItem`'s
        `storage_class`/`per_serving` are freeze-time snapshots, not
        re-derivable from a field edit.
        """
        existing = next((row for row in self.freezer if row.get("id") == item_id), None)
        if existing is None:
            return "That freezer item no longer exists."
        try:
            item = FreezerItem.model_validate(dict(existing, **fields))
        except ValidationError as exc:
            return f"That isn't a usable freezer entry: {exc}"
        data = item.model_dump()
        await repository.save_freezer_item(data)
        self.freezer = [data if row.get("id") == item_id else row for row in self.freezer]
        return None

    async def remove_freezer_item(self, repository: LocalJSONRepository, item_id: str) -> None:
        """Delete one declared lot outright — the same tolerance
        `delete_freezer_item` extends to an id already gone."""
        self.freezer = [row for row in self.freezer if row.get("id") != item_id]
        await repository.delete_freezer_item(item_id)

    def slot_views(self) -> Dict[str, SlotView]:
        """slot_id -> SlotView for every slot in the week."""
        spec = self.spec
        events = self.week_plan.by_slot() if self.week_plan else {}
        views: Dict[str, SlotView] = {}

        # Hoisted: both are whole-week scans, and calling them per slot made
        # rendering 28 cards quadratic in the size of the week.
        portions = portions_for(spec)
        claims = eaten_on(spec)

        # One chain per cook that anything else eats — a cook nobody inherits
        # from is not a link, so it gets no colour and no marker.
        chains = {
            slot.id: index
            for index, slot in enumerate(
                cook for cook in spec.cook_slots() if len(claims.get(cook.id, [])) > 1
            )
        }

        for slot in spec.slots:
            if slot.mode == MODE_SKIP:
                estimate = slot.skip_estimate
                views[slot.id] = SlotView(
                    day=slot.day,
                    meal_type=slot.meal_type,
                    status=STATUS_SKIP,
                    # An estimated skip is a meal that happened, so it says so
                    # rather than reading "Skipped" beside a calorie figure.
                    title="Eaten out" if estimate else "Skipped",
                    mode=slot.mode,
                    skip_estimate=estimate,
                )
                continue

            # A leftover shows the *source* recipe: it is the same food, so
            # resolving through `source` here is what makes the two cards
            # visibly the same dish.
            source_id = slot.id if slot.mode == MODE_COOK else slot.source
            event = events.get(source_id or "")
            source_label = slot_label(slot.source, short=True) if slot.source else ""

            link_target, link_error = "", ""
            if slot.mode == MODE_COOK and slot.meal_type == LINK_SOURCE_MEAL:
                link_target = next_day_slot_id(spec, slot.day, LINK_TARGET_MEAL) or ""
                link_error = (
                    leftover_link_error(spec, link_target, slot.id) or ""
                    if link_target
                    else f"{slot.day} is the last day of the week."
                )

            common = dict(
                day=slot.day,
                meal_type=slot.meal_type,
                mode=slot.mode,
                style=humanize(slot.style),
                cuisine=humanize(slot.cuisine),
                portions=portions.get(source_id or "", 0),
                extra_portions=slot.extra_portions,
                source_label=source_label if slot.mode == MODE_LEFTOVER else "",
                source_id=(slot.source or "") if slot.mode == MODE_LEFTOVER else "",
                chain=chains.get(source_id or ""),
                feeds=(
                    [slot_label(value, short=True) for value in claims.get(slot.id, [])[1:]]
                    if slot.mode == MODE_COOK
                    else []
                ),
                link_target=link_target,
                link_error=link_error,
            )

            if event is None:
                # Two different absences. With a plan loaded, a missing event
                # means this slot's cook (or the cook it points at) is in
                # WeekPlan.failures — that is the red "not generated" state,
                # and it must stay visible so the gap is obvious rather than
                # silently blank.
                # With no plan at all nothing has failed: the grid is a
                # *preview* of the shape about to be generated, so the slot
                # keeps its planned mode and only the recipe is missing.
                views[slot.id] = SlotView(
                    status=(
                        STATUS_MISSING
                        if self.week_plan
                        else (STATUS_COOK if slot.mode == MODE_COOK else STATUS_LEFTOVER)
                    ),
                    title="Not generated" if self.week_plan else "To be generated",
                    **common,
                )
                continue

            # A slot eating a Sunday-prepped batch gets a badge — see
            # `planner.is_sunday_prepped`. That includes the batch's own
            # anchor slot (MODE_COOK), not just the leftovers eating it:
            # the anchor's own recipe was actually cooked in the Sunday
            # session too (its grid day is just where the leftover chain
            # has to start), so it needs the same "prepped ahead" signal a
            # downstream leftover gets, not just the from-scratch cook it
            # would otherwise look like. "fridge" vs. "freezer" mirrors the
            # same span-vs-fridge-safe-days threshold `storage_note` used to
            # write the batch's own storage note, and counts from the same
            # day it does (`week.cook_day_index`) so the badge and the note
            # can't disagree about how old the food is.
            prep_badge, prep_origin = "", ""
            sunday_prepped = is_sunday_prepped(event, self.week_plan)
            # Computed beside it rather than inside the branch below: the
            # badge asks "how old is this food" and `prep_minutes` further
            # down asks "how long does it take", and both turn on the same
            # narrower question of whether the pan was actually on before the
            # week started.
            prepped_ahead = is_prepped_ahead(event, self.week_plan)
            if sunday_prepped:
                # The dish's own window, not one global number — a rice tray
                # bake reaches its freeze point two days before a stew does,
                # so two cards in one week can now legitimately differ. The
                # identical `fridge_day_gaps` call `planner.storage_note`
                # makes for the same event, deliberately: that pair has had to
                # be reconciled twice already (`cook_day_index`, then the
                # prep-day origin) and a per-dish window is its third chance
                # to drift.
                safe_day_gaps = fridge_day_gaps(
                    event.recipe.storage_class, self.config
                )
                # Per-slot distance from its cook day, not `span_days`'s
                # whole-batch span to its *farthest* eater — a Tuesday
                # portion of a batch that runs to next Sunday is still
                # fridge-fresh even though the Sunday portion isn't. The
                # anchor's own slot is 1, not 0, for a prep-session batch:
                # it was cooked the day before the week started.
                days_since_cook = spec.day_index(slot.day) - cook_day_index(
                    spec, event.day, prepped_ahead
                )
                frozen = days_since_cook >= safe_day_gaps
                prep_badge = "freezer" if frozen else "fridge"
                storage_suffix = (
                    " — frozen, thaw ahead of eating" if frozen else " — kept refrigerated"
                )
                prep_origin = (
                    f"From the Sunday prep session: {event.recipe.name} "
                    f"({event.portions} portions, cooked {event.day})" + storage_suffix
                    if slot.mode == MODE_LEFTOVER
                    # The anchor's own slot: "cooked {event.day}" would name
                    # itself, which reads as circular rather than informative.
                    else f"Prepped ahead in the Sunday prep session ({event.portions} portions)"
                    + storage_suffix
                )

            # Style and cuisine come off the event, which recorded whatever
            # `resolve_auto_choices` settled on — the slot may still say
            # "auto".
            views[slot.id] = SlotView(
                status=STATUS_COOK if slot.mode == MODE_COOK else STATUS_LEFTOVER,
                title=event.recipe.name,
                prep_minutes=(
                    weeknight_prep_minutes(event, self.week_plan)
                    if slot.mode == MODE_LEFTOVER
                    # The shake candidate rides along in the same session
                    # (`find_shake_candidate`) but is never cooked ahead —
                    # each morning genuinely blends it fresh — so it keeps
                    # its own prep time while the batch anchors collapse to
                    # the reheat estimate. `is_prepped_ahead` is exactly that
                    # distinction, and asking it rather than re-deriving it
                    # is the fix: this used to test `event.meal_type ==
                    # "dinner"`, which was a faithful proxy for "cooked on
                    # prep day" only while the long cook was the sole anchor.
                    # `apply_batch_selections` anchors bulk prep on **lunch**,
                    # so that card showed the full from-scratch cook time for
                    # a dish that came out of the pan the day before the week
                    # started — and a third batch axis would have reopened it
                    # a third time.
                    else (
                        SUNDAY_PREP_REHEAT_MINUTES
                        if prepped_ahead
                        else event.recipe.prep_time_minutes
                    )
                ),
                macros=event.recipe.per_serving_macros,
                recipe=event.recipe,
                prep_badge=prep_badge,
                prep_origin=prep_origin,
                **{
                    **common,
                    "style": humanize(event.style),
                    "cuisine": humanize(event.cuisine),
                },
            )

        return views


def location_view(
    config: dict, meal_types_: List[str], day: str
) -> Optional[LocationView]:
    """Where `day` is spent, per `base_schedule`/`location_rules`, or None.

    Split out of `day_context` when the Settings destination's location page
    (phase 6e of `ui-redesign.md`) needed the same seven answers without the
    six other things `day_context` computes: its `training_notes` are only
    reachable through `planning_config()`, so building the whole context for
    each of seven days would be seven `apply_training_adjustments` passes over
    the week to print a table of default locations. This takes an
    already-loaded config instead, so the page pays for one.

    A named location with no `location_rules` entry — "Home" in the shipped
    schedule — is still a `LocationView`, not None: it says where the day is
    spent, which is the question, and an empty rule simply constrains nothing.
    None means the config has no `base_schedule` at all, which is every config
    predating that feature and is the one case with genuinely nothing to say.
    """
    where = location_for(config, day)
    if not where:
        return None
    rule = location_rule(config, day)
    max_prep = rule.get("max_prep_minutes")
    return LocationView(
        name=where,
        restrictions=list(rule.get("restrictions") or []),
        notes=str(rule.get("notes") or "").strip(),
        max_prep_minutes=int(max_prep) if max_prep is not None else None,
        meal_modes={
            meal_type: rule[f"{meal_type}_mode"]
            for meal_type in meal_types_
            if f"{meal_type}_mode" in rule
        },
        skip_estimates={
            meal_type: rule[f"{meal_type}_skip_estimate"]
            for meal_type in meal_types_
            if rule.get(f"{meal_type}_mode") == MODE_SKIP
            and rule.get(f"{meal_type}_skip_estimate")
        },
    )


def day_context(state: PlannerState, day: str) -> DayContext:
    """Where `day` is spent and what is being trained that day.

    Built from the config the *next run* would use (`planning_config()`), not
    the file on disk, so the drawer's training edits reach this the same way
    they already reach `targets_for` — a session added in the drawer changes
    the day's budget and its post-workout pin, and a Today tab that kept
    showing the file's schedule would contradict the calorie bar directly
    above it. Location has no drawer control today and so is identical in
    both, but reading one config rather than two is what keeps it that way if
    one ever lands.

    Everything here degrades to "say nothing": a config with no
    `base_schedule` (every config predating that feature) yields
    `location=None`, and a day nobody trains on yields no sessions and no
    notes. That is the same opt-in tolerance `week.location_for` and
    `apply_training_adjustments` already extend, kept rather than turned into
    an empty-state the Today tab would have to render around.
    """
    config = state.planning_config()
    location = location_view(config, state.meal_types, day)
    sessions = state.training_for(day)

    notes = (config.get("training_notes") or {}).get(day) or {}
    meal_notes = {}
    for meal_type, note in notes.items():
        for kind, prefix in TRAINING_NOTE_PREFIXES.items():
            if note.startswith(prefix):
                reason = note[len(prefix):]
                meal_notes[meal_type] = TrainingNote(
                    kind=kind,
                    text=(reason[:-1] if reason.endswith("]") else reason).strip(),
                )
                break

    return DayContext(location=location, sessions=sessions, meal_notes=meal_notes)


def slot_target_budget(state: PlannerState, view: SlotView) -> Optional[dict]:
    """The per-serving macro budget generation would aim this slot at right now.

    Mirrors `generate_day`'s own split rather than a rough per-day average:
    leftover slots eaten that day have their macros subtracted from the day
    target first, then the remainder is divided across the day's cook slots
    by `split_targets` — the same function and the same order of operations
    generation uses. So the swap modal's "target" column is the number a
    fresh generation would have handed the model for this slot, not an
    approximation of it.
    """
    if not view.day:
        return None
    spec = state.spec
    config = state.planning_config()
    day_slots = [slot for slot in spec.slots if slot.day == view.day]
    cook_slots = [slot for slot in day_slots if slot.mode == MODE_COOK]
    if not cook_slots:
        return None

    # `planner.day_multiplicity`, not a local count off `eaten_on`: the latter
    # counts every slot claiming a cook across the WHOLE WEEK, so a dinner
    # feeding tomorrow's lunch scored 2 here and 1 in generation. That inflated
    # `split_targets`'s total weight and understated every budget on the day by
    # ~30%. Sharing generation's own function is what keeps this honest.
    multiplicity = day_multiplicity(spec, view.day)

    events = state.week_plan.by_slot() if state.week_plan else {}
    carried = {key: 0.0 for key in MACRO_KEYS}
    for slot in day_slots:
        if slot.mode != MODE_LEFTOVER:
            continue
        event = events.get(slot.source or "")
        if event is None:
            continue
        per_serving = event.recipe.per_serving_macros
        for key in MACRO_KEYS:
            carried[key] += per_serving[key]

    targets = state.targets_for(view.day)
    remaining = {key: max(0.0, float(targets[key]) - carried[key]) for key in MACRO_KEYS}
    overrides = meal_overrides_for(view.day, config)
    budgets = split_targets(remaining, cook_slots, multiplicity, config, overrides)

    slot = spec.by_id().get(view.id)
    if slot is None:
        return None
    source_id = slot.id if slot.mode == MODE_COOK else slot.source
    return budgets.get(source_id or "")


# ---- the Library's row view model ----------------------------------------
# The Library destination is a table, and every column in it except the name
# is *derived*: per-serving macros off a validated `Recipe`, and a last-cooked
# date read out of `meal_history.json`. Both derivations live here rather than
# in `ui_catalog_browser` for the standing reason this module exists — it is
# the only UI module with tests, and a column quietly reading the wrong field
# is exactly the failure an element-tree harness could not see.


def catalog_slot_view(entry: dict) -> Optional[SlotView]:
    """A catalog entry, reshaped into the same `SlotView` the grid's own
    cards render from — `ui_cards.recipe_detail` reads a `SlotView`, not a
    raw recipe dict, and building a second detail renderer would be a second
    place for the two to disagree about how a recipe reads. There is no
    day/meal-type slot behind a catalog entry, so those fields are left at
    their defaults; `status=STATUS_COOK` is the closest of the four statuses
    to "a real, cookable recipe", which is all a catalog entry ever claims to
    be. Returns None for a stored recipe that no longer validates (a manual
    edit to the JSON, say), so a bad entry can still be deleted from the
    table without crashing it on click.

    It moved here from `ui_catalog_browser._detail_view` when the Library
    became a table: the row needs the same validated `Recipe` its macro
    columns are read off, so validating once per row and carrying the view on
    it replaces validating once to draw the card and again on every click.
    """
    try:
        recipe = Recipe.model_validate(entry["recipe"])
    except Exception:
        return None
    return SlotView(
        day="",
        meal_type=recipe.meal_type,
        status=STATUS_COOK,
        title=recipe.name,
        mode=MODE_COOK,
        portions=recipe.servings,
        prep_minutes=recipe.prep_time_minutes,
        macros=recipe.per_serving_macros,
        recipe=recipe,
    )


def catalog_history_window(history: List[dict]) -> Optional[str]:
    """The earliest date the retained history still covers, or None.

    This is what a blank Last eaten cell *means*. `record_week_history` keeps
    `history_max_entries` (28) day entries, so the window is about four weeks:
    a recipe absent from it was either never cooked or last cooked before the
    window opened, and nothing stored can tell those two apart. Printing the
    window's first date once above the table says which question the column is
    answering, rather than leaving an em dash to be read as "never".
    """
    dates = sorted(
        str(entry.get("date") or "")[:10] for entry in history if entry.get("date")
    )
    return dates[0] if dates else None


def last_eaten_index(history: List[dict], today: date) -> Dict[str, date]:
    """recipe name -> the most recent date it was cooked on, never later than
    `today`.

    Keyed by *name* because that is the only handle `meal_history.json` keeps:
    `record_week_history` stores `recipe_names`, never ids or content keys, so
    a renamed catalog entry legitimately loses its history here. Matching on
    `recipe_content_key` instead — the key every other catalog lookup uses —
    would need history to carry ingredients, which is a storage change and a
    migration; the name is what the file actually has.

    **A future-dated entry is skipped, and that is the whole reason this takes
    a clock.** History records the *plan's* dates, not the day a run happened
    (`record_week_history` reads `week_start_date`), so generating next week
    writes seven entries dated ahead of today. Counting those would have the
    column claim you had last night's dinner on Thursday — the file is the
    app's rotation memory, which is a record of what has been *served*, and
    "eaten" is a strictly smaller thing. `recent_recipe_names` is right to
    ignore the distinction (a dish planned for Thursday should not also be
    generated for Tuesday); a column headed "Last eaten" is not.

    An entry whose `date` won't parse is skipped rather than raising, the same
    tolerance `history_styles()` extends to pre-rewrite entries: history can't
    be regenerated, so a single bad line must not cost the whole column.
    """
    latest: Dict[str, date] = {}
    for entry in history:
        try:
            cooked = date.fromisoformat(str(entry.get("date") or "")[:10])
        except ValueError:
            continue
        if cooked > today:
            continue
        for name in entry.get("recipe_names") or []:
            key = str(name).strip()
            if key and latest.get(key, date.min) < cooked:
                latest[key] = cooked
    return latest


def last_eaten_label(cooked: Optional[date], today: date) -> str:
    """A compact reading of a last-cooked date: relative inside a week, an
    absolute date beyond it.

    Relative is right at the near end because "have I had this recently" is a
    question with a week-ish horizon — `favorite_reuse_days` is 7 for
    breakfast and 21 for lunch — and wrong at the far end, where "17d ago"
    against "23d ago" is arithmetic the reader has to redo for every pair of
    rows they compare. `last_eaten_index` never hands this a future date — it
    drops those — so there is no negative-count branch to get wrong; the
    absolute form catches one anyway, since this is a plain formatter and the
    caller is where that rule actually lives.
    """
    if cooked is None:
        return "—"
    days = (today - cooked).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if 1 < days < 7:
        return f"{days}d ago"
    return f"{cooked.day} {cooked:%b}"


@dataclass
class CatalogRow:
    """One row of the Library table.

    `entry` is carried verbatim because the row's own controls act on the
    stored record, not on this projection of it — favorite, rename and delete
    all key off `entry["id"]`/`entry["recipe"]`, and handing them the record
    they already expect keeps the table from becoming a second definition of
    what a catalog entry is.

    `macros` is per serving and `servings` is what the recipe yields, which is
    the pair a reader needs to judge a row at all: the same dish stored at 6
    servings and at 1 differs by a factor of six in every stored total, and
    only the per-serving figure compares across rows. `view` is None for a
    stored recipe that no longer validates — the row still renders and can
    still be deleted, it just isn't clickable and has no macros.
    """

    entry: dict
    id: str
    name: str
    meal_type: str
    is_favorite: bool
    servings: int
    macros: Optional[Dict[str, float]]
    tags: List[str]
    last_eaten: Optional[date]
    last_eaten_label: str
    view: Optional[SlotView]

    @property
    def readable(self) -> bool:
        """Whether the stored recipe still validates — i.e. whether this row
        has macros to show and a detail dialog to open."""
        return self.view is not None


def build_catalog_rows(
    entries: List[dict],
    history: Optional[List[dict]] = None,
    today: Optional[date] = None,
) -> List[CatalogRow]:
    """`CatalogRow` per entry, in the order given. Pure but for the clock,
    which is a default rather than a read (the same seam
    `planner.build_rejection_rule` takes `today` for) so a test can age a
    cooked date without touching the machine's own.
    """
    today = today or date.today()
    cooked_on = last_eaten_index(history or [], today)
    rows = []
    for entry in entries:
        recipe = entry.get("recipe") or {}
        name = str(recipe.get("name") or "")
        view = catalog_slot_view(entry)
        cooked = cooked_on.get(name.strip())
        rows.append(
            CatalogRow(
                entry=entry,
                id=str(entry.get("id") or ""),
                name=name,
                meal_type=str(recipe.get("meal_type") or ""),
                is_favorite=bool(entry.get("is_favorite")),
                # The recipe's own `servings`, not `view.portions` — they are
                # the same number today, and reading it off the raw record is
                # what keeps the column populated for an entry that failed to
                # validate.
                servings=int(recipe.get("servings") or 1),
                macros=view.macros if view else None,
                tags=[
                    label
                    for flag, label in (
                        ("long_oven_cook", "Long cook"),
                        ("bulk_prep_friendly", "Bulk prep"),
                    )
                    if recipe.get(flag)
                ],
                last_eaten=cooked,
                last_eaten_label=last_eaten_label(cooked, today),
                view=view,
            )
        )
    return rows


# Which columns the table can be ordered by. `favorite` is the default and is
# deliberately the *composite* the Library has always sorted by — favourites
# together, then meal type, then name — because that is the shape a "are my
# favourites right" pass wants before any other question, and because it makes
# the table's default order byte-identical to the card grid's it replaced.
CATALOG_SORT_COLUMNS = (
    "favorite",
    "name",
    "meal_type",
    "servings",
    "calories",
    "protein_g",
    "net_carbs_g",
    "fat_g",
    "last_eaten",
)


def _catalog_sort_key(column: str):
    if column == "favorite":
        return lambda row: (not row.is_favorite, row.meal_type, row.name.lower())
    if column == "name":
        return lambda row: (row.name.lower(),)
    if column == "meal_type":
        return lambda row: (row.meal_type, row.name.lower())
    if column == "servings":
        return lambda row: (row.servings, row.name.lower())
    if column == "last_eaten":
        # `date.min` for a row outside the retained window, so ascending reads
        # "longest since I cooked this" and descending reads "most recent
        # first". Sorting the unknowns to one predictable end beats scattering
        # them, since "not in the window" is itself the answer to the question
        # the ascending sort asks.
        return lambda row: (row.last_eaten or date.min, row.name.lower())
    return lambda row: ((row.macros or {}).get(column, 0.0), row.name.lower())


def sort_catalog_rows(
    rows: List[CatalogRow], column: str, descending: bool = False
) -> List[CatalogRow]:
    """`rows` ordered by one column, name-tiebroken.

    An unknown column falls back to the default rather than raising: the only
    way to reach one is a stale sort field on a state object, and an
    unsortable table is a worse answer than a differently sorted one.
    """
    if column not in CATALOG_SORT_COLUMNS:
        column = CATALOG_SORT_COLUMNS[0]
    return sorted(rows, key=_catalog_sort_key(column), reverse=descending)


# ---- the sync-status view model ------------------------------------------
# Phase 6e of `ui-redesign.md`. Pure, day-parameterised and clock-free for the
# same reason every other view model here is: the whole test suite runs without
# a network, a model or a `date.today()`, and a status view that read the clock
# itself could only be tested on the day it was written.


# The three states a date can be in (`SYNC_RECORDED`/`SYNC_CHECKED`/
# `SYNC_UNCHECKED`) live in `ui_theme.py` beside the styles that render them,
# the same split `STATUS_COOK`..`STATUS_MISSING` already has with
# `STATUS_STYLES` — see the note there for why the third one has to exist.

# How many days back the Settings sync view draws. Deliberately the same
# horizon as `sync_service`'s `--lookback-days` default, so what the strip
# shows is roughly what a catchup run would still be willing to walk — but
# it is a *display* choice, not a coupling: nothing here computes what a sync
# would fetch (that is `get_sync_date_range`'s job, and duplicating its capped
# walk would be a second answer to the same question), and changing the CLI
# default would not make this strip wrong, only differently scoped.
SYNC_WINDOW_DAYS = 14


@dataclass
class SyncDay:
    """One date in the window, and what this source knows about it."""

    date: str
    state: str


@dataclass
class SyncSourceStatus:
    """One sync source's standing, as `data/biometrics.json` records it.

    `last_checked` is the source's `sync_checkpoints` entry — the last date it
    was actually asked about — and `last_recorded` is the newest date it
    actually stored a row for. They are separate fields rather than one
    "latest" because the gap between them is information: a Garmin checked
    through Wednesday whose last weigh-in was Sunday means three mornings
    nobody stood on the scale, which is a different situation from a Garmin
    nobody has synced since Sunday, and only these two numbers side by side
    tell them apart.

    One row per stored *list*, not per source. `weigh_ins` and `readiness_log`
    are both filled by one Garmin sync and share its checkpoint, but they
    answer different questions — a morning nobody stood on the scale is not a
    night nobody wore the watch — and a merged row could only report the
    weaker of the two. `shares_source` is true for exactly those rows, so the
    page can say once that a `last checked` they hold in common comes from one
    login rather than leaving two identical dates to look like a coincidence.
    """

    source: str
    section: str
    label: str
    shares_source: bool
    last_checked: Optional[str]
    last_recorded: Optional[str]
    recorded_total: int
    days: List[SyncDay]

    def count(self, state: str) -> int:
        return sum(1 for day in self.days if day.state == state)

    @property
    def connected(self) -> bool:
        """Whether this source has ever produced anything at all.

        A source with no checkpoint *and* no rows has never run — the normal
        state of a fresh checkout, and the one case where the strip below is
        14 identical unchecked cells and says nothing worth reading.
        """
        return bool(self.last_checked or self.last_recorded)


def sync_status(
    biometrics: dict, today: date, window_days: int = SYNC_WINDOW_DAYS
) -> List[SyncSourceStatus]:
    """Each sync source's checkpoint, latest row, and the last `window_days`.

    A read over what `sync_service` already wrote, in `BIOMETRIC_SECTION_SOURCES`
    order — it never triggers a sync and never decides what one *would* fetch.
    Those are two different questions and only the CLI can answer the second:
    `get_sync_date_range` caps its walk, anchors on whichever *requested*
    source is furthest behind, and is the only place that reasoning belongs.
    What this answers is the narrower one the maintainer actually asked
    (ISSUES.md item 8): which days are present, which are missing, and which
    were never looked at.

    **A source's effective checkpoint is the later of its stored checkpoint
    and its newest row**, mirroring `get_sync_date_range`'s own `max(dates +
    [checkpoint])`. `sync_checkpoints` postdates the two original lists, so a
    `biometrics.json` written before it existed — or hand-edited since — has
    rows past a checkpoint that would otherwise mark them as never-checked.
    A stored row is proof the day was asked about, whatever the checkpoint
    says.

    **One card per stored list, and two of them share a source.** A Garmin
    sync fills `weigh_ins` and `readiness_log` under one
    `sync_checkpoints["garmin"]` entry, so both cards report the same
    `last_checked` — which is honest (the login did ask about that date) but
    has one pre-migration wrinkle worth knowing: a date checked before
    `readiness_log` existed reads as "checked, nothing recorded" for
    readiness, when in truth nothing asked. `--date` re-syncs it, since
    Garmin keeps the history. Inventing a second checkpoint to draw that
    distinction would mean a second thing for a sync to forget to advance,
    for one fortnight of one-time ambiguity.
    """
    checkpoints = biometrics.get("sync_checkpoints") or {}
    window = [today - timedelta(days=offset) for offset in range(window_days - 1, -1, -1)]
    sections_per_source = Counter(BIOMETRIC_SECTION_SOURCES.values())

    statuses = []
    for section, source in BIOMETRIC_SECTION_SOURCES.items():
        recorded = sorted(
            {
                str(row["date"])
                for row in (biometrics.get(section) or [])
                if row.get("date")
            }
        )
        checkpoint = checkpoints.get(source) or None
        last_recorded = recorded[-1] if recorded else None
        known = [stamp for stamp in (checkpoint, last_recorded) if stamp]
        checked_through = max(known) if known else None

        days = []
        for stamp in window:
            iso = stamp.isoformat()
            if iso in recorded:
                state = SYNC_RECORDED
            elif checked_through and iso <= checked_through:
                state = SYNC_CHECKED
            else:
                state = SYNC_UNCHECKED
            days.append(SyncDay(date=iso, state=state))

        statuses.append(
            SyncSourceStatus(
                source=source,
                section=section,
                label=SYNC_SECTION_LABELS.get(section) or humanize(section).title(),
                shares_source=sections_per_source[source] > 1,
                last_checked=checkpoint,
                last_recorded=last_recorded,
                recorded_total=len(recorded),
                days=days,
            )
        )
    return statuses


# The scheduled job (`./scripts/sync.sh install`) runs once a day, so a
# checkpoint dated yesterday is the normal state for the whole morning — the
# job at 07:30 has simply not run yet when you look at 07:00. Two days without
# one is not normal, and is the first visible sign that the launchd job was
# never loaded, the laptop has been shut rather than asleep, or a credential
# expired and every run since has failed.
SYNC_STALE_AFTER_DAYS = 2


@dataclass
class SyncFreshness:
    """Whether anything is syncing at all, above the per-list cards.

    A different question from what any one `SyncSourceStatus` answers, and the
    reason it needed its own line: those cards report what each *stored list*
    knows, and a reader with three cards all saying "Last checked 21 Aug" has
    to notice the date is a week old and then work out that they are all one
    week old for one reason. v0.31.0 chose a scheduled job over syncing from
    the app, so the failure mode this app can actually have is "the scheduler
    stopped", and the app's whole responsibility toward it is making that
    visible. See `scripts/sync.sh`.

    `last_checked` is the **newest** checkpoint across every source — the last
    time anything asked anyone anything — because that is what answers "is the
    job running". `lagging` is the separate question stacked on top of it: a
    source whose own checkpoint sits `stale_after_days` behind that newest one
    has been failing while its sibling kept advancing, which a single date
    could not say. A source that has never run at all is not lagging; it is
    unconfigured, and its own card already says so.
    """

    last_checked: Optional[str]
    days_since: Optional[int]
    stale_after_days: int
    lagging: List[str]

    @property
    def state(self) -> str:
        if self.last_checked is None or self.days_since is None:
            return SYNC_FRESH_NEVER
        if self.days_since >= self.stale_after_days:
            return SYNC_FRESH_STALE
        return SYNC_FRESH_CURRENT


def sync_freshness(
    biometrics: dict, today: date, stale_after_days: int = SYNC_STALE_AFTER_DAYS
) -> SyncFreshness:
    """When anything last synced, and which source is behind the others.

    Pure and clock-free like every other view model here — `today` is a
    parameter, which is what lets the suite test "three days stale" without
    waiting three days.

    It reads `sync_checkpoints` alone, never the stored rows. A row is proof a
    day was *recorded*, and a scale nobody stood on for a week records nothing
    while the sync runs perfectly — reading rows here would report a
    functioning job as a broken one, which is the exact confusion
    `sync_checkpoints` was added to end. (`sync_status` folds the two together
    deliberately, for the opposite reason: there, a stored row past a
    checkpoint is a day that was plainly asked about.)
    """
    checkpoints = {
        source: stamp
        for source, stamp in (biometrics.get("sync_checkpoints") or {}).items()
        if stamp
    }
    if not checkpoints:
        return SyncFreshness(
            last_checked=None,
            days_since=None,
            stale_after_days=stale_after_days,
            lagging=[],
        )

    newest = max(checkpoints.values())
    days_since = (today - date.fromisoformat(newest)).days
    lagging = sorted(
        source
        for source, stamp in checkpoints.items()
        if (date.fromisoformat(newest) - date.fromisoformat(stamp)).days
        >= stale_after_days
    )
    return SyncFreshness(
        last_checked=newest,
        days_since=days_since,
        stale_after_days=stale_after_days,
        lagging=lagging,
    )


@dataclass
class FibreView:
    """A day's planned fibre against its target, and — when Cronometer logged
    the day — what was actually eaten beside it.

    **Two numbers with a divider and one without, and which is which is the
    whole rule.** `planned/target` is the same `actual/target` shape every
    other figure in the telemetry row carries, and it is honest now that
    `nutrition_engine.calculate_fiber_target_g` gives the day a figure to aim
    at. `logged` is *not* that: it is the same quantity measured a second way,
    not a goal, so it sits beside the pair rather than under it. This class
    used to say a divider of any kind would invent a goal the planner never
    aimed at, and that was exactly right until the planner started aiming at
    one.

    `target` is None for a week generated before fibre had one — a stored
    `WeekPlan.targets` has no `fiber_g` — and `label` falls back to the bare
    `FIB 32g` those weeks always printed. Same pre-migration tolerance
    `logged_actuals_for` draws for a plan with no `week_start_date`.

    `delta` stays signed against the **plan**, never against the target
    (`logged - planned`, so negative means the day came in short of what was
    cooked for it). Those are two different questions and only one of them is
    about whether the sync agrees with the kitchen.
    """

    planned: float
    logged: Optional[float]
    target: Optional[float]
    delta: Optional[float]
    label: str
    logged_label: str
    detail: str


def fibre_view(
    planned: float, logged: Optional[float], target: Optional[float] = None
) -> FibreView:
    """`FibreView` for one day. Pure — the caller supplies all three figures.

    `logged` is None for every day nothing has been synced against, which is
    the normal state for the whole of a week planned ahead: only days that
    have actually happened can have been logged. `target` defaults to None so
    the two-argument call still answers the question it always did.
    """
    label = f"FIB {planned:.0f}/{target:.0f}g" if target else f"FIB {planned:.0f}g"
    against = (
        f"{planned:.0f}g planned of a {target:.0f}g target"
        if target
        else f"{planned:.0f}g planned (tracked, no target)"
    )
    if logged is None:
        return FibreView(
            planned=planned,
            logged=None,
            target=target,
            delta=None,
            label=label,
            logged_label="",
            detail=f"fibre: {against}",
        )
    delta = logged - planned
    return FibreView(
        planned=planned,
        logged=logged,
        target=target,
        delta=delta,
        label=label,
        logged_label=f"logged {logged:.0f}g",
        # The logged half is named as a comparison against the *plan*, never
        # against the target beside it — the wording is what stops a reader
        # taking the sync's figure as the thing being scored.
        detail=f"fibre: {against}, {logged:.0f}g logged ({delta:+.0f}g vs plan)",
    )


# What the adaptive TDEE is currently doing, as one word. The first three are
# `nutrition_engine`'s own unmet-precondition states passed straight through;
# the last three are what `reconcile_adaptive_tdee` did with a figure that
# cleared them, which the engine records as `basis["tdee_source"]`.
ADAPTIVE_VIEW_MEASURED = "measured"
ADAPTIVE_VIEW_REJECTED = "rejected"
ADAPTIVE_VIEW_ADAPTIVE = "adaptive"


@dataclass
class AdaptiveTDEEView:
    """Why the week is planned on the TDEE it is, in one headline and one line.

    The gap this closes: `calculate_adaptive_tdee` returns a bare
    `Optional[float]`, and `basis["tdee_source"]` spells every one of its
    `None` cases `"formula"` — the same string a fresh checkout with an empty
    `biometrics.json` produces. So a database with five weigh-ins and five
    logged days, which by every visible count should be measuring, reported
    itself identically to one with nothing in it. The two surfaces that
    talked about the estimate made that worse rather than better: Insights
    printed the counts and then stated the *rule* without ever evaluating it,
    so a reader with five of each concluded it was on, and Settings named the
    winner (`"(formula)"`) without saying why the alternative lost.

    One view model for both, per the standing rule that logic worth testing
    leaves the widget module — the alternative is two surfaces free to phrase
    the same state differently, which for a diagnostic readout is the whole
    failure being fixed.

    `headline` is the state in a few words; `detail` is the measured evidence
    and, for a blocked state, what would clear it. Nothing here is a claim
    about what a sync *would* fetch — that is `get_sync_date_range`'s job, the
    same line `sync_status` draws.
    """

    state: str
    measuring: bool
    headline: str
    detail: str
    status: AdaptiveTDEEStatus


def adaptive_tdee_view(
    biometrics: Optional[dict], basis: Optional[dict] = None
) -> AdaptiveTDEEView:
    """The current adaptive-TDEE state, from the same series hydration reads.

    `basis` is `config["dynamic_basis"]` — `hydrate_dynamic_targets`'
    diagnostic record of the run it just computed. It is what separates a
    measured figure that was *used* from one `reconcile_adaptive_tdee`
    disbelieved, which is a verdict only the formula can supply. It is
    legitimately absent — every switchable macro manual, or a profile with
    nothing in it, means no engine call was made — and a measured estimate
    with no basis beside it is reported as exactly that rather than as an
    adaptive week.
    """
    status = measure_adaptive_tdee(
        (biometrics or {}).get("daily_actuals") or [],
        (biometrics or {}).get("weigh_ins") or [],
    )
    window = status.window_days

    if status.state == ADAPTIVE_NO_WEIGH_INS:
        return AdaptiveTDEEView(
            state=ADAPTIVE_NO_WEIGH_INS,
            measuring=False,
            headline="Measured TDEE off — not enough weigh-ins",
            detail=(
                f"{status.weigh_ins} weigh-in(s) in the last {window} days. "
                f"The weight trend needs at least 2, spanning "
                f"{status.required_span_days} days."
            ),
            status=status,
        )
    if status.state == ADAPTIVE_SHORT_SPAN:
        # Named in days rather than in weigh-ins on purpose: this is the
        # precondition that collapses while every count looks healthy, and
        # more weigh-ins bunched into the same three days do not clear it.
        short_by = status.required_span_days - status.span_days
        return AdaptiveTDEEView(
            state=ADAPTIVE_SHORT_SPAN,
            measuring=False,
            headline="Measured TDEE off — weigh-in span too short",
            detail=(
                f"Weigh-in span {status.span_days} days, needs "
                f"{status.required_span_days} ({status.weigh_ins} weigh-ins in the "
                f"last {window}). About {short_by} more day(s) of weighing in "
                "clears it."
            ),
            status=status,
        )
    if status.state == ADAPTIVE_NO_LOGS:
        return AdaptiveTDEEView(
            state=ADAPTIVE_NO_LOGS,
            measuring=False,
            headline="Measured TDEE off — no logged intake",
            detail=(
                f"{status.weigh_ins} weigh-ins spanning {status.span_days} days, but "
                f"no logged calories inside the same {window}. Sync Cronometer for "
                "a day in that window."
            ),
            status=status,
        )

    source = (basis or {}).get("tdee_source")
    estimate = status.estimate or 0.0
    evidence = (
        f"{status.logged_days} logged day(s) and a {status.span_days}-day "
        "weigh-in span"
    )
    if source == "formula_adaptive_rejected":
        return AdaptiveTDEEView(
            state=ADAPTIVE_VIEW_REJECTED,
            measuring=False,
            headline="Measured TDEE rejected — planning on the formula",
            detail=(
                f"Measured {estimate:.0f} kcal from {evidence}, against the "
                f"formula's {(basis or {}).get('tdee_formula', 0):.0f} — more than "
                f"{ADAPTIVE_TDEE_TOLERANCE * 100:.0f}% out, so the formula was kept. "
                "Usually under-logged intake, or a weigh-in series dominated by "
                "water weight."
            ),
            status=status,
        )
    if source == ADAPTIVE_VIEW_ADAPTIVE:
        return AdaptiveTDEEView(
            state=ADAPTIVE_VIEW_ADAPTIVE,
            measuring=True,
            headline="Measured from intake and weight trend",
            detail=(
                f"{estimate:.0f} kcal from {evidence} — this is what the week is "
                "planned against, not the formula."
            ),
            status=status,
        )
    # Enough data, and no verdict to report: either nothing called the engine
    # this run, or it was called without the series. Saying "adaptive" here
    # would claim a reconciliation that never happened.
    return AdaptiveTDEEView(
        state=ADAPTIVE_VIEW_MEASURED,
        measuring=False,
        headline="Measured, but nothing is planning from it yet",
        detail=(
            f"{estimate:.0f} kcal from {evidence}. No engine figure to reconcile it "
            "against — the week's calories and protein are both set manually, or "
            "there is no body profile to compute a formula from."
        ),
        status=status,
    )


@dataclass
class ProposedSessionView:
    """One accept/dismiss row, already worded.

    Three strings and the proposal behind them, so the widget prints what it
    is handed and the phrasing stays testable — the same split
    `AdaptiveTDEEView` draws, and for the same reason: a proposal is a
    sentence the user is being asked to agree to, and two surfaces (or two
    edits) free to word it differently is exactly how "seen 2 of 4" turns
    into a claim the evidence never made.
    """

    session: ProposedSession
    title: str
    detail: str
    evidence: str

    @property
    def adds(self) -> bool:
        return self.session.kind != PROPOSAL_DROP


@dataclass
class TrainingProposalsView:
    """What the schedule proposal has to say, and what it looked at.

    `headline` is the state in a few words and `evidence` is the span behind
    it, exactly as `AdaptiveTDEEView` splits them — and for the same reason
    that view model exists at all: three of this feature's states produce an
    empty list, and "the watch has recorded nothing yet", "not enough history
    to see a pattern" and "your declared week already matches what was
    recorded" are three different answers that a bare empty list spells
    identically. The last one is the *good* one, and is the one a reader is
    most likely to misread as broken.
    """

    state: str
    headline: str
    evidence: str
    rows: List[ProposedSessionView]

    @property
    def has_proposals(self) -> bool:
        return bool(self.rows)


def training_proposals_view(proposal: TrainingScheduleProposal) -> TrainingProposalsView:
    """`propose_training_schedule`'s answer, in words.

    Pure, and takes the proposal rather than the state, so a test can hand it
    a hand-built status without a `PlannerState` — same shape as
    `sync_status`/`fibre_view`.
    """
    if proposal.state == TRAINING_PROPOSAL_NO_ACTIVITY:
        return TrainingProposalsView(
            state=proposal.state,
            headline="Nothing recorded to compare against",
            evidence=(
                f"No Garmin activity stored in the last {proposal.window_days} days. "
                "The daily sync fills this in — ./scripts/sync.sh status says "
                "whether it is running."
            ),
            rows=[],
        )
    if proposal.state == TRAINING_PROPOSAL_SHORT_HISTORY:
        return TrainingProposalsView(
            state=proposal.state,
            headline="Not enough history to see a pattern yet",
            evidence=(
                f"{proposal.observed_days} day(s) observed, "
                f"{proposal.activity_days} with recorded activity. A weekday has "
                f"to come round at least {MIN_PROPOSAL_OCCURRENCES} times before "
                "what happens on it is a routine."
            ),
            rows=[],
        )

    rows = [
        ProposedSessionView(
            session=session,
            title=(
                f"{session.day} {session.time} · "
                f"{TRAINING_TYPE_LABELS.get(session.type, humanize(session.type))}"
            ),
            detail=(
                f"declared {session.duration_minutes} min · "
                f"{session.estimated_burn_kcal:.0f} kcal"
                if session.kind == PROPOSAL_DROP
                else f"{session.duration_minutes} min · "
                f"{session.estimated_burn_kcal:.0f} kcal"
            ),
            evidence=(
                f"never recorded on {session.observations} observed {session.day}s"
                if session.kind == PROPOSAL_DROP
                else f"recorded {session.occurrences} of {session.observations} "
                f"{session.day}s"
            ),
        )
        for session in proposal.proposals
    ]
    observed = (
        f"{proposal.observed_days} day(s) observed "
        f"({proposal.observed_from} to {proposal.observed_to}), "
        f"{proposal.activity_days} with recorded activity"
    )
    if not rows:
        return TrainingProposalsView(
            state=proposal.state,
            headline="Your schedule matches what the watch recorded",
            evidence=observed,
            rows=[],
        )
    return TrainingProposalsView(
        state=proposal.state,
        headline=f"{len(rows)} suggestion(s) from recorded activity",
        evidence=observed,
        rows=rows,
    )


# ---------------------------------------------------------------------------
# The Insights destination's series
# ---------------------------------------------------------------------------
#
# CHANGE-QUEUE.md's trend-charts item (`future-ideas.md` 5c ·
# `ui-redesign.md` finding 3). Five readouts — weight against target, planned
# calories against logged, macro accuracy, adherence tiles and the weigh-in
# table — over data this app has been accumulating for one week.
#
# **The item was filed as blocked on runtime data, and it still is; what
# changed is who says so.** Its own trigger — `calculate_adaptive_tdee`
# returning a number, and ~14 rows in both lists — was measured unmet on the
# live file the day this shipped (6 weigh-ins, 5 logged days, a 5-day span
# against a floor of 7). Waiting for it would have meant a page that says
# "not enough yet" in prose it cannot check, which is the exact failure
# v0.30.0 fixed for the adaptive estimate: Insights printed the counts and
# then named the rule without ever evaluating it. So every series below
# evaluates its own precondition and reports which one stopped it, and the
# page fills itself as rows land rather than waiting on another release.
#
# **Thin is a state, not a reason to draw nothing.** The item's worry is
# precise — "a 14-day chart against 5 points is thin; a 30-day one is
# misleading" — and it is a worry about the *axis*, not about the points. A
# window anchored on the data's own last row and captioned with the span it
# actually covers cannot mislead the way a fixed 30-day axis with three dots
# in the corner does. `INSIGHT_THIN` is drawn and labelled; `INSIGHT_SPARSE`
# is not drawn at all.

# How far back any series looks. Wider than the adaptive estimate's 14 days
# because the two want different things from the same rows — that wants
# recent intake to pair a weight trend against, a chart wants enough points
# to read as a line.
INSIGHT_WINDOW_DAYS = 30

# Below this many points there is no line, only dots pretending to be one.
INSIGHT_MIN_POINTS = 3

# Above this it is a series; between the two it is drawn with the span named,
# so nobody reads six days as a fortnight. 14 is the item's own figure.
INSIGHT_THIN_POINTS = 14

# Which precondition a series is in. Four rather than a bare `ready` flag for
# the reason `AdaptiveTDEEStatus` carries four: "nothing recorded", "not
# enough to draw" and "drawn, but short" have different fixes, and spelling
# them identically is what had a reader holding five of everything
# concluding the feature was broken.
INSIGHT_EMPTY = "empty"
INSIGHT_SPARSE = "sparse"
INSIGHT_THIN = "thin"
INSIGHT_READY = "ready"


@dataclass
class InsightPanel:
    """One readout's verdict — the two lines every section prints above itself.

    Same contract as `AdaptiveTDEEView`: `headline` is the state in a few
    words, `detail` is the measured evidence and, for a blocked state, what
    would clear it. `drawable` is the single test a widget branches on, so
    no widget module re-derives the threshold.
    """

    state: str
    headline: str
    detail: str

    @property
    def drawable(self) -> bool:
        return self.state in (INSIGHT_THIN, INSIGHT_READY)


@dataclass
class WeighInRow:
    """One row of the weigh-in table — the fifth of the item's five readouts.

    It rides on the chart's own windowed rows rather than re-reading
    `biometrics.json`, so the table and the line above it cannot disagree
    about which weigh-ins are in view. `delta_kg` is against the previous row
    *in the window*, which is why the first row carries None rather than 0.0:
    a zero would claim a weigh-in that didn't move.
    """

    date: str
    weight_kg: float
    delta_kg: Optional[float]
    body_fat_pct: Optional[float]


@dataclass
class WeightTrendPanel(InsightPanel):
    dates: Tuple[str, ...] = ()
    labels: Tuple[str, ...] = ()
    weights: Tuple[float, ...] = ()
    smoothed: Tuple[float, ...] = ()
    rows: List[WeighInRow] = field(default_factory=list)
    target_kg: Optional[float] = None
    kg_per_week: Optional[float] = None
    span_days: int = 0

    @property
    def to_target_kg(self) -> Optional[float]:
        if self.target_kg is None or not self.weights:
            return None
        return round(self.weights[-1] - self.target_kg, 1)

    @property
    def target_in_range(self) -> bool:
        """Whether the target is close enough to share the plot's own axis.

        **A 19 kg gap and a 1 kg span cannot both be legible on one linear
        axis**, and this is the chart where that collides: the y-axis is
        scaled to the weigh-ins (a zero-based one draws a real week as a flat
        line 99 kg above the origin), which puts a distant target outside the
        plot entirely — ECharts clips it, so the chart titled "weight against
        target" silently shows no target at all. Widening the axis to include
        it flattens the trend instead, which is the same trade the macro
        chart resolves by moving to a percentage axis.

        So the line is drawn only once it is genuinely in view, and the gap
        is stated in words either way. A target that appears as the scale
        approaches it is the honest version of both states.
        """
        if self.target_kg is None or not self.weights:
            return False
        low, high = min(self.weights), max(self.weights)
        margin = max((high - low) * 0.5, 0.5)
        return low - margin <= self.target_kg <= high + margin


@dataclass
class IntakePanel(InsightPanel):
    dates: Tuple[str, ...] = ()
    labels: Tuple[str, ...] = ()
    planned: Tuple[float, ...] = ()
    logged: Tuple[float, ...] = ()
    bands: Tuple[str, ...] = ()
    mean_planned: float = 0.0
    mean_logged: float = 0.0


@dataclass
class MacroAccuracyRow:
    key: str
    label: str
    planned: float
    logged: float
    band: str

    @property
    def pct(self) -> Optional[float]:
        return (self.logged / self.planned * 100) if self.planned > 0 else None


@dataclass
class MacroAccuracyPanel(InsightPanel):
    rows: List[MacroAccuracyRow] = field(default_factory=list)
    days: int = 0


@dataclass
class AdherencePanel(InsightPanel):
    counts: Dict[str, int] = field(default_factory=dict)
    marked_days: int = 0
    workouts_recorded: int = 0
    workouts_marked: int = 0

    @property
    def marks(self) -> int:
        return sum(self.counts.values())

    @property
    def as_planned_pct(self) -> Optional[float]:
        """Eaten as a share of what was *marked*, never of what was planned.

        The denominator is marks because that is the only denominator that
        exists: `adherence.json` is keyed by date and slot, and the plans
        those dates were generated against are gone from `week_plan.json` the
        moment a new week is generated over them. A percentage of "meals
        planned" would be a divider under a number nobody counted, which is the
        rule fibre was the other example of until it acquired a target
        somebody actually aims at. Nothing counts the plans behind these
        marks, so every surface printing this has to say "of marks" in words.
        """
        return (self.counts.get(ADHERENCE_EATEN, 0) / self.marks * 100) if self.marks else None


def _windowed_dates(dates: List[str], window_days: int) -> Set[str]:
    """`dates` within `window_days` of the latest of them.

    Anchored on the data's own last date rather than on today — the anchoring
    `measure_adaptive_tdee` and `measure_weight_trend` both use, and for the
    same reason: a series that stops a fortnight before it is read should
    show the fortnight it recorded instead of an empty window.
    """
    parsed = sorted({d for d in (str(value)[:10] for value in dates) if _iso_date(d)})
    if not parsed:
        return set()
    end = _iso_date(parsed[-1])
    start = end - timedelta(days=window_days)
    return {d for d in parsed if start <= _iso_date(d) <= end}


def _iso_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _short_dates(dates) -> Tuple[str, ...]:
    """ISO dates as `24 Aug` for a chart's category axis.

    Formatted here rather than in the widget because the axis labels and the
    weigh-in table below them are two readings of one series, and a category
    label built in `ui_insights.py` would be the one string on the page that
    the view model could not be tested against. Full ISO stays on `dates`,
    which is what the table prints and what any date match keys on.
    """
    return tuple(
        (_iso_date(value).strftime("%-d %b") if _iso_date(value) else str(value))
        for value in dates
    )


def _span_days(dates) -> int:
    parsed = sorted(d for d in (_iso_date(value) for value in dates) if d)
    return (parsed[-1] - parsed[0]).days if len(parsed) >= 2 else 0


def _series_state(points: int) -> str:
    if points <= 0:
        return INSIGHT_EMPTY
    if points < INSIGHT_MIN_POINTS:
        return INSIGHT_SPARSE
    return INSIGHT_THIN if points < INSIGHT_THIN_POINTS else INSIGHT_READY


def _span_note(points: int, span_days: int) -> str:
    """The caption that keeps a short series from reading as a long one."""
    return f"{points} point(s) across {span_days} day(s)"


def weight_trend_panel(
    biometrics: Optional[dict],
    target_kg: Optional[float] = None,
    window_days: int = INSIGHT_WINDOW_DAYS,
) -> WeightTrendPanel:
    """Weigh-ins against the target weight, and the table beneath them.

    The line through the scatter is `nutrition_engine.smooth_series` and the
    *rate* under it is `measure_weight_trend`'s least-squares fit — two
    estimators on purpose, because smoothing for the eye and estimating a
    rate are different jobs and the module says so at length. Reading the
    rate off the smoothed endpoints understates a noise-free decline by 26%.

    `kg_per_week` is legitimately None on a drawable chart: the points clear
    `INSIGHT_MIN_POINTS` well before the span clears `MIN_TREND_SPAN_DAYS`,
    and a rate quoted off four days of weighing is the noise amplification
    that floor exists to refuse. The chart draws; the caption says why there
    is no rate on it yet.
    """
    trend = measure_weight_trend((biometrics or {}).get("weigh_ins") or [], window_days)
    state = _series_state(len(trend.weights))
    rows = []
    previous: Optional[float] = None
    windowed = {
        str(row.get("date") or "")[:10]: row
        for row in ((biometrics or {}).get("weigh_ins") or [])
        if isinstance(row, dict)
    }
    for iso, weight in zip(trend.dates, trend.weights):
        body_fat = (windowed.get(iso) or {}).get("body_fat_pct")
        rows.append(
            WeighInRow(
                date=iso,
                weight_kg=weight,
                delta_kg=None if previous is None else round(weight - previous, 2),
                body_fat_pct=float(body_fat) if isinstance(body_fat, (int, float)) else None,
            )
        )
        previous = weight

    common = dict(
        dates=trend.dates,
        labels=_short_dates(trend.dates),
        weights=trend.weights,
        smoothed=trend.smoothed,
        rows=rows,
        target_kg=target_kg,
        kg_per_week=trend.kg_per_week,
        span_days=trend.span_days,
    )
    if state == INSIGHT_EMPTY:
        return WeightTrendPanel(
            state=state,
            headline="No weigh-ins yet",
            detail=(
                "Nothing on the scale inside the window. Garmin's sync writes "
                "these — see Settings' Biometric Sync."
            ),
            **common,
        )
    if state == INSIGHT_SPARSE:
        return WeightTrendPanel(
            state=state,
            headline="Not enough weigh-ins to draw",
            detail=(
                f"{len(trend.weights)} weigh-in(s) recorded; a line needs "
                f"{INSIGHT_MIN_POINTS}."
            ),
            **common,
        )
    rate = (
        f"{trend.kg_per_week:+.2f} kg/week"
        if trend.kg_per_week is not None
        else f"no rate yet — the trend needs a {MIN_TREND_SPAN_DAYS}-day span, "
        f"this one covers {trend.span_days}"
    )
    panel = WeightTrendPanel(
        state=state,
        headline=f"{trend.weights[-1]:.1f} kg",
        detail=f"{_span_note(len(trend.weights), trend.span_days)} · {rate}",
        **common,
    )
    # The gap is always in words, and only *sometimes* on the chart — see
    # `target_in_range`. A caption that appears the moment the line has to be
    # dropped would be a caption nobody reads until it matters; this one is
    # the standing answer and the line is the bonus.
    if panel.to_target_kg is not None:
        panel.detail += f" · {panel.to_target_kg:+.1f} kg to target"
    return panel


def paired_intake_days(
    history: Optional[List[dict]],
    biometrics: Optional[dict],
    window_days: int = INSIGHT_WINDOW_DAYS,
) -> List[Tuple[str, dict, dict]]:
    """Dates carrying both a planned target and a logged row, oldest first.

    The planned half is `meal_history.json`'s own `targets` block —
    `record_week_history` stamps one per cooked day — and the logged half is
    `daily_actuals`. **Both are keyed by date, which is the whole reason this
    pairing is possible at all**: a `slot_id` is a weekday name and would
    repeat every seven days, the same distinction `logged_actuals_for` draws
    against `planner.logged_intake_for`.

    Two rules, both borrowed rather than invented:

    - **Last history entry wins for a date.** History appends an entry per
      generation, so a regenerated day legitimately has two, and the later
      one is the plan that stood. Same rule `meal_adherence_view` applies to
      a duplicated mark.
    - **A zero-calorie logged row is not a pairing.** A partial sync can
      write one, and it would read as a day nobody ate — exactly the case
      `planner.logged_intake_for` already refuses to substitute for a plan.
      A *partly* logged day (a real figure, short of the day) is kept, and
      reads as a shortfall, which is the honest reading and the same one
      `reconcile_adaptive_tdee` warns systematic under-logging produces.
    """
    planned: Dict[str, dict] = {}
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        iso = str(entry.get("date") or "")[:10]
        targets = entry.get("targets")
        if iso and isinstance(targets, dict):
            planned[iso] = targets
    logged = {
        str(row.get("date") or "")[:10]: row
        for row in ((biometrics or {}).get("daily_actuals") or [])
        if isinstance(row, dict) and float(row.get("calories") or 0) > 0
    }
    shared = _windowed_dates(sorted(set(planned) & set(logged)), window_days)
    return [(iso, planned[iso], logged[iso]) for iso in sorted(shared)]


def intake_panel(
    history: Optional[List[dict]],
    biometrics: Optional[dict],
    window_days: int = INSIGHT_WINDOW_DAYS,
) -> IntakePanel:
    """What each day was planned to eat, against what Cronometer logged.

    The per-day `bands` are `ui_theme.macro_band`'s existing on/near/off
    read, not a second tolerance: this chart is the first thing outside the
    telemetry header to ask "how close did that day land", and two answers to
    that question would be free to disagree on a screen showing both.
    """
    days = paired_intake_days(history, biometrics, window_days)
    dates = tuple(iso for iso, _, _ in days)
    planned = tuple(float(target.get("calories") or 0) for _, target, _ in days)
    logged = tuple(float(row.get("calories") or 0) for _, _, row in days)
    state = _series_state(len(days))
    common = dict(
        dates=dates,
        labels=_short_dates(dates),
        planned=planned,
        logged=logged,
        bands=tuple(macro_band(a, p) for a, p in zip(logged, planned)),
        mean_planned=round(sum(planned) / len(planned), 1) if planned else 0.0,
        mean_logged=round(sum(logged) / len(logged), 1) if logged else 0.0,
    )
    if state == INSIGHT_EMPTY:
        return IntakePanel(
            state=state,
            headline="No day has both a plan and a log",
            detail=(
                "This pairs `meal_history.json`'s targets against Cronometer's "
                "logged day. Generate a week, then sync the days you ate it."
            ),
            **common,
        )
    if state == INSIGHT_SPARSE:
        return IntakePanel(
            state=state,
            headline="Not enough paired days to draw",
            detail=(
                f"{len(days)} day(s) carry both a plan and a log; a chart needs "
                f"{INSIGHT_MIN_POINTS}."
            ),
            **common,
        )
    delta = common["mean_logged"] - common["mean_planned"]
    return IntakePanel(
        state=state,
        headline=f"{delta:+.0f} kcal/day against plan",
        # The chart has no legend, deliberately: its bars are coloured per
        # day by `macro_band`, and one legend swatch cannot stand for five
        # different fills without being wrong about four of them. So the
        # encoding is said here, where every other explanation on this page
        # already lives.
        detail=(
            f"{_span_note(len(days), _span_days(dates))} · logged "
            f"{common['mean_logged']:.0f} against a planned "
            f"{common['mean_planned']:.0f} · bars are the logged day, tinted by "
            "how close it landed; the dashed line is the plan"
        ),
        **common,
    )


def macro_accuracy_panel(
    history: Optional[List[dict]],
    biometrics: Optional[dict],
    window_days: int = INSIGHT_WINDOW_DAYS,
) -> MacroAccuracyPanel:
    """Mean logged against mean planned, one row per targeted nutrient.

    **Fibre is here now, and only for the days that can carry it.** This
    docstring used to say fibre had no planned figure to divide by, and the
    filter below said `key not in MACRO_KEYS` on the strength of it — a
    perfectly good rule that stopped being true the day
    `nutrition_engine.calculate_fiber_target_g` gave the day a target. What is
    still true is that a history entry written before that release carries no
    `fiber_g` in its `targets`, and a mean taken over those days would read as
    a 0 g plan massively overshot rather than as a plan nobody made. So the
    row appears only when **every** paired day in the window states one, which
    is the same all-or-nothing precondition `WeightTrendPanel.target_in_range`
    applies to its own line: a figure that can only be drawn honestly for part
    of the window is not drawn.

    Means rather than a per-day series because the question is different from
    `intake_panel`'s. That one asks which days went off; this asks whether
    the split is drifting — a week that hits its calories 20 g of protein
    light every day is the failure CLAUDE.md's portion-sizing section says a
    single trim factor structurally cannot fix, and it is invisible on a
    calorie chart.
    """
    days = paired_intake_days(history, biometrics, window_days)
    rows = []
    # `MACRO_DETAIL_LABELS` for the wording and `NUTRIENT_KEYS` for the
    # membership, rather than either alone: the labels are the three-letter
    # forms a reader knows (PRO/CHO/FAT/FIB), and `NUTRIENT_KEYS` is the tuple
    # that decides what has a target anywhere else in the app now that fibre
    # has one. Filtering one by the other is what keeps this from becoming a
    # second hand-maintained list.
    for key, label, _ in MACRO_DETAIL_LABELS:
        if key not in NUTRIENT_KEYS:
            continue
        planned = [float((target or {}).get(key) or 0) for _, target, _ in days]
        logged = [float((row or {}).get(key) or 0) for _, _, row in days]
        if not planned:
            continue
        if key not in MACRO_KEYS and not all(value > 0 for value in planned):
            # A day predating the target states no figure. See the docstring:
            # the row is omitted rather than averaged over a 0.
            continue
        mean_planned = sum(planned) / len(planned)
        mean_logged = sum(logged) / len(logged)
        rows.append(
            MacroAccuracyRow(
                key=key,
                label=label,
                planned=round(mean_planned, 1),
                logged=round(mean_logged, 1),
                band=macro_band(mean_logged, mean_planned),
            )
        )
    state = _series_state(len(days))
    if state == INSIGHT_EMPTY:
        return MacroAccuracyPanel(
            state=state,
            headline="Nothing to compare yet",
            detail="Needs a day that was both planned and logged.",
            rows=[],
            days=0,
        )
    if state == INSIGHT_SPARSE:
        return MacroAccuracyPanel(
            state=state,
            headline="Not enough paired days",
            detail=(
                f"{len(days)} day(s) of {INSIGHT_MIN_POINTS}. One day's split is "
                "a meal, not a pattern."
            ),
            rows=rows,
            days=len(days),
        )
    off = [row.label for row in rows if row.band == "off"]
    return MacroAccuracyPanel(
        state=state,
        headline=(
            f"{', '.join(off)} off plan" if off else "Every macro within 15% of plan"
        ),
        detail=f"Mean of {len(days)} paired day(s), logged against planned.",
        rows=rows,
        days=len(days),
    )


def adherence_panel(
    adherence: Optional[Dict[str, List[dict]]],
    biometrics: Optional[dict],
    window_days: int = INSIGHT_WINDOW_DAYS,
) -> AdherencePanel:
    """What was marked, and what the watch recorded, over the same window.

    **Tiles, not a trend, so there is no `INSIGHT_MIN_POINTS` gate on them.**
    A count of three marks is a true statement about three marks; a line
    through three points is a claim about a direction. The only failure a
    count has is being zero, and that is `INSIGHT_EMPTY`.

    **This is the thinnest of the five and the queue says so.** Unlike a
    weigh-in or a Cronometer row, a mark exists only because somebody clicked
    it — the series does not accumulate merely because the sync job runs — so
    "have I been marking" is its own precondition, and the empty state names
    it rather than implying the data is late.
    """
    meals = [row for row in ((adherence or {}).get("meals") or []) if isinstance(row, dict)]
    workouts = [
        row for row in ((adherence or {}).get("workouts") or []) if isinstance(row, dict)
    ]
    activity = [
        row for row in ((biometrics or {}).get("activity_log") or []) if isinstance(row, dict)
    ]
    dates = _windowed_dates(
        [str(row.get("date") or "") for row in meals + workouts + activity], window_days
    )
    marked = [row for row in meals if str(row.get("date") or "")[:10] in dates]
    counts = Counter(
        str(row.get("status") or "") for row in marked if row.get("status")
    )
    completions = [
        row
        for row in workouts
        if str(row.get("date") or "")[:10] in dates and row.get("completed")
    ]
    recorded = sum(1 for row in activity if str(row.get("date") or "")[:10] in dates)
    marked_days = len({str(row.get("date") or "")[:10] for row in marked})
    common = dict(
        counts={status: counts.get(status, 0) for status in ADHERENCE_MARK_ORDER},
        marked_days=marked_days,
        workouts_recorded=recorded,
        workouts_marked=len(completions),
    )
    if not marked and not completions and not recorded:
        return AdherencePanel(
            state=INSIGHT_EMPTY,
            headline="Nothing marked yet",
            detail=(
                "Unlike the charts above, this one fills only when you mark a "
                "meal — the tick, cross and swap under each card in Daily View. "
                "The sync job cannot fill it for you."
            ),
            **common,
        )
    return AdherencePanel(
        state=INSIGHT_READY,
        headline=f"{sum(common['counts'].values())} meal(s) marked across {marked_days} day(s)",
        detail=(
            f"{recorded} session(s) recorded by Garmin, {len(completions)} marked by hand, "
            f"in the last {window_days} days."
        ),
        **common,
    )


# --------------------------------------------------------------------------
# The generation dialog's per-stage checklist
# --------------------------------------------------------------------------

GENERATION_STAGE_PENDING = "pending"
GENERATION_STAGE_RUNNING = "running"
GENERATION_STAGE_BANKED = "banked"


@dataclass
class GenerationStageView:
    """One meal type's line in the progress dialog: banked, running, pending.

    A `linear_progress` bar and a status line say "4 of 5" and nothing else.
    Across a run that can take fifteen minutes, what a reader wants is
    *which* meal types are already banked — a dinner that landed twelve
    minutes ago is a result, not a fraction of one.
    """

    meal_type: str
    label: str
    state: str
    cooks: Optional[int]

    @property
    def detail(self) -> str:
        """The cook count, once that stage has reported one.

        None until `on_meal_type` fires for this stage — the count comes from
        that callback, so a stage still pending has genuinely not been counted
        yet and prints nothing rather than a `0` it would then have to correct.
        """
        if self.cooks is None:
            return ""
        return f"{self.cooks} recipe(s)" if self.cooks else "nothing to cook"


def generation_stage_views(
    order: Sequence[str],
    started: int,
    cooks: Dict[str, int],
    complete: bool,
) -> List[GenerationStageView]:
    """Which stages are banked, which one is running, which are still queued.

    **`started` counts stages *begun*, not stages finished**, because that is
    what `generate_week_plan`'s `progress_callback` gives us: it fires once
    per meal type *before* that meal type's call. So index `started - 1` is
    the one currently in flight and everything below it is banked. Reading
    `started` as "finished" would tick a stage as done up to three minutes
    before its recipes exist, which on a free route is exactly the window a
    reader is watching this list to understand.

    That leaves the final stage with nothing to bank it — no later callback
    ever fires — which is what `complete` is for: `generate_week` sets it once
    `generate_week_plan` has returned, and it banks every stage at once. A run
    that raises instead leaves the last stage showing as running, which is
    true: it is the one that was in flight when the run came apart.
    """
    views: List[GenerationStageView] = []
    for index, meal_type in enumerate(order):
        if complete or index < started - 1:
            state = GENERATION_STAGE_BANKED
        elif index == started - 1:
            state = GENERATION_STAGE_RUNNING
        else:
            state = GENERATION_STAGE_PENDING
        views.append(
            GenerationStageView(
                meal_type=meal_type,
                label=humanize(pluralize(meal_type)).capitalize(),
                state=state,
                cooks=cooks.get(meal_type),
            )
        )
    return views
