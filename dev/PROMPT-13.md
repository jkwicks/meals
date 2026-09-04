# PROMPT-13 — Blocks: dated, one-off exceptions over standing presets

**Not queue-safe** (in `dev/`; `claude-queue.sh` globs `.prompts/prompt-*.md`).
It introduces a new supplemental config file with load-time validation
(overlap refusal, a required successor), a new standing-commitment write path,
and has visual acceptance on three surfaces.

**Delivery tier 3, "the larger half of Arm A." Depends on PROMPT-2 (day-scoped
diet styles), PROMPT-8 (preset container, layer, weekly pick) and PROMPT-9
(preset editor + validator), all shipped.** This prompt does not re-open the
preset mechanism; it adds the one thing on top of it that has dates.

Cold session. Read `dev/design-01-presets-and-blocks.md` §4–§6 and §8–§11 in
full (§1–§3 and §7 describe the preset mechanism and the NOVA/processing axis,
both already shipped or explicitly out of this prompt's scope — see below).
Read CLAUDE.md's **"Targets come from the body, not the file — unless you say
otherwise"** (the `target_modes`/`target_locks` split this reuses),
**"Presets: naming the profile `config/` already implies"**, and **"TDEE is
measured once there is enough data to measure it"** (the FFM/BIA noise
argument the frozen protein floor relies on). Load the `ui-work` skill before
touching any `ui_*.py`.

Before writing anything, AST/Tree-sitter-inspect the on-disk preset
implementation this prompt sits on top of — do not assume a name or signature
from this document or from `design-01`, which predates the code:

- `src/presets.py` — `PresetFailure`, `PresetResolution`, `resolve_config`,
  `apply_overrides`, `preset_entries`/`active_preset_name`/`preset_overrides`,
  `preset_changes`. The pure, storage-free resolver shape a block's own
  resolver should match.
- `src/planner.py` — `resolve_preset_layer`/`apply_preset_layer` (where the
  preset layer meets `AppConfig`), `day_scoped_entries` (general over
  `subject_key`, already built for exactly this reuse — see §5 below),
  `active_diet_styles`, `hydrate_dynamic_targets` (the day loop this prompt
  hooks into: `target_is_stated`, `TARGET_MODE_MACROS`,
  `diet_style_calorie_ceiling`, the training-uplift replay), `WeekPlan.preset`
  (**already exists and is already recorded** at generation — confirm, do not
  rebuild), `recipe_eligibility_error`/`select_favorite_assignments` (named
  here only so you don't touch them — see Scope).
- `src/nutrition_engine.py` — `calculate_macro_targets`,
  `calculate_dynamic_deficit`, `resolve_current_weight_kg`,
  `measure_adaptive_tdee`/`calculate_adaptive_tdee`.
- `src/repository.py` — `CONFIG_FILES`/`CONFIG_KEY_OWNER`,
  `load_presets_config`/`save_presets_config` (the supplemental read/write
  shape a block store follows), the freezer store's upsert-by-id methods
  (`load_freezer`/`save_freezer_item`/`delete_freezer_item` — the alternative
  shape worth comparing against, since a block is a stable-identity record
  too).
- `src/ui_state.py` — `PlannerState.set_preset`/`_relayer`,
  `PRESET_SEEDED_FIELDS`, `preset_catalog_view` (the re-layer-and-re-seed
  pattern a block's mid-week effect must not fight), `set_target_mode` and
  `accept_training_proposal` (the two existing "resolve once, persist as a
  standing fact" precedents the frozen protein floor should follow).
- `src/ui_review.py`'s `training_editor` (the list-of-records editor
  convention the Blocks panel copies) and `src/ui_presets.py` (the preset
  editor's validate-before-save shape).

## The requirement

Once a preset is a **weekly** pick (PROMPT-8), a **block** is the
pre-commitment device that suspends it: a dated span — "Fast 800 for four
days" is the brief's own example — that overrides a small, fixed set of
planning inputs without mutating the preset underneath it, and without ever
becoming a second preset-shaped mechanism that can set arbitrary config keys.

Two things make this a real design constraint rather than "a preset with
dates on it":

- **A block boundary can fall mid-week.** Monday–Thursday in-block,
  Friday–Sunday out, inside one generated 7-day grid. Block resolution is
  therefore "which block covers each day of the week being planned," not
  "which block is active today" — and it can only vary **day-scoped numbers**
  hydration already computes per day, never the whole-week config a preset
  replaces once before generation. This is why `design-01` amended itself to
  remove `preset` from a block's field list (§4.1a) — pinning a preset
  mid-week would require the merged config to be two different objects
  inside one `default_week_spec` call, which nothing in this codebase does
  and nothing here should start doing.
- **A block needs a successor.** `docs/rapid-weightloss.md`'s adipostat/
  proteinstat mechanism (§4.7) makes the *end* of a restriction block the
  highest-risk moment in the protocol — lean mass lags fat mass on the way
  back, and a block that just ends and hands you back to `default` is the
  shape most likely to end above where it started. The successor is
  required, and skipping it takes a recorded, explicit override
  (`skip_transition: true`), never a silent absence.

## Scope

This prompt owns:

- `config/blocks.json`: a typed, dated record with a **fixed field list**,
  its own supplemental read/write path, and load-time validation (overlap
  refusal, the successor requirement, no arbitrary keys, no `preset` field).
- Mid-week resolution: which block covers each day of the week being
  planned, threaded into `hydrate_dynamic_targets` so a block's `diet_styles`
  activation and `target_rate_kg_per_week` bind on exactly their days and
  nowhere else.
- The protein floor as a block property: resolved once when the block
  starts, frozen with provenance, and reported (never silently dropped) when
  the active preset's `week_defaults` cannot carry it.
- The required successor: a restriction block's `next_block`/
  `skip_transition` state, and the `transition` block type's specified ramp.
- Surfaces: a Settings Blocks panel, the "pinned by a block" state on the
  existing weekly-pick control, the telemetry header naming the active block
  (and giving `training_intent`/`peak_day` their first human reader), and the
  review dialog marking which days are in-block.

This prompt does **not** own, and must not touch:

- **§7's NOVA/processing axis** — `allowed_nova_groups` presettability, the
  selection-time eligibility filter closing the "a NOVA-4 dish admitted under
  `comfort` survives into a strict week" hole, the NOVA-4 count cap, or any
  `lazy`-preset dial (prep ceilings, the long-cook threshold, the dinner
  variety/portion-density numbers). Every one of those is a **preset**
  concern under `design-01` §3.4a's audit, not a block concern — a block's
  field list is fixed and does not include a NOVA rule. Filed as follow-up
  work; do not fold it in here because it happens to live in the same design
  document.
- Anything reading `training_intent` or `peak_day` beyond **displaying**
  them. Storing them ahead of their reader is deliberate (§4.1a), but the
  reader is Arm E (`design-06`/`PROMPT-15`), not this prompt.
- Automatic block *suggestion* ("you've plateaued, start a block"). That
  needs Arm D's outcome-measurement half.
- A general config editor, preset inheritance, or a block that sets a key
  outside its fixed field list. If it would let a block do what a preset
  does, it is the wrong shape — see "Why a block cannot pin a preset," §4.1a.

## What to do

### 1. The typed block, `config/blocks.json`, and its own supplemental store

Add a new pure module (parallel to `src/presets.py`, not inside it — a block
is a different object with different rules, and the two should not import
each other's internals beyond what `day_scoped_entries` already generalizes).
It owns:

- The fixed field list from `design-01` §4.1, **with the two 2026-09-01
  corrections already folded in**: `body_goal` and `fitness_goal` are both
  **required** (never collapse them into one goal field — a fat-loss body
  goal and a VO2-max fitness goal actively conflict, and the whole point of
  two fields is that the app can see the conflict and say so rather than
  silently serving one); `preset` is **absent** from the field list, and a
  block naming it is a load-time failure, not an ignored key. The remaining
  fields: `name`, `starts_on`/`ends_on` (ISO dates, end not before start),
  `diet_styles`, `protein_floor` (`{multiplier, basis}`), `target_rate_kg_
  per_week`, `training_intent`, `peak_day`, `notes`, plus whatever the
  successor mechanism in step 4 needs.
- A `BlockFailure`-shaped pure validator (mirror `presets.PresetFailure`'s
  `problem`/name-carrying shape) checking: every required field present and
  typed; **two blocks covering one date fail, naming both blocks and the
  overlapping range** — never pick a winner; a `preset` key present anywhere
  on a block fails, naming the block; the successor requirement from step 4.
  An expired block (its `ends_on` in the past) is never a validation
  problem — it stays on file, inert, per §4.3.
- `active_block(blocks, on_date)` (or the per-day equivalent step 2 needs) —
  a **date parameter**, with a convenience wrapper defaulting to the clock,
  matching the existing seam in `build_rejection_rule(today=...)` and
  `select_favorite_assignments`. Do not read the clock inside the pure
  function itself.

In `src/repository.py`, add the supplemental read/write pair this needs,
following `load_presets_config`/`save_presets_config`'s shape and reasoning
exactly: **not** added to `CONFIG_FILES`, **not** a key on `AppConfig` (a
missing `blocks.json` means no active block, byte-identical to today), and
**not** written through `save_config_keys`, which raises on every key outside
`CONFIG_KEY_OWNER`. Compare against the freezer store's upsert-by-id shape too
before choosing one — a block is a stable-identity record like a lot, but
`presets.json`'s whole-file merge is the one `design-01` §9.3 names
explicitly ("through the supplemental write path PROMPT-8 §1a adds"). Pick
the one that lets a hand-added block survive an app-driven write untouched,
the same tolerance `_save_presets_config` gives a hand-added preset.

New `tests/test_blocks.py`: round-trip; missing file is no active block;
overlap fails naming both blocks; a `preset` key fails naming the block; an
expired block still loads and resolves inert; `active_block` takes the date
as a parameter and a fixture may call `date.today()` but no assertion may
depend on what it returned (CLAUDE.md's Tests section — this bit two tests
already, in `test_ui_state.py`'s day-picker fixtures).

### 2. Mid-week resolution feeds `hydrate_dynamic_targets`, and only there

A block cannot pin a preset (§4.1a) because a preset is consumed once, before
hydration, by `default_week_spec`/`resolve_auto_choices`/`pick_cuisine_
blocks`, while every field a block is allowed to carry is a **per-day
number** `hydrate_dynamic_targets` already computes inside its own loop. So:

- Resolve, once per week generation/preview, which block (if any) covers
  each weekday of the grid being planned — using `WeekPlan`/`WeekSpec`'s
  existing `week_start_date`/`week_date_range` to map weekday names to
  calendar dates, the same mapping `design-01` §5 says already exists and
  "just has to be threaded to the layer."
- A block's `diet_styles` union into that day's active styles for exactly
  the days it covers. Reuse `day_scoped_entries`/`active_diet_styles` — do
  not invent a second parser for "which days does this bind on." A day with
  no covering block resolves to the preset's `active_diet_styles` exactly as
  today.
- `target_rate_kg_per_week` feeds the deficit slide (`nutrition_engine.
  calculate_dynamic_deficit` or wherever the per-day deficit is actually
  computed — inspect it, the rate may need to become a parameter rather than
  a profile-wide constant) for exactly the days the block covers. It **never
  writes `weekly_schedule` calories directly** and **never touches
  `target_modes`** — a block supplies what the owner aims at, not who owns
  the number (§4.2). A day whose calorie macro is already stated
  (`target_is_stated`) is not touched by this at all, same rule the diet-
  style ceiling already follows.
- This must survive being called twice on the same config (the UI's live
  preview, then generation) without drifting — the exact idempotence lesson
  CLAUDE.md records against the training-uplift bug that took a 2200 kcal
  override to 1850. If a block's contribution needs replaying rather than
  recomputing, follow the `training_uplift` precedent already in
  `hydrate_dynamic_targets`, not a second ad hoc mechanism.
- `PlannerState.planning_config()` must see the resolved block exactly as it
  sees the resolved preset, so the header previews what the run will
  actually aim at — CLAUDE.md's "a number the UI displays and a number a run
  plans against must come from one call, not two."

Extend `tests/test_planner_dynamic_targets.py`: a block spanning Mon–Thu of a
Mon-start week caps/adjusts exactly those four days and leaves Fri–Sun
untouched, asserted across **both** hydration passes; a day with no covering
block is byte-identical to no `blocks.json`; a stated target is never moved
by a block.

### 3. The protein floor as a resolved-once, frozen block property

`protein_floor: {multiplier, basis}`, `basis` ∈ `target_weight` (today's
144 g behavior), `ffm`, `current_weight`, or a bare `grams` figure typed by
hand.

**Resolve once, when the block starts, and write the resolved grams back
onto the block record** — this is the load-bearing decision in `design-01`
§6, not an implementation detail. An FFM basis reads the scale's BIA
body-fat reading, which CLAUDE.md already documents as noisy (4–8% MAPE);
re-deriving it on every hydration pass would move the day's protein target
on instrument noise mid-block, which breaks the standing invariant that the
floor never slides within a block. Follow the pattern `PlannerState.
set_target_mode` and `accept_training_proposal` already use for "resolve
from the engine once, then persist as a standing fact" — inspect both before
deciding where the resolve-and-write step lives; do not invent a third
shape. Keep the multiplier and basis beside the resolved grams as
provenance (the same discipline `basis["tdee_source"]` already gives the
adaptive-TDEE pick) — "165 g" alone is unauditable.

In-block days read the frozen figure instead of `target_weight_kg ×
protein_multiplier`; out-of-block days are unaffected. A new weigh-in mid-
block must not move it.

**The unaffordable-combination check.** `apply_protein_floor` is already
tight (CLAUDE.md: 144 g over four meals against a 35 g `min_meal_protein_g`
floor leaves 4 g of slack for the whole day). A block raising the floor
while the active preset still cooks four meals a day can be arithmetically
unsatisfiable. Report it, do not correct it — this codebase's standing
answer everywhere numbers fail to reconcile (`cap_to_weighted_share` drops
its surplus, an overspent `meal_overrides` floors the rest at zero and
warns, `apply_protein_floor` itself does nothing and logs). Check the
resolved floor against `min_meal_protein_g` × the meals the day actually
cooks, at block-start resolution time, and warn naming both figures.

Extend `tests/test_planner_dynamic_targets.py` and/or
`tests/test_nutrition_engine.py`: each basis resolves the expected grams;
resolution happens once and a second call/weigh-in does not change it; the
unaffordable-combination warning fires and names both numbers.

### 4. The required successor and the `transition` block type

A restriction block (one declaring a `protein_floor` and/or
`target_rate_kg_per_week` that increases the deficit — decide the exact
predicate during implementation, but it must not be "every block") must
carry either a named successor block or `skip_transition: true`. Neither
present is a **load-time failure** naming the block — the two states must
never look the same on disk (`design-01` §4.7: "they must not look the same,
or the app cannot tell 'I know what I'm doing' from 'nobody noticed.'").

A `transition`-type block is a specified algorithm, not free-form data,
from `docs/rapid-weightloss.md`: **+100–250 kcal every 1–2 weeks**, protein
held constant at whatever floor was frozen going in (the anchor the research
names — added calories come from carbohydrate and fat, not protein), and
**hold the ramp for 7–14 days if the 7-day average weight rises past a
threshold**. The ramp is a *rate*, not a fixed target, so — like the protein
floor above — it must produce the same numbers on a second hydration pass
for the same inputs; do not let it silently advance twice in one calendar
day because the UI repainted.

Implement the ramp in `src/nutrition_engine.py`, and wire its calorie
contribution into step 2's per-day feed the same way `target_rate_kg_per_
week` is fed — through the deficit, never by writing `weekly_schedule`
directly.

Extend `tests/test_nutrition_engine.py`: the ramp advances only on its
schedule; a weight rise past the threshold holds it for the stated window;
protein is unaffected by the ramp.

### 5. Surfaces

Load the `ui-work` skill first; what follows is placement and scope, styling
comes from the skill.

- **Settings — a Blocks panel.** List, the active block marked, create/edit/
  end-early. Copy `ui_review.training_editor`'s list-of-records convention:
  selects/numbers/text only, add/remove repaint, field edits keep focus, no
  drag ordering. Persist through step 1's supplemental write path — never
  `save_config_keys`.
- **The existing weekly-pick control** (`design-01` §9.1, already built)
  gains the in-block state: during a block, show the pinned preset and name
  which block pinned it, with an explicit "end this block early" — never a
  disabled control. A week outside any block keeps its ordinary pick,
  defaulting to last week's, exactly as it does today.
- **Telemetry header** (`src/ui_telemetry.py`) names the active preset
  (already shown) and now the active block, and gives `training_intent`/
  `peak_day` their first human reader — CLAUDE.md's rule that a field
  nothing displays and nothing consumes should not ship applies to both.
- **Review dialog** marks which days of the grid are in-block, since a
  boundary can fall mid-week and the target curve visibly steps at it.
- **No new colour.** A block is a label; glyph-and-wording is the route
  `sync_freshness` and the adherence marks already took.

Extend `tests/test_ui_state.py` for whatever moves into `PlannerState`
(mirroring how the preset picker's state lives there, not in the widget
module). Visually verify the Blocks panel, the in-block weekly-pick state,
the header, and the review dialog's in-block marking at 1280px and 1440px.

## Acceptance

- **Gates:** `presets.py`'s resolver, `day_scoped_entries`, and
  `hydrate_dynamic_targets`'s existing structure are imported and extended,
  never duplicated. `WeekPlan.preset` and its recording in `meal_history.json`
  already exist — confirm this, do not rebuild it.
- No `blocks.json` → byte-identical to today: same merged config, same
  targets, same generated week.
- Overlapping blocks fail at load, naming both blocks and the overlapping
  date range.
- **A block carries no `preset` field.** A block naming one fails at load
  rather than being silently ignored.
- A block spanning Mon–Thu of a Mon-start week caps/adjusts exactly those
  four days and leaves Fri–Sun untouched, across **both** hydration passes
  (the UI's live preview and generation).
- A block's protein floor is resolved once and does not move when a new
  weigh-in lands mid-block, whichever basis it names.
- A block whose floor the active preset's `week_defaults` cannot carry warns
  at load, naming both the resolved floor and the shortfall.
- A restriction block with neither a named successor nor `skip_transition:
  true` fails at load, naming the block. A `skip_transition: true` block and
  one with no successor are distinguishable on disk and never conflated.
- A `transition` block's ramp advances only on its stated schedule, holds
  when the 7-day average weight rises past threshold, and never moves
  protein.
- `PlannerState.planning_config()` sees the resolved block, so the header
  previews exactly what the run will aim at.
- An expired block is inert and still present on disk — never auto-deleted.
- A block never writes `weekly_schedule` calories directly and never touches
  `target_modes`.
- Settings' Blocks panel persists through the new supplemental write path,
  not `save_config_keys`. Field edits keep focus; add/remove repaint.
- The weekly-pick control shows the pinned preset and naming block during a
  block, with a working "end early," and its ordinary behavior outside any
  block is unchanged.
- No new colour anywhere in this prompt's surfaces.

New `tests/test_blocks.py`. Extended: `tests/test_planner_dynamic_targets.py`
(the frozen floor, the mid-week boundary across both hydration passes),
`tests/test_nutrition_engine.py` (the transition ramp), `tests/test_ui_state.py`
(the header and weekly-pick state seeing the block). Run the full suite.

## Do not

- Give a block a `preset` field, or any field outside its fixed list. If a
  block needs to do what a preset does, that is evidence the two objects
  have collapsed into one — refuse the temptation, do not add the escape
  hatch (§4.1a's own argument turned on itself).
- Let a block write `weekly_schedule` calories/protein directly, or flip a
  `target_modes` entry. It supplies what the owner aims at, never who owns
  the number.
- Re-resolve a block's protein floor on every hydration pass. Resolve once,
  freeze, persist.
- Auto-delete an expired block, or pick a winner between two overlapping
  ones. Refuse the overlap at load; keep the expired one on file.
- Fold `design-01` §7 (NOVA presettability, the selection-time eligibility
  filter, the NOVA-4 count cap, the `lazy`-preset dials) into this prompt.
  It is a preset concern, scheduled separately.
- Build anything that *reads* `training_intent` or `peak_day` beyond
  displaying them, or anything that *suggests* starting a block.
- Add a general config editor, preset inheritance, or a second validator
  disagreeing with the one the loader uses — one function, two
  presentations, exactly as `presets.py` already establishes.
