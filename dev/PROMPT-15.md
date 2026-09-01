# PROMPT-15 — Constraint-aware workout plans and the first progression loop

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It calls
an LLM, writes a new plan and feedback store, and changes the Today/Adaptive
Workout surface. Its acceptance requires visual and generated-plan review.

**Depends on PROMPT-14.** Hevy (`PROMPT-4`) is an optional richer input, not a
gate: the static-plan and manual-feedback path must work without it. This is the
useful first half of Arm E, not the full fatigue/deload controller.

Cold session. Read `design-06-exercise-planning.md` in full,
`docs/exercise-protocols.md` §§Clinical Monitoring–Detailed Protocols,
`docs/fitness-model.md` §§Sub-Maximal Gym Strength–Integrated Synthesis, and
CLAUDE.md's generation, storage, adherence, and "confirmation is the feature"
sections. Load the `ui-work` and OpenRouter model-choice skills before changing
UI or model calls.

## The requirement

For each gym/resistance session already present in `training_schedule`, generate
a structured exercise plan from the selected gym program while always applying
the persistent personal constraints from `training_profile`.

The motivating acceptance case is concrete: a squat pattern for the declared
hip impingement may use only the user's established pain-free depth. A full-depth
squat, a missing modification note, or progression by increasing depth must not
be storable.

## What to do

### 1. Add typed workout-plan models and their own storage

Implement the models in `design-06` §4. Every exercise carries a controlled
movement pattern, role, dosage, optional evidenced target load, execution notes,
applied constraint ids, and a progression rule. A target load comes only from
an unambiguous history match; with no match it is null and the user selects a
load that reaches target RIR. The model never invents a starting weight.

Add `data/workout_plans.json` and repository methods keyed by calendar date and
stable session id. Follow the current/next week selection convention rather
than keying generated content by bare weekday. Keep it separate from:

- `training_schedule`, which owns standing day/time/type/duration/burn;
- `adherence.json`, which owns whether a session happened;
- Hevy strength history, which owns completed sets.

Missing file means no generated details and the existing Adaptive Workout view
continues to show the schedule summary.

### 2. Generate all gym sessions for the week in one structured call

Build one Instructor/Pydantic call over the week's gym sessions, the selected
program, available equipment, personal constraints, and—when available—recent
exercise history. Weekly generation is required so movement-pattern distribution
and A/B/C variation are coherent.

Do not add, remove, or retime sessions. If no active gym program or no gym
sessions exist, make no model call and return an explicit no-op result.

The prompt must state every personal constraint verbatim, preferred variations,
the precedence rules, and that constrained range of motion is fixed rather than
a progression target. Log completion timing/tokens through the existing helper.

### 3. Enforce constraints in one pure validator

Implement `design-06` §5 as shared pure logic used by full generation,
single-session regeneration, imported/manual edits if those exist, and storage
validation:

- exact normalized exercise and controlled movement-pattern matches;
- reject `exclude`;
- require matching `modify` ids and instructions on the exercise;
- reject progression that changes a constrained range of motion;
- use preferences in prompting without treating absence as invalid.

Instructor retries receive the exact failure. Never silently delete an invalid
exercise, relabel its movement pattern, or substitute one after validation.

Add a direct test of the hip example covering full-depth rejection, missing-note
rejection, valid user-approved partial-range acceptance, and range-progression
rejection.

### 4. Make Adaptive Workout the owner of the detail

Expand Today/Adaptive Workout from its schedule summary to show:

- session and selected program;
- exercise order, sets/reps/RIR/rest;
- execution notes;
- visibly attached personal constraint notes;
- regenerate-session action;
- completion source from the existing adherence/Garmin view.

Generation/regeneration is explicit, never on page load or field change. A plan
that fails leaves the previous stored plan intact and reports the failure.

### 5. Add the minimal limitation-response loop

Add `data/workout_feedback.json` with the exact key and three responses from
`design-06` §7. Surface the response only on exercises to which a personal
constraint applied:

```text
No issue | Mild irritation | Worse than usual
```

Writing a response updates the view immediately. It never edits
`training_profile` and never diagnoses or assigns a numeric pain score.

### 6. Propose progression; never apply it silently

Implement pure progression proposal logic for `double_progression` and
`two_for_two` as specified in `design-06` §6. Evidence priority:

1. Hevy completed sets with load/reps/RPE, if the integration exists;
2. otherwise no performance claim—manual feedback can hold/flag but cannot
   manufacture qualifying reps.

`mild_irritation` holds the prescription. `worse_than_usual` suppresses
progression and flags substitution/review. `no_issue` removes that feedback
block but is not itself proof that reps were completed.

Every proposal names its evidence and requires an Accept action before the next
stored plan changes. Range of motion remains fixed for a modified movement.
A load-increase proposal requires a non-null load established by history.

Do not implement the full HRV/RHR fatigue matrix here. If readiness data is
shown, it is read-only context unless a separately tested rule already exists.

### 7. Documentation

Update CLAUDE.md with the three distinct stores and ownership rules, workout
generation/validation path, and feedback/proposal semantics. Update the root
README with the user workflow. Mark only the delivered portions of
`design-06`, `OUTSTANDING.md`, and this prompt complete.

## Acceptance

- Null active program and no workout-plan file produce today's schedule-only
  behaviour and no model call.
- Birth date is not read by workout generation or constraint resolution.
- Generation creates exactly one detailed session per declared gym session and
  never changes schedule timing or burn.
- The same constraint validator serves full generation and regeneration.
- The four hip-constraint cases in §3 are directly tested.
- Every stored exercise has a known movement pattern and typed dose.
- An exercise without matched history has null `target_load_kg`; no generated
  starting weight is presented as personal evidence.
- Applied constraints are visible beside the affected exercise.
- A failed generation/regeneration leaves the previous file byte-identical.
- Feedback round-trips under its four-part key and never mutates config.
- Mild/worse feedback blocks progression as specified; no-issue alone cannot
  fabricate performance evidence.
- A Hevy-backed proposal cites the qualifying sets, percentage increment, and
  rule; acceptance is the only writer of the changed prescription.
- Without Hevy, the detailed plan and manual feedback remain fully usable and
  no fake load/RPE appears.
- Full suite passes; visually test Today and the workout dialog at 1280px and
  1440px; run one model call against a constrained fixture and retain the
  structured result as acceptance evidence without committing personal data.

Focused tests should live in new `test_workout_planning.py` and
`test_workout_feedback.py`, with existing adherence/UI tests extended only for
the boundaries they already own.

## Do not

- Infer any restriction, exercise, or program from age.
- Add/remove/retime `training_schedule` sessions.
- Store detailed exercises in `training_schedule` or `WeekPlan`.
- Treat the model prompt as the constraint validator.
- Fuzzy-match diagnoses or claim biomechanical proof from exercise prose.
- Automatically edit a personal constraint, accept progression, or substitute
  an exercise.
- Count `no_issue` as proof of completed repetitions.
- Build the fatigue matrix, automated deloads, functional-test diagnosis, or
  medical screening in this prompt.