# PROMPT-7 — The hard-coding audit: which planning decisions may a preset reach?

**Complete.** It wrote no code, and its deliverable — the verdicts — is
`design-01` §3.4a, which is also the field list `PROMPT-9` consumed. Kept as
the record of what was asked for and why; it is not work to pick up.
`dev/README.md`'s order of delivery is the authority on what is still
outstanding — this banner states a verdict, never a rank, because a verdict
cannot go stale.

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It
changes **no code at all** — the deliverable is a set of verdicts — so there is
nothing for the queue to run and everything for a human to disagree with.

**Priority: ahead of PROMPT-8 and PROMPT-9**, and ahead of anything in 4–6.
The file numbers are identity, not rank; see `dev/README.md`.

Cold session. Read `design-00-program.md` **§1's governing rule and its
table**, then `design-01` **§3.4** (where the data/code line falls) and **§9.2**
(the editor's field list, which this prompt exists to stop being decided by
accident).

## The requirement

Stated 2026-09-01:

> *"I don't want to add any more logic blocks to creating a 'default' meal
> plan. I want to make all logic/customisation of meal plans to be done via
> presets where possible."*

`design-00` §1 promotes that to a program-wide constraint and supplies the test:

> **Can a user turn this off, or change it, without editing Python?**

§3.4 names exactly one behaviour that fails it — `apply_batch_selections` — and
`design-02` fixes that one. **Nothing has checked the rest.** A first pass found
four more; this prompt is the complete pass.

## Why this comes before the editor, which is the non-obvious part

`design-01` §9.2 lists nine key groups the preset editor will expose. That list
was assembled from the brief's own examples — which is a reasonable way to start
and a poor way to finish, because it makes the editor's field list a record of
*what the user happened to complain about* rather than of what is actually
compiled in.

Run the other way, the audit produces the field list as its output. Build the
editor first and every ❌ row becomes a second release.

## What to do

**Step 1 — enumerate.** Every module-level constant and `planning_rules` key in
`src/planner.py` and `src/week.py` that participates in *what a week looks
like*. Ignore anything that is protocol, formatting, logging, or an ordering
guarantee. The starting set found on 2026-09-01, which is not claimed to be
complete:

| Candidate | Where |
|---|---|
| `WORKOUT_BREAKFAST_TYPES`, `WORKOUT_BREAKFAST_STYLE`, `MORNING_TRAINING_CUTOFF` | `planner.py` |
| `cuisine_block_pattern`, `min_baseline_cuisine_share` | `engine.json` |
| `WEEKNIGHT_ELAPSED_LIMIT_MINUTES` | `planner.py` |
| `WEEKNIGHT_PREP_LIMIT_MINUTES`, `WEEKEND_PREP_LIMIT_MINUTES`, `WEEKEND_DAYS` | `planner.py` |
| `DEFAULT_MEAL_WEIGHTS`, `DEFAULT_SERVINGS_PER_MEAL` | `planner.py`, `week.py` |
| `favorite_breakfast_slots`, `favorite_dinner_slots`, `favorite_reuse_days` | `planning_rules` |
| `NUDGE_FOOD_SAMPLE_SIZE` | `planner.py` |
| `TRAINING_INTENSITY_SPLIT`, `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES` | `planner.py` |
| `UNDER_TARGET_NOTE_THRESHOLD`, `SUNDAY_PREP_REHEAT_MINUTES` | `planner.py` |
| `MEAL_TYPE_PRIORITY`, `PREP_DAY_INDEX`, `MACRO_KEYS` | `planner.py`, `week.py` |

**Step 2 — rule each one, with reasoning.** §3.4's line: *data describes what
you want; code describes how it is achieved.* Three verdicts, not two:

| Verdict | Means |
|---|---|
| **data** | becomes a preset key. Goes on §9.2's list, with its absent-meaning |
| **code** | stays compiled in — **and the reason is recorded** |
| **config, not preset** | reachable in `engine.json` already, and a *mood* should not vary it |

**Step 3 — write the verdicts into `design-01` §3.4's table** and reconcile
§9.2's field list against the *data* rows. Resolve every ❌ in `design-00` §1's
table; that table is the audit's own checklist.

**Step 4 — graduate the *code* verdicts to CHANGE-QUEUE.md.** OUTSTANDING §E
records that this project has already accumulated seven decided-against items
living only in `dev/`, which is precisely the anti-pattern CHANGE-QUEUE.md has
a rule about. A row ruled *code* is a decision against a feature.

## The judgement is the deliverable, not the list

Two worked examples, because the answer is genuinely different per row:

- **The morning-gym shake pin is probably `code`.** CLAUDE.md gives it a
  nutritional argument — a hypertrophy session needs fast-digesting protein,
  and a shake is the only breakfast in `meal_styles` drinkable ten minutes
  before a session. A preset is a *mood*, and a mood overriding that is the
  rigidity trade running the wrong way. But `MORNING_TRAINING_CUTOFF` and
  `WORKOUT_BREAKFAST_TYPES` may split from it: *whether cardio counts* is a
  preference where *what a lifted morning needs* is not.
- **`cuisine_block_pattern` is plainly `data`.** 4/3 is a taste about how many
  nights share a spice shelf. It is already in `engine.json`, so the verdict is
  most likely *config, not preset* — unless a `comfort` week wants 7/0, which
  is exactly the sort of thing to decide here rather than discover.

**A row ruled `code` with a written reason is a successful outcome.** The
failure mode is a row with no verdict, because that is how it stays compiled in
by default.

## Acceptance

- Every candidate has a verdict and a reason. No row is left unruled.
- Every ❌ in `design-00` §1's table is resolved to one of the three verdicts.
- `design-01` §9.2's field list is derived from the *data* rows, and any group
  it currently lists that the audit did not reach is either justified or
  dropped.
- Every *data* row states its **absent-meaning**, and it is exactly today's
  behaviour (`design-03` §4.1). A row that cannot state one is not ready.
- Every *code* row is filed in CHANGE-QUEUE.md with its reasoning.
- **Zero lines of `src/` changed.** If the audit finds a bug, file it; do not
  fix it here.

## Do not

- Change behaviour. This is a read of `main`.
- Rule a row `data` because it is easy to expose. The test is whether a *mood*
  legitimately varies it.
- Rule a row `code` silently. An unrecorded verdict is re-litigated.
- Expand §9.2's list past what the audit justifies — the editor's cost tracks
  widget shapes (`design-03` §1), and every field is a shape to maintain.
