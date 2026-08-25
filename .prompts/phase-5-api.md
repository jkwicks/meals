# Phase 5 — extract the API boundary

**Interactive session, in plan mode.** This is the phase that decides whether
a different front end is ever cheap, so the shape matters more than the speed.

Read `ui-redesign.md` phase 5. Phases 1–4 should have landed, though this one
is genuinely independent of them.

## What this is, and what it is not

**Is:** mount FastAPI routes in the existing NiceGUI process, exposing what a
front end needs to read and write, so the UI stops being the only thing that
can reach the planner.

**Is not:** a React rewrite. Do not add a JavaScript build step, a `package.json`,
or a second server. The recommendation in `ui-redesign.md` is explicit —
extract the boundary now, adopt React only if this ships to other people, and
that decision has not been made.

**Is not:** moving business logic. Everything the routes expose already exists
in `planner.py`, `week.py`, `nutrition_engine.py` and `repository.py`. A route
that computes something is a route that will disagree with the UI.

## Why this is tractable

NiceGUI already runs on FastAPI and Uvicorn, so routes mount in the same
process — no new deployment. `PlanRepository`'s own docstring anticipates a
different backend ("point the app at a backend by constructing a different
subclass"), and every method is already `async` for exactly this reason:
CLAUDE.md says the interface was shaped for a future backend receiving
asynchronous pushes, so business logic awaits its storage today rather than
being rewritten around an `await` boundary later. That bet is what this phase
collects on.

## The hard part

`PlannerState` is ~1,300 lines of view model living as a **per-client Python
object** that calls `split_targets`, `portions_for` and `planning_config()`
synchronously. None of it is reachable from a browser today.

Do not try to expose `PlannerState` itself. It holds unsaved edit state for
one connected client, which is a session concept, not an API one. Expose the
**pure, stateless** layer beneath it — the repository, and the functions in
`week` and `nutrition_engine` that already take their inputs as arguments —
and let a future client own its own edit state the way `PlannerState` does.

Where a route would need something only `PlannerState` can currently produce,
that is a finding: it means view-model logic is doing work that belongs one
layer down. **Record it, do not fix it opportunistically.**

## Start read-only

`future-ideas.md`'s 5c (trend charts) is the natural first consumer — all
read, no write, so it exercises the boundary without risking the generation
path. **But 5c is deliberately blocked on data**: three weigh-ins and one
`daily_actuals` row as of 2026-08-26, and `calculate_adaptive_tdee` returns
`None` below two weigh-ins spanning `MIN_TREND_SPAN_DAYS`.

So build the read routes 5c would use, and verify them against the real files
even though the series is short. Do not build the charts — that remains 5c's,
and remains blocked.

Suggested first surface, all GET:

- the current and next week plans
- the recipe catalog, with filters
- history
- biometrics, and the derived targets/TDEE basis including `tdee_source`

Writes come later and want their own thinking: generation is a long-running
job that currently reports progress over NiceGUI's socket, and turning that
into an HTTP-shaped operation is a design question, not a translation.

## Constraints

- **Generated types, not hand-written ones.** If TypeScript types are ever
  wanted, they come from FastAPI's OpenAPI schema. Two hand-maintained copies
  of `Recipe` is how the UI and the planner start disagreeing about what a
  portion is.
- **Do not break `python src/planner.py`.** The CLI is a first-class front end
  and its `run_sync` bridge must keep working.
- **Do not add auth in this phase** unless the app is about to be exposed
  beyond localhost — but note in your plan where it would go, because the
  answer shapes whether routes are per-user from the start.
- Flat-sibling imports still apply; this is not a package.

## Acceptance

- Routes serve real data from the real files, in the same process as the UI.
- The NiceGUI app is unchanged in behaviour and still passes
  `python -m unittest discover -s tests`.
- The CLI still runs a week end to end.
- New tests cover the routes at the same seam everything else is tested at —
  no network, no model, no clock. The whole suite stays under a tenth of a
  second.

## Finish by

Adding an Architecture section to CLAUDE.md for the API boundary: what it
exposes, what it deliberately does not, and why `PlannerState` is not on it.
Record any view-model logic you found that belongs a layer down — that list is
the input to whatever front-end decision comes next, and it is worth more than
the routes themselves.
