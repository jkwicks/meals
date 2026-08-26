"""The header strip: the week date banner, the context-pipeline chip row (and
its per-day detail dialog), and the macro telemetry bars.

All four sections render inside `ui.header(...)` in `ui_app.py`, in this
order (`week_banner`, `context_pipeline`, `telemetry`) — `build_telemetry`
only builds the refreshable functions and the pipeline dialog; the header
`with` block that actually places them is still the page shell's job, since
where they render is layout, not this module's concern.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_context import UIContext
from ui_state import pipeline_value
from ui_theme import (
    MACRO_LABELS,
    MACRO_TINTS,
    PIPELINE_STAGES,
    RADIUS_CARD,
    RADIUS_PANEL,
    RADIUS_PILL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    telemetry_bar,
)
from week import week_date_range


@dataclass
class TelemetryHandles:
    week_banner: Callable
    telemetry: Callable
    context_pipeline: Callable
    pipeline_detail: Callable


def build_telemetry(ctx: UIContext) -> TelemetryHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    # ---- context pipeline: what fed a day's plan --------------------------
    # One dialog reused for every day, refreshable off state.pipeline_day —
    # same pattern as recipe_detail/state.focus in ui_cards. The expanded
    # ui.stepper lives here rather than inline because a stepper's headers
    # need more width than a grid-cols-7 column has (telemetry already
    # fights this squeezing three macro numbers into the same column).

    @ui.refreshable
    def pipeline_detail() -> None:
        day = state.pipeline_day
        if day is None:
            return
        ui.label(f"{day} — context pipeline").classes(
            f"{TEXT_HEAD} font-semibold text-slate-200 mb-2"
        )
        with ui.stepper().props("header-nav flat").classes("bg-transparent w-full"):
            for key, label, icon, description, connected in PIPELINE_STAGES:
                value = pipeline_value(state, day, key)
                step = ui.step(label, icon=icon)
                if not connected:
                    step.props("disable")
                with step:
                    ui.label(description).classes(f"{TEXT_BODY} text-slate-400")
                    if connected:
                        ui.label(value if value is not None else "Nothing scheduled").classes(
                            f"{TEXT_HEAD} font-mono mt-1 "
                            + ("text-emerald-300" if value is not None else "text-slate-500")
                        )
                    else:
                        ui.label("Not connected").classes(
                            f"{TEXT_MICRO} uppercase tracking-wide text-slate-600 mt-1"
                        )

    with ui.dialog() as pipeline_dialog:
        with ui.element("div").classes(f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[32rem] max-w-full"):
            pipeline_detail()

    def open_pipeline(day: str) -> None:
        state.pipeline_day = day
        refreshables.refresh("pipeline_detail")
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
        with ui.element("div").classes(f"grid grid-cols-8 gap-{SPACE_BASE} w-full mb-1"):
            # Empty spacer, not a pipeline row — none of PIPELINE_STAGES applies
            # to the prep column, but the grid still needs a column 0 here to
            # stay aligned with telemetry() and canvas() below it.
            ui.element("div")
            for day in state.days:
                with ui.element("div").classes(
                    f"flex flex-row items-center gap-{SPACE_HAIR} cursor-pointer {RADIUS_CARD} "
                    f"px-{SPACE_HAIR} py-{SPACE_HAIR} hover:bg-slate-800/60"
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
                        with ui.icon(icon).classes(f"{TEXT_HEAD} {RADIUS_PILL} p-{SPACE_TIGHT} {look}"):
                            ui.tooltip(tip)
                        if i < len(PIPELINE_STAGES) - 1:
                            ui.icon("chevron_right").classes(f"{TEXT_MICRO} text-slate-700")

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
        with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_BASE} mb-1"):
            with ui.element("div").classes(
                f"flex flex-row items-center gap-{SPACE_TIGHT} px-{SPACE_BASE} py-{SPACE_TIGHT} {RADIUS_CARD} border "
                "border-slate-800 bg-slate-800/40 w-fit"
            ):
                ui.label("📅").classes(TEXT_BODY)
                ui.label(f"Week of {start.strftime(fmt)} – {end.strftime(fmt)}").classes(
                    f"{TEXT_BODY} font-medium text-slate-300 tracking-wide"
                )
            # Unique plant-department ingredients (Produce, Herbs & Spices,
            # Nuts/Seeds & Spreads) across the week's cook events — see
            # `shopping.collect_unique_plants`. Absent until a week is
            # generated, same as every other week_plan-derived reading here.
            plant_count = len(state.week_plan.unique_plants) if state.week_plan else 0
            with ui.element("div").classes(
                f"flex flex-row items-center gap-{SPACE_TIGHT} px-{SPACE_BASE} py-{SPACE_TIGHT} {RADIUS_CARD} border "
                "border-emerald-800/60 bg-emerald-900/20 w-fit"
            ):
                ui.label("🌱").classes(TEXT_BODY)
                ui.label(f"Plant Diversity: {plant_count}").classes(
                    f"{TEXT_BODY} font-medium text-emerald-300 tracking-wide"
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
        with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT} min-w-0"):
            with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                ui.label("PREP").classes(
                    f"{TEXT_BODY} font-semibold tracking-wider text-indigo-300"
                )
            if session is None:
                ui.label("Not generated").classes(f"{TEXT_MICRO} font-mono text-slate-500")
            else:
                ui.label(
                    f"Active Prep: {session.total_active_minutes} / {max_active} mins"
                ).classes(f"{TEXT_MICRO} font-mono text-indigo-200")
                ui.label(
                    f"Passive Time: {session.total_passive_minutes} mins"
                ).classes(f"{TEXT_MICRO} font-mono text-indigo-200/70")

    @ui.refreshable
    def telemetry() -> None:
        bar_scale_limit = state.config["ui_settings"]["bar_scale_limit"]
        with ui.element("div").classes(f"grid grid-cols-8 gap-{SPACE_BASE} w-full"):
            prep_telemetry_cell()
            for day in state.days:
                target = state.targets_for(day)
                totals = state.totals_for(day)
                kcal, kcal_goal = totals["calories"], float(target["calories"])
                protein, protein_goal = totals["protein_g"], float(target["protein_g"])
                overridden = day in state.target_overrides
                training = state.has_training(day)
                with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT} min-w-0"):
                    with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                        # A dot is why the denominator moved: amber for a drawer
                        # target override, emerald for a scheduled workout —
                        # either way this day is being measured against a live
                        # preview, not config.json or the numbers the week was
                        # actually generated for.
                        marker = "•" if overridden else ("⚡" if training else "")
                        ui.label(day[:3].upper() + marker).classes(
                            f"{TEXT_BODY} font-semibold tracking-wider "
                            + (
                                "text-amber-300"
                                if overridden
                                else "text-emerald-300" if training else "text-slate-300"
                            )
                        )
                        ui.label(f"{kcal:.0f}/{kcal_goal:.0f} kcal").classes(
                            f"{TEXT_MICRO} font-mono text-slate-400"
                        )
                    # Calories: the primary bar, dual-segmented — fill colour
                    # bands on how close the day landed (macro_band), and a
                    # thin marker at the target itself so an overshoot reads as
                    # "past the line" rather than just "a long green bar".
                    telemetry_bar(kcal, kcal_goal, height="9px", bar_scale_limit=bar_scale_limit)
                    with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                        ui.label("protein").classes(
                            f"{TEXT_MICRO} uppercase tracking-wide text-slate-500"
                        )
                        ui.label(f"{protein:.0f}/{protein_goal:.0f}g").classes(
                            f"{TEXT_MICRO} font-mono {MACRO_TINTS['protein_g']}"
                        )
                    telemetry_bar(protein, protein_goal, height="5px", bar_scale_limit=bar_scale_limit)
                    with ui.element("div").classes(f"flex flex-row gap-{SPACE_BASE} mt-0.5"):
                        for key, short, unit in MACRO_LABELS[2:]:
                            ui.label(
                                f"{short} {totals[key]:.0f}/{float(target[key]):.0f}{unit}"
                            ).classes(f"{TEXT_MICRO} font-mono {MACRO_TINTS[key]}")
                        # Fibre rides on the same row but carries no
                        # denominator, because there is no fibre target to
                        # divide by (`planner.NUTRIENT_KEYS`) — printing one
                        # would invent a goal the planner never aimed at.
                        # `.get` because a plan generated before `fiber_g`
                        # existed totals without the key.
                        ui.label(f"FIB {totals.get('fiber_g', 0.0):.0f}g").classes(
                            f"{TEXT_MICRO} font-mono {MACRO_TINTS['fiber_g']}"
                        )
                    with ui.tooltip():
                        for key, short, unit in MACRO_LABELS:
                            delta = totals[key] - float(target[key])
                            ui.label(
                                f"{short}: {totals[key]:.0f}{unit} "
                                f"({delta:+.0f} vs {float(target[key]):.0f})"
                            )
                        ui.label(
                            f"fibre: {totals.get('fiber_g', 0.0):.0f}g (tracked, no target)"
                        )
                        if overridden:
                            ui.label("target overridden — applies on next generation")
                        if training:
                            ui.label("training day — burn folded into target, applies on next generation")

    return TelemetryHandles(
        week_banner=week_banner,
        telemetry=telemetry,
        context_pipeline=context_pipeline,
        pipeline_detail=pipeline_detail,
    )
