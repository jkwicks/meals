# Design 04 — The freezer, and a movable prep day (Arm B)

Status: **draft for approval.** Nothing built.

Chosen as the next arm on 2026-09-01. It is the one blocking a thing already
asked for: *"a big bulk prep session and send to freezer, as well as sending to
Mon/Tue/Thu meals."*

**One correction, because `design-05` moved the number underneath this.**
Written against `fridge_safe_days: 3`, Thursday was out of range and had to be
frozen. At the corrected 4-day (96 h) default, Thursday from a Sunday prep sits
exactly at the limit, so **Mon/Tue/Thu is reachable from the fridge alone** —
and a rice or pasta dish still is not, since that is bound at 48 h.

The freezer is not needed less; it binds a day later. It remains the only way
to express the three things actually asked for: **the back half of a week** fed
from one prep session (Friday is 5 days out), **multi-week bulk prep**, and
**the surplus** a big session leaves behind (§6a).

Two independent pieces. The second needs nothing from the first.

---

## 1. The freezer is half-built already

`SlotSpec.extra_portions` exists today, is documented as *"Spare portions to
freeze, on top of the slots claiming this cook"*, and is genuinely counted:
`week.portions_for` adds it to the batch, and `build_cook_event` scales the
recipe to that full figure — **so the extra portions are already bought and
already cooked.**

Then they cease to exist. Five references, all arithmetic or validation, and
nothing receives them.

That is the same shape this project has now closed three times from the other
direction — sleep, recorded activity and `net_calories` were each *fetched and
never read*, and each turned out to be the enabling half of a ranked item. This
is the mirror: **written and never read.** The ledger is not a new feature so
much as the missing consumer of one that already ships.

## 2. The freezer is a declared list, confirmed before every run

**Settled 2026-09-01**, and it replaces a more elaborate design this document
carried first: *"take the freezer contents as a manual list. At meal plan
generation time, the user can provide a list of items available in the freezer.
Stored on disk, but verified before each generation."*

**That is the right shape, and it removes the hardest problem in this arm.**
The earlier design had the app seeding lots when a batch was cooked and
decrementing them when a draw was planned — which meant the stored count could
disagree with the actual freezer, and CLAUDE.md is emphatic about why that is
the wrong bet: the pantry ledger *never reaches disk* precisely because a count
*"would start disagreeing with the actual shelf the moment you cook something
without telling the app"*.

A confirmed list cannot drift, because **the confirmation is the
reconciliation**. Nothing is inferred, so nothing can be inferred wrongly.

**This is the pantry model exactly**, one shelf over:

| | Pantry | Freezer |
|---|---|---|
| what it is | a hand-edited list of what is in the house | a hand-edited list of what is in the freezer |
| where it lives | `inventory_to_clear` in config | `data/freezer.json` |
| when it is confirmed | edited in the review drawer | **confirmed at generation** |
| what generation does with it | prefers those ingredients | assigns portions to draw slots |

`data/` rather than `config/` is the one difference and it is deliberate:
the pantry is a standing statement about how you shop, and freezer contents are
observed state. It also keeps generation from becoming a third writer to
`config/`.

### 2.1 A freezer item

```
FreezerItem:
  id               # stable, generated on add — nothing else identifies a lot
  label            # "beef massaman", free text
  portions         # how many meals are in there
  cooked_on        # REQUIRED — a real date. Food safety, see design-05
  frozen_on        # REQUIRED — the date it went in. See below
  storage_class    # snapshot at freeze time; design-05 §6
  per_serving      # snapshot at freeze time; MACRO_KEYS
  recipe_id        # optional; a catalog entry, if it came from one
```

**`cooked_on` is required, and it is the *cook* date rather than the freeze
date.** Stated 2026-09-01: *"needs to include cook date for food safety."*
Right, and the distinction matters — freezing pauses quality decline, it does
not reset the clock on what happened before it. An item that sat in a fridge
for three days before going in is not the same as one frozen the evening it was
cooked.

**`frozen_on` is required too, and its absence was an internal
inconsistency** — added 2026-09-01 under review. The paragraph above names the
cook-to-freeze gap as the thing that distinguishes two otherwise identical
items, and then the schema stored only one end of it, making the distinction it
had just called decisive unrepresentable. §6's draw resolution had already
reached for the missing field on its own — *"oldest suitable first, by
`frozen_on` where present"* — which is the same field arriving through the back
door.

The two dates answer two different questions and neither substitutes:

| | measured from | is a question about |
|---|---|---|
| cook → freeze | `cooked_on` → `frozen_on` | **safety** — was it still good when it went in |
| freeze → eaten | `frozen_on` → the draw's day | **quality** — is it still worth eating |

`design-05` §6 is emphatic that those are different sentences and must not be
conflated; storing one date made conflating them unavoidable.

**A lot snapshots its own properties, and does not read them back off the
catalog.** `storage_class` and `per_serving` are copied in at freeze time, with
`recipe_id` kept beside them as provenance rather than as the source of truth.
Editing a catalog recipe — re-importing it, correcting its macros, reclassifying
it — must not retroactively change what the app believes is in the freezer,
because the food in there was cooked from the recipe *as it was*. This is
`design-00` F5's own rule applied to a lot rather than to a recipe: **store a
measurement iff it is scale-invariant**, and "this tub is 480 kcal a serving"
is exactly that. A missing snapshot on an item written before this field
existed falls back to the recipe, and is flagged as inferred.

**`id` exists because nothing else identifies a lot.** Two tubs of the same
recipe frozen a fortnight apart are two rows that agree on every other field,
and a draw has to be able to take the older one.

It is the one required field beyond the label and the count, because **its
absence cannot be defaulted safely.** Every other optional field degrades to
"less information"; a missing cook date degrades to "no idea how old this is",
and the conservative reading of that is not a number the app can pick. See
`design-05` for what the date is measured against — the window is **per dish
type**, not one global figure.

### 2.2 How an item gets on the list

Three routes, and the first is the one that will actually be used:

- **"Send to freezer" on a recipe card.** Prompts for servings, pre-fills the
  label from the recipe, the `recipe_id` from the card, and `cooked_on` from
  the plan's date for that slot. **Everything is already on screen** — this is
  the cheapest possible capture and it is where frozen food actually comes
  from.
- **`week_shape`'s `freeze_portions`** (§6) — declared ahead of the run, and
  **confirmed after it**. Not the same data decided earlier, which is what this
  route originally claimed; see the correction below.
- **Manual add**, in the row editor, for food this app never cooked — a
  shop-bought meal, or something frozen before any of this existed.

**The card button is a change of state on the plan, not a staged edit.** It
records something that has already happened in your kitchen, so it persists on
click — the same test the adherence marks pass, and for the same reason: a tick
that vanished on reload would be a control with no effect.

#### A declared surplus is not observed stock — corrected 2026-09-01

The second route contradicted this section's own opening line. §2's table says
freezer contents are **observed state**, and that is the stated reason the file
lives in `data/` rather than `config/`. `freeze_portions` is a *plan*: it says
what the week intends to cook, written before a pot has been on the stove. A
generated plan writing straight into `data/freezer.json` would populate a
ledger of what you own from a statement of what you meant to do — and every
failure mode of that is silent, because the file is hand-editable and nothing
downstream can tell an observed row from a projected one.

It also fails the test the card button passes two paragraphs above: that button
persists on click *because* it records something that already happened. A
`freeze_portions` declaration has not happened yet.

So the route is three steps rather than one:

1. `freeze_portions` records **planned surplus on the `WeekPlan`** — no
   `data/freezer.json` write, exactly as `extra_portions` behaves today.
2. The plan surfaces it as a pending item: *"6 portions of beef massaman to
   freeze."* It appears on the cook day's card, beside the adherence marks,
   which is where you are standing when it becomes true.
3. **Confirming writes the freezer item**, stamping `frozen_on` from the clock
   at that moment — which is the honest value and the one a declaration could
   never have supplied, since it is the date you actually put it in.

That is also the answer to *"what if I cooked less than I planned"*: the
confirm step takes a count, defaulted to the declared one. A batch that came
out smaller is a correction typed once, not a ledger silently wrong.

**Not confirming is a legitimate end state.** An unconfirmed surplus stays on
the plan and never reaches the freezer file — which is the correct reading of a
week where the batch was not cooked. The pending item is a prompt, not a debt.

### 2.2 The one real cost, stated plainly

**A hand-declared item has no recipe attached, so a slot eating it has no
macros.** That is the price of dropping the seeded ledger, and it is worth
naming rather than discovering: the day would silently under-report, and the
telemetry header would show a shortfall that is not real.

Three ways an item can carry macros, in preference order:

1. **`recipe_id` — pick it from the catalog.** Most frozen food here came from
   a cooked batch, and those recipes are catalog entries. The interaction
   already exists (`swap_slot_with_favorite`'s picker), and
   `planner.single_serving` normalises to one portion the same way a pinned
   favourite does.
2. **`estimate` — typed macros.** The precedent is `SlotSpec.skip_estimate`,
   which exists for exactly this: *"estimated macros for a meal eaten
   elsewhere"*. Same four keys, same "fat is derived, the rest is typed"
   division `ui_review.day_target_row` already uses.
3. **Neither.** The slot contributes **0** and the day shows a **visible
   shortfall** — never a guess. That is this codebase's standing answer
   wherever the numbers do not reconcile: an orphaned leftover contributes 0,
   a capped surplus is dropped, an unaffordable protein floor does nothing and
   logs.

**Suggest the catalog link and default to it**, since the common case is food
this app cooked. But do not require it: an item with no recipe still belongs on
the list, because knowing it is *there* is most of the value.

## 3. Confirmation is a step in the review dialog

"Verified before each generation" has an obvious home: the review dialog is
already where the week's shape is settled before a run, and it already holds
the pantry row editor. **The freezer list sits beside it, in the same row
shape** (`design-03` shape 5 — label, portions, remove, add).

Three rules:

- **Confirmation is explicit, not implied by opening the dialog.** A "still
  right?" acknowledgement with a visible date of last confirmation, so a list
  nobody has looked at for a month says so.
- **A stale list does not block generation.** It warns. Refusing to plan a week
  because the freezer has not been audited would make the feature a tax rather
  than a tool.
- **Nothing is decremented automatically.** If Thursday's draw happens, the
  count changes when *you* change it — at the next confirmation. The app never
  quietly asserts what is in your freezer, which is the whole point of the
  manual list.

## 4. Eating from it: a leftover with a different origin

**Not a fourth `SlotSpec.mode`.** CLAUDE.md gives the reason `recipe_id` avoided
becoming one: a fourth mode *"would have meant revisiting every
`mode == MODE_COOK` test in the repo"*.

`link_origin` already exists to answer "who made this link, and what may
overwrite it" across `user` / `location` / `batch`. It gains
**`LINK_ORIGIN_FREEZER`**, and `source` names a **declared freezer item**
rather than a slot in this week.

Everything falls out correctly:

| | |
|---|---|
| **shopping** | buys nothing — it is not a cook, and the food was bought in the week that cooked it (`portions_for` already includes `extra_portions`) |
| **macros** | from the item's `recipe_id`, else its `estimate`, else 0 and a visible shortfall (§2.2) |
| **portions** | one draw consumes one portion per person, `spec.servings_per_meal` |
| **`validate_week`** | needs **one narrow exemption**: its "a leftover may only point backwards" rule assumes the source is in this grid. A lot genuinely predates the week, often by many. Keyed on the origin, so `user` and `batch` links are checked exactly as now |

**A draw with nothing declared to satisfy it is a warning, not a load
failure.** The list is a statement about the world and the world changes; a
plan should not fail to open because the freezer is empty. Same call
`design-02` §7 already makes.

## 5. Storage windows moved out — see `design-05`

This section originally proposed a single `freezer_safe_days` number beside
`fridge_safe_days`. **That was corrected on 2026-09-01 and the whole topic is
now its own document**, because the answer is not one number or even two:

> *"Fridge is usually 96 hours (4 days), except pasta/rice dishes which should
> be consumed within 48. Freezer: soups/stews/casseroles 2–3 months; cooked
> beef, pork, poultry 2–4; poultry nuggets or patties 1–3; fish and seafood
> 1–3; fried food 1 month."*

Storage life is a property of **the dish**, not of the config — and the current
`fridge_safe_days: 3` is a single global that is simultaneously too short for
most dishes and too long for a rice one. That ripples through five consumers
and has an ordering problem underneath it (the grid is built before any recipe
exists), so it is designed separately in **`design-05-food-safety.md`**.

What stays true here: an item's age is measured from `cooked_on`, warnings are
raised and nothing is ever auto-removed.

## 6. Declaring it: `week_shape`, from `design-02`

Two fields, already sketched there:

```json
"batches": [
  { "name": "sunday-roast", "meal_type": "dinner", "cook_on": "prep_day",
    "serves": ["Monday", "Tuesday"], "freeze_portions": 6 }
],
"freezer_draws": [ { "meal_type": "lunch", "day": "Thursday" } ]
```

`freeze_portions` is `extra_portions` under a name that says where they go —
**declared here, confirmed after the cook** (§2.2). It puts nothing in
`data/freezer.json` on its own.

**A draw does not name an item.** It says "eat something from the freezer
here", and which item is resolved at generation — oldest suitable first, by
`frozen_on` where present. That is deliberate and it is `design-00` F1's pull-versus-push line:
week two decides what it eats, the cooking week does not reach forward and
book a Thursday three weeks out. It is also how a freezer actually works.

**And this is what makes Monday/Tuesday/Thursday expressible**, which was the
whole request: Monday and Tuesday are ordinary fridge leftovers inside the
3-day window; Thursday is a draw. `spread_batch`'s contiguous forward walk
stays exactly as it is — the gap at Wednesday was never a limitation to relax,
it was the signal that a second storage mode was in play.

## 6a. The surplus path — what a big session leaves behind

Confirmed 2026-09-01: *"the big bulk prep session that doesn't eat all serves
during the week goes to the freezer draw list."*

That is `freeze_portions` → **a pending freezer item**, and it is the fourth
population route beside the three in §2.2. It was declared before the run and
is confirmed after the cook, which is the one interaction it does need: a
declaration is a plan, and `data/freezer.json` holds observed state.

### 6a.1 Surplus is the input, total is the display

Worth pinning down, because there are two ways to say the same thing and only
one keeps the derivation intact:

| | You declare | Adding a day to `serves` then… |
|---|---|---|
| **surplus form** ✅ | `serves` + `freeze_portions: 6` | cooks **more**; the freezer allocation is untouched |
| total form | `total_portions: 12` | cooks the same; the **freezer silently shrinks** |

The surplus form is right for two reasons. It is what `extra_portions` already
means — *"spare portions to freeze, **on top of** the slots claiming this
cook"* — and `week.portions_for` is built on that reading. And it makes the
frozen amount **stable while you fiddle with the week**: a total-portions field
would quietly convert next month's dinners into this week's lunches every time
you added a day.

**But the pot is how you actually think, so show the total.** The mental model
is "I'm making a big pot", not "three meals plus six spare". The editor should
display the computed total beside the inputs — *"3 meals × 2 + 6 spare = 12
portions"* — which is the same move `portions_for` already makes everywhere
else: derived, shown, never typed.

### 6a.2 The date on a surplus item is **prep day**, not the anchor's day

**This is a trap with a precedent, and it now has safety consequences.**

A prep-day batch anchors on the first day it is *eaten* — Monday, on the
shipped config — but the food was cooked the **day before the week started**.
CLAUDE.md records this biting once already: `storage_note`'s `keeps_for_days`
measured from the anchor's grid day, was short by exactly one on every prep
batch, and *"told you to refrigerate the one batch in the week sitting at the
fridge limit — the whole reason the freeze branch exists."*

`week.PREP_DAY_INDEX` and `cook_day_index(spec, day, prepped_ahead)` exist to
answer this, and **a surplus item's `cooked_on` must come from that same
call**, never from the anchor's slot. Otherwise every freezer item from a prep
session is dated a day late, and under `design-05` a date is no longer
cosmetic — it is what a storage window is measured against.

`planner.prep_day_batch_slot_ids(config)` already names the anchors, and
`planner.is_prepped_ahead(event, week_plan)` already answers it after the fact.
Reuse whichever the caller has, exactly as `build_cook_event` and `slot_views`
each do.

### 6a.3 A batch must still be eaten at least once — and the reason is shopping

§4a of `design-02` raised `serves: []` as an open question and recommended
requiring at least one entry. **The argument is stronger than the one given
there**, which was about bookkeeping.

**Shopping aggregates cook events.** A batch nothing eats this week has no cook
event, so **nothing is bought** — and the prep session would call for a dish
whose ingredients never reached a shopping list. That is a concrete failure,
not a convention.

And the requirement costs almost nothing: `serves: ["Monday"]` with
`freeze_portions: 10` is a twelve-portion session with a single eating day.
The big prep session is fully expressible; it just has to be tasted once.

## 7. A movable prep day — and where its logic belongs

Raised with the right doubt attached: *"wondering if preset is the correct
place and not something else, as its logic is based off reality. A preset
doesn't assume Sunday is ride day in Ballarat."*

**That doubt is correct, and the resolution is a split the app has already made
once.** Three layers, and only two of them are the preset's:

| Question | Lives in | Because |
|---|---|---|
| **Does this week have a prep day at all?** | **preset** — a flag | A preference. "This week I'm cooking fresh every night" |
| **Which day can it be?** | **derived from `base_schedule`** | A fact about where you are. No preset can know a Sunday ride is in Ballarat |
| **What gets prepped?** | **preset** — `week_shape` | A preference |

**The precedent is `day_allows_long_cook`, and its argument is verbatim
yours.** CLAUDE.md: *"a location may rule a weekend day out, not only rule a
weekday in… the complaint against the old rule was that the calendar is not
where you are, and you cannot start a braise on a day you are out."* It reads
`location_rules.<location>.allows_long_cook` off the day's `base_schedule`
entry and falls back to the weekend when nothing is declared. Prep-day
placement is the same question about the same data, so it should be the same
shape — a small derived function, not a config key restating what the schedule
already says.

**Its accuracy depends on something not yet built.** Placement derives from
`base_schedule`, which is **hand-maintained** — so a prep session lands on a
day you are away whenever the file and the calendar disagree. `PROMPT-6` closes
that with a read-only, revocable calendar feed, and prep day is its most
demanding consumer: the other location readers cost a worse meal, this one
costs a two-hour cooking session on a day you are in Ballarat.

**The proposed rule, confirmed:**

```
if not preset.includes_prep_day:      no prep day; every slot cooks
else: walk back from the day before the week starts,
      take the first day you are home;
      none found -> no prep day, and say so
```

### 7.1 The five questions, answered

**Q1 — What counts as "home"? — `Home` is the default when nothing says
otherwise.** Settled: *"Home is default if no location data says otherwise."*
That matches `week.location_rule`, which already collapses "no `base_schedule`",
"unknown location" and "no rule for it" into `{}` — so a config that says
nothing keeps today's behaviour, and an absent day is available rather than
excluded.

**Recommendation stands: a new `allows_prep_session`, defaulting to
`allows_long_cook`.** A long cook claims your *presence*; a prep session claims
2 hours of your *attention*. Today's config needs no change and the two can
diverge later.

**Q2 — How far back may the walk go? — two days, and no further.** Settled:
*"prep days is always in the days BEFORE the meal plan starts — no point
prepping during the week, or at the end of the week. So prep day is either the
Sat/Sun before the meal week."*

That is simpler and better than the fridge-window derivation this section first
proposed. **Candidates are exactly N−1 and N−2**, and the rule needs no
arithmetic: prepping mid-week is not a prep day, it is just cooking, and the
grid already has a slot for that.

**Q3 — Is the default Sunday? — no, it is N−1**, which is Sunday only because
`week_start_day` is Monday. Keep it an offset; change the week start and the
candidates move with it, correctly.

**Q4 — When no prep day is found, announce it. ✅** Confirmed, and it opened a
larger idea — see §7.3.

**Q5 — The shortened window is accepted.** *"No way around — except maybe to
freeze more of the servings."* Which is exactly right and is worth building as
guidance rather than leaving as a realisation: when prep falls to N−2, a batch
loses a day of reach, and **the app should say so and offer to push the
displaced portions to the freezer** rather than silently planning one fewer
leftover. The information is all there at the moment the grid is built.

### 7.2 The flag is the preset's half

*"The part which SHOULD be part of preset is a feature flag: does the week
include bulk prep day."* Agreed, and it composes cleanly with `design-02`:

- **flag false** → no prep day, `week_shape.batches` is effectively empty,
  every slot cooks. This is the *"skip bulk prep, full week of cooked meals"*
  case, and note it can now be reached two ways — by the flag, or by declaring
  no batches. **They should mean the same thing**, and the flag should be the
  readable way to say it.
- **flag true** → placement is derived per the rule above, and `week_shape`
  says what gets prepped.

**One consequence worth stating:** the flag is a *preference* and the placement
is a *fact*, so a preset with the flag on can still produce a week with no prep
day — when you are away both candidate days. That is not a conflict; it is the
system correctly reporting that reality did not cooperate, and Q4 is why it has
to say so out loud.

### 7.3 The week briefing — announcing more than the prep day

Q4 asked whether "no prep day this week" should be announced. It should, and
the answer grew: *"it would be good to have a description of the week's menu /
fitness plan to show what is happening this week. Maybe more detailed notes on
each day in the daily view for anything noteworthy — location, fitness,
meals."*

**This is the missing output surface for the entire program.** Presets, blocks,
goals, week shape and prep placement all *decide* things, and until now none of
them *say* anything. A system that removes decisions has to explain what it
decided, or it stops being trustworthy the first time it does something
surprising — and "no prep day this week" is exactly such a moment.

It also costs little: **every input already exists**, and by `design-03` §2 it
is shape 6, a read view. No new mechanism, no new storage.

Two levels, and they answer different questions:

| | Answers | Draws on |
|---|---|---|
| **Week briefing** — one paragraph at the top of Plan | "what am I doing this week, and why" | active preset and its diff from default · the block, its goals and where this week sits in the chain · prep day and why it is where it is · batch shape · anything the run had to report |
| **Day notes** — a line or two per day, in Daily View | "what is different about today" | location and its restrictions · training and its timing · a target that moves · a leftover's source and age · a freezer draw · an in-block boundary |

Three rules it should follow, all from existing house style:

- **Say the unusual, not the routine.** The day's line is silent when nothing
  is noteworthy — the same call the adherence counter makes by staying silent
  until something is marked, so it reports rather than announcing that a
  feature exists.
- **Explain the surprising thing at the moment it surprises.** "No prep day —
  you are out both Saturday and Sunday" belongs in the briefing, not in a log.
- **It is generated from state, never written by the model.** A briefing that
  cost an API call would be a fifth thing that can fail a run, and it would be
  free to describe a week other than the one on screen.

**Scope note:** this deserves its own small design and is *not* part of the
freezer work. Recorded here because Q4 is where it came from.


## 8. Acceptance

- **No `freezer.json` → byte-identical.** Same grids, same shopping lists, same
  generated weeks as today. `extra_portions` keeps behaving exactly as it does
  now for anyone not using the feature.
- A declared item with a `recipe_id` gives its draw correct macros; one with an
  `estimate` uses that; one with neither contributes 0 and the day shows a
  visible shortfall rather than a guess.
- A draw consumes exactly one meal's worth, contributes correct macros, and
  **adds nothing to any shopping list**.
- A draw with nothing declared to satisfy it **warns and still opens the plan**.
- `validate_week` accepts a freezer link pointing outside the week and still
  rejects a `user` or `batch` leftover pointing forwards.
- An item past its class's freezer window (`design-05`) is flagged and **still
  present**.
- **"Send to freezer" on a recipe card** pre-fills label, `recipe_id` and
  `cooked_on`, prompts only for servings, and persists on click.
- **A prep-day batch's surplus is dated from prep day**, not from its anchor's
  grid day (§6a.2) — the off-by-one `keeps_for_days` already paid for once.
- **A batch with `serves: []` fails at load**, naming shopping as the reason.
- **A preset with the prep flag on, and neither candidate day at home, produces
  no prep day and says so** (§7.1 Q4).
- Editing the freezer list by hand survives the next generation, and nothing
  decrements it automatically.
- Prep day moved to Saturday: `max_day_index`, `span_days`, `storage_note` and
  the card badge all shift together, and the shortened reach is visible.

New: `tests/test_freezer.py`. Extended: `test_week_mechanics.py` (the
`validate_week` exemption, the shortened batch), `test_ui_state.py` (draws in
slot views, lot editing).

## 9. Sequence

| | Step | Size |
|---|---|---|
| 1 | **Movable prep day** — independent, no ledger | **S** |
| 2 | `freezer.json`, the declared item, "send to freezer", the confirmation step | M |
| 3 | `LINK_ORIGIN_FREEZER`, draws, the `validate_week` exemption | M |
| 4 | Ageing warning from `cooked_on`, the card badge | S — needs `design-05` |
| 5 | The freezer row editor | S — a copy of the pantry editor |
| 6 | `week_shape`'s `freeze_portions` / `freezer_draws` | with `design-02` |

**Step 1 is worth doing first and alone.** It answers a real question, needs
none of the rest, and moves four consumers through one function they already
share.

## 10. Deliberately not in this design

- **A fourth `SlotSpec.mode`** (§4).
- **A seeded, auto-decremented ledger** (§2). The list is declared and
  confirmed; the app never asserts what is in your freezer. Note the one
  exception this does *not* make: a `freeze_portions` surplus **creates** an
  item (§6a), because that is a declaration made before the run rather than an
  inference made after it.
- **A declared total batch size** (§6a.1) — the surplus is the input.
- **Auto-removing an aged item** (§5).
- **Push assignment** — a cooking week naming the future day a lot is eaten
  (§6). `design-00` F1's line, unchanged.
- **Freezer *space* modelling.** How much room you have is not observable, and
  a capacity nobody can verify is a number that will be wrong.
