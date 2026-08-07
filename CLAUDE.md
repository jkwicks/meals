# AI Meal Planner CLI

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

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

```bash
source venv/bin/activate
python planner.py                       # plans for today
python planner.py --day Wednesday       # plans for a specific day
python planner.py --config other.json   # use an alternate config file
```

### Web UI

```bash
source venv/bin/activate
streamlit run app.py
```

`app.py` is a Streamlit wrapper around `planner.py`/`shopping.py` — it reuses
their functions directly (`calculate_daily_targets`, `resolve_serving_rules`,
`scale_recipe`, `generate_meal_plan`, `aggregate_meal_plan`, etc.) rather than
reimplementing any planning logic, so behavior stays identical to the CLI.
Sidebar controls (day, servings, batch prep, protein/net-carb sliders, model)
build an overridden `config`/`targets` pair that's fed into the same
`generate_meal_plan()` the CLI uses; results are cached in
`st.session_state` so widget interactions (e.g. checking off shopping list
items) don't trigger a new OpenRouter call — only the "Generate Meal Plan"
button does.

## Architecture

- `config.json` — external configuration. `openrouter_model` is the OpenRouter
  model ID used for generation (defaults to a free model if the key is
  missing — see `DEFAULT_MODEL` in `planner.py`); `weekly_schedule` holds
  per-day `calories` / `protein_g` / `net_carbs_g` / `is_keto` targets;
  `dietary_rules` holds `allowed_nova_groups` and `banned_ingredients`.
- `planner.py`:
  - `load_config()` reads `config.json`.
  - `calculate_daily_targets()` deterministically computes `fat_g` in Python
    from `calories - (protein_g*4 + net_carbs_g*4) / 9`. **Never let the AI
    compute macros** — Python calculates exact targets first, then the AI is
    told the numbers and only fills in real food that hits them.
  - `Ingredient` / `Recipe` / `MealPlan` are Pydantic models. `Ingredient` has
    field validators that reject any `nova_group` not in
    `dietary_rules.allowed_nova_groups` (Group 4 ultra-processed is always
    rejected, even without config context) and reject any ingredient name
    containing a `banned_ingredients` substring.
  - Config is threaded into Pydantic validation via `context={"config": config}`
    passed to `instructor`'s `client.chat.completions.create(...)` — this is
    how the field validators see live config instead of hardcoded values.
    (Note: this installed `instructor` version uses `context=`, not the older
    `validation_context=` kwarg — check `inspect.signature` if this breaks
    again after an upgrade.)
  - AI calls go through OpenRouter (`https://openrouter.ai/api/v1`) using the
    OpenAI SDK client patched with `instructor.from_openai(..., mode=instructor.Mode.MD_JSON)`,
    model taken from `config["openrouter_model"]`, `response_model=MealPlan`,
    `max_retries=3`, `max_tokens=8000`, client `timeout=60.0`.
  - **Why `MD_JSON` mode, not `TOOLS`:** the default tool-calling mode sends
    the Pydantic JSON schema as a function-call tool. Several free OpenRouter
    providers reject nested schemas (`Ingredient` inside `Recipe` inside
    `MealPlan` produces `$defs`/`$ref`) with a 422 `"uses $defs"` error.
    `MD_JSON` mode just asks the model to emit JSON as text, which works with
    far more free-tier providers.

### Picking a free OpenRouter model — known gotcha

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
- The system prompt in `generate_meal_plan()` explicitly says "Do not show
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
- Per-day `is_keto` flag (in `weekly_schedule`) drives whether the system
  prompt instructs the AI to produce a low-carb/high-fat day.

## Notes for future sessions

- No Garmin integration in this phase — do not add it unless explicitly
  asked.
- If `planner.py` fails with a Pydantic validation error after 3 retries,
  it's `instructor` surfacing the model's inability to satisfy the schema —
  check the exception message for which field failed before assuming a code
  bug.
