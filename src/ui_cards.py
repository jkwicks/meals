"""The weekly grid: recipe detail dialog, the swap-with-a-favorite modal, the
Sunday prep column, and the 7-day x 4-meal canvas itself.

`build_cards(ctx, generation)` needs `generation` (see `ui_generation`)
because a card's per-meal regenerate icon and a day column's regenerate icon
both trigger it, and because both live in `meal_card`/`canvas` here.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import ui

from planner import Recipe
from shopping import format_quantity
from ui_catalog import favorited_catalog, is_favorited, toggle_favorite
from ui_context import UIContext
from ui_generation import GenerationHandles
from ui_state import SlotView, slot_target_budget
from ui_theme import (
    LINK_ACTION_LABEL,
    LINK_SOURCE_MEAL,
    MACRO_LABELS,
    MACRO_TINTS,
    PREP_BADGE_STYLES,
    PREP_COLUMN_ACCENT,
    STATUS_SKIP,
    STATUS_STYLES,
    link_line,
)
from week import MODE_COOK, MODE_LEFTOVER, portions_for, slot_id, slot_label


@dataclass
class CardHandles:
    canvas: Callable
    recipe_detail: Callable
    swap_matches: Callable
    swap_dialog_body: Callable


def build_cards(ctx: UIContext, generation: GenerationHandles) -> CardHandles:
    state = ctx.state
    refreshables = ctx.refreshables

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
        refreshables.refresh("recipe_detail")
        detail_dialog.open()

    # ---- swap-in modal -----------------------------------------------------

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
        refreshables.refresh("swap_matches")

    def confirm_swap() -> None:
        if state.swap_target is None or state.swap_selected_id is None:
            return
        favorite = next(
            (f for f in favorited_catalog(ctx) if f["id"] == state.swap_selected_id), None
        )
        if favorite is None:
            return
        error = state.swap_slot_with_favorite(state.swap_target.id, favorite["recipe"])
        if error:
            ui.notify(error, type="warning")
            return
        swap_dialog.close()
        refreshables.refresh("plan")
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
        favorites = favorited_catalog(ctx)
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
                refreshables.refresh("swap_matches")

            def on_query_change(event) -> None:
                state.swap_query = event.value or ""
                refreshables.refresh("swap_matches")

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
        refreshables.refresh("swap_dialog")
        swap_dialog.open()

    # ---- canvas: 7 day columns x 4 meal cards -----------------------------

    def on_link_next_lunch(view: SlotView) -> None:
        """Apply the Macro Action, then repaint whatever it moved.

        The "plan" topic includes the telemetry header on purpose — the
        linked lunch now eats the dinner's macros, so its day's totals
        change too.
        """
        error = state.link_to_next_lunch(view.id)
        if error:
            ui.notify(error, type="warning")
            return
        refreshables.refresh("plan")
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
                            favorited = is_favorited(ctx, recipe_dict)
                            fav_button = ui.button(
                                icon="bookmark" if favorited else "bookmark_border",
                                on_click=lambda r=recipe_dict: toggle_favorite(ctx, r),
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
                            lambda v=view, btn=meal_regen_button: generation.regenerate_meal(v, btn)
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
                                    lambda day=day, btn=regen_button: generation.regenerate_day(day, btn)
                                )
                                with regen_button:
                                    ui.tooltip(f"Regenerate {day} — re-cooks just this day")
                        ui.label(str(state.days.index(day) + 1)).classes(
                            "text-[9px] font-mono text-slate-600"
                        )
                    for meal_type in state.meal_types:
                        meal_card(views.get(slot_id(day, meal_type)), meal_type)

    return CardHandles(
        canvas=canvas,
        recipe_detail=recipe_detail,
        swap_matches=swap_matches,
        swap_dialog_body=swap_dialog_body,
    )
