# AI Weekly Meal Planner

Generates a week of meals that hit **your** macro targets, plans the bulk
cooking around them, and produces a shopping list per shopping trip.

The macro arithmetic is done in Python — the model is told the exact calorie
and protein numbers for each meal and only fills in real food to hit them. It
never computes targets itself.

Two front ends, one engine:

- **Web UI** (`ui_app.py`, NiceGUI) — a 7-day grid of meal cards with per-day
  macro telemetry, leftover linking, editable targets and a shopping-list
  drawer. Can generate a week.
- **CLI** (`planner.py`) — generates a week and is the only one that prints
  shopping lists to the terminal / writes `shopping_list.md`.

Both go through the same `generate_week_plan`, write the same `week_plan.json`
and append to the same history.

---

## Setup

Requires Python 3.9+ and an [OpenRouter](https://openrouter.ai) API key (free
models work — see [Choosing a model](#choosing-a-model)).

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Then put your key in `.env` (the file already has a placeholder):

```
OPENROUTER_API_KEY=sk-or-v1-...
```

Two dependency pins exist for a reason and shouldn't be dropped on Python 3.9:
`eval_type_backport` (instructor uses `str | Path` union syntax that 3.9's
typing can't evaluate) and `urllib3<2` (silences a harmless LibreSSL warning
from macOS system Python). Details in [CLAUDE.md](CLAUDE.md#setup).

---

## Running it

### Web UI

```bash
./server.sh start              # http://localhost:8080
./server.sh status
./server.sh stop
./server.sh restart
MEALS_PORT=9000 ./server.sh start
```

`server.sh` handles venv activation, backgrounding, the PID file
(`.nicegui.pid`) and the log (`nicegui.log`). Generate a week from the left
drawer's **Generate week** button.

**Only generating writes to disk.** Grid edits (leftover links, target
overrides, pantry items) live in the browser session until the next generation
run; the header shows an "edited — not saved" chip while they're outstanding.

### CLI

```bash
source venv/bin/activate
python planner.py --help
```

| Flag | What it does |
|---|---|
| `--config PATH` | Use a different config file (default `config.json`) |
| `--model NAME` | Override `openrouter_model` for this run |
| `--week-start DAY` | Day the week starts on (default from config) |
| `--servings N` | People cooked for per meal |
| `--shop-days Sunday,Wednesday` | Shopping windows run from each shop day to the next |
| `--leftover-lunches` | Every lunch becomes leftovers of the previous dinner |
| `--save-shopping-list` | Also write the lists to `shopping_list.md` |
| `--use-cached-plan` | Read `week_plan.json` instead of calling the API — for iterating on shopping lists for free |

A full week is 5–20 minutes depending on the model: one API call per day that
has cooking to do. **A failed day does not fail the week** — it lands in
`WeekPlan.failures`, prints as a warning, and renders as red NOT GENERATED
cards you can regenerate.

---

## Configuring your week — `config.json`

Everything below lives in `config.json`. Nothing here needs code changes.

### Targets per day

```json
"weekly_schedule": {
  "Wednesday": {
    "calories": 2000,
    "protein_g": 160,
    "net_carbs_g": 30,
    "meal_overrides": {}
  }
}
```

Set calories, protein and net carbs; **fat is always derived** from what's
left (`calories - (protein*4 + carbs*4) / 9`), so it's never typed anywhere.
That also means a low-carb day automatically becomes a high-fat day — there's
no keto flag, a low `net_carbs_g` is the whole mechanism.

`meal_overrides` pins a specific meal's budget:

```json
"meal_overrides": {"breakfast": {"calories": 450, "protein_g": 45, "net_carbs_g": 25}}
```

A pinned meal is assigned verbatim and pushes the *other* meals down, so the
day still totals its target.

### Which meals get cooked

```json
"week_defaults": {"breakfast": "cook", "lunch": "cook", "dinner": "cook", "snack": "skip"}
```

Each of the 28 slots is `cook`, `leftover` (points at an earlier cook) or
`skip`.

### Styles and cuisines

`meal_styles` maps a style name to a sentence describing it to the model.
`cuisines` + `cuisine_meal_types` control which meals get a cuisine assigned
(dinner only, by default). Anything left as `auto` is filled by strict
least-recently-used rotation seeded from `meal_history.json`, so you don't eat
the same five dinners forever.

### Diet rules

```json
"dietary_rules": {
  "allowed_nova_groups": [1, 2, 3],
  "banned_ingredients": ["high fructose corn syrup", "seed oils", "banana"]
}
```

NOVA group 4 (ultra-processed) is always rejected. `banned_ingredients` is a
substring blocklist — good for both additives and things you simply don't
like. Both are enforced as schema validation, so a violating response is
handed back to the model to retry rather than reaching your plate.

### Other keys

| Key | Meaning |
|---|---|
| `openrouter_model` | Model id; falls back to `DEFAULT_MODEL` in `planner.py` |
| `week_start_day` | First day of the planning week |
| `meal_weights` | How a day's calories split across meals when not pinned |
| `serving_rules.servings_per_meal` | Household size |
| `shopping.shop_days` | Days you shop — defines the shopping windows |
| `inventory_to_clear` | Free-text list of things to use up (`"600g chicken thighs"`) |

`inventory_to_clear` is a **priority, not a constraint**: the model prefers
these where they fit but won't bend a meal's macros or style to use one. They
still appear on the shopping list — the list describes what the recipes need,
not what you still have to buy.

All quantities are metric: grams for ingredients and macros, kcal for energy.

---

## How it thinks: cook events vs eating slots

The one idea worth knowing before reading the code. A week is 28 **eating
slots** laid over a smaller set of **cook events**. Everything follows:

- **Bulk cooking** is a cook slot with several slots pointing at it. Portion
  counts are *derived* from how many slots claim it × household size, so a
  batch can never disagree with the meals it has to cover. There's
  deliberately no "batch multiplier" setting.
- **Shopping windows** group by **cook day, never eating day** — a Sunday
  batch eaten Wednesday belongs entirely to the Sunday trip.
- **Cost scales with cook days**, not calendar days. A day of pure leftovers
  is free.

In the UI, the "Link to next lunch" button on a dinner card is this in
action: one click makes tomorrow's lunch a leftover, and the batch — plus the
shopping quantities — grows to match.

## Choosing a model

Swapping `openrouter_model` has real gotchas: reasoning-token blowups, free-tier
churn, latency vs the client timeout. Ask Claude Code to use the
`openrouter-model-choice` skill before changing it.

One rule that is not negotiable: **every request disables reasoning**
(`extra_body={"reasoning": {"enabled": False}}`). With reasoning on, measured
on Claude Sonnet with an identical prompt, latency went 16s → 303s and the
response hit the token cap with *zero content*. The macro arithmetic is
already done in Python; there is nothing to deliberate about.

---

## Files

| File | |
|---|---|
| `planner.py` | Targets, prompts, model calls, portion fitting, CLI |
| `week.py` | All deterministic planning — the week is fully resolved before a token is generated |
| `ui_app.py` | NiceGUI web UI |
| `shopping.py` | Ingredient aggregation and normalisation |
| `repository.py` | The storage boundary — nothing else opens a file |
| `config.json` | Everything above |
| `week_plan.json` | The current generated week (regenerable) |
| `meal_history.json` | Style/cuisine rotation history (**not** regenerable) |
| `meals.log` | Per-day generation timing, finish reason, token counts |

`CLAUDE.md` is the deep architecture document — the *why* behind each design
decision, and the place to look before changing behaviour.

## Troubleshooting

**A Pydantic validation error after 3 retries** — that's `instructor`
surfacing the model's inability to satisfy the schema, not a code bug. Check
which field failed. If it's about kcal totals, the model is off by more than
the portion trim can absorb: swap models rather than widening
`PORTION_TRIM_LIMITS`.

**A day took minutes or came back empty** — check `meals.log`. A
`reasoning_tokens` count well above 0, or `finish_reason: length`, is the
reasoning-blowup signature, not a hung request.

**Calories right but protein low** — a single scale factor can't change a
macro *ratio*, so the portion trim can't fix it and it shows as a visible
delta in the day summary. If it's chronic, change the model.

**Shopping list looks wrong** — ingredient normalisation rules, and the bad
line each one fixes, are in `.claude/rules/shopping.md`.
