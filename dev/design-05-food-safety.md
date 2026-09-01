# Design 05 — Storage windows are a property of the dish

Status: **built**, 2026-09-01. Two corrections were made during
implementation and both are recorded in place rather than silently applied —
§2a's hours-to-days arithmetic (which decided the number) and §5's consumer
list, which was short by two.

Split out of `design-04` on 2026-09-01, because the answer is not one number.

> *"All scheduled meals need to be consumed within food safety guidelines — it
> doesn't matter if it's a bulk prep or a leftover meal. I believe food stored
> in fridge is usually 96 hours (4 days), except for pasta/rice dishes which
> should be consumed within 2 days. So need to track cook event and ensure
> scheduled meal consumption is within limits."*

**This is the only part of the program where being wrong makes somebody ill**,
so it is designed with different defaults from everything else here.

---

## 1. What the app does today, and why it is wrong in both directions

`inventory_rules.fridge_safe_days` is **3**. One global number, read in five
places: `spread_batch`'s span bound, `apply_batch_selections`' `max_day_index`,
`validate_week`'s backstop, `storage_note`'s refrigerate-versus-freeze wording,
and the per-card fridge badge.

A single number cannot be right, and this one is wrong **both ways at once**:

| Dish | Safe for | App says | |
|---|---|---|---|
| A beef stew | 4 days | 3 | **too short** — a day of good food thrown away, and a batch that could have covered Thursday |
| A rice tray bake | **2 days** | 3 | **too long** — and this is the direction that matters |

The rice case is not pedantry. Cooked rice and pasta carry *Bacillus cereus*
spores that survive cooking and produce toxin as the dish sits; the 48-hour
figure exists for that reason and it is why the general 4-day rule has an
exception carved out of it at all.

**So the current setting is a compromise nobody chose**, and moving it to 4
without the dish-level exception would make the dangerous case worse.

## 2. The rule: storage life belongs to the dish

Two tables in config, and a class on the recipe.

**Neither half is a preset key, and that was asked and answered directly.**
`PROMPT-7`'s hard-coding audit ran every planning constant against the rule
that *presets should be the predominant way to customise meal planning*, and
this was the one row where the answer was to take the thing off the table:
*"`inventory_rules.fridge_safe_days` needs to be a per-dish measurement, not
part of preset"* (2026-09-01). The audit had floated a preset that could only
ever **tighten** the window — a `min()`, the shape `DietStyle.calorie_ceiling`
uses — and that is now dropped, because §1's table is the refutation: a
tightening-only lever fixes the stew case *never*, and the rice case only by
accident. **A preset over one global number could only ever have picked a
different wrong global.**

`Recipe.storage_class` is also the reason this belongs on the recipe rather
than anywhere in `config/`: it passes `design-00` F5's rule — *store a
measurement iff it is scale-invariant; never store a verdict* — since a rice
tray bake is `rice_or_pasta` scaled to two servings or to six. The **verdict**
it feeds ("can this batch reach Thursday") stays derived, in the five consumers
§5 lists.

```
inventory_rules.storage_windows:
  fridge:
    default:            96h   (4 days)
    rice_or_pasta:      48h
  freezer_months:
    soup_stew_casserole:  2
    cooked_meat:          2
    cooked_poultry:       2
    poultry_pieces:       1
    fish_seafood:         1
    fried:                1
    default:              1
```

**The freezer figures are the *lower* end of each range you gave** (2–3, 2–4,
1–3, 1–3, 1 month). That is deliberate and it is the governing principle of
this document — see §3.

`Recipe.storage_class` is a new field, one of that enumeration, reported by the
model exactly as `long_oven_cook` and `bulk_prep_friendly` already are.

### 2a. The windows are stated in hours and measured in days — say which

Added 2026-09-01 under review, because the two halves of this document did not
agree and the disagreement points the unsafe way.

The windows above are hours (96h, 48h). Everything that could measure against
them holds a **date**: a `SlotSpec` carries a weekday name, `WeekPlan` carries
`week_start_date`, `CookEvent` resolves to a grid day, and `FreezerItem` carries
`cooked_on`/`frozen_on`. Nothing anywhere stores a *time*. So no consumer can
establish that a Sunday cook eaten Thursday was inside 96 hours — Sunday 09:00
to Thursday 20:00 is 107, and Sunday 18:00 to Thursday 12:00 is 90, and the
stored data cannot tell those apart.

**The policy: the windows are whole-day gaps, and the figures in hours are the
guidance they were derived from, never a computation the app performs.**
Concretely:

```
days_allowed = window_hours // 24        # 96h → 4 day-gaps, 48h → 2
```

The figure has to be derived here rather than written into the config as `4`
beside `96h` — those two are not the same claim, and a reader who saw both
would reasonably assume they were.

> **Corrected 2026-09-01, and this is the correction that decided the
> number.** This section originally derived **3** day-gaps from 96h and
> **1** from 48h, by worst case: a day-gap of 4 spans anywhere from 72 to 120
> hours, so the only way to *guarantee* staying inside 96 without a clock is
> the floor of that range. The prose and the code line beside it disagreed —
> the line said `// 24`, which is 4 — and so did §5, whose whole "reaches
> Thursday instead of Wednesday" arithmetic is computed at 4.
>
> **Resolved in favour of the day count**, because the source figures *are*
> day counts and the hours are their gloss: the requirement was written
> "96 hours (4 days)" and "within 2 days". Taking the floor of the range
> would have tightened the default by a further day on top of the per-dish
> exception — and the tightening is not where the safety win is. The win is
> that rice is now bound at 2 where it was 3; the default moving 3 → 4 is the
> stew case being *un*-tightened, which is the other half of §1's table.
>
> Recorded rather than quietly rewritten, per the rule `design-02` §5 states
> about its own moved premise. Two consequences to hold on to: the
> conservative reading is the one to return to if the app ever becomes
> time-aware, and §7's first acceptance bullet consequently reads **two**
> day-gaps for rice, not one.

**Adding cook and consumption *times* is the alternative, and it is
deliberately not taken.** It would buy a genuine 96-hour guarantee and cost a
time on every cook event, a time on every eating slot and a clock question at
every freeze — for one extra day of reach on the stew case, in an app whose
grid is day-granular everywhere else. If a future change makes the app
time-aware for other reasons, this is the section to revisit; until then the
app must not print "96 hours" at a user, because it does not know that. **Every
surface says days.**

## 3. Every default here fails **short**, and that inverts the house rule

**This is the one place the codebase's usual default convention must not be
followed, and the reason is on the record.**

Everywhere else, an absent value resolves to *the behaviour before the feature
existed* — `long_oven_cook` defaults False, `total_time_minutes` defaults to
None meaning unknown, an absent `sourcing` block emits nothing. All of those
are safe because being wrong costs a worse meal plan.

Here it costs a food-poisoning risk. So:

- **An unclassified dish gets the shortest fridge window (48h), not the
  default 96h.**
- **An unclassified dish gets the shortest freezer window (1 month).**
- **A missing `cooked_on` is not defaulted at all** — the item is flagged as
  undateable rather than assumed fresh.

**And there is a documented failure that makes this concrete rather than
theoretical.** CLAUDE.md records that `is_sunday_prepped` broke because the
anchor *"came back with both flags `False` despite `LONG_COOK_ANCHOR_SLOT_
DIRECTIVE` telling the model to set one"* — a model self-report that was simply
dropped. `storage_class` is the same kind of field with the same failure mode.
If a dropped report defaulted to 96 hours, **the failure mode of a model
forgetting a field is a rice dish scheduled four days out.**

Failing short means a dropped report costs a shorter batch. That is the right
direction to be wrong in.

## 4. The ordering problem, and why the prompt is the answer

**The grid is built before any recipe exists.** `spread_batch` decides how far
a batch reaches during `default_week_spec` / `apply_batch_selections`, and
generation happens afterwards — so at the moment the span is chosen, nothing
knows whether the dish will be a rice tray bake.

Three ways out, and only one is good:

- **Plan short (48h) always.** Safe and useless: no batch could reach past two
  days, which removes bulk cooking.
- **Plan long, validate after.** The rice dish arrives, the span is already
  wrong, and the run has to be thrown away — a 30s–3min retry to discover a
  constraint that could have been stated.
- **Tell the model the span the slot needs, and validate against it.** ✅

**The third is what this codebase already does for exactly this shape of
problem**, twice. `build_batch_roast_rule(config, days)` names the days a long
cook may land on *because* `reject_misplaced_long_cook` will reject the others,
and CLAUDE.md states the lesson outright: *"a model rejected for breaking a
rule it was never given burns a 30s–3min retry to discover a constraint one
sentence would have stated."* `WEEKEND_PREP_LIMIT_MINUTES` learned the same
thing from the other direction — stated in the prompt, enforced nowhere, so a
200-minute recipe passed validation while violating its own brief.

So:

- **`build_storage_rule(span_hours)`** joins the shared rules block: *"this
  dish will be eaten over N days from cooking and refrigerated in between, so
  it must keep that long — do not build it on rice or pasta."* Emitted only
  when the span exceeds the short window, so a single-day cook's prompt is
  byte-identical to today's.
- **A validator rejects a returned `storage_class` whose window is shorter than
  the span the slot needs**, per the two-axis split `reject_misplaced_long_cook`
  already uses (`DayRecipes` from its context's day, `MealTypeWeekRecipes` over
  its own keys, one shared function so the two axes cannot disagree).

**Two batch anchors are exempt from the *day* judgement but not the window** —
they are cooked on prep day, not their grid day, which `prep_day_batch_slot_ids`
already identifies. Their span is measured from prep day and is therefore
*longer*, not shorter. This is the case that most needs the rule.

## 5. What changes in the five consumers

| | Today | After |
|---|---|---|
| `spread_batch` `max_span_days` | `fridge_safe_days` | the **default** window — the model is being told to build something that keeps that long (§4) |
| `apply_batch_selections` `max_day_index` | `fridge_safe_days - 1` | same, from the default window, still counted from prep day |
| `validate_week` backstop | one number | the **actual dish's** window once a recipe exists; the default before that |
| `storage_note` | fridge-vs-freeze on one number | on the dish's own window |
| card badge | one number | the dish's own window, and it may now differ between two cards in one week |

**The backstop is the one that genuinely changes character.** Today it is a
static check on the grid; afterwards it checks a *generated* week against the
dishes actually in it — which is where a dropped `storage_class` or a rice dish
that slipped through is caught. It should report the slot and the days, not
silently trim.

> **Two consumers were missing from this table, found while building.**
>
> - **`generate_sunday_prep_session`'s prompt** stated a "4-Day Storage Rule"
>   and interpolated the global number into it, so it was a sixth read and one
>   that printed the wrong figure at the model. It now says the Storage line
>   per candidate is already computed and that two candidates may legitimately
>   differ, without naming a number at all.
> - **`select_favorite_assignments`** is a **third route to a long span**,
>   beside a batch spread and a hand-built chain, and the one §4's
>   prompt-then-validate pairing structurally cannot cover: a favourite is
>   never generated, so nothing briefs it and nothing judges its response.
>   Left ungated it would have reproduced, in mirror image, the bug
>   `favorite_fits_day` exists for — there a *saved* braise was refused a
>   Thursday a *generated* one could take. `favorite_keeps_long_enough` is its
>   sibling and deliberately a second function rather than a widening of the
>   first: attention and presence are one axis, how long the dish keeps is
>   another, and a stew passes both where a rice tray bake passes only one.
>
> Also settled here: `storage_windows` **merges** over the shipped tables
> rather than replacing them. Replacing meant a config naming only
> `fridge: {"default": 72}` had no `rice_or_pasta` row, so a rice dish
> resolved through `default` and its window *lengthened* — §3's rule broken by
> a config that never mentioned rice.

**Note the fridge default moves 3 → 4 days**, which *lengthens* batches: a
prep-day batch reaches **Thursday instead of Wednesday**. That is a real
behaviour change in the permissive direction and must not ride along unnoticed
with a safety change — it should land with the dish-level exception in the same
change, never before it.

> **Corrected 2026-09-01.** This sentence read "reaches Wednesday instead of
> Tuesday", which was off by one at *both* ends and disagreed with `design-02`
> §5 and `design-04` §0, both of which are right. The arithmetic, verified
> against the code: `apply_batch_selections` passes `max_day_index =
> fridge_safe_days - 1` and day index `i` is `i + 1` days after prep, so a
> Sunday prep session reaches day index 2 — **Wednesday** — under today's
> `fridge_safe_days: 3`, and day index 3 — **Thursday** — under the 4-day
> default. Recorded rather than quietly rewritten, per the same rule
> `design-02` §5 states about its own moved premise.

## 6. Where a freezer item's window comes from

`design-04`'s `FreezerItem` carries `cooked_on` and, usually, a `recipe_id`. So:

- **With a `recipe_id`** — the catalog recipe's `storage_class` gives the
  freezer window. Nothing extra to enter.
- **Without one** — the class is asked for when the item is added by hand. One
  select, six options, defaulting to the shortest.
- **Past the window** — warn on the item and on any draw that would eat it.
  **Never auto-remove** (`design-04` §5): on a hand-declared list that would be
  the app editing your statement of what you own.

**A quality window is not a safety window, and the wording must say which.**
Frozen food does not become unsafe at two months; it degrades. The fridge
figures are safety; the freezer figures are quality. Two different sentences,
because "unsafe" and "past its best" prompt different behaviour and conflating
them teaches you to ignore both.

## 7. Acceptance

- A rice-classed dish cannot be scheduled more than **two day-gaps** from its
  cook (§2a's 48h, corrected), from **either** a batch spread or a hand-built
  leftover chain — and, added in implementation, from a **pinned favourite**,
  which turned out to be a third route neither the prompt nor the response
  validator can see because a favourite is never generated.
- A dish with **no** `storage_class` gets the rice window, not the default one.
- **No surface states hours.** The config carries them as the derivation; every
  note, badge, warning and log line says days (§2a).
- A recipe saved before this field existed loads, and is treated as
  unclassified — so it is short, not long.
- The prompt names the required span **only** when it exceeds the short window;
  otherwise byte-identical.
- A model returning a class too short for its slot's span is **rejected and
  retried**, not silently accepted.
- A prep-day anchor's span is measured from prep day, not its grid day.
- `storage_note` and the card badge agree with each other on every card — the
  disagreement CLAUDE.md already had to fix twice for this pair.
- A freezer item past its class's window warns and stays.

New: `tests/test_food_safety.py`. Extended: `test_week_mechanics.py`
(`validate_week` against dish windows, the lengthened default),
`test_meal_selection.py` (the prompt rule and the rejection, beside the
long-cook ones it mirrors).

## 8. Deliberately not in this design

- **Deriving the class from ingredients.** "Contains rice" is a substring match
  away from claiming a rice-vinegar dressing is a rice dish, and the direction
  it fails in is the unsafe one. The model reports, the validator checks, the
  default is short.
- **Auto-trimming a week that violates a window.** Report the slot; a plan
  quietly rewritten is one nobody checks.
- **Modelling how long something sat out before going in the fridge**, or
  fridge temperature. Neither is observable.
- **Treating freezer windows as safety limits** (§6).
- **Letting a preset vary any of it** — settled 2026-09-01, see §2. The
  windows are a food-safety reference table and the class is a measurement;
  neither is a preference, and `design-01` §3.4a records the verdict alongside
  every other planning constant.
