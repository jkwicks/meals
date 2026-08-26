"""The day inspector: a floating, read-mostly panel for one day, opened by
clicking its telemetry column. Phase 4 of `ui-redesign.md`.

**Cheap because `ui_today.py` already proved the shape.** Everything this
panel shows — `targets_for`, `totals_for`, `day_context`, `slot_views` — is
already day-parameterised, and the day-context strip plus the four slot
cards are the exact same module-level functions `ui_today.py`'s own panel
calls (`context_strip`, `today_card`) — see that module's docstring. This
file adds no new rendering for either; it only adds the dialog shell and the
targets/training/location summary shared between "today" and "any day".

**Floats, never pushes.** `ui.dialog()` is a true Quasar overlay — centered,
dimmed backdrop, no reflow of the page behind it — which is what satisfies
"floats over the canvas, never pushes it" with no new positioning mechanism.
This is the same one-dialog-reused-by-key pattern `ui_cards.py`'s recipe
detail dialog already uses, keyed here off `PlannerState.inspector_day`
instead of `.focus`.

**Read-only targets.** Editing a day's target is `ui_review.py`'s job (the
target curve, phase 4.2) — this panel shows `targets_for`/`totals_for` as a
bar and offers a link into the review dialog rather than growing a second
place to type a number.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_cards import CardHandles
from ui_context import UIContext
from ui_review import ReviewHandles
from ui_state import day_context
from ui_today import context_strip, today_card
from ui_theme import (
    RADIUS_PANEL,
    SPACE_BASE,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_DISPLAY,
    TEXT_BODY,
    format_day_label,
    telemetry_bar,
)
from week import slot_id


@dataclass
class InspectorHandles:
    panel: Callable
    open: Callable[[str], None]


def build_inspector(ctx: UIContext, cards: CardHandles, review: ReviewHandles) -> InspectorHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    @ui.refreshable
    def panel() -> None:
        day = state.inspector_day
        if day is None or day not in state.days:
            return

        target = state.targets_for(day)
        totals = state.totals_for(day)
        bar_scale_limit = state.config["ui_settings"]["bar_scale_limit"]
        context = day_context(state, day)
        views = state.slot_views()

        with ui.element("div").classes(f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_PAGE} w-[40rem] max-w-full"):
            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label(format_day_label(day, state.day_date_iso(day))).classes(
                    f"{TEXT_DISPLAY} font-semibold text-slate-200"
                )
                ui.button(icon="close", on_click=inspector_dialog.close).props(
                    "dense flat round size=sm"
                ).classes("text-slate-400")

            with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_BASE}"):
                with ui.element("div").classes(f"flex flex-col gap-{SPACE_TIGHT} flex-1"):
                    telemetry_bar(
                        totals["calories"],
                        float(target["calories"]),
                        height="10px",
                        bar_scale_limit=bar_scale_limit,
                    )
                    ui.label(
                        f"{totals['calories']:.0f} / {float(target['calories']):.0f} kcal"
                    ).classes(f"{TEXT_BODY} text-slate-400")
                ui.button("Edit targets", on_click=review.open).props(
                    "dense flat no-caps size=sm"
                ).classes("text-sky-300")

            context_strip(context)

            with ui.element("div").classes(f"flex flex-row flex-wrap gap-{SPACE_BASE}"):
                for meal_type in state.meal_types:
                    today_card(views.get(slot_id(day, meal_type)), meal_type, context, cards)

    with ui.dialog() as inspector_dialog:
        with ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} border border-slate-800 overflow-y-auto max-h-[85vh]"
        ):
            panel()

    # Fires on every dismissal path (the X button, backdrop click, ESC), not
    # just the button — so `state.inspector_day` never says "open" once the
    # dialog is actually gone from screen.
    inspector_dialog.on("hide", lambda: state.close_inspector())

    def open_day(day: str) -> None:
        state.open_inspector(day)
        refreshables.refresh("inspector")
        inspector_dialog.open()

    return InspectorHandles(panel=panel, open=open_day)
