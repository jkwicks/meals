"""The staged-changes bar: one persistent strip reading "N pending changes —
<summaries> · Review · Generate week" (plus "Save changes" when a grid edit
is among them), replacing three separate "Applies to the next generation
only" disclaimers, the telemetry override dot, and the old "edited —
not saved" chip with a single honest statement. Sits between the header and
the rail — not inside the header (which stays macro telemetry and the week
selector) and not inside any one destination panel, because what's pending
has to stay visible no matter which destination is open.

`build_staged_bar(ctx, review, generation)` needs `review` (see `ui_review`)
for the "Review" button and `generation` (see `ui_generation`) for the
"Generate week" shortcut, the "Save changes" shortcut and the "Discard
pending changes" action — the renamed "Reload from disk", now living where
the rest of the staged-changes vocabulary lives instead of a standalone
drawer button.

See `PlannerState.pending_changes()` for what counts as pending and why it
deliberately does *not* clear the target/training/pantry entries after a
generation — only the grid-edit entry does, because saving is what makes the
grid match disk; the others are inputs that are never written to disk at
all — there is no "Save" for them, only "Review" (to change them) or
"Discard". A grid edit alone (a swap, a leftover link, a skip estimate) needs
no model call to be fully decided, which is what `generation.save_grid`
exists for: it writes `state.week_plan` straight to disk, the same file a
generation writes, without asking the LLM for anything.
"""

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from ui_context import UIContext
from ui_generation import GenerationHandles
from ui_review import ReviewHandles
from ui_theme import RADIUS_CARD, SPACE_BASE, SPACE_SECTION, SPACE_TIGHT, TEXT_BODY, TEXT_MICRO


@dataclass
class StagedBarHandles:
    bar: Callable


def build_staged_bar(
    ctx: UIContext, review: ReviewHandles, generation: GenerationHandles
) -> StagedBarHandles:
    state = ctx.state
    refreshables = ctx.refreshables

    async def on_discard() -> None:
        # `reload_from_disk` alone only ever discarded grid edits (it
        # re-reads week_plan.json); a button reading "Discard pending
        # changes" right beside "Mon +700 kcal" has to make that line go
        # away too. `discard_pending_inputs` is the other three quarters —
        # see its docstring for why this is allowed to be stronger than a
        # generation, which deliberately leaves them alone.
        state.discard_pending_inputs()
        await generation.reload_from_disk()

    @ui.refreshable
    def bar() -> None:
        changes = state.pending_changes()
        if not changes:
            return

        summaries = " · ".join(c.summary for c in changes[:3])
        if len(changes) > 3:
            summaries += f" · +{len(changes) - 3} more"

        with ui.element("div").classes(
            f"w-full flex flex-row flex-wrap items-center gap-{SPACE_BASE} px-{SPACE_SECTION} "
            f"py-{SPACE_TIGHT} {RADIUS_CARD} bg-amber-400/10 border border-amber-800/60"
        ):
            ui.icon("edit_note").classes(f"{TEXT_BODY} text-amber-300")
            ui.label(f"{len(changes)} pending change(s)").classes(
                f"{TEXT_BODY} font-semibold text-amber-200"
            )
            ui.label(summaries).classes(f"{TEXT_MICRO} text-amber-200/70 truncate")
            ui.space()
            ui.button("Discard pending changes", on_click=on_discard).props(
                "dense flat no-caps size=sm"
            ).classes("text-slate-400")
            ui.button("Review", icon="fact_check", on_click=review.open).props(
                "dense flat no-caps size=sm"
            ).classes("text-amber-200")

            # Only meaningful when there's an actual grid edit — a swap, a
            # leftover link/unlink, a skip estimate. The other three pending
            # categories (target overrides, training, pantry) are inputs to
            # the *next* generation and are never written to disk on their
            # own; "Review" is still the only way to act on those. This is
            # what closes the gap "Review" alone left: a swap needed no model
            # call to decide, but before `save_grid` existed the only way to
            # make it stick was paying for a full week regeneration.
            if state.edited:
                async def on_save_grid() -> None:
                    await generation.save_grid(save_button)

                save_button = ui.button(
                    "Save changes", icon="save", on_click=on_save_grid
                ).props("dense flat no-caps size=sm").classes("text-emerald-200")
                with save_button:
                    ui.tooltip(
                        "Write the grid as it is now — swaps, links, skip "
                        "estimates — straight to disk. No AI call."
                    )

            async def on_generate_now() -> None:
                await generation.run_generation(generate_button)

            generate_button = ui.button(
                "Generate week", icon="bolt", on_click=on_generate_now
            ).props("dense no-caps size=sm")

    return StagedBarHandles(bar=bar)
