# PROMPT-10 — Storage windows per dish, and the one live safety defect

**Shipped in v0.42.0.** The one live safety defect in the program, and the only
prompt in the set that fixed something already wrong. Kept as the record of
what was asked for and why; it is not work to pick up. `dev/README.md`'s order
of delivery is the authority on what is still outstanding — this banner states
a verdict, never a rank, because a verdict cannot go stale.

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It
changes what the app considers safe to eat, its acceptance includes a
behaviour change in the *permissive* direction, and a human should watch the
byte-identical comparison run.

**Priority: first. Ahead of PROMPT-7, 8 and 9.** This is the only item in the
program where being wrong makes somebody ill, and unlike everything else in the
set it fixes a defect that is **live today** rather than enabling a feature.

Cold session. Read CLAUDE.md's **"Leftovers can't outlive the fridge"**,
**"Batch cooking on purpose: the two prep toggles"** (especially
`PREP_DAY_INDEX` and `cook_day_index`) and **"The generated side is enforced
too, and needed a measured field to do it"** — that last one is the pattern
this change copies almost exactly. Then `design-05` **in full**; it is 250
lines and every section is load-bearing. `design-04` §2.1 and §6 for the
freezer half.

## The defect, stated plainly

`inventory_rules.fridge_safe_days` is **3**, one global number read in five
places. A rice or pasta dish carries *Bacillus cereus* spores that survive
cooking and produce toxin as the dish sits; the accepted window is **2 days**.
So the shipped config permits a rice tray bake batched on prep day to be eaten
a day past its safe window, and `apply_batch_selections` builds exactly that
shape on every week the long-cook toggle runs.

The same number is wrong in the other direction for a beef stew, which keeps 4
days — a day of good food thrown away, and a batch that could have covered
Thursday. `design-05` §1 has the table.

**One global number cannot be right, and this one is wrong both ways at once.**

## Why this is not a config change

Moving `fridge_safe_days` to 4 **makes the dangerous case worse**. The
lengthening and the dish-level exception have to land in the same change or not
at all — `design-05` §5 says so and this prompt exists to enforce it.

## What to do

Follow `design-05`. The five pieces, in the order they unblock each other:

### 1. `Recipe.storage_class` and the two window tables

`design-05` §2. A new `Recipe` field, one of a fixed enumeration, reported by
the model exactly as `long_oven_cook` and `bulk_prep_friendly` already are.
`inventory_rules.storage_windows` carries the fridge hours and the freezer
months.

### 2. Days, not hours — the resolver

`design-05` §2a, added under review and easy to skip. The tables are in hours;
every consumer holds a **date** and nothing anywhere stores a time. So one
resolver turns a window into whole day-gaps by **floor** — 96h → 3, 48h → 1 —
and no surface ever prints hours. Do not write the day figure into config
beside the hour figure; they are different claims and a reader would take them
for one.

### 3. Defaults fail short, inverting the house rule

`design-05` §3, and it is the section most likely to be "helpfully" undone by a
session applying the codebase's usual convention. Everywhere else an absent
value resolves to the behaviour before the feature existed. Here:

- an unclassified dish gets the **rice** window, not the default;
- an unclassified freezer item gets **1 month**;
- a missing `cooked_on` is **not defaulted at all** — flag the item undateable.

CLAUDE.md's `is_sunday_prepped` incident is the precedent: a model self-report
that was simply dropped, on a field the prompt explicitly asked for. If a
dropped `storage_class` defaulted long, the failure mode of a model forgetting
a field is a rice dish scheduled four days out.

### 4. Tell the model the span, then validate it

`design-05` §4. `build_storage_rule(span)` joins the shared rules block and is
emitted **only** when the span exceeds the short window — so a single-day
cook's prompt is byte-identical to today's. A validator rejects a returned
`storage_class` too short for its slot's span, over the two-axis split
`reject_misplaced_long_cook` already uses: `DayRecipes` from its context's day,
`MealTypeWeekRecipes` over its own keys, **one shared function** so the axes
cannot disagree about a Tuesday.

The prep-day anchors are exempt from the *day* judgement and emphatically not
from the *window*: `prep_day_batch_slot_ids` identifies them, and their span is
measured from prep day, so it is **longer**. This is the case that most needs
the rule.

### 5. The five consumers

`design-05` §5's table. The backstop changes character — `validate_week` goes
from a static grid check to checking a *generated* week against the dishes
actually in it, which is where a dropped class or a slipped-through rice dish
is caught. **Report the slot and the days; never silently trim.**

### 6. The freezer half, if `data/freezer.json` exists yet

`design-05` §6 and `design-04` §2.1. A lot snapshots its own `storage_class` at
freeze time rather than reading the catalog back — editing a recipe must not
change what the app believes is in the freezer. Past the window: **warn on the
item and on any draw that would eat it, never auto-remove.** And keep the two
sentences apart — the fridge figures are safety, the freezer figures are
quality, and "unsafe" and "past its best" prompt different behaviour.

**If PROMPT-11 (the freezer ledger) has not landed, do this half anyway as the
resolver plus its tests**, with no storage. The window logic is what the
freezer work will import; writing it twice is how the two come to disagree.

## Acceptance

`design-05` §7 in full. The ones that carry the change:

- **A rice-classed dish cannot be scheduled more than one day-gap from its
  cook**, from **either** a batch spread or a hand-built leftover chain of
  "Link to next lunch" clicks. Both paths, because only one of them goes
  through `spread_batch`.
- **An unclassified dish is treated as rice, not as default.** A recipe saved
  before this field existed loads and is short, not long.
- **A prep-day anchor's span is measured from prep day**, not its grid day.
  CLAUDE.md records this exact off-by-one being fixed twice already
  (`max_day_index`, then `storage_note`); it is a third chance to reintroduce
  it.
- **The prompt is byte-identical** for a week whose spans are all inside the
  short window. Assert it.
- **The default lengthening is visible in the diff.** A prep-day batch reaches
  **Thursday** (day index 3) after this change where it reached Wednesday
  before — verified arithmetic in `design-05` §5's correction box. That is a
  permissive change riding on a safety one and it must be asserted, not
  discovered.
- **`storage_note` and the card badge agree on every card.** CLAUDE.md records
  this pair disagreeing twice; a per-dish window gives it a third opportunity,
  and now two cards in one week can legitimately differ.
- No surface prints hours.

New: `tests/test_food_safety.py`. Extended: `test_week_mechanics.py`
(`validate_week` against dish windows, the lengthened default),
`test_meal_selection.py` (the prompt rule and the rejection, beside the
long-cook ones they mirror).

## Do not

- Move `fridge_safe_days` to 4 without the dish-level exception in the same
  change. That is the whole reason this prompt exists.
- Make `storage_windows` a preset key. `PROMPT-7`'s audit ran this row and took
  it off the table: a preset could only ever have picked a different wrong
  global, and a tightening-only lever fixes the stew case never and the rice
  case by accident. `design-05` §2 has the reasoning.
- Apply the codebase's usual "absent means the old behaviour" default. §3
  inverts it deliberately and says why.
- Derive `storage_class` from `total_time_minutes` or `long_oven_cook`. They
  measure different things, and CLAUDE.md's rule about `total_time_minutes`
  never being derived from `prep_time_minutes` is the same rule.
- Print hours at a user. The app does not know them (§2a).
- Auto-remove an expired freezer item. On a hand-declared list that is the app
  editing your statement of what you own.
