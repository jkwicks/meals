# Change queue

Every unfinished item and known defect, consolidated from `ui-redesign.md`,
`future-ideas.md` and `ISSUES.md`, in recommended priority order.

**Why this file exists separately from the other two.** `ui-redesign.md` is
work that waits on nothing and `future-ideas.md` is work that waits on a
product decision or on runtime data — a split that answers "may I start
this?" but not "what should I start?", because neither file ranks against
the other and neither holds the defects recorded in CLAUDE.md as they were
found. This file ranks everything in one list. The other two stay the place
where an item’s full reasoning lives; entries below point at them rather
than restating them.

**Provenance is stated per item**, because several of the entries below were
never filed anywhere — they were recorded in CLAUDE.md prose at the moment a
phase decided not to fix them, which is a good habit for keeping the *why*
and a bad one for ever getting back to them. The first item this queue ranked
was one of those, and is now closed.

**Everything here was verified against the code on 2026-08-27**, not against
the documents' own account of themselves, and re-checked against `main` at
**v0.29.0**. Two releases have now closed the item this queue ranked first:
**v0.28.0** the fridge-day origin, **v0.29.0** the discarded Garmin
sleep/readiness. Three of the source docs' claims turned out to be stale and
are corrected in the entries: `ISSUES.md` item 9 is fixed,
`future-ideas.md`'s 5c biometric counts are out of date, and
`ui-redesign.md`'s phase 4 aside is still unfiled.

**Items 1 and 5 were added on 2026-08-28** (they ranked 2 and 6 before
v0.29.0 shipped), and neither came from a source doc — both were found by
asking what the Garmin and Cronometer syncs actually feed, and both were
verified against the running engine and the live `biometrics.json` rather
than against any document's account of itself.

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
| 1 | [The adaptive TDEE has never fired, and nothing says why](#1--the-adaptive-tdee-has-never-fired-and-nothing-says-why) | Bug | S | — |
| 2 | [`/api/recipes` reimplements the catalog filter](#2--apirecipes-reimplements-the-catalog-filter) | Tech debt | XS | — |
| 3 | [Nothing syncs on server start, and nothing is scheduled](#3--nothing-syncs-on-server-start-and-nothing-is-scheduled) | Feature | S | one decision |
| 4 | [Amber carries five meanings, violet two](#4--amber-carries-five-meanings-violet-two) | Design debt | S–M | — |
| 5 | [Cronometer logs no fibre, so the fibre readout has no denominator](#5--cronometer-logs-no-fibre-so-the-fibre-readout-has-no-denominator) | Feature | S | — |
| 6 | [Propose the training schedule from Garmin activity history](#6--propose-the-training-schedule-from-garmin-activity-history) | Feature | L | one decision |
| 7 | [Rejection list has no decay](#7--rejection-list-has-no-decay) | Feature | M | one decision |
| 8 | [Morning readiness check-in](#8--morning-readiness-check-in) | Feature | M | one decision |
| 9 | [Adherence and workout-completion tracking (5b)](#9--adherence-and-workout-completion-tracking-5b) | Feature | L | two decisions |
| 10 | [Pantry inventory ledger with real quantities](#10--pantry-inventory-ledger-with-real-quantities) | Feature | L | two decisions |
| 11 | [Trend charts / the Insights destination (5c)](#11--trend-charts--the-insights-destination-5c) | Feature | L | **data** |
| 12 | [Write and generation routes on the API](#12--write-and-generation-routes-on-the-api) | Feature | L | a design pass |
| 13 | [OpenAPI schema is off, so there are no generated types](#13--openapi-schema-is-off-so-there-are-no-generated-types) | Tech debt | S | — |
| 14 | [No auth on `/api`](#14--no-auth-on-api) | Feature | S | only if exposed |
| 15 | [Food waste tracking](#15--food-waste-tracking) | Feature | XL | not scoped |

Plus six smaller deferrals in [the appendix](#appendix--deferrals-recorded-in-claudemd-never-filed), each XS–M
and none urgent.

---

## 1 — The adaptive TDEE has never fired, and nothing says why

**Type:** Bug (silent no-op) &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp;
**Source:** not previously filed — found 2026-08-28 while auditing what the
Garmin/Cronometer sync actually feeds

`calculate_adaptive_tdee` is the measured expenditure figure the entire
Cronometer sync exists to produce: mean logged calories + (kg lost per day ×
7700), the thing that replaces a population regression sitting ±300 kcal from
any individual. It returns `None` — "keep using the formula" — for fewer than
two weigh-ins, a windowed span under `MIN_TREND_SPAN_DAYS` (7), or no logged
calories in the window. All three are legitimate cold-start states and
returning `None` is correct.

**What is wrong is that nothing reports which of them you are in.** Measured
against the live `biometrics.json` on 2026-08-28: 5 weigh-ins, 5
`daily_actuals` rows, both sources checkpointed to 08-27 — a database that
looks by every visible count like it should be measuring — and

```
calculate_adaptive_tdee(daily_actuals, weigh_ins)  ->  None
```

because the 08-11 weigh-in falls outside the 14-day window and the remaining
four span 08-24 → 08-27: **three days against a floor of seven**. So
`basis["tdee_source"]` reads `"formula"`, exactly as it does on a fresh
checkout with an empty file, and has done since the sync was built.

**The rejection path is fine; it is the reporting that isn't.** Widening the
window to 21 days admits the 341 kcal partial log from 08-09, yields 1212.5
against a 2472 formula figure, and `reconcile_adaptive_tdee` correctly
returns `formula_adaptive_rejected` — the 25% bound doing exactly its job on
under-logged data. That state is already distinguishable. "Enough data to
look like it should work, and it doesn't" is not: it is spelled `"formula"`,
the same string as "nothing to measure yet".

**Neither existing surface closes the gap**, and one is close enough to be
misleading. `ui_insights.py` prints the counts (`"5 weigh-in(s) and 5 logged
day(s) of intake on record right now"`) and then names the *rule* — "at least
two weigh-ins spanning `MIN_TREND_SPAN_DAYS` days" — without ever evaluating
it, so a reader with five of each concludes it is on. Settings' Daily Targets
panel prints `"… → TDEE 2472 (formula)"`, naming the winner but not why the
alternative lost.

**The precondition worth naming loudest is the weigh-in span, not the log
count.** A gap in weighing collapses the window even when Cronometer is fully
caught up — which is precisely the state on disk today, and the least
guessable of the three from the outside. Chasing missing Cronometer days
would not have fixed it.

**Scope: no new arithmetic and no new storage.** `calculate_adaptive_tdee`
returns a bare `Optional[float]`; the fix is for it (or a thin sibling beside
it, so the pure function keeps its signature) to say *which* precondition
failed and by how much, and for the two surfaces above to print that. Note
this is upstream of item 11 — trend charts are blocked on data, and this is
the check that says whether the data has arrived.

**Acceptance:** the four states are distinguishable — no data / insufficient
span / rejected / adaptive — rather than three collapsing into `"formula"`;
Insights and the Settings targets panel report the current one and what would
clear it ("weigh-in span 3 days, needs 7"); a fresh checkout still says
something honest rather than erroring; `calculate_adaptive_tdee`'s existing
`None` contract and its tests are unchanged.

---

## 2 — `/api/recipes` reimplements the catalog filter

**Type:** Tech debt &nbsp;·&nbsp; **Size:** XS &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 5, recorded as a finding and deliberately not fixed

`api.py`'s `get_recipes` filters favourites, meal-type equality and a
case-insensitive substring on name inline; `ui_catalog_browser._matches`
(line 85) does the same three things. Four lines, two copies, free to
disagree — and they would disagree *silently*, since the API would simply
return a differently-filtered list with no error.

They are already subtly different: `_matches` treats `"All"` as the
no-filter meal type, the route treats `None` as it. That is fine as long as
one function owns both spellings and wrong as soon as a fourth filter is
added to one side.

Phase 5 declined to fix it because the original is private and lives in a UI
widget module that phase wasn't touching. It has no `PlannerState` dependency
and never did, so it wants a real home — a pure helper importable by both,
alongside the other catalog helpers in `ui_catalog.py`, or `planner.py` if
the API should not import a `ui_*` module at all. Decide which on the way
past; it is a one-line import either way.

**Acceptance:** one function, two callers, `test_api.py`'s existing filter
assertions unchanged and passing.

---

## 3 — Nothing syncs on server start, and nothing is scheduled

**Type:** Feature &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Source:**
`ISSUES.md` item 8, first bullet — asked as a question, never answered

Verified: `ui_app.py` and `scripts/server.sh` contain no sync call at all.
The only way biometrics reach disk is a hand-run
`src/integrations/sync_service.py`. So the question as asked — *"would like
recommendations to see if server start causes sync process to happen"* —
has a short answer (no, it doesn't) and a real follow-up (should it?).

**The rate-limit worry behind the question is already handled, and the
answer is worth recording.** A restart could not cause redundant fetches even
if a sync were wired to it: `sync_checkpoints` in `biometrics.json` records
each source's last-checked date, and `get_sync_date_range` anchors on
whichever requested source is furthest behind, so a same-day restart resolves
to an empty range and issues nothing. This is the same field the phase 6e
sync dialog reads to tell "checked, nothing there" from "nobody asked yet."
Cronometer's per-call cost was the real exposure and it was fixed separately
— `fetch_range_summaries` now asks for a span in one export request instead
of roughly five requests per day.

**The decision is which shape**, and they are meaningfully different:

- **On server start**, as a fire-and-forget task. Simplest, and matches how
  the app is actually used (start it when you want to plan). Needs care not
  to block page construction — `planner_page` is already `async` and the
  repository is already awaited there, so a task rather than an inline await.
- **A scheduled job** (cron/launchd calling the existing CLI). Zero new code
  in the app, keeps sync failures out of the UI process entirely, and syncs
  on days the server is never started. Arguably the correct answer, and it is
  a documentation change plus a plist rather than a feature.
- **A button in Settings.** The integrations rows are deliberately read-only
  today (phase 6e: "the row that owns a piece of state keeps owning it"), and
  adding a write action there reopens that call.

**Recommendation:** the scheduled job, documented in CLAUDE.md's Biometric
sync section, plus a "last synced" line on the existing Settings dialog so a
stale scheduler is visible. That keeps the app read-only with respect to
sync, which is the line phase 6e drew on purpose.

---

## 4 — Amber carries five meanings, violet two

**Type:** Design debt &nbsp;·&nbsp; **Size:** S–M &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 1, recorded rather than resolved · `.claude/rules/ui.md`
"Known collisions"

Still exactly as recorded. Amber is near-target (`BAND_COLOURS`), carbs
(`MACRO_TINTS`), training (`TRAINING_ACCENT`), a target override (the
telemetry marker) and fridge storage (`PREP_BADGE_STYLES`). Violet is fat
(`MACRO_TINTS`) and location (`LOCATION_ACCENT`).

Phase 1 deferred this to "phase 3, when the surfaces using them are rebuilt
anyway." Phase 3 shipped, then 6a–6e shipped, and the collisions came through
untouched — which is the useful signal here: this will not get resolved as a
side effect of a rebuild, because every rebuild so far has had a reason not
to widen its own scope. It needs to be its own small pass.

It is genuinely visible, not theoretical: a training day's telemetry column
can show an amber bolt (training), an amber dot (override) and an amber carb
figure simultaneously, none of which are related.

**What the codebase already decided about how to fix it.** `.claude/rules/ui.md`
records the precedent: *icon, not colour, distinguishes members of a set* —
`TRAINING_TYPE_ICONS` chose six glyphs over six hues for exactly this reason.
So the fix is likely to be subtraction: pick which one or two of amber's five
meanings genuinely need a hue and move the rest to shape, weight or position.
The rule file's own instruction — "adding a sixth meaning to amber is the
specific thing not to do" — is the constraint, and it means this item blocks
nothing but does quietly tax every new surface.

**Acceptance:** no colour in `ui_theme.py` carries more than two meanings;
`.claude/rules/ui.md`'s "Known collisions" section is either emptied or
rewritten to name what survived and why.

---

## 5 — Cronometer logs no fibre, so the fibre readout has no denominator

**Type:** Feature &nbsp;·&nbsp; **Size:** S (XS capture + S readout)
&nbsp;·&nbsp; **Source:** not previously filed — raised 2026-08-28

`CRONOMETER_MACRO_COLUMNS` maps four columns into a `daily_actuals` row.
Cronometer's daily-summary export carries fibre and the capture is one entry
in that dict.

**File the capture and the readout together, or file neither.** Capture alone
reproduces exactly the shape v0.29.0 closed for sleep: `daily_actuals`'s only
consumers are `calculate_adaptive_tdee` (calories alone) and
`logged_intake_for` (`MACRO_KEYS` alone), so a stored `fiber_g` would be
written on every sync and read by nothing — a second field paying its fetch
cost and returning nothing.

**The readout is the reason to do it.** `fiber_g` is deliberately
reported-never-budgeted: it has no term in `calories ≈ 4p + 4c + 9f`, and the
telemetry header prints a bare `FIB 32g` precisely because `32/xx` would
invent a goal the planner never aimed at. A *logged* figure is not a goal.
"Planned 32 g, ate 24 g" is a comparison against something real, and it is
the only macro where the app currently has one half of that pair and not the
other.

**This is not the appendix's "No daily fibre target" (M), and the distinction
is why it is filed separately.** A target needs a term in
`calculate_macro_targets` and a per-slot share in `split_targets` — real
changes to arithmetic that is checked. A planned-vs-actual readout needs
neither: it is two numbers the app would already hold, side by side, and it
deliberately does not settle the harder question.

Two things to get right, both with precedent in the module:

- **Keys are the repository's, not Cronometer's** — `fiber_g`, matching
  `Ingredient.fiber_g` and `NUTRIENT_KEYS`, never `fiber`. This is the exact
  failure `sync_service.py`'s own docstring already records for `protein_g`:
  a row keyed the CSV's way stores, sorts and displays perfectly and feeds
  nothing.
- **`fiber_g` stays out of `MACRO_KEYS`.** `NUTRIENT_KEYS` is the list it
  rides on, so `logged_intake_for`'s budget arithmetic and every reconciling
  check are untouched — the same separation CLAUDE.md's "Fibre is reported,
  never budgeted" already draws on the planning side.

**Deliberately narrow.** Sodium, potassium and the micronutrients are in the
same export and have neither a consumer nor an argument like the above. An
entry in `CRONOMETER_MACRO_COLUMNS` should assert that something reads it.

**Acceptance:** a synced day stores `fiber_g` when the column is present and
omits it when absent (`_prune`/`has_measurements` rules unchanged, so a row
of nothing but provenance is still not a logged day); `MACRO_KEYS` untouched
and no budget changes anywhere; one surface shows planned vs. logged fibre
for a day with both, and the planned figure alone — still with no
denominator — for a day with no log.

---

## 6 — Propose the training schedule from Garmin activity history

**Type:** Feature &nbsp;·&nbsp; **Size:** L &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 4 aside — *"a loose thread in an otherwise finished
phase. Worth filing there rather than leaving it here"* — and it was never
filed. This entry is that filing.

`config/schedule.json`'s `training_schedule` is hand-declared and hand-edited
in `ui_review.py`. `GarminSyncService` already syncs the activity data a
recurring-pattern detector would need. Phase 4 derived the *burn* for a
session (`nutrition_engine.estimate_session_burn_kcal`, MET-based) and
explicitly stopped short of deriving the *sessions*: "a real, separate
feature (a confirmation UI over inferred sessions)."

**The confirmation UI is the feature, not the detector.** Inferring "you
train Monday and Saturday mornings" from a few weeks of activities is
straightforward; what makes this L rather than M is that the schedule is a
planning *input* — `apply_training_adjustments` reads it to expand a day's
calorie budget and pin a post-workout meal — so a detector that wrote to it
directly would silently move targets based on a guess. Every inferred session
has to be proposed and accepted, which means a diff surface (proposed vs.
declared), an accept/reject action, and a rule for what happens to a session
the user declared that Garmin never sees.

**Precedent to follow:** the derived burn one. A derived default the user can
overrule, applied on an explicit click, never a live recompute — and
critically, *the same field*, so nothing downstream can tell a derived
session from a typed one. `estimate_burn`'s calculator-icon button is the
interaction to copy.

**Dependency worth noting:** it wants the same Garmin plumbing v0.29.0 built for readiness, and
naturally follows it.

---

## 7 — Rejection list has no decay

**Type:** Feature (product decision) &nbsp;·&nbsp; **Size:** M &nbsp;·&nbsp;
**Source:** `future-ideas.md`, "Rejection-list decay"

`build_rejection_rule` sends every entry in `data/rejections.json` to every
generation call, forever. That was a deliberate choice to raise the question
rather than settle it — capping to "most recent N" would just be a decay
policy picked silently.

**Ranked lower than the doc's urgency implies, deliberately.**
`data/rejections.json` does not exist yet: zero rejections have been captured
since phase 4 shipped. The failure mode — an unbounded dislike list starving
the rotation, the same way "unused in the last N" starves the tail of a list
— is real and will arrive, but it arrives slowly and there is nothing to
migrate when it does. Do this before the file gets large, not before it
exists.

**Three questions, all product calls, all in the source doc:**

1. What N should be. Too short and a stable dislike stops being honoured
   after a month; too long and it is today's behaviour with extra steps.
2. Hard cutoff or soft discount. A discount changes
   `build_rejection_rule`'s aggregation, not just its inputs.
3. Whether the *reason* changes the rate. "Had it recently" is arguably
   self-resolving in a way "don't fancy it" is not, so one uniform window
   across all four reasons may be the wrong model entirely.

**No storage change either way** — every entry already carries its `date`, so
the filtering or weighting lands in `build_rejection_rule` (a pure function of
`config["rejected_preferences"]`) or at the point that list is assembled.

---

## 8 — Morning readiness check-in

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

## 9 — Adherence and workout-completion tracking (5b)

**Type:** Feature &nbsp;·&nbsp; **Size:** L &nbsp;·&nbsp; **Source:**
`future-ideas.md` 5b

Nothing observes whether a planned meal was eaten, skipped or swapped, or
whether a declared training session actually happened. The nearest thing
today is the swap-with-favourite flow, which changes the *plan* rather than
logging a deviation from it.

**Two maintainer decisions, unchanged since the doc was written:**

1. **Where it is stored.** Proposed: `data/adherence.json` (`AdherenceEntry`:
   `date`, `slot_id`, `status` ∈ `eaten`/`skipped`/`swapped`, `marked_at`)
   and `data/workout_log.json` (`WorkoutCompletion`: `date`, `session_type`,
   `scheduled`, `completed`, `source`). Neither folds into `daily_actuals` —
   a manual mark and a Cronometer sync writing the same key would silently
   overwrite each other. Same reasoning as items 2 and 4 above; this codebase
   has now made that call three times, which is a good sign it is the right
   default rather than a coincidence.
2. **What "click a card" means.** A Today-tab click opens recipe detail
   today. "Mark eaten" needs its own affordance — a checkbox, a swipe, a
   second target on the same card — and that changes the interaction model of
   a screen that already shipped. `ui_cards.py`'s established pattern is the
   answer to copy: an icon row as a *sibling* of the clickable body, so a
   mark can't bubble into the recipe dialog.

**Order:** repository methods (`save_adherence_entry`, `load_adherence`, same
upsert-by-`date`+`slot_id` shape as `_upsert_dated_entry`) before any UI. The
write path is the design question; the read path is not.

**It gates part of item 11** — two of 5c's five proposed charts (7-day
adherence, gym completion) have no data source without this.

---

## 10 — Pantry inventory ledger with real quantities

**Type:** Feature &nbsp;·&nbsp; **Size:** L &nbsp;·&nbsp; **Source:**
`future-ideas.md`, "Pantry photo → an inventory ledger"

`config.inventory_to_clear` is a flat list of strings and
`inventory_instruction()` sends it as one priority line per day. There are no
quantities the code can reason about, so one tin of tuna can be written into
five recipes in the same week — nothing tracks that it was spent the first
time. It is also why the shopping list cannot subtract what you already have,
which is the thing people actually want from this feature.

**Two decisions:**

1. **Whether the photo path earns a third model role.** `models.json` names
   two today, both text; reading a shelf needs a vision model and somewhere
   to put an image, and `StoragePaths` handles JSON only — nothing in `data/`
   is binary. **The doc's own recommendation is to skip the camera entirely
   for v1** — a typed quantity column on the existing list — because the
   ledger is the hard part, not the OCR. That is also what makes this
   startable without settling the vision question at all.
2. **Whether a decremented ledger writes back to disk.** A count that lives
   for one run is honest and simple; a persisted count starts disagreeing
   with the actual shelf the moment you cook something without telling the
   app — the same "state able to disagree with reality" problem the shopping
   list's unpersisted checkboxes were designed around.

**The mechanism already has a precedent**, which is what keeps this L rather
than XL: a week-wide count that each generation stage spends and passes on is
exactly `seafood_used` for `max_seafood_meals_per_week`, and
`avoid_proteins`/`avoid_recipe_names` for variety. An inventory ledger is
that pattern with a dict instead of an int — `{"tinned tuna": 1}` seeded from
config, decremented by what each meal type actually used, later axes told
when an item is gone. Handing every meal type the full pantry is the current
behaviour and is what permits four meal types to claim the same tin.

---

## 11 — Trend charts / the Insights destination (5c)

**Type:** Feature &nbsp;·&nbsp; **Size:** L &nbsp;·&nbsp; **Blocked by:**
runtime data &nbsp;·&nbsp; **Source:** `future-ideas.md` 5c ·
`ui-redesign.md` finding 3 (the last open finding from the original review)

`ui_insights.py` is a 66-line honest empty state that reads live counts off
`biometrics.json` so the message ages correctly. The charts behind it are
scoped (weight vs. target, calories actual-vs-planned, macro accuracy,
adherence tiles, a weigh-in table) and need no new dependency —
`ui.echart` ships with the installed NiceGUI.

**The blocker is still real, but the source doc's numbers are stale and the
gap is closing.** `future-ideas.md` records one weigh-in and one
`daily_actuals` row as of 2026-08-16. Measured today: **5 weigh-ins**
(2026-08-11, then daily 08-24 → 08-27) and **5 `daily_actuals`** rows, plus
28 `meal_history.json` entries.

`calculate_adaptive_tdee` still returns `None`, and it is worth knowing
exactly why rather than assuming: it windows weigh-ins to 14 days anchored on
the most recent, which drops the 08-11 reading, leaving a 3-day span against
`MIN_TREND_SPAN_DAYS = 7`. **Roughly four more consecutive daily weigh-ins
clears it.** That is the trigger to re-evaluate this item — not a date, and
not "when there is enough data," but that one function returning a number.

Chart-worthiness needs more than the adaptive estimate does. A 14-day chart
against 5 points is thin; a 30-day one is misleading. Suggest revisiting once
`calculate_adaptive_tdee` returns non-`None` **and** there are ~14 daily rows
in both lists.

**Two of the five charts additionally depend on item 9** (adherence, gym
completion) and should be dropped from a first version rather than waiting
for it.

---

## 12 — Write and generation routes on the API

**Type:** Feature &nbsp;·&nbsp; **Size:** L &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 5, deliberately out of scope

`src/api.py` is read-only by design: five `GET` routes, each calling one
existing repository method or pure planner function, because "a route that
computed something would be a route free to disagree with the UI."

Generation is the hard part and the reason phase 5 stopped. It runs 30s–3min
*per meal type* and reports progress over NiceGUI's own socket
(`progress_callback`/`note_callback`). Turning that into an HTTP-shaped
operation is a genuine design question — poll a job id? SSE? WebSocket? —
not a mechanical translation. `PlannerState.generating` guarding re-entry
also becomes a cross-process concern the moment a second client can start a
run.

Nothing needs this today. It is filed so that "the API is read-only" stays a
recorded decision with a known cost rather than an assumption.

---

## 13 — OpenAPI schema is off, so there are no generated types

**Type:** Tech debt &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Source:**
`ui-redesign.md` phase 5

`nicegui`'s `App.__init__` hardcodes `docs_url=None, redoc_url=None,
openapi_url=None` regardless of what is passed to `ui.run()`, so
`/api/docs` and `/api/openapi.json` do not exist.

Only worth doing if a real front end is ever built against `/api` — but then
it is worth doing *first*, because the alternative is a hand-maintained
second copy of `Recipe`, which is the duplication this codebase reliably
regrets (see item 2, and `/api/recipes` before it). Re-enabling it is a small
separate task against the NiceGUI app object.

---

## 14 — No auth on `/api`

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

## 15 — Food waste tracking

**Type:** Feature &nbsp;·&nbsp; **Size:** XL (not scoped) &nbsp;·&nbsp;
**Source:** `future-ideas.md` 5c, "Not scoped at all yet"

Flagged in the original architecture review as having no data source
whatsoever. It would need a new logging entry point of its own — a separate
product decision from both item 9 and item 11, and the only item in this
queue with no proposed schema, no proposed surface and no proposed
interaction.

Kept here so it is not rediscovered as a new idea. It needs a design pass
before it can be estimated at all.

---

## Appendix — deferrals recorded in CLAUDE.md, never filed

Each of these was decided against at the moment a feature shipped, with the
reasoning captured in prose and no entry anywhere. None is urgent; all are
small enough to fold into adjacent work. Listed so the queue is complete.

| Item | Type | Size | Detail |
|---|---|---|---|
| `favorite_fits_day` keys on the weekend, not on where you actually are | Feature | S | A `long_oven_cook` favourite may only take a weekend slot. `base_schedule` knows Tuesday is a WFH day and a slow cooker started at 8am is fine, but widening the rule means a second notion of "a day with room to cook" that has to stay in agreement with `prep_limit_for` and `BATCH_ROAST_RULE`. CLAUDE.md: "a real improvement and belongs in `favorite_fits_day` when it happens." |
| Generated long cooks can still land on a weeknight | Bug (soft) | M | The favourite path is hard-gated; the generated path is not. `BATCH_ROAST_RULE` states a weekend preference and nothing rejects a model that puts a 4-hour braise on a Tuesday while truthfully reporting 25 active minutes. Making it hard needs an elapsed-time field on `Recipe` that no saved recipe carries — a schema, prompt and validator change together. |
| The Daily View day picker cannot cross weeks | Feature | M | Chevrons clamp at both ends of the loaded week rather than wrapping or spilling. Crossing weeks needs an async load of the other cached plan plus a second control free to disagree with the header's week selector. CLAUDE.md: "a real feature, and a bigger one than this." |
| No daily fibre target | Feature | M | `fiber_g` is reported everywhere and budgeted nowhere, deliberately — it has no term in `calories ≈ 4p + 4c + 9f`. A real target needs a term in `calculate_macro_targets` and a per-slot share in `split_targets`. Displaying `32/xx` today would invent a goal the planner never aimed at. |
| The bulk-prep **lunch** anchor keeps its from-scratch prep time | Bug (minor) | XS | `ui_state.slot_views` collapses a prep-session dish to `SUNDAY_PREP_REHEAT_MINUTES` on `sunday_prepped and event.meal_type == "dinner"` — a test written when only the long cook was anchored. `apply_batch_selections` anchors bulk prep on **lunch**, so that card shows the full cook time for a dish that was cooked on prep day. Found while fixing the fridge-day origin (below); left alone deliberately, since "how long does it take" is a different question from "how old is it" and the shake still has to be excluded either way. |
| Fast 800's calorie ceiling as a hard target | Feature | S | Currently expressed as food-selection guidance inside whatever budget the day was already given, because `hydrate_dynamic_targets` owns every day's calorie number and a second diet-style-driven adjustment would double-count. If the real ceiling is ever wanted, it belongs *inside* `hydrate_dynamic_targets`, not as a config knob beside it. |

---

## Verified closed — do not re-file

Checked against the running code on 2026-08-27. `ISSUES.md` predates phases
6a–6e and reads as open; it isn't.

| Source | Item | Closed by |
|---|---|---|
| `ISSUES.md` 1 | Header space, repeated day names, header/canvas misalignment | Phase 6a (alignment + one day identity), 6b (stat block → `week_banner`) |
| `ISSUES.md` 2 | All controls from the left panel | Phase 6b — the rail's action block |
| `ISSUES.md` 3 | Dates on day names | Phase 6a — `format_day_label(day, day_date_iso, short=True)` |
| `ISSUES.md` 4 | No swap/regenerate for batch cooking | Phase 6c — `prep_candidate_card`'s icon row |
| `ISSUES.md` 5 | Can't open a batch-cooking recipe | Phase 6c — body opens the shared `open_detail` |
| `ISSUES.md` 6 | Rename "Today" to "Daily View" | Shipped post-phase-3 (rail label only; function names unchanged) |
| `ISSUES.md` 7 | Library cards clickable only on the title | Phase 6d — `catalog_card` mirrors `meal_card`'s split |
| `ISSUES.md` 8, bullets 2–4 | Sync / location / workout pages | Phase 6e — three read-only dialogs off the integrations rows |
| `ISSUES.md` 9 | `--date` fetched a whole catchup range | Fixed — `--date` defaults to `None`, `--catchup` to `None`, resolved as "catch up unless a date was named"; Cronometer now costs one export request per span |
| `ISSUES.md` 11 · `future-ideas.md` 5d (step 1) | Garmin sleep/readiness fetched every sync and thrown away | Shipped in **v0.29.0** — `readiness_log` in biometrics.json (`sleep_score`, `sleep_hours`, `hrv_ms`, `readiness_label`) via `PlanRepository.save_readiness_entry`; `fetch_readiness` gained HRV from `get_hrv_data`'s `hrvSummary.lastNightAvg`, caught separately from sleep so one endpoint failing keeps the other. `BIOMETRIC_SECTION_SOURCES` became one-to-many, so `get_sync_date_range` folds a source's lists before ranking them and `sync_status` returns one card per stored list. Settings' Biometric Sync dialog shows the third coverage row; `/api/biometrics` mirrors the list. Deliberately storage-plus-one-read-surface: 5d's decision 2 is item 8 above and is untouched — nothing reads `readiness_log` into a target |
| CLAUDE.md, "Batch cooking on purpose" | `storage_note` counted fridge days from the anchor day, not prep day | Shipped in **v0.28.0** — `week.PREP_DAY_INDEX`/`cook_day_index`, `span_days(prepped_ahead=)`, `planner.prep_day_batch_slot_ids` (generation side) and `planner.is_prepped_ahead` (after it). `ui_state`'s rescale, favourite swap and fridge/freezer badge count from the same origin, so a grid edit can't put the off-by-one back |
| `ui-redesign.md` | Phases 1, 2a, 2b, 3, 4, 5, 6a–6e | All shipped; CLAUDE.md's "NiceGUI front end" is the source of truth |

`ISSUES.md` 8's first bullet is item 3 above and 10 is item 8; 11 is closed
(v0.29.0).
Nothing else in that register is open.
