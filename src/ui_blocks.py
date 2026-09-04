"""Settings — Blocks (`dev/task-queue-modified.md`'s 3.1e).

A block is a dated exception laid over the standing preset — `dev/PROMPT-13.md`'s
"Fast 800 for four days" — never a second preset-shaped mechanism: it carries
a small fixed field list and no `preset` field at all (`src/blocks.py`).
This module is the Settings surface for that record: list, create, edit, end
early. It is a copy of `ui_presets.py`'s own copy of
`ui_review.training_editor`'s list-of-records convention (selects/numbers/
text only, add/remove repaints, a save-time check before anything reaches
disk) — the same reason `ui_presets.py`'s docstring gives: this needs no
widget shape the app does not already have.

**The check is the same one a future loud loader would run.** `PlannerState.
save_block`/`end_block_early`/`delete_block` all build the *whole* candidate
`blocks.json` and run it through `blocks.validate_blocks` before writing —
one function, two presentations, exactly as `presets.py` already establishes
and `ui_presets.py` copies for its own save path.

The logic lives in `ui_state.py` (`blocks_view`, `block_by_name`, `save_block`,
`end_block_early`, `delete_block`) and is tested there; this module is widget
construction only. **No new colour** — `lock`/`lock_open` (filled vs outline,
the same idiom `bookmark`/`bookmark_border` already uses for a preset's
"Active" row) carries "in a block" everywhere this task's surfaces need it,
slate throughout.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from nicegui import ui

import blocks as block_layer
from ui_context import UIContext
from ui_state import BlockRow
from ui_theme import (
    RADIUS_CARD,
    RADIUS_PANEL,
    RADIUS_PILL,
    SPACE_BASE,
    SPACE_HAIR,
    SPACE_PAGE,
    SPACE_SECTION,
    SPACE_TIGHT,
    SURFACE_INSET,
    TEXT_BODY,
    TEXT_HEAD,
    TEXT_MICRO,
)
from week import humanize


@dataclass
class BlocksHandles:
    section: Callable


_BLOCK_TYPE_OPTIONS = {"": "Ordinary", block_layer.TRANSITION_BLOCK_TYPE: "Transition (reverse-diet ramp)"}
_PROTEIN_FLOOR_BASIS_OPTIONS = {
    basis: ("Grams (typed by hand)" if basis == "grams" else humanize(basis).title())
    for basis in block_layer.PROTEIN_FLOOR_BASES
}
_DEFAULT_PROTEIN_FLOOR = {"basis": "target_weight", "multiplier": 1.8}

# What a save/delete/end-early touches beyond the panel's own list: a block
# can move a day's active diet styles, deficit rate and protein floor, the
# same blast radius `ui_review.preset_block`'s pick handler already refreshes
# for a preset change.
_BLOCK_WRITE_TOPICS = ("blocks", "plan", "targets", "training", "pantry")


def build_blocks(ctx: UIContext) -> BlocksHandles:
    state = ctx.state
    repository = ctx.repository
    refreshables = ctx.refreshables

    def diet_style_options() -> Dict[str, str]:
        return {
            key: entry.get("label", humanize(key).title())
            for key, entry in (state.base_config.get("diet_styles") or {}).items()
        }

    # ---- the editor dialog --------------------------------------------------

    def open_editor(name: Optional[str]) -> None:
        """Build a fresh dialog for `name` (None = a new block). Rebuilt each
        open, the same shape `ui_presets.py`'s own editor uses — a dialog
        closed overnight has no business holding a stale draft."""
        is_new = name is None
        existing = None if is_new else state.block_by_name(name)
        # The draft carries blocks.py's own field names directly — a block
        # has a small *fixed* field list, not an arbitrary set of override
        # paths, so unlike the preset editor there is no override-path
        # indirection to build here: the draft *is* the candidate record.
        draft: Dict[str, object] = dict(existing) if existing else {}
        draft_name = {"value": "" if is_new else name}
        protein_floor_on = {"value": bool(draft.get(block_layer.PROTEIN_FLOOR_KEY))}
        skip_transition_on = {"value": bool(draft.get(block_layer.SKIP_TRANSITION_KEY))}

        def value_of(key, default=""):
            return draft.get(key, default)

        with ui.dialog() as dialog, ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[36rem] max-w-full "
            f"max-h-[85vh] overflow-y-auto flex flex-col gap-{SPACE_SECTION}"
        ):
            with ui.element("div").classes(
                f"flex flex-row items-center gap-{SPACE_TIGHT}"
            ):
                ui.icon("lock").classes(f"{TEXT_HEAD} text-slate-300")
                ui.label("New block" if is_new else f"Edit “{name}”").classes(
                    f"{TEXT_HEAD} font-semibold"
                )

            ui.label(
                "A dated exception over this week's preset — never a second "
                "way to pick the preset itself."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            if is_new:
                ui.input(
                    label="Name (permanent — what a successor names)",
                    value=draft_name["value"],
                    on_change=lambda e: draft_name.__setitem__(
                        "value", (e.value or "").strip()
                    ),
                ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")
            else:
                ui.label(name).classes(f"{TEXT_BODY} font-semibold text-slate-200")

            with ui.row().classes(
                f"w-full items-center flex-nowrap gap-{SPACE_BASE}"
            ):
                ui.input(
                    label="Starts (YYYY-MM-DD)",
                    value=value_of(block_layer.STARTS_ON_KEY),
                    on_change=lambda e: draft.__setitem__(
                        block_layer.STARTS_ON_KEY, (e.value or "").strip()
                    ),
                ).props("dense outlined debounce=350").classes(f"flex-1 {TEXT_BODY}")
                ui.input(
                    label="Ends (YYYY-MM-DD)",
                    value=value_of(block_layer.ENDS_ON_KEY),
                    on_change=lambda e: draft.__setitem__(
                        block_layer.ENDS_ON_KEY, (e.value or "").strip()
                    ),
                ).props("dense outlined debounce=350").classes(f"flex-1 {TEXT_BODY}")

            ui.input(
                label="Body goal",
                value=value_of(block_layer.BODY_GOAL_KEY),
                on_change=lambda e: draft.__setitem__(
                    block_layer.BODY_GOAL_KEY, e.value or ""
                ),
            ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")
            ui.input(
                label="Fitness goal",
                value=value_of(block_layer.FITNESS_GOAL_KEY),
                on_change=lambda e: draft.__setitem__(
                    block_layer.FITNESS_GOAL_KEY, e.value or ""
                ),
            ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")

            def on_change_block_type(event) -> None:
                if event.value:
                    draft[block_layer.BLOCK_TYPE_KEY] = event.value
                else:
                    draft.pop(block_layer.BLOCK_TYPE_KEY, None)

            ui.select(
                _BLOCK_TYPE_OPTIONS,
                value=value_of(block_layer.BLOCK_TYPE_KEY, ""),
                label="Block type",
                on_change=on_change_block_type,
            ).props("dense outlined").classes(f"w-full {TEXT_BODY}")

            ui.select(
                diet_style_options(),
                value=list(value_of(block_layer.DIET_STYLES_KEY) or []),
                multiple=True,
                label="Diet styles active in this block",
                on_change=lambda e: draft.__setitem__(
                    block_layer.DIET_STYLES_KEY, list(e.value or [])
                ),
            ).props("dense outlined use-chips clearable").classes(f"w-full {TEXT_BODY}")
            ui.label(
                "Every day this block covers, unions onto whatever the preset "
                "already activates — not a per-day window (hand-edit "
                "config/blocks.json for that)."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            def on_change_rate(event) -> None:
                if event.value in (None, ""):
                    draft.pop(block_layer.TARGET_RATE_KEY, None)
                else:
                    draft[block_layer.TARGET_RATE_KEY] = float(event.value)

            ui.number(
                label="Target rate (kg/week — positive loses faster, negative reverse-diets)",
                value=value_of(block_layer.TARGET_RATE_KEY, None),
                step=0.05,
                on_change=on_change_rate,
            ).props("dense outlined clearable debounce=350").classes(f"w-full {TEXT_BODY}")

            # ---- protein floor ------------------------------------------
            with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} w-full"):
                with ui.row().classes("items-center gap-2"):
                    def on_toggle_floor(event) -> None:
                        protein_floor_on["value"] = bool(event.value)
                        if not event.value:
                            draft.pop(block_layer.PROTEIN_FLOOR_KEY, None)
                        else:
                            draft.setdefault(
                                block_layer.PROTEIN_FLOOR_KEY, dict(_DEFAULT_PROTEIN_FLOOR)
                            )
                        floor_body.refresh()

                    ui.switch(
                        value=protein_floor_on["value"], on_change=on_toggle_floor
                    ).props("dense size=sm color=teal")
                    ui.label("Protein floor").classes(f"{TEXT_BODY} text-slate-300")

                @ui.refreshable
                def floor_body() -> None:
                    if not protein_floor_on["value"]:
                        return
                    protein_floor = draft.setdefault(
                        block_layer.PROTEIN_FLOOR_KEY, dict(_DEFAULT_PROTEIN_FLOOR)
                    )
                    with ui.row().classes(
                        f"w-full items-center flex-nowrap gap-{SPACE_BASE}"
                    ):
                        ui.select(
                            _PROTEIN_FLOOR_BASIS_OPTIONS,
                            value=protein_floor.get("basis"),
                            label="Basis",
                            on_change=lambda e, pf=protein_floor: pf.__setitem__(
                                "basis", e.value
                            ),
                        ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")
                        ui.number(
                            label="Multiplier (or grams, for the 'grams' basis)",
                            value=protein_floor.get("multiplier"),
                            step=0.1,
                            on_change=lambda e, pf=protein_floor: pf.__setitem__(
                                "multiplier",
                                float(e.value) if e.value not in (None, "") else None,
                            ),
                        ).props("dense outlined debounce=350").classes(
                            f"flex-1 {TEXT_BODY}"
                        )
                    if protein_floor.get("resolved_g") is not None:
                        ui.label(
                            f"Frozen at {protein_floor['resolved_g']:.0f}g as of "
                            f"{protein_floor.get('resolved_on', '?')} — leaving "
                            "basis/multiplier unchanged keeps this figure; "
                            "changing either re-resolves it at next use."
                        ).classes(f"{TEXT_MICRO} text-slate-400")

                floor_body()

            ui.input(
                label="Training intent",
                value=value_of(block_layer.TRAINING_INTENT_KEY),
                on_change=lambda e: draft.__setitem__(
                    block_layer.TRAINING_INTENT_KEY, e.value or ""
                ),
            ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")
            ui.input(
                label="Peak day",
                value=value_of(block_layer.PEAK_DAY_KEY),
                on_change=lambda e: draft.__setitem__(
                    block_layer.PEAK_DAY_KEY, e.value or ""
                ),
            ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")
            ui.input(
                label="Notes",
                value=value_of(block_layer.NOTES_KEY),
                on_change=lambda e: draft.__setitem__(
                    block_layer.NOTES_KEY, e.value or ""
                ),
            ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")

            ui.label(
                "A restriction block (a protein floor, or a rate that speeds "
                "up the deficit) needs a named successor or an explicit "
                "skip — the end of a restriction is the highest-risk moment "
                "in the protocol."
            ).classes(f"{TEXT_MICRO} text-slate-400")
            successor_options = {
                row_name: row_name
                for row_name in state.blocks_view().names
                if row_name and row_name != name
            }
            ui.select(
                successor_options,
                value=value_of(block_layer.NEXT_BLOCK_KEY) or None,
                label="Successor block",
                on_change=lambda e: (
                    draft.__setitem__(block_layer.NEXT_BLOCK_KEY, e.value)
                    if e.value
                    else draft.pop(block_layer.NEXT_BLOCK_KEY, None)
                ),
            ).props("dense outlined clearable").classes(f"w-full {TEXT_BODY}")

            def on_toggle_skip(event) -> None:
                skip_transition_on["value"] = bool(event.value)
                if event.value:
                    draft[block_layer.SKIP_TRANSITION_KEY] = True
                else:
                    draft.pop(block_layer.SKIP_TRANSITION_KEY, None)

            ui.checkbox(
                "Skip the transition (explicit — never a silent absence)",
                value=skip_transition_on["value"],
                on_change=on_toggle_skip,
            ).classes(f"{TEXT_BODY} text-slate-300")

            # One repaint target for a rejected save — below every input, so
            # refreshing it never steals a field's cursor, the same
            # `ui_presets.py`'s `outcome` shape.
            save_result: Dict[str, object] = {"failures": None}

            @ui.refreshable
            def outcome() -> None:
                failures = save_result["failures"]
                if not failures:
                    return
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_HAIR} p-{SPACE_TIGHT} "
                    f"{RADIUS_CARD} {SURFACE_INSET}"
                ):
                    with ui.element("div").classes(
                        f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
                    ):
                        ui.icon("report_problem").classes("text-slate-300 shrink-0")
                        ui.label("Can't save this block:").classes(
                            f"{TEXT_BODY} font-semibold text-slate-200"
                        )
                    for message in failures:
                        ui.label(message).classes(f"{TEXT_MICRO} text-slate-300")

            async def on_save() -> None:
                target_name = draft_name["value"] if is_new else name
                candidate = dict(draft, **{block_layer.NAME_KEY: target_name})
                failures = await state.save_block(repository, candidate, is_new=is_new)
                if failures:
                    save_result["failures"] = failures
                    outcome.refresh()
                    return
                dialog.close()
                ui.notify(f"Block “{target_name}” saved")
                refreshables.refresh(*_BLOCK_WRITE_TOPICS)

            outcome()

            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center justify-end gap-{SPACE_BASE}"
            ):
                ui.button("Cancel", on_click=dialog.close).props(
                    "flat no-caps size=sm"
                ).classes("text-slate-400")
                ui.button("Save", icon="check", on_click=on_save).props(
                    "unelevated no-caps size=sm color=teal"
                ).classes("text-slate-900 font-semibold")

        dialog.open()

    # ---- the list -------------------------------------------------------

    @ui.refreshable
    def section() -> None:
        view = state.blocks_view()
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_SECTION} max-w-xl"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-baseline justify-between gap-{SPACE_BASE}"
            ):
                ui.label("Blocks").classes(f"{TEXT_HEAD} font-semibold text-slate-200")
                ui.button(
                    "New block", icon="add", on_click=lambda: open_editor(None)
                ).props("flat no-caps size=sm").classes("text-slate-300")
            ui.label(
                "A dated exception over this week's preset — a diet-style "
                "activation, a deficit rate, a protein floor — for a named "
                "span, never a standing change."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            if not view.rows:
                ui.label(
                    "No blocks declared — “New block” writes config/blocks.json."
                ).classes(f"{TEXT_MICRO} text-slate-400 italic")

            for row in view.rows:
                _block_row(row)

    def _block_row(row: BlockRow) -> None:
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_HAIR} p-{SPACE_TIGHT} {RADIUS_CARD} "
            f"border border-slate-800 {SURFACE_INSET}"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT}"
            ):
                ui.icon("lock" if row.active else "lock_open").classes(
                    "text-slate-300 shrink-0"
                )
                ui.label(row.name).classes(
                    f"{TEXT_BODY} font-semibold text-slate-200 min-w-0"
                )
                if row.active:
                    ui.label("Active").classes(
                        f"{TEXT_MICRO} text-slate-300 border border-slate-700 "
                        f"px-{SPACE_HAIR} {RADIUS_PILL} shrink-0"
                    )
                ui.element("div").classes("grow")
                ui.button(
                    icon="edit", on_click=lambda _=None, n=row.name: open_editor(n)
                ).props("dense flat size=xs").classes("min-h-0 p-0 text-slate-400")
                if row.active:
                    end_button = ui.button(
                        icon="stop_circle",
                        on_click=lambda _=None, n=row.name: _end_early(n),
                    ).props("dense flat size=xs").classes("min-h-0 p-0 text-slate-400")
                    with end_button:
                        ui.tooltip("End this block early")
                ui.button(
                    icon="delete", on_click=lambda _=None, n=row.name: _delete(n)
                ).props("dense flat size=xs").classes("min-h-0 p-0 text-slate-400")

            ui.label(f"{row.starts_on} – {row.ends_on}").classes(
                f"{TEXT_MICRO} font-mono text-slate-400"
            )
            ui.label(f"{row.body_goal} · {row.fitness_goal}").classes(
                f"{TEXT_MICRO} text-slate-300"
            )
            if row.diet_style_labels:
                ui.label("Diet styles: " + ", ".join(row.diet_style_labels)).classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
            if row.protein_floor_summary:
                ui.label(f"Protein floor: {row.protein_floor_summary}").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
            if row.target_rate_kg_per_week is not None:
                ui.label(
                    f"Rate: {row.target_rate_kg_per_week:+.2f} kg/week"
                ).classes(f"{TEXT_MICRO} text-slate-400")
            if row.block_type:
                ui.label(f"Type: {humanize(row.block_type).title()}").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
            if row.next_block:
                ui.label(f"Successor: {row.next_block}").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
            elif row.skip_transition:
                ui.label("Transition explicitly skipped").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
            if row.notes:
                ui.label(row.notes).classes(f"{TEXT_MICRO} text-slate-400 italic")
            for problem in row.problems:
                with ui.element("div").classes(
                    f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
                ):
                    ui.icon("report_problem").classes(
                        f"{TEXT_MICRO} text-slate-400 shrink-0"
                    )
                    ui.label(problem).classes(f"{TEXT_MICRO} text-slate-300")

    async def _end_early(name: str) -> None:
        failures = await state.end_block_early(repository, name)
        if failures:
            ui.notify(failures[0], type="warning")
            return
        ui.notify(f"Block “{name}” ended.")
        refreshables.refresh(*_BLOCK_WRITE_TOPICS)

    async def _delete(name: str) -> None:
        failures = await state.delete_block(repository, name)
        if failures:
            ui.notify(failures[0], type="warning")
            return
        ui.notify(f"Block “{name}” deleted")
        refreshables.refresh(*_BLOCK_WRITE_TOPICS)

    return BlocksHandles(section=section)
