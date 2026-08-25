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
- **Generation cost scales with the meal types actually cooked.** One API call
  per *meal type*, asking for that meal on every day it's cooked at once —
  breakfast, then dinner, then lunch, then snack. A meal type that's leftover
  or skipped all week makes no call at all.

  Generating by meal type rather than by day buys two things a per-day call
  couldn't have. The model sees all seven dinners in one request, so it can be
  told not to repeat a main protein across the week. And each day's budget
  *cascades*: once breakfast is generated, its **actual** macros — not its
  a-priori weighted share — are subtracted from every affected day before
  dinner's budget is split. Dinner runs before lunch so that the one
  cross-meal-type leftover the app allows (a lunch eating last night's dinner)
  always has its source already cooked.

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

Requires **Python 3.11+** and a free [OpenRouter](https://openrouter.ai) API
key. Developed against Homebrew's 3.14 (`/opt/homebrew/bin/python3.14`); 3.11
is the real floor because `cronometer-mcp` needs it.

```bash
/opt/homebrew/bin/python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy your key into `.env` (a placeholder is already there):

```
OPENROUTER_API_KEY=sk-or-v1-...
```

Add `GARMIN_*` / `CRONOMETER_*` too if you want the biometric sync.

This project used to run on the macOS system Python 3.9 and carried two pins
to prop that up. Both are **gone**, noted here so they don't get helpfully
restored: `eval_type_backport` (3.10+ evaluates `instructor`'s `str | Path`
unions natively) and `urllib3<2` (Homebrew's Python links real OpenSSL 3.x,
so the `NotOpenSSLWarning` it silenced cannot occur). See `CLAUDE.md`'s Setup
section.

### Project layout

Source, data and scripts are separated; run everything from the project root:

```
src/        planner.py, week.py, repository.py, shopping.py, nutrition_engine.py,
            export_menu.py, and ui_*.py (the NiceGUI front end, one module per concern)
src/integrations/  sync_service.py — Garmin and Cronometer
config/     everything you edit — profile, meals, week, schedule, engine, models, integrations
reference/  whfoods.json (shipped corpus, sampled to nudge generation)
data/       everything the app writes — week plans, history, biometrics, recipe catalog
logs/       meals.log, nicegui.log
scripts/    server.sh, release.sh, prepare.sh, upload.sh, claude-queue.sh, model-list.py
```

The four file directories split by **who writes the file**, so "which file do
I change?" has one answer: `config/`. Paths are anchored to the project root
inside `repository.py`, not to the working directory, so `python
src/planner.py` finds them from anywhere. The shell scripts `cd` to the root
themselves for the same reason.

`config/` is seven files rather than one. Five of them (profile, meals, week,
schedule, engine) are merged back into a single object at load — see
`CLAUDE.md` for the key-to-file map — and a key in the wrong file or a typo'd
key name fails at startup naming the file, not silently. The other two
(`models.json`, `integrations.json`) are loaded separately and are optional:
every value in them has an in-code default.

### Start the server

```bash
./scripts/server.sh start      # NiceGUI desktop canvas on http://localhost:8080
./scripts/server.sh status
./scripts/server.sh stop
./scripts/server.sh restart
MEALS_PORT=9000 ./scripts/server.sh start
```

`scripts/server.sh` handles venv activation, backgrounding (`nohup`), the PID
file (`logs/.nicegui.pid`) and the log (`logs/nicegui.log`). Open
[http://localhost:8080](http://localhost:8080) for the high-density desktop
canvas: a left drawer of global controls, a header of seven per-day macro
telemetry bars, and a 7-column x 4-row grid of meal cards below it.

A week can also be generated headlessly from the CLI — see Section 5.

### Model configuration (`config/models.json`)

Model choice lives in its own file so that swapping models never touches your
macro targets:

| Key | Meaning |
|---|---|
| `meal_generation_model` | Model used to generate a week |
| `recipe_parser_model` | Model used to parse a pasted/imported recipe. Deliberately independent of the generation model, so a cheap fast model can do the parsing regardless of what generates the week |
| `request_timeout_seconds` | Client timeout. **No in-code fallback** — a missing value fails loudly at startup rather than drifting onto a stale default |
| `models` | The model ids the drawer's dropdown offers, each mapped to its quirks. `{}` means "nothing unusual". `{"reasoning_required": true}` marks a model that rejects the reasoning-disable switch with a hard `400`, for which the `reasoning` key is omitted entirely — see `CLAUDE.md`, "Reasoning must be disabled" |

The CLI's `--model` and the drawer's model select override
`meal_generation_model` **for that run only** — neither writes to the file.

Token budgets are **not** configured here — they're derived in `planner.py`
from whether the model is a `:free` route and how many recipes a single call is
asking for (`meal_type_week_max_tokens`), because a 7-dinner call needs roughly
double what a 4-recipe day did.

Before changing a model, invoke the `openrouter-model-choice` skill — free-tier
churn, reasoning-token blowups and timeout headroom all have real gotchas that
have each cost a full run before.

---

## 3. Feature Guides & Workflows

### Target Tuning & Training Plans

**Macro targets** live under **"Daily Targets"** in the
left drawer, one row per day (calories, protein g, net carbs g). Fat is
never typed — it's computed from what's left
(`calories - (protein*4 + carbs*4) / 9`), so a low-carb day automatically
becomes a high-fat day with no separate keto flag. An edited day is marked
with an amber `•` and amber label wherever its telemetry appears; that
override wins over whatever the current plan or `config/profile.json` says, because
the point of editing a target before a run is seeing how far the current week
sits from where you're about to aim it. It's reset per-day from the drawer,
which writes the file's numbers back in and clears the marker. **Overrides
apply on the next generation** — they never touch the files in `config/`.

A specific meal's budget can be pinned instead of weighted, via
`weekly_schedule.<day>.meal_overrides` in `config/profile.json`
(`{"breakfast": {"calories": 450, "protein_g": 45, "net_carbs_g": 25}}`). A
pinned meal is assigned that budget verbatim and pushes the *other* meals of
that day down so the day still totals its target.

**Training plans** live in the drawer's **"Training Schedule"**
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
never written to any file in `config/`.

### Biometric Sync — Garmin & Cronometer

`src/integrations/sync_service.py` pulls two things into `data/biometrics.json`,
with no phone-side app involved: body-weight readings from Garmin Connect and
logged food intake from Cronometer. Once that file holds a few weeks of both,
day targets stop coming purely from `config/profile.json` and start reflecting
your actual measured TDEE — see "Target Tuning & Training Plans" above and
`CLAUDE.md`'s "Targets come from the body" section for the full mechanics.

**One-time setup** — add credentials to `.env` (alongside `OPENROUTER_API_KEY`):

```
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=...
CRONOMETER_USERNAME=you@example.com
CRONOMETER_PASSWORD=...
```

Cronometer needs a paid tier that supports web login — there's no public API
for personal accounts, so the sync drives the same protocol the web app does.

**Run a sync** (from the project root, venv active):

```bash
./venv/bin/python src/integrations/sync_service.py --sync-garmin
./venv/bin/python src/integrations/sync_service.py --sync-cronometer --date 2026-08-24
./venv/bin/python src/integrations/sync_service.py --sync-garmin --sync-cronometer --date 2026-08-24
```

- `--sync-garmin` writes the latest weigh-in(s) to `weigh_ins`. The first run
  logs in with your password and caches a token under `~/.garminconnect`;
  later runs reuse the cached token and only fall back to the password if it's
  expired, since Garmin rate-limits and MFA-challenges repeated password
  logins.
- `--sync-cronometer --date YYYY-MM-DD` writes that day's logged
  calories/protein/carbs/fat to `daily_actuals`. Omit `--date` to sync today.
- The two flags fail independently — a Garmin outage doesn't cost you a
  working Cronometer sync, and vice versa — and each prints its own
  success/failure line.

Both write by upsert-on-date, so re-running for the same day overwrites rather
than duplicates. There's no schedule to set up: run either command whenever
you want fresher numbers. `data/biometrics.json` ships empty, so skipping this
entirely just leaves targets on `config/profile.json`'s numbers, with a
warning logged rather than a failure.

### Pantry Clearing (`inventory_to_clear`)

The drawer's **"Pantry Clear"** section is a free-text list of things
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

**"Generate Current Week"** in the drawer (the label follows the header's
week selector) runs the whole week — CLI and UI both go through the same
`generate_week_plan`. In the UI each meal type's call runs on a background
thread while the event loop stays free, so the browser stays fully interactive
(other tabs, other clicks) for the 5–20 minutes a full week can take. A
progress dialog shows:

- a progress bar and "Generating Dinners (2/4) — 7 recipe(s)…" status line,
  advancing once per **meal type**;
- a scrolling log of notes — portion trims, failed meals — that arrive
  mid-run, kept in a log rather than a single status label because two
  different notes can arrive close together and a label would overwrite the
  one you were reading.

A missing/invalid API key is caught up front, before any call is made, so a
misconfiguration fails once with a clear message instead of four identical
failures after a long wait. **A failed meal never fails the week** — it's
recorded in `WeekPlan.failures` (keyed by `day:meal_type`), shown as a warning
toast, and rendered as a red "NOT GENERATED" card you can regenerate; every
other meal's result is kept and saved. Because one call now covers a whole meal
type, a bad call can cost up to seven recipes rather than one day's worth —
that's the accepted trade for the week-wide variety and budget cascading it
buys. Nothing is adopted onto the grid unless the run finishes and saves
successfully, and the plan is saved to disk *before* it's adopted into the
visible grid — so a browser refresh can never lose a completed run.

### Regenerating without redoing the week

Two narrower retries sit alongside the full run, both saving to disk and
adopting exactly the way a full generation does:

- **Regenerate a day** — the refresh icon beside a day's name in the canvas
  header. One call, re-cooking every cook slot on that day. Other days are
  untouched: a leftover on this day already points backwards at a resolved
  source, and a later day pointing *at* this one picks up the new recipe
  automatically because the slot id doesn't change.
- **Regenerate a meal** — the small refresh icon on an individual card,
  including a red NOT GENERATED one. The narrowest retry in the app: every
  other slot on the day (leftover *or* independently cooked) is treated as
  fixed, its macros subtracted, and the entire remaining budget goes to this
  one meal.

Both drop a saved Sunday prep session if the regenerated food entered or left
the batch-prep candidate set — a stale prep timeline is worse than none.

### Staggered Shopping Lists

The shopping-list drawer (opened from the header) shows one section per
shopping trip (`shopping.shop_days`), each grouped into departments, built
against cook events in that window. It's rebuilt from the current plan on
every repaint, so a leftover link made a minute ago is already reflected in
the quantities. A failed meal shows an explicit note in the trip it would have
contributed to, so a short list reads as "a day failed" rather than "a cheap
week." Perishables the window buys too early for are flagged inline
("← buy fresh closer to the day") rather than moved to a different trip — the
list still shows everything the trip's recipes need.

Two export paths, for two different use cases:

- **"Copy for Keep"** (per-trip button in the UI drawer) copies a plain
  one-line-per-item list to the clipboard, formatted so pasting into Google
  Keep turns each line into its own checkbox.
- **`--save-shopping-list`** (CLI flag) writes every window to
  `data/shopping_list.md` as Markdown, in addition to printing them to the
  terminal.

### Recipe Catalog — favorites, imports and swaps

`recipes_master.json` is the one place recipe content outlives the week it was
generated in (`week_plan.json` is overwritten every run, and `meal_history.json`
keeps only lean per-day summaries). Three ways in and out, all from the
drawer's **"Recipe Catalog"** expansion or a card's own icons:

- **Bookmark** a cooked card to add it to the catalog and favorite it in one
  click. Un-favoriting keeps the entry — only the explicit delete removes it.
- **Import** pasted recipe text, an ingredient list, or a URL. It's parsed into
  grams, macros and NOVA groups by `recipe_parser_model` under the *same*
  dietary rules generation enforces, so an imported recipe answers to the same
  blocklist and NOVA groups a generated one does.
- **Swap** any generated card for a favorite via its ⇄ icon. The modal shows
  the favorite's per-serving macros beside the budget a fresh generation would
  have aimed that slot at, so you can see what the swap costs you. The favorite
  is normalised to one serving and rescaled to the slot's derived batch size.

Identity is content-based (`repository.recipe_content_key`: name plus rounded
ingredient composition), so re-cooking the same dish next month resolves to the
same catalog entry instead of a duplicate.

### Two cached weeks — Current and Next

The header's selector switches between two independently cached plans,
`week_plan.json` and `week_plan_next.json`. Switching only ever *reads* from
disk. The Generate button relabels itself to whichever week is showing
("Generate Next Week"), so a run can't silently overwrite the wrong one.

### Sunday Prep Session

With `enable_sunday_prep` on, one extra call after the week is generated
reorganises its batch cooks into a single ahead-of-time prep timeline, shown in
the canvas's indigo eighth column. Candidates are cook events that both keep
beyond their cook day *and* are `long_oven_cook` — a genuinely long, mostly
hands-off roast/bake/braise. A quick stir-fry cooked in bulk is deliberately
excluded: it still needs active attention on its own day. Identical prep across
recipes is aggregated ("dice all the onions once"), and `max_prep_active_mins`
caps hands-on time — passive oven/slow-cooker time doesn't count against it.

A leftover card eating a prepped batch shows a ⚡ *Prepped on Sun* or ❄️ *From
Freezer* badge and a reheat estimate rather than the cook's from-scratch time.
This session is never regenerated by a day/meal retry — it's dropped instead.

### PDF menu export

The printer icon in the header downloads `weekly_menu.pdf`: a day-by-day
summary grid, the prep checklist, one page per recipe grouped by meal type, and
a department-grouped shopping list. It reads `WeekPlan` directly (not the UI's
view model), so it always matches the grid on screen including unsaved edits.
There is deliberately no separate print stylesheet — print the PDF from your
browser's viewer instead of maintaining a second layout.

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
      "Generate Current Week" and, while it's running, interact with something else
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
      than a typical meal of that type. (`logs/meals.log` and the day's recipe
      notes are the source of truth here — the model can still miss a
      constraint on a bad response, which is what the portion/macro retry
      logic exists to catch.)

---

## 5. CLI vs. UI Commands

| Action | CLI (`src/planner.py`) | UI (`src/ui_app.py`) |
|---|---|---|
| Generate a week | `python src/planner.py` | Drawer → **Generate Current Week** |
| Use a different config directory | `--config-dir PATH` | — (always `config/`) |
| Override the model for one run | `--model NAME` | Drawer model selector |
| Set the week's start day | `--week-start DAY` | Fixed by `week_start_day` in config |
| Set household size | `--servings N` | Drawer servings field |
| Set shopping trip days | `--shop-days Sunday,Wednesday` | `config/week.json` (`shopping.shop_days`) |
| Make every lunch a leftover of the prior dinner | `--leftover-lunches` | Per-dinner **"Link to next lunch"** button |
| Export shopping lists as Markdown | `--save-shopping-list` → `data/shopping_list.md` | — |
| Export a shopping trip for Google Keep | — | Per-trip **"Copy for Keep"** button |
| Re-use the last generated plan without an API call | `--use-cached-plan` | Grid always shows the last saved `week_plan.json` until you generate again |
| Regenerate a single day or meal | — | Refresh icon on a day header / on a card |
| Favorite, import or swap in a recipe | — | Drawer → **Recipe Catalog**; card bookmark and ⇄ icons |
| Export the week as a PDF menu | — | Header printer icon → `weekly_menu.pdf` |
| Keep a second week in progress | — | Header **Current / Next Week** selector |
| Edit a day's macro target for the next run | Edit `config/profile.json` `weekly_schedule` | Drawer → **Daily Targets** |
| Pin one meal's budget | `config/profile.json` `meal_overrides` | (not yet editable from the drawer) |
| Add a training/workout session | `config/schedule.json` `training_schedule` | Drawer → **Training Schedule** |
| Prioritize using up pantry items | `config/week.json` `inventory_to_clear` | Drawer → **Pantry Clear** |
| Print shopping lists to the terminal | Always, after generation | — (use the shopping drawer) |
| Monitor per-call generation timing/failures | `logs/meals.log` | Progress dialog (live) + warning toast on completion |
| Sync weigh-ins / logged intake | `src/integrations/sync_service.py --sync-garmin` / `--sync-cronometer --date YYYY-MM-DD` | — (not in UI) |

CLI-only and UI-only differences are structural, not accidental: the CLI is
the batch/scriptable path and is the only one that writes `data/shopping_list.md`
or prints to the terminal; the UI is the only one with live, pre-generation
previews (telemetry, chaining, training) because those need a browser to
interact with before committing to a 5–20 minute run. Both write the same
`week_plan.json` and append to the same `meal_history.json` — a week started
on one front end can be inspected, or its shopping list pulled, from the
other.

---

## Configuration reference

Everything in Sections 3 and 5 that isn't drawer-editable lives in `config/`.
The "File" column is the one to open; see `CLAUDE.md` for why the split falls
where it does.

| Key | File | Meaning |
|---|---|---|
| `user_profile` | `profile.json` | Height, birth date, target weight, activity level, protein multiplier — what the dynamic targets are computed from |
| `weekly_schedule.<day>` | `profile.json` | Per-day `calories`, `protein_g`, `net_carbs_g`, `meal_overrides`. Calories and protein are recomputed from the body when a weigh-in exists; `net_carbs_g` and `meal_overrides` always survive |
| `meal_weights` | `profile.json` | How a day's calories split across un-pinned meals |
| `dietary_rules.allowed_nova_groups` | `profile.json` | NOVA processing groups allowed (group 4 is always rejected) |
| `dietary_rules.banned_ingredients` | `profile.json` | Substring blocklist, enforced as schema validation |
| `dietary_rules.active_diet_styles` | `profile.json` | Which `diet_styles` entries are in effect. Soft guidance via the prompt, not a hard constraint; an unknown name fails at startup |
| `diet_styles` | `meals.json` | The catalog of named eating patterns to choose from — `label` plus `principles` |
| `week_defaults` | `meals.json` | Default mode (`cook`/`leftover`/`skip`) per meal type |
| `meal_styles` / `cuisines` / `cuisine_meal_types` | `meals.json` | Style/cuisine pools; anything left `auto` rotates least-recently-used from `meal_history.json`. A gym/cardio session starting before 11:00 pins that day's breakfast to `custom_shake` unless you picked a style yourself |
| `cuisine_affinities` | `meals.json` | `cuisine -> cuisines that share its pantry`, used to pick the week's second cuisine block. Optional — an unlisted cuisine just falls back to the least-recently-used pick |
| `week_start_day` | `week.json` | First day of the planning week |
| `serving_rules.servings_per_meal` | `week.json` | Household size |
| `shopping.shop_days` | `week.json` | Days you shop — defines the shopping windows |
| `inventory_to_clear` | `week.json` | Free-text priority list (see Section 3) |
| `enable_sunday_prep` | `week.json` | Turns on the batch-prep session and its canvas column |
| `max_prep_active_mins` | `week.json` | Hands-on ceiling for that session (passive oven/slow-cooker time is not counted) |
| `inventory_rules.fridge_safe_days` | `week.json` | Days a cooked batch keeps refrigerated before the storage note says "freeze the rest" |
| `inventory_rules.perishable_day_gap` | `week.json` | Gap after which a perishable is flagged "buy fresh closer to the day" |
| `training_schedule` | `schedule.json` | List of `{day, time, type, duration_minutes, estimated_burn_kcal}` |
| `planning_rules.history_max_entries` | `engine.json` | How many past days of rotation history to retain |
| `planning_rules.protein_lookback_entries` / `protein_avoid_window` | `engine.json` | How far back to look for recent main proteins, and how many to name in the prompt |
| `planning_rules.portion_trim_limits` | `engine.json` | Clamp on the post-generation portion rescale, e.g. `[0.6, 1.6]`. Also derives the threshold above which a response is rejected and retried |
| `planning_rules.portion_trim_deadband` | `engine.json` | Trims smaller than this are skipped as noise |
| `planning_rules.min_meal_protein_g` | `engine.json` | Floor each cooked meal is briefed at, by moving grams between meals rather than creating any. Skipped entirely when the day can't afford it |
| `planning_rules.max_meal_share_multiple` | `engine.json` | How far past its weighted share a meal may be briefed when earlier meals came back under budget. Stops the last meal of the day absorbing the whole shortfall; the day lands visibly under target instead |
| `planning_rules.cuisine_block_pattern` | `engine.json` | Contiguous blocks of days sharing one cuisine, as a ratio scaled to the days actually cooked. `[4, 3]` gives four nights of one cuisine and three of a complementary second; `[1,1,1,1,1,1,1]` restores a different cuisine every night |
| `planning_rules.batch_target_servings` | `engine.json` | Servings the bulk-prep / long-cook toggles spread an anchor toward. A ceiling, not a promise |
| `ui_settings.bar_scale_limit` | `engine.json` | How far past target a telemetry bar keeps growing before it stops |
| `ui_settings.title_tooltip_chars` | `engine.json` | Title length above which a card gets a full-name tooltip |
| `meal_generation_model` / `recipe_parser_model` | `models.json` | The two model roles. Each must also appear in the same file's `models` table, which is where per-model quirks like `reasoning_required` live — a role naming a model the table doesn't describe fails at load |
| `garmin.exercise_recovery_factor` | `integrations.json` | Fraction of an activity's gross calories counted as genuinely additional (0.50) |
| `--model` / drawer select | — | Model id for this run only, overriding `meal_generation_model`. Not a config-file key — it is never written to disk, and unlike the file's roles it is deliberately free-form. Both unset is a hard error, never a silent fallback |

The five core files are validated against the `AppConfig` Pydantic model at
startup with `extra="forbid"` — a typo'd or unknown key fails immediately with
a clear message naming the file, before any API call, rather than being
silently ignored.

All ingredient quantities are grams, all energy is kcal — no cups, oz or lbs
anywhere in the schema.

---

## Files

| File | |
|---|---|
| `src/planner.py` | Targets, training adjustments, prompts, model calls, portion fitting, CLI |
| `src/week.py` | All deterministic planning — the week is fully resolved before a token is generated |
| `src/ui_app.py` | NiceGUI web UI |
| `src/shopping.py` | Ingredient aggregation, normalisation, Keep/Markdown formatting |
| `src/export_menu.py` | Week → printable PDF menu (`reportlab`) and its Markdown equivalent |
| `src/repository.py` | The storage boundary — nothing else reads or writes a stored file |
| `src/integrations/sync_service.py` | Garmin weigh-in and Cronometer intake sync (see Section 3) |
| `config/profile.json` | Body, per-day targets, meal weights, dietary rules |
| `config/meals.json` | Meal types, styles, cuisines and affinities |
| `config/week.json` | Week shape, shopping days, prep and pantry |
| `config/schedule.json` | Training sessions and location/regional context |
| `config/engine.json` | Planner tuning and UI settings |
| `config/models.json` | Model selection and timeout (see Section 2) |
| `config/integrations.json` | Garmin/Cronometer sync tuning |
| `reference/whfoods.json` | Nutrient-dense whole foods; ~12 are sampled per run to nudge generation |
| `data/recipes_master.json` | Recipe catalog — every recipe ever favorited or imported |
| `data/week_plan.json` | The current generated week (regenerable) |
| `data/week_plan_next.json` | The "Next Week" slot — the app keeps two cached weeks at once |
| `data/meal_history.json` | Style/cuisine rotation history (**not** regenerable) |
| `data/biometrics.json` | Garmin weigh-ins and Cronometer daily intake, from the sync above (ships empty) |
| `logs/meals.log` | Per-call generation timing, finish reason, token counts |

Bundles for pasting into an AI assistant — `python_codebase.md`,
`project_context.md`, `data_schemas.md` — are **generated** by `./scripts/prepare.sh`.
Edit `CLAUDE.md` and re-run it; never edit the bundles directly.

`CLAUDE.md` is the deep architecture document — the *why* behind each design
decision, and the place to look before changing behaviour.

## Troubleshooting

**A Pydantic validation error after 3 retries** — that's `instructor`
surfacing the model's inability to satisfy the schema, not a code bug. Check
which field failed. If it's about kcal totals, the model is off by more than
the portion trim can absorb: swap models (see the `openrouter-model-choice`
skill) rather than widening `planning_rules.portion_trim_limits` — widening it
would let through portions absurd enough to be unusable.

**A call took minutes or came back empty** — check `logs/meals.log`. A
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
