# Design 02 — The week shape declaration

Status: **draft for approval.** Nothing built. Reads on top of
`design-01-presets-and-blocks.md`; this is the *content* of a preset, where
that document is the mechanism.

The ask, verbatim: *"the profiles define pretty much everything required to
plan the week. It would also replace existing logic such as 'bulk prep 2 meals
on Sunday' and eat Mon-Wed logic… I might decide to skip bulk prep and need a
full week of cooked meals, or do a big bulk prep session and send to freezer as
well as sending to Mon/Tue/Thu meals — or I might want lunches to be shakes."*

This is a **separate document from design-01 because it is the largest single
piece in the program** and it re-opens something the codebase deliberately
closed. It should be reviewed on its own.

---

## 1. The scar — this flexibility existed once and was removed on purpose

Before anything else, because it is the reason to be careful.

The current "both batches anchor on day 1, straight across the front of the
week" behaviour is not a default nobody revisited. It is the **replacement for
a flexible-day system that failed**, and CLAUDE.md records the failure in
detail:

> *"Both toggles anchored on 'dinner', so they competed for the same seven
> slots; the second was pushed later and later; a weekend preference dragged
> the long cook to Saturday; and because `spread_batch` only ever adds claims,
> whatever shape one run happened to land on was frozen into every run after
> it. The symptom was Sunday-prepped food scheduled for Thursday and Friday.
> **None of that machinery survives** — no `prefer_days`, no cross-toggle
> `exclude_days`, no lunch-versus-dinner preference — because two batches on
> two different rows, both starting at day 1, cannot collide and cannot drift."*

So: day preferences, cross-batch exclusions and per-toggle row selection have
all been tried here and all three produced drifting, unsafe plans.

**Any design that reintroduces them has to say why this time is different, or
it will reproduce that bug exactly.**

## 2. Why declaring is different from inferring

It is different, and the difference is the whole safety argument.

Every failure above is a failure of **search**. `prefer_days` is a hint that
something has to resolve; `exclude_days` is a constraint two toggles negotiate
over; "push the second one later" is a fallback. The grid that came out was
*emergent* — a function of which toggle ran first, what the previous run had
already claimed, and which day happened to be free.

A preset does not hint. **It states the shape, and the shape is the input
rather than the output.** "Batch A: lunch, cooked prep day, serves Monday and
Tuesday, six portions to the freezer" is a specification. There is nothing to
resolve, nothing to prefer, and nothing for a second batch to negotiate with.

Three properties follow, and each is the direct negation of one failure:

| Old failure | Why a declaration cannot reproduce it |
|---|---|
| Two toggles competing for one row | Both rows are **named**. Two batches naming the same slot is a **load-time error**, not a runtime negotiation |
| A preference dragging the anchor to Saturday | There is no preference. The cook day is stated |
| One run's shape frozen into the next | The shape comes from the preset every run. **Same preset ⇒ same grid**, always |

**The rule to hold the implementation to: the week shape is applied, never
searched.** If a line of the implementation is choosing between candidate days,
it has reintroduced the bug. `spread_batch`'s anchor *selection* — the part
that filters candidates and picks one — is exactly what a declaration replaces;
its *linking* is what a declaration reuses.

## 3. Where it lives — and the asymmetry moving it fixes

`apply_batch_selections` is in **`ui_generation.py`**. It is UI-layer code, and
`planner.py` says so at the point it matters:

> `# Bulk-prep's own whole-week rule, gated on config["bulk_prep_anchor"] — set`
> `# only by ui_generation.apply_batch_selections, so this never fires for CLI`

**So `python src/planner.py` produces a week with no bulk prep and no long cook
at all.** The biggest structural decision about a week exists on one of the two
front ends. `generate_and_store_week` was extracted specifically so "the route
cannot drift from the CLI", and the batch shape sits outside it.

Moving the shape into config moves its application into **`week.py`** — the
module CLAUDE.md describes as *"all the deterministic, API-free planning… the
entire week resolved here before a single token is generated"*, which is
precisely what a batch plan is. Both front ends and the API generation route
then get it from one place.

**That is a fix, not a side effect**, and it makes a good first step on its
own: moving `apply_batch_selections` into `week.py` unchanged, with a
byte-identical grid test, de-risks everything after it (see §10).

## 4. The declaration

Inside a preset, beside the keys design-01 §9.2 already exposes:

```json
"week_shape": {
  "batches": [
    {
      "name": "sunday-soup",
      "meal_type": "lunch",
      "cook_on": "prep_day",
      "serves": ["Monday", "Tuesday"],
      "freeze_portions": 4
    },
    {
      "name": "sunday-roast",
      "meal_type": "dinner",
      "cook_on": "prep_day",
      "serves": ["Monday", "Tuesday"],
      "freeze_portions": 6
    }
  ],
  "freezer_draws": [
    { "meal_type": "lunch", "day": "Thursday" }
  ]
}
```

The three cases from the brief:

| Want | Declaration |
|---|---|
| "skip bulk prep, full week of cooked meals" | `"batches": []`. Every slot cooks. **Expressed by absence** — no toggle, no negation |
| "big bulk prep, freeze *and* serve Mon/Tue/Thu" | Two batches with `freeze_portions`, plus a `freezer_draws` entry for Thursday — see §5 |
| "lunches are shakes" | **Not `week_shape` at all.** That is `meal_styles.lunch`, already presettable by design-01 §3 |

That last row is worth keeping straight. `week_shape` is about **who cooks
what, when, and who eats it**. What the food *is* stays in `meal_styles`,
`cuisines` and `week_defaults`, all of which a preset already overrides
wholesale. Folding style into `week_shape` would give two keys an opinion on
one question.

`cook_on` takes `"prep_day"` or a weekday name. Prep day is
`week.PREP_DAY_INDEX` (−1) and becomes movable per design-00's Arm B — which
is how "away Sunday, prep Saturday" is expressed without touching this schema.

## 4a. Three things asked for, and only two are new fields

Raised 2026-09-01: *"`spread_batch`, which defines when to eat bulk food,
should be part of the preset. How to cater for a large prep session where more
than 2 meals are prepped. So the number of prepped meals, and the number of
eating events that use those prepped meals, needs to be part of the preset…
unless there is a good reason not to."*

There is a good reason not to, for exactly one of the three.

### When to eat it — ✅ already the design

`serves` is precisely this, and it is the whole point of §4. Confirmed rather
than new.

**And `spread_batch` splits cleanly along §3.4's data/code line.** It currently
does two jobs: it **picks** an anchor (a search over candidate days) and it
**links** the following slots to it. The declaration removes the first
entirely — the preset names the meal type and the cook day, so there is nothing
to search — and reuses the second unchanged. *Policy to the preset, mechanism
stays code.*

### How many dishes — ✅ free, because it is a list

Today `apply_batch_selections` hard-codes **exactly two**: bulk prep on lunch,
long cook on dinner. Under `week_shape`, `batches` is a **list**, so four or
five dishes is four or five entries.

**There is no "number of prepped meals" setting, because the count is the
length of the list.** A separate number beside the list is a second thing that
can disagree with it.

### How many eating events — ❌ **and this is the good reason**

This one must not be a field, and the argument is one this codebase has already
made and written down.

`week.portions_for` derives the batch as *(slots claiming this cook × household
size) + extras*, and CLAUDE.md states the consequence flatly: *"a batch size
can never silently disagree with the meals it has to cover. **There is
deliberately no 'batch multiplier' setting.**"*

**The count is already in the preset — as `serves`, in a strictly better
form.** `serves: ["Monday","Tuesday","Wednesday"]` says *three* and also says
*which three*, which a count cannot. Add `eating_events: 3` beside it and you
have two sources of truth for one quantity, free to disagree the moment either
is edited — and the failure is silent, because both are perfectly well-formed.

So: **`serves` is the input; the portion count is the output.** Same rule as
`design-04` §2's freezer list, where the declared item is the input and nothing
is inferred behind it.

## 4b. A large prep session — what actually bounds it

"More than 2 meals prepped" needs no new field at all; it is more entries in
`batches`. What is worth knowing is **what stops you**, because the constraints
are real and none of them is the batch count:

| Bound | Where | Effect |
|---|---|---|
| **hands-on time** | `max_prep_active_mins` (120), presettable | five dishes will not fit two hours |
| **the fridge window** | `design-05` — 4 days default, **48 h for rice or pasta** | the binding one, see below |
| **containers and fridge room** | not modelled, and should not be | not observable, so a capacity nobody can verify is a number that will be wrong |

**The fridge window is what actually limits a big prep session, and the freezer
is what extends it past the limit.** From a Sunday prep with a 4-day default,
batches reach Thursday. Friday, Saturday and Sunday cannot be fridge leftovers
of that session at any batch count.

So "cook once, eat all week" is expressible, and it is two mechanisms rather
than one:

```
batches:     4-5 dishes cooked on prep day, each serving Mon-Thu
             with freeze_portions for the back half of the week
freezer_draws: Fri, Sat, Sun
```

Which is `design-04` and `design-05` doing exactly the job they were split out
for. **The batch count was never the constraint; the storage window was.**

### One open question: a batch cooked only for the freezer

*"A big bulk prep session and send to freezer"* has a case this schema does not
yet answer: a dish cooked on prep day, **frozen entirely, eaten in no slot this
week** — `serves: []`, `freeze_portions: 12`.

**It has nowhere to live.** Every `CookEvent` is keyed by `slot_id`, and prep
day has no slot of its own — which is the whole reason a batch anchors on the
first day it is *eaten* (CLAUDE.md: *"The anchor is bookkeeping, not a
decision"*). A batch eaten in no slot has no anchor.

Two ways out:

- **Require at least one `serves` entry.** ✅ **Recommended, and the real
  argument is shopping rather than bookkeeping** — see `design-04` §6a.3.
  Shopping aggregates *cook events*; a batch nothing eats this week has none,
  so nothing is bought, and the prep session would call for a dish whose
  ingredients never reached a list. Barely limiting either way:
  `serves: ["Monday"]` with `freeze_portions: 10` is a twelve-portion session
  with one eating day.
- **Synthetic prep-day slot ids.** Cleaner in principle, and it collides
  immediately: days are weekday *names*, so a prep-day "Sunday" is
  indistinguishable from the Sunday at the end of the week — the same
  ambiguity `week.PREP_DAY_INDEX` exists to keep straight. Worth doing only if
  the requirement above genuinely bites.

## 5. Mon/Tue/**Thu** — the request that needs two storage modes

This is the subtle one and it is the reason the example has a gap at Wednesday.

> **Corrected 2026-09-01 — `design-05` moved the number this example was built
> on.** It was written against `fridge_safe_days: 3`, under which Thursday was
> out of range and *had* to be a freezer draw. The fridge default is becoming
> **4 days (96 h)**, so from a Sunday prep Thursday is now exactly at the
> limit and **Mon/Tue/Thu is achievable from the fridge alone**. The mechanism
> below is unchanged and still necessary; only the day at which it starts
> binding has moved. This is recorded rather than quietly rewritten, because a
> worked example that silently changes its own premise is worse than one that
> says which number moved.

Counted from the cook day, and prep day is the day *before* the week starts.
So from a Sunday prep session under the new default: Monday is 1 day out,
Tuesday 2, Wednesday 3, Thursday 4 — at the limit — and **Friday is 5, past
it.** A rice or pasta dish is bound at 48 h instead (`design-05`), so the same
batch cannot reach past Tuesday.

That bound is not incidental; `apply_batch_selections` passes
`fridge_safe_days - 1` as `max_day_index` precisely because measuring from the
anchor's own grid day was short by one and let Sunday food reach a day it
should not.

So a `serves` list that reaches past the window is **not** all fridge
leftovers. Under the 4-day default that is Friday onward from a Sunday prep —
or Wednesday onward for a rice dish. Such a day is **a portion that went to the
freezer and came back** — which is exactly what the brief says
("send to freezer, as well as sending to Mon/Tue/Thu meals"), and the app has
no word for it.

**`spread_batch`'s contiguous forward walk is therefore right and stays.** The
non-contiguity is not a limitation to relax; it is the signal that a second
storage mode is in play. Relaxing the walk to allow gaps would let food sit
four days in a fridge with no warning — the exact bug `max_day_index` was
added to fix.

### The mechanism: a freezer draw is a leftover with a different origin

Not a fourth `SlotSpec.mode`. CLAUDE.md is explicit about why `recipe_id` did
not become one: a fourth mode *"would have meant revisiting every
`mode == MODE_COOK` test in the repo"*.

Instead, `link_origin` — which already exists to answer "who made this link,
and what may overwrite it" across `user` / `location` / `batch` — gains
`LINK_ORIGIN_FREEZER`, and `source` names a **freezer lot** rather than a slot
in this week.

Four consequences, all of which fall out correctly:

- **Shopping buys nothing for it.** It is not a cook, so it contributes no
  ingredients — which is right, because the lot was bought in the week that
  cooked it (design-00 F1 verified that `portions_for` already includes
  `extra_portions` and `build_cook_event` scales to the full figure).
- **`validate_week`'s "a leftover may only point backwards" rule needs an
  explicit exemption.** A freezer lot genuinely predates the week, often by
  weeks. The exemption is narrow and keyed on the origin, so a `user` or
  `batch` link is still checked exactly as now.
- **The lot must carry its recipe**, or the slot has no macros and the card
  has nothing to draw. That is the freezer ledger's job and it is what makes
  the ledger a **hard dependency of this document**, not a neighbour: the
  brief's own Mon/Tue/Thu example is inexpressible without it.
- **Freezer life is not fridge life.** The ledger needs its own window; a lot
  older than it should warn rather than be silently served.

## 6. What the preset must *not* declare

The brief says "defines all logic — or as much as possible". Three things have
to stay out, and the first is the important one.

- **Portion counts.** `week.portions_for` derives them — *(slots claiming this
  cook × household size) + extras* — and CLAUDE.md states the consequence
  plainly: *"a batch size can never silently disagree with the meals it has to
  cover. There is deliberately no 'batch multiplier' setting."* A declared
  serving count is that setting under a new name, and it can disagree with the
  `serves` list right beside it. **`serves` and `freeze_portions` are the
  inputs; the batch size is the output.**
- **Which recipe.** Generation and the library own that (design-01 §8's "still
  yours" — the user pin from PROMPT-3 is the affordance).
- **Macros and targets.** The engine and the block own those (design-01 §4.2).

## 7. Validation at load — where the value actually is

The payoff for declaring rather than inferring is that **every failure becomes
a load-time error naming the preset**, instead of an emergent grid to eyeball.
Same policy `CONFIG_FILES` applies to a key in the wrong file and
`diet_styles_are_known` applies to a typo'd style: fail loudly, name the thing.

| Check | Fails when |
|---|---|
| slot collision | two batches claim one slot, or a batch claims a `freezer_draws` day |
| fridge window | a `serves` day is more than `fridge_safe_days` from `cook_on` — **with the message naming the freezer draw as the fix**, since that is what the author meant |
| contiguity | `serves` has a gap — same message; a gap is a freezer draw written as a leftover |
| location | a batch cooks on a day `base_schedule` says you are away, or serves a slot a location rule skips |
| meal type | `meal_type` is not in `meal_types` |
| last day | a batch serves `spec.days[-1]` from prep day — `exclude_target_days`' existing rule |
| freezer stock | a draw with no lot available — **a warning, not an error**: stock is runtime state and a plan should not fail to load because the freezer is empty |

The fridge-window and contiguity messages are the two that earn their keep.
Both are the author writing a freezer draw as a leftover, and a message that
says so teaches the distinction §5 introduces at the exact moment it matters.

## 8. Precedence — location still wins on facts

`apply_location_modes` runs on a fresh grid before any batch logic and encodes
**facts about your calendar**, not preferences: you are at the office Monday,
so lunch is a leftover. A preset declaring "cook lunch every day" must not
silently override that.

It does not need to. The existing precedence already handles it, and
deliberately: `_claimable` accepts a `LINK_ORIGIN_LOCATION` leftover **as a
target and re-points it** (the rule says an Office lunch *is* a leftover and
never says whose), while `_releasable_dependants` frees a blocking location
link when every dependant is one. A `user` link is never taken.

**So the declaration slots in exactly where `apply_batch_selections` sits
today** — after location, before generation — and inherits all of it. That is
the minimal-change insertion point and the reason this is an L rather than an
XL.

## 9. Migration — today's behaviour is a preset

The acceptance test that matters, and the one that makes this safe to ship:

> **The `default` preset reproduces the current grid byte-identically.**

Today's shape is expressible in the schema above — bulk prep on lunch and long
cook on dinner, both `cook_on: "prep_day"`, both serving Monday and Tuesday
(Wednesday too, where `fridge_safe_days` allows), no freezer portions:

```json
"week_shape": {
  "batches": [
    { "name": "bulk-prep", "meal_type": "lunch",  "cook_on": "prep_day", "serves": ["Monday","Tuesday","Wednesday"] },
    { "name": "long-cook",  "meal_type": "dinner", "cook_on": "prep_day", "serves": ["Monday","Tuesday","Wednesday"] }
  ]
}
```

CLAUDE.md records the measured result of the current code — *"bulk prep
`Monday:lunch` → Tuesday and Wednesday lunches, long cook `Monday:dinner` →
Tuesday and Wednesday dinners. Six meals, 6 portions each, nothing landing
Thursday or later, `validate_week` clean and byte-identical across repeated
runs"* — so there is a published figure to assert against rather than a
subjective "looks the same".

**The two review-dialog toggles become presets**, not a second mechanism
beside them. A toggle that survived alongside `week_shape` would be a second
thing with an opinion on the same grid, and the first divergence would be
silent.

## 10. Sequence — and an honest size

> Scoped to this document. **`design-03` §8 is the authoritative order across
> Arm A**, and it places all of this last, after the preset selector, editor
> and the XS dimensions are already in use. `design-03` also confirms the §4
> schema must stay a list of records — direct grid editing is an L on its own —
> and adds a validate-before-save requirement to §7.

This is an **L**, and it promotes design-01's overall shape: a preset mechanism
without week shape is half the feature the brief asked for, and week shape has
a hard dependency on the freezer ledger that design-00 ranked in Tier 2.

The order that de-risks it:

| | Step | Size | Why |
|---|---|---|---|
| 1 | **Move `apply_batch_selections` into `week.py` unchanged** | S | §3. Pure refactor, byte-identical grid test, fixes the CLI asymmetry on its own. Everything after it is safer |
| 2 | Freezer ledger (design-00 Arm B) | M | §5. The brief's own example needs it |
| 3 | `week_shape` schema + load-time validation | M | §7. Validation before application — an unvalidatable declaration is the old bug with better syntax |
| 4 | Apply it in place of the toggles; `default` preset test | M | §9 |
| 5 | `LINK_ORIGIN_FREEZER` and draws | M | §5 |
| 6 | Retire the two toggles from the review dialog | S | §9 |

**Step 1 is worth doing whatever happens to the rest of this document.** It is
a pure move, it has an objective test, and it closes a real asymmetry between
the two front ends today.

## 11. Deliberately not in this design

- **Any day *preference*, anywhere.** §1 and §2. If the implementation is
  choosing between candidate days, it is the removed machinery returning.
- **Relaxing `spread_batch`'s contiguous walk** (§5). A gap means a freezer
  draw, and saying so is the feature.
- **A fourth `SlotSpec.mode`** (§5).
- **Declared portion counts, or a declared count of eating events** (§6, §4a) —
  both are the "batch multiplier" this app has refused once already, and
  `serves` already carries the information in a better form.
- **Food style in `week_shape`** (§4). `meal_styles` owns that and a preset
  already overrides it.
- **Cross-week batch *assignment*.** A lot goes to the freezer and a later
  week draws it; the cooking week does not name the day it is eaten. That is
  design-00 F1's pull-versus-push line, unchanged.
