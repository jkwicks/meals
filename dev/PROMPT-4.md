# PROMPT-4 — Hevy: confirm the account logs RPE, then land the first slice

**Not queue-safe** (in `dev/`; `claude-queue.sh` globs `.prompts/prompt-*.md`).
It authenticates against a live third-party account, and its first half is a
probe whose outcome decides the second.

Cold session. Read `dev/design-00-program.md` **D4** — the API-level question
is already answered there — and CLAUDE.md's **"Biometric sync"** section,
whose eight decisions this integration has to follow rather than re-derive.

## What is already known, so it is not re-researched

Resolved from the `go-hevy` client's public type definitions on 2026-09-01:

```
WorkoutSet{ Index, Type, WeightKg*, Reps*, DistanceMeters*,
            DurationSeconds*, RPE*, CustomMetric* }
WorkoutExercise{ Index, Title, Notes, ExerciseTemplateID, SupersetID*, Sets[] }
Workout{ ID, Title, Description, RoutineID, StartTime, EndTime, UpdatedAt, ... }
ExerciseTemplate{ ID, Title, Type, PrimaryMuscleGroup,
                  SecondaryMuscleGroups[], Equipment, IsCustom }
SetType ∈ { warmup, normal, failure, dropset }
```

`GET /v1/workouts` (paginated), `/v1/workouts/{id}`, `/v1/workouts/count`.
Auth is an **`api-key` header**. A **Hevy Pro** subscription is required; the
key comes from the Hevy web app under **Settings → Developer**.

**Every input the four sub-maximal metrics need is present.** The one thing
type definitions cannot answer is whether *this account* populates `RPE` —
it is a nullable field.

## Part 1 — probe. Do not build storage yet.

`HEVY_API_KEY` goes in **`.env`**, beside `GARMIN_*` and `CRONOMETER_*`.
That is the established split and not a fresh choice: CLAUDE.md states that
for the sync integrations *"credentials live in `.env`"* while
`integrations.json` holds tuning. Any Hevy **tuning** later — which set types
count as working sets, the e1RM rep cutoff, the RPE→RIR offset — belongs in
`config/integrations.json` under a `hevy` key, which that file is explicitly
described as "the declared home for the next such setting".

Write a throwaway probe **in the scratchpad, not in `src/`**, and report:

1. **What fraction of `normal`/`failure` sets carry a non-null `RPE`**, over
   the last ~20 workouts. This is the whole question. Report the number, not
   an impression.
2. **Payload size** — bytes for one workout, and for 20. This decides the
   storage question in Part 2 and cannot be guessed.
3. **Pagination and rate limits** — page size, headers, whether `count`
   makes an incremental sync cheap.
4. **Whether `UpdatedAt` supports incremental fetch**, so a sync can ask for
   what changed since a checkpoint rather than re-walking. This is the same
   `sync_checkpoints` shape Garmin and Cronometer already use.
5. **Do the exercise templates need their own fetch and cache**, and how many
   are there? Muscle group lives on the template, not the workout.

**Stop and report.** If under roughly half of working sets carry RPE, say so
plainly — three of the four metrics degrade to volume-only, and that changes
what Arm E is worth building before it is built.

## Part 2 — the storage slice, shaped by Part 1

Only after Part 1 is reported.

**Where it goes is a real decision and Part 1's payload measurement settles
it.** `biometrics.json` holds four flat one-row-per-date lists; a workout is
nested (workout → exercises → sets) and far larger. The precedent for a
genuinely different-shaped signal getting its own file is `adherence.json` and
`rejections.json`. **Recommend `data/strength_log.json` unless the measurement
says otherwise**, and state the reasoning either way.

**It must not collide with `activity_log`.** Garmin already records that a
`strength_training` session happened; Hevy records what was *in* it. Two
sources, one event — which is precisely the collision
`BIOMETRIC_SECTION_SOURCES` keeps `weigh_ins` and `daily_actuals` apart to
avoid, and the reason `readiness_log` and `activity_log` are separate lists
rather than extra columns. Garmin keeps answering "did it happen"
(`match_recorded_sessions`); Hevy answers "what was in it". Neither overwrites
the other.

Follow the sync module's existing shape rather than inventing one:

- One seam for the client, so tests substitute at it and **nothing in the test
  suite touches the network**.
- `_from_env` distinguishing `None` ("read the environment") from `""` ("no
  credential") — and **a credential-guard test run against a *populated* fake
  environment**. CLAUDE.md records why: the earlier guard test constructed its
  subject with `""`, silently picked up the developer's real `.env`, and made
  genuine authenticated requests on every run of the suite until the account
  started returning 429s.
- A `sync_checkpoints` entry, so a re-run the same day issues no requests.
- Sequential fetches. A burst against a rate-limited account is the reliable
  way to turn a working catchup into a wall of failures.

**Store only what something reads.** This project's standing rule — *"an entry
in `CRONOMETER_MACRO_COLUMNS` has to assert that something reads it"* — was
learned from three signals fetched on every sync and read by nothing, and
`activity_log` is currently the fourth (PROMPT-1). For the first slice that
means: load, reps, RPE, set type, exercise template id, timestamps. Not
`CustomMetric`, not `SupersetID`, not notes, until something reads them.

## Part 3 — one reader, in the same change

Do not ship storage with no reader. The cheapest genuine one, and the one the
research rates highest for detecting fatigue *before* performance falls:

**e1RM per primary movement, blended Epley/Brzycki, RIR-adjusted** — with
`RIR = 10 − RPE` converted in **exactly one place**, and sets above ~10 reps
discarded, where the research says substrate depletion masks mechanical
capacity. A pure function in `nutrition_engine.py`, beside
`match_recorded_sessions`, which already speaks this vocabulary — a second
module knowing how a logged set maps onto a movement is a second chance to
disagree about it.

Report the trend for the account's top movements. That is the acceptance
evidence.

## Webhooks — deliberately not in this prompt

Hevy offers them and they are the wrong first step here.

`PlanRepository` was made fully async *"for the future backend that receives
asynchronous webhook pushes"*, so this would be the first real consumer of
that bet — which is a reason to note it, not to start with it. A webhook needs
a **publicly reachable URL**, and this app is localhost-only with **no auth on
`/api`** (a CHANGE-QUEUE.md item currently filed "blocked by: only if
exposed"). Accepting Hevy pushes flips that item from optional to **required**,
and drags a deployment story in with it.

Against which: gym sessions happen a few times a week, and the sync agent
already runs daily at 07:30. **A daily poll from the checkpoint is the same
shape Garmin and Cronometer already use, and latency buys nothing** — a week
is planned once, not reactively. Poll now; revisit webhooks only if something
genuinely needs sub-day freshness.

## Do not

- Put the API key in `config/integrations.json`. Credentials are `.env`.
- Store a field nothing reads.
- Merge Hevy sessions into `activity_log`.
- Expose an endpoint to accept webhooks.
- Build the workout planner or deload engine. Those are Arm E: the first useful
  slice is `design-06`/`PROMPT-14`–`15`, and the later controller needs the
  fatigue matrix from `docs/periodization-engine.md`. PROMPT-1 is complete;
  Hevy remains an enrichment/gate only for evidence-backed progression and the
  full fatigue controller.
