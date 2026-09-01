# PROMPT-12 — Declarative `week_shape`: apply the grid, never search for it

**Not queue-safe** (in `dev/`; `claude-queue.sh` globs
`.prompts/prompt-*.md`). It replaces the two controls that currently shape the
whole grid, touches the preset editor and NiceGUI review surface, and has visual
and byte-identical acceptance.

**Delivery tier 6, immediately after PROMPT-11. Depends on PROMPT-10 and
PROMPT-11**, and on the preset container/editor from PROMPT-8/9. Per-dish
storage windows must bind before this extends batch reach; the freezer ledger
must exist before a `freezer_draws` record can mean anything.

Cold session. Read CLAUDE.md's **"Batch cooking on purpose: the two prep
toggles"**, **"Some slots are decided before the model is called"**, and the
`ui-work` skill. Then read `design-02` **in full**, `design-03` **§4 and §6–§8**,
and `design-04` **§4, §6 and §7**. PROMPT-10 owns storage limits; PROMPT-11 owns
lots, snapshots, prep-day resolution and freezer-origin leftovers.

## The requirement

The app currently hard-codes two batch shapes in `ui_generation.py`: bulk prep
across lunches and long cook across dinners. That creates two problems:

1. the CLI/API do not receive the same structural decision as the UI;
2. a preset cannot state no batches, more than two batches, exact eating days,
   planned surplus, or a freezer draw.

Replace that with a declarative list of records. The governing invariant:

> **A week shape is applied, never searched.**

If implementation code chooses among candidate cook/eating days, pushes a
batch later, or negotiates one batch around another, it has rebuilt the removed
bug. The declaration either names a coherent grid or validation refuses it.

## Scope

This prompt owns:

- moving the current batch applier into deterministic shared planning code;
- the `week_shape` schema and shared validation;
- applying declared batches and freezer draws;
- preset-editor records and on-demand preview;
- migration of today's two toggles into data;
- retiring the old UI-only mechanism.

It does not add arbitrary portion totals, drag/drop, direct grid manipulation,
recipe choice, food styles, targets or cross-week push assignment.

## What to do

### 1. First move `apply_batch_selections` into `week.py` unchanged

Do this as a pure refactor before introducing the new schema. Both the CLI/API
and NiceGUI must call the shared function at the same deterministic point:
after location modes, before `validate_week`, auto choices and the first model
call.

Preserve and assert the current output before changing it: with the legacy
controls/config, bulk prep anchors `Monday:lunch`, long cook anchors
`Monday:dinner`, each claims the same front-of-week slots PROMPT-10 now permits,
and repeated runs are byte-identical. This step closes the existing front-end
asymmetry even if the rest fails.

Do not move NiceGUI notifications into `week.py`. Return structured warnings
or results and let each caller present them.

### 2. Add a typed `week_shape` declaration

Use `design-02` §4's list-of-records shape:

```json
{
  "batches": [
    {
      "name": "sunday-soup",
      "meal_type": "lunch",
      "cook_on": "prep_day",
      "serves": ["Monday", "Tuesday"],
      "freeze_portions": 4
    }
  ],
  "freezer_draws": [
    {"meal_type": "lunch", "day": "Thursday"}
  ]
}
```

Add `week_shape` as a core top-level key owned by `config/week.json`, declared
on `AppConfig` and in `CONFIG_FILES`. It is ordinary resolved planning data,
not a supplemental-file key: the base config needs a behavior when no preset
is active, and PROMPT-8's preset resolver only permits paths whose first
segment belongs to `CONFIG_FILES`. A preset then overrides `week_shape` (or a
supported leaf beneath it) through the same typed leaf-path mechanism as every
other preset value. Do not teach the resolver a one-off exception for this
field.

`cook_on` is `"prep_day"` or a weekday. `serves` names eating days, not a
count. `freeze_portions` is surplus on top of those claims, maps to the
existing `SlotSpec.extra_portions` arithmetic, and defaults to zero. A batch
must have at least one `serves` entry because a cook event nobody eats produces
no shopping event; a freezer-only pot is represented by one tasting day plus
surplus.

Use PROMPT-8's shared resolver and PROMPT-9's read-modify-write editor behavior.
During migration, absence from an older base config means today's legacy
behavior; the shipped `week.json` receives the explicit current-behavior
declaration before the legacy fallback is retired. An explicit
`{"batches": [], "freezer_draws": []}` means no automatic batches or draws and
every otherwise-cook slot cooks.

### 3. Validate before save and at load, with one function

Extend the shared preset resolver rather than adding a widget-only validator.
Return structured failures naming preset, batch/draw and field. Check all of
`design-02` §7 against the **resolved** base config and resolved prep day:

- unique, non-empty batch names;
- known meal type and weekday names;
- `cook_on` is prep day or a real day in the week;
- `serves` is non-empty, ordered in week order, contains no duplicates and is
  contiguous for refrigerated leftovers;
- no two batches claim one slot;
- no batch and freezer draw claim one slot;
- the first served slot can hold the cook anchor after location rules;
- subsequent served slots are valid leftover targets;
- no user-origin link is overwritten;
- each served day is within PROMPT-10's conservative required storage window
  from its real cook origin; a gap or out-of-window day must explicitly suggest
  a freezer draw rather than being silently shortened;
- a prep-day batch does not serve the final grid day;
- `freeze_portions` is a non-negative integer;
- a freezer draw with no suitable run-time lot is a **warning**, not a load
  error, because stock is observed mutable state.

The editor calls this before save and leaves `presets.json` byte-identical on
failure. Hand-edited invalid data fails at load through the same function.

### 4. Apply records literally in `week.py`

Write one pure applier that receives a fresh/location-resolved `WeekSpec`, the
resolved `week_shape`, config, prep-day resolution and PROMPT-11's freezer
snapshot. It returns the updated spec, batch anchors, selected freezer lots and
structured warnings.

For each batch:

- anchor the recipe on the first named `serves` slot;
- link exactly the remaining named slots to it with
  `LINK_ORIGIN_BATCH`;
- stamp `freeze_portions` onto that anchor's `extra_portions`;
- identify prep-day batches through the same anchor data
  `prep_day_batch_slot_ids`/`is_prepped_ahead` consume;
- never call `spread_batch` to choose a day. Its low-level claiming/linking
  rules may be reused only if they can be driven by exact named targets without
  a candidate search.

For each freezer draw, call PROMPT-11's resolver: oldest suitable stock first,
reserve household portions only in the run-local snapshot, and create a
`MODE_LEFTOVER`/`LINK_ORIGIN_FREEZER` slot. Missing stock warns and leaves a
visible shortfall; it does not convert the slot to a model-generated cook and
does not fail the run.

Apply location facts first. A location-origin leftover may be repointed where
the existing precedence permits; a user-origin link is never taken. Do not add
a second precedence system for declarations.

### 5. Keep planned surplus planned until PROMPT-11 confirms it

`freeze_portions` changes portions, recipe scaling and shopping through the
existing `extra_portions` path. It also creates a pending freezer item on the
saved `WeekPlan`, dated from resolved prep day for a prep batch and from the
declared cook day otherwise.

It does **not** write `data/freezer.json`. Reuse PROMPT-11's "Record frozen
portions" action to turn that pending item into observed stock after cooking.
The displayed total is derived:

```text
served slots × household servings + freeze_portions = total pot
```

Never add a `total_portions` field beside it.

### 6. Add the preset-editor records and an on-demand preview

Load the `ui-work` skill. `week_shape.batches` and `.freezer_draws` are editable
record lists, copying `training_editor`:

- selects/numbers/text/multi-select only;
- add/remove repaint;
- editing a field does not repaint the focused list;
- no drag ordering, because record order has no semantic meaning;
- derived total portions are displayed, never editable.

Preview on a button, not live. It runs the same pure validator/applier against
the current draft and shows the resulting slot relationships plus warnings.
It must not mutate `PlannerState.spec`, repaint the 28-card canvas, save the
preset, reserve real stock or call a model. A preview that uses a simplified
second applier is not a preview of what Generate will do.

### 7. Retire the two toggles and migrate today's behavior into data

Once the declaration is active, remove Bulk prep and Long cook from the review
dialog and remove `bulk_prep_enabled`/`long_cook_enabled` as a second source of
grid truth. Remove the UI-only branch in `ui_generation.py`; every entry point
uses the shared applier.

Ship the current two-batch declaration in the **base `week.json`**, reproducing
today's behavior byte-identically, adjusted only for PROMPT-10's already-landed
per-dish safety windows. An empty `default` preset therefore still means the
base behavior, while a no-bulk preset explicitly overrides `week_shape` with
empty lists. Do not special-case a preset named `default`; it remains ordinary
data and the comparison baseline remains PROMPT-8's base config.

During migration, distinguish **absent** `week_shape` (legacy compatibility)
from an explicit empty one (turn all automatic batching off). Document when the
legacy fallback can be removed; do not leave both mechanisms indefinitely.

## Acceptance

- **Gates:** PROMPT-10's per-dish resolver and PROMPT-11's freezer operations,
  snapshots and external-link support are imported, not duplicated.
- Moving `apply_batch_selections` to `week.py` changes no output on its own and
  makes CLI/API/UI run the same structural pass.
- `week_shape` is owned by `week.json`, validated by `AppConfig`, and reachable
  through PROMPT-8's ordinary leaf-path resolver with no special case.
- The shipped base current-behavior declaration produces the same batch anchors,
  links, portions, prompts and shopping list as the legacy two toggles, subject
  only to PROMPT-10's already-shipped safety correction.
- An explicit empty shape creates no automatic batch links, surplus or draws.
  Absence follows the documented migration fallback.
- The same valid shape applied twice to a fresh spec is byte-identical. No
  previous run's chosen anchor can freeze the next run.
- Four batch records create four independently named dishes; there is no
  separate count that can disagree with the list.
- `serves: []` is refused, naming the missing shopping/cook-event consequence.
- A collision names both records and the slot. A gap/out-of-window serve is
  refused with a freezer-draw correction, never shortened or searched around.
- Location-origin links retain existing precedence; user-origin links are
  never overwritten.
- A declared freezer draw contributes snapshot macros and no shopping. Two
  draws cannot over-allocate one lot within a run. Missing stock warns and the
  plan still opens with an honest shortfall.
- `freeze_portions` increases `portions_for`, recipe scaling and shopping by
  exactly that surplus. It writes no stock until the user invokes PROMPT-11's
  confirmation action.
- A prep-day surplus uses resolved prep day for `cooked_on`, not its first
  eating day. Moving prep N−1 → N−2 moves its safety span and pending-lot date
  together.
- Invalid editor input does not change `presets.json`. A hand-edited copy fails
  at load through the same structured validator.
- The preview calls no model, writes no file, mutates no live spec, and returns
  the same grid/warnings Generate subsequently uses.
- The Bulk prep and Long cook controls and their state fields are gone after
  migration; grep finds no active second mechanism.
- Field edits keep focus; add/remove refresh; visually verify the editor and
  preview at 1280px and 1440px. Use no new color.

Add focused tests (a new `tests/test_week_shape.py` is appropriate), and extend
`test_week_mechanics.py`, `test_config_layout.py`, and `test_ui_state.py` where
their existing invariants live. Run the full suite and exercise one generation
through both the CLI and NiceGUI using a cached/fake model path where possible;
the purpose of moving the applier is parity between entry points, so a unit test
of `week.py` alone is not enough.

## Do not

- Search for an anchor, prefer days, push a record later, or negotiate records
  around one another. Apply or refuse.
- Keep the legacy toggles as another way to shape the same grid.
- Add a fourth `SlotSpec.mode`.
- Add `total_portions`, a batch multiplier or a declared number of eating
  events. `serves` plus surplus are the inputs.
- Put food style, recipe choice, macros or targets in `week_shape`.
- Deep-merge records or make one preset inherit another.
- Build a clickable week-grid editor, drag ordering, or live per-keystroke
  preview.
- Let preview reserve stock or let generation decrement it.
- Write planned surplus directly into `freezer.json`.
- Let this cooking week name the future week/day that will eat its surplus.
