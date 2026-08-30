"""Insights: the five readouts CHANGE-QUEUE.md's trend-charts item scoped —
weight against target, planned calories against logged, macro accuracy,
adherence tiles and the weigh-in table (`future-ideas.md` 5c ·
`ui-redesign.md` finding 3, the last open finding from the original review).

**This page was a stub for a reason, and the reason has not gone away.** The
item is blocked on runtime data, and it still is: measured the day this
shipped, the live file held 6 weigh-ins across a 5-day span against a floor
of 7, and 5 logged days against the item's own suggested 14. What changed is
not the data but *who evaluates it*. The stub printed the counts and named
the rule; v0.30.0 found that a reader holding five of everything concluded
the estimate was on when it had never once fired. A page that says "not
enough yet" in prose it cannot check is the same failure at a larger scale,
and the fix is the same one `ui_state.adaptive_tdee_view` applied: every
section below asks `ui_state`'s panel function for a verdict, prints it, and
draws the chart only where the answer is `drawable`.

So the page fills itself as rows land, rather than waiting on another
release to notice they have. That is also why the four view models live in
`ui_state.py` and not here — per the `ui-work` skill, logic worth testing
leaves the widget module, and "is this series worth drawing" is the only
logic on this page.

`biometrics` is loaded once in `planner_page()` and handed in, as before
(nothing on this page writes it, and `build_insights` is synchronous while
`load_biometrics` is a coroutine); the history and adherence halves come off
`ctx.state`, which already holds both from `PlannerState.load`. The panel is
`@ui.refreshable` and registered on `"plan"` and `"adherence"`, because
generating a week appends the history entries the intake charts pair against
and marking a meal is the only thing that fills the adherence tiles.
"""

from dataclasses import dataclass
from typing import Callable, List

from nicegui import ui

from ui_context import UIContext
from ui_state import (
    INSIGHT_EMPTY,
    INSIGHT_SPARSE,
    INSIGHT_THIN,
    InsightPanel,
    adaptive_tdee_view,
    adherence_panel,
    intake_panel,
    macro_accuracy_panel,
    weight_trend_panel,
)
from ui_theme import (
    ADHERENCE_MARK_ICONS,
    ADHERENCE_STATUS_LABELS,
    BAND_COLOURS,
    CHART_AXIS,
    CHART_GRID,
    CHART_HEIGHT,
    CHART_HEIGHT_SHORT,
    CHART_INK,
    CHART_MACRO_COLOURS,
    CHART_MUTED,
    CHART_REFERENCE_DASH,
    MONO_SECTION_LABEL,
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    chart_scaffold,
)

# Which glyph a section's verdict wears. Slate throughout and no hue at all,
# per the palette contract: amber is a staged reading and emerald is the cook
# status, so either on a chart heading would read as a slot state. The set is
# the same three-way distinction the verdict itself draws — nothing yet, not
# enough yet, here it is — and `INSIGHT_THIN` deliberately shares `ready`'s
# glyph, because a thin chart *is* drawn and the caption under it is where
# the shortness is said.
INSIGHT_STATE_ICONS = {
    INSIGHT_EMPTY: "radio_button_unchecked",
    INSIGHT_SPARSE: "hourglass_empty",
    INSIGHT_THIN: "show_chart",
}
INSIGHT_ICON_FALLBACK = "show_chart"

CARD_CLASSES = (
    f"flex flex-col gap-{SPACE_TIGHT} w-full min-w-0 p-{SPACE_SECTION} "
    f"{RADIUS_CARD} border border-slate-800 bg-slate-950/30"
)


@dataclass
class InsightsHandles:
    panel: Callable


def _section(title: str, view: InsightPanel):
    """One readout's card — heading, verdict, evidence — returned for filling.

    Every section is this shape whether or not it has a chart in it, which is
    what makes an empty one read as a section that is waiting rather than as
    a hole in the page. The caller adds the chart inside the returned element
    when `view.drawable`.
    """
    card = ui.element("div").classes(CARD_CLASSES)
    with card:
        ui.label(title).classes(MONO_SECTION_LABEL)
        with ui.element("div").classes(
            f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
        ):
            ui.icon(
                INSIGHT_STATE_ICONS.get(view.state, INSIGHT_ICON_FALLBACK)
            ).classes(f"{TEXT_BODY} shrink-0 text-slate-400")
            ui.label(view.headline).classes(
                f"{TEXT_HEAD} font-semibold min-w-0 text-slate-200"
            )
        ui.label(view.detail).classes(f"{TEXT_MICRO} text-slate-400")
    return card


def _chart(options: dict, height: str = CHART_HEIGHT) -> None:
    ui.echart(options).classes(f"w-full {height}")


def build_insights(ctx: UIContext, biometrics: dict) -> InsightsHandles:
    state = ctx.state

    @ui.refreshable
    def panel() -> None:
        series = state.biometrics or biometrics
        target_kg = (state.config.get("user_profile") or {}).get("target_weight_kg")
        weight = weight_trend_panel(series, target_kg)
        intake = intake_panel(state.history, series)
        macros = macro_accuracy_panel(state.history, series)
        adherence = adherence_panel(state.adherence, series)

        # The verdict, not the rule — the line the stub version of this page
        # existed to print, kept at the top because it is what explains a
        # chart that has points on it and no rate under it.
        adaptive = adaptive_tdee_view(
            series, state.planning_config().get("dynamic_basis")
        )

        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_SECTION} w-full min-w-0 max-w-4xl mx-auto "
            f"p-{SPACE_PAGE}"
        ):
            with ui.element("div").classes(CARD_CLASSES):
                ui.label("MEASURED TDEE").classes(MONO_SECTION_LABEL)
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
                ):
                    ui.icon(
                        "trending_up" if adaptive.measuring else "trending_flat"
                    ).classes(f"{TEXT_BODY} shrink-0 text-slate-400")
                    ui.label(adaptive.headline).classes(
                        f"{TEXT_HEAD} font-semibold min-w-0 text-slate-200"
                    )
                ui.label(adaptive.detail).classes(f"{TEXT_MICRO} text-slate-400")

            with _section("WEIGHT AGAINST TARGET", weight):
                if weight.drawable:
                    _chart(_weight_options(weight))
                    _weigh_in_table(weight.rows)

            with _section("PLANNED AGAINST LOGGED", intake):
                if intake.drawable:
                    _chart(_intake_options(intake))

            with _section("MACRO ACCURACY", macros):
                if macros.drawable:
                    _chart(_macro_options(macros), CHART_HEIGHT_SHORT)

            with _section("WHAT ACTUALLY HAPPENED", adherence):
                if adherence.state != INSIGHT_EMPTY:
                    _adherence_tiles(adherence)

    return InsightsHandles(panel=panel)


def _weight_options(view) -> dict:
    """Weigh-ins as points, the smoothed line through them, target as a rule.

    Two series from two estimators, which `nutrition_engine` is emphatic
    about keeping apart: `smooth_series` is for the eye and the rate under
    the chart comes from the least-squares fit. Drawing the smoothed line
    *and* the raw points is what stops the smoothing from hiding the scatter
    it was applied to.

    `scale: True` on the y-axis (from `chart_scaffold`) is load-bearing here:
    a zero-based axis renders a real 0.6 kg week as a flat line 99 kg above
    the origin.
    """
    options = chart_scaffold()
    options["xAxis"]["data"] = list(view.labels)
    options["yAxis"]["axisLabel"]["formatter"] = "{value} kg"
    trend = {
        "name": "Trend",
        "type": "line",
        "data": list(view.smoothed),
        "smooth": True,
        "symbol": "none",
        "lineStyle": {"color": CHART_INK, "width": 2},
        # `itemStyle` as well as `lineStyle`, because the *legend* swatch
        # reads the former: set only the latter and ECharts labels a white
        # line with a blue chip out of its own default palette. A legend that
        # disagrees with the mark it names is worse than no legend, which is
        # why the chart below this one has none at all.
        "itemStyle": {"color": CHART_INK},
    }
    if view.target_in_range:
        # The target is a rule across the plot, not a series: it has no per-day
        # value and drawing it as one would put a second, flat "measurement"
        # in the legend. Dashed, per `CHART_REFERENCE_DASH` — the reference is
        # always the dashed one on this page. Drawn only while the target is
        # near enough to share the axis — see `WeightTrendPanel
        # .target_in_range`, and the caption, which states the gap either way.
        trend["markLine"] = {
            "silent": True,
            "symbol": "none",
            "label": {
                "formatter": f"target {view.target_kg:g} kg",
                # `CHART_AXIS`, not `CHART_MUTED`: this is the one part of the
                # markLine that is text, and it reads at 10px.
                "color": CHART_AXIS,
                "fontSize": 10,
                "position": "insideEndTop",
            },
            "lineStyle": {"color": CHART_MUTED, "type": CHART_REFERENCE_DASH},
            "data": [{"yAxis": view.target_kg}],
        }
    options["series"] = [
        {
            "name": "Weigh-in",
            "type": "scatter",
            "data": list(view.weights),
            "symbolSize": 6,
            "itemStyle": {"color": CHART_MUTED},
        },
        trend,
    ]
    return options


def _intake_options(view) -> dict:
    """Logged calories as banded bars, the plan as the dashed line over them.

    Each bar takes `ui_theme.macro_band`'s existing on/near/off fill rather
    than a new tolerance — this is the same question the telemetry header
    asks of the same day, and two answers to it would be free to disagree on
    a screen that shows both.

    **No legend, because a per-point encoding has no swatch.** One chip
    cannot stand for five differently-banded bars without being wrong about
    four of them, and the alternative — a neutral chip labelled "Logged" —
    would imply the colours mean nothing. The mapping is in the panel's
    caption instead (`ui_state.intake_panel`), which is where every other
    explanation on this page lives.
    """
    options = chart_scaffold()
    options.pop("legend")
    options["grid"]["top"] = 8
    options["xAxis"]["data"] = list(view.labels)
    options["series"] = [
        {
            "name": "Logged",
            "type": "bar",
            "barMaxWidth": 18,
            "data": [
                {"value": value, "itemStyle": {"color": BAND_COLOURS[band]}}
                for value, band in zip(view.logged, view.bands)
            ],
        },
        {
            "name": "Planned",
            "type": "line",
            "data": list(view.planned),
            "symbol": "none",
            "step": "middle",
            "lineStyle": {"color": CHART_MUTED, "type": CHART_REFERENCE_DASH},
        },
    ]
    return options


def _macro_options(view) -> dict:
    """Each macro's logged mean as a percentage of its planned mean.

    **A percentage axis, because a shared absolute one would be a lie about
    scale** — 2000 kcal and 79 g of protein cannot share a value axis, and
    four separate charts would say less than one. The dashed rule at 100 is
    the plan itself, drawn the same way the weight chart draws its target.

    Colour is `CHART_MACRO_COLOURS`, i.e. `MACRO_TINTS` in the units ECharts
    takes: categorical, saying *which macro*, which is the one job those hues
    already have everywhere else in the UI.
    """
    # The scaffold's two axes, swapped: this is the one horizontal chart on
    # the page, and swapping is what keeps its gridlines, ticks and tooltip
    # identical to the two above it rather than hand-built a third time.
    options = chart_scaffold()
    options["grid"]["top"] = 8
    options.pop("legend")
    options["xAxis"], options["yAxis"] = options["yAxis"], options["xAxis"]
    options["xAxis"]["axisLabel"]["formatter"] = "{value}%"
    options["xAxis"]["splitLine"] = {"lineStyle": {"color": CHART_GRID}}
    # Percentages start at zero, unlike the weight chart's `scale: True`
    # framing: there the interesting range is a kilogram inside a hundred,
    # here a bar's *length against 100* is the entire reading, and an axis
    # starting at 47% would draw a day at half its plan as a stub beside a
    # full one.
    options["xAxis"]["scale"] = False
    options["xAxis"]["min"] = 0
    options["yAxis"]["data"] = [row.label for row in reversed(view.rows)]
    options["yAxis"]["splitLine"] = {"show": False}
    options["series"] = [
        {
            "name": "Logged vs planned",
            "type": "bar",
            "barMaxWidth": 14,
            "data": [
                {
                    "value": round(row.pct or 0, 1),
                    "itemStyle": {"color": CHART_MACRO_COLOURS[row.key]},
                }
                for row in reversed(view.rows)
            ],
            "markLine": {
                "silent": True,
                "symbol": "none",
                "label": {"show": False},
                "lineStyle": {"color": CHART_MUTED, "type": CHART_REFERENCE_DASH},
                "data": [{"xAxis": 100}],
            },
        }
    ]
    return options


def _weigh_in_table(rows: List) -> None:
    """The item's fifth readout, on the chart's own windowed rows.

    A plain element table rather than `ui.table`: four columns of ten
    characters do not need a Quasar grid, and this way the delta column can
    carry the same `+0.00`/`-0.00` convention the staged-changes bar uses for
    a signed difference.
    """
    with ui.element("div").classes(
        f"flex flex-col gap-{SPACE_HAIR} w-full min-w-0 pt-{SPACE_BASE}"
    ):
        for row in reversed(rows):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-baseline gap-{SPACE_SECTION} "
                f"min-w-0 {TEXT_MICRO}"
            ):
                ui.label(row.date).classes("text-slate-400 shrink-0 w-20")
                ui.label(f"{row.weight_kg:.2f} kg").classes(
                    "text-slate-300 shrink-0 w-20 text-right"
                )
                ui.label(
                    "" if row.delta_kg is None else f"{row.delta_kg:+.2f}"
                ).classes("text-slate-400 shrink-0 w-16 text-right")
                ui.label(
                    "" if row.body_fat_pct is None else f"{row.body_fat_pct:.1f}% fat"
                ).classes("text-slate-400 min-w-0")


def _adherence_tiles(view) -> None:
    """Three mark counts and the two ways a session was done.

    The percentage prints "of marks" in words, because that is genuinely its
    denominator — see `ui_state.AdherencePanel.as_planned_pct`. Glyphs are
    `ADHERENCE_MARK_ICONS`, unchanged from the cards that raised the marks,
    so a tile and the tick that fed it are recognisably the same thing.
    """
    with ui.element("div").classes(
        f"flex flex-row flex-wrap items-stretch gap-{SPACE_BASE} w-full pt-{SPACE_BASE}"
    ):
        for status, count in view.counts.items():
            with ui.element("div").classes(
                f"flex flex-col gap-{SPACE_HAIR} px-{SPACE_SECTION} py-{SPACE_BASE} "
                f"{RADIUS_CARD} bg-slate-900/60 min-w-[5.5rem]"
            ):
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR} min-w-0"
                ):
                    ui.icon(ADHERENCE_MARK_ICONS[status]).classes(
                        f"{TEXT_BODY} shrink-0 text-slate-400"
                    )
                    ui.label(str(count)).classes(
                        f"{TEXT_HEAD} font-semibold text-slate-200"
                    )
                ui.label(ADHERENCE_STATUS_LABELS[status]).classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
        if view.as_planned_pct is not None:
            with ui.element("div").classes(
                f"flex flex-col gap-{SPACE_HAIR} px-{SPACE_SECTION} py-{SPACE_BASE} "
                f"{RADIUS_CARD} bg-slate-900/60 min-w-[5.5rem]"
            ):
                ui.label(f"{view.as_planned_pct:.0f}%").classes(
                    f"{TEXT_HEAD} font-semibold text-slate-200"
                )
                ui.label("eaten, of marks").classes(f"{TEXT_MICRO} text-slate-400")
