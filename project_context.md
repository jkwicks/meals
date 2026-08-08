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
for flags, or `streamlit run app.py` for the web UI.

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
streamlit
pandas
watchdog


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

Note: reasoning must stay disabled on every request regardless of which model
you pick — see the "Reasoning must be disabled" section in `CLAUDE.md`.


