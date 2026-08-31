"""The shopping list, drawn in two places from one builder.

A right-hand slide-over **and** a rail destination. The drawer's reasoning
stands unchanged and is why it is still here: the list is read *against* the
grid — "what is Wednesday's trip for" is answered by looking at both at once —
and a modal would cover the week it describes. The destination is the other
half of CHANGE-QUEUE.md's "should shopping be its own destination?", answered
`both`: a 420px slide-over is the right shape for reading a trip beside the
week and the wrong shape for working through one, and the two share
`build_panel()` so they cannot come to differ about a trip. `ui_inspector.py`
reusing `ui_today.py`'s renderers is the precedent.

Everything shown here is derived from the plan on each repaint; nothing is
stored. The ticks are the exception and they are **still** not persisted —
storing them would be more state able to disagree with `week_plan.json` — but
they now live on `PlannerState` rather than in the DOM, so a repaint no longer
wipes them mid-shop. Per-client, dies with the tab, never reaches `data/`.

`build_shopping(ctx)` constructs the `ui.right_drawer(...)` itself (nothing
else needs to build it) and returns the drawer element so the rail's Shopping
button (the action block in `ui_app.py`) can toggle it, plus `build_panel` for
the destination to call at its own render position. That button was in
`ui.header()` until phase 6b of `ui-redesign.md`; the rail is global in the
same way the header was, which is what that finding required of it — Daily
View and the day inspector show slots worth shopping against too, so this
could not become a Plan-only control.
"""

import json
from dataclasses import dataclass, field
from typing import Callable, List

from nicegui import ui

from shopping import (
    ShoppingItem,
    cook_plan_lines,
    format_quantity,
    format_shopping_list_keep,
    ordered_departments,
    pantry_covered_line,
    pantry_note,
)
from ui_context import UIContext
from ui_theme import (
    RADIUS_CARD,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
)


@dataclass
class ShoppingPanels:
    """Every instance of the list, refreshed as one registered section.

    `Refreshables` de-dupes by `id`, calls `.refresh()` on whatever it was
    handed, and is wired up in `ui_app.py` *before* the rail is built — so the
    destination's own `@ui.refreshable` does not exist yet at registration
    time. One object registered on the topics, fanning out to however many
    instances end up existing, is the whole fix; the alternative was moving
    every `refreshables.on(...)` call below the rail so that one section could
    be reached, which would make a page-shell ordering rule out of a detail
    private to this module.
    """

    panels: List[Callable] = field(default_factory=list)

    def add(self, panel: Callable) -> None:
        self.panels.append(panel)

    def refresh(self) -> None:
        for panel in self.panels:
            panel.refresh()


@dataclass
class ShoppingHandles:
    shopping_drawer: object
    # What `ui_app.py` registers on "plan"/"shopping"/"shopping_days".
    shopping_panel: ShoppingPanels
    # Called by the destination at its own render position; safe to call more
    # than once, and every instance is kept in sync by the handle above.
    build_panel: Callable[[], None]


def build_shopping(ctx: UIContext) -> ShoppingHandles:
    state = ctx.state
    refreshables = ctx.refreshables
    panels = ShoppingPanels()

    def copy_for_keep(text: str, label: str) -> None:
        """Put `text` on the system clipboard, formatted for a Keep list.

        `json.dumps` here is escaping a JavaScript string literal, not touching
        storage — an ingredient name with an apostrophe in it ("Bird's eye
        chilli", seen on a real run) would otherwise end the literal early and
        break the whole handler. The `execCommand` branch is for the
        non-localhost case: `navigator.clipboard` is unavailable outside a
        secure context, and this server is often reached over plain HTTP on a
        LAN address.
        """
        ui.run_javascript(
            f"""
            const text = {json.dumps(text)};
            if (navigator.clipboard && window.isSecureContext) {{
                navigator.clipboard.writeText(text);
            }} else {{
                const area = document.createElement('textarea');
                area.value = text;
                area.style.position = 'fixed';
                area.style.opacity = '0';
                document.body.appendChild(area);
                area.select();
                document.execCommand('copy');
                area.remove();
            }}
            """
        )
        ui.notify(f"{label} copied — paste into a Google Keep list", type="positive")

    def item_row(window_label: str, item: ShoppingItem) -> None:
        """One line, its tick held on `PlannerState` rather than in the DOM."""

        def on_tick(event) -> None:
            state.set_shopping_tick(window_label, item.name, bool(event.value))

        # `flex-nowrap`: Quasar's `.flex` sets `flex-wrap: wrap` and Tailwind's
        # `flex-col` does not undo it — see the drawer root below, where the
        # same omission was the bug this file was opened to fix.
        with ui.element("div").classes("flex flex-col flex-nowrap min-w-0"):
            ui.checkbox(
                f"{item.name} — {format_quantity(item.name, item.total_amount_g)}",
                value=state.shopping_tick(window_label, item.name),
                on_change=on_tick,
            ).props("dense size=xs color=teal").classes(
                f"{TEXT_BODY} "
                + ("text-slate-300" if item.buy_late or item.from_pantry else "text-slate-200")
            )
            # Both notes are slate and carry no hue, per `ui_theme`'s palette
            # table: amber already means five things and this would be a
            # sixth. The ⏳ that used to sit inline went with them — v0.38.0
            # retired emoji because they render in the platform's own emoji
            # font at its own colours, and this was the last one left.
            notes = [
                note
                for note in (
                    pantry_note(item),
                    "buy fresh closer to the day" if item.buy_late else "",
                )
                if note
            ]
            if notes:
                # Indented to clear the dense checkbox rather than spaced with
                # the parent's gap: this is an alignment to a sibling's control,
                # not spacing between siblings.
                ui.label(" · ".join(notes)).classes(
                    f"{TEXT_MICRO} text-slate-400 pl-6"
                )

    def department_band(department: str, items: List[ShoppingItem]) -> None:
        """A department header that cannot be mistaken for something to buy.

        It was 10px uppercase slate-400 with nothing else on the row, which is
        skimmable straight past in a column of 10px item labels. The count is
        what makes it a header rather than a label — it is information no item
        line could carry — and the rule under it is what separates the groups
        without a hue. The Keep copy has the same problem far more sharply and
        solves it typographically (`── DAIRY & EGGS ──`), because there every
        line becomes a checkbox.
        """
        with ui.element("div").classes(
            f"flex flex-row flex-nowrap items-baseline justify-between gap-{SPACE_TIGHT} "
            f"border-b border-slate-800 pb-{SPACE_HAIR} mt-{SPACE_TIGHT}"
        ):
            ui.label(department).classes(
                f"{TEXT_MICRO} uppercase tracking-widest text-slate-300 font-semibold min-w-0"
            )
            ui.label(str(len(items))).classes(f"{TEXT_MICRO} text-slate-400 shrink-0")

    def window_card(view) -> None:
        shopping_list = view.shopping_list
        with ui.element("div").classes(
            f"flex flex-col flex-nowrap gap-{SPACE_BASE} p-{SPACE_BASE} {RADIUS_CARD} "
            "border border-slate-800 bg-slate-950/40"
        ):
            with ui.element("div").classes(
                f"flex flex-row items-center justify-between gap-{SPACE_BASE}"
            ):
                with ui.element("div").classes("flex flex-col flex-nowrap min-w-0"):
                    ui.label(view.label).classes(f"{TEXT_BODY} font-semibold text-slate-100")
                    ui.label(
                        f"{len(view.events)} cook session(s)"
                        + (f" · {view.item_count} items" if shopping_list else "")
                    ).classes(f"{TEXT_MICRO} text-slate-400")
                if shopping_list:
                    ui.button(
                        "Copy for Keep",
                        icon="content_copy",
                        # The trip's own label goes into the payload, not just
                        # into the toast: two trips pasted into Keep were
                        # indistinguishable once the toast had gone.
                        on_click=lambda sl=shopping_list, label=view.label: copy_for_keep(
                            format_shopping_list_keep(sl, trip=label), label
                        ),
                    ).props("dense flat no-caps size=sm").classes("shrink-0 text-sky-300")

            if not view.events:
                ui.label("Nothing cooked in this window.").classes(
                    f"{TEXT_BODY} text-slate-400 italic"
                )
                return

            # A failed meal contributes no recipe and therefore no
            # ingredients, so say so here: a short list is otherwise
            # indistinguishable from a cheap week. `WeekPlan.failures` is
            # keyed by slot_id (day:meal_type), not day, since one bad
            # meal-type call can fail some of a window's days without
            # failing all of them.
            if view.failed:
                ui.label(
                    f"{', '.join(view.failed)} failed to generate — nothing for "
                    "those meals is on this list."
                ).classes(
                    f"{TEXT_MICRO} text-rose-300 p-{SPACE_TIGHT} {RADIUS_CARD} bg-rose-500/10"
                )

            # Quoted because NiceGUI's props parser drops an unquoted value
            # containing brackets — an unquoted arbitrary-value class like
            # `header-class=w-[3px]` silently never reaches Quasar at all.
            with ui.expansion("What this trip is for").props(
                f"dense header-class='{TEXT_BODY} text-slate-400 px-0'"
            ).classes("w-full"):
                ui.label("Quantities below already include every portion.").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
                for line in cook_plan_lines(view.events):
                    ui.label(line).classes(f"{TEXT_MICRO} text-slate-400")

            for department in ordered_departments(shopping_list):
                items = shopping_list.categories[department]
                department_band(department, items)
                for item in items:
                    item_row(view.label, item)

            # Named rather than silently dropped: the pantry is hand-edited
            # and can go stale, and a line that vanished with no trace is the
            # one you cannot notice is wrong.
            covered = pantry_covered_line(shopping_list)
            if covered:
                ui.label(covered).classes(
                    f"{TEXT_MICRO} text-slate-400 italic mt-{SPACE_TIGHT}"
                )

    def build_panel() -> Callable:
        """A fresh `@ui.refreshable` instance at the caller's render position.

        `@ui.refreshable` binds to where it was first called, so the drawer
        and the destination need one each. Both are added to `panels`, which
        is the single section `ui_app.py` registers on the topics — that is
        what keeps them from drifting apart.
        """

        @ui.refreshable
        def shopping_panel() -> None:
            def on_daily_shop_toggle(event) -> None:
                state.daily_shop_mode = event.value
                refreshables.refresh("shopping")

            views = state.shopping_view()
            if state.week_plan is None:
                ui.label("No shopping list yet").classes(f"{TEXT_HEAD} text-slate-300")
                ui.label(
                    "A list is built from generated recipes, so there is nothing to buy "
                    "until the week has been generated."
                ).classes(f"{TEXT_BODY} text-slate-400")
                return

            # Inside the refreshable rather than beside it, which is what lets
            # two instances of this panel exist: the toggle is state both of
            # them read, so flipping it in the drawer has to move the switch on
            # the destination too. A control built once outside would be the
            # "second control free to disagree" objection the week select
            # already answers by repainting the first rather than adding a
            # second.
            with ui.element("div").classes(
                "flex flex-row flex-nowrap items-center justify-between"
            ):
                ui.label("Shop days (batch trips)").classes(f"{TEXT_BODY} text-slate-400")
                ui.switch(
                    value=state.daily_shop_mode, on_change=on_daily_shop_toggle
                ).props("dense size=sm color=teal")
                ui.label("Daily shop").classes(f"{TEXT_BODY} text-slate-400")

            if not views:
                ui.label("No shopping days set — pick some in the drawer.").classes(
                    f"{TEXT_BODY} text-slate-400"
                )
                return

            for view in views:
                window_card(view)

        panels.add(shopping_panel)
        shopping_panel()
        return shopping_panel

    def destination_panel() -> None:
        """The rail destination: the same panel, given a page to breathe in."""
        with ui.element("div").classes(
            f"flex flex-col flex-nowrap gap-{SPACE_SECTION} w-full min-w-0 max-w-3xl "
            f"mx-auto p-{SPACE_PAGE}"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
            ):
                ui.icon("shopping_cart").classes(f"{TEXT_HEAD} text-slate-400")
                ui.label("Shopping list").classes(
                    f"{TEXT_BODY} uppercase tracking-widest text-slate-400"
                )
            build_panel()

    # `flex-nowrap`, and it is the fix this whole item was filed for. Quasar's
    # own `.flex` rule sets `flex-wrap: wrap` and Tailwind's `flex-col` does
    # not undo it, so a *wrapping column* whose content outgrows its height
    # does not overflow downward — it starts a second column beside the first,
    # off the 420px edge, and the `overflow-y-auto` on this same element never
    # fires because vertically the content does fit. Every other module in the
    # repo already carried the class (`ui_cards.py:94`, `ui_app.py:684`, both
    # with comments saying it is load-bearing); this file was the only one
    # that did not, in three places, and the symptom was a drawer that scrolled
    # sideways and hid its own Copy-for-Keep buttons and every window after the
    # first.
    with ui.right_drawer(value=False, bordered=True).classes(
        f"bg-slate-900 p-{SPACE_SECTION} flex flex-col flex-nowrap "
        f"gap-{SPACE_SECTION} overflow-y-auto"
    ).props(":width=420") as shopping_drawer:
        with ui.element("div").classes(
            "flex flex-row flex-nowrap items-center justify-between"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT} min-w-0"
            ):
                ui.icon("shopping_cart").classes(f"{TEXT_HEAD} text-slate-400")
                ui.label("Shopping list").classes(
                    f"{TEXT_BODY} uppercase tracking-widest text-slate-400"
                )
            ui.button(icon="close", on_click=lambda: shopping_drawer.hide()).props(
                "dense flat size=sm"
            ).classes("text-slate-400")

        build_panel()

    return ShoppingHandles(
        shopping_drawer=shopping_drawer,
        shopping_panel=panels,
        build_panel=destination_panel,
    )
