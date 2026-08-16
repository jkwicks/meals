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
    day_multiplicity,
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
    selectable_models,
    short_error,
    split_targets,
    weeknight_prep_minutes,
)
from repository import PROJECT_ROOT, LocalJSONRepository, recipe_content_key
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

# Explicit path — see the matching note in planner.py. NiceGUI's reloader can
# also start the process from a different directory than the one you typed in.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
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


def pluralize(word: str) -> str:
    """Plural of a meal-type name, for the progress dialog's stage heading.

    Display only, and only for meal types — `meal_type_order` lets config
    define its own, so this can't assume the four built-ins. The sibilant rule
    is what "breakfasts" and "lunches" need between them; anything else takes a
    bare -s, which is right for every meal name in English worth the extra code.
    """
    if word.endswith(("ch", "sh", "s", "x", "z")):
        return word + "es"
    return word + "s"


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
    # models.json, loaded alongside config — the drawer's model select offers
    # `selectable_models(models_config)` and `.load()` uses
    # `models_config["meal_generation_model"]` as `model`'s starting value
    # until the select changes it for this session.
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
                    # `self.config` threaded through so the storage note uses the
                    # configured `inventory_rules.fridge_safe_days`. Omitting it
                    # silently fell back to week.DEFAULT_INVENTORY_RULES, so an
                    # edited config would disagree with the note on the card.
                    "recipe": event.recipe.scale_to_servings(
                        target, span_days(spec, event.slot_id), self.config
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

    # These three read `state.recipe_catalog` (the in-memory copy loaded at
    # startup) rather than awaiting the repository, deliberately: `canvas()`
    # calls `is_favorited` once per cooked card on every repaint, and turning
    # that into a disk read per card would make a repaint O(cards) file opens.
    # Every handler that mutates the catalog refreshes this list from disk, so
    # it stays in sync — do not "fix" these into async repository calls.
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
    # The run is long (30s-3min per meal type) and its progress is the only
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
                "One API call per meal type, 30s–3 min each. This window stays "
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
            label = humanize(pluralize(meal_type)).capitalize()
            progress_status.text = (
                f"Generating {label} ({done}/{len(stages)}) — {cooks} recipe(s)…"
                if cooks
                else f"{label} ({done}/{len(stages)}) — nothing to cook, all leftovers or skipped"
            )

        generate.props("loading")
        progress_status.text = (
            f"Starting {len(stages)} meal type(s) across {cooking_days} cooking day(s) "
            f"on {state.model}…"
        )
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
                f"Generation failed: {short_error(exc)}",
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
                f"Regenerating {day} failed: {short_error(exc)}",
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
                f"Regenerating {slot_label(target_slot_id)} failed: {short_error(exc)}",
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
                f"Import failed: {short_error(exc)}",
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
                    "meal type, covering each day it's cooked. Overwrites the selected "
                    "week's cached plan and appends to history."
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
                selectable_models(state.models_config),
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
