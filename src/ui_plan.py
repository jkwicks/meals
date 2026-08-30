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

What is left is genuinely neither, and there are two of them, both banners
that sit immediately above the thing they describe: a generation failure
names slots whose cards below are the red NOT GENERATED ones, and the
empty state says what the grid below *is* on a week that has never been
generated. Both are readings of this destination's own canvas, so neither
belongs in the header (which reads the week) or the rail (which acts on it).

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
    SPACE_HAIR,
    SPACE_SECTION,
    SPACE_TIGHT,
    SURFACE_INSET,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
    WEEK_SELECTION_LABELS,
    week_grid_scroll,
)
from week import slot_label


@dataclass
class PlanHandles:
    week_failures: Callable
    empty_state: Callable
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

    @ui.refreshable
    def empty_state() -> None:
        """What to say above a week that has never been generated.

        The grid below is *not* empty when there is no plan — `slot_views`
        builds from the spec, so 28 placeholder cards render regardless, and
        every structural control on them (mode, "Link to next lunch", a skip
        estimate) works and is worth using *before* a run rather than after
        one. So this is a banner above the canvas, on `week_failures`' own
        shape, and deliberately not a hero that replaces it: hiding a
        functioning grid to announce that it holds no recipes yet would cost
        the one thing a first-time visitor most needs to do next.

        **It carries no Generate button, and that is phase 6b's rule rather
        than an omission.** The rail is the single place a click starts
        something and a destination panel reports and shows — see this
        module's own docstring for why the button left this panel in the
        first place. A second Generate here would be a second thing to keep
        in step with `state.week_selection`, which the rail's own button
        already binds to. It names the button instead, with the icon it
        wears, so the sentence points at a real thing on screen.
        """
        if state.week_plan is not None:
            return
        label = WEEK_SELECTION_LABELS[state.week_selection].lower()
        cook_slots = len(state.spec.cook_slots())
        with ui.element("div").classes(
            f"mb-{SPACE_SECTION} p-{SPACE_SECTION} {RADIUS_CARD} border border-slate-800 "
            f"{SURFACE_INSET} flex flex-col gap-{SPACE_TIGHT}"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
            ):
                ui.icon("calendar_view_week").classes(f"{TEXT_HEAD} shrink-0 text-slate-400")
                ui.label(f"Nothing generated for the {label} yet").classes(
                    f"{TEXT_HEAD} font-semibold text-slate-200 min-w-0"
                )
            ui.label(
                f"The grid below is the shape a run would fill: {cook_slots} "
                "meal(s) to cook, the rest leftovers or skipped. Set modes, "
                "link leftovers and pin favourites here first — they are what "
                "the run is asked for."
            ).classes(f"{TEXT_BODY} text-slate-400")
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR} min-w-0"
            ):
                ui.icon("bolt").classes(f"{TEXT_BODY} shrink-0 text-slate-400")
                ui.label(
                    f"Ready? \u201cGenerate {label}\u201d in the rail on the left."
                ).classes(f"{TEXT_MICRO} text-slate-400 min-w-0")

    def panel() -> None:
        # `w-full min-w-0`: `.q-tab-panel` (this div's parent) is a Quasar flex
        # container, and a flex item's default `min-width: auto` refuses to
        # shrink below its content's natural width — the same trap
        # the `ui-work` skill documents for `flex-nowrap` rows, one level
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
            empty_state()
            week_failures()
            with week_grid_scroll():
                cards.canvas()

    return PlanHandles(week_failures=week_failures, empty_state=empty_state, panel=panel)
