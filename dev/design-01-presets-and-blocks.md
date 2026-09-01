# Design 01 — Presets and blocks (Arm A)

Status: **draft for approval.** Nothing built. Depends on
`dev/design-00-program.md`; read its §1 (the reframe) and findings **F3** and
**F7** first.

Decisions already taken, from the 2026-09-01 workshop:

- **Arm A goes first.**
- **Middle path on the library** — raise the share of slots filled from the
  catalog, do not invert generation. Arm C, later.
- **The protein floor becomes a block property** rather than moving now. That
  answer lands the whole of D3 inside this design, and it means 144 g stands
  until this arm ships.

---

## 1. A naming problem, taken first because everything downstream inherits it

The brief says **profile**. `config/profile.json` already holds
`user_profile` — the body: birth date, height, target weight, activity level.
A "meal plan profile" and a "user profile" are different objects, and one word
would name both.

This project has a standing habit of hunting exactly this: `MACRO_KEYS` was
found to be answering two questions at once, and the daily fibre target was
mis-filed as "changes what a macro budget is" purely because of it. A name
doing two jobs here would be worse than either of those, because it would sit
in a filename.

**Recommendation: call the standing bundle a `preset`.** Short, unambiguous,
and nobody reads `config/presets.json` as being about a person. The dated
object stays a **block**, which has no collision. This document uses those two
words throughout; if you prefer the brief's original wording, the substitution
is mechanical but it should be decided before a file is created rather than
after.

| Term | Is | Lives in | Chosen |
|---|---|---|---|
| **preset catalog** | the named modes available — vocabulary | `config/presets.json` | grows; entries never expire |
| **active preset** | the mode *this week* runs under | recorded per week (§4.6) | **weekly** |
| **block** | a dated commitment that *pins* the preset for its span | `config/blocks.json` | **expires on its own** |
| `user_profile` | the body | `config/profile.json` (unchanged) | n/a |

**The first draft called a preset "standing, no expiry" and that was wrong**,
corrected 2026-09-01: *"Profiles are not set/forget… I might want to eat
comfort food as I've had a bad week, or knuckle down and choose Fast 800 for a
2-week block."* A preset is picked at the **start of a week, against how you
feel**, which makes it neither permanent nor dated.

The shape that fits is one this codebase already uses three times — a
**catalog plus a selection made against it**. `cuisines` is vocabulary and the
per-block pick is the choice; `meal_styles` is vocabulary and
`resolve_auto_choices` makes the pick; `diet_styles` is vocabulary and
`dietary_rules.active_diet_styles` is the standing choice. CLAUDE.md states
the pattern outright: *"The catalog is vocabulary, `dietary_rules` is the
standing choice made against it."* Presets are the fourth instance, and the
selection cadence is weekly.

---

## 2. Storage: two supplemental files

Both are **supplemental**, joining `models.json` and `integrations.json` rather
than the five core files. `CONFIG_FILES`' two-tier split has one test and this
passes it cleanly: *a missing core file is fatal because every core key has no
safe default; a missing supplemental file resolves to `{}` because every value
in it has an in-code fallback.* A checkout with neither file plans exactly as
the app does today — which is also the compatibility claim this whole arm is
accepted against.

They are **not** added to `CONFIG_FILES`, and their keys never appear in
`AppConfig`. Same as the other two supplementals, and for the same reason: the
manifest answers "which core file owns this key", and neither of these files
owns core keys — they *layer over* them.

### `config/presets.json`

```json
{
  "active": "default",
  "presets": {
    "default":       { "label": "Standard week",     "overrides": {} },
    "comfort":       { "label": "Bad week — take it easy",
                       "overrides": { "dietary_rules.allowed_nova_groups": [1,2,3,4] } },
    "lazy":          { "label": "Minimum effort",     "overrides": { "...": "see §7.3" } },
    "fast_800":      { "label": "Fast 800",           "overrides": { "...": "..." } },
    "no_bulk_cook":  { "label": "No bulk cooking",    "overrides": { "week_defaults.lunch": "..." } },
    "freezer_stock": { "label": "Cook ahead, freeze", "overrides": { "...": "..." } },
    "simple_repeat": { "label": "Shakes and soups",   "overrides": { "meal_styles.breakfast": ["..."] } }
  }
}
```

**The field is `overrides`, not `keys`, and the rename is load-bearing.** A
map called `keys` whose members are dotted paths invites exactly the reading
that produced the bug in §3 — that each member is a config *key* and owns it.
`overrides` says what the map holds: statements of difference from the base
config, each addressing one leaf.

**`active` is this week's pick, not a permanent setting** (§1). It is written
whenever the pick changes and read at load; a block overrides it for its span
without altering it, so the pick you had before a block is the pick you return
to after it.

**Four of those are specified in the research, not invented.** `design-01` asks
for a shipped set and `docs/rapid-weightloss.md` supplies the definitions:

| Preset | From the research |
|---|---|
| `total_wellbeing` | CSIRO TWD: **30–35% protein, 40–45% low-GI carbohydrate, 20–25% fat, >35–40 g fibre.** `total_wellbeing_diet` is **already** in `diet_styles`, so this is a `meal_weights` + `net_carbs_g` + fibre-floor preset over an existing style |
| `fast_800` | 600–800 kcal; the `fast_800` style already declares the ceiling |
| `five_two` | **Five normal days and two low days at 800 kcal.** Confirmed 2026-09-01, and the 800 figure is the user's, not the research's 500–600 |
| `comfort` | see §7.3 — and the research's own device is better than relaxing NOVA wholesale |

### The naming that was bothering you, resolved

"Meal strategy" felt wrong, and 5:2 is why. Stated on 2026-09-01: *"5:2 is like
Fast 800 — a diet strategy that could be part of a preset."*

**That is exactly right, and the app already has the concept under another
name.** `diet_styles` is a catalog of twelve named eating patterns, each with
its `principles` text and — for `fast_800` — a `calorie_ceiling`. A "diet
strategy" *is* a diet style. So:

| The word you reached for | The app's name | Is |
|---|---|---|
| goal | a block's `body_goal` / `fitness_goal` | what you are aiming at |
| **diet strategy** | **`diet_styles`** | *how you eat* — Fast 800, CSIRO, Mediterranean, keto |
| "meal strategy" | **preset** | the bundle: one or more strategies **plus** cooking and effort |

"Meal strategy" was wrong in two directions at once — **too narrow**, because a
preset also decides how much cooking and how much effort a week can carry, and
**already taken**, because the food half is `diet_styles`.

**And 5:2 therefore needs no new machinery whatsoever.** It is the `fast_800`
style — which already declares its 800 kcal ceiling — scoped to two days,
which is precisely what `PROMPT-2` builds. A `five_two` preset's entire content
is a diet style and two weekday names:

```json
"five_two": {
  "label": "5:2",
  "overrides": { "dietary_rules.active_diet_styles": [
    { "style": "fast_800", "days": ["Monday", "Thursday"] } ] }
}
```

**One line, and it leaves `banned_ingredients` and `allowed_nova_groups`
exactly as the base config states them** — which is the whole point of §3's
leaf paths, and which the first draft's whole-key rule could not do.

Non-consecutive by convention rather than by rule — the research specifies
non-consecutive days, and since the days are named explicitly there is nothing
to enforce, only a sensible default to ship.

**This also settles what a preset is *for*.** A preset is not "a diet". It is a
diet strategy (or several, or none) **plus** everything about the week that
isn't food: what gets cooked, what gets batched, how long you're willing to
spend. That is why it needed a word of its own.

**16:8 / time-restricted eating was considered and dropped** (2026-09-01, not
important). It would have been the one item here needing a genuinely new
dimension rather than a preset key — `meal_types` are named, not timed, and
nothing in the app carries a clock — so dropping it removes the only preset
candidate that could not be expressed in the existing model.

**A shipped set matters as much as the mechanism.** The brief asks for *"a
number of profiles… defined (like it is in the wellbeing diet and others)"*,
and it is the right instinct: an empty catalog with a good editor still leaves
you authoring a mode on the Monday you least want to. The CSIRO programme
ships structured levels for exactly this reason, and its measured benefit —
24% greater loss at 12 weeks — came from members *using* prepared plans, not
from the tooling existing. Six or seven presets that cover how a week
realistically starts is the deliverable; the editor is what stops that set
being a ceiling.

### `config/blocks.json`

```json
{
  "blocks": [
    {
      "name": "fast-800-kickstart",
      "starts_on": "2026-09-07",
      "ends_on": "2026-09-10",
      "preset": "simple_repeat",
      "diet_styles": ["fast_800"],
      "protein_floor": { "multiplier": 2.0, "basis": "ffm" },
      "target_rate_kg_per_week": 1.2,
      "training_intent": "fat_loss",
      "notes": "Four days. Deliberately short."
    }
  ]
}
```

---

## 3. How a preset reaches the config, and the one rule that keeps it honest

**A preset merges into the same flat dict `AppConfig` already validates.**
Nothing downstream of the repository learns that presets exist; `planner`,
`week` and `ui_app` keep reading `config["weekly_schedule"]` unchanged. That is
the identical trick the config split already proved — splitting the *files*
without splitting the *object* touched zero call sites — and it is the reason
this arm is tractable at all.

The load order:

```
1. merge the five core files           → base dict  (CONFIG_FILES manifest check)
2. resolve the active preset           → preset layer   (whole week — §4.1a)
3. resolve the block covering each day → block layer    (day-scoped numbers only)
4. validate                            → AppConfig (extra="forbid")
```

**Step 2 is whole-week and step 3 is per-day, and that asymmetry is the
design** rather than an accident of ordering — see §4.1a. A preset replaces
leaves consumed once, before hydration; a block sets numbers hydration already
computes per day.

**Validation moves to after the layers, and that is a real change.** Today the
merge validates and nothing touches the dict afterwards. If a preset could
override a key *after* validation, a preset could introduce a state
`AppConfig` would have rejected. Validating last is the only ordering where
`extra="forbid"` still means anything.

### Three rules on the preset layer

> **Amended 2026-09-01 under review — the first draft said *whole-key
> replacement*, and §2's own shipped example broke it on its first line.**
> Recorded rather than quietly rewritten, per the same rule `design-02` §5
> applies to its own moved premise. The refutation is below the rules, because
> the failure mode is the argument.

- **A preset states typed leaf paths, not top-level keys.** An override is a
  dotted path into the merged dict — `"dietary_rules.allowed_nova_groups":
  [1, 2, 3, 4]` — whose value replaces **that leaf**, whole. A path absent
  means inherit. An empty list or object is an explicit value, not an absence:
  `"dietary_rules.banned_ingredients": []` genuinely bans nothing, and says so
  where an omitted path would have said nothing at all.
- **The first segment of every path must be a key `CONFIG_FILES` knows**,
  checked at load, failing with the preset name and the offending path.
  Without it a typo'd path silently does nothing — a preset that appears to be
  applied and is not is strictly worse than one that fails to load. Same loud
  policy `CONFIG_FILES` already applies to a key in the wrong file, and the
  same manifest answering it: only the *first* segment is a `CONFIG_FILES`
  question, because only the first segment is a question about file ownership.
- **Each leaf is replaced whole; there is no recursive merge anywhere.** The
  original objection to deep-merging stands untouched — a merge cannot express
  deletion, and "which days does this preset actually plan against" becomes
  unanswerable without replaying it in your head. A path bottoming out on
  `weekly_schedule.Thursday` replaces that day entire; a preset wanting a
  different seven-day calorie shape names `weekly_schedule` itself and restates
  all seven, which is still the readable answer for that case.
- **No preset chaining or inheritance.** One layer. A preset extending another
  makes the effective config a graph walk, and the whole value here is that
  you can read one object and know what the week will do.

#### Why whole-key replacement had to go, stated as the bug it produces

§2's `comfort` preset is four lines and reads as obviously correct:

```json
"comfort": { "label": "Bad week — take it easy",
             "keys": { "dietary_rules": { "allowed_nova_groups": [1,2,3,4] } } }
```

Under whole-key replacement that object **is** `dietary_rules` for the week.
`DietaryRules` has no required fields — all three carry a `default_factory` —
so it validates **cleanly** and silently discards the other two:
`banned_ingredients`, which is **17 entries** in the shipped `profile.json`
(`high fructose corn syrup`, `hydrogenated oil`, `seed oils`, and the
allergen-shaped `eggplant`, `banana`, `grapes`), and `active_diet_styles`.

A "bad week, take it easy" preset would unban every ingredient the user has
ever excluded, with no error, no warning, and nothing in §9.1's one-line pick
diff to show it — that diff is computed against a preset with empty keys, so
it would report "NOVA 4 allowed" and stop.

**The root cause is a granularity borrowed from a mechanism answering a
different question.** `CONFIG_FILES` is a manifest of *top-level* keys, and the
whole-key rule inherited that granularity for free, which is why it read as
consistent. But the manifest's granularity was chosen to answer *"which file
owns this key"* — a question about file placement. A preset asks *"what is
this preset's opinion"* — a question about semantics. `dietary_rules` bundles
three unrelated opinions that merely share a file, and reusing the manifest's
granularity across that gap is what produces the deletion.

**A second consequence, in the other direction: this makes the `planning_rules`
split optional rather than a prerequisite.** `OUTSTANDING.md` hardened its
recommendation to *splitting the key* precisely because whole-key replacement
trapped over half of it. Leaf paths reach `planning_rules.favorite_dinner_slots`
without touching `planning_rules.portion_trim_limits`, so the split is now a
thing worth doing on its own merits — it separates preference from engine
invariant, which is a real distinction — rather than something PROMPT-8 has to
land first.

---

### 3.4 Nothing about a preset may be hard-coded — and where that line actually falls

Stated as a requirement on 2026-09-01: *"I want to avoid hard-coding logic like
presets — they should be defined somewhere and be able to be edited via
interface."*

Agreed, and it is worth being exact about it, because "make everything data" has
a failure mode of its own: you end up inventing a programming language in JSON,
and then the *editor* has to be an IDE. The line that holds:

> **Data describes what you want. Code describes how it is achieved.**

| Data — a preset owns it, editable in the UI | Code — fixed, never a preset key |
|---|---|
| which processing levels are allowed, and how many convenience items | **how** a dish's processing level is measured (§7.5) |
| how many treats and how big (§7.4) | how a treat is costed against the day |
| cooking-time ceilings | how prep minutes are counted |
| which meals are cooked, left over or skipped | how a leftover links to its source |
| **what is batch-cooked, when, and who eats it** (`week_shape`) | how portions are derived from the slots claiming a cook |
| which diet styles are active, and on which days | how a calorie ceiling is applied |
| the week's carb shape and meal weights | the energy identity every budget is checked against |

**Today's batch behaviour is exactly the hard-coding being objected to**, and
naming it makes the requirement concrete. `apply_batch_selections` contains, in
Python: *bulk prep claims the lunches, long cook claims the dinners, both start
on day 1, both run forward as far as the fridge window allows.* None of that is
a fact about cooking — it is one shape, chosen once, compiled in. `design-02`
turns all four of those clauses into fields of a record you can edit.

Two consequences worth stating:

- **The shipped presets are data too, not built-ins.** `default` is a row in
  `presets.json` like any other, and it must be editable and deletable. A
  preset the UI treats as special is a hard-coded preset wearing a costume —
  and it is also how the byte-identical acceptance test in §10 stays honest:
  `default` reproduces today's behaviour *because of what it contains*, not
  because the code falls back to it.
- **The editor is not the only way in.** `presets.json` stays hand-editable,
  and a preset naming a key the editor does not expose must survive an edit
  untouched (§9.2). That is the same division `training_schedule` already has,
  and it is what stops the editor's field list becoming a ceiling on what a
  preset can say.

### 3.4a The audit — every planning constant, ruled

Run 2026-09-01 against `main` at `418c223`, per `design-00` §1's instruction
that the rule it states creates an audit nobody had done. Scope: every
module-level constant and every `planning_rules` key in `src/planner.py` and
`src/week.py` that participates in **what a week looks like**.

**Re-run the same day with the burden of proof reversed**, on the requirement
being restated more strongly:

> *"I want presets to be the predominant way to customise meal planning.
> Everything should be on the table for analysis for integration into
> presets."*

The first pass put the burden on `data` — a row had to earn its way out of
Python. **That is the wrong way round given the rule this program is held to**,
and it showed: rows were ruled `config, not preset` on the reasoning that no
*mood* varies them, when the actual question is whether a **week** might. A
week with guests, a long weekend, a training block and a comfort week are all
weeks, and four of the rows first ruled `config` are varied by one of them.

**The burden now sits on `code` and `config`.** A row is `data` unless it can
be shown not to be. The three verdicts, restated with what each now has to
prove:

| Verdict | Means | What it must prove |
|---|---|---|
| **data** | a preset key. Goes on §9.2's list with its absent-meaning | **nothing — this is the default** |
| **code** | stays compiled in | a preset setting it produces a week the app *rejects*, or the value is arithmetic/measurement rather than preference |
| **config, not preset** | reachable in `config/`, not a preset key | already reachable **and** varying it per week is meaningless or unsafe |

That reversal moved **eight rows into `data`** and cut `code` from eleven to
six. The six that survive each fail a specific test rather than being defended
by taste, which is the standard this section is now held to.

#### The schema is not the panel — and that is what makes "everything" affordable

The obvious objection to putting everything on the table is §3.4's own stated
failure mode: *you end up inventing a programming language in JSON, and then
the editor has to be an IDE.* The answer is that **"presets are the
predominant way to customise" is a claim about the schema, not about one
screen**, and this design already has the device that separates them —
`presets.json` is hand-editable and authoritative, and §9.2's editor is
deliberately bounded.

So the resolution is **tiered disclosure, not a shorter schema**:

- **Every row below ruled `data` is a preset key.** No exceptions, no
  built-ins, no "the code falls back to it".
- **§9.2's editor surfaces the groups a week actually varies**, with the
  remainder behind an *Advanced* disclosure on the same panel. `design-03` §1's
  rule holds either way — cost tracks widget *shapes*, and nearly every row
  added here is `ui.number` — but panel length is a real cost even when
  interaction cost is zero, and a fold is how it is paid.
- **A preset naming an Advanced key still survives an edit untouched**, which
  §9.2 already requires and which is what makes the fold safe.

The thing that must not happen is a key being unreachable from data. A key
being one fold down is not that.

#### The structural finding — **dissolved by §3's amendment, and kept because it caused it**

`planning_rules` is **one** `CONFIG_FILES` key, and §3's second rule *was*
whole-key replacement. On the first pass that trapped five `data` rows inside
an object a preset cannot partially name. **It then trapped nine**, including
`portion_trim_limits`, which CLAUDE.md explicitly says to leave alone and swap
models instead.

At five rows the editor could plausibly read-modify-write the whole object and
nobody would notice. At nine — over half the key — a preset file becomes
unreadable in exactly the way the rule existed to prevent: you cannot tell
which value was chosen and which was carried along.

That reasoning produced a recommendation to **split `planning_rules`** into a
preset-able group and an engine group. It is now superseded, and by the same
observation carried one step further:

> **This finding was evidence against the rule, not a cost of it.** "Over half
> a key is trapped inside an object a preset cannot partially name" is a
> statement that the *granularity* is wrong — and `dietary_rules` proved it
> the dangerous way a fortnight of `planning_rules` rows had not, by silently
> deleting a banned-ingredient list (§3). Typed leaf-path overrides reach
> `planning_rules.favorite_dinner_slots` without touching
> `planning_rules.portion_trim_limits`, so nothing is trapped and nothing has
> to be split.

**The split stays worth doing, and stops being a prerequisite.** Separating
preference from engine invariant is a real distinction and it is what this
audit was measuring; it belongs in CHANGE-QUEUE.md as ordinary work, not in
`PROMPT-8`'s critical path. The alternatives recorded on the first pass are
kept for the same reason: a per-key deep-merge exception (rejected — one
exception is how "which keys deep-merge" becomes a thing to memorise) and the
editor writing the whole object (workable at five rows, not at nine, and moot
now that it writes a path instead).

#### Ruled **data** — the default verdict

Absent-meaning is `design-03` §4.1 throughout: exactly today's behaviour. **⬆
marks a row moved here by the reversal.**

| Row | Where today | Which week varies it | Absent means |
|---|---|---|---|
| `cuisine_block_pattern` | `planning_rules` | comfort wants `[7]`, adventurous wants seven `1`s — the code comment names both | `[4, 3]` |
| `min_baseline_cuisine_share` | `planning_rules` | how much of the week is plain roast-and-veg | `0.5` |
| `favorite_breakfast_slots` | `planning_rules` | **the lazy/comfort dial §7.6 asks for** | `2` |
| `favorite_dinner_slots` | `planning_rules` | same axis; `0` is "invent everything" | `2` |
| `favorite_reuse_days` | `planning_rules` | a comfort week wants last week's dinners back, which the 21-day lunch window forbids | `{breakfast: 7, lunch: 21, dinner: 21}` |
| `meal_weights` | `profile.json` ✅ | where the day's energy sits. `DEFAULT_MEAL_WEIGHTS` is only the no-config fallback | `{0.30, 0.30, 0.30, 0.10}` |
| `min_meal_protein_g` | `planning_rules` | **data, but the *block* owns it** — `design-00` D3 and §6 below | `35.0` |
| `batch_target_servings` | `planning_rules` | superseded per batch by `design-02` `week_shape.batches[].serves`; the global becomes the fallback | `6` |
| `WEEKNIGHT_PREP_LIMIT_MINUTES` / `WEEKEND_PREP_LIMIT_MINUTES` | **constants** | the effort axis — §9.2's "prep ceilings", which had no key behind it | `30` / `180` |
| `WEEKNIGHT_ELAPSED_LIMIT_MINUTES` | **constant** | the *presence* axis §7.3 separates from effort | `90` |
| **the long-cook threshold** (60+ min) | **four copies, all prose** | what counts as "a long cook" at all — see below | `60` |
| the two numbers inside `DINNER_VARIETY_RULE` | **welded into a prompt string** | a comfort week eats chicken four times | cap `2`, consecutive `False` |
| `PORTION_DENSITY_GUARD`'s per-ingredient caps | **welded into a prompt string** | a bigger eater wants 3 slices of toast and 200 g of yoghurt | today's nine caps |
| ⬆ `WORKOUT_BREAKFAST_STYLE` | **constant** | see the shake pin below — the nutritional argument becomes the **default**, not a lock | `"custom_shake"` |
| ⬆ `WORKOUT_BREAKFAST_TYPES` | **constant** | whether an early ride morning gets the same treatment. A multi-select, `design-03` §3 XS | `("gym",)` |
| ⬆ `MORNING_TRAINING_CUTOFF` | **constant** | how early is "before breakfast can settle" | `"11:00"` |
| ⬆ `TRAINING_INTENSITY_SPLIT` | **constant** | a low-carb week wants a session's burn bought back as protein and fat | today's six pairs |
| ⬆ `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES` | **constant** | how long before a session you stop eating | `120` |
| ⬆ `MEAL_TIME_OF_DAY` | **constant** | a late-dinner week; it decides which meal is pre- vs post-workout | `07:00 / 12:30 / 15:30 / 19:00` |
| ⬆ `WEEKEND_DAYS` | **constant** | **a long weekend is a week-shaped fact** — and see the layering note below | `{Saturday, Sunday}` |
| ⬆ `serving_rules.servings_per_meal` | `week.json` ✅ | **cooking for guests is a week, not a life change** | `2` |
| ⬆ `protein_avoid_window`, `protein_lookback_entries` | `planning_rules` | how hard the week pushes protein variety; a comfort week wants a short window | `6`, `3` |
| ⬆ `rejection_decay_days`, `rejection_reason_window_days`, `REJECTION_REASON_SIGNAL_MIN` | `planning_rules` ✅ ✅, **constant** | how strictly past vetoes bind. All three are one policy and should move together | `21/60/90/180`, `180`, `3` |
| ⬆ `NUDGE_FOOD_SAMPLE_SIZE` | **constant** | how hard the week leans on the whole-foods corpus | `12` |
| ⬆ `SUNDAY_PREP_REHEAT_MINUTES` | **constant** | how long reheating takes in *your* kitchen; it is what the card tells you to plan your evening around | `10` |

Five of these need their reasoning spelled out.

**The long-cook threshold is one number in four places, and none is
reachable.** `build_batch_roast_rule`, `BATCH_ROAST_ANCHOR_RULE`,
`LONG_OVEN_COOK_RULE` and `Recipe.long_oven_cook`'s own field description each
say "60+ minutes" independently. That is the `sorted(categories)` shape
`DEPARTMENT_ORDER` closed — a decision made in seven places and agreed only by
accident — and it means a preset wanting a 45-minute bar would have to change
four strings, one of which is a Pydantic field description the model reads as
schema. **One key, four readers.** Found only because the reversal forced a
sweep of the prompt constants for embedded numbers.

**`DINNER_VARIETY_RULE` and `PORTION_DENSITY_GUARD` are data wearing prose.**
"No single main protein in more than two dinners", "never on two consecutive
nights", "max 2 slices of bread per serving", "max 45 g protein powder" are
tastes and appetites, unreachable because they are English inside a module
constant. **The extraction has an exact precedent**: `BATCH_ROAST_RULE` became
`build_batch_roast_rule(config, days)`, and `build_long_cook_day_rule` joined
it. So the shape is known and each is an **S** — a builder, a few keys, and
the prompt text says whatever the config says.

**`WEEKEND_DAYS` gets a layering rather than a flat key.** `day_allows_long_
cook` already moved the *presence* question off the calendar and onto
`location_rules.<location>.allows_long_cook`, with CLAUDE.md's argument that
*"the calendar is not where you are"*. That argument is about where the
**default** comes from, and it is right: derive the day names from
`base_schedule`, the same shape as `allows_prep_session` in `design-04` §7.
**A preset then overrides the derived answer**, which is how "treat Friday as
a weekend this week" gets said without editing your standing schedule. Derived
default, preset override — the same two-layer arrangement `design-04` §7 uses
for prep day.

**`favorite_reuse_days` carries the first real validation rule into the
editor.** Both windows must stay inside `history_max_entries` (28) or
`recipe_last_scheduled` cannot see far enough back, and a favourite that aged
off the window is indistinguishable from one never cooked — the failure that
forced `history_max_entries` from 21 to 28. A preset setting `lunch: 40`
silently stops binding. That is a §4.2 validate-before-save case with a
concrete rule, and it is the first one this program has that is not a type
check. **It is also the argument for keeping `history_max_entries` itself in
`config`**: a validator whose bound is preset-varied has two moving parts.

**`NUDGE_FOOD_SAMPLE_SIZE` is exposed with a warning rather than withheld.**
Its effect is not monotonic in the preference it looks like — more entries
dilutes the nudge rather than strengthening it, since `build_slot_brief` names
the whole sample in every slot's brief. The first pass ruled it `code` on that
basis, which was paternalism dressed as rigour: the honest move is to ship the
number and put the non-monotonicity in the editor's help text, the way
`portion_trim_limits` carries its warning in a comment today.

#### Ruled **config, not preset** — four rows, and all four are already reachable

**None of these fails `design-00` §1's test.** Every one is in `engine.json` or
`week.json` today, so none is "compiled in", and the rule this program is held
to is satisfied by all four as they stand. What they are not is week-varying.

| Row | Where | Why not a preset key |
|---|---|---|
| `portion_trim_limits`, `portion_trim_deadband`, `max_meal_share_multiple` | `planning_rules` ✅ | these govern **how much bad model output is accepted**, not what the week is. CLAUDE.md: *"swap models rather than widening `portion_trim_limits`"* — widening lets through portions absurd enough to be unusable, which is not a customisation anyone wants per week |
| `history_max_entries` | `planning_rules` ✅ | a **storage** bound (entries kept on disk), and the bound `favorite_reuse_days`' validator checks against. Varying it per week makes the file oscillate and gives that validator a moving target |
| `inventory_rules.fridge_safe_days` | `week.json` ✅ | **food safety, and it is being replaced rather than withheld** — `design-05` splits it into a config reference table plus a measured per-dish `storage_class`. Settled 2026-09-01; see below |
| `regional`, `week_start_day`, `meal_types` | ✅ | already excluded by §9.2's own reasoning — facts about where and when you live |

**`fridge_safe_days` is settled, and the answer is that it stops being a
customisation point at all.** Decided 2026-09-01: *"it needs to be a per-dish
measurement, not part of preset."*

That is `design-05`'s design exactly, and it is the right answer for a reason
stronger than "a preset is a mood and food poisoning is not". **One global
number is wrong in both directions at once** — a beef stew keeps 4 days and
the app says 3, a rice tray bake keeps 2 and the app says 3. Making it
preset-able would have let a preset pick a *different* wrong global, which is
not an improvement on the wrong global it already has. A tightening-only
`min()` — the shape floated on the first re-run — would have been worse still:
it fixes the stew case never and the rice case only by accident.

So the key splits, and neither half is a preset key:

| Half | Where | What it is |
|---|---|---|
| `inventory_rules.storage_windows` | `week.json` | a **reference table** — 96 h default, 48 h for rice or pasta, the freezer months per class. Food-safety figures, not preferences |
| `Recipe.storage_class` | on the recipe | a **measurement**, reported by the model exactly as `long_oven_cook` and `bulk_prep_friendly` are |

**`storage_class` passes `design-00` F5's test, which is why it belongs on the
recipe rather than in a preset**: *store a measurement iff it is
scale-invariant; never store a verdict.* A rice tray bake is `rice_or_pasta`
whether it is scaled to two servings or six — the class does not move when the
portion does. The **verdict** it feeds ("can this batch reach Thursday") stays
derived, in the five consumers `design-05` §5 lists.

This is the one row in the audit where "everything should be on the table" was
answered by taking the thing off the table entirely, and it is worth keeping
straight what that does and does not concede: the rule this program is held to
is that no *logic* is compiled in, and `design-05` satisfies it twice over —
the windows are config and the class is data on the record. What it declines
is a preset's authority over a food-safety figure, which was never
customisation in the sense §1 means.

#### Ruled **code** — six rows, each failing a stated test

| Row | Fails which test |
|---|---|
| `MEAL_TYPE_PRIORITY` | **a preset setting it produces a week the app rejects.** Dinner precedes lunch so the one cross-type leftover `week.leftover_meal_type_error` permits always has its source generated. Reordering it fails `validate_week` |
| `MACRO_KEYS` | **arithmetic, not preference** — the energy identity `calories ≈ 4p + 4c + 9f`. Already in §3.4's right-hand column verbatim |
| `PREP_DAY_INDEX` | **arithmetic** — "the day before day 0". `design-04` §7 makes prep-day *placement* movable by deriving it; that changes where the walk lands, not what this means |
| `SEAFOOD_TERMS` | **measurement, not preference** — *how* a dish is classified, §3.4's right column. The **cap** is already `sourcing.max_seafood_meals_per_week` and is preset-able |
| `UNDER_TARGET_NOTE_THRESHOLD` | **customises nothing about the plan** — it decides when a warning is emitted about a week already generated |
| prompt-rule **wording** — `SHAKE_ROTATION_RULE`, `PANTRY_CONSOLIDATION_RULE`, `FIBER_TARGET_RULE`, `ELAPSED_TIME_RULE`, `LOCATION_RESTRICTION_PHRASES`, the `WEEK_*`/`DAY_*` rules, both anchor directives | **how a want is achieved, not the want.** The *numbers inside* three of them are ruled `data` above — that is the line, and the sweep that found them is recorded there |

**The shake pin, re-ruled.** The first pass made `WORKOUT_BREAKFAST_STYLE`
`code` on a nutritional argument — a shake is the only breakfast in
`meal_styles` drinkable ten minutes before a session. **That argument is sound
and it is an argument for a default, not for a lock.** A preset that names a
different style is a user who has a different answer for their own kitchen,
and this program's whole premise is that they get to say so. It is now `data`
with `custom_shake` as its absent-meaning, and the nutritional reasoning
belongs in the editor's help text where it can actually be read.

What does **not** become a key is the third layer, and that is unchanged:

| Question | Verdict |
|---|---|
| Which style does a lifted morning get? | **data** — default `custom_shake` |
| Which sessions, and how early? | **data** — a multi-select and a number |
| Does it fire at all this week? | **already answered, by facts.** `morning_training_days` reads `training_schedule`; a deload week declares no morning gym session and the pin does not fire. A preset flag would give two objects an opinion on one question |

`resolve_auto_choices` already checks the style exists in `meal_styles.
breakfast` before pinning and keeps rotating normally when it does not, so a
preset that replaces `meal_styles` and omits its own pinned style degrades
safely rather than briefing the model on a style it was never given. That
graceful degradation is now a **backstop** rather than the escape hatch it was
asked to be on the first pass.

#### Excluded from scope, recorded so the exclusion is not silent

`OPENROUTER_BASE_URL`, `FREE_MODEL_MAX_TOKENS`, `PAID_MODEL_MAX_TOKENS`,
`LOG_FILE`, `DEFAULT_STORAGE_PATHS` (protocol and transport);
`STORAGE_NOTE_PREFIX`, `TRAINING_NOTE_PREFIXES`, `REJECTION_REASON_LABELS`,
`ADHERENCE_*`, `MODES`, `LINK_ORIGIN_*`, `AUTO`, `TARGET_MODE_*`,
`PERISHABLE_DEPARTMENTS` (vocabulary the UI or storage matches on — changing
one breaks a lookup rather than changing a week). `DEFAULT_MEAL_TYPES`,
`DEFAULT_MEAL_WEIGHTS`, `DEFAULT_SERVINGS_PER_MEAL`,
`DEFAULT_ALLOWED_NOVA_GROUPS`, `DEFAULT_CUISINE_BLOCK_PATTERN`,
`DEFAULT_INVENTORY_RULES`, `DEFAULT_PLANNING_RULES` are **fallbacks for config
keys that already exist**, so the verdict belongs to the key and not to the
constant — but each is named above under its key rather than left here, since
"ruled by implication" is how a row ends up with no verdict at all.

#### One defect, filed rather than fixed

`inventory_rules.perishable_day_gap` is declared on `InventoryRules`,
validated by `AppConfig`, and **read by nothing**. `shopping.ShoppingItem.
buy_late` imports `week.PERISHABLE_DAY_GAP` — the module constant — because it
is a computed property with no config in scope, and `week.py` carries a comment
saying to keep the two in sync by hand. They agree today at 3, so nothing is
visibly wrong; editing the config key does nothing at all.

That is the declared-but-unread shape this codebase has closed three times
(`readiness_log`, `activity_log`, `smooth_series`), reached from the config
side. Filed in CHANGE-QUEUE.md. **Not fixed here — this audit changed zero
lines of `src/`.**

---

## 4. The block, which is the genuinely new mechanism

Everything else in `config/` is undated. A block is the first object in this
app that **expires without anybody clearing it**.

### 4.1 It is typed, not a second preset

A block does **not** set arbitrary config keys. It carries a small, fixed set
of *intents*, each of which feeds something that already exists.

**It carries two goals, not one — settled and confirmed 2026-09-01.** The question was put
as *"I wonder if I need fitness goals as well as health/weight goals"*, and the
answer is yes, for a reason that is the whole point of separating them:

| | Example | Feeds |
|---|---|---|
| **body goal** | lose 8 kg · hold · recomposition | the deficit slide, the protein floor |
| **fitness goal** | raise VO2 max · build strength · maintain | session order, separation, modality, frequency (§4.1's table) |

**They are pursued at the same time and they interfere**, which is exactly why
one field cannot carry both. `docs/periodization-engine.md` quantifies the
interference — a fat-loss phase compromises recovery and glycogen resynthesis,
so it **lowers every training-fatigue threshold by about 20%** — and
`docs/fitness-model.md` adds that a deficit blunts the adaptations a VO2 max
block is trying to produce.

With one goal field you would have to choose, and the app would never know it
was a trade. **With two, the app can see the conflict and say so** — which is
this codebase's standing answer whenever numbers do not reconcile: report the
gap, do not silently pick a winner.

That is not hypothetical here. The stated fitness goal is **VO2 max**; the body
state is **99.8 kg against a target of 80**. Those pull in opposite directions,
and a system with one goal slot would quietly serve one of them.

The full field list:

| Field | Feeds | Notes |
|---|---|---|
| `body_goal` | the deficit slide, the protein floor | Required. The table above |
| `fitness_goal` | session order, separation, modality, frequency | Required. Arm E reads it; stored now |
| `diet_styles` | `dietary_rules.active_diet_styles` | Day-scoped — see §5 |
| `protein_floor` | the locked protein figure | See §6. This is D3's answer |
| `target_rate_kg_per_week` | the deficit slide in `nutrition_engine` | Never `weekly_schedule` calories directly |
| `training_intent` | Arm E, later | Stored now, read later — but see the warning below |
| `peak_day` | "energy for Sunday's ride" | Arm E |
| `notes` | the UI | |

**If a block could set any key it would be a dated preset, and there would be
no reason to have both.** The typed field list is the whole distinction.

> **Two corrections, both made 2026-09-01 under review.**
>
> **`body_goal` and `fitness_goal` were missing from the table** while the six
> paragraphs immediately above it argue at length that a block carries two
> goals and that collapsing them is the error. The prose was the decision and
> the table was stale; an implementation session reading the "full field list"
> would have built the single-goal block this section exists to refuse.
>
> **`preset` has been removed from the table**, and §5a is the argument.

### 4.1a Why a block cannot pin a preset

`preset` was the first row of this table, and it was the one field whose
granularity disagreed with every other. The rest are per-day *numbers* —
`diet_styles` is explicitly day-scoped (§5), `protein_floor` resolves to grams
looked up per day, `target_rate_kg_per_week` feeds a deficit slide already
computed per day inside `hydrate_dynamic_targets`. A preset is not a number:
it replaces leaves under `week_defaults`, `meal_styles`, `weekly_schedule` and
`planning_rules`, and those are consumed **once, before hydration**, by
`default_week_spec`, `resolve_auto_choices` and `pick_cuisine_blocks`.

So a Monday–Thursday block pinning a preset is asking one flat `AppConfig` to
be two different objects during a single `default_week_spec` call. It cannot,
and the honest options are all expensive: an `effective_config(day)` threaded
through every consumer, or generation split at block boundaries — which breaks
cuisine runs, the week-wide variety rules, batch shapes and the shopping walk,
every one of which is week-scoped by design.

**None of that has to be paid, because the brief's own example never needed
it.** "Fast 800 for four days" is a diet-style activation, and `diet_styles`
is day-scoped. §10's acceptance criterion already knew this and says so in its
own verb — *"a block spanning Mon–Thu of a Mon-start week **caps** four days"*.
A cap is the calorie ceiling. Not one acceptance test in this design exercised
a mid-week preset change.

A block therefore sets dated numbers; **the preset stays the weekly pick**
(§9.1), whole-week by construction. That is this section's own rule applied to
its own first row: a block that could pin a preset *is* a dated preset, and
there would be no reason to have both.

**`training_intent` is not a placeholder — its values are already specified.**
The first draft stored it as a string with "Arm E, later" beside it.
`docs/periodization-engine.md` defines exactly what each value means, in a
table this design should have been reading rather than deferring:

| | VO2 max | Hypertrophy | Fat loss / recomp |
|---|---|---|---|
| intra-day order | HIIT → resistance | resistance → low-intensity cardio | resistance → Zone 2 |
| min separation | **6 h** (resistance secondary) | 6 h | 3 h |
| endurance modality | run / bike / row | low-impact bike, incline walk, row | ergometer, row, incline walk |
| frequency & zone | 3–5/wk, 80% Z2 / 20% HIIT | 2–3/wk, 90% Z2 | 3–4/wk, Z2 emphasis |
| resistance config | 2–3 d/wk, maintenance | 4–5 d/wk, 10–20 sets/muscle/wk | 3–4 d/wk, load preserved |

**And part of it reaches the *meal* planner, not only Arm E** — which is why
deferring the whole field was wrong. Session order and the separation buffer
shape `training_schedule`, and `training_schedule` is already read by
`apply_training_adjustments` (the day's calorie uplift), by
`morning_training_days` (which pins a breakfast shake), and by
`training_pin_budget` (the post-workout meal). A block declaring `vo2max`
therefore has consequences for Monday's breakfast today, with no periodization
engine in sight.

`peak_day` remains genuinely ahead of its reader.

**Storing a field ahead of its reader is something this project has a rule
about.** CLAUDE.md's standing rule — *"an entry in
`CRONOMETER_MACRO_COLUMNS` has to assert that something reads it"* — was
learned from three signals fetched on every sync and read by nothing. These
two are the same shape. They are included anyway because a block with no
training intent cannot express half of what the brief asks for, and because
unlike a sync column they cost no fetch. **But they must be surfaced in the
Settings read view from day one**, so a human is their reader even before the
engine is. A field nothing displays and nothing consumes should not ship.

### 4.2 A block sets targets; it never sets a number the engine owns

`target_rate_kg_per_week` feeds the deficit; it does **not** write
`weekly_schedule` calories. Otherwise the block becomes the second source of
truth `target_modes` exists to prevent, and the telemetry header would preview
a figure the run does not use — the exact failure CLAUDE.md records as the
shipped config's 1000 kcal Thursday against a computed 1722.

For the same reason a block **never touches `target_modes`**. That setting
answers *who owns* a number; a block supplies *what the owner aims at*. A
block that flipped a macro to manual would silently change who decides, which
is precisely the thing a toggle exists to make explicit.

### 4.3 Overlap is refused, gaps are normal

- **Two blocks covering one date fail at load**, naming both blocks and the
  overlapping range. Picking a winner would be a rule nobody chose, the same
  answer `reconcile_adaptive_tdee` gives when two TDEE figures disagree: pick
  one deliberately or refuse, never average.
- **A gap is the normal state.** No block covering a day means preset + base,
  which is today's behaviour exactly.
- **An expired block is kept and inert.** Never auto-deleted — it is the
  record of what you actually did, and it is what a later "did the block
  work?" readout pairs against. The same treatment `-deprecated.md` gets:
  superseded, not discarded.

### 4.4 The clock is a seam, not a call

`active_block(blocks, on_date)` takes the date. The convenience wrapper
defaults to the clock — the same seam `build_rejection_rule(today=...)` and
`select_favorite_assignments` already use, and for the reason the Tests
section of CLAUDE.md paid for once: *a fixture may read the clock; an
assertion may not depend on what it said.* Two tests were quietly
weekday-dependent and only surfaced when the date rolled over mid-session.

---

### 4.5 A block is a pre-commitment device — that is *why* it exists

Once presets are a weekly pick, the block's purpose sharpens. It is not merely
"a preset with dates on it". **A block is what suspends the weekly choice.**

"Fast 800 for a 2-week block to start a weight loss process" is precisely
that: you decide once, on a good day, and the fortnight's Mondays do not get
to re-litigate it. That is a **pre-commitment strategy**, and
`docs/rapid-weightloss.md` names it directly in the choice-architecture table
as the structural intervention against hedonic food cues — alongside default
healthy ordering and removing visible cues.

So the two objects are two different behavioural instruments, and the research
backs each:

| | Instrument | What it is for |
|---|---|---|
| **weekly preset pick** | **flexible restraint** | Graduated boundaries; a bad week gets comfort food *inside* the system rather than as a breach of it. The research ties this to better long-term maintenance and far fewer binge episodes |
| **block** | **pre-commitment** | Removes the decision for a fixed span, on purpose, while motivation is high |

**This is also the answer to the "rigid restraint" risk the research warns
about.** A system with only blocks would be rigid — an all-or-nothing rule set
is the profile correlated with regain and with the "what-the-hell effect"
after a minor breach. A system with only weekly picks would have no
pre-commitment at all, and the pick would be made under exactly the decision
fatigue it exists to relieve. **You need both, and they must not be the same
object.**

Two consequences for the design:

- **A block pins the preset; it does not replace the picker.** During a block
  the Settings picker shows the pinned preset and says which block pinned it.
  Not disabled — an explicit "end this block early" is the escape hatch, and a
  pre-commitment with no stated exit is a trap rather than a device.
- **A week outside a block always has a pick to make**, and the default is
  last week's. Presenting an empty choice every Monday would reintroduce the
  decision the whole arm exists to remove.

### 4.6 The weekly pick has to be recorded, or the feedback loop cannot attribute anything

If the preset changes weekly, then **"did the block work?" is unanswerable
without knowing which preset each week ran under.** Nothing records it today
because nothing varied.

So the active preset's name goes onto `WeekPlan` and into the
`meal_history.json` entry, alongside the targets the week was generated
against. It is one string, and without it the entire measurement half of the
program (Arm D) can compare weeks but never explain them.

Worth stating plainly because it is the same trap this project has now paid
for three times from the other direction: sleep, activity and `net_calories`
were each *stored and never read*. This is the mirror — a reader (the feedback
loop) with nothing written for it. The check is the same one CLAUDE.md applies
to a sync column: **before adding a field, ask what reads it; before building
a reader, ask what writes it.**


### 4.7 A block needs a successor — the exit is the risky part

**Added 2026-09-01 after a research audit found this missing.** The block model
above had `ends_on` and then nothing, and `docs/rapid-weightloss.md` devotes an
entire section to the fact that **the end of a restriction block is the highest-
risk moment in the whole protocol.**

The mechanism it names is specific. After weight loss, fat mass and fat-free
mass are both depleted, and they recover on **separate feedback signals** — the
*adipostat* (leptin, sensing lipid depletion) and the *proteinstat* (lean-tissue
signalling). Adipose refills faster: by the time fat mass is back to 100% of
baseline, lean mass is typically only **70–80%** restored. Because lean mass is
still short, the proteinstat keeps driving hyperphagia and suppressed
thermogenesis — and the surplus goes into adipose. That is **post-dieting fat
overshooting**, and across repeated cycles it pushes adiposity *above* the
pre-diet baseline.

So a block that simply ends and hands you back to `default` is not neutral. It
is the shape most likely to end above where it started.

**Settled 2026-09-01: the successor is required, and skipping it takes an
explicit override.** Stated as *"if I do 2–4 weeks of 800 calories, I should
follow best practice to reverse diet back to normal levels — would need
explicit override to not follow this process."*

That is stronger than this section first proposed (require it below a calorie
threshold, suggest it otherwise), and it is the better reading. The research
does not describe the transition as advisable; it describes skipping it as the
mechanism by which repeated diets *increase* long-term adiposity. A default
that has to be actively turned off is the honest encoding of that, and it is
the same shape `reasoning_extra_body` already uses — off by default, on only
where a specific entry says so.

**The override must be recorded, not merely permitted.** A block carrying
`skip_transition: true` is a decision someone made; a block that simply has no
successor is an oversight. They must not look the same on disk, or the app
cannot tell "I know what I'm doing" from "nobody noticed".

**Two mechanisms:**

- **A restriction block declares its successor** — required, per above.
- **A `transition` block type**, which is a fully specified algorithm rather
  than something to invent. From the same document: **+100–250 kcal every 1–2
  weeks**, **protein held constant as the anchor** (added calories come from
  low-GI carbohydrate and essential fats, which is what restores hepatic
  deiodinase activity and circulating T3), and **hold the ramp for 7–14 days if
  the 7-day average weight rises past a threshold**.

  Note the ramp is a *rate*, not a target, which makes it the one block whose
  numbers move over its own span. Everything in §4.2 still holds: it feeds the
  deficit slide, it never writes `weekly_schedule` calories.

**And it composes with the protein floor (§6) rather than fighting it.** The
floor is resolved once and frozen per block, so a transition block resolves its
own — which is exactly right here, since the research's whole point is that
protein is the constant while energy ramps.

**One coupling worth recording**, from `docs/periodization-engine.md`: a
caloric deficit compromises recovery and glycogen resynthesis, so that document
**lowers its deload trigger thresholds by ~20% during a fat-loss phase**. That
is a direct block → training-engine coupling. It is Arm E's to implement, but
it is `training_intent`'s to *carry*, which is the next section's point.

### 4.8 The chain *is* the longer planning horizon — and the earlier instinct was right

Raised alongside §4.7: *"this is why I was thinking we need a longer planning
horizon."*

**That instinct was correct, and `design-00` F1 answered a different question
than the one being asked.** F1 argued that none of four stated needs buys a
longer *grid*, and that still holds — a 14-day grid collides on every slot id
and buys nothing here either. But "longer horizon" was never really about the
grid. It is about the **plan above the week**, and until §4.7 there was none:
blocks existed one at a time, with nothing expressing that one follows another.

`docs/rapid-weightloss.md` describes a **20-week sequence**, not a block:
weeks 1–12 restriction, 13–14 / 15–16 / 17–18 stepped reintroduction, 19+
maintenance. That is the horizon, and it is a sequence of ordinary weeks —
each still generated seven days at a time.

**So the app plans short and commits long**, and those are different things:

| | Span | Object |
|---|---|---|
| what is *generated* | 7 days | a week |
| what is *committed to* | 2–20 weeks | a **chain of blocks** |

**Recommendation: a chain, rendered as a timeline — not a new container
object.** Each block names what follows it, and the UI draws the run. A
container holding an ordered list would let you edit the arc as one thing,
which is genuinely nicer, and it is more machinery for a sequence the research
describes as strictly linear. The chain gives the *view* — which is what was
actually asked for — at the cost of one field.

Two things fall out, and both are the point rather than side effects:

- **A total span becomes visible before you start.** "Two weeks at 800" is a
  very different commitment from "two weeks at 800 followed by six weeks of
  stepped return", and only the second is what the research actually
  prescribes. Showing the whole arc at the moment of committing is the
  pre-commitment argument of §4.5 applied to the *sequence* rather than to one
  block.
- **A chain that ends in maintenance has an end; one that does not, does not.**
  The final block having no successor is meaningful — it means "this is where
  normal life resumes" — which is exactly why §4.7's override has to be a
  recorded flag rather than an absence.

## 5. The wrinkle that decides the implementation: a block boundary can fall mid-week

"Fast 800 for **four days**" is the brief's own example, and it means a block
does not align to the planning week. Monday–Thursday in-block, Friday–Sunday
out, in one generated week.

So block resolution is **not** "which block is active today". It is **which
block covers each day of the week being planned**, and that needs the
weekday-name → calendar-date mapping. `WeekPlan` carries `week_start_date` and
`week.week_date_range` already derives the span from `generated_at`, so the
mapping exists; it just has to be threaded to the layer.

**What a mid-week boundary may vary is exactly the typed field list, and no
more** (§4.1a). Every field on it is a per-day number that `hydrate_dynamic_
targets` is already the right place to look up. The preset is not, and the
first draft of §4.1 let a block pin one — which would have required the whole
merged config to differ between Thursday and Friday inside a single
`default_week_spec` call. That row is gone; this section is the reason.

**This makes PROMPT-2 (day-scoped diet styles) a dependency of this arm, not a
sibling of it.** PROMPT-2 builds exactly the per-day scoping a block needs, on
the one field where the machinery is already half-present — `diet_style_
calorie_ceiling` is applied per day inside `hydrate_dynamic_targets` today,
and `_sourcing_day_split` already solves "this call spans days where the rule
binds and days where it does not" for specialty grocers. Ship PROMPT-2 first
and this arm inherits the substrate. Build them together and the scoping gets
invented twice.

**A day with no block is not a special case.** It resolves to preset + base,
which is the same code path a config with no `blocks.json` at all takes.

---

## 6. The protein floor as a block property — D3's answer

Today: `target_weight_kg` (80) × `protein_multiplier` (1.8) = **144 g**,
locked, and the lock is deliberate. CLAUDE.md: *"Tying it to current weight
would shrink the floor exactly as the diet began to threaten the lean mass it
exists to protect."* That reasoning is correct and survives this change intact.

At 99.8 kg and 33.4% body fat, FFM ≈ 66 kg, and `docs/rapid-weightloss.md`
puts an aggressive deficit at 1.6–2.4 g/kg total mass (160–239 g) or 2.3–3.1
g/kg FFM (153–206 g). 144 g is below the floor of both.

### The block declares a basis; the figure is resolved once and frozen

```json
"protein_floor": { "multiplier": 2.0, "basis": "ffm" }
```

Bases: `target_weight` (today's behaviour), `ffm`, `current_weight`,
or a bare `grams` for a figure decided by hand.

**Resolution happens once, when the block starts, and the resolved grams are
written onto the block.** This is the load-bearing decision and it is what
makes an FFM basis safe:

- FFM comes from the scale's BIA body-fat reading, which the research puts at
  **4–8% MAPE** and which CLAUDE.md already stores-but-ignores on exactly that
  advice. A floor recomputed nightly from BIA would move the day's protein
  target on instrument noise.
- Freezing it preserves the existing invariant — *within a block the floor
  never slides* — while letting the number be derived from real body
  composition instead of a target weight 20 kg away.
- It is the same move `PlannerState.set_target_mode` already makes: switching
  a macro to manual **seeds from what the engine currently computes** rather
  than exposing a stale file figure, because handing back a stale number would
  look like the toggle had re-planned the week.

The multiplier and basis are kept beside the resolved grams as provenance, the
way `basis["tdee_source"]` records which TDEE won. "165 g" alone is a number
nobody can audit; "165 g = 2.0 × 82.5 kg FFM, resolved 2026-09-07" is.

### The consequence that has to be designed for, not discovered

`apply_protein_floor` is **already tight**. CLAUDE.md is explicit: 144 g
against a 35 g `min_meal_protein_g` floor over four meals leaves 4 g of slack
across the whole day, which is why `week_defaults.snack` had to become `skip`
and why the two `gym_hypertrophy` mornings needed 550 kcal / 60 g pins.

Raise the floor to ~165 g over three meals and the arithmetic gets *easier*
(55 g/meal, well clear of 35 g). Raise it over four and it does not. So a
block declaring a higher floor and a preset restoring the snack slot is a
combination that can be unsatisfiable.

**It must be reported, not corrected.** That is this codebase's standing
answer everywhere the numbers fail to reconcile — an overspent `meal_overrides`
floors the rest at 0 and warns, `cap_to_weighted_share` drops its surplus,
`apply_protein_floor` does nothing and logs, an unaffordable diet-style
ceiling emits a note naming the days. A block whose floor its own preset
cannot carry should say so **at load**, naming both, rather than silently
producing meals that miss it. That check is cheap and it is the difference
between a bad combination you can see and one you discover six weeks later in
a body-composition chart.

---

## 7. Processing level, and the "lazy week" axis

Asked for directly: *"NOVA compliance based on level… given group 4 is so
broad, should we also investigate SIGA/UNC systems for classifying group 4 to
allow for lazy weeks where I just need to eat something easy."*

Three separate questions hide in that, and they have three different answers.

### 7.1 A preset setting `allowed_nova_groups` — works under §3 as amended, with two holes

`dietary_rules.allowed_nova_groups` is a leaf under a core config key, so
under §3's leaf-path overrides a preset sets it with no new mechanism at all.
A `comfort` preset naming `[1, 2, 3, 4]` and a `strict` one naming `[1, 2]`
are one line each.

> **This section previously read "already works", and under the *first* draft
> of §3 it did not.** Whole-key replacement made the same four-line preset
> silently delete `banned_ingredients` — the refutation is in §3, and it was
> found here first: this was the section confident enough to call the
> mechanism free, which is what made the cost of getting it wrong visible.

`Ingredient.enforce_allowed_nova_group` reads **live config** through
`info.context`, so relaxing the rule genuinely relaxes generation. That half
is free.

**The hole is that the catalog outlives the preset that admitted a recipe.**
The validator has a documented fallback: a caller with no context — *"a bare
`Recipe.model_validate` of a saved favorite, for instance"* — skips the check
entirely, so a saved recipe stays loadable rather than blowing up. That is
correct behaviour and must not change.

But it means a NOVA-4 dish imported during a comfort week **sits in
`recipes_master.json` for ever**, loads without complaint during a strict
week, and is a perfectly ordinary candidate for
`select_favorite_assignments` — which would pin it into the very week whose
whole point was excluding it. Nothing today would raise a word.

**So catalog *selection* has to filter on the active preset's
`allowed_nova_groups`, not lean on the import-time gate.** The import gate
answers "may this be created", against the preset live at import; selection
answers "may this be served this week", against the preset live now. They are
different questions and the app currently only asks the first. This is the
single most important consequence of making NOVA presettable, and it is
invisible until the first strict week after a comfort week.

**The second hole is that `select_favorite_assignments` is about to stop being
the only claimant.** `PROMPT-3` adds a *user* recipe pin reading the same
catalog, and a pin chosen by hand walks straight past a filter written only
into the automatic selector — so the check this section exists to add would be
added and then bypassed, by a feature landing in the same arm. The filter
therefore has to be **one shared eligibility function** both claimants call,
not a guard inside `select_favorite_assignments`. `PROMPT-3` carries the
matching instruction.

It is worth being exact about what that function does and does not decide. A
user pin is a **veto and it outranks preference** — §8's "still yours" line —
so it overrides style rotation, cuisine blocks and the LRU reuse window
without argument. It does **not** override the two hard rules: a recipe
carrying a `banned_ingredients` match or a disallowed NOVA group is refused,
with the reason named, because those are the constraints `Ingredient`'s
validators enforce at generation and a pin is the one path that reaches a slot
without passing through them.

### 7.2 SIGA and UNC — investigated, and neither is applicable

Both are real and both do what was hoped in principle.

**SIGA** (French; Davidou et al., evaluated across French supermarkets) keeps
NOVA's four holistic groups and adds reductionist subgroups: `A0` unprocessed,
`A1` minimally processed, `A2` culinary ingredients, `B1`/`B2` processed split
by whether salt/sugar/fat levels are balanced or high, and a `C` band that
subdivides ultra-processed by **the number of markers of ultra-processing
(MUPs)** and the level of at-risk additives — `C1` being a UPF carrying more
than one marker.

**UNC** (Poti and colleagues) uses **seven** processing categories, and its top
band is named *"highly processed stand-alone"* rather than "ultra-processed".
It scored the **highest inter-rater reliability of the major systems**
(ρ = 0.97) and agrees with NOVA about **80%** of the time.

**Neither can be computed from what this app stores, and the reason is
structural rather than a gap to fill.** An `Ingredient` carries `name`,
`quantity_g`, `nova_group` and five nutrient figures. SIGA's discriminator is
**additive-level detail** — emulsifiers, colourings, flavourings, modified
starches — and its B1/B2 split additionally needs **sodium**, which
`CRONOMETER_MACRO_COLUMNS` deliberately excludes on the standing rule that a
captured column has to assert a reader.

There is a deeper mismatch. **SIGA classifies supermarket products; this app
plans cooked dishes.** A jar of korma sauce is one *product* with a
declarable additive list, and in this app it is one ingredient line reading
"korma simmer sauce" with no additives enumerated anywhere. Applying SIGA
would require storing the label of every packaged component — a data
acquisition problem an order of magnitude larger than the metadata backfill
in `design-00`'s F4, and one no import path currently touches.

**Verdict: do not adopt either taxonomy.** Recorded here so the question is
not re-opened without new information, and so the *reason* is on file: it is
missing input data, not a judgement that the systems are unsound.

### 7.3 The real axis is effort, and the app already has it

Worth separating the want from the mechanism reached for. "A lazy week where I
just need to eat something easy" is a statement about **effort**, and
processing level is a proxy for it — a good proxy, but not the thing itself. A
40-minute NOVA-1 dinner from scratch is *not* an easy week; a NOVA-3 tin of
lentils in a 10-minute bowl is.

And this app already models effort directly, in four places: `prep_time_
minutes`, `total_time_minutes`, `location_rules.<location>.max_prep_minutes`,
and `prep_limit_for`'s weeknight ceiling.

**So a `lazy` preset is three existing dials, not a new taxonomy:**

| Dial | Existing key | A lazy week |
|---|---|---|
| what may be in it | `allowed_nova_groups` | admit `4` |
| **how much** of it | *new* — a count cap | at most N NOVA-4 ingredients per meal |
| how long it takes | prep ceilings | relaxed down, not up |

**The count cap is the only new thing, and it is a shape this codebase already
has twice**: `sourcing.max_seafood_meals_per_week` and
`planning_rules.min_baseline_cuisine_share` are both "a bounded share of the
week" rather than a taxonomy, and both are enforced by counting what a stage
actually returned rather than by classifying anything. A cap gives the graded
answer that motivated the SIGA question — *some* convenience, not a binary
gate — for a fraction of the cost, and with no data the app does not hold.

### 7.4 Indulgence units — the research's own device, and a better `comfort`

Found in the same audit. `docs/rapid-weightloss.md` describes how the CSIRO
framework handles exactly the "bad week" case, and it is **not** by relaxing a
rule: it allocates **planned discretionary portions** — *"two 400 kJ
indulgence units per week"* — inside the energy model.

That is a materially better mechanism than admitting NOVA 4 wholesale, and the
reason is the one §4.5 already turns on. Relaxing a rule for a week is a
*boundary moved*, which is the rigid-restraint pattern the research ties to the
"what-the-hell effect": the rule was there, then it wasn't, and the next breach
has nothing to breach. A discretionary allowance is **flexible restraint made
explicit** — the indulgence is inside the plan, budgeted, and does not need the
rule to be suspended to fit.

Mechanically it is close to something that already exists:
`SlotSpec.skip_estimate` is *"a meal genuinely eaten, costed against the day"*,
and an indulgence unit is the same idea with a budget attached rather than an
estimate. `week_defaults` deciding a slot is discretionary, plus a kJ cap, is
most of it.

**Settled 2026-09-01: this is a preset field, not a global setting** — *"lets
follow CSIRO approach, but this should be a feature flag defined in the
preset"*, and *"treat budgets should again be defined in preset."* So a preset
declares both whether indulgence units are in play and what the allowance is:
a comfort week may carry more than a strict block, and a block may carry none.
Absent means none, which is today's behaviour — the §4.1 rule that every
preset field must have an absent-meaning.

**This does not replace 7.1 and 7.3** — a genuinely lazy week is about effort,
and effort is a different axis from indulgence. But `comfort` should reach for
this first and `allowed_nova_groups` second.

### 7.5 "Strict" is not a setting — the measurement is fixed, every rule is the preset's

Settled 2026-09-01: *"strict is not a term I should be catering for; any logic
should be defined by the preset."*

**Right, and honouring it means fixing one thing so everything else can vary.**
There were two questions hiding under "how strict is processed", and only one
of them is a preference:

| | Question | Where it belongs |
|---|---|---|
| **the measurement** | given four ingredients at NOVA 1, 1, 1, 3 — what is *the dish's* score? | **fixed, app-wide** |
| **the rule** | what may this week's food do with that score? | **the preset, entirely** |

The measurement cannot be presettable. `design-00` F5's whole argument for
storing a NOVA score is that it is a **fact about the food**; if preset A
scores a dish 3 and preset B scores it 1, the stored number means nothing and
the Library cannot be filtered on it. It would also disagree with the shopping
list, which already takes the worst ingredient when merging lines.

**So the aggregation is fixed at worst-ingredient — matching shopping, one
call, one answer — and the preset gets every rule built on top.** That is
strictly more expressive than the strict/proportional choice this section
originally offered, because the preset can say things neither of those could:

| A preset may say | Meaning |
|---|---|
| `allowed_nova_groups: [1,2,3]` | nothing more processed than this |
| `allowed_nova_groups: [1,2,3,4]` + `max_nova4_ingredients: 2` | convenience allowed, but bounded |
| a cap **per meal** vs **per week** | a Friday shortcut without a processed week |
| no NOVA key at all | the app has no opinion this week |

**Nothing in the app is "strict" or "lenient" any more.** There is a number,
measured one way, and a set of rules that a named preset owns. Which is the
same division §7.1's hole demanded anyway: *selection* has to apply the
**current** preset's rule, because the catalog outlives the preset that
admitted a recipe.

### 7.6 "Lazy" gets dials, not a definition

Settled 2026-09-01: *"lazy week is a preset to be defined — no hard/fast rules
yet."*

So this design deliberately **does not decide** whether a lazy week is about
effort, convenience food, or both. That is a preset's content, and deciding it
here would be the hard-coding §3.4 rules out — a shipped `lazy` preset with
opinionated values baked in is exactly a preset the app treats as special.

What the work owes instead is that **every dial exists and is presettable**, so
the definition can be written later without code:

| Dial | From |
|---|---|
| cooking-time ceiling | §7.3 — already a config key |
| processing levels allowed | §7.1 |
| convenience-item cap, per meal or per week | §7.3 |
| which meals are cooked at all | `week_defaults` |
| how much batch cooking | `week_shape` (`design-02`) |

Ship a `lazy` row in `presets.json` as a **starting point to edit**, not a
definition — the same status `default` has (§3.4).

**Recommendation: ship 7.1, 7.3, 7.4, 7.5 and 7.6. Do not build 7.2.** If the cap turns out
too blunt in practice — if "two NOVA-4 ingredients" genuinely fails to
separate a jar of pasta sauce from a packet of instant noodles — that is the
evidence that would justify revisiting a sub-classification, and it should be
gathered before the taxonomy is, not after.

## 8. What the user stops choosing

The payoff, and the thing to hold the UI to. With a block active:

**Decided for you:** calories · protein floor · deficit rate · diet style ·
whether to bulk cook · prep behaviour · meal-type defaults · (later) training
intent and whether this is a deload week.

**Still yours:** "steak on Wednesday" (PROMPT-3) · "not tacos this week" ·
marking what actually happened.

That division is the research's **flexible restraint**, and it is deliberate.
`docs/rapid-weightloss.md` names rigid, all-or-nothing rule sets as the
profile correlated with regain, disinhibition, and the "what-the-hell effect"
after a minor breach — while the CSIRO cohort using automated planning lost
**24% more at 12 weeks**. The system should hold the numbers; the human should
keep the vetoes. A UI that offers a calorie slider while a block is active is
giving back the decision the block exists to remove.

---

## 9. Surfaces

**Load the `ui-work` skill before touching any `ui_*.py`.** What follows is
placement and scope, not styling.

### 9.1 The weekly pick — the surface that matters most

A preset chosen weekly needs to be **one obvious control near the point of
generation**, not buried in Settings. The Plan destination's Generate button
already opens the review dialog rather than running the week; that dialog is
where the week's shape is settled, so the pick belongs at the top of it, above
the batch toggles it can override.

Three rules:

- **The default is last week's pick.** An empty choice every Monday
  reintroduces exactly the decision this arm exists to remove.
- **During a block, show the pinned preset and which block pinned it** — with
  an explicit "end early", never a disabled control (§4.5).
- **Say what the pick changed.** A one-line diff against `default`
  ("NOVA 4 allowed · prep ceiling 20 min · no bulk cook") is what makes a
  preset trustworthy. A mode whose effect you cannot see is the stale-config
  problem wearing a new hat.

### 9.2 The preset editor — deliberately bounded

Asked for directly: *"I would also need an interface to define different
profiles."* Agreed, with one scoping decision.

**The editor exposes a bounded set of preset-able keys, not all of
`CONFIG_FILES`.** A general config editor over every key in five files is a
large, low-value surface — most core keys (`week_start_day`, `regional`,
`meal_types`) are not things a *mood* varies. The set a mode actually varies
is small, and comes straight from the brief's own examples:

**The list is derived from §3.4a's `data` rows, not assembled from examples.**
It was originally taken from the brief's own — a reasonable way to start a
list and a poor way to finish one, since it makes the editor a record of what
the user happened to complain about. §3.4a ran the other way, with the burden
of proof on `code`, and these are its output.

**Two tiers on one panel**, per §3.4a's schema-is-not-the-panel argument.
Every row here is equally a preset key; the tier decides only what is open by
default. The split is by how often a *week* varies it, and a preset naming an
Advanced key survives an edit untouched exactly as one naming an open key does.

**Tier 1 — open.** The nine groups a week routinely varies:

| Key | Varied by |
|---|---|
| `dietary_rules.allowed_nova_groups` | comfort, lazy, strict |
| `dietary_rules.active_diet_styles` | Fast 800, keto, gut-health — day-scoping is `PROMPT-2` |
| `week_defaults` | which meals are cooked at all |
| `meal_styles` | "shakes and soups" |
| `weekly_schedule.<day>.net_carbs_g` | the week's carb cycling. Survives hydration, `design-03` §5 |
| `weekly_schedule.<day>.meal_overrides` | a pinned meal budget, verbatim through hydration |
| `meal_weights` | where the day's energy sits |
| **cooking ceilings** — active weeknight, active weekend, **elapsed weeknight**, **the long-cook threshold** | lazy; the effort axis and the *presence* axis §7.3 separates from it |
| `week_shape` — who cooks what, when, who eats it | **all of them.** `design-02`; absorbs `batch_target_servings` per batch |

**Tier 2 — Advanced, folded.** Everything else §3.4a ruled `data`. Same panel,
same validator, same file:

| Group | Keys | Varied by |
|---|---|---|
| cuisine block shape | `cuisine_block_pattern`, `min_baseline_cuisine_share` | comfort wants one cuisine all week; adventurous wants seven |
| favourite pinning | `favorite_breakfast_slots`, `favorite_dinner_slots`, `favorite_reuse_days` | **the lazy/comfort dial §7.6 asks for** |
| variety pressure | dinner protein repeat cap and consecutive-night rule, `protein_avoid_window`, `protein_lookback_entries` | a comfort week eats chicken four times |
| the training breakfast | `WORKOUT_BREAKFAST_STYLE`, `WORKOUT_BREAKFAST_TYPES`, `MORNING_TRAINING_CUTOFF` | whether an early ride morning is fuelled like a lift |
| training fuelling | `TRAINING_INTENSITY_SPLIT`, `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES` | a low-carb week buys a session's burn back as protein and fat |
| the week's clock | `MEAL_TIME_OF_DAY`, `WEEKEND_DAYS` | a late-dinner week; a long weekend. `WEEKEND_DAYS` **overrides a `base_schedule`-derived default** rather than replacing it — see §3.4a |
| the table | `serving_rules.servings_per_meal` | cooking for guests |
| appetite | `PORTION_DENSITY_GUARD`'s nine per-ingredient caps | a bigger eater wants 3 slices of toast |
| rejection weight | `rejection_decay_days`, `rejection_reason_window_days`, `REJECTION_REASON_SIGNAL_MIN` | how strictly past vetoes bind. One policy, moved together |
| nudges and notes | `NUDGE_FOOD_SAMPLE_SIZE`, `SUNDAY_PREP_REHEAT_MINUTES` | how hard the week leans on the whole-foods corpus; how long reheating takes in your kitchen |

Plus the NOVA-4 count cap (§7.3), open or folded as it lands.

**Nineteen groups, and the cost is six widget shapes' worth of nothing new.**
`design-03` §1's rule holds: cost tracks distinct *shapes*, and every added row
is `ui.number` (shape 2) or `ui.select(multiple=True)` (an XS addition to the
vocabulary). What the fold pays for is panel *length*, which is a real cost
that shape-counting does not capture and a disclosure triangle does.

Three corrections the audit forces onto this list, each of which would
otherwise have surfaced mid-build:

- **"Prep ceilings" was ✅ against a dimension, not against a key.** Both
  numbers are Python constants with no `planning_rules` entry, the elapsed-time
  ceiling is a third constant that was on nobody's list, and the long-cook
  threshold is a fourth that exists in **four separate prose copies**.
  `design-00` §1 marked the row ✅ and `design-03` §6 scored it **XS** on
  "code support: `prep_limit_for` exists" — true of the *function*, not the
  *key*.
- **Three groups need a code change before they can be fields at all.** The
  dinner variety numbers, the portion-density caps and the long-cook threshold
  are English inside prompt constants. `build_batch_roast_rule` is the
  precedent for all three and each is an **S**; they are the only rows here
  that are not free once the container exists.
- **`favorite_reuse_days` brings the first real validation rule.** Both windows
  must stay inside `history_max_entries` or they silently stop binding — which
  is also why that bound stays in `config` and out of this list. §4.2's
  validate-before-save function has something to check beyond types.

**Five original rows were config keys rather than constants**, so a constant
sweep was never going to reach them; each is justified in Tier 1 above rather
than carried on inertia. **None was dropped.** `weekly_schedule` as a whole
stays excluded for the hard code reason below, which is not a scoping choice.

**`weekly_schedule` is deliberately not on that list as a whole**, and the
reason is a hard limit in existing code rather than a UI choice.
`hydrate_dynamic_targets` **replaces** a day's `calories` and `protein_g`
whenever that macro's `target_modes` entry is `auto` — the shipped default for
both — so a preset setting either would be **inert**: overwritten before
generation, while the editor showed a figure nothing plans from. That is the
exact bug the `ui-work` contract warns about (*"a widget reading the file is
displaying a number nothing plans from"*), and the live example is a config
stating 1000 kcal on a Thursday that every run plans at 1722.

`net_carbs_g` is passed *into* the engine and never replaced, and a
hand-written `meal_overrides` entry stays verbatim, so those two work. The
division that falls out:

> **A preset shapes the week's *distribution* — carbs, meal pins, meal
> weights. A block shapes its *level*, via `target_rate_kg_per_week` and the
> protein floor.**

Which is §4.2's rule reached from the other side: a preset that set the
calorie level would either do nothing or have to flip `target_modes`, and
flipping that silently changes *who decides* a number. Corrected here from
`design-03` §5 and §7.

Nineteen groups across two tiers. That is still a tractable panel because
nine of them are open and ten are behind one fold — and **hand-editing
`presets.json` stays authoritative for all nineteen**, the same division
`training_schedule` already has, where the review dialog edits it and the file
remains the source of truth. The escape hatch is no longer doing the work it
did in the first draft: §3.4a's rule is that a key must be reachable *from
data*, and being one fold down on a panel is not a failure of that.

A preset naming an override path the editor does not expose must **survive an
edit untouched**. Read-modify-write per preset through `PROMPT-8` §1a's
methods, the way `save_config_keys` already merges named keys per file so a
hand-added key the app has never heard of survives the next settings change.

Leaf paths make this materially cheaper. Under the superseded whole-key rule,
exposing one field of `planning_rules` obliged the editor to materialise and
store the *entire* object, freezing every value it did not show; a path-valued
override writes one leaf and leaves the rest inherited.

### 9.3 Everything else

- **Settings — a Blocks panel.** List, active marked, create/edit/end-early.
  A block persists on edit, like `target_modes` and an accepted
  `training_schedule` proposal — the **third writer to `config/`**, on the
  same test both pass: a standing commitment, not an input to one run.
  Through the supplemental write path `PROMPT-8` §1a adds — **not**
  `save_config_keys`, which raises on any key outside `CONFIG_FILES`, and
  `blocks.json` is supplemental for the same reason `presets.json` is.
- **Telemetry header — name the active preset and any block.** "What am I
  doing this week" belongs where the week's numbers already are, and it gives
  `training_intent` and `peak_day` the human reader §4.1 requires.
- **Review dialog — mark which days are in-block**, since §5 means a boundary
  can fall mid-week and the target curve will visibly step.
- **No new colour.** The `ui-work` skill's table already has amber meaning
  five things. A preset is a label; glyph-and-wording is the route
  `sync_freshness` and the adherence marks both took.

## 10. Acceptance

The compatibility claim first, because everything else is negotiable and this
is not:

- **No `presets.json` and no `blocks.json` → byte-identical.** Same merged
  config, same prompts, same targets, same generated week as `main` today.
  Assert it; do not assume it.
- A preset override whose **first path segment** `CONFIG_FILES` does not know
  **fails at load**, naming preset and path.
- A preset override replaces **its leaf**, whole; no recursive merge anywhere.
  Assert the case that refuted whole-key replacement (§3): a preset setting
  `dietary_rules.allowed_nova_groups` leaves all 17 `banned_ingredients`
  entries and `active_diet_styles` **intact**.
- An override whose value is `[]` or `{}` is applied as that value, not
  treated as absent.
- **`presets.json` is read and written through its own repository methods**,
  not `save_config_keys` — which raises on any key `CONFIG_KEY_OWNER` does not
  hold, and by §2 that is every key in this file. Assert the write path.
- Overlapping blocks **fail at load**, naming both and the overlap.
- **A block carries no `preset` field** (§4.1a). Assert that a block naming one
  fails at load rather than being ignored — an ignored field is the
  "appears applied and is not" failure §3 refuses.
- A block spanning Mon–Thu of a Mon-start week caps four days and leaves three
  untouched, across **both** hydration passes (the UI preview and generation).
- A block's protein floor is resolved **once** and does not move when a new
  weigh-in lands mid-block.
- A block whose floor the active preset cannot satisfy **warns at load**,
  naming both.
- `PlannerState.planning_config()` sees the block, so the header previews what
  the run will aim at. CLAUDE.md's standing rule — *"a number the UI displays
  and a number a run plans against must come from one call, not two"* — and
  the reason block resolution belongs at the load/hydration layer rather than
  inside generation.
- An expired block is inert and still present on disk.
- **The week's preset is recorded** on `WeekPlan` and in the history entry
  (§4.6). Without it the feedback arm can compare weeks and never explain
  them.
- **A recipe admitted under a relaxed preset is not served under a strict
  one** (§7.1). Import a NOVA-4 dish under `comfort`, switch to `strict`,
  generate: `select_favorite_assignments` must not pin it. This is the one
  acceptance test that fails silently today, and would not be noticed until
  the first strict week after a comfort week.
- **A *user* pin of that same dish is refused too**, by the same shared
  eligibility function and with the reason named (§7.1's second hole). A pin
  overrides preference, never a hard rule.
- **The NOVA-4 count cap binds per meal** and is reported when it fires, the
  way `cap_to_weighted_share` and the seafood cap already report.

New: `tests/test_presets_and_blocks.py`. Extended:
`test_config_layout.py` (the merged snapshot, regenerated only alongside the
deliberate change, so the diff shows exactly which keys moved),
`test_planner_dynamic_targets.py` (the frozen floor; the mid-week boundary),
`test_ui_state.py` (the header previews the block).

---

## 11. Sequence within the arm

> **`design-03` §8 is the authoritative build order.** It was written
> capability-first and orders by what is cheapest to actually build, which
> moves the preset *selector* and *editor* ahead of the block machinery below.
> The table here is the dependency view — what needs what — and the two agree
> on dependencies while differing on order. Where they differ, follow
> `design-03`.

| | | Size | Why here |
|---|---|---|---|
| 1 | **PROMPT-2** — day-scoped diet styles | S | §5. Substrate, not sibling. Ships standing value alone |
| 2 | **PROMPT-3** — user recipe pin | S | Independent; the brief's loudest complaint; needed for §7's "still yours" |
| 3 | `presets.json`, the layer, the manifest check | M | No dated logic yet — the merge is the risky half, isolate it |
| 4 | `blocks.json`, expiry, overlap refusal, mid-week resolution | M | Rides on 1 and 3 |
| 5 | Protein floor as a block property | S | Needs 4. **D3 is unanswered until this lands, and 144 g stands until then** |
| 6 | `allowed_nova_groups` presettable **+ the selection-time filter** | S | §7.1. The filter is the half that is not optional |
| 7 | NOVA-4 count cap and prep-ceiling relaxation — the `lazy` preset | S | §7.3. Mirrors `max_seafood_meals_per_week` |
| 8 | The weekly pick in the review dialog; preset recorded on the week | M | §9.1, §4.6. Load `ui-work` first |
| 9 | Preset editor, Blocks panel, header, in-block banner | M | §9.2–9.3 |

**`week_shape` is the largest thing a preset carries and has its own
document** — `design-02-week-shape.md`. It replaces the two batch toggles
outright, moves the batch logic out of `ui_generation.py` (where the CLI
never sees it), and depends on the freezer ledger. Its step 1 — moving
`apply_batch_selections` into `week.py` unchanged — is worth doing whatever
happens to the rest of it.

Steps 1 and 2 are already written as prompts and neither commits to anything
in 3–9.

---

## 12. Deliberately not in this design

- **Presets that inherit from other presets** (§3).
- **Blocks that set arbitrary config keys** (§4.1) — that is a dated preset,
  and then there is no reason to have two objects.
- **A block writing `weekly_schedule` calories or `target_modes`** (§4.2).
- **A nightly-recomputed protein floor** (§6).
- **Auto-deleting an expired block** (§4.3).
- **Anything reading `training_intent` or `peak_day`** beyond displaying them.
  Arm E, and gated on `activity_log` having rows (PROMPT-1).
- **Automatic block *suggestion*** — "you have plateaued, start a block". That
  needs the outcome-measurement half of Arm D, and it is a much larger
  product question than a stored intent.
- **SIGA or UNC sub-classification of NOVA 4** (§7.2). Investigated and
  declined on **missing input data, not on merit**: SIGA needs additive-level
  detail and sodium that no `Ingredient` carries, and it classifies
  supermarket *products* where this app plans cooked *dishes*. The count cap
  in §7.3 is the graded answer that motivated the question. Revisit only if
  the cap proves too blunt in practice — that evidence first, the taxonomy
  second.
- **A general config editor** over every `CONFIG_FILES` key (§9.2). The editor
  is bounded to the eight groups a mood actually varies; the file stays the
  escape hatch.
