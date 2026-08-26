"""The review dialog: every input to the *next* generation in one place —
cuisine picker, western-style share slider, diet-style picker, bulk-prep and
long-cook toggles, people per meal, per-day macro targets, training schedule,
and the pantry list. None of it is written to config.json (see
`PlannerState.target_overrides`/`pantry`/`training_schedule`/`cuisine_override`
and siblings); it's all merged into `planning_config()` for whatever the next
"Generate" click does.

Renamed from `ui_prep_options.py` when phase 3 of `ui-redesign.md` folded the
drawer's Daily Targets/Pantry Clear/Training Schedule sections in here — the
same category of thing (inputs to the next run) that used to be split across
two surfaces for no reason other than the drawer having run out of room.
"People per meal" joins them here rather than moving to Settings because
`generation_spec()` already force-reapplies `state.servings` on every run and
`PlannerState.spec` ignores it once a week exists (see `_shape()`) — it was
already behaving as a per-run option, just living in the wrong place.

`build_review(ctx, generation)` is built once per page load, after
`build_generation` (its own "Generate" button is what actually starts a run,
via `generation.run_generation`) and before `build_staged_bar` (whose
"Review" button opens this dialog).
"""

from dataclasses import dataclass
from typing import Callable, Dict

from nicegui import ui

from ui_context import UIContext
from ui_generation import GenerationHandles
from ui_theme import (
    RADIUS_CARD,
    RADIUS_PANEL,
    SPACE_BASE,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TARGET_FIELDS,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    TRAINING_TYPE_LABELS,
)
from week import humanize


@dataclass
class ReviewHandles:
    open: Callable
    # Exposed so `ui_app.py` can register them under "targets"/"training" —
    # a training edit changes the day's expanded target too, and the
    # staged-changes bar's own repaint on either topic needs both to already
    # be part of the registry regardless of whether the dialog is open.
    targets_editor: Callable
    training_editor: Callable


def build_review(ctx: UIContext, generation: GenerationHandles) -> ReviewHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    # Static for the page's lifetime — config.json's cuisine/diet-style
    # catalog doesn't change after PlannerState.load(), so this is built once
    # rather than inside a refreshable body.
    cuisine_options = {c: humanize(c).title() for c in state.config["cuisines"]}
    diet_style_options = {
        key: entry["label"] for key, entry in state.config["diet_styles"].items()
    }

    # ---- per-day macro targets ---------------------------------------------

    def day_target_row(day: str) -> None:
        """One day's editable calorie/protein/carb targets.

        The row is built once and then mutated in place — the derived-fat
        readout has to keep up with every keystroke, and repainting a section
        that owns the focused input would take the cursor out of the number
        being typed. Only `telemetry` is refreshed on an edit, because that is
        the only other thing on screen showing a target live; the
        staged-changes bar's own count only needs to be right the next time
        *it* repaints, which "targets"/"plan" already cover elsewhere.
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

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT}"):
            with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_TIGHT}"):
                ui.label(day).classes(f"{TEXT_BODY} font-semibold text-slate-200")
                with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                    # Fat is shown, never typed: it is whatever energy is left
                    # once protein and carbs are paid for.
                    fat_label = ui.label(f"fat {target['fat_g']:.0f}g").classes(
                        f"{TEXT_MICRO} font-mono text-slate-500"
                    )
                    reset = (
                        ui.button(icon="undo", on_click=on_reset)
                        .props("dense flat size=xs")
                        .classes("min-h-0 p-0 text-amber-300")
                    )
                    reset.set_visibility(day in state.target_overrides)
                    with reset:
                        ui.tooltip(f"Reset {day} to config.json")
            with ui.row().classes(f"w-full items-center flex-nowrap gap-{SPACE_BASE}"):
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
                        .classes(f"flex-1 {TEXT_BODY}")
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
            ui.label().classes(f"{TEXT_MICRO} text-amber-300").bind_text_from(
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
                f"{TEXT_MICRO} text-slate-500 italic"
            )
        for index, session in enumerate(state.training_schedule):

            def on_remove(i: int = index) -> None:
                state.remove_training_session(i)
                refreshables.refresh("training")

            with ui.element("div").classes(
                f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_TIGHT} {RADIUS_CARD} border border-slate-800 bg-slate-950/30"
            ):
                with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                    ui.select(
                        state.days,
                        value=session.get("day"),
                        on_change=training_field_handler(index, "day"),
                    ).props("dense outlined").classes(f"flex-1 min-w-0 {TEXT_BODY}")
                    ui.button(icon="delete", on_click=on_remove).props(
                        "dense flat size=xs"
                    ).classes("min-h-0 p-0 text-slate-500")
                with ui.row().classes(f"w-full items-center flex-nowrap gap-{SPACE_BASE}"):
                    ui.input(
                        label="Time (HH:MM)",
                        value=session.get("time", ""),
                        on_change=training_field_handler(index, "time"),
                    ).props("dense outlined debounce=350").classes(f"flex-1 {TEXT_BODY}")
                    ui.select(
                        TRAINING_TYPE_LABELS,
                        value=session.get("type"),
                        on_change=training_field_handler(index, "type"),
                    ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")
                with ui.row().classes(f"w-full items-center flex-nowrap gap-{SPACE_BASE}"):
                    ui.number(
                        label="Duration (min)",
                        value=session.get("duration_minutes", 0),
                        min=0,
                        step=5,
                        precision=0,
                        on_change=training_field_handler(index, "duration_minutes"),
                    ).props("dense outlined debounce=350").classes(f"flex-1 {TEXT_BODY}")
                    ui.number(
                        label="Burn (kcal)",
                        value=session.get("estimated_burn_kcal", 0),
                        min=0,
                        step=10,
                        precision=0,
                        on_change=training_field_handler(index, "estimated_burn_kcal"),
                    ).props("dense outlined debounce=350").classes(f"flex-1 {TEXT_BODY}")

        def on_add() -> None:
            state.add_training_session()
            refreshables.refresh("training")

        ui.button("Add session", icon="add", on_click=on_add).props(
            "dense flat no-caps size=sm"
        ).classes("text-slate-400 mt-1")

    # ---- pantry -------------------------------------------------------------

    def on_pantry(event) -> None:
        state.pantry = [
            str(item).strip() for item in (event.value or []) if str(item).strip()
        ]
        # The staged-changes bar counts pantry items live — nothing else on
        # screen reads `state.pantry`, so before the bar existed this handler
        # had nothing worth refreshing.
        refreshables.refresh("plan")

    with ui.dialog() as dialog:
        with ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[32rem] max-w-full max-h-[85vh] overflow-y-auto "
            f"flex flex-col gap-{SPACE_SECTION}"
        ):
            with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                ui.icon("fact_check").classes(f"{TEXT_HEAD} text-amber-300")
                ui.label("Review pending changes").classes(f"{TEXT_HEAD} font-semibold")
            ui.label(
                "Everything below is staged for the next generation only — "
                "nothing here is saved to config.json until you generate."
            ).classes(f"{TEXT_MICRO} text-slate-500")

            ui.number(
                label="People per meal",
                min=1,
                max=8,
                step=1,
                precision=0,
            ).bind_value(state, "servings").props("dense outlined").classes(
                f"w-full {TEXT_BODY}"
            )

            ui.select(
                cuisine_options,
                label="Cuisines this week",
                multiple=True,
            ).bind_value(state, "cuisine_override").props(
                "dense outlined use-chips"
            ).classes(f"w-full {TEXT_BODY}")
            ui.label("Leave empty to use config.json's cuisine list.").classes(
                f"{TEXT_MICRO} text-slate-600 -mt-2"
            )

            baseline_cuisines = state.config.get("baseline_cuisines") or []
            if baseline_cuisines:
                with ui.element("div").classes("flex flex-row items-center justify-between"):
                    ui.label("Min. western-style share").classes(f"{TEXT_BODY} text-slate-300")
                    ui.label().classes(f"{TEXT_BODY} font-mono text-slate-400").bind_text_from(
                        state, "baseline_cuisine_share", backward=lambda share: f"{share:.0%}"
                    )
                ui.slider(min=0.0, max=1.0, step=0.05).bind_value(
                    state, "baseline_cuisine_share"
                ).props("dense color=teal")
                ui.label(
                    "Floor on how much of the week's cook days go to "
                    + ", ".join(humanize(c).title() for c in baseline_cuisines)
                    + " before the rest rotates freely. 0% turns the floor off"
                    " for this run."
                ).classes(f"{TEXT_MICRO} text-slate-600 -mt-2")

            if diet_style_options:
                ui.select(
                    diet_style_options,
                    label="Diet styles this week",
                    multiple=True,
                ).bind_value(state, "diet_style_override").props(
                    "dense outlined use-chips"
                ).classes(f"w-full {TEXT_BODY}")
                ui.label(
                    "Leave empty to use config.json's active diet styles."
                ).classes(f"{TEXT_MICRO} text-slate-600 -mt-2")

            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label("Bulk prep").classes(f"{TEXT_BODY} text-slate-300")
                ui.switch().bind_value(state, "bulk_prep_enabled").props(
                    "dense size=sm color=teal"
                )
            ui.label(
                "Batches one dinner across several days automatically — which "
                "days is decided for you, no picking required. Absorbs the old "
                "Sunday-prep timeline (no longer tied to Sunday)."
            ).classes(f"{TEXT_MICRO} text-slate-600 -mt-2")

            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label("Long cook meal").classes(f"{TEXT_BODY} text-slate-300")
                ui.switch().bind_value(state, "long_cook_enabled").props(
                    "dense size=sm color=teal"
                )
            ui.label(
                "One dinner this week is a genuinely long, hands-off oven "
                "roast/braise — a different day than bulk prep's, if both are "
                "on."
            ).classes(f"{TEXT_MICRO} text-slate-600 -mt-2")

            with ui.expansion("Daily targets", icon="track_changes").classes("w-full").props(
                f"dense header-class='{TEXT_BODY} px-0'"
            ):
                with ui.element("div").classes(f"flex flex-col gap-{SPACE_BASE}"):
                    targets_editor()

            with ui.expansion("Training schedule", icon="fitness_center").classes("w-full").props(
                f"dense header-class='{TEXT_BODY} px-0'"
            ):
                ui.label(
                    "A workout's burn is added to that day's target, and the "
                    "meal closest to it is pinned for glycogen replenishment."
                ).classes(f"{TEXT_MICRO} text-slate-500 mb-1")
                with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT}"):
                    training_editor()

            with ui.expansion("Pantry clear", icon="kitchen").classes("w-full").props(
                f"dense header-class='{TEXT_BODY} px-0'"
            ):
                # `new_value_mode="add-unique"` is what makes this a free-text
                # multi-item box rather than a picker: there is no fixed list
                # of things that can be in your fridge. Seeded from config so
                # an inventory set on disk shows up already entered.
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
                ).classes(f"w-full {TEXT_BODY}")
                ui.label(
                    "A priority, not a rule: the model prefers these where "
                    "they fit and never bends a meal's style, cuisine or "
                    "macro budget to use one up. They are still ordinary "
                    "ingredients, so they still appear on the shopping list."
                ).classes(f"{TEXT_MICRO} text-slate-500 mt-1")

            with ui.row().classes(f"justify-end gap-{SPACE_BASE} mt-1"):
                ui.button("Close", on_click=dialog.close).props("dense flat no-caps")

                async def on_generate() -> None:
                    dialog.close()
                    await generation.run_generation(generate_button)

                generate_button = ui.button(
                    "Generate", icon="bolt", on_click=on_generate
                ).props("dense no-caps")

    return ReviewHandles(
        open=dialog.open,
        targets_editor=targets_editor,
        training_editor=training_editor,
    )
