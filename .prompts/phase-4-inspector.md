# Phase 4 — day inspector, target curve, rejection capture

**Interactive session, in plan mode.** Three items, in this order. The third
is not a UI change and is the most valuable thing in `ui-redesign.md`.

Read `ui-redesign.md` phase 4. Phase 3 must have landed.

---

## 4.1 — The contextual day inspector

A panel that shows one day: its targets, its training sessions, its location,
its four slots. Opened by clicking a day's telemetry column. **Floats over the
canvas — never pushes it.** Pushing is the exact failure phase 2a removed.

**This is cheap, and `ui_today.py` is the proof.** Its `today_view` had exactly
one line deciding which day it showed, and everything beneath it —
`targets_for`, `totals_for`, `day_context`, `slot_id` — already takes a day
argument. Adding the day picker changed that one line and no plumbing
followed. The inspector is the same shape again: reuse `ui_state.day_context`
rather than assembling a second view of the same facts.

`day_context` is built **once per repaint**, not once per card, because the
per-meal training notes are only reachable through `planning_config()`, which
runs `apply_training_adjustments` over the whole week. Keep that property.

---

## 4.2 — Targets as a curve

Replace `ui_drawer.day_target_row`'s 21 spinboxes (7 days × kcal/protein/carbs)
with one editable week shape:

- a filled bar per day for the base target
- a second stacked segment for the training uplift
- a dashed ghost at the config value on any day carrying an override

Carb cycling is currently invisible in a form; as a shape it is the thing you
see first.

**Three existing rules must survive:**

- **Fat is derived, never typed.** `derive_fat_g` computes it from the other
  three. An input for it could only disagree with the number the planner uses.
- **`target_overrides` holds only what differs from the file**, per day. That
  is what lets a day follow config when config changes, and what makes the
  reset button work by cancellation rather than by re-creating an override.
- **An override wins over `week_plan.targets` in the telemetry denominator.**
  The point of editing a target before a run is seeing how far the current
  week sits from where you are about to aim it.

`day_target_row`'s build-once-and-mutate-in-place workaround exists because
refreshing a section containing a focused input steals the cursor. A drag
target has no cursor to steal, so that workaround should disappear with the
form rather than be carried into the new control.

---

## 4.3 — Derive the training burn

`estimated_burn_kcal` is hand-typed today. Nobody knows their kcal burn;
asking is asking the user to do the app's arithmetic. Derive it from session
type, duration and current weight — a MET-style estimate is enough, and the
weight is already available from the latest weigh-in.

**The trap: do not double-count.** `apply_training_adjustments` already folds
a session's burn into the day's target and records what it did in
`training_uplift`, which `hydrate_dynamic_targets` then *replays*. A derived
burn must feed that same single path, not become a second source of the same
calories. Read "Targets come from the body" in CLAUDE.md before writing any
of this — it documents exactly which parts of the uplift are replayed and
which are deliberately dropped, and why protein is not among them.

Keep the field editable. A derived default the user can override is the goal;
a computed value they cannot correct is worse than the text box.

**Then, optionally:** propose the schedule from Garmin. `GarminSyncService`
already syncs activity history, so a recurring weekly pattern is detectable,
and a confirmation beats a data-entry form. Treat this as a separate,
following change — it is a real feature, not a finishing touch on 4.3.

---

## 4.4 — Rejection capture

**The point of this phase.** Hitting regenerate on a meal card is the
strongest signal that a suggestion was wrong, and the app currently learns
nothing: the recipe is discarded and an identically-briefed call is made.
Favourites capture the positive signal; there is no negative one.

Add a small prompt at that moment — *too much prep · don't fancy it · had it
recently · wrong for this slot* — aggregate the answers, and send the result
in `build_generation_rules` beside `banned_ingredients` and the diet-style
principles.

**Design constraints:**

- **New storage, not an existing file.** This is a distinct signal from
  `future-ideas.md`'s 5b: `AdherenceEntry` logs whether a plan was *eaten*;
  this logs why a suggestion was *refused before it ever became the plan*.
  Two signals writing one key overwrite each other with no way to tell which
  won — the same reasoning that keeps `weigh_ins` and `daily_actuals` as
  separate upsert targets. Go through `repository.py`; invent no new storage
  path.
- **It is soft guidance, like `diet_styles` and `sourcing`** — prompt text,
  not a validator. A rejection is a preference, and a preference that hard-
  rejects a response costs a full 30s–3min retry. Something that must never
  appear belongs in `banned_ingredients`.
- **Do not block the regenerate.** The prompt appears alongside the retry,
  never in front of it. A user in a hurry must be able to ignore it, and an
  ignored prompt records nothing rather than recording a default.

**Maintainer decision — raise it, do not settle it:** whether the preference
list decays. A dislike honoured forever will starve the rotation the same way
"unused in the last N" starves the tail of a list (see `planner.next_choice`
on why it is strict LRU instead). A decay window is probably right; its length
is a product call.

---

## Acceptance

- The inspector opens from a telemetry column, shows the right day, and does
  not reflow the grid.
- `day_target_row` and its 21 spinboxes are gone; overrides still round-trip,
  still reset by cancellation, and still win in the telemetry denominator.
- A derived burn changes the day's target exactly once — verify against
  `training_uplift`, not by eye.
- A rejection is recorded, survives a reload, and reaches the prompt.
- `python -m unittest discover -s tests` passes, and `test_ui_state.py` gains
  cases for the override behaviour under the new control. If logic worth
  testing ends up in a widget module, move it to `ui_state.py` — that is the
  standing rule, not a preference.

## Finish by

Updating CLAUDE.md: the drawer-targets section describes a control that no
longer exists, and rejection capture needs its own subsection under
Architecture — what it stores, why it is separate from 5b, and why it is soft
guidance. Add the decay decision to `future-ideas.md` if it is still open.

Publish a release in github. 