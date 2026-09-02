"""The weekly grid: recipe detail dialog, the swap-with-a-favorite modal, the
Sunday prep column, and the 7-day x 4-meal canvas itself.

`build_cards(ctx, generation)` needs `generation` (see `ui_generation`)
because a card's per-meal regenerate icon and a day column's regenerate icon
both trigger it, and because both live in `meal_card`/`canvas` here.
"""

from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, Optional

from nicegui import ui

from planner import Recipe, derive_fat_g
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
    RADIUS_CARD,
    RADIUS_PANEL,
    RADIUS_PILL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    STATUS_SKIP,
    STATUS_STYLES,
    SURFACE_CARD_LIFT,
    SURFACE_PANEL,
    TEXT_BODY,
    TEXT_DISPLAY,
    TEXT_HEAD,
    TEXT_MICRO,
    WEEK_GRID_COLS,
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
        in-place reasoning as `ui_review.day_target_row`.

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
            f"flex flex-row flex-nowrap items-start gap-{SPACE_SECTION} px-{SPACE_SECTION} py-{SPACE_BASE} {RADIUS_CARD} "
            "cursor-pointer border border-slate-800 bg-slate-800/30 "
            "hover:border-slate-700 transition-colors duration-100"
        )
        with row:
            marker = ui.label(str(number)).classes(
                f"shrink-0 w-5 h-5 {RADIUS_PILL} grid place-items-center "
                f"{TEXT_MICRO} font-mono border border-slate-700 text-slate-400"
            )
            label = ui.label(step).classes(
                f"min-w-0 {TEXT_HEAD} leading-snug text-slate-200"
            )

        def toggle() -> None:
            # add/remove rather than `toggle=`, because the pairs here are
            # conflicting Tailwind utilities (`text-slate-200` vs
            # `text-slate-400`): both present at once resolves by stylesheet
            # order, which is not something this file gets to decide.
            nonlocal done
            done = not done
            if done:
                label.classes(add="line-through text-slate-400", remove="text-slate-200")
                marker.classes(
                    add="bg-emerald-400/15 text-emerald-300 border-emerald-400/40",
                    remove="text-slate-400 border-slate-700",
                )
            else:
                label.classes(add="text-slate-200", remove="line-through text-slate-400")
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
        with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_SECTION}"):
            eyebrow = " — ".join(
                part.upper() for part in [view.meal_type, view.style] if part
            )
            ui.label(eyebrow).classes(MONO_SECTION_LABEL)
            with ui.element("div").classes(
                f"flex items-center gap-{SPACE_TIGHT} px-{SPACE_BASE} py-[2px] {RADIUS_PILL} shrink-0 {look['badge']}"
            ):
                ui.icon(look["icon"]).classes(TEXT_BODY)
                ui.label(look["label"]).classes(f"{TEXT_MICRO} font-semibold tracking-wide")

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
            ui.label(subtitle).classes(f"{TEXT_HEAD} text-slate-400 mt-1")

        if view.macros:
            # One ruled strip, dot-separated, rather than four stacked
            # figure/label pairs: these numbers are read as a set ("590 / 46 /
            # 34 / 29"), and a single line is what lets you compare them to
            # the budget without the eye travelling.
            cells = [
                (f"{view.macros[key]:.0f}{unit}", label, MACRO_TINTS.get(key, "text-slate-400"))
                for key, label, unit in MACRO_DETAIL_LABELS
                if key in view.macros
            ]
            if view.prep_minutes is not None:
                # The reference reads "30m total"; this is prep time, which is
                # the only figure the recipe actually carries, so it says so.
                cells.append((f"{view.prep_minutes}m", "PREP", "text-slate-400"))

            with ui.element("div").classes(
                f"flex flex-row items-center justify-between gap-{SPACE_BASE} mt-3 px-{SPACE_PAGE} py-{SPACE_SECTION} "
                f"{RADIUS_PANEL} border border-slate-800 bg-slate-800/40"
            ):
                for index, (value, label, tint) in enumerate(cells):
                    if index:
                        ui.label("·").classes(f"text-slate-400 {TEXT_HEAD}")
                    with ui.element("div").classes(f"flex flex-row items-baseline gap-{SPACE_TIGHT}"):
                        # Colour rides on the label, never the number — the
                        # same rule `MACRO_TINTS` states for the card strip.
                        ui.label(value).classes(f"{TEXT_DISPLAY} font-semibold text-slate-100")
                        ui.label(label).classes(
                            f"{TEXT_MICRO} font-mono tracking-wider {tint}"
                        )
            ui.label("PER SERVING").classes(
                f"block text-right {TEXT_MICRO} font-mono tracking-[0.18em] "
                "text-slate-400 mt-1"
            )

        hairline()

        # Ingredients are for the whole batch while the macros above are for
        # one serving, and on a bulk-cooked dinner those are different numbers
        # by a factor of four. Each half says which it is, next to itself.
        count = len(view.recipe.ingredients)
        with ui.element("div").classes(f"flex flex-row items-baseline justify-between gap-{SPACE_SECTION}"):
            ui.label(f"INGREDIENTS ({count} ITEM{'' if count == 1 else 'S'})").classes(
                MONO_SECTION_LABEL
            )
            if view.portions:
                ui.label(f"ALL {portion_note.upper()}").classes(
                    f"{TEXT_MICRO} font-mono tracking-wider text-slate-400 shrink-0"
                )
        with ui.element("div").classes(f"grid grid-cols-1 sm:grid-cols-2 gap-{SPACE_BASE} mt-2"):
            for ingredient in view.recipe.ingredients:
                with ui.element("div").classes(
                    f"flex flex-row items-baseline justify-between gap-{SPACE_BASE} min-w-0 "
                    f"px-{SPACE_SECTION} py-{SPACE_BASE} {RADIUS_CARD} border border-slate-800 bg-slate-800/30"
                ):
                    ui.label(ingredient.name).classes(
                        f"{TEXT_HEAD} text-slate-200 truncate"
                    )
                    amount, unit = split_quantity(
                        format_quantity(ingredient.name, ingredient.quantity_g)
                    )
                    with ui.element("div").classes(
                        f"flex flex-row items-baseline gap-{SPACE_TIGHT} shrink-0"
                    ):
                        ui.label(amount).classes(f"{TEXT_HEAD} font-mono text-slate-300")
                        if unit:
                            ui.label(unit).classes(f"{TEXT_BODY} font-mono text-slate-400")
                    # NOVA group moves to a tooltip rather than off the card:
                    # every group that reaches here is an allowed one (4 is
                    # rejected in validation), so it is worth being able to
                    # check and not worth a column of its own.
                    ui.tooltip(f"NOVA group {ingredient.nova_group}")

        hairline()

        with ui.element("div").classes(f"flex flex-row items-baseline justify-between gap-{SPACE_SECTION}"):
            ui.label("PREPARATION INSTRUCTIONS").classes(MONO_SECTION_LABEL)
            ui.label("Click a step when complete").classes(
                f"{TEXT_MICRO} text-slate-400 shrink-0"
            )
        with ui.element("div").classes(f"flex flex-col gap-{SPACE_BASE} mt-2"):
            for number, step in enumerate(view.recipe.instructions, start=1):
                step_row(number, step)

        if view.recipe.prep_notes:
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-start gap-{SPACE_BASE} mt-4 px-{SPACE_SECTION} py-{SPACE_BASE} "
                f"{RADIUS_CARD} border border-slate-700 bg-slate-800/40"
            ):
                ui.icon("inventory_2").classes(
                    f"shrink-0 {TEXT_HEAD} text-slate-300 mt-[3px]"
                )
                ui.label(view.recipe.prep_notes).classes(
                    f"min-w-0 {TEXT_BODY} leading-snug text-slate-300"
                )

    with ui.dialog() as detail_dialog:
        with ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} border border-slate-800 p-6 "
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
    # `ui_review.day_target_row` uses for daily targets: `derive_fat_g` is the
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
        ui.label("Eaten out").classes(f"{TEXT_DISPLAY} font-semibold text-slate-100")
        ui.label(
            slot_label(skip_target["slot_id"]) if skip_target["slot_id"] else ""
        ).classes(f"{TEXT_BODY} font-mono uppercase tracking-widest text-slate-400")
        ui.label(
            "Roughly what this meal cost. It comes off the day so the other "
            "meals aren't briefed for budget you've already spent."
        ).classes(f"{TEXT_BODY} text-slate-400 mt-2 mb-1")

        with ui.element("div").classes(f"flex flex-row gap-{SPACE_BASE}"):
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
            f"{TEXT_MICRO} font-mono text-slate-400 mt-1"
        )

        with ui.element("div").classes("flex flex-row justify-between items-center w-full mt-4"):
            ui.button("Not eaten", on_click=clear_skip_estimate).props(
                "flat dense no-caps size=sm"
            ).classes("text-slate-400")
            with ui.element("div").classes(f"flex flex-row gap-{SPACE_BASE}"):
                ui.button("Cancel", on_click=lambda: skip_dialog.close()).props(
                    "flat dense no-caps size=sm"
                ).classes("text-slate-400")
                ui.button("Save", on_click=save_skip_estimate).props(
                    "unelevated dense no-caps size=sm"
                ).classes("bg-sky-400/20 text-sky-200")

    with ui.dialog() as skip_dialog:
        with ui.card().classes("bg-slate-900 border border-slate-800 min-w-[340px]"):
            skip_estimate_body()

    # ---- "send to freezer" modal --------------------------------------------
    # `design-04` §2.2's first capture route: label, recipe id and cook date
    # come straight off the cook card already on screen, leaving only the
    # count and the freeze date to state — same "prefilled from what's
    # already on screen" shape as the "Eaten out?" modal above, and the same
    # shared write path (`state.capture_freezer_item`, via `send_to_freezer`)
    # the pending-surplus "Record" pill below also goes through.
    freezer_target: dict = {
        "slot_id": "", "label": "", "cooked_on": "", "portions": 1, "frozen_on": "",
    }

    def open_send_to_freezer(view: SlotView) -> None:
        defaults = state.freezer_capture_defaults_for(view.id)
        if defaults is None:
            ui.notify(f"{slot_label(view.id)} hasn't been generated yet.", type="warning")
            return
        freezer_target["slot_id"] = view.id
        freezer_target["label"] = defaults.label
        freezer_target["cooked_on"] = defaults.cooked_on or ""
        freezer_target["portions"] = 1
        freezer_target["frozen_on"] = date.today().isoformat()
        freezer_body.refresh()
        freezer_dialog.open()

    async def save_send_to_freezer() -> None:
        error = await state.send_to_freezer(
            ctx.repository,
            freezer_target["slot_id"],
            portions=int(freezer_target["portions"] or 0),
            frozen_on=str(freezer_target["frozen_on"] or "").strip(),
            label=str(freezer_target["label"] or "").strip() or None,
        )
        if error:
            ui.notify(error, type="warning")
            return
        freezer_dialog.close()
        ui.notify("Sent to freezer.", type="positive")
        refreshables.refresh("plan")

    @ui.refreshable
    def freezer_body() -> None:
        ui.label("Send to freezer").classes(f"{TEXT_DISPLAY} font-semibold text-slate-100")
        ui.label(
            slot_label(freezer_target["slot_id"]) if freezer_target["slot_id"] else ""
        ).classes(f"{TEXT_BODY} font-mono uppercase tracking-widest text-slate-400")

        ui.input(label="Label").props("dense outlined").classes(
            f"w-full mt-2 {TEXT_BODY}"
        ).bind_value(freezer_target, "label")

        with ui.element("div").classes(f"flex flex-row gap-{SPACE_BASE} mt-1"):
            ui.number(label="Portions", min=1, step=1, precision=0).props(
                "dense outlined"
            ).classes(f"w-24 {TEXT_BODY}").bind_value(freezer_target, "portions")
            ui.input(label="Frozen on", placeholder="YYYY-MM-DD").props(
                "dense outlined"
            ).classes(f"w-32 {TEXT_BODY}").bind_value(freezer_target, "frozen_on")

        ui.label(f"Cooked {freezer_target['cooked_on']}").classes(
            f"{TEXT_MICRO} text-slate-400 mt-1"
        )

        with ui.element("div").classes(f"flex flex-row justify-end gap-{SPACE_BASE} mt-4"):
            ui.button("Cancel", on_click=lambda: freezer_dialog.close()).props(
                "flat dense no-caps size=sm"
            ).classes("text-slate-400")
            ui.button("Send to freezer", on_click=save_send_to_freezer).props(
                "unelevated dense no-caps size=sm"
            ).classes("bg-sky-400/20 text-sky-200")

    with ui.dialog() as freezer_dialog:
        with ui.card().classes("bg-slate-900 border border-slate-800 min-w-[340px]"):
            freezer_body()

    async def record_surplus(view: SlotView) -> None:
        """The pending-surplus card's "Record" pill — a direct action, no
        form: the count and the date are already settled
        (`design-04` §6a.2), so this is one click rather than a dialog."""
        error = await state.record_freezer_surplus(ctx.repository, view.id)
        if error:
            ui.notify(error, type="warning")
            return
        ui.notify("Recorded to the freezer.", type="positive")
        refreshables.refresh("plan")

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
        # Captured before the swap mutates state: `state.swap_target` is the
        # SlotView from when the modal opened, so its `.recipe` is still the
        # one about to be discarded.
        target_id = state.swap_target.id
        outgoing_recipe = state.swap_target.recipe
        error = state.swap_slot_with_favorite(target_id, favorite["recipe"])
        if error:
            ui.notify(error, type="warning")
            return
        swap_dialog.close()
        refreshables.refresh("plan")
        ui.notify(f"Swapped in \"{favorite['recipe']['name']}\"", type="positive")
        # Same negative signal a discarded regenerate captures — see
        # CLAUDE.md's "Rejection capture". A swap is an equally deliberate
        # "not this one", and was silently exempt before
        # `offer_rejection_prompt` was exposed on `GenerationHandles`.
        if outgoing_recipe is not None:
            generation.offer_rejection_prompt(target_id, outgoing_recipe.name)

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
            ).classes(f"{TEXT_BODY} text-slate-400 italic")

        # `flex-nowrap`, the same fix `ui_shopping.py`'s drawer needed and
        # found by generalising it: Quasar's `.flex` sets `flex-wrap: wrap`,
        # Tailwind's `flex-col` does not undo it, and a wrapping column that
        # outgrows `max-h-64` lays a second column out beside the first rather
        # than overflowing — so this `overflow-y-auto` never fired. The
        # shipped catalog has 36 dinner favourites, so this was not
        # hypothetical; it was just less visible than the drawer's, because a
        # dialog has room to the right and a 420px slide-over does not.
        with ui.element("div").classes(
            f"flex flex-col flex-nowrap gap-{SPACE_TIGHT} max-h-64 overflow-y-auto"
        ):
            for favorite in matches:
                recipe = favorite["recipe"]
                macros = Recipe.model_validate(recipe).per_serving_macros
                is_selected = favorite["id"] == state.swap_selected_id
                with ui.element("div").classes(
                    f"flex flex-row items-center justify-between gap-{SPACE_BASE} p-{SPACE_TIGHT} {RADIUS_CARD} "
                    "cursor-pointer border "
                    + (
                        "bg-emerald-400/15 border-emerald-400/40"
                        if is_selected
                        else "border-slate-800 hover:border-slate-600"
                    )
                ).on("click", lambda f=favorite: select_swap_favorite(f["id"])):
                    with ui.element("div").classes("flex flex-col min-w-0"):
                        ui.label(recipe["name"]).classes(
                            f"{TEXT_BODY} font-semibold truncate"
                        )
                        ui.label(recipe.get("meal_type", "").title()).classes(
                            f"{TEXT_MICRO} text-slate-400"
                        )
                    ui.label(f"{macros['calories']:.0f} kcal").classes(
                        f"{TEXT_MICRO} font-mono text-slate-300 shrink-0"
                    )

        ui.separator()
        with ui.element("div").classes(f"flex flex-row gap-{SPACE_PAGE}"):
            with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} flex-1"):
                ui.label("Target slot budget").classes(
                    f"{TEXT_MICRO} uppercase tracking-wide text-slate-400"
                )
                if budget:
                    for key, short, unit in MACRO_LABELS:
                        ui.label(f"{short}: {budget[key]:.0f}{unit}").classes(
                            f"{TEXT_BODY} text-slate-300"
                        )
                else:
                    ui.label("—").classes(f"{TEXT_BODY} text-slate-400")
            with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} flex-1"):
                ui.label("Selected favorite (per serving)").classes(
                    f"{TEXT_MICRO} uppercase tracking-wide text-slate-400"
                )
                if selected:
                    macros = Recipe.model_validate(selected["recipe"]).per_serving_macros
                    for key, short, unit in MACRO_LABELS:
                        ui.label(f"{short}: {macros[key]:.0f}{unit}").classes(
                            f"{TEXT_BODY} text-emerald-200"
                        )
                else:
                    ui.label("Pick a favorite above").classes(
                        f"{TEXT_BODY} text-slate-400 italic"
                    )

    @ui.refreshable
    def swap_dialog_body() -> None:
        view = state.swap_target
        if view is None:
            return

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_BASE}"):
            ui.label(f"Swap {slot_label(view.id)}").classes(f"{TEXT_HEAD} font-semibold")

            def on_filter_change(event) -> None:
                state.swap_filter = event.value
                refreshables.refresh("swap_matches")

            def on_query_change(event) -> None:
                state.swap_query = event.value or ""
                refreshables.refresh("swap_matches")

            with ui.row().classes(f"w-full items-center flex-nowrap gap-{SPACE_BASE}"):
                ui.select(
                    ["All meal types"] + state.meal_types,
                    value=state.swap_filter or "All meal types",
                    on_change=on_filter_change,
                ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")
                ui.input(
                    placeholder="Search favorites…",
                    value=state.swap_query,
                    on_change=on_query_change,
                ).props("dense outlined clearable").classes(f"flex-1 {TEXT_BODY}")

            swap_matches()

            with ui.row().classes(f"justify-end gap-{SPACE_BASE} mt-1"):
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
            f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[36rem] max-w-full max-h-[85vh] overflow-y-auto"
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

    def on_unlink(view: SlotView) -> None:
        """Undo a leftover link, then repaint — same "plan" topic as linking.

        The source is read *before* the edit: afterwards this slot no longer
        has one, and its former source's new portion count is exactly what
        the notification needs to report.
        """
        source_id = view.source_id
        error = state.unlink_slot(view.id)
        if error:
            ui.notify(error, type="warning")
            return
        refreshables.refresh("plan")
        portions = portions_for(state.spec)
        ui.notify(
            f"{slot_label(view.id)} cooks its own meal again"
            + (
                f" — {slot_label(source_id)} drops to "
                f"{portions.get(source_id, 0)} portions"
                if source_id
                else ""
            ),
            type="positive",
        )

    def meal_card(view: Optional[SlotView], meal_type: str) -> None:
        if view is None:
            view = SlotView(day="", meal_type=meal_type, status=STATUS_SKIP, title="—")
        look = STATUS_STYLES[view.status]
        clickable = "cursor-pointer" if view.recipe else ""
        chain = f"chain chain-{view.chain}" if view.chain is not None else ""

        with ui.element("div").classes(
            f"meal-card card-{view.status} {RADIUS_CARD} p-{SPACE_BASE} flex flex-col gap-{SPACE_TIGHT} min-w-0 "
            f"w-full overflow-hidden transition-shadow duration-150 {SURFACE_CARD_LIFT} "
            f"{look['card']} {chain}"
        ):
            # `w-full` and `overflow-hidden` are both load-bearing, not
            # tidiness. The day column above this is a real 110px-ish flex
            # column (the grid gives it that), but nothing here made the card
            # itself stretch to fill it — measured, a card's own `width:auto`
            # was instead sizing off its widest descendant's unwrapped
            # content (the macro-pill row's ~178px), and every card in the
            # column was following it out to the same width. `w-full` pins
            # the card to its column's actual width instead of trusting
            # stretch to do it; `overflow-hidden` is what then makes that
            # width real for `max-w-full` on the pill and `truncate`/
            # `line-clamp-2` on the title and subtitle, none of which mean
            # anything against a box still sized by its own content. Found as
            # cards visually overlapping their neighbours on Monday/Thursday/
            # Saturday at common laptop widths (1280-1440px) — box-shadow
            # (the hover glow) and NiceGUI's tooltips both still render
            # outside `overflow-hidden`, so neither is affected.
            # Header row is a sibling of the clickable body below, not a child
            # of it — same reasoning as the "Link to next lunch" button: a
            # click on the favorite/swap buttons would otherwise bubble up
            # through `body`'s click handler and open the detail dialog too.
            # No meal-type label here any more (phase 2b of `ui-redesign.md`)
            # — the swim-lane gutter in `canvas()` says which meal type this
            # row is, once per row rather than once per card.
            with ui.element("div").classes(f"flex flex-row items-center justify-end gap-{SPACE_TIGHT}"):
                with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_HAIR}"):
                    if view.recipe is not None:
                        if view.mode == MODE_COOK:
                            recipe_dict = view.recipe.model_dump()
                            favorited = is_favorited(ctx, recipe_dict)
                            fav_button = ui.button(
                                icon="bookmark" if favorited else "bookmark_border",
                                on_click=lambda r=recipe_dict: toggle_favorite(ctx, r),
                            )
                            fav_button.props("dense flat round size=xs").classes(
                                f"min-h-0 p-{SPACE_HAIR} "
                                + (
                                    "text-slate-200"
                                    if favorited
                                    else "text-slate-400 hover:text-slate-300"
                                )
                            )
                            with fav_button:
                                ui.tooltip(
                                    "Remove from favorites" if favorited else "Save to favorites"
                                )
                            # `ac_unit` — the same glyph `PREP_BADGE_STYLES`
                            # already uses for "From Freezer", so the icon
                            # means one thing everywhere it appears rather
                            # than introducing a second symbol for the same
                            # idea. Plain slate: nothing here is a new hue,
                            # per the palette contract.
                            freezer_button = ui.button(
                                icon="ac_unit",
                                on_click=lambda v=view: open_send_to_freezer(v),
                            )
                            freezer_button.props("dense flat round size=xs").classes(
                                f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-slate-200"
                            )
                            with freezer_button:
                                ui.tooltip("Send to freezer")
                        swap_button = ui.button(
                            icon="swap_horiz",
                            on_click=lambda v=view: open_swap_modal(v),
                        )
                        swap_button.props("dense flat round size=xs").classes(
                            f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-sky-300"
                        )
                        with swap_button:
                            ui.tooltip("Swap with a favorite")
                    if view.mode == MODE_COOK and state.week_plan is not None:
                        # Offered even without a recipe (STATUS_MISSING, a
                        # failed day) — a single-meal retry, not the whole
                        # day `regenerate_day` would redo.
                        meal_regen_button = ui.button(icon="refresh")
                        meal_regen_button.props("dense flat round size=xs").classes(
                            f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-emerald-300"
                        )
                        meal_regen_button.on_click(
                            lambda v=view, btn=meal_regen_button: generation.regenerate_meal(v, btn)
                        )
                        with meal_regen_button:
                            ui.tooltip("Regenerate this meal — re-cooks just it")
                    # Icon only, not icon+label: the card's own left-border
                    # colour (`look['card']`) already encodes cook/leftover/
                    # skip/missing, so the text repeated the border's answer
                    # in the one row on the card with no width to spare — see
                    # CLAUDE.md's "one visual risk" for why that row is tight.
                    # The tooltip keeps the label reachable, just not paid for
                    # in width on every one of 28 cards.
                    with ui.element("div").classes(
                        f"flex items-center px-{SPACE_HAIR} py-[1px] {RADIUS_PILL} "
                        f"{look['badge']}"
                    ):
                        ui.icon(look["icon"]).classes(TEXT_MICRO)
                        ui.tooltip(look["label"])

            # The recipe dialog opens from this inner block rather than the
            # card, so the action buttons above are siblings of it and a click
            # on them can't also open the dialog on its way up.
            body = ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT} min-w-0 {clickable}")
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
                    f"{TEXT_BODY} leading-tight font-bold text-slate-100 line-clamp-2"
                )
                title_tooltip_chars = state.config["ui_settings"]["title_tooltip_chars"]
                if len(view.title) > title_tooltip_chars:
                    with title_label:
                        ui.tooltip(view.title)

                tags = " · ".join(part for part in [view.style, view.cuisine] if part)
                if tags:
                    ui.label(tags).classes(f"{TEXT_MICRO} text-slate-400 truncate")

                if view.mode == MODE_LEFTOVER and view.source_label:
                    link_line("↩ from", view.source_label, view.chain_colour)
                if view.feeds:
                    link_line("→ feeds", " · ".join(view.feeds), view.chain_colour)

                if view.prep_badge:
                    badge_look = PREP_BADGE_STYLES[view.prep_badge]
                    prep_badge_el = ui.element("div").classes(
                        f"flex flex-nowrap items-center gap-{SPACE_HAIR} px-{SPACE_TIGHT} py-[1px] {RADIUS_PILL} w-fit mt-0.5 "
                        f"{badge_look['classes']}"
                    )
                    with prep_badge_el:
                        # `flex-nowrap` on the badge and `shrink-0` here: Quasar's
                        # `.flex` sets `flex-wrap: wrap`, which Tailwind's
                        # `flex-row` does not undo, so an icon-plus-text row this
                        # narrow otherwise drops its label below its own glyph.
                        ui.icon(badge_look["icon"]).classes(
                            f"{TEXT_MICRO} shrink-0 leading-none"
                        )
                        ui.label(badge_look["label"]).classes(
                            f"{TEXT_MICRO} font-semibold tracking-wide min-w-0"
                        )
                        if view.prep_origin:
                            ui.tooltip(view.prep_origin)

                if view.macros:
                    # One pill, "450 kcal · 45g P · 30g C · 12g F" — a colour
                    # per macro (MACRO_TINTS) rather than per digit, so the
                    # numbers stay comparable down the column while the
                    # letters carry the identity.
                    with ui.element("div").classes(
                        f"flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-{SPACE_TIGHT} py-{SPACE_HAIR} "
                        f"{RADIUS_PILL} bg-slate-950/40 w-fit max-w-full"
                    ):
                        ui.label(f"{view.macros['calories']:.0f} kcal").classes(
                            f"{TEXT_MICRO} font-mono text-slate-300"
                        )
                        for key, short, unit in MACRO_LABELS[1:]:
                            ui.label("·").classes(f"{TEXT_MICRO} text-slate-400")
                            ui.label(f"{view.macros[key]:.0f}{unit} {short}").classes(
                                f"{TEXT_MICRO} font-mono {MACRO_TINTS[key]}"
                            )

                if view.mode == MODE_SKIP and view.skip_estimate:
                    # Same pill shape as a recipe's macro strip so the day
                    # reads consistently, but slate rather than tinted: these
                    # are estimated, not measured off a recipe, and the strip
                    # should not claim the precision the cooked cards have.
                    with ui.element("div").classes(
                        f"flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-{SPACE_TIGHT} py-{SPACE_HAIR} "
                        f"{RADIUS_PILL} bg-slate-950/40 w-fit max-w-full"
                    ):
                        ui.label(
                            f"~{view.skip_estimate.get('calories', 0):.0f} kcal"
                        ).classes(f"{TEXT_MICRO} font-mono text-slate-400")
                        ui.label("·").classes(f"{TEXT_MICRO} text-slate-400")
                        ui.label(
                            f"~{view.skip_estimate.get('protein_g', 0):.0f}g P"
                        ).classes(f"{TEXT_MICRO} font-mono text-slate-400")

                if view.mode == MODE_COOK and view.portions:
                    ui.label(
                        f"{view.portions} portions · {view.prep_minutes} min"
                        if view.prep_minutes is not None
                        else f"{view.portions} portions"
                    ).classes(f"{TEXT_MICRO} text-emerald-300/70 truncate")

                if view.mode == MODE_LEFTOVER and view.prep_badge and view.prep_minutes is not None:
                    ui.label(f"{view.prep_minutes} min reheat/assemble").classes(
                        f"{TEXT_MICRO} text-slate-400 truncate"
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
                    f"self-start min-h-0 px-{SPACE_TIGHT} py-{SPACE_HAIR} {RADIUS_PILL} {TEXT_MICRO} "
                    "transition-all duration-150 bg-slate-800/60 text-slate-400 "
                    "hover:bg-slate-700/60 hover:text-slate-200"
                )
                with skip_button:
                    ui.tooltip(
                        "Record roughly what this meal cost, so the rest of "
                        "the day is planned around it"
                    )

            if view.mode == MODE_COOK and view.extra_portions:
                # `design-04` §6a: the batch already cooked more than this
                # week eats — `SlotSpec.extra_portions` — and nothing has
                # written it to the freezer yet. `freezer_surplus_for`
                # returns None the moment `record_freezer_surplus` succeeds,
                # which is what makes this pill disappear on its own rather
                # than needing a second flag to track "already recorded".
                surplus = state.freezer_surplus_for(view.id)
                if surplus is not None:
                    record_button = ui.button(
                        f"Record {surplus.extra_portions} to freezer",
                        icon="ac_unit",
                        on_click=lambda v=view: record_surplus(v),
                    )
                    record_button.props("unelevated dense no-caps size=sm").classes(
                        f"self-start min-h-0 px-{SPACE_TIGHT} py-{SPACE_HAIR} {RADIUS_PILL} {TEXT_MICRO} "
                        "transition-all duration-150 bg-slate-800/60 text-slate-400 "
                        "hover:bg-slate-700/60 hover:text-slate-200"
                    )
                    with record_button:
                        ui.tooltip(surplus.total_expression)

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
                    f"self-start min-h-0 px-{SPACE_TIGHT} py-{SPACE_HAIR} {RADIUS_PILL} {TEXT_MICRO} "
                    "transition-all duration-150 "
                    + (
                        "bg-slate-800/60 text-slate-400"
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

            if view.mode == MODE_LEFTOVER:
                # The inverse action, and the only way to undo a link: the
                # link button's own repeat-click hits `leftover_link_error`'s
                # guard rather than toggling. Deliberately quieter than the
                # link pill above (flat, slate) — undoing is the rarer half of
                # the pair, and two filled pills of equal weight would read as
                # a choice rather than an action and its undo.
                unlink_button = ui.button(
                    "Unlink", icon="link_off", on_click=lambda v=view: on_unlink(v)
                )
                unlink_button.props("flat dense no-caps size=sm").classes(
                    f"self-start min-h-0 px-{SPACE_TIGHT} py-{SPACE_HAIR} {RADIUS_PILL} {TEXT_MICRO} "
                    "text-slate-400 hover:text-rose-200"
                )
                with unlink_button:
                    ui.tooltip(
                        "Cook this meal instead of eating leftovers — the "
                        "batch it came from shrinks to match."
                    )

    # ---- prep day: Sunday batch-prep column --------------------------------
    # An eighth grid column, left of day 0, for `week_plan.sunday_prep_session`
    # — raw prep work aggregated across the week's cook events (see
    # `planner.generate_sunday_prep_session`), done ahead of the week rather
    # than repeated per cook day. It is prep work, not an eating slot, so it
    # gets its own indigo accent (`PREP_COLUMN_ACCENT`) rather than any
    # `STATUS_STYLES` treatment, and the column *itself* sits outside
    # `state.days` entirely — there is no slot_id or macro target for the
    # session as a whole.
    #
    # The dishes it batches are a different matter, and phase 6c of
    # `ui-redesign.md` is that distinction. Each one is an ordinary
    # `MODE_COOK` slot with a real recipe on it — the anchor lives on day 1
    # (CLAUDE.md's "the anchor day is therefore always day 1") and the shake
    # candidate on its own training morning — so "batch-cooked meals can't be
    # opened, swapped or regenerated" was never true, only unreachable from
    # the one column that exists to say what is cooking for the week ahead.
    # These cards are that second entry point: the same `open_detail`,
    # `open_swap_modal` and `generation.regenerate_meal` every day card
    # already gets, wired to handles this closure was already holding.

    def prep_candidate_card(view: SlotView) -> None:
        """One dish this prep session covers, as an actionable card.

        Indigo, not `STATUS_STYLES` emerald: this is still the prep column,
        and borrowing the cook accent for a card sitting inside it would read
        as a fifth slot status (the `ui-work` skill's colour contract). The
        icon row is a *sibling* of the clickable body rather than its parent,
        for the same reason `meal_card` splits them — a click on swap or
        regenerate would otherwise bubble into the body's handler and open
        the recipe dialog on its way past.
        """
        # `flex-nowrap` on the card's own column, for the second half of the
        # same Quasar `.flex` trap the column container above hits: a
        # *wrapping* column flex container stretches its children to the flex
        # line's cross size, which is the widest child's max-content — not to
        # the container's own width. Measured without it: the shake's "MON
        # BREAKFAST" eyebrow plus two icons has a 126px max-content against a
        # 123px content box, so the header row refused to shrink (its label's
        # `truncate` never engaged) and dragged the title and portions rows
        # out to 126px with it. `nowrap` makes the container single-line, and
        # a single-line column stretches to its own content box instead.
        with ui.element("div").classes(
            f"{RADIUS_CARD} p-{SPACE_TIGHT} {PREP_COLUMN_ACCENT} flex flex-col flex-nowrap "
            f"gap-{SPACE_HAIR} min-w-0 w-full overflow-hidden shrink-0"
        ):
            # `flex-nowrap` because Quasar's own `.flex` sets `flex-wrap: wrap`
            # and Tailwind's `flex-row` doesn't undo it; `min-w-0`/`truncate`
            # on the label because a flex item won't shrink past its longest
            # word. Both are the standing trap for any icon-plus-text row in
            # this UI, and this column is the narrowest one on the page.
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center justify-between gap-{SPACE_HAIR} min-w-0"
            ):
                ui.label(slot_label(view.id, short=True).upper()).classes(
                    f"{TEXT_MICRO} font-semibold tracking-wide text-indigo-400 truncate min-w-0"
                )
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR} shrink-0"
                ):
                    swap_button = ui.button(
                        icon="swap_horiz", on_click=lambda v=view: open_swap_modal(v)
                    )
                    swap_button.props("dense flat round size=xs").classes(
                        f"min-h-0 p-{SPACE_HAIR} text-indigo-400/70 hover:text-sky-300"
                    )
                    with swap_button:
                        ui.tooltip("Swap with a favorite")
                    regen_button = ui.button(icon="refresh")
                    regen_button.props("dense flat round size=xs").classes(
                        f"min-h-0 p-{SPACE_HAIR} text-indigo-400/70 hover:text-emerald-300"
                    )
                    regen_button.on_click(
                        lambda v=view, btn=regen_button: generation.regenerate_meal(v, btn)
                    )
                    with regen_button:
                        # Says what it costs, because from *this* column the
                        # cost is the column: `regenerate_single_meal` drops
                        # `sunday_prep_session` outright whenever the slot it
                        # re-cooks was a prep candidate ("drop rather than
                        # risk a stale plan"), so this timeline goes back to
                        # its empty state until the week is generated again.
                        ui.tooltip(
                            "Regenerate this meal — clears the prep timeline "
                            "until the week is regenerated"
                        )

            body = ui.element("div").classes(
                f"flex flex-col gap-{SPACE_HAIR} min-w-0 cursor-pointer"
            )
            body.on("click", lambda v=view: open_detail(v))
            with body:
                # Same two-line clamp plus overflow tooltip `meal_card` gives
                # its own titles, and for the same reason: this column is
                # narrower than a day column, so a batch dish's full name
                # reliably doesn't fit.
                title_label = ui.label(view.title).classes(
                    f"{TEXT_BODY} leading-tight font-semibold text-indigo-100 line-clamp-2"
                )
                title_tooltip_chars = state.config["ui_settings"]["title_tooltip_chars"]
                if len(view.title) > title_tooltip_chars:
                    with title_label:
                        ui.tooltip(view.title)
                if view.portions:
                    ui.label(f"{view.portions} portions").classes(
                        f"{TEXT_MICRO} text-indigo-300/70 truncate"
                    )

    def prep_day_column(total_rows: int, views: Dict[str, SlotView]) -> None:
        session = state.week_plan.sunday_prep_session if state.week_plan else None
        # Column 2 (column 1 is the meal-type gutter, phase 2b of
        # `ui-redesign.md`), spanning every row the grid has this repaint —
        # the header row plus one per meal type — because prep work isn't
        # decomposed by meal type the way the day columns are.
        #
        # **The spanning cell is deliberately empty of in-flow content**, and
        # that is load-bearing rather than an extra wrapper for its own sake.
        # A grid item spanning N auto-sized rows still has to *fit*, and auto
        # rows size to their items' max-content, so this column's natural
        # height — its "Batching for" box plus one expansion per prep phase,
        # measured at ~1420px against ~750px of actual cards — was inflating
        # every meal row proportionally and putting ~640px of dead space
        # between a single day's cards. `overflow-y: auto` alone does *not*
        # fix that: it zeroes an item's automatic *minimum* size, which is a
        # different quantity from the max-content contribution auto rows
        # actually use. Taking the content out of flow (`absolute inset-0`)
        # is what drops the contribution to a real zero, so the rows size off
        # the day cards alone and the timeline scrolls inside whatever height
        # they come to. `self-stretch` on the outer cell is the other half:
        # the grid sets `items-start` (so a short card sits at its row's top
        # edge rather than stretching), which would otherwise leave this cell
        # at zero height with nothing for `inset-0` to fill.
        with ui.element("div").classes("relative self-stretch min-w-0").style(
            f"grid-column: 2; grid-row: 1 / span {total_rows};"
        ):
            # `flex-nowrap` is not tidiness — without it this column silently
            # renders *outside itself*. Quasar's own `.flex` sets
            # `flex-wrap: wrap` and Tailwind's `flex-col` doesn't undo it
            # (the standing trap in the `ui-work` skill, here in its
            # column-direction form): a wrapping column flex container whose
            # content is taller than its box starts a *second column* beside
            # the first rather than overflowing, so `overflow-y: auto` above
            # never has anything to scroll. Measured at 1440px once phase 6c
            # added the batching cards: the last prep phase laid itself out
            # at x=423, which is the Monday day column, on top of Monday's
            # own cards and 143px clear of this cell's 135px track. The
            # `absolute` positioning is what makes it invisible rather than
            # merely wrong — nothing reflows to reveal it.
            with ui.element("div").classes(
                f"absolute inset-0 flex flex-col flex-nowrap gap-{SPACE_BASE} min-w-0 "
                "overflow-y-auto"
            ):
                with ui.element("div").classes(
                    f"px-{SPACE_TIGHT} py-{SPACE_HAIR} border-b border-indigo-400/40 flex flex-row "
                    "justify-between items-baseline shrink-0"
                ):
                    ui.label("PREP DAY").classes(
                        f"{TEXT_BODY} font-semibold text-indigo-300 tracking-wide"
                    )
                    ui.icon("checklist").classes(f"{TEXT_BODY} text-indigo-400")
                if session is None:
                    with ui.element("div").classes(
                        f"{RADIUS_CARD} p-{SPACE_BASE} {PREP_COLUMN_ACCENT} border-dashed shrink-0"
                    ):
                        ui.label("Not generated").classes(f"{TEXT_MICRO} text-slate-400")
                        ui.label(
                            "Turn on Bulk prep or Long cook meal in the Generate popup "
                            "for a batch-prep timeline here (or set enable_sunday_prep "
                            "in config.json to do it every week)."
                        ).classes(f"{TEXT_MICRO} text-slate-400 mt-1")
                    return
                # What this session is for, before how — a shopper glancing at
                # the column should see which dishes it batches without opening
                # any of the phase timeline below.
                #
                # Resolved through `candidate_slot_ids` rather than rendered
                # from `meals_included`, which is the model's own prose list
                # and is frozen at generation time: the slot ids are what
                # Python actually folded into the session (see
                # `generate_sunday_prep_session`, which stamps them on after
                # the call precisely so nothing downstream has to trust the
                # model's self-report), and they are also the only handle a
                # click can act on. A dish whose slot has since been swapped
                # therefore shows its *current* recipe here, where the string
                # list would still be naming the one it replaced.
                candidates = [
                    view
                    for view in (views.get(cid) for cid in session.candidate_slot_ids)
                    if view is not None and view.recipe is not None
                ]
                if candidates:
                    ui.label("Batching for").classes(
                        f"{TEXT_MICRO} uppercase tracking-wide text-indigo-400 shrink-0"
                    )
                    for view in candidates:
                        prep_candidate_card(view)
                elif session.meals_included:
                    # A session saved before `candidate_slot_ids` existed has
                    # an empty list, and one saved against a different week
                    # start has ids that no longer resolve — the same
                    # pre-migration tolerance `is_sunday_prepped` extends to
                    # exactly this field. Both fall back to the model's prose
                    # list, inert as it was before phase 6c, rather than to a
                    # column that silently stops saying what it batches.
                    with ui.element("div").classes(
                        f"{RADIUS_CARD} p-{SPACE_BASE} {PREP_COLUMN_ACCENT} shrink-0"
                    ):
                        ui.label("Batching for").classes(
                            f"{TEXT_MICRO} uppercase tracking-wide text-indigo-400 mb-1"
                        )
                        for meal in session.meals_included:
                            ui.label(f"• {meal}").classes(
                                f"{TEXT_MICRO} text-indigo-200 leading-tight"
                            )
                for phase in session.timeline:
                    with ui.expansion(
                        phase.name,
                        caption=f"{phase.active_minutes} active / {phase.passive_minutes} passive min",
                    ).classes(
                        f"{RADIUS_CARD} {PREP_COLUMN_ACCENT} {TEXT_BODY} w-full shrink-0"
                    ).props(
                        f"dense header-class='text-indigo-200 {TEXT_BODY} font-medium'"
                    ):
                        if phase.description:
                            ui.label(phase.description).classes(
                                f"{TEXT_MICRO} text-slate-400 mb-1"
                            )
                        ui.checkbox(f"Done: {phase.name}").props(
                            "dense size=xs color=indigo"
                        ).classes(f"{TEXT_MICRO} text-indigo-200")

    def meal_type_gutter_cell(row: int, meal_type: str) -> None:
        """One sticky swim-lane label, column 1, one row per meal type.

        `sticky left-0` keeps it in view while the grid scrolls horizontally
        beneath it — the same `overflow-x: auto` wrapper phase 2a already
        gives this canvas (`ui_theme.week_grid_scroll()`), which is what
        `sticky` positions itself against here. An opaque background is what
        stops a scrolled-under card showing through it, and `z-10` is what
        keeps that background above the cards rather than beneath them.

        It is `SURFACE_PANEL` rather than the page ground: this cell sits
        *inside* the Plan panel, so painting it slate-950 would draw a dark
        stripe down the left of a slate-900 surface. It was slate-950 before
        the elevation pass only because the two were then the same thing.
        """
        with ui.element("div").classes(
            f"sticky left-0 z-10 flex items-center px-{SPACE_TIGHT} {SURFACE_PANEL}"
        ).style(f"grid-column: 1; grid-row: {row};"):
            ui.label(meal_type.upper()).classes(
                f"{TEXT_MICRO} font-semibold tracking-widest text-slate-400"
            )

    @ui.refreshable
    def canvas() -> None:
        """The week grid: a real CSS grid, not 7 independent flex columns.

        Phase 2b of `ui-redesign.md`. Before this, each day was its own
        `flex flex-col` of 4 cards — a long title in one card made its whole
        column taller with nothing keeping the next day's same meal type
        level with it; they lined up by luck, not by structure. Now every
        cell (a day's header, and each of its meal-type cards) is placed
        explicitly by `grid-column`/`grid-row`, so cells sharing a row are
        genuinely in that row — the browser sizes the row to the tallest one
        and, with `items-start` on the grid, aligns every other cell in it to
        the same top edge regardless of its own height.

        Column 1 is the meal-type gutter, column 2 is the prep-day column
        (spanning every row, since prep work isn't split by meal type), and
        columns 3.. are the days — `WEEK_GRID_COLS` reserves all of this,
        including the gutter track the header's `telemetry()` also carries as
        an empty spacer, which is what keeps this grid's day columns aligned
        with the header's above it.
        """
        views = state.slot_views()
        meal_types = state.meal_types
        total_rows = len(meal_types) + 1  # the header row, plus one per meal type
        with ui.element("div").classes(f"meal-canvas grid {WEEK_GRID_COLS} gap-{SPACE_BASE} w-full items-start"):
            for meal_index, meal_type in enumerate(meal_types):
                meal_type_gutter_cell(meal_index + 2, meal_type)
            prep_day_column(total_rows, views)
            for day_index, day in enumerate(state.days):
                day_column = day_index + 3  # 1 = gutter, 2 = prep, 3.. = days
                with ui.element("div").classes(
                    f"px-{SPACE_TIGHT} py-{SPACE_HAIR} border-b border-slate-800 flex flex-row "
                    "justify-between items-baseline min-w-0"
                ).style(f"grid-column: {day_column}; grid-row: 1;"):
                    # Phase 6a: no day-name label here. The telemetry header
                    # cell directly above this column already prints the day
                    # (with its date), and the two grids share one horizontal
                    # scroll position (`week_grid_scroll`, phase 2a), so the
                    # identity stays overhead at every viewport width — a
                    # second copy was repetition, not hierarchy. The row keeps
                    # what only it has: the day-regenerate icon and the day's
                    # 1-indexed position in the week.
                    with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                        # Only offered once a week exists and this day has
                        # something to cook — regenerating a leftover/skip-only
                        # day would be a no-op API call for nothing.
                        if state.week_plan is not None and state.spec.cook_slots_on(day):
                            regen_button = ui.button(icon="refresh")
                            regen_button.props("dense flat round size=xs").classes(
                                f"min-h-0 p-{SPACE_HAIR} text-slate-400 hover:text-emerald-300"
                            )
                            regen_button.on_click(
                                lambda day=day, btn=regen_button: generation.regenerate_day(day, btn)
                            )
                            with regen_button:
                                ui.tooltip(f"Regenerate {day} — re-cooks just this day")
                    ui.label(str(day_index + 1)).classes(
                        f"{TEXT_MICRO} font-mono text-slate-400"
                    )
                for meal_index, meal_type in enumerate(meal_types):
                    with ui.element("div").classes("min-w-0").style(
                        f"grid-column: {day_column}; grid-row: {meal_index + 2};"
                    ):
                        meal_card(views.get(slot_id(day, meal_type)), meal_type)

    return CardHandles(
        canvas=canvas,
        recipe_detail=recipe_detail,
        swap_matches=swap_matches,
        swap_dialog_body=swap_dialog_body,
        open_detail=open_detail,
        skip_estimate_body=skip_estimate_body,
    )
