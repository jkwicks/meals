"""Insights: a stub destination. Its real content is `future-ideas.md`'s 5c
(trend charts — weight vs. target, calories actual-vs-planned, macro
accuracy, adherence), tracked there rather than here because it's blocked on
data, not engineering: `calculate_adaptive_tdee` itself returns `None` —
keep using the formula — below two weigh-ins spanning `MIN_TREND_SPAN_DAYS`,
and a chart built on less than that would be near-empty or actively
misleading.

This destination exists anyway, per phase 3 of `ui-redesign.md`, as an
honest empty state rather than a missing rail icon: it reads live counts off
`biometrics.json` so the message ages correctly as data accumulates, instead
of a hardcoded "not enough data yet" that goes stale the moment it isn't.
`biometrics` is loaded once in `planner_page()` (there is no per-client
reason to reload it on every repaint — nothing on this page writes it) and
handed in rather than fetched here, because `build_insights` runs
synchronously during page construction and `repository.load_biometrics()` is
a coroutine.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from nutrition_engine import MIN_TREND_SPAN_DAYS
from ui_context import UIContext
from ui_theme import RADIUS_CARD, SPACE_PAGE, SPACE_SECTION, SPACE_TIGHT, TEXT_BODY, TEXT_HEAD, TEXT_MICRO


@dataclass
class InsightsHandles:
    panel: Callable


def build_insights(ctx: UIContext, biometrics: dict) -> InsightsHandles:
    weigh_ins = len(biometrics.get("weigh_ins") or [])
    daily_actuals = len(biometrics.get("daily_actuals") or [])

    def panel() -> None:
        with ui.element("div").classes(
            f"flex flex-col items-center justify-center gap-{SPACE_TIGHT} p-{SPACE_PAGE} "
            f"max-w-lg mx-auto text-center"
        ):
            ui.icon("insights").classes("text-4xl text-slate-600")
            ui.label("Not enough history yet").classes(
                f"{TEXT_HEAD} font-semibold text-slate-300"
            )
            ui.label(
                f"{weigh_ins} weigh-in(s) and {daily_actuals} logged day(s) of intake "
                "on record right now. Trend charts — weight vs. target, calories "
                "actual-vs-planned, macro accuracy, adherence — need real weeks of "
                "runtime data to be more than noise, not more code: this is the "
                "same reason the adaptive TDEE estimate itself stays off until "
                f"there are at least two weigh-ins spanning {MIN_TREND_SPAN_DAYS} "
                "days."
            ).classes(f"{TEXT_BODY} text-slate-500")
            with ui.element("div").classes(
                f"mt-2 p-{SPACE_SECTION} {RADIUS_CARD} border border-slate-800 bg-slate-950/30"
            ):
                ui.label(
                    "Keep syncing Garmin weigh-ins and Cronometer daily actuals — "
                    "see \"Biometric sync\" in CLAUDE.md. Charts land once there's "
                    "something honest to draw."
                ).classes(f"{TEXT_MICRO} text-slate-500")

    return InsightsHandles(panel=panel)
