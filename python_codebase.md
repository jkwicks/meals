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
  days, model) plus the generation trigger. Everything that applies to the
  whole week rather than one meal.
- **Header** — macro telemetry: one horizontal bar per day, in the *same*
  7-column grid as the canvas below, so a day's bar sits directly above its
  column of meals.
- **Canvas** — 7 day columns x 4 stacked meal cards, cook vs. leftover
  distinguished by colour, border and badge.

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

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dotenv import load_dotenv
from nicegui import ui

from planner import (
    DEFAULT_MODEL,
    MACRO_KEYS,
    WeekPlan,
    api_key_error,
    calculate_daily_targets,
    configure_logging,
    day_slot_macros,
    generate_week_plan,
    per_serving_totals,
    record_week_history,
    rescale_cook_event,
    resolve_auto_choices,
)
from repository import LocalJSONRepository
from shopping import format_quantity
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
    portions_for,
    shopping_windows,
    slot_id,
    slot_label,
    span_days,
    validate_week,
    week_days,
)

load_dotenv()
configure_logging()

CONFIG_PATH = "config.json"

# One repository for the server, imported once rather than re-executed per
# interaction. It holds paths only, so pointing the app at a different backend
# stays a one-line change here.
REPOSITORY = LocalJSONRepository(config_path=CONFIG_PATH)

MODEL_OPTIONS = [
    "anthropic/claude-sonnet-5",
    "deepseek/deepseek-v4-flash",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "poolside/laguna-s-2.1:free",
]

# A slot's render status is its mode, except that a cook (or the cook a
# leftover points at) whose day failed to generate has no recipe to show. That
# is a fourth visual state, not an error: CLAUDE.md's "a failed day must not
# fail the week" means the rest of the grid still renders around it.
STATUS_COOK = "cook"
STATUS_LEFTOVER = "leftover"
STATUS_SKIP = "skip"
STATUS_MISSING = "missing"

# Tailwind per status. Cook is solid with a bright left rule; leftover is
# dimmer and dashed — nothing is bought or cooked for it, so it should read as
# derived from the card it points at rather than as its own event.
STATUS_STYLES = {
    STATUS_COOK: {
        "card": "border border-slate-700 border-l-4 border-l-emerald-400 bg-slate-800",
        "badge": "bg-emerald-400/15 text-emerald-300",
        "label": "COOK",
    },
    STATUS_LEFTOVER: {
        "card": "border border-dashed border-slate-700 border-l-4 border-l-sky-400 bg-slate-800/40",
        "badge": "bg-sky-400/15 text-sky-300",
        "label": "LEFTOVER",
    },
    STATUS_SKIP: {
        "card": "border border-dashed border-slate-800 border-l-4 border-l-slate-700 bg-slate-900/40",
        "badge": "bg-slate-700/40 text-slate-400",
        "label": "SKIP",
    },
    STATUS_MISSING: {
        "card": "border border-rose-900 border-l-4 border-l-rose-500 bg-rose-950/30",
        "badge": "bg-rose-500/15 text-rose-300",
        "label": "NOT GENERATED",
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
    week_plan: Optional[WeekPlan] = None
    week_start: str = ""
    servings: int = 2
    shop_days: List[str] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    focus: Optional[SlotView] = None
    edited: bool = False
    # A run holds this client for 30s-3min per cooking day. The loop stays free
    # (planner dispatches each call to a thread), so the browser is still live
    # and perfectly able to click Generate again — this is the flag that says
    # no. Per-client, like everything else here: two tabs generating at once
    # would race to overwrite the same week_plan.json.
    generating: bool = False

    # The live week shape. Held rather than re-derived on every access because
    # it is now editable: rebuilding it per read would throw away the links the
    # user just made. `_spec_shape` records what it was derived from, so a
    # genuine change of week shape still rebuilds it.
    _spec: Optional[WeekSpec] = None
    _spec_shape: tuple = ()

    @classmethod
    async def load(cls, repository: LocalJSONRepository) -> "PlannerState":
        config = await repository.load_config()
        state = cls(
            config=config,
            week_start=config.get("week_start_day") or list(config["weekly_schedule"])[0],
            servings=int(config.get("serving_rules", {}).get("servings_per_meal", 2)),
            shop_days=list(config.get("shopping", {}).get("shop_days", [])),
            model=config.get("openrouter_model", DEFAULT_MODEL),
        )
        await state.reload_plan(repository)
        return state

    async def reload_plan(self, repository: LocalJSONRepository) -> None:
        """Pull the cached week off disk. Validation lives here, not in the
        repository, which deliberately deals in plain dicts."""
        raw = await repository.load_week_plan()
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
        self.week_plan = plan.model_copy(
            update={
                "slots": list(spec.slots),
                # A slot that stopped being a cook keeps its event in the list,
                # orphaned: nothing resolves to it any more, and holding it
                # means the recipe is still there if the week is re-pointed.
                "cook_events": [
                    rescale_cook_event(
                        event,
                        portions[event.slot_id],
                        span_days(spec, event.slot_id),
                        claims.get(event.slot_id, [event.slot_id]),
                    )
                    if event.slot_id in portions
                    else event
                    for event in plan.cook_events
                ],
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

    def targets_for(self, day: str) -> dict:
        """A generated week's targets are whatever it was generated against;
        an un-generated one recomputes from config so the header still has a
        denominator to show."""
        if self.week_plan and day in self.week_plan.targets:
            return self.week_plan.targets[day]
        return calculate_daily_targets(day, self.config)

    def totals_for(self, day: str) -> dict:
        if not self.week_plan:
            return {key: 0.0 for key in MACRO_KEYS}
        return day_slot_macros(self.week_plan, day)

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
                # means this slot's cook is on a day in WeekPlan.failures —
                # that is the red "not generated" state, and it must stay
                # visible so the gap is obvious rather than silently blank.
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

            # Style and cuisine come off the event, which recorded whatever
            # `resolve_auto_choices` settled on — the slot may still say
            # "auto".
            views[slot.id] = SlotView(
                status=STATUS_COOK if slot.mode == MODE_COOK else STATUS_LEFTOVER,
                title=event.recipe.name,
                prep_minutes=event.recipe.prep_time_minutes,
                macros=per_serving_totals(event.recipe),
                recipe=event.recipe,
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


def macro_colour(actual: float, target: float) -> str:
    """Quasar colour for how close a day landed to its calorie target.

    A single scale factor can't fix a bad macro *ratio* (CLAUDE.md), so this is
    a read on the day, not a promise the plan is right — the bands are wide
    enough that only a genuinely off day goes red.
    """
    if target <= 0 or actual <= 0:
        return "grey-7"
    ratio = actual / target
    if 0.9 <= ratio <= 1.1:
        return "positive"
    if 0.75 <= ratio <= 1.25:
        return "warning"
    return "negative"


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
        clickable = "cursor-pointer hover:brightness-125" if view.recipe else ""
        chain = f"chain chain-{view.chain}" if view.chain is not None else ""

        with ui.element("div").classes(
            f"rounded p-2 flex flex-col gap-1 min-w-0 {look['card']} {chain}"
        ):
            # The recipe dialog opens from this inner block rather than the
            # card, so the action button below is a sibling of it and a click
            # on the button can't also open the dialog on its way up.
            body = ui.element("div").classes(f"flex flex-col gap-1 min-w-0 {clickable}")
            if view.recipe:
                body.on("click", lambda v=view: open_detail(v))

            with body:
                with ui.element("div").classes("flex flex-row items-center justify-between gap-1"):
                    ui.label(meal_type[:5].upper()).classes(
                        "text-[9px] font-semibold tracking-widest text-slate-500"
                    )
                    ui.label(look["label"]).classes(
                        f"text-[9px] font-semibold px-1 rounded {look['badge']}"
                    )

                ui.label(view.title).classes(
                    "text-[11px] leading-tight font-medium text-slate-100 line-clamp-2"
                )

                tags = " · ".join(part for part in [view.style, view.cuisine] if part)
                if tags:
                    ui.label(tags).classes("text-[9px] text-slate-400 truncate")

                if view.mode == MODE_LEFTOVER and view.source_label:
                    link_line("↩ from", view.source_label, view.chain_colour)
                if view.feeds:
                    link_line("→ feeds", " · ".join(view.feeds), view.chain_colour)

                if view.macros:
                    with ui.element("div").classes("flex flex-row gap-1.5 mt-0.5"):
                        for key, short, unit in MACRO_LABELS:
                            text = (
                                f"{view.macros[key]:.0f}"
                                if key == "calories"
                                else f"{short}{view.macros[key]:.0f}"
                            )
                            ui.label(text).classes("text-[9px] font-mono text-slate-400")

                if view.mode == MODE_COOK and view.portions:
                    ui.label(
                        f"{view.portions} portions · {view.prep_minutes} min"
                        if view.prep_minutes is not None
                        else f"{view.portions} portions"
                    ).classes("text-[9px] text-emerald-300/70 truncate")

            if view.mode == MODE_COOK and view.meal_type == LINK_SOURCE_MEAL:
                # Left enabled even when it can't be applied: a disabled Quasar
                # button swallows hover, so the tooltip explaining *why* would
                # never appear. Clicking says the same thing in a notification.
                button = ui.button(
                    LINK_ACTION_LABEL,
                    icon="subdirectory_arrow_right",
                    on_click=lambda v=view: on_link_next_lunch(v),
                )
                button.props("dense flat no-caps size=sm").classes(
                    "self-start min-h-0 px-1 py-0 text-[9px] "
                    + ("text-slate-600" if view.link_error else "text-sky-300")
                )
                with button:
                    ui.tooltip(
                        view.link_error
                        or f"{slot_label(view.link_target)} eats this instead of "
                        "cooking — the batch grows to match."
                    )

    @ui.refreshable
    def canvas() -> None:
        views = state.slot_views()
        with ui.element("div").classes("meal-canvas grid grid-cols-7 gap-2 w-full items-start"):
            for day in state.days:
                with ui.element("div").classes("flex flex-col gap-2 min-w-0"):
                    with ui.element("div").classes(
                        "px-1 py-0.5 border-b border-slate-800 flex flex-row "
                        "justify-between items-baseline"
                    ):
                        ui.label(day).classes("text-xs font-semibold text-slate-200")
                        ui.label(str(state.days.index(day) + 1)).classes(
                            "text-[9px] font-mono text-slate-600"
                        )
                    for meal_type in state.meal_types:
                        meal_card(views.get(slot_id(day, meal_type)), meal_type)

    # ---- header: macro telemetry -----------------------------------------

    @ui.refreshable
    def telemetry() -> None:
        with ui.element("div").classes("grid grid-cols-7 gap-2 w-full"):
            for day in state.days:
                target = state.targets_for(day)
                totals = state.totals_for(day)
                kcal, goal = totals["calories"], float(target["calories"])
                with ui.element("div").classes("flex flex-col gap-1 min-w-0"):
                    with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                        ui.label(day[:3].upper()).classes(
                            "text-[11px] font-semibold tracking-wider text-slate-300"
                        )
                        ui.label(f"{kcal:.0f}/{goal:.0f}").classes(
                            "text-[10px] font-mono text-slate-400"
                        )
                    # Clamped to 1.0 — an overshoot is reported by the delta
                    # text and the colour, not by a bar that runs off the end.
                    ui.linear_progress(
                        value=min(1.0, kcal / goal) if goal else 0.0,
                        size="8px",
                        show_value=False,
                        color=macro_colour(kcal, goal),
                    ).props("rounded")
                    with ui.element("div").classes("flex flex-row gap-2"):
                        for key, short, unit in MACRO_LABELS[1:]:
                            ui.label(
                                f"{short} {totals[key]:.0f}/{float(target[key]):.0f}{unit}"
                            ).classes("text-[10px] font-mono text-slate-500")
                    with ui.tooltip():
                        for key, short, unit in MACRO_LABELS:
                            delta = totals[key] - float(target[key])
                            ui.label(
                                f"{short}: {totals[key]:.0f}{unit} "
                                f"({delta:+.0f} vs {float(target[key]):.0f})"
                            )

    with ui.header(bordered=True).classes("bg-slate-900 px-3 py-2 flex flex-col gap-2"):
        with ui.element("div").classes("flex flex-row items-baseline gap-3"):
            ui.label("AI Weekly Meal Planner").classes("text-sm font-semibold tracking-wide")
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
                ui.label(f"{len(failures)} day(s) failed to generate").classes(
                    "text-xs text-rose-300 font-semibold"
                )
                for day, error in failures.items():
                    ui.label(f"{day}: {error}").classes("text-[10px] text-rose-200/80")

    def refresh_all() -> None:
        telemetry.refresh()
        canvas.refresh()
        week_summary.refresh()

    async def reload_from_disk() -> None:
        await state.reload_plan(REPOSITORY)
        refresh_all()
        ui.notify(
            "Reloaded week_plan.json" if state.week_plan else "No cached week plan on disk",
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
        # The drawer's model wins over config's: it is the one the user can see.
        config = dict(state.config, openrouter_model=state.model)
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

        days = spec.days
        cooking_days = len({slot.day for slot in spec.cook_slots()})
        done = 0

        def on_day(day: str, cooks: int) -> None:
            """Fired on the loop by generate_week_plan, once per day, before its call."""
            nonlocal done
            done += 1
            progress_bar.value = (done - 1) / len(days)
            progress_status.text = (
                f"Generating {day} ({done}/{len(days)}) — {cooks} recipe(s)…"
                if cooks
                else f"{day} ({done}/{len(days)}) — leftovers only, nothing to cook"
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
                progress_callback=on_day,
                note_callback=progress_log.push,
                repository=REPOSITORY,
            )
            progress_status.text = "Saving…"
            progress_bar.value = 1.0
            await REPOSITORY.save_week_plan(week_plan.model_dump())
            await record_week_history(week_plan, REPOSITORY)
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
                f"{len(week_plan.failures)} of {cooking_days} cooking day(s) failed — "
                "their meals show as NOT GENERATED. "
                + " · ".join(f"{day}: {error}" for day, error in week_plan.failures.items()),
                type="warning",
                multi_line=True,
                close_button=True,
                timeout=0,
            )
        else:
            ui.notify(
                f"Generated {cooking_days} cooking day(s) and saved to week_plan.json",
                type="positive",
            )

    with ui.left_drawer(bordered=True).classes("bg-slate-900 p-3 gap-3 flex flex-col").props(
        ":width=270"
    ):
        ui.label("Week setup").classes("text-xs uppercase tracking-widest text-slate-500")

        all_days = list(state.config["weekly_schedule"].keys())

        def on_week_start(event) -> None:
            # Set the field explicitly before refreshing: `bind_value` keeps
            # state in sync through the binding loop, which runs *after* this
            # handler, so a refresh relying on it alone would repaint the old
            # week order.
            state.week_start = event.value
            refresh_all()

        ui.select(
            all_days,
            label="Week starts on",
            on_change=on_week_start,
        ).bind_value(state, "week_start").props("dense outlined").classes("w-full")

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
        ).bind_value(state, "servings").props("dense outlined").classes("w-full")

        def on_shop_days(event) -> None:
            state.shop_days = list(event.value or [])
            week_summary.refresh()

        ui.select(
            all_days,
            label="Shopping days",
            multiple=True,
            on_change=on_shop_days,
        ).bind_value(state, "shop_days").props("dense outlined use-chips").classes("w-full")

        ui.select(MODEL_OPTIONS, label="Model").bind_value(state, "model").props(
            "dense outlined"
        ).classes("w-full")

        ui.separator()
        ui.label("This week").classes("text-xs uppercase tracking-widest text-slate-500")
        week_summary()

        ui.separator()
        with ui.element("div").classes("flex flex-col gap-2"):
            # The one thing here that spends money and overwrites disk, so it
            # says so on the tooltip rather than in a confirmation step the
            # user would learn to click through.
            generate = (
                ui.button("Generate week", icon="bolt", on_click=run_generation)
                .props("dense")
                .classes("w-full")
            )
            with generate:
                ui.tooltip(
                    "Generates every meal set to cook in this grid — one API call per "
                    "cooking day. Overwrites week_plan.json and appends to history."
                )

            ui.button(
                "Reload from disk", icon="refresh", on_click=reload_from_disk
            ).props("dense flat").classes("w-full")

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
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import instructor
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from repository import (
    DEFAULT_CONFIG_FILE,
    DEFAULT_HISTORY_FILE,
    DEFAULT_WEEK_PLAN_FILE,
    LocalJSONRepository,
    PlanRepository,
    run_sync,
)
from shopping import (
    aggregate_cook_events,
    format_shopping_list_markdown,
    format_shopping_list_text,
)
from week import (
    FRIDGE_SAFE_DAYS,
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
    portions_for,
    shopping_windows,
    styles_for,
    validate_week,
)

load_dotenv()

DEFAULT_ALLOWED_NOVA_GROUPS = [1, 2, 3]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
# Where the local files live is repository.py's business now; these names are
# kept only for the CLI's help text and log messages.
WEEK_PLAN_CACHE_FILE = DEFAULT_WEEK_PLAN_FILE
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
MEAL_HISTORY_FILE = DEFAULT_HISTORY_FILE
HISTORY_MAX_ENTRIES = 21
PROTEIN_LOOKBACK_ENTRIES = 3
# How many recent main proteins to name in the prompt. Long enough to stop a
# week of chicken, short enough that a 7-day plan doesn't end up banning
# everything the model knows by Friday.
PROTEIN_AVOID_WINDOW = 6
FREE_MODEL_MAX_TOKENS = 8000
PAID_MODEL_MAX_TOKENS = 16000

MACRO_KEYS = ("calories", "protein_g", "net_carbs_g", "fat_g")

# Share of the day each meal type gets when splitting targets across slots.
# Only the ratios matter — they're normalised over whichever slots are
# actually being cooked, so a day with no snack redistributes its share.
DEFAULT_MEAL_WEIGHTS = {"breakfast": 0.25, "lunch": 0.30, "dinner": 0.35, "snack": 0.10}

# Models compose plausible meals but size them badly, so portions are corrected
# after the fact by scaling every quantity linearly. The clamp stops a trim
# producing an absurd portion (a 30g breakfast, a 900g steak).
PORTION_TRIM_LIMITS = (0.6, 1.6)
PORTION_TRIM_DEADBAND = 0.03


def is_free_model(model: str) -> bool:
    return model.endswith(":free")


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
    raw = (config.get("weekly_schedule", {}).get(day) or {}).get("meal_overrides") or {}
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


def recent_main_proteins(
    history: List[dict], lookback_entries: int = PROTEIN_LOOKBACK_ENTRIES
) -> List[str]:
    """Main proteins across the last few days, de-duplicated, so the model can
    be told not to repeat them."""
    seen = set()
    proteins = []
    for entry in history[-lookback_entries:]:
        for protein in entry.get("main_proteins", []):
            if protein not in seen:
                seen.add(protein)
                proteins.append(protein)
    return proteins


def resolve_auto_choices(spec: WeekSpec, config: dict, history: List[dict]) -> WeekSpec:
    """Fill in every `auto` style and cuisine with a concrete choice.

    Runs before any API call so the entire week is deterministic and
    previewable: rotation continues from meal_history.json and then keeps
    rotating *within* the week, so seven auto breakfasts don't all resolve to
    whatever happens to be first in the config list.
    """
    cuisines = config.get("cuisines", [])
    cuisine_meal_types = config.get("cuisine_meal_types") or meal_types(config)

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


class DayRecipes(BaseModel):
    """The model's response for a single day: one recipe per cook slot."""

    recipes: List[Recipe]

    @model_validator(mode="after")
    def reject_untrimmable_macro_miss(self, info: ValidationInfo) -> "DayRecipes":
        """Bounce a response too far off budget for the portion trim to rescue.

        The threshold is derived from PORTION_TRIM_LIMITS rather than picked:
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
        budget = (info.context or {}).get("day_budget")
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
        low, high = PORTION_TRIM_LIMITS
        if not low <= factor <= high:
            raise ValueError(
                f"the recipes total {total:.0f} kcal per serving but the budget for "
                f"these meals is {target:.0f} kcal ({(total - target) / target:+.0%}). "
                "Resize the portions to match each meal's stated budget — do not "
                "add or remove meals, and remember any meals already listed as "
                "fixed leftovers are NOT yours to generate."
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

    def by_slot(self) -> Dict[str, CookEvent]:
        return {event.slot_id: event for event in self.cook_events}

    def events_on_days(self, days: List[str]) -> List[CookEvent]:
        day_set = set(days)
        return [event for event in self.cook_events if event.day in day_set]


# --------------------------------------------------------------------------
# Macro math (always Python, never the model)
# --------------------------------------------------------------------------


def compute_recipe_totals(recipe: Recipe) -> dict:
    totals = {key: 0.0 for key in MACRO_KEYS}
    for ingredient in recipe.ingredients:
        for key in MACRO_KEYS:
            totals[key] += getattr(ingredient, key)
    return totals


def per_serving_totals(recipe: Recipe) -> dict:
    servings = max(1, recipe.servings)
    return {key: value / servings for key, value in compute_recipe_totals(recipe).items()}


def round_quantity(grams: float) -> float:
    """Whole grams once there's enough of something to weigh out that way.

    Trimming portions by a fraction produces quantities like 393.8g, which is
    noise on a shopping list. Spices and oils keep a decimal because 2.4g of
    turmeric and 2g are meaningfully different amounts.
    """
    return max(0.1, round(grams) if grams >= 10 else round(grams, 1))


def resize_recipe(recipe: Recipe, factor: float) -> Recipe:
    """Multiply every ingredient quantity and its macros by `factor`."""
    return recipe.model_copy(
        update={
            "ingredients": [
                ingredient.model_copy(
                    update=dict(
                        {key: round(getattr(ingredient, key) * factor, 1) for key in MACRO_KEYS},
                        quantity_g=round_quantity(ingredient.quantity_g * factor),
                    )
                )
                for ingredient in recipe.ingredients
            ]
        }
    )


def fit_recipe_to_budget(recipe: Recipe, budget: dict) -> Tuple[Recipe, float]:
    """Resize one serving of a recipe so its calories land on its budget.

    Models pick sensible *ingredients* and implausible *amounts*, and every
    macro is linear in quantity, so a single scale factor fixes the portion
    without touching the dish. It cannot fix a bad macro ratio — a recipe with
    the right calories and the wrong protein split stays wrong, and shows up
    as a visible delta in the day summary rather than being papered over.
    """
    actual = compute_recipe_totals(recipe)["calories"]
    target = budget.get("calories", 0)
    if actual <= 0 or target <= 0:
        return recipe, 1.0

    factor = target / actual
    factor = min(max(factor, PORTION_TRIM_LIMITS[0]), PORTION_TRIM_LIMITS[1])
    if abs(factor - 1.0) < PORTION_TRIM_DEADBAND:
        return recipe, 1.0
    return resize_recipe(recipe, factor), factor


# Opening words of a storage note we wrote ourselves. Used to tell our note
# apart from a model-authored one when a batch is later resized: ours is stale
# the moment the portion count moves, a model's is about the dish and must
# survive. Worst case a model happens to open its note this way and gets an
# accurate note in place of its own.
STORAGE_NOTE_PREFIX = "Yields "


def storage_note(portions: int, keeps_for_days: int) -> str:
    """How to keep a batch that has to last until the meal that finishes it.

    Empty for a single serving eaten the day it's cooked — there is nothing to
    say, and `scale_recipe` leaves `prep_notes` alone rather than writing one.
    """
    if portions <= 1 or keeps_for_days <= 0:
        return ""
    storage = (
        "refrigerate in airtight containers"
        if keeps_for_days < FRIDGE_SAFE_DAYS
        else f"refrigerate what you'll eat within {FRIDGE_SAFE_DAYS} days and freeze the rest"
    )
    return (
        f"{STORAGE_NOTE_PREFIX}{portions} portions, eaten across {keeps_for_days} day(s). "
        f"Portion immediately, {storage}; reheat thoroughly before serving."
    )


def scale_recipe(recipe: Recipe, portions: int, keeps_for_days: int) -> Recipe:
    """Scale a recipe from the model's single serving up to its full yield.

    The model reports one serving; the portion count comes from how many slots
    claim this cook (see week.portions_for), so this stays a plain linear
    multiply and the arithmetic never leaves Python.
    """
    scaled = resize_recipe(recipe, portions)
    prep_notes = recipe.prep_notes or storage_note(portions, keeps_for_days) or None
    return scaled.model_copy(update={"servings": portions, "prep_notes": prep_notes})


def rescale_cook_event(
    event: CookEvent, portions: int, keeps_for_days: int, eaten_by: List[str]
) -> CookEvent:
    """Resize an already-scaled cook event's batch to a new portion count.

    Editing the week changes how many slots claim a cook, and portions are
    *derived* from exactly that (`week.portions_for`) — so the batch has to
    follow, or the card says "6 portions" over ingredients weighed for 4,
    which is the disagreement the derived-portion rule exists to prevent.

    This is the same linear arithmetic as `scale_recipe`, just starting from a
    batch instead of a single serving, so re-pointing a leftover costs no
    generation call. It cannot invent a new dish — only more or less of this
    one — which is exactly right for "the same recipe now feeds another meal".
    """
    if event.portions <= 0:
        return event

    recipe = event.recipe
    if portions != event.portions:
        recipe = resize_recipe(recipe, portions / event.portions)

    prep_notes = recipe.prep_notes
    if not prep_notes or prep_notes.startswith(STORAGE_NOTE_PREFIX):
        prep_notes = storage_note(portions, keeps_for_days) or None

    return event.model_copy(
        update={
            "portions": portions,
            "eaten_by": list(eaten_by),
            "recipe": recipe.model_copy(
                update={"servings": portions, "prep_notes": prep_notes}
            ),
        }
    )


def day_slot_macros(week_plan: WeekPlan, day: str) -> dict:
    """What one person actually eats on `day`, summed across their slots."""
    by_slot = week_plan.by_slot()
    totals = {key: 0.0 for key in MACRO_KEYS}
    for slot in week_plan.slots:
        if slot.day != day or slot.mode == MODE_SKIP:
            continue
        source_id = slot.id if slot.mode == MODE_COOK else slot.source
        event = by_slot.get(source_id)
        if event is None:
            continue
        serving = per_serving_totals(event.recipe)
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
    totals = {key: 0.0 for key in MACRO_KEYS}
    descriptions = []
    for slot in spec.slots:
        if slot.day != day or slot.mode != MODE_LEFTOVER or not slot.source:
            continue
        event = events.get(slot.source)
        if event is None:
            continue
        serving = per_serving_totals(event.recipe)
        for key in MACRO_KEYS:
            totals[key] += serving[key]
        descriptions.append(
            f"{slot.meal_type}: leftovers of \"{event.recipe.name}\" "
            f"(cooked {event.day}) — {serving['calories']:.0f} kcal, "
            f"{serving['protein_g']:.0f}g protein, {serving['net_carbs_g']:.0f}g net carbs, "
            f"{serving['fat_g']:.0f}g fat"
        )
    return totals, descriptions


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


def build_client() -> instructor.Instructor:
    error = api_key_error()
    if error:
        raise RuntimeError(error)
    openai_client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=120.0,
    )
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


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
    weights_config = config.get("meal_weights", DEFAULT_MEAL_WEIGHTS)

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
        for item in config.get("inventory_to_clear", [])
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
    parts.append(
        f"budget (one serving): {budget['calories']:.0f} kcal, "
        f"{budget['protein_g']:.0f}g protein, {budget['net_carbs_g']:.0f}g net carbs, "
        f"{budget['fat_g']:.0f}g fat"
    )
    if pinned:
        parts.append("[fixed budget for this meal — the other meals absorb the rest of the day]")
    if times_eaten_today > 1:
        parts.append(f"[eaten {times_eaten_today}x today, budget already accounts for that]")
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
    progress_note=None,
) -> Dict[str, Recipe]:
    """Generate one day's cooked recipes, returned keyed by meal_type.

    Only the slots set to cook are generated. Leftover slots' macros are
    subtracted from the day's target first, so the model is asked for the
    remaining gap rather than a full day it would then overshoot.
    """
    client = build_client()
    dietary_rules = config["dietary_rules"]

    remaining = {key: max(0.0, targets[key] - carried.get(key, 0.0)) for key in MACRO_KEYS}

    avoid_protein_instruction = (
        "- Avoid making any of these the primary protein again — they were used "
        f"recently: {', '.join(avoid_proteins)}.\n"
        if avoid_proteins
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
        f"{avoid_protein_instruction}"
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

    model = config.get("openrouter_model", DEFAULT_MODEL)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("%s: requesting %d recipe(s) from %s", day, len(cook_slots), model)
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=DayRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        # OpenRouter's unified switch for turning a model's hidden reasoning
        # off. Measured on anthropic/claude-sonnet-5 with this exact prompt:
        # reasoning on gave 303s and a run that consumed all 32000 tokens on
        # 6981 reasoning tokens and returned *zero* content (finish_reason
        # "length"); reasoning off gave 16-19s, ~2200 completion tokens and
        # finish_reason "stop" on 3/3 attempts. This task needs no deliberation
        # — the macro arithmetic is already done in Python — so the reasoning
        # budget is pure cost and a pure failure mode. Harmless for models that
        # have no reasoning mode.
        extra_body={"reasoning": {"enabled": False}},
        # The validator compares against the sum of the per-recipe budgets, not
        # `remaining`: a meal eaten twice in one day contributes its macros
        # twice, so the recipes legitimately total less than the day does.
        context={
            "config": config,
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
        recipe, factor = fit_recipe_to_budget(by_meal_type[slot.meal_type], budgets[slot.id])
        if factor != 1.0 and progress_note:
            progress_note(
                f"{day} {slot.meal_type}: portions resized x{factor:.2f} to hit "
                f"{budgets[slot.id]['calories']:.0f} kcal"
            )
        fitted[slot.meal_type] = recipe
    return fitted


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


async def generate_week_plan(
    spec: WeekSpec,
    config: dict,
    history: Optional[List[dict]] = None,
    progress_callback=None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Generate the whole week, one API call per day that has cooking to do.

    Days are walked in week order so a leftover slot's source recipe always
    exists by the time its macros are needed. Cost scales with cook days, not
    calendar days: a week where lunches are all leftovers is 7 smaller calls,
    and a day with nothing to cook is free.

    Every day's API call is dispatched with `asyncio.to_thread`. `generate_day`
    blocks on instructor's *synchronous* client for 30s-3min, and awaiting it
    inline held the loop for the length of a run — invisible in the CLI, fatal
    in NiceGUI, where it froze every connected browser for the whole 20 minutes
    and the progress updates it was meant to be showing could not be delivered
    until the run they described had finished. The thread is what makes the
    `await` a real yield; `on_calling_loop` puts `note_callback` back on the
    loop afterwards, so a caller's callbacks still arrive where they were
    registered. `progress_callback` is fired here, on the loop, and never
    crosses the boundary at all.

    Days remain strictly sequential — one thread at a time, walked in week
    order — because a later day's prompt is built from earlier days' recipes
    (`carried_macros`, `avoid_proteins`). This is about not blocking the loop,
    not about generating faster.
    """
    if history is None:
        history = await (repository or LocalJSONRepository()).load_history()
    targets = week_targets(spec, config)
    portions = portions_for(spec)
    claims = eaten_on(spec)
    # Seeded from previous weeks, then extended as this week generates —
    # otherwise every day is told to avoid the same stale list and nothing
    # stops all seven dinners being chicken.
    avoid_proteins = recent_main_proteins(history)
    thread_safe_note = on_calling_loop(note_callback)

    events: Dict[str, CookEvent] = {}
    failures: Dict[str, str] = {}

    for day in spec.days:
        cook_slots = spec.cook_slots_on(day)
        carried, descriptions = carried_macros(spec, day, events)

        if progress_callback:
            progress_callback(day, len(cook_slots))
        if not cook_slots:
            continue

        try:
            recipes = await asyncio.to_thread(
                generate_day,
                day=day,
                targets=targets[day],
                cook_slots=cook_slots,
                config=config,
                servings_per_meal=spec.servings_per_meal,
                multiplicity=day_multiplicity(spec, day),
                carried=carried,
                carried_descriptions=descriptions,
                avoid_proteins=avoid_proteins[-PROTEIN_AVOID_WINDOW:],
                progress_note=thread_safe_note,
            )
        except Exception as exc:
            # One bad day must not discard the six good ones. Free routes fail
            # in ways no amount of retrying fixes (a provider returning an
            # empty completion, a model that can't hit the budget), and a
            # week is ~7 chances to hit one. The day is recorded and skipped;
            # its slots simply render as "not generated" and its ingredients
            # never reach a shopping list.
            failures[day] = f"{type(exc).__name__}: {exc}".split("\n")[0][:300]
            logger.warning("%s: generation failed — %s", day, failures[day])
            if note_callback:
                note_callback(f"{day}: generation failed — {failures[day]}")
            continue

        for recipe in recipes.values():
            protein = extract_main_protein(recipe)
            if protein and protein not in avoid_proteins:
                avoid_proteins.append(protein)

        for slot in cook_slots:
            recipe = recipes[slot.meal_type]
            claim_ids = claims.get(slot.id, [slot.id])
            last_day_index = max(spec.day_index(value.split(":")[0]) for value in claim_ids)
            recipe = scale_recipe(
                recipe,
                portions=portions[slot.id],
                keeps_for_days=last_day_index - spec.day_index(slot.day),
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

    ordered_events = [events[slot.id] for slot in spec.cook_slots() if slot.id in events]
    return WeekPlan(
        days=spec.days,
        servings_per_meal=spec.servings_per_meal,
        generated_at=datetime.now().isoformat(),
        cook_events=ordered_events,
        slots=spec.slots,
        targets=targets,
        failures=failures,
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
    max_entries: int = HISTORY_MAX_ENTRIES,
) -> None:
    """One history entry per cooked day, so rotation carries across weeks."""
    repository = repository or LocalJSONRepository()
    history = await repository.load_history()
    generated_at = week_plan.generated_at

    for day in week_plan.days:
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
        print("\n!! Some days failed to generate — re-run to retry them:")
        for day, error in week_plan.failures.items():
            print(f"   {day}: {error}")
    by_slot = week_plan.by_slot()
    slots_by_day: Dict[str, List[SlotSpec]] = {}
    for slot in week_plan.slots:
        slots_by_day.setdefault(slot.day, []).append(slot)

    for day in week_plan.days:
        totals = day_slot_macros(week_plan, day)
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
        "--config", default=DEFAULT_CONFIG_FILE, help="Path to config JSON file"
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
            f"Load the week from {WEEK_PLAN_CACHE_FILE} instead of calling "
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
    config = await repository.load_config()
    if args.model:
        config["openrouter_model"] = args.model
    spec = default_week_spec(config, args.week_start, args.servings)

    if args.leftover_lunches:
        from week import autofill_leftovers

        spec = autofill_leftovers(spec, "lunch", "dinner")

    if args.use_cached_plan:
        print(f"Loading cached week plan from {WEEK_PLAN_CACHE_FILE}...", flush=True)
        cached = await repository.load_week_plan()
        if cached is None:
            print(f"No cached week plan found ({WEEK_PLAN_CACHE_FILE}). Generate one first.")
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

        model = config.get("openrouter_model", DEFAULT_MODEL)
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
        await record_week_history(week_plan, repository)

    print_week_summary(week_plan)

    shop_days = (
        [day.strip() for day in args.shop_days.split(",") if day.strip()]
        if args.shop_days
        else config.get("shopping", {}).get("shop_days", [])
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
    note — bullets or blank lines would just become extra junk items."""
    lines = []
    for department in sorted(shopping_list.categories):
        lines.append(department)
        for item in shopping_list.categories[department]:
            lines.append(f"{item.name}: {format_quantity(item.name, item.total_amount_g)}")
    return "\n".join(lines)
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

from typing import Dict, List, Optional

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
PERISHABLE_DAY_GAP = 3

# Cooked food keeps ~3-4 days refrigerated, so a leftover eaten 4+ days after
# its cook day is at the edge — flagged in the grid and reflected in the
# recipe's storage note rather than silently planned.
FRIDGE_SAFE_DAYS = 4


def humanize(value: Optional[str]) -> str:
    return value.replace("_", " ") if value else ""


def week_days(config: dict, week_start: Optional[str] = None) -> List[str]:
    """The week in cooking order, rotated so it begins on week_start.

    Generation walks this order, and leftovers may only point backwards
    along it, so "day 1" is whatever the user considers the start of their
    shopping week rather than a hardcoded Monday.
    """
    days = list(config["weekly_schedule"].keys())
    start = week_start or config.get("week_start_day")
    if start in days:
        index = days.index(start)
        days = days[index:] + days[:index]
    return days


def meal_types(config: dict) -> List[str]:
    return config.get("meal_types", DEFAULT_MEAL_TYPES)


def styles_for(config: dict, meal_type: str) -> Dict[str, str]:
    """style key -> prose description handed to the model."""
    return config.get("meal_styles", {}).get(meal_type, {})


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


def slot_label(value: str, short: bool = False) -> str:
    """A slot id as prose: 'Monday:dinner' -> 'Monday dinner' / 'Mon dinner'.

    `humanize` only swaps underscores, so it leaves the colon in a slot id
    sitting in the middle of a sentence. Anything that names a slot to the
    user goes through here instead.
    """
    day, _, meal_type = value.partition(":")
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
    defaults = config.get("week_defaults", {})
    servings = servings_per_meal or config.get("serving_rules", {}).get(
        "servings_per_meal", DEFAULT_SERVINGS_PER_MEAL
    )

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
    cuisines = config.get("cuisines", [])
    cuisine_meal_types = config.get("cuisine_meal_types") or meal_types(config)

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


def week_warnings(spec: WeekSpec) -> List[str]:
    """Non-blocking notes — things that are legal but probably not intended."""
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
        if span >= FRIDGE_SAFE_DAYS:
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
    cook_index = spec.day_index(cook_id.split(":")[0])
    last_index = max(spec.day_index(value.split(":")[0]) for value in claims)
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
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, List, Optional, TypeVar

DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_HISTORY_FILE = "meal_history.json"
DEFAULT_WEEK_PLAN_FILE = "week_plan.json"

T = TypeVar("T")


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
    async def load_week_plan(self) -> Optional[dict]:
        """The last generated week as a raw dict, or None if there isn't one.

        Returned unvalidated so this module stays independent of planner's
        Pydantic models; callers run `WeekPlan.model_validate` themselves.
        """

    @abc.abstractmethod
    async def save_week_plan(self, week_plan: dict) -> None:
        """Store the generated week, replacing any previous one.

        Not in the original four methods, but the cached week plan is the other
        half of `load_week_plan` and the CLI's `--use-cached-plan` flag reads
        what this writes — leaving it out would have left a bare `json.dump`
        behind in planner.py, which is the thing this module exists to remove.
        """


class LocalJSONRepository(PlanRepository):
    """The current on-disk layout: three JSON files next to the code.

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
    ) -> None:
        self.config_path = config_path
        self.history_path = history_path
        self.week_plan_path = week_plan_path

    # -- PlanRepository ----------------------------------------------------

    async def load_config(self) -> dict:
        config = await asyncio.to_thread(self._read_json, self.config_path)
        if config is None:
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        return config

    async def load_history(self) -> List[dict]:
        return await asyncio.to_thread(self._read_json, self.history_path) or []

    async def save_history(self, history: List[dict]) -> None:
        await asyncio.to_thread(self._write_json, self.history_path, history)

    async def load_week_plan(self) -> Optional[dict]:
        return await asyncio.to_thread(self._read_json, self.week_plan_path)

    async def save_week_plan(self, week_plan: dict) -> None:
        await asyncio.to_thread(self._write_json, self.week_plan_path, week_plan)

    # -- blocking helpers, only ever called in a worker thread --------------

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

