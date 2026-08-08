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
PID file and the log. It takes an optional UI argument, defaulting to the
NiceGUI app:

    ./server.sh start              # NiceGUI (ui_app.py) on :8080
    ./server.sh start streamlit    # Streamlit (app.py) on :8501
    ./server.sh status             # reports on both, whichever you asked for
    ./server.sh stop [ui]
    MEALS_PORT=9000 ./server.sh start

Each UI has its own port, PID file and log, so both can run at once — which
you currently need, because `ui_app.py` is read-only and only the Streamlit app
can generate a week.

### Web UI

`app.py` is a Streamlit wrapper around `planner.py`/`week.py`/`shopping.py` —
it reuses their functions directly rather than reimplementing planning logic,
so behavior stays identical to the CLI. The **Plan Setup** tab holds an
editable per-day macro table and the 28-row meal grid; **The Week** and
**Shopping** render `st.session_state["week_plan"]`, so widget interactions
(ticking off shopping items) never trigger an OpenRouter call — only the
"Generate Week" button does.

The grid lives in `st.session_state["grid_rows"]` and is rebuilt only when the
*week shape* changes (`ensure_grid`) — rebuilding every rerun would discard
the user's edits, never rebuilding would leave stale days after changing the
week start.

### NiceGUI front end (migration in progress)

`ui_app.py` (`./server.sh start`, serves on :8080) is the high-density desktop
replacement for `app.py`: left drawer for global controls, a header of 7
per-day macro bars, and a 7-column x 4-card canvas, both grids `grid-cols-7`
so a day's telemetry sits above its meals. Cook/leftover/skip/not-generated
are four distinct card treatments (`STATUS_STYLES`).

**It is read-only for now** — it loads config and the cached week plan and
renders them; the Generate button is deliberately disabled. `app.py` therefore
stays until generation is ported, since it is the only UI that can produce a
week.

Two things it does differently from the Streamlit app, both worth keeping:

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
`planner.main()` and `REPOSITORY` in `app.py` are the only two places that name
one.

**Every method is `async`, including the local file one, deliberately.** The
interface is shaped for the future backend that receives asynchronous webhook
pushes, so business logic awaits its storage today rather than being rewritten
around an `await` boundary later. `LocalJSONRepository` runs its blocking
`open()`/`json` work in `asyncio.to_thread`, so an `await` genuinely yields
instead of wrapping a blocking call in a coroutine.

Consequences worth knowing:

- `generate_week_plan()` and `record_week_history()` are coroutines. Sync
  callers bridge with `repository.run_sync()` — one `asyncio.run` per entry
  point (`planner.main()`, and the Streamlit button/`main()` in `app.py`),
  never one per storage call.
- `run_sync` falls back to a scratch thread if a loop is already running in the
  calling thread. Streamlit's script thread has no running loop, so the normal
  path is plain `asyncio.run` and progress callbacks stay on the script thread
  where `st.progress` still works.
- **The generation calls are still synchronous.** `generate_day()` blocks on
  instructor's sync client for 30s–3min, inside an async function, so a run
  still holds the loop for its whole duration. Async storage doesn't change
  that; only an async OpenAI client (or a `to_thread` hop, which would move
  progress callbacks off the Streamlit script thread) would.
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

`configure_logging()` (called from both `planner.main()` and `app.py` at
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
