# The NiceGUI front end — architecture

The full design record for every `ui_*.py` module: what each surface is, why
it is shaped that way, and which apparently-removable details are load-bearing.
This was CLAUDE.md's "NiceGUI front end" section through "Printing and PDF
export" until it moved here, so a session that never touches the UI no longer
carries it.

`SKILL.md` beside this file is the *contract* — the type/spacing/radius scale,
the colour roles, the traps and the refresh rules — and is the thing to read
first. This file is the reasoning behind the surfaces themselves. Read the
section for the area you are changing; you rarely need all of it.

## NiceGUI front end

`ui_app.py` (`./scripts/server.sh start`, serves on :8080) is the high-density
desktop UI: a header of 7 per-day macro bars, a persistent staged-changes bar
beneath it, and a slim vertical rail choosing one of six destinations — Plan
(the week grid), Today, Library, Insights, Settings — each owning the full
canvas below the bar (see "Module layout" and "Five destinations, one rail"
below). The rail also carries **every control the app has** — see "The rail's
action block" below; phase 6b of `ui-redesign.md` collected them there out of
`ui.header()` and the Plan panel, so a destination reports and the rail acts. The Plan destination's canvas and the header's telemetry row are both
`grid-cols-[minmax(80px,auto)_repeat(8,minmax(110px,1fr))]`
(`ui_theme.WEEK_GRID_COLS`) — a meal-type gutter at index 0 (real content only
in the canvas, see "Real grid rows and a meal-type gutter" below; an empty
spacer cell in the header, purely to keep the two grids' tracks in step),
then an indigo Sunday-prep column at index 1, ahead of the seven days — so a
day's telemetry stays directly above its meals, at rest and while scrolled.
The 110px floor is what makes "while scrolled" possible at all: a viewport
too narrow for nine such columns needs to scroll, but the header
(`position: fixed`, so it stays visible while the canvas scrolls vertically
beneath it) and the canvas (in the page container below it) can never be the
same physical scroll parent. Each gets its own `overflow-x: auto` wrapper
instead — `ui_theme.week_grid_scroll()`, two call sites sharing one class,
`ui_theme.WEEK_GRID_SCROLL_CLASS` — and a `scroll` listener
(`WEEK_GRID_SCROLL_SYNC_JS`) mirrors `scrollLeft` between them entirely
client-side, the same reasoning `chain_css` already gives for staying out of
Python on a per-frame effect. Phase 2a of `ui-redesign.md`.

**"Directly above its meals" is not free, and stopped being true for a
while.** Phase 2a could take it for granted — header and canvas both sat
`px-3` in from the same full-width page — but phase 3 put the rail to the
left of the destination panels, and only the canvas is inside one. Measured
at 1440px before phase 6a fixed it: the header's grid started at x=12 with
159px day columns, the canvas's at x=192 with 135px ones, so every day's
telemetry sat above its *neighbour's* meals and the two drifted further
apart with every column. The header's call is now
`week_grid_scroll(inset=True)`, which applies
`ui_theme.WEEK_GRID_HEADER_INSET_STYLE` — a left margin of the rail's width
plus the destination panel's own padding, and an explicit
`width: calc(100% - ...)` rather than a stretched `w-auto`, because Quasar's
`.flex` makes the header a *wrapping* column flex container and those size a
stretched child to the widest item's max-content, which for this grid is all
nine columns at their 110px floor. The rail is pinned to
`ui_theme.RAIL_WIDTH_CLASS` for the same reason: `ui.tabs()` sizes a vertical
rail to its widest tab, and the Daily View tab's label is whichever day is
being browsed, so an intrinsic width slid the whole canvas sideways as you
stepped through the week. Both wrappers now measure the same left edge, the
same width and the same column tracks — which is also what makes the scroll
sync mean anything: at 1000px the header's wrapper was 1024px wide with
nothing to overflow while the canvas scrolled beneath it, so mirroring
`scrollLeft` moved one grid out from under the other.

**A day is named once, and the name carries its date.** Phase 6a. The
telemetry header's day cell is the only place a day's identity is printed —
`format_day_label(day, state.day_date_iso(day), short=True).upper()`, so a
cell reads "MON 24 AUG", or "MON" for a plan generated before
`week_start_date` existed (the same tolerance the Today tab's picker relies
on). `ui_cards.canvas()`'s swim-lane header row underneath keeps only what is
its own — the day-regenerate icon and the day's 1-indexed position — because
a second copy of the name was repetition rather than hierarchy, the same
diagnosis phase 1 made about font sizes one level up. It is the alignment
above that earns this: the identity has to genuinely be overhead at every
viewport width before the column beneath it can stop repeating it.

**Phase 2a's fix targeted a problem phase 3 later made structurally
impossible to reintroduce.** The left drawer that existed before phase 3
toggled open and closed, and an earlier version inset the fixed header by
the drawer's width (`ui.left_drawer(top_corner=True)`) to keep the header's
and canvas's grid columns aligned whenever it was open — which was also the
bug report: 320px of drawer competing with an 8-column grid for the rest of
the viewport is what widened `.nicegui-content` enough for the *document* to
scroll sideways. `overlay` mode fixed that without removing the toggle.
Phase 3 (below) removed the drawer, and the toggle, outright: the rail is a
fixed-width, always-visible flex sibling of the destination panels, not a
`QDrawer`, so there is no open/close event left to reflow anything.
Cook/leftover/skip/not-generated are four distinct card treatments
(`STATUS_STYLES`).

**Real grid rows and a meal-type gutter.** Phase 2b of `ui-redesign.md`.
Before this, `ui_cards.canvas()` laid each day out as its own `flex flex-col`
of four cards — nothing shared a row, so a card with a long title or a link
line made its whole column taller with no effect on the next day's same meal
type, and same-row cards lined up by luck rather than by structure.
`canvas()` is now a genuine CSS grid: every cell — a day's header, and each
of its meal-type cards — is placed by explicit `grid-column`/`grid-row`
rather than document order, so cells assigned the same row genuinely share
one (the browser sizes the row to its tallest cell), and `items-start` on
the grid keeps every shorter cell flush to that row's top rather than
stretching to fill it. Column 1 is the meal-type gutter — `position: sticky;
left: 0`, so it stays in view while `week_grid_scroll()`'s `overflow-x: auto`
wrapper scrolls the rest of the grid beneath it, with an opaque background
so a scrolled-under card can't show through — column 2 is the prep-day
column (`grid-row: 1 / span N`, spanning the header row plus every meal-type
row, since prep work isn't split by meal type), and columns 3.. are the
seven days. The per-card meal-type label (`meal_card` printing
`meal_type[:5].upper()` on all 28 cards) is gone; the gutter says it once
per row instead.

**The prep column's spanning cell holds no in-flow content, and that empty
wrapper is load-bearing.** A grid item spanning N auto-sized rows still has
to fit, and auto rows size to their items' *max-content*, so the prep
column's natural height (its "Batching for" box plus one `ui.expansion` per
prep phase — measured at ~1420px against ~750px of actual cards) was
inflating all five rows proportionally: ~640px of dead space between a
single day's four cards, which is what the shared-row restructure looked
like it had caused and hadn't. `overflow-y: auto` on the cell does **not**
fix this, and is the obvious thing to reach for — it zeroes an item's
automatic *minimum* size, a different quantity from the max-content
contribution auto rows actually use, and measured no change at all. The fix
is to take the content out of flow: the spanning cell is
`relative self-stretch` and empty, and the timeline lives in an
`absolute inset-0 overflow-y-auto` child, so the column contributes a real
zero to row sizing, the rows size off the day cards alone, and the timeline
scrolls inside whatever height they come to. `self-stretch` is the other
half — the grid sets `items-start`, so without it the cell would sit at zero
height with nothing for `inset-0` to fill.

**That `overflow-y: auto` could never actually fire, and phase 6c is what
found out.** The out-of-flow child is `flex flex-col`, and Quasar's own
`.flex` sets `flex-wrap: wrap` — which Tailwind's `flex-col` does not undo,
the standing trap in `SKILL.md` in its column-direction form. A
*wrapping* column flex container whose content outgrows its box does not
overflow; it starts a **second column beside the first**. Measured at 1440px
once 6c's batching cards added height: the last prep phase laid itself out
at x=423, which is the Monday day column, 143px clear of this cell's own
135px track and on top of Monday's cards — and `absolute` positioning is
what made that invisible rather than merely wrong, since nothing reflows to
reveal it. `flex-nowrap` on that child is the fix, and it is what makes the
`overflow-y: auto` beside it mean anything for the first time. The same trap
has a second form one level down, on each batching card's own `flex flex-col`
— see `prep_candidate_card`, where a stretched child sized to the widest
sibling's max-content rather than the card's width, so the eyebrow's
`truncate` never engaged. Both wanted `flex-nowrap`; neither is decoration.

**The dishes in it are cards, not captions** (phase 6c of `ui-redesign.md`).
The column used to render `session.meals_included` — the model's own prose
list of what it batched — as plain `ui.label` bullets, which read as
"batch-cooked meals can't be swapped or regenerated". They always could: each
one is an ordinary `MODE_COOK` slot with a real recipe, the two anchors on
day 1 and the shake candidate on its own training morning, and their cards in
the weekly grid have carried the full icon row all along. The column was just
the one place you could not act on any of it, or even open a recipe to check
it, without scrolling right to find day 1. Each dish is now a card carrying
`open_detail` on its body and the same `open_swap_modal`/
`generation.regenerate_meal` icons as a sibling row above it — no new
capability, no new dialog, and no new handles: `prep_day_column` is a closure
inside `build_cards` and was already holding every one of them.

Two things about how it resolves them:

- **It reads `candidate_slot_ids`, never `meals_included`.** The slot ids are
  what Python actually folded into the session — `generate_sunday_prep_session`
  stamps them onto the response after the call precisely so nothing downstream
  has to trust the model's self-report (see "Batch cooking on purpose") — and
  they are also the only handle a click can act on. It has a second effect
  worth knowing: a dish whose slot has since been swapped shows its *current*
  recipe here, where the frozen string list would still be naming the one it
  replaced.
- **A session with no resolvable ids falls back to the old inert list.** An
  empty `candidate_slot_ids` (a session saved before that field existed) or
  ids that no longer resolve against `state.slot_views()` render exactly as
  they did before 6c — the same pre-migration tolerance `is_sunday_prepped`
  already extends to this identical field, rather than a column that silently
  stops saying what it batches.

The regenerate icon here says what it costs, because from this column the
cost *is* this column: `regenerate_single_meal` drops `sunday_prep_session`
outright whenever the slot it re-cooks was a prep candidate ("drop rather
than risk a stale plan"), so clicking it empties the timeline until the week
is generated again. That is existing, deliberate behaviour — the tooltip
names it rather than letting the column vanish unexplained.

Shipped alongside a real, pre-existing bug this phase's own acceptance
criteria exposed rather than introduced: `ui_plan.panel()`'s own wrapper
`div` had no `min-w-0`. Its parent, Quasar's `.q-tab-panel`, is a flex
container, and a flex item's default `min-width: auto` refuses to shrink
below its content's natural width — so the panel wrapper always grew to the
grid's full min-content width regardless of viewport, `week_grid_scroll()`'s
own `overflow-x: auto` on the canvas never actually had anything to overflow,
and a *different*, outer Quasar ancestor (`.q-panel.scroll`) silently scrolled
the whole panel — summary stats and all — instead. Harmless before this phase
(nothing inside the canvas needed to stay put while the rest scrolled), but
it would have made the gutter's `sticky` a dead letter: sticky positions
against the nearest scrolling ancestor, which was never the one actually
overflowing. Fixed by adding `w-full min-w-0` to that one wrapper — the same
combination, and the same underlying flex/grid min-size trap, `SKILL.md`'s
NiceGUI-traps list and this file's own phase-1 `meal_card` writeup below both
already document.

**Fibre is the one figure in the header carrying three numbers, and the rule
is which two of them share a divider.** `FIB 24/30g · logged 22g`. The first
pair is the `actual/target` shape every other entry in the row already
carries, and it is honest since the planner started aiming at a figure
(CLAUDE.md, "Fibre is targeted, and still has no term in the energy
identity"). The logged half — from `CRONOMETER_MACRO_COLUMNS` — is
emphatically *not* a second denominator, so it renders as a **second label**
(`logged 22g`, slate) beside the cyan pair rather than as a number inside it.
A slash there would read as a goal that was missed, which is not what a
measurement says.

This paragraph used to state the opposite half — that `FIB 32g` was bare
because no fibre target existed — and that was correct for four releases.
What survived the target's arrival is the *logged* rule, which is the one
worth remembering: it was never the missing denominator.

`ui_state.fibre_view(planned, logged, target)` holds both rules and every
formatted half, and `PlannerState.fibre_for(day)` is what the header calls,
reading the target off `targets_for` so fibre follows the stored-plan versus
live-preview branch every other figure in the row follows; the
widget prints what it is handed. That is the standing "logic worth testing
leaves the widget module" split, and here it is load-bearing rather than
tidy: the phrasing *is* the guard. The lookup matches a `daily_actuals` row
by the day's **calendar date** (`day_date_iso`), not by weekday —
`planner.logged_intake_for` refuses every day but today because a `SlotSpec`
carries only a weekday name, but a loaded `WeekPlan` carries
`week_start_date`, so every column here has a real date. A plan predating
that field, and a row synced before fibre was captured, both fall back to the
planned figure alone — as does a week generated before fibre had a target,
which has no `fiber_g` in `week_plan.targets` and gets back exactly the bare
label the header printed then.

### The type, spacing and radius scale

`ui_theme.py` names four text sizes (`TEXT_MICRO`/`TEXT_BODY`/`TEXT_HEAD`/
`TEXT_DISPLAY`), five spacing steps (`SPACE_HAIR`/`SPACE_TIGHT`/`SPACE_BASE`/
`SPACE_SECTION`/`SPACE_PAGE`) and three radii (`RADIUS_CARD`/`RADIUS_PANEL`/
`RADIUS_PILL`), and every `ui_*.py` call site now names one of these instead
of a literal Tailwind class. Phase 1 of `ui-redesign.md`; the canonical
statement of the scale, including which value goes where, is `SKILL.md`
beside this file.

What it replaced: nine font sizes between 8px and 14px (`text-[8px]` through
`text-base`), 56 call sites at the smallest pixel value alone and 35 at the
next — a band too narrow for any two of them to read as different at a
glance. That was noise being mistaken for hierarchy; weight and colour
already carried the real distinctions, and still do. Spacing had the same
problem one step down: `1.5` (6px) sat exactly halfway between the `1` and
`2` steps with no principled reason to reach for it over either, so all 24 of
its call sites now resolve to `SPACE_TIGHT` — down, not up, because the type
scale is simultaneously getting larger and the dense card interiors where
`1.5` appeared can't absorb growth from both directions at once.
`rounded-md`/`rounded-xl`, the two in-between Tailwind radii, are retired the
same way — every site that used either now names `RADIUS_CARD` or
`RADIUS_PANEL`, whichever the element actually was.

**This is a token pass, not a layout change.** No element moved, was added,
or was removed — `mt-*`/`mb-*`/`mx-*` margins are deliberately untouched,
because folding them into a parent `gap` is a layout decision that belongs to
phase 2, which is already restructuring containers. Phase 1 also recorded,
without resolving, that amber currently carries five meanings (near-target,
carbs, training, a target override, fridge storage) and violet two (fat,
location) — see the collision note beside `LOCATION_ACCENT` in
`ui_theme.py`. Resolving either is a phase 3 decision, made when the
surfaces using them are rebuilt anyway.

**The larger scale did surface the risk this phase called out, and it took a
real bug to find it.** Reported as cards visually overlapping their
neighbours on Monday/Thursday/Saturday at common laptop widths (1280-1440px;
invisible at 1600px+, which is why the phase's own screenshot pass missed
it). Root cause turned out to be older than the token pass: `meal_card`'s
outer `div` had no explicit width, and relied on the day column's flex
`stretch` to size it to the grid's ~110px track. That stretch was never
actually engaging — measured, the card's `width:auto` was instead sizing off
its widest descendant's *unwrapped* content (the macro-pill row's ~178px),
so every card in the column followed it out to the same wrong width. This
already overflowed slightly before phase 1; the larger fonts added enough
per card to push it from invisible to visible. Fixed with `w-full` (pin the
card to the column's real width instead of trusting stretch) plus
`overflow-hidden` (which is what then makes `max-w-full` on the macro pill
and `truncate`/`line-clamp-2` on the title mean anything — none of them
constrain content against a box that's still sizing itself off that
content). Both look removable to a future reader who doesn't know this; they
aren't. The status badge (`STATUS_STYLES`) also dropped its text label down
to icon-only-plus-tooltip in the same pass — the card's own left-border
colour already said cook/leftover/skip/missing, so the label was spending
width on every one of 28 cards to repeat the border's answer.

### The three surfaces

CHANGE-QUEUE.md's "the UI reads flat", and the accurate core of the
2026-08-30 front-end review: there was exactly one `shadow-*` class in the
entire front end, everything else was `slate-900`/`950` fills separated by 1px
borders, and at this density every surface therefore carried identical visual
weight. The measured version is worse than the prose one — the page ground was
Quasar's own dark `#121212`, which sits *lighter* than `slate-950` and barely
darker than the `slate-900` every panel is painted in, and `ui.tab_panels` was
`bg-transparent`, so a meal card's translucent tint composited onto the body
with nothing at all between them.

`SURFACE_PAGE` / `SURFACE_PANEL` / `SURFACE_INSET` / `SURFACE_CARD_LIFT` in
`ui_theme.py` are the answer, and the per-value table is in `SKILL.md`. What
belongs here is why it is fill and shadow rather than the brighter border the
review proposed: **the card border is already spoken for.** `STATUS_STYLES`
puts four structural meanings on that exact edge — emerald cook, sky leftover,
slate skip, rose not-generated — and its 3px left accent repeats them. A
neutral border bright enough to read as elevation would compete with all four
and would read as a fifth slot status, which is the one thing the palette
contract names as not to do.

Three consequences the implementation turned up, none of them foreseen by the
review, and all three the same shape — **a translucent fill is a claim about
what is behind it**:

- **The cook tint went `/[0.07]` → `/[0.12]`** (the review's own item (b),
  and it lands for a better reason than "brighter": 7% of anything against a
  lighter ground had been carrying the one card in the week that costs you an
  evening).
- **The skip tint went `slate-900/40` → `slate-950/40`.** Over the old
  `#121212` it read as a slightly cooler tile; over a slate-900 panel it
  composites to slate-900 exactly — to nothing. Going *down* a step instead
  makes a skipped slot read as recessed, which is the honest shape for the
  one status where nothing is planned. `ui_insights`' adherence tiles and
  `ui_catalog_browser`'s row hover were the other two `/60`-and-below fills
  that vanished the same way; both are `SURFACE_INSET` now.
- **The rail's surface had to move off `ui.tabs()` onto a wrapper div.**
  `.q-tabs` carries a height of its own and ignores `self-stretch`, so the
  painted column stopped under the last tab and the row's ground showed for
  the rest of the height. That had been true all along and was invisible
  while the ground was `#121212`. **Raising the contrast between surfaces
  exposes every element that was sized to its content rather than to its
  column** — worth checking for, rather than being surprised by, next time.

### Two things a Plan panel says in front of its grid

`week_failures` had this region to itself; `empty_state` joins it for a week
that has never been generated. Both are readings of *this destination's own
canvas*, which is what keeps them here rather than in the header (which reads
the week) or the rail (which acts on it).

**It is a banner, not the hero the review asked for**, and the difference is
load-bearing: the grid below is not empty when there is no plan.
`slot_views()` builds from the spec, so 28 placeholder cards render, and every
structural control on them — mode, "Link to next lunch", a skip estimate, a
pinned favourite — works and is worth using *before* a run rather than after
one. Replacing that with a centred call to action would hide the one thing a
first-time visitor most needs to do next in order to announce that it holds no
recipes yet.

**It carries no Generate button**, which is phase 6b's rule rather than an
omission — the rail is the single place a click starts something. A second
Generate here would also be a second control to keep in step with
`state.week_selection`, which the rail's own button already binds to. It names
that button and wears its icon instead, so the sentence points at something
real on screen.

The review claimed `state.week_plan is None` "already gates the shopping list
the same way". It does not: it gates the PDF and HTML *exports* in
`ui_app.py`. This is a new branch, not a moved one.

### The generation dialog says which meal types are banked

`ui_generation.py` already built a persistent dialog with a `linear_progress`
bar, a status label and a live `ui.log`; `on_meal_type` already fired on the
loop, once per meal type, with that stage's cook count. Everything a staged
readout needs was arriving — only the rendering was missing, which is why the
review's own three-day costing for this was wrong by about an order of
magnitude, and worth correcting in writing so it does not get deferred again
on a cost it does not have.

**The one piece with real logic in it lives in `ui_state.py`**
(`generation_stage_views`), for the reason `SKILL.md` gives: if you want to
test a `build_*` function, move the logic down rather than growing a NiceGUI
harness. What it holds is the off-by-one that makes this feature honest —
`progress_callback` fires *before* each meal type's call, so its count is
stages **started**, and index `started - 1` is the one in flight. Reading it
as "finished" would tick a stage as done up to three minutes before its
recipes exist, which is exactly the window a reader is watching this list
during. The final stage therefore has nothing to bank it, and `complete` —
set once `generate_week_plan` returns — is what does. A run that raises leaves
the last stage showing as running, which is true rather than a bug: that is
the stage that was in flight when the run came apart.

Glyph carries all three states and no hue does (`check_circle` /
`autorenew` / `radio_button_unchecked`), because emerald — the obvious tick —
is the cook status, and a green check in front of a meal type would read as a
slot state.

### Discard asks first

"Discard pending changes" is the only irreversible button on the page. Target
overrides, training edits and pantry rows are never written to disk, so there
is nothing to reload them from: its undo is retyping everything. It sat one
unlabelled click away, immediately beside "Review", which is the button a
reader actually wants.

The confirmation is built once in `build_staged_bar`, outside the
`@ui.refreshable` `bar()` — a dialog constructed inside a refreshable stacks
another copy into the page on every repaint, the same reason
`ui_generation`'s progress dialog is built at factory time and merely
*opened* by the run. Its body is its own refreshable so the list of what is
about to be thrown away is current at the moment of opening rather than at
the moment of construction.

The confirm button is slate, not rose. Rose already means a failed slot and an
off-target reading; a destructive-action red would be a third meaning on a hue
the contract caps at two — and a dialog whose entire purpose is to say what
you are about to lose can afford to say it in words.

### Module layout

`ui_app.py` used to be the whole UI — every widget a closure inside one
~3,200-line page function. It is now an ~300-line **page shell**: it builds
a `UIContext` (`state`, the repository, a `Refreshables` registry), calls
each concern's `build_*(ctx)` factory, lays out the header, the
staged-changes bar and the rail's action block (the three regions with no
natural module of their own — the header because it's shared chrome above
every destination, the bar because it needs two other modules already built,
the action block because it needs handles from four), lays out the rail and
its five destination panels, and registers every returned refreshable into
one topic map. The concerns:

| module | owns |
|---|---|
| `ui_theme.py` | presentation constants, CSS, pure render helpers (`STATUS_STYLES`, `telemetry_bar`, `chain_css`, `week_grid_scroll`, ...) — no `PlannerState` dependency |
| `ui_state.py` | `PlannerState`, `SlotView` — the view model, unchanged in substance from before the split |
| `ui_context.py` | `Refreshables` (the topic registry, see below) and `UIContext` |
| `ui_catalog.py` | favorites helpers shared by `ui_cards` and `ui_catalog_browser` (`is_favorited`, `toggle_favorite`, `build_rename_dialog`, ...) |
| `ui_generation.py` | everything that writes `week_plan.json`: `run_generation`, `regenerate_day`, `regenerate_meal`, `save_grid`, `reload_from_disk`, plus the progress dialog and the rejection-capture prompt (see "Rejection capture" below) |
| `ui_cards.py` | the meal cards, recipe detail and swap-with-favorite dialogs, and the canvas the Plan destination wraps |
| `ui_telemetry.py` | the header's week banner (week dates, plant count, the week's shape) and macro bars |
| `ui_shopping.py` | the right-hand shopping slide-over |
| `ui_review.py` | the review dialog — every input to the *next* generation: cuisine, diet style, bulk-prep/long-cook, people per meal, daily targets (the target curve, see below), training schedule, pantry (see "The review dialog and the staged-changes bar" below) |
| `ui_staged_bar.py` | the persistent pending-changes strip between the header and the rail |
| `ui_inspector.py` | the day inspector — a floating, read-mostly panel for one day, opened from its telemetry column (see "The day inspector" below) |
| `ui_plan.py` | the Plan destination — `ui_cards`' canvas, plus the generation-failure list above it |
| `ui_today.py` | the Today destination — one day's cards, its location/training context strip, and the day picker that moves between days (see below); its day-rendering helpers are module-level so `ui_inspector.py` reuses them |
| `ui_catalog_browser.py` | the Library destination — the recipe catalog, its filters, and recipe import |
| `ui_insights.py` | the Insights destination — five readouts (weight/target, the weigh-in table, planned-against-logged, macro accuracy, adherence tiles), each drawn only behind its own `ui_state` gate (see "Insights" below) |
| `ui_settings.py` | the Settings destination — week start, shopping days, model, the Daily Targets source panel (see below), and an integrations list whose rows open three read-only detail dialogs |

Each `build_*(ctx)` returns a small dataclass of the refreshable functions
(and, for `ui_shopping`, the drawer element) other modules or the shell
need — `ui_cards.build_cards`, for instance, needs `ui_generation`'s
handles passed in, because a card's regenerate icon calls into it. This is
why build order matters in `planner_page()`: `ui_generation` before
`ui_review` (its Generate button starts a run), `ui_cards`
before `ui_plan` (the Plan destination's canvas is `ui_cards`' own —
`ui_plan` stopped needing `ui_review` when phase 6b moved the Generate
button to the rail), `ui_cards` and `ui_review`
before `ui_inspector` (its slot cards open `ui_cards`' recipe detail dialog,
its "Edit targets" link opens `ui_review`'s), `ui_inspector` before
`ui_telemetry` (the header's day cell wires the click that opens it),
`ui_review` and `ui_shopping` before the rail's action block (its Generate
opens the review dialog, its Shopping button toggles the drawer),
everything before the refresh-topic registration at the bottom.

### The rail's action block

Phase 6b of `ui-redesign.md`. Five buttons — Generate, Shopping, Shuffle
styles, PDF menu, Mobile page — sitting in the rail between the Plan/Daily
View tabs and the Shopping/Library/Insights/Settings ones. **The six destinations
answer "what am I looking at"; these answer "what do I want to do", and
nothing else on the page is clickable chrome** except the header's week
selector (which changes scope, not state) and the staged-changes bar. They
came out of two places: Generate and "Shuffle styles" from the Plan
destination's own header row, and the print/mobile/shopping buttons from
`ui.header()`, where they were competing for the same fixed strip of pixels
the day columns need. The `model: <id>` readout that sat beside them was
deleted rather than moved — the select is in Settings and the progress
dialog names the model mid-run, so the header's copy was a third.

Two things about it are load-bearing:

- **They are ordinary children of `ui.tabs()`, not a second tab strip.**
  Quasar's QTabs puts its default slot in a flex `.q-tabs__content` (a
  column, in `vertical` mode) and only registers real QTab children with its
  model, so a plain `div` sits in the flow without joining the selection —
  which is what lets the block sit *between* the two groups of tabs with no
  second `ui.tabs()` element and therefore no second `ui.tab_panels` value
  to keep in sync with the first. Clicking an action leaves the selected
  destination alone.
- **`RAIL_WIDTH_PX` bounds every button in it.** The header's copy of the
  week grid is inset by exactly that many pixels
  (`WEEK_GRID_HEADER_INSET_STYLE`) so each day's telemetry sits over its own
  meals, so a button wide enough to grow the rail slides all seven days off
  their columns. Hence `TEXT_MICRO`, `align=left` and `w-full` on all five,
  and "Shopping (108)" rather than the header's old "Shopping list (108
  items)". Anything added here has to be measured, not assumed to fit.

**The Plan destination's "This week" stat block moved in the other
direction**, into `ui_telemetry.week_banner` beside the week dates and the
plant-diversity count: cook sessions, days cooking, portions and shopping
trips are *readings* of the week, the same kind of thing as the two pills
already there, and one reporting strip beats a second header inside the
destination it describes. It reads `state.spec` rather than `week_plan`, so
an un-generated week still previews its shape exactly as the Plan row did.
The consequence for refresh topics: `week_banner` is registered on
`"shopping_days"` as well as `"plan"`, because the trip count is a partition
of the week by `state.shop_days` — that topic used to reach the Plan row
this content came from, for the identical reason. What is left in `ui_plan`
is the generation-failure list, which is neither a control nor a reading of
the week but an error banner for *this* grid, naming the slots whose cards
below are the red NOT GENERATED ones.

The presentation contract — the type/spacing/radius scales, which colours are
structural vs. semantic vs. categorical, and the NiceGUI traps that have each
cost a debugging session (`props()` dropping bracketed values, `bind_value`
firing at build time, refresh stealing input focus, Quasar's `.flex` wrapping)
— is in `SKILL.md` beside this file, which is why the `ui-work` skill should
be loaded before any `ui_*.py` edit.

**The `Refreshables` registry replaces a hand-maintained `refresh_all()`.**
A call site says *what changed* — `refreshables.refresh("plan")`,
`"targets"`, `"catalog"` — instead of naming every widget that currently
depends on it. Topics are registered once, in `planner_page()`, after every
module is built. `"plan"` is the broad one (a generation, a reload, a
leftover link, or a settings control that reshapes the week all repaint the
same set); several narrower topics exist because rebuilding a section
mid-edit would steal an input's focus — see `ui_review.day_target_row`'s
`sync()`, which refreshes `"telemetry"` alone rather than `"targets"` for
exactly that reason.

No package structure: every module above is still a flat sibling, importable
via plain `python src/ui_app.py`, per this file's `sys.path[0]` note under
Layout. Nothing outside `src/ui_*.py` and `ui_app.py` changed shape — the
repository, planner and week modules are untouched by the split.

**Generating is the only thing here that calls the model — it is no longer
the only thing that writes to disk.** `run_generation` saves `week_plan.json`
and records history *before* adopting the plan into `PlannerState`, so the
grid can never show a week that isn't saved — a 20-minute run one browser
refresh from being lost is the failure that ordering prevents. Grid *edits*
stay in-memory only until something acts on them: the staged-changes bar's
"Discard pending changes" throws them away, "Generate week" folds them into a
full re-plan (`adopt_plan` clears the grid-edit part of `pending_changes()`,
because saving is what it just did), and — new, see `save_grid` below —
"Save changes" writes `state.week_plan` to `week_plan.json` directly, with no
model call, because a deterministic edit (a swap, a leftover link, a skip
estimate) needed no LLM to be fully decided in the first place. See "The
review dialog and the staged-changes bar" below for why the
target/training/pantry parts of `pending_changes()` never clear this way —
`save_grid` doesn't touch them either, for the same reason.

Things worth knowing about the generation path specifically:

- **It generates what's on the grid**, not a fresh default week — including
  any "Link to next lunch" edits, so a linked lunch is a leftover the model is
  told not to generate. `generation_spec()` reapplies the review dialog's
  people-per-meal, which `PlannerState.spec` deliberately ignores once a week
  exists (see `_shape()`); without that, the control would silently do nothing
  on every run after the first.
- **The API key is checked up front** (`planner.api_key_error`). Left to the
  per-stage handler it would become one identical failure per meal type after a
  long wait, because "a failed meal must not fail the week" is exactly the
  wrong policy for a misconfiguration that will fail every call.
- The progress modal is built once per page and *opened* per run, so the
  `progress_callback`/`note_callback` handlers just assign to elements that
  already exist. Notes go to a `ui.log` rather than a status label because
  portion trims and failed meal types both arrive mid-run and a label would
  overwrite the one you were reading.
- Only a whole-run exception reaches the `except` (no config, storage
  unwritable); per-meal-type failures arrive in `WeekPlan.failures` and become a
  warning toast plus the red NOT GENERATED cards. Nothing is adopted on the
  exception path, so a failed run leaves the week on screen untouched.
- `PlannerState.generating` guards re-entry. The loop stays free during a run
  (see below), which is exactly why the button is still clickable and needs
  the flag: two tabs generating at once would race to overwrite the same file.

The main edit it offers is the **"Link to next lunch"** button on each dinner
card: one click sets the following day's lunch to `MODE_LEFTOVER` with
`source` pointing at that dinner. Because portions are derived, that single
change is also what grows the batch. Its inverse is the **Unlink** button on
every leftover card (`PlannerState.unlink_slot`), which is the *only* way to
undo one — clicking the link button a second time hits
`leftover_link_error`'s repeat-click guard rather than toggling, so before
this existed a grid could only ever accumulate links, and
`ui_generation.generate_week`'s stranding warning told users to "unlink one"
with no control anywhere that did it. Both go through
`PlannerState.apply_spec`, which is where every future grid edit should land
too:

- The spec is now **held** (`PlannerState._spec`) rather than re-derived per
  read, and rebuilt only when `_shape()` changes — re-deriving on read would
  discard the edit that was just made. (The deleted Streamlit app dodged the
  same trap in `ensure_grid`.) `_shape()` excludes `servings` for a generated
  week, whose
  portions come from `week_plan.servings_per_meal` instead.
- `apply_spec` writes the new slots back into `week_plan` as well as `_spec`,
  because `day_slot_macros` walks the *plan's* slots — otherwise the linked
  lunch's macros would never reach the telemetry header.
- It also rescales the affected cook events, via
  `Recipe.scale_to_servings()` directly on each event's recipe. Portions being
  derived means a card reading "4 portions" over ingredients weighed for 2 is
  exactly the disagreement the derived-portions rule exists to prevent, and the
  fix is linear arithmetic, not a regeneration call.
- `week.leftover_link_error` gates the click. It re-checks what
  `validate_week` enforces, but returns *one sentence about the two meals
  clicked* — a whole-week error list can't say which entry the click caused.
  The button is left enabled when it fails, because a disabled Quasar button
  swallows hover and the tooltip explaining why would never appear.

The leftover/cook pairing is drawn two ways. Statically, both cards carry a
dot and a line in their chain's colour — the cook says "→ feeds Tue lunch",
the leftover says "↩ from Mon dinner" — so the link reads without touching
anything. On hover, `chain_css()` outlines every card in the chain at once,
via `.meal-canvas:has(.chain-N:hover) .chain-N`; `:has()` is what lets one
card's hover style its partners three columns away without a Python round trip
per mouseenter, which would be visibly laggy for a hover effect. Chain classes
are unique per chain, colours cycle, so the outline is what disambiguates when
a busy week reuses a hue.

### The expanded recipe card

Clicking any card — Plan, Today or Library destination, all three share the
one dialog — opens
`ui_cards.recipe_detail`, which is laid out as a document you cook from rather
than as a roomier version of the card that opened it: a mono eyebrow
(`MEAL TYPE — STYLE`), the title, one ruled strip carrying
`KCAL/PRO/CHO/FAT/PREP`, ingredients as a two-column table with the quantities
right-aligned in one mono column, and the method as numbered rows. Type and
alignment do the work because the grid *behind* the dialog is already spending
every colour the app owns; the only hues inside it are the status chip, the
`MACRO_TINTS` on the three macro labels, and the amber prep note.

Four decisions in it are worth keeping:

- **Ingredients are for the batch, macros are for one serving**, and on a
  bulk-cooked dinner those differ by a factor of six. Each half is labelled
  next to itself — `ALL 6 PORTIONS` on the ingredients header, `PER SERVING`
  under the macro strip — rather than once at the top where it would be read
  as applying to both.
- **The reference design this came from had a 1x/2x/4x portion multiplier,
  and it is deliberately absent.** Portions are derived (`week.portions_for`),
  which is what makes a batch size unable to disagree with the meals it
  covers; a multiplier sitting on the recipe would be a second source of
  truth for the same number. The status chip took that corner of the eyebrow
  instead, so the dialog says what it opened from.
- **Ticking a step mutates that row's classes**, never `recipe_detail.refresh()`
  — repainting to strike one line out of nine would lose the scroll position
  in the recipe you are reading. It is not persisted, and resets whenever the
  dialog opens: scratch state for one cook, the same reasoning as the shopping
  list's unticked checkboxes. `add=`/`remove=` rather than `toggle=` because
  the pairs are conflicting Tailwind utilities (`text-slate-200` vs
  `text-slate-600`), and both present at once resolves by stylesheet order.
- **`flex-nowrap` on the step and prep-note rows is load-bearing.** Quasar's
  own `.flex` rule sets `flex-wrap: wrap` and Tailwind's `flex-row` does not
  undo it, so a step long enough to fill its row wrapped *below* its number
  and ran back underneath it. `min-w-0` on the label is the other half — a
  flex item's default `min-width: auto` won't shrink past its longest word.
  Worth knowing before adding any icon-plus-text row anywhere in this UI.

NOVA group moved to a per-ingredient tooltip: every group that reaches the
dialog is an allowed one (4 is rejected in validation), so it is worth being
able to check and not worth a column.

**Every card that opens it splits its icon row off from its clickable body**,
and the split is structural rather than stylistic: the icons are a *sibling*
of the body, never its parent, so a click on favorite/swap/regenerate/edit
can't bubble into the body's handler and open the recipe dialog on its way
past. `meal_card`, `today_card`, `prep_candidate_card` and — since phase 6d
of `ui-redesign.md` — `ui_catalog_browser.catalog_card` are all that one
shape. The Library card was the holdout: it wired `title.on("click", ...)`
alone, so the recipe name was the only live pixel on a card whose whole body
was about that recipe.

Making the split possible there meant moving the **title out of the icon row
and down into the body**, which is what the other three already do — and that
move fixed a second thing, found by measuring the running page rather than by
reading the source. That row carried the standing Quasar `.flex` wrap trap:
with the title still in it, any name whose max-content ran past roughly 200px
pushed all three icons onto a line of their own, because wrapping is decided
from the items' *unshrunk* widths and the title's `min-w-0` therefore never
got a chance to prevent it. Measured at 1440px on the shipped 92-recipe
catalog, that was most of a screen of cards rendering icons-beside-title and
the rest icons-under-title, on nothing more principled than name length. The
row is icons-only and `flex-nowrap` now, and all 92 cards measure one 18px
icon row at every breakpoint from 620px to 1440px.

`hover:text-sky-300` moved onto the body with the handler, not onto the
title: it reaches the title by inheritance, that label being the only
descendant with no colour of its own, while the tag line and every macro
figure set theirs and are left alone. So the hover affordance now covers the
whole clickable region rather than the four words that used to be it.

## The Today tab

`ui_today.py` is a read-only preview of just today's four cards, its own
destination on the rail beside Plan. It is deliberately not built on
`ui_cards.meal_card` — that function's action-row buttons all need
`ui_catalog`/`ui_generation` wired in, none of which a card with no buttons
needs, so a smaller card of its own there is a real decoupling rather than a
"fix later" shortcut. No favorite/swap/regenerate buttons yet, but clicking
a card *does* open the recipe detail dialog — `build_today(ctx, cards)`
takes `ui_cards`'s `CardHandles` and calls `cards.open_detail(view)` on
click, the same one dialog every Plan-destination card already shares,
rather than a second copy of it living here.

(Still called "the Today tab" throughout this section — mechanically it is
still one `ui.tab`/`ui.tab_panel` pair, per `ui_app.py`'s rail; only its
orientation and its four siblings changed in phase 3 of `ui-redesign.md`,
not the widget or any of the logic below. The rail's own label reads
**"Daily View"**, not "Today" — renamed post-phase-3 since the tab is a day
picker, not only today's meals, and a dynamic label already had to drop the
word "Today" the moment you stepped off today (see "Browsing to another
day" below). Every function, class and file name (`ui_today.py`,
`TodayHandles`, `build_today`, ...) keeps the old name regardless; only the
on-screen string changed.)

**Knowing "today" needed a real calendar date, which nothing in this
codebase stored.** `WeekPlan.days` is a rotation of weekday *names*
(`week.week_days` rotates names, not dates), and `week.week_date_range`
existed only to *derive* a plausible date range for display — it anchors on
`generated_at` or on "now", so the same cached plan looks equally plausible
whether it's five weeks old or generated ten minutes ago. That ambiguity is
fine for a banner ("Week of Aug 10 – Aug 16") but not for deciding whether
today's Thursday slot is actually *this* Thursday — confidently rendering
last week's Thursday would be worse than saying nothing.

`WeekPlan.week_start_date` fixes this: the ISO date `days[0]` fell on at
generation time, set once in `generate_week_plan` (anchored on the same
`generated_at` timestamp, not a second `date.today()` call, so the two can't
disagree) and preserved through `regenerate_single_day`/`regenerate_single_meal`,
which only `model_copy` the fields they actually change. `week.today_in_week`
is the check built on top: given a plan's `week_start_date` (or, for a plan
generated before this field existed, `week_date_range(days, generated_at)`'s
own anchor — the same pre-migration tolerance `history_styles()` already
extends to old `meal_history.json` entries), it returns today's weekday name
only if today's actual calendar date falls inside that week's span, else
`None`. `PlannerState.today_day()` wraps it against whichever week is
currently loaded. It used to be the whole story — a `None` replaced the
panel with "no cached week covers today" — but a tab with a day picker can
show the week anyway, so that case is now a note beside the heading and
`viewed_day()` falls back to day one (see below). Only "nothing generated at
all" still replaces the panel.

### Where you are and what you trained

The Today tab also carries a **day-context strip** above the calorie bar:
where the day is spent, and the workouts scheduled for it. This is the one
thing the tab can show that the Plan destination structurally cannot — seven
columns have room for an amber bolt saying *that* a day has a workout, and
one day has room to say which session, at what time, for how many calories,
and what the location does to lunch. So it lives here rather than in the
shared header above every destination.

`ui_state.day_context` is the whole view model, built **once per repaint**
rather than once per card: the per-meal training notes are only reachable
through `planning_config()`, which runs `apply_training_adjustments` over the
entire week, and four cards each asking for their own would be four copies of
that work for one day's answer.

It reads the config **the next run would use**, not the file on disk, which
is what puts it under the same "live preview" contract `targets_for` already
honours — a session added in the review dialog changes the day's budget
*and* its post-workout pin, so a strip still showing the file's schedule
would contradict the calorie bar directly above it. `today_view` is
registered on the `targets` and `training` refresh topics for that reason.
It is deliberately **not** on `telemetry`: that topic exists so a keystroke
in a focused target input can repaint the header without disturbing the
dialog, and rebuilding four cards plus a `planning_config()` per keystroke is
exactly the cost it was carved out to avoid.

Four things in it are decisions rather than detail:

- **A location badge appears on a card only if the location declares that
  meal's `<meal_type>_mode`.** `LocationView.constrains`/`brief` mirror
  `planner.build_location_note`'s scope rule rather than re-deciding it, so
  "must travel in a container" reaches an Office *lunch* and never the
  breakfast eaten at home before leaving. Getting this wrong renders as an
  ordinary-looking card carrying a constraint the prompt never sent — nothing
  else in the app would catch it, which is why `test_ui_state.py` pins it.
- **The restriction chips are tag-labelled and prose-tooltipped, and the two
  come from `LocationView.phrase_pairs` as pairs.** A tag with no
  `LOCATION_RESTRICTION_PHRASES` entry is dropped exactly as
  `build_location_note` drops it — so zipping `restrictions` against a
  filtered `phrases` would silently pair the surviving tags with the wrong
  sentences the moment one tag went unrecognised. Pairing at the source is
  what makes that unrepresentable.
- **The post/pre-workout badge classifies `training_notes` by
  `planner.TRAINING_NOTE_PREFIXES`**, a constant `apply_training_adjustments`
  now writes those notes *with*. Matching on the wording instead would mean a
  reworded prompt silently dropping the badge, and a note that fails to parse
  renders as no badge rather than as an error. The badge carries the kind and
  the tooltip carries the model's own sentence with the prefix stripped, so
  the two don't restate each other.
- **A rest day, or any zero-burn session, is muted rather than amber.**
  `apply_training_adjustments` skips both, so neither expands a budget or
  pins a meal, and an amber chip would promise calories it never bought.
  `TrainingView.is_rest` folds the two cases together for that reason — a
  typed `rest` and a session logged at 0 kcal are the same thing downstream.

Sessions are ordered by `planner._clock_minutes`, shared rather than
reimplemented so the strip orders a day by the same tolerant clock reading
that decides which meal gets the post-workout pin. Everything degrades to
saying nothing: a config with no `base_schedule` yields no location, an
untrained day yields no chips, and a day with neither renders no strip at
all rather than an empty panel announcing the absence of a feature.

### Marking what actually happened

The Daily View is where adherence is recorded — CHANGE-QUEUE.md's adherence
item, and the reason `today_card` was restructured. The full record is in
CLAUDE.md's "Whether the plan actually happened"; what belongs here is the
front-end half.

**The mark row is a sibling of the clickable body, and that is what the
restructure was for.** `today_card` used to put its click handler on the
whole card element — correct while nothing on the card was clickable in its
own right, and exactly wrong the moment a button appeared inside it: a click
on a mark would bubble through the card's handler and open the recipe dialog
on top of the mark it had just recorded. The handler moved down onto a `body`
element, and the header row — meal type, the three marks, the status badge —
is now its sibling, which is the structure `ui_cards.meal_card` has used for
its favourite/swap/regenerate row since phase 1 and documents in the same
words.

**Both surfaces get it for free, because both already shared the
renderers.** `ui_inspector.py` calls the same module-level `context_strip` and
`today_card`, so threading one `DayMarks` parameter object through them
reached the inspector with no second copy of anything. That parameter is an
object rather than four more arguments precisely because there are two call
sites and both would have grown the same four.

Four decisions, none of them cosmetic:

- **No hue.** The three meal marks and the two completion sources are all
  slate, distinguished by glyph — `check_circle`/`remove_circle`/`swap_horiz`
  for ate/skipped/swapped, and `check_circle` versus `task_alt` for a
  Garmin-recorded session versus a hand-marked one. Emerald is the obvious
  colour for a tick and is the cook status, so a green check on a card would
  read as a fifth slot state; every other hue is equally spoken for. Set
  versus unset is fill and weight, the `bookmark`/`bookmark_border`
  distinction, rather than a second glyph — only `check_circle` has an
  outline twin in the base Material set, and three marks each encoding "set"
  a different way is not an encoding.
- **A Garmin-recorded session renders as an icon, not a button.**
  `activity_log` is the answer for those and nothing on this page may
  overwrite it; the tooltip carries what the watch actually saw, which is the
  half worth reading when a 20-minute walk is answering for a declared hour.
  A hand-marked one *is* a button, because it is a row in `adherence.json`
  and a mis-click has to be takeable back.
- **A day with no `week_start_date` offers nothing.** Not an unmarked circle
  — that would state as fact something never checked — and not an error. The
  same honest silence `context_strip` already takes for a day with no
  location and no session, and the same tolerance `day_date_iso` draws for a
  plan generated before that field existed.
- **`"adherence"` is its own refresh topic.** Two sections draw a mark, and
  `"plan"` would repaint the canvas, the header and the shopping panel on
  every tick.

Marks persist on click rather than staging. Every grid edit waits for Save
because it is an input to the next generation; a mark is not, so the staged
bar has nothing to hold and a tick that vanished on reload would be a control
with no effect. It writes to `data/`, so the "two places write to `config/`"
rule above is untouched.

### Browsing to another day

The tab is no longer pinned to today: a row of seven day pills with a chevron
either side moves through the loaded week, and the **tab's own label becomes
the day being viewed** — "Daily View · Sun 23 Aug" on today, "Fri 21 Aug" once
you step away. Each pill carries an amber mark per workout that day, so the row
doubles as a week-at-a-glance of the training schedule.

**This was cheap because the panel was already day-parameterized.**
`today_view` had exactly one line deciding the day, and everything under it —
`targets_for`, `totals_for`, `day_context`, `slot_id` — already took a day
argument. Adding the picker changed that one line; no plumbing followed.

Four things in it are decisions:

- **`selected_day = None` means "follow today", and is a distinct state from
  storing today's name.** A tab left open overnight should be on the right day
  in the morning, and the resolved name would pin it to whichever day the page
  loaded on. The "Today" reset button clears the key rather than re-pointing
  it — the same reasoning as `set_target` dropping an override that matches
  the file.

  **It now crosses back, too** (`go_to_today`), and its visibility reads
  `today_is_reachable()` — `weeks_covering_today`, a fact about disk — rather
  than `week_covers_today()`, a fact about the plan on screen. Step forward
  into next week and the loaded plan has no today in it at all, which is
  precisely when a way back is most wanted; keying off the loaded plan would
  hide the button exactly there.
- **Stepping crosses into the adjacent cached week, and clamps only at the
  outer ends of the timeline.** This paragraph read "clamps at both ends
  rather than wrapping or spilling into the next week" for four releases, on
  two objections — an async load of the other cached plan, and "a second
  control free to disagree with the header's week selector". Both are
  answered rather than dodged.

  `PlannerState.browsable_timeline` is the concatenation of each *cached*
  week's columns, and `step_target(delta)` is one index step along it,
  returning `(week, day)` or None. It can be that simple because `days` is
  derived from config rather than from the plan — both weeks are the same
  seven weekdays in the same rotation, so crossing is an index step and never
  a re-read of anybody's day list.

  - **There is no second control, because the chevron drives the existing
    one.** `switch_week` remains the only writer of `week_selection`, and the
    header select is now `@ui.refreshable` and registered on `"plan"`, so it
    repaints from that value. `go()` widens its own refresh from `"today"` to
    `"plan"` exactly when the week changed — the canvas, the telemetry row and
    the shopping panel are all describing the week that just moved under them.
  - **The async load is `scan_cached_weeks`, run once from `.load()`.** It
    probes the week that is not on screen and records two facts: which weeks
    have a plan (`cached_weeks`) and which have a column for today
    (`weeks_covering_today`). That buys an honest disabled state — a chevron
    is offered only when there is something on the other side of it, which is
    the standard the clamped version already set for itself. Spilling first
    and discovering the week is empty afterwards *strands* the reader:
    `viewed_day()` is None with no plan, so there is no picker left to step
    back with.
  - **A chevron asks `step_target`, never the day's index.** The answer now
    depends on whether the neighbour is cached, and a chevron deciding that
    for itself would be a second copy of the rule free to disagree with the
    one that acts. None means "would not move", which is what a disabled
    chevron says.
  - **An edge step announces where it goes.** Crossing changes what the whole
    page shows and — like the header select it drives — drops unsaved grid
    edits, so it must not be the one gesture in the app that does that
    silently. The tooltip names the week and the bare weekday: the other
    week's `week_start_date` is not in hand (the scan read that plan to answer
    whether it exists, not to keep it), and a tooltip is the last place to
    print a plausible-looking wrong date.
  - **A state that never scanned behaves exactly as it did before.**
    `_known_weeks` reads an empty set as "not asked yet", never "nothing
    exists", and falls back to what the loaded plan alone vouches for. Every
    test fixture is in that state, and so is anything built before the scan
    lands.

  Still never wrapping, and still for the original reason: past the last
  cached week there is genuinely nothing, and looping Sunday back to Monday
  would pretend the calendar is a ring.
- **`week_covers_today()` is stricter than `today_day() is not None`, and the
  gap is the point.** `today_in_week` answers "is today inside this week's
  seven-day *span*" — a question about dates — while the grid is drawn from
  `state.days`. A config whose `weekly_schedule` names fewer than seven days
  has a span wider than its columns, and it is the columns a picker can
  navigate to. Both the "doesn't cover today" note and the reset button's
  visibility key off the columns for that reason.
- **A plan with no `week_start_date` shows the bare weekday name.**
  `day_date_iso` returns None for it and `ui_theme.format_day_label` degrades
  accordingly, because `week.day_date` deliberately refuses a `generated_at`
  fallback — and a tab title is the most visible possible place to print a
  plausible-looking wrong date.
- **The workout marks differ by icon, never by colour, and "today" gave up
  its dot for them.** `ui_theme.TRAINING_TYPE_ICONS`/`training_icon` map a
  type to a glyph — dumbbell, bolt, bike, runner, heart, walker — and every
  one of them stays amber, because emerald, sky, slate, rose, indigo, amber,
  violet and cyan already each mean something specific here (slot status,
  prep column, training, location, freezer) and seven new hues would collide
  with one of those long before they read as a scale. Today used to be a `•`
  on the pill; it is now a ring, since two different dots on one pill would
  be two meanings competing for the same glyph. The same map drives the
  context strip's chips, so a day's mark and its chip can't disagree.

  Matching is exact first, then **longest prefix** — the same widening
  `WORKOUT_BREAKFAST_TYPES` uses — so a future `gym_strength` gets the
  dumbbell and a `cardio_swim` the heart with no edit, and an unrecognised
  type falls back to a generic workout rather than taking the picker down.
  Marks are deduped per day by icon (two gym sessions, one dumbbell) while
  Saturday's gym-plus-HIIT keeps two, and the pill reserves the mark row's
  height whether or not the day trains so the row keeps one baseline.

- **`PlannerState.training_for` exists so the picker can afford this.** It
  reads `training_schedule` alone, no config, because the pills call it for
  all seven days on every repaint — routing that through `day_context` (which
  needs `planning_config()` for its per-meal notes) would have been seven
  `apply_training_adjustments` passes over the week to draw one row of icons.
  `day_context` calls the same method, so the strip and the pills are reading
  one list.

The label is kept in step by `today_view` calling `sync_tab_label()` on every
repaint, rather than by a NiceGUI binding: the label depends on the plan and
on today as well as on the browsed day, so a binding keyed to any one of them
would go stale on the others. The shell injects its `ui.tab` through
`TodayHandles.bind_tab` because `build_today` runs well before the tabs exist
(see `planner_page`'s build order).

## The day inspector

`ui_inspector.py` (phase 4 of `ui-redesign.md`) is a second, floating
consumer of everything the Today tab already knows how to draw for one day:
click a day's telemetry column (the header cell `ui_telemetry.py`'s
`telemetry()` builds per day) and a panel opens over the canvas showing that
day's targets, training/location context, and its four slots — without
leaving the Plan destination or reflowing the grid the click came from.

**Cheap because the Today tab already proved the shape.** `targets_for`,
`totals_for`, `day_context` and `slot_views` all already take a day
argument, so the inspector adds no new data path — it just calls them for
whichever day was clicked instead of `viewed_day()`. Its slot cards,
location/training strip and card badges are the *same* functions
`ui_today.py` uses (`context_strip`, `today_card`, `card_context_badges`,
...), hoisted to module level in that file rather than duplicated here —
`ui_today.py`'s own docstring says so, and is where to look if either
surface's day rendering needs to change. `today_card` is the one that takes
`cards: CardHandles` as an explicit argument rather than a closure, purely
so a second caller can pass it in.

**A true Quasar overlay, not a second drawer.** `ui.dialog()` centers over
the page with a dimmed backdrop and reflows nothing behind it — which is
what satisfies "floats over the canvas, never pushes it" for free, the exact
failure phase 2a's `overlay`-mode drawer fix and phase 3's drawer removal
both exist to prevent (see "Phase 2a's fix..." above). One dialog is built
once and reused for all seven days, keyed off `PlannerState.inspector_day`
— the same one-dialog-reused-by-key shape `ui_cards.py`'s recipe detail
dialog already uses for `.focus`. `inspector_day` repurposes a field that
used to back a per-day *pipeline* dialog phase 3 removed (the 28-chip row
`ui_telemetry.py`'s own docstring describes moving to Settings) — it had
been sitting dead since, reserved for exactly this shape of thing.

**Targets are read-only here on purpose.** Editing a day's target is
`ui_review.py`'s job (the target curve, immediately below) — the inspector
shows `targets_for`/`totals_for` as a bar and links into the review dialog
("Edit targets") rather than growing a second place to type a number, which
would risk the two disagreeing about which value is live.

Registered on its own `"inspector"` refresh topic (so `inspector.open()` can
force a repaint independent of anything else changing) and also rides
`"plan"`/`"targets"`/`"training"`, the same three `today.today_view` already
does — safe here because, unlike `day_target_row`, nothing in this panel
owns a focused input, so a full repaint while it's open never steals a
keystroke.

## The review dialog and the staged-changes bar

`ui_review.py`'s dialog is where every input to the *next* generation lives:
cuisine, western-style share, diet styles, bulk-prep/long-cook, people per
meal, and — folded in from the deleted left drawer by phase 3 of
`ui-redesign.md` — "Daily Targets", "Training Schedule" and "Pantry Clear".
All of it edits `PlannerState`, never the files in `config/`, and is merged
into a config by `PlannerState.planning_config()` — one object carrying the
model, the overrides, the training schedule and the pantry, because
`generate_week_plan`, `validate_week`, `split_targets` and
`inventory_instruction` all read plain config and would otherwise each need
their own patch. Generating is still the only thing in the app that writes
to disk.

- `target_overrides` holds only what **differs** from the file, per day. That
  is what lets the dialog count overridden days, reset them one at a time, and
  leave untouched days following config if config changes. `set_target` clears
  a key whose value matches the file, which is also how the reset button undoes
  itself: it writes the file's numbers back into the inputs and the change
  events those fire cancel out instead of re-creating the override.
- An override wins over `week_plan.targets` in the telemetry header's
  denominator (marked with an amber dot), because the point of editing a target
  before a run is seeing how far the current week sits from where you're about
  to aim it.
- Fat is displayed, never typed — `derive_fat_g` computes it from the other
  three, so an input for it could only disagree with the number the planner
  uses.
- `day_target_row` is built once and mutated in place rather than being
  refreshable: the derived-fat readout updates per keystroke, and repainting a
  section containing the focused input takes the cursor out of the number being
  typed. Only `telemetry` (and, riding along on that same topic,
  `ui_staged_bar.bar` — see below) is refreshed on an edit.

**Daily targets render as a curve — one bar per day, not 21 stacked
spinboxes.** Phase 4.2 of `ui-redesign.md`. Each day is a filled segment
(the base target) with a second, amber segment stacked on top for the
training-uplift portion (`state.planning_config()["training_uplift"]`,
already computed by `apply_training_adjustments` — the curve reads it
rather than re-deriving the split), and a dashed ghost line at the
config.json value once the day is overridden. `targets_editor` computes
`config`/`targets_by_day`/`uplift_by_day`/`max_calories` **once for the
whole row**, not once per day the way the old per-day panel called
`planned_targets` (itself a full `planning_config()` rebuild) seven times
over — the bar-height split needed the uplift figure anyway, and pulling
the shared work up to the row level is strictly fewer calls than before,
not more.

**The numeric inputs did not go away — only the container around them
did.** Calories/protein/carbs are still typed, `dense outlined
debounce=350` `ui.number`s, still wired through the identical
`set_target`/`on_edit`/`sync()`/`on_reset` — a bar reader can *see* the
shape but still needs to *type* a precise number, and there is no
pointer-drag precedent anywhere else in this app to build one on. That
means **`day_target_row`'s build-once-mutate-in-place workaround survives
unchanged** — a focused input is still a focused input, whatever the
container around it looks like. A drag-only control, with no cursor to
steal, would have let this go; that is a real, bigger alternative, deferred
deliberately rather than overlooked — no pointer-drag interaction exists
anywhere in this codebase to build one against, and every acceptance
criterion this phase actually needed (overrides round-trip, reset by
cancellation, override wins in the telemetry denominator) holds either way.
Bar proportions are fixed at row-build time, not recomputed per keystroke,
for the same reason: rescaling all seven columns on every digit typed would
rebuild the section the input lives in.

**The staged-changes bar (`ui_staged_bar.py`) is what phase 3 replaced three
separate "Applies to the next generation only" disclaimers, the amber
telemetry override dot, and the old "edited — not saved" chip with.** One
persistent strip beneath the header, on every destination, reading "N pending
changes — <summaries> · Review · Generate week" (plus "Save changes" when
`state.edited` is among them), or nothing at all when
`PlannerState.pending_changes()` is empty. It counts four independent things
— an overridden day (signed delta against `config["weekly_schedule"]`), an
added/removed/edited training session (diffed against
`_original_training_schedule`, the snapshot `.load()` takes), a non-empty
pantry list, and `state.edited` (grid edits: leftover links, unlinks, skip
estimates, favorite swaps) — and none of the four suppresses or duplicates
another.

**A successful generation deliberately clears only the fourth.**
`target_overrides`/`training_schedule`/`pantry` are never written to
config.json, so a week just generated from an overridden Wednesday is still,
honestly, a week generated from settings that disagree with the file — the
next regenerate uses them again, and the bar is right to keep saying so.
Only the grid-edit entry clears on generation, because saving is what makes
the grid match disk. The bar's own "Discard pending changes" button is
allowed to be stronger: `PlannerState.discard_pending_inputs()` resets all
three of the non-grid categories back to their config/session baseline (the
same numbers a per-day reset button would land on), paired with
`generation.reload_from_disk` for the grid-edit quarter — a button sitting
directly beside "Mon +700 kcal" has to make that line go away too, not only
the part `reload_from_disk` alone ever touched.

**"Save changes" (`ui_generation.save_grid`) is the other way the grid-edit
quarter clears, and it is deliberately narrower than "Generate week."** A
swap, a leftover link/unlink or a skip estimate is fully decided the moment
it's clicked — `state.week_plan` already reflects it — so nothing about it
needs the LLM in the loop, and before this button existed the *only* path to
disk ran through a full multi-minute re-plan of every meal type just to keep
an edit that generation itself had no part in making. The button is
conditional on `state.edited` alone, not on the other three
`pending_changes()` categories, because those are inputs to the *next*
generation and were never candidates for a plain disk write — "Review" is
still the only way to act on them. `save_grid` writes `state.week_plan`
straight to `REPOSITORY.save_week_plan`, exactly what `run_generation` does
after its model call, and then sets `state.edited = False` directly rather
than calling `adopt_plan` — `adopt_plan`'s job is discarding unsaved edits by
replacing `week_plan` with a freshly generated one, and a same-object persist
is the opposite of that. It also does not call `record_week_history`: that
records what the *model* chose for next week's rotation, and a grid edit
makes no new choice for it to remember — the favorite or leftover it wrote
down already came from history-aware selection upstream.

**The rail carries a "Generate" button independent of the bar**, and this is
deliberate, not redundant: the bar hides entirely when nothing is pending,
which is the common case on a fresh page load, and
cuisine/diet-style/bulk-prep/servings picks in the review dialog don't count
toward `pending_changes()` at all (see above) — so without a second entry
point, there would be runs where the *only* way to reach "Generate" is
inside a bar that isn't there. Both buttons open the same dialog; the bar
additionally offers a "Generate week" shortcut that skips straight to
`generation.run_generation` for when something is already staged and the
current picks are fine as they are.

This button lived in the Plan destination's own header row until phase 6b
moved it to the rail. Same reason, better satisfied: a rail button is
visible from all six destinations, not only from Plan.

### Garmin's recorded week, offered against the declared one

The Training Schedule section opens with a proposals block above its editable
rows — `proposals_block()` inside `training_editor`, deliberately not a
refreshable of its own: accepting a proposal changes the rows underneath it,
and a separately-refreshed block would leave a suggestion on screen for a
session already in the list below.

Each row is an icon pair, a title, its evidence and two buttons. **Colour
carries none of it** — `add`/`remove` says which direction the proposal goes
and `training_icon` says what kind of session it is, which is the same
division `TRAINING_TYPE_ICONS` already relies on and the reason no new hue
was needed: every hue in `ui_theme.py` is spoken for twice over.

The wording lives in `ui_state.training_proposals_view`, not here, on the
`adaptive_tdee_view` precedent: three of the feature's states produce no
proposals — nothing recorded, not enough history, and "your week already
matches" — and a bare empty list spells all three identically, with the good
one the most likely to be misread as broken. All three print.

Accept is the one control in this dialog that writes to disk
(`PlannerState.accept_training_proposal` → `save_config_keys`), and its own
copy says so. Dismiss is session-local. Both refresh `"training"`; accept
also refreshes `"targets"`, because an accepted session expands that day's
budget and pins a meal exactly as a typed one does. The engine behind them is
CLAUDE.md's "Proposing the week you actually trained".

## Insights

Five readouts — weight against target with the weigh-in table under it,
planned calories against logged, macro accuracy, adherence tiles. CLAUDE.md's
"Insights: five readouts" carries the reasoning; what matters when editing
this module is that **it decides nothing**. Each section asks `ui_state` for
an `InsightPanel`, prints `headline` and `detail`, and draws a chart only
where `.drawable` is true. A threshold, a percentage or a caption computed in
`ui_insights.py` would be the one string on the page no test could reach.

Four things here will look removable and are not:

- **`chart_scaffold()` in `ui_theme.py`, not per-chart options.** A chart
  reads as part of this UI only while its gridlines, tick colour and tooltip
  ground match the panels around it exactly — the argument `MONO_SECTION_LABEL`
  already makes for the expanded card's headings. The macro chart *swaps* the
  scaffold's two axes rather than hand-building a horizontal one, for the same
  reason.
- **`itemStyle` alongside `lineStyle` on every line series.** ECharts takes
  the legend swatch from the former. Set only the latter and it labels your
  white trend line with a blue chip from its own default palette.
- **No legend on the intake chart.** Its bars are `macro_band`-tinted per
  day, and one swatch cannot stand for five fills without being wrong about
  four. The encoding is in the panel's caption instead.
- **`view.labels` for a category axis, `view.dates` for anything else.**
  The short `24 Aug` form is built in the view model beside the ISO strings
  the table prints and every date match keys on.

Colour follows the palette contract with no additions: `CHART_MACRO_COLOURS`
is `MACRO_TINTS` in hex (categorical), a logged bar takes `BAND_COLOURS`
(semantic), everything structural is slate, and the *reference* series — a
target, a plan — is always the dashed one.

The panel is `@ui.refreshable` and registered on `"plan"` and `"adherence"`.
It owns no input, so the focus-theft trap that keeps other sections off
`"plan"` does not apply.

## Settings' Daily Targets panel

Where each macro's number comes from, and one of the two controls in the app
that write to `config/` — the other is accepting a Garmin schedule proposal
in the review dialog's Training Schedule section, which persists
`training_schedule` on the same "a standing setting is not a per-week input"
reasoning. Five rows — calories, protein, carbs, fat, fibre — each naming
its source, its current figure for the week (collapsed to one number when
every day agrees, a range when they don't) and, for the two on `auto`, the
engine's own arithmetic behind it: "Katch-Mcardle BMR 1798 → TDEE 2472
(formula) − 738 deficit (99.4 → 80.0 kg)".

Calories and protein carry an Auto/Manual toggle; the other three carry a chip
saying they have no mode to switch and a sentence saying why (see
`TARGET_SOURCE_ROWS` in `ui_theme.py`). **Naming all of them rather than only
the two with a control is the point** — the question the panel answers is
"where does this number come from", and it has an answer for every figure the
week is planned against.

**Fibre is a row because it became one of those figures.** It arrived with the
daily fibre target and could not be left off: a number the telemetry header
prints as `FIB 24/30g` and whose origin is stated nowhere is the exact gap
this panel exists to close. It reads "Fixed" rather than "Yours", via
`DERIVED_TARGET_MACROS` — a named tuple rather than the `macro == "fat_g"`
equality test that was there, which is how the row would silently have
claimed somebody typed a figure the engine computes.

Per-day number inputs appear under carbs always, and under a switchable macro
once it is manual. They are debounced and written through
`save_manual_targets` on change rather than per keystroke, and the section
deliberately does not repaint on an edit — the same focus-theft rule
`ui_review.day_target_row` follows. Fat gets no inputs at all: an editable fat
would be a second answer to what `derive_fat_g` already computes.

Registered on its own `"settings"` topic (its toggles' own repaint, since
switching to manual makes a row grow inputs) *and* on `"targets"`, because a
review-dialog override changes the figures it reports even though nothing
here was touched.

## Settings' three read views

Phase 6e of `ui-redesign.md`. The Settings destination's integrations list
(`PIPELINE_STAGES`) used to be four static connected/not-connected rows.
Three of them now open a read-only `ui.dialog` over data the app already
reads on every generation and displayed nowhere:

| row | dialog | reads |
|---|---|---|
| Biometric Sync | which days each stored list has | `biometrics.json`'s `weigh_ins`/`daily_actuals`/`readiness_log`/`sync_checkpoints` |
| Calendar/Location | where each day is spent, and what that constrains | `schedule.json`'s `base_schedule`/`location_rules` |
| Adaptive Workout | the week's sessions, type/time/duration/burn | `PlannerState.training_schedule` |

**All three are reads, and the row that owns a piece of state keeps owning
it.** Nothing here triggers a sync, writes a config file or edits a session —
training is still edited in `ui_review.py`, and fetching a missing day is
still the sync CLI's job. That is the same division the day inspector already
draws with the review dialog ("Targets are read-only here on purpose"), and
it is what keeps these pages from becoming a second place a value can be set
and therefore a second value to disagree with the first.

**Dialogs, not more sections in the panel**, on the maintainer's call
(ISSUES.md item 8 asks for a "popup/page" for each): the rail is deliberately
six destinations, these are reference views rather than places to work, and
three tables stacked under the panel's three selects would bury the selects.
Each body is `@ui.refreshable` and repainted by `open_stage` *on open* rather
than registered on a refresh topic — two of them read live state
(`training_schedule`, `planning_config()`) and one reads the clock, and a
dialog that is closed almost always has no business being repainted by every
edit that touches them. Repainting on open is also what stops a tab left open
overnight drawing yesterday's 14-day sync window.

**Two `PIPELINE_STAGES` rows said "not built yet" long after they were
built**, which is what a dialog full of live data made impossible to leave
standing: `sync` described a Health Connect feed that never landed while the
Garmin/Cronometer sync that did has been writing `biometrics.json` since (see
"Biometric sync"), and `context` predated `week.apply_location_modes`. Both
are `connected=True` now, and `connected` means "something real reaches a
plan from this" rather than "this is finished" — hence descriptions that name
what each still doesn't do (no calendar integration) instead of a third flag
state to interpret. `readiness` is the one genuinely unbuilt stage and stays
a plain, unopenable row.

### What the sync view actually answers

`ui_state.sync_status(biometrics, today, window_days)` is the view model —
pure, clock-free (today is a parameter) and tested, per the standing rule
that logic worth testing leaves the widget module. It reports each stored
list's source checkpoint, its own newest row, and the last 14 days classified
three ways:

- `SYNC_RECORDED` — a row exists for that date.
- `SYNC_CHECKED` — inside the checkpoint, no row: the sync asked and found
  nothing. A forgotten weigh-in or an unlogged day, which is a real answer.
- `SYNC_UNCHECKED` — past the checkpoint: nobody has asked yet.

**The third state is the whole point, and it only exists because
`sync_checkpoints` does.** Those two "no row" cases are identical in the file
otherwise, and that indistinguishability is precisely what
`save_sync_checkpoint` was added to fix for `get_sync_date_range`'s catchup
walk — reading it from the other end is what lets this page tell a scale
nobody stood on from a fortnight nobody synced. The constants and their
styles live in `ui_theme.py` beside `STATUS_*`/`STATUS_STYLES`, same split
and same reason; fill and outline carry the distinction rather than three
hues, per that file's own "icon, not colour" rule.

Four details are decisions:

- **One card per stored list, not per source.** `weigh_ins` and
  `readiness_log` come from one Garmin login and share its checkpoint, but a
  morning nobody stood on the scale is not a night nobody wore the watch, and
  one merged row could only report the weaker of the two. `shares_source` is
  true for exactly those cards so the page can say once that a `last checked`
  they hold in common comes from a single sync, and `SYNC_SECTION_LABELS`
  names them, since labelling by source would print "Garmin" twice.
- **`last_checked` and `last_recorded` are separate fields, not one
  "latest".** The gap between them is the information: checked through
  Wednesday with the last weigh-in on Sunday is three mornings nobody stood
  on the scale, which is a different situation from a source nobody has
  synced since Sunday, and only both numbers side by side tell them apart.
- **A source's effective checkpoint is the later of its checkpoint and its
  newest row**, mirroring `get_sync_date_range`'s own `max(dates +
  [checkpoint])`. `sync_checkpoints` postdates the two lists, so a file
  written before it existed — or hand-edited since — has rows a checkpoint
  doesn't cover, and a stored row is proof the day was asked about.
- **It never computes what a sync *would* fetch.** That is
  `get_sync_date_range`'s job — it caps its walk and anchors on whichever
  *requested* source is furthest behind — and a second answer to the same
  question is exactly the duplication the `/api/recipes` finding recorded.
  `SYNC_WINDOW_DAYS` (14) is deliberately the same horizon as the CLI's
  `--lookback-days` default, but as a display choice, not a coupling.

One line sits above those cards and answers a question none of them can:
`sync_freshness`, whether the scheduled job is running at all. It is a
separate view model rather than a fourth card because it reads checkpoints
alone where these read rows and checkpoints together — see "Nothing syncs
from the app" under "Biometric sync" for why that difference is the whole
point of it.

`BIOMETRIC_SECTION_SOURCES` (which source fills which list) moved from
`sync_service.py` to `repository.py` for this — it is a fact about the file's
layout, the same kind of thing `BIOMETRIC_SECTIONS` beside it already states,
and it now has two readers with nothing else in common. A second copy in the
UI would be free to disagree about which source writes what, and would do so
silently: the read view would simply report the wrong list as empty. **It is
one-to-many since `readiness_log` arrived**, and both readers had to stop
assuming otherwise — see "Biometric sync" for what each had to change.

### The location and workout views

Both reuse `ui_today.py`'s module-level render helpers (`location_row`,
`session_chip`) rather than growing a second way to draw a location chip or a
session — the same reuse `ui_inspector.py` already makes of that file, which
is why those helpers are module-level in the first place.

`ui_state.location_view(config, meal_types, day)` was split out of
`day_context` for this: `day_context`'s per-meal training notes are only
reachable through `planning_config()`, so seven days of it would be seven
`apply_training_adjustments` passes over the week to print a table of default
locations. The page pays for one config instead. `LocationView` gained
`skip_estimates` at the same time — `<meal_type>_skip_estimate` for the meals
a location skips, off the same rule `meal_modes` already comes from, because
a skip carrying an estimate is a meal that was *eaten*, not one that was
missed. Printing MODE_SKIP's "not planned" beside "eaten out, ~795 kcal"
would be two clauses contradicting each other on one line; the page says "not
cooked, but eaten" instead.

Both pages print the bare weekday name rather than `format_day_label`'s dated
one, deliberately: `base_schedule` and `training_schedule` are keyed by
weekday and apply to every week, so dating them against the loaded plan would
read as a claim about this particular Monday.

## Shopping list — a drawer *and* a destination

A right-hand slide-over (opened from the rail's action block) rather than a
dialog: the list
is read *against* the grid, and a modal would cover the week it describes. One
section per `shopping_windows()` trip, grouped into departments by
`aggregate_cook_events` — by cook day, never eating day.

**And a sixth rail destination, drawing the same panel.** CHANGE-QUEUE.md
asked whether shopping should be promoted; the answer is `both`, because the
two are different jobs. 420px beside the grid is the right shape for reading a
trip against the week it belongs to — which is the drawer's whole documented
reason for existing and is why it survives — and the wrong shape for working
through one. `build_shopping(ctx)` returns `build_panel`, which the
destination calls at its own render position.

Three things about that sharing are load-bearing:

- **`@ui.refreshable` binds to where it was first called**, so the drawer and
  the destination need one instance each. `ShoppingPanels` is a small handle
  holding both and exposing a single `.refresh()`; that one object is what
  `ui_app.py` registers on `"plan"`/`"shopping"`/`"shopping_days"`. The
  registration block runs *before* the rail is built, so the destination's
  instance does not exist yet at registration time — a list that is appended
  to later is the fix, and it keeps a detail private to this module from
  becoming an ordering rule in the page shell.
- **The Daily-shop toggle lives inside the refreshable, not beside it.** Two
  instances both read `state.daily_shop_mode`, so flipping it in the drawer
  has to move the switch on the destination. A control built once outside
  would be the "second control free to disagree" objection the week select
  already answers by repainting the first rather than adding a second.
- **Nothing else moved.** One builder means the two cannot come to differ
  about a trip — the `ui_inspector.py`-reuses-`ui_today.py` precedent.

It is derived from the plan on every repaint, so a leftover link that grows a
batch also grows the quantities to buy. Days in `WeekPlan.failures` get an
explicit note per window, because a short list is otherwise indistinguishable
from a cheap week.

**The ticks are still not persisted, and they now survive a repaint.** Those
are different claims and only the first had ever been decided: storing them
would be more state able to disagree with `week_plan.json` and that argument
stands, but they were living in the DOM inside a `@ui.refreshable` registered
on two topics, so any edit that repainted wiped them **mid-shop**.
`PlannerState.shopping_ticks` is the middle path — per-client, dies with the
tab, never reaches `data/`. Keyed `(window label, item name)`, because
`aggregate_cook_events` combines by normalised name so a name is unique within
one window, and the same ingredient on two trips is two purchases that must
tick independently.

**The department header is a band with a count, and that is what makes it a
header.** It was 10px uppercase slate-400 with nothing else on the row, which
is skimmable straight past in a column of 10px item labels; a count is
information no item line could carry, and a `border-b` separates the groups
without spending a hue. The same problem is far sharper in the Keep copy,
where every pasted line becomes a checkbox — see CLAUDE.md's "The order a list
is walked, and the two lines that are not items".

**The ⏳ went with the rest of the emoji.** It was the last one in the front
end, sitting inline in a checkbox *label string* rather than as an element,
which is how it survived v0.38.0's pass. The buy-late and pantry notes are now
a `TEXT_MICRO` slate-400 label under the checkbox — no hue, per the palette
table, since amber already means five things.

"Copy for Keep" uses `format_shopping_list_keep` (one line per item, since Keep
turns each pasted line into a checkbox) and `ui.run_javascript`. Two things
there are load-bearing: the payload goes through `json.dumps` because it is a
JS string literal and an apostrophe in an ingredient name would end it early,
and there is an `execCommand` fallback because `navigator.clipboard` doesn't
exist outside a secure context — this server is often reached on a LAN address
over plain HTTP.

One NiceGUI trap worth remembering: `props()` silently **drops** an unquoted
value containing brackets, so a Tailwind class in a Quasar prop must be quoted
(`header-class='text-[11px]'`), or it never reaches the component at all.

Two things it does differently from the old Streamlit app, both worth keeping:

- NiceGUI page handlers run *on* the event loop, so it `await`s the repository
  directly. **Do not use `repository.run_sync()` here** — it detects the
  running loop and hands the coroutine to a scratch thread, which is pure
  overhead when the caller is already async.
- There is no re-run, so there is no session-state cache to defend. UI widgets
  bind to a per-client `PlannerState` and structural changes call
  `.refresh()` on the `@ui.refreshable` sections that depend on them. Note
  that `bind_value`'s own sync back to `state` runs *after* a widget's
  `on_change` handler, not before — `ui_settings.py`'s week-start select sets
  `state.week_start` explicitly at the top of its handler before calling
  `refreshables.refresh("plan")`, rather than trusting the binding to have
  already landed it, or the repaint would still be reading the old week
  order.

`PlannerState.slot_views()` flattens both a generated `WeekPlan` and an
un-generated `WeekSpec` into the same `SlotView` shape, so the card widget has
one code path and a cold start previews the planned week rather than rendering
28 empty cells.

## Printing and PDF export

One path, not two. There used to be a second "Print Menu" button that called
`window.print()` against a `@media print` stylesheet (`print_css()`) — it
printed whatever the dashboard happened to render (drawer icons, macro bars,
dish names with no ingredients), which was a strictly worse document than the
PDF sitting one button over, and having both meant two things to keep
formatted well instead of one. `print_css()` and the CSS-only path are gone;
the "PDF menu" button now triggers the same download as before.

- **The "PDF menu" button** (the rail's action block; it was a printer icon
  in `ui.header()` until phase 6b) downloads `weekly_menu.pdf` —
  `export_menu.build_week_menu_pdf()` does the formatting — it reads
  `WeekPlan.slots`/`WeekPlan.by_slot()` directly, the same source
  `planner.day_slot_macros` does, not `PlannerState`/`SlotView`, so the
  module has no UI dependency and would work the same from a future CLI
  export flag. It needs `reportlab`: pure Python, so it installs into this
  venv with a plain `pip install` — unlike `weasyprint`, which needs
  Cairo/Pango system libraries this project doesn't otherwise depend on.
  `format_week_menu_markdown()` in the same module is the Markdown
  equivalent, sharing the per-slot walk (`_slot_entry`) so the two formats
  can't silently disagree about what a slot says. It has no button today —
  it is kept as the text-pipeline counterpart (diffing two weeks, pasting a
  menu into a note) and is the reason `_slot_entry` is factored out at all. Printing this document is
  then just whatever the browser's own PDF viewer does with a print
  command — no separate print stylesheet to keep in sync with the app's
  actual look.
- **The PDF itself** is a day-by-day summary grid (meal types across the
  top, days down the rows), an optional Sunday prep checklist, one page per
  recipe grouped into a section per meal type, and a department-grouped
  shopping list at the end — restrained dark-ink typography and
  hairline-ruled ingredient lists, styled after the CSIRO Total Wellbeing
  Diet's printed meal plans.

