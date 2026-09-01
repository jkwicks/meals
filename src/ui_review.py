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
from typing import Callable, Dict, Optional

from nicegui import ui

from planner import calculate_daily_targets
from ui_context import UIContext
from ui_generation import GenerationHandles
from ui_state import training_proposals_view
from ui_theme import (
    RADIUS_CARD,
    RADIUS_PANEL,
    SURFACE_INSET,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TARGET_FIELDS,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    TRAINING_TYPE_LABELS,
    training_icon,
)
from week import PIN_ORIGIN_USER, humanize


@dataclass
class ReviewHandles:
    open: Callable
    # Exposed so `ui_app.py` can register them under "targets"/"training" —
    # a training edit changes the day's expanded target too, and the
    # staged-changes bar's own repaint on either topic needs both to already
    # be part of the registry regardless of whether the dialog is open.
    targets_editor: Callable
    training_editor: Callable
    # Registered under "pantry", its own topic: adding or removing a row is
    # the only thing that changes what the staged bar counts, and "plan" (what
    # the chip box this replaced refreshed) would rebuild the 28-card canvas,
    # the telemetry header and the shopping panel for a row nobody has typed
    # into yet.
    pantry_editor: Callable
    # Registered under "pins": pinning is a next-generation input, distinct
    # from a saved grid edit and from the catalog browser's own refresh topic.
    recipe_pin_editor: Callable


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

    # Bar height in px for the target-curve column — tall enough that a
    # training-uplift segment (typically a fifth or less of the day) still
    # reads as a visible sliver, short enough that seven of them plus their
    # number inputs fit one dialog screen without scrolling.
    TARGET_BAR_HEIGHT_PX = 96

    def day_target_row(
        day: str,
        target: dict,
        uplift_calories: float,
        max_calories: float,
        baseline_calories: Optional[float] = None,
    ) -> None:
        """One day's target as a bar (filled = base, amber = training uplift,
        dashed ghost = what the day would aim at unoverridden), with the same
        editable calorie/protein/carb inputs stacked beneath it.

        `baseline_calories` is the ghost's height and is passed in rather than
        read here, because it costs a `planning_config()` rebuild — see
        `targets_editor`, which computes it only for the days that actually
        draw one. It is deliberately **not** `weekly_schedule`'s stated
        figure: on a macro set to `auto` that number is inert (the shipped
        config says 1000 kcal on a Thursday the engine puts at 1722), so a
        ghost drawn at it would measure the override against a line nothing
        plans from.

        The row is built once and then mutated in place — the derived-fat
        readout has to keep up with every keystroke, and repainting a section
        that owns the focused input would take the cursor out of the number
        being typed. Only `telemetry` is refreshed on an edit, because that is
        the only other thing on screen showing a target live; the
        staged-changes bar's own count only needs to be right the next time
        *it* repaints, which "targets"/"plan" already cover elsewhere.

        The bar's own proportions (`base_pct`/`uplift_pct`/`ghost_pct`) are
        computed once, from the `target`/`uplift_calories`/`max_calories` this
        was built with — they do **not** live-update per keystroke, the same
        "only mutate what has to" rule the fat label and reset button already
        followed before this became a bar. `targets_editor`'s own rebuild (on
        a week-start change, or the "targets"/"training" refresh topics) is
        what keeps them honest; a single day's edit changing the week's max
        is a rare, cosmetic staleness, not a correctness problem, and rescaling
        every column on every digit typed would defeat the point of not
        rebuilding this section on edit.
        """
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

        base_calories = max(0.0, target["calories"] - uplift_calories)
        base_pct = min(100.0, (base_calories / max_calories * 100) if max_calories else 0.0)
        uplift_pct = min(
            100.0 - base_pct, (uplift_calories / max_calories * 100) if max_calories else 0.0
        )
        ghost_calories = float(
            target["calories"] if baseline_calories is None else baseline_calories
        )
        ghost_pct = min(100.0, (ghost_calories / max_calories * 100) if max_calories else 0.0)

        with ui.element("div").classes(f"flex flex-col items-stretch gap-{SPACE_TIGHT} flex-1 min-w-0"):
            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label(day[:3]).classes(f"{TEXT_MICRO} font-semibold text-slate-300")
                reset = (
                    ui.button(icon="undo", on_click=on_reset)
                    .props("dense flat size=xs")
                    .classes("min-h-0 p-0 text-amber-300")
                )
                reset.set_visibility(day in state.target_overrides)
                with reset:
                    ui.tooltip(f"Reset {day} to its calculated target")

            # The shape: a filled base segment, a lighter training-uplift
            # segment stacked on top of it, and — only on an overridden day —
            # a dashed ghost line at the config.json value, so an override
            # reads as "how far this day now sits from the file" rather than
            # a bare number next to another bare number.
            with ui.element("div").classes(
                "relative w-full rounded bg-slate-800/50 overflow-hidden"
            ).style(f"height: {TARGET_BAR_HEIGHT_PX}px"):
                ui.element("div").classes("absolute inset-x-0 bottom-0 bg-slate-500/70").style(
                    f"height: {base_pct:.1f}%"
                )
                if uplift_calories > 0:
                    with ui.element("div").classes(
                        "absolute inset-x-0 bg-slate-300/70"
                    ).style(f"height: {uplift_pct:.1f}%; bottom: {base_pct:.1f}%"):
                        ui.tooltip(f"+{uplift_calories:.0f} kcal training uplift")
                if day in state.target_overrides:
                    ui.element("div").classes(
                        "absolute inset-x-0 border-t border-dashed border-slate-100/60"
                    ).style(f"bottom: {ghost_pct:.1f}%")

            # Fat is shown, never typed: it is whatever energy is left once
            # protein and carbs are paid for.
            fat_label = ui.label(f"fat {target['fat_g']:.0f}g").classes(
                f"{TEXT_MICRO} font-mono text-slate-400 text-center"
            )

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
                    .classes(f"w-full {TEXT_MICRO}")
                )

    @ui.refreshable
    def targets_editor() -> None:
        """The whole week's targets as one bar-per-day row, left to right in
        the same order as the grid — the "target curve" of `ui-redesign.md`'s
        phase 4.2, replacing the old 21-spinbox stack of per-day panels.

        `config`/`targets_by_day`/`uplift_by_day`/`max_calories` are each
        computed once here rather than once per day inside
        `day_target_row` (which used to call `state.planned_targets(day)` —
        itself a full `planning_config()` rebuild — seven times over). One
        `planning_config()` call for the whole row is what makes the
        uplift-vs-base split affordable without adding calls beyond what the
        row already made before it needed the uplift figure at all.

        Refreshable only so a change of week start reorders it, or a
        target/training edit elsewhere changes the shape; edits inside a row
        never refresh this section themselves (see `day_target_row`).
        """
        config = state.planning_config()
        uplift_by_day = config.get("training_uplift", {})
        targets_by_day = {day: calculate_daily_targets(day, config) for day in state.days}
        max_calories = max((t["calories"] for t in targets_by_day.values()), default=0) or 1

        # Only the overridden days draw a ghost line, and only they pay for
        # the extra `planning_config()` rebuild that finding their unoverridden
        # baseline costs. Usually none of them.
        baseline_by_day = {
            day: state.baseline_targets(day)["calories"]
            for day in state.days
            if day in state.target_overrides
        }

        with ui.element("div").classes(f"flex flex-row items-stretch gap-{SPACE_TIGHT} w-full"):
            for day in state.days:
                day_target_row(
                    day,
                    targets_by_day[day],
                    uplift_by_day.get(day, {}).get("calories", 0.0),
                    max_calories,
                    baseline_by_day.get(day),
                )

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

    def proposal_row(row) -> None:
        """One "add this" / "drop that" line, with its evidence and two buttons.

        Colour carries nothing here — the icon does. `add`/`remove` says which
        direction the proposal goes and `training_icon` says what kind of
        session it is, which is the same division `TRAINING_TYPE_ICONS`
        already relies on and the reason no new hue was needed: every one in
        `ui_theme.py` is spoken for twice over.
        """
        with ui.element("div").classes(
            f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT}"
        ):
            ui.icon("add" if row.adds else "remove").classes("text-slate-400 shrink-0")
            ui.icon(training_icon(row.session.type)).classes("text-slate-400 shrink-0")
            with ui.element("div").classes("flex flex-col min-w-0 grow"):
                ui.label(row.title).classes(f"{TEXT_BODY} text-slate-200 truncate")
                ui.label(f"{row.detail} · {row.evidence}").classes(
                    f"{TEXT_MICRO} text-slate-400 truncate"
                )

            async def on_accept(session=row.session) -> None:
                await state.accept_training_proposal(ctx.repository, session)
                # "training" repaints this block and the rows below it;
                # "targets" because an accepted session expands that day's
                # budget and pins a meal, exactly as a typed one does.
                refreshables.refresh("training", "targets")

            def on_dismiss(session=row.session) -> None:
                state.dismiss_training_proposal(session)
                refreshables.refresh("training")

            accept = ui.button(icon="check", on_click=on_accept).props(
                "dense flat size=xs"
            ).classes("min-h-0 p-0 text-slate-300 shrink-0")
            with accept:
                ui.tooltip(
                    "Remove it from the schedule and save to config/schedule.json"
                    if not row.adds
                    else "Add it to the schedule and save to config/schedule.json"
                ).classes("max-w-xs")
            dismiss = ui.button(icon="close", on_click=on_dismiss).props(
                "dense flat size=xs"
            ).classes("min-h-0 p-0 text-slate-400 shrink-0")
            with dismiss:
                ui.tooltip("Not now — hidden until the page reloads").classes("max-w-xs")

    def proposals_block() -> None:
        """Garmin's recorded week, offered against the declared one.

        Rendered inside `training_editor` rather than as its own refreshable
        so the two can never disagree: accepting a proposal changes the rows
        below it, and a separately-refreshed block would leave a suggestion
        on screen for a session already in the list underneath it.

        Every state prints, including the three that propose nothing —
        "nothing recorded yet", "not enough history" and "your schedule
        already matches" are three different answers, and an empty block
        spells them identically. Same reasoning as the adaptive-TDEE readout
        in Settings.
        """
        view = training_proposals_view(state.training_proposals())
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_TIGHT} {RADIUS_CARD} "
            "border border-slate-800 bg-slate-950/30"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
            ):
                ui.icon("watch").classes("text-slate-400 shrink-0")
                ui.label(view.headline).classes(
                    f"{TEXT_BODY} font-semibold text-slate-200 min-w-0"
                )
            ui.label(view.evidence).classes(f"{TEXT_MICRO} text-slate-400")
            for row in view.rows:
                proposal_row(row)
            if view.has_proposals:
                ui.label(
                    "Accepting writes the session to config/schedule.json — this "
                    "is your standing week, not a change staged for the next run."
                ).classes(f"{TEXT_MICRO} text-slate-400")

    @ui.refreshable
    def training_editor() -> None:
        proposals_block()
        if not state.training_schedule:
            ui.label("No workouts scheduled.").classes(
                f"{TEXT_MICRO} text-slate-400 italic"
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
                    ).classes("min-h-0 p-0 text-slate-400")
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
                    burn_input = (
                        ui.number(
                            label="Burn (kcal)",
                            value=session.get("estimated_burn_kcal", 0),
                            min=0,
                            step=10,
                            precision=0,
                            on_change=training_field_handler(index, "estimated_burn_kcal"),
                        )
                        .props("dense outlined debounce=350")
                        .classes(f"flex-1 {TEXT_BODY}")
                    )

                    # A MET-derived starting point, applied only on click — see
                    # CLAUDE.md's "Derive the training burn". Deliberately not
                    # a live recompute on every type/duration change: that
                    # would mean rebuilding this whole row (there's no way to
                    # update `burn_input.value` without holding a reference to
                    # it, and that reference only exists inside this closure),
                    # and rebuilding while an adjacent field in the same row
                    # is still settling its debounce is exactly the
                    # focus-theft trap `training_field_handler` already
                    # avoids by refreshing "targets" rather than "training".
                    # An explicit click is a deliberate action, the same "safe
                    # to disrupt" idiom `on_remove`/`day_target_row.on_reset`
                    # already use.
                    estimate = state.estimate_burn(
                        session.get("type"), session.get("duration_minutes", 0)
                    )
                    if estimate is not None:

                        def apply_estimate(
                            i: int = index, number: ui.number = burn_input
                        ) -> None:
                            new_estimate = state.estimate_burn(
                                state.training_schedule[i].get("type"),
                                state.training_schedule[i].get("duration_minutes", 0),
                            )
                            if new_estimate is None:
                                return
                            state.training_schedule[i]["estimated_burn_kcal"] = new_estimate
                            number.value = new_estimate

                        estimate_button = ui.button(
                            icon="calculate", on_click=apply_estimate
                        ).props("dense flat size=xs").classes("min-h-0 p-0 text-slate-400")
                        with estimate_button:
                            ui.tooltip(
                                f"Estimate ≈ {estimate:.0f} kcal from type, duration and "
                                "your latest weigh-in — click to apply"
                            )

        def on_add() -> None:
            state.add_training_session()
            refreshables.refresh("training")

        ui.button("Add session", icon="add", on_click=on_add).props(
            "dense flat no-caps size=sm"
        ).classes("text-slate-400 mt-1")

    # ---- pantry -------------------------------------------------------------

    def pantry_field_handler(index: int, key: str):
        """One `on_change` per (row, field), index and key baked in via closure
        arguments exactly as `training_field_handler` does — and for the same
        reason: a row removed between render and edit must not write into
        whichever entry has since taken its place.

        Refreshes nothing. The staged-changes bar counts pantry *rows* and
        nothing on screen reads a row's contents, so repainting on every
        keystroke would rebuild the row the cursor is sitting in for no visible
        change — the focus-theft trap the training editor sidesteps by
        refreshing `"targets"` rather than `"training"`, avoided here by having
        nothing to refresh at all. Adding and removing rows do refresh, because
        those change the count the bar shows.
        """

        def handler(event) -> None:
            value = event.value
            if key == "quantity_g":
                # Blank clears back to unquantified rather than storing 0: an
                # emptied field means "I don't know how much", where 0 would
                # mean "there is none", and the ledger reads those differently
                # — an item at 0 drops out of the prompt entirely.
                state.pantry[index][key] = None if value in (None, "") else float(value)
            else:
                state.pantry[index][key] = str(value or "").strip()

        return handler

    def on_pantry_add() -> None:
        state.pantry.append({"item": "", "quantity_g": None})
        refreshables.refresh("pantry")

    @ui.refreshable
    def pantry_editor() -> None:
        if not state.pantry:
            ui.label("Nothing to use up.").classes(f"{TEXT_MICRO} text-slate-400 italic")
        for index, item in enumerate(state.pantry):

            def on_remove(i: int = index) -> None:
                del state.pantry[i]
                refreshables.refresh("pantry")

            with ui.row().classes(
                f"w-full items-center flex-nowrap gap-{SPACE_BASE}"
            ):
                ui.input(
                    label="Item",
                    value=item.get("item", ""),
                    on_change=pantry_field_handler(index, "item"),
                ).props(
                    'dense outlined debounce=350 placeholder="chicken thighs"'
                ).classes(f"flex-1 min-w-0 {TEXT_BODY}")
                ui.number(
                    label="Grams",
                    value=item.get("quantity_g"),
                    min=0,
                    step=50,
                    precision=0,
                    on_change=pantry_field_handler(index, "quantity_g"),
                ).props("dense outlined debounce=350").classes(f"w-24 {TEXT_BODY}")
                ui.button(icon="delete", on_click=on_remove).props(
                    "dense flat size=xs"
                ).classes("min-h-0 p-0 text-slate-400")

    # ---- the weekly preset pick --------------------------------------------

    # `(select, read-its-options)` for every select whose option list comes
    # out of config — i.e. every one a preset can replace under the user.
    # Populated as the selects are built, below.
    catalog_selects: list = []

    def preset_block() -> None:
        """The pick, its one-line diff, and nothing else.

        Built once and **mutated in place** — `summary.set_text` after a
        pick, rather than a `@ui.refreshable` body — which is the same
        decision `day_target_row` makes for the same reason: the section
        owns the control that fired the change, and repainting it rebuilds
        the widget the user just interacted with.
        """
        view = state.preset_view()
        if not view.available:
            # Nothing to pick from is the state of every checkout with no
            # `config/presets.json`, and an empty select would be a control
            # announcing that a feature exists rather than offering one.
            return

        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_HAIR} {SURFACE_INSET} {RADIUS_CARD} p-{SPACE_BASE}"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT}"
            ):
                ui.icon("tune").classes("text-slate-400 shrink-0")
                ui.label("This week's preset").classes(f"{TEXT_BODY} text-slate-300")

            async def on_pick(event) -> None:
                await state.set_preset(ctx.repository, event.value or None)
                updated = state.preset_view()
                summary.set_text(updated.summary)
                # The catalogs the two selects below offer are read from
                # config once at build time, and a preset may have just
                # replaced either — set_options rather than a repaint, for
                # the same build-once reason this block is not refreshable.
                # Via a registry because the diet-style select is
                # conditional: a direct reference would be a NameError on
                # every config whose `diet_styles` is empty.
                for select, options in catalog_selects:
                    select.set_options(options())
                # A preset can move every one of these: the day targets, the
                # training schedule, the pantry rows and the grid itself.
                refreshables.refresh("plan", "targets", "training", "pantry")

            ui.select(
                view.options,
                value=view.active,
                on_change=on_pick,
            ).props("dense outlined clearable").classes(f"w-full {TEXT_BODY}")

            summary = ui.label(view.summary).classes(f"{TEXT_MICRO} text-slate-400")
            ui.label(
                "Saved as soon as you pick it — the preset is a standing "
                "choice, and next week starts from this one."
            ).classes(f"{TEXT_MICRO} text-slate-400")

    # ---- user recipe pins --------------------------------------------------

    pin_pick = {"day": state.days[0] if state.days else "", "meal_type": ""}

    @ui.refreshable
    def recipe_pin_editor() -> None:
        """A day / meal / recipe row plus every active weekly pin."""
        cook_slots = state.spec.cook_slots()
        if not cook_slots:
            ui.label("There are no cook slots to pin.").classes(
                f"{TEXT_MICRO} text-slate-400"
            )
            return

        cook_days = list(dict.fromkeys(slot.day for slot in cook_slots))
        if pin_pick["day"] not in cook_days:
            pin_pick["day"] = cook_days[0]
        meal_options_for = lambda day: {  # noqa: E731 - local widget projection
            slot.meal_type: humanize(slot.meal_type).title()
            for slot in cook_slots
            if slot.day == day
        }
        meals = meal_options_for(pin_pick["day"])
        if pin_pick["meal_type"] not in meals:
            pin_pick["meal_type"] = next(iter(meals))

        def selected_slot_id() -> str:
            return f"{pin_pick['day']}:{pin_pick['meal_type']}"

        def recipe_options() -> Dict[str, str]:
            return {
                record["id"]: (record.get("recipe") or {}).get("name", "Unnamed recipe")
                for record in state.recipe_pin_options(selected_slot_id())
            }

        def sync_recipes() -> None:
            slot = state.spec.by_id().get(selected_slot_id())
            recipes.set_options(recipe_options())
            recipes.set_value(slot.recipe_id if slot else None)

        def choose_day(event) -> None:
            pin_pick["day"] = event.value
            options = meal_options_for(event.value)
            pin_pick["meal_type"] = next(iter(options))
            meal_select.set_options(options)
            meal_select.set_value(pin_pick["meal_type"])
            sync_recipes()

        def choose_meal(event) -> None:
            pin_pick["meal_type"] = event.value
            sync_recipes()

        def choose_recipe(event) -> None:
            error = state.pin_recipe_for_slot(selected_slot_id(), event.value)
            if error:
                ui.notify(error, type="negative", multi_line=True, close_button=True)
                sync_recipes()
                return
            refreshables.refresh("pins")

        with ui.element("div").classes(f"flex flex-row gap-{SPACE_TIGHT} w-full"):
            ui.select(
                {day: day[:3] for day in cook_days},
                value=pin_pick["day"],
                label="Day",
                on_change=choose_day,
            ).props("dense outlined").classes(f"w-28 {TEXT_BODY}")
            meal_select = ui.select(
                meals,
                value=pin_pick["meal_type"],
                label="Meal",
                on_change=choose_meal,
            ).props("dense outlined").classes(f"w-32 {TEXT_BODY}")
            current = state.spec.by_id().get(selected_slot_id())
            recipes = ui.select(
                recipe_options(),
                value=current.recipe_id if current else None,
                label="Recipe",
                on_change=choose_recipe,
            ).props("dense outlined clearable").classes(f"flex-1 {TEXT_BODY}")

        ui.label(
            "Only catalog recipes matching the meal and your banned-ingredient/NOVA rules "
            "are offered. Clear Recipe to hand the slot back to generation."
        ).classes(f"{TEXT_MICRO} text-slate-400")

        user_pins = [
            slot for slot in state.spec.cook_slots()
            if slot.recipe_id and slot.recipe_pin_origin == PIN_ORIGIN_USER
        ]
        names = {
            record.get("id"): (record.get("recipe") or {}).get("name", "Recipe")
            for record in state.recipe_catalog
        }
        for slot in user_pins:
            with ui.element("div").classes(
                f"flex flex-row items-center gap-{SPACE_TIGHT} w-full"
            ):
                ui.icon("push_pin").classes("text-amber-300")
                ui.label(
                    f"{slot.day[:3]} {humanize(slot.meal_type)} — "
                    f"{names.get(slot.recipe_id, 'Recipe')}"
                ).classes(f"{TEXT_BODY} text-slate-300 flex-1")

                def clear_pin(slot_id=slot.id) -> None:
                    state.pin_recipe_for_slot(slot_id, None)
                    refreshables.refresh("pins")

                ui.button(icon="close", on_click=clear_pin).props(
                    "dense flat round size=sm"
                ).classes("text-slate-400")

    with ui.dialog() as dialog:
        with ui.element("div").classes(
            # Widened from 32rem to fit the target curve's 7 side-by-side
            # bar-columns (`targets_editor`) without cramming — every other
            # section here is a single-column form that's just as readable
            # wider.
            f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[40rem] max-w-full max-h-[85vh] overflow-y-auto "
            f"flex flex-col gap-{SPACE_SECTION}"
        ):
            with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                ui.icon("fact_check").classes(f"{TEXT_HEAD} text-amber-300")
                ui.label("Review pending changes").classes(f"{TEXT_HEAD} font-semibold")

            # The weekly pick sits at the very top, above the batch toggles it
            # can override, because this dialog is where the week's shape is
            # settled — the Generate button opens it rather than running the
            # week. It is deliberately *above* the "everything below is
            # staged" line as well as above the controls: that sentence is
            # true of everything under it and false of this, which persists
            # the moment it changes.
            preset_block()

            ui.label(
                "Everything below is staged for the next generation only — "
                "nothing here is saved to config.json until you generate."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            ui.number(
                label="People per meal",
                min=1,
                max=8,
                step=1,
                precision=0,
            ).bind_value(state, "servings").props("dense outlined").classes(
                f"w-full {TEXT_BODY}"
            )

            cuisines = ui.select(
                cuisine_options,
                label="Cuisines this week",
                multiple=True,
            ).bind_value(state, "cuisine_override").props(
                "dense outlined use-chips"
            ).classes(f"w-full {TEXT_BODY}")
            catalog_selects.append((
                cuisines,
                lambda: {c: humanize(c).title() for c in state.config["cuisines"]},
            ))
            ui.label("Leave empty to use config.json's cuisine list.").classes(
                f"{TEXT_MICRO} text-slate-400 -mt-2"
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
                ).classes(f"{TEXT_MICRO} text-slate-400 -mt-2")

            if diet_style_options:
                diet_styles = ui.select(
                    diet_style_options,
                    label="Diet styles this week",
                    multiple=True,
                ).bind_value(state, "diet_style_override").props(
                    "dense outlined use-chips"
                ).classes(f"w-full {TEXT_BODY}")
                catalog_selects.append((
                    diet_styles,
                    lambda: {
                        key: entry["label"]
                        for key, entry in state.config["diet_styles"].items()
                    },
                ))
                ui.label(
                    "Leave empty to use config.json's active diet styles."
                ).classes(f"{TEXT_MICRO} text-slate-400 -mt-2")

            with ui.expansion("Pin a recipe", icon="push_pin").classes("w-full").props(
                f"dense header-class='{TEXT_BODY} px-0'"
            ):
                with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT}"):
                    recipe_pin_editor()

            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label("Bulk prep").classes(f"{TEXT_BODY} text-slate-300")
                ui.switch().bind_value(state, "bulk_prep_enabled").props(
                    "dense size=sm color=teal"
                )
            ui.label(
                "Batches one dinner across several days automatically — which "
                "days is decided for you, no picking required. Absorbs the old "
                "Sunday-prep timeline (no longer tied to Sunday)."
            ).classes(f"{TEXT_MICRO} text-slate-400 -mt-2")

            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label("Long cook meal").classes(f"{TEXT_BODY} text-slate-300")
                ui.switch().bind_value(state, "long_cook_enabled").props(
                    "dense size=sm color=teal"
                )
            ui.label(
                "One dinner this week is a genuinely long, hands-off oven "
                "roast/braise — a different day than bulk prep's, if both are "
                "on."
            ).classes(f"{TEXT_MICRO} text-slate-400 -mt-2")

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
                ).classes(f"{TEXT_MICRO} text-slate-400 mb-1")
                with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT}"):
                    training_editor()

            with ui.expansion("Pantry clear", icon="kitchen").classes("w-full").props(
                f"dense header-class='{TEXT_BODY} px-0'"
            ):
                # A row editor rather than the free-text chip box this was,
                # because a chip cannot hold two fields and the grams are the
                # whole point: without them nothing could tell that one tin of
                # tuna had already been written into Monday's lunch when
                # Thursday's dinner asked for it. Same shape as the training
                # editor above, which is this file's established pattern for
                # an editable list of records.
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} w-full"
                ):
                    pantry_editor()
                ui.button(
                    "Add item", icon="add", on_click=on_pantry_add
                ).props("dense flat no-caps size=sm").classes("text-slate-400 mt-1")
                ui.label(
                    "A priority, not a rule: the model prefers these where "
                    "they fit and never bends a meal's style, cuisine or "
                    "macro budget to use one up. Give an amount in grams and "
                    "it is spent as the week generates, so the same 600g is "
                    "not written into four different meals; leave it blank "
                    "and the item is simply named every time, as before. "
                    "They are still ordinary ingredients, so they still "
                    "appear on the shopping list."
                ).classes(f"{TEXT_MICRO} text-slate-400 mt-1")

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
        pantry_editor=pantry_editor,
        recipe_pin_editor=recipe_pin_editor,
    )
