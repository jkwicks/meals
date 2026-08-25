"""The left drawer: the generate/shuffle/reload buttons, global controls
(week start, servings, shopping days, model), per-day macro targets, pantry
clear, training schedule, and the recipe catalog (search, favorites list,
rename/delete, import, and a link into the full-screen browser).

`build_drawer(ctx, generation, prep_options, rename_dialog, catalog_browser)`
needs `generation` (see `ui_generation`) for the "Generate" and "Reload from
disk" buttons at the top, `rename_dialog` (see `ui_catalog`) so this list's
own edit icon can open the one shared rename dialog rather than owning a
second copy of it, and `catalog_browser` (see `ui_catalog_browser`) for the
"Browse all" button — this list stays a quick 7-row search, the browser is
where you actually review the whole catalog.
"""

from dataclasses import dataclass
from typing import Callable, Dict

from nicegui import ui

from planner import api_key_error, import_external_recipe, selectable_models, short_error
from ui_catalog import RenameDialogHandles, delete_recipe, toggle_favorite
from ui_catalog_browser import CatalogBrowserHandles
from ui_context import UIContext
from ui_generation import GenerationHandles
from ui_prep_options import PrepOptionsHandles
from ui_theme import TARGET_FIELDS, TRAINING_TYPE_LABELS, WEEK_SELECTION_LABELS
from week import portions_for, shopping_windows, slot_label


@dataclass
class DrawerHandles:
    week_summary: Callable
    targets_editor: Callable
    training_editor: Callable
    favorites_list: Callable


def build_drawer(
    ctx: UIContext,
    generation: GenerationHandles,
    prep_options: PrepOptionsHandles,
    rename_dialog: RenameDialogHandles,
    catalog_browser: CatalogBrowserHandles,
) -> DrawerHandles:
    state = ctx.state
    REPOSITORY = ctx.repository
    refreshables = ctx.refreshables

    # ---- this week summary -------------------------------------------------

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

    # ---- per-day macro targets ---------------------------------------------

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
            refreshables.refresh("telemetry")

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
            refreshables.refresh("targets")

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

    # ---- training & activity schedule --------------------------------------

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
            refreshables.refresh("targets")

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
                refreshables.refresh("training")

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
            refreshables.refresh("training")

        ui.button("Add session", icon="add", on_click=on_add).props(
            "dense flat no-caps size=sm"
        ).classes("text-slate-400 mt-1")

    # ---- recipe catalog & import --------------------------------------------

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
        refreshables.refresh("favorites")
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
                ui.button(icon="bolt", on_click=lambda: prep_options.open())
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
                    "Opens cuisine, diet-style, bulk-prep and long-cook options, then "
                    "generates every meal set to cook in this grid — one API call per "
                    "meal type, covering each day it's cooked. Overwrites the selected "
                    "week's cached plan and appends to history."
                )

            def on_shuffle_styles() -> None:
                state.shuffle_styles()
                refreshables.refresh("plan")
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
                "Reload from disk", icon="refresh", on_click=generation.reload_from_disk
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
                refreshables.refresh("plan")

            ui.select(
                all_days,
                label="Week starts on",
                on_change=on_week_start,
            ).bind_value(state, "week_start").props("dense outlined").classes(
                "w-full text-xs"
            )

            def on_servings(event) -> None:
                state.servings = int(event.value or 1)
                refreshables.refresh("plan")

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
                # Shop days *are* the window boundaries, so this repartitions
                # every list in the shopping drawer.
                refreshables.refresh("shopping_days")

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

            with ui.row().classes("w-full items-center justify-between gap-2 mb-1"):
                ui.label().classes("text-[10px] text-slate-500").bind_text_from(
                    state,
                    "recipe_catalog",
                    backward=lambda catalog: f"{len(catalog)} recipe(s)",
                )
                ui.button(
                    "Browse all", icon="open_in_full", on_click=catalog_browser.open
                ).props("dense flat no-caps size=sm").classes("text-slate-300")

            def on_catalog_search(event) -> None:
                state.catalog_search = (event.value or "").strip()
                refreshables.refresh("favorites")

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
                                    on_click=lambda r=recipe: toggle_favorite(ctx, r),
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
                                    on_click=lambda e=entry: rename_dialog.open(e),
                                ).props("dense flat round size=xs").classes(
                                    "min-h-0 p-0.5 text-slate-500 hover:text-sky-300"
                                )
                                ui.button(
                                    icon="delete",
                                    on_click=lambda rid=entry["id"]: delete_recipe(ctx, rid),
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

    return DrawerHandles(
        week_summary=week_summary,
        targets_editor=targets_editor,
        training_editor=training_editor,
        favorites_list=favorites_list,
    )
