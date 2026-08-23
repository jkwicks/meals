"""The "Generate Current Week" options popup: cuisine picker, western-style
share slider, diet-style picker, bulk-prep toggle, long-cook toggle. Every
pick here is a one-off for the *next* generation only (see
`PlannerState.cuisine_override` and its siblings), never written to
config.json — the same contract the drawer's per-day target overrides already
keep.

`build_prep_options(ctx, generation)` is built once per page load, after
`build_generation` (its own "Generate" button is what actually starts a run,
via `generation.run_generation`) and before `build_drawer` (whose sticky
"Generate" button now opens this dialog instead of running the week
directly — see `ui_drawer.build_drawer`'s `prep_options` parameter).
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_context import UIContext
from ui_generation import GenerationHandles
from week import humanize


@dataclass
class PrepOptionsHandles:
    open: Callable


def build_prep_options(ctx: UIContext, generation: GenerationHandles) -> PrepOptionsHandles:
    state = ctx.state

    # Static for the page's lifetime — config.json's cuisine/diet-style
    # catalog doesn't change after PlannerState.load(), so this is built once
    # rather than inside a refreshable body.
    cuisine_options = {c: humanize(c).title() for c in state.config["cuisines"]}
    diet_style_options = {
        key: entry["label"] for key, entry in state.config["diet_styles"].items()
    }

    with ui.dialog() as dialog:
        with ui.element("div").classes(
            "bg-slate-900 rounded-lg p-4 w-[28rem] max-w-full flex flex-col gap-3"
        ):
            with ui.element("div").classes("flex flex-row items-center gap-1.5"):
                ui.icon("tune").classes("text-sm text-amber-300")
                ui.label("Generate options").classes("text-sm font-semibold")
            ui.label(
                "Applies to this generation only — nothing here is saved to "
                "config.json."
            ).classes("text-[10px] text-slate-500")

            ui.select(
                cuisine_options,
                label="Cuisines this week",
                multiple=True,
            ).bind_value(state, "cuisine_override").props(
                "dense outlined use-chips"
            ).classes("w-full text-xs")
            ui.label("Leave empty to use config.json's cuisine list.").classes(
                "text-[9px] text-slate-600 -mt-2"
            )

            baseline_cuisines = state.config.get("baseline_cuisines") or []
            if baseline_cuisines:
                with ui.element("div").classes("flex flex-row items-center justify-between"):
                    ui.label("Min. western-style share").classes("text-xs text-slate-300")
                    ui.label().classes("text-xs font-mono text-slate-400").bind_text_from(
                        state, "baseline_cuisine_share", backward=lambda share: f"{share:.0%}"
                    )
                ui.slider(min=0.0, max=1.0, step=0.05).bind_value(
                    state, "baseline_cuisine_share"
                ).props("dense color=teal")
                ui.label(
                    "Floor on how much of the week's cook days go to "
                    + ", ".join(humanize(c).title() for c in baseline_cuisines)
                    + " before the rest rotates freely. 0% turns the floor off"
                    " for this run."
                ).classes("text-[9px] text-slate-600 -mt-2")

            if diet_style_options:
                ui.select(
                    diet_style_options,
                    label="Diet styles this week",
                    multiple=True,
                ).bind_value(state, "diet_style_override").props(
                    "dense outlined use-chips"
                ).classes("w-full text-xs")
                ui.label(
                    "Leave empty to use config.json's active diet styles."
                ).classes("text-[9px] text-slate-600 -mt-2")

            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label("Bulk prep").classes("text-xs text-slate-300")
                ui.switch().bind_value(state, "bulk_prep_enabled").props(
                    "dense size=sm color=teal"
                )
            ui.label(
                "Batches one dinner across several days automatically — which "
                "days is decided for you, no picking required. Absorbs the old "
                "Sunday-prep timeline (no longer tied to Sunday)."
            ).classes("text-[9px] text-slate-600 -mt-2")

            with ui.element("div").classes("flex flex-row items-center justify-between"):
                ui.label("Long cook meal").classes("text-xs text-slate-300")
                ui.switch().bind_value(state, "long_cook_enabled").props(
                    "dense size=sm color=teal"
                )
            ui.label(
                "One dinner this week is a genuinely long, hands-off oven "
                "roast/braise — a different day than bulk prep's, if both are "
                "on."
            ).classes("text-[9px] text-slate-600 -mt-2")

            with ui.row().classes("justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("dense flat no-caps")

                async def on_generate() -> None:
                    dialog.close()
                    await generation.run_generation(generate_button)

                generate_button = ui.button(
                    "Generate", icon="bolt", on_click=on_generate
                ).props("dense no-caps")

    return PrepOptionsHandles(open=dialog.open)
