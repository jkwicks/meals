"""The Plan destination: the week grid and the generation flow. This is what
`ui_cards.canvas()` plus the old "Week" tab used to be on their own, now with
the drawer's "This week" stat block (cook sessions, days cooking, portions,
shopping trips, and any generation failures) sitting above the canvas rather
than at the bottom of a 320px column scrolled well past the grid it describes.

`build_plan(ctx, cards, review)` needs `cards` (see `ui_cards`) for
`canvas()` — this module never builds its own grid, it composes the one
`ui_cards` already owns — and `review` (see `ui_review`) for the "Generate"
button.

That button matters because the staged-changes bar (`ui_staged_bar.py`)
deliberately hides when `pending_changes()` is empty — a fresh page load
with no edits yet is the common case, not an edge case, and it must not
leave "how do I generate a week" with no answer on screen. Clicking it opens
the same review dialog the bar's own "Review" button does; the dialog's
Generate button is what actually starts a run. The bar's extra "Generate
week" shortcut is for when something *is* staged and you want to go with it
without reopening the dialog — a convenience layered on top of this, not a
replacement for it.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_cards import CardHandles
from ui_context import UIContext
from ui_review import ReviewHandles
from ui_theme import (
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_MICRO,
    WEEK_SELECTION_LABELS,
    week_grid_scroll,
)
from week import portions_for, shopping_windows, slot_label


@dataclass
class PlanHandles:
    week_summary: Callable
    panel: Callable


def build_plan(ctx: UIContext, cards: CardHandles, review: ReviewHandles) -> PlanHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    @ui.refreshable
    def week_summary() -> None:
        spec = state.spec
        cooks = spec.cook_slots()
        cook_days = {slot.day for slot in cooks}
        windows = shopping_windows(state.days, state.shop_days)
        total_portions = sum(portions_for(spec).values())

        def on_shuffle_styles() -> None:
            state.shuffle_styles()
            refreshables.refresh("plan")
            ui.notify(
                "Styles cleared — next Generate will re-roll every cook slot.",
                type="positive",
            )

        with ui.element("div").classes(f"flex flex-row items-center justify-between gap-{SPACE_BASE}"):
            with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_SECTION}"):
                for label, value in [
                    ("Cook sessions", len(cooks)),
                    ("Days cooking", len(cook_days)),
                    ("Portions", total_portions),
                    ("Shopping trips", len(windows)),
                ]:
                    with ui.element("div").classes(f"flex flex-row items-baseline gap-{SPACE_TIGHT}"):
                        ui.label(str(value)).classes(f"{TEXT_BODY} font-mono font-semibold text-slate-200")
                        ui.label(label).classes(f"{TEXT_MICRO} text-slate-500")

            with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT}"):
                with ui.button(
                    icon="casino", on_click=on_shuffle_styles
                ).props("dense flat size=sm").classes("text-slate-400"):
                    ui.tooltip(
                        "Once a week is generated, its slots keep the style/cuisine "
                        "they resolved to, so re-generating repeats them and only "
                        "reworks the dish. This blanks style/cuisine on every cook "
                        "slot (leftover links and skips are untouched) so the next "
                        "Generate rotates them fresh — nothing is written to disk "
                        "until you generate."
                    )
                generate = (
                    ui.button(icon="bolt", on_click=review.open).props("dense no-caps size=sm")
                )
                generate.bind_text_from(
                    state,
                    "week_selection",
                    backward=lambda w: f"Generate {WEEK_SELECTION_LABELS[w]}",
                )
                with generate:
                    ui.tooltip(
                        "Opens cuisine, diet-style, bulk-prep, long-cook, targets, "
                        "training and pantry options, then generates every meal set "
                        "to cook in this grid — one API call per meal type, "
                        "covering each day it's cooked. Overwrites the selected "
                        "week's cached plan and appends to history."
                    )

        failures = state.week_plan.failures if state.week_plan else {}
        if failures:
            with ui.element("div").classes(
                f"mt-2 p-{SPACE_BASE} {RADIUS_CARD} bg-rose-500/10 border border-rose-900"
            ):
                ui.label(f"{len(failures)} meal(s) failed to generate").classes(
                    f"{TEXT_BODY} text-rose-300 font-semibold"
                )
                for key, error in failures.items():
                    ui.label(f"{slot_label(key)}: {error}").classes(f"{TEXT_MICRO} text-rose-200/80")

    def panel() -> None:
        with ui.element("div").classes(f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_SECTION}"):
            week_summary()
            with week_grid_scroll():
                cards.canvas()

    return PlanHandles(week_summary=week_summary, panel=panel)
