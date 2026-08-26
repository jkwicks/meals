# Phase 2b — real grid rows and a meal-type gutter

**Interactive session. Not queue-safe** — this changes how all 28 cards are
placed and its acceptance is visual.

Read `ui-redesign.md` phase 2b. Phase 2a (shared scroll container, overlaying
drawer) must have landed first.

## The problem, stated precisely

`ui_cards.canvas` builds `grid grid-cols-8 gap-2 w-full items-start`, and each
day is a `flex flex-col` **inside** one of those columns, holding its four
meal cards.

So there are no shared grid rows. Every column is laid out independently, and
`items-start` means a column with a taller card — a long recipe title, a
leftover chain's "↩ from Mon dinner" line, a prep badge — simply grows, and
its dinner card stops sitting level with the neighbouring day's. **The rows
align by luck, not by structure.** Look at a generated week with a few
leftover links before starting; the drift is visible.

That is also why a sticky meal-type gutter is impossible today: there is no
row for a gutter label to be sticky *against*.

## What to do

Restructure to a real two-dimensional grid:

- Columns: gutter, prep, then one per day in `state.days` (9 with the shipped
  config, but derive it — `state.days` is config-driven and `week_start_day`
  rotates it).
- Rows: a day-header row, then one row per entry in `state.meal_types` —
  also config-driven, not the four built-ins.
- The prep column spans every meal row. `prep_day_column` is currently one
  tall element in a column of its own; it needs to keep that behaviour under
  the new placement.

Then add the sticky gutter carrying the meal-type names, and **drop the
per-card meal-type label** — the gutter now says it, and removing it takes
noise out of 28 cards at once.

## Decisions to make deliberately, not by default

1. **Explicit placement or `grid-auto-flow: column`.** Auto-flow keeps the
   build loop close to what it is now; explicit `grid-row`/`grid-column`
   survives a config that adds a fifth meal type more obviously. Pick one and
   say why in the comment.
2. **What the gutter shows at the day-header row.** It is the cell where the
   meal-type column meets the day-name row, and it has nothing to say. Empty
   is a fine answer; `ui_telemetry.context_pipeline` already does exactly this
   with a bare `ui.element("div")` spacer, and its comment explains why.
3. **Whether the telemetry header also gets a gutter column.** It must stay
   aligned with the canvas, and the canvas just gained a column. This is the
   thing most likely to break, and phase 2a's shared scroll container is what
   makes it checkable.

## Scope fence

- `src/ui_cards.py` and `src/ui_telemetry.py`, plus `ui_theme.py` if the
  gutter needs a constant.
- Do **not** change what a card contains beyond removing the meal-type label.
- Do **not** touch `ui_state.py`'s logic. `slot_views()` already returns a
  flat dict keyed by `slot_id(day, meal_type)`, which is exactly the shape a
  two-dimensional grid wants — no view-model change should be needed. If you
  find one is, stop and say so.

## Acceptance

1. Every day's dinner card sits on one baseline, whatever the card heights.
   Verify against a week that has leftover links and at least one long title.
2. The gutter stays fixed while the grid scrolls horizontally under it.
3. The prep column still spans the full height of the meal rows.
4. Telemetry columns still align with canvas day columns, at rest and scrolled.
5. A config with a different `meal_types` list still renders correctly — check
   by reasoning through the loop, not by editing shipped config.
6. `python -m unittest discover -s tests` passes.

## Finish by

Updating CLAUDE.md's NiceGUI front-end section. The current text says "a
7-column x 4-card canvas" and describes `grid-cols-8`; both are now wrong.
Replace them, and record *why* the restructure happened — that the previous
layout aligned rows by coincidence — because that is the reasoning a future
reader would otherwise undo by "simplifying" back to per-column flex stacks.

Publish a release in github. 