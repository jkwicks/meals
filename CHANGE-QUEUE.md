# Change queue

Every unfinished item and known defect, consolidated from `ui-redesign.md`,
`future-ideas.md`, `ISSUES.md` and the 2026-08-30 front-end review
(`glm-suggestions.md`), in recommended priority order.

**Why this file exists separately from the other two.** `ui-redesign.md` is
work that waits on nothing and `future-ideas.md` is work that waits on a
product decision or on runtime data — a split that answers "may I start
this?" but not "what should I start?", because neither file ranks against
the other and neither holds the defects recorded in CLAUDE.md as they were
found. This file ranks everything in one list. The other two stay the place
where an item’s full reasoning lives; entries below point at them rather
than restating them. `ui-redesign.md` now contributes exactly one open entry
(finding 3, blocked on data); its last unfiled thread shipped in v0.33.0.

**Provenance is stated per item**, because several of the entries below were
never filed anywhere — they were recorded in CLAUDE.md prose at the moment a
phase decided not to fix them, which is a good habit for keeping the *why*
and a bad one for ever getting back to them. The first item this queue ranked
was one of those, and is now closed; so is the phase 4 aside that led it
until v0.33.0, which was recorded in prose in two documents and filed in
neither. The rejection decay v0.34.0 closed is the counter-example worth
keeping in view: it came from `future-ideas.md` with all three of its
questions already written down, so the work was choosing between answers
rather than reconstructing what the question had been. The adherence item
v0.35.0 closed is the same shape and makes the point twice over: its two
decisions were stated in `future-ideas.md`, and the *re-scoping* note that
halved the work was one this file added later, in the entry itself. An entry
is worth amending as the ground under it moves, not only when it is closed.

**Everything here was verified against the code on 2026-08-27**, not against
the documents' own account of themselves, and re-checked against `main` at
**v0.35.0**. Eight releases have now closed whatever this queue ranked first
at the time: **v0.28.0** the fridge-day origin, **v0.29.0** the discarded
Garmin sleep/readiness, **v0.30.0** the adaptive TDEE that had never fired
and never said why, **v0.31.0** the duplicated catalog filter *and* the sync
nothing ever ran, **v0.32.0** the amber/violet collision *and* the fibre
readout with no measured half, **v0.33.0** the training schedule proposed
from Garmin activity, **v0.34.0** the rejection list that never decayed,
**v0.35.0** the plan nothing ever checked against what was eaten.
All three of the source docs' stale claims are now settled: `ISSUES.md`
item 9 was already fixed, `ui-redesign.md`'s phase 4 aside is filed *and*
shipped, and only `future-ideas.md`'s out-of-date 5c
biometric counts remain — corrected in that entry.

**`ui-redesign.md`'s last *unfiled* thread is closed.** v0.33.0 shipped the
phase 4 aside that called proposing the schedule from Garmin activity "a
loose thread ... worth filing there rather than leaving it here" and was
then never filed anywhere. What that document still contributes is one
properly-filed entry — finding 3, the trend charts, which waits on data and
nothing else, and closing the adherence item moved it up to third.
Everything now at the top of this queue comes from `future-ideas.md`, but
the long-standing "the top three are all blocked on a decision" reading no
longer holds: v0.35.0 took the second of them, so items 1 and 2 are
decision-blocked and item 3 is blocked on data alone. The queue's top is
now two questions and a wait, not three questions.

**A third fetched-and-discarded signal turned out to be the enabling
half.** v0.29.0 found Garmin's sleep data fetched every sync and thrown
away; v0.32.0 found Cronometer's fibre column uncaptured beside a planned
figure with no measured counterpart; v0.33.0 found `fetch_cardio_activities`
in exactly the first shape — fetched on every sync, printed, stored nowhere
— and it was the missing input for the schedule proposal, not a separate
tidy-up. The pattern is worth stating because it has now paid three times:
**ask what each sync actually feeds, and check the answer against the
running code rather than against any document.**

v0.35.0 is the same pattern seen from the far end and worth recording as
such: `activity_log` had a reader within one release of being stored, and
that reader is what shrank this queue's largest remaining item from an L
with two schemas to one file and a derived read. **A signal with a consumer
keeps paying**; the three above are the case for storing one, and this is
the case for checking, before writing a new schema, whether something
already stored answers most of the question.

**Two of the last three releases closed two items each**, which is worth
noting because the top of this queue moved by more than one place twice
running. v0.31.0: the `/api/recipes` duplication was an XS with no decision
in it, and the sync item's single blocking decision — which of three shapes
— was answered by taking the one this file already recommended, a launchd
job outside the app process. v0.32.0: the amber/violet pass and the fibre
item were independent, and shipped together only because the palette work
had to touch every `ui_*` module anyway and the fibre readout lands in one
of them. v0.33.0 closed one, and it was an L; v0.34.0 closed one M;
v0.35.0 closed one that had been filed as an L and turned out to be smaller
than filed, because half of it had already been built for another reason.

**A decision-blocked item is unblocked by asking, not by waiting.** Three
releases have now cleared one, and they cleared it differently: v0.31.0's
sync item had three candidate shapes and this file had already recommended
one, so the decision was made by reading. v0.34.0's rejection decay did not
— its three questions were open, and the whole cost of clearing them was
putting them to the maintainer and building against the answers. v0.35.0's
adherence item was the same shape as v0.34.0's and is the strongest case
yet: it had carried **two** decisions and an L since the doc was written,
and both were answered in one exchange before a line was built. Items 1 and
2 are still in that state, with no recommendation standing. The corollary
holds and has now paid twice: "blocked by: one decision" is not a reason to
skip an item when picking what to do next — it is a reason to start it with
a question.

**A blocking decision is also worth re-reading before it is asked.** This
entry's storage question proposed two files and was answered with one file
holding two lists — an option it had not listed, because `biometrics.json`
had grown its multi-section shape after the question was written. Its
workout question had already half-answered itself in a note added later
("this shrank in v0.33.0 and should be re-scoped"), and that note was worth
more than the original proposal. **An old decision's options age; the
question rarely does.**

**Why the front-end block ranks 4–6 rather than higher or lower.** Items
1–3 are signals the app cannot currently see at all; 4–6 are the surface
every session already passes through, and none is blocked on work — only the
accent and the wordmark wait on a name. They rank below 1–3 because polish on
a working surface is worth less than a signal that does not exist, and above
the API entries because those have no consumer today: `/api` is read-only,
nothing outside NiceGUI calls it, and both the write routes and the generated
types are groundwork for a front end nobody has asked for. Every item in 4–6
was verified against the running code on 2026-08-30 rather than against the
review that raised them — **four of that review's proposals did not survive
that check** and are recorded as deliberately excluded inside the entries, so
they are not re-filed as fresh ideas later.

**This file's own cross-references are by name, not by number**, the same
rule CLAUDE.md states for citing it from anywhere else. They had gone stale
by one after an earlier renumber — "item 8" pointing at what had become
item 7, in three places — which is the argument for the rule rather than
against it: a number here has a shelf life of exactly one release, and this
renumber is the seventh. The previous one was an *insertion* rather than a
closure — the front-end block took 5–7 and pushed the three API entries and
food waste down to 8–11 — which was the same hazard from the other
direction, and the only time this file has had one. This renumber is an
ordinary closure again: removing the adherence item moved everything below
it up by one, so nine of the ten anchors changed and all nine were
re-checked against their headings.

**Both body cross-references pointed at the item that closed**, which is
the case the by-name rule exists for and the first time it has actually
bitten. Neither was left as a dangling anchor: the trend-charts entry's
"two of these charts have no data source" became "both data sources now
exist, and here is what is thin about the new one", and the food-waste
entry's became a note on why a `skipped` mark is *not* a waste signal. A
closed item is a change of fact for whatever cited it, not just a link to
repoint.

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
| 2 | [Pantry inventory ledger with real quantities](#2--pantry-inventory-ledger-with-real-quantities) | Feature | L | two decisions |
| 3 | [Trend charts / the Insights destination (5c)](#3--trend-charts--the-insights-destination-5c) | Feature | L | **data** |
| 4 | [The front end declares no typography, and its muted text is below AA](#4--the-front-end-declares-no-typography-and-its-muted-text-is-below-aa) | Tech debt | S | — |
| 5 | [The UI reads flat, and three moments are missing](#5--the-ui-reads-flat-and-three-moments-are-missing) | Tech debt | M | — |
| 6 | [The app has no name, mark, accent or favicon](#6--the-app-has-no-name-mark-accent-or-favicon) | Feature | S | one decision |
| 7 | [Write and generation routes on the API](#7--write-and-generation-routes-on-the-api) | Feature | L | a design pass |
| 8 | [OpenAPI schema is off, so there are no generated types](#8--openapi-schema-is-off-so-there-are-no-generated-types) | Tech debt | S | — |
| 9 | [No auth on `/api`](#9--no-auth-on-api) | Feature | S | only if exposed |
| 10 | [Food waste tracking](#10--food-waste-tracking) | Feature | XL | not scoped |

Plus six smaller deferrals in [the appendix](#appendix--deferrals-recorded-in-claudemd-never-filed)
and ten [front-end craft items](#front-end-craft-items--small-none-urgent), each XS–M
and none urgent.

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

## 2 — Pantry inventory ledger with real quantities

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

## 3 — Trend charts / the Insights destination (5c)

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

**Two of the five charts used to additionally depend on the adherence item,
and no longer do — v0.35.0 built both their data sources.** The adherence
chart has `data/adherence.json`'s `meals` list, keyed by date and slot, and
the gym-completion one has `nutrition_engine.match_recorded_sessions` over
v0.33.0's `activity_log` plus the `workouts` list for what the watch missed.
So all five charts are now blocked on exactly one thing — runtime data — and
none on missing work.

**That said, adherence is the *thinnest* of the five, and for a reason worth
stating**: unlike a weigh-in or a Cronometer row, a mark only exists if
somebody clicks it, so this series does not accumulate merely by the sync job
running. Treat "have I actually been marking" as its own precondition
alongside the weigh-in span, rather than assuming the file fills the way the
biometric ones do.

---

## 4 — The front end declares no typography, and its muted text is below AA

**Type:** Tech debt &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Blocked by:** —
&nbsp;·&nbsp; **Source:** `glm-suggestions.md` (2026-08-30), verified against
the code the same day

`grep font-family src/` returns nothing. The one `ui.add_css` call in the app
sets page padding, the leftover-chain CSS and the card hover glow and stops
there, so the whole UI renders in Quasar's default Roboto and the 39
`font-mono` figures render in whatever monospace the viewer's OS happened to
pick. For an app whose primary surface is seven columns of macros meant to be
scanned vertically, that is the largest gap between what it does and how it
looks.

| | change | size | note |
|---|---|---|---|
| a | Declare a UI stack and a figure stack in `ui_app.py`'s existing `add_css` | XS | Self-hosted or a system stack — no CDN, matching the app's otherwise offline-capable posture |
| b | `font-feature-settings: "tnum"` on figure text | XS | The 39 `font-mono` sites already align; this is for the telemetry and card figures that are not mono |
| c | Contrast floor — no `text-slate-600` at any size, no `text-slate-500` at `TEXT_MICRO` | S | **102 sites**, not the ~15 the review estimated: 78 `text-slate-500`, 24 `text-slate-600` |

(c) is the only one with real cost, and it is a defect rather than a
preference: `slate-600` on `slate-900` measures about 2.3:1 and `slate-500`
about 3.9:1 against AA's 4.5:1 floor for small text — and `TEXT_MICRO` is
10px, so the worst pairing in the app is also one of its most common. The
rule belongs in the `ui-work` skill's colour section once applied, beside the
palette table, where `slate` is already documented as "the neutral ground,
not a meaning".

**`TEXT_MICRO` 10px → 11px is filed here and must not ride along with (a)–(c).**
The review costs it as a tiny one-line change; it *is* one line, and the cost
is entirely verification. `RAIL_WIDTH_PX` is pinned at 168,
`WEEK_GRID_HEADER_INSET_STYLE` derives the header grid's position from it, day
columns floor around 110px, and `ui_cards.meal_card` carries a comment saying
its status badge row is "the one row on the card with no width to spare". A
10% bump on the most-used size in a nine-column layout can reflow all of
that. Do it alone, measure at 1280px and 1440px, and mirror the new value
into the skill's type table — that table is the canonical statement of the
scale, so a change made only in `ui_theme.py` leaves the contract lying.

---

## 5 — The UI reads flat, and three moments are missing

**Type:** Tech debt &nbsp;·&nbsp; **Size:** M &nbsp;·&nbsp; **Blocked by:** —
&nbsp;·&nbsp; **Source:** `glm-suggestions.md` (2026-08-30), verified against
the code the same day

There is exactly one `shadow-*` class in the entire front end
(`ui_generation.py`'s progress dialog). Everything else is `slate-900`/`950`
fills separated by 1px borders, so at this density every surface carries
identical visual weight and nothing reads as foreground. That is the accurate
core of the review.

| | change | size | note |
|---|---|---|---|
| a | Three surfaces — page `slate-950`, panels/rail/dialogs `slate-900`, cards `slate-900` + `shadow-sm` | M | **Fills and shadows only.** See the trap below |
| b | Brighten the cook-card fill `emerald-400/[0.07]` → `/0.12` | XS | One line in `ui_theme.STATUS_STYLES`, reversible |
| c | Empty-state hero for a week that has never been generated | S | New branch in `ui_plan.panel()` |
| d | Per-stage checkmarks in the generation dialog | XS | `on_meal_type` already fires per meal type with its cook count |
| e | Confirmation on "Discard pending changes" | XS | `ui_staged_bar.on_discard` throws away grid edits *and* pending inputs with no prompt |

**The trap in (a): the card border is already spoken for.** The review
proposes giving cards "a brighter border (`slate-700/60`)", which collides
head-on with `STATUS_STYLES`, where a card's border and its 3px left accent
are *structural* colour — emerald cook, sky leftover, slate skip, rose not
generated. A neutral border bright enough to read as elevation would compete
with four meanings already living on that exact edge, and would read as a
fifth slot status, which the skill names as the specific thing not to do.
Elevation has to come from fill and shadow; the borders stay where the
palette contract put them.

**(d) is an hour, not the two-to-three days the review costs it at.**
`ui_generation.py` already builds a persistent dialog with a
`linear_progress` bar, a status label and a live `ui.log` fed by
`note_callback`, and `on_meal_type` fires *on the loop* before each stage
with the meal type and its recipe count. Everything a staged readout needs is
already arriving; only the rendering is missing. Worth correcting in writing
because the review's own phasing puts (d) in a three-day block and it would
otherwise be deferred on a cost it does not have.

**(c) does not reuse an existing gate.** The review says `state.week_plan is
None` "already gates the shopping list the same way" — it gates the PDF and
HTML *exports* in `ui_app.py`. The grid renders SKIP cards off the spec
regardless of whether a plan exists, so this is a new branch, not a moved one.

**Two of the review's items under this theme are deliberately excluded, and
should not be re-filed.** Card interior padding, proposed as `SPACE_TIGHT` →
`SPACE_BASE`, rests on a misread: `meal_card` is already `p-{SPACE_BASE}` and
the `SPACE_TIGHT` is the row gap, which is exactly the job the spacing scale
assigns it ("between rows inside one card"). And hiding the Insights
destination until enough data exists contradicts the decision recorded in
`ui_insights.py`'s own docstring — the empty state is deliberate, reads live
counts so the message ages correctly, and now prints `adaptive_tdee_view`'s
verdict rather than restating the rule; hiding it would additionally make the
rail's shape depend on how much biometric data you happen to hold.

---

## 6 — The app has no name, mark, accent or favicon

**Type:** Feature &nbsp;·&nbsp; **Size:** S &nbsp;·&nbsp; **Blocked by:** one
decision &nbsp;·&nbsp; **Source:** `glm-suggestions.md` (2026-08-30), verified
against the code the same day

`ui.run(title="AI Weekly Meal Planner")` is the only place the app names
itself, and that string is a description rather than a name. Nothing appears
on screen at all: the page opens straight into the week date pill and the
grid, and the browser tab carries NiceGUI's default icon.

**The decision is the name**, and it blocks (b) and (c) but not (a) or (d).

| | change | size | note |
|---|---|---|---|
| a | Favicon | XS | `favicon=` is a kwarg on `ui.run()` — an emoji or an SVG path, *not* `add_head_html` as the review proposed. Unblocked: any mark will do |
| b | Wordmark at the top of the rail, above the Plan tab | S | Must not measure wider than `RAIL_WIDTH_PX` |
| c | One named accent token, used only on Generate and the wordmark | S | Must clear the palette table first |
| d | Emoji → Material icons, **both** ⚡ sites together | S | A paired change; see below |

**(b)'s constraint is not cosmetic.** `RAIL_WIDTH_CLASS` is pinned because
`ui.tabs()` sizes a vertical rail to its widest child, and
`WEEK_GRID_HEADER_INSET_STYLE` insets the header's copy of the week grid by
exactly that many pixels so it sits over the canvas. Anything in the rail
measuring wider slides every day's telemetry off its column — which is why
every existing `rail_button` is `w-full`, `TEXT_MICRO` and `align=left`
rather than intrinsically sized. A wordmark is an ordinary non-tab child of
`ui.tabs()`, the same shape `rail_actions` already uses, so the mechanism
exists; only the width rule has to be honoured.

**(c) has to clear the palette table before a hue is picked.** Each hue there
means at most two things and a third meaning is named as the specific thing
not to do; amber, emerald, sky, rose, violet, orange, cyan and indigo are all
spoken for. A brand accent is a ninth role, so it needs an unclaimed hue or a
documented exception — not a quiet reuse of Quasar's `primary`, which is what
happens today and is why the Generate button currently has no accent anyone
chose.

**(d) is a paired change, not a find-and-replace.** ⚡ appears in two
unrelated places: `ui_theme.PREP_BADGE_STYLES` ("Prepped on Sun") and
`ui_telemetry`'s day marker, where `•` means a target override and `⚡` an
edited training session. Swapping one and leaving the other has one glyph
meaning two things in adjacent surfaces. The skill also records that ⚡ and ❄️
are load-bearing rather than decorative — they are what let the fridge/freezer
badges give up their hues, which is what freed cyan for fibre — so a
replacement icon has to carry the same weight the emoji does, and `•`/`⚡`
has to move as a pair or not at all.

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
product decision from
[Trend charts](#3--trend-charts--the-insights-destination-5c) and from the
adherence marking v0.35.0 shipped, and the only item in this queue with no
proposed schema, no proposed surface and no proposed interaction.

**Adherence closing does not shorten it**, which is worth saying because the
two look adjacent: a `skipped` mark says a planned meal was not eaten and
says nothing whatever about whether its ingredients were thrown away. The
signal would still be new, and its entry point still unbuilt.

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

## Front-end craft items — small, none urgent

Raised by the same 2026-08-30 review and verified against the code, but each
individually too small to rank against the list above. All are XS–S, and each
folds naturally into whichever of items 5–7 is already touching that file —
which is the point of listing them here rather than filing ten entries that
would drown the ranking this file exists to provide.

| Item | Type | Size | Detail |
|---|---|---|---|
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
| `ui-redesign.md` phase 4 aside, never filed until this queue filed it | The training schedule was hand-declared while Garmin recorded what actually happened | Shipped in **v0.33.0** — `activity_log` is a fourth `biometrics.json` section (`GarminSyncService.fetch_activities` → `PlanRepository.save_activity_entries`), and `nutrition_engine.propose_training_schedule` diffs four weeks of it against the declared week. **The detector was the easy half; the confirmation is the feature**: nothing writes without a click, and the precedent copied is `estimate_session_burn_kcal`'s calculator button — a derived default, into the same field, applied explicitly. **The blocking decision — a declared session Garmin never sees — is answered symmetrically**: proposed for removal, never removed, behind two guards (the weekday must have come round at least twice inside the *observed* span, and `MIN_ACTIVE_DAYS_FOR_DROP` asks whether the watch is worn at all before its silence is evidence); a weekday that recorded *something* is never dropped, since a Sunday ride that became a Sunday walk is a day you plainly train on. **What counts as observed was the real difficulty** — `activity_log` holds only days that recorded something, the same ambiguity `sync_checkpoints` closed for weigh-ins, so the span runs from the first recorded activity to the later of the last one and Garmin's checkpoint, capped at today, and under-claiming is the safe direction. Storage is replace-per-date, not upsert: this is the one section with several rows per day. Only mapped, timed rows are stored — `GARMIN_SESSION_TYPES` has no catch-all and `startTimeLocal` is read rather than `startTimeGMT` — because a proposal is a sentence the user is asked to agree to. `net_calories` finally has a reader: it is the proposed burn, with the MET formula only as fallback. Accepting persists `training_schedule` through `save_config_keys`, making that the second UI control that writes to `config/` on `set_target_mode`'s reasoning, and applies the change to the file's list and the staged list separately so an accept never writes out someone else's half-typed session. Additions diff against the staged schedule, drops against the file's. `ui_state.training_proposals_view` carries the wording over three no-proposal states, on the `adaptive_tdee_view` precedent — "your week already matches" is the good answer and the one most likely to be misread as broken |
| `future-ideas.md`, "Rejection-list decay" — this queue's item 1 until v0.34.0 | `build_rejection_rule` sent every recorded rejection to every generation call, forever | Shipped in **v0.34.0** — and the answer to all three of the questions this entry left open is that there were **two signals in one rule**, wanting two windows. The **dish list** is a veto on one recipe and expires per reason (`planning_rules.rejection_decay_days`): `had_it_recently` 21 days because it is self-resolving — the dish stops having been had recently whether or not anything honours the entry — `too_much_prep` 60, `dont_fancy_it` 90, `wrong_for_slot` 180 because it is structural and a curry is never breakfast. **Per reason rather than one N** answers question 3 rather than deferring it, on the precedent `favorite_reuse_days` already set for its own split. The **recurring-reason tally** counts over the longer `rejection_reason_window_days` (180), so a standing preference outlives the dishes that evidenced it — which is what makes question 2's hard-cutoff/soft-discount choice moot rather than merely decided: a hard cutoff on the half that should expire, no cutoff at all on the half that shouldn't. **The tally moved into Python**, a consequence of the split rather than a flourish: once the halves have different windows the model only ever sees the shorter one, so asking it to notice a repeated reason had it weighing a subset while being told to weigh the whole. `REJECTION_REASON_GUIDANCE` names what a run of each answer implies, split from `REJECTION_REASON_LABELS` the way that dict was already split between UI and prompt; `REJECTION_REASON_SIGNAL_MIN` (3) is what counts as a run. **No storage change and nothing to migrate**, exactly as this entry predicted — every entry already carried its `date` — and done at the moment it recommended: `data/rejections.json` still did not exist, so this landed before the file got large rather than before it existed. Two fixes carried along: `build_rejection_rule` takes `today`, the `select_favorite_assignments` seam, because the existing tests held fixed date literals against a live clock and would have begun failing about six weeks out — the failure CLAUDE.md's "Tests" section already records catching once; and `planning_rule` extends its documented fallback to a config with no `planning_rules` section at all, which `AppConfig` already treats as legal |
| `future-ideas.md` 5b — this queue's item 2 until v0.35.0 | Nothing observed whether a planned meal was eaten, skipped or swapped | Shipped in **v0.35.0**, with both of this entry's decisions answered rather than deferred. **Storage** is `data/adherence.json`: two lists in one file — `meals` (`planner.AdherenceEntry`, this entry's own field list) and `workouts` (`planner.WorkoutCompletion`) — keyed by `date` plus a second field named per section in `ADHERENCE_SECTIONS`, so one `_upsert_adherence` serves both and Thursday's lunch cannot overwrite Thursday's dinner. Separate *lists* rather than the separate *files* this entry proposed: the part that matters is that the signals share no key, which is the call this codebase has now made five times, but they answer one question and are always read together, so `biometrics.json`'s shape (four signals, four lists, one file) is the precedent taken. A mark is an update, not an append — the one thing separating it from `save_rejection_entry` — and un-marking **deletes** the row, because absence and a status are different answers and a fourth `unknown` status is one every reader would have to treat as absent anyway; clearing what was never marked writes nothing at all, so an untouched checkout stays distinguishable on disk. **The workout half shrank exactly as this entry predicted it should be re-scoped to**: `nutrition_engine.match_recorded_sessions` is the per-date read of v0.33.0's `activity_log` against the declared week — pure, type-and-date matched with the clock only breaking ties, each declared session claiming the nearest *unclaimed* recording, an unmapped modality answering nothing — and only the gap is stored. `PlannerState.mark_workout` refuses a session the watch recorded rather than merely not offering the button, so `activity_log` and `adherence.json` can never hold two answers to one question; where both somehow say yes, Garmin wins. `data/workout_log.json` was therefore not needed. **Decision 2 took the answer this entry named**: `ui_cards.meal_card`'s icon row as a *sibling* of the clickable body — which meant moving `today_card`'s click handler off the card element onto a body element, or a mark click would have bubbled through and opened the recipe dialog on top of the mark it just recorded. Three statuses rather than a boolean, because a skipped meal and a swapped one fail differently and the chart this feeds could not otherwise tell a missed dinner from a dinner out. All slate, glyph-distinguished, per the palette rule v0.32.0 established — emerald is the cook status, so a green tick would read as a fifth slot state. New `ui_adherence.py` and a new `"adherence"` refresh topic; the day inspector got it free, sharing `today_card`/`context_strip`. Marks persist on click and deliberately do not stage, and nothing in the generation path reads them: what to do with a run of skipped Thursdays is a product question, not a fourth soft prompt rule. Writes land in `data/`, so the two-writers-to-`config/` rule is untouched. `tests/test_adherence.py`, 45 tests over all three layers |
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
Nothing else in that register is open. `ui-redesign.md` has one entry left
in the queue above — finding 3, the trend charts — and no unfiled asides:
v0.33.0 shipped the last of those.
