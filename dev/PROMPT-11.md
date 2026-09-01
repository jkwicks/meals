# PROMPT-11 — The declared freezer ledger, and food crossing between weeks

**Not queue-safe** (in `dev/`; `claude-queue.sh` globs
`.prompts/prompt-*.md`). It writes observed household state, touches
`ui_*.py`, and its acceptance is partly visual. A human must confirm that a
planned surplus never becomes stock without an explicit click.

**Delivery tier 6, before PROMPT-12. Depends on PROMPT-10.** Do not start
while storage life is still one global `fridge_safe_days`: this feature extends
how far food can reach, and doing that before per-dish windows makes the live
rice/pasta defect worse. `PROMPT-10`'s freezer-window resolver is an input to
this prompt, not logic to copy.

Cold session. Read CLAUDE.md's **"Storage goes through an async repository"**,
**"Leftovers can't outlive the fridge"**, and the `ui-work` skill before
editing any `ui_*.py`. Then read `design-04` **§1–§7 and §8 acceptance**;
`design-05` **§2a and §6** own every storage-window number and the distinction
between fridge safety and freezer quality.

## The requirement

`SlotSpec.extra_portions` already buys and cooks spare portions, but nothing
receives them. Separately, there is no way to say what is actually in the
freezer or to use it in a later week without shopping for it again.

Build the missing consumer as a **declared, confirmed list of observed stock**.
It is not an inferred inventory system:

- the user states what is in the freezer;
- the list is confirmed before generation;
- planning a draw does not decrement it;
- planning surplus does not add it;
- only an explicit "this is now frozen" action writes a new lot.

That last division is load-bearing. `WeekPlan` holds intentions;
`data/freezer.json` holds food that exists.

## Scope

This prompt builds the freezer substrate in `design-04`:

1. movable prep-day resolution;
2. `data/freezer.json` and repository operations;
3. the typed lot and its snapshots;
4. manual and recipe-card capture;
5. confirmation of planned surplus after cooking;
6. freezer-origin draws, including macro and shopping behavior;
7. age/quality warnings and the review-dialog editor.

It does **not** build the preset-authored `week_shape` record editor or applier.
That is PROMPT-12. Provide the pure operations PROMPT-12 will call, but do not
smuggle a second shape declaration into this change.

## What to do

### 1. Resolve prep day from reality, in `week.py`

Implement `design-04` §7's three-way split:

- whether a week includes prep is a preference (the existing
  `enable_sunday_prep` until PROMPT-12 makes it preset-authored);
- where prep lands is derived from `base_schedule`/`location_rules`;
- what is prepared remains separate.

Walk backward from the day before `spec.days[0]`, over **N−1 and N−2 only**,
and take the first day whose location allows prep. Add
`location_rules.<location>.allows_prep_session`; when absent, fall back to that
location's `allows_long_cook`, and when there is no usable location rule treat
Home as available. If neither candidate works, return no prep day and a reason
the UI can say. Never silently move it earlier than N−2.

Make the resolved prep date/day one value every consumer shares. Moving prep
from Sunday to Saturday must move the batch food-safety origin, prep column,
storage wording and surplus lot date together. Do not leave `PREP_DAY_INDEX`
as a second hard-coded answer in a caller.

### 2. Add `FreezerItem`, with stable identity and snapshots

Follow amended `design-04` §2.1:

```text
FreezerItem
  id                 stable generated id; two lots of one dish remain distinct
  label              user-facing free text
  portions           integer meal portions remaining by declaration
  cooked_on          required ISO date
  frozen_on          required ISO date
  storage_class      snapshot at freeze time
  per_serving        snapshot of MACRO_KEYS at freeze time
  recipe_id          optional provenance only
```

Use a Pydantic model in a non-UI module. Validate dates and positive portions.
Reject `frozen_on < cooked_on`. Use PROMPT-10's storage-class vocabulary and
freezer-window resolver; do not recreate either table.

The snapshots are the source of truth. A later catalog edit must not alter the
macros or class of food already frozen. For a legacy/manual row missing a
snapshot, a catalog recipe may supply it as an explicitly **inferred** fallback;
missing macros otherwise mean zero, visibly, never a guess. A missing class
uses PROMPT-10's shortest freezer window.

### 3. Put observed stock behind the repository

Add explicit async operations to `PlanRepository` and
`LocalJSONRepository`, following the adherence/catalog shapes:

```python
async def load_freezer(self) -> list[dict]
async def save_freezer_item(self, item: dict) -> None
async def delete_freezer_item(self, item_id: str) -> None
```

Add any narrowly named update operation the row editor needs rather than
exposing a general file rewrite from a widget. The local path is
`data/freezer.json`, derived from `data_dir`; a missing file returns `[]`.
Upsert by stable `id`, preserve unrelated rows, and use the repository's one
JSON write path. No widget or planner function opens the file directly.

`data/freezer.json` is app-written observed state, not `config/`; do not add it
to `CONFIG_FILES` and do not route it through `save_config_keys`.

### 4. Load one per-tab snapshot and confirm it before a run

`PlannerState.load()` reads the freezer once, validates rows, and keeps editable
rows on state. Put a **Freezer** record editor in the review dialog, copying the
pantry/training editor convention:

- add/remove refresh the list;
- field edits do not repaint the focused row;
- save failures are shown before a run;
- stale/undateable/inferred rows are visible rather than dropped;
- the list is explicitly confirmed as part of Generate.

Confirmation is reconciliation, not an assertion that the app's previous
count was right. Editing and confirming writes exactly what the user says is
present. Generation may reserve portions within its in-memory snapshot, but
**never decrements the stored list**.

### 5. Add the two capture routes that make the ledger usable

**Recipe card — "Send to freezer".** On a generated cook card, pre-fill label,
recipe id, recipe storage class and per-serving macros. Derive `cooked_on` from
the cook event's real origin — prep day for a prepped-ahead batch, its grid date
otherwise. Default `frozen_on` to the action date but keep it explicit/editable.
Ask for portions, validate, and persist on confirmation. This is an observed
action and writes on click, like adherence; it is not a staged grid edit.

**Planned surplus — confirm after cooking.** Existing `extra_portions`, and
later PROMPT-12's `freeze_portions`, remain only planned surplus on the
`WeekPlan`. Surface each as a pending lot with its derived total and a
"Record frozen portions" action. Only that action snapshots the generated
recipe and writes the lot. A generation finishing successfully must create
zero freezer rows by itself.

Both routes go through one state/repository operation so they cannot disagree
about dating, snapshots or ids.

### 6. Add freezer-origin leftovers without a fourth mode

Add `LINK_ORIGIN_FREEZER` to `SlotSpec.link_origin`. A freezer draw remains
`mode == MODE_LEFTOVER`; its `source` is the freezer lot id rather than a slot
id. This preserves the cook/leftover/skip vocabulary and makes provenance
explicit.

Resolve draws oldest suitable lot first by `frozen_on`, with stable `id` as a
tie-breaker. Multiple draws in one generated plan use an in-memory remaining
count so one two-portion lot cannot satisfy three slots, but this reservation
never writes back to `freezer.json`.

Thread the distinction through the existing readers:

- `validate_week` permits an external source only when
  `link_origin == LINK_ORIGIN_FREEZER`; ordinary user/location/batch leftovers
  still require a real earlier cook slot;
- `WeekPlan.day_slot_macros` and `PlannerState.slot_views` resolve a freezer
  draw from its snapshotted per-serving macros;
- a draw adds no `CookEvent` and therefore nothing to a shopping list;
- a missing/insufficient lot warns and leaves an honest visible shortfall; it
  does not fail the whole plan or invent food;
- a lot beyond PROMPT-10's freezer quality window warns on the lot and draw but
  stays selectable/present. Say **past its best**, not unsafe.

If the cleanest representation requires `WeekPlan` to snapshot the lots used
by that plan, do so: a cached plan must still render its macros and label after
`freezer.json` is edited. Do not make a historical plan's truth depend on the
current mutable ledger.

### 7. Keep the UI behavior inside existing contracts

Load the `ui-work` skill. Use glyph and wording rather than a new color. Reuse
the existing `ac_unit` freezer glyph and record-list surfaces. Keep logic worth
testing in `ui_state.py` or a pure helper, never in a widget closure.

The recipe card and Daily View must say the lot label, its frozen date/age in
whole days, and whether its macro/class data was snapshotted or inferred when
that distinction matters. No surface prints storage hours.

## Acceptance

- **Gate:** PROMPT-10 is present. The implementation imports its storage
  resolver; no second fridge/freezer window table appears.
- **No `freezer.json` is byte-identical behavior.** Same grid, generated
  recipes, targets and shopping lists as before; a missing file is `[]`.
- Repository round-trip tests cover add, update, delete, stable ids, two lots
  of one recipe, missing file and preservation of unrelated rows.
- A lot snapshots storage class and per-serving macros. Editing/deleting the
  source catalog recipe does not change a cached plan or existing lot.
- A manual lot with no usable macros contributes 0 and produces a visible
  shortfall; it never borrows a guessed meal average.
- A draw consumes one meal's household portions from the run-local snapshot,
  contributes the snapshotted macros, creates no cook event, and adds nothing
  to shopping.
- Two draws cannot over-allocate one lot within a plan. The stored portions are
  nevertheless unchanged after generation.
- A freezer link outside the week's slot ids is valid only with
  `LINK_ORIGIN_FREEZER`; a typo'd ordinary leftover remains invalid.
- A missing lot or insufficient portions warns and the plan still opens.
- A lot beyond its freezer window says **past its best** and remains in the
  file. A cook-to-freeze breach uses fridge-safety wording; the two messages do
  not collapse into one.
- "Send to freezer" derives a prep batch's `cooked_on` from resolved prep day,
  not the anchor's Monday date, and snapshots the recipe on confirmation.
- Generating a plan with `extra_portions > 0` writes **nothing** to
  `freezer.json`; clicking "Record frozen portions" writes exactly one lot and
  a repeated click cannot duplicate it silently.
- With prep enabled and N−1 unavailable but N−2 available, every prep-day
  consumer moves to N−2. With neither available there is no prep day and the
  reason is shown.
- Field edits in the freezer editor do not steal focus; add/remove do refresh.
  Visually check the review dialog and cards at 1280px and 1440px.

New: `tests/test_freezer.py`. Extend `test_week_mechanics.py` for the external
leftover validation and `test_ui_state.py` for draw macros, pending surplus and
lot editing. Run the full suite; the repository interface change means a fake
implementing `PlanRepository` can fail far away from these tests.

## Do not

- Start before PROMPT-10 or duplicate its window logic.
- Auto-seed stock when generation finishes, or auto-decrement it when a draw is
  planned. A plan is not an observation.
- Make `recipe_id` the live source of a lot's macros or storage class.
- Add a fourth `SlotSpec.mode`.
- Add freezer ingredients to shopping.
- Auto-remove old food.
- Model freezer capacity or physical space.
- Build `week_shape`, its preset editor, or its batch declarations here.
- Let a cooking week assign its surplus to a named day in a future week. The
  freezer is pull-based: the later week decides what it draws.
