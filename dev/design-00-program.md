# Program design — goals, profiles, horizon, library, feedback

Status: **draft for approval.** Nothing here is built. Written 2026-09-01
against `main` at `418c223`, and every claim about the code was checked that
day — per CHANGE-QUEUE.md's own filing rule, a report is verified against the
code on the day it is *filed*, not the day it is picked up. Re-check the
Findings section before acting on any of it.

This is the overview. It states the reframe, the findings that change the
shape of the work, the model, and the sequencing. The per-arm designs
(`design-01…06`) are written only once the decisions at the end are answered
— writing five detailed designs against an unagreed frame is the waste this
project's queue rules already warn about.

## 0. The document set

This file is the overview. Six designs hang off it, and **where a later one
disagrees with an earlier one, the later one wins** — each was written after a
challenge that changed the answer.

| | Is | Authoritative for |
|---|---|---|
| **this file** | the program: reframe, findings, five arms | the **cross-arm** ranking (§6) and the open decisions (§7) |
| `design-01-presets-and-blocks.md` | Arm A — the preset/block mechanism, the weekly pick, NOVA, the lazy-week axis | what a preset and a block *are* |
| `design-02-week-shape.md` | `week_shape` — the largest thing a preset carries; replaces the two batch toggles | the grid declaration and its scar history |
| `design-03-interface-feasibility.md` | what the front end can actually build | **the build order, and the schema constraints that overrule 01 and 02** |
| `design-04-freezer-and-prep.md` | Arm B — the declared freezer list, the surplus path, a movable prep day | how food crosses weeks, and where prep-day logic lives |
| `design-05-food-safety.md` | storage windows as a property of the dish | **every storage number.** It moved the fridge default 3 → 4, which corrected worked examples in 02 and 04 |
| `design-06-exercise-planning.md` | Arm E's first useful slice — personal constraints, gym programs, workout generation and progression | **the separation between persistent limitations and weekly program choice; age activates nothing** |

`design-03` was written capability-first, in answer to *"the profile rules will
be driven by what is possible — not necessarily what I want."* It corrects two
things in the earlier documents and adds one requirement neither had; they are
listed in its §7.

**There are four sequencing tables across the set and they answer different
questions.** §6 below ranks the *arms* against each other. `design-03` §8 is
**the authoritative build order** for Arm A, because it is ordered by what is
cheapest to actually build. `design-01` §11 and `design-02` §10 are
within-scope views that defer to it.

---

## 1. The reframe

The brief asks for two new concepts — **meal plan profiles** and **meal plan
goals**. The app already has exactly one of each. They are not named, not
switchable, and not dated; they are smeared across `config/` as constants:

| The thing | Where it lives today |
|---|---|
| "How I want to eat and cook" — the **profile** | `weekly_schedule`'s carb-cycling shape, `week_defaults`, `meal_weights`, `active_diet_styles`, `sourcing`, `inventory_rules`, the two batch toggles |
| "What I am trying to achieve" — the **goal** | `target_weight_kg` + the deficit slide in `nutrition_engine`, `protein_multiplier`, `training_schedule` |

So this is not "add a new abstraction". It is **naming a concept that already
exists as scattered constants and making it switchable and dated.** That is a
much smaller and much more tractable job, and it is the same move this
codebase has made twice already and recorded both times: `target_modes` named
"who owns this number" out of an unstated default, and `MACRO_KEYS` turned out
to be answering two questions at once. Neither added a concept; both named one.

**But there are three concepts here, not two, and collapsing two of them is
what would make the result rigid again.** The distinction is *lifetime*:

| | Lifetime | Expires? | Example |
|---|---|---|---|
| **Profile** | Standing | No | "I shop at Coles"; "office lunches are leftovers"; "no snack slot" |
| **Block** (the brief's "goal") | Dated span | **Yes, on its own** | "Fast 800 for 4 days"; "2 weeks keto"; "carb-load into Sunday's ride" |
| **Week shape** | One run | Already gone | Which slots cook, the two batch toggles, a hand-set cuisine |

The brief calls #2 a goal and asks for it under #1's name ("a set of meal plan
profiles where these options are defined"). The split matters because they
need different machinery: a profile is a **default**, and merges into config
exactly the way `CONFIG_FILES` already merges five files into one dict — so
nothing downstream needs to know it exists. A block is a **dated override that
must expire without anybody clearing it**, and *nothing in `config/` is dated
today*. That is the genuinely new mechanism, and it is the whole of Arm A.

The psychological argument in the brief is the right one and it is the
strongest justification in the pile. `docs/rapid-weightloss.md` is explicit:
Total Diet Replacement gets its adherence almost entirely from **removing food
decisions**, not from its macros; and in the CSIRO trial of 78,000 members,
the cohort actively using automated meal planning lost **24% more weight at 12
weeks** than low-engagement members. "It's all defined in the goal + profile"
is not a convenience feature. It is the intervention.

### The rule this program is now held to

Stated 2026-09-01, and it governs every arm rather than only Arm A:

> **No new logic goes into the default meal plan. Every new piece of
> customisation is expressed as a preset, wherever it can be.**

`design-01` §3.4 already draws the line this needs — *data describes what you
want; code describes how it is achieved* — but it drew it as a statement about
the preset schema. This promotes it to a **constraint on what may be built at
all**, which is a different and stronger claim: a behaviour that would land as
another Python constant or another `planning_rules` key nobody can reach is
not a smaller version of the feature, it is the problem this program exists to
undo.

Two consequences, and the second is the work nobody has scheduled.

**Every future change is now testable against one question:** *can a user turn
this off, or change it, without editing Python?* If not, it needs a preset key
before it ships. The §10 acceptance test is the same rule read backwards — no
`presets.json` means a byte-identical week, which can only hold if every
behaviour a preset varies is reachable from data.

**The audit this implies has not been done.** §3.4 names `apply_batch_
selections` as the hard-coding being objected to, and `design-02` turns it into
records. That is one behaviour. Nothing has checked the rest, and a first pass
against `main` finds planning decisions that are neither preset-able today nor
claimed by any preset dimension in `design-01` §9.2 or `design-03` §6:

| Hard-coded today | Where | Claimed before? | **Verdict** |
|---|---|---|---|
| bulk prep claims lunches, long cook claims dinners, both from day 1 | `apply_batch_selections` | ✅ `design-02` `week_shape` | **data** |
| weeknight/weekend prep ceilings (30 / 180 min) | `planner.py` constants | ⚠️ the *dimension* was claimed; **no config key exists** | **data** |
| where the day's energy sits across meals | `DEFAULT_MEAL_WEIGHTS` | ✅ §9.2 `meal_weights` | **data** |
| **a morning gym session's breakfast is a shake** | `WORKOUT_BREAKFAST_STYLE` | ❌ nothing | **data** — the nutritional case is a *default*, not a lock |
| **which sessions count, and how early** | `WORKOUT_BREAKFAST_TYPES`, `MORNING_TRAINING_CUTOFF` | ❌ nothing | **data** |
| **cuisines run in 4/3 contiguous blocks** | `cuisine_block_pattern`, `min_baseline_cuisine_share` | ❌ in `engine.json`, not a preset key | **data** |
| **the weeknight elapsed-time ceiling (90 min)** | `WEEKNIGHT_ELAPSED_LIMIT_MINUTES` | ❌ nothing | **data** |
| **how many favourite slots are auto-pinned, and the reuse windows** | `planning_rules` | ❌ nothing | **data** |
| 🆕 **the dinner protein repeat cap and the consecutive-night rule** | English inside `DINNER_VARIETY_RULE` | ❌ unreachable | **data** — needs a builder first |
| 🆕 **what counts as a long cook at all** (60+ min) | **four prose copies**, incl. a Pydantic field description | ❌ unreachable | **data** — one key, four readers |
| 🆕 **per-ingredient portion caps** (2 slices of toast, 45 g powder…) | English inside `PORTION_DENSITY_GUARD` | ❌ unreachable | **data** |
| 🆕 **which days are the weekend** | `WEEKEND_DAYS` | ❌ nothing | **data**, over a `base_schedule`-derived default |
| 🆕 **when each meal is eaten**, deciding pre- vs post-workout | `MEAL_TIME_OF_DAY` | ❌ nothing | **data** |
| 🆕 how a workout's burn splits carb/protein, and the pre-workout gap | `TRAINING_INTENSITY_SPLIT`, `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES` | ❌ nothing | **data** |
| 🆕 how many whole foods are nudged per run | `NUDGE_FOOD_SAMPLE_SIZE` | ❌ nothing | **data**, with its non-monotonicity as help text |
| 🆕 how many people a meal serves | `serving_rules.servings_per_meal` | ✅ reachable | **data** — guests are a week, not a life change |
| 🆕 variety pressure, rejection decay, reheat estimate | `protein_avoid_window` et al. | partly reachable | **data** |
| 🆕 portion-trim clamps, history size, fridge-safe days | `planning_rules`, `week.json` | ✅ reachable | **config, not preset** — see below |
| 🆕 meal-type order, `MACRO_KEYS`, `PREP_DAY_INDEX`, `SEAFOOD_TERMS`, the under-target warning, prompt *wording* | constants | ❌ | **code** — six rows, each failing a stated test |

**✅ Resolved 2026-09-01 by `PROMPT-7`, then re-run the same day with the
burden of proof reversed.** The full audit is `design-01` §3.4a.

The first pass put the burden on `data` — a row had to earn its way out of
Python — and was corrected on the requirement being restated: *"presets should
be the predominant way to customise meal planning; everything should be on the
table."* **The burden now sits on `code` and `config`**, and a row is `data`
unless it can be shown not to be. That reversal moved eight rows and cut
`code` from eleven to six. Five outcomes matter upward:

- **The reversal was the right call and the first pass shows why.** Rows were
  ruled `config, not preset` on the reasoning that no *mood* varies them —
  but the question is whether a **week** does, and a week with guests, a long
  weekend, a training block and a comfort week are all weeks. `servings_per_
  meal` and `WEEKEND_DAYS` both failed the wrong test.
- **"Everything on the table" is a claim about the schema, not one screen.**
  §3.4a resolves it as tiered disclosure: every `data` row is a preset key with
  no exceptions, and §9.2's editor opens nine groups with ten behind a fold.
  `design-03` §1's cost rule still holds — nearly every added row is
  `ui.number` — but panel *length* is a real cost that shape-counting misses.
- **Three rows are unreachable rather than merely hard-coded**: the dinner
  protein rules, the per-ingredient portion caps, and the long-cook threshold —
  which turns out to be **one number written in four separate prose copies**,
  including a Pydantic field description the model reads as schema. That is the
  `sorted(categories)` shape `DEPARTMENT_ORDER` closed, found only because the
  reversal forced a sweep of the prompt constants for embedded numbers.
- **`planning_rules` is one `CONFIG_FILES` key and presets replace keys whole**,
  and the reversal took the trapped rows from five to nine — over half the key.
  The recommendation therefore hardened from "the editor writes the whole
  object" to **splitting `planning_rules` in `CONFIG_FILES`**. It is a
  migration plus a `test_config_layout.py` snapshot regeneration, and it should
  land with `PROMPT-8`, not be discovered during `PROMPT-9`.
- **One row is settled by taking it off the table entirely.** Decided
  2026-09-01: `inventory_rules.fridge_safe_days` *"needs to be a per-dish
  measurement, not part of preset."* That is `design-05`'s design, and the
  argument is stronger than "a mood must not vary food safety" — **one global
  number is wrong in both directions at once** (a stew keeps 4 days, a rice
  tray bake keeps 2, the app says 3), so a preset could only ever have picked a
  different wrong global. It splits into a config reference table plus a
  measured `Recipe.storage_class`, which passes F5's rule — a rice tray bake is
  `rice_or_pasta` at two servings or six, so the class is scale-invariant while
  the verdict it feeds stays derived.

One defect was found and filed rather than fixed
(`inventory_rules.perishable_day_gap` is validated and read by nothing); the
audit changed zero lines of `src/`.

---

## 2. Seven findings that change the shape of the work

These are why the plan below is not the plan the brief implies.

### F1 — A longer grid is expensive, and the four stated needs do not buy it

`SlotSpec.day` is a weekday **name** and a `slot_id` is `"Monday:dinner"`. A
14-day plan has two Mondays and every id collides. That id is load-bearing in
at least six places (`WeekPlan.failures`, `adherence.json`, `rejections.json`,
shopping windows, the card grid, telemetry), so date-based ids are an L that
touches most of the repo.

The first draft of this finding claimed a longer grid was "almost certainly
never" needed. That was stronger than the evidence supports, and it was
challenged on 2026-09-01. **The defensible claim is narrower: none of the four
horizon needs in the brief buys a longer grid, and one of them is already
solved.** Taking them one at a time:

| The ask | What it actually needs | State |
|---|---|---|
| "bulk cook for multiple weeks, store in freezer" | Portions that **survive between weeks** | Half-built — see F2 |
| "2 weeks of keto" | A **block that outlives one week**. Each week is still planned as a week | Arm A |
| "away Sunday — prep Saturday?" | Prep day **movable**. `PREP_DAY_INDEX` is a constant `-1` and four callers read it through one function | S |
| "large Sunday prep catering for multiple weeks" | Same as the first | |

**The shopping objection is the strongest one against this, and it turns out
to be already handled.** Cooking two weeks of food in one session means buying
two weeks of ingredients in one trip, which looks like it needs a 14-day plan.
It does not: `week.portions_for` is `claims x servings_per_meal +
extra_portions`, `build_cook_event` scales the recipe to that full figure, and
shopping aggregates cook events. **So the freezer portions are already bought,
in the week that cooks them.** Verified in `planner.build_cook_event` and
`week.portions_for`. Week two then draws from the freezer and buys nothing for
those slots.

**What a 7-day grid genuinely gives up.** One thing, and it should be stated
plainly rather than argued away: you cannot, at cook time, schedule a frozen
batch to a *named day in a future week*. The freezer ledger is a **pull**
model — week two's planner sees "12 portions of curry in stock" and assigns
them — not a **push** model where week one dictates when they are eaten.

That is a real difference, and the pull model is arguably the more honest one:
it is how a freezer actually works, and a push assignment made three weeks
ahead is a commitment the plan has no way to hold you to. But if you want to
plan a fortnight as a single object and *see* both weeks at once, that is a
legitimate want this design does not serve.

**One thing this finding got wrong, corrected 2026-09-01.** It read "longer
horizon" as a question about the *grid* and answered that. The question was
really about **the plan above the week** — and there wasn't one: blocks existed
singly, with nothing expressing that a restriction phase is followed by a
staged return. `design-01` §4.8 adds the chain, and `docs/rapid-weightloss.md`
describes a **20-week sequence** to fill it. The app plans short (7 days) and
commits long (2–20 weeks), and conflating those is what made a reasonable
instinct look like a request for a 14-day grid. **The grid conclusion below
still stands; it was answering a narrower question than the one asked.**

**What would change the answer.** Any of: wanting to see and edit two weeks as
one grid; a block routinely longer than a week whose *days* need individually
shaping ahead of time; or the freezer ledger proving in practice that pull
assignment loses track of what was cooked for what. If one of those turns up,
date-based slot ids are the right fix and they deserve their own design — not
a widening of this one.

One thing worth knowing either way: `keeps_for_days` is computed from the last
*claiming* slot, and extra portions have no claiming slot. So nothing today
tracks how long a frozen portion keeps. That is a gap the freezer ledger has
to close regardless of which horizon model wins.

### F2 — The freezer is half-built already, and the missing half is the ledger

`SlotSpec.extra_portions` exists, is documented as *"Spare portions to freeze,
on top of the slots claiming this cook"*, and is genuinely counted into
`week.portions_for`. **Nothing receives them.** Grep finds five references,
all of them arithmetic or validation; there is no store, so the portions are
cooked, counted, paid for in the shopping list, and then cease to exist as far
as the app is concerned.

That is precisely the pattern CHANGE-QUEUE.md has now closed three times from
the storage side — sleep, activity and `net_calories` were each fetched on
every sync and read by nothing, and each turned out to be the *enabling half*
of a ranked item rather than a tidy-up. This is the same shape seen from the
other end: written, never read. The freezer ledger is not a new feature so
much as the missing consumer of one that already ships.

### F3 — The two most-felt rigidity complaints are capability probes, not features

"Steak on Wednesday" and "Fast 800 for 4 days" are **examples chosen to show
why presets are wanted**. Neither is a behaviour this program proposes to
build, and neither may reach default plan logic — corrected 2026-09-01, because
the paragraph that used to close this finding read as a commitment to ship both
behaviours early, which was never the claim.

What each one probes is whether the mechanism underneath can express a rule
**a user writes**. In both cases it nearly can already, and that is the finding.

- **"Steak on Wednesday."** `SlotSpec.recipe_id` already exists, is honoured
  end to end (the slot stays a cook, portions derive, shopping aggregates it,
  `span_days` works), and is set *only* by the automatic LRU picker
  `select_favorite_assignments`. There is a post-generation swap
  (`swap_slot_with_favorite`) and **no pre-generation user pin**. So the
  affordance is missing over a field that is already fully wired. That is a UI
  change plus one state method, not a new mechanism.
- **"Fast 800 for 4 days."** `active_diet_styles` is whole-config, so the
  ceiling applies to all seven days — but `diet_style_calorie_ceiling` is
  already applied **per day**, inside `hydrate_dynamic_targets`, taking a
  `min()` against that day's final calories. Day-scoping it is a change to the
  signature of one function and its two callers. The hard part (idempotence
  across the two hydration passes, applying after the training uplift, never
  overriding a stated target) is already solved and tested.

**What ships in each case is an empty affordance, not a rule.** PROMPT-3
delivers a pin control with nothing pinned; PROMPT-2 delivers day-scoping over
an `active_diet_styles` that ships `[]` and stays `[]`. That is `design-03`
§4.1's rule applied — *absence resolves to exactly today's behaviour* — and it
is what makes both safe to build before the preset schema is settled: an
unexercised capability changes no generated week.

Both are early because they **widen what a preset can eventually say**, not
because either behaviour is wanted by default. They buy the most expressible
surface per line changed.

**One question they leave open, and it is the user's.** Day-scoped diet styles
are plainly preset-shaped — a mode is exactly the thing that says "restricted
for four days". A *recipe* pin is not obviously so: `design-01` §8 files it
under what stays yours week to week, where a preset carrying `recipe_pins`
would make it a standing rule instead. Both are buildable on the same field;
they differ only in which surface owns the row. See `design-03` §2 — either way
the row is three selects.

### F4 — The catalog cannot serve library-first planning, and metadata is the blocker

91 records. Every one of them is missing what a library-first planner would
have to query on:

| Field | Present |
|---|---|
| `cuisine` | **0/91 — `Recipe` has no such field at all** |
| `total_time_minutes` | 0/91 |
| `long_oven_cook` | 9 true, 77 false, 5 null |
| `bulk_prep_friendly` | 54 true, 32 false, 5 null |
| per-recipe macros | never stored; derived from `ingredients` |

Cuisine lives on the **slot**, not the recipe — that is why the field does not
exist. The catalog is queryable today by `meal_type`, favourite flag and a
name substring (`repository.catalog_matches`) and by nothing else. Any plan
that starts "pick from the library" is blocked on this backfill, and the
backfill is a schema change plus 91 model calls.

### F5 — Store a measurement if it is scale-invariant; never store a verdict

> *"If we have this, do we also need to label as 'high protein', low cal,
> keto, nova 1-3 compliant?"*

This finding has been wrong twice and is on its third statement. Draft one
said "derive, don't store". Draft two conceded that an externally published
nutrition figure is an observation worth keeping. **Draft three concedes the
larger point: `nova: <score>` is a perfectly good stored key, and the argument
that ruled it out does not apply to it.**

The rule is two clauses, on two independent axes:

> **Store a measurement iff it is scale-invariant. Never store a verdict.**

**Axis 1 — scale-dependence.** This app rescales recipes at ten call sites
(`fit_recipe_to_budget`, `scale_to_servings`, `single_serving`, all via
`resize_by_factor`). `Ingredient.scaled` updates `NUTRIENT_KEYS` and
`quantity_g` — **and nothing else.** So absolute macros are scale-dependent and
a stored total is a fact about one *scaling* of a record rather than about the
record. `nova_group` is provably untouched by that method, so a NOVA score is
**scale-invariant**, and the whole rescale argument simply does not reach it.
So are cuisine, elapsed time, and every macro *ratio*.

**Axis 2 — measurement or verdict.** A verdict is a measurement compared
against a threshold, and in this app the threshold is **config the user
edits**. `allowed_nova_groups` and `banned_ingredients` are keys in
`profile.json`.

That is the distinction the earlier drafts missed by writing "NOVA 1–3
compliant" in the same cell as everything else. `nova: 3` and
`nova_compliant: true` are different objects:

| | Is | On config? | Verdict |
|---|---|---|---|
| `nova: 3` | a **measurement** of the food | no | **store** |
| `nova_compliant: true` | a **verdict** against `allowed_nova_groups` | **yes** | derive |

Ban an ingredient tomorrow and every stored verdict is instantly wrong,
nothing recomputes, and a mislabelled recipe is a well-formed record that
raises no error. The score is unaffected, because it never claimed anything
about the config. `banned_ingredients` is deliberately a **single lever** — ban
an item and it stops being suggested as well as rejected — and a stored verdict
re-splits that lever where a stored score does not touch it.

Applying both clauses:

| Field | Scale-invariant? | Verdict? | |
|---|---|---|---|
| calories, protein, carbs, fat, fibre | **no** | no | derive (`Recipe.total_macros`, already a property) |
| **`nova`** (recipe score) | **yes** | no | **store** |
| `cuisine` | yes | no | store — and judged, so nothing could derive it |
| `total_time_minutes` | yes | no | store |
| `source_nutrition` (a site's published figure) | yes as published | no | store, as a **cross-check** the planner never reads |
| macro *ratios* (protein % of kcal) | yes | no | storable, but a cache is the better call — trivially cheap, and no judgement in it |
| `nova_compliant`, keto, high-protein, "≤30 min" | — | **yes** | derive |

**One thing to settle when the field is added: the aggregation rule.**
`max()` over ingredients is the obvious answer and this codebase has *already
made that choice once* — `shopping.py` takes `max(nova_group)` when merging
duplicate ingredient lines. But it is a genuine judgement, not arithmetic: 200 g
of NOVA-1 chicken plus 5 g of NOVA-3 stock powder is a NOVA-3 dish under
`max()` and essentially NOVA-1 under a mass weighting.

Two consequences. The recipe-level rule should **be the same call the shopping
aggregation makes**, or two places will answer "what is the NOVA of this
combination" differently — which is exactly the silent drift CLAUDE.md records
between `ui_catalog_browser._matches` and the `/api/recipes` filter, where
neither side was wrong and no error was ever raised. And because it is a
judgement, the stored value should carry a **derived default with a human
override**, the same shape as `source_nutrition`: compute `max()`, store it,
flag a disagreement.

**Why this matters more than it looks.** A verdict is cheap to compute and
expensive to store; a measurement is the reverse. Storing `nova` makes the
Library filterable on processing level without re-walking ingredients, which
is precisely the kind of query the middle-path library work (D2) needs — and
it does it without caching a single thing the user can invalidate by editing
config.

**Net effect on the backfill:** it grows by one field. `cuisine`,
`total_time_minutes`, `nova`, and `source_nutrition` where an import has a
published figure. Every nutritional figure the planner reads stays derived.

### F6 — `activity_log` is empty, and it is the floor under the entire training arm

`data/biometrics.json` today:

| Section | Rows (as filed) | Rows (2026-09-01, re-measured) | Read by |
|---|---|---|---|
| `weigh_ins` | 7 | **12** | the engine, Insights |
| `daily_actuals` | 5 | **8** | adaptive TDEE, logged-intake substitution |
| `readiness_log` | 6 | **28** | **nothing** |
| `activity_log` | **0** | **23**, over 16 dates | `propose_training_schedule` |

> **This finding is closed — cause 1, and the backfill is done.** Re-measured
> against `data/biometrics.json` on 2026-09-01: the `--date` backfill across
> the 28 days `propose_training_schedule` reads restored 23 rows over 16
> dates, and its answer went from "nothing recorded" to four proposals.
> CLAUDE.md's "Biometric sync" section carries the full diagnosis. The
> counts above are kept beside the new ones rather than overwritten, because
> the *finding* was true when filed and the sequencing built on it is not
> retrospectively wrong — this is the same device `design-02` §5 uses.
>
> **Two consequences beyond `activity_log`, which is why the whole table was
> re-measured rather than one row.** `readiness_log` went 6 → **28**, so Tier
> 1 item 5 (its first reader, the HRV band) is now working against a month of
> data rather than six rows — its own precondition has quietly been met. And
> `PROMPT-1` is complete against every line of its acceptance: `dev/README.md`
> still ranks it 4th, and should not.

`sync_checkpoints.garmin` is `2026-08-31` — yesterday — so Garmin *has* been
asked, and `sync_garmin` calls `save_activity_entries` unconditionally on
every date. Yet the list is empty. Two candidate causes, and they need
separating before anything is built on top:

1. **The checkpoint hides the history.** `activity_log` shipped after those
   dates were first checked, and `get_sync_date_range` anchors the catchup on
   the checkpoint — so every date that could hold activities is already marked
   "asked". CLAUDE.md names this exact trap for `readiness_log` and says the
   fix is a `--date` re-sync. If so, the whole history is one backfill away.
2. **`_storable` is rejecting everything.** A row needs a mapped
   `GARMIN_SESSION_TYPES` modality *and* a readable local start time. A Fenix 8
   recording `indoor_cycling`, `virtual_ride`, `lap_swimming`, `walking` or
   `strength_training` under a `typeKey` the table does not name is dropped
   silently and by design.

Either way the observable fact stands: **`propose_training_schedule` has never
had a row to propose from, and every signal in Arm E reads this list.**
Diagnosing it is the cheapest highest-leverage thing on the entire list, and
it is unblocked today.

### F7 — The protein floor is below what the research says this deficit needs

Not a code defect — a config number that the research now contradicts, worth
stating because it is the one number that protects the outcome.

`protein_multiplier` is 1.8 against `target_weight_kg` 80, giving **144 g/day**
— locked, deliberately, to the *target* weight so the floor does not shrink as
the diet proceeds. That reasoning is sound and should not change.

The current weigh-in is **99.77 kg at 33.4% body fat**, so fat-free mass is
about **66 kg**. `docs/rapid-weightloss.md` gives two ranges for an aggressive
hypocaloric state: **1.6–2.4 g/kg total mass** (→ 160–239 g) or **2.3–3.1 g/kg
FFM** (→ 153–206 g). 144 g sits **below the floor of both**, on the very
protocol whose defining risk is lean-mass loss — and `apply_protein_floor` is
already reported as tight: 144 g against a 35 g/meal floor over four meals
leaves 4 g of slack across the whole day, which is why `week_defaults.snack`
had to become `skip`.

This is a decision for the user, not a change to make silently — see D3.

---

## 3. The model

### 3.1 Profile

A named bundle of the standing keys. **It merges into the same flat dict
`AppConfig` already validates**, exactly as `CONFIG_FILES`' five core files
do — which means nothing downstream of the repository knows a profile exists,
and `planner`, `week` and `ui_app` keep reading `config["weekly_schedule"]`
unchanged. That is the whole trick, and it is the one the config split already
proved: splitting the *files* without splitting the *object* touched zero call
sites.

A profile may set any ordinary core key. It is a **layer**, not a copy — a profile
states the handful of values it changes and inherits the rest, so a profile
cannot silently pin a value it was never meant to have an opinion about.

**Narrow exception added by `design-06` §3:** persistent personal training
constraints and the gym-program catalog are protected roots, not weekly
preferences. A preset may select `active_gym_program`; it may not replace
`training_profile` or `gym_programs`. Without that rule a weekly "fat loss"
preset could erase a hip limitation while changing an unrelated goal.

**`design-01` §3 is the mechanism, and its first draft did not deliver this
sentence.** Whole-key replacement meant a profile touching one field of
`dietary_rules` owned the whole object and silently dropped the rest — pinning
values it was never meant to have an opinion about, which is exactly what this
paragraph forbids. Typed leaf-path overrides are what make sparse layering
*true* rather than merely intended, and the correction is recorded there.

Candidate profiles, drawn straight from the brief: `default`, `no_bulk_cook`,
`freezer_stock` (big Sunday prep, high `extra_portions`), `simple_repeat`
(shakes for breakfast, soups for lunch — a very short style rotation),
`travel`.

### 3.2 Block

The new thing. A **dated span** with an intent, and the only object in this
app that expires on its own.

```
Block:
  name, starts_on, ends_on            # dates, not weekday names
  body_goal                           # lose | hold | recomposition
  fitness_goal                        # vo2max | strength | maintain
  diet_styles: [...]                  # day-scoped, per F3
  target_rate_kg_per_week | target_weight_kg
  protein_floor_override
  training_intent: vo2max | hypertrophy | fat_loss | maintain
  peak_day                            # "energy for Sunday's ride"
  notes
```

**No `profile` field**, corrected 2026-09-01 — `design-01` §4.1a is the
argument. A block boundary can fall mid-week, and every other field here is a
per-day *number* that `hydrate_dynamic_targets` already looks up per day; a
preset is not, because it replaces leaves consumed once by `default_week_spec`
before hydration runs. A Monday–Thursday block pinning a preset would ask one
flat `AppConfig` to be two objects inside a single call.

Nothing is lost: the brief's own example, "Fast 800 for four days", is a
`diet_styles` activation, and that field is day-scoped. **The preset stays the
weekly pick** and is whole-week by construction.

Four things about it are decisions:

- **It writes to `config/`, and that makes it the third such writer.** The
  existing two — `target_modes` and an accepted `training_schedule` proposal —
  both persist a *standing* setting, and a block passes the same test: it is
  not an input to the next run, it survives a reload, and a block that
  evaporated would be an animation rather than a feature. It goes through the
  supplemental write path `PROMPT-8` §1a adds, **not** `save_config_keys` —
  that method raises on any key `CONFIG_FILES` does not own, and `blocks.json`
  is supplemental for the same reason `presets.json` is.
- **It expires by date, and nothing clears it.** `active_block(today)` returns
  the block covering today or `None`. An expired block is inert, kept for
  history, and never deleted — the same "keep the record, stop citing it"
  treatment `-deprecated.md` gets.
- **Overlap is refused at load, not resolved.** Two blocks covering one date
  is a config error with the dates named, the same loud-failure policy
  `CONFIG_FILES` applies to a key in the wrong file. Picking a winner would be
  a number nobody chose.
- **A block sets targets; it never sets a *number* the engine already owns.**
  A block declaring `target_rate_kg_per_week` feeds the deficit slide; it does
  not write `weekly_schedule` calories. Otherwise it becomes the second source
  of truth that `target_modes` exists to prevent, and the header would preview
  a figure the run does not use.

### 3.3 What the user stops choosing

The point of the whole exercise. With a block active, these are all decided
and the UI should say so rather than offering a control:

calories · protein · deficit rate · diet style · cuisine rotation · whether to
bulk cook · prep day · which training intent · whether this is a deload week.

What the user still chooses: **"steak on Wednesday"**, "not tacos this week",
and marking what actually happened. That is the right division — the research
calls it *flexible restraint*, and names rigid all-or-nothing rule sets as the
thing correlated with regain and with the "what-the-hell effect" after a
minor breach. The system should hold the numbers; the human should keep the
vetoes.

---

## 4. The five arms

Independent, separately shippable, in dependency order within each.

### Arm A — Presets and blocks
`config/presets.json` + `config/blocks.json` as two new **supplemental** files
(missing → `{}`, like `models.json` and `integrations.json`) rather than core
files, so a checkout with neither plans byte-identically to today.

**Three things this arm learned after it was first scoped**, all in
`design-01`: presets are a **catalog plus a weekly pick** rather than a
standing setting, and a block is what *suspends* that pick; the protein floor
became a block property (D3); and NOVA compliance is presettable, with a hole —
the catalog outlives the preset that admitted a recipe, so *selection* has to
filter as well as import.

**Its largest component has its own document.** `week_shape` (`design-02`)
replaces the two batch toggles, moves batch logic out of `ui_generation.py`
into `week.py` — where the CLI can finally see it — and depends on Arm B's
freezer ledger. `design-03` scores every preset dimension and finds nine of
eleven are XS–S; `week_shape` is the outlier, and even its UI is a copy of an
existing pattern.

### Arm B — Horizon without a longer grid
**Designed: `design-04` (freezer, prep day) and `design-05` (storage windows).**
The freezer turned out to be a *declared and confirmed list* rather than a
seeded ledger, which removes the reconciliation problem entirely; and storage
life turned out to belong to the dish rather than to config, which is a bigger
change than this arm was first scoped for.

Three separable pieces, in order:
1. **Freezer ledger** — the consumer `extra_portions` never got (F2). Portions
   in on a cook, portions out when a slot eats from stock. A new `data/` file;
   it must be *derived from and reconciled against* the plans that wrote it,
   not a free-standing count, or it drifts from the actual freezer the way
   CLAUDE.md says a persisted pantry ledger would.
2. **Movable prep day** — `PREP_DAY_INDEX` is the constant `-1`. Making it a
   per-week value answers "away Sunday, prep Saturday" and shifts everything
   that already counts from it (`cook_day_index`, `span_days`, the fridge
   badge, `storage_note`) for free, because they all read that one function.
3. **Block-aware weeks** — the block supplies the week's shape; the grid stays
   7 days.

### Arm C — Recipe library
1. **Metadata**: add `cuisine`, `total_time_minutes` and `nova` to `Recipe`,
   plus `source_nutrition` where an import has a published figure worth
   keeping as a cross-check; backfill the 91 (F4, F5). All four are
   scale-invariant measurements. Every *verdict* — compliance, keto,
   high-protein — and every scale-dependent figure stays derived.
2. **Import by URL** — extend `import_external_recipe` with a fetch step. This
   is the cheap, safe version of "curated sites": the user supplies the URL.
3. **Search over curated sites** — a crawler is a much larger commitment with
   a ToS surface, and should not be attempted before (1) and (2) are earning
   their keep.
4. **Library-first selection** — the XL, and see D2. Note the arithmetic: 91
   recipes across ~7 dinners a week is 13 weeks before a repeat *with no
   variety constraint at all*. **Library-first with today's catalog produces
   more repetition, not less.** Import has to run well ahead of the switch.

### Arm D — Feedback signals
Inventory below (§5). The unblocked slice is small and high-value: fetch the
three missing Garmin metrics, and give `readiness_log` its first reader.

### Arm E — Training engine
`design-06` now splits this arm into a useful first loop and the full controller.
The first loop is personal constraints, selectable gym programs, structured
workout generation, manual limitation feedback, and confirmed progression
proposals (`PROMPT-14`/`PROMPT-15`). It is **not gated on Hevy**: Hevy enriches
progression evidence, while the static plan and manual response remain useful
without it. The later fatigue/deload controller still depends on Arm D's trusted
readiness signals and Hevy performance data. F6 is closed: `activity_log` has
rows.

---

## 5. Signal inventory — what this stack can actually measure

Cross-referencing `docs/fitness-model.md` against what the app fetches today.

| Goal | Signal | Source | Status |
|---|---|---|---|
| Weight loss | **EWMA weight** (α ≈ 10-day) | Garmin scale | ✅ stored. `smooth_series` exists and `measure_weight_trend` reads it |
| Weight loss | **Empirical TDEE** (mean intake + kg/day × 7700) | Cronometer + scale | ✅ built (`calculate_adaptive_tdee`) — blocked on **span**, not on row count |
| Weight loss | **Garmin AEE bias correction** | derived | ❌ new. Formula is in the research; this is the one that fixes chronically-wrong burn figures |
| Body comp | BIA body-fat trend | scale | ✅ stored, unread — and research says **use for multi-month direction only**, MAPE 4–8% |
| Aerobic | **Efficiency Factor** (pace or power ÷ avg HR) | Fenix activity | ⚠️ **reachable** — needs avg HR + avg speed/power, which are activity-*summary* fields. 1–2 week lead on Garmin's own VO2max |
| Aerobic | **Aerobic decoupling** (Pw:Hr) | Fenix activity | ❌ **not reachable from the summary.** Needs first-half/second-half splits — the activity *detail* or laps endpoint. Biggest new fetch in the arm |
| Aerobic | VO2max | Fenix | ❌ not fetched. Lagging (4–8 wk) but it is the brief's stated goal, so it belongs on the chart |
| Aerobic | HR recovery | Fenix | ❌ not fetched |
| Gym | **e1RM** (Epley/Brzycki blend, RIR-adjusted) | Hevy | ❌ no integration. See D4 |
| Gym | **Direct hard sets** /muscle/week (≤ 4 RIR) | Hevy | ❌ — research prefers this over tonnage |
| Gym | Effective volume load | Hevy | ❌ |
| Recovery | **HRV** (lnRMSSD, 7d vs 28d SWC band) | Fenix | ✅ `readiness_log.hrv_ms` **stored, no reader** |
| Recovery | Sleep score / hours | Fenix | ✅ stored, no reader |
| Recovery | **RHR** | Fenix | ❌ not fetched — **cheapest missing signal on the list.** A named trigger in the fatigue matrix (weight 0.5), one endpoint |
| Recovery | Training Readiness (0–100) | Fenix | ❌ not fetched. Firstbeat has already done the compositing |

**Three conclusions worth acting on:**

- **Efficiency Factor is reachable and aerobic decoupling is not.** They are
  usually named together; only one is a summary-level metric. Ship EF; treat
  decoupling as its own fetch with its own cost.
- **RHR is the best value/effort ratio in the whole program.** One endpoint,
  and it is half of the two-signal autonomic trigger the deload engine needs.
- **`readiness_log` needs a reader before it needs more columns.** Six rows of
  sleep and HRV are already sitting there unused. Per CLAUDE.md's own rule,
  an entry in a sync's column list *has to assert that something reads it*.

### On the brief's sub-maximal gym question

> *"not interested in max weight tests, but are there any other sub maximal
> gym tests?"*

The research answers this directly, and none of the four needs a 1RM attempt:

1. **e1RM from a working set at known RIR** — the primary. Blend Epley and
   Brzycki, discard sets above ~10 reps where substrate depletion masks
   mechanical capacity.
2. **RPE drift at fixed load** — the sharpest fatigue signal, and it needs no
   new arithmetic. Same weight, same reps, reported one RPE point higher =
   reduced readiness. The research's worked example: 100 kg × 6 @ RPE 8 → RPE
   9 the following week drops e1RM without a single number on the bar changing.
2. is worth emphasising because it detects fatigue *before* performance falls.
3. **Direct hard sets per muscle per week** (≤ 4 RIR), compounds allocated
   fractionally — target 10–20/muscle/week.
4. **Effective volume load** — volume weighted by proximity to failure, which
   down-weights warm-ups instead of counting them as progress.

**All four require RIR/RPE per set.** That is the single dependency the gym
arm stands on, and it is the first thing to verify about Hevy (D4).

---

## 5a. Research traceability — what `docs/` specifies and where each part lands

Added 2026-09-01 after the question *"were any of the research documents
referenced?"* They were — ten citations across the set — but **unevenly, and
the audit found two real gaps.** This table exists so the next reader can tell
"considered and placed" from "not yet read".

### `docs/rapid-weightloss.md`

| Specifies | Lands in | |
|---|---|---|
| Protein 1.6–2.4 g/kg mass, 2.3–3.1 g/kg FFM in deficit | F7, D3, `design-01` §6 | ✅ |
| CSIRO: automated planning → 24% greater loss at 12 wk | `design-01` §2, §8 | ✅ |
| Rigid vs flexible restraint; the "what-the-hell effect" | `design-01` §4.5, §8 | ✅ |
| Pre-commitment as choice architecture | `design-01` §4.5 — the block's whole justification | ✅ |
| Decision fatigue; TDR removes food decisions | §1 | ✅ |
| VLCD 600–800 kcal; Fast 800 | D1 | ✅ |
| **Refeed: 5 stages, +200–300 kcal per step** | **`design-01` §4.7** | ⚠️ **added late** |
| **Collateral fattening / fat overshooting** | **`design-01` §4.7** | ⚠️ **added late** |
| **Reverse diet: +100–250 kcal/1–2 wk, hold if 7-d avg rises** | **`design-01` §4.7** | ⚠️ **added late** |
| **CSIRO macro split — 30–35% P, 40–45% low-GI C, 20–25% F, >35–40 g fibre** | `design-01` §2 | ⚠️ **added late** |
| **5:2 — two non-consecutive days at 500–600 kcal** | `design-01` §2 | ⚠️ **added late** |
| **Indulgence units — 2 × 400 kJ discretionary/week** | `design-01` §7.3 | ⚠️ **added late** |
| T3 drops 30–55% in 24–72 h; adaptive thermogenesis 50–250 kcal | D1's reasoning | partial |
| 16:8 / TRE feeding windows | — | ⛔ **dropped 2026-09-01**, not important. Would have needed a feeding-window concept the app has none of |
| VLCD micronutrient fortification | nowhere | ❌ open |

### `docs/fitness-model.md`

| Specifies | Lands in | |
|---|---|---|
| Signal inventory across scale / Fenix / Cronometer / Hevy | §5 | ✅ |
| EF reachable from a summary; decoupling is not | §5 | ✅ |
| e1RM formulas, RIR, hard sets, effective volume load | §5, D4, `PROMPT-4` | ✅ |
| BIA MAPE 4–8%, multi-month direction only | `design-01` §6 | ✅ |
| **Reading a rate off a smoothed series understates it ~26%** | — | ✅ **code already does this right**, see below |
| **EWMA α for a ~10-day window (≈0.18)** | — | ⚠️ code uses **0.3** (~6-day), forward-backward. Unreconciled |
| **Garmin AEE bias correction from logged intake × EWMA velocity** | §5 lists it; nothing designs it | ❌ open — **and it is the tool D1 needs** |
| Forbes partitioning (lean-loss fraction) | nowhere | ❌ open |
| HRV: lnRMSSD, 7-d vs 28-d, SWC = 0.5 × SD | §5 names it; the band is not specified anywhere | ❌ open |
| SWS/REM thresholds | nowhere — `readiness_log` has no stages | ❌ open |

**One check that passed, worth recording.** The research warns that reading a
trend off a smoothed series understates a noise-free decline by 26%.
`nutrition_engine` already separates the two jobs deliberately — `smooth_series`
is a **zero-phase forward-backward EMA for display**, and
`_trend_slope_kg_per_day` is **least squares for the rate**, with a docstring
explaining the startup-transient error. The code exceeds the research here
rather than lagging it.

### `docs/periodization-engine.md`

**The most under-used of the three: 346 lines of quantified rules, cited
twice, both times as "Arm E, later".** Nearly all of it is specified enough to
build against today.

| Specifies | Lands in | |
|---|---|---|
| Volume-reduced deload beats cessation | Arm E, in passing | partial |
| **Fatigue matrix — weights 1.0 / 0.5 / 1.5 / 1.0 / 0.5 / 0.5, windows, threshold** | nowhere | ❌ open |
| **Deload structure — 40–50% volume cut, 85–90% intensity held, cap 3–4 RIR** | nowhere | ❌ open |
| **Session separation — 6 h ideal, 3 h absolute floor** | nowhere | ❌ open — **and it reaches the meal planner**, not just Arm E |
| **Goal-specific table** (session order, buffer, modality, frequency, resistance config per goal) | **`design-01` §4.1** | ⚠️ **added late** |
| **Fat-loss deficit lowers trigger thresholds ~20%** | `design-01` §4.7 | ⚠️ **added late** |
| Proactive deload ceiling after N weeks of loading | nowhere | ❌ open |
| Low-eccentric modality preference near lower-body days | nowhere | ❌ open |

### `docs/exercise-protocols.md`

Added after the first audit and traced in `design-06`. It supplies the exercise
prescription and older-trainee outcome layer the first three documents lacked:

| Specifies | Lands in | |
|---|---|---|
| Full-body 2–3-day architecture, exercise roles and modality dosing | `design-06` gym-program schema and initial catalog | ✅ designed |
| RPE/RIR prescription without max testing | `design-06` exercise dose; `PROMPT-4` evidence | ✅ designed |
| Double progression and 2-for-2 load progression | `design-06` §6, `PROMPT-15` | ✅ prompted |
| High-velocity/power work as a distinct role | `design-06` program/exercise schemas | ✅ designed, opt-in |
| Older-trainee recovery defaults and low-impact preferences | selectable gym-program data | ✅ designed, **never age-activated** |
| Personal movement limitation handling | `training_profile` protected from weekly presets | ✅ designed and prompted |
| Chair stand, TUG, grip and VO2 functional outcomes | later Arm D outcome measurement | ❌ open; does not block workout planning |

The important product decision is not a clinical threshold: **birth date does
not choose a program or manufacture a limitation.** The research expands the
catalog of programs the user may select; `training_profile` contains only facts
the user explicitly declares.

### The two gaps that mattered

- **A block had no successor.** `ends_on` and then nothing — while
  `rapid-weightloss.md` devotes a whole section to the fact that **the end of a
  restriction block is the highest-risk moment in the protocol**, with a named
  mechanism (adipose refills faster than lean tissue, the proteinstat keeps
  driving hyperphagia, the surplus lands as fat, and repeated cycles push
  adiposity *above* baseline). Fixed in `design-01` §4.7.
- **`training_intent` was stored as an unread string** with "Arm E, later"
  beside it, when `periodization-engine.md` already defines exactly what each
  value means — and part of that reaches `training_schedule`, which the *meal*
  planner already reads. Fixed in `design-01` §4.1.

Both are the same mistake: **treating a research document as background for a
later arm rather than as a specification for the one being designed.**

## 6. Sequencing

The rank is by *value per unit of risk*, not by how interesting the arm is.

> **This table ranks the *arms*. For the order to actually run the prompts in,
> read `dev/README.md`'s "Order of delivery"**, revised 2026-09-01 and the
> single authority on execution order. Where the two appear to disagree, that
> one wins — it is the one that knows which prompts exist.
>
> **Tier 0, added 2026-09-01 and above everything in this table:** per-dish
> storage windows (`design-05`, `PROMPT-10`). Every other row here enables
> something; that one fixes something already wrong. `inventory_rules.
> fridge_safe_days` is 3 against a 2-day window for rice and pasta, and
> `apply_batch_selections` builds a batch reaching that far on every week the
> long-cook toggle runs. It ranks above Tier 1 on the "value per unit of risk"
> axis this section already uses — the risk is not a worse meal plan.
>
> It also **gates Tier 2 item 7** (the freezer ledger) and Arm A's `week_shape`
> step, both of which extend how far batched food reaches. `design-05` §5's
> rule — the lengthening and the dish-level exception land together or not at
> all — applies to the work around it as well as to the change itself.
>
> **Tier 1 item 1 is done.** See §5a: the backfill ran, `activity_log` holds 23
> rows over 16 dates.

**Tier 1 — unblocked, cheap, and each one removes a felt problem.** These do
not wait on any decision below.

| | Work | Arm | Size |
|---|---|---|---|
| 1 | Diagnose the empty `activity_log`; backfill (F6) | E | XS–S |
| 2 | Day-scoped diet styles — "Fast 800 for 4 days" (F3) | A | S |
| 3 | User-driven pre-generation recipe pin — "steak on Wednesday" (F3) | A | S |
| 4 | Fetch RHR, VO2max, Training Readiness | D | S |
| 5 | Give `readiness_log` its first reader | D | S |

**Tier 2 — the enabling halves.** Nothing above them in value, but everything
later depends on them.

| | Work | Arm |
|---|---|---|
| 6 | `Recipe.cuisine` + `total_time_minutes`, backfill 91 records | C |
| 7 | Freezer ledger — the consumer `extra_portions` never got | B |
| 8 | Profile + block model, as supplemental config | A |

**Tier 3 — needs a decision first.** D1–D5 below, three of which are now
answered.

**This table ranks the arms against each other. It does not order the work
inside one** — for Arm A that is `design-03` §8, which is ordered by build
cost and supersedes the Tier 2 placement of the preset model above.

**Tier 4 — the large ones.** Library-first selection; the full periodization
controller; aerobic decoupling; curated-site search. The smaller exercise-plan
substrate and first progression loop are now designed separately in `design-06`
and ordered by `dev/README.md`.

The ordering claim worth defending: **Tier 1 items 2 and 3 are the ones the
brief is most frustrated by, and they are among the cheapest things in the
document.** Doing them first buys the most felt flexibility per line changed,
and neither commits the design to anything in Tier 3.

**Both were re-checked against §1's rule on 2026-09-01 and both pass**, which
is worth recording because the rule looks like it should have reordered them
and does not:

- **Day-scoped diet styles widen a key a preset will later carry.** A preset
  overrides `dietary_rules.active_diet_styles` as a leaf (`design-01` §3, as
  amended), so the day-scoped shape landing in `profile.json` first is the
  preset layer's *input format*, not a rival home for it. Nothing has to move
  later. PROMPT-2 already says this — and now settles the schema itself rather
  than deferring it, since `design-01` §5 makes it the substrate a block
  reuses.
- **The recipe pin adds no default logic at all.** It writes to the week spec,
  which is thrown away after the run — the "week shape" lifetime in §1's table.
  A veto the user types is not customisation compiled into the plan; §8's
  flexible-restraint argument is that some of these should *stay* manual.

So the rule constrains what may be **compiled in**, and neither of these is.
The work it actually creates is the audit in §1, which is new and unranked.

---

## 7. Decisions that block

Each changes what gets built, not merely how. **Three were answered on
2026-09-01** and are kept with their answers rather than deleted — a closed
decision is a change of fact for whatever cited it, and CHANGE-QUEUE.md's own
rule is that a closed row's *reasoning* goes stale even though its verdict
cannot.

### D1 — How aggressive is the weight-loss target? — **OPEN, and it is a data question, not a preference**

**It does not depend on the Hevy key.** D4 does; this does not.

Re-checked against live data on 2026-09-01, and the finding changes the
question. **The adaptive TDEE estimate became available for the first time
today** — `measure_adaptive_tdee` returns `state: ready`, on a span of exactly
7 days against a floor of exactly 7. `design-00` was written days earlier
saying this precondition was unmet; it is now met.

And the estimate is immediately rejected:

| | |
|---|---|
| formula TDEE | **2573** kcal (BMR 1871 × 1.375 `light_office`) |
| adaptive TDEE | **1710** kcal |
| gap | **33.5%**, against `ADAPTIVE_TDEE_TOLERANCE` of 25% |
| verdict | **rejected** — `reconcile_adaptive_tdee` keeps the formula and logs `formula_adaptive_rejected` |

That is the one `tdee_source` state CLAUDE.md calls worth investigating —
*measured and disbelieved*, as distinct from *nothing to measure*.

**The measurement is almost certainly wrong, and there is a physiological tell
rather than just a suspicion.** 1710 kcal is **below the computed BMR of
1871**. Holding weight while eating under basal metabolic rate is not
coherent, so the intake side is under-reported — exactly the failure
`reconcile_adaptive_tdee`'s bound exists to catch, and the direction the
research says systematic under-logging always errs in.

The log supports that reading: **5 logged days out of the 7-day span**, one of
them 429 kcal (a partial sync or a partly-logged day), and weight essentially
flat at +0.06 kg across the window.

**So D1 cannot be answered as a preference yet.** Choosing a deficit means
choosing a fraction of TDEE, and TDEE is currently contested by ~860 kcal —
wider than most deficits. What resolves it is not a decision:

1. **14 consecutive complete days in Cronometer.** The window is
   `measure_adaptive_tdee`'s own default of 14; completeness matters more than
   count, since one 429 kcal day drags the mean.
2. Re-check the estimate. Inside 25% of the formula it is accepted and becomes
   the planning basis; still outside and the gap itself is the finding.
3. *Then* choose the rate.

**Partly defused by D3's answer.** Since the protein floor became a block
property, aggressiveness is per-block rather than one global setting — so the
first block can run conservatively off the formula while the measurement
accumulates, and nothing has to be decided in advance of the data.

### D2 — Library-first, or AI-first with a bigger library? — **ANSWERED: middle path**
Raise the share of slots filled from the catalog (today ≤ 5/week, and only by
automatic LRU) **without inverting generation**. The repetition arithmetic in
Arm C is why: 91 recipes across ~7 dinners a week is 13 weeks before a repeat
*before any variety rule*, so full library-first with today's catalog produces
more repetition, not less. Import runs hard first; the selection question is
re-asked when the catalog is big enough to answer it.

### D3 — Does the protein floor move? — **ANSWERED: it becomes a block property**
144 g stands until Arm A ships. A block then declares a basis
(`target_weight` / `ffm` / `current_weight` / bare grams), **resolved once at
block start and frozen** so BIA noise cannot move a day's protein target. Full
reasoning in `design-01` §6, including why an unaffordable floor is reported
rather than corrected.

### D4 — Hevy: does the API expose RIR/RPE per set? — **ANSWERED: yes, RPE per set**

Resolved 2026-09-01 from the public type definitions of the `go-hevy` client,
without needing an API key. **Every input the four sub-maximal metrics need is
present:**

```
WorkoutSet{ Index, Type, WeightKg*, Reps*, DistanceMeters*,
            DurationSeconds*, RPE*, CustomMetric* }
WorkoutExercise{ Index, Title, Notes, ExerciseTemplateID, SupersetID*, Sets[] }
Workout{ ID, Title, Description, RoutineID, StartTime, EndTime, ... }
ExerciseTemplate{ ID, Title, Type, PrimaryMuscleGroup,
                  SecondaryMuscleGroups[], Equipment, IsCustom }
SetType ∈ { warmup, normal, failure, dropset }
```

| Metric (`docs/fitness-model.md`) | Needs | Hevy field |
|---|---|---|
| **e1RM**, Epley/Brzycki blended, RIR-adjusted | load, reps, proximity to failure | `WeightKg`, `Reps`, `RPE` |
| **RPE drift at fixed load** — fatigue *before* performance drops | RPE per movement over time | `RPE` + `ExerciseTemplateID` |
| **Direct hard sets** per muscle per week (≤ 4 RIR) | set type, muscle, proximity | `SetType`, `PrimaryMuscleGroup` + `SecondaryMuscleGroups`, `RPE` |
| **Effective volume load** | proximity to failure | `RPE` |

Four things follow, and three are gotchas worth having in writing:

- **Hevy stores RPE; the research speaks RIR.** `RIR = 10 − RPE` on the
  Zourdos scale `docs/fitness-model.md` uses. One conversion, in exactly one
  place, or the two vocabularies will drift the way `_matches` and the
  `/api/recipes` filter did.
- **`RPE` is a nullable pointer, so the field existing is not the same as your
  data having it.** That is the residual unknown and it needs one authenticated
  call — see `PROMPT-4.md`. The API question is closed; the account question
  is not.
- **`SecondaryMuscleGroups` is exactly the compound allocation the research
  describes** — "1 set of back squats contributes 1.0 direct set to the
  quadriceps and 0.5 indirect to the glutes". It maps without inventing a
  table.
- **Muscle group is on the *template*, not the workout**, so hard-sets-per-
  muscle needs a second endpoint and a local template cache. Not hard, but it
  is a second fetch nobody would predict from the workout payload.

**Arm E is therefore unblocked at the API level.** PROMPT-1 has since filled
`activity_log`; only Hevy-backed progression and the later fatigue matrix wait
on the one-call account check. `design-06`'s static plan and manual-feedback
path deliberately do not.

### D5 — Which arm first? — **ANSWERED: Arm A**
And it has since grown two documents (`design-02`, `design-03`). Tier 1 stays
worth doing regardless of this answer.

## 8. What this deliberately does not propose

- **Date-based slot ids.** F1 says the four stated horizon needs do not buy
  them, and names the three things that would change that answer. If one turns
  up, it is its own design — not a widening of this one.
- **A daily readiness *adjustment* to targets.** CHANGE-QUEUE.md already
  carries the morning-readiness item as blocked on one decision, and
  CLAUDE.md is explicit that no conversion of a sleep score or an HRV
  millisecond figure to kcal could be legitimate. Reading the signal and
  *reporting* it is Tier 1; letting it move a calorie target is not, and
  should stay a separate decision.
- **Storing any derivable nutritional label** (F5).
- **A crawler over curated recipe sites** before URL-paste import has proved
  itself.
