"""The "Today" tab's initial content: a read-only preview of today's meals.

Deliberately minimal — no favorite/swap/regenerate buttons yet. Clicking a
card does open the same recipe detail dialog the Week tab's cards use
(`cards.open_detail`, from `ui_cards.CardHandles`) — one dialog reused by
both tabs, same as it's already reused across all 28 Week-tab cards, rather
than a second copy living here. Not built on `ui_cards.meal_card` itself:
that function's action-row buttons all need `ui_catalog`/`ui_generation`
wired in, none of which a card with no buttons needs, so a smaller card of
its own here is a real decoupling rather than a "fix later" shortcut.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import ui

from ui_cards import CardHandles
from ui_context import UIContext
from ui_state import SlotView
from ui_theme import (
    MACRO_LABELS,
    MACRO_TINTS,
    STATUS_SKIP,
    STATUS_STYLES,
    link_line,
    telemetry_bar,
)
from week import MODE_COOK, MODE_LEFTOVER, slot_id


@dataclass
class TodayHandles:
    today_view: Callable


def build_today(ctx: UIContext, cards: CardHandles) -> TodayHandles:
    state = ctx.state

    def today_card(view: Optional[SlotView], meal_type: str) -> None:
        if view is None:
            view = SlotView(day="", meal_type=meal_type, status=STATUS_SKIP, title="—")
        look = STATUS_STYLES[view.status]
        clickable = "cursor-pointer" if view.recipe else ""

        card = ui.element("div").classes(
            f"meal-card card-{view.status} rounded p-3 flex flex-col gap-1.5 min-w-0 "
            f"w-56 {look['card']} {clickable}"
        )
        if view.recipe:
            card.on("click", lambda v=view: cards.open_detail(v))

        with card:
            with ui.element("div").classes("flex flex-row items-center justify-between gap-1"):
                ui.label(meal_type.upper()).classes(
                    "text-[10px] font-semibold tracking-widest text-slate-500"
                )
                with ui.element("div").classes(
                    "flex items-center gap-0.5 px-1.5 py-[1px] rounded-full "
                    f"{look['badge']}"
                ):
                    ui.icon(look["icon"]).classes("text-[10px]")
                    ui.label(look["label"]).classes(
                        "text-[8px] font-semibold tracking-wide"
                    )

            ui.label(view.title).classes(
                "text-sm leading-tight font-bold text-slate-100 line-clamp-2"
            )

            tags = " · ".join(part for part in [view.style, view.cuisine] if part)
            if tags:
                ui.label(tags).classes("text-[10px] text-slate-400 truncate")

            if view.mode == MODE_LEFTOVER and view.source_label:
                link_line("↩ from", view.source_label, view.chain_colour)

            if view.macros:
                with ui.element("div").classes(
                    "flex flex-row flex-wrap items-center gap-x-1 mt-0.5 px-1.5 py-0.5 "
                    "rounded-full bg-slate-950/40 w-fit max-w-full"
                ):
                    ui.label(f"{view.macros['calories']:.0f} kcal").classes(
                        "text-[10px] font-mono text-slate-300"
                    )
                    for key, short, unit in MACRO_LABELS[1:]:
                        ui.label("·").classes("text-[10px] text-slate-600")
                        ui.label(f"{view.macros[key]:.0f}{unit} {short}").classes(
                            f"text-[10px] font-mono {MACRO_TINTS[key]}"
                        )

            if view.mode == MODE_COOK and view.portions:
                ui.label(
                    f"{view.portions} portions · {view.prep_minutes} min"
                    if view.prep_minutes is not None
                    else f"{view.portions} portions"
                ).classes("text-[10px] text-emerald-300/70 truncate")

    @ui.refreshable
    def today_view() -> None:
        day = state.today_day()
        if day is None:
            ui.label(
                "No cached week covers today — generate a fresh week, or "
                "switch the header's week selector to whichever one does."
            ).classes("text-sm text-slate-500 p-4")
            return

        target = state.targets_for(day)
        totals = state.totals_for(day)
        bar_scale_limit = state.config["ui_settings"]["bar_scale_limit"]

        with ui.element("div").classes("flex flex-col gap-3 p-3"):
            ui.label(day).classes("text-base font-semibold text-slate-200")

            with ui.element("div").classes("flex flex-col gap-1 max-w-md"):
                telemetry_bar(
                    totals["calories"],
                    float(target["calories"]),
                    height="10px",
                    bar_scale_limit=bar_scale_limit,
                )
                ui.label(
                    f"{totals['calories']:.0f} / {float(target['calories']):.0f} kcal"
                ).classes("text-xs text-slate-400")

            views = state.slot_views()
            with ui.element("div").classes("flex flex-row flex-wrap gap-2"):
                for meal_type in state.meal_types:
                    today_card(views.get(slot_id(day, meal_type)), meal_type)

    return TodayHandles(today_view=today_view)
