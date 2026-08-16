# AI Weekly Meal Planner

## Setup

**Python 3.14**, from Homebrew (`/opt/homebrew/bin/python3.14`), in `venv/`:

    /opt/homebrew/bin/python3.14 -m venv venv
    source venv/bin/activate && pip install -r requirements.txt

The project ran on the macOS **system Python 3.9** until the 3.14 move, and
two requirements existed only to prop that up. Both are now deleted, recorded
here because their absence is the thing a future reader might otherwise
"helpfully" restore:

- `eval_type_backport` — `instructor`'s internals use `str | Path` union
  syntax that 3.9's typing module can't evaluate; the backport patched it in
  for pydantic. 3.10+ evaluates it natively.
- `urllib3<2` — the system Python linked Apple's LibreSSL 2.8.3 rather than
  real OpenSSL, and urllib3 v2 warns (`NotOpenSSLWarning`) when it can't find
  OpenSSL 1.1.1+. Homebrew's 3.14 links OpenSSL 3.x, so the warning can't
  occur and urllib3 is unpinned.

`instructor` and `pydantic` resolve to the same versions on 3.14 as they did
on 3.9, so everything the Architecture section says about `MD_JSON` mode and
the `context=` kwarg still holds. What *did* move: `nicegui` 3.6 -> 3.16 and
`garminconnect` 0.2.8 -> 0.3.10 (see "Biometric sync" — the two garminconnect
lines disagree about token persistence, and the interpreter is what picks
between them).

Set `OPENROUTER_API_KEY` in `.env` (copy the placeholder already there), plus
`GARMIN_*`/`CRONOMETER_*` if you use the biometric sync.

## Layout

Six directories, flat inside each — `src/` (the Python modules), `scripts/`
(the shell entry points), `tests/`, and four for files:

    config/     hand-edited. Change these to change what the app does.
    reference/  shipped corpora (whfoods.json). Read-only in practice.
    data/       written by the app. Never hand-edit.
    logs/       written by the app. Disposable.

Root holds only README.md, CLAUDE.md, .env, .gitignore and requirements.txt.

**The four-way split is by who writes the file, not by what the file is
about**, because "which file do I edit to change X" is the question a reader
actually arrives with. The test of whether a file is in the right place is
`.gitignore`: `data/` and `logs/` are ignored as whole directories, so
anything needing a per-file exception is a file whose lifecycle disagrees
with its neighbours. There is exactly one exception today —
`data/recipes_master.json`, app-written but worth keeping in history — and it
is called out in `.gitignore` rather than left to be inferred.

It is **not** a package: no `__init__.py`, no `setup.py`, and the modules
import each other as flat siblings (`from week import ...`), which keeps
working because `python src/planner.py` puts `src/` on `sys.path[0]`. Don't
convert these to relative imports.

Paths are anchored on `__file__` in `repository.py` (`PROJECT_ROOT`,
`CONFIG_DIR`, `REFERENCE_DIR`, `DATA_DIR`, `LOGS_DIR`), never relative to the
working directory — `./scripts/server.sh` runs the app from the root while a
bare `python planner.py` runs it from `src/`, and a cwd-relative `data/…`
would resolve in only one of those. The shell scripts each `cd` to the project
root for the same reason. Anything new that needs a file should go through
`StoragePaths`, not spell out a relative path.

### config/ is six files, merged back into one dict

`config.json` was one 196-line file holding twenty unrelated top-level keys.
It is now six, and `LocalJSONRepository.load_config()` merges them into the
same flat dict `AppConfig` has always validated — so **nothing downstream of
the repository knows the config arrived in pieces**, and `planner`, `week` and
`ui_app` still read `config["weekly_schedule"]` exactly as before. Splitting
the *files* without splitting the *object* is the whole trick; namespacing the
dict would have touched hundreds of call sites for no gain.

| file | holds |
|---|---|
| `profile.json` | the body and the numbers aimed at it — `user_profile`, `weekly_schedule`, `meal_weights`, `dietary_rules` |
| `meals.json` | what a meal may be — `meal_types`, `meal_styles`, `cuisines`, `cuisine_affinities`, `cuisine_meal_types`, `week_defaults` |
| `week.json` | the shape of a week — `week_start_day`, `shopping`, `serving_rules`, `enable_sunday_prep`, `max_prep_active_mins`, `inventory_to_clear`, `inventory_rules` |
| `schedule.json` | where you are and what you're doing — `training_schedule`, plus the location keys (see below) |
| `engine.json` | tuning for the planner, not the food — `planning_rules`, `ui_settings` |
| `models.json` | model selection (see "Picking a model") |
| `integrations.json` | sync tuning (see "Biometric sync") |

`CONFIG_FILES` in `repository.py` is the manifest of which file owns which
key, and the merge validates against it: a key in the wrong file, a typo'd
key, or a missing file each fail at load with the **filename** in the message.
That is strictly better than `AppConfig`'s `extra="forbid"`, which knows a key
is unwanted but not where it should have gone — and far better than the
silent-default failure, where the file holding `weekly_schedule` goes unread
and a week gets planned against nothing.

Adding a field to `AppConfig` therefore means adding it to `CONFIG_FILES` too.
The merge says so if you forget. That coupling is deliberate: a new key has to
belong to *some* file, and deciding which one at the moment it is added is the
entire point.

`tests/test_config_layout.py` holds a snapshot of the merged dict and asserts
nothing was lost or altered. Regenerate it (`python tests/test_config_layout.py
--update`) only alongside a deliberate change, so the diff shows exactly which
keys moved.

## Run

Run from the venv: `source venv/bin/activate`, then
`python src/planner.py --help` for flags.

For the web UI use `./scripts/server.sh start` — it handles venv activation,
nohup, the PID file and the log:

    ./scripts/server.sh start              # NiceGUI (src/ui_app.py) on :8080
    ./scripts/server.sh status
    ./scripts/server.sh stop
    MEALS_PORT=9000 ./scripts/server.sh start

### Web UI

NiceGUI (`ui_app.py`) is the only web UI. The Streamlit app (`app.py`) was
deleted once the migration landed; if you need its rendering or grid-editing
code as a reference, it is in git history at `git show e237872:app.py`.

A week can be generated from either front end — `python src/planner.py` or the
NiceGUI drawer's "Generate Current Week" — and both go through the same
`generate_week_plan`, write the same `week_plan.json` and append the same
history. The CLI is still the one that prints shopping lists.

### NiceGUI front end

`ui_app.py` (`./scripts/server.sh start`, serves on :8080) is the high-density desktop
UI: left drawer for global controls, a header of 7 per-day macro bars, and a
7-column x 4-card canvas. Both grids are `grid-cols-8` — an indigo Sunday-prep
column sits at index 0, ahead of the seven days — so a day's telemetry stays
directly above its meals. Cook/leftover/skip/not-generated are four distinct card
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
  per-stage handler it would become one identical failure per meal type after a
  long wait, because "a failed meal must not fail the week" is exactly the
  wrong policy for a misconfiguration that will fail every call.
- The progress modal is built once per page and *opened* per run, so the
  `progress_callback`/`note_callback` handlers just assign to elements that
  already exist. Notes go to a `ui.log` rather than a status label because
  portion trims and failed meal types both arrive mid-run and a label would
  overwrite the one you were reading.
- Only a whole-run exception reaches the `except` (no config, storage
  unwritable); per-meal-type failures arrive in `WeekPlan.failures` and become a
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
- It also rescales the affected cook events, via
  `Recipe.scale_to_servings()` directly on each event's recipe. Portions being
  derived means a card reading "4 portions" over ingredients weighed for 2 is
  exactly the disagreement the derived-portions rule exists to prevent, and the
  fix is linear arithmetic, not a regeneration call.
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

The left drawer's "Daily Targets" and "Pantry Clear" sections (plus "Training
Schedule") edit `PlannerState`, never the files in `config/`, and are merged into a config
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
  can't silently disagree about what a slot says. It has no button today —
  it is kept as the text-pipeline counterpart (diffing two weeks, pasting a
  menu into a note) and is the reason `_slot_entry` is factored out at all. Printing this document is
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
- **Generation cost** scales with the meal types actually cooked: one API call
  per *meal type*, covering every day it's cooked, and a meal type that is
  leftover or skipped all week is free. See `generate_week_plan` — generation
  runs along the meal-type axis, not the day axis.

Days are walked in week order (`WeekSpec.days`, rotated by `week_start_day`)
so a leftover's source recipe always exists before its macros are needed —
which is why `validate_week` rejects a leftover pointing at a later day. The
meal-type *order* (`MEAL_TYPE_PRIORITY`) carries the same guarantee across
types: dinner is generated before lunch so the one cross-type leftover
`week.leftover_meal_type_error` permits always has its source already cooked.

- `config/` — external configuration, six files merged and validated once at
  load through `AppConfig` (`extra="forbid"`, so an unknown or typo'd key
  fails at startup). See "config/ is six files" under Layout.
  Model selection lives in `config/models.json`: `meal_generation_model` is
  the standing choice, and `config["openrouter_model"]` is a per-run
  selection injected **in memory only** by the CLI's `--model` and the
  drawer's model select. There is **no in-code model default** — both unset
  raises (`resolve_planner_model`), deliberately, so the app can never
  silently plan against a stale hardcoded model.
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

    Two of the three choices it makes are **not** per-slot picks, and can't be:

    - **Cuisines are laid out in contiguous blocks**, not one per night —
      `planning_rules.cuisine_block_pattern` (4/3 by default) scaled to the
      days actually cooked by `cuisine_block_sizes`, then filled by
      `pick_cuisine_blocks`. Seven cuisines is seven half-used jars of paste
      and a shopping list with no overlap anywhere in it; four Mexican nights
      and three Cajun ones share a pantry. The first block is the same strict
      LRU pick as before, so rotation *across* weeks is unchanged; later
      blocks prefer a cuisine `config.cuisine_affinities` lists as
      complementary to the one before (thai -> vietnamese share fish sauce,
      lime, coriander), falling back to global LRU when that list is empty.
      A slot-at-a-time LRU pick structurally cannot produce a block, which is
      why this had to move out of the per-slot loop. Explicit cuisines are
      left alone and seeded into the LRU *first*, so a hand-picked Wednesday
      pushes the auto blocks away from itself rather than being overwritten.
    - **A morning session's breakfast is pinned to a shake** before any
      rotation runs: `morning_training_days()` (gym/cardio starting at or
      before `MORNING_TRAINING_CUTOFF`, walks excluded) picks the days,
      `week.pin_style()` applies `WORKOUT_BREAKFAST_STYLE`. A shake is the
      only breakfast in `meal_styles` drinkable ten minutes before a session,
      and the style rotation has no way to know that — left to it, a 06:30 gym
      slot gets eggs and smoked salmon on toast about one week in five. An
      *evening* session is deliberately not covered: it is already handled as
      macros by `apply_training_adjustments` (expanded budget, pinned
      post-workout meal, pre-workout digestion note), and pinning a *style*
      is only warranted when the session lands before the meal can settle.
      Both are pins, not overrides — a style or cuisine the user chose in the
      drawer always survives, the same precedence a hand-written
      `meal_overrides` entry gets over a computed one.

    The prompt side of blocking lives in `generate_meal_type_week`, which is
    the only call that can see the whole week: `build_cuisine_continuity_rule`
    tells the model which days share a cuisine *on purpose* and to make those
    nights differ by protein/vegetable/method instead, and it swaps
    `WEEK_STYLE_RULE` for `WEEK_CUISINE_BLOCK_STYLE_RULE` — the standing rule
    says consecutive days must differ in tradition, which is the exact
    opposite of a 4/3 split and invites the model to "fix" the repetition by
    substituting a cuisine. It emits nothing at all when no cuisine spans more
    than one day, so a hand-picked week of seven cuisines still reads as
    before. `DINNER_VARIETY_RULE` gained "never the same primary protein on
    two consecutive nights" for the same reason: once four nights are Greek,
    the week-wide "no protein more than twice" cap is satisfied by lamb, lamb,
    chicken, chicken, which reads as the same meal twice.

    `SHAKE_ROTATION_RULE` (whole-week, sent when more than one breakfast is a
    shake) and `SHAKE_SLOT_DIRECTIVE` (per slot, so a single regenerated shake
    gets it too) are the same split: keep the base identical, rotate the
    secondary components, and spread the pools evenly rather than demanding
    every ingredient be unique — config lists three fruits and three seeds, and
    a rule that can't be satisfied is one the model drops entirely.
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

### Targets come from the body, not the file

`weekly_schedule`'s per-day calories and protein are no longer what the week
is planned against. `hydrate_dynamic_targets()` replaces them with
`nutrition_engine.calculate_macro_targets()`'s output — BMR from the latest
weigh-in, TDEE from the activity factor, and a deficit that slides with the
remaining gap to `target_weight_kg`. It is a **pure function**; `hydrate_config()`
is the thin `async` wrapper that fetches the weigh-in for it.

It is called at the top of all three generation entry points
(`generate_week_plan`, `regenerate_single_day`, `regenerate_single_meal`)
rather than once in the CLI, because NiceGUI builds its config in the
*synchronous* `PlannerState.planning_config()`, which cannot await storage.
Hydrating where the repository is already in hand gets both front ends onto
the same numbers with no UI change. The consequence worth knowing: the
drawer's telemetry header still previews the **file's** targets, so before a
run it can disagree with what the run will actually aim at. Closing that gap
means giving `planning_config()` a weigh-in, which is a `ui_app.py` change.

Four things it deliberately does *not* compute:

- **Each day's `net_carbs_g`** is passed *into* the engine, not replaced, so
  carb cycling in `weekly_schedule` survives and fat absorbs the difference.
- **`meal_overrides` written by hand** stay verbatim — a pin is a fixed budget
  by definition.
- **The training uplift** is replayed from `training_uplift`, a record
  `apply_training_adjustments` now leaves behind. Only the *calorie* uplift:
  the carb share is already inside the `net_carbs_g` figure being passed
  through (replaying it would double it), and the protein share is dropped
  because protein is locked. A workout buys back carbs and fat, not protein.
- **The post-workout pin** *is* recomputed (`training_pin_budget`, now its own
  function, keyed off `training_pins`) because it is a fixed number derived
  from targets hydration just replaced. Left alone it claimed 49 g of protein
  worked out from the file's 164 on a day that now has 144 — enough to push
  the day's snack under the floor and make `apply_protein_floor` give up on
  the whole day.

**Protein is locked to the target weight, not today's and not the day's
activity.** 80 kg x 1.8 is 144 g every day of the week. Tying it to current
weight would shrink the floor exactly as the diet began to threaten the lean
mass it exists to protect.

`planning_rules.min_meal_protein_g` (35 g) then makes that per-day figure
reach each meal: `split_targets` ends with `apply_protein_floor`, which
**moves grams between meals rather than creating any** — under-floor slots are
raised, donors give in proportion to their surplus, and calories travel with
the protein at 4 kcal/g so every budget still reconciles and the day's totals
are conserved exactly. A weight-only split gives the 0.10-weighted snack ~14 g
of a 144 g day, which is a snack with no protein source in it. Pinned and
leftover slots are excluded (a leftover's protein comes from its source
recipe). When the floor is unaffordable it does **nothing** and logs — raising
some meals and starving others would be an arbitrary choice about which meal
gets short-changed, and a day that can't carry `n x 35 g` is a target problem,
not a split problem.

Everything degrades to the file's numbers, with a warning and a UI note, when
no weight is available. That is not the fabricated body `nutrition_engine`
refuses to invent: `weekly_schedule` holds real targets somebody chose.
**`biometrics.json` ships empty, so this fallback is the normal path until the
first Garmin sync lands** — a checkout with no weigh-ins plans exactly as it
did before this section existed.

### Regenerating one meal against what you actually ate

`regenerate_single_meal` checks `daily_actuals` via `logged_intake_for()`. When
the slot's day *is* today and Cronometer has logged it, the log replaces the
plan for the meals already behind you: earlier slots (ordered by
`MEAL_TIME_OF_DAY`) are dropped from the carried total so their planned macros
aren't double-counted against the log that already contains them, later slots
keep their reservation, and the model is briefed on the genuine remaining
deficit rather than the planned one.

`logged_intake_for` returns None — "use the plan" — for any day that isn't
today (a `SlotSpec` carries only a weekday name, so next Thursday is not the
Thursday that was logged) and for an all-zero row, which a partial sync can
write and which would otherwise read as "you have eaten nothing today" and
hand one meal the entire day's budget.

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
- **The generation calls are still synchronous, dispatched to threads.** Both
  `generate_meal_type_week()` (the weekly path) and `generate_day()` (the
  day/meal retry paths) block on instructor's sync client for 30s–3min, so
  `generate_week_plan()` hands each meal type to `asyncio.to_thread` — that is
  what makes the `await` a real yield. Awaiting it inline held the loop for the whole run: invisible in the
  CLI, fatal in NiceGUI, where it froze every connected browser and the
  progress updates it was meant to be showing couldn't be delivered until the
  run they described had finished. Meal types stay strictly sequential (one
  thread at a time, in `meal_type_order`) because each stage's per-day budget is
  computed from every earlier stage's *actual* output — this is about not
  blocking the loop, not about going faster.
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
   the portion without changing the dish. Clamped to
   `planning_rules.portion_trim_limits` in `config/engine.json` (0.6–1.6) so a trim can
   never produce an absurd portion.
3. **`DayRecipes.reject_untrimmable_macro_miss()`** — a `model_validator` that
   rejects only what layer 2 *can't* rescue, i.e. a response needing a factor
   outside `planning_rules.portion_trim_limits`, so `instructor` hands the
   model its own numbers back and retries. Same mechanism that already enforces NOVA groups
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

### A failed meal must not fail the week

`generate_week_plan()` catches per-*meal-type* exceptions into
`WeekPlan.failures` — keyed by **slot_id** (`"Monday:dinner"`), not by day —
and carries on to the next meal type. Sequential calls on a free route are
sequential chances to hit an unfixable provider failure: an empty completion
(`choices` is `None`, which crashes inside `instructor`'s own response
parser), a rate-limit, a model that can't hit the budget. Losing three good
meal types to a bad fourth is the worst possible outcome after a 20-minute run.

Note the trade the meal-type axis accepts: one bad call now costs up to seven
recipes rather than the single day's worth a per-day call could lose. That is
paid for by week-wide protein variety and real budget cascading, and softened
by the two narrower retries below.

Failed slots render as "not generated" and their ingredients never reach a
shopping list; the CLI prints them and the UI shows a warning telling you to
re-generate. Orphaned leftovers pointing at a failed cook contribute 0 macros,
so the day shows up as a visible shortfall rather than crashing.

**Both narrower retries must clear what they fix.** `regenerate_single_day`
pops every cook slot on the day out of `failures`; `regenerate_single_meal`
pops its one slot. Forgetting this doesn't show up on the card (which reads
`cook_events`, so it turns green) — it shows up in the drawer's failure list
and the shopping drawer's "nothing for those meals is on this list" note,
which keep naming a meal that now exists. The per-card regenerate button is
offered *on* NOT GENERATED cards, so that is the common path, not an edge case.

### Reasoning must be disabled — this is not optional

Every request sends `extra_body=reasoning_extra_body(model, config)`
(`planner.py`), which is OpenRouter's unified switch for a model's hidden
reasoning budget, `{"reasoning": {"enabled": False}}`, **except for models
marked `"reasoning_required": true` in `config/models.json`.** Do not change
the *default* to enabled — see the measurement below for why — but do mark a
model if it needs the exception (next section).

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
that model fails within a second. `"reasoning_required": true` on the
model's entry in `config/models.json` is how `reasoning_extra_body()` knows to
omit the `reasoning` key entirely for a model like this rather than sending
`enabled: True` — the reasoning is disabled by default *because* the task
needs none, and that's equally true whether or not the provider insists on
doing it anyway. If a newly picked model fails every slot in under a second
with this exact message, it needs that flag, not a workaround in the prompt.

The flag lives on the model's own entry rather than in a parallel list of
ids, because a second list beside the selectable ones is free to name a model
no longer offered, or to miss one that is.

### Diagnosing a slow or failed call

`configure_logging()` (called from both `planner.main()` and `ui_app.py` at
import time) writes per-call generation timing to `logs/meals.log`: request start,
elapsed seconds, `finish_reason`, `completion_tokens`, and `reasoning_tokens`
for every `generate_day()` call, plus a line for any day that fails. This is
the same data the manual diagnostic below asks you to check by hand —
`reasoning_tokens` far above 0 or `finish_reason: length` in the log is the
signature of the reasoning-blowup failure mode, not a hung request.

### Picking a model

`config/models.json` names **two roles**, because there are two, and they want
different models:

- `meal_generation_model` — what a week is generated with (`generate_day`,
  `generate_meal_type_week`, `generate_sunday_prep_session`).
- `recipe_parser_model` — what `import_external_recipe` parses pasted recipe
  text with. Cheap and mechanical, so it runs on a fast model regardless of
  what the week costs. It deliberately does **not** follow the generation
  model.

Its `models` table doubles as the drawer's selectable list (the UI offers its
keys) and as the home for per-model quirks; an entry with nothing unusual
about it is just `{}`.

`config["openrouter_model"]` is a third thing and is **not a file key**: it is
the per-run selection injected in memory by `--model` and the drawer's model
select, and no front end ever writes it to disk. It used to exist as a
config.json field too, where its only effect was to give the standing choice a
second place to hide.

There is no `openrouter_base_url` key any more — it was the same URL for every
model and a knob nobody turned, so it is a constant in `planner.py`.

Swapping the generation model has real gotchas (reasoning-token blowups,
free-tier churn, latency variance vs. the client timeout). They live in the
`openrouter-model-choice` skill — invoke it before changing
`meal_generation_model` or the `models` table.

### Shopping lists

`shopping.py` aggregates cook events (not days) and normalises ingredient
names before combining them. Every normalisation rule and the bad line it
fixes are in `.claude/rules/shopping.md`, which loads automatically when
working on `shopping.py`.

Duplicate *staples* are attacked from both ends. `PANTRY_CONSOLIDATION_RULE`
(in `build_generation_rules`, so both generation axes send it) asks the model
for one variant per staple — one cottage cheese, one mustard, one oil — and
says explicitly that this is not the food-variety rule two lines above it,
because the two pull in opposite directions unless the prompt names the
difference. `shopping.CANONICAL_INGREDIENTS` then catches what the model
produces anyway: "Sardines (canned)", "sardines in water (tinned)" and
"tinned sardines" are one purchase, and only the first two normalise to the
same key on their own. It is deliberately narrow — an entry there *asserts*
two names are the same thing, which is exactly the merge `STATE_QUALIFIERS`
exists to prevent when they aren't, so a canonical name carrying a state only
claims names whose own state is absent or equivalent ("frozen sardines" stays
its own line) and exclusion lists keep "mustard seeds" out of mustard and "oat
milk" out of oats.

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

### Biometric sync — Garmin Connect and Cronometer

`src/integrations/sync_service.py` fills the two lists `biometrics.json`
holds, with no phone-side app in the loop:

    ./venv/bin/python src/integrations/sync_service.py --sync-garmin
    ./venv/bin/python src/integrations/sync_service.py --sync-cronometer --date 2026-08-16

`GarminSyncService` writes `weigh_ins`, `CronometerSyncService` writes
`daily_actuals`, both through `LocalJSONRepository`'s existing upsert-by-date
methods. Neither invents storage, and the CLI reports each source
independently — a Garmin outage must not cost a Cronometer sync that would
have worked, the same policy as "a failed meal must not fail the week".

Six things here are decisions, not detail:

- **It is the only code in `src/` living in a subdirectory**, which breaks the
  flat-sibling import rule at the top of this file: `python
  src/integrations/sync_service.py` puts `src/integrations/` on `sys.path[0]`,
  not `src/`, so `from repository import ...` fails with the real module one
  directory up. The `sys.path.insert` near the top is what buys the
  subdirectory back — it is load-bearing, and every editor and linter will
  resolve the import fine without it.
- **Macro keys are the repository's, not the upstream's.** Cronometer's column
  is `Protein (g)` and the obvious key is `protein`, but `daily_actuals` rows
  are read by `nutrition_engine.calculate_macro_targets`, which indexes
  `protein_g`/`net_carbs_g`/`fat_g`. A row keyed the CSV's way stores, sorts
  and displays perfectly and feeds *nothing* — the failure surfaces weeks
  later as an adaptive loop that never adapts.
- **Exercise calories are discounted by `EXERCISE_RECOVERY_FACTOR` (0.50).**
  Garmin reports an activity's gross calories, which include the BMR that hour
  would have cost anyway — and every TDEE figure the app computes already
  contains that hour. Adding the gross number double-counts the overlap and
  inflates the day by a few hundred kcal, which is most of a deficit. Both
  `gross_calories` and `net_calories` are kept on each session, because a
  silently adjusted number can't be reconciled against the watch.

  `config/integrations.json`'s `garmin.exercise_recovery_factor` is the knob,
  injected into `GarminSyncService` at construction rather than read at the
  point of use, so the value that discounted a session is the one the service
  was built with. It is the *only* genuine knob the sync has: credentials live
  in `.env`, the Garmin token directory is already `GARMINTOKENS`, and the
  activity-type keys and Cronometer column spellings are protocol detail, not
  preference. The file is thin on purpose — it is the declared home for the
  next such setting, so it doesn't land back in a module constant.
- **Sleep and HRV never reach an energy equation.** `fetch_readiness` returns
  a sleep score and a word; HRV isn't returned at all, being the metric most
  likely to be mistaken for a recovery-cost number. A sleep score is a
  unitless 0–100 index, so no conversion to kcal could be legitimate. The
  separation is enforced by these being different methods writing different
  keys, not by a comment.
- **Absent metrics are omitted, never zeroed.** `save_biometric_entry` merges
  on `date`, so a scale that reported only weight must not send
  `body_fat_pct: 0.0` and overwrite a real reading. `_prune` drops the Nones
  and `has_measurements` decides whether a row is worth storing at all —
  count the *measured* keys, not `len(entry)`, which an earlier version did
  and which the `source` tag alone was enough to fool: a day the scale never
  saw was written as a weigh-in with no weight, and `get_latest_biometrics`
  handed that empty row back as the newest reading.
- **Cronometer is reverse-engineered, and that is the only option.** There is
  no public Cronometer API for individual accounts; `cronometer-mcp` drives
  the same GWT-RPC protocol the web app uses, and re-discovers the protocol's
  build hashes per login rather than pinning them, which is what lets it
  survive a Cronometer web release. It needs a paid tier that supports web
  login.

  It also needs **Python >= 3.11**, which is now simply satisfied — it imports
  in-process and there is nothing to configure. Before the 3.14 move it sat
  behind a `python_version >= "3.11"` marker in requirements.txt and ran in a
  separate `venv-cronometer/` interpreter driven over a pipe. That subprocess
  bridge (`_rows_via_subprocess`, `MEALS_CRONOMETER_PYTHON`, the
  `venv-cronometer/` sidecar) has been deleted from `CronometerSyncService`
  now that every interpreter this project runs on satisfies 3.11+ — there is
  no longer a version gap for it to bridge.

- **garminconnect's two lines disagree about token persistence**, and pip's
  choice between them follows the interpreter: 3.9 caps at **0.2.8**, which
  exposes the underlying garth client as `.garth` and leaves the token dump
  to the caller; 3.10+ resolves **0.3.x**, which removed the attribute and
  made `login(tokenstore)` persist them itself. `GarminSyncService.client()`
  guards the dump with `hasattr(client, "garth")` so both work. Calling it
  unconditionally is an `AttributeError` on the first login under 0.3.x —
  and only the first, since a cached token skips that branch, which is
  exactly the kind of bug that surfaces a month later on a new machine.

Credentials come from `.env` (`GARMIN_EMAIL`/`GARMIN_PASSWORD`,
`CRONOMETER_USERNAME`/`CRONOMETER_PASSWORD`). Garmin auth resumes from the
cached tokens in `~/.garminconnect` and only falls back to the password when
that fails — not merely a speed optimisation, since Garmin rate-limits and
MFA-challenges repeated password logins, so a timer-driven sync that logged in
fresh every run would start failing after days of working fine.

Tests are `tests/test_sync_service.py`, `unittest` like the rest. Nothing there
touches the network: both clients are reached through one seam each, and the
fakes speak the real payload dialect (grams for Garmin mass, `Energy (kcal)`
headers for the CSV) because the unit and key mapping *is* the module.

## Metric unit rules

- All ingredient quantities are in **grams** (`quantity_g`). No cups, oz, lbs,
  or imperial units anywhere in ingredients or recipes.
- All energy is in **kcal**, all macros in **grams**.

## Dietary constraints

- `dietary_rules.allowed_nova_groups` in `config/profile.json` restricts ingredients
  to NOVA groups 1–3 (unprocessed/minimally processed, processed culinary
  ingredients, processed foods). Group 4 (ultra-processed) is always rejected.
- `dietary_rules.banned_ingredients` is a substring-matched blocklist enforced
  by a Pydantic `field_validator` on `Ingredient.name`.
- There is no separate keto flag — a low-carb day is just a low `net_carbs_g`
  target in `weekly_schedule`. `calculate_daily_targets()` derives `fat_g`
  from whatever's left after protein and carbs, so a low carb target already
  pushes fat up without any special-casing.

## Notes for future sessions

- Garmin and Cronometer sync now exists — see "Biometric sync" above. The
  long-standing "no Garmin integration in this phase" note is retired.
- If `planner.py` fails with a Pydantic validation error after 3 retries,
  it's `instructor` surfacing the model's inability to satisfy the schema —
  check the exception message for which field failed before assuming a code
  bug. If the message is about kcal totals it's
  `DayRecipes.reject_untrimmable_macro_miss` and the model is off by more than
  the portion trim can absorb; swap models rather than widening
  `planning_rules.portion_trim_limits` (widening it would let through portions
  absurd enough to be unusable). Note this now fails only that day, not the run.
- `meal_history.json` entries written before the weekly rewrite have no
  `styles` key. `history_styles()` tolerates that (those days simply don't
  seed style rotation), so old history files don't need migrating.
