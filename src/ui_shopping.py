"""The shopping list: a right-hand slide-over rather than a dialog, because
the list is read *against* the grid — "what is Wednesday's trip for" is
answered by looking at both at once — and a modal would cover the week it
describes.

Everything shown here is derived from the plan on each repaint; nothing is
stored. The ticks are the exception, and they are deliberately not
persisted: this is a scratch list for one trip, not another piece of state
that could disagree with week_plan.json.

`build_shopping(ctx)` constructs the `ui.right_drawer(...)` itself (nothing
else needs to build it) and returns the drawer element so the header's
shopping-cart button (in `ui_app.py`) can toggle it.
"""

import json
from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from shopping import (
    aggregate_cook_events,
    cook_plan_lines,
    format_quantity,
    format_shopping_list_keep,
)
from ui_context import UIContext
from week import parse_slot_id, shopping_windows, slot_label


@dataclass
class ShoppingHandles:
    shopping_drawer: object
    shopping_panel: Callable


def build_shopping(ctx: UIContext) -> ShoppingHandles:
    state = ctx.state
    refreshables = ctx.refreshables

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

    @ui.refreshable
    def shopping_panel() -> None:
        plan = state.week_plan
        if plan is None:
            ui.label("No shopping list yet").classes("text-sm text-slate-300")
            ui.label(
                "A list is built from generated recipes, so there is nothing to buy "
                "until the week has been generated."
            ).classes("text-xs text-slate-500")
            return

        # Daily mode reuses the same partitioning function with every day
        # treated as a shop day — the cook events and quantities in each
        # window are unaffected, only where the boundaries fall.
        window_days = state.days if state.daily_shop_mode else state.shop_days
        windows = shopping_windows(state.days, window_days)
        if not windows:
            ui.label("No shopping days set — pick some in the drawer.").classes(
                "text-xs text-slate-400"
            )
            return

        for window in windows:
            # By cook day, never eating day: a Sunday batch eaten on Wednesday
            # is bought entirely on the Sunday trip, so its ingredients are
            # never split across two lists.
            events = plan.events_on_days(window.days)
            shopping_list = aggregate_cook_events(events, window.days) if events else None

            with ui.element("div").classes(
                "flex flex-col gap-2 p-2 rounded border border-slate-800 bg-slate-950/40"
            ):
                with ui.element("div").classes("flex flex-row items-center justify-between gap-2"):
                    with ui.element("div").classes("flex flex-col min-w-0"):
                        ui.label(window.label).classes("text-xs font-semibold text-slate-100")
                        ui.label(
                            f"{len(events)} cook session(s)"
                            + (f" · {len(shopping_list.items())} items" if shopping_list else "")
                        ).classes("text-[10px] text-slate-500")
                    if shopping_list:
                        ui.button(
                            "Copy for Keep",
                            icon="content_copy",
                            on_click=lambda sl=shopping_list, w=window: copy_for_keep(
                                format_shopping_list_keep(sl), w.label
                            ),
                        ).props("dense flat no-caps size=sm").classes("shrink-0 text-sky-300")

                if not events:
                    ui.label("Nothing cooked in this window.").classes(
                        "text-[11px] text-slate-500 italic"
                    )
                    continue

                # A failed meal contributes no recipe and therefore no
                # ingredients, so say so here: a short list is otherwise
                # indistinguishable from a cheap week. `plan.failures` is
                # keyed by slot_id (day:meal_type), not day, since one bad
                # meal-type call can fail some of a window's days without
                # failing all of them.
                failed = [
                    slot_label(key)
                    for key in plan.failures
                    if parse_slot_id(key)[0] in window.days
                ]
                if failed:
                    ui.label(
                        f"{', '.join(failed)} failed to generate — nothing for "
                        "those meals is on this list."
                    ).classes("text-[10px] text-rose-300 p-1 rounded bg-rose-500/10")

                # Quoted because NiceGUI's props parser drops an unquoted value
                # containing brackets — `header-class=text-[11px]` silently
                # never reaches Quasar at all.
                with ui.expansion("What this trip is for").props(
                    "dense header-class='text-[11px] text-slate-400 px-0'"
                ).classes("w-full"):
                    ui.label(
                        "Quantities below already include every portion."
                    ).classes("text-[10px] text-slate-500")
                    for line in cook_plan_lines(events):
                        ui.label(line).classes("text-[10px] text-slate-400")

                for department in sorted(shopping_list.categories):
                    ui.label(department).classes(
                        "text-[10px] uppercase tracking-widest text-slate-500 mt-1"
                    )
                    for item in shopping_list.categories[department]:
                        text = f"{item.name} — {format_quantity(item.name, item.total_amount_g)}"
                        # buy_late is a perishable this window doesn't cook for
                        # days yet. Annotated, never moved to another trip —
                        # whether to make a second run is the shopper's call.
                        if item.buy_late:
                            text += "  ← buy fresh closer to the day"
                        ui.checkbox(text).props("dense size=xs color=teal").classes(
                            "text-[11px] "
                            + ("text-amber-300" if item.buy_late else "text-slate-200")
                        )

    with ui.right_drawer(value=False, bordered=True).classes(
        "bg-slate-900 p-3 flex flex-col gap-3 overflow-y-auto"
    ).props(":width=420") as shopping_drawer:
        with ui.element("div").classes("flex flex-row items-center justify-between"):
            with ui.element("div").classes("flex flex-row items-center gap-1"):
                ui.icon("shopping_cart").classes("text-sm text-slate-500")
                ui.label("Shopping list").classes(
                    "text-xs uppercase tracking-widest text-slate-500"
                )
            ui.button(icon="close", on_click=lambda: shopping_drawer.hide()).props(
                "dense flat size=sm"
            ).classes("text-slate-400")

        def on_daily_shop_toggle(event) -> None:
            state.daily_shop_mode = event.value
            refreshables.refresh("shopping")

        with ui.element("div").classes("flex flex-row items-center justify-between"):
            ui.label("Shop days (batch trips)").classes("text-[11px] text-slate-400")
            ui.switch(value=state.daily_shop_mode, on_change=on_daily_shop_toggle).props(
                "dense size=sm color=teal"
            )
            ui.label("Daily shop").classes("text-[11px] text-slate-400")

        shopping_panel()

    return ShoppingHandles(shopping_drawer=shopping_drawer, shopping_panel=shopping_panel)
