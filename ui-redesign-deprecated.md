# UI redesign

Work that is **scoped and unblocked**, which is what separates this file from
`future-ideas.md`: everything there waits on a product decision only the
maintainer can make, or on weeks of runtime data. Nothing below waits on
anything.

Phases 1–5, 2b and 6a–6e have shipped (verified against the running code and
the test suite on 2026-08-26, and 6e again on 2026-08-27, not just against
CLAUDE.md's account of them). **Nothing in this file is open.** What is left
of the original review is finding 3 — Insights — which is `future-ideas.md`'s
5c and blocked on weeks of runtime data rather than on engineering, so it was
never this file's to do. Once a phase ships, this file stops being the source
of truth for it — CLAUDE.md's "NiceGUI front end" section is, and repeating
its detail here would just be a second copy able to go stale — so each
shipped phase below is a pointer, not a rebuild of the original writeup.

**The visual proposal these phases came from is a Claude artifact**, not in
this repo — the same arrangement `future-ideas.md` records for 5c's mockup,
and the same caveat applies: ask for it to be republished if the link has
gone stale.

---

## The diagnosis, in short

Six findings from the original review. Only the last two were about looks,
and the table is historical — kept because it's still the reason phases 1–4
exist — not a live checklist.

| # | Finding | Cost | Resolved by |
|---|---|---|---|
| 1 | `ui_drawer.py` holds five unrelated kinds of work — actions, run parameters, plan inputs, a content library, and a readout — in one 320px column | Root cause | Phase 3 |
| 2 | The staged-changes model (`PlannerState` → `planning_config()`, never written to disk) is communicated by "Applies to the next generation only" in 10px grey, three times | Root cause | Phase 3 |
| 3 | 28 days of `meal_history.json`, a weigh-in series, logged intake and an adaptive-TDEE reconciliation surface as one plant count and four counters | High | Open — this is Insights, `future-ideas.md`'s 5c |
| 4 | Three of four `PIPELINE_STAGES` are `connected=False`, so 21 permanently dashed chips sit above the telemetry | High | Phase 3 (moved to Settings) |
| 5 | Nine font sizes between 8px and 14px — noise, not hierarchy | Polish | Phase 1 |
| 6 | Model picker, raw exception text, "Shuffle styles", "Reload from disk" are developer controls in the user's first screen | Polish | Phase 3 (partly); phase 6b finished it — every control is in the rail, the `model:` readout is gone |

**Finding 1 is the one everything else hung off**, and phase 3 is what
resolved it: the drawer wasn't badly built, it was being asked to hold
things that had nothing in common except that they were all "global."

---

## The target shape

Five destinations on a slim icon rail, each owning the full canvas — this
shipped in phase 3 and is the current layout, not a proposal:

| Destination | Holds | Built as |
|---|---|---|
| **Plan** | the week grid, and the failure list in front of it | `ui_plan.py` + `ui_cards.py`'s canvas |
| **Today** | one day's cards, its context strip, the day picker | `ui_today.py` |
| **Library** | the recipe catalog, filters, all three import paths | `ui_catalog_browser.py` |
| **Insights** | trends — blocked on data, see phase 5 below | `ui_insights.py`, an honest empty state |
| **Settings** | week start, shopping days, model, integrations status | `ui_settings.py` |

Beneath the first two tabs, the rail also carries the **action block** phase
6b collected — Generate, Shopping, Shuffle styles, PDF menu, Mobile page.
Destinations answer "what am I looking at"; these answer "what do I want to
do". Nothing else on the page is clickable chrome except the week selector
(scope, not action) and the staged-changes bar.

---

## Shipped

- **Phase 1 — typography and token pass.** Four text sizes
  (`TEXT_MICRO`/`TEXT_BODY`/`TEXT_HEAD`/`TEXT_DISPLAY`), five spacing steps,
  three radii — all named constants in `ui_theme.py`, used at every call
  site. Verified directly:
  `grep -ohE "text-\[[0-9]+px\]|text-(xs|sm|base|lg|xl)" src/ui_*.py | sort -u`
  returns exactly `text-[10px]`, `text-xs`, `text-sm`, `text-lg` — the four
  values `.claude/rules/ui.md` names as the scale. `python -m unittest
  discover -s tests` passes (646 tests). The amber/violet colour collisions
  this phase recorded rather than resolved are still recorded, not fixed —
  see `.claude/rules/ui.md`'s "Known collisions."

- **Phase 2a — overflow container.** Superseded rather than merely done: the
  drawer this was written against (switch `ui.left_drawer` from pushing to
  overlaying) no longer exists — phase 3 removed the drawer outright, which
  is a stronger fix than the one originally proposed. What survives is the
  actual goal: the telemetry header and the canvas each scroll inside their
  own `overflow-x: auto` wrapper (`ui_theme.week_grid_scroll()`,
  `WEEK_GRID_SCROLL_CLASS`), kept in sync client-side by a `scroll` listener.
  See CLAUDE.md's "NiceGUI front end" section, the paragraph beginning
  "Phase 2a's fix targeted a problem phase 3 later made structurally
  impossible to reintroduce."

- **Phase 3 — the rail and the staged-changes bar.** `src/ui_drawer.py` is
  gone from the repo. Five destinations, each its own `build_*(ctx)` module
  (`ui_plan.py`, `ui_today.py`, `ui_catalog_browser.py`, `ui_insights.py`,
  `ui_settings.py`), plus `ui_staged_bar.py` as the persistent pending-
  changes strip. All three maintainer decisions this phase called for were
  made and are visible in the code: people-per-meal stayed a per-run option
  in the review dialog; the three unconnected `PIPELINE_STAGES` moved to
  Settings as a plain connected/not-connected list (`ui_settings.py`'s own
  docstring names this as "a decision made for phase 3"); "Reload from disk"
  is now "Discard pending changes" on the staged bar.

- **Phase 4 — inspector, target curve, rejection capture.** All three
  shipped. `ui_inspector.py` is the floating, day-parameterised panel.
  `ui_review.py`'s `targets_editor`/`day_target_row` render the bar-per-day
  target curve — its own docstring calls itself out by name: "the 'target
  curve' of `ui-redesign.md`'s phase 4.2, replacing the old 21-spinbox stack
  of per-day panels." `ui_generation.py`'s `offer_rejection_prompt` /
  `record_rejection` capture a regenerate-icon or favourite-swap discard into
  `data/rejections.json` via `RejectionEntry`. Training burn is derived
  (`nutrition_engine.estimate_session_burn_kcal`) and editable via a
  calculator-icon button, not typed from a blank field.

  **One aside from this phase's own text is still open, and isn't tracked
  anywhere else yet:** "the schedule itself can be proposed from Garmin."
  CLAUDE.md confirms this was "deliberately left for its own change," but it
  has no entry in `future-ideas.md` — it's a loose thread in an otherwise
  finished phase. Worth filing there rather than leaving it here.

- **Phase 5 — API extraction.** `src/api.py` exists and mounts read-only
  routes (`/api/weeks/*`, `/api/recipes`, `/api/history`, `/api/biometrics`,
  `/api/targets`) directly onto NiceGUI's own FastAPI app — see CLAUDE.md's
  "The API boundary." Insights, this phase's other stated half, was already
  correctly filed under `future-ideas.md`'s 5c rather than in this file, and
  still is — nothing to move. It remains blocked on data, not engineering: as
  of 2026-08-26 there still isn't enough biometric history for
  `calculate_adaptive_tdee` to return anything but "keep using the formula."

- **Phase 2b — real grid rows and a meal-type gutter.** `ui_cards.canvas()`
  is a genuine 9-column x N-row CSS grid now (gutter, prep, 7 days x header
  row + one row per meal type), every cell placed by explicit
  `grid-column`/`grid-row`, not 7 independent `flex flex-col` stacks — see
  CLAUDE.md's "NiceGUI front end" for the placement scheme and why
  `WEEK_GRID_COLS` grew a leading gutter track that `ui_telemetry.py`'s
  header row also carries (empty) so the two grids stay aligned. The sticky
  `BREAKFAST`/`LUNCH`/`DINNER`/`SNACK` gutter replaced the per-card
  `meal_card` label. Shipped alongside a real, pre-existing bug this phase's
  own acceptance criteria exposed: `ui_plan.panel()`'s wrapper had no
  `min-w-0`, so the Quasar flex ancestor it sits in never let it shrink below
  its content's natural width — the canvas's own `overflow-x: auto` never
  actually overflowed, and a different, outer Quasar container scrolled the
  whole panel instead. Fixed in the same change; without it the gutter's
  `sticky` positioning had nothing to stick against at any viewport narrow
  enough to need it.

- **Phase 6a — one day identity per column, with its date.**
  `ui_cards.canvas()`'s swim-lane header no longer prints the day name; it
  keeps only what is its own (the day-regenerate icon, the 1-indexed
  position). `ui_telemetry.telemetry()`'s day cell is now the single place a
  day is named, and it carries the date —
  `format_day_label(day, state.day_date_iso(day), short=True).upper()`, so a
  cell reads "MON 24 AUG" where a date is known and "MON" for a plan
  generated before `week_start_date` existed. The pair of day-name and
  kcal figure wraps to two lines at ordinary laptop widths rather than
  overflowing the column; `truncate` on the label and `shrink-0` on the
  figure settle the narrower case in the date's disfavour, never the
  number's. 646 tests still pass, unchanged.

  **Shipped alongside the alignment this finding assumed and did not have.**
  6a's reasoning was that the header cell "three rows up" already says which
  day a column is — but phase 3 put the rail to the left of the destination
  panels and only the canvas is inside one, so the two grids had drifted a
  full column apart: measured at 1440px, the header's grid started at x=12
  with 159px day columns and the canvas's at x=192 with 135px ones, which
  put every day's telemetry above its *neighbour's* meals. Deleting the
  canvas's day label on top of that would have made the grid actively
  misleading rather than merely repetitive, so the header's
  `week_grid_scroll()` now takes `inset=True`
  (`ui_theme.WEEK_GRID_HEADER_INSET_STYLE`: past the rail, plus the
  destination panel's own padding) and the rail is pinned to
  `RAIL_WIDTH_CLASS` — it used to size to its widest tab, and the Daily View
  tab's label is whichever day is being browsed, so the canvas shifted
  sideways as you stepped through the week. Both regions now measure
  identical left edges, widths and column tracks, which also repairs
  phase 2a's scroll sync: at 1000px the header's wrapper was 1024px wide
  with nothing to overflow while the canvas scrolled beneath it, and the
  mirrored `scrollLeft` moved one grid out from under the other. They now
  scroll in lockstep, verified by measuring both wrappers.

- **Phase 6b — one place a click starts something.** The rail now holds
  every control the app has, and the two headers above it hold none. What
  moved, and out of what:

  | control | was | now |
  |---|---|---|
  | Generate | `ui_plan.week_summary` | rail action block |
  | Shuffle styles | `ui_plan.week_summary` | rail action block |
  | Shopping list | `ui.header()` | rail action block |
  | PDF menu | `ui.header()` | rail action block |
  | Mobile page | `ui.header()` | rail action block |
  | `model: <id>` readout | `ui.header()` | deleted — the select is in Settings and the progress dialog names the model mid-run |
  | Cook sessions / days cooking / portions / shopping trips | `ui_plan.week_summary` | `ui_telemetry.week_banner`, beside the week dates and plant count |

  **The audit is what produced that table**, per this finding's own
  instruction not to move anything mechanically. Three questions decided
  every row: does it *start* something (rail) or *report* something (the
  header's reporting strip); is it global or scoped to one destination; and
  does it already have a home elsewhere. Only two controls survived the
  audit outside the rail, both correctly: the week selector in `ui.header()`
  (it changes what all five destinations show — scope, not action) and the
  staged-changes bar's Review / Generate week / Discard, which exist
  precisely to act on what is staged and are already one strip rather than
  scattered chrome.

- **Phase 6c — the Sunday-prep column's cards act.** Each dish the session
  batches is now a card in `ui_cards.prep_candidate_card`: its body opens the
  same `open_detail` recipe dialog every other card in the app shares, and a
  sibling icon row above it carries the same `open_swap_modal` and
  `generation.regenerate_meal` a day card gets. No new capability, no new
  dialog and no new plumbing — `prep_day_column` is a closure inside
  `build_cards` and was already holding every handle involved, so the
  `CardHandles` threading this finding proposed turned out to be unnecessary.
  The one argument it did grow is `views`, the `slot_views()` dict `canvas()`
  had already built for the day cards.

  **It resolves dishes through `candidate_slot_ids`, not `meals_included`.**
  The slot ids are what Python actually folded into the session (see
  CLAUDE.md's "Batch cooking on purpose"), and they are the only handle a
  click can act on; the string list is the model's own prose, frozen at
  generation. A session with an empty or unresolvable id list falls back to
  exactly the inert bullets it rendered before, the same pre-migration
  tolerance `is_sunday_prepped` extends to that identical field. The
  regenerate icon's tooltip names its cost — `regenerate_single_meal` drops
  `sunday_prep_session` outright when it re-cooks a prep candidate, so from
  this column the thing it clears is this column.

  **Shipped alongside a latent layout bug that this phase's own added height
  is what exposed** — the same shape as 2b's `min-w-0` and 6a's grid
  misalignment, and the reason this file told a queued 6c to measure rather
  than trust. `prep_day_column`'s out-of-flow child is `flex flex-col`, and
  Quasar's `.flex` sets `flex-wrap: wrap`, which `flex-col` does not undo: a
  wrapping column container does not overflow when its content outgrows its
  box, it starts a **second column beside the first**, so the
  `overflow-y: auto` that phase 2b put there had never once been able to
  fire. Measured at 1440px with the batching cards added, the last prep phase
  laid itself out at x=423 — inside the Monday day column, 143px clear of the
  cell's own 135px track and on top of Monday's cards, invisible only because
  the cell is `absolute`. `flex-nowrap` fixes it, and the same trap has a
  second form one level down, on each batching card's own `flex flex-col`,
  where a stretched child sized to the widest sibling's max-content instead
  of the card's width and the eyebrow's `truncate` therefore never engaged.
  Both are now recorded in `.claude/rules/ui.md` beside the row-direction
  form of the same trap.

  Verified in the running app against a synthetic week with both batch
  anchors and a shake candidate: three cards render, body click opens the
  right recipe, the swap icon opens "Swap Monday dinner" and does *not* also
  open the recipe dialog (the sibling split holds), the regenerate tooltip is
  wired, no console errors, no document-level horizontal overflow at 1440px,
  1280px or 1000px, and all nine children of the prep column measure inside
  its own track. The pre-migration fallback was exercised separately and
  renders the old bullets. 646 tests still pass, unchanged.

  **This goes further than the finding proposed, on the maintainer's call.**
  6b suggested print/mobile move into the Plan destination's own header and
  shopping stay global. `ISSUES.md`'s item 2 is the ask behind it and asks
  for something stronger — *"Can all buttons/menus start from left panel? …
  it's hard to figure out where the app 'controls' are"* — and its item 1
  independently calls the Plan header row too heavy, so relocating controls
  *into* that row would have traded one crowded strip for another. The rail
  keeps shopping global (6b's one hard constraint: the drawer is read
  against the grid, and Daily View and the inspector show slots worth
  shopping against too) while still being the single place a click starts
  something.

  **Generate moving out of Plan does not reopen what Plan's own button was
  for.** That button existed because the staged-changes bar hides whenever
  `pending_changes()` is empty — a fresh page load — so "how do I generate a
  week" must never have no answer on screen. A rail button satisfies that
  strictly better: it is visible from all five destinations, not only from
  Plan. `ui_plan.py` is down to the failure list, its `review` parameter is
  gone, and `PlanHandles.week_summary` is now `week_failures`.

  **The action buttons are ordinary children of `ui.tabs()`, between the two
  groups of tabs** — Plan/Daily View above, Library/Insights/Settings below,
  which is where `ISSUES.md` asked for them. Quasar's QTabs puts its default
  slot in a flex `.q-tabs__content` (a column in `vertical` mode) and only
  registers real QTab children with its model, so a plain div sits in the
  flow without joining the selection. That is what avoids a second
  `ui.tabs()` element with a second `ui.tab_panels` value to keep in sync.
  Verified in a browser: clicking each of the five actions fires its handler
  (both downloads arrive, the drawer opens, the review dialog opens, shuffle
  notifies) and leaves `.q-tab--active` on Plan throughout.

  **`RAIL_WIDTH_PX` is a hard constraint on this block, not a suggestion.**
  The header's copy of the week grid is inset by exactly that many pixels
  (phase 6a, above) to sit over the canvas, so a button wide enough to grow
  the rail would slide every day's telemetry off its column. Hence
  `TEXT_MICRO`, `align=left` and `w-full` on every button, and
  "Shopping (108)" rather than the header's old "Shopping list (108 items)".
  Measured after the change: the rail is still exactly 168px at 1440px and
  at 1100px, both grid wrappers still report an identical x/width/track set,
  and scrolling the canvas at 1100px still mirrors the header 1:1.

  646 tests still pass, unchanged — nothing here touches `ui_state.py`.

- **Phase 6e — three Settings read views.** The integrations list's rows are
  doors now: Biometric Sync, Calendar/Location and Adaptive Workout each open
  a read-only `ui.dialog` over data the app already reads on every generation
  and showed nowhere. Detail in CLAUDE.md's "Settings' three read views";
  what is worth keeping here is what the phase found rather than what it
  built.

  **Dialogs, not more panel sections** — the maintainer's call, asked before
  building, per this file's own note that 6e "wants the same treatment" 6b
  got. `ISSUES.md` item 8 asks for a "popup/page" for each of the three, the
  rail is deliberately five destinations, and three tables stacked under the
  panel's three selects would bury the selects.

  **Two `PIPELINE_STAGES` rows had been lying for months, and only a dialog
  exposed it.** `sync` read "Health Connect Sync — Garmin sleep/Body Battery
  — not built yet" while the Garmin/Cronometer sync that actually shipped had
  been writing `biometrics.json` since; `context` read "Calendar/Location —
  not built yet" while `week.apply_location_modes` had been reading
  `base_schedule`/`location_rules` on every generation. Both rendered "Not
  connected" beside a row that, once clicked, listed four real weigh-ins.
  That is the fourth phase in a row (6a, 6c, 6d, now 6e) where the mechanical
  change was fine and something in its stated premise wasn't — here, the
  premise that the list around the new pages was accurate.

  **Two clock-dependent tests in `test_ui_state.py` were failing on the day
  this was built**, both pre-existing and neither caused by this phase — but
  found by it, because the date rolled from Wednesday to Thursday mid-session
  and the suite went from 646 passing to one failure with nothing touched.
  `test_browsing_away_from_today_stops_being_today` stepped three days from
  `days[0]`, which is Thursday; and
  `test_covering_today_is_about_the_columns_not_the_span` asserted against
  `elsewhere[0]` where `week_days` *rotates* the grid, so it disagreed on
  exactly the two weekdays (Friday, Saturday) where Monday lands inside its
  three-day window. Both now measure from today and from the grid's own first
  column, and the module was re-run under all seven frozen weekdays. CLAUDE.md
  claims this suite touches neither network, model nor clock; it does now.

  **Verified in a browser at 1440px and 1000px**: all three dialogs open from
  their rows, size to 576px, produce no document-level horizontal scroll and
  no console errors; the sync strip's three states are distinguishable by fill
  and outline; the copied CLI command carries real `--` hyphens. Two copy bugs
  were found that way and only that way — a tooltip reading "Fri 14 Aug —
  checked — nothing recorded" (two dashes, one thought too many; `phrase` and
  `count` are separate labels now) and a location row reading "not planned"
  directly beside "eaten out, ~795 kcal", which are two clauses contradicting
  each other about the same meal.

  656 tests pass (646 before, plus ten pinning `sync_status`'s three-way split
  and `location_view`).

---

## Phase 6 — post-ship findings from real use

**All five have shipped** (6a–6d on 2026-08-26, 6e on 2026-08-27); each
heading below points at its entry under "Shipped" and then keeps the original
problem statement, because what a finding got wrong about the code is the
part worth re-reading. They were five findings from actually using the
shipped rail/canvas/header, recorded 2026-08-26 — none of them engineering
unknowns, each either a mechanical fix or an IA call only the maintainer
could make, and each said which it was.

The record of where they stood when written: **checked against the current
code on 2026-08-26, none of 6a–6e had been built at the time of writing.**

### 6a — The day header and the swim-lane header say the same thing twice

**Shipped 2026-08-26** — see the entry under "Shipped" above, including the
header/canvas grid misalignment this finding assumed away and which had to be
fixed with it. The record of the problem, as first written:

Still present. `ui_telemetry.py`'s `telemetry()` prints `day[:3].upper()`
("MON") as the first line of each day's macro column (line ~137).
`ui_cards.py`'s `canvas()` prints `ui.label(day)` — the full name
("Monday") — as the first line of the swim lane directly beneath it (line
~933), alongside that day's regenerate icon and its 1-indexed position. Both
rows exist for a real reason (the header row is the inspector's click
target; the canvas row hosts the day-regenerate icon), but neither needs to
*repeat the identity* the other already established — this is the same
"noise, not hierarchy" diagnosis phase 1 made about font sizes, one level up.

Fix: the canvas row keeps the regenerate icon and the day index, but drops
its own day-name label — the header cell three rows up already said which
day this is, and the shared `overflow-x: auto` scroll container (phase 2a,
shipped) keeps the two vertically aligned at all viewport widths, so "which
column am I in" is never ambiguous even while scrolled.

**Folds in the date request for free.** `PlannerState.day_date_iso` and
`ui_theme.format_day_label(day, iso, short=...)` already exist — built for
the Today tab's day picker — and neither is wired into `telemetry()` or
`canvas()` today; both print the bare weekday name. Once the canvas label is
removed, the header cell is the one place a date has to render, so change
`ui.label(day[:3].upper() + marker)` to
`ui.label(format_day_label(day, state.day_date_iso(day), short=True) + marker)`.
`format_day_label` already degrades to the bare name when a plan has no
`week_start_date` (see CLAUDE.md's "A plan with no `week_start_date` shows
the bare weekday name") — the same tolerance the Today tab picker relies on,
so an old cached plan renders exactly as it does today.

**Acceptance:** no day identity string appears twice between the header and
its canvas column; every day cell reads e.g. "MON 2 JUL" where a date is
known and "MON" where it isn't; `test_ui_state.py` still passes unchanged
(this touches `ui_telemetry.py`/`ui_cards.py` only, not the view model).

### 6b — The header still holds output controls the rail was supposed to absorb

**Shipped 2026-08-26** — see the entry under "Shipped" above. It went
further than this finding proposed (every control to the rail, not
print/mobile to the Plan header), on the maintainer's call against
`ISSUES.md` item 2; the reasoning is in that entry. The record of the
problem, as first written:

Phase 3's whole premise was "five destinations on a rail, replacing the
drawer" — but `ui.header()` in `ui_app.py` still carries the print-PDF
button, the mobile-export button and the shopping-cart toggle directly in
the bar above the telemetry grid, alongside the staged-changes bar phase 3
already put there. (A duplicate header "Generate" button was the original
version of this finding; checked directly against `ui_app.py` on
2026-08-26 and it's not there — no `ui.button` for generation exists in the
header today, only these three export/shopping controls. So this finding is
narrower than first recorded: Generate needs no decision, only the three
below do.) That's still the header competing with itself for the same strip
of pixels the day columns need — finding 6 from the original diagnosis
("developer controls in the user's first screen") recurring for a different
set of controls.

**Maintainer decision, not an engineering one — where each one actually
belongs:**

- **Print PDF / mobile export** are outputs of a *generated week*, not
  controls over the app — closer in kind to the shopping-cart toggle than to
  a generation control. A natural home is the Plan destination's own header
  (next to its "This week" stat block), or a small overflow menu there,
  rather than the global chrome.
- **Shopping cart** opens a drawer that reads *against* the grid (CLAUDE.md:
  "the list is read against the grid, and a modal would cover the week it
  describes") — that argues for keeping it global rather than moving it into
  Plan specifically, since Today and the inspector also show slots someone
  might want to shop against.

**Do this as a real IA review, not a mechanical move** — audit every control
left in `ui.header()`/`ui_app.py` against the five destinations and Settings,
the same way phase 3's own diagnosis table did, before relocating anything.

### 6c — The Sunday-prep column's cards are inert

**Shipped 2026-08-26** — see the entry under "Shipped" above, including the
latent `flex-wrap` bug this finding's added height exposed and which had to
be fixed with it, and the `CardHandles` threading it proposed that turned
out to be unnecessary. The record of the problem, as first written:

Still present. `ui_cards.py`'s `prep_day_column()` (the eighth, indigo
column left of day 0) renders `session.meals_included` as plain `ui.label`
text — no click handler, no regenerate icon, no swap button, confirmed by
reading the function on 2026-08-26. That reads as "batch-cooked meals can't
be swapped or regenerated," but they can: the anchor recipe itself sits on
an ordinary `MODE_COOK` slot at day 1 (CLAUDE.md: "the anchor day is
therefore always day 1"), and that slot's real card, in the normal weekly
grid, already has the same `swap_horiz`/`refresh` icons every other cook
card gets (`ui_cards.py`'s `meal_card`, gated only on `view.mode ==
MODE_COOK`) — `regenerate_single_meal` already re-derives the correct batch
size from `portions_for(spec)` on a retry, and `swap_slot_with_favorite`
already calls `scale_to_servings` for exactly this case (`ui_state.py`, both
confirmed by reading, not assumed).

So this isn't a missing capability, it's a missing second entry point: the
column that exists specifically to show "what's cooking for the week ahead"
is the one place you can't act on any of it, or even open a recipe to check
it, without scrolling right to find day 1's own card.

Fix: thread `CardHandles` (`open_detail`, and the same regenerate/swap
callables `meal_card` already closes over) into `prep_day_column`, and make
each line in `session.meals_included` — or better, the actual cook events
the session covers — open the same recipe-detail dialog on click, with the
same swap/regenerate icons beside it. No new capability to build, no new
dialog: this is wiring the column up to handles that already exist and are
already passed around `ui_cards.py`.

### 6d — Library cards are only clickable on the title — **shipped**

`ui_catalog_browser.catalog_card` now mirrors `meal_card`'s split: an
icons-only row, then one clickable body holding the title, tags and macros.
See CLAUDE.md's "The expanded recipe card" for the detail.

Two things worth carrying forward, both of which needed the running page
rather than the source. The title had to move *out* of the icon row for the
split to exist at all — this file described that row as an icon row, and it
was a title-plus-icons row — and moving it fixed an unrelated bug nobody had
filed: the row carried the standing Quasar `.flex` wrap trap, so any name
past roughly 200px of max-content pushed all three icons onto a second line.
Wrapping is decided from items' *unshrunk* widths, so the title's existing
`min-w-0` never got a chance to prevent it, and the 92-recipe catalog was
rendering half its cards icons-beside-title and half icons-under-title on
nothing but name length. That is the third phase in a row (6a, 6c, now 6d)
where the mechanical change was fine and its stated premise wasn't.

### 6e — Three Settings surfaces that read data that already exists

**Shipped 2026-08-27** — see the entry under "Shipped" above, including the
two stale `PIPELINE_STAGES` descriptions the dialogs made impossible to leave
standing, and the two clock-dependent tests that turned out to be failing on
the day this was built. The record of the problem, as first written:

Still present. `ui_settings.py` is 112 lines and has exactly two sections —
`integrations_status()` (connected/not-connected only, no dates) and
`panel()` — confirmed by reading the file on 2026-08-26; none of the three
surfaces below exist yet. All three have the same shape: the data already
lives in `config/` or `biometrics.json`, and Settings has nowhere to show
it — none of these need a new sync, a new schema, or a maintainer product
decision, only a read view.

- **Sync status.** `biometrics.json`'s `sync_checkpoints` already names each
  source's last-checked date; diff it against which dates actually have a
  `weigh_ins`/`daily_actuals` row to show which days are missing. Read-only;
  it doesn't trigger a sync.
- **Location / calendar.** `schedule.json`'s `base_schedule` and
  `location_rules` are read today only by `week.apply_location_modes` at
  generation time. A page listing each day's default location and what it
  constrains (reusing `LocationView`/`LOCATION_RESTRICTION_PHRASES`, already
  built for the Today tab) closes that — explicitly labelled as *defaults*,
  with a note that there is no Google Calendar integration yet.
- **Workout schedule.** `training_schedule` is edited only inside
  `ui_review.py` and previewed only in the Today tab. A page listing the
  week's sessions is a pure read view. **Recommendations, proposing sessions
  from Garmin history, or any analytics belong to `future-ideas.md`'s 5b**
  instead — those need an adherence schema this doesn't invent.

---

## Order, if built

6a/6c/6d were the same shape phase 2b was: mechanical, objectively checkable,
safe to queue unattended (`scripts/claude-queue.sh`). All of 6a–6d are done.
Every one of the three mechanical ones needed one unmechanical thing with it —
6a's premise assumed a header/canvas alignment the grids didn't have, 6c's
added height exposed a `flex-wrap` bug that had been silently rendering the
prep column's last item on top of Monday, and 6d's "icon row" turned out to
be a title-plus-icons row whose own wrapping was already inconsistent across
the catalog. All three were found by measuring the running page, not by
reading it, which is now the standing expectation for anything queued out of
this file rather than a caveat on one phase. 6b wanted a short interactive
pass first and got one — the "where does this control go" call turned out to
have an answer in `ISSUES.md` that was stronger than the one this file
proposed, which is exactly why it was not queued. 6e got the same treatment
and needed it: its pages were new surfaces with no existing acceptance
criteria to check against, so the one call worth making — dialogs off the
integrations rows, rather than more sections in the panel or more rail
destinations — was asked before a line was written. Every phase finishes by
updating CLAUDE.md, because a cold session is only as competent as that file
is true.
