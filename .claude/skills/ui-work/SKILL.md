---
name: ui-work
description: The NiceGUI front-end contract for this project — the type/spacing/radius scale, what each colour is allowed to mean, the NiceGUI and Quasar traps that have each cost a debugging session, refresh topics, and which module a change belongs in. Load before editing any ui_*.py file (ui_app, ui_theme, ui_state, ui_cards, ui_plan, ui_today, ui_review, ui_settings, ui_telemetry, ui_shopping, ui_inspector, ui_catalog_browser, ui_insights, ui_staged_bar, ui_generation, ui_context, ui_catalog, ui_adherence), or when changing anything about the web UI's layout, styling, colours or widgets.
---

# The NiceGUI front end — contract and traps

`ui_app.py` is a page shell; every other `ui_*.py` is one concern exposing a
`build_*(ctx)` factory. This file is the presentation contract and the traps —
the things a cold session will otherwise get wrong twice. **Read it before
touching any `ui_*.py`.**

`architecture.md` beside this file is the design record for the surfaces
themselves — what each destination is and why it is shaped that way. Read the
section you need from it; you rarely need all of it:

| if you are changing | read in `architecture.md` |
|---|---|
| the week grid, the header, scroll alignment | "NiceGUI front end" |
| any card, the recipe dialog | "The expanded recipe card", "Module layout" |
| the rail, its buttons, a destination | "The rail's action block", "Module layout" |
| the Today / Daily View destination | "The Today tab" and its three subsections |
| the day inspector | "The day inspector" |
| the review dialog, staged-changes bar, target curve | "The review dialog and the staged-changes bar", "Discard asks first" |
| the Plan panel's banners, the empty state | "Two things a Plan panel says in front of its grid" |
| the generation progress dialog | "The generation dialog says which meal types are banked" |
| Insights, any chart | "Insights" |
| Settings | "Settings' Daily Targets panel", "Settings' three read views" |
| the shopping drawer | "Shopping list drawer" |
| PDF / Markdown export | "Printing and PDF export" |
| tokens, the scale's history | "The type, spacing and radius scale" |
| surfaces, elevation, a fill that vanished | "The three surfaces" |

## Where a change goes

- **`ui_theme.py`** — presentation constants and pure render helpers. Nothing
  here may import or read `PlannerState`. Every function takes its inputs as
  arguments.
- **`ui_state.py`** — the view model, and the *only* UI module with tests.
  Logic worth testing belongs here or in a pure helper, never in a widget
  module. If you find yourself wanting to test a `build_*` function, that is
  the signal to move the logic down, not to grow a NiceGUI test harness.
- **everything else** — widget construction. Untested on purpose: asserting
  on element trees pins the layout rather than the behaviour.

## Identity — the app is called Larder

`APP_NAME`, `APP_MARK_ICON`, `APP_FAVICON` and `APP_TITLE` in `ui_theme.py`.
Never spell any of them at a call site: the name reaches the browser tab, the
window title and the header wordmark, and a fourth literal is a fourth thing
to miss.

The wordmark is `restaurant_menu` in `ACCENT_MARK_CLASS` beside the name in
`text-slate-100`, at the top-left of `ui.header()` — **not** in the rail, where
CHANGE-QUEUE.md originally filed it. Two reasons, and the second is the one
worth remembering: `ui.header()` is `position: fixed` (the whole reason the
week grid needs `WEEK_GRID_HEADER_INSET_STYLE`) while the rail is not, so a
rail wordmark scrolls off on the first scroll of a 28-card grid. The item's
stated premise — that the app named itself nowhere on screen — was also simply
untrue when it was written; an icon and a title string had sat in the header
since before v0.23.0. What was missing was a *name* rather than a place to put
one.

## The type scale — four sizes

Established by phase 1 of `ui-redesign.md`. Until that phase lands you will
still see legacy literals; **do not add new ones**, and convert any you touch.

| constant | value | for |
|---|---|---|
| `TEXT_MICRO` | `text-[10px]` | data figures, chip and badge labels, link lines, captions |
| `TEXT_BODY` | `text-xs` (12px) | the default — labels, inputs, card titles, list rows |
| `TEXT_HEAD` | `text-sm` (14px) | section headings, day names, dialog section titles |
| `TEXT_DISPLAY` | `text-lg` (18px) | dialog titles, a recipe name in the detail view |

Nine sizes crammed into a 6-pixel band (`text-[8px]` through `text-base`,
with 56 uses of `text-[10px]` and 35 of `text-[9px]`) is what this replaces.
That was noise, not hierarchy — no two of those sizes were distinguishable at
a glance. **Weight and colour carry the rest of the hierarchy**, not a fifth
size.

`TEXT_MICRO` is **10px and stays 10px** until somebody does the measuring pass
CHANGE-QUEUE.md files separately for it. It is one line to change and entirely
verification to ship: `RAIL_WIDTH_PX` is pinned at 168,
`WEEK_GRID_HEADER_INSET_STYLE` derives the header grid's position from it, day
columns floor around 110px, and `ui_cards.meal_card`'s status badge row is
already "the one row on the card with no width to spare". A 10% bump on the
most-used size in a nine-column layout can reflow all of it.

### The two faces those sizes render in

`UI_FONT_STACK` and `FIGURE_FONT_STACK`, emitted by `ui_theme.typography_css()`
from `ui_app.py`'s single `add_css` call. Before them `grep font-family src/`
returned nothing at all: everything rendered in Quasar's default Roboto and the
39 `font-mono` figures in whatever monospace the viewer's OS picked.

| | |
|---|---|
| **System stacks, never a webfont** | Nothing else on this page needs the network — no CDN anywhere, `whfoods.json` ships in the repo, and the only outbound call is OpenRouter's, from the *server*. A Google Fonts link would make the front end the one part of the app that fails offline. |
| **Custom properties, not literal stacks** | The figure face is applied by redefining what `.font-mono` resolves to, which puts all 39 existing figure sites on it with no call site touched. |
| **`:where()` on the Quasar selectors** | Quasar sets `font-family` on its own components, so `body` alone loses every input, button and tab label. `:where()` carries zero specificity, so a component that genuinely needs its own face still wins without an `!important` arms race. |
| **`tabular-nums` at the root, not on a class** | The `font-mono` figures already align — every glyph in a monospace face is one width. This is for the telemetry header's `1722/1850`, the card macro pills and the Insights captions, which are proportional, so a `1` is narrower than a `7` and a column of them shimmers as the week regenerates. Declared at the root because this app is labels and numbers almost end to end: there is no prose for it to cost anything on, and the alternative is a class every such site has to remember. |

## Spacing — five steps, each with a job

| constant | value | for |
|---|---|---|
| `SPACE_HAIR` | `0.5` (2px) | inside a chip or badge; icon to its own label |
| `SPACE_TIGHT` | `1` (4px) | between rows inside one card |
| `SPACE_BASE` | `2` (8px) | between cards, between form fields |
| `SPACE_SECTION` | `3` (12px) | between sections in a panel or dialog |
| `SPACE_PAGE` | `4` (16px) | dialog padding, page gutters |

**Space siblings with the parent's `gap`, not per-element margins.** Margins
collapse and double in ways a gap does not, and they put the spacing decision
on the child rather than on the layout that owns it. Write new code this way.

The scattered `mt-1`/`mt-2`/`mb-1`/`mt-0.5` still in the current code is
legacy, and is **deliberately not phase 1's job** — converting a margin to a
parent gap moves layout, and phase 1's premise is that nothing moves. Phase 2
handles it, while it is restructuring containers anyway.

`1.5` is not a step. Where it appears in legacy code it resolves **down** to
`SPACE_TIGHT`: it is 6px, exactly halfway between the `1` and `2` steps, so
"nearest" is undefined, and the dense card interiors where it appears cannot
absorb growth from both a larger type scale and a wider gap at once.

## Radius — three values

`rounded` (cards, boxes, inputs) · `rounded-lg` (dialogs, panels) ·
`rounded-full` (bars, dots, pills). `rounded-md` and `rounded-xl` are not
used; both currently appear and are legacy.

## Elevation — three surfaces, and no border may say which

`SURFACE_PAGE` / `SURFACE_PANEL` / `SURFACE_INSET` / `SURFACE_CARD_LIFT` in
`ui_theme.py`, plus `surface_css()` for the one that is set on `body`.

| | is | painted on |
|---|---|---|
| `SURFACE_PAGE` | slate-950, via `surface_css()` on `body` | the ground. It shows in the gutters and between regions and never holds content |
| `SURFACE_PANEL` | `bg-slate-900` | the header, the rail's wrapper, `ui.tab_panels`, every dialog, the shopping drawer, the canvas's sticky meal-type gutter |
| `SURFACE_INSET` | `bg-slate-950/30` | a box recessed *into* a panel — a settings read view, an Insights chart frame, the Plan empty state |
| `SURFACE_CARD_LIFT` | `shadow-sm` | a meal card, raised off its panel |

The app had exactly one of these before: the ground was Quasar's own
`#121212`, the tab panels were `bg-transparent` on top of it, and there was a
single `shadow-*` class in the whole front end. Nothing read as foreground.

Four things about it are worth not re-deriving:

- **A card is `SURFACE_PANEL` plus its status tint, and that is free.** Every
  `STATUS_STYLES` fill is translucent, so painting the panel slate-900 put
  each tint onto slate-900 with no call site touched.
- **Elevation is fill and shadow, never border.** A card's border and 3px left
  accent are structural colour (four slot statuses); a neutral border bright
  enough to read as raised would be a fifth meaning on that exact edge.
- **Two fills had to move with the ground, both for the same reason.** A
  translucent fill is only visible against what is behind it: the cook tint
  went `emerald-400/[0.07]` → `/[0.12]` (the one card that costs you an
  evening was the least distinguishable thing on the page), and skip went
  `slate-900/40` → `slate-950/40`, since at slate-900 over a slate-900 panel
  it composited to *exactly nothing*. Anything at `/60` or below over the old
  ground is worth re-checking against the new one; `ui_insights`' adherence
  tiles and `ui_catalog_browser`'s row hover were the two that vanished.
- **The rail's surface is a wrapper div, not the `ui.tabs()`.** `.q-tabs`
  carries a height of its own and ignores `self-stretch`, so the painted
  column stopped under the last tab. That was invisible while the ground was
  `#121212` and is not now — a general lesson: raising the contrast between
  surfaces exposes every element that was sized to its content rather than to
  its column.

## Colour: structural, semantic and categorical are three different things

Three roles, and they must not borrow each other's hues:

- **Structural** — `STATUS_STYLES` (emerald cook / sky leftover / slate skip /
  rose not-generated) and `PREP_COLUMN_ACCENT` (indigo). These say *what a
  thing is*. A new UI element that borrows "cook" green reads as a fifth slot
  status.
- **Semantic** — `BAND_COLOURS` (on / near / off target). These say *how it is
  going*.
- **Categorical** — `MACRO_TINTS` (which macro). These say *which of several*.

### The palette — each hue means at most two things

Resolved by CHANGE-QUEUE.md's amber/violet pass. Before it, amber carried
**eight** meanings (the five that were documented, plus the favourite star,
the shopping list's buy-late flag and the recipe dialog's prep note) and
emerald had quietly reached four. Both grew *after* the collision was first
recorded, which is the argument for keeping this list current.

| hue | may mean | and nothing else |
|---|---|---|
| **amber** | a staged/overridden reading · `BAND_COLOURS`' near-target band | the two never co-occur: the band only fills a telemetry bar, the other only marks a label or chip |
| **emerald** | a cook slot (`STATUS_STYLES`) · on-target (`BAND_COLOURS`) | |
| **sky** | a leftover slot · protein | |
| **rose** | a failed slot · off-target | |
| **violet** | fat · location (`LOCATION_ACCENT`) | |
| **orange** | carbs | |
| **cyan** | fibre | |
| **indigo** | the prep column | |
| **teal** | the brand accent (`ACCENT_*`) | one meaning, not two: *this is Larder talking* — the wordmark, the Generate button, and the checked state of a control |
| **slate** | *the neutral ground, not a meaning* | anything subtracted from a hue lands here |

**Adding a third meaning to any of these is the specific thing not to do.** If
a new element needs an accent, check this table first; the answer is usually a
glyph and `slate`.

What moved, and what carried the meaning instead — every one of these had a
shape already doing the work, which is why the hue was removable:

| was | now | carried by |
|---|---|---|
| training (amber, everywhere) | slate | `TRAINING_TYPE_ICONS`' glyph, which already distinguished the *kind* |
| fridge / freezer badge (amber / cyan) | both slate | the glyph in the label — this is what freed cyan for fibre. Was ⚡/❄️; now `kitchen`/`ac_unit`, see below |
| favourite star (amber) | slate | `bookmark` vs `bookmark_border` — filled vs outline |
| buy-late (amber) | slate | the ⏳ and the "buy fresh closer to the day" annotation |
| recipe prep note (amber box) | slate | the `inventory_2` icon |
| carbs (amber) | orange | — a macro is categorical; it needed a hue, just not that one |
| fibre (emerald) | cyan | — same |
| edited training session (emerald marker) | amber | the glyph: `tune` override, `fitness_center` training. Both mean "measured against a live preview", so one colour and two glyphs is the honest encoding |

**Icon, not colour, distinguishes members of a set.** `TRAINING_TYPE_ICONS`
is the precedent and `ADHERENCE_MARK_ICONS`/`ADHERENCE_SOURCE_ICONS` are the
newest application — three meal marks and two completion sources, all slate,
because emerald (the obvious tick) is the cook status and a green check on a
card would read as a fifth slot state. Set versus unset is the fill-and-weight
distinction `bookmark`/`bookmark_border` already draws. The reasoning is in
`TRAINING_TYPE_ICONS`' comment: every hue above is
spoken for, so seven new ones would collide with an existing meaning long
before they read as a scale. Match exactly first, then longest prefix, and
never raise on an unknown key.

**Known residue, outside `ui_theme.py`.** Emerald still marks an integration
as connected (`ui_settings.py`), a ticked step in the recipe dialog
(`ui_cards.py`), and several `hover:` affordances. The pass was scoped to the
theme module's constants, which is where the acceptance criterion drew the
line; these are named here rather than silently left.

### The accent is teal, and it is one meaning

`ACCENT_HUE`, `ACCENT_MARK_CLASS`, `ACCENT_BUTTON_PROPS`,
`ACCENT_BUTTON_CLASSES`. Teal was **already in the app in five places** — the
Shopping rail button, the shopping drawer's checkboxes and three review
controls — picked one widget at a time, in no palette table, meaning nothing in
particular. Naming it is what turned five accidents into one token, and it is
why the row above reads as a single meaning rather than two.

Two rules:

- **The Shopping button had to give the hue back.** It sat beside Generate as
  the second of "the week's primary verbs", both un-flat and each in a
  different saturated colour, so the two competed rather than ranking. It is
  outlined now: **filled accent (Generate) > outlined slate (Shopping) > flat
  slate (the three exports)** — the same fill-versus-outline distinction
  `bookmark`/`bookmark_border` draws for a favourite.
- **Do not spread it.** An accent on every button becomes a tenth structural
  meaning ("clickable"), which the rail's flat slate tabs already communicate
  by contrast.

Spelled bare — `color=teal`, not the equivalent `teal-6` — so one grep finds
every use and the two spellings cannot drift.

### The contrast floor: `text-slate-400` is the dimmest text there is

`text-slate-500` and `text-slate-600` are **retired from text** and must not
come back. Measured on `slate-900`:

| | ratio | verdict |
|---|---|---|
| `slate-600` | 2.3:1 | was the app's most common pairing with `TEXT_MICRO` |
| `slate-500` | 3.7:1 | under AA at every size this app uses — `TEXT_BODY` is 12px |
| `slate-400` | 6.9:1 | the floor |

All 110 sites moved in one pass. CHANGE-QUEUE.md filed the rule as "no 600 at
any size, no 500 at `TEXT_MICRO`"; it went further because 500 at `TEXT_BODY`
is also under AA, and a mixed rule produces the odd inversion of a 10px label
sitting *brighter* than the 12px label beside it.

Three sites where low contrast was carrying meaning kept the meaning by other
means, which is the same "shape, not hue" move as everywhere else here: a
completed recipe step is `slate-400` **plus** `line-through` against
`slate-200`; an unset favourite or adherence mark is `slate-400` against
`slate-200` **plus** the outline-vs-filled icon.

**Charts split the constant rather than following the rule blindly.** WCAG asks
4.5:1 of *text* and only 3:1 of a *graphical object*, and `slate-500` sits
between the two. So `CHART_AXIS` (slate-400) is the axis, legend and markLine
*labels*, and `CHART_MUTED` (slate-500) stays the reference *series* and its
markers — which is what stops the planned line brightening into competition
with `CHART_INK`. The dash, not the tint, is what distinguishes it.

### Emoji are not glyphs — use Material icons

The prep badges were `⚡ Prepped on Sun` / `❄️ From Freezer` and the telemetry
day marker was `•` / `⚡`. All four are Material icons now: `kitchen` /
`ac_unit` and `tune` / `fitness_center`.

- **Emoji render in the platform's own emoji font, at its metrics and its
  colours.** ⚡ arrived amber-yellow on macOS and flat blue on Windows —
  reintroducing hues this module had spent a whole pass removing, in the exact
  two badges whose *justification* for going slate was that the glyph carried
  the distinction.
- **Each pair moves together or not at all.** ⚡/❄️ are one set (fridge vs
  freezer) and •/⚡ are another (which staged reading this is); swapping one
  member leaves a set half-distinguished by a glyph and half by a colour that
  no longer exists. And the ⚡ in the two pairs was never one symbol — it meant
  "prepped ahead" in one and "training" in the other, in adjacent surfaces.
- **Reuse the vocabulary.** `fitness_center` is deliberately the icon
  `TRAINING_TYPE_ICONS` already uses for a gym session and as its fallback for
  any unrecognised type. `kitchen` is also `ui_review.py`'s "Pantry clear"
  icon; the two never share a surface — a badge on a meal card versus a section
  header inside a dialog — which is the test the palette table applies to
  violet meaning both fat and location.

## NiceGUI traps — every one of these cost a debugging session

- **`props()` silently drops an unquoted value containing brackets.** A
  Tailwind class in a Quasar prop must be quoted:
  `props("header-class='text-[11px]'")`. Unquoted, it never reaches the
  component at all and there is no error.
- **`bind_value`'s own sync back to `state` runs *after* a widget's
  `on_change` handler, not before.** A handler that both sets the field and
  refreshes something must set it explicitly first rather than trusting the
  binding to have already landed it — `ui_settings.py`'s week-start select
  does exactly this before calling `refreshables.refresh("plan")`, or the
  repaint would read the old week order.
- **Refreshing a section that owns the focused input steals the cursor.**
  `day_target_row` is built once and mutated in place for exactly this
  reason, and refreshes only the narrow `telemetry` topic rather than
  `targets`. If a new editable section repaints per keystroke, this is the
  pattern to copy — or better, pick a narrower refresh topic.
- **Quasar's `.flex` sets `flex-wrap: wrap` and Tailwind's `flex-row` does not
  undo it.** Any icon-plus-text row needs `flex-nowrap`, and the text label
  needs `min-w-0` — a flex item's default `min-width: auto` will not shrink
  past its longest word. A step long enough to fill its row otherwise wraps
  *below* its number and runs back underneath it.
- **The same wrap, in a `flex-col`, renders content outside its own box.**
  `flex-col` does not undo `flex-wrap: wrap` either, and a wrapping *column*
  container whose content outgrows its height does not overflow — it starts a
  second column beside the first, so any `overflow-y: auto` on it never
  fires. `ui_cards.prep_day_column` is the precedent: measured at 1440px,
  the prep column's last timeline phase laid itself out at x=423, inside the
  Monday day column and 143px clear of its own 135px track, and the cell's
  `absolute` positioning made that invisible rather than merely wrong. Any
  `flex flex-col` that is expected to scroll needs `flex-nowrap`.
- **A flex or grid item's default `min-width`/`min-height` is `auto`, which
  refuses to shrink below its content's natural size — including a wrapper
  that's just passing width down to an `overflow-x: auto` grid inside it.**
  Several Quasar containers (`.q-tab-panel` among them) are themselves flex,
  so a plain `div` wrapper with no `min-w-0` grows to its content's
  min-content width regardless of how little room its parent actually has —
  the wrapper's own `overflow-x: auto` then never overflows, and a
  *different*, outer ancestor ends up scrolling instead. `ui_plan.panel()`
  hit exactly this (phase 2b of `ui-redesign.md`): its wrapper had no
  `min-w-0`, so `week_grid_scroll()`'s canvas never actually needed to
  scroll — a Quasar container two levels up silently scrolled the whole
  panel instead, which would have left the meal-type gutter's `sticky`
  positioning nothing to stick against. `w-full min-w-0` on the wrapper is
  the fix — same shape as `meal_card`'s own `w-full`/`overflow-hidden` fix
  for a sibling sizing trap, documented in `architecture.md`'s phase-1 writeup.
- **A stretched child of a *wrapping* column flex container sizes to the
  widest sibling's max-content, not to the container's own width.** Quasar's
  `.flex` sets `flex-wrap: wrap` (see the trap above), and in a column
  container that also changes what `align-items: stretch` stretches to: the
  flex *line's* cross size, which is the widest item's hypothetical width.
  `ui.header()` is such a container, so a `w-auto` wrapper around
  `WEEK_GRID_COLS` measured 1024px — all nine columns at their 110px floor —
  inside a 1000px viewport, with nothing left to overflow while the canvas
  below scrolled normally. An explicit percentage width
  (`width: calc(100% - <inset>)`, `ui_theme.WEEK_GRID_HEADER_INSET_STYLE`)
  resolves against the container's content box and is immune to it. Reach
  for a definite width, not stretch, for anything that has to match a box
  elsewhere on the page.
- **A grid item spanning several auto rows sizes those rows to its own
  content, and `overflow-y: auto` does not stop it.** Auto rows size to
  their items' *max-content*; `overflow` only zeroes an item's automatic
  *minimum* size, which is a different quantity, so reaching for it here
  measures as no change whatsoever. To make a tall spanning item stop
  driving row heights, take its content out of flow — the cell becomes
  `relative self-stretch` and empty, the content an `absolute inset-0
  overflow-y-auto` child. `ui_cards.prep_day_column` is the precedent, and
  the symptom there was ~640px of dead space appearing between one day's
  meal cards, which looked like the grid restructure's fault and wasn't.
  `self-stretch` is required alongside it wherever the grid sets
  `items-start`, or the cell sits at zero height and `inset-0` fills
  nothing.
- **`ui.add_css` from inside a `@ui.refreshable` stacks another copy into the
  head on every repaint.** Emit page CSS once, from the page function.
- **Never call `repository.run_sync()` here.** NiceGUI page handlers run *on*
  the event loop, so `await` the repository directly. `run_sync` detects the
  running loop and hands the coroutine to a scratch thread — pure overhead,
  and it serialises page loads behind a thread pool.
- **Anything long must not block the handler.** A bare blocking call freezes
  every connected browser. `generate_week_plan` keeps the loop free by
  dispatching each blocking API call to a worker thread; anything new that
  takes seconds must do the same.

## Refresh topics

A call site says *what changed* (`refreshables.refresh("plan")`), never which
widgets depend on it. Topics are registered once, in `planner_page()`, after
every module is built. Before adding a topic, check whether an existing one
already covers the change — and before adding a section to `"plan"`, check it
does not own a focused input.

`"adherence"` is the newest and shows when a new topic *is* warranted: marking
a meal repaints exactly three sections (`today.today_view`, `inspector.panel`
and `insights.panel`), where `"plan"` would additionally rebuild the 28-card
canvas, the telemetry header and the shopping panel on every click of a tick —
none of which draw a mark. Insights joined it with the trend charts, which is
worth noting because this paragraph previously said the topic had no third
member to grow: **a topic's membership is a fact about today's readers, not a
property of the topic.**

## Targets: read the resolved number, never the file

`config["weekly_schedule"][day]["calories"]` and `["protein_g"]` are **inert**
while that macro's `target_modes` entry is `auto` — `hydrate_dynamic_targets`
replaces them with the engine's figure, and the shipped config states 1000
kcal on a Thursday every run plans at 1722. A widget reading the file is
therefore displaying a number nothing plans from, which is exactly the bug
that had the telemetry header measuring the week against targets no run had
ever used.

- `state.planned_targets(day)` — what the next run will aim at.
- `state.baseline_targets(day)` — what it would aim at with that day's own
  overrides suppressed. This is what an override is a *difference from*, so
  it is what a signed delta, a clear-on-match, or a ghost line measures
  against. It costs a `planning_config()` rebuild, so compute it only for the
  days that need one.
- `state.targets_for(day)` — the telemetry denominator, which is the *stored
  plan's* target unless `target_is_staged(day)`. Do not branch on
  `has_training(day)` for this: a workout already in the config is not a
  staged change, and branching on it put six days of one row on a live
  preview and the seventh on the plan.

Two places write to `config/`, and only two: the Settings destination's Daily
Targets panel (`PlannerState.set_target_mode`) and accepting a Garmin
schedule proposal in the review dialog's Training Schedule section
(`PlannerState.accept_training_proposal`), both through
`repository.save_config_keys`. Both persist a *standing* setting rather than
an input to the next run — a toggle or an accepted session that reset on
reload would be a control with no effect. Everything else here stays
session-only.

## State lives per client

`PlannerState` is created *inside* the page function. Module-level state would
be shared by every browser tab connected to the server. A generation is the
only thing that calls the model, but it is no longer the only thing that
writes `week_plan.json` — `ui_generation.save_grid` persists a deterministic
grid edit (a swap, a leftover link, a skip estimate) straight to disk with no
model call, via the staged bar's "Save changes" button (`state.edited` gates
whether it's shown). Grid edits still live only in the client's state until
one of Save/Generate/Discard acts on them.
