"""NiceGUI front end — the whole week on one high-density desktop screen.

The only web front end, built in stages. It can rearrange a week but not
produce one: the Generate button is deliberately inert until generation is
ported, so `python planner.py` is currently the only way to produce a week.

**Nothing here writes to disk.** Edits (today: "Link to next lunch") live in
the client's `PlannerState` until "Reload from disk" throws them away, and the
header carries an "edited — not saved" chip while any are outstanding. Persisting
them belongs with the rest of the mutation port, not ahead of it.

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
    calculate_daily_targets,
    configure_logging,
    day_slot_macros,
    per_serving_totals,
    rescale_cook_event,
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
        self.week_plan = WeekPlan.model_validate(raw) if raw else None
        # What's on disk is the truth again, so discard the edited spec and
        # let the next `spec` read rebuild from the reloaded plan.
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
            # Generation is intentionally not wired up in this stage. The
            # button exists so the layout is final, but a 20-minute run that
            # writes history belongs with the rest of the mutation port —
            # until then the CLI is the only way to generate a week.
            generate = ui.button("Generate week", icon="bolt").props("dense").classes("w-full")
            generate.disable()
            with generate:
                ui.tooltip("Not wired up yet — generate with `python planner.py`.")

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
