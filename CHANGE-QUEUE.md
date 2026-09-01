# Change queue

**Every unfinished item and known defect, ranked in one list.** Each entry
carries its type, size, what blocks it, and where it was first recorded, plus
a [Verified closed](#verified-closed--do-not-re-file) table so a shipped item
is not re-filed as a new idea. This is the **only current** planning
document; the four it consolidated are history.

It replaced `ui-redesign.md` (work that waited on nothing) and
`future-ideas.md` (work that waited on a product decision or on runtime data)
on one point: that split answered "may I start this?" and never "what should
I start?", because neither ranked against the other and neither held the
defects recorded in CLAUDE.md at the moment they were found. Those two keep
the `-deprecated` suffix and remain the place an item's full *reasoning*
lives — entries here point at them rather than restating them. `ISSUES.md` is
the maintainer's own defect register from before phases 6a–6e: read it for
the original wording of a complaint, never for what is still open. All four
sources are now exhausted except `future-ideas.md`, which contributes the two
entries whose Source column says so.

**Verified against the code at `main`, v0.41.0** — not against any document's
account of itself, which is how several stale claims were caught.

## How to read this file

- **Cite entries by name, never by number.** The numbers renumber on every
  release that closes something — twelve times so far, twice in one release
  once, and once by an insertion that moved half the list — so "item 3" in a
  comment has a shelf life of one release. This file's own cross-references
  had gone stale by one in three places before the rule existed. **The anchor
  links in the table below still carry a number, and they are the one thing a
  renumber has to be checked against.**
- **Provenance is stated per entry**, because several were never filed
  anywhere: they were recorded in CLAUDE.md prose at the moment a phase
  decided not to fix them, which keeps the *why* and loses the *what next*.
  Six such deferrals were eventually found by reading the code against the
  document; all six have shipped.

## Rules this file has learned

Distilled from thirteen releases. Each was paid for at least once.

**On filing**

- **Verify a report against the code the day it is filed, not the day it is
  picked up.** Five proposals from one front-end review did not survive that
  check. And verifying a review's *premises* is not the same as verifying its
  *proposals* — one entry asserted the app "names itself nowhere on screen"
  while three lines of `ui.header()` said otherwise, which changed the answer
  and not merely the reasoning.
- **A claim can sometimes be verified from the mechanism rather than the
  symptom**, which is cheaper and stronger. The shopping drawer's sideways
  scroll was filed as "five minutes with devtools" and settled instead by two
  greps: Quasar defines `.flex{flex-wrap:wrap}` and Tailwind's `.flex-col`
  overrides only `flex-direction`.
- **A size written in prose and a size written in the table beside it are two
  claims, and only one of them gets read.** The prose was wrong twice; the
  Size column was right both times.
- **The closed table is not a place a to-do can live** — nor is CLAUDE.md
  prose, nor a deferral note inside a shipped feature. Anything decided
  against belongs in the ranked list on the day it is decided.
- **A closed row's *reasoning* can go stale even though its verdict cannot**,
  and nobody re-reads a closed row. A closure is a change of *fact* for
  whatever cited it, not just a link to repoint — the by-name rule stops a
  reference dangling and does nothing to stop one going false. Five repairs
  so far, four of them a rewrite rather than a repoint.

**On picking what to do next**

- **"Blocked by: one decision" is a reason to start with a question, not a
  reason to skip.** Five of the last seven releases cleared one that way,
  four of them in a single exchange before a line was built.
- **Re-read a blocking decision before asking it.** Its *options* age even
  when its question doesn't: one storage question proposed two files and was
  answered with a third shape it had not listed, because the codebase had
  grown that shape after the question was written.
- **A blocking decision that other ranked items build underneath outranks one
  that blocks only itself.** Answering "drawer or destination" first is what
  stopped two shopping items being built twice.
- **"Blocked by: data" is two questions.** What needs the data, and what only
  needs to *report* that the data is missing. The second is almost never
  blocked, and it is what stops the first having to be noticed by a human
  later — the Insights destination shipped its whole verdict layer against
  preconditions that were still unmet.
- **Three ways to pick, and they are not the same.** The *ranking* answers
  "what is most worth doing". A *theme* answers "what is cheapest together",
  and is only worth choosing on when the second item genuinely gets cheaper —
  not merely more convenient to review. A *bundle* is several things too small
  to rank hanging off one stated premise, which is how they get done at all.
- **A row's rank is a fact about the row, not about what is next to it.** A
  craft item does not ride along because something adjacent is being worked,
  and is not promoted because its neighbour closed.
- **A row filed as "different in kind" is worth re-reading for whether the
  difference is in the work or in a name doing two jobs.** The daily fibre
  target was deferred for having to "change what a macro budget is"; nothing
  about a budget changed, because `MACRO_KEYS` had simply been answering two
  questions at once.

**On where the work turns out to be**

- **Ask what each sync actually feeds, and check against the running code.**
  Three separate signals were fetched on every sync and thrown away, and each
  turned out to be the *enabling* half of a ranked item rather than a
  tidy-up. Conversely, before writing a new schema, check whether something
  already stored answers most of the question — that check halved this
  queue's largest remaining item.
- **Nothing in the eleven-release streak came from anybody using the app.**
  What broke it was the maintainer saying a surface felt forgotten, and an
  hour spent checking that report against two modules. It produced this
  file's first `Defect`, in shipped, working, weekly-used code — and the gap
  it exposed is worth more than the entries it produced.

## Size scale

| | Means |
|---|---|
| **XS** | One function, one file. No new schema, no new surface, no decision. |
| **S** | One module plus its test. Possibly a new config key. |
| **M** | Several modules, or a new UI surface, or a new stored field. |
| **L** | New storage schema **and** a UI surface, and at least one product decision first. |
| **XL** | Not scoped — needs its own design pass before it can be estimated. |

## The queue at a glance

| # | Item | Type | Size | Blocked by |
|---|---|---|---|---|
| 1 | [Morning readiness check-in](#1--morning-readiness-check-in) | Feature | M | one decision |
| 2 | [Write routes on the API](#2--write-routes-on-the-api) | Feature | M | — |
| 3 | [OpenAPI schema is off, so there are no generated types](#3--openapi-schema-is-off-so-there-are-no-generated-types) | Tech debt | S | — |
| 4 | [No auth on `/api`](#4--no-auth-on-api) | Feature | S | only if exposed |
| 5 | [Food waste tracking](#5--food-waste-tracking) | Feature | XL | not scoped |
| 6 | [`perishable_day_gap` is validated and read by nothing](#6--inventory_rulesperishable_day_gap-is-validated-and-read-by-nothing) | Defect | XS | — |
| 7 | [Split `planning_rules` into a preset-able group and an engine group](#7--split-planning_rules-into-a-preset-able-group-and-an-engine-group) | Tech debt | S | wanted with the preset container |
| 8 | [Three planning numbers are welded into prompt prose](#8--three-planning-numbers-are-welded-into-prompt-prose) | Tech debt | S | — |
| 9 | [Decided against: six planning constants stay compiled in](#9--decided-against-six-planning-constants-stay-compiled-in) | Record | — | — |

Eleven [front-end craft items](#front-end-craft-items--small-none-urgent)
remain below, each XS–S and none urgent. **The numbering above moved by three
on 2026-08-31 and by three again the same day**, when the shopping block was
inserted at the top and then closed by v0.41.0 — the shortest life any entry
here has had. Every anchor link carries a number; that is the one thing a
renumber has to be checked against.

**6–9 were appended on 2026-09-01 rather than ranked into place**, which is a
departure worth stating. All four come from `dev/`'s hard-coding audit
(`design-01` §3.4a): 6 is a defect that is invisible today because the two
values it straddles happen to agree, 7 wants doing *with* the preset container
rather than before it, 8 is worth doing whether or not presets ever ship, and
9 is a record rather than a task. None outranks 1–5, and appending them avoided
a fifth renumber this file would then have had to check every anchor against —
the cost the note above is about.

**8 is the one to take if the preset arm stalls.** One number — what counts as
a long cook — is written in four independent prose copies, one of them a
Pydantic field description; that is the `sorted(categories)` shape
`DEPARTMENT_ORDER` closed, and it is a defect in its own right.

---

## 1 — Morning readiness check-in

**Type:** Feature &nbsp;·&nbsp; **Size:** M &nbsp;·&nbsp; **Blocked by:** one
decision &nbsp;·&nbsp; **Source:** `ISSUES.md` item 10 · `future-ideas.md` 5d
(decision 2)

The half of 5d that v0.29.0 deliberately deferred, and the storage half of
that blocker is now gone: `readiness_log` holds sleep score, sleep hours, HRV
and a bucketed label per date, and Settings' Biometric Sync dialog reports
which nights have one. What is left is the decision, not the plumbing — the
question is what a check-in *does*.

**Two products, and the doc is firm that they should be settled in order:**

- **Read-only.** A readiness figure surfaced on the Today tab's context strip
  or the phase 6e workout dialog. Small, obviously correct, no new coupling.
- **Adjusting.** Softening a training uplift on a low-readiness morning.
  Touches `apply_training_adjustments`, which today reads nothing but
  `estimated_burn_kcal`. Materially bigger, and it is the exact conflation
  CLAUDE.md's "sleep and HRV never reach an energy equation" line exists to
  prevent — a sleep score is a unitless 0–100 index, so no conversion to kcal
  can be legitimate.

If the adjusting version is ever wanted, note that it can be legitimate
without becoming an energy conversion: scaling a *planned* session's expected
burn on a bad night is a statement about whether the session will happen as
scheduled, not a claim that sleep costs calories. That framing is the one to
argue about; the doc does not settle it.

---

## 2 — Write routes on the API

**Type:** Feature &nbsp;·&nbsp; **Size:** M &nbsp;·&nbsp; **Blocked by:**
nothing &nbsp;·&nbsp; **Source:** `ui-redesign.md` phase 5, deliberately out
of scope

**This was "write *and generation* routes" and an L blocked on a design pass
until v0.41.0**, which shipped the generation half — `generation_jobs.py`,
`POST /api/weeks/{id}/generate` answering `202` with a job id, `GET
/api/jobs[/{id}]`, and the single-flight claim a browser tab and an API
client now share. What remains is the smaller and duller half, and it is
unblocked: the design question was entirely about generation's 30s–3min
runtime and its progress callbacks, and it was answered by *counting the
events* rather than by weighing poll-versus-SSE-versus-WebSocket — a dozen
events across a quarter of an hour is not a streaming problem, and the other
two designs both need the job registry underneath them anyway.

`src/api.py`'s five `GET` routes are still read-only by design, each calling
one existing repository method or pure planner function, because "a route
that computed something would be a route free to disagree with the UI."
Generation is the deliberate exception: it is the one thing the CLI has
always done from outside a browser.

What is left is saving a grid edit, a mark, a favourite or a config key. Each
has an owning surface already, and most are session concepts with no business
being an HTTP resource — so the real work is deciding which of them are
genuinely standing state (the two that write to `config/` are the model to
follow) rather than writing routes. Nothing needs it today. It stays filed so
that "everything but generation is read-only" is a recorded decision with a
known cost rather than an assumption.

---

## 3 — OpenAPI schema is off, so there are no generated types

**Type:** Tech debt &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 5

`nicegui`'s `App.__init__` hardcodes `docs_url=None, redoc_url=None,
openapi_url=None` regardless of what is passed to `ui.run()`, so
`/api/docs` and `/api/openapi.json` do not exist.

Only worth doing if a real front end is ever built against `/api` — but then
it is worth doing *first*, because the alternative is a hand-maintained
second copy of `Recipe`, which is the duplication this codebase reliably
regrets — `/api/recipes`'s own filter is the most recent example, and it took
until v0.31.0 to fold back into one function (see "Verified closed"), having
drifted silently in the meantime. Re-enabling it is a small separate task
against the NiceGUI app object.

---

## 4 — No auth on `/api`

**Type:** Feature &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 5

The app is localhost-only, so there is nothing to do today. Filed because
the *shape* of the answer was decided and is worth not re-deriving: a
dependency on the router (`APIRouter(dependencies=[Depends(...)])`), which
*gates access* rather than *scoping data*. Nothing in this app's storage is
per-user, so auth here is a lock on the door, not a multi-tenancy
foundation — and it must not be mistaken for one.

Do this the same day the server is first bound to anything but localhost, and
not before.

---

## 5 — Food waste tracking

**Type:** Feature &nbsp;·&nbsp; **Size:** XL (not scoped) &nbsp;·&nbsp;
**Source:** `future-ideas.md` 5c, "Not scoped at all yet"

Flagged in the original architecture review as having no data source
whatsoever. It would need a new logging entry point of its own — a separate
product decision from the adherence marking v0.35.0 shipped and the trend
charts v0.36.0 shipped, and the only item in this queue with no proposed
schema, no proposed surface and no proposed interaction.

**Neither of those closures shortens it**, which is worth saying because all
three look adjacent. A `skipped` mark says a planned meal was not eaten and
says nothing whatever about whether its ingredients were thrown away. The
Insights destination is now the surface a waste readout would live on and
`ui_state`'s `InsightPanel` is the shape it would take — so the *reporting*
half has a home it did not have before, which is genuinely new — but every
readout there is drawn from a signal something already records, and this one
has no signal at all. The entry point is still unbuilt, and that is the
whole item.

Kept here so it is not rediscovered as a new idea. It needs a design pass
before it can be estimated at all.

---

## 6 — `inventory_rules.perishable_day_gap` is validated and read by nothing

**Type:** Defect &nbsp;·&nbsp; **Size:** XS &nbsp;·&nbsp; **Blocked by:** —
&nbsp;·&nbsp; **Source:** `dev/design-01` §3.4a, the hard-coding audit,
2026-09-01

`perishable_day_gap` is declared on `InventoryRules`, merged from
`config/week.json` and validated by `AppConfig`. Nothing reads it.
`shopping.ShoppingItem.buy_late` imports `week.PERISHABLE_DAY_GAP` — the
module constant — because it is a computed property with no config in scope
at evaluation time, and `week.py` carries a comment saying to keep the two in
sync by hand.

**It is invisible today because they agree at 3**, which is what makes it
worth filing rather than leaving: editing the config key produces no change at
all, silently, and the next person to edit it will be someone who read
`week.json` and reasonably believed it was the value in force. Its sibling
`fridge_safe_days` is read through config properly, so the file gives every
appearance of being live.

The fix is the one the comment already names: thread a config through
`aggregate_cook_events` to `ShoppingItem` construction, or resolve `buy_late`
at the call site where a config is in hand. Either ends the hand-sync.

**Related, and deliberately not merged into this:** the constant's *own*
default in `week.DEFAULT_INVENTORY_RULES` still says `fridge_safe_days: 4`
where the shipped `week.json` says 3. That one is correct as written — it is
the documented "what the app did before that section existed" fallback, per
the same convention `history_styles()` follows — but the two numbers sitting
four lines apart is why this defect survived a read.

This is the declared-but-unread shape this codebase has now closed three times
(`readiness_log`, `activity_log`, `smooth_series`), reached from the config
side rather than the sync side.

---

## 7 — Split `planning_rules` into a preset-able group and an engine group

**Type:** Tech debt &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Blocked by:**
wanted *with* the preset container, not before it &nbsp;·&nbsp; **Source:**
`dev/design-01` §3.4a, the hard-coding audit, 2026-09-01

`planning_rules` is **one** `CONFIG_FILES` key, and `dev/design-01` §3's
preset rule is whole-key replacement, never a deep merge. Nine of its fifteen
sub-keys were ruled preset-able by the audit, so a preset wanting a different
`cuisine_block_pattern` must restate all fifteen — including
`portion_trim_limits`, which CLAUDE.md says to leave alone and swap models
instead.

At five preset-able rows (the audit's first pass) the editor could plausibly
read-modify-write the whole object and nobody would notice. At nine — over
half the key — a preset file becomes unreadable in exactly the way whole-key
replacement exists to prevent: you cannot tell which value was chosen and
which was carried along.

**Two keys in `engine.json`**, split on the audit's own verdicts: the nine
`data` rows in one, and `portion_trim_limits`, `portion_trim_deadband`,
`max_meal_share_multiple` and `history_max_entries` in the other. Those four
govern how much bad model output is accepted and how much history is kept —
not what the week is — and `history_max_entries` is additionally the bound
`favorite_reuse_days`' validator checks against, so a preset moving it would
give that validator a moving target.

A migration plus a `test_config_layout.py` snapshot regeneration, which is
what that test exists to make safe. **Do it with `PROMPT-8`**, which already
moves config validation to after the preset layer; discovering it during
`PROMPT-9` is the failure this entry exists to prevent.

**Four constants also need a key before the split is worth much** —
`WEEKEND_DAYS`, `MORNING_TRAINING_CUTOFF`, `WORKOUT_BREAKFAST_TYPES`,
`MEAL_TIME_OF_DAY` — all ruled `data` and none reachable today. `WEEKEND_DAYS`
should derive its default from `base_schedule` the way `day_allows_long_cook`
already does, with the preset overriding the derived answer; that is the same
two-layer arrangement `dev/design-04` §7 uses for prep day, so it should land
with that work rather than here.

---

## 8 — Three planning numbers are welded into prompt prose

**Type:** Tech debt &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Blocked by:** —
&nbsp;·&nbsp; **Source:** `dev/design-01` §3.4a, the hard-coding audit,
2026-09-01

Three sets of numbers that the audit ruled preset-able are **English inside
module constants**, so they are unreachable rather than merely hard-coded. Each
needs a builder before it can be a config key at all, and
`build_batch_roast_rule(config, days)` is the precedent for all three — the
same conversion, made for the neighbouring reason that a model rejected for
breaking a rule it was never given burns a 30 s–3 min retry.

| What | Where | Today |
|---|---|---|
| the dinner protein repeat cap, and the consecutive-night rule | `DINNER_VARIETY_RULE` | "more than two dinners", "never … two consecutive nights" |
| per-ingredient portion caps | `PORTION_DENSITY_GUARD` | nine of them — 2–3 eggs, 150 g yoghurt, 2 slices of bread, 45 g powder, ~200 g cooked meat |
| **what counts as a long cook** | **four separate copies** | "60+ minutes" |

**The long-cook threshold is the one worth doing first.** It appears in
`build_batch_roast_rule`, `BATCH_ROAST_ANCHOR_RULE`, `LONG_OVEN_COOK_RULE`
and `Recipe.long_oven_cook`'s own field description — four independent
statements of one number, one of which is a Pydantic description the model
reads as schema. That is the `sorted(shopping_list.categories)` shape
`DEPARTMENT_ORDER` closed at seven call sites: a decision made four times and
agreed only by accident. **One key, four readers**, and it is worth doing
whether or not presets ever ship.

It was found by sweeping the prompt constants for embedded numbers, which the
audit's first pass did not do — it ruled the whole class `code` on the
correct principle that prompt *wording* is how a want is achieved, without
checking whether any of that wording contained a want.

---

## 9 — Decided against: six planning constants stay compiled in

**Type:** Record &nbsp;·&nbsp; **Size:** — &nbsp;·&nbsp; **Source:**
`dev/design-01` §3.4a, the hard-coding audit, 2026-09-01

**Filed under this file's own rule** — *"the closed table is not a place a
to-do can live, nor is CLAUDE.md prose, nor a deferral note inside a shipped
feature; anything decided against belongs in the ranked list on the day it is
decided"* — which `dev/OUTSTANDING.md` §E raised against the whole `dev/`
design set. Recorded as one entry rather than six, the same call the front-end
craft section makes.

The audit was **re-run with the burden of proof on `code`** after the
requirement was restated as *"presets should be the predominant way to
customise meal planning"*. That cut this list from eleven rows to six, and
each survivor now fails a stated test rather than being defended by taste.

| Constant | Fails which test |
|---|---|
| `MEAL_TYPE_PRIORITY` | **a preset setting it produces a week the app rejects.** Dinner precedes lunch so the one cross-type leftover `week.leftover_meal_type_error` permits always has its source generated; reordering fails `validate_week` |
| `MACRO_KEYS` | **arithmetic, not preference** — the energy identity every budget is checked against |
| `PREP_DAY_INDEX` | **arithmetic** — "the day before day 0". `dev/design-04` §7 makes prep-day *placement* movable by deriving it; that changes where the walk lands, not what this means |
| `SEAFOOD_TERMS` | **measurement, not preference** — *how* a dish is classified. The **cap** is already `sourcing.max_seafood_meals_per_week` and is preset-able |
| `UNDER_TARGET_NOTE_THRESHOLD` | **customises nothing about the plan** — it decides when a warning is emitted about a week already generated |
| prompt-rule **wording** (`SHAKE_ROTATION_RULE`, `PANTRY_CONSOLIDATION_RULE`, `FIBER_TARGET_RULE`, `ELAPSED_TIME_RULE`, `LOCATION_RESTRICTION_PHRASES`, the `WEEK_*`/`DAY_*` rules, both anchor directives) | **how a want is achieved, not the want.** The *numbers* inside three sibling constants are ruled preset-able — see item 8. That is the line |

**Five rows moved out of this list on the re-run**, and the reasoning is worth
keeping because it is the same mistake twice: `WORKOUT_BREAKFAST_STYLE`,
`TRAINING_INTENSITY_SPLIT`, `TRAINING_PRE_WORKOUT_DIGESTION_MINUTES`,
`NUDGE_FOOD_SAMPLE_SIZE` and `SUNDAY_PREP_REHEAT_MINUTES` were each defended
by a good argument for their **default** — a shake is the only breakfast
drinkable ten minutes before a session; more nudge foods dilutes rather than
strengthens — and a good argument for a default is not an argument for a lock.
Each is now a preset key whose absent-meaning is today's value, with the
reasoning moved to the editor's help text where it can be read.

**Three rows stay in `config/` rather than becoming preset keys, and none of
them fails the program rule** — all three are already reachable without
editing Python. `portion_trim_limits`/`portion_trim_deadband`/
`max_meal_share_multiple` govern how much bad model output is accepted rather
than what the week is; and `history_max_entries` is a storage bound and the bound
another validator checks against.

**`inventory_rules.fridge_safe_days` was the third and is now neither** —
settled 2026-09-01 as *"a per-dish measurement, not part of preset"*, which is
`dev/design-05`'s design, and **shipped that same day** (see "Verified
closed"). It split into `inventory_rules.storage_windows` (a food-safety
reference table in `week.json`) and a measured `Recipe.storage_class` reported
by the model the way `long_oven_cook` already is. The argument is not that a
mood must not vary food safety — it is that **one global number is wrong in
both directions at once**: a beef stew keeps 4 days, a rice tray bake keeps 2
for a *Bacillus cereus* reason, and the app said 3 to both. A preset could
only ever have chosen a different wrong global.

---

## Front-end craft items — small, none urgent

Raised by the 2026-08-30 front-end review and verified against the code, but
each individually too small to rank against the list above. All are XS–S.
Listing them here rather than filing eleven entries is the point: they would
drown the ranking this file exists to provide.

**They fold into nothing, and that changes how to take them.** This sentence
used to name whichever larger item was already touching the same files; every
such item has now closed, and repointing it at the next-nearest entry would
be a citation kept alive past the fact it was asserting. **Take them as a
batch on a theme instead**: the four motion rows are one afternoon and one
decision about duration, and the three token/vocabulary rows are one sweep.
That is the "a theme is worth choosing on when the second item gets cheaper"
rule, applied where it actually holds.

**No row here has ever moved because something adjacent shipped**, which is
the rule being tested rather than a coincidence — three larger items have now
touched these files in passing without any row riding along. *The shopping
drawer has no loading state* had a parent for exactly one day and did not
move in either direction.

**The `TEXT_MICRO` row is the one filed from *inside* a larger item**, with an
explicit instruction not to let it ride along — the only time this file has
done that. The instruction held: the typography and contrast work shipped and
the size did not move.

| Item | Type | Size | Detail |
|---|---|---|---|
| `TEXT_MICRO` 10px → 11px | Tech debt | XS | **One line, and the cost is entirely verification — do it alone.** `RAIL_WIDTH_PX` is pinned at 168, `WEEK_GRID_HEADER_INSET_STYLE` derives the header grid's position from it, day columns floor around 110px, and `ui_cards.meal_card` carries a comment saying its status badge row is "the one row on the card with no width to spare". A 10% bump on the most-used size in a nine-column layout can reflow all of that. Measure at 1280px and 1440px, and mirror the new value into the `ui-work` skill's type table — that table is the canonical statement of the scale, so a change made only in `ui_theme.py` leaves the contract lying. Filed inside the typography item v0.37.0 closed, and deliberately not shipped with it |
| No line-height tokens | Tech debt | S | The type scale carries four sizes and no matching leading, so multi-line prose in Insights and Settings sets at the same tightness as a one-line card figure. A `leading-*` per scale step, added to the skill's type table alongside the sizes. |
| Toasts are the one off-brand component | Tech debt | S | `ui.notify` renders stock Quasar — its own radius, type and colour — and is used at ~15 call sites in `ui_generation.py` alone. Restyle globally from `ui_app.py`'s `add_css`, not per call. |
| The phase-2 token sweep never finished | Tech debt | S | A literal `p-6` in `ui_cards.py`'s recipe dialog where `SPACE_PAGE` is the token, ~40 `mt-*`/`mb-*` margins across nine files, and stray `rounded-md`/`rounded-xl`. All three are named in the skill as deliberate phase-1 leftovers for phase 2, which restructured containers and did not come back for them. |
| No entrance motion on the grid | Feature | XS | 28 cards appear at once on load. A per-index `transition-delay` staggered over ~200ms is pure CSS emitted once from the page function — note `ui.add_css` from inside a `@ui.refreshable` stacks a copy into the head on every repaint, so it must not be emitted from `canvas`. |
| Panel refreshes blink rather than cross-fade | Feature | S | A `duration-150` opacity transition on refreshable containers. Cheapest of the motion items and the most visible, since a refresh fires on nearly every edit. |
| The recipe dialog has no entrance | Feature | XS | The most-opened modal in the app appears instantly. A scale-0.98→1 over `duration-200`. |
| The shopping drawer has no loading state | Feature | S | It blanks during a plan load rather than showing a skeleton. Smallest payoff of the five motion items — the load is usually fast enough not to be seen. |
| Settings reads like a debug panel | Feature | S | Internal vocabulary on a user-facing surface: "sync checkpoints", `readiness_log`, raw OpenRouter model ids. Labels only — the three integration dialogs stay read-only, per phase 6e's "the row that owns a piece of state keeps owning it". |
| Tooltip placement and delay vary | Tech debt | XS | ~29 tooltip sites, several already `max-w-xs` and several not. A consistency pass, no behaviour change. |
| Nothing on the page reads as its title | Feature | XS | The scale tops out at `TEXT_DISPLAY` (18px), used for dialog titles. Promote the week date pill in `ui_telemetry.week_banner` to that weight so *it* anchors the page — **not** a fifth size, which the skill names as the thing to resist. |

---

## Verified closed — do not re-file

Checked against the running code. `ISSUES.md` predates phases 6a–6e and reads
as open; it isn't — only its item 10 (the morning readiness check-in) is
still live. `ui-redesign.md` and the front-end review have nothing left at
all, filed or unfiled.

| Source | Item | Closed by |
|---|---|---|
| `dev/design-05`, and the one row `PROMPT-7`'s hard-coding audit took off the table | One global fridge window, wrong in both directions at once | Shipped in **v0.42.0**, and it is the only item in this file where being wrong made somebody ill rather than producing a worse plan. `inventory_rules.fridge_safe_days: 3` was read in **six** places (the design said five; `generate_sunday_prep_session`'s prompt was the sixth, and it interpolated the wrong global straight at the model). It threw away a day of good stew and — the direction that matters — let a rice tray bake batched on prep day be eaten a day past the 48-hour *Bacillus cereus* window, a shape `apply_batch_selections` built on **every** week the long-cook toggle ran. Replaced by `inventory_rules.storage_windows` plus `Recipe.storage_class`, with the grid planned against the default window, the span named in the prompt (`build_storage_rule`) and the answer judged against it (`reject_short_storage_class`, over the same two-axis split as `reject_misplaced_long_cook`). **Every default fails short**, inverting this codebase's usual convention, on the `is_sunday_prepped` precedent: a dropped self-report must cost a shorter batch, never a longer one. Two behaviour changes landed together and are pinned together in `tests/test_food_safety.py` — the default lengthening (a prep batch may now reach Thursday) and rice tightening to two day-gaps — because a permissive change riding on a safety one must be asserted rather than discovered. Two things the design missed and the build found: the sixth consumer above, and `select_favorite_assignments` as a **third route** to a long span that neither the prompt nor the response validator can see, since a favourite is never generated (`favorite_keeps_long_enough`, the sibling of `favorite_fits_day`). |
| maintainer report, 2026-08-31 | The shopping list had been left behind — six parts, one premise | Shipped in **v0.41.0**. (a) three `flex flex-col` containers in `ui_shopping.py` had no `flex-nowrap`, so the drawer's `overflow-y-auto` never fired and a week's list started a second column off the 420px edge. **The one claim this entry filed as inferred is now measured**, and not with devtools: Quasar ships `.flex{display:flex;flex-wrap:wrap}` and Tailwind's `.flex-col` sets only `flex-direction`, so the wrap is a fact about the two stylesheets rather than a story about a symptom. (b) `DEPARTMENT_ORDER`, one constant behind the seven `sorted(categories)` call sites. (c) a department band with a count in the drawer, `── DAIRY & EGGS ──` in the Keep copy. (d) `═══ trip ═══` heading that copy. (e) the rail badge reads `PlannerState.shopping_item_count()` — measured on the live week the day it was fixed, the badge said **86** over a drawer showing **120**. (f) ticks moved from the DOM to `PlannerState.shopping_ticks`; still never persisted, and no longer wiped mid-shop by a repaint. **A seventh part was found by generalising (a)** — `ui_cards.py`'s swap-with-favourite list is the same wrapping column over a 36-entry catalog — which is the argument for fixing a class of bug rather than an instance. |
| the v0.37.0 pantry-ledger row in this very table | The shopping list asked you to buy what was already in the house | Shipped in **v0.41.0** — `shopping.apply_pantry`, taking the second of the three shapes this entry proposed: subtract at render time from `inventory_to_clear` itself, never from a stored count. Every one of v0.37.0's arguments for a run-scoped ledger survives intact, because the two ask different questions of one hand-edited list. A quantified entry is subtracted, an unquantified one only annotated (the two entry shapes are two statements, and collapsing them would invent a quantity or discard one); a line the pantry covers outright is lifted onto `ShoppingList.pantry_covered` and *named*, because a stale pantry is the failure mode and a line that vanished with no trace is the one you could not notice was wrong. CLAUDE.md's standing claim that this "needs the ledger to survive the run" was the thing that turned out to be false. |
| maintainer question, 2026-08-31 | Should shopping be its own destination? | Answered **both**, shipped in **v0.41.0**. The drawer's documented reason for existing is that a list is read *against* the grid, and that is a different job from working through a trip — 420px is right for the first and wrong for the second. `build_shopping(ctx)` now returns `build_panel`, which a sixth rail destination calls at its own render position; `ShoppingPanels` is one registered section fanning out to both instances, so they cannot drift. **The sequencing worry this entry was ranked on turned out to be real and cheap**: the defect fix and the pantry subtraction both did build inside the drawer, and none of it was built twice, because sharing one builder was a refactor of about forty lines rather than a second surface. |
| `ui-redesign.md` phase 5, "the reason phase 5 stopped" | Generation had no HTTP shape, so the API could not start a run | Shipped in **v0.41.0** — `generation_jobs.py`, a `202`-with-a-job-id `POST`, `/api/jobs[/{id}]`, and one single-flight claim shared by the route and the page. **The design question was answered by counting the events, not by weighing the three protocols**: `progress_callback` fires once per meal type, so a dozen events across a quarter of an hour is not a streaming problem — and SSE and a WebSocket both need the job registry underneath them anyway, which makes polling the substrate rather than a third peer. What is left of that entry is the write routes, now an unblocked M. |
| `ISSUES.md` 1 | Header space, repeated day names, header/canvas misalignment | Phase 6a (alignment + one day identity), 6b (stat block → `week_banner`) |
| `ISSUES.md` 2 | All controls from the left panel | Phase 6b — the rail's action block |
| `ISSUES.md` 3 | Dates on day names | Phase 6a — `format_day_label(day, day_date_iso, short=True)` |
| `ISSUES.md` 4 | No swap/regenerate for batch cooking | Phase 6c — `prep_candidate_card`'s icon row |
| `ui-redesign.md` phase 1, recorded rather than resolved | Amber carried five documented meanings (eight in fact) and violet two | Shipped in **v0.32.0** — role separation: training, fridge/freezer, the favourite star, buy-late and the prep note all gave up their hue to a glyph already doing the work; carbs → orange, fibre → cyan, and the telemetry marker's emerald training case folded into amber. No hue in `ui_theme.py` now carries more than two meanings; the `ui-work` skill's collisions section is rewritten as "The palette". |
| not previously filed — raised 2026-08-28 | Cronometer logged no fibre, so the fibre readout had no measured half | Shipped in **v0.32.0** — `CRONOMETER_MACRO_COLUMNS` gains `fiber_g` (`Fiber (g)`/`Fiber`), keyed the repository's way rather than the CSV's. Capture and readout landed together, per this entry's own "file both or neither": capture alone reproduces the shape v0.29.0 closed for Garmin sleep. `ui_state.fibre_view(planned, logged)` is the pure view model holding the rule and both formatted halves, `PlannerState.fibre_for(day)` matches a `daily_actuals` row by the day's **calendar date** — not by weekday, which is why this is not `planner.logged_intake_for` — and `ui_telemetry.py` prints the logged figure as a second slate label beside the cyan planned one. **Side by side, never over a divider**: `32/24` in a row where every other entry is `actual/target` reads as a goal that was missed, and there is no fibre goal. `MACRO_KEYS` untouched, no budget change anywhere, `_prune`/`has_measurements` unchanged so an absent column is omitted rather than zeroed and every row synced before this reads as "no log". The appendix's **No daily fibre target** is deliberately still open and is a different, larger change |
| `ui-redesign.md` phase 4 aside, never filed until this queue filed it | The training schedule was hand-declared while Garmin recorded what actually happened | Shipped in **v0.33.0** — `activity_log` is a fourth `biometrics.json` section (`GarminSyncService.fetch_activities` → `PlanRepository.save_activity_entries`), and `nutrition_engine.propose_training_schedule` diffs four weeks of it against the declared week. **The detector was the easy half; the confirmation is the feature**: nothing writes without a click, and the precedent copied is `estimate_session_burn_kcal`'s calculator button — a derived default, into the same field, applied explicitly. **The blocking decision — a declared session Garmin never sees — is answered symmetrically**: proposed for removal, never removed, behind two guards (the weekday must have come round at least twice inside the *observed* span, and `MIN_ACTIVE_DAYS_FOR_DROP` asks whether the watch is worn at all before its silence is evidence); a weekday that recorded *something* is never dropped, since a Sunday ride that became a Sunday walk is a day you plainly train on. **What counts as observed was the real difficulty** — `activity_log` holds only days that recorded something, the same ambiguity `sync_checkpoints` closed for weigh-ins, so the span runs from the first recorded activity to the later of the last one and Garmin's checkpoint, capped at today, and under-claiming is the safe direction. Storage is replace-per-date, not upsert: this is the one section with several rows per day. Only mapped, timed rows are stored — `GARMIN_SESSION_TYPES` has no catch-all and `startTimeLocal` is read rather than `startTimeGMT` — because a proposal is a sentence the user is asked to agree to. `net_calories` finally has a reader: it is the proposed burn, with the MET formula only as fallback. Accepting persists `training_schedule` through `save_config_keys`, making that the second UI control that writes to `config/` on `set_target_mode`'s reasoning, and applies the change to the file's list and the staged list separately so an accept never writes out someone else's half-typed session. Additions diff against the staged schedule, drops against the file's. `ui_state.training_proposals_view` carries the wording over three no-proposal states, on the `adaptive_tdee_view` precedent — "your week already matches" is the good answer and the one most likely to be misread as broken |
| Appendix — deferral recorded in CLAUDE.md's "Batch cooking on purpose", never filed | The bulk-prep **lunch** anchor kept its from-scratch prep time | Shipped in **v0.39.0**, at the XS it was costed at: one line in `ui_state.slot_views` and one fixture. `event.meal_type == "dinner"` was a faithful proxy for "cooked on prep day" only while the long cook was the sole anchor, and `apply_batch_selections` has anchored bulk prep on **lunch** since v0.31.0 — so a card showed its full 45-minute cook for a dish that came out of the pan the day before the week started, while the fridge badge two lines above it, counting from `cook_day_index`, correctly said otherwise. **Two surfaces on one card disagreeing about one batch**, which is the exact failure the badge and the storage note were reconciled to avoid. The fix is `planner.is_prepped_ahead`, the function that already *names* the rule and that `days_since_cook` was already reading, rather than a wider `in ("lunch", "dinner")` test: the shake still has to be excluded (only *portioned* ahead, blended fresh each training morning), `is_prepped_ahead` is already precisely that distinction, and a third batch axis cannot reopen it a third time. The deferral's own stated reason for leaving it — "how long does it take" is a different question from "how old is it" — was right about the questions and wrong about the answer: both turn on whether the pan was on before the week started, which is why one call now serves both |
| Appendix — deferral recorded in CLAUDE.md's "Diet styles", never filed | Fast 800's calorie ceiling was food-selection guidance and never a number | Shipped in **v0.39.0** as `DietStyle.calorie_ceiling` (optional, None on eleven of twelve; `fast_800` declares 800), read by `planner.diet_style_calorie_ceiling` and applied inside `hydrate_dynamic_targets` — **exactly where this deferral said it would have to live**, which is most of why it stayed an S. **A ceiling is admissible where an adjustment was not, and the difference is idempotence**: hydration runs twice on one config (the UI previews, generation re-hydrates), so anything that *shifts* a number shifts it twice — the failure an earlier uplift-unwinding pass produced when it took a 2200 kcal override to 1850 — while `min()` on an already-capped day returns the same figure. Four decisions: applied **after** the training uplift, because a ceiling bounds what the day may total and a workout does not buy an exemption from a bound its owner chose; **never** over a `target_is_stated` macro, which would be the second source of truth this section refuses and would make flipping calories to manual silently move the day; **lowest wins** when two active styles declare one, since two bounds are two bounds and averaging invents a third nobody asked for (`reconcile_adaptive_tdee`'s reasoning); and an unaffordable ceiling is **reported, not corrected** — locked protein (144 g) plus the day's carbs exceed 800 on the shipped numbers, so `derive_fat_g` floors at 0 and hydration warns naming the days, the same answer an overspent `meal_overrides` and `cap_to_weighted_share` already give. **The prompt still never states the number**: telling the model a figure its budget already reflects is how it starts optimising for the number instead of the food. `active_diet_styles` is empty by default, so every day plans byte-identically |
| Appendix — deferral recorded in the `ui-work` skill and CLAUDE.md, never filed | The Daily View day picker could not cross weeks | Shipped in **v0.39.0**, and **both of the objections that kept it clamped for four releases are answered rather than dodged**. There is no "second control free to disagree with the header's week selector" because the chevron *drives* that selector: `switch_week` stays the only writer of `week_selection`, the header select is now `@ui.refreshable` and registered on `"plan"`, and `go()` widens its own refresh from `"today"` to `"plan"` exactly when the week changed. The async load is `PlannerState.scan_cached_weeks`, one extra small read at page load, recording which weeks have a plan and which have a column for today. **That read is what buys an honest disabled state** — the clamped version's own standard — and the alternative is worse than untidy: spilling into an uncached week *strands* the reader, since `viewed_day()` is None with no plan and there is no picker left to step back with. `browsable_timeline` is the concatenation of each cached week's columns and `step_target(delta)` is one index step along it, which can be that simple only because `days` comes from config rather than from the plan, so both weeks are the same seven weekdays in the same rotation. Three details: a chevron asks `step_target` rather than comparing the day's index, so the rule that decides and the rule that acts cannot disagree; an edge step names the week it crosses into, because crossing changes the whole page and drops unsaved grid edits and must not be the one gesture that does that silently; and `_known_weeks` reads an empty scan as "not asked yet" rather than "nothing exists", so an unscanned state behaves exactly as this tab did before it could cross. The "Today" reset crosses back too, keyed on disk rather than on the loaded plan — step into next week and the plan on screen has no today at all, which is precisely when a way back is most wanted. Verified in a browser against the two real cached weeks: Sun 30 Aug → Next Week/Mon and back, the select following both ways, no console or server errors |
| `future-ideas.md`, "Rejection-list decay" — this queue's item 1 until v0.34.0 | `build_rejection_rule` sent every recorded rejection to every generation call, forever | Shipped in **v0.34.0** — and the answer to all three of the questions this entry left open is that there were **two signals in one rule**, wanting two windows. The **dish list** is a veto on one recipe and expires per reason (`planning_rules.rejection_decay_days`): `had_it_recently` 21 days because it is self-resolving — the dish stops having been had recently whether or not anything honours the entry — `too_much_prep` 60, `dont_fancy_it` 90, `wrong_for_slot` 180 because it is structural and a curry is never breakfast. **Per reason rather than one N** answers question 3 rather than deferring it, on the precedent `favorite_reuse_days` already set for its own split. The **recurring-reason tally** counts over the longer `rejection_reason_window_days` (180), so a standing preference outlives the dishes that evidenced it — which is what makes question 2's hard-cutoff/soft-discount choice moot rather than merely decided: a hard cutoff on the half that should expire, no cutoff at all on the half that shouldn't. **The tally moved into Python**, a consequence of the split rather than a flourish: once the halves have different windows the model only ever sees the shorter one, so asking it to notice a repeated reason had it weighing a subset while being told to weigh the whole. `REJECTION_REASON_GUIDANCE` names what a run of each answer implies, split from `REJECTION_REASON_LABELS` the way that dict was already split between UI and prompt; `REJECTION_REASON_SIGNAL_MIN` (3) is what counts as a run. **No storage change and nothing to migrate**, exactly as this entry predicted — every entry already carried its `date` — and done at the moment it recommended: `data/rejections.json` still did not exist, so this landed before the file got large rather than before it existed. Two fixes carried along: `build_rejection_rule` takes `today`, the `select_favorite_assignments` seam, because the existing tests held fixed date literals against a live clock and would have begun failing about six weeks out — the failure CLAUDE.md's "Tests" section already records catching once; and `planning_rule` extends its documented fallback to a config with no `planning_rules` section at all, which `AppConfig` already treats as legal |
| `future-ideas.md` 5b — this queue's item 2 until v0.35.0 | Nothing observed whether a planned meal was eaten, skipped or swapped | Shipped in **v0.35.0**, with both of this entry's decisions answered rather than deferred. **Storage** is `data/adherence.json`: two lists in one file — `meals` (`planner.AdherenceEntry`, this entry's own field list) and `workouts` (`planner.WorkoutCompletion`) — keyed by `date` plus a second field named per section in `ADHERENCE_SECTIONS`, so one `_upsert_adherence` serves both and Thursday's lunch cannot overwrite Thursday's dinner. Separate *lists* rather than the separate *files* this entry proposed: the part that matters is that the signals share no key, which is the call this codebase has now made five times, but they answer one question and are always read together, so `biometrics.json`'s shape (four signals, four lists, one file) is the precedent taken. A mark is an update, not an append — the one thing separating it from `save_rejection_entry` — and un-marking **deletes** the row, because absence and a status are different answers and a fourth `unknown` status is one every reader would have to treat as absent anyway; clearing what was never marked writes nothing at all, so an untouched checkout stays distinguishable on disk. **The workout half shrank exactly as this entry predicted it should be re-scoped to**: `nutrition_engine.match_recorded_sessions` is the per-date read of v0.33.0's `activity_log` against the declared week — pure, type-and-date matched with the clock only breaking ties, each declared session claiming the nearest *unclaimed* recording, an unmapped modality answering nothing — and only the gap is stored. `PlannerState.mark_workout` refuses a session the watch recorded rather than merely not offering the button, so `activity_log` and `adherence.json` can never hold two answers to one question; where both somehow say yes, Garmin wins. `data/workout_log.json` was therefore not needed. **Decision 2 took the answer this entry named**: `ui_cards.meal_card`'s icon row as a *sibling* of the clickable body — which meant moving `today_card`'s click handler off the card element onto a body element, or a mark click would have bubbled through and opened the recipe dialog on top of the mark it just recorded. Three statuses rather than a boolean, because a skipped meal and a swapped one fail differently and the chart this feeds could not otherwise tell a missed dinner from a dinner out. All slate, glyph-distinguished, per the palette rule v0.32.0 established — emerald is the cook status, so a green tick would read as a fifth slot state. New `ui_adherence.py` and a new `"adherence"` refresh topic; the day inspector got it free, sharing `today_card`/`context_strip`. Marks persist on click and deliberately do not stage, and nothing in the generation path reads them: what to do with a run of skipped Thursdays is a product question, not a fourth soft prompt rule. Writes land in `data/`, so the two-writers-to-`config/` rule is untouched. `tests/test_adherence.py`, 45 tests over all three layers |
| `future-ideas.md` 5c · `ui-redesign.md` finding 3 — this queue's item 3 until v0.36.0 | The Insights destination was an empty state describing a blocker it never evaluated | Shipped in **v0.36.0**, and the notable thing is that **the entry's own trigger was still unmet on the day it shipped** — 6 weigh-ins across a 5-day span against a floor of 7, 5 logged days against the ~14 this entry suggested. What was blocked on data was a chart being *worth looking at*; what was never blocked was the page saying which precondition was unmet, and the stub proved the cost of leaving that undone by printing the counts and naming the rule without evaluating it — the identical failure v0.30.0 fixed one floor down. Five readouts: weight against target with the weigh-in table under it, planned calories against logged, macro accuracy, adherence tiles. Each is an `InsightPanel` from `ui_state.py` (`state`/`headline`/`detail`/`drawable`) over four states — `INSIGHT_EMPTY` (nothing recorded), `INSIGHT_SPARSE` (fewer than `INSIGHT_MIN_POINTS`, nothing drawn), `INSIGHT_THIN` (drawn, span named) and `INSIGHT_READY` — because empty and sparse spell identically as a missing chart and have different fixes, the `AdaptiveTDEEStatus` precedent. **Thin is drawn**, since this entry's worry ("a 14-day chart against 5 points is thin; a 30-day one is misleading") is about the *axis*: a window anchored on the data's own last row and captioned `6 point(s) across 5 day(s)` cannot mislead the way a fixed axis with six dots in one corner does. `paired_intake_days` is the single join between `meal_history.json`'s per-day `targets` and `daily_actuals`, borrowing two rules rather than inventing them (last history entry per date wins; a zero-calorie row is not a pairing, per `logged_intake_for`) — **this entry never mentioned `meal_history.json`, and it was carrying the planned half of two of the five charts all along**, which is the "check what is already stored before writing a schema" lesson v0.35.0 recorded, paying again. `nutrition_engine.measure_weight_trend` gives the chart and the estimate one slope, and is the first production caller `smooth_series` has ever had — its docstring has said "for display: the weight-trend line a UI draws" since it was written, so this is the fetched-and-never-read pattern seen from the build side. Three chart decisions are all the same decision: the target line draws only when `target_in_range` (a 19 kg gap and a 1 kg span cannot share a legible linear axis, and a scaled axis clips it outright — the gap is captioned either way), macro accuracy is a percentage axis and was `MACRO_KEYS`-only so fibre kept its no-denominator rule — v0.40.0 gave fibre a target and a row, behind the stricter precondition that every paired day in the window states one, and the adherence percentage says "of marks" in words because the plans those dates were generated against are gone from `week_plan.json`. No chart introduces a hue: `CHART_MACRO_COLOURS` is `MACRO_TINTS` in hex, a logged bar takes `BAND_COLOURS` from the same `macro_band` call the header makes, the reference series is always the dashed one. `insights.panel` becomes the third member of the `"adherence"` topic, which was documented as unable to grow one |
| `future-ideas.md`, "Pantry photo → an inventory ledger" — this queue's item 2 until v0.37.0 | `inventory_to_clear` was a flat list of strings, so one tin of tuna could be written into five recipes in the same week | Shipped in **v0.37.0**, with both decisions answered rather than deferred. **Decision 1 took this entry's own recommendation and skipped the camera**: a `quantity_g` on the existing list, no vision model, no third model role, nothing binary in `data/` — the ledger was always the hard part and the OCR never was. **Decision 2 is run-scoped, never persisted**: `generate_week_plan` seeds `seed_inventory_ledger(config)` once, publishes it to each stage as `config["inventory_ledger"]` and calls `spend_inventory` on what that stage actually returned, then throws it away. A count that survived the run would start disagreeing with the shelf the moment you cook something without telling the app — the "state able to disagree with reality" problem the shopping list's unpersisted checkboxes were designed around — and it would have made generation a *third* writer to `config/`, where both existing ones persist a standing setting. It rides on `config` in memory, the `nudge_foods` channel, covered by the standing rule that `save_config_keys` merges named keys rather than saving what it is handed. **The mechanism was the precedent this entry named**, and it held exactly: `seafood_used` with a dict instead of an int, spent in `MEAL_TYPE_PRIORITY` order, later axes told when an item is gone — because no single call sees more than its own axis, which is why a quantity stated to all four permits four. **The matching is the part this entry did not anticipate**, and it is where the reuse paid: `shopping.ingredient_draws_on` rather than a third notion of "same food", `normalize_name`'s equality widened to containment (a pantry entry says "chicken thighs", a recipe says "Chicken thigh fillets, diced") and guarded by matching departments and states, because over-matching tells later meal types an item is spent while it is still in the fridge and under-matching merely restores the old behaviour. Counted per cook event, not per slot that eats it, the call `is_seafood_meal` already makes. Overshoot floors at 0 and an exhausted item drops out of the prompt rather than being announced as gone. Both entry shapes stay legal — a bare string is unquantified and always was — with `inventory_entries` the single parser, dropping a malformed entry with a warning on `split_targets`' policy. The drawer's chip box became a row editor (a chip cannot hold two fields) on the training editor's pattern, `PlannerState.pantry` carries rows through the same parser generation reads, and `"pantry"` is a new refresh topic because typing in a row must refresh nothing while adding one changes the staged bar's count. **Subtracting the pantry from the shopping list is still not done**, and this entry called it "the thing people actually want": it needs the ledger to survive the run, which is precisely what decision 2 declined |
| appendix, "`favorite_fits_day` keys on the weekend" · appendix, "Generated long cooks can still land on a weeknight" | The two halves of long-cook placement disagreed: a *saved* braise could not take a Thursday and a *generated* one could | Shipped in **v0.37.0**, together, because they were one question. `planner.day_allows_long_cook` is the single answer both read — `location_rules.<location>.allows_long_cook` off the day's `base_schedule` location, falling back to the weekend when the location says nothing, so a config predating the key plans byte-identically. **The worry that deferred the first was that a second notion of "a day with room to cook" would drift from `prep_limit_for`. It is not a second notion — it is the other axis**: active minutes are a claim on your attention and stay weeknight-versus-weekend, elapsed hours are a claim on your presence, which is what a braise needs and what `base_schedule` already records. `prep_limit_for` is untouched. **A location may rule a weekend day *out*, which the appendix entry ("widening it") did not anticipate**: the shipped `Saturday: Outing` loses the long cook the calendar gave it while Tuesday and Wednesday gain one, and that direction is the point — the complaint was that the calendar is not where you are. The second half needed the elapsed-time field the entry predicted: `Recipe.total_time_minutes`, `None` for unknown and never 0, asked for by `ELAPSED_TIME_RULE` and checked by `reject_misplaced_long_cook` on both response models over one shared function, exactly as `enforce_prep_limit` already splits. **Two ways to fail, because the flag alone catches only half**: `long_oven_cook` is a self-report a careless model omits, and the elapsed figure is what catches the braise that never declared itself. **A batch anchor is exempt or the rule would break the long-cook toggle outright** — both anchors sit on day 1 but are cooked on prep day, the same `prep_day_batch_slot_ids` `build_cook_event` counts fridge days from. `BATCH_ROAST_RULE` became `build_batch_roast_rule(config, days)` and now names the days the validator will accept, emitting nothing when none qualify, since asking for a dish certain to be rejected is a guaranteed wasted 30s–3min call |
| `glm-suggestions.md` (2026-08-30) — this queue's item 2 until v0.37.0 | The front end declared no typography at all, and its two commonest muted greys were below AA | Shipped in **v0.37.0**. **Typography:** `grep font-family src/` returned nothing before this — everything rendered in Quasar's default Roboto and the 39 `font-mono` figures in whatever monospace the viewer's OS picked. `ui_theme.UI_FONT_STACK`/`FIGURE_FONT_STACK` are emitted by the new `typography_css()` from `ui_app.py`'s single `add_css` call, **system stacks and never a webfont**: nothing else on this page needs the network — no CDN anywhere, `whfoods.json` ships in the repo, and the only outbound call is OpenRouter's, from the *server* — so a Google Fonts link would make the front end the one part of the app that fails offline. Applied as custom properties and by redefining what `.font-mono` resolves to, which moves all 39 figure sites with no call site touched; the Quasar selectors are wrapped in `:where()` (zero specificity) so a component that genuinely needs its own face still wins without an `!important` arms race. `font-variant-numeric: tabular-nums` is declared at the **root**, not on a figure class — this entry costed it as "the telemetry and card figures that are not mono", which is right, and a class every one of those sites has to remember is the wrong way to reach them in an app that is labels and numbers almost end to end. **Contrast: (c) went further than filed, deliberately.** The rule as written was "no `slate-600` at any size, no `slate-500` at `TEXT_MICRO`"; measured on `slate-900`, `slate-600` is 2.3:1 and `slate-500` is 3.7:1 — under AA at *every* size this app uses, since `TEXT_BODY` is 12px — and the filed rule would have left 39 body labels below the floor while producing the odd inversion of a 10px label sitting brighter than the 12px label beside it. Both are retired from text across **110 sites** (the count had drifted from this entry's 102), and `text-slate-400` (6.9:1) is now the dimmest text there is. **A floor that stops at one size is not a floor**, which is the general form of it. Three places where dimness was carrying meaning keep it by other means, the same shape-not-hue move v0.32.0 made throughout: a done recipe step is `slate-400` **plus** `line-through`, an unset favourite or adherence mark is `slate-400` **plus** the outline-versus-filled icon. **Charts split the constant rather than following the rule blindly** — WCAG asks 4.5:1 of text and only 3:1 of a graphical object, and `slate-500` sits between the two, so it was simultaneously fine for the reference *line* and short for the 10px axis *labels* beside it: `CHART_AXIS` (slate-400) now takes axis, legend and markLine labels while `CHART_MUTED` keeps the series and its markers, which is what stops the planned line brightening into competition with `CHART_INK`. The dash, not the tint, is what distinguishes it. **`TEXT_MICRO` 10px → 11px did not ride along**, exactly as this entry instructed; it is now its own row in the craft-items table with the verification note intact |
| `glm-suggestions.md` (2026-08-30) — this queue's item 2 until v0.38.0 | The UI read flat: one `shadow-*` class in the whole front end, and three moments missing | Shipped in **v0.38.0**, all five parts. **(a) is worse in the measurement than in the prose.** The review said "`slate-900`/`950` fills separated by 1px borders"; the page ground was in fact Quasar's own `#121212` — *lighter* than slate-950 and barely darker than the slate-900 every panel uses — and `ui.tab_panels` was `bg-transparent`, so a card's translucent tint composited onto the body with nothing at all between them. `SURFACE_PAGE`/`SURFACE_PANEL`/`SURFACE_INSET`/`SURFACE_CARD_LIFT` and `surface_css()` are the answer: ground slate-950 on `body`, panels/rail/dialogs slate-900, cards `shadow-sm`. **The trap this entry recorded held exactly as written** — elevation is fill and shadow, the borders stay where `STATUS_STYLES` put them. **What the entry did not anticipate is that a translucent fill is a claim about what is behind it**, and three of them had to move with the ground. (b) rides in as one of them, for a better reason than "brighter": 7% of anything against a lighter ground had been carrying the one card in the week that costs you an evening. Skip went `slate-900/40` → `slate-950/40`, because over a slate-900 panel the old value composites to *exactly nothing* — going down a step instead reads as recessed, which is the honest shape for the one status where nothing is planned. `ui_insights`' adherence tiles and `ui_catalog_browser`'s row hover were the other two that vanished; both are `SURFACE_INSET`. And the rail's surface had to move off `ui.tabs()` onto a wrapper div: `.q-tabs` carries a height of its own and ignores `self-stretch`, so the painted column stopped under the last tab — true all along and invisible at the old contrast. **Raising the contrast between surfaces exposes every element sized to its content rather than to its column**, which is the general form and is now in the skill. **(c) is a banner, not the hero the review asked for**, and this entry's own correction is why it could be settled: it had already established that `week_plan is None` gates the *exports*, not the shopping list, so this was known to be a new branch. The grid is not empty without a plan — `slot_views` builds from the spec, 28 placeholder cards render, and every structural control on them works and is worth using *before* a run — so replacing it would hide the thing a first-time visitor most needs to do. It carries no Generate button, per phase 6b; it names the rail's and wears its icon. **(d) landed in the hour this entry costed it at**, against the review's three days, and the correction was worth writing down: everything needed was already arriving on the loop. The one piece with logic in it moved to `ui_state.generation_stage_views` per the skill's "move it down rather than grow a harness" rule, and what it holds is the off-by-one that makes the feature honest — `progress_callback` fires *before* each call, so its count is stages **started** and index `started - 1` is in flight; reading it as finished would tick a stage done up to three minutes before its recipes exist. Nothing banks the last stage but the run returning (`complete`), and a run that raises correctly leaves the in-flight stage running. Glyph carries all three states, no hue does. 7 tests. **(e)** the confirmation is built once outside the refreshable `bar()` — a dialog inside a refreshable stacks a copy per repaint, the `ui_generation` precedent — with its own refreshable body, so it lists what is *actually* pending at the moment of opening; the confirm button is slate, since rose already means a failed slot and an off-target reading, and a dialog whose whole purpose is naming what you lose can say it in words. **Both of this entry's deliberate exclusions stayed excluded**: card interior padding (the misread) and hiding Insights behind a data gate |
| `glm-suggestions.md` (2026-08-30) — this queue's item 4 until v0.37.0 | The app had no name, mark, accent or favicon; `ui.run(title=...)` held a description of the program | Shipped in **v0.37.0**, and the blocking decision was answered by asking: the app is called **Larder**. The store cupboard — it names the half of this app that is actually unusual (the pantry ledger, `inventory_to_clear`, the fridge window bounding a batch, shopping windows grouped by cook day) rather than the meal planning every app in the category also does, and it is six characters, which was the constraint that mattered. `APP_NAME`/`APP_MARK_ICON`/`APP_FAVICON`/`APP_TITLE` in `ui_theme.py` are the only places that name it. **(a)** favicon through `ui.run(favicon=)`, a kwarg taking an emoji directly, exactly as this entry corrected the review's `add_head_html` proposal — no asset to ship and nothing to 404. **(b) moved, and the premise is why.** This entry filed the wordmark for the top of the rail on the stated ground that the app named itself nowhere on screen, and that was untrue when written: an icon and a title string had sat in `ui.header()` since before v0.23.0. What was missing was a *name*, not a place to put one. It stays in the header, because `ui.header()` is `position: fixed` — the whole reason the week grid needs `WEEK_GRID_HEADER_INSET_STYLE` — while the rail is not, so a rail wordmark scrolls off on the first scroll of a 28-card grid, and printing the name in both places would be saying it twice. `RAIL_WIDTH_PX` was therefore never tested, which is the one part of this entry's reasoning that went unused. **(c) cleared the palette table, and found the hue was already in the app.** Teal was in five places — the Shopping rail button, the shopping drawer's checkboxes and three review controls — picked one widget at a time, in no table, meaning nothing in particular. Naming it turned five accidents into one token, which is why the palette row reads as a single meaning (*this is Larder talking*) rather than two. Its neighbours never share a surface with it: emerald is on cards and telemetry bars, cyan only on the recipe dialog's fibre figure. **The Shopping button had to give the hue back**, which this entry did not anticipate: it sat beside Generate as the second of "the week's primary verbs", both un-flat and each in a different saturated colour, so the two competed rather than ranking. It is outlined now — filled accent > outlined slate > flat slate ranks by *shape*, the `bookmark`/`bookmark_border` distinction again. Generate loses Quasar's `color=primary`, the framework default this entry correctly identified as the reason it had no accent anyone chose. **(d) moved as the pair this entry insisted on**, and the reason to retire emoji turned out to be stronger than "consistency": an emoji renders in the platform's own emoji font at its own colours, so ⚡ arrived amber-yellow on macOS and flat blue on Windows — reintroducing a hue in the exact two badges whose justification for going slate in v0.32.0 was that the glyph carried the distinction. `kitchen`/`ac_unit` for fridge/freezer (the literal distinction rather than the ⚡'s metaphor for it), `tune`/`fitness_center` for the telemetry marker, the latter deliberately reusing `TRAINING_TYPE_ICONS`' own vocabulary rather than adding a second word for "training" |
| appendix, "No daily fibre target" — the last row in the appendix | Fibre was reported everywhere and aimed at nowhere; `FIB 32g` was the one figure in the header with no denominator | Shipped in **v0.40.0**, and the entry's own framing is what had to give. It was filed as the row that "has to change what a macro budget *is*", which is why v0.39.0 left it standing — and nothing about a macro budget changed. **`MACRO_KEYS` was answering two questions**: "which keys have a term in `calories ≈ 4p + 4c + 9f`" and "which keys have a target". Those were the same set only while fibre was the one nutrient reported and not aimed at, and only the first is a fact about the tuple. So identity operations (`derive_fat_g`, `apply_protein_floor`, `reject_untrimmable_macro_miss`) still walk `MACRO_KEYS` and proportional ones may walk `NUTRIENT_KEYS`, and fibre takes a briefed share of a day without entering a single budget check. **The term is not where this entry said to put it.** `nutrition_engine.calculate_fiber_target_g(calories, floor_g)` is `max(floor, calories/1000 × 14)` — 14 g/1000 kcal is the dietary reference figure, `user_profile.fiber_floor_g` (30 g) the preference half — and it is deliberately *not* assembled inside `calculate_macro_targets`, because that function returns `calories` before `hydrate_dynamic_targets` replays the training uplift onto it and takes it `min()` against a diet-style ceiling: a figure computed there would be wrong on exactly the days that move. Same "after the uplift, not before it" argument that places the ceiling. **The floor is the load-bearing half**, on the argument that already locks protein to the *target* weight: scaled alone, an 800 kcal Fast 800 day asks for 11 g, cutting the target exactly as the deficit that made the day small starts to need the satiety — so the energy term may only ever raise it. `with_fiber_targets` covers hydration's three no-engine paths, or `/api/targets` would omit a figure the telemetry header prints either way. **The per-slot share is a fourth pass in `split_targets`, and three things it is not are the design**: a `meal_overrides` pin does not pin fibre (an override states fixed *energy*, and energy says nothing about fibre); there is no fibre counterpart to `apply_protein_floor` (protein is dose-limited per meal, fibre is not); and **the share does not cascade** — every macro budget is a share of what is left of the day, but a fibre goal piled onto whichever meal type runs last is the failure `cap_to_weighted_share` bounds for calories and could not bound here, so each slot reads its share out of `apriori_budgets` and a meal's fibre brief is the same number whichever stage generates it. **There is still no validator**, on the standing rule that a rejection costs a 30s–3min retry nothing downstream could act on. `FIBER_REPORTING_RULE` became `FIBER_TARGET_RULE`: its second sentence said "has no target: never pick an ingredient to raise it", the exact opposite of what a target means, and the clause that survived is the one about trading — fibre is bought by substitution at constant macros (wholegrain for refined, legumes for some starch, skins on), named explicitly because "don't trade" alone leaves a model with a target and no permitted way to reach it, which is the shape of rule it drops. The header reads `FIB 24/30g · logged 22g` — the divider is the target's, and the logged figure stays beside the pair, since a measurement was never the missing denominator. Insights' macro accuracy gained a fibre row, but only when **every** paired day in the window states a target, since a history entry predating it would read as a 0 g plan massively overshot; Settings' Daily Targets grew a fifth row, because a figure the header prints and whose origin is stated nowhere is the gap that panel exists to close |
| `ISSUES.md` 5 | Can't open a batch-cooking recipe | Phase 6c — body opens the shared `open_detail` |
| `ISSUES.md` 6 | Rename "Today" to "Daily View" | Shipped post-phase-3 (rail label only; function names unchanged) |
| `ISSUES.md` 7 | Library cards clickable only on the title | Phase 6d — `catalog_card` mirrors `meal_card`'s split |
| `ISSUES.md` 8, bullets 2–4 | Sync / location / workout pages | Phase 6e — three read-only dialogs off the integrations rows |
| `ISSUES.md` 9 | `--date` fetched a whole catchup range | Fixed — `--date` defaults to `None`, `--catchup` to `None`, resolved as "catch up unless a date was named"; Cronometer now costs one export request per span |
| `ui-redesign.md` phase 5, recorded as a finding | `/api/recipes` reimplemented `ui_catalog_browser._matches` | Shipped in **v0.31.0** — `repository.catalog_matches` is now the one filter, called by the route and by the Library grid. It sits beside `recipe_content_key` on the `BIOMETRIC_SECTION_SOURCES` precedent (a fact about the shape of a stored record, two readers with nothing else in common); `ui_catalog.py` was the other candidate home and lost on the one point that `api.py` deliberately imports nothing from `ui_*`, and this needs no `PlannerState`, no `UIContext` and no NiceGUI. **The predicted silent drift had already happened**: `_matches` read `"All"` as the no-filter meal type and the route read `None`, so `/api/recipes?meal_type=All` returned nothing while the grid's own default returned everything — two well-formed answers, no error either side. `CATALOG_MEAL_TYPE_ANY` names the UI's spelling and the shared function accepts both |
| `ISSUES.md` 8, first bullet | Nothing synced on server start, and nothing was scheduled | Shipped in **v0.31.0** — the decision was which of three shapes, and it went to the scheduled job this file recommended: `scripts/sync.sh` runs both sources (`run`) or installs a daily launchd agent (`install`, 07:30 by default, `MEALS_SYNC_HOUR`/`MEALS_SYNC_MINUTE` at install time), with `uninstall`/`status` beside them and output to `logs/sync.log`. **Nothing in the app triggers a sync**, which keeps phase 6e's read-only line intact and keeps a Garmin outage out of the UI process. What the app owes a job it doesn't run is saying when it stopped: `ui_state.sync_freshness` draws one line above the sync dialog's cards, answering two questions separately — the newest checkpoint across sources for "is anything running at all" (`SYNC_STALE_AFTER_DAYS` 2, not 1, since a once-daily job leaves yesterday's date on the board all morning) and a per-source lag for "is one credential failing while the others advance". It reads `sync_checkpoints` and never rows, unlike `sync_status` beside it — a scale nobody stood on for a week records nothing while the job runs perfectly. No new colour: the icon carries it, since amber then meant five things — the collision v0.32.0 closed below |
| not previously filed — found 2026-08-28 | The adaptive TDEE had never fired, and nothing said which precondition stopped it | Shipped in **v0.30.0** — `nutrition_engine.measure_adaptive_tdee` returns an `AdaptiveTDEEStatus` (estimate, weigh-in count, weigh-in span, logged days, the `MIN_TREND_SPAN_DAYS` floor), every count taken inside the window the estimate would have used; `calculate_adaptive_tdee` is a one-line wrapper over `.estimate`, so its `Optional[float]` contract and its tests are unchanged. `ui_state.adaptive_tdee_view` is the one view model both diagnostic surfaces read, over six states — the three unmet preconditions, `rejected`/`adaptive` off `basis["tdee_source"]`, and `measured` for a figure with no basis beside it. Settings' Daily Targets panel prints it under the calories row (and now takes one `planning_config()` for the whole section); Insights prints the verdict instead of stating the rule unevaluated. No new arithmetic, no new storage, nothing changed about what a week is planned against — the rejection path was always right, the reporting was the bug |
| `ISSUES.md` 11 · `future-ideas.md` 5d (step 1) | Garmin sleep/readiness fetched every sync and thrown away | Shipped in **v0.29.0** — `readiness_log` in biometrics.json (`sleep_score`, `sleep_hours`, `hrv_ms`, `readiness_label`) via `PlanRepository.save_readiness_entry`; `fetch_readiness` gained HRV from `get_hrv_data`'s `hrvSummary.lastNightAvg`, caught separately from sleep so one endpoint failing keeps the other. `BIOMETRIC_SECTION_SOURCES` became one-to-many, so `get_sync_date_range` folds a source's lists before ranking them and `sync_status` returns one card per stored list. Settings' Biometric Sync dialog shows the third coverage row; `/api/biometrics` mirrors the list. Deliberately storage-plus-one-read-surface: 5d's decision 2 is the morning-readiness item above and is untouched — nothing reads `readiness_log` into a target |
| CLAUDE.md, "Batch cooking on purpose" | `storage_note` counted fridge days from the anchor day, not prep day | Shipped in **v0.28.0** — `week.PREP_DAY_INDEX`/`cook_day_index`, `span_days(prepped_ahead=)`, `planner.prep_day_batch_slot_ids` (generation side) and `planner.is_prepped_ahead` (after it). `ui_state`'s rescale, favourite swap and fridge/freezer badge count from the same origin, so a grid edit can't put the off-by-one back |
| `ui-redesign.md` | Phases 1, 2a, 2b, 3, 4, 5, 6a–6e | All shipped; CLAUDE.md's "NiceGUI front end" is the source of truth |

`ISSUES.md` 8's first bullet is closed (v0.31.0) and 10 is the
morning-readiness item; 11 is closed (v0.29.0).
Nothing else in that register is open. **`ui-redesign.md` has nothing left
at all** — no entries in the queue above and no unfiled asides: v0.33.0
shipped the last aside and v0.36.0 shipped finding 3, its last filed
finding. Of the four source documents this queue consolidated, only
`future-ideas.md` and the front-end review still contribute open work.
