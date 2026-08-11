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

One path, not two. There used to be a second "Print Menu" button that called
`window.print()` against a `@media print` stylesheet (`print_css()`) — it
printed whatever the dashboard happened to render (drawer icons, macro bars,
dish names with no ingredients), which was a strictly worse document than the
PDF sitting one button over, and having both meant two things to keep
formatted well instead of one. `print_css()` and the CSS-only path are gone;
the header's printer-icon button now triggers the same download as before.

- **The printer-icon button** (header) downloads `weekly_menu.pdf` —
  `export_menu.build_week_menu_pdf()` does the formatting — it reads
  `WeekPlan.slots`/`WeekPlan.by_slot()` directly, the same source
  `planner.day_slot_macros` does, not `PlannerState`/`SlotView`, so the
  module has no UI dependency and would work the same from a future CLI
  export flag. It needs `reportlab`: pure Python, so it installs into this
  venv with a plain `pip install` — unlike `weasyprint`, which needs
  Cairo/Pango system libraries this project doesn't otherwise depend on.
  `format_week_menu_markdown()` in the same module is the Markdown
  equivalent, sharing the per-slot walk (`_slot_entry`) so the two formats
  can't silently disagree about what a slot says. Printing this document is
  then just whatever the browser's own PDF viewer does with a print
  command — no separate print stylesheet to keep in sync with the app's
  actual look.
- **The PDF itself** is a day-by-day summary grid (meal types across the
  top, days down the rows), an optional Sunday prep checklist, one page per
  recipe grouped into a section per meal type, and a department-grouped
  shopping list at the end — restrained dark-ink typography and
  hairline-ruled ingredient lists, styled after the CSIRO Total Wellbeing
  Diet's printed meal plans.

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

Every request sends `extra_body=reasoning_extra_body(model, config)`
(`planner.py`), which is OpenRouter's unified switch for a model's hidden
reasoning budget, `{"reasoning": {"enabled": False}}`, **for every model
except the ones in `models.json`'s `reasoning_required_models`.** Do not
change the *default* to enabled — see the measurement below for why — but do
add a model there if it needs the exception (next section).

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

#### Some providers reject the disable switch outright

`google/gemini-3.6-flash` returns a hard `400` on every call, instantly, the
moment `reasoning` is present at all: `"Reasoning is mandatory for this
endpoint and cannot be disabled"`. This isn't the intermittent
zero-content failure above — it's a flat rejection, so `instructor`'s
`max_retries` just burns three attempts at the same 400 and every slot on
that model fails within a second. `models.json`'s
`reasoning_required_models` (a list of model ids) is how `reasoning_extra_body()`
knows to omit the `reasoning` key entirely for a model like this rather than
sending `enabled: True` — the reasoning is disabled by default *because* the
task needs none, and that's equally true whether or not the provider insists
on doing it anyway. If a newly picked model fails every slot in under a
second with this exact message, it belongs in that list, not a workaround in
the prompt.

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


=== File: requirements.txt ===
pydantic
instructor
openai
python-dotenv
eval_type_backport
urllib3<2
nicegui
reportlab


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
- **Some providers reject the disable switch outright rather than misbehaving
  with it on.** `google/gemini-3.6-flash` returned a hard `400` —
  `"Reasoning is mandatory for this endpoint and cannot be disabled"` — on
  every single call, instantly, failing an entire week in under a second
  (not the slow/intermittent failure above; `max_retries` doesn't help
  because the same 400 comes back every attempt). If a newly picked model
  fails this way, add its id to `models.json`'s `reasoning_required_models`
  rather than trying to work around it — `reasoning_extra_body()` in
  `planner.py` then omits the `reasoning` key for that model instead of
  sending `enabled: False`. Don't flip the *default* to enabled for
  everyone else; this is a per-model exception, not a change to the rule.

Note: reasoning must stay disabled by default on every request regardless of
which model you pick — see the "Reasoning must be disabled" section in
`CLAUDE.md`, including the per-model exception list just above.


