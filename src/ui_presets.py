"""The preset editor — Settings → Presets (PROMPT-9).

`config/presets.json` and the weekly pick shipped in v0.43.0; this is the
other half of what the user asked for — *"an interface to define different
profiles… editable via interface."* It is a copy of `ui_review.training_editor`'s
list-of-records pattern (design-03 §1: UI cost tracks widget *shapes*, and
this needs none the app does not already have) plus one save-time check.

**The check is the same one the loader runs.** `PlannerState.save_preset` goes
through `planner.resolve_preset_layer` — `apply_preset_layer`'s work returning
`PresetFailure`s instead of raising — so a preset the editor accepts is a
preset the next start accepts, and a preset it refuses is one the loader would
have refused too. A second validator here would be free to disagree about a
file this one wrote (design-03 §4.2).

The logic lives in `ui_state.py` (`PRESET_EDITOR_FIELDS`, `save_preset`,
`delete_preset`, `preview_preset`, `preview_week_shape`, `preset_catalog_view`)
and is tested there and in `test_presets.py`; this module is widget
construction only.

Bounded to preset keys with a config home and a clean widget shape.
design-01 §9.2 lists more — the prep-ceiling constants, the long-cook
threshold (one number in four prose copies), the numbers welded into
`DINNER_VARIETY_RULE`/`PORTION_DENSITY_GUARD`, the training constants,
`meal_styles`, `meal_overrides`. Each needs a code change first and is filed
in CHANGE-QUEUE.md items 7-9; every one is a later release, as PROMPT-7
intended. `week_shape` is no longer one of them — Task 1.2d gave it its own
"Week shape" field below (`render_week_shape`), a list-of-records editor
rather than the generic scalar/multi/object widgets the rest of this list
uses, because a batch or freezer draw is a record a preset adds and removes,
not a fixed set of sub-keys.

**No new colour.** A preset is a label — the active row is marked with a
filled `bookmark` glyph and the word "Active", the route `sync_freshness` and
the adherence marks both took (`ui-work` skill: amber already means five
things).
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from nicegui import ui

from ui_context import UIContext
from ui_state import (
    PRESET_EDITOR_FIELDS,
    PRESET_FIELD_DAY_CARBS,
    PRESET_FIELD_ENUM_OBJECT,
    PRESET_FIELD_INT,
    PRESET_FIELD_INT_LIST,
    PRESET_FIELD_MULTI_INT,
    PRESET_FIELD_MULTI_STR,
    PRESET_FIELD_WEEK_SHAPE,
    PRESET_OBJECT_KINDS,
    PresetField,
    preset_field_subkeys,
)
import presets as preset_layer
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

# Topics a save touches when the edited preset is the *active* one — the same
# set `ui_review.preset_block`'s pick handler refreshes, because a preset can
# move the day targets, the training schedule, the pantry rows and the grid.
_ACTIVE_EDIT_TOPICS = ("plan", "targets", "training", "pantry")


@dataclass
class PresetsHandles:
    section: Callable


def _parse_int_list(text: str) -> Optional[List[int]]:
    """"4, 3" -> [4, 3]; None when it is not a list of positive ints."""
    parts = [chunk.strip() for chunk in str(text).replace(",", " ").split()]
    if not parts:
        return None
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None
    if any(value < 1 for value in values):
        return None
    return values


def build_presets(ctx: UIContext) -> PresetsHandles:
    state = ctx.state
    repository = ctx.repository
    refreshables = ctx.refreshables

    def diet_style_options() -> Dict[str, str]:
        return {
            key: entry.get("label", humanize(key).title())
            for key, entry in (state.base_config.get("diet_styles") or {}).items()
        }

    # ---- the editor dialog ------------------------------------------------

    def open_editor(name: Optional[str]) -> None:
        """Build a fresh dialog for `name` (None = a new preset). Rebuilt each
        open, the same shape `ui_settings.py`'s three read views use — a
        dialog closed overnight has no business holding a stale draft."""
        is_new = name is None
        prior_overrides = (
            {} if is_new else preset_layer.preset_overrides(state.presets_config, name)
        )

        # `draft[field.key]` holds the widget's current value; a key absent
        # from `draft` (or holding the "unset" sentinel below) means "no
        # override for this field". Object fields also carry an enabled flag.
        draft: Dict[str, object] = {}
        enabled: Dict[str, bool] = {}
        draft_name = {"value": "" if is_new else name}
        draft_label = {
            "value": ""
            if is_new
            else preset_layer.preset_label(state.presets_config, name),
        }

        def base_value(field: PresetField):
            if field.kind == PRESET_FIELD_DAY_CARBS:
                return {
                    day: entry.get("net_carbs_g")
                    for day, entry in state.base_config.get("weekly_schedule", {}).items()
                }
            node = state.base_config
            for segment in field.path.split("."):
                node = node.get(segment, {}) if isinstance(node, dict) else {}
            return node

        # Seed the draft from the preset being edited.
        for field in PRESET_EDITOR_FIELDS:
            if field.kind == PRESET_FIELD_DAY_CARBS:
                per_day = {}
                for day in state.base_config.get("weekly_schedule", {}):
                    path = f"weekly_schedule.{day}.net_carbs_g"
                    if path in prior_overrides:
                        per_day[day] = prior_overrides[path]
                if per_day:
                    draft[field.key] = per_day
            elif field.path in prior_overrides:
                stored = prior_overrides[field.path]
                if field.kind == PRESET_FIELD_INT_LIST and isinstance(stored, list):
                    # the widget for this one is a text `ui.input`
                    stored = ", ".join(str(item) for item in stored)
                draft[field.key] = stored
                if field.kind in PRESET_OBJECT_KINDS:
                    enabled[field.key] = True

        def editor_overrides() -> dict:
            """`{override path: value}` for every field the user has set."""
            out: dict = {}
            for field in PRESET_EDITOR_FIELDS:
                if field.kind == PRESET_FIELD_DAY_CARBS:
                    for day, value in (draft.get(field.key) or {}).items():
                        if value not in (None, ""):
                            out[f"weekly_schedule.{day}.net_carbs_g"] = float(value)
                    continue
                if field.kind in PRESET_OBJECT_KINDS:
                    if not enabled.get(field.key):
                        continue
                    out[field.path] = draft.get(field.key) or {}
                    continue
                value = draft.get(field.key)
                if field.kind in (PRESET_FIELD_MULTI_INT, PRESET_FIELD_MULTI_STR):
                    if value:
                        out[field.path] = list(value)
                elif field.kind == PRESET_FIELD_INT_LIST:
                    parsed = _parse_int_list(value) if value not in (None, "") else None
                    if parsed is not None:
                        out[field.path] = parsed
                elif value not in (None, ""):
                    out[field.path] = (
                        int(value) if field.kind == PRESET_FIELD_INT else float(value)
                    )
            return out

        def local_field_errors() -> List[str]:
            """The one class of mistake the shared validator can't see: text
            the widget could not turn into a value. `editor_overrides` drops an
            unparseable list silently, so a "4, x" typed into the cuisine
            pattern would just not save — worth naming rather than ignoring."""
            errors = []
            for field in PRESET_EDITOR_FIELDS:
                if field.kind != PRESET_FIELD_INT_LIST:
                    continue
                raw = draft.get(field.key)
                if raw not in (None, "") and _parse_int_list(raw) is None:
                    errors.append(
                        f"{field.label}: “{raw}” is not a list of positive whole "
                        f"numbers (e.g. “4, 3”)."
                    )
            return errors

        # ---- one widget per field ---------------------------------------

        def field_help(field: PresetField, base) -> None:
            shown = base if not isinstance(base, dict) else None
            suffix = f" Currently: {shown}." if shown not in (None, "") else ""
            ui.label(field.help + suffix).classes(
                f"{TEXT_MICRO} text-slate-400"
            )

        def render_scalar(field: PresetField) -> None:
            base = base_value(field)
            with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} w-full"):
                ui.label(field.label).classes(f"{TEXT_BODY} text-slate-300")

                def on_change(event, key=field.key) -> None:
                    draft[key] = event.value

                if field.kind == PRESET_FIELD_INT_LIST:
                    ui.input(
                        value=draft.get(field.key, ""),
                        placeholder=", ".join(str(v) for v in (base or [])),
                        on_change=on_change,
                    ).props("dense outlined clearable debounce=350").classes(
                        f"w-full {TEXT_BODY}"
                    )
                else:
                    ui.number(
                        value=draft.get(field.key),
                        placeholder=str(base) if base not in (None, "") else "",
                        min=field.minimum,
                        max=field.maximum,
                        step=field.step,
                        on_change=on_change,
                    ).props("dense outlined clearable debounce=350").classes(
                        f"w-full {TEXT_BODY}"
                    )
                field_help(field, base)

        def render_multi(field: PresetField) -> None:
            base = base_value(field)
            if field.kind == PRESET_FIELD_MULTI_INT:
                options = {value: str(value) for value in field.choices}
            else:
                options = diet_style_options()
            with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} w-full"):
                ui.label(field.label).classes(f"{TEXT_BODY} text-slate-300")

                def on_change(event, key=field.key) -> None:
                    draft[key] = list(event.value or [])

                ui.select(
                    options,
                    value=list(draft.get(field.key) or []),
                    multiple=True,
                    on_change=on_change,
                ).props("dense outlined use-chips clearable").classes(
                    f"w-full {TEXT_BODY}"
                )
                field_help(field, base)

        def render_object(field: PresetField) -> None:
            subkeys = preset_field_subkeys(field, state.base_config)
            base = base_value(field) or {}

            @ui.refreshable
            def body() -> None:
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} w-full"
                ):
                    with ui.element("div").classes(
                        "flex flex-row flex-nowrap items-center justify-between "
                        f"gap-{SPACE_BASE}"
                    ):
                        ui.label(field.label).classes(
                            f"{TEXT_BODY} text-slate-300 min-w-0"
                        )

                        def on_toggle(event, key=field.key) -> None:
                            enabled[key] = bool(event.value)
                            if enabled[key] and key not in draft:
                                draft[key] = {sub: base.get(sub) for sub in subkeys}
                            body.refresh()

                        ui.switch(
                            value=enabled.get(field.key, False), on_change=on_toggle
                        ).props("dense size=sm color=teal")
                    if not enabled.get(field.key):
                        ui.label(field.help).classes(f"{TEXT_MICRO} text-slate-400")
                        return
                    values = draft.setdefault(
                        field.key, {sub: base.get(sub) for sub in subkeys}
                    )
                    with ui.element("div").classes(
                        f"flex flex-row flex-wrap gap-{SPACE_BASE}"
                    ):
                        for sub in subkeys:

                            def on_sub(event, key=field.key, s=sub) -> None:
                                draft[key][s] = event.value

                            if field.kind == PRESET_FIELD_ENUM_OBJECT:
                                ui.select(
                                    {c: c for c in field.choices},
                                    value=values.get(sub),
                                    label=humanize(sub).title(),
                                    on_change=on_sub,
                                ).props("dense outlined").classes(
                                    f"flex-1 min-w-[6rem] {TEXT_BODY}"
                                )
                            else:
                                ui.number(
                                    value=values.get(sub),
                                    label=humanize(sub).title(),
                                    min=field.minimum,
                                    max=field.maximum,
                                    step=field.step,
                                    on_change=on_sub,
                                ).props("dense outlined debounce=350").classes(
                                    f"flex-1 min-w-[6rem] {TEXT_BODY}"
                                )
                    ui.label(field.help).classes(f"{TEXT_MICRO} text-slate-400")

            body()

        def render_day_carbs(field: PresetField) -> None:
            days = list(state.base_config.get("weekly_schedule", {}).keys())
            base = base_value(field)
            with ui.element("div").classes(f"flex flex-col gap-{SPACE_HAIR} w-full"):
                ui.label(field.label).classes(f"{TEXT_BODY} text-slate-300")
                ui.label(field.help).classes(f"{TEXT_MICRO} text-slate-400")
                with ui.element("div").classes(
                    f"flex flex-row flex-wrap gap-{SPACE_TIGHT}"
                ):
                    seeded = draft.get(field.key) or {}
                    for day in days:

                        def on_day(event, d=day) -> None:
                            bag = draft.setdefault(field.key, {})
                            if event.value in (None, ""):
                                bag.pop(d, None)
                            else:
                                bag[d] = event.value

                        ui.number(
                            value=seeded.get(day),
                            label=day[:3],
                            placeholder=str(base.get(day)),
                            min=0,
                            step=5,
                            on_change=on_day,
                        ).props("dense outlined clearable debounce=350").classes(
                            f"w-20 {TEXT_BODY}"
                        )

        # ---- week shape (Task 1.2d) ---------------------------------------
        #
        # A copy of `ui_review.training_editor`'s list-of-records convention
        # (this module's own docstring names it as the pattern the whole file
        # follows): one bordered card per record, fields bound through
        # `on_change` closures that mutate the record dict in place rather
        # than `bind_value` — so typing in one field never repaints the list
        # and steals focus from itself — and add/remove are the only actions
        # that call `body.refresh()`. Record order carries no meaning (unlike
        # a `serves` list's own day order, which the shared validator checks),
        # so there is no drag handle.

        _BATCH_DEFAULTS = {
            "name": "", "meal_type": "", "cook_on": "prep_day", "serves": [],
            "freeze_portions": 0,
        }
        _DRAW_DEFAULTS = {"meal_type": "", "day": ""}

        def render_week_shape(field: PresetField) -> None:
            meal_type_options = {m: humanize(m).title() for m in state.meal_types}
            cook_on_options = {"prep_day": "Prep day", **{d: d for d in state.days}}
            day_options = {d: d for d in state.days}

            @ui.refreshable
            def body() -> None:
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} w-full"
                ):
                    with ui.element("div").classes(
                        "flex flex-row flex-nowrap items-center justify-between "
                        f"gap-{SPACE_BASE}"
                    ):
                        ui.label(field.label).classes(
                            f"{TEXT_BODY} text-slate-300 min-w-0"
                        )

                        def on_toggle(event, key=field.key) -> None:
                            enabled[key] = bool(event.value)
                            if enabled[key] and key not in draft:
                                draft[key] = {"batches": [], "freezer_draws": []}
                            body.refresh()

                        ui.switch(
                            value=enabled.get(field.key, False), on_change=on_toggle
                        ).props("dense size=sm color=teal")
                    if not enabled.get(field.key):
                        ui.label(field.help).classes(f"{TEXT_MICRO} text-slate-400")
                        return

                    shape = draft.setdefault(field.key, {"batches": [], "freezer_draws": []})
                    shape.setdefault("batches", [])
                    shape.setdefault("freezer_draws", [])

                    def batch_row(index: int, record: dict) -> None:
                        with ui.element("div").classes(
                            f"flex flex-col gap-{SPACE_HAIR} p-{SPACE_TIGHT} {RADIUS_CARD} "
                            "border border-slate-800 bg-slate-950/30"
                        ):
                            with ui.row().classes(
                                f"w-full items-center flex-nowrap gap-{SPACE_BASE}"
                            ):
                                ui.input(
                                    label="Name", value=record.get("name", ""),
                                    on_change=lambda e, r=record: r.__setitem__(
                                        "name", e.value or ""
                                    ),
                                ).props("dense outlined debounce=350").classes(
                                    f"flex-1 min-w-0 {TEXT_BODY}"
                                )

                                def on_remove_batch(i: int = index) -> None:
                                    shape["batches"].pop(i)
                                    body.refresh()

                                ui.button(icon="delete", on_click=on_remove_batch).props(
                                    "dense flat size=xs"
                                ).classes("min-h-0 p-0 text-slate-400")
                            with ui.row().classes(
                                f"w-full items-center flex-nowrap gap-{SPACE_BASE}"
                            ):
                                ui.select(
                                    meal_type_options, value=record.get("meal_type"),
                                    label="Meal",
                                    on_change=lambda e, r=record: r.__setitem__(
                                        "meal_type", e.value
                                    ),
                                ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")
                                ui.select(
                                    cook_on_options, value=record.get("cook_on", "prep_day"),
                                    label="Cook on",
                                    on_change=lambda e, r=record: r.__setitem__(
                                        "cook_on", e.value
                                    ),
                                ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")
                            ui.select(
                                day_options, value=list(record.get("serves") or []),
                                label="Serves", multiple=True,
                                on_change=lambda e, r=record: r.__setitem__(
                                    "serves", list(e.value or [])
                                ),
                            ).props("dense outlined use-chips clearable").classes(
                                f"w-full {TEXT_BODY}"
                            )
                            ui.number(
                                label="Freeze portions", value=record.get("freeze_portions", 0),
                                min=0, step=1, precision=0,
                                on_change=lambda e, r=record: r.__setitem__(
                                    "freeze_portions", int(e.value or 0)
                                ),
                            ).props("dense outlined debounce=350").classes(
                                f"w-40 {TEXT_BODY}"
                            )
                            served = len(record.get("serves") or [])
                            freeze = int(record.get("freeze_portions") or 0)
                            total = served * state.servings + freeze
                            ui.label(
                                f"→ {total} portion(s): {served} meal(s) × "
                                f"{state.servings} people + {freeze} to the freezer"
                            ).classes(f"{TEXT_MICRO} text-slate-400")

                    def draw_row(index: int, record: dict) -> None:
                        with ui.row().classes(
                            f"w-full items-center flex-nowrap gap-{SPACE_BASE} p-{SPACE_TIGHT} "
                            f"{RADIUS_CARD} border border-slate-800 bg-slate-950/30"
                        ):
                            ui.select(
                                meal_type_options, value=record.get("meal_type"),
                                label="Meal",
                                on_change=lambda e, r=record: r.__setitem__(
                                    "meal_type", e.value
                                ),
                            ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")
                            ui.select(
                                day_options, value=record.get("day"), label="Day",
                                on_change=lambda e, r=record: r.__setitem__("day", e.value),
                            ).props("dense outlined").classes(f"flex-1 {TEXT_BODY}")

                            def on_remove_draw(i: int = index) -> None:
                                shape["freezer_draws"].pop(i)
                                body.refresh()

                            ui.button(icon="delete", on_click=on_remove_draw).props(
                                "dense flat size=xs"
                            ).classes("min-h-0 p-0 text-slate-400")

                    ui.label("Batches").classes(
                        f"{TEXT_MICRO} text-slate-400 font-semibold"
                    )
                    if not shape["batches"]:
                        ui.label("No automatic batches — every slot cooks fresh.").classes(
                            f"{TEXT_MICRO} text-slate-400 italic"
                        )
                    for index, record in enumerate(shape["batches"]):
                        batch_row(index, record)

                    def on_add_batch() -> None:
                        shape["batches"].append(dict(_BATCH_DEFAULTS))
                        body.refresh()

                    ui.button("Add batch", icon="add", on_click=on_add_batch).props(
                        "dense flat no-caps size=sm"
                    ).classes("text-slate-400")

                    ui.label("Freezer draws").classes(
                        f"{TEXT_MICRO} text-slate-400 font-semibold mt-1"
                    )
                    for index, record in enumerate(shape["freezer_draws"]):
                        draw_row(index, record)

                    def on_add_draw() -> None:
                        shape["freezer_draws"].append(dict(_DRAW_DEFAULTS))
                        body.refresh()

                    ui.button("Add freezer draw", icon="add", on_click=on_add_draw).props(
                        "dense flat no-caps size=sm"
                    ).classes("text-slate-400")

                    # ---- on-demand preview -------------------------------
                    #
                    # Its own button, separate from the dialog's overall
                    # "Preview" (which shows the resolved calorie curve) —
                    # this one runs `PlannerState.preview_week_shape`, the
                    # same validator/applier the loader and generation use,
                    # and shows what it actually returns: slot relationships
                    # and warnings, never a second, simplified applier. No
                    # model call, no file write, no live-spec mutation, no
                    # stock reservation, no repaint of the week canvas.
                    preview_state: Dict[str, object] = {"value": None}

                    @ui.refreshable
                    def preview_body() -> None:
                        result = preview_state["value"]
                        if result is None:
                            return
                        with ui.element("div").classes(
                            f"flex flex-col gap-{SPACE_HAIR} p-{SPACE_TIGHT} {RADIUS_CARD} "
                            f"{SURFACE_INSET}"
                        ):
                            if not result.ok:
                                ui.label("Can't apply this shape:").classes(
                                    f"{TEXT_MICRO} font-semibold text-slate-200"
                                )
                                for message in result.errors:
                                    ui.label(message).classes(
                                        f"{TEXT_MICRO} text-slate-300"
                                    )
                                return
                            if not result.batch_anchors and not result.warnings:
                                ui.label(
                                    "No automatic batches or freezer draws this week."
                                ).classes(f"{TEXT_MICRO} text-slate-400")
                            for name, anchor in result.batch_anchors.items():
                                ui.label(
                                    f"{name} → {anchor}" if anchor
                                    else f"{name} → no room this week"
                                ).classes(f"{TEXT_MICRO} font-mono text-slate-300")
                            for warning in result.warnings:
                                ui.label(warning).classes(
                                    f"{TEXT_MICRO} text-slate-400"
                                )

                    def on_preview() -> None:
                        preview_state["value"] = state.preview_week_shape(shape)
                        preview_body.refresh()

                    ui.button("Preview", icon="visibility", on_click=on_preview).props(
                        "dense outline no-caps size=sm"
                    ).classes("text-slate-200 mt-1")
                    preview_body()
                    ui.label(field.help).classes(f"{TEXT_MICRO} text-slate-400")

            body()

        def render_field(field: PresetField) -> None:
            if field.kind == PRESET_FIELD_DAY_CARBS:
                render_day_carbs(field)
            elif field.kind in (PRESET_FIELD_MULTI_INT, PRESET_FIELD_MULTI_STR):
                render_multi(field)
            elif field.kind == PRESET_FIELD_WEEK_SHAPE:
                render_week_shape(field)
            elif field.kind in PRESET_OBJECT_KINDS:
                render_object(field)
            else:
                render_scalar(field)

        # ---- the dialog shell ------------------------------------------

        with ui.dialog() as dialog, ui.element("div").classes(
            f"bg-slate-900 {RADIUS_PANEL} p-{SPACE_PAGE} w-[40rem] max-w-full "
            f"max-h-[85vh] overflow-y-auto flex flex-col gap-{SPACE_SECTION}"
        ):
            with ui.element("div").classes(
                f"flex flex-row items-center gap-{SPACE_TIGHT}"
            ):
                ui.icon("tune").classes(f"{TEXT_HEAD} text-slate-300")
                ui.label("New preset" if is_new else f"Edit “{draft_label['value']}”").classes(
                    f"{TEXT_HEAD} font-semibold"
                )

            ui.label(
                "Every field left blank keeps today's behaviour — a preset only "
                "changes what you fill in. Hand-edited keys the editor does not "
                "show are preserved."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            if is_new:
                ui.input(
                    label="Name (permanent — used to record which preset a week ran under)",
                    value=draft_name["value"],
                    on_change=lambda e: draft_name.__setitem__("value", (e.value or "").strip()),
                ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")
            ui.input(
                label="Label",
                value=draft_label["value"],
                placeholder=draft_name["value"] or "shown in the weekly pick",
                on_change=lambda e: draft_label.__setitem__("value", e.value or ""),
            ).props("dense outlined debounce=350").classes(f"w-full {TEXT_BODY}")

            for field in PRESET_EDITOR_FIELDS:
                if not field.advanced:
                    render_field(field)

            advanced = [f for f in PRESET_EDITOR_FIELDS if f.advanced]
            if advanced:
                with ui.expansion("Advanced", icon="expand_more").classes("w-full").props(
                    f"dense header-class='{TEXT_BODY} px-0'"
                ):
                    with ui.element("div").classes(
                        f"flex flex-col gap-{SPACE_BASE} pt-{SPACE_TIGHT}"
                    ):
                        for field in advanced:
                            render_field(field)

            # One repaint target for both the preview and a rejected save. It
            # sits below every input, so refreshing it never steals a field's
            # cursor, and it renders nothing until asked.
            result: Dict[str, object] = {"preview": None, "failures": None}

            def _render_failures(failures: List[str]) -> None:
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_HAIR} p-{SPACE_TIGHT} {RADIUS_CARD} "
                    f"{SURFACE_INSET}"
                ):
                    with ui.element("div").classes(
                        f"flex flex-row flex-nowrap items-center gap-{SPACE_HAIR}"
                    ):
                        ui.icon("report_problem").classes("text-slate-300 shrink-0")
                        ui.label("Can't save this preset:").classes(
                            f"{TEXT_BODY} font-semibold text-slate-200"
                        )
                    for message in failures:
                        ui.label(message).classes(f"{TEXT_MICRO} text-slate-300")

            def _render_preview(preview) -> None:
                with ui.element("div").classes(
                    f"flex flex-col gap-{SPACE_TIGHT} p-{SPACE_TIGHT} {RADIUS_CARD} "
                    f"{SURFACE_INSET}"
                ):
                    if preview.identical:
                        ui.label(
                            "This preset changes nothing — the week is identical to "
                            "the base config."
                        ).classes(f"{TEXT_BODY} text-slate-200")
                        return
                    ui.label("What this preset changes").classes(
                        f"{TEXT_BODY} font-semibold text-slate-200"
                    )
                    for line in preview.changes:
                        ui.label(line).classes(f"{TEXT_MICRO} font-mono text-slate-300")
                    ui.label("Resolved daily targets").classes(
                        f"{TEXT_MICRO} text-slate-400 pt-{SPACE_HAIR}"
                    )
                    with ui.element("div").classes(
                        f"flex flex-col {TEXT_MICRO} font-mono text-slate-300 "
                        "overflow-x-auto"
                    ):
                        for day in preview.day_targets:
                            moved = day.preset != day.base
                            preset = day.preset
                            row = (
                                f"{day.day[:3]}  "
                                f"{preset['calories']:>4.0f} kcal   "
                                f"P {preset['protein_g']:>3.0f}   "
                                f"C {preset['net_carbs_g']:>3.0f}   "
                                f"Fib {preset['fiber_g']:>2.0f}"
                                f"{'   ← moved' if moved else ''}"
                            )
                            ui.label(row).classes(
                                "whitespace-pre "
                                + ("text-slate-200" if moved else "text-slate-400")
                            )

            @ui.refreshable
            def outcome() -> None:
                if result["failures"]:
                    _render_failures(result["failures"])
                elif result["preview"] is not None:
                    _render_preview(result["preview"])

            def _do_preview() -> None:
                local = local_field_errors()
                if local:
                    result["failures"] = local
                    result["preview"] = None
                    outcome.refresh()
                    return
                preview = state.preview_preset(
                    name=(draft_name["value"] if is_new else name),
                    label=draft_label["value"],
                    editor_overrides=editor_overrides(),
                    is_new=is_new,
                )
                result["preview"] = preview
                result["failures"] = None if preview.ok else preview.failures
                outcome.refresh()

            async def _do_save() -> None:
                local = local_field_errors()
                if local:
                    result["failures"] = local
                    result["preview"] = None
                    outcome.refresh()
                    return
                target_name = draft_name["value"] if is_new else name
                failures = await state.save_preset(
                    repository,
                    name=target_name,
                    label=draft_label["value"],
                    editor_overrides=editor_overrides(),
                    is_new=is_new,
                )
                if failures:
                    result["failures"] = failures
                    result["preview"] = None
                    outcome.refresh()
                    return
                dialog.close()
                ui.notify(f"Preset “{draft_label['value'] or target_name}” saved")
                topics = ["presets"]
                if target_name == preset_layer.active_preset_name(state.presets_config):
                    topics += list(_ACTIVE_EDIT_TOPICS)
                refreshables.refresh(*topics)

            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center justify-end gap-{SPACE_BASE}"
            ):
                ui.button("Cancel", on_click=dialog.close).props(
                    "flat no-caps size=sm"
                ).classes("text-slate-400")
                ui.button("Preview", icon="visibility", on_click=_do_preview).props(
                    "outline no-caps size=sm"
                ).classes("text-slate-200")
                ui.button("Save", icon="check", on_click=_do_save).props(
                    "unelevated no-caps size=sm color=teal"
                ).classes("text-slate-900 font-semibold")

            outcome()

        dialog.open()

    # ---- the list --------------------------------------------------------

    @ui.refreshable
    def section() -> None:
        view = state.preset_catalog_view()
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_SECTION} p-{SPACE_SECTION} max-w-xl"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-baseline justify-between gap-{SPACE_BASE}"
            ):
                ui.label("Presets").classes(f"{TEXT_HEAD} font-semibold text-slate-200")
                ui.button(
                    "New preset", icon="add", on_click=lambda: open_editor(None)
                ).props("flat no-caps size=sm").classes("text-slate-300")
            ui.label(
                "A preset is a named set of overrides for the week — what is "
                "cooked, the carb shape, how strict, how lazy. Pick one for the "
                "week in the review dialog; edit them here. Hand-editing "
                "config/presets.json stays authoritative."
            ).classes(f"{TEXT_MICRO} text-slate-400")

            if not view.rows:
                ui.label(
                    "No presets yet — “New preset” writes config/presets.json."
                ).classes(f"{TEXT_MICRO} text-slate-400 italic")

            for row in view.rows:
                _preset_row(row)

    def _preset_row(row) -> None:
        with ui.element("div").classes(
            f"flex flex-col gap-{SPACE_HAIR} p-{SPACE_TIGHT} {RADIUS_CARD} "
            f"border border-slate-800 {SURFACE_INSET}"
        ):
            with ui.element("div").classes(
                f"flex flex-row flex-nowrap items-center gap-{SPACE_TIGHT}"
            ):
                ui.icon("bookmark" if row.active else "bookmark_border").classes(
                    "text-slate-300 shrink-0"
                )
                ui.label(row.label).classes(
                    f"{TEXT_BODY} font-semibold text-slate-200 min-w-0"
                )
                if row.label != row.name:
                    ui.label(row.name).classes(f"{TEXT_MICRO} text-slate-400 shrink-0")
                if row.active:
                    ui.label("Active").classes(
                        f"{TEXT_MICRO} text-slate-300 border border-slate-700 "
                        f"px-{SPACE_HAIR} {RADIUS_PILL} shrink-0"
                    )
                ui.element("div").classes("grow")
                ui.button(
                    icon="edit", on_click=lambda _=None, n=row.name: open_editor(n)
                ).props("dense flat size=xs").classes("min-h-0 p-0 text-slate-400")
                delete = ui.button(
                    icon="delete", on_click=lambda _=None, n=row.name: _delete(n)
                ).props("dense flat size=xs").classes("min-h-0 p-0 text-slate-400")
                if row.active:
                    with delete:
                        ui.tooltip(
                            "Switch the weekly pick before deleting the active preset"
                        ).classes("max-w-xs")

            if not row.changes:
                ui.label("No changes from the base config").classes(
                    f"{TEXT_MICRO} text-slate-400"
                )
            for line in row.changes:
                ui.label(line).classes(f"{TEXT_MICRO} font-mono text-slate-300")

    async def _delete(name: str) -> None:
        failures = await state.delete_preset(repository, name)
        if failures:
            ui.notify(failures[0], type="warning")
            return
        ui.notify(f"Preset “{name}” deleted")
        refreshables.refresh("presets")

    return PresetsHandles(section=section)
