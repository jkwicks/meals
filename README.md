# Larder — AI Weekly Meal Planner

A macro-accurate weekly meal planner and shopping-list generator. Python
computes every calorie and gram target deterministically; an LLM (via
OpenRouter) only fills in real food to hit the numbers it's handed. This
document is both a **user manual** and an **end-to-end verification guide** —
Section 4 is a checklist you can run after any change to confirm the app
still does what this document says it does.

**Larder** is the web app's name — the browser tab, the window title and the
header wordmark all read it from `ui_theme.APP_NAME`. The name is the store
cupboard, and it names the half of this app that is actually unusual: the
pantry ledger, the fridge window bounding a batch, the shopping windows
grouped by cook day.

If you only want to *use* the app rather than change it, read
[`user-manual.md`](user-manual.md) instead — same app, plain English, no
architecture.

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
            api.py, generation_jobs.py, export_menu.py, and ui_*.py (the NiceGUI
            front end, one module per concern)
src/integrations/  sync_service.py — Garmin and Cronometer; keep_import.py — a once-off
config/     everything you edit — profile, meals, week, schedule, engine, models, integrations, presets
reference/  whfoods.json (shipped corpus, sampled to nudge generation)
data/       everything the app writes — week plans, history, biometrics, the recipe
            catalog, adherence marks and rejections
logs/       meals.log, nicegui.log, sync.log
scripts/    server.sh, sync.sh, release.sh, prepare.sh, upload.sh, claude-queue.sh,
            model-list.py
docs/       the research the planning rules are argued from
dev/        approved designs for work not built yet, and the prompts that implement them
```

The four file directories split by **who writes the file**, so "which file do
I change?" has one answer: `config/`. Paths are anchored to the project root
inside `repository.py`, not to the working directory, so `python
src/planner.py` finds them from anywhere. The shell scripts `cd` to the root
themselves for the same reason.

`config/` is eight files rather than one. Five of them (profile, meals, week,
schedule, engine) are merged back into a single object at load — see
`CLAUDE.md` for the key-to-file map — and a key in the wrong file or a typo'd
key name fails at startup naming the file, not silently. The other three
(`models.json`, `integrations.json`, `presets.json`) are loaded separately and
are optional: every value in them has an in-code default, so a checkout missing
any of the three plans exactly as it would with the file present and empty.

### Presets

A **preset** is a named set of overrides for the week — what is cooked, the
carb shape, how strict, how lazy — laid over the merged config before it is
validated (`config/presets.json`). It is not a diet: a diet strategy is
`dietary_rules.active_diet_styles`, which a preset may switch on; a preset is
that *plus* everything about the week that is not food. The choice is
**weekly**, made at the top of the review dialog, and the week it produced is
stamped with the preset's name so "did that change work?" stays answerable.

Presets are authored in **Settings → Presets** — a list you add to, edit and
delete, with an on-demand preview of the week each one resolves to. The editor
exposes the dozen dimensions a mood actually varies (NOVA groups, diet styles,
which meals cook, meal weights, per-day carbs, cuisine-block shape, favourite
pinning); hand-editing `config/presets.json` stays authoritative for anything
it does not show, and a hand-added override survives an edit untouched. An
invalid preset is refused at save with the reason named — the same check the
loader runs, so the editor and the next start cannot disagree about a file.
The shipped file holds one row, `default`, overriding nothing.

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
canvas: a header of seven per-day macro telemetry bars, a persistent
staged-changes bar beneath it naming anything queued for the next
generation, and a slim rail choosing one of six destinations — **Plan**
(the 7-column x 4-row grid of meal cards), **Today**, **Shopping**,
**Library** (the recipe catalog and import), **Insights**, **Settings**
(week start, shopping days, model, the preset editor, and the read-only sync
and location views). Shopping is the *same* panel the right-hand drawer draws: the
drawer is for reading a trip against the grid, the destination for working
through one. Every per-run input that used to live in a left
drawer — daily targets, training schedule, pantry, cuisine/diet-style/
bulk-prep picks, people per meal — now lives in the review dialog, opened
from either Plan's own Generate button or the staged-changes bar's "Review."

A week can also be generated headlessly from the CLI — see Section 5.

### Model configuration (`config/models.json`)

Model choice lives in its own file so that swapping models never touches your
macro targets:

| Key | Meaning |
|---|---|
| `meal_generation_model` | Model used to generate a week |
| `recipe_parser_model` | Model used to parse a pasted/imported recipe. Deliberately independent of the generation model, so a cheap fast model can do the parsing regardless of what generates the week |
| `request_timeout_seconds` | Client timeout. **No in-code fallback** — a missing value fails loudly at startup rather than drifting onto a stale default |
| `models` | The model ids the Settings destination's dropdown offers, each mapped to its quirks. `{}` means "nothing unusual". `{"reasoning_required": true}` marks a model that rejects the reasoning-disable switch with a hard `400`, for which the `reasoning` key is omitted entirely — see `CLAUDE.md`, "Reasoning must be disabled" |

The CLI's `--model` and the Settings destination's model select override
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

**Macro targets** live under **"Daily targets"** in the
review dialog (opened from Plan's Generate button or the staged-changes
bar's "Review"), one row per day (calories, protein g, net carbs g). Fat is
never typed — it's computed from what's left
(`calories - (protein*4 + carbs*4) / 9`), so a low-carb day automatically
becomes a high-fat day with no separate keto flag. An edited day is marked
with an amber `•` and amber label wherever its telemetry appears, and shows
up by name ("Mon +200 kcal") in the staged-changes bar; that override wins
over whatever the current plan or `config/profile.json` says, because
the point of editing a target before a run is seeing how far the current week
sits from where you're about to aim it. It's reset per-day from the review
dialog, which writes the file's numbers back in and clears the marker.
**Overrides apply on the next generation** — they never touch the files in
`config/`, and surviving a generation is intentional (see "Non-Blocking Week
Generation" below) — only the staged-changes bar's "Discard pending changes"
clears them.

A specific meal's budget can be pinned instead of weighted, via
`weekly_schedule.<day>.meal_overrides` in `config/profile.json`
(`{"breakfast": {"calories": 450, "protein_g": 45, "net_carbs_g": 25}}`). A
pinned meal is assigned that budget verbatim and pushes the *other* meals of
that day down so the day still totals its target.

**Training plans** live in the review dialog's **"Training schedule"**
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
budget-expanded target is being shown, and an add/remove/edit shows up in
the staged-changes bar the same way a target override does. Like targets
and pantry, training sessions are review-dialog-only input — they apply to
the next generation and are never written to any file in `config/`.

### Fibre — targeted, never budgeted

Fibre has a daily target, a per-meal share and its own reading on the
telemetry header (`FIB 24/30g`), and it is deliberately kept out of every
*budget*. The figure is a floor raised by a big day —
`max(user_profile.fiber_floor_g, calories / 1000 x 14)` — because a deficit
doesn't shrink what the gut needs: scaled alone, an 800 kcal day would ask for
11 g, cutting the target exactly as the small day starts to need the satiety.

It stays out of the energy identity `calories ≈ 4p + 4c + 9f` because it has no
term in it, so no validator checks it and nothing rejects a fibre-light
response. The prompt asks for fibre to be bought by **substitution at constant
macros** — wholegrain for refined, legumes for some of the starch, skins left
on — and forbids trading any of the four budgeted macros for it. A meal that
comes back short leaves the day visibly short rather than being distorted to
hide it. Where Cronometer has logged a figure for the same date, it's printed
*beside* the planned/target pair rather than under it: it's the same quantity
measured a second way, not a second goal.

### Biometric Sync — Garmin & Cronometer

`src/integrations/sync_service.py` fills four lists in `data/biometrics.json`,
with no phone-side app involved. Garmin Connect writes three — `weigh_ins`,
`readiness_log` (sleep score, sleep hours, HRV) and `activity_log` (what you
actually trained) — and Cronometer writes `daily_actuals` (logged calories,
protein, carbs, fat and fibre). Once that file holds a few weeks of both,
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

- `--sync-garmin` writes the weigh-in, the readiness row and the day's
  recorded activities. The first run logs in with your password and caches a
  token under `~/.garminconnect`; later runs reuse the cached token and only
  fall back to the password if it's expired, since Garmin rate-limits and
  MFA-challenges repeated password logins.
- `--sync-cronometer` writes that day's logged macros and fibre to
  `daily_actuals`. One export request covers a whole span, so a catch-up costs
  one call rather than one per day.
- **`--date` defaults to *yesterday*, not today**, and that is a correctness
  rule rather than a preference: a day is only complete once it's over. A
  morning fetch of today asks about a half-empty day and then checkpoints it
  as done, stranding everything logged or recorded later that day — the app
  never re-requests a checkpointed date. Naming `--date` explicitly means that
  day and only that day.
- The two flags fail independently — a Garmin outage doesn't cost you a
  working Cronometer sync, and vice versa — and each prints its own
  success/failure line.

Each source records a checkpoint of the last date it asked about, so a second
run the same day issues no requests at all, and a missed day is backfilled
rather than lost. Rows are upserted by date, except `activity_log`, which is
replaced per day — a re-sync's answer for a date *is* the answer for that date.
`data/biometrics.json` ships empty, so skipping this entirely just leaves
targets on `config/profile.json`'s numbers, with a warning logged rather than a
failure.

**Run it on a schedule rather than by hand.** `scripts/sync.sh` is the CLI
above with both sources and no `--date`:

```bash
./scripts/sync.sh run          # both sources, yesterday, catching up anything missed
./scripts/sync.sh install      # a launchd agent, daily at 07:30
./scripts/sync.sh status       # is it loaded, and each source's stored checkpoint
./scripts/sync.sh uninstall
MEALS_SYNC_HOUR=6 MEALS_SYNC_MINUTE=0 ./scripts/sync.sh install
```

Nothing in the app itself ever triggers a sync — not starting the server, not
opening a page. A scheduled job keeps a Garmin outage and a rate-limited
Cronometer out of the UI process, and covers the days you never open the app
at all. Settings' sync panel reports when anything last ran, and flags a single
source falling behind while the others advance, since those have entirely
different fixes.

### Proposing the week you actually trained

`training_schedule` is hand-declared while the watch records what really
happened, and `activity_log` is what introduces the two. The review dialog's
Training Schedule section diffs four weeks of recorded activity against the
declared week and offers one row per proposal — sessions to add, and declared
ones to drop — each with an accept and a dismiss.

**The detector is the easy half; the confirmation is the feature.** A schedule
written from a guess would move a day's calorie budget and pin its post-workout
meal off a pattern nobody agreed to, so nothing is applied without a click.
Accepting writes `config/schedule.json`, because the training schedule is your
*standing* week rather than an input to the next run — an accept that
evaporated on reload would re-offer the same proposal forever. A dismissal is
session-local on the opposite reasoning: it says "not now", and the evidence
for the proposal grows rather than goes.

Three states produce no proposals and mean different things — nothing recorded
yet, not enough history to see a pattern, and your declared week already
matching what was recorded. The last is the good answer most likely to be
misread as broken, so the panel names which one it is.

### Pantry Clearing (`inventory_to_clear`)

The review dialog's **"Pantry clear"** section is a row editor — item, grams,
remove — listing what's already in the house and should be cooked through.
Both shapes are legal and mean different things: a bare `"half a bag of
spinach"` is an unquantified item, and `{"item": "chicken thighs",
"quantity_g": 600}` is one the app can do arithmetic with. Requiring a
quantity would make the honest answer ("I don't know how much") unexpressible,
so neither shape is normalised away.

For **generation** it's a **priority, not a constraint**: the prompt tells the
model to prefer these items where they naturally fit, and explicitly forbids
bending a meal's macros, style or cuisine to use one up — a model told it
*must* use an item will wedge chicken thighs into a breakfast shake. A
quantified item is also *spent*: each meal type's call is handed what's left
after the earlier ones actually used, so one 600 g pack can't be written into
five recipes in the same week. That count never reaches disk — it would start
disagreeing with the real shelf the moment you cooked something without
telling the app.

For the **shopping list** it's subtracted, which it did not used to be. 600 g
of chicken thighs against an 800 g line leaves 200 g to buy; against a 400 g
line it covers it outright, and the line moves to a "covered from the pantry"
sentence rather than silently vanishing — a stale pantry is the failure mode
here, and a line that disappeared without trace is the one you couldn't notice
was wrong. An unquantified item is annotated only, since there is no number to
subtract. The subtraction happens at render time from the rows currently in
the drawer, so nothing is stored and nothing can drift.

### Buying what the shops actually stock (`sourcing`)

A generated week once called for mustard greens, which no supermarket within
reach carries. `sourcing` in `config/schedule.json` is the answer, and it lives
beside `regional` rather than in `dietary_rules` because it is a fact about the
**shops**, not about the body — move house and every value here changes while
not one dietary rule does.

| Key | Means |
|---|---|
| `supermarkets` | The shops a week is bought from, named verbatim in the prompt — a shop name tells the model more about what is on the shelf than any adjective could |
| `specialty_grocers_available_days` | Which weekdays an Asian grocer, deli or farmers' market is actually reachable on. Absent means every day; `[]` means none |
| `fresh_seafood_available_days` | Same shape, for a reliable fresh fish counter |
| `max_seafood_meals_per_week` | Whole-week cap on meals whose *dominant* protein is seafood |

It's soft guidance, phrased as a **substitution instruction rather than a
prohibition** ("substitute the closest ingredient a mainstream supermarket
stocks") — "don't use it" invites the model to abandon the cuisine over one
ingredient. An ingredient that must *never* appear belongs in
`banned_ingredients`, which is enforced as schema validation; `sourcing` covers
the unenumerable tail. The seafood cap is genuinely counted rather than merely
stated: no single generation call sees more than its own meal type, so each
stage is handed the remaining allowance the previous ones didn't spend.

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

### Slots decided before the model is called

Three things claim a slot ahead of generation, in this order, and everything
they claim is one fewer recipe the model is asked for — so a week with
favourites in it is also a cheaper week to run.

1. **Where you are that day.** `base_schedule` and `location_rules` in
   `config/schedule.json` say an Office lunch inherits the previous day's
   dinner and a Holiday block skips all four meals. It applies to a fresh grid
   only: once a week exists, its slots carry structural edits you made on
   purpose, and re-imposing the schedule over those would silently undo them.
2. **A morning gym session's breakfast is pinned to a shake.** A hypertrophy
   session starting at or before 11:00 gets one, because a shake is the only
   breakfast in `meal_styles` drinkable ten minutes before a session. Cardio
   and walks are deliberately excluded — forcing one on every cardio morning
   would empty the breakfast rotation for a session that doesn't need it.
3. **A saved favourite.** One standing breakfast across two mornings (the point
   of a standing breakfast is that it's the same one), one per eligible lunch,
   and up to two *distinct* dinners. Dinner is capped rather than
   one-per-slot because it's the only meal type cuisine blocks are laid over,
   and every pin blanks its slot's cuisine — uncapped against a large catalog,
   no block survives and the pantry overlap they exist for goes with them.

A pinned slot is still a **cook**, so portions derive, shopping aggregates it
and storage windows apply exactly as for a generated recipe. Eligibility is
strict least-recently-used over `favorite_reuse_days`, and a long-cook
favourite may only take a day with the hours in it — which is presence, not
the calendar: `location_rules.<location>.allows_long_cook` decides, falling
back to the weekend when a location says nothing. A working-from-home Tuesday
can start a braise at 8am; a Saturday you're out cannot.

### Non-Blocking Week Generation

**"Generate Current Week"** — the Plan destination's own button, or the
staged-changes bar's "Generate week" shortcut, or the review dialog's own
Generate button (the label follows the header's week selector) — runs the
whole week; CLI and UI both go through the same
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

### Why you rejected it

Hitting a card's regenerate icon used to be a pure discard — the recipe
vanished and an identically-briefed call replaced it, with nothing learned from
the fact that a real suggestion had just been thrown away. Once the replacement
lands, a small prompt offers four reasons (too much prep / don't fancy it / had
it recently / wrong for that slot). Ignoring it records nothing. Swapping a
card for a favourite offers the same prompt, since that is at least as
deliberate a "not this one".

Entries go to `data/rejections.json` as an event log, never an upsert:
regenerating the same slot twice is two facts, not a correction. They reach the
next generation as soft guidance beside the diet-style principles — a
rejection that hard-failed a response would cost a full 30s–3min retry for what
is, at worst, a repeated dish.

**Two signals with two windows**, because the rule was always carrying both.
The **dish list** is a veto on one recipe and expires per reason
(`rejection_decay_days`: 21 days for "had it recently", which is
self-resolving, out to 180 for "wrong for that slot", which is structural — a
curry is never breakfast). The **reason tally** is a standing statement about
how you want to eat, counted over the longer `rejection_reason_window_days`, so
a run of "too much prep" answers outlives the dish names that evidenced it. A
veto honoured forever would starve the rotation; a preference expiring with a
dish name would lose the more valuable half.

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
**Library** destination or a card's own icons:

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
the canvas's indigo eighth column. Candidates are the batches the review
dialog's two toggles actually anchored this run, matched by slot id rather than
by what the recipe says about itself — a model that forgets to flag its own
roast used to cost a genuine prepped batch its badge. A week with neither
toggle on falls back to any `long_oven_cook` dinner. Identical prep across
recipes is aggregated ("dice all the onions once"), and `max_prep_active_mins`
caps hands-on time — passive oven/slow-cooker time doesn't count against it.

**Prep day is the day *before* the week starts, not the Sunday on the grid.**
On a Monday-start week those are seven days apart, and nothing prepped ahead is
still food by the second one — so no batch may reach the last column, and every
fridge count runs from prep day rather than from the card's own column.

The two toggles take one meal type each, straight across the front of the week:
**bulk prep claims the lunches, long cook claims the dinners**, both starting
at day 1. That pairing isn't arbitrary — a soup or stew reheats at a desk and
travels in a container, an oven roast is dinner food — and it gives day 1 two
different dishes rather than the same one twice.

A card eating a prepped batch shows a ⚡ *Prepped on Sun* or ❄️ *From Freezer*
badge and a reheat estimate rather than the cook's from-scratch time, including
the anchor's own card: nothing is cooked fresh on that day either. This session
is never regenerated by a day/meal retry — it's dropped instead.

### How long a dish keeps

`inventory_rules.fridge_safe_days` used to be one global number, and **it was
wrong in both directions at once**: a beef stew keeps 4 days, so 3 threw away a
day of good food, while a rice or pasta dish keeps **2** — cooked rice carries
*Bacillus cereus* spores that survive cooking — so 3 let a rice tray bake
batched on prep day be eaten a day past its safe window. That key is gone. How
long a dish keeps is now a property of the dish:
`inventory_rules.storage_windows` holds the reference tables and the model
reports which row a recipe takes.

This is a **hard** constraint, in the same class as `banned_ingredients` rather
than the soft guidance `sourcing` and the diet styles give. The grid is planned
against the default window, each slot's brief names the span that slot actually
needs, and a dish whose class is too short for its slot is rejected and
retried. A pinned favourite is checked the same way — it's never generated, so
nothing else could catch it.

**Every default here fails short**, which inverts this codebase's usual
convention that an absent value means the behaviour before the feature existed.
An unclassified or unrecognised dish resolves to the *shortest* row, not the
default one, because the precedent is a model dropping a field the prompt
explicitly asked for — and if that resolved long, a forgotten field would put a
rice dish four days out. Being wrong here makes somebody ill rather than
producing a worse meal plan.

The tables are stated in hours and **nothing prints hours at you**: the app
measures whole day-gaps, because nothing anywhere stores a cook *time*. The
freezer half is about quality, not safety, and its wording says so — "past its
best", never "unsafe" — and nothing is ever auto-removed from a hand-declared
list.

### Marking what actually happened

Nothing used to observe whether a planned meal was eaten. A week could be
planned, cooked around and half-ignored, and the app's own record of it stayed
the week it had generated. The Today destination (and the day inspector, which
shares its renderers) puts three marks on each meal — **eaten**, **skipped**,
**swapped** — written to `data/adherence.json`.

**Three statuses rather than a tick**, because the two failures differ in kind
and that difference is the whole reason for recording anything: a *skipped*
meal is a day that came short of a target, where a *swapped* one is a day fed
by something else. Clicking a mark a slot already carries clears it, so three
buttons are a complete control rather than three one-way doors, and clearing
something never marked writes nothing at all.

Marks persist on click rather than staging for the next generation — a mark
isn't an input to a run, so there's nothing for the staged-changes bar to hold
and a tick that vanished on reload would be a control with no effect. A slot
set to `skip` isn't markable and isn't in the day's denominator: nothing was
planned there, so "did you eat it" has no answer.

Workouts are mostly **derived** rather than marked. `activity_log` already
records what the watch saw, so a hand mark is offered only for a session it
never recorded — a lift on a day the watch was flat, a class with no device. A
stored "completed" beside a Garmin recording would be a second answer to one
question, free to disagree the moment a re-sync changed either.

### Insights

The Insights destination draws five readouts, each gated on its own
precondition: weight against target, the weigh-in table, planned against
logged intake, macro accuracy, and adherence tiles.

**It evaluates rather than describes.** Each readout reports one of four states
— nothing recorded yet, too few points to draw a line, drawn but thin, or ready
— because "nothing recorded" and "recorded but not enough" spell identically as
a missing chart and have completely different fixes. A thin chart is still
drawn, anchored on the data's own last row and captioned with what it's made
of; it's a fixed 30-day axis with six dots in one corner that misleads, not a
short window honestly labelled.

Two details worth knowing when reading them. The weight chart's target line
appears only once the target is in view — the y-axis is scaled to the weigh-ins,
because a zero-based one renders a real 0.6 kg week as a flat line, and a
target outside the plot would leave a chart headed "weight against target" with
no target on it. And the adherence percentage's denominator is **marks**, and
says so in words: the plans those dates were generated against are gone from
`week_plan.json` the moment a new week is generated over them, so "of meals
planned" would be a divider under a number nobody counted.

### PDF menu export

The printer icon in the header downloads `weekly_menu.pdf`: a day-by-day
summary grid, the prep checklist, one page per recipe grouped by meal type, and
a department-grouped shopping list. It reads `WeekPlan` directly (not the UI's
view model), so it always matches the grid on screen including unsaved edits.
There is deliberately no separate print stylesheet — print the PDF from your
browser's viewer instead of maintaining a second layout.

### The HTTP API

The NiceGUI server mounts a small JSON API on its own FastAPI app — no second
port, no second process. Five reads, one write, and the two routes that answer
for the write:

| Route | Returns |
|---|---|
| `GET /api/weeks/{current\|next}` | The stored week plan |
| `GET /api/recipes?favorite=&meal_type=&search=` | The recipe catalog, filtered |
| `GET /api/history` | The rotation history |
| `GET /api/biometrics` | All four biometric lists plus the latest reading |
| `GET /api/targets` | `weekly_schedule` after hydration, plus which TDEE won |
| `POST /api/weeks/{current\|next}/generate` | `202` and a job id |
| `GET /api/jobs`, `GET /api/jobs/{id}` | The run's status, stages and failures |

Every read route calls an existing repository method or an existing pure
function and returns the answer — **a route that computed something would be a
route free to disagree with the UI.**

Generation answers with a **job id** rather than streaming, because the event
rate says so: progress fires once per meal type, at most four times in a run of
up to twenty minutes. That's not a streaming problem, and a client polling
every few seconds sees every event from `curl`, a script or a phone shortcut.
The finished week is deliberately *not* on the job — it's saved through the
repository before the job completes, so `GET /api/weeks/…` already answers for
it, and a copy on the job would be a second answer free to disagree.

A browser tab and an API client share one single-flight claim, so a second
Generate is refused with a `409` naming who holds it rather than racing to
overwrite the same file. The registry is in memory in one process, which is
exactly what the server runs today.

There is **no auth** — the app is localhost-only. There are also no OpenAPI
docs: NiceGUI hardcodes them off on the app this router mounts onto.

---

## 4. End-to-End Verification Checklist

Run this after any change that touches targets, generation, chaining or
shopping. Each item names the surface to look at and what "working" means.

- [ ] **Macro telemetry recalculation on schedule edits.** Edit a day's
      calorie/protein/carb target in the review dialog (or add a training
      session). The header's telemetry bar and numbers for that day update
      immediately, without a page reload or generation run, and the day gets
      its marker (amber `•` for an override, green `⚡` for training) — and
      the staged-changes bar shows the edit by name.
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
- [ ] **A batch never outlives its dish's fridge window.** Turn both prep
      toggles on in the review dialog and generate. Confirm no leftover eats a
      batch more day-gaps after its cook than that dish's `storage_class`
      allows — counting from **prep day**, the day before the week starts, not
      from the anchor's own column — and that the storage note and the card's
      fridge/freezer badge agree with each other.
- [ ] **Marks persist without staging.** Mark a meal eaten on the Today
      destination, then reload the page. The mark is still there, and the
      staged-changes bar never mentioned it — a mark is a record of a day, not
      an input to the next run. Click the same mark again and it clears.

---

## 5. CLI vs. UI Commands

| Action | CLI (`src/planner.py`) | UI (`src/ui_app.py`) |
|---|---|---|
| Generate a week | `python src/planner.py` | Plan destination → **Generate**, or the staged-changes bar's **Generate week** |
| Use a different config directory | `--config-dir PATH` | — (always `config/`) |
| Override the model for one run | `--model NAME` | Settings destination model selector |
| Set the week's start day | `--week-start DAY` | Fixed by `week_start_day` in config |
| Set household size | `--servings N` | Review dialog **"People per meal"** field |
| Set shopping trip days | `--shop-days Sunday,Wednesday` | `config/week.json` (`shopping.shop_days`) |
| Make every lunch a leftover of the prior dinner | `--leftover-lunches` | Per-dinner **"Link to next lunch"** button |
| Export shopping lists as Markdown | `--save-shopping-list` → `data/shopping_list.md` | — |
| Export a shopping trip for Google Keep | — | Per-trip **"Copy for Keep"** button |
| Re-use the last generated plan without an API call | `--use-cached-plan` | Grid always shows the last saved `week_plan.json` until you generate again |
| Regenerate a single day or meal | — | Refresh icon on a day header / on a card |
| Favorite, import or swap in a recipe | — | **Library** destination; card bookmark and ⇄ icons |
| Export the week as a PDF menu | — | Header printer icon → `weekly_menu.pdf` |
| Keep a second week in progress | — | Header **Current / Next Week** selector |
| Edit a day's macro target for the next run | Edit `config/profile.json` `weekly_schedule` | Review dialog → **Daily targets** |
| Pin one meal's budget | `config/profile.json` `meal_overrides` | (not yet editable from the UI) |
| Add a training/workout session | `config/schedule.json` `training_schedule` | Review dialog → **Training schedule** |
| Prioritize using up pantry items | `config/week.json` `inventory_to_clear` | Review dialog → **Pantry clear** (item + grams rows) |
| Mark a meal eaten / skipped / swapped | — | **Today** destination, or the day inspector |
| Accept a training session Garmin recorded | Edit `config/schedule.json` | Review dialog → **Training schedule** → accept |
| Choose who owns calories/protein (auto or manual) | `config/profile.json` `target_modes` | Settings destination → **Daily targets** |
| Pick this week's preset | Edit `config/presets.json` `active` | Review dialog → top of the dialog |
| Create / edit / delete a preset | Edit `config/presets.json` `presets` | Settings destination → **Presets** |
| Read the week, catalog, history or targets as JSON | — | `GET /api/…` on the running server |
| Generate a week from a script or a phone | `python src/planner.py` | `POST /api/weeks/current/generate`, then poll `GET /api/jobs/{id}` |
| Print shopping lists to the terminal | Always, after generation | — (use the shopping drawer) |
| Monitor per-call generation timing/failures | `logs/meals.log` | Progress dialog (live) + warning toast on completion |
| Sync weigh-ins / readiness / activity / logged intake | `./scripts/sync.sh run`, or `src/integrations/sync_service.py --sync-garmin --sync-cronometer` | — (read-only status in Settings) |
| Run the sync daily, unattended | `./scripts/sync.sh install` (launchd) | — |

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

Everything in Sections 3 and 5 that isn't editable from the review dialog or
Settings destination lives in `config/`.
The "File" column is the one to open; see `CLAUDE.md` for why the split falls
where it does.

| Key | File | Meaning |
|---|---|---|
| `user_profile` | `profile.json` | Height, birth date, target weight, activity level, protein multiplier, `fiber_floor_g` — what the dynamic targets are computed from |
| `weekly_schedule.<day>` | `profile.json` | Per-day `calories`, `protein_g`, `net_carbs_g`, `meal_overrides`. Calories and protein are recomputed from the body when a weigh-in exists **and that macro's `target_modes` entry is `auto`**; `net_carbs_g` and `meal_overrides` always survive |
| `target_modes` | `profile.json` | `auto` or `manual` per macro, for the two macros with two possible sources (`calories`, `protein_g`). `manual` means the file's number is the week's number. Written by the Settings toggle — one of the handful of UI actions that persist to `config/` (the others: accepting a Garmin schedule proposal, the weekly preset pick, and the preset editor) |
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
| `inventory_to_clear` | `week.json` | What's in the house to cook through. A bare string is unquantified; `{"item": ..., "quantity_g": ...}` is subtracted from the shopping list and spent across the run (see Section 3) |
| `enable_sunday_prep` | `week.json` | Turns on the batch-prep session and its canvas column |
| `max_prep_active_mins` | `week.json` | Hands-on ceiling for that session (passive oven/slow-cooker time is not counted) |
| `inventory_rules.storage_windows` | `week.json` | Two reference tables in **hours** — `fridge` (per `storage_class`, e.g. `default: 96`, `rice_or_pasta: 48`) and `freezer_months`. A configured table merges over the shipped one, so naming one row can't silently lengthen another. Replaces `fridge_safe_days`, which was one global number and wrong in both directions |
| `inventory_rules.perishable_day_gap` | `week.json` | Gap after which a perishable is flagged "buy fresh closer to the day". **Currently inert** — `shopping.py` reads the module constant `week.PERISHABLE_DAY_GAP` instead, and the two happen to agree at 3. Filed in `CHANGE-QUEUE.md` |
| `training_schedule` | `schedule.json` | List of `{day, time, type, duration_minutes, estimated_burn_kcal}` |
| `base_schedule` / `location_rules` | `schedule.json` | Where you are each day, and what that does to its meals — `<meal_type>_mode`, `restrictions`, and `allows_long_cook` (whether you're home with the hours for something to sit in the oven; absent falls back to the weekend) |
| `sourcing` | `schedule.json` | Which shops, which days a specialty grocer or fish counter is reachable, and the week's seafood cap (see Section 3) |
| `planning_rules.history_max_entries` | `engine.json` | How many past days of rotation history to retain |
| `planning_rules.protein_lookback_entries` / `protein_avoid_window` | `engine.json` | How far back to look for recent main proteins, and how many to name in the prompt |
| `planning_rules.portion_trim_limits` | `engine.json` | Clamp on the post-generation portion rescale, e.g. `[0.6, 1.6]`. Also derives the threshold above which a response is rejected and retried |
| `planning_rules.portion_trim_deadband` | `engine.json` | Trims smaller than this are skipped as noise |
| `planning_rules.min_meal_protein_g` | `engine.json` | Floor each cooked meal is briefed at, by moving grams between meals rather than creating any. Skipped entirely when the day can't afford it |
| `planning_rules.max_meal_share_multiple` | `engine.json` | How far past its weighted share a meal may be briefed when earlier meals came back under budget. Stops the last meal of the day absorbing the whole shortfall; the day lands visibly under target instead |
| `planning_rules.cuisine_block_pattern` | `engine.json` | Contiguous blocks of days sharing one cuisine, as a ratio scaled to the days actually cooked. `[4, 3]` gives four nights of one cuisine and three of a complementary second; `[1,1,1,1,1,1,1]` restores a different cuisine every night |
| `baseline_cuisines` | `meals.json` | The everyday-Western pool (`homestyle`, `modern_australian`, `pub_classic`) other cuisines are routed toward for the block *after* theirs, so a spice-paste-heavy block is followed by a plain roast-and-veg one |
| `planning_rules.min_baseline_cuisine_share` | `engine.json` | Floor on the share of the week's cook days reserved for that pool, so an adventurous cuisine reads as the exception rather than the default. Inert while `baseline_cuisines` is empty |
| `planning_rules.favorite_breakfast_slots` / `favorite_dinner_slots` | `engine.json` | How many slots saved favourites may claim before generation. Breakfast is one favourite across N mornings; dinner is N *distinct* favourites |
| `planning_rules.favorite_reuse_days` | `engine.json` | Per meal type, how recently a favourite may have been cooked and still be eligible — strict least-recently-used, e.g. `{"breakfast": 7, "lunch": 21}` |
| `planning_rules.rejection_decay_days` | `engine.json` | Per reason, how long a rejected dish stays vetoed. `had_it_recently` expires soonest; `wrong_for_slot` barely decays |
| `planning_rules.rejection_reason_window_days` | `engine.json` | The longer window the *reason* tally is counted over, so a standing preference outlives the dish names that evidenced it |
| `planning_rules.batch_target_servings` | `engine.json` | Servings the bulk-prep / long-cook toggles spread an anchor toward. A ceiling, not a promise |
| `ui_settings.bar_scale_limit` | `engine.json` | How far past target a telemetry bar keeps growing before it stops |
| `ui_settings.title_tooltip_chars` | `engine.json` | Title length above which a card gets a full-name tooltip |
| `meal_generation_model` / `recipe_parser_model` | `models.json` | The two model roles. Each must also appear in the same file's `models` table, which is where per-model quirks like `reasoning_required` live — a role naming a model the table doesn't describe fails at load |
| `garmin.exercise_recovery_factor` | `integrations.json` | Fraction of an activity's gross calories counted as genuinely additional (0.50) |
| `active` / `presets` | `presets.json` | The weekly preset pick and the preset catalog. Each preset is `{label, overrides}` where `overrides` maps a dotted config leaf path to the value that week wants instead; the leaf is replaced whole. Not part of the merged config, not in `AppConfig` — a supplemental file like `models.json`. Edit via **Settings → Presets** or by hand |
| `--model` / Settings destination select | — | Model id for this run only, overriding `meal_generation_model`. Not a config-file key — it is never written to disk, and unlike the file's roles it is deliberately free-form. Both unset is a hard error, never a silent fallback |

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
| `src/nutrition_engine.py` | BMR/TDEE/deficit arithmetic, the adaptive estimate, the fibre target, session burn, and the schedule proposal |
| `src/api.py` | The JSON API mounted onto NiceGUI's own FastAPI app (see Section 3) |
| `src/generation_jobs.py` | The single-flight claim a browser tab and an API client share, and the job records a client polls |
| `src/ui_state.py` | `PlannerState` — the view model, and the only UI module with tests |
| `src/ui_presets.py` | The preset editor shown in Settings — a list of records plus the save-time validator |
| `src/integrations/sync_service.py` | Garmin and Cronometer sync (see Section 3) |
| `src/integrations/keep_import.py` | A once-off bootstrap of the recipe catalog from a Google Keep Takeout export |
| `config/profile.json` | Body, per-day targets, meal weights, dietary rules |
| `config/meals.json` | Meal types, styles, cuisines and affinities |
| `config/week.json` | Week shape, shopping days, prep and pantry |
| `config/schedule.json` | Training sessions and location/regional context |
| `config/engine.json` | Planner tuning and UI settings |
| `config/models.json` | Model selection and timeout (see Section 2) |
| `config/integrations.json` | Garmin/Cronometer sync tuning |
| `config/presets.json` | The weekly preset pick and the preset catalog (see Presets, Section 2) |
| `reference/whfoods.json` | Nutrient-dense whole foods; ~12 are sampled per run to nudge generation |
| `data/recipes_master.json` | Recipe catalog — every recipe ever favorited or imported |
| `data/week_plan.json` | The current generated week (regenerable) |
| `data/week_plan_next.json` | The "Next Week" slot — the app keeps two cached weeks at once |
| `data/meal_history.json` | Style/cuisine rotation history (**not** regenerable) |
| `data/biometrics.json` | Four lists from the sync above — weigh-ins, readiness, activity, logged intake (ships empty) |
| `data/adherence.json` | Whether each planned meal was eaten, skipped or swapped, and workouts the watch never saw |
| `data/rejections.json` | Why a suggested recipe was thrown away — an event log, never an upsert |
| `logs/meals.log` | Per-call generation timing, finish reason, token counts |
| `logs/sync.log` | Where the scheduled sync reports, and what a "did the scheduler stop?" question is answered against |

Bundles for pasting into an AI assistant — `python_codebase.md`,
`project_context.md`, `data_schemas.md` — are **generated** by `./scripts/prepare.sh`.
Edit `CLAUDE.md` and re-run it; never edit the bundles directly.

`user-manual.md` is the plain-English guide for using the app without any of
the above. `CLAUDE.md` is the deep architecture document — the *why* behind each design
decision, and the place to look before changing behaviour. `CHANGE-QUEUE.md`
is the only current answer to "what should I work on next": every unfinished
item and known defect in one ranked list, plus a table of what shipped in which
release. `dev/` holds approved designs for work not built yet; `docs/` holds
the research the planning rules are argued from.

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

**A Cronometer sync fails with a 403 quoting `NotLoggedInException`** — that's
an expired saved session, not bad credentials, and the `.env` you're about to
re-check is fine. The upstream client can't tell: an expired session is
answered with HTTP 200 and a serialized exception whose class name passes for
an auth token. The sync clears the cached session and retries **once**, and
only when there was one to clear — a run that already logged in fresh has a
real failure, and a second full login against an endpoint that rate-limits
would double the cost of every genuine one.

**A biometric list is empty even though the account has the data** — check the
checkpoints (`./scripts/sync.sh status`) before suspecting the mapping. Each
source records the last date it *asked* about and never revisits it, so a list
added after those dates were checkpointed stays empty forever without a
`--date` backfill. Diagnose it by fetching before the filter: one
`--sync-garmin --date <a day you trained>` prints every activity found, marking
the ones it dropped, which separates "never asked" from "asked and rejected".

**Nothing is suggesting a favourite you saved** — eligibility is strict
least-recently-used over `favorite_reuse_days`, so a dish cooked inside that
window is deliberately skipped. A long-cook favourite also needs a day with the
hours in it (`allows_long_cook` on that day's location, falling back to the
weekend), and one that keeps long enough for the slot it would fill.
