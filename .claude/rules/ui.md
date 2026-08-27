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
- **The same wrap, in a `flex-col`, renders content outside its own box.**
  `flex-col` does not undo `flex-wrap: wrap` either, and a wrapping *column*
  container whose content outgrows its height does not overflow — it starts a
  second column beside the first, so any `overflow-y: auto` on it never
  fires. `ui_cards.prep_day_column` is the precedent: measured at 1440px,
  the prep column's last timeline phase laid itself out at x=423, inside the
  Monday day column and 143px clear of its own 135px track, and the cell's
  `absolute` positioning made that invisible rather than merely wrong. Any
  `flex flex-col` that is expected to scroll needs `flex-nowrap`.
- **A flex or grid item's default `min-width`/`min-height` is `auto`, which
  refuses to shrink below its content's natural size — including a wrapper
  that's just passing width down to an `overflow-x: auto` grid inside it.**
  Several Quasar containers (`.q-tab-panel` among them) are themselves flex,
  so a plain `div` wrapper with no `min-w-0` grows to its content's
  min-content width regardless of how little room its parent actually has —
  the wrapper's own `overflow-x: auto` then never overflows, and a
  *different*, outer ancestor ends up scrolling instead. `ui_plan.panel()`
  hit exactly this (phase 2b of `ui-redesign.md`): its wrapper had no
  `min-w-0`, so `week_grid_scroll()`'s canvas never actually needed to
  scroll — a Quasar container two levels up silently scrolled the whole
  panel instead, which would have left the meal-type gutter's `sticky`
  positioning nothing to stick against. `w-full min-w-0` on the wrapper is
  the fix — same shape as `meal_card`'s own `w-full`/`overflow-hidden` fix
  for a sibling sizing trap, documented in CLAUDE.md's phase-1 writeup.
- **A stretched child of a *wrapping* column flex container sizes to the
  widest sibling's max-content, not to the container's own width.** Quasar's
  `.flex` sets `flex-wrap: wrap` (see the trap above), and in a column
  container that also changes what `align-items: stretch` stretches to: the
  flex *line's* cross size, which is the widest item's hypothetical width.
  `ui.header()` is such a container, so a `w-auto` wrapper around
  `WEEK_GRID_COLS` measured 1024px — all nine columns at their 110px floor —
  inside a 1000px viewport, with nothing left to overflow while the canvas
  below scrolled normally. An explicit percentage width
  (`width: calc(100% - <inset>)`, `ui_theme.WEEK_GRID_HEADER_INSET_STYLE`)
  resolves against the container's content box and is immune to it. Reach
  for a definite width, not stretch, for anything that has to match a box
  elsewhere on the page.
- **A grid item spanning several auto rows sizes those rows to its own
  content, and `overflow-y: auto` does not stop it.** Auto rows size to
  their items' *max-content*; `overflow` only zeroes an item's automatic
  *minimum* size, which is a different quantity, so reaching for it here
  measures as no change whatsoever. To make a tall spanning item stop
  driving row heights, take its content out of flow — the cell becomes
  `relative self-stretch` and empty, the content an `absolute inset-0
  overflow-y-auto` child. `ui_cards.prep_day_column` is the precedent, and
  the symptom there was ~640px of dead space appearing between one day's
  meal cards, which looked like the grid restructure's fault and wasn't.
  `self-stretch` is required alongside it wherever the grid sets
  `items-start`, or the cell sits at zero height and `inset-0` fills
  nothing.
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

## Targets: read the resolved number, never the file

`config["weekly_schedule"][day]["calories"]` and `["protein_g"]` are **inert**
while that macro's `target_modes` entry is `auto` — `hydrate_dynamic_targets`
replaces them with the engine's figure, and the shipped config states 1000
kcal on a Thursday every run plans at 1722. A widget reading the file is
therefore displaying a number nothing plans from, which is exactly the bug
that had the telemetry header measuring the week against targets no run had
ever used.

- `state.planned_targets(day)` — what the next run will aim at.
- `state.baseline_targets(day)` — what it would aim at with that day's own
  overrides suppressed. This is what an override is a *difference from*, so
  it is what a signed delta, a clear-on-match, or a ghost line measures
  against. It costs a `planning_config()` rebuild, so compute it only for the
  days that need one.
- `state.targets_for(day)` — the telemetry denominator, which is the *stored
  plan's* target unless `target_is_staged(day)`. Do not branch on
  `has_training(day)` for this: a workout already in the config is not a
  staged change, and branching on it put six days of one row on a live
  preview and the seventh on the plan.

The Settings destination's Daily Targets panel is the only place that writes
to `config/` (`PlannerState.set_target_mode` → `repository.save_config_keys`).
Everything else here stays session-only.

## State lives per client

`PlannerState` is created *inside* the page function. Module-level state would
be shared by every browser tab connected to the server. A generation is the
only thing that calls the model, but it is no longer the only thing that
writes `week_plan.json` — `ui_generation.save_grid` persists a deterministic
grid edit (a swap, a leftover link, a skip estimate) straight to disk with no
model call, via the staged bar's "Save changes" button (`state.edited` gates
whether it's shown). Grid edits still live only in the client's state until
one of Save/Generate/Discard acts on them.
