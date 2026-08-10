=== File: CLAUDE.md ===
# AI Weekly Meal Planner

## Setup

`eval_type_backport` is required on Python < 3.10 — `instructor`'s internals use
`str | Path`-style union syntax that Python 3.9's typing module can't evaluate
natively; the backport package patches that in for pydantic.

`urllib3<2` avoids a noisy but harmless `NotOpenSSLWarning` on macOS: this
venv's Python 3.9 is the macOS system Python, which links against Apple's old
LibreSSL 2.8.3 fork instead of real OpenSSL. `urllib3` v2 checks for OpenSSL
1.1.1+ and warns when it isn't found — HTTPS calls still work fine either way,
but pinning `urllib3<2` skips the check and the warning entirely.

Set `OPENROUTER_API_KEY` in `.env` (copy the placeholder already there).

## Run

Run from the venv: `source venv/bin/activate`, then `python planner.py --help`
for flags.

For the web UI use `./server.sh start` — it handles venv activation, nohup, the
PID file and the log:

    ./server.sh start              # NiceGUI (ui_app.py) on :8080
    ./server.sh status
    ./server.sh stop
    MEALS_PORT=9000 ./server.sh start

### Web UI

NiceGUI (`ui_app.py`) is the only web UI. The Streamlit app (`app.py`) was
deleted once the migration landed; if you need its rendering or grid-editing
code as a reference, it is in git history at `git show e237872:app.py`.

A week can be generated from either front end — `python planner.py` or the
NiceGUI drawer's "Generate week" — and both go through the same
`generate_week_plan`, write the same `week_plan.json` and append the same
history. The CLI is still the one that prints shopping lists.

### NiceGUI front end

`ui_app.py` (`./server.sh start`, serves on :8080) is the high-density desktop
UI: left drawer for global controls, a header of 7 per-day macro bars, and a
7-column x 4-card canvas, both grids `grid-cols-7` so a day's telemetry sits
above its meals. Cook/leftover/skip/not-generated are four distinct card
treatments (`STATUS_STYLES`).

**Generating is the only thing here that writes to disk.** `run_generation`
saves `week_plan.json` and records history *before* adopting the plan into
`PlannerState`, so the grid can never show a week that isn't saved — a
20-minute run one browser refresh from being lost is the failure that ordering
prevents. Grid *edits* are still in-memory only: they live in the client's
`PlannerState` until "Reload from disk" discards them, and the header shows an
"edited — not saved" chip while they're outstanding (`adopt_plan` clears it,
because saving is what it just did).

Things worth knowing about the generation path specifically:

- **It generates what's on the grid**, not a fresh default week — including
  any "Link to next lunch" edits, so a linked lunch is a leftover the model is
  told not to generate. `generation_spec()` reapplies the drawer's
  people-per-meal, which `PlannerState.spec` deliberately ignores once a week
  exists (see `_shape()`); without that, the control would silently do nothing
  on every run after the first.
- **The API key is checked up front** (`planner.api_key_error`). Left to the
  per-day handler it would become seven identical failures after a long wait,
  because "a failed day must not fail the week" is exactly the wrong policy for
  a misconfiguration that will fail every day.
- The progress modal is built once per page and *opened* per run, so the
  `progress_callback`/`note_callback` handlers just assign to elements that
  already exist. Notes go to a `ui.log` rather than a status label because
  portion trims and failed days both arrive mid-run and a label would
  overwrite the one you were reading.
- Only a whole-run exception reaches the `except` (no config, storage
  unwritable); per-day failures arrive in `WeekPlan.failures` and become a
  warning toast plus the red NOT GENERATED cards. Nothing is adopted on the
  exception path, so a failed run leaves the week on screen untouched.
- `PlannerState.generating` guards re-entry. The loop stays free during a run
  (see below), which is exactly why the button is still clickable and needs
  the flag: two tabs generating at once would race to overwrite the same file.

The one edit it offers today is the **"Link to next lunch"** button on each
dinner card: one click sets the following day's lunch to `MODE_LEFTOVER` with
`source` pointing at that dinner. Because portions are derived, that single
change is also what grows the batch — see `PlannerState.apply_spec`, which is
where every future grid edit should land too:

- The spec is now **held** (`PlannerState._spec`) rather than re-derived per
  read, and rebuilt only when `_shape()` changes — re-deriving on read would
  discard the edit that was just made. (The deleted Streamlit app dodged the
  same trap in `ensure_grid`.) `_shape()` excludes `servings` for a generated
  week, whose
  portions come from `week_plan.servings_per_meal` instead.
- `apply_spec` writes the new slots back into `week_plan` as well as `_spec`,
  because `day_slot_macros` walks the *plan's* slots — otherwise the linked
  lunch's macros would never reach the telemetry header.
- It also rescales the affected cook events (`planner.rescale_cook_event`).
  Portions being derived means a card reading "4 portions" over ingredients
  weighed for 2 is exactly the disagreement the derived-portions rule exists
  to prevent, and the fix is linear arithmetic, not a regeneration call.
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

### Drawer inputs to the next run: targets and pantry

The left drawer's "Daily macro targets & overrides" and "Inventory to clear"
sections edit `PlannerState`, never config.json, and are merged into a config
by `PlannerState.planning_config()` — one object carrying the model, the
overrides and the pantry, because `generate_week_plan`, `validate_week`,
`split_targets` and `inventory_instruction` all read plain config and would
otherwise each need their own patch. Generating is still the only thing in the
app that writes to disk.

- `target_overrides` holds only what **differs** from the file, per day. That
  is what lets the drawer count overridden days, reset them one at a time, and
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
  typed. Only `telemetry` is refreshed on an edit.

### Shopping list drawer

A right-hand slide-over (opened from the header) rather than a dialog: the list
is read *against* the grid, and a modal would cover the week it describes. One
section per `shopping_windows()` trip, grouped into departments by
`aggregate_cook_events` — by cook day, never eating day.

It is derived from the plan on every repaint and is in `refresh_all()`, so a
leftover link that grows a batch also grows the quantities to buy. The
checkboxes are deliberately not persisted: it is a scratch list for one trip,
and storing ticks would be more state able to disagree with `week_plan.json`.
Days in `WeekPlan.failures` get an explicit note per window, because a short
list is otherwise indistinguishable from a cheap week.

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
  that attaching `bind_value` fires an initial change event, so a handler's
  callees must be defined before the widget is built — that is why `canvas` is
  defined above the drawer and only *called* at the end.

`PlannerState.slot_views()` flattens both a generated `WeekPlan` and an
un-generated `WeekSpec` into the same `SlotView` shape, so the card widget has
one code path and a cold start previews the planned week rather than rendering
28 empty cells.

### Printing and PDF export

Two independent paths, because they solve different problems:

- **"Print Menu"** (header, printer icon) just calls `window.print()`.
  `print_css()` (added once via the same `ui.add_css()` call as `chain_css`/
  `card_hover_css`) is a `@media print` block that hides both drawers, every
  dialog and every clickable control (`.q-drawer`, `.q-dialog`, `.q-btn`,
  `.q-field`), un-fixes the header (`position: static` — a `position: fixed`
  element repeats or clips at every page break instead of flowing once), and
  forces light-on-white text. That last part has to reach every descendant
  (`.meal-canvas *`, not just `.meal-canvas`): Tailwind's text-colour
  utilities (`text-slate-100`, `text-emerald-200`, ...) set `color` directly
  on the element they're applied to, so they never inherit an ancestor's
  override — an ancestor-only rule left the dark theme's near-white card text
  illegible on the forced-white page. `.q-page-container` needs its own
  `padding: 0` too: Quasar reserves space for the (now-hidden) left drawer and
  the fixed header as *inline* padding set by its own JS, not a stylesheet
  class, so leaving it in place squeezed the whole canvas into a leftover
  sliver instead of using the full page width.
- **"Download PDF Menu"** (shopping drawer, above the per-window "Copy for
  Keep" buttons) exports the whole week rather than one shopping trip, so it
  lives once near the top of the drawer rather than repeated per window.
  `export_menu.build_week_menu_pdf()` does the formatting — it reads
  `WeekPlan.slots`/`WeekPlan.by_slot()` directly, the same source
  `planner.day_slot_macros` does, not `PlannerState`/`SlotView`, so the
  module has no UI dependency and would work the same from a future CLI
  export flag. It needs `reportlab`: pure Python, so it installs into this
  venv with a plain `pip install` — unlike `weasyprint`, which needs
  Cairo/Pango system libraries this project doesn't otherwise depend on.
  `format_week_menu_markdown()` in the same module is the Markdown
  equivalent, sharing the per-slot walk (`_slot_entry`) so the two formats
  can't silently disagree about what a slot says.

## Architecture

### The central idea: cook events vs. eating slots

A week is a grid of **eating slots** (one per day x meal_type, 28 of them) laid
over a smaller set of **cook events**. Each slot is `cook`, `leftover` (points
at an earlier cook slot), or `skip`. Every other feature falls out of this:

- **Bulk cooking** is just a cook slot with several slots pointing at it.
  Portion counts are *derived* (`week.portions_for`) from how many slots claim
  it x household size, so a batch size can never silently disagree with the
  meals it has to cover. There is deliberately no "batch multiplier" setting.
- **Shopping windows** group cook events by **cook day, never eating day**. A
  Sunday batch eaten on Wednesday belongs entirely to the Sunday trip;
  grouping by eating day would split one recipe's ingredients across two
  shopping lists.
- **Generation cost** scales with cook days, not calendar days: one API call
  per day that has cooking to do, and a day of pure leftovers is free.

Days are walked in week order (`WeekSpec.days`, rotated by `week_start_day`)
so a leftover's source recipe always exists before its macros are needed —
which is why `validate_week` rejects a leftover pointing at a later day.

- `config.json` — external configuration; `DEFAULT_MODEL` in `planner.py` is
  the fallback when `openrouter_model` is unset or the API key is missing.
- `repository.py` — the storage boundary (see below).
- `week.py` — all the deterministic, API-free planning. The entire week —
  styles, cuisines, portions, windows — is resolved here before a single token
  is generated, so the UI previews exactly what it will ask for.
- `planner.py`:
  - `calculate_daily_targets()` deterministically computes `fat_g` in Python
    from `calories - (protein_g*4 + net_carbs_g*4) / 9`. **Never let the AI
    compute macros** — Python calculates exact targets first, then the AI is
    told the numbers and only fills in real food that hits them.
  - `resolve_auto_choices()` fills every `auto` style/cuisine using
    `next_choice()`, a strict least-recently-used pick seeded from
    `meal_history.json` and then continuing to rotate *within* the week.
    Note it is strict LRU, not "unused in the last N": the latter looks
    equivalent but starves the tail of the list — with 5 breakfast styles and
    N=3 it cycles through the first 4 forever and never picks the 5th.
  - Config is threaded into Pydantic validation via
    `context={"config": config, "day_budget": remaining}` passed to
    `instructor`'s `client.chat.completions.create(...)` — this is how the
    validators see live config instead of hardcoded values.
    (Note: this installed `instructor` version uses `context=`, not the older
    `validation_context=` kwarg — check `inspect.signature` if this breaks
    again after an upgrade.)
  - **Why `MD_JSON` mode, not `TOOLS`:** the default tool-calling mode sends
    the Pydantic JSON schema as a function-call tool. Several free OpenRouter
    providers reject nested schemas (`Ingredient` inside `Recipe` inside
    `DayRecipes` produces `$defs`/`$ref`) with a 422 `"uses $defs"` error.
    `MD_JSON` mode just asks the model to emit JSON as text, which works with
    far more free-tier providers.

### Storage goes through an async repository

Nothing outside `repository.py` opens a file or touches `json` any more.
`PlanRepository` is the interface (`load_config`, `load_history`,
`save_history`, `load_week_plan`, `save_week_plan`); `LocalJSONRepository` is
the only implementation today and keeps the same three files in the same
places. Point the app at a backend by constructing a different subclass —
`planner.main()` and `REPOSITORY` in `ui_app.py` are the only two places that
name one.

**Every method is `async`, including the local file one, deliberately.** The
interface is shaped for the future backend that receives asynchronous webhook
pushes, so business logic awaits its storage today rather than being rewritten
around an `await` boundary later. `LocalJSONRepository` runs its blocking
`open()`/`json` work in `asyncio.to_thread`, so an `await` genuinely yields
instead of wrapping a blocking call in a coroutine.

Consequences worth knowing:

- `generate_week_plan()` and `record_week_history()` are coroutines. Sync
  callers bridge with `repository.run_sync()` — one `asyncio.run` per entry
  point (today just `planner.main()`), never one per storage call.
- `run_sync` falls back to a scratch thread if a loop is already running in the
  calling thread. The CLI has no running loop, so the normal path is plain
  `asyncio.run` and progress callbacks stay on the calling thread. NiceGUI is
  the opposite case and must `await` the repository directly — see the front
  end section above.
- **`generate_day()` is still a synchronous call, dispatched to a thread.** It
  blocks on instructor's sync client for 30s–3min, so `generate_week_plan()`
  hands each day to `asyncio.to_thread` — that is what makes the `await` a real
  yield. Awaiting it inline held the loop for the whole run: invisible in the
  CLI, fatal in NiceGUI, where it froze every connected browser and the
  progress updates it was meant to be showing couldn't be delivered until the
  run they described had finished. Days stay strictly sequential (one thread at
  a time, in week order) because a later day's prompt is built from earlier
  days' recipes — this is about not blocking the loop, not about going faster.
- **Callbacks come back to the loop.** `on_calling_loop()` wraps
  `note_callback` so a worker thread's call is re-scheduled with
  `call_soon_threadsafe` — NiceGUI elements queue their updates against the
  loop that owns the client, so the alternative is a UI mutated off-thread.
  `progress_callback` never crosses the boundary; it fires on the loop, between
  days.
- Writes go via a temp file + `os.replace`. A crash mid-write used to be able
  to leave truncated JSON where `meal_history.json` was, and history can't be
  regenerated.
- `--use-cached-plan` now exits with a clear message when there is no cached
  plan, instead of an `open()` traceback.

### Portion sizing — three layers, because models can't size meals

Measured behaviour on `google/gemma-4-26b-a4b-it:free`: asked for two meals
totalling 1680 kcal (the rest of the day being leftovers), it returned 2564
kcal — it composes plausible *dishes* but reaches for a familiar "full day"
regardless of the stated target. Three layers correct this, in order:

1. **`split_targets()` gives each meal its own budget** rather than one daily
   number for the model to apportion. Weights come from `config.meal_weights`,
   normalised over the slots actually being cooked. A meal eaten more than
   once that day takes a proportionally larger share of the day while its own
   recipe budget stays a single serving.

   Explicit budgets win over weights: `weekly_schedule.<day>.meal_overrides`
   pins named meals (`{"breakfast": {"calories": 450, "protein_g": 45,
   "net_carbs_g": 25}}`, `fat_g` optional and otherwise derived by
   `derive_fat_g()`, the same rule `calculate_daily_targets()` uses). Those
   budgets are assigned verbatim, what they consume — override x how many
   times it's eaten that day — comes off the day, and only the remainder is
   split by weight across the un-pinned slots. So a pinned breakfast pushes
   the other meals *down*, exactly the way leftover macros already do, and the
   day still totals its target. Overrides that exceed the day floor the rest
   at 0 and log a warning rather than going negative; a malformed or
   unknown-meal-type override is dropped with a warning, because a config typo
   must not cost a day of generation.
2. **`fit_recipe_to_budget()`** linearly rescales the response so its calories
   land on budget. Every macro is linear in quantity, so one factor resizes
   the portion without changing the dish. Clamped to `PORTION_TRIM_LIMITS`
   (0.6–1.6) so a trim can never produce an absurd portion.
3. **`DayRecipes.reject_untrimmable_macro_miss()`** — a `model_validator` that
   rejects only what layer 2 *can't* rescue, i.e. a response needing a factor
   outside `PORTION_TRIM_LIMITS`, so `instructor` hands the model its own
   numbers back and retries. Same mechanism that already enforces NOVA groups
   and banned ingredients.

**The threshold in 3 is derived from 2 on purpose — don't replace it with a
standalone tolerance.** An earlier version used a flat 25%, and a real 7-day
run died on day 7: two responses at +62% and +43% were rejected, the third
attempt hit a provider bug, `max_retries` was exhausted, and the exception
took the whole week with it. Both of those responses need factors (0.62,
0.70) well inside the clamp, so the trim would have placed them exactly on
budget. A tolerance tighter than the trim's reach rejects answers it could
have fixed, and every rejection is another 30s–3min call on a free route.

**What this deliberately does not fix:** a recipe with the right calories and
the wrong protein/carb split stays wrong — a single scale factor can't change
a macro *ratio*. That drift shows as a visible delta in the day summary. If
protein is chronically low, change the model, not the trim limits.

Adjustments are surfaced, never silent: `note_callback` collects them and the
UI lists them under "Portion adjustments".

### A failed day must not fail the week

`generate_week_plan()` catches per-day exceptions into `WeekPlan.failures`
(day -> error) and carries on. Seven sequential calls on a free route is seven
chances to hit an unfixable provider failure — an empty completion (`choices`
is `None`, which crashes inside `instructor`'s own response parser), a
rate-limit, a model that can't hit the budget — and losing six good days to
the seventh is the worst possible outcome after a 20-minute run. Failed days'
slots render as "not generated" and their ingredients never reach a shopping
list; the CLI prints them and the UI shows a warning telling you to
re-generate. Orphaned leftovers pointing at a failed cook contribute 0 macros,
so the day shows up as a visible shortfall rather than crashing.

### Reasoning must be disabled — this is not optional

Every request sends `extra_body={"reasoning": {"enabled": False}}`, OpenRouter's
unified switch for a model's hidden reasoning budget. **Do not remove it.**

Measured on `anthropic/claude-sonnet-5` with the identical Sunday prompt:

| | reasoning on | reasoning off |
|---|---|---|
| latency | 303s | 16–19s |
| completion tokens | 32000 (hit the cap) | ~2200 |
| reasoning tokens | 6981 | 0 |
| finish_reason | `length`, **zero content** | `stop` (3/3 runs) |

The same prompt on another attempt used 2149 reasoning tokens and succeeded —
so it is intermittent, which is what makes it nasty. Two of seven days failed
this way on a real Sonnet run with `IncompleteOutputException`. Raising
`max_tokens` does not fix it: 32000 was consumed too. This task needs no
deliberation — the macro arithmetic is already done in Python — so the
reasoning budget is pure cost and a pure failure mode.

Note this makes the free-model reasoning gotcha a *general* problem, not a
free-tier one. A paid frontier model hit it harder than gemma did.

### Diagnosing a slow or failed day

`configure_logging()` (called from both `planner.main()` and `ui_app.py` at
import time) writes per-day generation timing to `meals.log`: request start,
elapsed seconds, `finish_reason`, `completion_tokens`, and `reasoning_tokens`
for every `generate_day()` call, plus a line for any day that fails. This is
the same data the manual diagnostic below asks you to check by hand —
`reasoning_tokens` far above 0 or `finish_reason: length` in the log is the
signature of the reasoning-blowup failure mode, not a hung request.

### Picking a free OpenRouter model

Swapping the generation model has real gotchas (reasoning-token blowups,
free-tier churn, latency variance vs. the client timeout). They live in the
`openrouter-model-choice` skill — invoke it before changing
`openrouter_model` or `DEFAULT_MODEL`.

### Shopping lists

`shopping.py` aggregates cook events (not days) and normalises ingredient
names before combining them. Every normalisation rule and the bad line it
fixes are in `.claude/rules/shopping.md`, which loads automatically when
working on `shopping.py`.

### Using up what's already in the house

`config.inventory_to_clear` is a flat list of things to cook through ("600g
chicken thighs", "half a bag of spinach"). `inventory_instruction()` turns it
into one system-prompt line per day; an empty list emits nothing, so the
prompt is byte-identical to before when the feature is unused.

It is deliberately a **priority, not a constraint** — the wording tells the
model to prefer these items where they fit and forbids it from bending a
meal's style, cuisine or macro budget to use one up. A model told it *must*
use an item will wedge chicken thighs into a breakfast shake.

Consequence worth knowing: these items are still ordinary ingredients in the
recipe, so they still appear on the shopping list. The list describes what the
recipes need, not what you have yet to buy — subtracting inventory from it
would need real quantities per item, which this list doesn't carry.

## Metric unit rules

- All ingredient quantities are in **grams** (`quantity_g`). No cups, oz, lbs,
  or imperial units anywhere in ingredients or recipes.
- All energy is in **kcal**, all macros in **grams**.

## Dietary constraints

- `dietary_rules.allowed_nova_groups` in `config.json` restricts ingredients
  to NOVA groups 1–3 (unprocessed/minimally processed, processed culinary
  ingredients, processed foods). Group 4 (ultra-processed) is always rejected.
- `dietary_rules.banned_ingredients` is a substring-matched blocklist enforced
  by a Pydantic `field_validator` on `Ingredient.name`.
- There is no separate keto flag — a low-carb day is just a low `net_carbs_g`
  target in `weekly_schedule`. `calculate_daily_targets()` derives `fat_g`
  from whatever's left after protein and carbs, so a low carb target already
  pushes fat up without any special-casing.

## Notes for future sessions

- No Garmin integration in this phase — do not add it unless explicitly
  asked.
- If `planner.py` fails with a Pydantic validation error after 3 retries,
  it's `instructor` surfacing the model's inability to satisfy the schema —
  check the exception message for which field failed before assuming a code
  bug. If the message is about kcal totals it's
  `DayRecipes.reject_untrimmable_macro_miss` and the model is off by more than
  the portion trim can absorb; swap models rather than widening
  `PORTION_TRIM_LIMITS` (widening it would let through portions absurd enough
  to be unusable). Note this now fails only that day, not the run.
- `meal_history.json` entries written before the weekly rewrite have no
  `styles` key. `history_styles()` tolerates that (those days simply don't
  seed style rotation), so old history files don't need migrating.


=== File: README.md ===
# AI Weekly Meal Planner

A macro-accurate weekly meal planner and shopping-list generator. Python
computes every calorie and gram target deterministically; an LLM (via
OpenRouter) only fills in real food to hit the numbers it's handed. This
document is both a **user manual** and an **end-to-end verification guide** —
Section 4 is a checklist you can run after any change to confirm the app
still does what this document says it does.

---

## 1. Overview & Core Philosophy

### Eating slots vs. cook events

A week is a grid of 28 **eating slots** — one per day x meal type (breakfast,
lunch, dinner, snack) — laid over a smaller set of **cook events**. Every slot
is one of:

- **`cook`** — a recipe is generated and eaten fresh.
- **`leftover`** — points at an earlier `cook` slot instead of generating
  anything new for itself.
- **`skip`** — not eaten, nothing generated, nothing bought.

Almost every other feature in the app is this idea applied once:

- **Bulk cooking** is just a cook slot with several slots pointing at it.
  Portion counts are never set by hand — they're *derived*
  (`portions_for` in `week.py`) from how many slots claim that cook event x
  household size. There is deliberately no "batch multiplier" setting to get
  out of sync with reality.
- **Shopping windows group by cook day, never eating day.** A Sunday batch
  eaten again on Wednesday belongs entirely to the Sunday trip — grouping by
  eating day would split one recipe's ingredients across two lists.
- **Generation cost scales with cook days, not calendar days.** One API call
  per day that has cooking to do; a day of pure leftovers costs nothing.

### Derived portions and staggered, multi-trip shopping lists

Because a cook event's portion count is derived from its slots, linking a
leftover (see the "Link to next lunch" workflow below) both grows the batch
*and* grows the shopping quantities for it — automatically, in the same
click. The shopping list itself is **staggered**: it's built as one section
per shopping trip (`shopping.shop_days` in config, or `--shop-days` on the
CLI), each trip covering the cook events between it and the next shop day, so
a Sunday-and-Wednesday shopper gets two separate, correctly-scoped lists
instead of one list for the whole week.

---

## 2. Quick Start & Setup

Requires Python 3.9+ and a free [OpenRouter](https://openrouter.ai) API key.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy your key into `.env` (a placeholder is already there):

```
OPENROUTER_API_KEY=sk-or-v1-...
```

Two dependency pins matter on Python 3.9 specifically and shouldn't be
dropped: `eval_type_backport` (`instructor` uses `str | Path` union syntax
that 3.9's `typing` can't evaluate natively) and `urllib3<2` (silences a
harmless `NotOpenSSLWarning` — macOS system Python links against Apple's old
LibreSSL, not real OpenSSL; HTTPS still works either way).

### Start the server

```bash
./server.sh start              # NiceGUI desktop canvas on http://localhost:8080
./server.sh status
./server.sh stop
./server.sh restart
MEALS_PORT=9000 ./server.sh start
```

`server.sh` handles venv activation, backgrounding (`nohup`), the PID file
(`.nicegui.pid`) and the log (`nicegui.log`). Open
[http://localhost:8080](http://localhost:8080) for the high-density desktop
canvas: a left drawer of global controls, a header of seven per-day macro
telemetry bars, and a 7-column x 4-row grid of meal cards below it.

A week can also be generated headlessly from the CLI — see Section 5.

---

## 3. Feature Guides & Workflows

### Target Tuning & Training Plans

**Macro targets** live under **"Daily macro targets & overrides"** in the
left drawer, one row per day (calories, protein g, net carbs g). Fat is
never typed — it's computed from what's left
(`calories - (protein*4 + carbs*4) / 9`), so a low-carb day automatically
becomes a high-fat day with no separate keto flag. An edited day is marked
with an amber `•` and amber label wherever its telemetry appears; that
override wins over whatever the current plan or `config.json` says, because
the point of editing a target before a run is seeing how far the current week
sits from where you're about to aim it. It's reset per-day from the drawer,
which writes the file's numbers back in and clears the marker. **Overrides
apply on the next generation** — they never touch `config.json`.

A specific meal's budget can be pinned instead of weighted, via
`weekly_schedule.<day>.meal_overrides` in `config.json`
(`{"breakfast": {"calories": 450, "protein_g": 45, "net_carbs_g": 25}}`). A
pinned meal is assigned that budget verbatim and pushes the *other* meals of
that day down so the day still totals its target.

**Training plans** live in the drawer's **"Training & Activity Schedule"**
expansion. Each row is one workout: day, time, type (hypertrophy / cardio /
walk / rest), duration and an estimated calorie burn. Adding a session does
three things to that day, live in the telemetry preview, before any
generation call is made:

1. **Budget expansion** — `estimated_burn_kcal` is added onto the day's
   calorie target, split into extra protein and carbs by workout type
   (resistance work skews protein, cardio skews carb) so the derived fat
   target is unchanged — a workout buys back carbs and protein, not fat.
2. **Meal pinning** — the meal closest in time to the workout, on the side it
   follows, is pinned with roughly half the day's expanded carbs for
   post-workout glycogen replenishment (unless that meal already has an
   explicit override, which always wins).
3. **Digestion notes** — any meal within two hours *before* a workout gets a
   prompt-level instruction to favor low-fibre, low-fat, easily digestible
   food, without changing that meal's macro budget.

A training day shows a green `⚡` marker in the header wherever the
budget-expanded target is being shown. Like targets and pantry, training
sessions are drawer-only input — they apply to the next generation and are
never written to `config.json`.

### Pantry Clearing (`inventory_to_clear`)

The drawer's **"Inventory to clear"** section is a free-text list of things
to use up (`"600g chicken thighs"`, `"half a bag of spinach"`). It's a
**priority, not a constraint**: the prompt tells the model to prefer these
items where they naturally fit, and explicitly forbids bending a meal's
macros, style or cuisine to use one up — a model told it *must* use an item
will wedge chicken thighs into a breakfast shake. These items still appear on
the shopping list normally; the list describes what the recipes need, not
what's already in the fridge, since this feature doesn't track real
quantities on hand.

### Cook Once, Eat Twice — Auto-Chaining

Every dinner card that has a recipe shows a **"Link to next lunch"** button.
One click:

- sets the following day's lunch to `leftover`, pointing at that dinner;
- rescales the dinner's portions and shopping quantities up to cover the
  extra person-meal, via the same derived-portions math bulk cooking always
  uses — no separate regeneration call.

The pairing is drawn two ways. Statically, both cards get a small dot and a
line in their chain's colour (the dinner reads "→ feeds Tue lunch", the lunch
reads "↩ from Mon dinner"), so the link is visible without touching anything.
On hover, every card in that chain outlines at once, even across columns —
useful once a week has several overlapping chains. The button stays clickable
even when a link can't be applied (e.g. the next day's lunch is already
something else); hover it to see why.

### Non-Blocking Week Generation

**"Generate week"** in the drawer runs the whole week — CLI and UI both go
through the same `generate_week_plan`. In the UI this runs on a background
thread per cooking day while the event loop stays free, so the browser stays
fully interactive (other tabs, other clicks) for the 5–20 minutes a full week
can take. A progress dialog shows:

- a progress bar and "Generating {day} (n/7) — k recipe(s)…" status line,
  advancing once per day;
- a scrolling log of notes — portion trims, failed days — that arrive
  mid-run, kept in a log rather than a single status label because two
  different notes can arrive close together and a label would overwrite the
  one you were reading.

A missing/invalid API key is caught up front, before any day starts, so a
misconfiguration fails once with a clear message instead of seven identical
failures after a long wait. **A failed day never fails the week** — it's
recorded in `WeekPlan.failures`, shown as a warning toast, and rendered as a
red "NOT GENERATED" card you can regenerate; every other day's result is kept
and saved. Nothing is adopted onto the grid unless the whole run finishes and
saves successfully, and the plan is saved to disk *before* it's adopted into
the visible grid — so a browser refresh can never lose a completed run.

### Staggered Shopping Lists

The shopping-list drawer (opened from the header) shows one section per
shopping trip (`shopping.shop_days`), each grouped into departments, built
against cook events in that window. It's rebuilt from the current plan on
every repaint, so a leftover link made a minute ago is already reflected in
the quantities. A failed day shows an explicit note in the trip it would have
contributed to, so a short list reads as "a day failed" rather than "a cheap
week." Perishables the window buys too early for are flagged inline
("← buy fresh closer to the day") rather than moved to a different trip — the
list still shows everything the trip's recipes need.

Two export paths, for two different use cases:

- **"Copy for Keep"** (per-trip button in the UI drawer) copies a plain
  one-line-per-item list to the clipboard, formatted so pasting into Google
  Keep turns each line into its own checkbox.
- **`--save-shopping-list`** (CLI flag) writes every window to
  `shopping_list.md` as Markdown, in addition to printing them to the
  terminal.

---

## 4. End-to-End Verification Checklist

Run this after any change that touches targets, generation, chaining or
shopping. Each item names the surface to look at and what "working" means.

- [ ] **Macro telemetry recalculation on schedule edits.** Edit a day's
      calorie/protein/carb target in the drawer (or add a training session).
      The header's telemetry bar and numbers for that day update immediately,
      without a page reload or generation run, and the day gets its marker
      (amber `•` for an override, green `⚡` for training).
- [ ] **Leftover chaining & visual outline highlighting.** Click "Link to
      next lunch" on a dinner with a recipe. Confirm: the next day's lunch
      card switches to a leftover treatment, both cards show a chain dot/line
      in a shared colour, hovering either card outlines both, and the
      shopping list quantities for that cook event increase.
- [ ] **Async background week generation without UI freeze.** Click
      "Generate week" and, while it's running, interact with something else
      in the UI (open the shopping drawer, hover a card). It should respond
      immediately — the progress dialog's bar and log should keep advancing
      independently.
- [ ] **Departmentalized shopping list generation and Google Keep clipboard
      export.** Open the shopping drawer after a generation. Confirm items
      are grouped under department headings per trip, and "Copy for Keep"
      produces a paste that becomes one checkbox per line in Keep.
- [ ] **Training day carb-shifting and digestion constraint application.**
      Add a training session, generate the week, and check the pinned meal's
      recipe brief/macros reflect the extra carbs, and that a meal scheduled
      within two hours before the workout came back lower-fibre/lower-fat
      than a typical meal of that type. (`meals.log` and the day's recipe
      notes are the source of truth here — the model can still miss a
      constraint on a bad response, which is what the portion/macro retry
      logic exists to catch.)

---

## 5. CLI vs. UI Commands

| Action | CLI (`planner.py`) | UI (`ui_app.py`) |
|---|---|---|
| Generate a week | `python planner.py` | Drawer → **Generate week** |
| Use a different config file | `--config PATH` | — (always `config.json`) |
| Override the model for one run | `--model NAME` | Drawer model selector |
| Set the week's start day | `--week-start DAY` | Fixed by `week_start_day` in config |
| Set household size | `--servings N` | Drawer servings field |
| Set shopping trip days | `--shop-days Sunday,Wednesday` | `config.json` (`shopping.shop_days`) |
| Make every lunch a leftover of the prior dinner | `--leftover-lunches` | Per-dinner **"Link to next lunch"** button |
| Export shopping lists as Markdown | `--save-shopping-list` → `shopping_list.md` | — |
| Export a shopping trip for Google Keep | — | Per-trip **"Copy for Keep"** button |
| Re-use the last generated plan without an API call | `--use-cached-plan` | Grid always shows the last saved `week_plan.json` until you generate again |
| Edit a day's macro target for the next run | Edit `config.json` `weekly_schedule` | Drawer → **Daily macro targets & overrides** |
| Pin one meal's budget | `config.json` `meal_overrides` | (not yet editable from the drawer) |
| Add a training/workout session | `config.json` `training_schedule` | Drawer → **Training & Activity Schedule** |
| Prioritize using up pantry items | `config.json` `inventory_to_clear` | Drawer → **Inventory to clear** |
| Print shopping lists to the terminal | Always, after generation | — (use the shopping drawer) |
| Monitor per-day generation timing/failures | `meals.log` | Progress dialog (live) + warning toast on completion |

CLI-only and UI-only differences are structural, not accidental: the CLI is
the batch/scriptable path and is the only one that writes `shopping_list.md`
or prints to the terminal; the UI is the only one with live, pre-generation
previews (telemetry, chaining, training) because those need a browser to
interact with before committing to a 5–20 minute run. Both write the same
`week_plan.json` and append to the same `meal_history.json` — a week started
on one front end can be inspected, or its shopping list pulled, from the
other.

---

## Configuration reference

Everything in Sections 3 and 5 that isn't drawer-editable lives in
`config.json`:

| Key | Meaning |
|---|---|
| `weekly_schedule.<day>` | Per-day `calories`, `protein_g`, `net_carbs_g`, `meal_overrides` |
| `week_defaults` | Default mode (`cook`/`leftover`/`skip`) per meal type |
| `training_schedule` | List of `{day, time, type, duration_minutes, estimated_burn_kcal}` |
| `meal_styles` / `cuisines` / `cuisine_meal_types` | Style/cuisine pools; anything left `auto` rotates least-recently-used from `meal_history.json` |
| `dietary_rules.allowed_nova_groups` | NOVA processing groups allowed (group 4 is always rejected) |
| `dietary_rules.banned_ingredients` | Substring blocklist, enforced as schema validation |
| `openrouter_model` | Model id; falls back to `DEFAULT_MODEL` in `planner.py` if unset |
| `week_start_day` | First day of the planning week |
| `meal_weights` | How a day's calories split across un-pinned meals |
| `serving_rules.servings_per_meal` | Household size |
| `shopping.shop_days` | Days you shop — defines the shopping windows |
| `inventory_to_clear` | Free-text priority list (see Section 3) |

All ingredient quantities are grams, all energy is kcal — no cups, oz or lbs
anywhere in the schema.

---

## Files

| File | |
|---|---|
| `planner.py` | Targets, training adjustments, prompts, model calls, portion fitting, CLI |
| `week.py` | All deterministic planning — the week is fully resolved before a token is generated |
| `ui_app.py` | NiceGUI web UI |
| `shopping.py` | Ingredient aggregation, normalisation, Keep/Markdown formatting |
| `repository.py` | The storage boundary — nothing else opens a file |
| `config.json` | Everything in the configuration reference above |
| `week_plan.json` | The current generated week (regenerable) |
| `meal_history.json` | Style/cuisine rotation history (**not** regenerable) |
| `meals.log` | Per-day generation timing, finish reason, token counts |

`CLAUDE.md` is the deep architecture document — the *why* behind each design
decision, and the place to look before changing behaviour.

## Troubleshooting

**A Pydantic validation error after 3 retries** — that's `instructor`
surfacing the model's inability to satisfy the schema, not a code bug. Check
which field failed. If it's about kcal totals, the model is off by more than
the portion trim can absorb: swap models (see the `openrouter-model-choice`
skill) rather than widening `PORTION_TRIM_LIMITS`.

**A day took minutes or came back empty** — check `meals.log`. A
`reasoning_tokens` count well above 0, or `finish_reason: length`, is the
reasoning-blowup signature, not a hung request. Every request disables
reasoning explicitly; if this shows up, something re-enabled it.

**Calories right but protein low** — a single scale factor can't change a
macro *ratio*, so the portion trim can't fix it and it shows as a visible
delta in the day summary. If it's chronic, change the model.

**Shopping list looks wrong** — ingredient normalisation rules, and the bad
line each one fixes, are in `.claude/rules/shopping.md`.

**A training day's numbers didn't change anything** — check the session's
`type` matches one of the known workout types (`gym_hypertrophy`,
`cardio_run`, `walk`) or `rest`; an unrecognised type is logged as a warning
and ignored rather than failing the run.


=== File: requirements.txt ===
pydantic
instructor
openai
python-dotenv
eval_type_backport
urllib3<2
nicegui
reportlab


=== File: prepare.sh ===
# 1. Clean up old bundle files if they exist
rm -f python_codebase.md project_context.md data_schemas.md

# 2. Bundle all active Python source files into a single annotated Markdown document
find . -maxdepth 1 -type f -name "*.py" ! -name ".*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \; > python_codebase.md

# 3. Bundle architecture documentation, rules, and skills
{
  [ -f CLAUDE.md ] && echo "=== File: CLAUDE.md ===" && cat CLAUDE.md && echo -e "\n"
  [ -f requirements.txt ] && echo "=== File: requirements.txt ===" && cat requirements.txt && echo -e "\n"
  [ -f .claude/rules/shopping.md ] && echo "=== File: .claude/rules/shopping.md ===" && cat .claude/rules/shopping.md && echo -e "\n"
  [ -f .claude/skills/openrouter-model-choice/SKILL.md ] && echo "=== File: .claude/skills/openrouter-model-choice/SKILL.md ===" && cat .claude/skills/openrouter-model-choice/SKILL.md && echo -e "\n"
} > project_context.md

# 4. Generate structural schema previews for JSON configuration and state files (first 35 lines each)
{
  for json_file in config.json week_plan.json meal_plan.json meal_history.json; do
    if [ -f "$json_file" ]; then
      echo "=== Sample Structure: $json_file ==="
      head -n 35 "$json_file"
      echo -e "\n"
    fi
  done

  # Pydantic models whose fields are optional or nested past the head -35
  # cutoff above (e.g. week_plan.json's sunday_prep_session) never appear in
  # the sample previews, so dump their real schema straight from the source
  # of truth instead of hoping a sample file happens to populate them.
  echo "=== Model Schema: WeekPlan.sunday_prep_session (planner.SundayPrepSession) ==="
  python3 -c "import json, planner; print(json.dumps(planner.SundayPrepSession.model_json_schema(), indent=2))"
  echo -e "\n"
} > data_schemas.md


=== File: server.sh ===
#!/usr/bin/env bash
# Start/stop/status for the meal planner web UI, so you don't have to remember
# venv activation, the right invocation, or how to find/kill it.
#
# NiceGUI (ui_app.py) is the only UI. Streamlit has been removed. The UI can
# generate a week itself now; `python planner.py --help` is the other way in,
# and still the only one that prints shopping lists.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PID_FILE=".nicegui.pid"
LOG_FILE="nicegui.log"
PORT="${MEALS_PORT:-8080}"
DESC="NiceGUI (ui_app.py)"

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
    # `return`, not `exit` — `restart` calls stop then start, and an exit here
    # would end the script instead of continuing to the other half.
    if is_running; then
        echo "$DESC already running (PID $(cat "$PID_FILE"), http://localhost:$PORT)."
        return 0
    fi
    if [ ! -d venv ]; then
        echo "No venv/ found — run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
        exit 1
    fi
    source venv/bin/activate

    # ui_app.py reads MEALS_UI_PORT; reload is off in the script it runs, so
    # this stays one process and the PID below is the one to kill.
    MEALS_UI_PORT="$PORT" nohup python ui_app.py > "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    disown
    echo "Started $DESC (PID $(cat "$PID_FILE")). UI: http://localhost:$PORT — output in $LOG_FILE"
}

stop() {
    if ! is_running; then
        echo "$DESC not running."
        rm -f "$PID_FILE"
        return 0
    fi
    kill "$(cat "$PID_FILE")"
    rm -f "$PID_FILE"
    echo "Stopped $DESC."
}

status() {
    if is_running; then
        echo "$DESC: running (PID $(cat "$PID_FILE")), http://localhost:$PORT"
    else
        echo "$DESC: not running."
    fi
}

ACTION="${1:-}"

case "$ACTION" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo "  Port override: MEALS_PORT=9000 $0 start"
        exit 1
        ;;
esac


=== File: claude-queue.sh ===
#!/usr/bin/env bash

# Directory containing your ordered markdown prompts
PROMPT_DIR="./.prompts"
RETRY_INTERVAL_SECS=600  # 10 minutes (600 seconds)

# Ensure the prompt directory exists
if [ ! -d "$PROMPT_DIR" ]; then
  echo "Error: Directory '$PROMPT_DIR' does not exist."
  exit 1
fi

# Find all matching files and sort them numerically by prompt number
# Matches: prompt-1.md, prompt-2.md, prompt-03.md, etc.
mapfile -t PROMPT_FILES < <(
  find "$PROMPT_DIR" -type f -name "prompt-*.md" \
  | awk -F'[/.-]' '{print $(NF-1), $0}' \
  | sort -n -k1,1 \
  | cut -d' ' -f2-
)

if [ ${#PROMPT_FILES[@]} -eq 0 ]; then
  echo "No prompt files matching 'prompt-*.md' found in $PROMPT_DIR."
  exit 0
fi

echo "=================================================="
echo "Found ${#PROMPT_FILES[@]} prompt files to process in sequence."
echo "=================================================="

for FILE in "${PROMPT_FILES[@]}"; do
  echo ""
  echo "--------------------------------------------------"
  echo "▶ Processing: $FILE"
  echo "--------------------------------------------------"

  PROMPT_CONTENT=$(cat "$FILE")

  while true; do
    # Temporary log file to capture Claude's output
    TMP_LOG=$(mktemp)

    # Run Claude Code non-interactively with auto-permissions
    claude -p "$PROMPT_CONTENT" --dangerously-skip-permissions > "$TMP_LOG" 2>&1
    EXIT_CODE=$?

    # Print output to terminal in real time
    cat "$TMP_LOG"

    if [ $EXIT_CODE -eq 0 ]; then
      echo ""
      echo "✅ Successfully executed: $FILE"
      rm -f "$TMP_LOG"
      break  # Break retry loop, move to the next file
    else
      # Check if failure was caused by rate limits or usage caps
      if grep -iqE "rate limit|resets at|quota|too many requests" "$TMP_LOG"; then
        echo ""
        echo "⚠️ Rate limit detected while executing $FILE."
        echo "⏳ Pausing for 10 minutes before retrying..."
        rm -f "$TMP_LOG"
        sleep $RETRY_INTERVAL_SECS
      else
        echo ""
        echo "❌ Fatal error occurred in $FILE (Exit Code: $EXIT_CODE)."
        echo "⛔ Halting execution queue. Resolve the issue before re-running."
        rm -f "$TMP_LOG"
        exit $EXIT_CODE  # Stop the entire queue
      fi
    fi
  done
done

echo ""
echo "=================================================="
echo "🎉 All prompts executed successfully!"
echo "=================================================="

=== File: release.sh ===
#!/usr/bin/env bash
set -euo pipefail

# Usage: ./release.sh <patch|minor|major> "Technical release notes" "Plain english release notes"

BUMP_TYPE="${1:-patch}"
TECH_NOTES="${2:-Routine technical updates.}"
PLAIN_NOTES="${3:-Routine maintenance and bug fixes.}"

MAIN_BRANCH="main"

# Ensure gh CLI is installed
if ! command -v gh &> /dev/null; then
  echo "❌ GitHub CLI ('gh') is required. Install via 'brew install gh' and run 'gh auth login'."
  exit 1
fi

# Fetch latest tags and main branch
git fetch origin --tags
git checkout "${MAIN_BRANCH}"
git pull origin "${MAIN_BRANCH}"

# Get latest SemVer tag or default to v0.0.0
LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
VERSION_NUM="${LATEST_TAG#v}"

IFS='.' read -r MAJOR MINOR PATCH <<< "${VERSION_NUM}"

case "${BUMP_TYPE}" in
  major)
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
    ;;
  minor)
    MINOR=$((MINOR + 1))
    PATCH=0
    ;;
  patch)
    PATCH=$((PATCH + 1))
    ;;
  *)
    echo "❌ Unknown bump type '${BUMP_TYPE}'. Use major, minor, or patch."
    exit 1
    ;;
esac

NEW_TAG="v${MAJOR}.${MINOR}.${PATCH}"
BRANCH_NAME="release/${NEW_TAG}"

echo "=================================================="
echo "Preparing Release ${NEW_TAG} on branch ${BRANCH_NAME}"
echo "=================================================="

# Create release branch
git checkout -b "${BRANCH_NAME}"
git add .

if git diff-index --quiet HEAD --; then
  echo "No changes to commit, proceeding with existing branch commits."
else
  COMMIT_MSG=$(cat <<EOF
release(${NEW_TAG}): ${BUMP_TYPE} update

[Plain English Summary]
${PLAIN_NOTES}

[Technical Summary]
${TECH_NOTES}
EOF
  )
  git commit -m "${COMMIT_MSG}"
fi

# Push branch to remote
git push -u origin "${BRANCH_NAME}"

# Release Body Formatting
RELEASE_BODY=$(cat <<EOF
## [Plain English Summary]
${PLAIN_NOTES}

## [Technical Summary]
${TECH_NOTES}
EOF
)

echo "Creating GitHub Pull Request..."
PR_URL=$(gh pr create \
  --title "release(${NEW_TAG}): Automated Release" \
  --body "${RELEASE_BODY}" \
  --base "${MAIN_BRANCH}" \
  --head "${BRANCH_NAME}")

echo "Pull Request created: ${PR_URL}"

echo "Merging Pull Request into ${MAIN_BRANCH}..."
gh pr merge "${PR_URL}" --merge --delete-branch

echo "Switching to ${MAIN_BRANCH} and pulling merged changes..."
git checkout "${MAIN_BRANCH}"
git pull origin "${MAIN_BRANCH}"

echo "Creating and pushing SemVer GitHub Release ${NEW_TAG}..."
gh release create "${NEW_TAG}" \
  --title "${NEW_TAG}" \
  --notes "${RELEASE_BODY}"

echo ""
echo "=================================================="
echo "🎉 Release ${NEW_TAG} created, merged, and published!"
echo "=================================================="

=== File: .claude/rules/shopping.md ===
---
paths: ["shopping.py"]
---

# Shopping lists

- `shopping.py` aggregates **cook events**, not days: `aggregate_cook_events()`
  takes the events in a window plus the window's day list, so ingredient
  totals include every portion of a batch on the day it is cooked.
- `ShoppingItem.latest_cook_offset` records how many days into the window an
  ingredient is finally needed; `buy_late` flags perishables
  (`week.PERISHABLE_DEPARTMENTS`) needed `PERISHABLE_DAY_GAP`+ days later. It
  only annotates — it never moves an item to another trip, since whether to
  make a top-up trip is the shopper's call.
- Multi-day windows are the *point* of this design, so the fresh-food tension
  is surfaced rather than solved.

## Ingredient name handling — every rule here came from a real bad line

Models write ingredient names for a cook, not for a shopper. `shopping.py`
normalises them before combining; each rule fixes something observed:

- **`strip_parentheticals()` runs before the comma split.** "Egg yolks (large,
  from free-range eggs)" split first leaves the dangling "Egg yolks (large".
- **`ingredient_head()`** keeps only the part before the first comma — what you
  buy, not how it's handled.
- **`contains_word()`** matches whole words with real plural forms. Substring
  matching rendered "Eggplant, cubed" as **"10 eggs"** and filed "Garlic,
  minced" under Meat & Poultry (the "mince" keyword). A bare `+s` plural
  missed "potatoes" and "berries".
- **`categorize_department()` picks the longest matching keyword**, not the
  first department in the list. Specificity beating list order is what fixes
  "garlic cloves" (spice "clove" vs produce "garlic"), "cauliflower rice"
  (vs "rice"), and "beef broth" (vs "beef") without fragile reordering.
  `<animal> broth` pairs are still spelled out, since "chicken" is longer
  than "broth".
- **`PREP_QUALIFIERS` vs `STATE_QUALIFIERS` is the important distinction.**
  Prep words (diced, sliced, grilled) are stripped so "Cucumber, diced" and
  "Cucumber, sliced" are one line. State words (cooked, dry, canned, frozen)
  are *preserved and folded into the key*, because they change what a gram
  means — merging "Quinoa, dry" with "Quinoa, cooked" would understate the
  shop. `raw`/`uncooked` are prep, not state: they describe the default, and
  treating them as state split "Red bell pepper" from "Red bell pepper (raw)".
- **`singularize()` is key-only.** Note the `-es` rule only fires after a
  sibilant or `-o`; applying it everywhere turned "cloves" into "clov".
- `NON_SHOPPING_INGREDIENTS` drops water — a "Water: 300g" line makes the rest
  of the list look untrustworthy.

When adding a keyword, prefer the most specific phrase; longest-match will do
the right thing without touching the ordering.
-e 

=== File: .claude/skills/openrouter-model-choice/SKILL.md ===
---
name: openrouter-model-choice
description: How to pick, sanity-check, and swap the OpenRouter model used for meal generation — free-tier gotchas, reasoning-token diagnosis, timeout headroom.
---

# Picking a free OpenRouter model — known gotcha

The current `DEFAULT_MODEL`/`openrouter_model` (`google/gemma-4-26b-a4b-it:free`)
was chosen after several free models failed in ways worth knowing about if you
swap it:

- **Reasoning models can hang or blow the token budget on this task.**
  Several free models (`openai/gpt-oss-20b:free`, `nvidia/nemotron-3-nano-30b-a3b:free`,
  `inclusionai/ling-3.0-flash:free`, `cohere/north-mini-code:free`) spend
  most/all of their output budget on hidden or visible step-by-step
  "reasoning" tokens — literally narrating arithmetic ("previously 150g was
  51 kcal...") instead of just writing the JSON — and either hit
  `max_tokens` (`instructor.v2.core.errors.IncompleteOutputException`) or
  appear to hang for 10+ minutes on a throttled free route. Check
  `response.usage.completion_tokens_details.reasoning_tokens` when
  diagnosing a slow/failing model — a large number there is the signature.
- **The free-tier lineup changes constantly.** A model can be `:free` one day
  and removed the next (`404 ... use this slug instead: <paid-id>`). Query
  the live list before assuming a model ID still works:
  `requests.get("https://openrouter.ai/api/v1/models").json()["data"]`,
  filter `id.endswith(":free")`.
  - Some free routes are rate-limited upstream and return 429 even on a
    trivial request (`google/gemma-4-31b-it:free` did this; the sibling
    `google/gemma-4-26b-a4b-it:free` did not).
- **How to sanity-check a candidate model before wiring it in:** send a
  minimal `client.chat.completions.create(...)` call directly (bypass
  `instructor`) with a small `max_tokens`, and inspect
  `resp.choices[0].finish_reason` and `resp.usage`. `finish_reason: "stop"`
  with `reasoning_tokens` near 0 is a good sign; `finish_reason: "length"`
  with most of the budget in `reasoning_tokens` means pick a different model.
- The system prompt in `generate_day()` explicitly says "Do not show
  your work, explain your reasoning, or narrate your process" — this was
  added after observing reasoning-heavy models ignore a softer instruction
  and helps steer well-behaved models toward direct JSON output. It doesn't
  fix a genuinely reasoning-heavy model; swap the model instead.
- **Even a "good" free model has highly variable latency, not just a binary
  hang/no-hang.** `google/gemma-4-26b-a4b-it:free` has been observed taking
  anywhere from ~2s (trivial prompt) to ~58s (full meal-plan prompt) for a
  normal, successful, non-reasoning response — this is free-tier queuing
  variance, not a code problem. The `OpenAI(..., timeout=...)` in
  `build_client()` must have real headroom above that (currently `120.0`;
  it was `60.0` and a request that legitimately took ~58s+ on a busier route
  came close to tripping it). If the client timeout fires mid-request,
  `instructor` doesn't just retry the same response — it re-runs the full
  generation (up to `max_retries=3` times), so a timeout that's set too
  tight turns a slow-but-fine call into what looks like a multi-minute hang.
  If you see this again: first re-run the raw-call diagnostic above with a
  generous hard wall-clock timeout (60-100s) to confirm the model itself
  still finishes with `finish_reason: "stop"` and low `reasoning_tokens`
  before assuming the model is broken — it may just need a longer client
  timeout.

- **A week is 7x the exposure to this.** Generating a full week on a free
  model means up to 7 sequential calls, each of which can take 30s–3min and
  may burn `max_retries` on the macro validator. Budget 10–20 minutes, and
  prefer a paid model (`anthropic/claude-sonnet-5`) when portion accuracy
  matters. `--use-cached-plan` re-renders `week_plan.json` with no API calls,
  which is the right way to iterate on shopping-list or display changes.

Note: reasoning must stay disabled on every request regardless of which model
you pick — see the "Reasoning must be disabled" section in `CLAUDE.md`.
-e 

