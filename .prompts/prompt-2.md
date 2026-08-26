# Phase 2a — overflow container and an overlaying drawer

Read `ui-redesign.md` (project root), phase 2a, for the rationale. Do **only**
2a. Phase 2b — real grid rows and the meal-type gutter — is a separate,
interactive phase; do not start it, and do not add a gutter here.

## The problem

The left drawer is `top_corner=True`, which makes Quasar inset the fixed
header by the drawer's width. That keeps the header's `grid-cols-8` telemetry
columns aligned with the canvas's eight columns — but it means the drawer
*pushes* the page, so 320px of drawer competes with an 8-column grid and the
document body scrolls sideways.

## What to do

Give the header's telemetry rows and the canvas **one shared horizontal
scroll container** with a sensible `min-width`, so the grid scrolls inside
itself rather than widening the page. Then switch the drawer from pushing to
overlaying.

**Read the `top_corner=True` comment in `ui_drawer.py` before changing it.**
It documents exactly why the inset exists. Removing the push removes the
reason for the inset — but the header and canvas must then share one scroll
container, or they will disagree about x-offset the moment the grid scrolls
horizontally. **That shared container is the real work in this phase**, not
the drawer prop.

The three regions that must scroll together, all currently `grid-cols-8`:

- `ui_telemetry.context_pipeline`
- `ui_telemetry.telemetry`
- `ui_cards.canvas`

They are built in different modules and placed in different parents (the
first two inside `ui.header`, the third inside a `ui.tab_panel`), which is
the constraint to solve. Decide where the container lives and say why in the
code comment — `ui_app.py`'s page shell is the likely home, since layout is
already its job.

## Scope fence

- `src/ui_cards.py` (`canvas` only), `src/ui_telemetry.py`, `src/ui_drawer.py`
  (the `ui.left_drawer` call only), `src/ui_app.py` (page CSS and layout).
- Do **not** change the drawer's *contents* — only how it sits over the page.
- Do **not** restructure the canvas grid, add a meal-type gutter, or remove
  the per-card meal-type label. All of that is 2b.
- Do **not** touch `ui_state.py`, `planner.py`, `week.py`, or `repository.py`.

## Acceptance

1. The document body never scrolls horizontally, at any viewport width.
2. The grid scrolls inside its own container instead.
3. The telemetry columns stay aligned with the canvas columns **while
   scrolled** — not just at rest. This is the check most likely to fail.
4. Opening and closing the drawer does not reflow or resize the grid.
5. `python -m unittest discover -s tests` passes.
6. The app starts and serves: `./scripts/server.sh start`, then
   `./scripts/server.sh status`. Stop it again when done.

**Acceptance items 1–4 are visual and you cannot fully self-verify them.** Do
what you can — read the computed classes back, check the DOM structure — and
then **state plainly in your report which of the four you confirmed and which
you could not.** Do not report a visual criterion as met on the strength of
having written the CSS that should meet it.

## Finish by

Updating CLAUDE.md's NiceGUI front-end section: the `top_corner` note now
describes history rather than current behaviour, and the shared scroll
container is a new structural fact a future reader needs. Rewrite that
passage rather than appending to it — leaving a stale explanation next to a
correction is worse than either alone.

Report: files changed, where the scroll container ended up and why, which
acceptance items you confirmed versus could not, and anything you found that
belongs in 2b.

Publish a release in github. 