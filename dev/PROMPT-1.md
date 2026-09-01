# PROMPT-1 — Diagnose the empty `activity_log`, then backfill it

**Not queue-safe, and structurally cannot be.** `scripts/claude-queue.sh`
globs `.prompts/prompt-*.md`; this file is in `dev/` and named `PROMPT-*`, so
the queue cannot see it. That is deliberate — this prompt hits a live Garmin
account and its first half is a diagnosis whose outcome decides the second.

Cold session. Read `dev/design-00-program.md` finding **F6** first.

**Unaffected by the preset/`week_shape` work** in `design-01`–`03`: this is
Garmin sync, and nothing in Arm A touches it. It is Tier 1 item 1 and the
highest-leverage unblocked thing in the program — the floor under Arm E, which
cannot start until `activity_log` has rows.

## The problem

`data/biometrics.json` has `activity_log: []` — zero rows — while
`sync_checkpoints.garmin` is recent. `sync_garmin` calls
`save_activity_entries` unconditionally for every date it processes, so the
list should not be empty unless something upstream is dropping rows.

This matters out of proportion to its size: `nutrition_engine.propose_training_
schedule` is the only reader today, and **every signal in the training and
periodization work reads this list**. It is the floor under that whole arm.

## Part 1 — diagnose. Do not fix anything yet.

Two candidate causes. Separate them before touching a line:

1. **The checkpoint hides the history.** `activity_log` shipped after those
   dates were first checked, and `get_sync_date_range` anchors catchup on the
   checkpoint — so dates that could hold activities are already marked
   "asked" and are never re-fetched. CLAUDE.md names this exact trap for
   `readiness_log` under "Biometric sync" and says a `--date` re-sync is the
   fix. If this is the cause, the whole history is one backfill away.
2. **`_storable` is rejecting everything.** A row needs a modality
   `GARMIN_SESSION_TYPES` maps *and* a readable `startTimeLocal`. A Fenix 8
   recording `indoor_cycling`, `virtual_ride`, `walking`, `lap_swimming`,
   `strength_training` or similar under a `typeKey` the table does not name is
   dropped silently and by design.

Establish which, by fetching one date you know had a workout and printing
what comes back **before** `_storable` filters it:

    ./venv/bin/python src/integrations/sync_service.py --sync-garmin --date <YYYY-MM-DD>

Report the raw `typeKey` values seen. That list is the evidence for Part 2.

## Part 2 — act on what Part 1 found

**If cause 1:** re-sync the historical window with explicit `--date` calls and
confirm rows land. No code change. Then check whether
`propose_training_schedule` starts producing proposals, and say so either way.

**If cause 2:** extend `GARMIN_SESSION_TYPES` with the modalities actually
observed — **only** those observed, mapped to the existing `training_schedule`
vocabulary. Do not invent a catch-all: CLAUDE.md is explicit that an unmapped
modality guessed at is worse than one absent, because "a yoga class offered as
'Cardio Easy, 45 min, 260 kcal' is a wrong answer that looks like a right
one". A modality with no honest counterpart stays unmapped.

**If both:** fix the mapping first, then backfill, or the backfill re-stores
the same nothing.

## Acceptance

- The cause is stated in the summary, with the raw `typeKey` evidence.
- `activity_log` has rows for dates that genuinely had activities.
- `propose_training_schedule` is exercised against the real data and its
  result reported — including "no proposals, and the declared week already
  matches", which is a good answer and the one most easily misread as broken
  (`ui_state.training_proposals_view` exists precisely to tell those apart).
- If `GARMIN_SESSION_TYPES` changed: a test in `tests/test_sync_service.py`
  covering the newly mapped modalities, and the record of *why* they were
  added — per CLAUDE.md's rule that a test written after a bug records the
  failure, not just the fix.
- CLAUDE.md updated if the mapping or the backfill behaviour changed.

## Do not

- Widen `_storable` to accept unmapped or untimed activities.
- Add a catch-all to `GARMIN_SESSION_TYPES`.
- Build anything that *reads* `activity_log` beyond what exists. This prompt
  fills the list; consuming it is later work.
