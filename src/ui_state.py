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
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from planner import (
    LOCATION_RESTRICTION_PHRASES,
    MACRO_KEYS,
    NUTRIENT_KEYS,
    SUNDAY_PREP_REHEAT_MINUTES,
    TRAINING_NOTE_PREFIXES,
    CookEvent,
    Recipe,
    WeekPlan,
    TARGET_MODE_AUTO,
    TARGET_MODE_MACROS,
    TARGET_MODE_MANUAL,
    apply_training_adjustments,
    calculate_daily_targets,
    hydrate_dynamic_targets,
    day_multiplicity,
    is_prepped_ahead,
    is_sunday_prepped,
    load_app_config,
    meal_overrides_for,
    resolve_planner_model,
    split_targets,
    weeknight_prep_minutes,
)
# `apply_training_adjustments` is its real owner; it is the tolerant "HH:MM"
# parse a drawer's free-text time field needs. Shared rather than
# reimplemented so the Today tab orders a day's sessions by the same clock
# reading that decides which meal gets the post-workout pin; a second parser
# is a second answer to "what time is `7:3o`?".
from planner import clock_minutes
from nutrition_engine import (
    ADAPTIVE_NO_LOGS,
    ADAPTIVE_NO_WEIGH_INS,
    ADAPTIVE_SHORT_SPAN,
    ADAPTIVE_TDEE_TOLERANCE,
    MIN_PROPOSAL_OCCURRENCES,
    PROPOSAL_DROP,
    TRAINING_PROPOSAL_NO_ACTIVITY,
    TRAINING_PROPOSAL_SHORT_HISTORY,
    AdaptiveTDEEStatus,
    ProposedSession,
    TrainingScheduleProposal,
    estimate_session_burn_kcal,
    measure_adaptive_tdee,
    propose_training_schedule,
    resolve_current_weight_kg,
)
from repository import BIOMETRIC_SECTION_SOURCES, LocalJSONRepository
from ui_theme import (
    LINK_COLOURS,
    LINK_SOURCE_MEAL,
    LINK_TARGET_MEAL,
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
)
from week import (
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    WeekSpec,
    clear_cuisines,
    clear_styles,
    cook_day_index,
    day_date,
    default_week_spec,
    eaten_on,
    humanize,
    leftover_link_error,
    link_leftover,
    location_for,
    location_rule,
    meal_types,
    next_day_slot_id,
    portions_for,
    set_skip_estimate,
    slot_id,
    slot_label,
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


@dataclass
class PendingChange:
    """One line of the staged-changes bar/review dialog's "N pending changes".

    Just a label — `pending_changes()` is the only producer, so there's
    nothing else a consumer needs to branch on. Kept as a real type rather
    than a bare string so a future consumer (grouping by category, say) has
    somewhere to add a field without every call site's tuple shape changing.
    """

    summary: str


@dataclass
class PlannerState:
    """Everything one browser tab is looking at.

    `week_plan` is None until a week has been generated at least once —
    `load_week_plan()` returning None is the documented cold start, not a
    failure, so the canvas falls back to previewing the default grid shape.
    """

    config: dict
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
    week_start: str = ""
    servings: int = 2
    shop_days: List[str] = field(default_factory=list)
    # Shopping-drawer display toggle only — never persisted and never fed to
    # `generate_week_plan`. True partitions the week into one window per cook
    # day (`shopping_windows(state.days, state.days)`) instead of the
    # configured `shop_days` trips; the underlying cook events and quantities
    # are identical either way, only the grouping changes.
    daily_shop_mode: bool = False
    # Real value is always set by `.load()` via `resolve_planner_model` —
    # this placeholder only exists because dataclasses require a default.
    model: str = ""
    focus: Optional[SlotView] = None
    edited: bool = False
    # Food already in the house, to be cooked through. Seeded from config's
    # `inventory_to_clear` and edited in the drawer; it reaches the model as a
    # priority, never a constraint (`planner.inventory_instruction`).
    pantry: List[str] = field(default_factory=list)
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
    # REPLACES the file's list. Booleans default from
    # config["enable_sunday_prep"] at load() time, so a config that already
    # had the old feature on opens the popup with both boxes pre-checked.
    cuisine_override: List[str] = field(default_factory=list)
    diet_style_override: List[str] = field(default_factory=list)
    bulk_prep_enabled: bool = False
    long_cook_enabled: bool = False
    # The popup's slider on `planning_rules.min_baseline_cuisine_share` — a
    # scalar, not a list, so unlike cuisine_override/diet_style_override there
    # is no "empty means use the file" state: it always feeds
    # `planning_config()`, the same way bulk_prep_enabled/long_cook_enabled
    # do, and is seeded from the file's own value at load() so opening the
    # popup previews the standing config rather than some other default.
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
    catalog_search: str = ""
    # The full-screen catalog browser's own filters — separate from
    # catalog_search above so typing in one surface doesn't silently refilter
    # the other. "All" means no meal-type filter, matching the select's own
    # placeholder option.
    catalog_browser_search: str = ""
    catalog_browser_meal_type: str = "All"
    catalog_browser_favorites_only: bool = False
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
        # `load_app_config` validates config.json against `AppConfig` here,
        # once, at startup — the same schema check the CLI gets from
        # `load_config_with_models`. Every field below is then guaranteed
        # present with a real value, so this reads them directly instead of
        # each picking its own `.get(key, DEFAULT)` fallback.
        config = load_app_config(await repository.load_config())
        models_config = await repository.load_models_config()
        latest_biometrics = await repository.get_latest_biometrics()
        # The *series* as well as the latest row, for the same reason
        # `hydrate_config` reads both: `calculate_adaptive_tdee` measures
        # expenditure from the whole weigh-in and intake history, and
        # `planning_config()` now runs that same hydration to preview it.
        biometrics = await repository.load_biometrics()
        state = cls(
            config=config,
            models_config=models_config,
            latest_biometrics=latest_biometrics,
            biometrics=biometrics,
            target_modes=dict(config["target_modes"]),
            weight_kg=resolve_current_weight_kg(config["user_profile"], latest_biometrics),
            week_start=config["week_start_day"],
            servings=config["serving_rules"]["servings_per_meal"],
            shop_days=list(config["shopping"]["shop_days"]),
            model=resolve_planner_model(dict(config, models=models_config)),
            pantry=[str(item).strip() for item in config["inventory_to_clear"] if str(item).strip()],
            training_schedule=[dict(session) for session in config["training_schedule"]],
            bulk_prep_enabled=config["enable_sunday_prep"],
            long_cook_enabled=config["enable_sunday_prep"],
            baseline_cuisine_share=config["planning_rules"]["min_baseline_cuisine_share"],
        )
        state._original_training_schedule = [dict(session) for session in state.training_schedule]
        state.recipe_catalog = await repository.load_recipe_catalog()
        await state.reload_plan(repository)
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

    @property
    def days(self) -> List[str]:
        return week_days(self.config, self.week_start)

    @property
    def meal_types(self) -> List[str]:
        return meal_types(self.config)

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

    def step_viewed_day(self, delta: int) -> None:
        """Move `delta` days through the week, clamped at both ends.

        Clamped rather than wrapped, and deliberately not spilling into the
        other cached week: `week_plan` holds exactly these seven days, and
        stepping past Sunday would mean an async load of the "next" plan plus
        a second control able to disagree with the header's week selector.
        The chevrons disable at the ends instead.
        """
        day = self.viewed_day()
        days = self.days
        if day is None or day not in days:
            return
        self.selected_day = days[min(max(days.index(day) + delta, 0), len(days) - 1)]

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
                    # `self.config` threaded through so the storage note uses the
                    # configured `inventory_rules.fridge_safe_days`. Omitting it
                    # silently fell back to week.DEFAULT_INVENTORY_RULES, so an
                    # edited config would disagree with the note on the card.
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
                    inventory_to_clear=list(self.pantry),
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
                            bulk_prep_enabled=self.bulk_prep_enabled,
                            long_cook_enabled=self.long_cook_enabled,
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

        if self.edited:
            changes.append(PendingChange("grid edited"))

        return changes

    def discard_pending_inputs(self) -> None:
        """Reset target overrides, the training schedule and the pantry list
        back to what config.json/`.load()` gave them — the non-grid three
        quarters of `pending_changes()`.

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
        self.pantry = [
            str(item).strip() for item in self.config["inventory_to_clear"] if str(item).strip()
        ]

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
        """What the day's recipes carry in fibre, beside what was logged."""
        logged = (self.logged_actuals_for(day) or {}).get("fiber_g")
        return fibre_view(
            float(self.totals_for(day).get("fiber_g") or 0.0),
            float(logged) if isinstance(logged, (int, float)) else None,
        )

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
            if sunday_prepped:
                fridge_safe_days = self.config["inventory_rules"]["fridge_safe_days"]
                # Per-slot distance from its cook day, not `span_days`'s
                # whole-batch span to its *farthest* eater — a Tuesday
                # portion of a batch that runs to next Sunday is still
                # fridge-fresh even though the Sunday portion isn't. The
                # anchor's own slot is 1, not 0, for a prep-session batch:
                # it was cooked the day before the week started.
                days_since_cook = spec.day_index(slot.day) - cook_day_index(
                    spec, event.day, is_prepped_ahead(event, self.week_plan)
                )
                frozen = days_since_cook >= fridge_safe_days
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
                    # each morning genuinely blends it fresh — so only the
                    # dinner-axis anchors (bulk-prep/long-cook) collapse to
                    # the reheat estimate here; meal_type is what tells the
                    # two apart, since both are MODE_COOK and sunday_prepped.
                    else (
                        SUNDAY_PREP_REHEAT_MINUTES
                        if sunday_prepped and event.meal_type == "dinner"
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
    """A day's planned fibre, and — when Cronometer logged the day — what was
    actually eaten beside it.

    **Fibre is reported, never budgeted** (`planner.NUTRIENT_KEYS`), so the
    telemetry header prints a bare `FIB 32g` where every other figure in that
    row carries `actual/target`: there is no fibre target, and `32/xx` would
    invent a goal the planner never aimed at. That is still true here and is
    what `logged` is *not* — a logged figure is not a goal, it is the same
    quantity measured a second way, so the two sit side by side rather than
    over a divider.

    This is the only macro the app has one half of that pair and not the
    other for: calories, protein, carbs and fat all have a target to be
    measured against, and until `CRONOMETER_MACRO_COLUMNS` learned `fiber_g`
    fibre had neither. `delta` is signed against the plan (`logged - planned`,
    so negative means the day came in short of what was cooked for it) and is
    None whenever `logged` is.
    """

    planned: float
    logged: Optional[float]
    delta: Optional[float]
    label: str
    logged_label: str
    detail: str


def fibre_view(planned: float, logged: Optional[float]) -> FibreView:
    """`FibreView` for one day. Pure — the caller supplies both figures.

    `logged` is None for every day nothing has been synced against, which is
    the normal state for the whole of a week planned ahead: only days that
    have actually happened can have been logged. Those get the planned figure
    alone, exactly as the header printed it before this existed.
    """
    label = f"FIB {planned:.0f}g"
    if logged is None:
        return FibreView(
            planned=planned,
            logged=None,
            delta=None,
            label=label,
            logged_label="",
            detail=f"fibre: {planned:.0f}g planned (tracked, no target)",
        )
    delta = logged - planned
    return FibreView(
        planned=planned,
        logged=logged,
        delta=delta,
        label=label,
        logged_label=f"logged {logged:.0f}g",
        # Named as a comparison against the plan, never against a target —
        # the wording is the whole guard against this reading as a goal that
        # was missed.
        detail=(
            f"fibre: {planned:.0f}g planned, {logged:.0f}g logged "
            f"({delta:+.0f}g vs plan) — still no target either way"
        ),
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
