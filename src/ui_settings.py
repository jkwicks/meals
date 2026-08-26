"""Settings: week start, shopping days, model, and an integrations status
list. Everything here is a durable-feeling global rather than a per-run
input — the opposite category from `ui_review.py`'s contents, which is the
whole reason the two used to be crammed into one drawer.

The integrations status list replaces `ui_telemetry.py`'s old per-day
`context_pipeline` chip row (28 chips: 3 unconnected stages x 7 days, plus
workout x 7 days) — a decision made for phase 3 of `ui-redesign.md` rather
than wiring the three unbuilt stages or deleting the roadmap outright.
Workout is the one connected stage, and its per-day detail already lives in
the Today destination's day-context strip (`ui_today.py`), so this list says
only whether each stage is connected — no per-day breakdown, no click-through
dialog.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from planner import selectable_models
from ui_context import UIContext
from ui_theme import (
    PIPELINE_STAGES,
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
)


@dataclass
class SettingsHandles:
    panel: Callable


def build_settings(ctx: UIContext) -> SettingsHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    def integrations_status() -> None:
        with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT}"):
            for key, label, icon, description, connected in PIPELINE_STAGES:
                with ui.element("div").classes(
                    f"flex flex-row items-center gap-{SPACE_BASE} p-{SPACE_TIGHT} {RADIUS_CARD} "
                    "border border-slate-800 bg-slate-950/30"
                ):
                    ui.icon(icon).classes(
                        f"{TEXT_HEAD} "
                        + ("text-emerald-300" if connected else "text-slate-600")
                    )
                    with ui.element("div").classes("flex flex-col min-w-0 flex-1"):
                        ui.label(label).classes(f"{TEXT_BODY} font-semibold text-slate-200")
                        ui.label(description).classes(f"{TEXT_MICRO} text-slate-500")
                    ui.label("Connected" if connected else "Not connected").classes(
                        f"{TEXT_MICRO} font-semibold uppercase tracking-wide "
                        + ("text-emerald-300" if connected else "text-slate-600")
                    )

    @ui.refreshable
    def panel() -> None:
        all_days = list(state.config["weekly_schedule"].keys())

        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_SECTION} max-w-xl"
        ):
            ui.label("Settings").classes(f"{TEXT_HEAD} font-semibold text-slate-200")

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
                f"w-full {TEXT_BODY}"
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
                f"w-full {TEXT_BODY}"
            )

            ui.select(
                selectable_models(state.models_config),
                label="Model",
            ).bind_value(state, "model").props("dense outlined").classes(f"w-full {TEXT_BODY}")

            ui.separator()
            ui.label("Integrations").classes(f"{TEXT_BODY} font-semibold text-slate-300")
            integrations_status()

    return SettingsHandles(panel=panel)
