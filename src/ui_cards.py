"""The weekly grid: recipe detail dialog, the swap-with-a-favorite modal, the
Sunday prep column, and the 7-day x 4-meal canvas itself.

`build_cards(ctx, generation)` needs `generation` (see `ui_generation`)
because a card's per-meal regenerate icon and a day column's regenerate icon
both trigger it, and because both live in `meal_card`/`canvas` here.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import ui

from planner import MACRO_KEYS, Recipe, derive_fat_g
from shopping import format_quantity
from ui_catalog import favorited_catalog, is_favorited, toggle_favorite
from ui_context import UIContext
from ui_generation import GenerationHandles
from ui_state import SlotView, slot_target_budget
from ui_theme import (
    LINK_ACTION_LABEL,
    LINK_SOURCE_MEAL,
    MACRO_DETAIL_LABELS,
    MACRO_LABELS,
    MACRO_TINTS,
    MONO_SECTION_LABEL,
    PREP_BADGE_STYLES,
    PREP_COLUMN_ACCENT,
    STATUS_SKIP,
    STATUS_STYLES,
    link_line,
    split_quantity,
)
from week import MODE_COOK, MODE_LEFTOVER, MODE_SKIP, portions_for, slot_id, slot_label


@dataclass
class CardHandles:
    canvas: Callable
    recipe_detail: Callable
    swap_matches: Callable
    swap_dialog_body: Callable
    open_detail: Callable
    skip_estimate_body: Callable


def build_cards(ctx: UIContext, generation: GenerationHandles) -> CardHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    # ---- recipe detail (read-only) ---------------------------------------
    # One dialog reused for every card: its body is refreshable and reads
    # state.focus, so opening a card is a refresh, not 28 pre-built dialogs.
    #
    # The layout is a cook's document rather than a denser version of the
    # card that opened it: a mono eyebrow saying what kind of meal this is, a
    # ruled macro strip, ingredients as a two-column table with the
    # quantities right-aligned in one mono column, and the method as numbered
    # rows you can tick off. Everything above `ui.separator`-free hairlines —
    # a rule at 1px of `slate-800` — because the grid behind this dialog is
    # already carrying every colour the app owns, and a recipe you are cooking
    # from wants type and alignment doing the work instead.

    def step_row(number: int, step: str) -> None:
        """One method step, struck through on click.

        Toggled by mutating this row's own classes, not by refreshing the
        dialog: ticking step 2 of 9 must not repaint the recipe you are
        reading (and lose its scroll position) to change one line. Same
        in-place reasoning as `ui_drawer.day_target_row`.

        Deliberately unpersisted, and reset every time the dialog opens
        because `recipe_detail` rebuilds these rows — it is scratch state for
        one cook, exactly like the shopping list's ticks, and storing it would
        be more state able to disagree with `week_plan.json`.
        """
        done = False

        # `flex-nowrap` is load-bearing, not tidiness: Quasar's own `.flex`
        # rule sets `flex-wrap: wrap`, which Tailwind's `flex-row` doesn't
        # undo, so a step long enough to fill the row wrapped *below* its
        # number and ran back under it. `min-w-0` is the other half — a flex
        # item's default `min-width: auto` won't shrink past its longest
        # word, which reintroduces the overflow one step further along.
        row = ui.element("div").classes(
            "flex flex-row flex-nowrap items-start gap-3 px-3 py-2 rounded-md "
            "cursor-pointer border border-slate-800 bg-slate-800/30 "
            "hover:border-slate-700 transition-colors duration-100"
        )
        with row:
            marker = ui.label(str(number)).classes(
                "shrink-0 w-5 h-5 rounded-full grid place-items-center "
                "text-[10px] font-mono border border-slate-700 text-slate-400"
            )
            label = ui.label(step).classes(
                "min-w-0 text-[13px] leading-snug text-slate-200"
            )

        def toggle() -> None:
            # add/remove rather than `toggle=`, because the pairs here are
            # conflicting Tailwind utilities (`text-slate-200` vs
            # `text-slate-600`): both present at once resolves by stylesheet
            # order, which is not something this file gets to decide.
            nonlocal done
            done = not done
            if done:
                label.classes(add="line-through text-slate-600", remove="text-slate-200")
                marker.classes(
                    add="bg-emerald-400/15 text-emerald-300 border-emerald-400/40",
                    remove="text-slate-400 border-slate-700",
                )
            else:
                label.classes(add="text-slate-200", remove="line-through text-slate-600")
                marker.classes(
                    add="text-slate-400 border-slate-700",
                    remove="bg-emerald-400/15 text-emerald-300 border-emerald-400/40",
                )

        row.on("click", toggle)

    def hairline() -> None:
        ui.element("div").classes("h-px bg-slate-700/60 my-4")

    @ui.refreshable
    def recipe_detail() -> None:
        view = state.focus
        if view is None or view.recipe is None:
            ui.label("Nothing to show.").classes("text-slate-400")
            return

        look = STATUS_STYLES[view.status]

        # Eyebrow: what kind of meal this is, and — where the reference design
        # put a portion multiplier this app can't have (portions are derived,
        # see `week.portions_for`) — the same cook/leftover chip the card
        # carries, so the dialog says what it opened from.
        with ui.element("div").classes("flex flex-row items-center justify-between gap-3"):
            eyebrow = " — ".join(
                part.upper() for part in [view.meal_type, view.style] if part
            )
            ui.label(eyebrow).classes(MONO_SECTION_LABEL)
            with ui.element("div").classes(
                f"flex items-center gap-1 px-2 py-[2px] rounded-full shrink-0 {look['badge']}"
            ):
                ui.icon(look["icon"]).classes("text-[11px]")
                ui.label(look["label"]).classes("text-[9px] font-semibold tracking-wide")

        ui.label(view.title).classes(
            "text-2xl font-semibold leading-tight text-slate-100 mt-2"
        )

        portion_note = f"{view.portions} portion{'' if view.portions == 1 else 's'}"
        subtitle = " · ".join(
            part
            for part in [
                view.cuisine,
                portion_note if view.portions else "",
                f"leftover from {view.source_label}" if view.source_label else "",
            ]
            if part
        )
        if subtitle:
            ui.label(subtitle).classes("text-sm text-slate-400 mt-1")

        if view.macros:
            # One ruled strip, dot-separated, rather than four stacked
            # figure/label pairs: these numbers are read as a set ("590 / 46 /
            # 34 / 29"), and a single line is what lets you compare them to
            # the budget without the eye travelling.
            cells = [
                (f"{view.macros[key]:.0f}{unit}", label, MACRO_TINTS.get(key, "text-slate-500"))
                for key, label, unit in MACRO_DETAIL_LABELS
                if key in view.macros
            ]
            if view.prep_minutes is not None:
                # The reference reads "30m total"; this is prep time, which is
                # the only figure the recipe actually carries, so it says so.
                cells.append((f"{view.prep_minutes}m", "PREP", "text-slate-500"))

            with ui.element("div").classes(
                "flex flex-row items-center justify-between gap-2 mt-3 px-4 py-3 "
                "rounded-lg border border-slate-800 bg-slate-800/40"
            ):
                for index, (value, label, tint) in enumerate(cells):
                    if index:
                        ui.label("·").classes("text-slate-600 text-sm")
                    with ui.element("div").classes("flex flex-row items-baseline gap-1.5"):
                        # Colour rides on the label, never the number — the
                        # same rule `MACRO_TINTS` states for the card strip.
                        ui.label(value).classes("text-base font-semibold text-slate-100")
                        ui.label(label).classes(
                            f"text-[10px] font-mono tracking-wider {tint}"
                        )
            ui.label("PER SERVING").classes(
                "block text-right text-[9px] font-mono tracking-[0.18em] "
                "text-slate-600 mt-1"
            )

        hairline()

        # Ingredients are for the whole batch while the macros above are for
        # one serving, and on a bulk-cooked dinner those are different numbers
        # by a factor of four. Each half says which it is, next to itself.
        count = len(view.recipe.ingredients)
        with ui.element("div").classes("flex flex-row items-baseline justify-between gap-3"):
            ui.label(f"INGREDIENTS ({count} ITEM{'' if count == 1 else 'S'})").classes(
                MONO_SECTION_LABEL
            )
            if view.portions:
                ui.label(f"ALL {portion_note.upper()}").classes(
                    "text-[10px] font-mono tracking-wider text-slate-500 shrink-0"
                )
        with ui.element("div").classes("grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2"):
            for ingredient in view.recipe.ingredients:
                with ui.element("div").classes(
                    "flex flex-row items-baseline justify-between gap-2 min-w-0 "
                    "px-3 py-2 rounded-md border border-slate-800 bg-slate-800/30"
                ):
                    ui.label(ingredient.name).classes(
                        "text-[13px] text-slate-200 truncate"
                    )
                    amount, unit = split_quantity(
                        format_quantity(ingredient.name, ingredient.quantity_g)
                    )
                    with ui.element("div").classes(
                        "flex flex-row items-baseline gap-1 shrink-0"
                    ):
                        ui.label(amount).classes("text-[13px] font-mono text-slate-300")
                        if unit:
                            ui.label(unit).classes("text-[11px] font-mono text-slate-500")
                    # NOVA group moves to a tooltip rather than off the card:
                    # every group that reaches here is an allowed one (4 is
                    # rejected in validation), so it is worth being able to
                    # check and not worth a column of its own.
                    ui.tooltip(f"NOVA group {ingredient.nova_group}")

        hairline()

        with ui.element("div").classes("flex flex-row items-baseline justify-between gap-3"):
            ui.label("PREPARATION INSTRUCTIONS").classes(MONO_SECTION_LABEL)
            ui.label("Click a step when complete").classes(
                "text-[10px] text-slate-500 shrink-0"
            )
        with ui.element("div").classes("flex flex-col gap-2 mt-2"):
            for number, step in enumerate(view.recipe.instructions, start=1):
                step_row(number, step)

        if view.recipe.prep_notes:
            with ui.element("div").classes(
                "flex flex-row flex-nowrap items-start gap-2 mt-4 px-3 py-2 "
                "rounded-md border border-amber-400/25 bg-amber-400/[0.07]"
            ):
                ui.icon("inventory_2").classes(
                    "shrink-0 text-[13px] text-amber-300 mt-[3px]"
                )
                ui.label(view.recipe.prep_notes).classes(
                    "min-w-0 text-[12px] leading-snug text-amber-200/90"
                )

    with ui.dialog() as detail_dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-xl border border-slate-800 p-6 "
            "w-[46rem] max-w-full max-h-[85vh] overflow-y-auto"
        ):
            recipe_detail()

    def open_detail(view: SlotView) -> None:
        if view.recipe is None:
            return
        state.focus = view
        refreshables.refresh("recipe_detail")
        detail_dialog.open()

    # ---- "eaten out" estimate modal ----------------------------------------
    # A skipped meal that was actually eaten somewhere — dinner with friends,
    # a working lunch — still costs the day. Recording an estimate is what
    # stops the remaining meals absorbing its share and coming back oversized
    # (`week.skip_estimate_totals` has the full reasoning).
    #
    # Calories/protein/carbs are typed and fat is derived, the same division
    # `ui_drawer.day_target_row` uses for daily targets: `derive_fat_g` is the
    # rule every other budget in the app is built on, so an input for fat
    # could only ever disagree with it.
    skip_target: dict = {"slot_id": "", "calories": 0.0, "protein_g": 0.0, "net_carbs_g": 0.0}

    def open_skip_estimate(view: SlotView) -> None:
        # Prefilled from the existing estimate if there is one, otherwise from
        # what this slot would have been briefed at had it been cooked —
        # "roughly what this meal is normally worth" is a far better starting
        # point to adjust than an empty box.
        seed = view.skip_estimate or state.default_skip_estimate(view.id)
        skip_target["slot_id"] = view.id
        for key in ("calories", "protein_g", "net_carbs_g"):
            skip_target[key] = round(float(seed.get(key, 0.0)), 1)
        skip_estimate_body.refresh()
        skip_dialog.open()

    def save_skip_estimate() -> None:
        estimate = {
            "calories": float(skip_target["calories"] or 0.0),
            "protein_g": float(skip_target["protein_g"] or 0.0),
            "net_carbs_g": float(skip_target["net_carbs_g"] or 0.0),
        }
        estimate["fat_g"] = derive_fat_g(
            estimate["calories"], estimate["protein_g"], estimate["net_carbs_g"]
        )
        error = state.set_skip_estimate(skip_target["slot_id"], estimate)
        if error:
            ui.notify(error, type="warning")
            return
        skip_dialog.close()
        refreshables.refresh("plan")

    def clear_skip_estimate() -> None:
        """Back to a plain skip — a meal genuinely not eaten, contributing 0.

        Distinct from an all-zero estimate, which claims the meal happened and
        cost nothing; `week.set_skip_estimate` keeps the two apart on purpose.
        """
        error = state.set_skip_estimate(skip_target["slot_id"], None)
        if error:
            ui.notify(error, type="warning")
            return
        skip_dialog.close()
        refreshables.refresh("plan")

    @ui.refreshable
    def skip_estimate_body() -> None:
        ui.label("Eaten out").classes("text-base font-semibold text-slate-100")
        ui.label(
            slot_label(skip_target["slot_id"]) if skip_target["slot_id"] else ""
        ).classes("text-[11px] font-mono uppercase tracking-widest text-slate-500")
        ui.label(
            "Roughly what this meal cost. It comes off the day so the other "
            "meals aren't briefed for budget you've already spent."
        ).classes("text-xs text-slate-400 mt-2 mb-1")

        with ui.element("div").classes("flex flex-row gap-2"):
            for key, label in (
                ("calories", "kcal"),
                ("protein_g", "Protein g"),
                ("net_carbs_g", "Net carbs g"),
            ):
                ui.number(label=label, min=0, step=5, format="%.0f").bind_value(
                    skip_target, key
                ).props("dense outlined").classes("w-28")

        # Read-only, recomputed on every repaint of this body — the same
        # "displayed, never typed" treatment fat gets in the drawer.
        fat = derive_fat_g(
            float(skip_target["calories"] or 0.0),
            float(skip_target["protein_g"] or 0.0),
            float(skip_target["net_carbs_g"] or 0.0),
        )
        ui.label(f"fat {fat:.0f}g — derived from the three above").classes(
            "text-[10px] font-mono text-slate-500 mt-1"
        )

        with ui.element("div").classes("flex flex-row justify-between items-center w-full mt-4"):
            ui.button("Not eaten", on_click=clear_skip_estimate).props(
                "flat dense no-caps size=sm"
            ).classes("text-slate-400")
            with ui.element("div").classes("flex flex-row gap-2"):
                ui.button("Cancel", on_click=lambda: skip_dialog.close()).props(
                    "flat dense no-caps size=sm"
                ).classes("text-slate-400")
                ui.button("Save", on_click=save_skip_estimate).props(
                    "unelevated dense no-caps size=sm"
                ).classes("bg-sky-400/20 text-sky-200")

    with ui.dialog() as skip_dialog:
        with ui.card().classes("bg-slate-900 border border-slate-800 min-w-[340px]"):
            skip_estimate_body()

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

                if view.mode == MODE_SKIP and view.skip_estimate:
                    # Same pill shape as a recipe's macro strip so the day
                    # reads consistently, but slate rather than tinted: these
                    # are estimated, not measured off a recipe, and the strip
                    # should not claim the precision the cooked cards have.
                    with ui.element("div").classes(
                        "flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-1.5 py-0.5 "
                        "rounded-full bg-slate-950/40 w-fit max-w-full"
                    ):
                        ui.label(
                            f"~{view.skip_estimate.get('calories', 0):.0f} kcal"
                        ).classes("text-[9px] font-mono text-slate-400")
                        ui.label("·").classes("text-[9px] text-slate-600")
                        ui.label(
                            f"~{view.skip_estimate.get('protein_g', 0):.0f}g P"
                        ).classes("text-[9px] font-mono text-slate-400")

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

            if view.mode == MODE_SKIP and view.day:
                # Offered on every skipped slot, not only estimated ones —
                # recording that a meal was eaten out is the *first* thing you
                # do to one, so there has to be a way in from the plain state.
                skip_button = ui.button(
                    "Edit estimate" if view.skip_estimate else "Eaten out?",
                    icon="restaurant",
                    on_click=lambda v=view: open_skip_estimate(v),
                )
                skip_button.props("unelevated dense no-caps size=sm").classes(
                    "self-start min-h-0 px-1.5 py-0.5 rounded-full text-[9px] "
                    "transition-all duration-150 bg-slate-800/60 text-slate-400 "
                    "hover:bg-slate-700/60 hover:text-slate-200"
                )
                with skip_button:
                    ui.tooltip(
                        "Record roughly what this meal cost, so the rest of "
                        "the day is planned around it"
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
                        "Turn on Bulk prep or Long cook meal in the Generate popup "
                        "for a batch-prep timeline here (or set enable_sunday_prep "
                        "in config.json to do it every week)."
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
        open_detail=open_detail,
        skip_estimate_body=skip_estimate_body,
    )
