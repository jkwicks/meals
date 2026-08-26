# Phase 3 — the rail, five destinations, the staged-changes bar

**Interactive session, in plan mode.** Not queue-safe: this carries a dozen
judgment calls an hour, and three of them (below) are the maintainer's, not
yours. Propose before you build.

Read `ui-redesign.md` phases "The target shape" and 3. Phases 1, 2a and 2b
must have landed.

## What this phase is

Replace the left drawer with five destinations on a slim icon rail, each
owning the full canvas:

| Destination | Holds | Exists today as |
|---|---|---|
| Plan | the week grid, the generation flow | `ui_cards.canvas` + the Week tab |
| Today | one day's cards, context strip, day picker | `ui_today.py`, already a tab |
| Library | catalog, filters, all import paths | `ui_catalog_browser.py`, in a dialog |
| Insights | trends | nothing — see below |
| Settings | week start, shopping days, model, integrations | the drawer's "Global Controls" |

Plus the **staged-changes bar**: one persistent strip reading *"3 pending
changes — Wed +200 kcal, Sat gym added, 2 pantry items · Review · Generate
week"*. It replaces three separate "Applies to the next generation only"
disclaimers, the amber telemetry override dot, and the "edited — not saved"
chip.

**Insights is a stub in this phase.** Its content is `future-ideas.md`'s 5c,
which is deliberately blocked on data — three weigh-ins and one `daily_actuals`
row as of 2026-08-26. Build the destination and an honest empty state that
says what is missing and roughly when it will be worth showing. Do not build
charts.

## The architectural trap — settle this first

`PlannerState` is created **inside** `planner_page()` and lives per client. Grid
edits (leftover links, unlinks, skip estimates, target overrides, the pantry
list, the training schedule) live in it and are **not written to disk** until a
generation runs.

So: if destinations become separate `@ui.page` routes, navigating from Plan to
Library and back constructs a **new `PlannerState`** and silently discards
every unsaved edit. That is a data-loss bug, and it is the most likely way this
phase goes wrong.

Two viable shapes. Decide deliberately and record the reasoning:

- **Tab-panel style** (what the app does today for Week/Today): everything is
  built once, hidden panels stay mounted, state survives navigation for free.
  Cost is build time and memory for destinations the user may never open.
- **Routed, with state lifted** out of the page function into a per-client
  store keyed by connection. More work, and it must not become module-level
  state — CLAUDE.md is explicit that module-level state would be shared by
  every browser tab.

The tab-panel shape is the smaller change and the one that cannot lose an
edit. Prefer it unless you find a concrete reason not to, and say what that
reason was.

## Three decisions that belong to the maintainer

Raise these in your plan; do not resolve them yourself.

1. **Is "people per meal" a setting or a per-run option?** It varies week to
   week, which argues for the generation-options popup beside the cuisine and
   prep toggles. But `PlannerState.spec` deliberately ignores it once a week
   exists (see `_shape()`), and `generation_spec()` reapplies it — so moving it
   changes which of those two paths is the honest one.
2. **What happens to the three unconnected `PIPELINE_STAGES`?** Wire them,
   remove them, or move them into Settings as an integrations status list.
   Leaving 21 permanently dashed chips above the telemetry is the one option
   that is clearly wrong.
3. **What is "Reload from disk" called now?** In a UI with a staged-changes
   bar it is "discard pending changes", and the bar is where it belongs — but
   that is a wording and placement call worth making explicitly.

## Constraints

- **Keep the `build_*(ctx)` factory pattern.** Each destination is a module
  returning a dataclass of refreshables, registered into `Refreshables` in
  `planner_page()`. Build order still matters where one needs another's
  handles — the comment in `planner_page()` documents the current dependencies
  and must be updated, not deleted.
- **Revisit the refresh topics.** Several exist only because the drawer had a
  focus-stealing problem (`targets` vs `telemetry` — see `day_target_row`).
  Some may collapse once the drawer is gone. Do not collapse `plan`; it is the
  broad one and several call sites depend on it.
- **`ui_drawer.py` should end this phase deleted or reduced to almost nothing.**
  If it survives with contents, the phase did not happen.
- The **header** stays shared chrome above all destinations — week selector,
  banner, telemetry — or make a deliberate case for moving it. Today's tabs
  already treat it that way.

## Acceptance

- Five destinations reachable from the rail; each renders its own content.
- Navigating away from Plan with unsaved edits and back preserves them.
  **Test this explicitly** — it is the trap above.
- The staged-changes bar shows a real count and a real summary, and hides when
  there is nothing pending.
- Generation still works end to end and still writes before adopting.
- `python -m unittest discover -s tests` passes. `test_ui_state.py` is the one
  that matters here; if it needs changing, that is a signal the view model
  moved when it should not have.

## Finish by

Rewriting CLAUDE.md's "NiceGUI front end" module-layout table and its
description of the three regions (left drawer / header / canvas / right
drawer) — that whole passage becomes wrong in this phase. Record why
destinations replaced the drawer: five kinds of work in one 320px column, with
nothing in common but the word "global".

Publish a release in github. 
