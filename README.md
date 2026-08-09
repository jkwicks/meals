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
