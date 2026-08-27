"""The Plan destination: the week grid, and the one thing that has to be said
in front of it — which meals failed to generate.

This is what `ui_cards.canvas()` plus the old "Week" tab used to be on their
own. It carried the drawer's "This week" stat block and a Generate button
above the canvas until phase 6b of `ui-redesign.md` emptied the row in two
directions at once, and the split is the point:

- **The stats** (cook sessions, days cooking, portions, shopping trips) are a
  *reading* of the week, so they moved up into the header's reporting strip
  beside the week dates and the plant-diversity count (`ui_telemetry.py`'s
  `week_banner`) — one place that says how the week is shaped, rather than
  two headers stacked on top of each other.
- **Generate and "Shuffle styles"** are *controls*, so they moved into the
  rail's action block (`ui_app.py`), with the shopping and export buttons
  that used to sit in `ui.header()`. The rail is now the single place a
  click starts something; a destination panel reports and shows.

What is left is genuinely neither: a generation failure is an error banner
for *this* destination's grid, naming slots whose cards below are the red
NOT GENERATED ones. It stays here, immediately above the thing it describes.

`build_plan(ctx, cards)` needs `cards` (see `ui_cards`) for `canvas()` — this
module never builds its own grid, it composes the one `ui_cards` already
owns. It no longer needs `review`: the Generate button that opened that
dialog is in the rail now.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_cards import CardHandles
from ui_context import UIContext
from ui_theme import (
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_SECTION,
    TEXT_BODY,
    TEXT_MICRO,
    week_grid_scroll,
)
from week import slot_label


@dataclass
class PlanHandles:
    week_failures: Callable
    panel: Callable


def build_plan(ctx: UIContext, cards: CardHandles) -> PlanHandles:
    state = ctx.state

    @ui.refreshable
    def week_failures() -> None:
        failures = state.week_plan.failures if state.week_plan else {}
        if not failures:
            return
        with ui.element("div").classes(
            f"mb-{SPACE_SECTION} p-{SPACE_BASE} {RADIUS_CARD} bg-rose-500/10 border border-rose-900"
        ):
            ui.label(f"{len(failures)} meal(s) failed to generate").classes(
                f"{TEXT_BODY} text-rose-300 font-semibold"
            )
            for key, error in failures.items():
                ui.label(f"{slot_label(key)}: {error}").classes(f"{TEXT_MICRO} text-rose-200/80")

    def panel() -> None:
        # `w-full min-w-0`: `.q-tab-panel` (this div's parent) is a Quasar flex
        # container, and a flex item's default `min-width: auto` refuses to
        # shrink below its content's natural width — the same trap
        # `.claude/rules/ui.md` documents for `flex-nowrap` rows, one level
        # up. Without it this wrapper grows to the grid's full min-content
        # width regardless of viewport, so `week_grid_scroll()`'s own
        # `overflow-x: auto` below never actually overflows — a *different*,
        # outer Quasar ancestor ends up scrolling the whole panel (summary
        # row included) instead of just the grid, which is what the JS
        # scroll-sync and the meal-type gutter's `sticky` (phase 2b of
        # `ui-redesign.md`) both assume is the thing scrolling.
        #
        # No `gap` on this column, unlike every other panel: `week_failures`
        # renders nothing at all on the common (successful) week, and a gap
        # would still space the canvas away from that empty container. It
        # carries its own `mb-` for the weeks it does render — the one place
        # in this file a margin beats a parent gap, because the sibling it
        # separates from is conditional.
        with ui.element("div").classes(
            f"flex flex-col p-{SPACE_SECTION} w-full min-w-0"
        ):
            week_failures()
            with week_grid_scroll():
                cards.canvas()

    return PlanHandles(week_failures=week_failures, panel=panel)
