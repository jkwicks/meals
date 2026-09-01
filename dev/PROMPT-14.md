# PROMPT-14 — Personal exercise constraints and the gym-program catalog

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It adds
config-writing Settings surfaces and changes the shared preset validator, so its
acceptance is both structural and visual.

**Delivery: the first exercise-planning slice.** Independent of Hevy and the
readiness analytics. `PROMPT-15` depends on it. `PROMPT-13` remains reserved for
blocks; prompt numbers are identity, not rank.

Cold session. Read `design-06-exercise-planning.md` in full, CLAUDE.md's
**"config/ is eight files"**, **"Presets"**, and the `ui-work` skill. Read
`src/presets.py`, `planner.UserProfile`/`AppConfig`, the Settings integration
dialogs, and the preset editor before editing.

## The requirement

Add optional persistent personal exercise constraints and a catalog of gym
programs. A weekly preset may select a named gym program but may never erase the
person's constraints. Merely having a birth date—specifically being 55—must
activate nothing.

This prompt builds configuration and UI only. It does not generate workouts.

## What to do

### 1. Add typed configuration with benign absent meanings

Follow `design-06` §2 exactly:

- `training_profile` is a top-level core key owned by `config/profile.json`;
- `gym_programs` and `active_gym_program` are top-level core keys owned by
  `config/schedule.json`;
- all are declared on `AppConfig` with strict nested Pydantic models;
- missing/empty profile means no personal restriction;
- empty catalog plus null active program means no detailed-workout feature.

Use controlled literals for constraint scope/action, movement pattern,
architecture, progression, and primary goal. IDs and catalog keys are stable
identities; labels are display text. Validate unique constraint ids, valid
rep-range pairs, a known active program, and program values with useful bounds.

Do not put these under `user_profile`: `training_profile` has its own lifecycle
and the preset-protection rule needs one unambiguous root.

Ship no active program. If a sample functional-hypertrophy catalog entry is
added, leave `active_gym_program` null so the merged base remains behaviourally
identical. Schema defaults and test fixtures keep `training_profile` empty, but
add the user's explicitly stated constraint to this installation's
`config/profile.json`: hip impingement means no full-depth squat and only a
user-approved partial range. Do not invent a preferred substitute they did not
name.

### 2. Protect persistent facts in the one shared preset resolver

The existing resolver accepts every core-rooted leaf. Add the narrow rule from
`design-06` §3:

```text
training_profile    protected
gym_programs        protected
active_gym_program  presettable
```

Both a root override and a nested override under either protected root fail.
Return an ordinary `PresetFailure` naming preset and path. The loader raises on
it and the editor renders it; do not add a UI-only guard.

Extend tests proving a preset can select a known program and cannot empty,
replace, or mutate either protected object. Re-layer from base exactly as today.

### 3. Add Settings editors using existing widget shapes

Add one Training section with:

- personal movement constraints as editable records;
- optional available equipment and notes;
- gym programs as catalog records;
- the standing base `active_gym_program` selection.

Use `design-03`'s existing shapes: selects, bounded numbers, free text,
multi-select, and list-of-record cards. Add/remove refreshes; field edits do not
steal focus. Validate through the same Pydantic/config path before writing.

Persist top-level keys through `save_config_keys`, which is correct here because
all three are core keys in `CONFIG_FILES`. Do not use `save_presets_config` for
the catalog or personal profile.

### 4. Let the weekly preset choose the active gym program

Add `active_gym_program` to the preset editor as an optional select populated
from the base catalog. It is the only exercise-planning field in the weekly
preset editor.

In the review dialog, show the resolved active gym program and a read-only
summary of personal constraints that will bind. Reuse the existing one weekly
preset pick; do not add an independent "gym preset for this week" selector.

If the user changes the standing base active program in Settings, persist it. If
the active weekly preset overrides it, the review must say which resolved value
won rather than showing the inert base value.

### 5. Documentation

Update CLAUDE.md's config manifest, preset protected-root rule, and UI ownership
notes. Update the root README configuration table. Do not describe workout
generation as built; this prompt only makes the inputs representable.

## Acceptance

- Existing config with none of the new keys loads and behaves identically.
- The shipped config selects no gym program by default.
- A birth date with an empty training profile produces no constraints and no
  program selection. Assert this directly.
- The hip example is present only in this installation's personal config and
  round-trips through config and the Settings editor; empty defaults remain empty.
- Duplicate constraint IDs and an unknown active program fail at load and are
  refused by the editor without changing the file.
- A preset can set `active_gym_program` to a known catalog key.
- Preset overrides rooted at `training_profile` or `gym_programs`, including
  nested leaves, fail through the shared resolver with preset/path named.
- Changing a weekly preset cannot mutate the base catalog or personal profile.
- Editing a field keeps focus; add/remove refreshes. Visually verify at 1280px
  and 1440px and use no new color.
- No workout model call, workout-plan file, progression, or medical inference is
  introduced.

Extend `test_config_layout.py`, `test_presets.py`,
`test_preset_validation.py`, and `test_ui_state.py`; add a focused
`test_training_config.py` if the schema/editor cases would otherwise bury their
purpose. Run the full suite.

## Do not

- Infer constraints or select a program from `birth_date`.
- Store personal constraints only inside a gym program or weekly preset.
- Let a preset override `training_profile` or redefine `gym_programs`.
- Add a second weekly preset picker.
- Put exercise arrays into `training_schedule`.
- Build workout generation, Hevy sync, feedback, progression, or deload logic.
- Add a general permission language for config paths; two protected roots are
  enough.