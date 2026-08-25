# UI redesign — five phases

Work that is **scoped and unblocked**, which is what separates this file from
`future-ideas.md`: everything there waits on a product decision only the
maintainer can make, or on weeks of runtime data. Nothing in phases 1–4 below
waits on anything. Phase 5 does, and says so.

Each phase is shippable on its own and leaves the app working. They are
ordered so that value lands before risk — phase 1 changes how every screen
reads without moving a single element, and the structural change doesn't
happen until phase 3.

**The visual proposal these phases came from is a Claude artifact**, not in
this repo — the same arrangement `future-ideas.md` records for 5c's mockup,
and the same caveat applies: ask for it to be republished if the link has
gone stale. This file is the source of truth. The artifact is a review
surface.

---

## The diagnosis, in short

Six findings. Only the last two are about looks.

| # | Finding | Cost |
|---|---|---|
| 1 | `ui_drawer.py` holds five unrelated kinds of work — actions, run parameters, plan inputs, a content library, and a readout — in one 320px column | Root cause |
| 2 | The staged-changes model (`PlannerState` → `planning_config()`, never written to disk) is communicated by "Applies to the next generation only" in 10px grey, three times | Root cause |
| 3 | 28 days of `meal_history.json`, a weigh-in series, logged intake and an adaptive-TDEE reconciliation surface as one plant count and four counters | High |
| 4 | Three of four `PIPELINE_STAGES` are `connected=False`, so 21 permanently dashed chips sit above the telemetry | High |
| 5 | Nine font sizes between 8px and 14px — noise, not hierarchy | Polish |
| 6 | Model picker, raw exception text, "Shuffle styles", "Reload from disk" are developer controls in the user's first screen | Polish |

**Finding 1 is the one everything else hangs off.** The drawer isn't badly
built; it is being asked to hold things that have nothing in common except
that they were all "global". They want different frequencies, different
widths and different places on screen.

---

## The target shape

Five destinations on a slim icon rail, each owning the full canvas, replacing
the drawer entirely:

| Destination | Holds | Exists today as |
|---|---|---|
| **Plan** | the week grid, the generation flow | `ui_cards.canvas` + the Week tab |
| **Today** | one day's cards, its context strip, the day picker | `ui_today.py`, already a tab |
| **Library** | the recipe catalog, filters, all three import paths | `ui_catalog_browser.py`, trapped in a dialog |
| **Insights** | trends — see phase 5 | nothing |
| **Settings** | week start, shopping days, model, profile, integrations | the drawer's "Global Controls" |

Three mechanics carry most of the improvement:

1. **The canvas owns its own scroll.** `overflow-x: auto` on the grid plus a
   sticky left gutter carrying `BREAKFAST / LUNCH / DINNER / SNACK`. The page
   stops scrolling sideways, and every card can drop its own meal-type label
   — noise removed from 28 cards at once.
2. **The inspector is contextual and floats.** Click a day's telemetry column
   and it shows that day: targets, training, location, its four slots.
   Editing Wednesday's target beneath Wednesday's bar, instead of finding row
   3 of 7 in a drawer form. It floats over the canvas rather than pushing it,
   so the grid never reflows — which is the whole failure of the current
   `top_corner` drawer.
3. **Staged changes become a real object.** One persistent bar: *"3 pending
   changes — Wed +200 kcal, Sat gym added, 2 pantry items · Review ·
   Generate week"*. That replaces three 10px disclaimers, the amber telemetry
   dot and the "edited — not saved" chip with one honest statement.

---

## Phase 1 — typography and token pass

**Nine font sizes down to four, and a real spacing and radius scale.** The
canonical statement of the scale is `.claude/rules/ui.md`, which auto-loads
whenever a `ui_*.py` file is touched; this phase is the migration to it.

**Scope fence:** `src/ui_theme.py` plus the class strings in `src/ui_*.py`
that reference sizes it now owns. No layout moves. `ui_state.py` is not
touched.

**Acceptance:**

- `grep -ohE "text-\[[0-9]+px\]|text-(xs|sm|base|lg|xl)" src/ui_*.py | sort -u`
  returns at most four values, and they are the four in `.claude/rules/ui.md`.
- Sizes, spacing and radius are named constants in `ui_theme.py`, not
  literals at call sites.
- `python -m unittest discover -s tests` passes.
- The app starts and the Week tab renders: `./scripts/server.sh start`.

**Why first:** it changes how everything reads, touches no structure, and
carries no risk of a regression a test wouldn't catch. It is also the phase
that makes every later phase cheaper, because the tokens exist before the new
surfaces are built against them.

**One thing this phase will surface and must not paper over:** amber
currently means five different things (near-target in `BAND_COLOURS`, carbs
in `MACRO_TINTS`, training in `TRAINING_ACCENT`, a target override in the
telemetry marker, and fridge storage in `PREP_BADGE_STYLES`), and violet
means two (fat, and location). Record the collisions in `ui_theme.py` where
they are; resolving them is a design decision for phase 3, when the surfaces
that use them are being rebuilt anyway.

---

## Phase 2 — the canvas owns its scroll

Split in two, because the fix for the reported problem is small and the
meal-type gutter it was bundled with is not. **2a is what actually stops the
sideways scrolling** and is worth shipping alone.

### 2a — overflow container and an overlaying drawer

Wrap the canvas and the telemetry header in a shared `overflow-x: auto`
container with a `min-width`, so the grid scrolls inside itself instead of
widening the page. Switch `ui.left_drawer` from pushing to overlaying.

**Scope fence:** `src/ui_cards.py`'s `canvas`, `src/ui_telemetry.py`'s
`telemetry`/`context_pipeline`, `src/ui_drawer.py`'s `ui.left_drawer` call,
and `src/ui_app.py`'s page CSS. The drawer's *contents* do not change — only
how it sits over the page.

**Acceptance:** the document body never scrolls horizontally at any viewport
width; the grid does, inside itself; opening the drawer does not reflow the
grid; tests pass.

**Read `top_corner=True`'s comment in `ui_drawer.py` before touching it.** It
exists so Quasar insets the fixed header by the drawer's width, keeping the
header's `grid-cols-8` columns aligned with the canvas's. An overlaying
drawer removes the reason for it — but the header and canvas must then share
one scroll container, or they will disagree about x-offset the moment the
grid scrolls. That shared container is the real work in 2a.

### 2b — real grid rows and a meal-type gutter

**The canvas has no shared rows today.** Each day is a `flex flex-col` of
four cards inside `grid-cols-8`, laid out with `items-start`, so a card with
a long title or a link line makes its column taller and Tuesday's dinner
stops sitting level with Wednesday's. They align by luck, not by structure.

Restructure to a real grid — a gutter column plus prep plus seven days,
against a header row plus one row per meal type — with the prep column
spanning all four meal rows. Then add the sticky `BREAKFAST / LUNCH / DINNER
/ SNACK` gutter and drop the per-card meal-type label, which removes noise
from 28 cards at once.

**Acceptance:** every day's dinner sits on one baseline regardless of card
height; the gutter stays fixed while the grid scrolls under it; the prep
column still spans the full height; tests pass.

**Why 2b is not queue-safe:** it changes how every card is placed, and its
acceptance is visual. Run it interactively.

**Why phase 2 is second overall:** 2a fixes the actual reported problem, and
both halves are independent of the rail — worth having whether or not phase 3
ever happens.

---

## Phase 3 — the rail and the staged-changes bar

**The structural change.** Five destinations; the drawer stops existing.
Today and Library are promoted rather than written. Settings absorbs Global
Controls.

**Run this one interactively, in plan mode — not through
`scripts/claude-queue.sh`.** It carries a dozen judgment calls an hour and
`--dangerously-skip-permissions` on an unattended queue is the wrong tool for
that.

**Maintainer decisions that belong here, not to whoever builds it:**

1. **Whether "people per meal" is a setting or a per-run option.** It
   genuinely varies week to week, which argues for the generation options
   popup beside the cuisine and prep toggles rather than Settings. But
   `PlannerState.spec` deliberately ignores it once a week exists (see
   `_shape()`), and `generation_spec()` reapplies it — so moving it changes
   which of those two paths is the honest one.
2. **What happens to the three unconnected `PIPELINE_STAGES`.** Wire them,
   remove them, or move them to Settings as an integrations status list.
   Leaving 21 dashed chips above the telemetry is the one option that is
   clearly wrong.
3. **What "Reload from disk" is called** once it sits in a UI with a staged
   changes bar. It is "discard pending changes", and the bar is where it
   belongs.

**Acceptance:** `ui_drawer.py` is deleted or reduced to the generation
trigger; each destination is its own `build_*(ctx)` module returning
refreshables, per the existing pattern in `ui_app.py`'s build order; the
refresh-topic registration still names every section.

---

## Phase 4 — inspector, target curve, rejection capture

Three things, in that order. The first two are UI; the third is not, and is
the most valuable item in this file.

**The day inspector.** Contextual panel, floating, driven by a selected day.
Cheap because the panel is already day-parameterised — `ui_today`'s
`today_view` proved this: it had exactly one line deciding the day, and
`targets_for` / `totals_for` / `day_context` / `slot_id` all already take a
day argument.

**Targets as a curve.** 21 spinboxes become one draggable shape: filled bar
for the base, a second segment for the training uplift, a dashed ghost at the
config value where a day is overridden. Carb cycling, currently invisible in
a form, becomes the thing you see. `day_target_row`'s build-once-and-mutate
workaround (which exists so a refresh doesn't steal focus from the number
being typed) goes away with the form.

**Training burn should be derived, not typed.** From type, duration and
current weight. Asking the user for a kcal figure is asking them to do the
app's arithmetic, and nobody knows the answer. Once derived, the schedule
itself can be *proposed* from Garmin — `GarminSyncService` already syncs
activity history, so the recurring weekly pattern is detectable, and a
confirmation beats a data-entry form.

### Rejection capture

**Hitting regenerate on a meal card is the strongest possible signal that a
suggestion was wrong, and the app learns nothing from it** — the recipe is
discarded and an identically-briefed call is made. Favourites already capture
the positive signal; there is no negative one.

Add three taps at that moment (*too much prep · don't fancy it · had it
recently · wrong for this slot*), aggregate into a standing preference list,
and send it in `build_generation_rules` beside `banned_ingredients` and the
diet-style principles.

**This is a distinct signal from `future-ideas.md`'s 5b**, and the two must
not share a file: 5b's `AdherenceEntry` logs whether the plan was *eaten*;
this logs why a suggestion was *refused before it ever became the plan*. Same
reasoning that keeps `weigh_ins` and `daily_actuals` as separate upsert
targets — a manual mark and a different signal writing the same key overwrite
each other with no way to tell which won.

**Maintainer decision:** whether the preference list decays. A dislike
recorded once and honoured forever will empty the rotation the same way a
"unused in the last N" rule starves the tail of a list (see
`planner.next_choice`'s note on why it is strict LRU). A decay window is
probably right; what it should be is a product call.

---

## Phase 5 — API extraction, then Insights

**Insights is `future-ideas.md`'s 5c and is tracked there, not here.** It is
blocked on data, not engineering, and that assessment still holds: as of
2026-08-26 `biometrics.json` carries three weigh-ins and one `daily_actuals`
row, and `calculate_adaptive_tdee` returns `None` — keep using the formula —
below two weigh-ins spanning `MIN_TREND_SPAN_DAYS`. A chart built now would
be near-empty or actively misleading. 5c already names its charts, its
sources and its library (`ui.echart`, bundled with NiceGUI 3.16, no new
dependency).

What belongs *here* is the thing 5c would be the first consumer of: **an API
boundary.** NiceGUI already runs on FastAPI, so routes mount in the same
process — no new deployment, no second server. `PlanRepository`'s docstring
already anticipates this ("point the app at a backend by constructing a
different subclass").

**Why Insights is the right first consumer:** it is all read and no write, so
it exercises the boundary without risking the generation path.

### On React

The components are the easy part; the boundary is not. `PlannerState` is
1,329 lines of view model living as a per-client Python object that calls
`split_targets`, `portions_for` and `planning_config()` synchronously — none
of it reachable from a browser. A React front end means exposing all of that
over HTTP or reimplementing it in TypeScript, and the second is how the UI
and the planner quietly start disagreeing about what a portion is.

| Concern | NiceGUI today | React + API |
|---|---|---|
| Rail, inspector, staged bar | Quasar does all three | no advantage |
| Trend charts | `ui.echart`, adequate | clearly ahead |
| Focus theft on re-render | worked around per widget | removed as a class |
| Mobile / kitchen use | a downloaded static file | a real client |
| Long generation with progress | free over the existing socket | SSE/WS to build |
| Multi-user, auth, hosting | full state per client in RAM | standard |
| Cost | refactor in place | API layer + rewrite + TS types |

**The tell is `build_week_menu_html`.** A downloadable static page with
tap-to-strike steps exists because the week was needed on a phone and the app
had no way to give you one. That is a real requirement being routed around,
and it is the strongest argument that the front end eventually wants to be a
client rather than a server-rendered desktop page.

**Recommendation: extract the boundary now, adopt React only if this ships to
other people.** The current problems are 80% information architecture — a
React rewrite that kept this IA would look better and work the same. Doing
the IA work in NiceGUI first, against an API that already exists and is
already exercised, means React (if it happens) is a front end rather than a
rewrite and an extraction attempted simultaneously.

---

## Order, if built

Phases 1 and 2 suit `scripts/claude-queue.sh` — mechanical, objectively
checkable, safe unattended. Phase 3 onward wants an interactive session in
plan mode. Every phase finishes by updating CLAUDE.md, because a cold session
is only as competent as that file is true.
