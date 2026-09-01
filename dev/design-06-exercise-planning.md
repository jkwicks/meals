# Design 06 — Exercise planning: personal constraints, gym programs, and feedback

Status: **approved direction, not built.** This is the first designed slice of
Arm E. `PROMPT-14` builds its configuration substrate; `PROMPT-15` builds the
planner and first feedback loop.

Read `design-00-program.md` §4–§5, `docs/exercise-protocols.md`,
`docs/fitness-model.md`, and `docs/periodization-engine.md` first. The three
research documents answer different questions:

- `docs/exercise-protocols.md` says what a useful program for an older trainee can
  contain and how exercises can progress;
- `docs/fitness-model.md` says what Garmin and Hevy can measure;
- `docs/periodization-engine.md` says when accumulated fatigue should change a day,
  week, or block.

This design deliberately does **not** wait for the complete periodization
controller. A useful first version can design and progress gym sessions from
declared facts, manual feedback, and whatever Hevy data exists. Biomarker-driven
deloading remains a later layer over the same plan.

---

## 1. The requirement

The workout planner must be able to produce a gym plan for one person rather
than for a population average. The motivating example is persistent and
specific:

> Hip impingement: do not prescribe a full-depth squat. A squat pattern is
> acceptable only at the user's established pain-free depth.

That statement must survive switching from a hypertrophy week to a deload,
functional-fitness, strength, or fat-loss week. It is not an implication of age
and it is not a property of one program.

At the same time, turning 55 must not silently activate a conservative program.
The new research provides **available program choices and defaults**, not a
diagnosis and not an age gate.

The governing split is:

```text
personal training profile       facts that remain true across programs
+ selected gym program          what this week/block is trying to do
+ training_schedule             when gym sessions can happen
+ history and feedback          what happened and how it felt
= proposed structured workouts
```

---

## 2. Three objects, three lifetimes

### 2.1 `training_profile`: persistent facts about the trainee

Owned by `config/profile.json`, beside `user_profile`. Optional and empty by
default. It contains only facts that should still bind after selecting a
different weekly preset:

```json
{
  "training_profile": {
    "movement_constraints": [
      {
        "id": "hip-impingement-squat-depth",
        "scope": "movement_pattern",
        "target": "squat",
        "action": "modify",
        "instruction": "Do not prescribe full-depth squats; use only my user-approved partial range.",
        "preferred_variations": []
      }
    ],
    "available_equipment": [],
    "notes": null
  }
}
```

The first version supports three actions:

| Action | Means | Example |
|---|---|---|
| `exclude` | Never propose the target exercise/pattern | no barbell back squat |
| `modify` | Keep the pattern only with the instruction attached | squat only through the user-approved partial range |
| `prefer` | Rank named, user-approved variations first | prefer a variation the user has entered |

`scope` is `exercise` or `movement_pattern`. Exercise matching is normalized
exact-title matching; movement patterns use a controlled vocabulary shared by
the generated exercise schema. No fuzzy medical ontology in version one.

An empty `training_profile`, absent `movement_constraints`, and absent config
all mean **no personal exercise restriction**. Existing installs therefore
generate no new behaviour merely by loading the schema.

### 2.2 `gym_programs`: a catalog of ways to train

Owned by `config/schedule.json`, because it describes the content of the gym
sessions already declared there. This is a catalog, not another calendar and
not another copy of `training_schedule`:

```json
{
  "active_gym_program": null,
  "gym_programs": {
    "functional_hypertrophy": {
      "label": "Functional hypertrophy",
      "primary_goal": "hypertrophy_and_function",
      "architecture": "full_body",
      "working_sets": 3,
      "compound_rep_range": [8, 12],
      "accessory_rep_range": [10, 15],
      "target_rir": 2,
      "progression": "double_progression",
      "include_power": true,
      "movement_patterns": [
        "squat", "hinge", "horizontal_push", "horizontal_pull",
        "vertical_push", "vertical_pull", "carry", "core"
      ],
      "notes": "Use low-impact power variations and distribute patterns across the week's gym sessions."
    }
  }
}
```

The active program supplies **content** for sessions whose
`training_schedule.type` is gym/resistance training. The schedule remains the
single source for day, time, duration, and expected burn. A two-gym-day schedule
gets two detailed sessions; the program must not manufacture a third day because
research says three can be useful.

`active_gym_program: null` means today's behaviour exactly: the app can show and
edit the declared session, but it does not generate exercise detail.

The initial catalog may contain a research-derived functional-hypertrophy
example, but it must not be selected automatically. In particular:

> **Birth date never selects a gym program and never creates a constraint.**

Age can later contextualize a functional assessment. It is not a switch.

### 2.3 The existing weekly preset selects a program

Do not add a second weekly preset selector. `config/presets.json` already names
the week and records that pick on `WeekPlan`. A meal/week preset may override
only this leaf:

```json
{
  "active_gym_program": "functional_hypertrophy"
}
```

That permits a future `fat_loss`, `recovery`, or `strength` weekly preset to
choose the appropriate gym program while retaining one explanation for why the
whole week looks as it does.

The gym-program catalog is edited in Settings; the weekly preset editor offers
the active program as a select populated from that catalog.

---

## 3. Personal constraints are protected, not merely inherited

The current leaf-path preset resolver can legally override any path rooted in a
core config key. Therefore putting constraints in `profile.json` is not enough:
a hand-edited preset could otherwise state
`"training_profile.movement_constraints": []` and remove them for a week.

Add an explicit protected-root rule to the shared preset resolver:

```text
training_profile    never presettable
gym_programs        never presettable
active_gym_program  presettable
```

The loader and editor use the same resolver, so a protected-path attempt is
refused in both places with the preset and path named. This is narrower than a
general permissions language: one constant/set of protected roots, one check.

A program may add conservative choices through its ordinary fields and notes.
It may not remove a personal constraint. Precedence is therefore:

```text
personal exclude > personal modify > personal prefer > program preference
```

There is no override checkbox in version one. Editing or removing the personal
constraint itself is the explicit override.

---

## 4. Structured workout output

Detailed workouts are a different lifecycle and shape from meal recipes and
from `training_schedule`. Store them in `data/workout_plans.json` through
repository methods, keyed by calendar date and a stable session id. Do not add
exercise arrays to the schedule: a standing Saturday session is not the same
fact as what Saturday's generated session contains.

The model output is typed:

```text
WorkoutPlan
  week_start_date
  gym_program
  sessions[]

WorkoutSession
  date
  session_id                 # same identity vocabulary as schedule/adherence
  name
  planned_duration_minutes
  exercises[]

ExercisePrescription
  exercise_id
  name
  movement_pattern
  role                       # power | compound | accessory | carry | core
  sets
  rep_min / rep_max
  target_load_kg | null      # matched history only; never an invented weight
  target_rir
  rest_seconds
  execution_notes
  applied_constraint_ids[]
  progression_rule
```

Generate the week together, not one session at a time. Distribution of movement
patterns, recovery between repeated patterns, and A/B/C variation are weekly
questions. One call can see all declared gym sessions and cannot accidentally
produce three unrelated copies of Monday.

The selected program and personal constraints are included in the prompt. The
output is still validated deterministically before it can be stored.

`target_load_kg` is populated only from an unambiguous exercise-history match.
With no match it is null and the instruction is to select a load that produces
the prescribed RIR. The model must not invent a kilogram figure for a person it
has never observed lifting that exercise. Progression proposals require a
non-null evidenced load.

---

## 5. The hard half: deterministic constraint validation

Prompt instructions are guidance; they are not enforcement. One pure validator
must run on generated, regenerated, imported, or manually edited workouts.

For each exercise:

1. normalize its title and validate its controlled `movement_pattern`;
2. collect matching exact-exercise and movement-pattern constraints;
3. reject an `exclude` match;
4. require every matching `modify` constraint id in
   `applied_constraint_ids` and require its instruction in `execution_notes`;
5. rank `preferred_variations` in the prompt, but do not reject a valid plan
   merely because none can be used;
6. reject progression text that changes a constrained range of motion unless
   the personal constraint explicitly permits it.

The validator is deliberately modest. It can prove that an excluded exact title
or declared pattern did not pass and that a required modification travelled
with the exercise. It cannot prove biomechanics from prose, and version one
does not claim it can. The UI shows the applied constraint beside the exercise
so the user can review the result.

Constraint failure retries the model with the exact failure, following meal
generation's Instructor/Pydantic pattern. It never silently drops the movement
pattern or substitutes an unreviewed exercise after generation.

---

## 6. Progression is a proposal, never a hidden edit

The first program-level feedback loop is exercise progression:

```text
prescription -> completed set evidence -> progression proposal -> user accepts
       ^                                                        |
       +--------------------------------------------------------+
```

Implement the program's declared progression method:

- `double_progression`: add reps inside the range; after all working sets reach
  the top at target RIR, propose a 2.5–5% load increase and reset to the bottom;
- `two_for_two`: after two qualifying sessions with two reps above target while
  retaining the required RIR, propose a 2.5–5% increase.

Hevy load/reps/RPE is the preferred evidence when `PROMPT-4` has landed. Convert
RPE to RIR in the one existing planned conversion. If Hevy is absent or its RPE
is null, show the plan and allow manual completion/response; do not invent set
performance.

For a modified range of motion, depth is **not a progression variable**. The
hip example may progress reps and then load while keeping the established depth
fixed. Only editing the personal constraint changes that fact.

Every proposal names its evidence and requires acceptance. This copies the
existing Garmin schedule-proposal rule: detection is cheap; confirmation is the
feature.

---

## 7. The first subjective feedback signal

The existing `adherence.json` answers whether a workout happened. It does not
answer how a constrained movement was tolerated. Keep those questions separate.

Store a small event/upsert record in `data/workout_feedback.json`, keyed by date,
session id, exercise id, and constraint id:

```json
{
  "date": "2026-09-07",
  "session_id": "2026-09-07:gym_hypertrophy:05:30",
  "exercise_id": "partial-range-squat",
  "constraint_id": "hip-impingement-squat-depth",
  "response": "no_issue",
  "note": null
}
```

Three responses are enough initially:

- `no_issue` — progression remains eligible;
- `mild_irritation` — hold load, repetitions, and constrained range;
- `worse_than_usual` — suppress progression and propose reviewing/substituting
  the exercise next time.

This is training feedback, not a diagnosis or pain score. It does not change the
persistent constraint automatically. A poor response can hold or flag a plan;
only the user edits their profile.

---

## 8. How this joins the larger feedback controller

This design establishes the controlled object—the actual exercises, doses, and
progression proposals—that Arm E previously lacked. Later work layers onto it:

| Horizon | Signal | Later action |
|---|---|---|
| morning | HRV/RHR/sleep/readiness | cap RPE, reduce sets, replace HIIT |
| session | Hevy RPE/RIR and manual limitation response | hold/progress/substitute exercise |
| week | e1RM, hard sets, adherence | progress volume or trigger deload |
| block | fatigue matrix and goal | change program/deload structure |
| periodic | chair stand, TUG, grip, VO2 estimate | change emphasis based on functional outcome |

`PROMPT-15` owns only the session row and the non-biomarker portion of the week
row. It may display readiness but must not implement the full fatigue matrix in
passing.

Functional assessments from `docs/exercise-protocols.md` are valuable, but recording
and interpreting them is a later Arm D outcome-measurement prompt. They do not
block useful workouts for a 55-year-old trainee.

---

## 9. UI surfaces

Use existing interface vocabulary from `design-03`:

- **Settings → Training profile:** editable constraint records and available
  equipment;
- **Settings → Gym programs:** catalog editor using the existing list-of-records
  pattern, with nested movement-pattern multi-select;
- **Review dialog:** active gym program selector, and a summary of constraints
  that will bind. The weekly preset can seed this value;
- **Today / Adaptive Workout:** the detailed session, applied-constraint notes,
  feedback buttons, and progression proposals;
- **Insights later:** trends and functional assessments, not part of the first
  slice.

Editing a personal constraint or gym program persists immediately because each
is standing config, not an input to one generation. Generating/re-generating a
workout is explicit and never happens on each keystroke.

---

## 10. Compatibility and acceptance

- No `training_profile`, no `gym_programs`, or `active_gym_program: null`
  preserves today's schedule and UI behaviour; no workout model call occurs.
- Birth date alone changes no exercise, schedule, volume, intensity, or UI
  warning.
- Switching weekly presets cannot remove or replace `training_profile`.
- A hand-edited preset attempting either protected root fails at the shared
  resolver with preset and path named.
- Selecting a program never adds/removes/retimes `training_schedule` sessions.
- Every generated exercise has a controlled movement pattern.
- The hip example cannot return a full-depth squat, cannot omit its range note,
  and cannot progress by increasing depth.
- Constraint validation is shared across full generation and regeneration.
- A feedback response affects eligibility for the next proposal but never edits
  the personal constraint.
- A progression is proposed and attributed; never silently applied.
- Missing Hevy/readiness data degrades to a static plan plus manual feedback,
  not to failure and not to fabricated evidence.

---

## 11. Sequence

1. **`PROMPT-14` — substrate:** schemas, protected preset paths, persistence,
   Settings editors, and active-program review control. No workout generation.
2. **`PROMPT-15` — useful loop:** structured generation, storage, deterministic
   constraint validator, Today surface, feedback, and progression proposals.
3. **Arm D/Arm E analytics later:** RHR/readiness fetch, HRV band, fatigue matrix,
   deload controller, and functional-assessment trends.

`PROMPT-14` is independent of Hevy. `PROMPT-15` may use Hevy only if `PROMPT-4`
has landed; its static/manual path is required either way.

---

## 12. Deliberately not in the first version

- Inferring a limitation, program, or deload threshold from age.
- Medical screening, diagnosis, rehabilitation, or automatic clearance.
- A general anatomical/biomechanical rules engine.
- Fuzzy matching arbitrary exercise prose to diagnoses.
- Automatically changing a personal constraint from feedback.
- Treating range of motion as a progression variable when it is constrained.
- Automatically accepting a load, substitution, schedule, or program change.
- The full HRV/RHR/e1RM fatigue matrix and automated deload controller.
- Functional-test normative diagnosis; later tracking may show trends and
  reference bands without diagnosing sarcopenia or fall risk.