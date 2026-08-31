"""The header strip: the week date banner and the macro telemetry bars.

Both sections render inside `ui.header(...)` in `ui_app.py`, in this order
(`week_banner`, `telemetry`) — `build_telemetry` only builds the refreshable
functions; the header `with` block that actually places them is still the
page shell's job, since where they render is layout, not this module's
concern.

Used to carry a third section, the per-day `context_pipeline` chip row (28
chips: 3 unconnected stages x 7 days, plus workout x 7 days) and its
click-through detail dialog. Phase 3 of `ui-redesign.md` moved that content
to the Settings destination as a static status list (`ui_settings.py`) —
workout was the only connected stage, and its per-day detail already lives
in the Today destination's day-context strip, so nothing here replaced it.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_context import UIContext
from ui_inspector import InspectorHandles
from ui_theme import (
    MACRO_LABELS,
    MACRO_TINTS,
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_TIGHT,
    TELEMETRY_MARKER_OVERRIDE,
    TELEMETRY_MARKER_TRAINING,
    TEXT_BODY,
    TEXT_MICRO,
    WEEK_GRID_COLS,
    format_day_label,
    telemetry_bar,
)
from week import portions_for, shopping_windows, week_date_range


@dataclass
class TelemetryHandles:
    week_banner: Callable
    telemetry: Callable


def build_telemetry(ctx: UIContext, inspector: InspectorHandles) -> TelemetryHandles:
    state = ctx.state

    # ---- header: week date banner + the week's shape -----------------------
    # A read-only strip — nothing here writes back into state. `state.days`
    # only ever carries weekday names (`week_days` rotates names, not dates),
    # so without the date pill a five-week-old cached plan and this week's
    # plan look identical at a glance. `week_date_range` anchors on the
    # plan's `generated_at` so the banner reflects the week that was actually
    # generated, falling back to today for an un-generated preview.
    #
    # Registered on `"shopping_days"` as well as `"plan"` in `ui_app.py`
    # since phase 6b: the shopping-trip count below is a partition of the
    # week by `state.shop_days`, so changing the shopping days in Settings
    # has to repaint this strip too. That topic used to reach the Plan row
    # this content came from, for the identical reason.

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
            # The week's shape, moved up here from the Plan destination's own
            # header row by phase 6b of `ui-redesign.md`. These four are
            # *readings* of the week — the same kind of thing as the two
            # pills beside them — so they belong in the one reporting strip
            # rather than in a second header inside the destination they
            # describe, which is what left Plan with a heading row carrying
            # both a readout and two controls. Reads `state.spec`, not
            # `week_plan`, so an un-generated week still previews its shape
            # exactly as the Plan row did.
            spec = state.spec
            cooks = spec.cook_slots()
            with ui.element("div").classes(
                f"flex flex-row items-baseline gap-{SPACE_BASE} px-{SPACE_BASE} py-{SPACE_TIGHT} "
                f"{RADIUS_CARD} border border-slate-800 bg-slate-800/40 w-fit"
            ):
                for label, value in [
                    ("Cook sessions", len(cooks)),
                    ("Days cooking", len({slot.day for slot in cooks})),
                    ("Portions", sum(portions_for(spec).values())),
                    ("Shopping trips", len(shopping_windows(state.days, state.shop_days))),
                ]:
                    with ui.element("div").classes(
                        f"flex flex-row items-baseline gap-{SPACE_HAIR}"
                    ):
                        ui.label(str(value)).classes(
                            f"{TEXT_BODY} font-mono font-semibold text-slate-200"
                        )
                        ui.label(label).classes(f"{TEXT_MICRO} text-slate-400")

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
                ui.label("Not generated").classes(f"{TEXT_MICRO} font-mono text-slate-400")
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
        with ui.element("div").classes(f"grid {WEEK_GRID_COLS} gap-{SPACE_BASE} w-full"):
            # Empty — `WEEK_GRID_COLS`'s leading track is `ui_cards.canvas()`'s
            # meal-type gutter. This row has no meal-type rows to label, but
            # the column still has to exist here or every cell after it would
            # land one track left of its counterpart in the canvas below.
            ui.element("div")
            prep_telemetry_cell()
            for day in state.days:
                target = state.targets_for(day)
                totals = state.totals_for(day)
                kcal, kcal_goal = totals["calories"], float(target["calories"])
                protein, protein_goal = totals["protein_g"], float(target["protein_g"])
                overridden = day in state.target_overrides
                # Not `has_training`: the marker's job is to say this day is
                # measured against a preview rather than against what the
                # week was generated for, and a workout that was already in
                # the config when the week was planned changes nothing about
                # that. `target_is_staged` is the same test `targets_for`
                # branches on, so the dot can never appear on a day reading
                # the stored plan (or fail to appear on one that isn't).
                training = state.training_edited_for(day)
                staged = state.target_is_staged(day)
                # Planned fibre against its target, and what Cronometer
                # logged for that calendar date if anything. The target half
                # arrived with `nutrition_engine.calculate_fiber_target_g`;
                # the logged half stays beside the pair rather than under it,
                # because a measurement is not a goal. See
                # `ui_state.fibre_view`, which owns both rules.
                fibre = state.fibre_for(day)
                # Opens the day inspector (`ui_inspector.py`) — a floating
                # overlay, so this never reflows the grid it's clicked from.
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} min-w-0 cursor-pointer"
                ).on("click", lambda d=day: inspector.open(d)):
                    with ui.element("div").classes(
                        f"flex flex-row justify-between items-baseline gap-{SPACE_HAIR} min-w-0"
                    ):
                        # A mark is why the denominator moved: this day is
                        # being measured against a live preview, not the
                        # numbers the week was actually generated for. An
                        # unmarked day is measured against the plan itself.
                        # **One colour, two glyphs.** Both cases mean the
                        # same thing, so both are amber and the glyph says
                        # which — `tune` a target override, `fitness_center`
                        # an edited training session. See the pair's own
                        # comment in `ui_theme`; they were `•` and `⚡` until
                        # the emoji retirement, and had to move together
                        # because they are one set rather than two symbols.
                        marker = (
                            TELEMETRY_MARKER_OVERRIDE
                            if overridden
                            else (TELEMETRY_MARKER_TRAINING if training else "")
                        )
                        marker_tint = (
                            "text-amber-300"
                            if (overridden or training)
                            else "text-slate-300"
                        )
                        # A row rather than one label, because the marker is
                        # an icon now and NiceGUI has no way to put one inside
                        # a label's text. `flex-nowrap` because Quasar's
                        # `.flex` wraps and `flex-row` does not undo it, and
                        # `min-w-0` on the name so `truncate` still has
                        # something to shrink — a flex item's default
                        # `min-width: auto` refuses to go below its longest
                        # word, which would push the marker out of the cell
                        # before the date ever elided.
                        with ui.element("div").classes(
                            f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR} min-w-0"
                        ):
                            # Phase 6a: this is now the *only* place a day's
                            # identity is printed — `ui_cards.canvas()`'s
                            # swim-lane header below dropped its own copy — so
                            # it carries the date as well as the weekday.
                            # `format_day_label` degrades to the bare short
                            # name for a plan generated before
                            # `week_start_date` existed, the same tolerance the
                            # Today tab's picker relies on.
                            ui.label(
                                format_day_label(
                                    day, state.day_date_iso(day), short=True
                                ).upper()
                            ).classes(
                                f"{TEXT_BODY} font-semibold tracking-wider truncate min-w-0 "
                                + marker_tint
                            )
                            if marker:
                                ui.icon(marker).classes(
                                    f"{TEXT_MICRO} shrink-0 leading-none {marker_tint}"
                                )
                        # The date makes this pair too wide for one line at
                        # ordinary laptop widths, and it wraps to two rather
                        # than overflowing into the next day's column —
                        # Quasar's `.flex` sets `flex-wrap: wrap`, which is a
                        # trap everywhere else in this UI and is the wanted
                        # behaviour here. `truncate`/`min-w-0` above and
                        # `shrink-0` here only decide the narrower case where
                        # even one line doesn't fit: the date gives way, never
                        # the figure. A clipped date still reads as its
                        # weekday; a clipped number reads as a different
                        # number.
                        ui.label(f"{kcal:.0f}/{kcal_goal:.0f} kcal").classes(
                            f"{TEXT_MICRO} font-mono text-slate-400 shrink-0"
                        )
                    # Calories: the primary bar, dual-segmented — fill colour
                    # bands on how close the day landed (macro_band), and a
                    # thin marker at the target itself so an overshoot reads as
                    # "past the line" rather than just "a long green bar".
                    telemetry_bar(kcal, kcal_goal, height="9px", bar_scale_limit=bar_scale_limit)
                    with ui.element("div").classes("flex flex-row justify-between items-baseline"):
                        ui.label("protein").classes(
                            f"{TEXT_MICRO} uppercase tracking-wide text-slate-400"
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
                        # Fibre rides on the same row and now carries the
                        # same `actual/target` shape as its neighbours, since
                        # there is a target to divide by. `fibre_view` holds
                        # that rule — including the fallback to a bare
                        # `FIB 32g` for a plan generated before fibre had a
                        # target, and for one generated before `fiber_g`
                        # existed at all. The widget only prints what it
                        # returns.
                        ui.label(fibre.label).classes(
                            f"{TEXT_MICRO} font-mono {MACRO_TINTS['fiber_g']}"
                        )
                        if fibre.logged_label:
                            # The Cronometer figure for the same day, once
                            # `CRONOMETER_MACRO_COLUMNS` captures fibre. A
                            # second label rather than a second number in the
                            # one above, so it can never be read as the
                            # denominator the line above refuses to print —
                            # and slate rather than cyan because it is the
                            # subordinate half of the pair, per the palette
                            # contract in the `ui-work` skill.
                            ui.label(fibre.logged_label).classes(
                                f"{TEXT_MICRO} font-mono text-slate-400"
                            )
                    with ui.tooltip():
                        for key, short, unit in MACRO_LABELS:
                            delta = totals[key] - float(target[key])
                            ui.label(
                                f"{short}: {totals[key]:.0f}{unit} "
                                f"({delta:+.0f} vs {float(target[key]):.0f})"
                            )
                        ui.label(fibre.detail)
                        if overridden:
                            ui.label("target overridden — applies on next generation")
                        if training:
                            ui.label(
                                "training edited — burn folded into target, "
                                "applies on next generation"
                            )
                        if not staged and state.week_plan:
                            ui.label("measured against the targets this week was generated for")

    return TelemetryHandles(
        week_banner=week_banner,
        telemetry=telemetry,
    )
