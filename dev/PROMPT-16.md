# PROMPT-16 — Morning readiness as read-only context (Arm E, read-only slice)

**Not queue-safe** (in `dev/`; `claude-queue.sh` globs `.prompts/prompt-*.md`).
It changes the Today/Adaptive Workout surface and adds a sixth Insights
readout; both have visual acceptance.

**Delivery tier: the first slice of `design-06` §8's larger feedback
controller — Phase 6 of `dev/task-queue-modified.md`.** Depends on **PROMPT-15
(shipped)** for the controlled object (actual exercises, doses, progression
proposals) this layers read-only context onto. Hevy (`PROMPT-4`, Task 2.3) is
**not** a gate for this slice specifically — HRV/sleep already sync
independent of Hevy — but see Scope: every other row of §8's table (week
e1RM/hard-sets, block fatigue matrix, periodic functional assessments) stays
out of this prompt because Hevy has not landed and no separately tested
control rule exists yet for any of them.

Cold session. Read `design-06-exercise-planning.md` §8 again with this
prompt open beside it, `docs/fitness-model.md` §"Wearable Physiological
Telemetry: Autonomic and Recovery Analytics", and CLAUDE.md's "Biometric
sync" and "Insights: five readouts, each gated on its own precondition"
sections. `docs/periodization-engine.md` §§1–2 (the fatigue-scoring matrix
and deload triggers) is background only — read it to know what you are
*not* building yet, not as a spec for this prompt. Load the `ui-work` skill
before touching `ui_today.py` or `ui_insights.py`.

Before writing anything, AST/Tree-sitter-inspect the on-disk shapes this
prompt sits on top of:

- `src/integrations/sync_service.py`'s `GarminSyncService.fetch_readiness` —
  the exact `readiness_log` row shape it writes: `date`, `sleep_score`,
  `sleep_hours`, `hrv_ms`, `readiness_label`, `source`. **There is no resting
  heart rate field.** `design-06` §8 names "HRV/RHR/sleep"; RHR is simply not
  fetched today. That is a sync-side gap for a later prompt, not something to
  patch here — show exactly the four fields that exist.
- `src/ui_state.py`'s `adaptive_tdee_view(biometrics, basis)` — the pattern
  to mirror for a one-date snapshot reader: pure, takes the `biometrics`
  dict, returns a small typed view with explicit no-data states rather than
  guessing. Also `PlannerState.day_date_iso(day)` — resolves a weekday name
  in the loaded week to the real calendar date a `readiness_log` lookup needs
  (a training decision is about one specific night, the same reason
  `logged_intake_for` refuses every day but the one asked about).
- `src/ui_state.py`'s Insights machinery: `InsightPanel`, `WeightTrendPanel`,
  `INSIGHT_EMPTY`/`INSIGHT_SPARSE`/`INSIGHT_THIN`/`INSIGHT_READY`,
  `INSIGHT_MIN_POINTS`/`INSIGHT_THIN_POINTS`, and `weight_trend_panel`/
  `intake_panel`/`macro_accuracy_panel`/`adherence_panel` — the pattern to
  mirror for a sixth trend readout.
- `src/ui_insights.py`'s `build_insights(ctx, biometrics)` — the
  `@ui.refreshable` panel function, `_section(title, view)`, and a
  specialized renderer per readout (`_weight_options`, `_adherence_tiles`) —
  where the sixth section and its chart/renderer go.
- `src/ui_today.py`'s `session_chip`, `workout_action`, `WorkoutHandles`,
  `is_gym_session`, `workout_session_view` — the training strip's existing
  per-session affordances, and where a read-only readiness snapshot for that
  session's day attaches without competing with the `fitness_center` button.
- `src/ui_theme.py`'s `PIPELINE_STAGES` `"readiness"` entry ("Subjective
  readiness check-in — not built yet.") — **a different, still-unbuilt
  feature** (a manual self-report), not the objective Garmin data this
  prompt surfaces. Do not touch its `connected` flag or description.

## The requirement

`design-06` §8 names the first row of the larger feedback controller sitting
on top of `PROMPT-15`'s controlled object:

| Horizon | Signal | Later action |
|---|---|---|
| morning | HRV/RHR/sleep | cap RPE, reduce sets, replace HIIT |

Nothing in the app currently surfaces the sleep/HRV data Garmin already syncs
anywhere near a workout — it is fetched and stored (`readiness_log`) and
shown only as a raw sync-status line in Settings. This prompt's entire job is
to make that already-synced data visible, in context, wherever a human is
about to make a training decision — and nothing more. **No control rule
exists yet**: no RPE cap, no set reduction, no HIIT substitution, because
none has been separately specified and tested, which both `design-06` §8 and
this task's own text require before any such rule may act automatically.

## Scope

This prompt owns:

- A pure, dateable readiness-snapshot reader over `readiness_log`, reporting
  sleep score, sleep hours, HRV, and Garmin's own `readiness_label` for one
  calendar date, with an honest "no reading that night" state distinct from
  "a reading with some fields missing" — never a guessed, zeroed, or
  interpolated value.
- Wiring that snapshot into Today/Adaptive Workout beside a gym session, so a
  human sees last night's numbers next to the decision they're about to
  make.
- A sixth Insights readout — a readiness trend panel gated through the same
  `INSIGHT_EMPTY`/`SPARSE`/`THIN`/`READY` machinery the other five already
  use — so a run of poor nights is visible over time, not only on the day.

This prompt does **not** own, and must not touch:

- Any automatic adjustment. §8's "later action" column for the morning row
  (cap RPE, reduce sets, replace HIIT) is out of scope by name — this prompt
  is the signal, not the controller.
- The HRV rolling-baseline/Smallest-Worthwhile-Change banding, the
  integrated fatigue scoring matrix, or any deload trigger from
  `docs/periodization-engine.md` §§1–2.
- Resting heart rate. Fetching it is separate sync work this prompt does not
  do.
- The week/block/periodic rows of §8's table (e1RM, hard sets, fatigue
  matrix, functional assessments) — gated on Task 2.3 (Hevy, not landed) and
  on control-rule prompts that do not exist yet.
- `ui_theme.PIPELINE_STAGES`'s `"readiness"` entry (see above).

## What to do

### 1. A pure readiness-snapshot reader

In `src/ui_state.py`, beside `adaptive_tdee_view`, add a pure function that
reads `biometrics.get("readiness_log")` for one ISO date and returns a small
typed view: sleep score, sleep hours, HRV ms, Garmin's label, and an explicit
state distinguishing "no row for this date" from "a row present but a
particular field absent" (the same distinction the sync layer already draws
when it decides whether a night has anything worth tagging with a
`source`). No numeric interpretation, banding, or threshold of any kind —
pass Garmin's own figures and label straight through, unmodified.

### 2. Wire it into Today/Adaptive Workout

In `src/ui_today.py`, render the snapshot for a gym session's resolved
calendar date (`PlannerState.day_date_iso`) as plain read-only text/chips,
placed so it reads as context for the session rather than competing with
`workout_action`'s `fitness_center` button — the workout detail dialog
`workout.open` shows is the natural home if the training strip itself is
already tight. No colour encodes "good" vs "bad" (the `ui-work` skill's
no-new-hue rule — `readiness_label` is already a label, not a colour to
invent one for). Nothing here writes anything or gates any other action; a
session with no reading for its date renders nothing rather than a
placeholder implying one was expected.

### 3. An Insights readiness trend panel

In `src/ui_state.py`, add a trend view-model function following
`weight_trend_panel`'s shape: the shared `InsightPanel` state via
`INSIGHT_EMPTY`/`INSIGHT_SPARSE`/`INSIGHT_THIN`/`INSIGHT_READY`
(`INSIGHT_MIN_POINTS`/`INSIGHT_THIN_POINTS`), over the window of
`readiness_log` rows that carry a sleep score and/or HRV. In
`src/ui_insights.py`, add a sixth `_section` to `build_insights`'s panel,
with its own chart/renderer alongside `_weight_options`/`_intake_options`/
`_macro_options` — sleep score (0–100) and HRV (ms) are different units, so
this is two series or two small charts, never one shared axis (the same
"a chart may not claim more than the data supports" rule the macro-accuracy
percentage axis already follows). No dashed target line — there is no target
here, only a record.

## Acceptance

- `readiness_log` absent or empty ⇒ the Today snapshot renders nothing and
  the Insights panel is `INSIGHT_EMPTY`; everything else is byte-identical to
  before this prompt (no config/schema change, no new store).
- A date whose Garmin row is missing one field (HRV still baselining, say)
  shows exactly what's present — never a zero or an inferred value.
- Nothing written by this prompt is read by `apply_training_adjustments`,
  `hydrate_dynamic_targets`, `generate_workout_week`, `propose_progression`,
  or any other planning/generation/progression path — grep confirms this
  stays view-model/UI only.
- No RPE, set count, exercise, or modality choice changes because of this
  data.
- No new colour anywhere in this prompt's surfaces; `readiness_label` renders
  as text.
- `ui_theme.PIPELINE_STAGES`'s `"readiness"` entry is untouched.
- Full suite passes. Visually verify Today's training strip/workout dialog
  and the new Insights panel at 1280px and 1440px.

New assertions belong in `tests/test_ui_state.py`, mirroring the existing
`adaptive_tdee_view`/`InsightPanel` test shapes: no data, partial data, a
populated snapshot, and the trend panel's four gate states.

## Do not

- Compute a 7-day/28-day rolling HRV baseline, a coefficient of variation, or
  any Smallest-Worthwhile-Change band from `docs/periodization-engine.md`.
- Compute a composite fatigue score or any weighted-trigger matrix.
- Cap RPE, reduce prescribed sets, or substitute an exercise/modality
  automatically, however "obviously" the data suggests it.
- Fetch, derive, or fabricate resting heart rate.
- Touch `data/workout_plans.json`, `data/workout_feedback.json`, or anything
  Task 2.3/Hevy owns.
- Build the week/block/periodic rows of §8's table in this prompt.
- Flip `ui_theme.PIPELINE_STAGES`'s `"readiness"` entry — it names an
  unrelated, still-unbuilt subjective check-in feature.
