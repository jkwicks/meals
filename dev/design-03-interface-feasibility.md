# Design 03 — What the interface can actually do, and what that permits a preset to say

Status: **draft for approval.** Nothing built.

Written to answer a question that inverts the two designs before it:

> *"I need to understand what flexibility is possible with the interface, as I
> think this will drive what configuration will be allowed/possible with
> profiles… how hard is it to provide flexibility, show it in an interface,
> configure it or skip it, and support it in code. So the profile rules will be
> driven by what is possible — not necessarily what I want."*

That is the right instinct and it is the opposite of how `design-01` and
`design-02` were written. Both are schema-first: they describe an expressive
declaration and assume a surface can be found for it. **This document is the
constraint running the other way**, and where it disagrees with them, it wins.

Read it before finalising `design-01` §9.2 (the preset editor) and `design-02`
§4 (the `week_shape` schema).

**Addendum — exercise planning.** `design-06` deliberately stays inside this
document's six-shape vocabulary: movement constraints and gym programs are
editable record lists; movement/equipment fields are selects or multi-selects;
the active program is one select; applied constraints and progression evidence
are read views. Workout generation is button-triggered, never a live preview.
No new interaction shape is needed for `PROMPT-14` or `PROMPT-15`.

Verified 2026-09-01 against `main` at `418c223`, `nicegui` 3.16.0, and the
`ui-work` skill.

---

## 1. The governing rule

**UI cost does not track how expressive the schema is. It tracks how many
distinct *widget shapes* the schema needs.**

A preset with forty fields that are all numbers and selects is cheap. A preset
with four fields, one of which needs a new interaction, is not. So the question
"what can a preset say" reduces to "what shapes does this front end already
have", and the answer is **six**.

That reframing matters because it means expressiveness is nearly free in the
directions the app already goes, and expensive in exactly one direction — which
turns out to be the one `design-02` needs.

## 2. The vocabulary that exists

Counted across `ui_review.py` (the richest editing surface) and
`ui_settings.py`:

| # | Shape | Built with | Already used for |
|---|---|---|---|
| 1 | **Pick one of a fixed list** | `ui.select` | training day, session type, model, week-start day |
| 2 | **A number with bounds** | `ui.number` (`min`/`step`/`precision`, `debounce=350`) | duration, burn kcal, target macros |
| 3 | **Free text** | `ui.input` (`debounce=350`) | session time, pantry item |
| 4 | **A boolean** | `ui.switch` / `ui.toggle` | the two batch toggles, target modes |
| 5 | **An editable list of records** | the training-editor pattern | `training_schedule`, the pantry rows |
| 6 | **A read view** | `SURFACE_INSET` box, no inputs | sync status, location, adaptive-TDEE verdict |

**Shape 5 is the important one and it is fully worked.** `ui_review.py`'s
`training_editor` is a complete, shipped implementation of "a list of records
you can add to, edit and delete":

- iterate `enumerate(state.<list>)`, one bordered `SURFACE_INSET` card per row;
- shapes 1–3 inside the row for its fields;
- a `delete` icon button with an index-bound closure;
- one `training_field_handler(index, field)` factory for every field;
- **add and remove refresh; a field edit does not** — see §4.3.

Anything expressible as a list of records made of selects, numbers and text is
therefore a **copy of an existing pattern**, not new interaction design. That is
the single most useful fact in this document.

## 3. What is *not* in the vocabulary, and what each costs

| Shape | Cost | Notes |
|---|---|---|
| **Multi-select** | **XS** | `ui.select(..., multiple=True)` — verified present in `nicegui` 3.16.0. Not used anywhere yet, so it is new to *this app* but not new code |
| **Per-field validation** | **XS** | `ui.select`/`ui.number` take a `validation` argument in 3.16. Relevant to §4.2 |
| **Toggle chips** (7 weekday buttons) | S | Just styled buttons, but bespoke layout and state |
| **Reorder / drag** | M | No precedent anywhere in the app |
| **Direct grid manipulation** — click a cell to set its mode | **L** | The 28-card canvas is a real 2-D grid with a sticky gutter and documented sizing traps; making cells editable is its own project |
| **Any canvas / freeform editor** | L+ | No precedent, and the charts are ECharts read-only |

**The verdict that shapes everything else: `week_shape` must be expressible as
a list of records, because direct grid editing is an L on its own.** That is
not a compromise — a record with `meal_type`, `cook_on`, `serves` and
`freeze_portions` is exactly `design-02` §4's schema, and it lands on shape 5
plus one multi-select. Cost: **a copy of the training editor.**

## 4. Three constraints the interface imposes back on the schema

These are the parts that would otherwise be discovered late.

### 4.1 "Skip it" is free only if absence is designed to mean something

The brief asks how hard it is to *skip* a dimension. In this codebase the
answer is **nearly free, and there is an established rule for it**: an absent
value resolves to *the behaviour before that feature existed*. CLAUDE.md states
it for at least six features — an empty `whfoods.json` resolves to `[]` and the
brief says nothing; an absent `sourcing` block emits nothing; a missing
supplemental config file is `{}`; `long_oven_cook` defaults False;
`total_time_minutes` is None meaning *unknown, never 0*.

So the rule for every preset field:

> **Every field must have an "absent means X" answer, and X must be exactly
> today's behaviour.**

A field that cannot state its absent meaning is a field that forces every
preset to have an opinion about it — which is the rigidity this whole program
is trying to remove, reintroduced one key at a time. It also gives the byte-
identical acceptance test in `design-01` §10 and `design-02` §9 for free: the
empty preset is the identity.

**This is a schema constraint, not a UI one**, but the UI is where it pays: a
field with a real default renders as an *unset* control the user may ignore,
where a field without one has to be filled before the preset is valid.

### 4.2 A UI that writes config can produce invalid config — which is new

Today exactly two things write to `config/`: `set_target_mode` and
`accept_training_proposal`. **Neither can produce an invalid file** — one
writes an enum, the other a session dict already shaped like its neighbours.

A preset editor is different in kind: it writes arbitrary structure, and
`design-02` §7 wants seven load-time checks over it. The app's standing policy
is **fail loudly at load**, which for a CLI is right and for a UI-authored file
is a trap — a bad edit would mean the *next start* raises, and the surface that
caused it is gone.

So: **preset validation must be callable without a config load, and the editor
must validate before it saves.** Concretely:

- the validator is a pure function over a preset dict plus the base config —
  the same shape `AppConfig.diet_styles_are_known` already has, and the same
  reason it lives on the parent model: only something seeing both can check
  one against the other;
- the editor calls it on save, shows the failure, and **does not write**;
- load-time validation stays, unchanged, for hand-edited files.

`ui.select`/`ui.number` also take a `validation` argument in 3.16, which covers
per-field bounds cheaply; the cross-field checks (slot collision, fridge
window) need the function above.

**This is the one genuinely new piece of architecture the interface forces**,
and it is small — but it must exist before a preset editor ships, not after.

### 4.3 The focus-theft trap decides the record shape

From the `ui-work` contract: *"Refreshing a section that owns the focused input
steals the cursor."* The training editor's answer is that **a field edit
refreshes a narrow topic (or nothing), while add and remove refresh the
section** — and `day_target_row` goes further, being built once and mutated in
place.

Two consequences for `week_shape`:

- **Editing a batch's fields must not repaint the batch list.** Fine — the
  training editor already proves it, with `training_field_handler` writing to
  state and refreshing `"targets"` rather than `"training"`.
- **A live preview of the resulting grid is *not* free.** Showing "here is the
  week this preset produces" as you type would repaint the canvas per
  keystroke, which is both the focus-theft trap and a `"plan"` refresh — the
  most expensive topic there is, rebuilding 28 cards, the telemetry header and
  the shopping panel.

  So a preview is **on demand, not live** — the same call
  `estimate_burn`'s calculator button already makes, and for the identical
  reason CLAUDE.md gives: a live recompute means rebuilding the row that owns
  whichever input the user is mid-edit on.

## 5. What a preset can and cannot do with *numbers* — a hard limit from existing code

Worth its own section because it is the one place a plausible preset field is
**silently inert**, and it is not a UI issue at all.

`hydrate_dynamic_targets` **replaces** `weekly_schedule`'s `calories` and
`protein_g` with the engine's figures whenever that macro's `target_modes`
entry is `auto` — which is the shipped default for both. The `ui-work` contract
states the consequence: *"a widget reading the file is displaying a number
nothing plans from"*, and the live example is a config saying 1000 kcal on a
Thursday that every run plans at 1722.

So, for a preset:

| Field | Effect |
|---|---|
| `weekly_schedule.<day>.calories` | **inert** while calories are `auto` — overwritten before generation |
| `weekly_schedule.<day>.protein_g` | **inert** while protein is `auto` |
| `weekly_schedule.<day>.net_carbs_g` | **works.** Passed *into* the engine, never replaced — the week's real cycling lever |
| `weekly_schedule.<day>.meal_overrides` | **works.** Hand-written overrides *"stay verbatim — a pin is a fixed budget by definition"* |
| `meal_weights` | **works.** Where the day's energy sits across meals |

This produces a clean division that falls out of existing behaviour rather than
being invented:

> **A preset shapes the week's *distribution* — carbs, meal pins, meal weights.
> A block shapes its *level* — via `target_rate_kg_per_week` and the protein
> floor.**

A preset that tried to set the calorie level would either be inert or would
have to flip `target_modes` to manual, which `design-01` §4.2 forbids for a
block on the grounds that it silently changes *who decides* a number. The same
argument applies here, and this is the clearest statement of it: **the level is
the block's, the shape is the preset's.**

## 6. The feasibility map

Every candidate preset dimension, scored on all four axes the brief asked
about. "Skip" is §4.1 throughout — free where the field has an absent-meaning.

| Dimension | Widget | Code support | Configure | Total |
|---|---|---|---|---|
| `allowed_nova_groups` | multi-select (1–4) | already a config key; validator reads live config | trivial | **XS** |
| prep ceilings | number | `prep_limit_for` exists | trivial | **XS** |
| `meal_weights` | 4 numbers | exists | trivial | **XS** |
| `week_defaults` | 4 selects | exists | trivial | **XS** |
| `net_carbs_g` per day | 7 numbers | survives hydration (§5) | trivial | **XS–S** |
| `meal_overrides` | records, or 4×3 numbers | survives hydration verbatim | moderate | **S** |
| `active_diet_styles` | multi-select from catalog | **needs day-scoping** (PROMPT-2) | trivial | **S** |
| `meal_styles` per meal type | multi-select | exists | trivial | **S** |
| NOVA-4 count cap | number | new counter, mirrors `max_seafood_meals_per_week` | trivial | **S** |
| **`week_shape.batches`** | **list of records + 1 multi-select** | **new applier in `week.py`**; `spread_batch` supplies the linking | moderate | **M–L** |
| `week_shape.freezer_draws` | list of records | **needs the freezer ledger** + `LINK_ORIGIN_FREEZER` | moderate | **L** |
| `calories` / `protein_g` | — | **inert (§5). Do not offer it** | — | **excluded** |
| Direct grid editing | — | canvas is read-only by design | — | **L, separate project** |

**The shape of the answer: everything is XS–S except `week_shape`, and even
`week_shape`'s UI is a copy of the training editor.** Its cost is in `week.py`
and the freezer ledger, not on screen.

## 7. What this permits, and the two places it overrules the earlier designs

**Permitted, cheaply — build all of it:** NOVA groups, prep ceilings, the NOVA-4
cap, `week_defaults`, `meal_styles`, `meal_weights`, per-day carbs,
`meal_overrides`, diet styles. That is nine dimensions covering every example in
the brief except the batch shape itself, and all of it is shapes 1–4 plus a
multi-select.

**Permitted, at real cost:** `week_shape` — and the cost is honest, since its
UI is a known pattern.

**Two corrections back onto the earlier documents:**

- **`design-01` §9.2's editor list drops `weekly_schedule` as a whole and gains
  `net_carbs_g` and `meal_overrides` specifically** (§5). Offering a calorie
  field that the engine overwrites would be the exact bug the `ui-work`
  contract warns about, shipped deliberately.
- **`design-02` §4's schema is confirmed as a list of records and must stay
  one** (§3). Any future field that would need drag-ordering, a grid click or a
  freeform editor should be rejected on those grounds alone.

**One addition to both:** the validate-before-save function in §4.2. Neither
document has it, and a preset editor cannot ship without it.

## 8. Recommended build order — cheapest surface first

Deliberately front-loads the parts that need no new mechanism, so the preset
concept is usable long before `week_shape` lands.

| | Step | Gets you |
|---|---|---|
| 1 | Preset **selector** + read view of what the active preset overrode | The weekly pick, over hand-edited JSON. One select and shape 6 |
| 2 | Preset **editor** for the XS dimensions + the validator (§4.2) | Authoring a mode in the UI, for nine of eleven dimensions |
| 3 | Diet styles day-scoped (PROMPT-2), NOVA cap, prep ceilings | The `comfort`, `lazy` and `fast_800` presets working end to end |
| 4 | `apply_batch_selections` moved to `week.py` unchanged | `design-02` §10 step 1. No UI at all |
| 5 | Freezer ledger | `design-00` Arm B |
| 6 | `week_shape` records + applier + on-demand preview (§4.3) | The batch shape declarable |

**Steps 1 and 2 are a stated requirement, not a convenience.** *"I need an
interface to define more presets… they should be defined somewhere and be able
to be edited via interface."* So the editor is not the reward at the end of the
arm — it is what makes presets usable at all, and it is why this order puts it
second, ahead of every block mechanism. See `design-01` §3.4 for where the
data/code line falls.

**Steps 1–3 deliver most of the brief's felt flexibility with no new
mechanism** — every one of them is an existing config key behind an existing
widget shape. Step 4 is a pure refactor with an objective test. Only 5 and 6
are genuinely large, and by then the preset concept has been in daily use long
enough to have opinions about.

## 9. Deliberately not proposed

- **Direct manipulation of the week grid** (§3). An L on its own, and the
  record list makes it unnecessary.
- **A live grid preview while editing a preset** (§4.3). On demand, on a
  button, like `estimate_burn`.
- **Calorie or protein fields in a preset** (§5). Inert while `target_modes`
  is `auto`, and flipping that mode is the block's business, not a preset's.
- **Drag-reordering batches.** No precedent, and batch order carries no
  meaning — `cook_on` and `serves` fully determine the grid.
- **A general config editor** over every `CONFIG_FILES` key. Unchanged from
  `design-01` §9.2: bounded to what a mood varies, file stays the escape hatch.
