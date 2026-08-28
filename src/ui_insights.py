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

**The counts alone were the trap this page fell into.** It printed them and
then named the rule — "at least two weigh-ins spanning MIN_TREND_SPAN_DAYS
days" — without ever evaluating it, so a reader holding five weigh-ins and
five logged days concluded the estimate was on. Measured against the real
file it was not, and had never been: the weigh-ins sat inside a span of
three days against a floor of seven. `ui_state.adaptive_tdee_view` evaluates
the rule rather than restating it, and this page now prints its verdict —
the same verdict, from the same call, that Settings' Daily Targets panel
prints beside the TDEE it names.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_context import UIContext
from ui_state import adaptive_tdee_view
from ui_theme import RADIUS_CARD, SPACE_PAGE, SPACE_SECTION, SPACE_TIGHT, TEXT_BODY, TEXT_HEAD, TEXT_MICRO


@dataclass
class InsightsHandles:
    panel: Callable


def build_insights(ctx: UIContext, biometrics: dict) -> InsightsHandles:
    weigh_ins = len(biometrics.get("weigh_ins") or [])
    daily_actuals = len(biometrics.get("daily_actuals") or [])

    def panel() -> None:
        # The verdict, not the rule. `dynamic_basis` is what separates a
        # measured figure that was used from one `reconcile_adaptive_tdee`
        # disbelieved, and it is legitimately absent (no engine call this
        # run), which the view reports as its own state rather than guessing.
        adaptive = adaptive_tdee_view(
            ctx.state.biometrics or biometrics,
            ctx.state.planning_config().get("dynamic_basis"),
        )
        with ui.element("div").classes(
            f"flex flex-col items-center justify-center gap-{SPACE_TIGHT} p-{SPACE_PAGE} "
            f"max-w-lg mx-auto text-center"
        ):
            ui.icon("insights").classes("text-4xl text-slate-600")
            ui.label(
                "Charts still to come"
                if adaptive.measuring
                else "Not enough history yet"
            ).classes(f"{TEXT_HEAD} font-semibold text-slate-300")
            ui.label(
                f"{weigh_ins} weigh-in(s) and {daily_actuals} logged day(s) of intake "
                "on record right now. Trend charts — weight vs. target, calories "
                "actual-vs-planned, macro accuracy, adherence — need real weeks of "
                "runtime data to be more than noise, not more code: the adaptive "
                "TDEE estimate, which is the first thing that data feeds, stands "
                "like this today."
            ).classes(f"{TEXT_BODY} text-slate-500")
            with ui.element("div").classes(
                f"flex flex-col items-center gap-{SPACE_TIGHT} w-full p-{SPACE_SECTION} "
                f"{RADIUS_CARD} border border-slate-800 bg-slate-950/30"
            ):
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
                ):
                    # Icon, not colour, per the `ui-work` skill: amber is
                    # staged-vs-stored and emerald is the cook status, so
                    # either would read as something this isn't. The trend
                    # glyph is the whole distinction, and it is the same
                    # pair Settings draws.
                    ui.icon(
                        "trending_up" if adaptive.measuring else "trending_flat"
                    ).classes(f"{TEXT_BODY} shrink-0 text-slate-400")
                    ui.label(adaptive.headline).classes(
                        f"{TEXT_BODY} font-semibold min-w-0 text-slate-300"
                    )
                ui.label(adaptive.detail).classes(f"{TEXT_MICRO} text-slate-500")
            ui.label(
                "Keep syncing Garmin weigh-ins and Cronometer daily actuals — "
                "see \"Biometric sync\" in CLAUDE.md. Charts land once there's "
                "something honest to draw."
            ).classes(f"{TEXT_MICRO} text-slate-600")

    return InsightsHandles(panel=panel)
