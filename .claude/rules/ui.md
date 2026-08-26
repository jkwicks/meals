---
paths: ["ui_*.py"]
---

# The NiceGUI front end

`ui_app.py` is a page shell; every other `ui_*.py` is one concern exposing a
`build_*(ctx)` factory. CLAUDE.md's "NiceGUI front end" section is the
architecture. This file is the presentation contract and the traps — the
things a cold session will otherwise get wrong twice.

## Where a change goes

- **`ui_theme.py`** — presentation constants and pure render helpers. Nothing
  here may import or read `PlannerState`. Every function takes its inputs as
  arguments.
- **`ui_state.py`** — the view model, and the *only* UI module with tests.
  Logic worth testing belongs here or in a pure helper, never in a widget
  module. If you find yourself wanting to test a `build_*` function, that is
  the signal to move the logic down, not to grow a NiceGUI test harness.
- **everything else** — widget construction. Untested on purpose: asserting
  on element trees pins the layout rather than the behaviour.

## The type scale — four sizes

Established by phase 1 of `ui-redesign.md`. Until that phase lands you will
still see legacy literals; **do not add new ones**, and convert any you touch.

| constant | value | for |
|---|---|---|
| `TEXT_MICRO` | `text-[10px]` | data figures, chip and badge labels, link lines, captions |
| `TEXT_BODY` | `text-xs` (12px) | the default — labels, inputs, card titles, list rows |
| `TEXT_HEAD` | `text-sm` (14px) | section headings, day names, dialog section titles |
| `TEXT_DISPLAY` | `text-lg` (18px) | dialog titles, a recipe name in the detail view |

Nine sizes crammed into a 6-pixel band (`text-[8px]` through `text-base`,
with 56 uses of `text-[10px]` and 35 of `text-[9px]`) is what this replaces.
That was noise, not hierarchy — no two of those sizes were distinguishable at
a glance. **Weight and colour carry the rest of the hierarchy**, not a fifth
size.

## Spacing — five steps, each with a job

| constant | value | for |
|---|---|---|
| `SPACE_HAIR` | `0.5` (2px) | inside a chip or badge; icon to its own label |
| `SPACE_TIGHT` | `1` (4px) | between rows inside one card |
| `SPACE_BASE` | `2` (8px) | between cards, between form fields |
| `SPACE_SECTION` | `3` (12px) | between sections in a panel or dialog |
| `SPACE_PAGE` | `4` (16px) | dialog padding, page gutters |

**Space siblings with the parent's `gap`, not per-element margins.** Margins
collapse and double in ways a gap does not, and they put the spacing decision
on the child rather than on the layout that owns it. Write new code this way.

The scattered `mt-1`/`mt-2`/`mb-1`/`mt-0.5` still in the current code is
legacy, and is **deliberately not phase 1's job** — converting a margin to a
parent gap moves layout, and phase 1's premise is that nothing moves. Phase 2
handles it, while it is restructuring containers anyway.

`1.5` is not a step. Where it appears in legacy code it resolves **down** to
`SPACE_TIGHT`: it is 6px, exactly halfway between the `1` and `2` steps, so
"nearest" is undefined, and the dense card interiors where it appears cannot
absorb growth from both a larger type scale and a wider gap at once.

## Radius — three values

`rounded` (cards, boxes, inputs) · `rounded-lg` (dialogs, panels) ·
`rounded-full` (bars, dots, pills). `rounded-md` and `rounded-xl` are not
used; both currently appear and are legacy.

## Colour: structural, semantic and categorical are three different things

Three roles, and they must not borrow each other's hues:

- **Structural** — `STATUS_STYLES` (emerald cook / sky leftover / slate skip /
  rose not-generated) and `PREP_COLUMN_ACCENT` (indigo). These say *what a
  thing is*. A new UI element that borrows "cook" green reads as a fifth slot
  status.
- **Semantic** — `BAND_COLOURS` (on / near / off target). These say *how it is
  going*.
- **Categorical** — `MACRO_TINTS` (which macro). These say *which of several*.

### Known collisions — do not paper over these

Recorded rather than silently worked around, because resolving them is a
design decision for phase 3 of `ui-redesign.md`, when the surfaces using them
are being rebuilt anyway:

- **Amber means five things**: near-target (`BAND_COLOURS`), carbs
  (`MACRO_TINTS`), training (`TRAINING_ACCENT`), a target override (the
  telemetry marker), and fridge storage (`PREP_BADGE_STYLES`).
- **Violet means two**: fat (`MACRO_TINTS`) and location (`LOCATION_ACCENT`).

Adding a sixth meaning to amber is the specific thing not to do. If a new
element needs an accent, check this list first.

**Icon, not colour, distinguishes members of a set.** `TRAINING_TYPE_ICONS`
is the precedent and the reasoning is in its comment: eight hues are already
spoken for, so seven new ones would collide with an existing meaning long
before they read as a scale. Match exactly first, then longest prefix, and
never raise on an unknown key.

## NiceGUI traps — every one of these cost a debugging session

- **`props()` silently drops an unquoted value containing brackets.** A
  Tailwind class in a Quasar prop must be quoted:
  `props("header-class='text-[11px]'")`. Unquoted, it never reaches the
  component at all and there is no error.
- **`bind_value`'s own sync back to `state` runs *after* a widget's
  `on_change` handler, not before.** A handler that both sets the field and
  refreshes something must set it explicitly first rather than trusting the
  binding to have already landed it — `ui_settings.py`'s week-start select
  does exactly this before calling `refreshables.refresh("plan")`, or the
  repaint would read the old week order.
- **Refreshing a section that owns the focused input steals the cursor.**
  `day_target_row` is built once and mutated in place for exactly this
  reason, and refreshes only the narrow `telemetry` topic rather than
  `targets`. If a new editable section repaints per keystroke, this is the
  pattern to copy — or better, pick a narrower refresh topic.
- **Quasar's `.flex` sets `flex-wrap: wrap` and Tailwind's `flex-row` does not
  undo it.** Any icon-plus-text row needs `flex-nowrap`, and the text label
  needs `min-w-0` — a flex item's default `min-width: auto` will not shrink
  past its longest word. A step long enough to fill its row otherwise wraps
  *below* its number and runs back underneath it.
- **`ui.add_css` from inside a `@ui.refreshable` stacks another copy into the
  head on every repaint.** Emit page CSS once, from the page function.
- **Never call `repository.run_sync()` here.** NiceGUI page handlers run *on*
  the event loop, so `await` the repository directly. `run_sync` detects the
  running loop and hands the coroutine to a scratch thread — pure overhead,
  and it serialises page loads behind a thread pool.
- **Anything long must not block the handler.** A bare blocking call freezes
  every connected browser. `generate_week_plan` keeps the loop free by
  dispatching each blocking API call to a worker thread; anything new that
  takes seconds must do the same.

## Refresh topics

A call site says *what changed* (`refreshables.refresh("plan")`), never which
widgets depend on it. Topics are registered once, in `planner_page()`, after
every module is built. Before adding a topic, check whether an existing one
already covers the change — and before adding a section to `"plan"`, check it
does not own a focused input.

## State lives per client

`PlannerState` is created *inside* the page function. Module-level state would
be shared by every browser tab connected to the server. Generating is the only
thing in this UI that writes to disk; grid edits live in the client's state
until discarded.
