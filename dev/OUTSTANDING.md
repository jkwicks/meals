# Outstanding — everything still open, ranked

Compiled 2026-09-01, after the research audit (`design-00` §5a), and extended
when `docs/exercise-protocols.md` arrived. This is a mixed register now: several
enabling prompts have shipped, while "outstanding" below means design,
dependency, or implementation still open.

Cite entries **by name, never by number** — the same rule CHANGE-QUEUE.md
learned across thirteen releases, for the same reason.

---

## A. Blocked on you — three things, and one is calendar time

### A1 — D1 needs 14 days of complete Cronometer logging
**Start it today; it is the only item here where waiting is the work.**
`measure_adaptive_tdee` now returns `ready` (span exactly 7 against a floor of
7) with an estimate of **1710 kcal** against a formula **2573** — a 33.5% gap,
outside the 25% tolerance, so it is rejected. 1710 is *below* the computed BMR
of 1871, which is not physiologically coherent, so the intake log is
under-reported: 5 logged days in the span, one of them 429 kcal.

Until it closes, TDEE is contested by ~860 kcal — wider than most deficits —
and no deficit can be chosen rationally. Completeness matters more than count.

### A2 — Hevy account check
`PROMPT-4` Part 1 is written and waiting on a key (Hevy web app → Settings →
Developer, **Pro required**), into `.env` as `HEVY_API_KEY`. The API-level
question is closed; the residual is **what fraction of working sets actually
carry a non-null `RPE`.** Under roughly half and three of the four gym metrics
degrade to volume-only, which changes what Arm E is worth building.

### A3 — Is a recipe pin preset-carried, or a weekly veto?
**New 2026-09-01**, raised by the instruction that all customisation should go
through presets where possible. `design-01` §8 files "steak on Wednesday" under
what **stays yours** week to week, with a research argument behind it — rigid
all-or-nothing rule sets correlate with regain, so the system holds the numbers
and the human keeps the vetoes. Reading it as a preset field is equally
buildable on the same `SlotSpec.recipe_id`, and the row is three selects either
way (`design-03` §2).

They differ in one consequence: a preset carrying `recipe_pins` references
catalog ids, so a deleted recipe needs an absent-meaning — degrade to
generating that slot normally, with a warning, never a load failure.

**Recommendation: build the weekly veto first (PROMPT-3, unchanged), decide the
preset version after using it.** The preset form is a strict addition on top.
`PROMPT-3` is **next up** in `dev/README.md`'s delivery order and its own gate
(`PROMPT-8`, for the shared eligibility function) is satisfied, so this
decision is now the live one rather than a hypothetical.

### A4 — Confirm the word "preset"
`design-01` §1 recommends it over the brief's "profile" because
`config/profile.json` already holds `user_profile`, and a name doing two jobs
in a *filename* is worse than either place it has happened before. Mechanical
to reverse, but it should be settled before a file exists.

---

## B. Designs and design gaps

### B1 — Arm B ✅ **designed** — `design-04` and `design-05`

Closed 2026-09-01. Every question this row listed is now answered, and two of
them turned out differently than expected:

- the ledger is a **declared, confirmed list**, not a seeded count — which
  removes the drift problem rather than managing it;
- storage life belongs to **the dish**, not to config, which grew its own
  document and moved `fridge_safe_days` from 3 to 4 with a 48 h exception for
  rice and pasta;
- `keeps_for_days` closes differently than planned: **you record the cook date**
  and the app measures against it, rather than the app tracking an age;
- the movable prep day is settled at **N−1 or N−2 only**, placement derived
  from `base_schedule`, the *whether* being the preset's flag.

### B2 — Arm C: recipe library
Sketched in `design-00` only. Owns the metadata backfill (`cuisine`,
`total_time_minutes`, `nova`, `source_nutrition` over 91 records), URL-paste
import, and the middle-path selection question (D2's answer). Two decisions
inside it are still open — see D1 and D2 in section D below.

### B3 — Arm D: feedback signals
`design-00` §5 is an inventory, not a design. Owns the Garmin fetch additions,
the HRV band, and the AEE bias correction — see C1 and C2.

### B4 — Arm E: training engine — **first slice designed**
`design-06-exercise-planning.md` now owns persistent personal constraints,
selectable gym programs, structured workouts, manual limitation feedback, and
confirmed progression proposals. `PROMPT-14` and `PROMPT-15` make that work
pickupable.

PROMPT-1 is complete and no longer a gate. The first slice is deliberately
usable without Hevy: A2 gates evidence-backed load progression and the later
fatigue controller, not static constraint-aware workout planning. The full
HRV/RHR/e1RM fatigue matrix, deload controller, and functional-assessment trend
surface remain open in section C.

---

## C. Research specified, not yet placed

Ranked by value, not by document. All from `design-00` §5a's ❌ rows.

### C1 — Garmin AEE bias correction ⭐ **highest value here**
`docs/fitness-model.md` gives the formula: match a rolling 14–28-day intake log
against the EWMA-filtered weight velocity, and derive the systematic bias in
Garmin's active-energy figure. **This is exactly the tool A1 needs** — it turns
"the two TDEE numbers disagree by 860 kcal" from a rejection into a *measured
correction factor*. Listed in `design-00` §5 and designed nowhere.

### C2 — The HRV band
`readiness_log` has **6 rows and no reader**, which was `design-00`'s own
Tier 1 item 5 — and that item has **no prompt written**. The research
specifies the calculation precisely: **lnRMSSD**, a **7-day rolling average**
against a **28-day baseline**, with the band at **SWC = 0.5 × SD**. The app
stores raw `hrv_ms` and computes none of it.

### C3 — Session separation (6 h ideal, 3 h absolute floor)
**Reaches the meal planner today, not just Arm E.** It shapes
`training_schedule`, which `apply_training_adjustments`, `morning_training_days`
(the breakfast shake pin) and `training_pin_budget` already read. The cheapest
part of `training_intent` to make real.

### C4 — The fatigue matrix
Fully specified: weights (lnRMSSD 1.0, RHR 0.5, e1RM 1.5, volume deficit 1.0,
RPE spike 0.5, wellness 0.5), rolling windows per trigger, and a cumulative
threshold. Needs RHR (not fetched), e1RM (A2), and C2.

### C5 — Deload structure
**40–50% volume reduction, 85–90% intensity preserved, all sets capped at
3–4 RIR**, endurance volume cut ~50% and restricted to Zone 2. Plus the
proactive ceiling: N weeks of progressive loading forces a deload even if no
trigger fires. And the coupling `design-01` §4.7 records — **a fat-loss block
lowers every trigger threshold ~20%**.

### C6 — Smoothing alpha, unreconciled
Research says EWMA over a ~10-day window (α ≈ 0.18); `DEFAULT_SMOOTHING_ALPHA`
is **0.3** (~6-day). Not obviously wrong — `smooth_series` runs
forward-and-backward, so the filter applies twice — but **nobody has compared
them**, and it is one line plus a measurement.

### C7 — Forbes partitioning
Predicts what fraction of loss is lean tissue. Would say whether the protein
floor is actually working, which is the whole justification for D3's answer.

### C8 — Adaptive thermogenesis and T3
T3 falls 30–55% within 24–72 h of severe restriction; RMR drops 50–250+ kcal
beyond what tissue loss predicts. Currently only informs D1's *reasoning*;
nothing models it. Relevant to why the formula drifts over a long block.

### C9 — Sleep stages
SWS and REM thresholds are specified; `readiness_log` stores a sleep *score*
and hours, no stages. A fetch question before it is a design question.

### C10 — VLCD micronutrient fortification
A safety note attached to any 800 kcal preset. Not a feature — a warning the
`fast_800` preset should probably carry.

### C11 — Functional capacity outcomes for older trainees
`docs/exercise-protocols.md` adds chair stand, Timed Up and Go, grip strength and
VO2 reference measures. `design-06` places these in a later Arm D outcome loop:
record trends and optionally show reference bands, then use sustained movement
to reconsider program emphasis. They do not block `PROMPT-14`/`15`, and age
alone must not select a program or create a limitation.

### ⛔ Dropped
**16:8 / time-restricted eating** (2026-09-01, not important). It was the only
preset candidate needing a genuinely new dimension — the app has no
feeding-window concept — so dropping it leaves every remaining candidate
expressible in the existing model.

---

## D. Loose decisions inside documents already written

| | Where | Open question |
|---|---|---|
| ~~D1~~ | `design-01` §7.5 | ✅ **Settled** — worst ingredient wins, the same call `shopping.py` makes. The measurement is fixed app-wide; every *rule* built on it is the preset's |
| D2 | `design-00` F5 | **`source_nutrition` tolerance** — what counts as a disagreement worth flagging between a site's published figure and the derived total |
| D3 | `PROMPT-4` Part 2 | **Where Hevy data is stored** — deliberately deferred to the probe's payload measurement. Recommendation is `data/strength_log.json` |
| ~~D4~~ | `design-01` §4.7 | ✅ **Settled** — required, with an explicit override that is *recorded*, so a deliberate skip never looks like an oversight |
| D5 | `design-01` §7.4 | **Indulgence units' mechanism** — sketched as close to `skip_estimate` with a budget rather than an estimate, not designed |
| D6 | `design-04` §7.1 | **`allows_prep_session` or reuse `allows_long_cook`?** Recommended: the new key, defaulting to the old one |
| D7 | `design-05` §5 | **The fridge default moves 3 → 4**, which lengthens every batch. Must land *with* the rice exception, never before it |

---

### B7 — Location from the calendar — **new, and it was entirely unfiled**

Discussed at length on 2026-09-01 — including the security analysis — and
recorded in **no document at all** until `PROMPT-6`. Found by grepping the set
for "Google Calendar" and getting nothing back.

`base_schedule` is hand-maintained and **four things read it**, the most
demanding being `design-04`'s prep-day placement: a stale file silently plans a
two-hour cooking session for a day you are away.

**Answered:** a dedicated Google calendar and its *Secret address in iCal
format* — read-only, scoped to one calendar, revocable, and holding **no
credential at all**. That is the "throwaway target, low impact" property asked
for, reached by holding nothing rather than something weaker. Push is worse: an
Apps Script runs with the account's own authority and needs a public endpoint.

**The design decision:** the calendar produces **dated overrides** over the
standing week, never a replacement — the third instance of the
standing-versus-dated split, after `target_modes`/`target_locks` and
presets/blocks.

### B6 — Export recipes to Cronometer — **new**

Requested 2026-09-01. `PROMPT-5` is written and **starts with a five-minute
manual test**: publish one recipe as a gist with a `schema.org/Recipe` JSON-LD
block and import the raw URL into Cronometer. That answers whether the route
works before anything is built.

**The prompt originally recommended the wrong route and has been corrected.**
Cronometer's URL importer resolving ingredient *strings* is the feature, not a
risk — and this app is an unusually good source for it, because every quantity
is already in grams (no density guessing) and `shopping.display_name` already
strips prep qualifiers while keeping state. Documented format with a known
resolver beats the undocumented JSON round-trip format, which is now the
fallback.

## B8 — The hard-coding audit ⭐ **new, and it is what §1's rule actually creates**

Filed 2026-09-01 from `design-00` §1's governing rule. That section carries a
first-pass table: four planning behaviours are already claimed by a preset
dimension, and **at least four are not** — the morning-gym shake pin, the 4/3
cuisine block pattern, the weeknight elapsed-time ceiling, and the favourite
slot counts and reuse windows.

Two things make this the right next piece of design work rather than a chore:

- **It does not wait on anything.** No schema, no container, no decision in
  section D. It is a read of `main`.
- **The judgement is the deliverable, not the list.** Each row needs a verdict
  on whether it *should* be preset-able — the shake pin has a nutritional
  argument a mood should probably not override, where the cuisine block pattern
  is plainly a taste. A row ruled *code* is as useful an answer as a row ruled
  *data*, and per section E it has to be recorded rather than left implicit.

✅ **`PROMPT-7` written 2026-09-01.** It precedes the preset editor, because the
editor's field list is otherwise decided by whatever happened to be in
`design-01` §9.2's table.

✅ **DONE 2026-09-01, then re-run the same day with the burden of proof
reversed.** The audit is `design-01` §3.4a; `design-00` §1's table carries the
verdicts and §9.2's field list is derived from the `data` rows. Zero lines of
`src/` changed.

**The re-run is the part worth reading.** The first pass put the burden on
`data` — a row had to earn its way out of Python — and was corrected on the
requirement being restated: *"presets should be the predominant way to
customise meal planning; everything should be on the table."* The burden now
sits on `code` and `config`. Eight rows moved, `code` went from eleven to six,
and the reason the first pass erred is instructive: rows were ruled `config,
not preset` because no *mood* varies them, when the question is whether a
**week** does. A week with guests, a long weekend, a training block and a
comfort week are all weeks.

Five outcomes changed something downstream:

- **"Everything on the table" is a claim about the schema, not one screen.**
  §3.4a resolves it as tiered disclosure — every `data` row is a preset key
  with no exceptions, and §9.2 opens nine groups with ten behind a fold.
  Nineteen groups, and `design-03` §1's cost rule still holds because nearly
  every added row is `ui.number`.
- **`planning_rules` is one `CONFIG_FILES` key and presets replace keys
  whole**, and the reversal took the trapped rows from five to nine — over half
  the key. The recommendation hardened from "the editor writes the whole
  object" to **splitting the key**, filed as CHANGE-QUEUE item 7. **This is
  `PROMPT-8`'s work, not `PROMPT-9`'s surprise.**

  > **Superseded 2026-09-01 as a *blocker*, not as work.** `design-01` §3 now
  > specifies **typed leaf-path overrides** rather than whole-key replacement,
  > so `planning_rules.favorite_dinner_slots` is reachable without disturbing
  > `planning_rules.portion_trim_limits` and nothing is trapped. The split still
  > has value on its own merits — it separates preference from engine invariant,
  > which is a real distinction and the thing `PROMPT-7`'s audit was measuring —
  > so it stays in CHANGE-QUEUE.md as ordinary work. It is no longer a
  > prerequisite of `PROMPT-8`, and the whole-key premise this bullet argued
  > from is the one the review refuted.
- **Three sets of numbers are unreachable rather than hard-coded** — the dinner
  variety rules, the per-ingredient portion caps, and the long-cook threshold,
  which is **one number in four prose copies** including a Pydantic field
  description. Filed as item 8, and worth doing whether or not presets ship.
- **A good argument for a default is not an argument for a lock.** Five rows
  left the `code` list on exactly that point, `WORKOUT_BREAKFAST_STYLE`
  included: the shake's nutritional case is real and belongs in the editor's
  help text, not in the absence of a key.
- **One row settled by taking it off the table.** `inventory_rules.fridge_
  safe_days` is *"a per-dish measurement, not part of preset"* (decided
  2026-09-01) — `design-05`'s design, and it lands as a config reference table
  plus a measured `Recipe.storage_class` that passes F5's scale-invariance
  rule. The audit had floated a tightening-only `min()`; that is now dropped,
  since it fixes the too-short case never.

CHANGE-QUEUE.md items **6–9** carry the defect found, the two code changes the
audit's `data` rows need first, and the six `code` verdicts as one record.

## B5 — The week briefing ⭐ **new, and it is the program's missing output**

Raised 2026-09-01 and recorded in `design-04` §7.3, with no design of its own.

Presets, blocks, goals, week shape and prep placement all *decide* things, and
**none of them currently says anything.** A system built to remove decisions
has to explain what it decided, or the first surprising week is the last one
trusted — "no prep day this week" being exactly such a moment.

Two levels: a **week briefing** (what am I doing, and why) and **per-day notes**
(what is different about today). Every input already exists and by `design-03`
§2 it is a read view, so it is cheap — it just has not been designed.

## E. Process gap — none of this is in CHANGE-QUEUE.md

**Worth its own section because the project has a rule about exactly this.**
CHANGE-QUEUE.md says: *"The closed table is not a place a to-do can live — nor
is CLAUDE.md prose, nor a deferral note inside a shipped feature. Anything
decided against belongs in the ranked list on the day it is decided."*

This design set has decided against **six** things, and all six live only in
`dev/` "Deliberately not in this design" sections — which is precisely the
anti-pattern that rule names:

**Partly closed 2026-09-01.** The hard-coding audit graduated its own
decisions rather than leaving them here: CHANGE-QUEUE.md items 6, 7 and 8 hold
the defect it found, the constants it ruled reachable-but-not-presettable, and
the eleven it ruled `code` — the last as **one** entry rather than eleven, the
same call the front-end craft section makes. **That is the shape the six rows
below should follow**, and it answers the question this section left open: a
batch of decisions-against is one queue entry with a table in it, not a queue
entry each.

The six below are still only in `dev/`:

| Decided against | Where | Why |
|---|---|---|
| SIGA / UNC sub-classification of NOVA 4 | `design-01` §12 | Needs additive data no `Ingredient` carries; classifies products, not dishes |
| Date-based slot ids | `design-00` §8 | Four stated horizon needs do not buy them; three named triggers would |
| Direct grid manipulation | `design-03` §9 | An L on its own; the record list makes it unnecessary |
| Hevy webhooks | `PROMPT-4` | Needs a public endpoint, which makes `/api` auth required rather than optional |
| 16:8 / TRE | §C above | Not important |
| Presets inheriting from presets | `design-01` §12 | Makes the effective config a graph walk |

They should graduate to CHANGE-QUEUE.md — with their reasoning, since a closed
row's *verdict* cannot go stale but its *reasoning* can, and that has needed
repairing five times already.

**Prompts missing for designed work — the list that matters most**, because the
working assumption is that `dev/PROMPT-*` *is* the outstanding work: anything
without a prompt cannot be picked up.

Four were closed on 2026-09-01 — `PROMPT-7` (the audit), `PROMPT-8` (the preset
container and weekly pick), `PROMPT-9` (the editor and its validator) and, later
the same day, `PROMPT-10` (per-dish storage windows). Until the first three
**the entire preset mechanism had no prompt**, so Arm A — three design
documents — was unscheduled work. `presets.json` appeared in no prompt in the
set.

**`PROMPT-10` was the worse gap of the two, and it was found by an external
review rather than by this list.** `design-05` is a complete design for a
**live defect** — `fridge_safe_days: 3` permits a rice dish a day past its safe
window, and `apply_batch_selections` builds that shape on every week the
long-cook toggle runs — and it sat in the "still missing" table below,
outranked by three prompts that enable features. Under this section's own
working assumption (*anything without a prompt cannot be picked up*), the one
change in the program where being wrong makes somebody ill was the least
pickupable thing in it. It now runs **first**; see `dev/README.md`'s order of
delivery.

Still missing, in rough value order. Rows with implementation prompts remain
here because the code is still outstanding:

| Work | Designed in | Rank | Gated on |
|---|---|---|---|
| Freezer ledger — the consumer `extra_portions` never got | `design-04`, **`PROMPT-11`** | Tier 2 item 7 | **`PROMPT-10`** |
| `week_shape` records + applier + on-demand preview | `design-02`, `design-03` §8 step 6, **`PROMPT-12`** | Arm A's last step | **`PROMPT-10`**, **`PROMPT-11`** |
| Personal exercise constraints + gym-program catalog | `design-06`, **`PROMPT-14`** | Arm E first slice | preset container/editor; **not Hevy** |
| Constraint-aware workout plans + manual feedback/progression | `design-06`, **`PROMPT-15`** | Arm E first useful loop | **`PROMPT-14`**; Hevy optional |
| `Recipe.cuisine` + `total_time_minutes`, backfill 91 records | `design-00` F4 | Tier 2 item 6 | — |
| Fetch RHR / VO2max / Training Readiness | nowhere | Tier 1 item 4 | — |
| `readiness_log`'s first reader — the HRV band (C2) | nowhere | Tier 1 item 5 | — |
| The week briefing (B5) | **nowhere — no design either** | ⭐ unranked | — |
| Garmin AEE bias correction (C1) | nowhere | ⭐ highest in C | — |
| Blocks — `blocks.json`, dated overrides, successors | `design-01` §4–§6 | Arm A, after presets | ~~`PROMPT-2`, `-8`, `-9`~~ — **all three shipped; ungated** |
| Functional assessments (chair stand/TUG/grip/VO2 trends) | `design-06` §8; research only | Arm D outcome loop | separate design/prompt |
| Full fatigue matrix + automatic deload proposals | `periodization-engine.md`; `design-06` boundary | Arm E controller | C2, RHR, A2/e1RM |

The blocks row is deliberate: `PROMPT-8` builds presets only, and blocks are the
larger half. The two exercise rows are also deliberately split: configuration
must be independently useful and testable before an LLM writes a workout.

**The blocks row's three gates are now all satisfied** (`PROMPT-2` in v0.44.0,
`-8` in v0.43.0, `-9` in v0.44.0), which changes what is true of it rather
than merely what it is waiting for: it is no longer *blocked*, it is
*unwritten*. `PROMPT-13` is reserved and has no briefing, so the next action
on it is writing one — a distinction this table is exactly the wrong shape to
show, since a satisfied gate and an absent one both render as an empty cell.
That is why the gate is struck through here rather than cleared.

**The two `PROMPT-10` gates are the review's sequencing finding and are not
optional.** Both the freezer ledger and `week_shape` extend how far batched food
reaches; extending reach while one global `fridge_safe_days` is the only bound
makes the rice case worse, not better. `design-05` §5 already says the
lengthening and the dish-level exception must land in the same change — these
rows are that rule applied to the work *around* it.

**A seventh closed-against decision** joined the list: a declared total batch
size, and a declared count of eating events (`design-02` §4a) — both are the
"batch multiplier" this app refused once already, and `serves` carries the
information in a better form.
