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
**v0.32.0**. Five releases have now closed whatever this queue ranked first
at the time: **v0.28.0** the fridge-day origin, **v0.29.0** the discarded
Garmin sleep/readiness, **v0.30.0** the adaptive TDEE that had never fired
and never said why, **v0.31.0** the duplicated catalog filter *and* the sync
nothing ever ran, **v0.32.0** the amber/violet collision *and* the fibre
readout with no measured half. Three of the source docs' claims turned out
to be stale and are corrected in the entries: `ISSUES.md` item 9 is fixed,
`future-ideas.md`'s 5c biometric counts are out of date, and
`ui-redesign.md`'s phase 4 aside is still unfiled.

**Two of the last two releases closed two items each**, which is worth
noting because the top of this queue has now moved by more than one place
twice running. v0.31.0: the `/api/recipes` duplication was an XS with no
decision in it, and the sync item's single blocking decision — which of
three shapes — was answered by taking the one this file already recommended,
a launchd job outside the app process. v0.32.0: the amber/violet pass and
the fibre item were independent, and shipped together only because the
palette work had to touch every `ui_*` module anyway and the fibre readout
lands in one of them.

**Both of the items found by this queue rather than inherited from a source
doc are now closed** — the adaptive TDEE that never fired (v0.30.0) and the
fibre readout with no measured half (v0.32.0), twins found the same way on
the same day: by asking what the Garmin and Cronometer syncs actually feed,
and checking the answer against the running engine and the live
`biometrics.json` rather than against any document's account of itself.
What leads the queue now is inherited again — `ui-redesign.md`'s phase 4
aside, which that phase said should be filed and never was.

**This file's own cross-references are by name, not by number**, the same
rule CLAUDE.md states for citing it from anywhere else. They had gone stale
by one after the previous renumber — "item 8" pointing at what had become
item 7, in three places — which is the argument for the rule rather than
against it: a number here has a shelf life of exactly one release.

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
| 1 | [Propose the training schedule from Garmin activity history](#1--propose-the-training-schedule-from-garmin-activity-history) | Feature | L | one decision |
| 2 | [Rejection list has no decay](#2--rejection-list-has-no-decay) | Feature | M | one decision |
| 3 | [Morning readiness check-in](#3--morning-readiness-check-in) | Feature | M | one decision |
| 4 | [Adherence and workout-completion tracking (5b)](#4--adherence-and-workout-completion-tracking-5b) | Feature | L | two decisions |
| 5 | [Pantry inventory ledger with real quantities](#5--pantry-inventory-ledger-with-real-quantities) | Feature | L | two decisions |
| 6 | [Trend charts / the Insights destination (5c)](#6--trend-charts--the-insights-destination-5c) | Feature | L | **data** |
| 7 | [Write and generation routes on the API](#7--write-and-generation-routes-on-the-api) | Feature | L | a design pass |
| 8 | [OpenAPI schema is off, so there are no generated types](#8--openapi-schema-is-off-so-there-are-no-generated-types) | Tech debt | S | — |
| 9 | [No auth on `/api`](#9--no-auth-on-api) | Feature | S | only if exposed |
| 10 | [Food waste tracking](#10--food-waste-tracking) | Feature | XL | not scoped |

Plus six smaller deferrals in [the appendix](#appendix--deferrals-recorded-in-claudemd-never-filed), each XS–M
and none urgent.

---

## 1 — Propose the training schedule from Garmin activity history

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

## 2 — Rejection list has no decay

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

## 3 — Morning readiness check-in

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

## 4 — Adherence and workout-completion tracking (5b)

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
   overwrite each other. Same reasoning as the fibre capture v0.32.0 shipped
   (see "Verified closed") and `readiness_log` before it; this codebase has
   now made that call three times, which is a good sign it is the right
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

**It gates part of [Trend charts / the Insights destination](#6--trend-charts--the-insights-destination-5c)**
— two of 5c's five proposed charts (7-day adherence, gym completion) have no
data source without this.

---

## 5 — Pantry inventory ledger with real quantities

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

## 6 — Trend charts / the Insights destination (5c)

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

`calculate_adaptive_tdee` still returns `None`, and **the app now says why
rather than leaving it to be re-derived here**: v0.30.0's
`measure_adaptive_tdee` reports the precondition, and this destination's own
empty state prints it. Measured on 2026-08-28 it read "weigh-in span 4 days,
needs 7" — the 14-day window is anchored on the most recent weigh-in, which
drops the 08-11 reading. **Roughly three more consecutive daily weigh-ins
clears it.** That is the trigger to re-evaluate this item — not a date, and
not "when there is enough data," but that one function returning a number,
which the page itself will now be the first thing to tell you.

Chart-worthiness needs more than the adaptive estimate does. A 14-day chart
against 5 points is thin; a 30-day one is misleading. Suggest revisiting once
`calculate_adaptive_tdee` returns non-`None` **and** there are ~14 daily rows
in both lists.

**Two of the five charts additionally depend on
[Adherence and workout-completion tracking](#4--adherence-and-workout-completion-tracking-5b)**
(adherence, gym completion) and should be dropped from a first version rather
than waiting for it.

---

## 7 — Write and generation routes on the API

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

## 8 — OpenAPI schema is off, so there are no generated types

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

## 9 — No auth on `/api`

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

## 10 — Food waste tracking

**Type:** Feature &nbsp;·&nbsp; **Size:** XL (not scoped) &nbsp;·&nbsp;
**Source:** `future-ideas.md` 5c, "Not scoped at all yet"

Flagged in the original architecture review as having no data source
whatsoever. It would need a new logging entry point of its own — a separate
product decision from both
[Adherence and workout-completion tracking](#4--adherence-and-workout-completion-tracking-5b)
and [Trend charts](#6--trend-charts--the-insights-destination-5c), and the
only item in this queue with no proposed schema, no proposed surface and no
proposed interaction.

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
| `ui-redesign.md` phase 1, recorded rather than resolved | Amber carried five documented meanings (eight in fact) and violet two | Shipped in **v0.32.0** — role separation: training, fridge/freezer, the favourite star, buy-late and the prep note all gave up their hue to a glyph already doing the work; carbs → orange, fibre → cyan, and the telemetry marker's emerald training case folded into amber. No hue in `ui_theme.py` now carries more than two meanings; the `ui-work` skill's collisions section is rewritten as "The palette". |
| not previously filed — raised 2026-08-28 | Cronometer logged no fibre, so the fibre readout had no measured half | Shipped in **v0.32.0** — `CRONOMETER_MACRO_COLUMNS` gains `fiber_g` (`Fiber (g)`/`Fiber`), keyed the repository's way rather than the CSV's. Capture and readout landed together, per this entry's own "file both or neither": capture alone reproduces the shape v0.29.0 closed for Garmin sleep. `ui_state.fibre_view(planned, logged)` is the pure view model holding the rule and both formatted halves, `PlannerState.fibre_for(day)` matches a `daily_actuals` row by the day's **calendar date** — not by weekday, which is why this is not `planner.logged_intake_for` — and `ui_telemetry.py` prints the logged figure as a second slate label beside the cyan planned one. **Side by side, never over a divider**: `32/24` in a row where every other entry is `actual/target` reads as a goal that was missed, and there is no fibre goal. `MACRO_KEYS` untouched, no budget change anywhere, `_prune`/`has_measurements` unchanged so an absent column is omitted rather than zeroed and every row synced before this reads as "no log". The appendix's **No daily fibre target** is deliberately still open and is a different, larger change |
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
Nothing else in that register is open.
