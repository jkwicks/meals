=== File: ./ui_app.py ===
"""NiceGUI front end — the whole week on one high-density desktop screen.

The only web front end, and now a complete one: it can both generate a week and
rearrange it, so `python planner.py` is an alternative rather than a
prerequisite.

**Generating is the only thing here that writes to disk**, and it writes
everything at once — `week_plan.json` and a history entry per cooked day —
because a generated week that isn't saved is a 20-minute run one browser
refresh from being lost. Grid *edits* ("Link to next lunch") are still
in-memory only: they live in the client's `PlannerState` until "Reload from
disk" throws them away, and the header carries an "edited — not saved" chip
while any are outstanding. Generating clears that chip, because saving is what
it just did.

Three regions, mirroring how the week is actually read:

- **Left drawer** — the global knobs (week start, household size, shopping
  days, model), the per-day macro targets and the pantry list, plus the
  generation trigger. Everything that applies to the whole week rather than one
  meal. Target overrides and the pantry are *inputs to the next run*: they are
  held in `PlannerState`, merged into `planning_config()`, and never written
  back to config.json.
- **Header** — macro telemetry: one horizontal bar per day, in the *same*
  7-column grid as the canvas below, so a day's bar sits directly above its
  column of meals.
- **Canvas** — 7 day columns x 4 stacked meal cards, cook vs. leftover
  distinguished by colour, border and badge.
- **Right drawer** — the shopping list, one section per trip, opened from the
  header. It is derived from the plan on every repaint, so it always describes
  the week as the grid currently stands rather than as it was generated.

Why this can await the repository directly
------------------------------------------
NiceGUI page handlers run *on* the event loop, so `await REPOSITORY.load_*()`
is the natural call here — this is the async repository paying off. Do **not**
reach for `repository.run_sync()` in this file: it detects the running loop and
hands the coroutine to a scratch thread, which is pure overhead when we are
already async, and would serialise page loads behind a thread pool.

The same rule is what makes the Generate button viable. `generate_week_plan` is
awaited straight from its click handler, and it keeps the loop free by
dispatching each day's blocking API call to a worker thread — so the progress
modal actually animates during the run instead of painting once it is over, and
other tabs stay responsive throughout. Anything long added here must do the
same; a bare blocking call in a handler freezes every connected browser.

Why refreshables, and not a re-run model
---------------------------------------
A re-run front end re-executes its whole module on every widget interaction,
forcing the grid into a session-state cache purely to survive that. NiceGUI
keeps the Python objects alive per client, so the UI binds to a `PlannerState`
and only the `@ui.refreshable` sections that depend on a changed field are
re-rendered. Changing the week start repaints 7 columns, not the page.

State is created *inside* the page function on purpose: module-level state
would be shared by every browser tab connected to this server.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dotenv import load_dotenv
from nicegui import ui
from pydantic import ValidationError

from export_menu import build_week_menu_pdf
from planner import (
    MACRO_KEYS,
    TRAINING_INTENSITY_SPLIT,
    CookEvent,
    Recipe,
    WeekPlan,
    api_key_error,
    apply_training_adjustments,
    calculate_daily_targets,
    configure_logging,
    generate_week_plan,
    import_external_recipe,
    is_sunday_prepped,
    load_app_config,
    meal_overrides_for,
    meal_type_order,
    record_week_history,
    regenerate_single_day,
    regenerate_single_meal,
    resolve_auto_choices,
    resolve_planner_model,
    split_targets,
    weeknight_prep_minutes,
)
from repository import LocalJSONRepository, recipe_content_key
from shopping import (
    aggregate_cook_events,
    cook_plan_lines,
    format_quantity,
    format_shopping_list_keep,
)
from week import (
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    WeekSpec,
    default_week_spec,
    eaten_on,
    humanize,
    leftover_link_error,
    link_leftover,
    meal_types,
    next_day_slot_id,
    parse_slot_id,
    portions_for,
    shopping_windows,
    slot_id,
    slot_label,
    span_days,
    validate_week,
    week_date_range,
    week_days,
)

load_dotenv()
configure_logging()

# One repository for the server, imported once rather than re-executed per
# interaction. It holds paths only, so pointing the app at a different backend
# stays a one-line change here. File names live on REPOSITORY.paths
# (repository.py's StoragePaths), not as a module constant here.
REPOSITORY = LocalJSONRepository()

# The two cached weeks the app keeps on disk at once (see
# `repository.LocalJSONRepository._week_plan_path`). "current" is the
# original single-file layout (week_plan.json); "next" is stored alongside it
# as week_plan_next.json. Keys are what's passed to `load_week_plan`/
# `save_week_plan`, values are what the header select shows.
WEEK_SELECTION_LABELS = {"current": "Current Week", "next": "Next Week"}

# A slot's render status is its mode, except that a cook (or the cook a
# leftover points at) whose day failed to generate has no recipe to show. That
# is a fourth visual state, not an error: CLAUDE.md's "a failed day must not
# fail the week" means the rest of the grid still renders around it.
STATUS_COOK = "cook"
STATUS_LEFTOVER = "leftover"
STATUS_SKIP = "skip"
STATUS_MISSING = "missing"

# Tailwind per status. Each status is a *tint plus a rule*, not just a border
# colour: cook is the only one that carries a lit background, because it is the
# only card that costs you an evening. Leftover is dashed and cooler — nothing
# is bought or cooked for it, so it should read as derived from the card it
# points at rather than as its own event. `glow` is the hover colour (see
# `card_css`), and `icon` is what makes the chip legible at a glance down a
# column of 28.
STATUS_STYLES = {
    STATUS_COOK: {
        "card": "border border-emerald-400/25 border-l-[3px] border-l-emerald-400 bg-emerald-400/[0.07]",
        "badge": "bg-emerald-400/20 text-emerald-200 ring-1 ring-inset ring-emerald-300/30",
        "label": "COOK",
        "icon": "local_fire_department",
        "glow": "#34d399",
    },
    STATUS_LEFTOVER: {
        "card": "border border-dashed border-sky-400/30 border-l-[3px] border-l-sky-400 bg-sky-400/[0.04]",
        "badge": "bg-sky-400/15 text-sky-200 ring-1 ring-inset ring-sky-300/30",
        "label": "LEFTOVER",
        "icon": "restore",
        "glow": "#38bdf8",
    },
    STATUS_SKIP: {
        "card": "border border-dashed border-slate-800 border-l-[3px] border-l-slate-700 bg-slate-900/40",
        "badge": "bg-slate-700/40 text-slate-400 ring-1 ring-inset ring-slate-600/30",
        "label": "SKIP",
        "icon": "remove",
        "glow": "#64748b",
    },
    STATUS_MISSING: {
        "card": "border border-rose-500/30 border-l-[3px] border-l-rose-500 bg-rose-500/[0.07]",
        "badge": "bg-rose-500/20 text-rose-200 ring-1 ring-inset ring-rose-400/30",
        "label": "NOT GENERATED",
        "icon": "error_outline",
        "glow": "#fb7185",
    },
}

# `SlotView.prep_badge` — set only on a leftover card eating a Sunday-prepped
# batch (see `planner.is_sunday_prepped`). "fridge" vs. "freezer" mirrors the
# same threshold `storage_note` used to write the cook's own storage note, so
# the badge never disagrees with the text a user would see on the recipe.
PREP_BADGE_STYLES = {
    "fridge": {
        "label": "⚡ Prepped on Sun",
        "classes": "bg-amber-400/15 text-amber-200 ring-1 ring-inset ring-amber-300/30",
    },
    "freezer": {
        "label": "❄️ From Freezer",
        "classes": "bg-cyan-400/15 text-cyan-200 ring-1 ring-inset ring-cyan-300/30",
    },
}

# (key, short label, unit suffix). Calories carry no suffix because their
# short label already reads as one — "kcal: 2200kcal" otherwise.
MACRO_LABELS = [
    ("calories", "kcal", ""),
    ("protein_g", "P", "g"),
    ("net_carbs_g", "C", "g"),
    ("fat_g", "F", "g"),
]

# The three macros that ride behind the calorie figure on a card's micro-pill
# strip, each with the tint that identifies it everywhere in the UI. Colour is
# on the letter, not the number: the digits are what you compare between cards,
# so they stay one weight and one colour down the whole column.
MACRO_TINTS = {
    "protein_g": "text-sky-300",
    "net_carbs_g": "text-amber-300",
    "fat_g": "text-violet-300",
}

# Fallbacks for config.json's "ui_settings" object, used when a config.json
# predates that section.
#
# A title longer than title_tooltip_chars can't fit the card's two lines at
# this column width, so it gets a tooltip carrying the full name. Below it the
# tooltip would only repeat what is already on screen.
DEFAULT_UI_SETTINGS = {
    "bar_scale_limit": 1.6,
    "title_tooltip_chars": 38,
}

# bar_scale_limit (in DEFAULT_UI_SETTINGS above) is how far a telemetry bar
# can extend past its target before it stops growing. The bar's full width is
# `max(1, ratio)` capped there, so an overshoot renders as a real second
# segment rather than a bar pinned at 100% that looks identical to landing
# exactly on budget.

# Band -> hex, for the telemetry bars. Hex rather than Quasar colour names
# because these are painted onto plain divs (a two-segment bar is not something
# `ui.linear_progress` can draw) and the same value has to serve as the
# overshoot segment at reduced alpha.
BAND_COLOURS = {
    "on": "#34d399",  # within ±5% of target
    "near": "#fbbf24",  # ±5–15%: worth seeing, not worth fixing
    "off": "#fb7185",  # beyond ±15%
    "none": "#475569",  # nothing generated for this day yet
}

# The one-click leftover action: tonight's dinner feeds tomorrow's lunch. This
# is the overwhelmingly common bulk-cooking pattern (it is the same one
# `week.autofill_leftovers` automates for the whole week), offered per-card so
# it can be applied to one dinner without rewriting the grid.
LINK_SOURCE_MEAL = "dinner"
LINK_TARGET_MEAL = "lunch"
LINK_ACTION_LABEL = "Link to next lunch"

# Hues for the cook->leftover chains. Cycled, so two chains can share a colour
# on a busy week — the colour is a hint, the hover outline (keyed on a unique
# class per chain) is what disambiguates.
LINK_COLOURS = ["#38bdf8", "#fbbf24", "#a78bfa", "#34d399", "#fb7185", "#22d3ee"]

# The three targets that are editable in the drawer. Fat is deliberately not
# among them: `derive_fat_g` computes it from the other three, so an input for
# it could only ever disagree with the number the planner actually uses.
TARGET_FIELDS = [
    ("calories", "kcal"),
    ("protein_g", "protein g"),
    ("net_carbs_g", "carbs g"),
]

# Indigo marks the Sunday prep column everywhere it appears (telemetry header,
# canvas, pipeline row) — deliberately outside the emerald/sky/slate/rose
# palette STATUS_STYLES and BAND_COLOURS already use for day statuses, since
# this column is prep work, not an eating slot, and must never read as a fifth
# status.
PREP_COLUMN_ACCENT = "border border-indigo-400/25 border-l-[3px] border-l-indigo-400 bg-indigo-400/[0.05]"

# Selectable workout types. "rest" is a legitimate entry (a day explicitly
# marked as no training) but carries no macro split — `apply_training_adjustments`
# skips it — so it isn't a key in TRAINING_INTENSITY_SPLIT and is appended here.
TRAINING_TYPES = list(TRAINING_INTENSITY_SPLIT) + ["rest"]
TRAINING_TYPE_LABELS = {value: humanize(value) for value in TRAINING_TYPES}

# The context pipeline shown above the telemetry header: what's supposed to
# feed a day's plan, in dependency order. (key, label, icon, description,
# connected). `connected=False` stages have no data source wired up yet —
# they render as a permanently dashed/muted chip until something real lands
# in `pipeline_value()`. "Meal Plan" isn't a fifth stage here because
# `telemetry()` already renders it immediately below this row.
PIPELINE_STAGES = [
    (
        "readiness",
        "Morning Readiness",
        "self_improvement",
        "Subjective readiness check-in — not built yet.",
        False,
    ),
    (
        "sync",
        "Health Connect Sync",
        "monitor_heart",
        "Garmin sleep/Body Battery — not built yet.",
        False,
    ),
    (
        "context",
        "Calendar/Location",
        "event",
        "WFH vs. in-office, meeting load — not built yet.",
        False,
    ),
    (
        "workout",
        "Adaptive Workout",
        "fitness_center",
        "Training session for the day, from the drawer's schedule.",
        True,
    ),
]


def pipeline_value(state: "PlannerState", day: str, key: str) -> Optional[str]:
    """What a connected pipeline stage has for `day`, or None if unset.

    Only "workout" is wired today — it reads the same `training_schedule`
    `has_training()`/the telemetry ⚡ marker already use, so this is a real
    signal, not a placeholder, from the day this pipeline ships. The other
    stages stay in `PIPELINE_STAGES` with `connected=False` and never reach
    here.
    """
    if key == "workout":
        session = next(
            (s for s in state.training_schedule if s.get("day") == day), None
        )
        if session is None:
            return None
        return TRAINING_TYPE_LABELS.get(session["type"], session["type"])
    return None


# --------------------------------------------------------------------------
# View model
# --------------------------------------------------------------------------


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

    @property
    def id(self) -> str:
        return slot_id(self.day, self.meal_type)

    @property
    def chain_colour(self) -> str:
        return LINK_COLOURS[(self.chain or 0) % len(LINK_COLOURS)]


@dataclass
class PlannerState:
    """Everything one browser tab is looking at.

    `week_plan` is None until a week has been generated at least once —
    `load_week_plan()` returning None is the documented cold start, not a
    failure, so the canvas falls back to previewing the default grid shape.
    """

    config: dict
    # models.json, loaded alongside config — the drawer's model select reads
    # `models_config["selectable_options"]` and `.load()` uses
    # `models_config["default_planner_model"]` as `model`'s starting value
    # when config.json has no `openrouter_model` override.
    models_config: dict = field(default_factory=dict)
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
    # Workout sessions ({day, time, type, duration_minutes, estimated_burn_kcal}).
    # Seeded from config's `training_schedule` and edited in the drawer; like
    # the pantry it is an input to the *next* run, folded into
    # `planning_config()` by `planner.apply_training_adjustments` and never
    # written back to config.json.
    training_schedule: List[dict] = field(default_factory=list)
    # Which day's context-pipeline dialog is open, if any. Held the same way
    # as `focus` is for the recipe detail dialog: one dialog reused for all
    # seven days, refreshable off this key rather than seven pre-built dialogs.
    pipeline_day: Optional[str] = None
    # A run holds this client for 30s-3min per cooking day. The loop stays free
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

    @classmethod
    async def load(cls, repository: LocalJSONRepository) -> "PlannerState":
        # `load_app_config` validates config.json against `AppConfig` here,
        # once, at startup — the same schema check the CLI gets from
        # `load_config_with_models`. Every field below is then guaranteed
        # present with a real value, so this reads them directly instead of
        # each picking its own `.get(key, DEFAULT)` fallback.
        config = load_app_config(await repository.load_config())
        models_config = await repository.load_models_config()
        state = cls(
            config=config,
            models_config=models_config,
            week_start=config["week_start_day"],
            servings=config["serving_rules"]["servings_per_meal"],
            shop_days=list(config["shopping"]["shop_days"]),
            model=resolve_planner_model(dict(config, models=models_config)),
            pantry=[str(item).strip() for item in config["inventory_to_clear"] if str(item).strip()],
            training_schedule=[dict(session) for session in config["training_schedule"]],
        )
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
        plan never actually loaded. No generation call here — this only ever
        reads what's already on disk, per CLAUDE.md's "generating is the only
        thing that writes to disk."
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
                    "recipe": event.recipe.scale_to_servings(
                        target, span_days(spec, event.slot_id)
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

    def shuffle_styles(self) -> None:
        """Blank the style/cuisine on every cook slot so the next generation
        re-rolls them from scratch.

        `resolve_auto_choices` only picks a fresh style/cuisine when a slot's
        is empty (planner.py) — once a week has been generated once, its
        slots carry the concrete values from that run and every later
        "Generate" re-requests the exact same style/cuisine per slot forever,
        varying only the dish the model composes inside it. This is the
        explicit escape hatch: it touches only style/cuisine, not mode or
        leftover links, so `apply_spec` can go through its normal rescale
        path unchanged.
        """
        spec = self.spec
        resolved = [
            slot.model_copy(update={"style": None, "cuisine": None})
            if slot.mode == MODE_COOK
            else slot
            for slot in spec.slots
        ]
        self.apply_spec(spec.model_copy(update={"slots": resolved}))

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
            keeps_for_days=span_days(spec, source_id),
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

    def planning_config(self) -> dict:
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

        Nothing here is written back to config.json. Overrides are meant to be
        "this week is different", and generating is still the only thing in
        this app that touches disk.
        """
        schedule = {
            day: dict(day_config, **self.target_overrides.get(day, {}))
            for day, day_config in self.config["weekly_schedule"].items()
        }
        return apply_training_adjustments(
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
            )
        )

    def planned_targets(self, day: str) -> dict:
        """What the next run will aim at for `day` — file numbers plus overrides.

        `fat_g` comes back derived, so the drawer shows the same figure the
        model will be told rather than one the UI computed its own way.
        """
        return calculate_daily_targets(day, self.planning_config())

    def set_target(self, day: str, key: str, value: float) -> None:
        """Record a drawer edit to one of a day's macro targets.

        A value equal to config.json clears that key instead of storing a no-op
        override, so "overridden" always means "differs from the file". That is
        also what makes the reset button able to undo itself: it writes the
        file's numbers back into the inputs, and the change events those fire
        land here and cancel out rather than re-creating the override.
        """
        base = self.config["weekly_schedule"].get(day, {})
        override = dict(self.target_overrides.get(day, {}))
        if float(base.get(key, 0)) == float(value):
            override.pop(key, None)
        else:
            override[key] = float(value)

        if override:
            self.target_overrides[day] = override
        else:
            self.target_overrides.pop(day, None)

    def clear_targets(self, day: Optional[str] = None) -> None:
        """Drop one day's overrides, or the whole week's."""
        if day is None:
            self.target_overrides.clear()
        else:
            self.target_overrides.pop(day, None)

    def add_training_session(self) -> None:
        """Append a new workout row with sane defaults, ready to edit in place."""
        self.training_schedule.append(
            {
                "day": self.days[0] if self.days else "Monday",
                "time": "07:00",
                "type": TRAINING_TYPES[0],
                "duration_minutes": 60,
                "estimated_burn_kcal": 300,
            }
        )

    def remove_training_session(self, index: int) -> None:
        if 0 <= index < len(self.training_schedule):
            self.training_schedule.pop(index)

    def has_training(self, day: str) -> bool:
        return any(session.get("day") == day for session in self.training_schedule)

    def targets_for(self, day: str) -> dict:
        """The denominator the telemetry header measures a day against.

        An override — or a workout scheduled that day — wins over the
        generated plan's own targets on purpose: the point of editing a
        target, or a training session, before a run is to see how far the
        current week sits from where you are about to aim it. Without this, a
        cached `week_plan.json` would keep showing yesterday's target and a
        training edit would silently do nothing until the next generation —
        exactly the "not live" failure this control exists to avoid. Otherwise
        a generated week is measured against what it was generated for, and an
        un-generated one against config, so the header always has something to
        divide by.
        """
        if day in self.target_overrides or self.has_training(day):
            return self.planned_targets(day)
        if self.week_plan and day in self.week_plan.targets:
            return self.week_plan.targets[day]
        return self.planned_targets(day)

    def totals_for(self, day: str) -> dict:
        if not self.week_plan:
            return {key: 0.0 for key in MACRO_KEYS}
        return self.week_plan.day_slot_macros(day)

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
                views[slot.id] = SlotView(
                    day=slot.day,
                    meal_type=slot.meal_type,
                    status=STATUS_SKIP,
                    title="Skipped",
                    mode=slot.mode,
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

            # A leftover eating a Sunday-prepped batch gets a badge and a
            # reheat/assemble estimate instead of the cook's from-scratch
            # prep time — see `planner.is_sunday_prepped`. "fridge" vs.
            # "freezer" mirrors the same span-vs-fridge-safe-days threshold
            # `storage_note` used to write the batch's own storage note.
            prep_badge, prep_origin = "", ""
            if slot.mode == MODE_LEFTOVER and is_sunday_prepped(event, self.week_plan):
                fridge_safe_days = self.config["inventory_rules"]["fridge_safe_days"]
                # Per-slot distance from its cook day, not `span_days`'s
                # whole-batch span to its *farthest* eater — a Tuesday
                # portion of a batch that runs to next Sunday is still
                # fridge-fresh even though the Sunday portion isn't.
                days_since_cook = spec.day_index(slot.day) - spec.day_index(event.day)
                frozen = days_since_cook >= fridge_safe_days
                prep_badge = "freezer" if frozen else "fridge"
                prep_origin = (
                    f"From the Sunday prep session: {event.recipe.name} "
                    f"({event.portions} portions, cooked {event.day})"
                    + (" — frozen, thaw ahead of eating" if frozen else " — kept refrigerated")
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
                    else event.recipe.prep_time_minutes
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


def chain_css(chains: int) -> str:
    """CSS that lights up a whole cook->leftover chain when any card in it is hovered.

    `:has()` on the canvas is what makes this work without JavaScript: hovering
    any member matches the ancestor, which then outlines *every* card carrying
    that chain's class, wherever it sits in the 7-column grid. A round trip to
    Python per mouseenter would be visibly laggy for a pure hover effect.

    One rule per chain because a selector can't compare one element's class to
    another's; `chains` is the most a week of this shape can hold, since every
    chain needs a cook plus at least one leftover. Outline rather than border
    so nothing reflows, and browsers without `:has()` (pre-2023) simply lose
    the highlight — the dot and the "feeds"/"from" lines still name both ends.
    """
    rules = [
        ".meal-canvas .chain {"
        " outline: 1px solid transparent; outline-offset: 2px;"
        " border-radius: 0.25rem; transition: outline-color 120ms ease; }"
    ]
    for index in range(max(chains, 0)):
        colour = LINK_COLOURS[index % len(LINK_COLOURS)]
        rules.append(
            f".meal-canvas:has(.chain-{index}:hover) .chain-{index}"
            f" {{ outline-color: {colour}; }}"
        )
    return "\n".join(rules)


def card_hover_css() -> str:
    """Per-status glow, keyed off `STATUS_STYLES[...]['glow']`.

    A `box-shadow` rather than a border-width change: the latter reflows
    neighbouring cards by a pixel on hover, which reads as a jitter down a
    column of 28 cards rather than as polish.
    """
    rules = []
    for status, look in STATUS_STYLES.items():
        rules.append(
            f".meal-card.card-{status}:hover {{"
            f" border-color: {look['glow']};"
            f" box-shadow: 0 0 0 1px {look['glow']}66, 0 0 14px 0 {look['glow']}40; }}"
        )
    return "\n".join(rules)


def link_line(marker: str, text: str, colour: str) -> None:
    """The one-line "this card is tied to that one" note, in its chain's colour.

    Both ends get one — the cook says who eats it, the leftover says what it
    came from — so the pairing is readable without hovering anything.
    """
    with ui.element("div").classes("flex flex-row items-center gap-1 min-w-0"):
        ui.element("span").classes("shrink-0 w-1.5 h-1.5 rounded-full").style(
            f"background: {colour}"
        )
        ui.label(f"{marker} {text}").classes("text-[9px] truncate").style(f"color: {colour}")


def macro_band(actual: float, target: float) -> str:
    """Which `BAND_COLOURS` key a day landed in against one macro's target.

    A single scale factor can't fix a bad macro *ratio* (CLAUDE.md), so this is
    a read on the day, not a promise the plan is right — on ±5%, near ±15%,
    off beyond that. Wide enough that only a genuinely off day goes red.
    """
    if target <= 0 or actual <= 0:
        return "none"
    delta = abs(actual / target - 1)
    if delta <= 0.05:
        return "on"
    if delta <= 0.15:
        return "near"
    return "off"


def telemetry_bar(
    actual: float,
    target: float,
    *,
    height: str = "8px",
    bar_scale_limit: float = DEFAULT_UI_SETTINGS["bar_scale_limit"],
) -> None:
    """A target-vs-actual bar that keeps growing past 100% instead of clipping.

    Plain nested divs, not `ui.linear_progress`: a bar pinned at 100% looks
    identical whether a day landed on target or blew past it, so the fill
    is scaled against `bar_scale_limit` (config.json's
    `ui_settings.bar_scale_limit`) and a genuine overshoot renders as a
    visibly longer bar. The thin marker line is where the target itself sits
    on that same scale, so "landed short" and "landed long" both read at a
    glance relative to it.
    """
    colour = BAND_COLOURS[macro_band(actual, target)]
    ratio = (actual / target) if target else 0.0
    fill_pct = min(max(ratio, 0.0), bar_scale_limit) / bar_scale_limit * 100
    target_pct = 100 / bar_scale_limit
    with ui.element("div").classes(
        "relative w-full rounded-full bg-slate-800 overflow-hidden"
    ).style(f"height: {height}"):
        ui.element("div").classes(
            "absolute inset-y-0 left-0 rounded-full transition-all duration-300"
        ).style(f"width: {fill_pct:.1f}%; background: {colour};")
        ui.element("div").classes("absolute inset-y-0 w-px bg-slate-100/50").style(
            f"left: {target_pct:.1f}%;"
        )


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

    claims = eaten_on(spec)
    multiplicity = {slot.id: len(claims.get(slot.id, [slot.id])) for slot in cook_slots}

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


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------


@ui.page("/")
async def planner_page() -> None:
    state = await PlannerState.load(REPOSITORY)

    ui.dark_mode(True)
    ui.add_css(
        # Quasar's page container assumes comfortable padding; a 7-column week
        # needs the horizontal space back.
        ".nicegui-content { padding: 0.75rem; gap: 0.75rem; }\n"
        # Emitted once per page rather than from `canvas`, which is refreshable
        # and would stack another copy into the head on every repaint. The
        # bound is the week's shape, not its current contents, so it stays
        # valid however the grid is edited afterwards.
        + chain_css((len(state.days) * len(state.meal_types)) // 2)
        + "\n"
        + card_hover_css()
    )

    # ---- recipe detail (read-only) ---------------------------------------
    # One dialog reused for every card: its body is refreshable and reads
    # state.focus, so opening a card is a refresh, not 28 pre-built dialogs.

    @ui.refreshable
    def recipe_detail() -> None:
        view = state.focus
        if view is None or view.recipe is None:
            ui.label("Nothing to show.").classes("text-slate-400")
            return

        with ui.element("div").classes("flex flex-row items-center gap-1.5"):
            ui.icon("restaurant").classes("text-base text-emerald-300")
            ui.label(view.title).classes("text-lg font-semibold")
        meta = " · ".join(
            part
            for part in [
                view.meal_type.title(),
                view.style,
                view.cuisine,
                f"{view.prep_minutes} min prep" if view.prep_minutes is not None else "",
                f"{view.portions} portions",
            ]
            if part
        )
        ui.label(meta).classes("text-xs text-slate-400")

        if view.macros:
            with ui.element("div").classes("flex flex-row gap-4 mt-2"):
                for key, short, unit in MACRO_LABELS:
                    with ui.element("div").classes("flex flex-col"):
                        ui.label(f"{view.macros[key]:.0f}{unit}").classes("text-sm font-mono")
                        ui.label(short).classes("text-[10px] text-slate-500 uppercase")
            ui.label("per serving").classes("text-[10px] text-slate-500")

        ui.separator().classes("my-2")
        ui.label(f"Ingredients — all {view.portions} portions").classes(
            "text-xs uppercase tracking-wide text-slate-400"
        )
        with ui.element("div").classes("flex flex-col gap-0.5 mt-1"):
            for ingredient in view.recipe.ingredients:
                ui.label(
                    f"{ingredient.name} — "
                    f"{format_quantity(ingredient.name, ingredient.quantity_g)} "
                    f"(NOVA {ingredient.nova_group})"
                ).classes("text-xs text-slate-300")

        ui.label("Method").classes("text-xs uppercase tracking-wide text-slate-400 mt-3")
        with ui.element("div").classes("flex flex-col gap-1 mt-1"):
            for number, step in enumerate(view.recipe.instructions, start=1):
                ui.label(f"{number}. {step}").classes("text-xs text-slate-300")

        if view.recipe.prep_notes:
            ui.label(view.recipe.prep_notes).classes(
                "text-xs text-amber-300 mt-3 p-2 rounded bg-amber-400/10"
            )

    with ui.dialog() as detail_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-[36rem] max-w-full max-h-[80vh] overflow-y-auto"
        ):
            recipe_detail()

    def open_detail(view: SlotView) -> None:
        if view.recipe is None:
            return
        state.focus = view
        recipe_detail.refresh()
        detail_dialog.open()

    # ---- favorites: mark, and swap-in modal -------------------------------
    # `favorites_list` (drawer) is defined later, inside the left drawer
    # section, but every handler here only *runs* on a click long after the
    # whole page has finished building — same forward-reference pattern
    # `on_link_next_lunch` already uses for `refresh_all` below.

    def catalog_entry_for(recipe: dict) -> Optional[dict]:
        key = recipe_content_key(recipe)
        return next(
            (r for r in state.recipe_catalog if r.get("content_key") == key), None
        )

    def is_favorited(recipe: dict) -> bool:
        entry = catalog_entry_for(recipe)
        return bool(entry and entry.get("is_favorite"))

    def favorited_catalog() -> List[dict]:
        return [r for r in state.recipe_catalog if r.get("is_favorite")]

    async def toggle_favorite(recipe: dict) -> None:
        new_state = await REPOSITORY.toggle_favorite(recipe)
        state.recipe_catalog = await REPOSITORY.load_recipe_catalog()
        favorites_list.refresh()
        canvas.refresh()
        ui.notify(
            "Saved to favorites" if new_state else "Removed from favorites",
            type="positive" if new_state else "info",
        )

    def swap_filter_matches(favorite: dict, meal_type: Optional[str], query: str) -> bool:
        recipe = favorite["recipe"]
        if meal_type and recipe.get("meal_type") != meal_type:
            return False
        if query:
            haystack = f"{recipe.get('name', '')} {recipe.get('cuisine', '')}".lower()
            if query.lower() not in haystack:
                return False
        return True

    def select_swap_favorite(favorite_id: str) -> None:
        state.swap_selected_id = favorite_id
        swap_matches.refresh()

    def confirm_swap() -> None:
        if state.swap_target is None or state.swap_selected_id is None:
            return
        favorite = next(
            (f for f in favorited_catalog() if f["id"] == state.swap_selected_id), None
        )
        if favorite is None:
            return
        error = state.swap_slot_with_favorite(state.swap_target.id, favorite["recipe"])
        if error:
            ui.notify(error, type="warning")
            return
        swap_dialog.close()
        refresh_all()
        ui.notify(f"Swapped in \"{favorite['recipe']['name']}\"", type="positive")

    @ui.refreshable
    def swap_matches() -> None:
        """The results list + budget comparison. Refreshed on every filter/query/selection
        change — kept separate from `swap_dialog_body` so those refreshes never touch the
        search `ui.input` itself. Rebuilding an input on every keystroke (the previous
        shape of this dialog) destroys and recreates the DOM node each time, which steals
        focus after one character — see the `day_target_row` note in CLAUDE.md for the same
        trap elsewhere in this file.
        """
        view = state.swap_target
        if view is None:
            return

        budget = slot_target_budget(state, view)
        meal_filter = None if state.swap_filter in (None, "All meal types") else state.swap_filter
        favorites = favorited_catalog()
        matches = [
            f for f in favorites if swap_filter_matches(f, meal_filter, state.swap_query)
        ]
        selected = next((f for f in favorites if f["id"] == state.swap_selected_id), None)

        if not matches:
            ui.label(
                "No favorites match — clear the filter or import one."
            ).classes("text-xs text-slate-500 italic")

        with ui.element("div").classes("flex flex-col gap-1 max-h-64 overflow-y-auto"):
            for favorite in matches:
                recipe = favorite["recipe"]
                macros = Recipe.model_validate(recipe).per_serving_macros
                is_selected = favorite["id"] == state.swap_selected_id
                with ui.element("div").classes(
                    "flex flex-row items-center justify-between gap-2 p-1.5 rounded "
                    "cursor-pointer border "
                    + (
                        "bg-emerald-400/15 border-emerald-400/40"
                        if is_selected
                        else "border-slate-800 hover:border-slate-600"
                    )
                ).on("click", lambda f=favorite: select_swap_favorite(f["id"])):
                    with ui.element("div").classes("flex flex-col min-w-0"):
                        ui.label(recipe["name"]).classes(
                            "text-xs font-semibold truncate"
                        )
                        ui.label(recipe.get("meal_type", "").title()).classes(
                            "text-[10px] text-slate-500"
                        )
                    ui.label(f"{macros['calories']:.0f} kcal").classes(
                        "text-[10px] font-mono text-slate-300 shrink-0"
                    )

        ui.separator()
        with ui.element("div").classes("flex flex-row gap-4"):
            with ui.element("div").classes("flex flex-col gap-0.5 flex-1"):
                ui.label("Target slot budget").classes(
                    "text-[10px] uppercase tracking-wide text-slate-500"
                )
                if budget:
                    for key, short, unit in MACRO_LABELS:
                        ui.label(f"{short}: {budget[key]:.0f}{unit}").classes(
                            "text-xs text-slate-300"
                        )
                else:
                    ui.label("—").classes("text-xs text-slate-500")
            with ui.element("div").classes("flex flex-col gap-0.5 flex-1"):
                ui.label("Selected favorite (per serving)").classes(
                    "text-[10px] uppercase tracking-wide text-slate-500"
                )
                if selected:
                    macros = Recipe.model_validate(selected["recipe"]).per_serving_macros
                    for key, short, unit in MACRO_LABELS:
                        ui.label(f"{short}: {macros[key]:.0f}{unit}").classes(
                            "text-xs text-emerald-200"
                        )
                else:
                    ui.label("Pick a favorite above").classes(
                        "text-xs text-slate-500 italic"
                    )

    @ui.refreshable
    def swap_dialog_body() -> None:
        view = state.swap_target
        if view is None:
            return

        with ui.element("div").classes("flex flex-col gap-2"):
            ui.label(f"Swap {slot_label(view.id)}").classes("text-sm font-semibold")

            def on_filter_change(event) -> None:
                state.swap_filter = event.value
                swap_matches.refresh()

            def on_query_change(event) -> None:
                state.swap_query = event.value or ""
                swap_matches.refresh()

            with ui.row().classes("w-full items-center flex-nowrap gap-2"):
                ui.select(
                    ["All meal types"] + state.meal_types,
                    value=state.swap_filter or "All meal types",
                    on_change=on_filter_change,
                ).props("dense outlined").classes("flex-1 text-xs")
                ui.input(
                    placeholder="Search favorites…",
                    value=state.swap_query,
                    on_change=on_query_change,
                ).props("dense outlined clearable").classes("flex-1 text-xs")

            swap_matches()

            with ui.row().classes("justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=swap_dialog.close).props(
                    "dense flat no-caps"
                )
                ui.button(
                    "Confirm swap", icon="swap_horiz", on_click=confirm_swap
                ).props("dense no-caps").bind_enabled_from(
                    state, "swap_selected_id", backward=bool
                )

    with ui.dialog() as swap_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-[36rem] max-w-full max-h-[85vh] overflow-y-auto"
        ):
            swap_dialog_body()

    def open_swap_modal(view: SlotView) -> None:
        if view.recipe is None:
            return
        state.swap_target = view
        state.swap_filter = view.meal_type
        state.swap_query = ""
        state.swap_selected_id = None
        swap_dialog_body.refresh()
        swap_dialog.open()

    # ---- canvas: 7 day columns x 4 meal cards -----------------------------
    # Defined before the header and drawer, rendered after them: attaching a
    # `bind_value` fires an initial change event, so every handler's callees
    # must already exist by the time the sidebar is built.

    def on_link_next_lunch(view: SlotView) -> None:
        """Apply the Macro Action, then repaint whatever it moved.

        `refresh_all` is defined further down the page body; a closure resolves
        it when the click fires, long after the page has finished building.
        The telemetry header is in that repaint on purpose — the linked lunch
        now eats the dinner's macros, so its day's totals change too.
        """
        error = state.link_to_next_lunch(view.id)
        if error:
            ui.notify(error, type="warning")
            return
        refresh_all()
        ui.notify(
            f"{view.title} now feeds {slot_label(view.link_target)} — "
            f"cooking {portions_for(state.spec).get(view.id, 0)} portions",
            type="positive",
        )

    def meal_card(view: Optional[SlotView], meal_type: str) -> None:
        if view is None:
            view = SlotView(day="", meal_type=meal_type, status=STATUS_SKIP, title="—")
        look = STATUS_STYLES[view.status]
        clickable = "cursor-pointer" if view.recipe else ""
        chain = f"chain chain-{view.chain}" if view.chain is not None else ""

        with ui.element("div").classes(
            f"meal-card card-{view.status} rounded p-2 flex flex-col gap-1 min-w-0 "
            f"transition-shadow duration-150 {look['card']} {chain}"
        ):
            # Header row is a sibling of the clickable body below, not a child
            # of it — same reasoning as the "Link to next lunch" button: a
            # click on the favorite/swap buttons would otherwise bubble up
            # through `body`'s click handler and open the detail dialog too.
            with ui.element("div").classes("flex flex-row items-center justify-between gap-1"):
                ui.label(meal_type[:5].upper()).classes(
                    "text-[9px] font-semibold tracking-widest text-slate-500"
                )
                with ui.element("div").classes("flex flex-row items-center gap-0.5"):
                    if view.recipe is not None:
                        if view.mode == MODE_COOK:
                            recipe_dict = view.recipe.model_dump()
                            favorited = is_favorited(recipe_dict)
                            fav_button = ui.button(
                                icon="bookmark" if favorited else "bookmark_border",
                                on_click=lambda r=recipe_dict: toggle_favorite(r),
                            )
                            fav_button.props("dense flat round size=xs").classes(
                                "min-h-0 p-0.5 "
                                + (
                                    "text-amber-300"
                                    if favorited
                                    else "text-slate-500 hover:text-amber-300"
                                )
                            )
                            with fav_button:
                                ui.tooltip(
                                    "Remove from favorites" if favorited else "Save to favorites"
                                )
                        swap_button = ui.button(
                            icon="swap_horiz",
                            on_click=lambda v=view: open_swap_modal(v),
                        )
                        swap_button.props("dense flat round size=xs").classes(
                            "min-h-0 p-0.5 text-slate-500 hover:text-sky-300"
                        )
                        with swap_button:
                            ui.tooltip("Swap with a favorite")
                    if view.mode == MODE_COOK and state.week_plan is not None:
                        # Offered even without a recipe (STATUS_MISSING, a
                        # failed day) — a single-meal retry, not the whole
                        # day `regenerate_day` would redo.
                        meal_regen_button = ui.button(icon="refresh")
                        meal_regen_button.props("dense flat round size=xs").classes(
                            "min-h-0 p-0.5 text-slate-500 hover:text-emerald-300"
                        )
                        meal_regen_button.on_click(
                            lambda v=view, btn=meal_regen_button: regenerate_meal(v, btn)
                        )
                        with meal_regen_button:
                            ui.tooltip("Regenerate this meal — re-cooks just it")
                    with ui.element("div").classes(
                        "flex items-center gap-0.5 px-1.5 py-[1px] rounded-full "
                        f"{look['badge']}"
                    ):
                        ui.icon(look["icon"]).classes("text-[10px]")
                        ui.label(look["label"]).classes(
                            "text-[8px] font-semibold tracking-wide"
                        )

            # The recipe dialog opens from this inner block rather than the
            # card, so the action buttons above are siblings of it and a click
            # on them can't also open the dialog on its way up.
            body = ui.element("div").classes(f"flex flex-col gap-1 min-w-0 {clickable}")
            if view.recipe:
                body.on("click", lambda v=view: open_detail(v))

            with body:
                # Bold, larger than the rest of the card — the one thing a
                # scan down a column of 28 cards actually needs to read.
                # Titles past ui_settings.title_tooltip_chars can't fit the
                # two clamped lines at this column width, so they get a
                # tooltip with the full name instead of just being cut off
                # silently.
                title_label = ui.label(view.title).classes(
                    "text-[12px] leading-tight font-bold text-slate-100 line-clamp-2"
                )
                title_tooltip_chars = state.config["ui_settings"]["title_tooltip_chars"]
                if len(view.title) > title_tooltip_chars:
                    with title_label:
                        ui.tooltip(view.title)

                tags = " · ".join(part for part in [view.style, view.cuisine] if part)
                if tags:
                    ui.label(tags).classes("text-[9px] text-slate-400 truncate")

                if view.mode == MODE_LEFTOVER and view.source_label:
                    link_line("↩ from", view.source_label, view.chain_colour)
                if view.feeds:
                    link_line("→ feeds", " · ".join(view.feeds), view.chain_colour)

                if view.prep_badge:
                    badge_look = PREP_BADGE_STYLES[view.prep_badge]
                    prep_badge_el = ui.element("div").classes(
                        "flex items-center gap-1 px-1.5 py-[1px] rounded-full w-fit mt-0.5 "
                        f"{badge_look['classes']}"
                    )
                    with prep_badge_el:
                        ui.label(badge_look["label"]).classes(
                            "text-[8px] font-semibold tracking-wide"
                        )
                        if view.prep_origin:
                            ui.tooltip(view.prep_origin)

                if view.macros:
                    # One pill, "450 kcal · 45g P · 30g C · 12g F" — a colour
                    # per macro (MACRO_TINTS) rather than per digit, so the
                    # numbers stay comparable down the column while the
                    # letters carry the identity.
                    with ui.element("div").classes(
                        "flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-1.5 py-0.5 "
                        "rounded-full bg-slate-950/40 w-fit max-w-full"
                    ):
                        ui.label(f"{view.macros['calories']:.0f} kcal").classes(
                            "text-[9px] font-mono text-slate-300"
                        )
                        for key, short, unit in MACRO_LABELS[1:]:
                            ui.label("·").classes("text-[9px] text-slate-600")
                            ui.label(f"{view.macros[key]:.0f}{unit} {short}").classes(
                                f"text-[9px] font-mono {MACRO_TINTS[key]}"
                            )

                if view.mode == MODE_COOK and view.portions:
                    ui.label(
                        f"{view.portions} portions · {view.prep_minutes} min"
                        if view.prep_minutes is not None
                        else f"{view.portions} portions"
                    ).classes("text-[9px] text-emerald-300/70 truncate")

                if view.mode == MODE_LEFTOVER and view.prep_badge and view.prep_minutes is not None:
                    ui.label(f"{view.prep_minutes} min reheat/assemble").classes(
                        "text-[9px] text-amber-300/70 truncate"
                    )

            if view.mode == MODE_COOK and view.meal_type == LINK_SOURCE_MEAL:
                # Left enabled even when it can't be applied: a disabled Quasar
                # button swallows hover, so the tooltip explaining *why* would
                # never appear. Clicking says the same thing in a notification.
                # Styled as a real (if tiny) primary action — a filled pill
                # rather than flat text — so the one edit this UI offers reads
                # as an action, not a caption.
                button = ui.button(
                    LINK_ACTION_LABEL,
                    icon="subdirectory_arrow_right",
                    on_click=lambda v=view: on_link_next_lunch(v),
                )
                button.props("unelevated dense no-caps size=sm").classes(
                    "self-start min-h-0 px-1.5 py-0.5 rounded-full text-[9px] "
                    "transition-all duration-150 "
                    + (
                        "bg-slate-800/60 text-slate-600"
                        if view.link_error
                        else "bg-sky-400/15 text-sky-200 hover:bg-sky-400/25 hover:scale-105"
                    )
                )
                with button:
                    ui.tooltip(
                        view.link_error
                        or f"{slot_label(view.link_target)} eats this instead of "
                        "cooking — the batch grows to match."
                    )

    # ---- prep day: Sunday batch-prep column --------------------------------
    # An eighth grid column, left of day 0, for `week_plan.sunday_prep_session`
    # — raw prep work aggregated across the week's cook events (see
    # `planner.generate_sunday_prep_session`), done ahead of the week rather
    # than repeated per cook day. It is prep work, not an eating slot, so it
    # gets its own indigo accent (`PREP_COLUMN_ACCENT`) rather than any
    # `STATUS_STYLES` treatment, and sits outside `state.days` entirely —
    # there is no slot_id, regen button, or macro target for it.

    def prep_day_column() -> None:
        session = state.week_plan.sunday_prep_session if state.week_plan else None
        with ui.element("div").classes("flex flex-col gap-2 min-w-0"):
            with ui.element("div").classes(
                "px-1 py-0.5 border-b border-indigo-400/40 flex flex-row "
                "justify-between items-baseline"
            ):
                ui.label("PREP DAY").classes(
                    "text-xs font-semibold text-indigo-300 tracking-wide"
                )
                ui.icon("checklist").classes("text-[11px] text-indigo-400")
            if session is None:
                with ui.element("div").classes(
                    f"rounded-md p-2 {PREP_COLUMN_ACCENT} border-dashed"
                ):
                    ui.label("Not generated").classes("text-[10px] text-slate-500")
                    ui.label(
                        "Enable enable_sunday_prep and regenerate the week for a "
                        "batch-prep timeline here."
                    ).classes("text-[9px] text-slate-600 mt-1")
                return
            # What this session is for, before how — a shopper glancing at the
            # column should see which dishes it batches without opening any
            # of the phase timeline below.
            if session.meals_included:
                with ui.element("div").classes(f"rounded-md p-2 {PREP_COLUMN_ACCENT}"):
                    ui.label("Batching for").classes(
                        "text-[9px] uppercase tracking-wide text-indigo-400 mb-1"
                    )
                    for meal in session.meals_included:
                        ui.label(f"• {meal}").classes(
                            "text-[10px] text-indigo-200 leading-tight"
                        )
            for phase in session.timeline:
                with ui.expansion(
                    phase.name,
                    caption=f"{phase.active_minutes} active / {phase.passive_minutes} passive min",
                ).classes(f"rounded-md {PREP_COLUMN_ACCENT} text-[11px] w-full").props(
                    "dense header-class='text-indigo-200 text-[11px] font-medium'"
                ):
                    if phase.description:
                        ui.label(phase.description).classes(
                            "text-[10px] text-slate-400 mb-1"
                        )
                    ui.checkbox(f"Done: {phase.name}").props(
                        "dense size=xs color=indigo"
                    ).classes("text-[10px] text-indigo-200")

    @ui.refreshable
    def canvas() -> None:
        views = state.slot_views()
        with ui.element("div").classes("meal-canvas grid grid-cols-8 gap-2 w-full items-start"):
            prep_day_column()
            for day in state.days:
                with ui.element("div").classes("flex flex-col gap-2 min-w-0"):
                    with ui.element("div").classes(
                        "px-1 py-0.5 border-b border-slate-800 flex flex-row "
                        "justify-between items-baseline"
                    ):
                        with ui.element("div").classes("flex flex-row items-center gap-1"):
                            ui.label(day).classes("text-xs font-semibold text-slate-200")
                            # Only offered once a week exists and this day has
                            # something to cook — regenerating a leftover/skip-only
                            # day would be a no-op API call for nothing.
                            if state.week_plan is not None and state.spec.cook_slots_on(day):
                                regen_button = ui.button(icon="refresh")
                                regen_button.props("dense flat round size=xs").classes(
                                    "min-h-0 p-0.5 text-slate-500 hover:text-emerald-300"
                                )
                                regen_button.on_click(
                                    lambda day=day, btn=regen_button: regenerate_day(day, btn)
                                )
                                with regen_button:
                                    ui.tooltip(f"Regenerate {day} — re-cooks just this day")
                        ui.label(str(state.days.index(day) + 1)).classes(
                            "text-[9px] font-mono text-slate-600"
                        )
                    for meal_type in state.meal_types:
                        meal_card(views.get(slot_id(day, meal_type)), meal_type)

    # ---- context pipeline: what fed a day's plan --------------------------
    # One dialog reused for every day, refreshable off state.pipeline_day —
    # same pattern as recipe_detail/state.focus above. The expanded ui.stepper
    # lives here rather than inline because a stepper's headers need more
    # width than a grid-cols-7 column has (telemetry already fights this
    # squeezing three macro numbers into the same column).

    @ui.refreshable
    def pipeline_detail() -> None:
        day = state.pipeline_day
        if day is None:
            return
        ui.label(f"{day} — context pipeline").classes(
            "text-sm font-semibold text-slate-200 mb-2"
        )
        with ui.stepper().props("header-nav flat").classes("bg-transparent w-full"):
            for key, label, icon, description, connected in PIPELINE_STAGES:
                value = pipeline_value(state, day, key)
                step = ui.step(label, icon=icon)
                if not connected:
                    step.props("disable")
                with step:
                    ui.label(description).classes("text-xs text-slate-400")
                    if connected:
                        ui.label(value if value is not None else "Nothing scheduled").classes(
                            "text-sm font-mono mt-1 "
                            + ("text-emerald-300" if value is not None else "text-slate-500")
                        )
                    else:
                        ui.label("Not connected").classes(
                            "text-[10px] uppercase tracking-wide text-slate-600 mt-1"
                        )

    with ui.dialog() as pipeline_dialog:
        with ui.element("div").classes("bg-slate-900 rounded-lg p-4 w-[32rem] max-w-full"):
            pipeline_detail()

    def open_pipeline(day: str) -> None:
        state.pipeline_day = day
        pipeline_detail.refresh()
        pipeline_dialog.open()

    # ---- header: context pipeline ------------------------------------------
    # Compact icon-chip row, one per pipeline stage, directly above the
    # telemetry it explains. A row of chips rather than an inline
    # ui.stepper — same width problem as above — connected by a thin
    # chevron line like a mini timeline. Clicking a day's row opens the full
    # stepper. Three of the four stages have no data source yet
    # (`connected=False` in PIPELINE_STAGES) and render dashed/muted;
    # "Adaptive Workout" is already live off the drawer's training schedule.

    @ui.refreshable
    def context_pipeline() -> None:
        with ui.element("div").classes("grid grid-cols-8 gap-2 w-full mb-1"):
            # Empty spacer, not a pipeline row — none of PIPELINE_STAGES applies
            # to the prep column, but the grid still needs a column 0 here to
            # stay aligned with telemetry() and canvas() below it.
            ui.element("div")
            for day in state.days:
                with ui.element("div").classes(
                    "flex flex-row items-center gap-0.5 cursor-pointer rounded "
                    "px-0.5 py-0.5 hover:bg-slate-800/60"
                ).on("click", lambda day=day: open_pipeline(day)):
                    for i, (key, label, icon, description, connected) in enumerate(
                        PIPELINE_STAGES
                    ):
                        value = pipeline_value(state, day, key)
                        if connected and value is not None:
                            look = "bg-emerald-400/20 text-emerald-300"
                            tip = f"{label}: {value}"
                        elif connected:
                            look = "bg-slate-800/60 text-slate-400 border border-slate-700"
                            tip = f"{label}: none scheduled"
                        else:
                            look = (
                                "bg-slate-800/60 text-slate-600 "
                                "border border-dashed border-slate-700"
                            )
                            tip = f"{label} — not connected yet"
                        with ui.icon(icon).classes(f"text-[13px] rounded-full p-1 {look}"):
                            ui.tooltip(tip)
                        if i < len(PIPELINE_STAGES) - 1:
                            ui.icon("chevron_right").classes("text-[10px] text-slate-700")

    # ---- header: week date banner -----------------------------------------
    # Purely cosmetic — nothing here reads back into state — but `state.days`
    # only ever carries weekday names (`week_days` rotates names, not dates),
    # so without this a five-week-old cached plan and this week's plan look
    # identical at a glance. `week_date_range` anchors on the plan's
    # `generated_at` so the banner reflects the week that was actually
    # generated, falling back to today for an un-generated preview.

    @ui.refreshable
    def week_banner() -> None:
        start, end = week_date_range(
            state.days, state.week_plan.generated_at if state.week_plan else None
        )
        fmt = "%b %-d, %Y"
        with ui.element("div").classes("flex flex-row items-center gap-2 mb-1"):
            with ui.element("div").classes(
                "flex flex-row items-center gap-1.5 px-2 py-1 rounded border "
                "border-slate-800 bg-slate-800/40 w-fit"
            ):
                ui.label("📅").classes("text-xs")
                ui.label(f"Week of {start.strftime(fmt)} – {end.strftime(fmt)}").classes(
                    "text-[11px] font-medium text-slate-300 tracking-wide"
                )
            # Unique plant-department ingredients (Produce, Herbs & Spices,
            # Nuts/Seeds & Spreads) across the week's cook events — see
            # `shopping.collect_unique_plants`. Absent until a week is
            # generated, same as every other week_plan-derived reading here.
            plant_count = len(state.week_plan.unique_plants) if state.week_plan else 0
            with ui.element("div").classes(
                "flex flex-row items-center gap-1.5 px-2 py-1 rounded border "
                "border-emerald-800/60 bg-emerald-900/20 w-fit"
            ):
                ui.label("🌱").classes("text-xs")
                ui.label(f"Plant Diversity: {plant_count}").classes(
                    "text-[11px] font-medium text-emerald-300 tracking-wide"
                )
                with ui.tooltip():
                    ui.label(
                        "Unique produce, herbs/spices, nuts/seeds & spreads across "
                        "this week's cooked recipes."
                    )

    # ---- header: macro telemetry -----------------------------------------
    # `prep_telemetry_cell` replaces the usual kcal/protein bars in the prep
    # column with labor telemetry instead — active/passive minutes, not
    # macros, since there's nothing eaten in this column to measure against a
    # target.

    def prep_telemetry_cell() -> None:
        session = state.week_plan.sunday_prep_session if state.week_plan else None
        max_active = state.config["max_prep_active_mins"]
        with ui.element("div").classes("flex flex-col gap-1 min-w-0"):
            with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                ui.label("PREP").classes(
                    "text-[11px] font-semibold tracking-wider text-indigo-300"
                )
            if session is None:
                ui.label("Not generated").classes("text-[10px] font-mono text-slate-500")
            else:
                ui.label(
                    f"Active Prep: {session.total_active_minutes} / {max_active} mins"
                ).classes("text-[10px] font-mono text-indigo-200")
                ui.label(
                    f"Passive Time: {session.total_passive_minutes} mins"
                ).classes("text-[10px] font-mono text-indigo-200/70")

    @ui.refreshable
    def telemetry() -> None:
        bar_scale_limit = state.config["ui_settings"]["bar_scale_limit"]
        with ui.element("div").classes("grid grid-cols-8 gap-2 w-full"):
            prep_telemetry_cell()
            for day in state.days:
                target = state.targets_for(day)
                totals = state.totals_for(day)
                kcal, kcal_goal = totals["calories"], float(target["calories"])
                protein, protein_goal = totals["protein_g"], float(target["protein_g"])
                overridden = day in state.target_overrides
                training = state.has_training(day)
                with ui.element("div").classes("flex flex-col gap-1 min-w-0"):
                    with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                        # A dot is why the denominator moved: amber for a drawer
                        # target override, emerald for a scheduled workout —
                        # either way this day is being measured against a live
                        # preview, not config.json or the numbers the week was
                        # actually generated for.
                        marker = "•" if overridden else ("⚡" if training else "")
                        ui.label(day[:3].upper() + marker).classes(
                            "text-[11px] font-semibold tracking-wider "
                            + (
                                "text-amber-300"
                                if overridden
                                else "text-emerald-300" if training else "text-slate-300"
                            )
                        )
                        ui.label(f"{kcal:.0f}/{kcal_goal:.0f} kcal").classes(
                            "text-[10px] font-mono text-slate-400"
                        )
                    # Calories: the primary bar, dual-segmented — fill colour
                    # bands on how close the day landed (macro_band), and a
                    # thin marker at the target itself so an overshoot reads as
                    # "past the line" rather than just "a long green bar".
                    telemetry_bar(kcal, kcal_goal, height="9px", bar_scale_limit=bar_scale_limit)
                    with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                        ui.label("protein").classes(
                            "text-[9px] uppercase tracking-wide text-slate-500"
                        )
                        ui.label(f"{protein:.0f}/{protein_goal:.0f}g").classes(
                            f"text-[9px] font-mono {MACRO_TINTS['protein_g']}"
                        )
                    telemetry_bar(protein, protein_goal, height="5px", bar_scale_limit=bar_scale_limit)
                    with ui.element("div").classes("flex flex-row gap-2 mt-0.5"):
                        for key, short, unit in MACRO_LABELS[2:]:
                            ui.label(
                                f"{short} {totals[key]:.0f}/{float(target[key]):.0f}{unit}"
                            ).classes(f"text-[9px] font-mono {MACRO_TINTS[key]}")
                    with ui.tooltip():
                        for key, short, unit in MACRO_LABELS:
                            delta = totals[key] - float(target[key])
                            ui.label(
                                f"{short}: {totals[key]:.0f}{unit} "
                                f"({delta:+.0f} vs {float(target[key]):.0f})"
                            )
                        if overridden:
                            ui.label("target overridden — applies on next generation")
                        if training:
                            ui.label("training day — burn folded into target, applies on next generation")

    # ---- shopping list: right-hand slide-over ----------------------------
    # A drawer rather than a dialog because this list is read *against* the
    # grid — "what is Wednesday's trip for" is answered by looking at both at
    # once — and a modal would cover the week it describes.
    #
    # Everything shown here is derived from the plan on each repaint; nothing
    # is stored. The ticks are the exception, and they are deliberately not
    # persisted: this is a scratch list for one trip, not another piece of
    # state that could disagree with week_plan.json.

    def copy_for_keep(text: str, label: str) -> None:
        """Put `text` on the system clipboard, formatted for a Keep list.

        `json.dumps` here is escaping a JavaScript string literal, not touching
        storage — an ingredient name with an apostrophe in it ("Bird's eye
        chilli", seen on a real run) would otherwise end the literal early and
        break the whole handler. The `execCommand` branch is for the
        non-localhost case: `navigator.clipboard` is unavailable outside a
        secure context, and this server is often reached over plain HTTP on a
        LAN address.
        """
        ui.run_javascript(
            f"""
            const text = {json.dumps(text)};
            if (navigator.clipboard && window.isSecureContext) {{
                navigator.clipboard.writeText(text);
            }} else {{
                const area = document.createElement('textarea');
                area.value = text;
                area.style.position = 'fixed';
                area.style.opacity = '0';
                document.body.appendChild(area);
                area.select();
                document.execCommand('copy');
                area.remove();
            }}
            """
        )
        ui.notify(f"{label} copied — paste into a Google Keep list", type="positive")

    @ui.refreshable
    def shopping_panel() -> None:
        plan = state.week_plan
        if plan is None:
            ui.label("No shopping list yet").classes("text-sm text-slate-300")
            ui.label(
                "A list is built from generated recipes, so there is nothing to buy "
                "until the week has been generated."
            ).classes("text-xs text-slate-500")
            return

        # Daily mode reuses the same partitioning function with every day
        # treated as a shop day — the cook events and quantities in each
        # window are unaffected, only where the boundaries fall.
        window_days = state.days if state.daily_shop_mode else state.shop_days
        windows = shopping_windows(state.days, window_days)
        if not windows:
            ui.label("No shopping days set — pick some in the drawer.").classes(
                "text-xs text-slate-400"
            )
            return

        for window in windows:
            # By cook day, never eating day: a Sunday batch eaten on Wednesday
            # is bought entirely on the Sunday trip, so its ingredients are
            # never split across two lists.
            events = plan.events_on_days(window.days)
            shopping_list = aggregate_cook_events(events, window.days) if events else None

            with ui.element("div").classes(
                "flex flex-col gap-2 p-2 rounded border border-slate-800 bg-slate-950/40"
            ):
                with ui.element("div").classes("flex flex-row items-center justify-between gap-2"):
                    with ui.element("div").classes("flex flex-col min-w-0"):
                        ui.label(window.label).classes("text-xs font-semibold text-slate-100")
                        ui.label(
                            f"{len(events)} cook session(s)"
                            + (f" · {len(shopping_list.items())} items" if shopping_list else "")
                        ).classes("text-[10px] text-slate-500")
                    if shopping_list:
                        ui.button(
                            "Copy for Keep",
                            icon="content_copy",
                            on_click=lambda sl=shopping_list, w=window: copy_for_keep(
                                format_shopping_list_keep(sl), w.label
                            ),
                        ).props("dense flat no-caps size=sm").classes("shrink-0 text-sky-300")

                if not events:
                    ui.label("Nothing cooked in this window.").classes(
                        "text-[11px] text-slate-500 italic"
                    )
                    continue

                # A failed meal contributes no recipe and therefore no
                # ingredients, so say so here: a short list is otherwise
                # indistinguishable from a cheap week. `plan.failures` is
                # keyed by slot_id (day:meal_type), not day, since one bad
                # meal-type call can fail some of a window's days without
                # failing all of them.
                failed = [
                    slot_label(key)
                    for key in plan.failures
                    if parse_slot_id(key)[0] in window.days
                ]
                if failed:
                    ui.label(
                        f"{', '.join(failed)} failed to generate — nothing for "
                        "those meals is on this list."
                    ).classes("text-[10px] text-rose-300 p-1 rounded bg-rose-500/10")

                # Quoted because NiceGUI's props parser drops an unquoted value
                # containing brackets — `header-class=text-[11px]` silently
                # never reaches Quasar at all.
                with ui.expansion("What this trip is for").props(
                    "dense header-class='text-[11px] text-slate-400 px-0'"
                ).classes("w-full"):
                    ui.label(
                        "Quantities below already include every portion."
                    ).classes("text-[10px] text-slate-500")
                    for line in cook_plan_lines(events):
                        ui.label(line).classes("text-[10px] text-slate-400")

                for department in sorted(shopping_list.categories):
                    ui.label(department).classes(
                        "text-[10px] uppercase tracking-widest text-slate-500 mt-1"
                    )
                    for item in shopping_list.categories[department]:
                        text = f"{item.name} — {format_quantity(item.name, item.total_amount_g)}"
                        # buy_late is a perishable this window doesn't cook for
                        # days yet. Annotated, never moved to another trip —
                        # whether to make a second run is the shopper's call.
                        if item.buy_late:
                            text += "  ← buy fresh closer to the day"
                        ui.checkbox(text).props("dense size=xs color=teal").classes(
                            "text-[11px] "
                            + ("text-amber-300" if item.buy_late else "text-slate-200")
                        )

    with ui.right_drawer(value=False, bordered=True).classes(
        "bg-slate-900 p-3 flex flex-col gap-3 overflow-y-auto"
    ).props(":width=420") as shopping_drawer:
        with ui.element("div").classes("flex flex-row items-center justify-between"):
            with ui.element("div").classes("flex flex-row items-center gap-1"):
                ui.icon("shopping_cart").classes("text-sm text-slate-500")
                ui.label("Shopping list").classes(
                    "text-xs uppercase tracking-widest text-slate-500"
                )
            ui.button(icon="close", on_click=lambda: shopping_drawer.hide()).props(
                "dense flat size=sm"
            ).classes("text-slate-400")

        def on_daily_shop_toggle(event) -> None:
            state.daily_shop_mode = event.value
            shopping_panel.refresh()

        with ui.element("div").classes("flex flex-row items-center justify-between"):
            ui.label("Shop days (batch trips)").classes("text-[11px] text-slate-400")
            ui.switch(value=state.daily_shop_mode, on_change=on_daily_shop_toggle).props(
                "dense size=sm color=teal"
            )
            ui.label("Daily shop").classes("text-[11px] text-slate-400")

        shopping_panel()

    with ui.header(bordered=True).classes("bg-slate-900 px-3 py-2 flex flex-col gap-2"):
        with ui.element("div").classes("flex flex-row items-baseline gap-3"):
            with ui.element("div").classes("flex flex-row items-center gap-1.5"):
                ui.icon("restaurant_menu").classes("text-sm text-slate-300")
                ui.label("AI Weekly Meal Planner").classes(
                    "text-sm font-semibold tracking-wide"
                )

            async def on_week_selection_change(event) -> None:
                target = event.value
                if target == state.week_selection:
                    return
                # `switch_week` only reads from disk — it never generates —
                # so this is instant regardless of which week it's loading.
                await state.switch_week(REPOSITORY, target)
                refresh_all()

            # No `bind_value` here on purpose: binding would let NiceGUI's
            # polling loop write `state.week_selection` the moment the user
            # picks an option, before `switch_week` has loaded that week's
            # plan — every other piece of state (`week_plan`, `edited`, the
            # spec) would then disagree with `week_selection` until the
            # `await` above finishes. `switch_week` is the only thing that's
            # allowed to set it, and only once the load it names has landed.
            ui.select(
                WEEK_SELECTION_LABELS,
                value=state.week_selection,
                on_change=on_week_selection_change,
            ).props("dense outlined size=sm").classes("text-slate-200 w-32")

            ui.label().classes("text-[11px] text-slate-400").bind_text_from(
                state,
                "week_plan",
                backward=lambda plan: (
                    f"generated {plan.generated_at[:16].replace('T', ' ')}"
                    if plan
                    else "no cached week — showing planned shape only"
                ),
            )
            # Linking is an in-memory reshuffle: nothing here writes
            # week_plan.json, so say so rather than letting the grid imply the
            # cached week on disk has changed.
            ui.label("edited — not saved").classes(
                "text-[10px] font-semibold px-1 rounded bg-amber-400/15 text-amber-300"
            ).bind_visibility_from(state, "edited")
            ui.space()
            ui.label().classes("text-[11px] text-slate-400").bind_text_from(
                state, "model", backward=lambda model: f"model: {model}"
            )

            def shopping_item_count(plan: Optional[WeekPlan]) -> str:
                if plan is None:
                    return "Shopping list"
                items = aggregate_cook_events(
                    plan.events_on_days(state.days), state.days
                ).items()
                if not items:
                    return "Shopping list"
                return f"Shopping list ({len(items)} items)"

            # One export path, not two: `window.print()` used to print
            # whatever the dashboard happened to look like (icons, drawers,
            # macro bars — none of it a recipe), which was a different,
            # worse document than "Download PDF Menu" right next to it. Now
            # this button *is* that download — `build_week_menu_pdf` reads
            # the same `state.week_plan` the grid shows, so it always
            # matches whatever edits (leftover links, regenerated days) are
            # on screen right now, and it's the one PDF the app produces —
            # print it from the browser's viewer or file it away as-is.
            def download_pdf_menu() -> None:
                if state.week_plan is None:
                    ui.notify("Generate a week first — there's nothing to export yet.", type="warning")
                    return
                ui.download(
                    build_week_menu_pdf(state.week_plan),
                    filename="weekly_menu.pdf",
                    media_type="application/pdf",
                )

            print_button = ui.button(icon="print", on_click=download_pdf_menu).props(
                "dense flat no-caps"
            ).classes("text-slate-300")
            with print_button:
                ui.tooltip("Download this week as a PDF — summary, every recipe, and the shopping list.")

            # Prominent and un-dense on purpose — this is the button that
            # gets used every single week, not an occasional control, so it
            # gets the same visual weight as "Generate" rather than blending
            # into the rest of the flat header icons.
            shopping_button = (
                ui.button(icon="shopping_cart", on_click=shopping_drawer.toggle)
                .props("no-caps unelevated color=teal")
                .classes("text-slate-900 font-semibold shadow-md shadow-teal-500/20")
            )
            shopping_button.bind_text_from(
                state, "week_plan", backward=shopping_item_count
            )
            with shopping_button:
                ui.tooltip(
                    "Every shopping trip in this week, grouped by department — "
                    "built from the grid as it stands, including any edits."
                )
        week_banner()
        context_pipeline()
        telemetry()

    # ---- left drawer: global controls ------------------------------------

    @ui.refreshable
    def week_summary() -> None:
        spec = state.spec
        cooks = spec.cook_slots()
        cook_days = {slot.day for slot in cooks}
        windows = shopping_windows(state.days, state.shop_days)
        total_portions = sum(portions_for(spec).values())

        for label, value in [
            ("Cook sessions", len(cooks)),
            ("Days with cooking", len(cook_days)),
            ("Portions total", total_portions),
            ("Shopping trips", len(windows)),
        ]:
            with ui.element("div").classes("flex flex-row justify-between text-xs"):
                ui.label(label).classes("text-slate-400")
                ui.label(str(value)).classes("font-mono text-slate-200")

        failures = state.week_plan.failures if state.week_plan else {}
        if failures:
            with ui.element("div").classes(
                "mt-2 p-2 rounded bg-rose-500/10 border border-rose-900"
            ):
                ui.label(f"{len(failures)} meal(s) failed to generate").classes(
                    "text-xs text-rose-300 font-semibold"
                )
                for key, error in failures.items():
                    ui.label(f"{slot_label(key)}: {error}").classes("text-[10px] text-rose-200/80")

    # ---- left drawer: per-day macro targets ------------------------------

    def day_target_row(day: str) -> None:
        """One day's editable calorie/protein/carb targets.

        The row is built once and then mutated in place — the derived-fat
        readout has to keep up with every keystroke, and repainting a section
        that owns the focused input would take the cursor out of the number
        being typed. Only `telemetry` is refreshed on an edit, because that is
        the only other thing on screen showing a target.
        """
        target = state.planned_targets(day)
        inputs: Dict[str, ui.number] = {}

        def sync() -> None:
            current = state.planned_targets(day)
            fat_label.text = f"fat {current['fat_g']:.0f}g"
            reset.set_visibility(day in state.target_overrides)
            telemetry.refresh()

        def on_edit(key: str, event) -> None:
            # An empty box is a half-typed number, not a target of zero.
            # Ignoring it leaves the day on its last real value instead of
            # briefly planning a 0 kcal Tuesday.
            if event.value is None or event.value == "":
                return
            state.set_target(day, key, float(event.value))
            sync()

        def on_reset() -> None:
            state.clear_targets(day)
            restored = state.planned_targets(day)
            for key, number in inputs.items():
                number.value = restored[key]
            sync()

        with ui.element("div").classes("flex flex-col gap-1"):
            with ui.element("div").classes("flex flex-row items-center justify-between gap-1"):
                ui.label(day).classes("text-[11px] font-semibold text-slate-200")
                with ui.element("div").classes("flex flex-row items-center gap-1"):
                    # Fat is shown, never typed: it is whatever energy is left
                    # once protein and carbs are paid for.
                    fat_label = ui.label(f"fat {target['fat_g']:.0f}g").classes(
                        "text-[10px] font-mono text-slate-500"
                    )
                    reset = (
                        ui.button(icon="undo", on_click=on_reset)
                        .props("dense flat size=xs")
                        .classes("min-h-0 p-0 text-amber-300")
                    )
                    reset.set_visibility(day in state.target_overrides)
                    with reset:
                        ui.tooltip(f"Reset {day} to config.json")
            with ui.row().classes("w-full items-center flex-nowrap gap-2"):
                for key, label in TARGET_FIELDS:
                    inputs[key] = (
                        ui.number(
                            label=label,
                            value=target[key],
                            min=0,
                            step=10,
                            precision=0,
                            on_change=lambda event, k=key: on_edit(k, event),
                        )
                        # Debounced so holding a key doesn't repaint the
                        # telemetry header once per digit.
                        .props("dense outlined debounce=350")
                        .classes("flex-1 text-xs")
                    )

    @ui.refreshable
    def targets_editor() -> None:
        """The whole week's targets, in the same order as the grid.

        Refreshable only so a change of week start reorders it; edits inside it
        never refresh it (see `day_target_row`).
        """
        for day in state.days:
            day_target_row(day)

        def reset_all() -> None:
            state.clear_targets()
            targets_editor.refresh()
            telemetry.refresh()

        with ui.element("div").classes("flex flex-row items-center justify-between mt-1"):
            ui.label().classes("text-[10px] text-amber-300").bind_text_from(
                state,
                "target_overrides",
                backward=lambda overrides: (
                    f"{len(overrides)} day(s) overridden" if overrides else ""
                ),
            )
            ui.button("Reset all", icon="undo", on_click=reset_all).props(
                "dense flat no-caps size=sm"
            ).classes("text-slate-400").bind_visibility_from(
                state, "target_overrides", backward=bool
            )

    # ---- left drawer: training & activity schedule ------------------------

    def training_field_handler(index: int, key: str):
        """One `on_change` callback per (row, field) — index and key baked in
        via closure arguments, not looked up from the row at call time, so a
        row removed or reordered between render and edit can't corrupt the
        wrong entry."""

        def handler(event) -> None:
            if event.value is None or event.value == "":
                return
            state.training_schedule[index][key] = event.value
            # A training edit changes the day's expanded target and, for the
            # pinned slot, its meal_override — both feed `planned_targets`, so
            # both live-preview surfaces have to repaint, same as a target
            # override edit.
            telemetry.refresh()
            targets_editor.refresh()

        return handler

    @ui.refreshable
    def training_editor() -> None:
        if not state.training_schedule:
            ui.label("No workouts scheduled.").classes(
                "text-[10px] text-slate-500 italic"
            )
        for index, session in enumerate(state.training_schedule):

            def on_remove(i: int = index) -> None:
                state.remove_training_session(i)
                training_editor.refresh()
                telemetry.refresh()
                targets_editor.refresh()

            with ui.element("div").classes(
                "flex flex-col gap-1 p-1.5 rounded border border-slate-800 bg-slate-950/30"
            ):
                with ui.element("div").classes("flex flex-row items-center gap-1"):
                    ui.select(
                        state.days,
                        value=session.get("day"),
                        on_change=training_field_handler(index, "day"),
                    ).props("dense outlined").classes("flex-1 min-w-0 text-xs")
                    ui.button(icon="delete", on_click=on_remove).props(
                        "dense flat size=xs"
                    ).classes("min-h-0 p-0 text-slate-500")
                with ui.row().classes("w-full items-center flex-nowrap gap-2"):
                    ui.input(
                        label="Time (HH:MM)",
                        value=session.get("time", ""),
                        on_change=training_field_handler(index, "time"),
                    ).props("dense outlined debounce=350").classes("flex-1 text-xs")
                    ui.select(
                        TRAINING_TYPE_LABELS,
                        value=session.get("type"),
                        on_change=training_field_handler(index, "type"),
                    ).props("dense outlined").classes("flex-1 text-xs")
                with ui.row().classes("w-full items-center flex-nowrap gap-2"):
                    ui.number(
                        label="Duration (min)",
                        value=session.get("duration_minutes", 0),
                        min=0,
                        step=5,
                        precision=0,
                        on_change=training_field_handler(index, "duration_minutes"),
                    ).props("dense outlined debounce=350").classes("flex-1 text-xs")
                    ui.number(
                        label="Burn (kcal)",
                        value=session.get("estimated_burn_kcal", 0),
                        min=0,
                        step=10,
                        precision=0,
                        on_change=training_field_handler(index, "estimated_burn_kcal"),
                    ).props("dense outlined debounce=350").classes("flex-1 text-xs")

        def on_add() -> None:
            state.add_training_session()
            training_editor.refresh()
            telemetry.refresh()
            targets_editor.refresh()

        ui.button("Add session", icon="add", on_click=on_add).props(
            "dense flat no-caps size=sm"
        ).classes("text-slate-400 mt-1")

    def refresh_all() -> None:
        week_banner.refresh()
        telemetry.refresh()
        canvas.refresh()
        week_summary.refresh()
        # The list is derived from the plan, so anything that changes the plan —
        # a generation, a reload, a leftover link that grows a batch — changes
        # what you have to buy.
        shopping_panel.refresh()
        targets_editor.refresh()
        training_editor.refresh()

    async def reload_from_disk() -> None:
        await state.reload_plan(REPOSITORY)
        refresh_all()
        label = WEEK_SELECTION_LABELS[state.week_selection]
        ui.notify(
            f"Reloaded {label}" if state.week_plan else f"No cached plan for {label}",
            type="positive" if state.week_plan else "warning",
        )

    # ---- generation -------------------------------------------------------
    # The run is long (30s-3min per cooking day) and its progress is the only
    # thing on screen worth looking at while it happens, so it gets a modal
    # rather than a toast. Built once per page: opening it is a state change,
    # not a construction, so the day-by-day updates below can just assign to
    # these elements from the progress callbacks.

    with ui.dialog().props("persistent") as progress_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-[32rem] max-w-full flex flex-col gap-2"
        ):
            with ui.element("div").classes("flex flex-row items-center gap-1.5"):
                ui.icon("bolt").classes("text-sm text-amber-300")
                ui.label("Generating week").classes("text-sm font-semibold")
            progress_status = ui.label("Starting…").classes("text-xs text-slate-300")
            progress_bar = ui.linear_progress(value=0.0, size="10px", show_value=False).props(
                "rounded color=primary"
            )
            ui.label(
                "One API call per cooking day, 30s–3 min each. This window stays "
                "until the whole week is done."
            ).classes("text-[10px] text-slate-500")
            # Portion trims and failed days both arrive as notes, mid-run. A log
            # keeps them all — a single status label would overwrite the trim
            # you wanted to read with the next day's heading.
            progress_log = ui.log(max_lines=200).classes(
                "w-full h-40 text-[10px] bg-slate-950 rounded"
            )

    def generation_spec() -> WeekSpec:
        """The week we're about to ask for: what's on screen, with live servings.

        `state.spec` deliberately ignores the drawer's people-per-meal once a
        week is generated (that plan's portions came from its own
        `servings_per_meal`), so the value is reapplied here — otherwise the
        control would silently do nothing on every run after the first.
        """
        return state.spec.model_copy(update={"servings_per_meal": max(1, int(state.servings or 1))})

    async def run_generation() -> None:
        if state.generating:
            return
        # Claimed before the first `await`, not just before the API calls:
        # every await below is a point where a second click gets its turn, and
        # the guard is worthless if it can be passed twice in between.
        state.generating = True
        try:
            await generate_week()
        finally:
            state.generating = False
            generate.props(remove="loading")
            progress_dialog.close()

    async def generate_week() -> None:
        # The drawer wins over the file throughout: the model, the per-day
        # target overrides and the pantry list are the ones the user can
        # actually see, and this run is what they were entered for.
        config = state.planning_config()
        key_error = api_key_error()
        if key_error:
            # Checked up front rather than left to fail per-day: it would fail
            # all seven identically, after a wait, with nothing to show for it.
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        spec = generation_spec()
        errors = validate_week(spec, config)
        if errors:
            ui.notify(
                "Can't generate — " + " ".join(errors[:3]),
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return

        history = await REPOSITORY.load_history()
        # Resolve auto styles/cuisines before the run so the recorded plan says
        # what it actually cooked, and so rotation continues across the week.
        spec = resolve_auto_choices(spec, config, history)

        stages = meal_type_order(config)
        cooking_days = len({slot.day for slot in spec.cook_slots()})
        done = 0

        def on_meal_type(meal_type: str, cooks: int) -> None:
            """Fired on the loop by generate_week_plan, once per meal type, before its call."""
            nonlocal done
            done += 1
            progress_bar.value = (done - 1) / len(stages)
            plural = meal_type + "es" if meal_type.endswith("ch") else meal_type + "s"
            label = humanize(plural).capitalize()
            progress_status.text = (
                f"Generating {label} ({done}/{len(stages)}) — {cooks} recipe(s)…"
                if cooks
                else f"{label} ({done}/{len(stages)}) — nothing to cook, all leftovers or skipped"
            )

        generate.props("loading")
        progress_status.text = f"Starting {cooking_days} cooking day(s) on {state.model}…"
        progress_bar.value = 0.0
        progress_log.clear()
        progress_dialog.open()

        try:
            week_plan = await generate_week_plan(
                spec,
                config,
                history,
                progress_callback=on_meal_type,
                note_callback=progress_log.push,
                repository=REPOSITORY,
            )
            progress_status.text = "Saving…"
            progress_bar.value = 1.0
            # Targets whichever week is selected in the header — generating
            # while "Next Week" is showing must not overwrite "current".
            await REPOSITORY.save_week_plan(week_plan.model_dump(), state.week_selection)
            await record_week_history(week_plan, REPOSITORY, config)
        except Exception as exc:
            # Per-day failures never reach here — generate_week_plan absorbs
            # those into WeekPlan.failures. This is the whole run coming apart
            # (no config, storage unwritable), so nothing is adopted and the
            # week on screen is left exactly as it was.
            ui.notify(
                f"Generation failed: {type(exc).__name__}: {exc}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return

        # Saved before adopted, so the grid can't show a week that isn't on
        # disk — `edited` clearing is a claim that they match.
        state.adopt_plan(week_plan)
        refresh_all()

        if week_plan.failures:
            ui.notify(
                f"{len(week_plan.failures)} meal(s) failed to generate — "
                "they show as NOT GENERATED. "
                + " · ".join(
                    f"{slot_label(key)}: {error}" for key, error in week_plan.failures.items()
                ),
                type="warning",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
        else:
            ui.notify(
                f"Generated {cooking_days} cooking day(s) and saved "
                f"{WEEK_SELECTION_LABELS[state.week_selection]}",
                type="positive",
            )

    async def regenerate_day(day: str, button) -> None:
        """Re-cook one day's meals in place, via the canvas header's refresh icon.

        Deliberately lighter than `generate_week`: no progress modal, just the
        clicked button's own `loading` prop, because one day is a single API
        call rather than up to seven. Mutual exclusion with a whole-week run
        (and with a second regenerate click) is `state.generating` /
        `state.regenerating_day` — checked but never surfaced with a toast,
        same as `run_generation`'s own re-entry guard.
        """
        if state.generating or state.regenerating_day:
            return
        spec = state.spec
        if state.week_plan is None or not spec.cook_slots_on(day):
            return

        config = state.planning_config()
        key_error = api_key_error()
        if key_error:
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        state.regenerating_day = day
        button.props("loading disable")
        try:
            history = await REPOSITORY.load_history()
            # Resolves only what's still `auto` on this day (e.g. after
            # "Shuffle styles") — every other day's already-concrete
            # style/cuisine is left exactly as it was.
            resolved_spec = resolve_auto_choices(spec, config, history)
            plan = await regenerate_single_day(
                day,
                resolved_spec,
                config,
                state.week_plan,
                history,
                repository=REPOSITORY,
            )
            await REPOSITORY.save_week_plan(plan.model_dump(), state.week_selection)
            await record_week_history(plan, REPOSITORY, config, days=[day])
        except Exception as exc:
            ui.notify(
                f"Regenerating {day} failed: {type(exc).__name__}: {exc}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            state.regenerating_day = None
            button.props(remove="loading disable")

        # Saved before adopted, same ordering as a full generation — the grid
        # can't show a day that isn't on disk.
        state.adopt_plan(plan)
        refresh_all()

        # regenerate_single_day writes one failures entry per cook slot on
        # `day` (see planner.py) — any of them present means the whole call
        # failed, since it's still one atomic API call for the day.
        day_failures = {key: error for key, error in plan.failures.items() if key.startswith(f"{day}:")}
        if day_failures:
            error = next(iter(day_failures.values()))
            ui.notify(
                f"{day} failed to regenerate — {error}",
                type="warning",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
        else:
            ui.notify(f"Regenerated {day}", type="positive")

    async def regenerate_meal(view: SlotView, button) -> None:
        """Re-cook one meal in place, via the small refresh icon on its card.

        Narrower still than `regenerate_day`: one API call for one slot
        rather than every cook on the day, via `planner.regenerate_single_meal`
        (siblings on the same day are treated as fixed and their macros
        subtracted from the day's budget). Unlike `regenerate_single_day`,
        that function doesn't catch its own exceptions into
        `WeekPlan.failures` — this is a single targeted retry, not a day walk
        — so the `try` here is what turns a raised exception into a toast
        instead of an unhandled error.
        """
        if state.generating or state.regenerating_day or state.regenerating_meal:
            return
        if view.mode != MODE_COOK or state.week_plan is None:
            return
        spec = state.spec
        day = view.day
        target_slot_id = view.id

        config = state.planning_config()
        key_error = api_key_error()
        if key_error:
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        state.regenerating_meal = target_slot_id
        button.props("loading disable")
        try:
            history = await REPOSITORY.load_history()
            # Resolves only what's still `auto` on this day, same reasoning
            # as `regenerate_day` — every other day's already-concrete
            # style/cuisine is left exactly as it was.
            resolved_spec = resolve_auto_choices(spec, config, history)
            plan = await regenerate_single_meal(
                target_slot_id,
                resolved_spec,
                config,
                state.week_plan,
                history,
                repository=REPOSITORY,
            )
            await REPOSITORY.save_week_plan(plan.model_dump(), state.week_selection)
            await record_week_history(plan, REPOSITORY, config, days=[day])
        except Exception as exc:
            ui.notify(
                f"Regenerating {slot_label(target_slot_id)} failed: "
                f"{type(exc).__name__}: {exc}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            state.regenerating_meal = None
            button.props(remove="loading disable")

        # Saved before adopted, same ordering as a full generation — the grid
        # can't show a meal that isn't on disk.
        state.adopt_plan(plan)
        refresh_all()
        ui.notify(f"Regenerated {slot_label(target_slot_id)}", type="positive")

    # ---- recipe catalog & import --------------------------------------------
    # `favorites_list` is referenced by handlers defined above this point
    # (`toggle_favorite`) and below it (drawer buttons) alike — every one of
    # them only runs on a later click, by which time this name already exists
    # in `planner_page`'s scope, same forward-reference pattern as
    # `refresh_all`.

    async def delete_catalog_entry(recipe_id: str) -> None:
        await REPOSITORY.delete_catalog_recipe(recipe_id)
        state.recipe_catalog = [r for r in state.recipe_catalog if r["id"] != recipe_id]
        favorites_list.refresh()
        canvas.refresh()
        ui.notify("Removed from catalog", type="positive")

    def open_edit_catalog_entry(entry: dict) -> None:
        state.edit_catalog_id = entry["id"]
        state.edit_catalog_name = entry["recipe"]["name"]
        edit_favorite_dialog.open()

    async def save_catalog_rename() -> None:
        entry = next(
            (r for r in state.recipe_catalog if r["id"] == state.edit_catalog_id), None
        )
        if entry is None:
            return
        new_name = (state.edit_catalog_name or "").strip()
        if not new_name:
            ui.notify("Name can't be empty.", type="warning")
            return
        record = await REPOSITORY.rename_catalog_recipe(entry["id"], new_name)
        if record:
            entry["recipe"] = record["recipe"]
        favorites_list.refresh()
        edit_favorite_dialog.close()
        ui.notify("Recipe renamed", type="positive")

    with ui.dialog() as edit_favorite_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-96 max-w-full flex flex-col gap-2"
        ):
            ui.label("Rename recipe").classes("text-sm font-semibold")
            ui.input(label="Name").bind_value(state, "edit_catalog_name").props(
                "dense outlined"
            ).classes("w-full text-xs")
            with ui.row().classes("justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=edit_favorite_dialog.close).props(
                    "dense flat no-caps"
                )
                ui.button("Save", on_click=save_catalog_rename).props("dense no-caps")

    async def on_import() -> None:
        text = (state.import_text or "").strip()
        if not text:
            ui.notify("Paste some recipe text first.", type="warning")
            return
        key_error = api_key_error()
        if key_error:
            ui.notify(key_error, type="negative", close_button=True, timeout=0)
            return

        import_button.props("loading")
        try:
            recipe = await import_external_recipe(
                text, config=state.planning_config(), repository=REPOSITORY
            )
        except Exception as exc:
            ui.notify(
                f"Import failed: {type(exc).__name__}: {exc}",
                type="negative",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
            return
        finally:
            import_button.props(remove="loading")

        favorite = state.import_as_favorite
        await REPOSITORY.import_recipe(recipe.model_dump(), favorite=favorite)
        state.recipe_catalog = await REPOSITORY.load_recipe_catalog()
        favorites_list.refresh()
        state.import_text = ""
        state.import_as_favorite = False
        import_dialog.close()
        ui.notify(
            f"Imported \"{recipe.name}\"" + (" and favorited it." if favorite else "."),
            type="positive",
        )

    with ui.dialog() as import_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-[32rem] max-w-full flex flex-col gap-2"
        ):
            ui.label("Import a recipe").classes("text-sm font-semibold")
            ui.label(
                "Paste raw recipe text, an ingredient list, or a URL — it's turned "
                "into grams, macros and NOVA groups under the same dietary rules "
                "generation uses."
            ).classes("text-[10px] text-slate-500")
            ui.textarea(placeholder="Paste recipe text or a URL…").bind_value(
                state, "import_text"
            ).props("dense outlined").classes("w-full text-xs").style(
                "min-height: 8rem"
            )
            ui.checkbox("Mark as favorite").bind_value(state, "import_as_favorite").classes(
                "text-xs"
            )
            with ui.row().classes("justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=import_dialog.close).props(
                    "dense flat no-caps"
                )
                import_button = ui.button(
                    "Analyze & Import", icon="auto_awesome", on_click=on_import
                ).props("dense no-caps")

    # `top_corner=True` is NiceGUI's own switch for this — it makes the left
    # drawer span past the header instead of sitting below it, which in turn
    # makes Quasar inset the fixed header by the drawer's width the same way
    # it already insets `.q-page-container`. Without it, the header spans the
    # full window regardless of the drawer, so telemetry's day columns (in
    # the header) and canvas's day columns (in the page container, inset by
    # the drawer) share the same grid-cols-8 math but render at different
    # x-offsets whenever the drawer is open — which it is by default at
    # desktop widths.
    with ui.left_drawer(bordered=True, top_corner=True).classes(
        "bg-slate-900 p-3 gap-3 flex flex-col h-screen overflow-y-auto w-full max-w-xs"
    ).props(":width=320"):
        # Pinned above the accordion (sticky, not just first-in-DOM) so the one
        # action that spends money and writes to disk is never a scroll away,
        # no matter how many sections below are expanded.
        with ui.element("div").classes(
            "sticky top-0 z-10 bg-slate-900 flex flex-col gap-2 pb-2"
        ):
            generate = (
                ui.button(icon="bolt", on_click=run_generation)
                .props("dense")
                .classes("w-full")
            )
            # Labelled after whichever week the header select has chosen, so
            # the button never reads "Generate week" while "Next Week" is on
            # screen and about to be the one overwritten.
            generate.bind_text_from(
                state,
                "week_selection",
                backward=lambda w: f"Generate {WEEK_SELECTION_LABELS[w]}",
            )
            with generate:
                ui.tooltip(
                    "Generates every meal set to cook in this grid — one API call per "
                    "cooking day. Overwrites the selected week's cached plan and "
                    "appends to history."
                )

            def on_shuffle_styles() -> None:
                state.shuffle_styles()
                refresh_all()
                ui.notify(
                    "Styles cleared — next Generate will re-roll every cook slot.",
                    type="positive",
                )

            with ui.button(
                "Shuffle styles", icon="casino", on_click=on_shuffle_styles
            ).props("dense flat").classes("w-full"):
                ui.tooltip(
                    "Once a week is generated, its slots keep the style/cuisine they "
                    "resolved to, so re-generating repeats them and only reworks the "
                    "dish. This blanks style/cuisine on every cook slot (leftover "
                    "links and skips are untouched) so the next Generate rotates them "
                    "fresh — nothing is written to disk until you generate."
                )
            ui.button(
                "Reload from disk", icon="refresh", on_click=reload_from_disk
            ).props("dense flat").classes("w-full")
            ui.separator()

        all_days = list(state.config["weekly_schedule"].keys())

        with ui.expansion("Global Controls", icon="settings", value=True).classes(
            "w-full"
        ).props("dense header-class='text-xs px-0'"):

            def on_week_start(event) -> None:
                # Set the field explicitly before refreshing: `bind_value`
                # keeps state in sync through the binding loop, which runs
                # *after* this handler, so a refresh relying on it alone
                # would repaint the old week order.
                state.week_start = event.value
                refresh_all()

            ui.select(
                all_days,
                label="Week starts on",
                on_change=on_week_start,
            ).bind_value(state, "week_start").props("dense outlined").classes(
                "w-full text-xs"
            )

            def on_servings(event) -> None:
                state.servings = int(event.value or 1)
                refresh_all()

            ui.number(
                label="People per meal",
                min=1,
                max=8,
                step=1,
                precision=0,
                on_change=on_servings,
            ).bind_value(state, "servings").props("dense outlined").classes(
                "w-full text-xs"
            )

            def on_shop_days(event) -> None:
                state.shop_days = list(event.value or [])
                week_summary.refresh()
                # Shop days *are* the window boundaries, so this repartitions
                # every list in the shopping drawer.
                shopping_panel.refresh()

            ui.select(
                all_days,
                label="Shopping days",
                multiple=True,
                on_change=on_shop_days,
            ).bind_value(state, "shop_days").props("dense outlined use-chips").classes(
                "w-full text-xs"
            )

            ui.select(
                state.models_config.get("selectable_options"),
                label="Model",
            ).bind_value(state, "model").props("dense outlined").classes("w-full text-xs")

        # Collapsed by default: seven days x three numbers is the densest thing
        # in the drawer, and most weeks run on the config file's targets.
        with ui.expansion("Daily Targets", icon="track_changes").classes(
            "w-full"
        ).props("dense header-class='text-xs px-0'"):
            ui.label(
                "Applies to the next generation only — config.json is not changed."
            ).classes("text-[10px] text-slate-500 mb-1")
            with ui.element("div").classes("flex flex-col gap-2"):
                targets_editor()

        with ui.expansion("Pantry Clear", icon="kitchen").classes(
            "w-full"
        ).props("dense header-class='text-xs px-0'"):

            def on_pantry(event) -> None:
                state.pantry = [
                    str(item).strip() for item in (event.value or []) if str(item).strip()
                ]

            # `new_value_mode="add-unique"` is what makes this a free-text
            # multi-item box rather than a picker: there is no fixed list of
            # things that can be in your fridge. Seeded from config so an
            # inventory set on disk shows up already entered.
            ui.select(
                list(state.pantry),
                value=list(state.pantry),
                label="Things to use up",
                multiple=True,
                new_value_mode="add-unique",
                on_change=on_pantry,
            ).props(
                "dense outlined use-chips use-input hide-dropdown-icon "
                'input-debounce=0 placeholder="600g chicken thighs — press enter"'
            ).classes("w-full text-xs")
            ui.label(
                "A priority, not a rule: the model prefers these where they fit and "
                "never bends a meal's style, cuisine or macro budget to use one up. "
                "They are still ordinary ingredients, so they still appear on the "
                "shopping list."
            ).classes("text-[10px] text-slate-500 mt-1")

        with ui.expansion("Training Schedule", icon="fitness_center").classes(
            "w-full"
        ).props("dense header-class='text-xs px-0'"):
            ui.label(
                "A workout's burn is added to that day's target, and the meal "
                "closest to it is pinned for glycogen replenishment — see "
                "Macro targets above and the meal brief once generated. Applies "
                "to the next generation only, same as targets and pantry."
            ).classes("text-[10px] text-slate-500 mb-1")
            with ui.element("div").classes("flex flex-col gap-1.5"):
                training_editor()

        with ui.expansion("Recipe Catalog", icon="favorite").classes(
            "w-full"
        ).props("dense header-class='text-xs px-0'"):

            def on_catalog_search(event) -> None:
                state.catalog_search = (event.value or "").strip()
                favorites_list.refresh()

            ui.input(
                placeholder="Search catalog…",
                on_change=on_catalog_search,
            ).props("dense outlined clearable").classes("w-full text-xs")

            @ui.refreshable
            def favorites_list() -> None:
                query = state.catalog_search.lower()
                matches = [
                    r
                    for r in state.recipe_catalog
                    if not query
                    or query in r["recipe"]["name"].lower()
                    or query in r["recipe"].get("meal_type", "").lower()
                ]
                if not state.recipe_catalog:
                    ui.label(
                        "Catalog is empty — bookmark a cooked meal or import one."
                    ).classes("text-[10px] text-slate-500 italic")
                elif not matches:
                    ui.label("No recipes match that search.").classes(
                        "text-[10px] text-slate-500 italic"
                    )
                with ui.element("div").classes("flex flex-col gap-1 max-h-56 overflow-y-auto"):
                    for entry in matches:
                        recipe = entry["recipe"]
                        favorited = bool(entry.get("is_favorite"))
                        with ui.element("div").classes(
                            "flex flex-row items-center justify-between gap-1 p-1 rounded "
                            "border border-slate-800 bg-slate-950/30"
                        ):
                            with ui.element("div").classes("flex flex-col min-w-0"):
                                ui.label(recipe["name"]).classes(
                                    "text-[11px] font-semibold truncate"
                                )
                                ui.label(recipe.get("meal_type", "").title()).classes(
                                    "text-[9px] text-slate-500"
                                )
                            with ui.element("div").classes(
                                "flex flex-row items-center gap-0.5 shrink-0"
                            ):
                                fav_toggle = ui.button(
                                    icon="bookmark" if favorited else "bookmark_border",
                                    on_click=lambda r=recipe: toggle_favorite(r),
                                ).props("dense flat round size=xs")
                                fav_toggle.classes(
                                    "min-h-0 p-0.5 "
                                    + (
                                        "text-amber-300"
                                        if favorited
                                        else "text-slate-500 hover:text-amber-300"
                                    )
                                )
                                ui.button(
                                    icon="edit",
                                    on_click=lambda e=entry: open_edit_catalog_entry(e),
                                ).props("dense flat round size=xs").classes(
                                    "min-h-0 p-0.5 text-slate-500 hover:text-sky-300"
                                )
                                ui.button(
                                    icon="delete",
                                    on_click=lambda rid=entry["id"]: delete_catalog_entry(rid),
                                ).props("dense flat round size=xs").classes(
                                    "min-h-0 p-0.5 text-slate-500 hover:text-rose-300"
                                )

            favorites_list()

            ui.separator().classes("my-1")
            ui.button(
                "Import recipe", icon="upload_file", on_click=import_dialog.open
            ).props("dense flat no-caps size=sm").classes("w-full text-slate-300")

        ui.separator()
        with ui.element("div").classes("flex flex-row items-center gap-1"):
            ui.icon("insights").classes("text-xs text-slate-500")
            ui.label("This week").classes(
                "text-xs uppercase tracking-widest text-slate-500"
            )
        week_summary()

    canvas()


if __name__ in {"__main__", "__mp_main__"}:
    # reload=False on purpose: once generation lands, an in-memory week plan
    # would be thrown away by every source-file save.
    ui.run(
        title="AI Weekly Meal Planner",
        # server.sh passes MEALS_UI_PORT so its MEALS_PORT override reaches
        # here; 8080 keeps `python ui_app.py` working on its own.
        port=int(os.environ.get("MEALS_UI_PORT", "8080")),
        dark=True,
        reload=False,
        show=False,
    )
-e 

=== File: ./planner.py ===
import argparse
import asyncio
import logging
import os
import random
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import instructor
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from repository import (
    LocalJSONRepository,
    PlanRepository,
    StoragePaths,
    run_sync,
)
from shopping import (
    aggregate_cook_events,
    categorize_department,
    collect_unique_plants,
    format_shopping_list_markdown,
    format_shopping_list_text,
    round_ingredient_quantity,
)
from week import (
    DEFAULT_INVENTORY_RULES,
    DEFAULT_MEAL_TYPES,
    DEFAULT_SERVINGS_PER_MEAL,
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    ShoppingWindow,
    SlotSpec,
    WeekSpec,
    default_week_spec,
    eaten_on,
    humanize,
    meal_types,
    parse_slot_id,
    portions_for,
    shopping_windows,
    slot_id,
    slot_label,
    styles_for,
    validate_week,
)

load_dotenv()

DEFAULT_ALLOWED_NOVA_GROUPS = [1, 2, 3]

# Where the local files live is repository.py's business now; this default
# instance exists only so the CLI's --help text and pre-repository log
# messages have a filename to print before a LocalJSONRepository is
# constructed. Once a repository exists, read its own `.paths` instead.
DEFAULT_STORAGE_PATHS = StoragePaths()
LOG_FILE = "meals.log"

logger = logging.getLogger("meals")


def configure_logging(log_file: str = LOG_FILE) -> None:
    """Log per-day generation timing/token usage to a file for diagnosing slow days.

    Latency on a free OpenRouter route is highly variable (see CLAUDE.md) and
    the two known failure modes — a hung/throttled request and a reasoning
    model burning its token budget on hidden tokens — both show up in
    completion_tokens_details, not in anything the CLI prints today.
    """
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

FREE_MODEL_MAX_TOKENS = 8000
PAID_MODEL_MAX_TOKENS = 16000

MACRO_KEYS = ("calories", "protein_g", "net_carbs_g", "fat_g")

WEEKEND_DAYS = {"Saturday", "Sunday"}

# Stops the model from hitting a high protein budget by linearly scaling a
# single low-density ingredient (6 eggs, 500g yoghurt) instead of composing a
# realistic dish. Multiple dense protein sources combined instead.
PORTION_DENSITY_GUARD = (
    "- NEVER scale a single ingredient beyond standard human eating portions "
    "to hit a protein target: max 2-3 eggs per serving, max 150g yoghurt per "
    "serving, max 2 slices of bread/toast per serving, max 1 standard tin "
    "(90-125g) of sardines or mackerel per serving, max ~200g cooked "
    "meat/poultry/fish per serving.\n"
    "- If a slot's protein target (e.g. >45g) is higher than one dense "
    "source can naturally provide at a standard portion, do NOT reach it by "
    "multiplying a single ingredient (never output 6 eggs or 500g yoghurt to "
    "hit a number). Instead COMBINE multiple dense protein sources at "
    "realistic portions each, e.g. whey protein powder + Greek yoghurt + "
    "hemp seeds, or chicken breast + edamame, or eggs + smoked salmon. The "
    "target is reached by composing several complementary sources, not by "
    "inflating one.\n"
)

# Share of the day each meal type gets when splitting targets across slots.
# Only the ratios matter — they're normalised over whichever slots are
# actually being cooked, so a day with no snack redistributes its share.
DEFAULT_MEAL_WEIGHTS = {"breakfast": 0.30, "lunch": 0.30, "dinner": 0.30, "snack": 0.10}

# generate_week_plan's generation order: one API call per meal type, across
# every day it's cooked, rather than one call per day across every meal type.
# Dinner comes before lunch specifically so the one cross-meal-type leftover
# week.leftover_meal_type_error allows — a lunch eating a dinner's leftovers —
# always has its source already generated by the time its own stage runs.
# Order otherwise doesn't matter for correctness, only for how early a
# variety/cascading benefit shows up.
MEAL_TYPE_PRIORITY = ["breakfast", "dinner", "lunch", "snack"]


def meal_type_order(config: dict) -> List[str]:
    """generate_week_plan's per-run meal-type sequence.

    Filters MEAL_TYPE_PRIORITY down to meal types this config actually uses,
    then appends any config-defined meal type MEAL_TYPE_PRIORITY doesn't know
    about (in config's own order) — a custom meal type still gets generated,
    just without a considered position in the sequence.
    """
    known = meal_types(config)
    ordered = [meal_type for meal_type in MEAL_TYPE_PRIORITY if meal_type in known]
    ordered += [meal_type for meal_type in known if meal_type not in ordered]
    return ordered


# Injected only into the dinner call: generating all 7 dinners in one request
# gives the model full-week visibility, which a per-day call never had — this
# is the rule that visibility is for.
DINNER_VARIETY_RULE = (
    "- You are generating all 7 dinners for the week in this one request, so "
    "you can see the whole week at once. Maximize variety in main proteins: "
    "do not repeat poultry, beef, or any single main protein as the primary "
    "protein in more than two dinners across the week.\n"
)

# --------------------------------------------------------------------------
# AppConfig: config.json's schema, strictly validated at load time
# --------------------------------------------------------------------------
#
# Every section below used to be read with `config.get("section", {}).get(
# "key", SOME_DEFAULT)` scattered across planner.py/week.py, each call site
# free to pick its own fallback (or forget one). `load_app_config` now runs
# config.json through this model exactly once, at startup: a missing or
# mistyped field fails loudly there, before a single API call is made, and
# every field that survives is guaranteed present with a real value in the
# dict the rest of the app reads — so a call site indexes `config["key"]`
# directly instead of re-deciding what to do when it's absent.
#
# Sections that already have their own documented, per-item tolerance for
# malformed *entries* — `weekly_schedule.<day>.meal_overrides` (a typo in one
# meal must not cost the whole day, see `meal_overrides_for`) and
# `training_schedule` (an unknown day/type is logged and skipped, see
# `apply_training_adjustments`) — are typed loosely here (`Dict[str, Any]` /
# `List[Dict[str, Any]]`) so that existing per-item leniency still runs
# exactly as before. Strictness here is about the *shape* of config.json,
# not about re-implementing business rules that already live elsewhere.


class PlanningRules(BaseModel):
    """config.json's "planning_rules" object.

    Defaults match the numbers this section replaced when it was still a
    bare module constant (see git history) — an omitted key, or a
    config.json predating this section, resolves to the same behaviour as
    before it existed.
    """

    model_config = ConfigDict(extra="forbid")

    # 28 entries = 4 weeks of daily history, so recipe-name/style/protein
    # rotation has a full 4-week non-repeat window rather than 3.
    history_max_entries: int = 28
    protein_lookback_entries: int = 3
    # How many recent main proteins to name in the prompt. Long enough to
    # stop a week of chicken, short enough that a 7-day plan doesn't end up
    # banning everything the model knows by Friday.
    protein_avoid_window: int = 6
    # Models compose plausible meals but size them badly, so portions are
    # corrected after the fact by scaling every quantity linearly. The clamp
    # stops a trim producing an absurd portion (a 30g breakfast, a 900g
    # steak).
    portion_trim_limits: Tuple[float, float] = (0.6, 1.6)
    portion_trim_deadband: float = 0.03


DEFAULT_PLANNING_RULES = PlanningRules().model_dump()


class DietaryRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_nova_groups: List[int] = Field(default_factory=lambda: list(DEFAULT_ALLOWED_NOVA_GROUPS))
    banned_ingredients: List[str] = Field(default_factory=list)


class InventoryRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fridge_safe_days: int = DEFAULT_INVENTORY_RULES["fridge_safe_days"]
    perishable_day_gap: int = DEFAULT_INVENTORY_RULES["perishable_day_gap"]


class ServingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    servings_per_meal: int = DEFAULT_SERVINGS_PER_MEAL


class ShoppingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_days: List[str] = Field(default_factory=list)


class UISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bar_scale_limit: float = 1.6
    title_tooltip_chars: int = 38


class DaySchedule(BaseModel):
    """One `weekly_schedule.<day>` entry: the day's whole-day macro target.

    `meal_overrides` stays a loose `Dict[str, Any]` — see the module-level
    note above about `meal_overrides_for` owning per-item tolerance for a
    malformed override.
    """

    model_config = ConfigDict(extra="forbid")

    calories: float
    protein_g: float
    net_carbs_g: float
    fat_g: float
    meal_overrides: Dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """The full schema of config.json, validated once at load time.

    `model_dump()` hands the rest of the app back a plain dict — every
    downstream function still takes `config: dict`, including the ones that
    thread it into `instructor`'s validation `context=`, so this is about
    validating the file up front, not about rewriting how config flows
    through the rest of the codebase. Once a dict has passed through here,
    every field this model declares is guaranteed present with a real
    (possibly defaulted) value, which is what lets call sites drop the
    `.get(key, SOME_DEFAULT)` guard they used to need.
    """

    model_config = ConfigDict(extra="forbid")

    week_start_day: str = "Monday"
    meal_types: List[str] = Field(default_factory=lambda: list(DEFAULT_MEAL_TYPES))
    weekly_schedule: Dict[str, DaySchedule]
    week_defaults: Dict[str, str] = Field(default_factory=dict)
    meal_styles: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    meal_weights: Dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_MEAL_WEIGHTS))
    cuisines: List[str] = Field(default_factory=list)
    cuisine_meal_types: List[str] = Field(default_factory=list)
    serving_rules: ServingRules = Field(default_factory=ServingRules)
    shopping: ShoppingConfig = Field(default_factory=ShoppingConfig)
    training_schedule: List[Dict[str, Any]] = Field(default_factory=list)
    inventory_to_clear: List[str] = Field(default_factory=list)
    enable_sunday_prep: bool = False
    max_prep_active_mins: int = 120
    dietary_rules: DietaryRules = Field(default_factory=DietaryRules)
    planning_rules: PlanningRules = Field(default_factory=PlanningRules)
    inventory_rules: InventoryRules = Field(default_factory=InventoryRules)
    ui_settings: UISettings = Field(default_factory=UISettings)
    # The CLI's --model flag and the NiceGUI drawer's model select both
    # persist their choice here; unset means "use models.json's
    # default_planner_model" (see resolve_planner_model).
    openrouter_model: Optional[str] = None

    @model_validator(mode="after")
    def default_cuisine_meal_types_to_meal_types(self) -> "AppConfig":
        """An empty `cuisine_meal_types` means "every meal type" — resolved
        once here rather than as `config.get("cuisine_meal_types") or
        meal_types(config)` at every call site."""
        if not self.cuisine_meal_types:
            self.cuisine_meal_types = list(self.meal_types)
        return self


def load_app_config(raw: dict) -> dict:
    """Validate `raw` (config.json's parsed JSON) against `AppConfig` and
    hand back a plain, fully-populated dict.

    Raising here — before `generate_week_plan` makes a single API call —
    turns a schema mistake (a typo'd key, a string where a number belongs,
    an unknown top-level section) into one clear message instead of a
    `KeyError`/`TypeError` surfacing minutes later, three functions deep into
    a run, or seven times over as every day's generation hits the same
    missing field.
    """
    try:
        app_config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"config.json failed schema validation:\n{exc}") from exc
    return app_config.model_dump(mode="json")


def planning_rule(config: Optional[dict], key: str):
    """Read one `planning_rules` value out of `config`.

    `config` may be None for a caller with no config loaded yet (e.g. a
    preview before one's been chosen) — that still resolves to
    `DEFAULT_PLANNING_RULES`. Any config that *has* been loaded went through
    `load_app_config`, so `planning_rules` is guaranteed to carry every key
    with a real value; no per-key fallback is needed once it exists.
    """
    if config is None:
        return DEFAULT_PLANNING_RULES[key]
    return config["planning_rules"][key]

# Share of a workout's estimated_burn_kcal that flows to carbs vs. protein —
# glycogen-heavy cardio skews carb, resistance work skews protein. Shares sum
# to 1 per type so the whole burn is accounted for and fat_g (derived from
# whatever calories are left) is left exactly as it was before the workout.
TRAINING_INTENSITY_SPLIT = {
    "gym_hypertrophy": {"carb_share": 0.5, "protein_share": 0.5},
    "cardio_run": {"carb_share": 0.75, "protein_share": 0.25},
    "walk": {"carb_share": 0.7, "protein_share": 0.3},
}

# Approximate clock time for each meal type. The schema has no real per-meal
# time, so this is a fixed stand-in used only to find which meal a workout
# sits closest to — good enough to decide "before" vs. "after", not a
# calendar feature.
MEAL_TIME_OF_DAY = {
    "breakfast": "07:00",
    "lunch": "12:30",
    "snack": "15:30",
    "dinner": "19:00",
}

# A workout within this many minutes *after* a meal is "fuelled by" that
# meal, per the spec's "within 2 hours" rule for digestion constraints.
TRAINING_PRE_WORKOUT_DIGESTION_MINUTES = 120


def _clock_minutes(value: str) -> int:
    hours, _, minutes = str(value).partition(":")
    try:
        return int(hours or 0) * 60 + int(minutes or 0)
    except ValueError:
        # A drawer time field is free text — a malformed value must not take
        # the whole telemetry preview down with it, same tolerance as a
        # malformed meal_override.
        logger.warning("training_schedule: ignoring unparseable time '%s'", value)
        return 0


def apply_training_adjustments(config: dict) -> dict:
    """Fold `config["training_schedule"]` into targets before they're calculated.

    Returns a new config — this module never mutates the one it's handed —
    with three changes per scheduled (non-rest) session:

    A. Daily budget expansion: `estimated_burn_kcal` is added straight onto
       the day's `calories`, and split into `protein_g`/`net_carbs_g`
       additions by `TRAINING_INTENSITY_SPLIT`. The shares sum to 1, so
       `derive_fat_g` lands on the same fat_g the day had before the workout
       — a workout buys back carbs and protein, not fat.
    B. Meal slot pinning: whichever configured meal type sits closest in
       clock time (`MEAL_TIME_OF_DAY`) to the workout, on the side it
       follows, is given a `meal_override` carrying half the day's
       (post-expansion) carbs, its usual weighted share of protein and fat.
       `meal_overrides_for`'s two-pass split then takes care of the rest —
       the other meals absorb what's left exactly as any other pin does. An
       explicit override the config already set for that meal always wins;
       this never overwrites one.
    C. Digestion rules: any meal within `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES`
       *before* the workout gets a prompt note (`training_notes`, read by
       `build_slot_brief`) asking for low-fibre, low-fat, easily digestible
       food — a constraint on what the meal is made of, not its macros, so it
       doesn't fight step B.

    Called once, up front (CLI: `run_cli`; UI: `PlannerState.planning_config`)
    so every downstream reader — `week_targets`, `meal_overrides_for`,
    `build_slot_brief` — sees the same already-adjusted config rather than
    each needing its own patch.
    """
    sessions = [
        session
        for session in config["training_schedule"]
        if session.get("type") != "rest" and float(session.get("estimated_burn_kcal", 0) or 0) > 0
    ]
    if not sessions:
        return config

    schedule = {day: dict(targets) for day, targets in config["weekly_schedule"].items()}
    notes: Dict[str, Dict[str, str]] = {}
    weights = config["meal_weights"]
    day_meals = [meal_type for meal_type in meal_types(config) if meal_type in MEAL_TIME_OF_DAY]

    for session in sessions:
        day = session.get("day")
        if day not in schedule:
            logger.warning("training_schedule: ignoring session for unknown day '%s'", day)
            continue
        split = TRAINING_INTENSITY_SPLIT.get(session.get("type"))
        if split is None:
            logger.warning(
                "training_schedule: ignoring session with unknown type '%s' on %s",
                session.get("type"), day,
            )
            continue

        burn = float(session["estimated_burn_kcal"])
        day_targets = schedule[day]
        day_targets["calories"] = day_targets.get("calories", 0) + burn
        day_targets["protein_g"] = (
            day_targets.get("protein_g", 0) + burn * split["protein_share"] / 4
        )
        day_targets["net_carbs_g"] = (
            day_targets.get("net_carbs_g", 0) + burn * split["carb_share"] / 4
        )

        if not day_meals:
            continue
        workout_minutes = _clock_minutes(session.get("time", "00:00"))
        nearest = min(
            day_meals, key=lambda meal: abs(_clock_minutes(MEAL_TIME_OF_DAY[meal]) - workout_minutes)
        )

        if _clock_minutes(MEAL_TIME_OF_DAY[nearest]) >= workout_minutes:
            overrides = dict(day_targets.get("meal_overrides") or {})
            if nearest not in overrides:
                day_fat_g = derive_fat_g(
                    day_targets["calories"], day_targets["protein_g"], day_targets["net_carbs_g"]
                )
                weight = weights.get(nearest, 0.25) or 0.25
                pinned_protein = round(day_targets["protein_g"] * weight, 1)
                pinned_carbs = round(day_targets["net_carbs_g"] * 0.5, 1)
                pinned_fat = round(day_fat_g * weight, 1)
                overrides[nearest] = {
                    "calories": round(pinned_protein * 4 + pinned_carbs * 4 + pinned_fat * 9, 1),
                    "protein_g": pinned_protein,
                    "net_carbs_g": pinned_carbs,
                    "fat_g": pinned_fat,
                }
                day_targets["meal_overrides"] = overrides
                notes.setdefault(day, {})[nearest] = (
                    "[POST-WORKOUT MEAL: high glycogen replenishment required — "
                    f"carb-forward to refuel after {humanize(session.get('type'))}]"
                )

        for meal in day_meals:
            gap = workout_minutes - _clock_minutes(MEAL_TIME_OF_DAY[meal])
            # A meal at the exact workout minute (gap 0) already has the
            # post-workout note from the pin above if it was the nearest one
            # — setdefault leaves that in place rather than overwriting it.
            if 0 <= gap <= TRAINING_PRE_WORKOUT_DIGESTION_MINUTES:
                notes.setdefault(day, {}).setdefault(
                    meal,
                    "[PRE-WORKOUT MEAL: low-fibre, ultra-easily digestible, low-fat fuel — "
                    f"a {humanize(session.get('type'))} session follows at {session.get('time')}]",
                )

    adjusted = dict(config, weekly_schedule=schedule)
    if notes:
        adjusted["training_notes"] = notes
    return adjusted


def is_free_model(model: str) -> bool:
    return model.endswith(":free")


def reasoning_extra_body(model: str, config: dict) -> dict:
    """OpenRouter's `extra_body` for turning a model's hidden reasoning off.

    Disabled by default — see CLAUDE.md "Reasoning must be disabled": the
    identical prompt shape measured 303s and, on repeated runs, zero content
    (finish_reason "length") with it left on. Some providers go further and
    reject the request outright whenever the key is present at all (a hard
    400 "Reasoning is mandatory for this endpoint and cannot be disabled",
    not a retryable validation failure — `google/gemini-3.6-flash` did this
    on every call of a real run, failing the whole week in under a second).
    `models.json`'s `reasoning_required_models` lists ids like that; for them
    the key is omitted entirely rather than sent as `enabled: True` — the
    reason this task disables reasoning in the first place (no deliberation
    needed, the macro arithmetic is already done in Python) doesn't change
    just because the model insists on doing it anyway.
    """
    models_config = config.get("models") or {}
    if model in (models_config.get("reasoning_required_models") or []):
        return {}
    return {"reasoning": {"enabled": False}}


def meal_type_week_max_tokens(model: str, num_recipes: int) -> int:
    """Token budget for a MealTypeWeekRecipes call, scaled to how many
    recipes it's actually asking for in this one request.

    FREE/PAID_MODEL_MAX_TOKENS were sized for generate_day's calls, which
    never asked for more than 4 recipes (one day's meal types). A meal-type
    call can ask for up to 7 (every day of the week) — a real run measured
    a 7-dinner call using 14900 of a flat 16000-token budget, 93% of it, one
    verbose day away from `finish_reason: length`, which returns *zero*
    content (see CLAUDE.md "Reasoning must be disabled"), not a merely
    truncated response. Scaling per recipe, at the same per-recipe rate the
    flat constants implied for a 4-recipe day, fixes that; the `max(...)`
    floor keeps a small call (e.g. a single regenerated meal) exactly as
    generous as before.
    """
    base = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS
    per_recipe = base // 4
    return max(base, per_recipe * num_recipes)


def derive_fat_g(calories: float, protein_g: float, net_carbs_g: float) -> float:
    """Fat is whatever energy is left once protein and carbs are paid for.

    The one place this arithmetic lives, so a per-meal override and a whole-day
    target are derived by the identical rule.
    """
    return max(0, (calories - (protein_g * 4 + net_carbs_g * 4)) / 9)


def calculate_daily_targets(day_of_week: str, config: dict) -> dict:
    weekly_schedule = config["weekly_schedule"]
    if day_of_week not in weekly_schedule:
        raise ValueError(
            f"'{day_of_week}' not found in weekly_schedule. "
            f"Valid days: {list(weekly_schedule.keys())}"
        )

    day_targets = weekly_schedule[day_of_week]
    calories = day_targets["calories"]
    protein_g = day_targets["protein_g"]
    net_carbs_g = day_targets["net_carbs_g"]

    fat_g = derive_fat_g(calories, protein_g, net_carbs_g)

    return {
        "day_of_week": day_of_week,
        "calories": calories,
        "protein_g": protein_g,
        "net_carbs_g": net_carbs_g,
        "fat_g": round(fat_g, 1),
    }


def week_targets(spec: WeekSpec, config: dict) -> Dict[str, dict]:
    return {day: calculate_daily_targets(day, config) for day in spec.days}


def meal_overrides_for(day: str, config: dict) -> Dict[str, dict]:
    """Per-meal budgets pinned by config for `day`, keyed by meal_type.

    `weekly_schedule[day].meal_overrides` is how you say "Saturday dinner is
    900 kcal whatever else that day looks like" — a fixed budget for one meal
    that the weight-based split must work around rather than compute.

    Written the same way a daily target is (calories, protein_g, net_carbs_g);
    fat_g may be given explicitly but is otherwise derived by the same rule, so
    an override that names only calories puts every remaining calorie in fat.
    Malformed entries are dropped with a warning to meals.log rather than
    raised — a typo in one meal must not cost the whole day's generation.
    """
    raw = config["weekly_schedule"].get(day, {}).get("meal_overrides") or {}
    known = meal_types(config)

    resolved: Dict[str, dict] = {}
    for meal_type, override in raw.items():
        if meal_type not in known:
            logger.warning(
                "%s: ignoring meal_override for unknown meal type '%s' (known: %s)",
                day, meal_type, ", ".join(known),
            )
            continue
        if not isinstance(override, dict) or "calories" not in override:
            logger.warning(
                "%s %s: ignoring meal_override without a calories target", day, meal_type
            )
            continue
        calories = float(override["calories"])
        protein_g = float(override.get("protein_g", 0))
        net_carbs_g = float(override.get("net_carbs_g", 0))
        resolved[meal_type] = {
            "calories": calories,
            "protein_g": protein_g,
            "net_carbs_g": net_carbs_g,
            "fat_g": float(
                override.get("fat_g", derive_fat_g(calories, protein_g, net_carbs_g))
            ),
        }
    return resolved


# --------------------------------------------------------------------------
# History-aware rotation
# --------------------------------------------------------------------------


def next_choice(options: List[str], recent: List[str]) -> Optional[str]:
    """Strict least-recently-used pick from `options`.

    `recent` is oldest-to-newest usage. Never-used options rank before used
    ones, ties break on config order, so repeated calls walk the whole list
    before repeating anything. (A "not used in the last N" rule looks similar
    but starves the tail of the list: with 5 styles and N=3 it just cycles
    through the first 4 forever.)
    """
    if not options:
        return None
    last_seen = {option: -1 for option in options}
    for index, value in enumerate(recent):
        if value in last_seen:
            last_seen[value] = index
    return min(options, key=lambda option: (last_seen[option], options.index(option)))


def history_values(history: List[dict], key: str) -> List[str]:
    """Flat oldest-to-newest list of a scalar history field."""
    return [entry[key] for entry in history if entry.get(key)]


def history_styles(history: List[dict], meal_type: str) -> List[str]:
    values = []
    for entry in history:
        style = (entry.get("styles") or {}).get(meal_type)
        if style:
            values.append(style)
    return values


def recent_main_proteins(history: List[dict], config: Optional[dict] = None) -> List[str]:
    """Main proteins across the last few days, de-duplicated, so the model can
    be told not to repeat them."""
    lookback_entries = planning_rule(config, "protein_lookback_entries")
    seen = set()
    proteins = []
    for entry in history[-lookback_entries:]:
        for protein in entry.get("main_proteins", []):
            if protein not in seen:
                seen.add(protein)
                proteins.append(protein)
    return proteins


def recent_recipe_names(history: List[dict]) -> List[str]:
    """Recipe names across the whole retained history, de-duplicated, so an
    exact dish is never regenerated within the non-repeat window.

    Unlike `recent_main_proteins`, this is not sliced to
    `protein_lookback_entries` — it walks every entry `record_week_history`
    kept, which is exactly `history_max_entries` (28, a 4-week window). A
    protein just needs to *rotate*; a recipe name is meant not to repeat at
    all inside that window.
    """
    seen = set()
    names = []
    for entry in history:
        for name in entry.get("recipe_names", []):
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def resolve_auto_choices(spec: WeekSpec, config: dict, history: List[dict]) -> WeekSpec:
    """Fill in every `auto` style and cuisine with a concrete choice.

    Runs before any API call so the entire week is deterministic and
    previewable: rotation continues from meal_history.json and then keeps
    rotating *within* the week, so seven auto breakfasts don't all resolve to
    whatever happens to be first in the config list.
    """
    cuisines = config["cuisines"]
    cuisine_meal_types = config["cuisine_meal_types"]

    recent_cuisines = history_values(history, "cuisine")
    recent_styles = {
        meal_type: history_styles(history, meal_type) for meal_type in meal_types(config)
    }

    resolved: List[SlotSpec] = []
    for slot in spec.slots:
        if slot.mode != MODE_COOK:
            resolved.append(slot)
            continue

        style = slot.style
        if not style:
            options = list(styles_for(config, slot.meal_type).keys())
            style = next_choice(options, recent_styles.get(slot.meal_type, []))
        if style:
            recent_styles.setdefault(slot.meal_type, []).append(style)

        cuisine = slot.cuisine
        if not cuisine and slot.meal_type in cuisine_meal_types:
            cuisine = next_choice(cuisines, recent_cuisines)
        if cuisine:
            recent_cuisines.append(cuisine)

        resolved.append(slot.model_copy(update={"style": style, "cuisine": cuisine}))

    return spec.model_copy(update={"slots": resolved})


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


class Ingredient(BaseModel):
    name: str = Field(..., description="Ingredient name")
    quantity_g: float = Field(..., gt=0, description="Quantity in grams")
    nova_group: int = Field(
        ..., ge=1, le=4, description="NOVA food processing classification (1-4)"
    )
    calories: float = Field(..., ge=0)
    protein_g: float = Field(..., ge=0)
    net_carbs_g: float = Field(..., ge=0)
    fat_g: float = Field(..., ge=0)

    @field_validator("nova_group")
    @classmethod
    def enforce_allowed_nova_group(cls, v: int, info: ValidationInfo) -> int:
        allowed = DEFAULT_ALLOWED_NOVA_GROUPS
        if info.context and "config" in info.context:
            allowed = info.context["config"]["dietary_rules"]["allowed_nova_groups"]
        if v not in allowed:
            raise ValueError(
                f"nova_group {v} is not allowed (allowed groups: {allowed}); "
                "ultra-processed (Group 4) ingredients are rejected"
            )
        return v

    @field_validator("name")
    @classmethod
    def reject_banned_ingredients(cls, v: str, info: ValidationInfo) -> str:
        banned = []
        if info.context and "config" in info.context:
            banned = info.context["config"]["dietary_rules"]["banned_ingredients"]
        name_lower = v.lower()
        for banned_item in banned:
            if banned_item.lower() in name_lower:
                raise ValueError(
                    f"ingredient '{v}' contains banned ingredient '{banned_item}'"
                )
        return v

    def per_serving_macros(self, total_servings: int = 1) -> Dict[str, float]:
        servings = max(1, total_servings)
        return {key: getattr(self, key) / servings for key in MACRO_KEYS}

    def scaled(self, factor: float) -> "Ingredient":
        """Multiply quantity and macros by `factor`, quantity snapped to a
        practical grocery amount via `round_ingredient_quantity`."""
        return self.model_copy(
            update=dict(
                {key: round(getattr(self, key) * factor, 1) for key in MACRO_KEYS},
                quantity_g=round_ingredient_quantity(
                    self.name, self.quantity_g * factor, categorize_department(self.name)
                ),
            )
        )


class Recipe(BaseModel):
    name: str = Field(..., description="Recipe name")
    meal_type: str = Field(..., description="breakfast, lunch, dinner, or snack")
    ingredients: List[Ingredient]
    instructions: List[str] = Field(..., description="Ordered preparation steps")
    prep_time_minutes: int = Field(..., ge=0)
    servings: int = Field(
        default=1,
        ge=1,
        description=(
            "Total portions this recipe yields. Left at the default of 1 by the "
            "model — Python overwrites it with the portion count derived from "
            "how many slots eat this cook."
        ),
    )
    prep_notes: Optional[str] = Field(
        default=None,
        description="Storage/reheating notes. Set by Python for multi-meal cooks.",
    )
    long_oven_cook: bool = Field(
        default=False,
        description=(
            "True only if this dish is primarily a long (roughly 60+ minutes), "
            "mostly hands-off oven roast/bake or slow-cooker/braise — the kind of "
            "thing you start and walk away from, suited to unattended batch "
            "cooking. False for anything needing active stovetop attention, a "
            "quick recipe, or a no-cook dish, even one made in bulk. Defaults to "
            "False (older saved recipes predate this field and are conservatively "
            "treated as not hands-off)."
        ),
    )

    @field_validator("prep_time_minutes")
    @classmethod
    def enforce_weeknight_prep_limit(cls, v: int, info: ValidationInfo) -> int:
        day = info.context.get("day") if info.context else None
        if day and day not in WEEKEND_DAYS and v > 30:
            raise ValueError(
                f"prep_time_minutes {v} exceeds the 30-minute weeknight limit for {day}; "
                "simplify the recipe to a quick weeknight meal."
            )
        return v

    @property
    def total_macros(self) -> Dict[str, float]:
        totals = {key: 0.0 for key in MACRO_KEYS}
        for ingredient in self.ingredients:
            for key in MACRO_KEYS:
                totals[key] += getattr(ingredient, key)
        return totals

    @property
    def per_serving_macros(self) -> Dict[str, float]:
        servings = max(1, self.servings)
        return {key: value / servings for key, value in self.total_macros.items()}

    def resize_by_factor(self, factor: float) -> "Recipe":
        """Multiply every ingredient's quantity and macros by `factor`.

        `servings` is left untouched — this is the single-serving portion
        trim (`fit_recipe_to_budget`), not a change in how many servings the
        recipe yields. `scale_to_servings` is the one that changes `servings`.
        """
        return self.model_copy(
            update={"ingredients": [ingredient.scaled(factor) for ingredient in self.ingredients]}
        )

    def round_ingredient_quantities(self) -> "Recipe":
        """Snap every ingredient's quantity to a practical grocery amount.

        `resize_by_factor` already does this via `Ingredient.scaled()` — this
        covers the untrimmed path, where `fit_recipe_to_budget` leaves a
        recipe's raw model-generated quantities untouched (within its
        deadband, or when there's no budget to trim to).
        """
        return self.model_copy(
            update={
                "ingredients": [
                    ingredient.model_copy(
                        update={
                            "quantity_g": round_ingredient_quantity(
                                ingredient.name,
                                ingredient.quantity_g,
                                categorize_department(ingredient.name),
                            )
                        }
                    )
                    for ingredient in self.ingredients
                ]
            }
        )

    def scale_to_servings(
        self,
        target_servings: int,
        keeps_for_days: int = 0,
        config: Optional[dict] = None,
    ) -> "Recipe":
        """Rescale from `self.servings` to `target_servings` and refresh storage notes.

        The factor is relative to `self.servings`, not assumed to be 1, so
        this covers both the model's single-serving output growing into a
        batch and an already-scaled batch being resized again after a grid
        edit changes how many slots claim it.
        """
        factor = target_servings / max(1, self.servings)
        scaled = self.resize_by_factor(factor) if factor != 1.0 else self

        prep_notes = scaled.prep_notes
        if not prep_notes or prep_notes.startswith(STORAGE_NOTE_PREFIX):
            prep_notes = storage_note(target_servings, keeps_for_days, config) or None

        return scaled.model_copy(update={"servings": target_servings, "prep_notes": prep_notes})


class DayRecipes(BaseModel):
    """The model's response for a single day: one recipe per cook slot."""

    recipes: List[Recipe]

    @model_validator(mode="after")
    def reject_untrimmable_macro_miss(self, info: ValidationInfo) -> "DayRecipes":
        """Bounce a response too far off budget for the portion trim to rescue.

        The threshold is derived from planning_rules.portion_trim_limits rather than picked:
        anything the trim can scale onto its budget is accepted and corrected
        silently, and only a response needing a factor outside the clamp is
        rejected so instructor can hand the model its own numbers back and
        retry. Coupling them this way keeps retries rare — which matters,
        because exhausting max_retries fails the whole week, and a free model
        that reliably missed by a fixed margin would do exactly that.

        The characteristic failure this catches: when some meals are already
        covered by leftovers, the model ignores the reduced target and writes
        a full day anyway.
        """
        context = info.context or {}
        budget = context.get("day_budget")
        if not budget or not self.recipes:
            return self

        target = budget.get("calories", 0)
        total = sum(
            ingredient.calories
            for recipe in self.recipes
            for ingredient in recipe.ingredients
        )
        if target <= 0 or total <= 0:
            return self

        factor = target / total
        low, high = planning_rule(context.get("config"), "portion_trim_limits")
        if not low <= factor <= high:
            raise ValueError(
                f"the recipes total {total:.0f} kcal per serving but the budget for "
                f"these meals is {target:.0f} kcal ({(total - target) / target:+.0%}). "
                "Resize the portions to match each meal's stated budget — do not "
                "add or remove meals, and remember any meals already listed as "
                "fixed leftovers are NOT yours to generate."
            )
        return self


class MealTypeWeekRecipes(BaseModel):
    """The model's response for one meal type across every day it's cooked
    this week — the transposed twin of DayRecipes, which held one day's
    several meal types. See generate_week_plan for why generation is now
    organised this way (macro cascading, protein variety across dinners).

    Keyed by day name rather than a list, same reasoning as DayRecipes being
    keyed by meal_type: a dict makes a missing or misnamed day a structural
    mismatch instructor can retry on, rather than a positional guess.
    """

    recipes: Dict[str, Recipe]

    @model_validator(mode="after")
    def enforce_weeknight_prep_limit(self) -> "MealTypeWeekRecipes":
        """Recipe.enforce_weeknight_prep_limit reads a single `day` out of
        instructor's context — that fits DayRecipes' one-call-one-day shape
        but not this one, where a single call spans up to 7 days each needing
        their own weekday check. Done here instead, over self.recipes' own
        day keys, rather than threading a per-item context through Pydantic.
        """
        for day, recipe in self.recipes.items():
            if day not in WEEKEND_DAYS and recipe.prep_time_minutes > 30:
                raise ValueError(
                    f"{day}: prep_time_minutes {recipe.prep_time_minutes} exceeds the "
                    "30-minute weeknight limit; simplify the recipe to a quick weeknight meal."
                )
        return self

    @model_validator(mode="after")
    def reject_untrimmable_macro_miss(self, info: ValidationInfo) -> "MealTypeWeekRecipes":
        """Per-day version of DayRecipes.reject_untrimmable_macro_miss (see
        that docstring for why the threshold is derived from
        planning_rules.portion_trim_limits rather than a flat tolerance).

        Checked per day rather than pooled across the week: a week with one
        day at +80% and another at -80% would net to zero on a pooled total
        and let two rejectable days hide behind the average.
        """
        context = info.context or {}
        day_budgets = context.get("day_budgets")
        if not day_budgets:
            return self

        low, high = planning_rule(context.get("config"), "portion_trim_limits")
        problems = []
        for day, recipe in self.recipes.items():
            budget = day_budgets.get(day)
            if not budget:
                continue
            target = budget.get("calories", 0)
            total = recipe.total_macros["calories"]
            if target <= 0 or total <= 0:
                continue
            factor = target / total
            if not low <= factor <= high:
                problems.append(
                    f"{day}: {total:.0f} kcal per serving vs a budget of {target:.0f} kcal "
                    f"({(total - target) / target:+.0%})"
                )
        if problems:
            raise ValueError(
                "These days' recipes are too far off their per-serving budget for portion "
                "resizing to fix: " + "; ".join(problems) + ". Resize the portions to match "
                "each day's stated budget — do not add or remove meals."
            )
        return self


class CookEvent(BaseModel):
    """One recipe, cooked once, eaten by one or more slots.

    `recipe.ingredients` hold the full scaled batch quantities (portions x the
    model's single-serving amounts), matching what you actually buy and cook.
    """

    slot_id: str
    day: str
    meal_type: str
    portions: int
    style: Optional[str] = None
    cuisine: Optional[str] = None
    eaten_by: List[str] = Field(default_factory=list)
    recipe: Recipe


class PrepPhase(BaseModel):
    """One step in the Sunday batch-prep timeline, run in order."""

    name: str
    description: Optional[str] = None
    active_minutes: int = 0
    passive_minutes: int = 0


class SundayPrepSession(BaseModel):
    """Optional Sunday batch-prep plan: raw prep work aggregated across the
    week's cook events (e.g. "dice all onions" once instead of per cook day),
    done ahead of time rather than repeated on each cook day.

    `total_active_minutes` is capped at 120 to match config's
    `max_prep_active_mins` default — hands-on prep time, not the passive
    minutes spent simmering/roasting/chilling while unattended.
    """

    total_active_minutes: int = Field(..., le=120)
    total_passive_minutes: int = 0
    aggregated_ingredients: Dict[str, str] = Field(default_factory=dict)
    timeline: List[PrepPhase] = Field(default_factory=list)
    meals_included: List[str] = Field(
        default_factory=list,
        description="Names of the dishes this prep session covers",
    )


class WeekPlan(BaseModel):
    days: List[str]
    servings_per_meal: int
    generated_at: str
    cook_events: List[CookEvent]
    slots: List[SlotSpec]
    targets: Dict[str, dict]
    failures: Dict[str, str] = Field(
        default_factory=dict,
        description="day -> error, for days whose generation failed outright",
    )
    sunday_prep_session: Optional[SundayPrepSession] = Field(
        default=None,
        description="Aggregated Sunday batch-prep plan, when enable_sunday_prep is on",
    )
    unique_plants: List[str] = Field(default_factory=list)

    def by_slot(self) -> Dict[str, CookEvent]:
        return {event.slot_id: event for event in self.cook_events}

    def events_on_days(self, days: List[str]) -> List[CookEvent]:
        day_set = set(days)
        return [event for event in self.cook_events if event.day in day_set]

    def day_slot_macros(self, day: str) -> dict:
        """What one person actually eats on `day`, summed across their slots."""
        by_slot = self.by_slot()
        events = []
        for slot in self.slots:
            if slot.day != day or slot.mode == MODE_SKIP:
                continue
            source_id = slot.id if slot.mode == MODE_COOK else slot.source
            event = by_slot.get(source_id)
            if event is not None:
                events.append(event)
        return sum_serving_macros(events)


# --------------------------------------------------------------------------
# Macro math (always Python, never the model)
# --------------------------------------------------------------------------


def fit_recipe_to_budget(
    recipe: Recipe, budget: dict, config: Optional[dict] = None
) -> Tuple[Recipe, float]:
    """Resize one serving of a recipe so its calories land on its budget.

    Models pick sensible *ingredients* and implausible *amounts*, and every
    macro is linear in quantity, so a single scale factor fixes the portion
    without touching the dish. It cannot fix a bad macro ratio — a recipe with
    the right calories and the wrong protein split stays wrong, and shows up
    as a visible delta in the day summary rather than being papered over.
    """
    actual = recipe.total_macros["calories"]
    target = budget.get("calories", 0)
    if actual <= 0 or target <= 0:
        return recipe.round_ingredient_quantities(), 1.0

    low, high = planning_rule(config, "portion_trim_limits")
    deadband = planning_rule(config, "portion_trim_deadband")
    factor = target / actual
    factor = min(max(factor, low), high)
    if abs(factor - 1.0) < deadband:
        return recipe.round_ingredient_quantities(), 1.0
    return recipe.resize_by_factor(factor), factor


# Opening words of a storage note we wrote ourselves. Used to tell our note
# apart from a model-authored one when a batch is later resized: ours is stale
# the moment the portion count moves, a model's is about the dish and must
# survive. Worst case a model happens to open its note this way and gets an
# accurate note in place of its own.
STORAGE_NOTE_PREFIX = "Yields "


def storage_note(portions: int, keeps_for_days: int, config: Optional[dict] = None) -> str:
    """How to keep a batch that has to last until the meal that finishes it.

    Empty for a single serving eaten the day it's cooked — there is nothing to
    say, and `scale_to_servings` leaves `prep_notes` alone rather than writing one.

    `config` supplies `inventory_rules.fridge_safe_days`; omitted (or missing
    the key) falls back to week.DEFAULT_INVENTORY_RULES's value.
    """
    if portions <= 1 or keeps_for_days <= 0:
        return ""
    fridge_safe_days = (config or {}).get("inventory_rules", {}).get(
        "fridge_safe_days", DEFAULT_INVENTORY_RULES["fridge_safe_days"]
    )
    storage = (
        "refrigerate in airtight containers"
        if keeps_for_days < fridge_safe_days
        else f"refrigerate what you'll eat within {fridge_safe_days} days and freeze the rest"
    )
    return (
        f"{STORAGE_NOTE_PREFIX}{portions} portions, eaten across {keeps_for_days} day(s). "
        f"Portion immediately, {storage}; reheat thoroughly before serving."
    )


# Weeknight slots that eat a Sunday-prepped batch show this instead of the
# cook's own prep_time_minutes — reheating/plating a dish that's already
# cooked is a few minutes, not the from-scratch cook time recorded on the day
# it was actually made.
SUNDAY_PREP_REHEAT_MINUTES = 10


def is_sunday_prepped(event: CookEvent, week_plan: WeekPlan) -> bool:
    """Whether `event` was folded into `week_plan`'s Sunday prep session.

    `prep_notes` is set only for a batch that outlives its cook day (see
    `Recipe.scale_to_servings`), and `generate_sunday_prep_session` takes
    every such candidate into the session — so "has prep_notes" plus "a
    session exists" is exactly "this batch was prepped ahead", without
    needing a separate stored link.
    """
    return bool(event.recipe.prep_notes) and week_plan.sunday_prep_session is not None


def weeknight_prep_minutes(event: CookEvent, week_plan: WeekPlan) -> int:
    """Active minutes a slot *eating* `event` needs.

    The cook's own card keeps showing `recipe.prep_time_minutes` — that's the
    real work, on the day it happens. A later slot living off the batch only
    reheats/assembles it.
    """
    if is_sunday_prepped(event, week_plan):
        return SUNDAY_PREP_REHEAT_MINUTES
    return event.recipe.prep_time_minutes


def scale_recipe(
    recipe: Recipe, portions: int, keeps_for_days: int, config: Optional[dict] = None
) -> Recipe:
    """Deprecated: use `recipe.scale_to_servings(portions, keeps_for_days, config)`."""
    return recipe.scale_to_servings(portions, keeps_for_days, config)


def rescale_cook_event(
    event: CookEvent,
    portions: int,
    keeps_for_days: int,
    eaten_by: List[str],
    config: Optional[dict] = None,
) -> CookEvent:
    """Deprecated: use `event.recipe.scale_to_servings(...)` and rebuild the event.

    Kept only as a thin wrapper for callers not yet migrated; new call sites
    should scale the recipe directly (see `PlannerState.apply_spec`).
    """
    if event.portions <= 0:
        return event

    recipe = event.recipe.scale_to_servings(portions, keeps_for_days, config)
    return event.model_copy(
        update={"portions": portions, "eaten_by": list(eaten_by), "recipe": recipe}
    )


def sum_serving_macros(events: Iterable[CookEvent]) -> dict:
    """Per-serving macros of `events`, summed key by key.

    The one place that walks `MACRO_KEYS` to total up `CookEvent`s — every
    caller differs only in *which* events it hands in (a day's slots, just
    the leftovers, every other slot but one), so that selection logic stays
    with the caller and only the summation is shared.
    """
    totals = {key: 0.0 for key in MACRO_KEYS}
    for event in events:
        serving = event.recipe.per_serving_macros
        for key in MACRO_KEYS:
            totals[key] += serving[key]
    return totals


def day_multiplicity(spec: WeekSpec, day: str) -> Dict[str, int]:
    """How many times each of `day`'s own cooks is eaten on that same day.

    Almost always 1. It's >1 when a big lunch is also eaten at dinner, and the
    prompt has to say so or the model will aim at the wrong daily total.
    """
    counts = {slot.id: 1 for slot in spec.cook_slots_on(day)}
    for slot in spec.slots:
        if slot.day == day and slot.mode == MODE_LEFTOVER and slot.source in counts:
            counts[slot.source] += 1
    return counts


def carried_macros(
    spec: WeekSpec, day: str, events: Dict[str, CookEvent]
) -> Tuple[dict, List[str]]:
    """Macros already locked in for `day` by leftovers cooked on earlier days,
    plus human-readable descriptions of those meals for the prompt."""
    carried = []
    for slot in spec.slots:
        if slot.day != day or slot.mode != MODE_LEFTOVER or not slot.source:
            continue
        event = events.get(slot.source)
        if event is None:
            continue
        carried.append((slot, event))

    descriptions = []
    for slot, event in carried:
        serving = event.recipe.per_serving_macros
        descriptions.append(
            f"{slot.meal_type}: leftovers of \"{event.recipe.name}\" "
            f"(cooked {event.day}) — {serving['calories']:.0f} kcal, "
            f"{serving['protein_g']:.0f}g protein, {serving['net_carbs_g']:.0f}g net carbs, "
            f"{serving['fat_g']:.0f}g fat"
        )
    return sum_serving_macros(event for _, event in carried), descriptions


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def api_key_error() -> Optional[str]:
    """Why generation can't start, or None if it can.

    Split out of `build_client` so a caller can ask *before* committing to a
    run. `generate_week_plan` turns a per-day exception into a per-day failure
    (see "a failed day must not fail the week"), which is exactly wrong for a
    missing key: it isn't a flaky provider, it will fail every day identically,
    and the user would wait through seven attempts to be told so seven times.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or api_key == "your_openrouter_api_key_here":
        return "OPENROUTER_API_KEY is not set. Add it to your .env file."
    return None


def _require_models_config(models_config: dict, *keys: str) -> None:
    """models.json is the only source for these values now — no in-code
    fallback. An empty or incomplete models.json must fail loudly here,
    not drift silently onto an outdated hardcoded model or endpoint."""
    missing = [key for key in keys if not models_config.get(key)]
    if missing:
        raise ValueError(
            f"models.json is missing required key(s): {', '.join(missing)}. "
            "Set them in models.json — there is no built-in fallback."
        )


def build_client(models_config: Optional[dict] = None) -> instructor.Instructor:
    """`models_config` is the loaded `models.json` (or a dict-alike with the
    same keys) — pass `config.get("models")` from a caller that already
    merged it in."""
    models_config = models_config or {}
    error = api_key_error()
    if error:
        raise RuntimeError(error)
    _require_models_config(models_config, "openrouter_base_url", "request_timeout_seconds")
    openai_client = OpenAI(
        base_url=models_config.get("openrouter_base_url"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=models_config.get("request_timeout_seconds"),
    )
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


def build_async_client(models_config: Optional[dict] = None) -> instructor.Instructor:
    """Async twin of `build_client`, for callers that already run on a loop.

    `import_external_recipe` is one call, not seven sequential days, so unlike
    `generate_day` there's no thread-per-call dance to do here — it can
    `await` OpenRouter directly instead of going through `asyncio.to_thread`
    the way a day's generation has to (see "Storage goes through an async
    repository" in CLAUDE.md for why that dance exists at all).
    """
    models_config = models_config or {}
    error = api_key_error()
    if error:
        raise RuntimeError(error)
    _require_models_config(models_config, "openrouter_base_url", "request_timeout_seconds")
    openai_client = AsyncOpenAI(
        base_url=models_config.get("openrouter_base_url"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=models_config.get("request_timeout_seconds"),
    )
    # MD_JSON, not JSON/TOOLS — same reason as build_client: Recipe nests
    # Ingredient, and several free OpenRouter providers 422 on the $defs/$ref
    # a schema-carrying mode emits for a nested model.
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


def resolve_planner_model(config: dict) -> str:
    """The model a weekly-generation call should use: config.json's explicit
    `openrouter_model` override wins (the CLI's `--model` flag and the
    NiceGUI drawer's model select both write this), else models.json's
    `default_planner_model`. There is no further fallback — an empty
    models.json and no override must fail loudly, not silently plan against
    an outdated hardcoded model."""
    models_config = config.get("models") or {}
    model = config["openrouter_model"] or models_config.get("default_planner_model")
    if not model:
        raise ValueError(
            "No planner model configured: config.json has no 'openrouter_model' "
            "override and models.json has no 'default_planner_model'. Set one "
            "in models.json."
        )
    return model


def resolve_recipe_parser_model(config: dict) -> str:
    """The model `import_external_recipe` should use.

    Deliberately does *not* consult `openrouter_model` — that field is the
    weekly planner's per-run override (CLI `--model`, the drawer's model
    select) and has nothing to do with parsing a pasted recipe. models.json's
    model-selection strategy names this the "Vision AI / Scans & Web Recipe
    Parser" role precisely so a cheap/fast model can be used here regardless
    of which (usually pricier) model the week is generating with.
    """
    models_config = config.get("models") or {}
    model = models_config.get("recipe_parser_model") or models_config.get("default_planner_model")
    if not model:
        raise ValueError(
            "No recipe parser model configured: models.json has neither "
            "'recipe_parser_model' nor 'default_planner_model' set."
        )
    return model


async def load_config_with_models(repository: PlanRepository) -> dict:
    """`load_config()` validated through `AppConfig`, plus `load_models_config()`
    merged under `config["models"]`.

    One call so every caller that needs a *usable* config — CLI, recipe
    import, the NiceGUI app at startup — gets the same schema validation and
    the same model selection, instead of each remembering to also load
    models.json (or skipping validation entirely). `models` is added after
    `load_app_config` returns, not before: it isn't part of config.json's own
    schema, it's a separate file merged in for caller convenience, the same
    way `nudge_foods`/`training_notes` are added to a config dict later at
    generation time.
    """
    raw = await repository.load_config()
    config = load_app_config(raw)
    config["models"] = await repository.load_models_config()
    return config


async def import_external_recipe(
    raw_input: str,
    config: Optional[dict] = None,
    repository: Optional[PlanRepository] = None,
) -> Recipe:
    """Parse pasted recipe text (or a scrape) into a typed, validated Recipe.

    `config` lets a caller that already has one (the NiceGUI drawer,
    mid-session) skip a reload; left out, one is loaded fresh so this also
    works as a standalone call. Dietary rules are enforced the same way
    generation enforces them — nova_group and banned_ingredients read
    `info.context["config"]` (see `Ingredient`'s validators) — so an imported
    recipe answers to the same rules a generated one does, not a weaker set.

    Unlike `generate_day`, there's no day budget to trim against: an imported
    recipe is reported as written, servings included, with ingredient
    quantities and macros for the FULL recipe at that serving count — exactly
    what `Recipe`/`Ingredient` already assume elsewhere (`per_serving_macros`
    divides by `servings`), so no extra scaling step belongs here. A caller
    dropping this into a specific slot (see `PlannerState.swap_slot_with_favorite`)
    normalises to one serving and rescales there, same as it would for any
    other favorite.
    """
    if config is None:
        config = await load_config_with_models(repository or LocalJSONRepository())

    dietary_rules = config["dietary_rules"]
    client = build_async_client(config.get("models"))

    system_prompt = (
        "You turn unformatted recipe text — pasted from a website, a photo's "
        "OCR, a handwritten note — into structured, precise data. Extract "
        "exactly one recipe.\n\n"
        "Rules:\n"
        "- Convert every quantity to grams (quantity_g). Normalize cups, "
        "tablespoons, teaspoons, ounces, pounds and count-based amounts "
        "('1 onion', '2 eggs') using standard ingredient densities/weights — "
        "never leave a non-metric unit in the output.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Classify honestly — if the source is genuinely an ultra-processed "
        "product (Group 4), classify it as 4 rather than mislabeling it; the "
        "schema will reject it rather than let it through unnoticed.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients']) or '(none configured)'}.\n"
        "- Report calories, protein_g, net_carbs_g and fat_g for every "
        "ingredient. If the source doesn't state an ingredient's macros, "
        "estimate them from standard nutrition data for that food and "
        "quantity — every macro field is required and must be a real number, "
        "never null or omitted, even when your best estimate is 0.\n"
        "- `servings` is however many portions the recipe as written yields "
        "(read it off the source if stated, e.g. 'serves 4'; otherwise your "
        "best judgement, minimum 1). Ingredient quantities and macros are for "
        "the FULL recipe at that serving count, not for one serving.\n"
        "- If meal_type isn't stated, infer breakfast/lunch/dinner/snack from "
        "the dish itself.\n"
        "- Do not invent ingredients or steps absent from the source, and add "
        "no commentary — respond with the structured data only."
    )

    model = resolve_recipe_parser_model(config)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("import_external_recipe: requesting parse from %s", model)
    started = time.monotonic()
    try:
        recipe, completion = await client.chat.completions.create_with_completion(
            model=model,
            response_model=Recipe,
            max_retries=3,
            max_tokens=max_tokens,
            extra_body=reasoning_extra_body(model, config),
            context={"config": config},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_input},
            ],
        )
    except Exception as exc:
        logger.warning(
            "import_external_recipe: failed after %.1fs — %s: %s",
            time.monotonic() - started,
            type(exc).__name__,
            str(exc).split("\n")[0][:300],
        )
        raise

    elapsed = time.monotonic() - started
    usage = getattr(completion, "usage", None)
    logger.info(
        "import_external_recipe: got response in %.1fs (finish_reason=%s, completion_tokens=%s)",
        elapsed,
        getattr(completion.choices[0], "finish_reason", None) if completion.choices else None,
        getattr(usage, "completion_tokens", None),
    )
    return recipe


def split_targets(
    remaining: dict,
    cook_slots: List[SlotSpec],
    multiplicity: Dict[str, int],
    config: dict,
    overrides: Optional[Dict[str, dict]] = None,
) -> Dict[str, dict]:
    """Divide the day's remaining macros into a per-meal budget.

    Handing the model one daily number and letting it apportion the meals
    itself is where free models drift worst — they reach for a familiar "full
    day" shape and blow past a reduced target. Splitting in Python is the same
    rule as calculate_daily_targets: Python does the arithmetic, the model
    only fills in food.

    Two passes, in order:

    1. Any slot with an explicit override (see meal_overrides_for) is assigned
       that budget verbatim — it is a fixed number, not a starting point.
    2. What those pinned meals consume is subtracted from the day, and only the
       leftover is split across the remaining slots by normalised weight. So
       pinning breakfast at 600 kcal moves the other meals down, exactly as
       leftover macros already do; weights renormalise over the un-pinned slots
       alone, which is the same rule that already redistributes a skipped meal.

    A meal eaten more than once today contributes its macros that many times,
    so it consumes (or takes a share of) the day proportionally while its own
    recipe budget stays a single serving.
    """
    overrides = overrides or {}
    weights_config = config["meal_weights"]

    budgets: Dict[str, dict] = {}
    pinned = {key: 0.0 for key in MACRO_KEYS}
    flexible: List[SlotSpec] = []
    for slot in cook_slots:
        override = overrides.get(slot.meal_type)
        if override is None:
            flexible.append(slot)
            continue
        budgets[slot.id] = {key: override[key] for key in MACRO_KEYS}
        eaten_today = multiplicity.get(slot.id, 1)
        for key in MACRO_KEYS:
            pinned[key] += override[key] * eaten_today

    if not flexible:
        return budgets

    left = {key: remaining[key] - pinned[key] for key in MACRO_KEYS}
    overspent = [key for key in MACRO_KEYS if left[key] < 0]
    if overspent:
        # Floored rather than raised: the day still generates, and a 0 budget
        # shows up as a visible shortfall in the day summary the same way an
        # orphaned leftover does.
        logger.warning(
            "meal_overrides for %s claim more %s than the day's target leaves — "
            "the un-overridden meals are floored at 0 for those macros",
            ", ".join(sorted(overrides)),
            ", ".join(overspent),
        )
        left = {key: max(0.0, left[key]) for key in MACRO_KEYS}

    base = {slot.id: weights_config.get(slot.meal_type, 0.25) or 0.25 for slot in flexible}
    total_weight = sum(base[slot.id] * multiplicity.get(slot.id, 1) for slot in flexible)
    if total_weight <= 0:
        budgets.update({slot.id: dict(left) for slot in flexible})
        return budgets

    budgets.update(
        {
            slot.id: {key: left[key] * base[slot.id] / total_weight for key in MACRO_KEYS}
            for slot in flexible
        }
    )
    return budgets


def inventory_instruction(config: dict) -> str:
    """Prompt line telling the model to build around food already in the house.

    `inventory_to_clear` is a plain list of things to use up ("400g chicken
    thighs", "half a bag of spinach"). It is a priority, never a constraint:
    the styles, cuisines and macro budgets still win, because a model told it
    *must* use an item will wedge it into a breakfast where it doesn't belong.

    Note these items are still costed as ordinary ingredients, so they appear
    on the shopping list — the list says what a recipe needs, not what you have
    yet to buy.
    """
    items = [
        str(item).strip()
        for item in config["inventory_to_clear"]
        if str(item).strip()
    ]
    if not items:
        return ""
    return (
        "- We already have these at home and want them used up first — prefer "
        "them over buying more of the same kind of thing, and spread them "
        f"across the day's meals where they genuinely fit: {', '.join(items)}. "
        "Never force one into a meal it doesn't suit, and never break a meal's "
        "style, cuisine or macro budget to use one up.\n"
    )


# How many whfoods.json entries to nudge the model toward per generation run
# (see `select_nudge_foods`). ~12 is enough to give the model real choice
# across a week of meals without dominating the prompt or the day's flavour
# profile.
NUDGE_FOOD_SAMPLE_SIZE = 12


async def select_nudge_foods(
    repository: Optional[PlanRepository] = None, count: int = NUDGE_FOOD_SAMPLE_SIZE
) -> List[str]:
    """A random sample of nutrient-dense whole foods (whfoods.json) to nudge
    generation toward this run.

    Sampled once per run, not once per day or slot: `build_slot_brief` reads
    the same list off `config["nudge_foods"]` for every slot, so the
    directive names one consistent dozen foods across the week's meals
    instead of a different set per recipe. An empty/missing whfoods.json
    (older checkout, fresh install) resolves to an empty list, which
    `build_slot_brief` treats as "say nothing" — the same tolerance
    `inventory_instruction` extends to an empty pantry list.
    """
    foods = await (repository or LocalJSONRepository()).load_whfoods()
    if not foods:
        return []
    return random.sample(foods, min(count, len(foods)))


def build_slot_brief(
    slot: SlotSpec, config: dict, times_eaten_today: int, budget: dict, pinned: bool = False
) -> str:
    """One line per meal the model has to invent: style, cuisine, macro budget."""
    parts = [f"- {slot.meal_type.upper()}"]
    style_description = styles_for(config, slot.meal_type).get(slot.style or "")
    if slot.style:
        parts.append(f"style: {humanize(slot.style)}")
        if style_description:
            parts.append(f"({style_description})")
    if slot.cuisine:
        parts.append(f"cuisine: {humanize(slot.cuisine)} — authentic flavours and technique")
    nudge_foods = config.get("nudge_foods")
    if nudge_foods:
        parts.append(
            "prioritize incorporating these nutrient-dense foods where flavour "
            f"profiles permit: {', '.join(nudge_foods)}"
        )
    parts.append(
        f"budget (one serving): {budget['calories']:.0f} kcal, "
        f"{budget['protein_g']:.0f}g protein, {budget['net_carbs_g']:.0f}g net carbs, "
        f"{budget['fat_g']:.0f}g fat"
    )
    if pinned:
        parts.append("[fixed budget for this meal — the other meals absorb the rest of the day]")
    if times_eaten_today > 1:
        parts.append(f"[eaten {times_eaten_today}x today, budget already accounts for that]")
    training_note = config.get("training_notes", {}).get(slot.day, {}).get(slot.meal_type)
    if training_note:
        parts.append(training_note)
    if slot.day in WEEKEND_DAYS:
        parts.append("[Weekend meal: multi-step or slow-cooked recipes up to 180 minutes allowed.]")
    else:
        parts.append("[Max prep/cook time: 30 minutes. Focus on quick weeknight meals.]")
    return " | ".join(parts)


def generate_day(
    day: str,
    targets: dict,
    cook_slots: List[SlotSpec],
    config: dict,
    servings_per_meal: int,
    multiplicity: Dict[str, int],
    carried: dict,
    carried_descriptions: List[str],
    avoid_proteins: Optional[List[str]] = None,
    avoid_recipe_names: Optional[List[str]] = None,
    progress_note=None,
) -> Dict[str, Recipe]:
    """Generate one day's cooked recipes, returned keyed by meal_type.

    Only the slots set to cook are generated. Leftover slots' macros are
    subtracted from the day's target first, so the model is asked for the
    remaining gap rather than a full day it would then overshoot.
    """
    client = build_client(config.get("models"))
    dietary_rules = config["dietary_rules"]

    remaining = {key: max(0.0, targets[key] - carried.get(key, 0.0)) for key in MACRO_KEYS}

    avoid_protein_instruction = (
        "- Avoid making any of these the primary protein again — they were used "
        f"recently: {', '.join(avoid_proteins)}.\n"
        if avoid_proteins
        else ""
    )
    avoid_recipe_name_instruction = (
        "- Do NOT generate any of these exact dishes again under the same or "
        "a trivially reworded name — they already appear in recent history "
        f"and must not repeat: {', '.join(avoid_recipe_names)}.\n"
        if avoid_recipe_names
        else ""
    )
    leftovers_instruction = (
        "- The following meals on this day are ALREADY FIXED (leftovers of an "
        "earlier cook). Do NOT generate them; their macros are already "
        "subtracted from the targets below:\n"
        + "\n".join(f"  * {line}" for line in carried_descriptions)
        + "\n"
        if carried_descriptions
        else ""
    )
    batch_slots = [slot for slot in cook_slots if multiplicity.get(slot.id, 1) > 1]
    batch_instruction = (
        "- Some meals below are eaten more than once. Design those to portion "
        "and reheat well (a tray/pot dish rather than something that must be "
        "served immediately). Still give quantities for ONE serving; Python "
        "scales them to the full batch.\n"
        if batch_slots
        else ""
    )

    overrides = meal_overrides_for(day, config)
    budgets = split_targets(remaining, cook_slots, multiplicity, config, overrides)
    slot_briefs = "\n".join(
        build_slot_brief(
            slot,
            config,
            multiplicity.get(slot.id, 1),
            budgets[slot.id],
            pinned=slot.meal_type in overrides,
        )
        for slot in cook_slots
    )

    system_prompt = (
        f"You are a precision meal-planning assistant cooking for "
        f"{servings_per_meal} people. Generate exactly {len(cook_slots)} recipe(s) "
        f"for {day} — one for each meal listed by the user, matching its meal_type "
        "exactly. Recipes must be realistic, varied and non-repetitive.\n\n"
        "Rules:\n"
        "- Use metric units only (grams) for all ingredient quantities.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Never use Group 4 ultra-processed ingredients.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients'])}.\n"
        "- Respect each meal's requested style and cuisine exactly. Where a "
        "cuisine is given it applies to that meal only — the other meals must "
        "draw on different culinary traditions so the day isn't one cuisine "
        "end to end.\n"
        "- Prioritize nutrient-dense whole foods: vary the vegetables, herbs/"
        "spices and protein sources across the day and minimize ingredient "
        "overlap between meals, the way a registered dietitian would design a "
        "menu — not just whatever hits the numbers with the fewest ingredients.\n"
        "- Keep single dairy/staple portions realistic (e.g., max 200–250g "
        "yoghurt or cottage cheese per serving).\n"
        "- Combine multiple complementary protein sources (e.g., yoghurt + "
        "protein powder, or eggs + lean meat) rather than scaling up a single "
        "low-density ingredient to meet high protein targets.\n"
        f"{PORTION_DENSITY_GUARD}"
        f"{avoid_protein_instruction}"
        f"{avoid_recipe_name_instruction}"
        f"{inventory_instruction(config)}"
        f"{leftovers_instruction}"
        f"{batch_instruction}"
        "- Each meal below carries its OWN macro budget. Hit that meal's "
        "budget — not a typical portion size for that meal, and not a whole "
        "day's worth. The budgets are already calculated and already add up "
        "correctly; do not recompute or redistribute them.\n"
        "- All budgets are PER SERVING (one portion for one person). Report "
        "every ingredient's quantity_g and its calories/protein_g/net_carbs_g/"
        "fat_g for a SINGLE serving too. Do not multiply by the number of "
        "people or by any batch size — Python scales the recipe afterwards.\n"
        "- Leave servings and prep_notes at their schema defaults — Python "
        "fills those in.\n"
        "- Set long_oven_cook to true only if this dish is a genuinely long "
        "(60+ minutes), mostly hands-off oven roast/bake or slow-cooker/braise "
        "— false for anything needing active stovetop attention, a quick "
        "recipe, or a no-cook dish, even one you're making in bulk.\n"
        "- Do not show your work, explain your reasoning, or narrate your "
        "process. Respond with the structured data only."
    )

    scope_note = (
        f"This is only PART of {day} — {len(carried_descriptions)} other meal(s) "
        "are already fixed and are NOT yours to generate. Do not try to make "
        "these recipes add up to a full day.\n\n"
        if carried_descriptions
        else ""
    )

    user_prompt = (
        f"{scope_note}"
        f"Generate exactly {len(cook_slots)} recipe(s) for {day}, one per line "
        f"below, each hitting its own budget:\n{slot_briefs}\n\n"
        "Together they must total approximately (per serving, already "
        "calculated — do not recompute):\n"
        f"- Calories: {remaining['calories']:.0f} kcal\n"
        f"- Protein: {remaining['protein_g']:.0f} g\n"
        f"- Net carbs: {remaining['net_carbs_g']:.0f} g\n"
        f"- Fat: {remaining['fat_g']:.0f} g\n"
    )

    model = resolve_planner_model(config)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("%s: requesting %d recipe(s) from %s", day, len(cook_slots), model)
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=DayRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        # The validator compares against the sum of the per-recipe budgets, not
        # `remaining`: a meal eaten twice in one day contributes its macros
        # twice, so the recipes legitimately total less than the day does.
        context={
            "config": config,
            "day": day,
            "day_budget": {
                key: sum(budget[key] for budget in budgets.values()) for key in MACRO_KEYS
            },
        },
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    elapsed = time.monotonic() - started
    usage = getattr(completion, "usage", None)
    reasoning_tokens = getattr(
        getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
    )
    logger.info(
        "%s: got response in %.1fs (finish_reason=%s, completion_tokens=%s, reasoning_tokens=%s)",
        day,
        elapsed,
        getattr(completion.choices[0], "finish_reason", None) if completion.choices else None,
        getattr(usage, "completion_tokens", None),
        reasoning_tokens,
    )

    # One slot per (day, meal_type) by construction, so meal_type is a safe key.
    by_meal_type = {recipe.meal_type.strip().lower(): recipe for recipe in response.recipes}
    missing = [slot.meal_type for slot in cook_slots if slot.meal_type not in by_meal_type]
    if missing:
        raise ValueError(
            f"{day}: model returned no recipe for {', '.join(missing)} "
            f"(got: {', '.join(sorted(by_meal_type)) or 'nothing'})"
        )

    fitted = {}
    for slot in cook_slots:
        recipe, factor = fit_recipe_to_budget(by_meal_type[slot.meal_type], budgets[slot.id], config)
        if factor != 1.0 and progress_note:
            progress_note(
                f"{day} {slot.meal_type}: portions resized x{factor:.2f} to hit "
                f"{budgets[slot.id]['calories']:.0f} kcal"
            )
        fitted[slot.meal_type] = recipe
    return fitted


def generate_meal_type_week(
    meal_type: str,
    cook_slots_by_day: Dict[str, SlotSpec],
    day_budgets: Dict[str, dict],
    config: dict,
    servings_per_meal: int,
    times_eaten_today: Dict[str, int],
    carried_descriptions_by_day: Dict[str, List[str]],
    pinned_days: Optional[List[str]] = None,
    avoid_proteins: Optional[List[str]] = None,
    avoid_recipe_names: Optional[List[str]] = None,
    progress_note=None,
) -> Dict[str, Recipe]:
    """Generate one meal type's recipe for every day it's cooked, in one call.

    The transposed twin of generate_day: that asked for one day's several
    meal types sharing a day budget; this asks for one meal type's several
    days, each already carrying its own cascaded budget (see
    generate_week_plan, which computes `day_budgets` fresh at this stage from
    what every earlier-generated meal type actually consumed).

    `cook_slots_by_day` only has entries for days this meal type is actually
    cooked — a leftover or skipped day never reaches here, so the model is
    never asked to invent something Python is about to discard.
    """
    client = build_client(config.get("models"))
    dietary_rules = config["dietary_rules"]
    days = list(cook_slots_by_day.keys())
    pinned_days = pinned_days or []

    avoid_protein_instruction = (
        "- Avoid making any of these the primary protein again — they were used "
        f"recently: {', '.join(avoid_proteins)}.\n"
        if avoid_proteins
        else ""
    )
    avoid_recipe_name_instruction = (
        "- Do NOT generate any of these exact dishes again under the same or "
        "a trivially reworded name — they already appear in recent history "
        f"and must not repeat: {', '.join(avoid_recipe_names)}.\n"
        if avoid_recipe_names
        else ""
    )
    dinner_variety_instruction = DINNER_VARIETY_RULE if meal_type == "dinner" else ""

    all_carried_descriptions = [
        f"{day}: {description}"
        for day, descriptions in carried_descriptions_by_day.items()
        for description in descriptions
    ]
    leftovers_instruction = (
        "- These meals are already fixed (cooked earlier this run, or already "
        "leftovers of something cooked earlier this run) and are NOT part of "
        "what you're generating — their macros are already subtracted from "
        "each day's budget below, but keep ingredients varied against them "
        "where you can:\n"
        + "\n".join(f"  * {line}" for line in all_carried_descriptions)
        + "\n"
        if all_carried_descriptions
        else ""
    )
    batch_days = [day for day in cook_slots_by_day if times_eaten_today.get(day, 1) > 1]
    batch_instruction = (
        "- Some days below are eaten more than once that same day. Design "
        "those to portion and reheat well (a tray/pot dish rather than "
        "something that must be served immediately). Still give quantities "
        "for ONE serving; Python scales them to the full batch.\n"
        if batch_days
        else ""
    )

    slot_briefs = "\n".join(
        f"- {day} "
        + build_slot_brief(
            slot,
            config,
            times_eaten_today.get(day, 1),
            day_budgets[day],
            pinned=day in pinned_days,
        ).lstrip("- ")
        for day, slot in cook_slots_by_day.items()
    )

    system_prompt = (
        f"You are a precision meal-planning assistant cooking for "
        f"{servings_per_meal} people. Generate exactly {len(cook_slots_by_day)} "
        f"{meal_type} recipe(s) for the week below — one per day listed, each "
        "matching that day's own budget. Recipes must be realistic, varied and "
        "non-repetitive across the days.\n\n"
        "Rules:\n"
        "- Use metric units only (grams) for all ingredient quantities.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Never use Group 4 ultra-processed ingredients.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients'])}.\n"
        "- Respect each day's requested style and cuisine exactly. Different "
        "days should draw on different culinary traditions and styles so the "
        "week isn't the same dish repeated under different names.\n"
        "- Prioritize nutrient-dense whole foods: vary the vegetables, herbs/"
        "spices and protein sources across the days and minimize ingredient "
        "overlap between them, the way a registered dietitian would design a "
        "week's menu — not just whatever hits the numbers with the fewest "
        "ingredients.\n"
        "- Keep single dairy/staple portions realistic (e.g., max 200-250g "
        "yoghurt or cottage cheese per serving).\n"
        "- Combine multiple complementary protein sources (e.g., yoghurt + "
        "protein powder, or eggs + lean meat) rather than scaling up a single "
        "low-density ingredient to meet high protein targets.\n"
        f"{PORTION_DENSITY_GUARD}"
        f"{dinner_variety_instruction}"
        f"{avoid_protein_instruction}"
        f"{avoid_recipe_name_instruction}"
        f"{inventory_instruction(config)}"
        f"{leftovers_instruction}"
        f"{batch_instruction}"
        "- Each day below carries its OWN macro budget, already reduced for "
        "whatever that day already has fixed from other meal types this run. "
        "Hit that day's budget for this one meal — not a typical portion size "
        "for it — and do not recompute or redistribute the numbers.\n"
        "- All budgets are PER SERVING (one portion for one person). Report "
        "every ingredient's quantity_g and its calories/protein_g/net_carbs_g/"
        "fat_g for a SINGLE serving too. Do not multiply by the number of "
        "people or by any batch size — Python scales the recipe afterwards.\n"
        "- Leave servings and prep_notes at their schema defaults — Python "
        "fills those in.\n"
        "- Respond with a JSON object whose keys are exactly the day names "
        "listed below and whose values are that day's recipe — do not add, "
        "omit, or rename a day.\n"
        "- Do not show your work, explain your reasoning, or narrate your "
        "process. Respond with the structured data only."
    )

    user_prompt = (
        f"Generate exactly {len(cook_slots_by_day)} {meal_type} recipe(s), one per "
        f"day below, each hitting its own budget:\n{slot_briefs}\n"
    )

    model = resolve_planner_model(config)
    max_tokens = meal_type_week_max_tokens(model, len(cook_slots_by_day))

    logger.info(
        "%s: requesting %d recipe(s) across %s from %s",
        meal_type, len(cook_slots_by_day), ", ".join(days), model,
    )
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=MealTypeWeekRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        context={
            "config": config,
            "day_budgets": day_budgets,
        },
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    elapsed = time.monotonic() - started
    usage = getattr(completion, "usage", None)
    reasoning_tokens = getattr(
        getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
    )
    logger.info(
        "%s: got response in %.1fs (finish_reason=%s, completion_tokens=%s, reasoning_tokens=%s)",
        meal_type,
        elapsed,
        getattr(completion.choices[0], "finish_reason", None) if completion.choices else None,
        getattr(usage, "completion_tokens", None),
        reasoning_tokens,
    )

    missing = [day for day in days if day not in response.recipes]
    if missing:
        raise ValueError(
            f"{meal_type}: model returned no recipe for {', '.join(missing)} "
            f"(got: {', '.join(sorted(response.recipes)) or 'nothing'})"
        )

    fitted = {}
    for day, slot in cook_slots_by_day.items():
        recipe, factor = fit_recipe_to_budget(response.recipes[day], day_budgets[day], config)
        if factor != 1.0 and progress_note:
            progress_note(
                f"{day} {meal_type}: portions resized x{factor:.2f} to hit "
                f"{day_budgets[day]['calories']:.0f} kcal"
            )
        fitted[day] = recipe
    return fitted


def build_sunday_prep_brief(event: CookEvent, spec: WeekSpec) -> str:
    """One candidate line for the Sunday prep prompt.

    Carries only numbers Python already computed (portions, prep time, which
    days it's eaten, the fridge/freezer storage window) so the model organises
    a cooking session instead of re-deriving any of them. Leads with
    `event.recipe.name` verbatim, which is also the exact string the system
    prompt tells the model to echo back into `meals_included` — one dish per
    candidate line here.
    """
    eaten_days = sorted(
        {parse_slot_id(slot_id)[0] for slot_id in event.eaten_by},
        key=spec.day_index,
    )
    ingredient_list = ", ".join(
        f"{ingredient.name} {ingredient.quantity_g:.0f}g" for ingredient in event.recipe.ingredients
    )
    return (
        f"- {event.recipe.name} ({event.meal_type}, {event.portions} portions, "
        f"{event.recipe.prep_time_minutes} min prep-as-written) — eaten on: "
        f"{', '.join(eaten_days)}. Storage: {event.recipe.prep_notes}\n"
        f"  Ingredients: {ingredient_list}\n"
        f"  Method: {' '.join(event.recipe.instructions)}"
    )


def generate_sunday_prep_session(
    cook_events: List[CookEvent],
    spec: WeekSpec,
    config: dict,
) -> Optional[SundayPrepSession]:
    """Turn the week's already-generated batch cooks into one Sunday prep timeline.

    Candidates are cook events with a `prep_notes` (`scale_to_servings` only
    writes one when `keeps_for_days > 0`, i.e. the batch has to outlive the
    day it's cooked) AND `recipe.long_oven_cook` — a batch that's actually a
    quick stovetop stir-fry or a no-cook smoothie pack doesn't belong in a
    hands-off Sunday session even though it's eaten across several days; it
    needs active attention on ITS OWN cook day like anything else. Only a
    genuinely long, mostly-unattended oven roast/bake or slow-cooker/braise —
    the kind of thing you start and walk away from — is worth folding into one
    aggregated prep block. A week with no such dish has nothing to aggregate,
    so this returns None rather than an empty session — same "no candidates,
    no prompt" rule `inventory_instruction` uses for an empty pantry list.

    This only reorganises recipes the day-generation calls already produced —
    it never invents food, so unlike `generate_day` there is no macro budget
    to validate against. The only hard constraint is
    `SundayPrepSession.total_active_minutes <= 120`, enforced by the schema
    itself; `config`'s `max_prep_active_mins` is the *target* handed to the
    model in the prompt and is clamped to that same 120 ceiling, since a
    config asking for more than the schema allows would fail validation on
    every single call.
    """
    if not config["enable_sunday_prep"]:
        return None

    candidates = [
        event for event in cook_events if event.recipe.prep_notes and event.recipe.long_oven_cook
    ]
    if not candidates:
        return None

    max_active = min(config["max_prep_active_mins"], 120)
    fridge_safe_days = config["inventory_rules"]["fridge_safe_days"]
    candidate_briefs = "\n".join(build_sunday_prep_brief(event, spec) for event in candidates)

    system_prompt = (
        "You are planning ONE Sunday batch-prep session that gets a week's "
        "worth of already-decided batch cooking done in advance. The recipes "
        "below are fixed — do not change ingredients, quantities or methods, "
        "only organise the work of cooking them.\n\n"
        "Rules:\n"
        f"- Hard cap: total_active_minutes must not exceed {max_active}. Active "
        "minutes are hands-on time (chopping, stirring, portioning, sealing "
        "bags) — time a slow cooker, oven or fridge runs unattended is "
        "passive_minutes, not active_minutes, and does not count against the "
        "cap.\n"
        "- meals_included must list the name of every dish being prepped in "
        "this session (one entry per candidate recipe below, verbatim) — "
        "the aggregated timeline says how to cook them, this says what they "
        "are.\n"
        "- Aggregate identical prep across recipes into one step instead of "
        "repeating it: if three recipes each need a chopped onion, one phase "
        "chops all the onions together rather than three separate steps.\n"
        "- Sequence the timeline chronologically: start the highest-passive-"
        "time tasks first (slow cookers, roasts, anything that simmers or "
        "bakes unattended), so their passive time overlaps with the active "
        "chopping/portioning/bagging work for the other dishes, rather than "
        "the whole session running start to end back to back.\n"
        "- 4-Day Storage Rule: each candidate's Storage line below already "
        "says whether it's fridge-only or fridge-plus-freezer (Python computed "
        f"this from how many days it has to last against a {fridge_safe_days}-"
        "day fridge-safe window) — do not recompute it. Any item marked to "
        "freeze must get its own explicit freeze step (portion, label, date, "
        "freeze) in the timeline; note the thaw lead time in that phase's "
        "description (e.g. 'move to fridge the night before eating') rather "
        "than scheduling a thaw step in this session, since thawing happens "
        "later in the week, not on Sunday.\n"
        "- aggregated_ingredients maps a combined prep task to what it covers, "
        'e.g. {"onions": "4 diced (for chilli, bolognese, curry)"} — only '
        "include ingredients that are actually shared prep across two or more "
        "of the candidates; it is not a shopping list.\n"
        "- Do not show your work or narrate — respond with the structured "
        "data only."
    )

    user_prompt = (
        f"This week's batch-prep candidates ({len(candidates)}):\n\n"
        f"{candidate_briefs}\n\n"
        "Build the Sunday prep session for exactly these candidates."
    )

    model = resolve_planner_model(config)
    client = build_client(config.get("models"))
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info(
        "sunday_prep: requesting session for %d candidate(s) from %s", len(candidates), model
    )
    started = time.monotonic()
    session, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=SundayPrepSession,
        max_retries=3,
        max_tokens=max_tokens,
        extra_body=reasoning_extra_body(model, config),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    elapsed = time.monotonic() - started
    usage = getattr(completion, "usage", None)
    logger.info(
        "sunday_prep: got response in %.1fs (finish_reason=%s, completion_tokens=%s)",
        elapsed,
        getattr(completion.choices[0], "finish_reason", None) if completion.choices else None,
        getattr(usage, "completion_tokens", None),
    )
    return session


def on_calling_loop(callback):
    """Wrap `callback` so a worker thread's call runs back on *this* loop.

    `generate_day` runs in a worker thread (see `generate_week_plan`), which
    means anything it calls back into runs off the event loop. That is fine for
    the CLI's `print`, and not fine for the NiceGUI front end, whose elements
    queue their updates against the loop that owns the client — so the hop is
    undone here, once, rather than being every caller's problem to remember.

    Must be called from the loop thread: it captures the running loop at wrap
    time, and there is none inside the worker.
    """
    if callback is None:
        return None
    loop = asyncio.get_running_loop()

    def forward(*args, **kwargs) -> None:
        loop.call_soon_threadsafe(lambda: callback(*args, **kwargs))

    return forward


async def _generate_day_events(
    day: str,
    spec: WeekSpec,
    config: dict,
    day_targets: dict,
    portions: Dict[str, int],
    claims: Dict[str, List[str]],
    carry_events: Dict[str, CookEvent],
    avoid_proteins: List[str],
    avoid_recipe_names: List[str],
    note_callback=None,
) -> Dict[str, CookEvent]:
    """Generate and scale one day's cook events, keyed by slot_id.

    Shared by `generate_week_plan` (which walks every day) and
    `regenerate_single_day` (which calls this for just one). `carry_events`
    supplies the cook events any of `day`'s leftover slots point at — for a
    full-week walk that's the days already generated this run; for a single
    day it's whatever is already in the saved plan, since a leftover only
    ever points backwards and those days aren't being touched.

    Raises on failure rather than swallowing it — callers decide whether
    that's fatal (a single-day retry) or recoverable (a week walking on to
    the next day).
    """
    cook_slots = spec.cook_slots_on(day)
    if not cook_slots:
        return {}

    carried, descriptions = carried_macros(spec, day, carry_events)
    protein_avoid_window = planning_rule(config, "protein_avoid_window")
    thread_safe_note = on_calling_loop(note_callback)

    recipes = await asyncio.to_thread(
        generate_day,
        day=day,
        targets=day_targets,
        cook_slots=cook_slots,
        config=config,
        servings_per_meal=spec.servings_per_meal,
        multiplicity=day_multiplicity(spec, day),
        carried=carried,
        carried_descriptions=descriptions,
        avoid_proteins=avoid_proteins[-protein_avoid_window:],
        avoid_recipe_names=avoid_recipe_names,
        progress_note=thread_safe_note,
    )

    events: Dict[str, CookEvent] = {}
    for slot in cook_slots:
        recipe = recipes[slot.meal_type]
        claim_ids = claims.get(slot.id, [slot.id])
        last_day_index = max(spec.day_index(parse_slot_id(value)[0]) for value in claim_ids)
        recipe = recipe.scale_to_servings(
            portions[slot.id],
            keeps_for_days=last_day_index - spec.day_index(day),
            config=config,
        )
        events[slot.id] = CookEvent(
            slot_id=slot.id,
            day=day,
            meal_type=slot.meal_type,
            portions=portions[slot.id],
            style=slot.style,
            cuisine=slot.cuisine,
            eaten_by=claim_ids,
            recipe=recipe,
        )
    return events


async def _generate_meal_type_events(
    meal_type: str,
    spec: WeekSpec,
    config: dict,
    day_budgets: Dict[str, dict],
    portions: Dict[str, int],
    claims: Dict[str, List[str]],
    carried_descriptions_by_day: Dict[str, List[str]],
    pinned_days: List[str],
    avoid_proteins: List[str],
    avoid_recipe_names: List[str],
    note_callback=None,
) -> Dict[str, CookEvent]:
    """Generate and scale one meal type's cook events across the week, keyed by slot_id.

    The transposed twin of `_generate_day_events`: that walked one day's
    several meal types, this walks one meal type's several days. `day_budgets`
    is keyed by day and already holds this stage's cascaded budget for
    `meal_type` (see `generate_week_plan`) rather than the day's full target.

    Raises on failure rather than swallowing it, same as `_generate_day_events`
    — `generate_week_plan` decides that's recoverable (mark every day this
    stage would have cooked as failed, move on to the next meal type).
    """
    cook_slots_by_day = {
        slot.day: slot
        for slot in spec.cook_slots()
        if slot.meal_type == meal_type and slot.day in day_budgets
    }
    if not cook_slots_by_day:
        return {}

    times_eaten_today = {
        day: day_multiplicity(spec, day).get(slot.id, 1)
        for day, slot in cook_slots_by_day.items()
    }
    thread_safe_note = on_calling_loop(note_callback)

    recipes = await asyncio.to_thread(
        generate_meal_type_week,
        meal_type=meal_type,
        cook_slots_by_day=cook_slots_by_day,
        day_budgets=day_budgets,
        config=config,
        servings_per_meal=spec.servings_per_meal,
        times_eaten_today=times_eaten_today,
        carried_descriptions_by_day=carried_descriptions_by_day,
        pinned_days=pinned_days,
        avoid_proteins=avoid_proteins,
        avoid_recipe_names=avoid_recipe_names,
        progress_note=thread_safe_note,
    )

    events: Dict[str, CookEvent] = {}
    for day, slot in cook_slots_by_day.items():
        recipe = recipes[day]
        claim_ids = claims.get(slot.id, [slot.id])
        last_day_index = max(spec.day_index(parse_slot_id(value)[0]) for value in claim_ids)
        recipe = recipe.scale_to_servings(
            portions[slot.id],
            keeps_for_days=last_day_index - spec.day_index(day),
            config=config,
        )
        events[slot.id] = CookEvent(
            slot_id=slot.id,
            day=day,
            meal_type=meal_type,
            portions=portions[slot.id],
            style=slot.style,
            cuisine=slot.cuisine,
            eaten_by=claim_ids,
            recipe=recipe,
        )
    return events


async def generate_week_plan(
    spec: WeekSpec,
    config: dict,
    history: Optional[List[dict]] = None,
    progress_callback=None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Generate the whole week, one API call per meal type that has cooking to do.

    Loops by meal type (`meal_type_order`: breakfast, dinner, lunch, snack)
    instead of by day: each call asks for one meal type's recipe on every day
    it's cooked, all at once. That gives the model full-week visibility for
    protein variety (`DINNER_VARIETY_RULE`) and lets each day's budget
    cascade — after a meal type is generated, its *actual* output (not its
    a-priori share) is subtracted from every affected day's remaining budget,
    so the next meal type's split is computed from the real number rather
    than a static weight guess. Dinner runs before lunch specifically so the
    one cross-meal-type leftover `week.leftover_meal_type_error` allows (a
    lunch eating a dinner's leftovers) always has its source already cooked.

    Cost still scales with what's actually being cooked, just along a
    different axis than before: a meal type nobody cooks this week (every
    slot leftover or skipped) makes no call at all, same as a day with
    nothing to cook used to be free.

    Each meal type's API call is dispatched with `asyncio.to_thread`, same
    reason as before: `generate_meal_type_week` blocks on instructor's
    *synchronous* client for 30s-3min (worse per call than a single day used
    to, since up to 7 recipes are being generated at once), and awaiting it
    inline would hold the loop for that whole span — fatal in NiceGUI, where
    it would freeze every connected browser until the call returned. Meal
    types remain strictly sequential — one thread at a time, in
    `meal_type_order` — because a later meal type's budget is computed from
    every earlier meal type's actual output.

    A whole meal-type call failing marks every day it would have cooked as
    failed (`WeekPlan.failures`, now keyed by slot_id rather than by day —
    see the module docstring's discussion of the trade this accepts:
    fewer, bigger calls means a bad one can cost up to 7 recipes instead of
    the one day's worth a per-day call could lose).
    """
    if history is None:
        history = await (repository or LocalJSONRepository()).load_history()
    nudge_foods = await select_nudge_foods(repository)
    if nudge_foods:
        config = dict(config, nudge_foods=nudge_foods)
    targets = week_targets(spec, config)
    portions = portions_for(spec)
    claims = eaten_on(spec)
    # Seeded from previous weeks, then extended as this week generates —
    # otherwise every stage is told to avoid the same stale list and nothing
    # stops all seven dinners being chicken.
    avoid_proteins = recent_main_proteins(history, config)
    # Same seed-then-extend pattern as avoid_proteins, but over the full
    # history_max_entries window (see recent_recipe_names) rather than a
    # short lookback — a recipe name must not repeat at all within it.
    avoid_recipe_names = recent_recipe_names(history)
    protein_avoid_window = planning_rule(config, "protein_avoid_window")

    by_id = spec.by_id()
    # The full daily targets, mutated down as each meal type's actual
    # consumption is subtracted — see the cascade step at the end of the loop
    # below. This is the "daily_macro_budgets" the horizontal-generation
    # design cascades through.
    daily_macro_budgets: Dict[str, dict] = {day: dict(targets[day]) for day in spec.days}

    events: Dict[str, CookEvent] = {}
    failures: Dict[str, str] = {}

    order = meal_type_order(config)
    for stage_index, meal_type in enumerate(order):
        cook_days = [
            day for day in spec.days if by_id[slot_id(day, meal_type)].mode == MODE_COOK
        ]

        if progress_callback:
            progress_callback(meal_type, len(cook_days))

        if cook_days:
            # This stage's per-day budget: split what's left of each day
            # (already reduced by every earlier-generated meal type) across
            # whichever meal types haven't been resolved yet — current stage
            # plus everything later in `order` — the same weight/override
            # logic `split_targets` already applies within one day, just
            # recomputed fresh at each stage against a shrinking remainder.
            pending_types = order[stage_index:]
            day_budgets: Dict[str, dict] = {}
            pinned_days: List[str] = []
            for day in cook_days:
                # A not-yet-resolved LEFTOVER slot (source not generated this
                # run yet — always true for a pending meal type, since its
                # source is either the same pending meal type or resolves no
                # earlier than it) still has to claim a weighted share of the
                # day, or the cook slots below would spend budget it needs
                # once its own stage runs. split_targets doesn't generate it
                # — nothing downstream reads a leftover slot's own budget
                # entry — it only uses it to shrink what's left for the rest.
                pending_slots = [
                    by_id[slot_id(day, pending_type)]
                    for pending_type in pending_types
                    if by_id[slot_id(day, pending_type)].mode in (MODE_COOK, MODE_LEFTOVER)
                ]
                overrides = meal_overrides_for(day, config)
                budgets = split_targets(
                    daily_macro_budgets[day], pending_slots, day_multiplicity(spec, day),
                    config, overrides,
                )
                day_budgets[day] = budgets[slot_id(day, meal_type)]
                if meal_type in overrides:
                    pinned_days.append(day)

            carried_descriptions_by_day = {
                day: carried_macros(spec, day, events)[1] for day in cook_days
            }

            try:
                stage_events = await _generate_meal_type_events(
                    meal_type, spec, config, day_budgets, portions, claims,
                    carried_descriptions_by_day, pinned_days,
                    avoid_proteins[-protein_avoid_window:], avoid_recipe_names,
                    note_callback,
                )
            except Exception as exc:
                # One bad meal type must not discard everything else. Free
                # routes fail in ways no amount of retrying fixes (a provider
                # returning an empty completion, a model that can't hit the
                # budget), and losing one meal type must not cost the other
                # three. Every day this stage would have cooked is recorded
                # and skipped; those slots render as "not generated" and
                # their ingredients never reach a shopping list.
                message = f"{type(exc).__name__}: {exc}".split("\n")[0][:300]
                for day in cook_days:
                    failures[slot_id(day, meal_type)] = message
                logger.warning(
                    "%s: generation failed for %s — %s", meal_type, ", ".join(cook_days), message
                )
                if note_callback:
                    note_callback(f"{meal_type}: generation failed — {message}")
                stage_events = {}

            for event in stage_events.values():
                protein = extract_main_protein(event.recipe)
                if protein and protein not in avoid_proteins:
                    avoid_proteins.append(protein)
                if event.recipe.name not in avoid_recipe_names:
                    avoid_recipe_names.append(event.recipe.name)
            events.update(stage_events)

        # Cascade: subtract this meal type's actual per-day consumption from
        # daily_macro_budgets, so the NEXT meal type's split (above) sees the
        # real remaining number. A day whose cook just failed is left
        # untouched — nothing was actually eaten, so nothing is owed.
        for day in spec.days:
            slot = by_id[slot_id(day, meal_type)]
            if slot.mode == MODE_COOK:
                event = events.get(slot.id)
                if event is None:
                    continue
                times = day_multiplicity(spec, day).get(slot.id, 1)
                serving = event.recipe.per_serving_macros
                for key in MACRO_KEYS:
                    daily_macro_budgets[day][key] = max(
                        0.0, daily_macro_budgets[day][key] - serving[key] * times
                    )
            elif slot.mode == MODE_LEFTOVER and slot.source:
                source = by_id.get(slot.source)
                if source is None or source.day == day:
                    # A same-day source's consumption was already folded into
                    # the cook's own subtraction above, via day_multiplicity.
                    continue
                event = events.get(slot.source)
                if event is None:
                    continue
                serving = event.recipe.per_serving_macros
                for key in MACRO_KEYS:
                    daily_macro_budgets[day][key] = max(
                        0.0, daily_macro_budgets[day][key] - serving[key]
                    )

    ordered_events = [events[slot.id] for slot in spec.cook_slots() if slot.id in events]

    # A failed prep session must not fail the week either — same rule as a
    # failed day (see CLAUDE.md), and for the same reason: this runs after
    # every day has already succeeded, so losing the whole plan to one extra
    # call would be the worst possible outcome after a full run.
    sunday_prep_session = None
    try:
        sunday_prep_session = await asyncio.to_thread(
            generate_sunday_prep_session, ordered_events, spec, config
        )
        if sunday_prep_session and note_callback:
            note_callback(
                f"Sunday prep session: {sunday_prep_session.total_active_minutes} active "
                f"min across {len(sunday_prep_session.timeline)} phase(s)"
            )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}".split("\n")[0][:300]
        logger.warning("sunday_prep: generation failed — %s", message)
        if note_callback:
            note_callback(f"Sunday prep session generation failed — {message}")

    return WeekPlan(
        days=spec.days,
        servings_per_meal=spec.servings_per_meal,
        generated_at=datetime.now().isoformat(),
        cook_events=ordered_events,
        slots=spec.slots,
        targets=targets,
        failures=failures,
        sunday_prep_session=sunday_prep_session,
        unique_plants=collect_unique_plants(ordered_events),
    )


async def regenerate_single_day(
    day: str,
    spec: WeekSpec,
    config: dict,
    week_plan: WeekPlan,
    history: Optional[List[dict]] = None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Re-cook just `day`, leaving every other day's cook events untouched.

    Every other day's cook events already live in `week_plan` — a leftover
    slot on `day` only ever points at an *earlier* day, so its source is
    already resolved and doesn't need regenerating; a later day's leftover
    that points *at* `day` keeps pointing at the same slot_id, so it picks up
    the new recipe automatically once that slot_id is replaced below. Neither
    direction needs the rest of the week to be walked again — that's the
    whole difference from `generate_week_plan`.
    """
    if history is None:
        history = await (repository or LocalJSONRepository()).load_history()
    targets = week_targets(spec, config)
    portions = portions_for(spec)
    claims = eaten_on(spec)
    by_slot = dict(week_plan.by_slot())

    # Seeded from history, same as a full week, then extended with every
    # OTHER day's proteins already locked into this plan — `day`'s own
    # (about to be replaced) proteins must not suppress themselves on retry.
    avoid_proteins = recent_main_proteins(history, config)
    # Same idea for recipe names — history's 4-week window plus every OTHER
    # day already locked into this plan; `day`'s own (about to be replaced)
    # recipes must not suppress themselves on retry.
    avoid_recipe_names = recent_recipe_names(history)
    for event in week_plan.cook_events:
        if event.day == day:
            continue
        protein = extract_main_protein(event.recipe)
        if protein and protein not in avoid_proteins:
            avoid_proteins.append(protein)
        if event.recipe.name not in avoid_recipe_names:
            avoid_recipe_names.append(event.recipe.name)

    # Keyed by slot_id, not day — see generate_week_plan, which now records a
    # failure per (day, meal_type) since a single API call no longer always
    # covers the whole day. A day-level regeneration still fails atomically
    # (one call, every cook slot on the day), so every one of them gets the
    # same message.
    cook_slots_today = spec.cook_slots_on(day)
    failures = dict(week_plan.failures)
    try:
        day_events = await _generate_day_events(
            day, spec, config, targets[day], portions, claims, by_slot,
            avoid_proteins, avoid_recipe_names, note_callback,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}".split("\n")[0][:300]
        for slot in cook_slots_today:
            failures[slot.id] = message
        logger.warning("%s: regeneration failed — %s", day, message)
        if note_callback:
            note_callback(f"{day}: regeneration failed — {message}")
        return week_plan.model_copy(update={"failures": failures})

    by_slot.update(day_events)
    for slot in cook_slots_today:
        failures.pop(slot.id, None)
    ordered_events = [by_slot[slot.id] for slot in spec.cook_slots() if slot.id in by_slot]

    # A saved Sunday prep session names specific recipes/ingredients from the
    # OLD plan. If `day` contributed a batch cook either before or after this
    # regeneration, that session may now describe a recipe that no longer
    # exists — a stale prep plan is worse than none, so drop it rather than
    # let the timeline silently disagree with the new recipes. It is not
    # regenerated here: this call is one targeted retry, not a second
    # sunday_prep API call on top of it.
    sunday_prep_session = week_plan.sunday_prep_session
    if sunday_prep_session is not None:
        was_candidate = any(
            event.day == day and event.recipe.prep_notes for event in week_plan.cook_events
        )
        now_candidate = any(event.recipe.prep_notes for event in day_events.values())
        if was_candidate or now_candidate:
            sunday_prep_session = None

    return week_plan.model_copy(
        update={
            "generated_at": datetime.now().isoformat(),
            "cook_events": ordered_events,
            "slots": spec.slots,
            "targets": targets,
            "failures": failures,
            "sunday_prep_session": sunday_prep_session,
            "unique_plants": collect_unique_plants(ordered_events),
        }
    )


async def regenerate_single_meal(
    slot_id: str,
    spec: WeekSpec,
    config: dict,
    week_plan: WeekPlan,
    history: Optional[List[dict]] = None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Re-cook just one meal, leaving every other slot's cook event untouched.

    The narrowest-grained regeneration in the app — `regenerate_single_day`
    still re-splits and re-generates every cook on that day, which is overkill
    for "just redo Tuesday dinner, the rest of the day is fine." Here every
    OTHER slot on `day` (leftover or independently cooked) is treated as
    fixed: its already-locked-in per-serving macros are summed and subtracted
    from the day's target, and whatever budget is left over goes entirely to
    this one meal — divided by how many times it's eaten today, the same rule
    `split_targets` applies to any other flexible slot. One API call, one
    recipe.

    Unlike `carried_macros` (which only knows about leftovers, because a
    full-day generation produces every same-day cook together in one call),
    this also has to treat a sibling COOK slot on the same day as fixed — it
    already has a recipe in `week_plan` that isn't being touched.
    """
    day, _ = parse_slot_id(slot_id)
    slot = spec.by_id().get(slot_id)
    if slot is None or slot.mode != MODE_COOK:
        raise ValueError(f"{slot_label(slot_id)} isn't a cooked meal — nothing to regenerate.")

    if history is None:
        history = await (repository or LocalJSONRepository()).load_history()

    targets = week_targets(spec, config)
    day_target = targets[day]
    portions = portions_for(spec)
    claims = eaten_on(spec)
    by_slot = dict(week_plan.by_slot())
    multiplicity = day_multiplicity(spec, day)

    other: List[Tuple[SlotSpec, CookEvent]] = []
    for other_slot in spec.slots:
        if other_slot.day != day or other_slot.id == slot_id or other_slot.mode == MODE_SKIP:
            continue
        source_id = other_slot.id if other_slot.mode == MODE_COOK else other_slot.source
        if source_id == slot_id:
            # A same-day leftover of THIS meal's own batch — its share is
            # already covered by dividing this meal's budget by multiplicity
            # below, not a separate fixed amount to subtract.
            continue
        event = by_slot.get(source_id)
        if event is None:
            continue
        other.append((other_slot, event))

    other_totals = sum_serving_macros(event for _, event in other)
    other_descriptions: List[str] = []
    for other_slot, event in other:
        serving = event.recipe.per_serving_macros
        origin = (
            f'leftovers of "{event.recipe.name}" (cooked {event.day})'
            if other_slot.mode == MODE_LEFTOVER
            else f'"{event.recipe.name}" (already generated)'
        )
        other_descriptions.append(
            f"{other_slot.meal_type}: {origin} — {serving['calories']:.0f} kcal, "
            f"{serving['protein_g']:.0f}g protein, {serving['net_carbs_g']:.0f}g net carbs, "
            f"{serving['fat_g']:.0f}g fat"
        )

    # Seeded from history, same as regenerate_single_day, then extended with
    # every OTHER slot already locked into this plan — this meal's own
    # (about to be replaced) protein/name must not suppress itself on retry.
    avoid_proteins = recent_main_proteins(history, config)
    avoid_recipe_names = recent_recipe_names(history)
    for event in week_plan.cook_events:
        if event.slot_id == slot_id:
            continue
        protein = extract_main_protein(event.recipe)
        if protein and protein not in avoid_proteins:
            avoid_proteins.append(protein)
        if event.recipe.name not in avoid_recipe_names:
            avoid_recipe_names.append(event.recipe.name)

    protein_avoid_window = planning_rule(config, "protein_avoid_window")
    thread_safe_note = on_calling_loop(note_callback)

    recipes = await asyncio.to_thread(
        generate_day,
        day=day,
        targets=day_target,
        cook_slots=[slot],
        config=config,
        servings_per_meal=spec.servings_per_meal,
        multiplicity=multiplicity,
        carried=other_totals,
        carried_descriptions=other_descriptions,
        avoid_proteins=avoid_proteins[-protein_avoid_window:],
        avoid_recipe_names=avoid_recipe_names,
        progress_note=thread_safe_note,
    )

    claim_ids = claims.get(slot_id, [slot_id])
    last_day_index = max(spec.day_index(parse_slot_id(value)[0]) for value in claim_ids)
    recipe = recipes[slot.meal_type].scale_to_servings(
        portions[slot_id],
        keeps_for_days=last_day_index - spec.day_index(day),
        config=config,
    )
    new_event = CookEvent(
        slot_id=slot_id,
        day=day,
        meal_type=slot.meal_type,
        portions=portions[slot_id],
        style=slot.style,
        cuisine=slot.cuisine,
        eaten_by=claim_ids,
        recipe=recipe,
    )
    by_slot[slot_id] = new_event
    ordered_events = [by_slot[s.id] for s in spec.cook_slots() if s.id in by_slot]

    # Same rule as regenerate_single_day: a saved Sunday prep session names
    # specific recipes from the OLD plan, and this meal may have joined or
    # left the batch-prep candidate set — drop rather than risk a stale plan.
    sunday_prep_session = week_plan.sunday_prep_session
    if sunday_prep_session is not None:
        old_event = week_plan.by_slot().get(slot_id)
        was_candidate = bool(old_event and old_event.recipe.prep_notes)
        now_candidate = bool(new_event.recipe.prep_notes)
        if was_candidate or now_candidate:
            sunday_prep_session = None

    return week_plan.model_copy(
        update={
            "generated_at": datetime.now().isoformat(),
            "cook_events": ordered_events,
            "slots": spec.slots,
            "targets": targets,
            "sunday_prep_session": sunday_prep_session,
            "unique_plants": collect_unique_plants(ordered_events),
        }
    )


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def extract_main_protein(recipe: Recipe) -> Optional[str]:
    """Cheap proxy for a recipe's protein source: its highest-protein
    ingredient. Only meaningful for lunch/dinner, where one dominates."""
    if recipe.meal_type.lower() not in ("lunch", "dinner") or not recipe.ingredients:
        return None
    return max(recipe.ingredients, key=lambda ingredient: ingredient.protein_g).name


async def record_week_history(
    week_plan: WeekPlan,
    repository: Optional[PlanRepository] = None,
    config: Optional[dict] = None,
    days: Optional[List[str]] = None,
) -> None:
    """One history entry per cooked day, so rotation carries across weeks.

    `config` supplies `planning_rules.history_max_entries`; omitted (or
    missing the key) falls back to DEFAULT_PLANNING_RULES's value, same as
    every other planning_rule() read.

    `days` restricts which of `week_plan.days` get a new entry — defaults to
    all of them (a full week's worth). `regenerate_single_day` passes just
    the one day it touched; recording the whole plan there would re-append
    history for six days that were never regenerated, throwing off rotation
    for styles/cuisines/proteins that had nothing to do with this run.
    """
    max_entries = planning_rule(config, "history_max_entries")
    repository = repository or LocalJSONRepository()
    history = await repository.load_history()
    generated_at = week_plan.generated_at
    target_days = week_plan.days if days is None else days

    for day in target_days:
        events = [event for event in week_plan.cook_events if event.day == day]
        if not events:
            continue
        proteins = [
            protein
            for protein in (extract_main_protein(event.recipe) for event in events)
            if protein
        ]
        history.append(
            {
                "day_of_week": day,
                "generated_at": generated_at,
                "cuisine": next((event.cuisine for event in events if event.cuisine), None),
                "styles": {event.meal_type: event.style for event in events if event.style},
                "main_proteins": proteins,
                "recipe_names": [event.recipe.name for event in events],
            }
        )

    await repository.save_history(history[-max_entries:])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def print_week_summary(week_plan: WeekPlan) -> None:
    print("\nWeek Plan")
    print("=========")
    if week_plan.failures:
        print("\n!! Some meals failed to generate — re-run to retry them:")
        for key, error in week_plan.failures.items():
            print(f"   {slot_label(key)}: {error}")
    by_slot = week_plan.by_slot()
    slots_by_day: Dict[str, List[SlotSpec]] = {}
    for slot in week_plan.slots:
        slots_by_day.setdefault(slot.day, []).append(slot)

    for day in week_plan.days:
        totals = week_plan.day_slot_macros(day)
        target = week_plan.targets[day]
        print(f"\n{day}")
        for slot in slots_by_day.get(day, []):
            if slot.mode == MODE_SKIP:
                print(f"  {slot.meal_type:<10} —")
                continue
            source_id = slot.id if slot.mode == MODE_COOK else slot.source
            event = by_slot.get(source_id)
            if event is None:
                print(f"  {slot.meal_type:<10} (unresolved)")
                continue
            if slot.mode == MODE_COOK:
                tag = f" [cook {event.portions} portions]" if event.portions > week_plan.servings_per_meal else ""
                print(f"  {slot.meal_type:<10} {event.recipe.name}{tag}")
            else:
                print(f"  {slot.meal_type:<10} {event.recipe.name} (leftovers from {event.day})")
        print(
            f"  → {totals['calories']:.0f}/{target['calories']:.0f} kcal · "
            f"P {totals['protein_g']:.0f}/{target['protein_g']:.0f} · "
            f"C {totals['net_carbs_g']:.0f}/{target['net_carbs_g']:.0f} · "
            f"F {totals['fat_g']:.0f}/{target['fat_g']:.0f}"
        )

    session = week_plan.sunday_prep_session
    if session:
        print(
            f"\nSunday Prep Session ({session.total_active_minutes} active / "
            f"{session.total_passive_minutes} passive min)"
        )
        print("=" * 26)
        for phase in session.timeline:
            print(f"  {phase.name} — {phase.active_minutes} active / {phase.passive_minutes} passive min")
            if phase.description:
                print(f"    {phase.description}")
        if session.aggregated_ingredients:
            print("  Aggregated prep:")
            for item, note in session.aggregated_ingredients.items():
                print(f"    {item}: {note}")


def print_shopping_windows(week_plan: WeekPlan, windows: List[ShoppingWindow]) -> None:
    for window in windows:
        events = week_plan.events_on_days(window.days)
        print(f"\n{window.label}")
        print("=" * len(window.label))
        if not events:
            print("  (nothing cooked in this window)")
            continue
        shopping_list = aggregate_cook_events(events, window.days)
        print(format_shopping_list_text(shopping_list, cook_events=events))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Weekly Meal Planner CLI")
    parser.add_argument(
        "--config", default=DEFAULT_STORAGE_PATHS.config, help="Path to config JSON file"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override config's openrouter_model for this run.",
    )
    parser.add_argument(
        "--week-start",
        default=None,
        help="Day the week starts on (default: config week_start_day).",
    )
    parser.add_argument(
        "--servings",
        type=int,
        default=None,
        help="People cooked for per meal (default: config serving_rules.servings_per_meal).",
    )
    parser.add_argument(
        "--shop-days",
        default=None,
        help=(
            "Comma-separated days you shop, e.g. 'Sunday,Wednesday'. Shopping "
            "windows run from each shop day to the next (default: config shopping.shop_days)."
        ),
    )
    parser.add_argument(
        "--leftover-lunches",
        action="store_true",
        help="Set every lunch to leftovers of the previous day's dinner.",
    )
    parser.add_argument(
        "--save-shopping-list",
        action="store_true",
        help="Write each window's shopping list to shopping_list.md.",
    )
    parser.add_argument(
        "--use-cached-plan",
        action="store_true",
        help=(
            f"Load the week from {DEFAULT_STORAGE_PATHS.week_plan} instead of calling "
            "OpenRouter (for iterating on the shopping list without API calls)."
        ),
    )
    return parser.parse_args(argv)


async def run_cli(args: argparse.Namespace, repository: PlanRepository) -> None:
    """The CLI's actual work, async so it can await the repository.

    Split from `main()` so there is exactly one `asyncio.run` in the process
    (in `main`) and everything below it is ordinary async code — the shape the
    future backend expects, and the reason storage calls are awaited here
    rather than bridged individually.
    """
    config = await load_config_with_models(repository)
    if args.model:
        config["openrouter_model"] = args.model
    config = apply_training_adjustments(config)
    spec = default_week_spec(config, args.week_start, args.servings)

    if args.leftover_lunches:
        from week import autofill_leftovers

        spec = autofill_leftovers(spec, "lunch", "dinner")

    if args.use_cached_plan:
        print(f"Loading cached week plan from {repository.paths.week_plan}...", flush=True)
        cached = await repository.load_week_plan()
        if cached is None:
            print(f"No cached week plan found ({repository.paths.week_plan}). Generate one first.")
            raise SystemExit(1)
        week_plan = WeekPlan.model_validate(cached)
    else:
        history = await repository.load_history()
        spec = resolve_auto_choices(spec, config, history)

        errors = validate_week(spec, config)
        if errors:
            print("Week plan is not valid:")
            for error in errors:
                print(f"  - {error}")
            raise SystemExit(1)

        model = resolve_planner_model(config)
        cook_days = len({slot.day for slot in spec.cook_slots()})
        print(
            f"Generating {len(spec.days)}-day plan ({len(spec.cook_slots())} cooks "
            f"across {cook_days} days) using {model}...",
            flush=True,
        )

        def report(day: str, cooks: int) -> None:
            print(f"  {day}: {cooks} recipe(s)..." if cooks else f"  {day}: leftovers only", flush=True)

        week_plan = await generate_week_plan(
            spec,
            config,
            history,
            progress_callback=report,
            note_callback=lambda message: print(f"    {message}", flush=True),
            repository=repository,
        )

        await repository.save_week_plan(week_plan.model_dump())
        await record_week_history(week_plan, repository, config)

    print_week_summary(week_plan)

    shop_days = (
        [day.strip() for day in args.shop_days.split(",") if day.strip()]
        if args.shop_days
        else config["shopping"]["shop_days"]
    )
    windows = shopping_windows(week_plan.days, shop_days)

    print("\n\nShopping Lists")
    print("==============")
    print_shopping_windows(week_plan, windows)

    if args.save_shopping_list:
        sections = []
        for window in windows:
            events = week_plan.events_on_days(window.days)
            if not events:
                continue
            shopping_list = aggregate_cook_events(events, window.days)
            sections.append(
                format_shopping_list_markdown(
                    shopping_list, cook_events=events, title=window.label
                )
            )
        with open("shopping_list.md", "w") as f:
            f.write("\n\n".join(sections))
        print("\nSaved shopping lists to shopping_list.md", flush=True)


def main() -> None:
    """Sync entry point: parse args, pick a repository, run the async CLI.

    `--config` still names a file because the only repository today is the
    local one; a backend implementation would be selected here instead and
    nothing below this line would change.
    """
    configure_logging()
    args = parse_args()
    repository = LocalJSONRepository(config_path=args.config)
    run_sync(run_cli(args, repository))


if __name__ == "__main__":
    main()
-e 

=== File: ./shopping.py ===
import re
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from week import PERISHABLE_DAY_GAP, PERISHABLE_DEPARTMENTS

# planner.py imports this module, so importing Recipe/CookEvent back from
# planner would be circular; they're only needed for type hints.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planner import CookEvent, Recipe

# Ordered (specific -> general) keyword -> department lookup. Matched as whole
# words against the ingredient's *head* — the part before the first comma —
# because models write "Garlic, minced" and "Pork shoulder, lean, cubed", and
# matching the whole string put minced garlic in Meat & Poultry (the "mince"
# keyword) on a real run.
DEPARTMENT_KEYWORDS = [
    # Ambient/bottled goods, matched before the fresh departments they'd
    # otherwise be dragged into: "Beef broth" is not meat, "Apple cider
    # vinegar" is not produce, "Coconut milk" is not dairy, "Fish sauce" is
    # not seafood, "Tomato paste" is not produce. All observed on real runs.
    ("Pantry", [
        # The "<animal> broth" pairs are spelled out because longest-match
        # would otherwise give "chicken broth" to Meat & Poultry on "chicken".
        "chicken broth", "beef broth", "vegetable broth", "fish broth",
        "chicken stock", "beef stock", "vegetable stock", "bone broth",
        "broth", "stock", "vinegar", "wine", "soy sauce", "fish sauce",
        "coconut milk", "coconut cream", "tomato paste", "tomato passata",
        "passata", "honey", "molasses", "gochujang", "miso", "curry paste",
        "canned tomato", "chopped tomato", "olive oil", "coconut oil",
        "avocado oil", "sesame oil", "mct oil", "vegetable oil", "ghee",
        "tamarind paste", "palm sugar", "brown sugar", "protein powder",
        "protein isolate",
    ]),
    # Before Dairy, or "peanut butter" matches "butter". Before Produce, or
    # "pumpkin seeds" matches "pea".
    ("Nuts, Seeds & Spreads", [
        "peanut butter", "almond butter", "cashew butter", "nut butter",
        "tahini", "peanut", "walnut", "almond", "cashew", "pecan",
        "pistachio", "hazelnut", "macadamia", "pumpkin seed", "sunflower seed",
        "sesame seed", "chia seed", "flaxseed", "flax seed", "hemp heart",
        "hemp seed", "nut", "seed",
    ]),
    # "pepper" is deliberately NOT a keyword here — it put "Red bell pepper"
    # in Herbs & Spices. The pepper *spices* are listed individually instead.
    ("Herbs & Spices", [
        "salt", "black pepper", "white pepper", "cayenne", "cayenne pepper",
        "peppercorn", "red pepper flake", "chili flake", "chilli flake",
        "cumin", "paprika", "oregano", "basil", "thyme",
        "rosemary", "cinnamon", "turmeric", "chili powder", "chilli powder",
        "garlic powder", "onion powder", "bay leaf", "parsley", "cilantro",
        "coriander", "dill", "sage", "nutmeg", "clove", "cardamom",
        "chives", "mint", "vanilla extract", "spice", "seasoning",
    ]),
    ("Fish & Seafood", [
        "salmon", "tuna", "shrimp", "prawn", "cod", "tilapia", "halibut",
        "trout", "sardine", "anchovy", "crab", "lobster", "mussel", "clam",
        "scallop", "mackerel", "kipper", "fish",
    ]),
    ("Meat & Poultry", [
        "chicken", "beef", "pork", "turkey", "lamb", "bacon", "sausage",
        "ground beef", "steak", "ham", "duck", "veal", "mince",
    ]),
    ("Dairy & Eggs", [
        "milk", "cheese", "yogurt", "yoghurt", "butter", "cream", "egg",
        "mozzarella", "cheddar", "parmesan", "ricotta", "feta",
    ]),
    ("Grains & Bakery", [
        "rice", "pasta", "bread", "oats", "oatmeal", "flour", "quinoa",
        "tortilla", "noodle", "cereal", "bun", "bagel", "cracker",
    ]),
    ("Produce", [
        "apple", "banana", "spinach", "kale", "lettuce", "tomato", "onion",
        "garlic", "pepper bell", "bell pepper", "broccoli", "cauliflower",
        "carrot", "potato", "zucchini", "courgette", "cucumber", "avocado",
        "lemon", "lime", "berry", "berries", "mushroom", "celery", "cabbage",
        "squash", "sweet potato", "asparagus", "green bean", "pea",
        "blueberry", "raspberry", "strawberry", "blackberry", "cranberry",
        "cherry", "orange", "grape", "peach", "pear", "mango", "melon", "eggplant",
        "aubergine", "okra", "pumpkin", "artichoke", "brussels sprout",
        "aubergine", "scallion", "spring onion", "shallot", "leek", "ginger",
        "greens", "chili", "chilli", "radish", "beet", "fennel", "turnip",
    ]),
]

DEFAULT_DEPARTMENT = "Pantry"

# Never appears on a shopping list — you don't buy it, and a "Water: 300g"
# line is noise that makes the rest look untrustworthy.
NON_SHOPPING_INGREDIENTS = {"water", "ice", "cold water", "hot water", "tap water"}

# Matched as whole words against the ingredient head -> average grams per
# unit, for shopping-list display only. Ingredient.quantity_g and all macro
# math stay in grams — this just renders the total as "6 eggs" instead of
# "300g" for items a shopper actually buys by the piece.
#
# Whole-word matching on the head is what stops "Eggplant, cubed" rendering as
# "10 eggs" and "Butter, for frying eggs" as "1 egg" — both happened on a real
# run under plain substring matching.
COUNT_UNIT_INGREDIENTS = {
    "egg": 50,
    "garlic clove": 5,
}

# Words describing how an ingredient is cut or presented. Stripped before
# combining, so "Cucumber, diced" and "Cucumber, sliced" become one line
# instead of sending you to buy cucumber twice.
#
# STATE_QUALIFIERS are the opposite: they change what a gram *means*, so they
# are pulled out of the full name (not just the head) and folded back into the
# combining key. Without this, splitting on the first comma silently discarded
# them and merged "Quinoa, cooked" with "Quinoa, dry" — two very different
# weights of the same purchase, which would understate the shop.
#
# "raw" and "uncooked" are excluded on purpose: they describe the *default*
# state, so treating them as qualifiers split "Red bell pepper" from "Red bell
# pepper (raw)" into two lines for the same purchase. Their absence still
# separates correctly, because the non-default state ("cooked") is the one
# that carries a qualifier.
STATE_QUALIFIERS = {
    "cooked", "dry", "dried", "canned", "tinned", "frozen",
}

PREP_QUALIFIERS = {
    "raw", "uncooked",
    "baby", "chopped", "cubed", "crushed", "diced", "finely", "fresh",
    "freshly", "grated", "grilled", "halved", "julienned", "large", "lean",
    "medium", "minced", "peeled", "quartered", "roasted", "sauteed",
    "sautéed", "shredded", "sliced", "small", "thin", "thinly", "toasted",
    "torn", "trimmed", "washed", "whole",
}


class ShoppingItem(BaseModel):
    name: str = Field(..., description="Ingredient name")
    total_amount_g: float = Field(..., ge=0, description="Combined quantity in grams")
    nova_group: int = Field(..., ge=1, le=4)
    department: str = Field(..., description="Grocery department/category")
    latest_cook_offset: int = Field(
        default=0,
        ge=0,
        description="Days between this shopping trip and the last meal that uses the item",
    )

    @property
    def buy_late(self) -> bool:
        """A perishable that isn't cooked until several days into the window.

        Multi-day shopping windows are the point of this planner, but they
        mean fresh fish bought on day 1 for a day 5 cook. Flagged rather than
        rescheduled — buying it on a second trip is the shopper's call.
        """
        return (
            self.department in PERISHABLE_DEPARTMENTS
            and self.latest_cook_offset >= PERISHABLE_DAY_GAP
        )


class ShoppingList(BaseModel):
    categories: Dict[str, List[ShoppingItem]] = Field(default_factory=dict)

    def items(self) -> List[ShoppingItem]:
        return [item for department in sorted(self.categories) for item in self.categories[department]]


def strip_parentheticals(name: str) -> str:
    """Remove bracketed asides, including an unclosed trailing one.

    Must run before the comma split: models write "Egg yolks (large, from
    free-range eggs)", and splitting first left the dangling "Egg yolks (large"
    on the shopping list.
    """
    cleaned = re.sub(r"\([^()]*\)", " ", name)
    while re.search(r"\([^()]*\)", cleaned):
        cleaned = re.sub(r"\([^()]*\)", " ", cleaned)
    cleaned = re.sub(r"[(\[].*$", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def ingredient_head(name: str) -> str:
    """The part before the first comma — the thing itself, minus preparation.

    Models write "Pork shoulder, lean, cubed" and "Butter, for frying eggs".
    Everything after the first comma describes handling, not what you buy, and
    matching against it is what produced miscategorised and miscounted lines.
    """
    return strip_parentheticals(name).split(",")[0].strip()


def contains_word(haystack: str, phrase: str) -> bool:
    """Whole-word/phrase containment, so 'egg' misses 'eggplant'.

    Handles the plural forms English actually uses: a bare +s missed
    "potatoes" (from "potato") and "berries" (from "berry"), which dropped
    both into the default department on a real run.
    """
    stem = re.escape(phrase)
    forms = [stem, stem + "s", stem + "es"]
    if phrase.endswith("y"):
        forms.append(re.escape(phrase[:-1]) + "ies")
    return re.search(rf"\b(?:{'|'.join(forms)})\b", haystack) is not None


# Different names for the same purchase. Applied to the combining key after
# normalisation, so "Garlic cloves" and "Garlic" become one line rather than
# two entries in the same department.
NAME_ALIASES = {
    "clove garlic": "garlic",
    "onion spring": "scallion",
    "coriander fresh": "cilantro",
}


def singularize(word: str) -> str:
    """Crude plural stripper, used only to build combining keys.

    "Carrot"/"Carrots" and "Garlic clove"/"Garlic cloves" are the same
    purchase and must land on one line. Only ever applied to the key, never
    to what the shopper reads, so an odd stem does no visible harm.
    """
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    # "-es" is only the plural marker after a sibilant or -o ("potatoes",
    # "boxes"). Applying it everywhere turned "cloves" into "clov", which kept
    # "Garlic cloves" and "Garlic" on separate shopping lines.
    if word.endswith("es") and word[:-2].endswith(("o", "x", "z", "ch", "sh", "s")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def states_in(name: str) -> List[str]:
    """State qualifiers anywhere in the full name, deduped and sorted."""
    words = set(re.findall(r"[a-z]+", name.lower()))
    return sorted(words & STATE_QUALIFIERS)


def normalize_name(name: str) -> str:
    """Combining key: head minus cut words, word-sorted, plus any state words.

    Word-sorting is what collapses "Fresh lemon juice" and "Lemon juice,
    fresh" — models phrase the same purchase both ways within one week. The
    state suffix is what keeps "Quinoa, dry" and "Quinoa, cooked" apart.
    """
    head = ingredient_head(name).lower()
    words = [
        singularize(word)
        for word in re.findall(r"[a-z]+", head)
        if word not in PREP_QUALIFIERS and word not in STATE_QUALIFIERS
    ]
    base = " ".join(sorted(words)) if words else head.strip()
    base = NAME_ALIASES.get(base, base)
    states = states_in(name)
    return f"{base} [{' '.join(states)}]" if states else base


def display_name(name: str) -> str:
    """What the shopper reads: head minus cut words, state kept in parentheses."""
    head = ingredient_head(name)
    words = [
        word
        for word in head.split()
        if word.lower().strip(".") not in PREP_QUALIFIERS
        and word.lower().strip(".") not in STATE_QUALIFIERS
    ]
    cleaned = " ".join(words).strip(" -,")
    if not cleaned:
        cleaned = ingredient_head(name)
    cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else name
    states = states_in(name)
    return f"{cleaned} ({', '.join(states)})" if states else cleaned


def categorize_department(ingredient_name: str) -> str:
    """Department of the longest matching keyword, not the first one found.

    Specificity beats list order, which removes a whole class of fragile
    ordering bugs seen on real runs: "garlic cloves" matched the spice
    "clove" before the produce "garlic"; "cauliflower rice" matched "rice";
    "beef broth" matched "beef". The longer phrase is the more specific
    description in every one of those cases. Ties fall back to list order, so
    the specific -> general ordering still decides genuine ambiguity.
    """
    head = ingredient_head(ingredient_name).lower()
    best_department = DEFAULT_DEPARTMENT
    best_length = 0
    for department, keywords in DEPARTMENT_KEYWORDS:
        for keyword in keywords:
            if len(keyword) > best_length and contains_word(head, keyword):
                best_department = department
                best_length = len(keyword)
    return best_department


def round_ingredient_quantity(name: str, quantity_g: float, department: str) -> float:
    """Snap a scaled ingredient quantity to an amount a shopper can actually buy.

    A portion trim or batch multiply leaves quantities like 516g — precise but
    unbuyable. Meat/fish and anything already sizeable (>=100g) round to the
    nearest 50g, the way it's sold; mid-size amounts round to the nearest 10g;
    small spice/seasoning amounts (Herbs & Spices, <20g) round to the nearest
    1g, since 2g of turmeric and 5g are meaningfully different; everything
    else under 20g rounds to the nearest 5g.

    Floored at one increment rather than letting a rounded-down trace ingredient
    hit 0g: `Ingredient.quantity_g` requires `gt=0`, and a positive amount that
    rounds to nothing is still on the recipe, just too small to weigh precisely.
    """
    if department in ("Meat & Poultry", "Fish & Seafood") or quantity_g >= 100:
        increment = 50.0
    elif quantity_g >= 20:
        increment = 10.0
    elif department == "Herbs & Spices":
        increment = 1.0
    else:
        increment = 5.0
    rounded = round(quantity_g / increment) * increment
    return rounded if rounded > 0 else increment


def aggregate_recipes(
    recipes: Sequence["Recipe"], offsets: Optional[Sequence[int]] = None
) -> ShoppingList:
    """Combine recipes into one departmentalised list.

    `offsets` is a parallel sequence giving each recipe's cook day as a day
    count from the start of the shopping window; an ingredient's offset is the
    latest cook that uses it, which is what decides the perishable warning.
    """
    if offsets is None:
        offsets = [0] * len(recipes)

    aggregated: Dict[str, dict] = {}

    for recipe, offset in zip(recipes, offsets):
        for ingredient in recipe.ingredients:
            if normalize_name(ingredient.name) in NON_SHOPPING_INGREDIENTS:
                continue
            key = normalize_name(ingredient.name)
            if key not in aggregated:
                aggregated[key] = {
                    "name": display_name(ingredient.name),
                    "total_amount_g": 0.0,
                    "nova_group": ingredient.nova_group,
                    "latest_cook_offset": offset,
                }
            aggregated[key]["total_amount_g"] += ingredient.quantity_g
            aggregated[key]["nova_group"] = max(
                aggregated[key]["nova_group"], ingredient.nova_group
            )
            aggregated[key]["latest_cook_offset"] = max(
                aggregated[key]["latest_cook_offset"], offset
            )

    categories: Dict[str, List[ShoppingItem]] = {}
    for item in aggregated.values():
        department = categorize_department(item["name"])
        shopping_item = ShoppingItem(
            name=item["name"],
            total_amount_g=round(item["total_amount_g"], 1),
            nova_group=item["nova_group"],
            department=department,
            latest_cook_offset=item["latest_cook_offset"],
        )
        categories.setdefault(department, []).append(shopping_item)

    for items in categories.values():
        items.sort(key=lambda i: i.name.lower())

    return ShoppingList(categories=categories)


PLANT_DEPARTMENTS = {"Produce", "Herbs & Spices", "Nuts, Seeds & Spreads"}


def collect_unique_plants(cook_events: Sequence["CookEvent"]) -> List[str]:
    """Unique plant-based ingredients across a week's cook events.

    Diversity, not shopping quantity: an ingredient counts once no matter how
    many recipes use it, keyed by the same `normalize_name()` used to combine
    shopping-list lines so "Spinach" and "Baby spinach, washed" aren't counted
    twice.
    """
    plants: Dict[str, str] = {}
    for event in cook_events:
        for ingredient in event.recipe.ingredients:
            if categorize_department(ingredient.name) not in PLANT_DEPARTMENTS:
                continue
            key = normalize_name(ingredient.name)
            plants.setdefault(key, display_name(ingredient.name))
    return sorted(plants.values())


def aggregate_cook_events(
    cook_events: Sequence["CookEvent"], window_days: Optional[Sequence[str]] = None
) -> ShoppingList:
    """Shopping list for a set of cook events, offsets derived from their days.

    Grouping is by **cook day**, never eating day: a Sunday batch eaten on
    Wednesday belongs entirely to the Sunday trip, so its ingredients are never
    split across two shopping lists.
    """
    days = list(window_days) if window_days else []
    offsets = [days.index(event.day) if event.day in days else 0 for event in cook_events]
    return aggregate_recipes([event.recipe for event in cook_events], offsets)


def format_grams(amount_g: float) -> str:
    if amount_g >= 1000:
        return f"{amount_g / 1000:.2f}kg"
    return f"{amount_g:g}g"


def format_quantity(name: str, amount_g: float) -> str:
    head = ingredient_head(name).lower()
    for keyword, grams_per_unit in COUNT_UNIT_INGREDIENTS.items():
        if contains_word(head, keyword):
            count = max(1, round(amount_g / grams_per_unit))
            unit = keyword if count == 1 else f"{keyword}s"
            return f"{count} {unit}"
    return format_grams(amount_g)


def cook_plan_lines(cook_events: Sequence["CookEvent"]) -> List[str]:
    """What this trip's shopping is actually for: each cook and the meals it
    covers. Ingredient totals below already include every portion."""
    lines = []
    for event in cook_events:
        meals = len(event.eaten_by)
        covers = (
            f"{meals} meals"
            if meals > 1
            else "1 meal"
        )
        lines.append(
            f"{event.day} {event.meal_type}: {event.recipe.name} — "
            f"{event.portions} portions, covers {covers}"
        )
    return lines


def _item_line(item: ShoppingItem) -> str:
    note = "  ← buy fresh closer to the day" if item.buy_late else ""
    return f"{item.name}: {format_quantity(item.name, item.total_amount_g)}{note}"


def format_shopping_list_text(
    shopping_list: ShoppingList, cook_events: Optional[Sequence["CookEvent"]] = None
) -> str:
    lines = []
    if cook_events:
        lines.append("Cooking this window (quantities below already include every portion):")
        for line in cook_plan_lines(cook_events):
            lines.append(f"  - {line}")
        lines.append("")
    for department in sorted(shopping_list.categories):
        lines.append(f"{department}:")
        for item in shopping_list.categories[department]:
            lines.append(f"  - {_item_line(item)}")
    return "\n".join(lines)


def format_shopping_list_markdown(
    shopping_list: ShoppingList,
    cook_events: Optional[Sequence["CookEvent"]] = None,
    title: str = "Shopping List",
) -> str:
    lines = [f"# {title}", ""]
    if cook_events:
        lines.append("## Cooking this window")
        lines.append("_Quantities below already include every portion._")
        lines.append("")
        for line in cook_plan_lines(cook_events):
            lines.append(f"- {line}")
        lines.append("")
    for department in sorted(shopping_list.categories):
        lines.append(f"## {department}")
        for item in shopping_list.categories[department]:
            note = " _(buy fresh closer to the day)_" if item.buy_late else ""
            lines.append(
                f"- [ ] {item.name} — {format_quantity(item.name, item.total_amount_g)}{note}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_shopping_list_keep(shopping_list: ShoppingList) -> str:
    """One item per line, no bullets/markdown/blank lines. Google Keep turns
    each line of pasted text into its own checkbox item inside a list-type
    note — bullets or blank lines would just become extra junk items.

    The perishable note rides along on the item's own line rather than getting
    a line of its own: this is the copy you read *in the shop*, which is the
    one place the warning can still change what you put in the basket, and a
    separate line would become a checkbox for a thing you can't buy.
    """
    lines = []
    for department in sorted(shopping_list.categories):
        lines.append(department)
        for item in shopping_list.categories[department]:
            note = " (buy fresh closer to the day)" if item.buy_late else ""
            lines.append(
                f"{item.name}: {format_quantity(item.name, item.total_amount_g)}{note}"
            )
    return "\n".join(lines)
-e 

=== File: ./export_menu.py ===
"""Formats a generated week into a printable menu — Markdown text and a
magazine-style PDF (CSIRO Total Wellbeing Diet inspired: restrained dark-ink
typography, a teal-header day-by-day grid, a tickable prep checklist, a
single hairline-ruled recipe page per meal grouped by meal type, and a
catalog-style shopping list).

Both walk `WeekPlan.slots` (one `SlotSpec` per eating slot) resolved against
`WeekPlan.by_slot()` (cook events), the same source `WeekPlan.day_slot_macros`
reads — not `PlannerState`/`SlotView`, so this module has no UI dependency
and works the same from the NiceGUI drawer today or a future CLI flag.

`build_week_menu_pdf` needs `reportlab` (pure Python, no system libraries —
unlike `weasyprint`, which needs Cairo/Pango, it installs cleanly into this
project's venv with a plain `pip install`); it's a hard requirement (see
`requirements.txt`), and `ui_app.py` already needs it for the "Download PDF
Menu" button to exist, so importing it at module level costs nothing the app
doesn't already pay.
"""

import io
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from planner import CookEvent, Recipe, SundayPrepSession, WeekPlan
from shopping import ShoppingItem, aggregate_cook_events, format_quantity, format_shopping_list_text
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, SlotSpec, humanize, slot_label

# --------------------------------------------------------------------------
# Palette + styles
#
# Every other paragraph style in this file derives from one of the four
# base styles below rather than `getSampleStyleSheet()` directly, so the
# whole document's look lives in one place instead of being re-decided
# per section.
# --------------------------------------------------------------------------

ACCENT_DARK = colors.HexColor("#134e4a")
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d1d5db")
ROW_TINT = colors.HexColor("#f9fafb")

PAGE_MARGIN = 18 * mm
CONTENT_WIDTH = letter[0] - 2 * PAGE_MARGIN

_base = getSampleStyleSheet()

STYLES = {
    # Large display heading (page 1 title, "Shopping List", checklist title).
    # Regular weight, not bold — Helvetica has no true light cut, and at this
    # size a regular weight already reads as an editorial display face rather
    # than a shouty banner, which is what the CSIRO reference uses throughout.
    "Heading1": ParagraphStyle(
        "MenuHeading1",
        parent=_base["Title"],
        fontName="Helvetica",
        fontSize=27,
        leading=31,
        textColor=INK,
        alignment=0,
        spaceAfter=10,
    ),
    "Heading2": ParagraphStyle(
        "MenuHeading2",
        parent=_base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=17,
        textColor=INK,
        spaceBefore=12,
        spaceAfter=6,
    ),
    "BodyText": ParagraphStyle(
        "MenuBodyText",
        parent=_base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14.5,
        textColor=INK,
    ),
    "SubText": ParagraphStyle(
        "MenuSubText",
        parent=_base["BodyText"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=MUTED,
    ),
}

STYLES.update(
    {
        "Eyebrow": ParagraphStyle(
            "MenuEyebrow",
            parent=STYLES["SubText"],
            fontName="Helvetica-Bold",
            textColor=MUTED,
            spaceAfter=2,
        ),
        # Recipe title — same restrained, regular-weight treatment as
        # Heading1 but sized for a page that's mostly ingredients/method
        # below it, matching the CSIRO reference's plain dark-grey recipe
        # headings (no colour, no bold).
        "RecipeTitle": ParagraphStyle(
            "MenuRecipeTitle", parent=STYLES["Heading1"], fontSize=19, leading=23, spaceAfter=10
        ),
        # In-flow section label ("Breakfast Meals") placed once, directly
        # above the first recipe of that meal type — not a full divider
        # page. Sized close to a recipe title since it reads as the same
        # kind of heading, one level up.
        "CategoryLabel": ParagraphStyle(
            "MenuCategoryLabel",
            parent=STYLES["Heading1"],
            fontSize=22,
            leading=26,
            spaceAfter=16,
        ),
        "SectionHeading": ParagraphStyle(
            "MenuSectionHeading",
            parent=STYLES["Heading2"],
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=12,
            spaceAfter=4,
        ),
        "GridHeader": ParagraphStyle(
            "MenuGridHeader",
            parent=STYLES["BodyText"],
            fontSize=9.5,
            leading=12,
            textColor=colors.white,
            alignment=1,
            fontName="Helvetica-Bold",
        ),
        "GridLabel": ParagraphStyle(
            "MenuGridLabel", parent=STYLES["BodyText"], fontSize=9.5, leading=12, fontName="Helvetica-Bold"
        ),
        "GridCell": ParagraphStyle("MenuGridCell", parent=STYLES["BodyText"], fontSize=8, leading=10.5),
        "GridTotal": ParagraphStyle(
            "MenuGridTotal",
            parent=STYLES["SubText"],
            fontSize=7.5,
            leading=9.5,
            fontName="Helvetica-Bold",
            textColor=ACCENT_DARK,
        ),
        "ChecklistText": ParagraphStyle(
            "MenuChecklistText",
            parent=STYLES["BodyText"],
            fontSize=10,
            leading=13.5,
            fontName="Helvetica-Bold",
        ),
        "ChecklistNote": ParagraphStyle(
            "MenuChecklistNote", parent=STYLES["SubText"], fontSize=8.5, leading=11, spaceBefore=1
        ),
        # The closing "Makes N servings. Each serving provides..." line —
        # a plain left-aligned sentence, not a boxed/centred footer, so it
        # reads as prose the way the CSIRO reference's "Makes 1 serve." does.
        "ServesLine": ParagraphStyle(
            "MenuServesLine",
            parent=STYLES["BodyText"],
            fontSize=9.5,
            leading=13,
            textColor=MUTED,
        ),
    }
)


def _slot_recipe(by_slot: Dict[str, CookEvent], slot: SlotSpec) -> Optional[Recipe]:
    source_id = slot.id if slot.mode == MODE_COOK else slot.source
    event = by_slot.get(source_id)
    return event.recipe if event else None


def _slot_entry(week_plan: WeekPlan, by_slot: Dict[str, CookEvent], slot: SlotSpec) -> dict:
    """One eating slot's exportable info, shared by both output formats.

    `macros` is `None` for a skipped or ungenerated slot — the caller decides
    how to render "nothing to show" for its format rather than this function
    picking blank text vs. a blank table cell.
    """
    if slot.mode == MODE_SKIP:
        return {"meal_type": slot.meal_type, "dish": "Skipped", "macros": None, "note": None}
    recipe = _slot_recipe(by_slot, slot)
    if recipe is None:
        source_id = slot.id if slot.mode == MODE_COOK else slot.source
        return {
            "meal_type": slot.meal_type,
            "dish": "Not generated",
            "macros": None,
            # Keyed by slot_id now, not day (see WeekPlan.failures) — a
            # leftover slot's own day may have generated fine even though its
            # source cook failed, so the lookup goes through source_id.
            "note": week_plan.failures.get(source_id),
        }
    note = (
        f"leftover from {slot_label(slot.source, short=True)}"
        if slot.mode == MODE_LEFTOVER
        else None
    )
    return {
        "meal_type": slot.meal_type,
        "dish": recipe.name,
        "macros": recipe.per_serving_macros,
        "note": note,
    }


def _day_entries(week_plan: WeekPlan, by_slot: Dict[str, CookEvent], day: str) -> List[dict]:
    return [_slot_entry(week_plan, by_slot, slot) for slot in week_plan.slots if slot.day == day]


def _macro_text(macros: dict) -> str:
    return (
        f"{macros['calories']:.0f} kcal · {macros['protein_g']:.0f}g P · "
        f"{macros['net_carbs_g']:.0f}g C · {macros['fat_g']:.0f}g F"
    )


def format_week_menu_markdown(week_plan: WeekPlan) -> str:
    """The whole week as Markdown — one section per day, one line per meal."""
    by_slot = week_plan.by_slot()
    lines = ["# Weekly Menu"]
    if week_plan.generated_at:
        lines.append(f"_Generated {week_plan.generated_at[:16].replace('T', ' ')}_")
    lines.append("")

    for day in week_plan.days:
        lines.append(f"## {day}")
        for entry in _day_entries(week_plan, by_slot, day):
            text = f"**{entry['meal_type'].title()}** — {entry['dish']}"
            if entry["note"]:
                text += f" ({entry['note']})"
            if entry["macros"]:
                text += f" · {_macro_text(entry['macros'])}"
            lines.append(f"- {text}")
        totals = week_plan.day_slot_macros(day)
        lines.append(f"- **Day total** — {_macro_text(totals)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _grid_meal_types(week_plan: WeekPlan) -> List[str]:
    """Meal types in first-seen slot order — both the summary grid's column
    order and the recipe section order.

    Not a fixed constant: a week's slots are the source of truth for which
    meal types actually appear, so a config with a non-default `meal_types`
    list (or no snack slots at all) still produces the right columns.
    """
    seen: List[str] = []
    for slot in week_plan.slots:
        if slot.meal_type not in seen:
            seen.append(slot.meal_type)
    return seen


def _summary_cell(entry: Optional[dict]) -> Paragraph:
    if entry is None:
        return Paragraph("", STYLES["GridCell"])

    text = entry["dish"]
    if entry["note"]:
        text += f" ({entry['note']})"
    if entry["macros"] is None:
        return Paragraph(f"<i>{escape(text)}</i>", STYLES["GridCell"])
    return Paragraph(escape(text), STYLES["GridCell"])


def _summary_table(week_plan: WeekPlan, by_slot: Dict[str, CookEvent]) -> Table:
    """Page 1: a weekly-at-a-glance grid — days down the rows, meal types
    across the columns, so each row reads like one day of a diary
    rather than one meal's history across the week.

    Deliberately doesn't repeat per-meal macros here — that detail lives on
    each recipe's own page. This page answers "what am I eating this week"
    at a glance, not "what's in it".
    """
    meal_types = _grid_meal_types(week_plan)
    entries_by_day = {
        day: {entry["meal_type"]: entry for entry in _day_entries(week_plan, by_slot, day)}
        for day in week_plan.days
    }

    header = [Paragraph("", STYLES["GridHeader"])]
    header += [Paragraph(meal_type.title(), STYLES["GridHeader"]) for meal_type in meal_types]
    header.append(Paragraph("Daily Total", STYLES["GridHeader"]))
    rows = [header]

    for day in week_plan.days:
        row = [Paragraph(day, STYLES["GridLabel"])]
        row += [_summary_cell(entries_by_day[day].get(meal_type)) for meal_type in meal_types]
        row.append(Paragraph(escape(_macro_text(week_plan.day_slot_macros(day))), STYLES["GridTotal"]))
        rows.append(row)

    label_width = 20 * mm
    total_width = 46 * mm
    meal_width = (CONTENT_WIDTH - label_width - total_width) / len(meal_types)
    col_widths = [label_width] + [meal_width] * len(meal_types) + [total_width]

    table = Table(rows, colWidths=col_widths, repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_DARK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    # No inner grid — a bottom rule per row plus alternating tints carries
    # the structure instead, which is what makes the CSIRO-style grid read
    # as one calm sheet rather than a spreadsheet.
    for row_index in range(1, len(rows)):
        style_commands.append(("LINEBELOW", (0, row_index), (-1, row_index), 0.5, colors.lightgrey))
        if row_index % 2 == 0:
            style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), ROW_TINT))
    table.setStyle(TableStyle(style_commands))
    return table


def _prep_checklist_page(session: SundayPrepSession) -> list:
    """Page 2 (when present): a tickable checklist for the Sunday batch-prep
    session, one checkbox per timeline phase, only rendered when a week
    actually has one.
    """
    subtitle_parts = [
        f"{session.total_active_minutes} min active",
        f"{session.total_passive_minutes} min passive",
    ]
    if session.meals_included:
        subtitle_parts.append(f"covers {', '.join(session.meals_included)}")

    flow: list = [
        Paragraph("Batch Cooking &amp; Preparation Checklist", STYLES["Heading1"]),
        Paragraph(escape(" · ".join(subtitle_parts)), STYLES["SubText"]),
        Spacer(1, 10),
    ]

    phase_items = []
    for phase in session.timeline:
        timing = f"{phase.name} — {phase.active_minutes} min active"
        if phase.passive_minutes:
            timing += f" / {phase.passive_minutes} min passive"
        cell = [Paragraph(escape(timing), STYLES["ChecklistText"])]
        if phase.description:
            cell.append(Paragraph(escape(phase.description), STYLES["ChecklistNote"]))
        phase_items.append(ListItem(cell, value="[ ]", spaceBefore=8))

    flow.append(
        ListFlowable(
            phase_items,
            bulletType="bullet",
            leftIndent=20,
            bulletFontName="Helvetica-Bold",
            bulletFontSize=11,
        )
    )

    if session.aggregated_ingredients:
        flow.append(Paragraph("Aggregated Prep", STYLES["SectionHeading"]))
        agg_items = [
            ListItem(
                Paragraph(f"<b>{escape(item)}</b> — {escape(note)}", STYLES["BodyText"]),
                value="[ ]",
            )
            for item, note in session.aggregated_ingredients.items()
        ]
        flow.append(ListFlowable(agg_items, bulletType="bullet", leftIndent=20))

    flow.append(PageBreak())
    return flow


def _ingredient_line(ingredient) -> str:
    return f"{ingredient.quantity_g:.0f}g {ingredient.name}"


def _recipe_meta_line(event: CookEvent) -> str:
    parts = [
        f"{event.day} · {event.meal_type.title()}",
        f"{event.portions} serving{'s' if event.portions != 1 else ''}",
    ]
    if event.recipe.prep_time_minutes:
        parts.append(f"{event.recipe.prep_time_minutes} min prep")
    if event.style:
        parts.append(humanize(event.style))
    if event.cuisine:
        parts.append(humanize(event.cuisine))
    return " · ".join(parts)


def _feeds_note(event: CookEvent) -> Optional[str]:
    """Which other slots this batch also covers, for a bulk-cooked recipe."""
    others = [value for value in event.eaten_by if value != event.slot_id]
    if not others:
        return None
    return "Also feeds: " + ", ".join(slot_label(value) for value in others)


def _serves_line(event: CookEvent) -> str:
    """Builds the "Makes N servings. Each serving provides..." sentence,
    with markup already applied — the caller doesn't need to escape it
    further since every interpolated value is a number or a word this
    module controls, none of it user-supplied recipe text.
    """
    macros = event.recipe.per_serving_macros
    portions = event.portions
    lead = f"Makes {portions} serving{'s' if portions != 1 else ''}."
    return (
        f"<b>{lead}</b> Each serving provides {macros['calories']:.0f} kcal, "
        f"{macros['protein_g']:.0f}g protein, {macros['net_carbs_g']:.0f}g carbs, "
        f"{macros['fat_g']:.0f}g fat."
    )


def _ingredient_table(recipe: Recipe) -> Table:
    """Ingredients as a hairline-ruled list — one row per ingredient, a thin
    grey rule under every row but the last — instead of a bulleted list, so
    a recipe page reads like a printed ledger rather than a slide deck.
    """
    rows = [[Paragraph(escape(_ingredient_line(ingredient)), STYLES["BodyText"])] for ingredient in recipe.ingredients]
    table = Table(rows, colWidths=[CONTENT_WIDTH])
    style_commands = [
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_index in range(len(rows) - 1):
        style_commands.append(("LINEBELOW", (0, row_index), (-1, row_index), 0.5, RULE))
    table.setStyle(TableStyle(style_commands))
    return table


def _recipe_page(event: CookEvent) -> list:
    """One recipe's dedicated page: eyebrow meta, title, a single-column
    hairline-ruled ingredient list, numbered method, and a closing
    "Makes N servings" sentence — no photo (none exist for a generated
    recipe), so the page stays single-column full-width rather than
    reserving dead space for an image that isn't there.
    """
    recipe = event.recipe
    flow: list = [
        Paragraph(escape(_recipe_meta_line(event)), STYLES["Eyebrow"]),
        Paragraph(escape(recipe.name), STYLES["RecipeTitle"]),
    ]

    feeds = _feeds_note(event)
    if feeds:
        flow.append(Paragraph(escape(feeds), STYLES["SubText"]))
        flow.append(Spacer(1, 6))

    flow.append(_ingredient_table(recipe))

    flow.append(Paragraph("Method", STYLES["SectionHeading"]))
    flow.append(
        ListFlowable(
            [
                ListItem(Paragraph(escape(step), STYLES["BodyText"]), spaceBefore=5)
                for step in recipe.instructions
            ],
            bulletType="1",
            leftIndent=16,
            bulletFontSize=9.5,
        )
    )

    if recipe.prep_notes:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph(escape(recipe.prep_notes), STYLES["SubText"]))

    flow.append(Spacer(1, 12))
    flow.append(Paragraph(_serves_line(event), STYLES["ServesLine"]))
    flow.append(PageBreak())
    return flow


def _recipes_by_category(week_plan: WeekPlan) -> List[tuple]:
    """Cook events grouped by meal type, in `_grid_meal_types` order, each
    bucket keeping the week-order the events already arrive in.
    """
    order = _grid_meal_types(week_plan)
    buckets: Dict[str, List[CookEvent]] = {meal_type: [] for meal_type in order}
    for event in week_plan.cook_events:
        buckets.setdefault(event.meal_type, []).append(event)
    return [(meal_type, buckets[meal_type]) for meal_type in order if buckets.get(meal_type)]


def _department_item_table(items: List[ShoppingItem], columns: int = 2) -> Table:
    """One department's items tiled into a fixed number of columns, each row
    hairline-ruled the same way a recipe's ingredient list is — one visual
    language for "things to check off" everywhere in the document."""
    cells = []
    for item in items:
        text = f"[ ]  {escape(item.name)} — {escape(format_quantity(item.name, item.total_amount_g))}"
        if item.buy_late:
            text += "<br/><font size=7 color='#6b7280'>buy fresh closer to the day</font>"
        cells.append(Paragraph(text, STYLES["BodyText"]))

    rows = [cells[i : i + columns] for i in range(0, len(cells), columns)]
    if rows and len(rows[-1]) < columns:
        rows[-1] += [Paragraph("", STYLES["BodyText"])] * (columns - len(rows[-1]))

    col_width = CONTENT_WIDTH / columns
    table = Table(rows, colWidths=[col_width] * columns)
    style_commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for row_index in range(len(rows) - 1):
        style_commands.append(("LINEBELOW", (0, row_index), (-1, row_index), 0.5, RULE))
    table.setStyle(TableStyle(style_commands))
    return table


def _shopping_list_pages(week_plan: WeekPlan) -> list:
    """The whole week's shopping, grouped by department in a
    catalog-style grid, followed by a plain-text page built from the same
    `format_shopping_list_text` the CLI and shopping-drawer copy buttons
    use — so the wording can't drift between the styled table and the copy
    someone pastes into their phone at the shop.
    """
    shopping_list = aggregate_cook_events(week_plan.cook_events, week_plan.days)

    flow: list = [
        Paragraph("Shopping List", STYLES["Heading1"]),
        Paragraph(
            escape(f"Everything for the week, grouped by department — {len(shopping_list.items())} items."),
            STYLES["SubText"],
        ),
        Spacer(1, 10),
    ]

    for department in sorted(shopping_list.categories):
        flow.append(Paragraph(escape(department), STYLES["SectionHeading"]))
        flow.append(_department_item_table(shopping_list.categories[department]))

    flow.append(PageBreak())
    flow.append(Paragraph("Plain-Text Version", STYLES["Heading2"]))
    flow.append(Paragraph("For copying onto your phone before you shop.", STYLES["SubText"]))
    flow.append(Spacer(1, 6))
    for line in format_shopping_list_text(shopping_list, week_plan.cook_events).splitlines():
        if not line.strip():
            flow.append(Spacer(1, 4))
        else:
            flow.append(Paragraph(escape(line), STYLES["SubText"]))

    return flow


def build_week_menu_pdf(week_plan: WeekPlan) -> bytes:
    """The whole week as a magazine-style PDF: a page-1 summary grid, an
    optional Sunday prep checklist, one page per recipe grouped into a
    section per meal type, then a department-grouped shopping list.

    Returns bytes rather than writing to disk: the caller (the NiceGUI
    shopping drawer today) hands this straight to `ui.download`, and nothing
    here needs to know whether it's a browser response or a file on disk.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Weekly Menu",
    )

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(doc.pagesize[0] - PAGE_MARGIN, 10 * mm, f"Page {doc.page}")
        canvas.drawString(PAGE_MARGIN, 10 * mm, "Weekly Menu")
        canvas.restoreState()

    by_slot = week_plan.by_slot()

    # --- Page 1: weekly summary grid ---
    story: list = [Paragraph("Weekly Menu", STYLES["Heading1"])]
    if week_plan.generated_at:
        story.append(
            Paragraph(f"Generated {week_plan.generated_at[:16].replace('T', ' ')}", STYLES["SubText"])
        )
    story.append(Spacer(1, 10))
    story.append(_summary_table(week_plan, by_slot))

    if week_plan.failures:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Not generated", STYLES["SectionHeading"]))
        for key, error in week_plan.failures.items():
            story.append(Paragraph(escape(f"{slot_label(key)}: {error}"), STYLES["SubText"]))

    story.append(PageBreak())

    # --- Page 2 (optional): Sunday batch-prep checklist ---
    if week_plan.sunday_prep_session:
        story.extend(_prep_checklist_page(week_plan.sunday_prep_session))

    # --- One page per recipe actually being cooked, grouped by meal type.
    # The category label ("Breakfast Meals") sits once, directly above the
    # first recipe of that meal type, rather than on its own divider page —
    # each recipe already ends in a PageBreak, so the category still starts
    # on a fresh page without spending a whole page on just its name. ---
    for meal_type, events in _recipes_by_category(week_plan):
        for index, event in enumerate(events):
            recipe_flow = _recipe_page(event)
            if index == 0:
                recipe_flow = [
                    Paragraph(escape(f"{meal_type.title()} Meals"), STYLES["CategoryLabel"])
                ] + recipe_flow
            story.extend(recipe_flow)

    # --- Shopping list, grouped by department ---
    if week_plan.cook_events:
        story.extend(_shopping_list_pages(week_plan))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
-e 

=== File: ./week.py ===
"""Week-level planning primitives.

The unit of planning is no longer "a day of recipes" but a grid of **eating
slots** (one per day x meal_type) laid over a smaller set of **cook events**.
A slot either cooks something new, eats leftovers of an earlier cook slot, or
is skipped. That single distinction is what makes bulk cooking ("cook Sunday,
eat it Wednesday") and flexible shopping windows expressible at all:

- A cook slot's portions are derived from how many slots claim it, so the
  batch size can never silently disagree with the meals it has to cover.
- Shopping windows group recipes by **cook day, not eating day** — a Sunday
  batch eaten on Wednesday is bought on the Sunday trip, not split across two.

Everything in this module is deterministic and API-free: the whole week is
fully resolved (styles, cuisines, portions, windows) before a single token is
generated, so the UI can preview exactly what it is about to ask for.
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

MODE_COOK = "cook"
MODE_LEFTOVER = "leftover"
MODE_SKIP = "skip"
MODES = [MODE_COOK, MODE_LEFTOVER, MODE_SKIP]

# Sentinel used in the UI dropdowns for "let the planner decide". Stored as
# None on the model so callers never have to special-case the string.
AUTO = "auto"

DEFAULT_MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]
DEFAULT_SERVINGS_PER_MEAL = 2

# Departments where a long gap between shopping and cooking is a real problem.
# Used only to annotate the shopping list, never to change quantities.
PERISHABLE_DEPARTMENTS = {
    "Fish & Seafood",
    "Produce",
    "Meat & Poultry",
    "Dairy & Eggs",
}

# Fallbacks for config.json's "inventory_rules" object, used when a caller has
# no config (or an older config.json predates this section). The canonical
# values now live in config.json; these are what the app used before that
# section existed.
DEFAULT_INVENTORY_RULES = {
    # Cooked food keeps ~3-4 days refrigerated, so a leftover eaten 4+ days
    # after its cook day is at the edge — flagged in the grid and reflected
    # in the recipe's storage note rather than silently planned.
    "fridge_safe_days": 4,
    "perishable_day_gap": 3,
}

# `shopping.py`'s `ShoppingItem.buy_late` still reads this module constant
# directly (it's a plain computed property with no config in scope at
# evaluation time) — dynamic wiring there would need `aggregate_cook_events`
# to thread a config through to `ShoppingItem` construction, which is outside
# this refactor. config.json's inventory_rules.perishable_day_gap is the
# value to edit; keep this constant in sync with it by hand until that's done.
PERISHABLE_DAY_GAP = DEFAULT_INVENTORY_RULES["perishable_day_gap"]


def humanize(value: Optional[str]) -> str:
    return value.replace("_", " ") if value else ""


def week_days(config: dict, week_start: Optional[str] = None) -> List[str]:
    """The week in cooking order, rotated so it begins on week_start.

    Generation walks this order, and leftovers may only point backwards
    along it, so "day 1" is whatever the user considers the start of their
    shopping week rather than a hardcoded Monday.
    """
    days = list(config["weekly_schedule"].keys())
    start = week_start or config["week_start_day"]
    if start in days:
        index = days.index(start)
        days = days[index:] + days[:index]
    return days


def week_date_range(days: List[str], generated_at: Optional[str] = None) -> Tuple[date, date]:
    """The calendar span `days` covers, anchored on `generated_at` (or today).

    Nothing in this codebase stores an actual calendar date — a week is a
    rotation of weekday *names* (see `week_days`) — so a banner that wants
    real dates has to derive them. The anchor's weekday tells us how far into
    the 7-day span it falls, which pins the whole week without needing a
    stored start date: a Wednesday generation still produces a Monday start
    if that's what `days[0]` is.
    """
    anchor = datetime.fromisoformat(generated_at).date() if generated_at else date.today()
    target_weekday = datetime.strptime(days[0], "%A").weekday()
    start = anchor - timedelta(days=(anchor.weekday() - target_weekday) % 7)
    return start, start + timedelta(days=6)


def meal_types(config: dict) -> List[str]:
    return config["meal_types"]


def styles_for(config: dict, meal_type: str) -> Dict[str, str]:
    """style key -> prose description handed to the model."""
    return config["meal_styles"].get(meal_type, {})


def all_style_keys(config: dict) -> List[str]:
    """Union of every meal type's styles, for the grid's single Style column.

    st.data_editor's dropdown options are per-column, not per-row, so the
    column offers every style and validate_week() rejects ones that don't
    belong to the row's meal type.
    """
    keys: List[str] = []
    for meal_type in meal_types(config):
        for key in styles_for(config, meal_type):
            if key not in keys:
                keys.append(key)
    return keys


def slot_id(day: str, meal_type: str) -> str:
    return f"{day}:{meal_type}"


def parse_slot_id(value: str) -> Tuple[str, str]:
    """Inverse of `slot_id`: 'Monday:dinner' -> ('Monday', 'dinner').

    The only place a slot id's `:` should get split apart — callers that need
    just the day (`span_days`, `generate_week_plan`) still go through this
    rather than a bare `.split(":")`, so a future change to the id format has
    one place to change.
    """
    day, _, meal_type = value.partition(":")
    return day, meal_type


def slot_label(value: str, short: bool = False) -> str:
    """A slot id as prose: 'Monday:dinner' -> 'Monday dinner' / 'Mon dinner'.

    `humanize` only swaps underscores, so it leaves the colon in a slot id
    sitting in the middle of a sentence. Anything that names a slot to the
    user goes through here instead.
    """
    day, meal_type = parse_slot_id(value)
    return f"{day[:3] if short else day} {meal_type}".strip()


class SlotSpec(BaseModel):
    """One eating slot: what the user wants at this day/meal, pre-generation."""

    day: str
    meal_type: str
    mode: str = MODE_COOK
    style: Optional[str] = None
    cuisine: Optional[str] = None
    source: Optional[str] = Field(
        default=None,
        description="slot_id of the cook slot this eats leftovers of (mode=leftover)",
    )
    extra_portions: int = Field(
        default=0,
        ge=0,
        description="Spare portions to freeze, on top of the slots claiming this cook",
    )

    @property
    def id(self) -> str:
        return slot_id(self.day, self.meal_type)


class WeekSpec(BaseModel):
    days: List[str]
    servings_per_meal: int = DEFAULT_SERVINGS_PER_MEAL
    slots: List[SlotSpec]

    def by_id(self) -> Dict[str, SlotSpec]:
        return {slot.id: slot for slot in self.slots}

    def cook_slots(self) -> List[SlotSpec]:
        return [slot for slot in self.slots if slot.mode == MODE_COOK]

    def cook_slots_on(self, day: str) -> List[SlotSpec]:
        return [slot for slot in self.cook_slots() if slot.day == day]

    def day_index(self, day: str) -> int:
        return self.days.index(day) if day in self.days else -1


def default_week_spec(
    config: dict,
    week_start: Optional[str] = None,
    servings_per_meal: Optional[int] = None,
) -> WeekSpec:
    """A fresh grid with every slot on config's per-meal-type default mode."""
    days = week_days(config, week_start)
    defaults = config["week_defaults"]
    servings = servings_per_meal or config["serving_rules"]["servings_per_meal"]

    slots = [
        SlotSpec(
            day=day,
            meal_type=meal_type,
            mode=defaults.get(meal_type, MODE_COOK),
        )
        for day in days
        for meal_type in meal_types(config)
    ]
    return WeekSpec(days=days, servings_per_meal=servings, slots=slots)


def autofill_leftovers(spec: WeekSpec, meal_type: str, source_meal_type: str) -> WeekSpec:
    """Point every slot of meal_type at the previous day's source_meal_type cook.

    The common "lunch is last night's dinner" pattern, as a one-click button
    rather than 7 manual dropdown selections. Day 1 is left alone — it has no
    previous day to inherit from.
    """
    by_id = spec.by_id()
    updated = []
    for slot in spec.slots:
        if slot.meal_type != meal_type:
            updated.append(slot)
            continue
        index = spec.day_index(slot.day)
        if index <= 0:
            updated.append(slot)
            continue
        candidate = slot_id(spec.days[index - 1], source_meal_type)
        source = by_id.get(candidate)
        if source is None or source.mode != MODE_COOK:
            updated.append(slot)
            continue
        updated.append(slot.model_copy(update={"mode": MODE_LEFTOVER, "source": candidate}))
    return spec.model_copy(update={"slots": updated})


def next_day_slot_id(spec: WeekSpec, day: str, meal_type: str) -> Optional[str]:
    """`meal_type`'s slot on the day after `day`, or None past the week's end.

    "The day after" is the next entry in `spec.days`, not the next calendar
    day: the week is rotated by `week_start_day`, so the last day has no
    following slot to link to even though a Sunday follows a Saturday.
    """
    index = spec.day_index(day)
    if index < 0 or index + 1 >= len(spec.days):
        return None
    return slot_id(spec.days[index + 1], meal_type)


def leftover_meal_type_error(source_meal_type: str, target_meal_type: str) -> Optional[str]:
    """Why a leftover from `source_meal_type` can't feed `target_meal_type`.

    Generation now runs one meal type at a time across the whole week (see
    planner.generate_week_plan), in priority order breakfast, dinner, lunch,
    snack — a leftover source has to have already been generated by the time
    its target's turn comes up, or the source recipe doesn't exist yet. Same
    meal type always works (that batch's cook and its leftover both wait for
    the same generation stage). The only cross-type link the priority order
    actually supports is dinner feeding lunch. Anything else — breakfast
    feeding lunch, lunch feeding dinner, either feeding snack, etc. — would
    ask the model to carry over a recipe from later in the run.
    """
    if source_meal_type == target_meal_type:
        return None
    if source_meal_type == "dinner" and target_meal_type == "lunch":
        return None
    return (
        f"a {humanize(target_meal_type)} can only eat leftovers of the same meal type "
        "or of a dinner — generation runs one meal type at a time, in an order that "
        "doesn't otherwise guarantee the source is cooked yet."
    )


def leftover_link_error(spec: WeekSpec, target_id: str, source_id: str) -> Optional[str]:
    """Why `target_id` can't eat `source_id`'s leftovers, or None if it can.

    Checked up front rather than by running `validate_week` over the mutated
    week: that returns every problem in the grid, and a one-click action needs
    a single sentence about the two meals the user just picked. The rules
    themselves are the same ones `validate_week` enforces, plus a repeat-click
    guard, so anything this accepts still passes validation afterwards.
    """
    by_id = spec.by_id()
    target = by_id.get(target_id)
    source = by_id.get(source_id)

    if source is None or target is None:
        return "That meal isn't part of this week."
    if source.id == target.id:
        return f"{slot_label(target_id)} can't be its own leftover."
    if source.mode != MODE_COOK:
        return f"{slot_label(source_id)} isn't cooked — leftovers need a cook to come from."
    meal_type_error = leftover_meal_type_error(source.meal_type, target.meal_type)
    if meal_type_error:
        return f"{slot_label(target_id)}: {meal_type_error}"
    if spec.day_index(source.day) > spec.day_index(target.day):
        return (
            f"{source.day} comes after {target.day} — leftovers only travel forwards "
            "through the week."
        )
    if target.mode == MODE_LEFTOVER and target.source == source_id:
        return f"{slot_label(target_id)} already eats this one."

    # Converting a cook into a leftover would strand anything already pointing
    # at it, which validate_week rejects as "source isn't a cooked meal". Say
    # so here instead of silently breaking the other end of that chain.
    dependants = [
        slot.id for slot in spec.slots if slot.mode == MODE_LEFTOVER and slot.source == target_id
    ]
    if dependants:
        return (
            f"{slot_label(target_id)} already feeds "
            f"{', '.join(slot_label(value) for value in dependants)} — repoint those first."
        )
    return None


def link_leftover(spec: WeekSpec, target_id: str, source_id: str) -> WeekSpec:
    """A copy of `spec` with `target_id` set to eat `source_id`'s leftovers.

    Call `leftover_link_error` first — this applies the edit unconditionally.
    `extra_portions` is cleared because it only means anything on a cook slot.
    """
    updated = [
        slot.model_copy(
            update={"mode": MODE_LEFTOVER, "source": source_id, "extra_portions": 0}
        )
        if slot.id == target_id
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def claim_counts(spec: WeekSpec) -> Dict[str, int]:
    """How many eating slots each cook slot has to feed, itself included."""
    counts = {slot.id: 1 for slot in spec.cook_slots()}
    for slot in spec.slots:
        if slot.mode == MODE_LEFTOVER and slot.source in counts:
            counts[slot.source] += 1
    return counts


def portions_for(spec: WeekSpec) -> Dict[str, int]:
    """Total person-portions each cook slot must yield.

    Derived, never entered by hand: (meals it covers x household size) plus any
    deliberate extras to freeze. This is why the grid has no "batch multiplier"
    — the batch size *is* the number of slots pointing at it.
    """
    by_id = spec.by_id()
    return {
        cook_id: claims * spec.servings_per_meal + by_id[cook_id].extra_portions
        for cook_id, claims in claim_counts(spec).items()
    }


def eaten_on(spec: WeekSpec) -> Dict[str, List[str]]:
    """cook slot id -> every slot id that eats it, in week order."""
    order = {slot.id: index for index, slot in enumerate(spec.slots)}
    claims: Dict[str, List[str]] = {slot.id: [slot.id] for slot in spec.cook_slots()}
    for slot in spec.slots:
        if slot.mode == MODE_LEFTOVER and slot.source in claims:
            claims[slot.source].append(slot.id)
    for slot_ids in claims.values():
        slot_ids.sort(key=lambda value: order.get(value, 0))
    return claims


def validate_week(spec: WeekSpec, config: dict) -> List[str]:
    """Everything that would make generation nonsensical, as plain messages.

    Returned rather than raised so the UI can show all problems at once and
    keep the Generate button disabled until the grid is coherent.
    """
    errors: List[str] = []
    by_id = spec.by_id()
    cuisines = config["cuisines"]
    cuisine_meal_types = config["cuisine_meal_types"]

    for slot in spec.slots:
        label = f"{slot.day} {slot.meal_type}"

        if slot.mode not in MODES:
            errors.append(f"{label}: unknown mode '{slot.mode}'.")
            continue

        if slot.mode == MODE_LEFTOVER:
            if not slot.source:
                errors.append(f"{label}: set to leftover but no source meal chosen.")
            else:
                source = by_id.get(slot.source)
                if source is None:
                    errors.append(f"{label}: source '{slot.source}' is not a slot in this week.")
                elif source.mode != MODE_COOK:
                    errors.append(
                        f"{label}: source '{slot_label(slot.source)}' isn't a cooked meal — "
                        "leftovers can only come from a slot set to cook."
                    )
                elif leftover_meal_type_error(source.meal_type, slot.meal_type):
                    errors.append(f"{label}: {leftover_meal_type_error(source.meal_type, slot.meal_type)}")
                elif spec.day_index(source.day) > spec.day_index(slot.day):
                    errors.append(
                        f"{label}: eats leftovers from {source.day}, which is later in the "
                        "week — leftovers can only come from an earlier or same day."
                    )
                elif source.id == slot.id:
                    errors.append(f"{label}: cannot be its own leftover source.")

        if slot.mode == MODE_COOK and slot.style:
            allowed = styles_for(config, slot.meal_type)
            if slot.style not in allowed:
                errors.append(
                    f"{label}: style '{humanize(slot.style)}' isn't a {slot.meal_type} style. "
                    f"Valid: {', '.join(humanize(key) for key in allowed) or 'none configured'}."
                )

        if slot.mode == MODE_COOK and slot.cuisine:
            if slot.cuisine not in cuisines:
                errors.append(f"{label}: cuisine '{humanize(slot.cuisine)}' is not in config cuisines.")
            elif slot.meal_type not in cuisine_meal_types:
                errors.append(
                    f"{label}: cuisine themes only apply to "
                    f"{', '.join(cuisine_meal_types)} — clear the cuisine here."
                )

        if slot.mode != MODE_COOK and slot.extra_portions:
            errors.append(f"{label}: extra portions only apply to a slot set to cook.")

    if not spec.cook_slots():
        errors.append("Nothing to cook: at least one slot must be set to cook.")

    return errors


def week_warnings(spec: WeekSpec, config: Optional[dict] = None) -> List[str]:
    """Non-blocking notes — things that are legal but probably not intended."""
    fridge_safe_days = (
        config["inventory_rules"]["fridge_safe_days"]
        if config
        else DEFAULT_INVENTORY_RULES["fridge_safe_days"]
    )
    warnings: List[str] = []
    counts = claim_counts(spec)
    by_id = spec.by_id()

    for cook_id, claims in counts.items():
        slot = by_id[cook_id]
        if claims >= 5:
            warnings.append(
                f"{slot.day} {slot.meal_type} feeds {claims} meals — that's a lot of "
                "repeats of one recipe, and it has to keep for "
                f"{span_days(spec, cook_id)} days."
            )
        span = span_days(spec, cook_id)
        if span >= fridge_safe_days:
            warnings.append(
                f"{slot.day} {slot.meal_type} is eaten up to {span} days after cooking — "
                "at or past safe fridge storage, so plan to freeze the later portions."
            )

    skipped = [slot for slot in spec.slots if slot.mode == MODE_SKIP]
    by_day: Dict[str, int] = {}
    for slot in skipped:
        by_day[slot.day] = by_day.get(slot.day, 0) + 1
    for day, count in by_day.items():
        if count >= 3:
            warnings.append(f"{day} has {count} skipped meals — its macro targets will be hard to hit.")

    return warnings


def span_days(spec: WeekSpec, cook_id: str) -> int:
    """Days between cooking and the last meal that eats it.

    Public because editing the week changes it: re-pointing a leftover moves
    the last meal a batch has to survive to, which is what decides whether its
    storage note says "refrigerate" or "freeze the rest".
    """
    claims = eaten_on(spec).get(cook_id, [])
    if not claims:
        return 0
    cook_index = spec.day_index(parse_slot_id(cook_id)[0])
    last_index = max(spec.day_index(parse_slot_id(value)[0]) for value in claims)
    return last_index - cook_index


class ShoppingWindow(BaseModel):
    """One shopping trip: the day you shop and the cook days it has to cover."""

    shop_day: str
    days: List[str]
    shop_ahead: bool = Field(
        default=False,
        description="True when this window's food must be bought before the week starts",
    )

    @property
    def label(self) -> str:
        span = self.days[0] if len(self.days) == 1 else f"{self.days[0]}–{self.days[-1]}"
        if self.shop_ahead:
            return f"Before {self.days[0]} · covers {span}"
        return f"Shop {self.shop_day} · covers {span}"


def shopping_windows(days: List[str], shop_days: List[str]) -> List[ShoppingWindow]:
    """Partition the week at the days you actually shop.

    Day 1 is always an implicit boundary: if you don't shop on it, the days
    before your first real trip still need buying, and that leading window is
    flagged shop_ahead so the UI can say "buy this before the week starts"
    rather than silently attaching it to the wrong trip.
    """
    if not days:
        return []

    indices = sorted({days.index(day) for day in shop_days if day in days} | {0})
    windows = []
    for position, start in enumerate(indices):
        end = indices[position + 1] if position + 1 < len(indices) else len(days)
        span = days[start:end]
        windows.append(
            ShoppingWindow(
                shop_day=days[start],
                days=span,
                shop_ahead=days[start] not in shop_days,
            )
        )
    return windows


def window_for_day(windows: List[ShoppingWindow], day: str) -> Optional[ShoppingWindow]:
    for window in windows:
        if day in window.days:
            return window
    return None
-e 

=== File: ./repository.py ===
"""Storage boundary for everything the planner reads and writes.

The planner used to `open()` and `json.load()` its own files inline, which
meant the file layout was baked into the business logic in a dozen places. All
of that now goes through a `PlanRepository`, so swapping local JSON for a
backend service is a matter of writing one more subclass — no caller changes.

**Every method is `async` on purpose, including the local file
implementation.** The interface is written for the future backend (which will
receive asynchronous webhook pushes and must not block an event loop), not for
today's filesystem: making the local implementation sync "because files are
fast" would put an `await` boundary in exactly the wrong place and force every
caller to change again later. `LocalJSONRepository` therefore does its blocking
`open()`/`json` work in a worker thread via `asyncio.to_thread`, so awaiting it
genuinely yields to the loop rather than just wrapping a blocking call in a
coroutine.

The repository deliberately deals in plain dicts/lists, never in `WeekPlan` or
`WeekSpec`: `planner` imports this module, so importing planner's models back
here would be a cycle. Callers do their own `WeekPlan.model_validate(...)`,
which also keeps schema validation a business-logic concern rather than a
storage one.
"""

import abc
import asyncio
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, List, Optional, TypeVar

DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_HISTORY_FILE = "meal_history.json"
DEFAULT_WEEK_PLAN_FILE = "week_plan.json"
DEFAULT_RECIPE_CATALOG_FILE = "recipes_master.json"
DEFAULT_MODELS_FILE = "models.json"
DEFAULT_WHFOODS_FILE = "whfoods.json"

T = TypeVar("T")


@dataclass(frozen=True)
class StoragePaths:
    """The on-disk filename for every JSON file the app persists.

    The single source of truth for these names — before this existed,
    `ui_app.py` and `planner.py` each redeclared "config.json"/"week_plan.json"
    as their own module constants, and the two copies were free to drift from
    whatever `LocalJSONRepository` actually defaulted to. Callers that need a
    path (a CLI `--help` string, a "loading from X" message) read it off a
    `LocalJSONRepository`'s `.paths` instead of naming the file themselves.

    There is no `favorites` entry: favoriting doesn't have its own file, it's
    an `is_favorite` flag on an entry in `recipe_catalog`
    (`recipes_master.json`) — adding a path here that nothing ever reads or
    writes would just be a second, misleading name for that same file.
    """

    config: str = DEFAULT_CONFIG_FILE
    history: str = DEFAULT_HISTORY_FILE
    week_plan: str = DEFAULT_WEEK_PLAN_FILE
    recipe_catalog: str = DEFAULT_RECIPE_CATALOG_FILE
    models: str = DEFAULT_MODELS_FILE
    whfoods: str = DEFAULT_WHFOODS_FILE


def recipe_content_key(recipe: dict) -> str:
    """Identity for "is this the same recipe" purposes: name + ingredient
    composition, not object identity or a generated id.

    This is what makes favoriting idempotent across regenerations — cooking
    the same dish again next month (same name, same grams of each
    ingredient) resolves to the same catalog entry rather than a duplicate,
    while a same-named dish with different ingredients is treated as a
    genuinely different recipe. Quantities are rounded to 2dp before hashing
    so float noise from portion trimming can't split one recipe into two
    entries.
    """
    name = (recipe.get("name") or "").strip().lower()
    ingredients = sorted(
        (
            (ingredient.get("name") or "").strip().lower(),
            round(float(ingredient.get("quantity_g") or 0), 2),
        )
        for ingredient in recipe.get("ingredients", [])
    )
    payload = json.dumps([name, ingredients], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class PlanRepository(abc.ABC):
    """Everything the planner needs to persist, as an async interface.

    Implementations must tolerate missing data the way the local files always
    have: an absent history is an empty list, an absent week plan is None. A
    missing *config* is an error — there is no sensible default for it, and
    silently planning against `{}` would be worse than failing loudly.
    """

    @abc.abstractmethod
    async def load_config(self) -> dict:
        """Targets, dietary rules, styles, cuisines. Raises if unavailable."""

    @abc.abstractmethod
    async def load_models_config(self) -> dict:
        """Model selection and LLM call parameters (models.json).

        Unlike `load_config`, a missing file is not an error: this data is
        supplemental defaults (which model, which base URL, which timeout),
        every one of which already has an in-code fallback, so a fresh
        install with no `models.json` yet must plan exactly as it did before
        this file existed, not fail to start. Implementations return `{}`
        when the file is absent.
        """

    @abc.abstractmethod
    async def load_whfoods(self) -> List[str]:
        """Names of the nutrient-dense whole foods in whfoods.json, flattened
        out of their category grouping.

        Supplemental, like `load_models_config`: a fresh install or an older
        checkout without whfoods.json must still plan exactly as it did
        before this file existed, so implementations return `[]` when the
        file is absent rather than raising.
        """

    @abc.abstractmethod
    async def load_history(self) -> List[dict]:
        """Past days, oldest first. Empty when nothing has been generated yet.

        This is what seeds style/cuisine rotation and the recent-protein list,
        so an empty result is a valid cold start, not a failure.
        """

    @abc.abstractmethod
    async def save_history(self, history: List[dict]) -> None:
        """Replace the stored history with `history` (already trimmed by the
        caller to its max length)."""

    @abc.abstractmethod
    async def load_week_plan(self, week_identifier: str = "current") -> Optional[dict]:
        """The last generated week as a raw dict, or None if there isn't one.

        `week_identifier` picks which cached week — `"current"` (the default,
        and the only one that existed before multi-week support) or `"next"`
        — since the app now keeps two weeks on disk at once rather than
        overwriting a single file. Returned unvalidated so this module stays
        independent of planner's Pydantic models; callers run
        `WeekPlan.model_validate` themselves.
        """

    @abc.abstractmethod
    async def save_week_plan(self, week_plan: dict, week_identifier: str = "current") -> None:
        """Store the generated week under `week_identifier`, replacing any
        previous plan stored under that same identifier.

        Not in the original four methods, but the cached week plan is the other
        half of `load_week_plan` and the CLI's `--use-cached-plan` flag reads
        what this writes — leaving it out would have left a bare `json.dump`
        behind in planner.py, which is the thing this module exists to remove.
        """

    @abc.abstractmethod
    async def load_recipe_catalog(self) -> List[dict]:
        """Every recipe ever favorited or imported, oldest first — the single
        store recipe content lives in outside of the current `week_plan.json`.

        Records look like `{id, content_key, recipe, is_favorite, source,
        added_at, updated_at}`. `week_plan.json` and `meal_history.json` are
        not this store: the former is overwritten every generation and the
        latter keeps only lean per-day summaries, so a recipe that isn't
        favorited or imported has no life beyond the week it was cooked in.
        """

    @abc.abstractmethod
    async def get_favorites(self) -> List[dict]:
        """The subset of the catalog with `is_favorite` true."""

    @abc.abstractmethod
    async def toggle_favorite(self, recipe: dict) -> bool:
        """Flip favorite status for the catalog entry matching `recipe`'s
        name + ingredients (see `recipe_content_key`).

        If no matching entry exists yet, one is created (`source="favorited"`,
        already favorited) — the first click on a card's bookmark is both
        "add to catalog" and "favorite it" in one step. An existing entry is
        never removed by this call, only its flag flipped, so un-favoriting a
        recipe never drops it from the catalog (see `delete_catalog_recipe`
        for actual removal). Returns the new `is_favorite` state.
        """

    @abc.abstractmethod
    async def import_recipe(self, recipe: dict, favorite: bool = False) -> dict:
        """Add `recipe` to the catalog (`source="imported"`), or fold into a
        matching existing entry (see `recipe_content_key`) if one exists.

        `favorite` can only ever turn an existing entry's flag on, never off
        — an import is not how a recipe gets un-favorited. Returns the
        stored record.
        """

    @abc.abstractmethod
    async def rename_catalog_recipe(self, recipe_id: str, name: str) -> Optional[dict]:
        """Rename a catalog entry's recipe in place. Returns the updated
        record, or None if `recipe_id` isn't in the catalog — a favorite
        deleted in another tab is a no-op here, not an error, the same
        tolerance `load_history`/`load_week_plan` extend to a missing file.
        """

    @abc.abstractmethod
    async def delete_catalog_recipe(self, recipe_id: str) -> None:
        """Remove an entry from the catalog outright. A no-op if already
        gone. Distinct from `toggle_favorite`'s un-favorite, which keeps the
        entry — this is for discarding a bad import or a mistaken save."""


class LocalJSONRepository(PlanRepository):
    """The current on-disk layout: four JSON files next to the code.

    Paths are constructor arguments rather than module constants so tests (and
    a second week in another directory) don't have to chdir. Writes go to a
    temporary file and are then renamed: a crash mid-write would otherwise
    leave truncated JSON where meal_history.json used to be, and history is not
    reproducible once lost.
    """

    def __init__(
        self,
        config_path: str = DEFAULT_CONFIG_FILE,
        history_path: str = DEFAULT_HISTORY_FILE,
        week_plan_path: str = DEFAULT_WEEK_PLAN_FILE,
        recipe_catalog_path: str = DEFAULT_RECIPE_CATALOG_FILE,
        models_path: str = DEFAULT_MODELS_FILE,
        whfoods_path: str = DEFAULT_WHFOODS_FILE,
    ) -> None:
        self.paths = StoragePaths(
            config=config_path,
            history=history_path,
            week_plan=week_plan_path,
            recipe_catalog=recipe_catalog_path,
            models=models_path,
            whfoods=whfoods_path,
        )

    # -- PlanRepository ----------------------------------------------------

    async def load_config(self) -> dict:
        config = await asyncio.to_thread(self._read_json, self.paths.config)
        if config is None:
            raise FileNotFoundError(f"Config file not found: {self.paths.config}")
        return config

    async def load_models_config(self) -> dict:
        return await asyncio.to_thread(self._read_json, self.paths.models) or {}

    async def load_whfoods(self) -> List[str]:
        foods = await asyncio.to_thread(self._read_json, self.paths.whfoods) or []
        return [food["name"] for food in foods if food.get("name")]

    async def load_history(self) -> List[dict]:
        return await asyncio.to_thread(self._read_json, self.paths.history) or []

    async def save_history(self, history: List[dict]) -> None:
        await asyncio.to_thread(self._write_json, self.paths.history, history)

    async def load_week_plan(self, week_identifier: str = "current") -> Optional[dict]:
        return await asyncio.to_thread(
            self._read_json, self._week_plan_path(week_identifier)
        )

    async def save_week_plan(self, week_plan: dict, week_identifier: str = "current") -> None:
        await asyncio.to_thread(
            self._write_json, self._week_plan_path(week_identifier), week_plan
        )

    async def load_recipe_catalog(self) -> List[dict]:
        return await asyncio.to_thread(self._read_json, self.paths.recipe_catalog) or []

    async def get_favorites(self) -> List[dict]:
        catalog = await self.load_recipe_catalog()
        return [record for record in catalog if record.get("is_favorite")]

    async def toggle_favorite(self, recipe: dict) -> bool:
        return await asyncio.to_thread(self._toggle_favorite, recipe)

    async def import_recipe(self, recipe: dict, favorite: bool = False) -> dict:
        return await asyncio.to_thread(self._import_recipe, recipe, favorite)

    async def rename_catalog_recipe(self, recipe_id: str, name: str) -> Optional[dict]:
        return await asyncio.to_thread(self._rename_catalog_recipe, recipe_id, name)

    async def delete_catalog_recipe(self, recipe_id: str) -> None:
        await asyncio.to_thread(self._delete_catalog_recipe, recipe_id)

    def _week_plan_path(self, week_identifier: str) -> str:
        """File for one named week.

        `"current"` maps to the original single-file layout
        (`self.paths.week_plan`, i.e. `week_plan.json`) rather than
        `week_plan_current.json`, so an existing install with a cached week
        already on disk needs no migration and no data movement the first
        time this runs. Every other identifier — `"next"`, or a week-start
        date — gets its own `week_plan_<identifier>.json` alongside it.
        """
        if week_identifier == "current":
            return self.paths.week_plan
        return f"week_plan_{week_identifier}.json"

    # -- blocking helpers, only ever called in a worker thread --------------

    def _find_catalog_entry(self, catalog: List[dict], recipe: dict) -> Optional[dict]:
        key = recipe_content_key(recipe)
        return next((r for r in catalog if r.get("content_key") == key), None)

    def _toggle_favorite(self, recipe: dict) -> bool:
        catalog = self._read_json(self.paths.recipe_catalog) or []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self._find_catalog_entry(catalog, recipe)
        if existing is not None:
            existing["is_favorite"] = not existing.get("is_favorite", False)
            existing["updated_at"] = now
            new_state = existing["is_favorite"]
        else:
            catalog.append(
                {
                    "id": uuid.uuid4().hex,
                    "content_key": recipe_content_key(recipe),
                    "recipe": recipe,
                    "is_favorite": True,
                    "source": "favorited",
                    "added_at": now,
                    "updated_at": now,
                }
            )
            new_state = True
        self._write_json(self.paths.recipe_catalog, catalog)
        return new_state

    def _import_recipe(self, recipe: dict, favorite: bool) -> dict:
        catalog = self._read_json(self.paths.recipe_catalog) or []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        existing = self._find_catalog_entry(catalog, recipe)
        if existing is not None:
            if favorite and not existing.get("is_favorite", False):
                existing["is_favorite"] = True
                existing["updated_at"] = now
            record = existing
        else:
            record = {
                "id": uuid.uuid4().hex,
                "content_key": recipe_content_key(recipe),
                "recipe": recipe,
                "is_favorite": favorite,
                "source": "imported",
                "added_at": now,
                "updated_at": now,
            }
            catalog.append(record)
        self._write_json(self.paths.recipe_catalog, catalog)
        return record

    def _rename_catalog_recipe(self, recipe_id: str, name: str) -> Optional[dict]:
        catalog = self._read_json(self.paths.recipe_catalog) or []
        updated = None
        for record in catalog:
            if record.get("id") == recipe_id:
                record["recipe"]["name"] = name
                record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                updated = record
                break
        if updated is not None:
            self._write_json(self.paths.recipe_catalog, catalog)
        return updated

    def _delete_catalog_recipe(self, recipe_id: str) -> None:
        catalog = self._read_json(self.paths.recipe_catalog) or []
        remaining = [record for record in catalog if record.get("id") != recipe_id]
        if len(remaining) != len(catalog):
            self._write_json(self.paths.recipe_catalog, remaining)

    @staticmethod
    def _read_json(path: str) -> Any:
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: str, payload: Any) -> None:
        temporary = f"{path}.tmp"
        with open(temporary, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(temporary, path)


def run_sync(awaitable: Awaitable[T]) -> T:
    """Run one coroutine to completion from synchronous code.

    The bridge for callers that are not (yet) async themselves — today the
    CLI's `main()`, which runs top-to-bottom in a plain thread with no loop.
    `asyncio.run` covers that, but it raises if a loop is already running in
    this thread — so when there is one, the coroutine is handed to a scratch
    thread with a loop of its own. That path costs a thread, and exists only so
    an embedded caller never deadlocks; the normal case is the first branch.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)  # type: ignore[arg-type]

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, awaitable).result()  # type: ignore[arg-type]
-e 

