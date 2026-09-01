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

Seven directories — `src/` (the Python modules), `scripts/` (the shell entry
points), `tests/`, and four for files:

    config/     hand-edited. Change these to change what the app does.
    reference/  shipped corpora (whfoods.json). Read by `select_nudge_foods`;
                see "Nudging generation toward whole foods".
    data/       written by the app. Never hand-edit.
    logs/       written by the app. Disposable.

Flat inside each, with two deliberate exceptions: `src/integrations/` (see
"Biometric sync", which explains the `sys.path` insert that buys the
subdirectory back) and `tests/fixtures/`.

`.claude/` is the eighth, and holds what this file deliberately does not:
`skills/` (a skill is loaded only when its subject is being worked on — see
"NiceGUI front end" for the front end's, which is the largest), `rules/`
(legacy, and read only when named — see "Shopping lists") and
`settings.local.json`.

Root holds README.md, CLAUDE.md, CHANGE-QUEUE.md, ISSUES.md, two deprecated
planning documents, .env, .gitignore and requirements.txt. It also
accumulates four **gitignored** AI-assistant bundles — `python_codebase.md`,
`project_context.md`, `data_schemas.md` (written by `./scripts/prepare.sh`)
and `test_suite.md` (written by `./scripts/upload.sh`). They are generated,
never edited: a change belongs in the source they concatenate. `Keep/` is a
transient eighth directory when it exists at all — a Google Takeout export
dropped in for the once-off catalog bootstrap, gitignored and deleted once
the import is done (see "Bootstrapping the catalog from Google Keep").

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

### Which document to read, of the four

**`CHANGE-QUEUE.md` is the only current one**, and the only one that answers
"what should I work on next". It ranks every unfinished item and known defect
in a single list — each with its type, size, what blocks it, and where it was
first recorded — plus a "Verified closed" table so a shipped item is not
re-filed as a new idea. It is verified against the *code*, not against the
other documents' account of themselves, which is how three of their claims
turned out to have been stale for phases.

Cite its entries **by name, never by number**. The numbers renumber on every
release that closes something — four times already — so "CHANGE-QUEUE.md
item 3" in a comment is a reference with a shelf life. The queue's own
internal cross-references have had to be repaired after every one of those
renumbers, and both v0.31.0 and v0.32.0 closed two items at once, which
moved everything below them by two. The queue now cites itself by name for
the same reason, having been caught with three references stale by one — and
its anchor links still carry a number, so they are the one thing a renumber
has to be checked against.

The other three are history, and all three are kept rather than deleted:

- **`future-ideas-deprecated.md`** and **`ui-redesign-deprecated.md`** were
  the two planning documents, split by whether the work was *blocked* —
  scoped work waiting on a product decision or on weeks of runtime data,
  versus scoped work waiting on nothing. Keeping them apart stopped the
  second being read as a wishlist, but the split answered "may I start
  this?" and never "what should I start?", because neither file ranked
  against the other and neither held the defects recorded in this file at
  the moment they were found. CHANGE-QUEUE.md replaced them on exactly that
  point, and points back at them for an item's full reasoning rather than
  restating it — which is what the `-deprecated` suffix means here:
  superseded as a to-do list, still the place the reasoning lives.

  **Every "phase 6b of `ui-redesign.md`" citation below names that document
  by its original title**, deliberately. The phases are real, shipped and
  worth citing; writing "phase 6b of `ui-redesign-deprecated.md`" would read
  as the phase having been deprecated rather than the file it was planned in.
- **`ISSUES.md`** is the maintainer's own defect register, written before
  phases 6a–6e and stale by design: nearly everything in it is now fixed, and
  CHANGE-QUEUE.md's closed table records which phase or release closed each.
  Read it for the original wording of a complaint, never for what is still
  open.

### config/ is seven files — five merged into one dict, two loaded apart

`config.json` was one 196-line file holding twenty unrelated top-level keys.
It is now seven files, and the split is **two-tier**: five *core* files are
merged by `LocalJSONRepository.load_config()` into the same flat dict
`AppConfig` has always validated — so **nothing downstream of the repository
knows the config arrived in pieces**, and `planner`, `week` and `ui_app` still
read `config["weekly_schedule"]` exactly as before. Splitting the *files*
without splitting the *object* is the whole trick; namespacing the dict would
have touched hundreds of call sites for no gain.

The five core files, listed in `CONFIG_FILES`:

| file | holds |
|---|---|
| `profile.json` | the body and the numbers aimed at it — `user_profile`, `target_modes`, `weekly_schedule`, `meal_weights`, `dietary_rules` |
| `meals.json` | what a meal may be — `meal_types`, `meal_styles`, `cuisines`, `cuisine_affinities`, `cuisine_meal_types`, `diet_styles`, `week_defaults` |
| `week.json` | the shape of a week — `week_start_day`, `shopping`, `serving_rules`, `enable_sunday_prep`, `max_prep_active_mins`, `inventory_to_clear`, `inventory_rules` (including `storage_windows`, see "Storage windows belong to the dish") |
| `schedule.json` | where you are and what you're doing — `training_schedule` and `sourcing`, plus `base_schedule`, `location_rules` and `regional`, of which only `regional` is read (see below) |
| `engine.json` | tuning for the planner, not the food — `planning_rules`, `ui_settings` |

The two supplemental files, loaded by their own methods and **not** part of
the merge:

| file | holds | loader |
|---|---|---|
| `models.json` | model selection and LLM call params (see "Picking a model") | `load_models_config()` |
| `integrations.json` | sync tuning (see "Biometric sync") | `load_integrations_config()` |

**The tiers differ in what a missing file means, which is why they are
separate.** A missing core file is fatal: every one of them carries keys with
no safe default, and planning a week against a silently-defaulted
`weekly_schedule` is the exact failure the loud `load_config` contract exists
to prevent. A missing supplemental file resolves to `{}`, because every value
in one has an in-code fallback and a checkout that never syncs anything must
not need `integrations.json` to start.

`CONFIG_FILES` in `repository.py` is the manifest of which core file owns
which key, and the merge validates against it: a key in the wrong file, a
typo'd key, or a missing file each fail at load with the **filename** in the
message. That is strictly better than `AppConfig`'s `extra="forbid"`, which
knows a key is unwanted but not where it should have gone.

Adding a field to `AppConfig` therefore means adding it to `CONFIG_FILES` too.
The merge says so if you forget. That coupling is deliberate: a new key has to
belong to *some* file, and deciding which one at the moment it is added is the
entire point. **It applies to the five core files only** — `models.json` and
`integrations.json` keys are read directly by the code that needs them and
appear in neither `AppConfig` nor `CONFIG_FILES`.

`base_schedule` and `location_rules` were in that same "declared so the file
loads, read by nothing" state until `week.apply_location_modes` gave them a
consumer — see "Some slots are decided before the model is called". They stay
typed loosely (`Dict[str, Dict[str, Any]]`) because the *shape* is still open:
a location rule is a bag of `<meal_type>_mode` keys plus `restrictions`, and
pinning that down would make adding the next kind of rule a schema change.
`allows_long_cook` is that next kind of rule, added without a schema change
exactly as the loose typing intends — it says whether you are home with the
hours for something to sit in the oven, and is read by `day_allows_long_cook`
(see "Which days those are: presence, not the calendar"). A location that
omits it falls back to the weekend, so the key is optional in fact as well as
in the schema.
`regional` reached the same milestone earlier, when `sourcing` arrived beside
it (see "Buying what the shops actually stock").

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

The other scripts, none of which the app itself calls:

| script | does |
|---|---|
| `server.sh` | the NiceGUI server — venv, nohup, PID file, log (above) |
| `sync.sh` | the biometric sync — `run` it, or `install` it as a daily launchd job (see "Biometric sync") |
| `prepare.sh` | regenerates `python_codebase.md`, `project_context.md`, `data_schemas.md` (all gitignored) |
| `upload.sh` | the same bundles plus `test_suite.md`, for pasting into an assistant |
| `release.sh` | `<patch\|minor\|major>` version bump, tag and release notes |
| `claude-queue.sh` | runs the ordered prompts in `.prompts/` |
| `model-list.py` | dumps OpenRouter's top 50 models and prices to CSV |

**`prepare.sh` walks `src/` recursively.** It used to carry `-maxdepth 1`,
which silently excluded all of `src/integrations/` — so the bundle described
the biometric sync in CLAUDE.md and shipped none of its code. If you add
another subdirectory under `src/`, check it lands in the bundle.

### Web UI

NiceGUI (`ui_app.py`) is the only web UI. The Streamlit app (`app.py`) was
deleted once the migration landed; if you need its rendering or grid-editing
code as a reference, it is in git history at `git show e237872:app.py`.

A week can be generated from either front end — `python src/planner.py` or the
NiceGUI rail's "Generate" button — and both go through the same
`generate_week_plan`, write the same `week_plan.json` and append the same
history. The CLI is still the one that prints shopping lists.

### NiceGUI front end

**The app is called Larder.** `ui_theme.APP_NAME`/`APP_MARK_ICON`/
`APP_FAVICON`/`APP_TITLE` are the only places that name it — the browser tab,
the window title and the header wordmark all read from them, so a literal at a
call site is a fourth thing to miss when it changes. The name is the store
cupboard, and it names the half of this app that is actually unusual: the
pantry ledger, `inventory_to_clear`, the fridge window bounding a batch, the
shopping windows grouped by cook day. `ui.run(title="AI Weekly Meal Planner")`
was the only thing that named it before, and that string is a description
rather than a name.

`ui_app.py` (`./scripts/server.sh start`, serves on :8080) is the high-density
desktop UI: a header of 7 per-day macro bars, a persistent staged-changes bar
beneath it, and a slim vertical rail choosing one of six destinations — Plan
(the week grid), Today, Shopping, Library, Insights, Settings. Shopping is
the newest and is the *same* panel the right-hand drawer draws (see
"Shopping lists"): the drawer is for reading a trip against the grid, the
destination for working through one. `ui_app.py` is a ~300-line
page shell; every other `ui_*.py` is one concern exposing a `build_*(ctx)`
factory, and `ui_state.py` holds `PlannerState`, the view model and the only
UI module with tests. No package structure — every module is a flat sibling,
per the `sys.path[0]` note under Layout.

**The full front-end record lives in the `ui-work` skill**
(`.claude/skills/ui-work/`), not here. `SKILL.md` is the contract — the
type/spacing/radius scale, what each colour is allowed to mean, the NiceGUI
and Quasar traps that have each cost a debugging session, the refresh topics,
and which module a change belongs in. `architecture.md` beside it is the
design record for every surface: the week grid and its scroll alignment, the
cards and recipe dialog, the rail's action block, Today, the day inspector,
the review dialog and staged-changes bar, Settings' panels and read views, the
shopping drawer, and PDF export.

**Load that skill before editing any `ui_*.py`.** It is ~1,100 lines of
reasoning about details that look removable and are not, and it is kept out of
this file so a session that never touches the UI does not carry it.

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

- `config/` — external configuration; five core files merged and validated
  once at load through `AppConfig` (`extra="forbid"`, so an unknown or typo'd
  key fails at startup), plus two supplemental files loaded apart from the
  merge. See "config/ is seven files" under Layout.
  Model selection lives in `config/models.json`: `meal_generation_model` is
  the standing choice, and `config["openrouter_model"]` is a per-run
  selection injected **in memory only** by the CLI's `--model` and the
  Settings destination's model select. There is **no in-code model default** — both unset
  raises (`resolve_planner_model`), deliberately, so the app can never
  silently plan against a stale hardcoded model.
- `repository.py` — the storage boundary (see below).
- `generation_jobs.py` — the runs in flight: the single-flight claim shared by
  the API route and the NiceGUI page, and the job records a client polls. No
  FastAPI and no NiceGUI in it, which is what lets both import it (see "The
  API boundary").
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
    - **A morning gym (hypertrophy) session's breakfast is pinned to a
      shake** before any rotation runs, as a hard rule: `morning_training_days()`
      (`WORKOUT_BREAKFAST_TYPES = ("gym",)`, starting at or before
      `MORNING_TRAINING_CUTOFF`) picks the days, `week.pin_style()` applies
      `WORKOUT_BREAKFAST_STYLE`. Cardio and walks are deliberately excluded —
      a shake is specifically what a hypertrophy session's fast-digesting,
      protein-forward refuel needs, and forcing one on every cardio morning
      too would empty the breakfast rotation for a session that doesn't need
      it. A shake is also the only breakfast in `meal_styles` drinkable ten
      minutes before a session, so left to plain rotation a 06:30 gym slot
      gets eggs and smoked salmon on toast about one week in five. An
      *evening* session is deliberately not covered: it is already handled as
      macros by `apply_training_adjustments` (expanded budget, pinned
      post-workout meal, pre-workout digestion note), and pinning a *style*
      is only warranted when the session lands before the meal can settle.
      Both are pins, not overrides — a style or cuisine the user chose in the
      review dialog always survives, the same precedence a hand-written
      `meal_overrides` entry gets over a computed one.

      **The pin only fires on a slot still on auto**, which is what makes
      `ui_generation.generate_week` call `week.clear_styles`/`clear_cuisines`
      unconditionally, on every full-week generation, before
      `resolve_auto_choices` runs. Without that, a slot already carrying a
      concrete style from a previous run — the normal state once a week has
      been generated once — blocks the pin from ever re-firing, even after a
      `training_schedule` edit newly qualifies that day: a schedule change
      would otherwise silently fail to reach the plan until the Plan
      destination's "Shuffle styles" button (`PlannerState.shuffle_styles`,
      same two `week.clear_*` calls) was clicked by hand. Mode, leftover
      links and skips survive the clear — those are structural edits the
      user made on purpose, not picks due for a re-roll.

    The prompt side of blocking lives in `generate_meal_type_week`, which is
    the only call that can see the whole week: `build_cuisine_continuity_rule`
    tells the model which days share a cuisine *on purpose* and to make those
    nights differ by protein/vegetable/method — and by how the shared
    aromatics are expressed (rub vs. marinade vs. finishing sauce), so a block
    doesn't read as the same spice paste four nights running — instead, and it
    swaps `WEEK_STYLE_RULE` for `WEEK_CUISINE_BLOCK_STYLE_RULE` — the standing
    rule says consecutive days must differ in tradition, which is the exact
    opposite of a 4/3 split and invites the model to "fix" the repetition by
    substituting a cuisine. It emits nothing at all when no cuisine spans more
    than one day, so a hand-picked week of seven cuisines still reads as
    before. `DINNER_VARIETY_RULE` gained "never the same primary protein on
    two consecutive nights" for the same reason: once four nights are Greek,
    the week-wide "no protein more than twice" cap is satisfied by lamb, lamb,
    chicken, chicken, which reads as the same meal twice.

    Everyday Western baselines (`homestyle`, `modern_australian`,
    `pub_classic`) exist in `cuisines` for the same food-waste reason as every
    other affinity pairing: `cuisine_affinities` routes heavily-spiced
    cuisines (`mexican`, `bbq`, `thai`, `indian`) toward one of them for the
    *next* block rather than toward another adventurous cuisine, so a spice-
    paste-heavy block is reliably followed by a plain roast-and-veg block
    instead of two intense blocks back to back. They carry no `principles`
    entry the way `diet_styles` do — a bare name in `cuisines` is how every
    other cuisine already works, and `humanize()` (underscore -> space, no
    special-casing) is enough for the model to know what "modern australian"
    means without a description to keep in sync.

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

### Diet styles: a standing philosophy, orthogonal to cuisine

`config/meals.json`'s `diet_styles` is a catalog of named eating patterns —
twelve today, spanning cognitive/longevity frameworks (Mediterranean, MIND,
Nordic, Blue Zones), metabolic/anti-inflammatory ones (Fast 800, Total
Wellbeing, Anti-Inflammatory, Paleo, Pegan) and gut-health/elimination ones
(DASH, Low-FODMAP, AIP) — each a `label` and a `principles` string.
`dietary_rules.active_diet_styles` (in
`config/profile.json`, empty by default) says which of them are in effect.
The two are split the same way cuisines split into `cuisines` and a
per-day/block pick, and for the same reason: the catalog is vocabulary,
`dietary_rules` is the standing choice made against it — but unlike cuisine,
diet style is deliberately **not** rotated per day or per block. These are
patterns people follow for a stretch, not a nightly pick, and a meal is meant
to satisfy its cuisine *and* every active diet style at once (a Korean dinner
can still be Mediterranean-principled), which a single per-slot pick like
`cuisines` can't express.

`AppConfig.diet_styles_are_known` cross-checks `active_diet_styles` against
the catalog at load time — same "fail loudly, name the typo" policy as every
other section, and the reason the check lives on `AppConfig` rather than on
`DietaryRules` itself: only the parent model can see both fields at once.

`build_diet_style_rule()` turns the active entries' `principles` into one
`Rules:` line, sent by both generation axes via `build_generation_rules` —
same mechanism as `PANTRY_CONSOLIDATION_RULE`, and empty when nothing is
active so the prompt is byte-identical to before this feature existed. It is
placed right after the cuisine rule (`style_rule`) and before the variety
rule, because cuisine and diet style are both "what approach" — variety is a
different concern.

**One numeric lever, and it is a ceiling rather than an adjustment.**
`DietStyle.calorie_ceiling` is optional and None on eleven of the twelve;
`fast_800` declares 800. `planner.diet_style_calorie_ceiling(config)` reads
the lowest one any *active* style declares, and `hydrate_dynamic_targets`
takes the day's computed calories `min()` against it.

This section previously said there was **no** numeric lever, and the
objection it recorded was right: `weekly_schedule`/`hydrate_dynamic_targets`
already own every day's calorie number, computed from the body and the
config's `deficit`/`target_weight_kg` gap, so a second diet-style-driven
calorie *adjustment* would be exactly the double-count that function already
guards against for training uplift. It also named where the answer would
have to live — "inside `hydrate_dynamic_targets`, not as a second config knob
sitting beside it" — and that is where it is.

**A ceiling is admissible where an adjustment was not, and the difference is
idempotence.** Hydration runs twice on the same config: the UI hydrates for
its own live preview and generation hydrates it again. Anything that *shifts*
a number shifts it twice — which is the exact failure an earlier
uplift-unwinding pass produced, taking a 2200 kcal override down to 1850.
`min()` applied to an already-capped day returns the same figure, the same
property that makes a stated target safe to take verbatim.

Four things about it are decisions:

- **After the training uplift, not before.** A ceiling bounds what the day may
  *total*, not the base it was built from, so a workout does not buy an
  exemption from a bound its owner chose to eat inside.
- **A stated target is never capped.** `target_is_stated` — either
  `target_modes` or a review-dialog `target_locks` entry — means somebody
  said this number on purpose, and it is the day's final figure by
  definition. A ceiling overriding one would be the second source of truth
  this whole section refuses, and would make flipping calories to manual
  silently move the day, the exact bug `target_modes` exists to prevent.
- **Lowest wins when two active styles both declare one.** Two bounds are two
  bounds and only the tighter is being kept; averaging would produce a figure
  neither style asked for — the same reason `reconcile_adaptive_tdee` picks
  one TDEE rather than blending two.
- **An unaffordable ceiling is reported, not corrected.** Protein is locked to
  the target weight (144 g = 576 kcal) and carbs come straight off
  `weekly_schedule`, so an 800 kcal ceiling cannot pay for both on the shipped
  numbers. `derive_fat_g` floors at 0 and the day stops reconciling against
  `calories ~= 4p + 4c + 9f`; hydration logs a warning naming the days and
  emits a note. That is this codebase's standing answer whenever the numbers
  do not reconcile — an overspent `meal_overrides` floors the rest at 0 and
  warns, `cap_to_weighted_share` drops its surplus rather than moving it,
  `apply_protein_floor` does nothing and logs. Raise the ceiling or lower that
  day's `net_carbs_g`; do not have the code pick a number nobody chose.

**The prompt still never states the number.** `build_diet_style_rule` sends
what it always sent — simple, lean, low-added-fat dishes, inside whatever
budget the day was given. Telling the model a figure the budget it was handed
already reflects is how a model starts optimising for the number instead of
the food, which is what `FIBER_TARGET_RULE`'s last clause exists to head
off.

`active_diet_styles` is empty in the shipped config, so
`diet_style_calorie_ceiling` returns None and every day plans byte-identically
to before this existed.

**All twelve are soft guidance, including the two — Paleo and AIP — that read
like hard elimination lists.** "Exclude all grains, legumes and dairy" is
`principles` text sent to the model, not a Pydantic validator: nothing stops
a slip-through the way `Ingredient.reject_banned_ingredients` stops a banned
ingredient outright. Real hard exclusions for one of these belong in
`dietary_rules.banned_ingredients`, same as any other must-never-appear
ingredient; `diet_styles` is for shaping what the model reaches for, not
policing what it must not.

### Targets come from the body, not the file — unless you say otherwise

`weekly_schedule`'s per-day calories and protein are, by default, not what
the week is planned against. `hydrate_dynamic_targets()` replaces them with
`nutrition_engine.calculate_macro_targets()`'s output — BMR from the latest
weigh-in, TDEE from the activity factor, and a deficit that slides with the
remaining gap to `target_weight_kg`. It is a **pure function**; `hydrate_config()`
is the thin `async` wrapper that fetches the biometrics for it.

#### Who owns a number: `target_modes` and `target_locks`

That replacement used to be **unconditional**, and it made two things true at
once that nobody had decided:

- `weekly_schedule`'s stated calories and protein were dead weight the moment
  a weigh-in existed. The shipped config said 1000 kcal on a Thursday every
  run planned at 1722, and the telemetry header — reading the file — printed
  the 1000.
- **Every override typed into the review dialog was a silent no-op.**
  `planning_config()` folds `target_overrides` into `weekly_schedule`, and
  hydration then overwrote calories, protein and fat for every day without
  being able to tell an edited value from a stale file one. The UI accepted
  2200 kcal, moved the bar, counted it in the staged-changes bar, and
  generation planned the computed figure regardless. Only `net_carbs_g`
  survived, because it is the one macro hydration reads back out of
  `day_targets` rather than replacing.

`planner.target_is_stated(config, day, macro)` is the rule that fixes both,
and it is deliberately **one function read by two callers** — a second copy
would let `apply_training_adjustments` and `hydrate_dynamic_targets` disagree
about whose number a day is aiming at, which surfaces as a target that moves
when you toggle who owns it. Two independent ways to say "somebody stated
this on purpose", different in lifetime:

| | `target_modes` | `target_locks` |
|---|---|---|
| scope | the whole week, one macro | one day, one macro |
| lives in | `config/profile.json` (`TargetModes`, on `AppConfig`) | injected at runtime by `planning_config()` |
| set by | the Settings destination's toggle | typing in the review dialog's target curve |
| persists | yes — the one thing besides generation that writes to `config/` | never reaches disk |

**Only two macros have a mode, because only two have two possible sources.**
`TARGET_MODE_MACROS` is `("calories", "protein_g")`. `net_carbs_g` has no
computed form at all — the engine takes `weekly_schedule`'s figure and hands
it straight back, which is what makes carbs the week's cycling lever — and
`fat_g` is always `derive_fat_g`. Both are honest answers to "where does this
number come from"; neither is a mode anyone can switch, and the Settings page
says so in words rather than offering a toggle that does nothing.

Three consequences worth knowing:

- **A stated target is the day's *final* number, so a workout does not grow
  it.** `apply_training_adjustments` skips a macro `target_is_stated` returns
  true for, and — importantly — does not record the uplift it declined to
  apply, because that record is replayed onto the engine's base by hydration
  and drawn as the amber segment of the review dialog's bar. This is what
  makes flipping a macro to manual leave every figure exactly where it was;
  before it, switching protein to manual silently moved a training Monday
  from 144 g to 187.8 g. A toggle that changes *who decides* a number must
  not change the number.
- **The skip happens at the source, not by unwinding it later.** An earlier
  version let the uplift be added and had hydration subtract it back off,
  which was correct exactly once: the UI hydrates for its own live preview
  (below) and generation hydrates that same config again, and the second pass
  subtracted an uplift the number no longer carried, taking a 2200 kcal
  override down to 1850. Hydration is now idempotent for a stated macro
  because it takes it verbatim.
- **Every switchable macro manual means the engine is never called.**
  `needs_engine` is false, no BMR is computed, `dynamic_basis` is absent, and
  a checkout with no weigh-in plans off the file without logging a warning
  about targets nobody is using.

Switching a macro to `manual` **seeds `weekly_schedule` from what the engine
currently computes** (`PlannerState.set_target_mode`) rather than exposing
whatever stale figure the file still holds — those two had drifted a long
way, and handing the stale one back as "your manual target" would look like
the toggle had re-planned the week rather than merely changed who owns it.

Defaults are `auto` for both, so a config predating `TargetModes` plans
byte-identically to before it existed.

#### The header previews what the run will actually aim at

`PlannerState.planning_config()` now ends with `hydrate_dynamic_targets`, so
`planned_targets`, the review dialog's target curve and the telemetry header
all read the engine's numbers rather than the file's. It can do this from a
synchronous method because hydration is *pure*: `.load()` already fetched the
latest weigh-in for `weight_kg` and now keeps it, alongside the full series
`calculate_adaptive_tdee` needs, on `PlannerState.latest_biometrics`/
`.biometrics`. `log=False` there, because this runs on every repaint and a
per-keystroke `dynamic targets: ...` line would bury the per-call generation
timing `logs/meals.log` exists for.

**`targets_for` branches on what *this session staged*, not on what the
config happens to say.** `target_is_staged(day)` is a target override or an
edit to that day's training — deliberate acts whose whole point is seeing
where the week is about to move. It used to branch on `has_training(day)`,
which is the config's standing state, and with a training schedule covering
most of the week that put six days on the live preview and one on the stored
plan: **one row of figures computed two different ways**, so a fresh weigh-in
read as a plan that had drifted off target on Monday and held on Thursday.
Everything unstaged is measured against `week_plan.targets` — what the week
was actually generated for — and re-generating is what reconciles the two
after the body moves. The telemetry marker (amber `•` / emerald `⚡`) keys off
the same predicate, so a dot can never appear on a day reading the stored
plan.

`has_training` itself was wrong in a second way: it counted a
`{"type": "rest", "estimated_burn_kcal": 0}` entry as training, which drew an
emerald bolt on an explicitly scheduled rest day. It now mirrors
`apply_training_adjustments`' own filter, the same distinction
`TrainingView.is_rest` already draws for the Today tab.

**An override is diffed against the day's *resolved* baseline**
(`PlannerState.baseline_targets`, which is `planning_config()` with that one
day's overrides suppressed), not against `weekly_schedule`. On an `auto`
macro the file's number is inert, so diffing against it marked every day
permanently overridden and reported "Thu +800 kcal" for an edit that had
moved the day 78 kcal. `set_target`'s clear-on-match, the staged bar's signed
delta and the target curve's dashed ghost line all measure from it.

#### TDEE is measured once there is enough data to measure it

The activity-factor TDEE above is a population regression, and those sit ~300
kcal from an individual. `calculate_adaptive_tdee` measures instead:

    adaptive TDEE = mean logged calories + (kg lost per day x 7700)

Eat 2000 and lose 0.5 kg a fortnight and you expended about 2275, whatever a
formula predicted. This is what closes the loop the Cronometer sync exists to
feed — before it was wired in, `daily_actuals` was written to disk, read once
by `logged_intake_for` for a single regenerated meal, and never reached a
target at all.

Three things about how it is wired matter:

- **It needs the series, not the latest row.** `hydrate_config` therefore
  reads `load_biometrics()` *as well as* `get_latest_biometrics()`. Two reads
  of one small file, on purpose: "latest" is a question about dates rather
  than list order, and reimplementing that rule to save a read would be a
  second place for it to be wrong.
- **The estimate is bounded, never blended.** `reconcile_adaptive_tdee` keeps
  the formula whenever the measured figure sits more than
  `ADAPTIVE_TDEE_TOLERANCE` (25%) away from it. Systematic under-logging is
  the common failure and it always reads *low*, so an unbounded measurement
  would quietly cut the target of whoever logs least carefully. It chooses one
  number rather than averaging the two, because an average of a good estimate
  and a bad one is a slightly bad estimate with no way to tell which it was.
- **`basis["tdee_source"]` says which won**, with `"formula"` (nothing to
  measure — the normal state until a few weeks of sync accumulate) kept
  distinct from `"formula_adaptive_rejected"` (measured and disbelieved). Only
  the second is worth investigating, and it logs a warning naming both figures.

`calculate_adaptive_tdee` returns `None` — meaning "keep using the formula" —
for fewer than two weigh-ins, a span under `MIN_TREND_SPAN_DAYS`, or no logs.
So a fresh checkout plans exactly as it did before this existed, and protein
stays locked to the target weight whichever TDEE wins: a measurement buys back
energy, not protein.

#### Which of those three `None`s it is, and why that needed saying

`measure_adaptive_tdee` holds the arithmetic and returns an
`AdaptiveTDEEStatus` — the estimate plus the weigh-in count, the span, the
logged-day count and the floor, all measured *inside* the window the estimate
would have used. `calculate_adaptive_tdee` is now a one-line wrapper over it
returning `.estimate`, so every existing caller keeps the bare
`Optional[float]` contract it was written against.

**The rejection path was always right; the reporting was the bug.** All three
unmet preconditions are legitimate cold-start states, and all three read
through to `basis["tdee_source"]` as `"formula"` — the same string an empty
`biometrics.json` produces. Measured against the live file on 2026-08-28:
five weigh-ins, five `daily_actuals` rows, both sources checkpointed to the
day before, and an estimate that had never once fired, because the weigh-ins
sat inside a span of four days against a floor of seven. "Enough data to look
like it should work, and it doesn't" was spelled identically to "nothing to
measure yet".

**The span is the precondition worth naming loudest**, and it is why the
status reports days rather than counts: it collapses while every visible
count looks healthy, more weigh-ins bunched into the same few days do not
clear it, and a fully caught-up Cronometer cannot fix it. Chasing missing
logged days would not have found this.

`ui_state.adaptive_tdee_view(biometrics, basis)` is the one view model both
surfaces read — a headline and one line of evidence, over six states: the
engine's three unmet preconditions, `rejected` and `adaptive` from
`basis["tdee_source"]`, and `measured` for a figure with no basis beside it
(every switchable macro manual, or no body profile, so no engine call was
made and nothing reconciled anything). Reporting that last case as
`adaptive` would claim arithmetic that never ran. Settings' Daily Targets
panel prints it under the calories row, where the basis note already names
the winner and now says why the alternative lost; Insights prints the same
verdict instead of stating the rule and leaving it unevaluated, which is what
had a reader holding five of each concluding the estimate was on. Colour
carries none of it — the trend glyph does, per the `ui-work` skill, since
amber already means five things here and emerald is the cook status.

It is called at the top of all three generation entry points
(`generate_week_plan`, `regenerate_single_day`, `regenerate_single_meal`)
rather than once in the CLI, because NiceGUI builds its config in the
*synchronous* `PlannerState.planning_config()`, which cannot await storage.
Hydrating where the repository is already in hand gets both front ends onto
the same numbers.

This used to leave the header previewing the **file's** targets, so before a
run it disagreed with what the run would actually aim at. That gap is closed:
`planning_config()` calls `hydrate_dynamic_targets` itself, against the
weigh-in `PlannerState.load()` now keeps — see "The header previews what the
run will actually aim at" above. Generation still hydrates again at these
three entry points, which is what the CLI needs and what keeps a run honest
if the scale reported between page load and Generate; hydration is idempotent,
so the second pass changes nothing the first already settled.

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

#### Derived training burn

`apply_training_adjustments` (above) reads `estimated_burn_kcal` straight off
each `training_schedule` session — it always has, and phase 4.3 of
`ui-redesign.md` doesn't touch that read. What changed is where the number
in that field comes from before a human ever sees it: it used to be a flat,
arbitrary 300 kcal (`PlannerState.add_training_session`'s hardcoded default,
whatever the session's actual type or duration), on the reasoning that nobody
actually knows their session's real energy cost and a placeholder was
honest about that. It is now `nutrition_engine.estimate_session_burn_kcal` —
the standard MET formula (`MET * 3.5 * weight_kg / 200 * minutes`) applied
to the session's own type and duration and `PlannerState.weight_kg` (the
latest weigh-in, fetched once at `.load()` time, falling back to
`user_profile.current_weight_kg` via the same
`nutrition_engine.resolve_current_weight_kg` `calculate_macro_targets`
itself now calls — pulled out specifically so the two don't carry two copies
of that fallback rule).

**This is a *default*, not a second calorie source, and that distinction is
the entire point of the trap this had to avoid.** `apply_training_adjustments`
still reads exactly one field for a session's energy cost, still folds it
into the day's budget exactly as before, and still records what it did in
`training_uplift` for the replay above — a derived starting number and a
hand-typed one are indistinguishable to it, because they're the same field.
`ui_review.py`'s training editor keeps `estimated_burn_kcal` a normal
editable `ui.number` for this reason: a derived default the user can
overrule is the goal, and a value they can't correct would be worse than the
flat guess it replaced. It's applied via an explicit calculator-icon button
next to the field (`estimate_burn` computed, shown in a tooltip, written into
the field only on click), not a live recompute on every type/duration edit —
recomputing automatically would mean rebuilding the row that owns whichever
adjacent input the user is still mid-edit on, the identical focus-theft trap
`training_field_handler` already sidesteps by refreshing `"targets"` rather
than `"training"` on a plain field edit.

**Proposing the schedule itself from Garmin activity history was deliberately
left for its own change, and that change has now happened** — see "Proposing
the week you actually trained" below. It follows this feature's precedent
exactly: a derived default, into the same field, applied on an explicit click.

#### Proposing the week you actually trained

`training_schedule` has always been hand-declared while the watch recorded
what really happened, and the two were never introduced.
`nutrition_engine.propose_training_schedule` is the introduction: it reads
`biometrics.json`'s `activity_log`, diffs four weeks of it against the
declared week, and returns a list of `ProposedSession`s — sessions to add,
and declared ones to drop.

**The detector is the easy half; the confirmation is the feature.** A
schedule written from a guess would move a day's calorie budget and pin its
post-workout meal off a pattern nobody agreed to, which is the one thing a
derived number here is never allowed to do. Nothing in `nutrition_engine`
writes; the review dialog's Training Schedule section renders one row per
proposal with an accept and a dismiss, and `estimate_burn`'s calculator-icon
button is the interaction being copied — same field, explicit click, a
default the user can overrule.

**What is stored, and why the activity list had to exist first.**
`fetch_cardio_activities` was fetched on every sync and printed — v0.29.0's
sleep bug, reached by a second route — so `activity_log` is a fourth
biometric section now, written by `GarminSyncService.fetch_activities`. Three
things about it are decisions:

- **It is the one section holding several rows per date**, so
  `save_activity_entries` replaces a day wholesale where its three siblings
  upsert one row. A re-sync's answer for a date *is* the answer for that
  date; merging would leave a deleted or re-classified activity outliving the
  day it happened on, with no key able to name it.
- **Only mapped, timed activities are stored.** `GARMIN_SESSION_TYPES`
  translates Garmin's `typeKey` into the `training_schedule` vocabulary with
  no catch-all, and `startTimeLocal` (never `startTimeGMT`, which is silently
  wrong by the timezone offset) supplies the clock time. A row missing either
  cannot become a proposal, and an unmapped modality guessed at would be
  worse than absent: a yoga class offered as "Cardio Easy, 45 min, 260 kcal"
  is a wrong answer that looks like a right one. `MET_FALLBACK` can afford
  that guess because it is refining a session the user already declared; this
  invents the session.
- **`net_calories` is finally load-bearing.** A proposal's
  `estimated_burn_kcal` is the median of what Garmin reported, already
  discounted by `EXERCISE_RECOVERY_FACTOR` at sync time; the MET formula is
  the fallback for a session the watch reported no calories for. Medians
  throughout, so one 90-minute Sunday among four 30-minute ones doesn't drag
  the proposal to 45.

**What counts as observed is the whole difficulty.** `activity_log` only
holds days that recorded something, so silence in it is ambiguous — a rest
day and an unsynced day look identical, which is the exact gap
`sync_checkpoints` was added to close for weigh-ins. The observed span
therefore runs from the first recorded activity in the window to the later of
the last recorded one and Garmin's own checkpoint, capped at today; a stored
row past the checkpoint still counts, because a row is proof the day was
asked about (the rule `ui_state.sync_status` already applies). Under-claiming
at the start is the safe direction: it costs a proposal, where over-claiming
invents weeks of evidence that a session never happened.

**A declared session Garmin never sees is proposed for removal — and only
proposed.** That was this item's one blocking decision, and it is answered
symmetrically with additions rather than left silent or absent, behind two
guards. A weekday has to have come round at least `MIN_PROPOSAL_OCCURRENCES`
times inside the observed span, and `MIN_ACTIVE_DAYS_FOR_DROP` asks whether
the watch is being worn at all before its silence is read as evidence — a
watch in a drawer looks exactly like a fortnight of rest. A weekday that
recorded *something* is never proposed for a drop even when the modality
disagrees: a Sunday ride that has become a Sunday walk is a day you plainly
train on, and the walk arrives as its own addition instead.

**Additions are diffed against the staged schedule; drops against the
file's.** The asymmetry is deliberate. An addition accepted (or typed by
hand) has to stop being proposed on the next repaint rather than at the next
page load; a drop is an edit to `config/schedule.json` and may only name a
session that file actually holds, or a row typed moments ago is argued with
while the cursor is still in the row above it.

**Accepting persists, which makes this the second thing in the UI that
writes to `config/`** — `PlannerState.set_target_mode` was the first, on
identical reasoning. `training_schedule` is the *standing* week, not an input
to the next run, so an accept that evaporated on reload would be a no-op with
an animation and the same proposal would be re-offered forever. The change is
applied to the file's list and to the staged one separately rather than
persisting whatever the drawer currently holds — those differ whenever
something else is staged, and a click on "accept" must not quietly write out
a half-typed session two rows below it. `_original_training_schedule` moves
with the file, so the staged bar reports no phantom change for the row just
accepted while still reporting every genuine edit beside it.

**Three states produce no proposals and mean different things**, which is
`ui_state.training_proposals_view`'s whole reason for existing — the same
lesson `adaptive_tdee_view` was written for: "nothing recorded yet", "not
enough history to see a pattern" and "your declared week already matches what
was recorded" spell identically as an empty list, and the last one is the
good answer most likely to be misread as broken. Dismissals are session-local
and deliberately unpersisted: a dismissal says "not now" where an accept says
"this is my week", and a proposal waved away today comes back next week
because the evidence for it has grown, not gone.

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

#### The floor and the day have to be affordable together, and once weren't

Worth knowing before changing either number, because the interaction is not
obvious and the shipped config was wrong about it. `hydrate_dynamic_targets`
locks protein at 144 g (80 kg x 1.8) **whatever `weekly_schedule` says** — the
file's `calories` and `protein_g` are replaced outright, and only
`net_carbs_g` and hand-written `meal_overrides` survive. Four meals against a
35 g floor need 140 g of that 144, leaving 4 g of slack across the entire day:
in effect every meal was pinned near 36 g and no meal could be protein-forward.

That is what made the shipped breakfast override unsatisfiable rather than
merely small. It pinned 30 g in 350-400 kcal, `split_targets` assigns an
override **verbatim**, and `apply_protein_floor` excludes pinned slots — so
nothing downstream could lift it. Meanwhile the `custom_shake` style's own
fixed base was 45 g of protein powder, about 36 g of protein, already over
the pin before a single other ingredient. The model resolved the contradiction
the only way it could: lean on the powder and ignore the rest of the template.

The fix was config, not code, and it is two coupled changes — **change them
together or not at all**:

- `week_defaults.snack` is `skip`. Three meals instead of four is what frees
  the protein: 144 - 60 leaves 84 g for lunch and dinner, 42 g each, clear of
  the floor.
- The two `gym_hypertrophy` mornings (Monday, Saturday — the days
  `morning_training_days` pins to the shake) get `550 kcal / 60 g`. The other
  five keep their smaller pins, because `eggs_salmon` and `beans_toast` have
  no protein-powder base to build 60 g on.

The arithmetic the matrix has to satisfy: base 150 kcal/30 g, then both
Protein Boost items (+20 g), edamame (+4 g) and a seed (+2 g) reach ~55-60 g
at ~400 kcal. That is 6-8 components, which is why the style text says
**"choose 3-6 items on top of the mandatory base, at least one Protein
Boost"** rather than the "2-4 items" it used to — a template that cannot
reach its own budget is one the model abandons wholesale, which is the
failure being fixed.

##### The shake's mandatory greens, and the rule that would have eaten them

The base is not just powder/creatine/water: **20-30 g of raw leafy green,
50-80 g of raw frozen vegetable and one Fruit Fusion item are mandatory in
every shake.** The green and vegetable cost ~25-30 kcal and return ~2.5 g
protein and ~2 g fibre, which is the best nutrient-per-calorie trade anywhere
in the template; the fruit is another ~25 kcal and is what makes the result
drinkable. There is no budget in which the three don't fit, and that sentence
is in the prompt for exactly that reason.

**Making them mandatory took three coordinated edits, not one.**
`SHAKE_ROTATION_RULE` (whole-week) and `SHAKE_SLOT_DIRECTIVE` (per-slot) both
told the model to keep "the base" identical and *vary the secondary
components* so no two shakes are the same drink. Greens and vegetables sat in
that secondary pool, so the rotation rule had standing permission to drop them
— and they are the cheapest thing in a shake to drop when two drinks have to
differ. Naming them as mandatory in `meals.json` alone would have set the
style text against the rotation rule, with the rotation rule winning on
whichever morning it needed a difference. All three now name them as base.

**Fruit is the subtlest member of that base, and was added last.** It is the
one base item the rotation rule *also* names as a thing to rotate — "no two
may share the same combination of fruit, seeds, nuts and flavouring" is the
clause immediately after. Listing fruit as base is what turns that into a rule
about *which* fruit rather than *whether* one: left out of the base, dropping
the fruit entirely is the single cheapest way to make two shakes differ, and
the result is protein powder, spinach and frozen broccoli, which is barely
drinkable. So it is the same three-way edit as the greens — `meals.json` base
item (d), `SHAKE_ROTATION_RULE`'s never-drop list, `SHAKE_SLOT_DIRECTIVE` —
with one extra step: the ingredient matrix says "choose 3-6 **further** items"
and tier (7) is annotated as already-spent, so the fruit is not silently
counted twice against a budget the shake has to hit.

Which green, which vegetable and which fruit may still vary between shakes;
that is the part rotation is welcome to touch. Only their presence is fixed.

This is soft guidance, like every other style instruction — there is no
validator rejecting a shake that arrives without spinach. That is the same
call `diet_styles` makes for Paleo and AIP (see "Diet styles"), and it is
worth remembering the reason: a rejection costs a full 30s-3min retry, and
nothing but a retry can add a missing ingredient. If shakes still turn up
without greens now the budget is satisfiable, a `model_validator` on the
shake style is the next step — but the previous failure had an unsatisfiable
budget as its root cause, and that is fixed.

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
`PlanRepository` is the interface (`load_config`, `save_config_keys`,
`load_history`, `save_history`, `load_week_plan`, `save_week_plan`);
`LocalJSONRepository` is the only implementation today and keeps the same
three files in the same places.

`save_config_keys` is the **one write path into `config/`**, and the only
thing besides generation that persists anything. It exists for
`target_modes` (see "Who owns a number"): a *setting* is not a per-week
input, and a toggle that reset on every page reload would answer "where do
my numbers come from" differently each time you looked. It has a second
caller now — accepting a Garmin schedule proposal writes `training_schedule`
(see "Proposing the week you actually trained"), which is a standing week on
exactly the same reasoning. Those two are the whole list; everything else in
the review dialog is an input to the next run and stays session-only. It merges the keys
it is handed into the file `CONFIG_FILES` says owns each one, read-modify-
write per file, so a hand-added key the app has never heard of survives the
next settings change. It is deliberately not a "save the config" call — the
config held in memory is a *merged* dict carrying runtime-injected keys
(`training_uplift`, `target_locks`, `nudge_foods`, `openrouter_model`) that
must never reach disk. Point the app at a backend by constructing a different subclass —
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

### The API boundary — five reads, and one write that answers with a job

`src/api.py`'s `build_api_router(repository) -> APIRouter` is phase 5 of
`ui-redesign.md`, collecting the bet the previous section describes:
`PlanRepository` was made fully `async` for a future backend, and this is
the first thing to actually reach it from outside NiceGUI's own socket.

**It mounts onto NiceGUI's own FastAPI app, not a second server.**
`nicegui.app` *is* a `fastapi.FastAPI` instance (`App(FastAPI)`, in the
installed `nicegui` package) — the same object Uvicorn serves — so
`ui_app.py` does `fastapi_app.include_router(build_api_router(REPOSITORY))`
at module scope, before `ui.run()`, the same timing `@ui.page("/")` already
relies on. No new port, no new deployment.

**What it exposes**, all `GET`, all under `/api`:

| route | behind it |
|---|---|
| `/api/weeks/{"current"\|"next"}` | `repository.load_week_plan(id)` → `WeekPlan.model_validate` |
| `/api/recipes?favorite=&meal_type=&search=` | `repository.load_recipe_catalog()`, filtered |
| `/api/history` | `repository.load_history()` |
| `/api/biometrics` | `repository.load_biometrics()` (all four lists, `readiness_log` and `activity_log` included) + `get_latest_biometrics()` |
| `/api/targets` | `load_config_with_models` → `hydrate_config`, returning `weekly_schedule` + `dynamic_basis` (which carries `tdee_source`) |

And three that are the generation route and its answer, added later (see
below):

| route | behind it |
|---|---|
| `POST /api/weeks/{"current"\|"next"}/generate` | `generation_jobs` claim → `planner.generate_and_store_week` on a background task; `202` with a job id |
| `GET /api/jobs` | the process's recent runs, newest first |
| `GET /api/jobs/{id}` | one run — status, stages started, notes, failures |

Every route calls an existing repository method or an existing pure
`planner.py` function and returns the answer — **a route that computed
something would be a route free to disagree with the UI**, which is the one
mistake this phase existed to avoid. `/api/targets` is the one route that
composes two calls (`load_config_with_models` then `hydrate_config`) rather
than one, and both are already used elsewhere (`PlannerState.load`, the
three generation entry points) — it still computes nothing itself.

**Why `PlannerState` is not on it.** Every method that looked like a
candidate — `targets_for`, `slot_views`, `day_context`, `planning_config` —
turns out to read per-client staged edits (`target_overrides`,
`training_schedule` edits, pantry, the cached `_spec`) that have no meaning
outside one browser tab; that is a session concept, not an API one. A read
route mirrors what is saved on disk, not what one open tab has staged but
never generated. The two near-exceptions, `today_day`/`week_covers_today`
and `totals_for`, are thin wrappers a route could call directly against a
loaded `WeekPlan` with no `PlannerState` instance at all — neither is
exposed yet because nothing in the "start read-only" surface needed them,
not because they don't fit.

**What it deliberately does not expose, yet:**

- **Every write except generation.** Nothing here saves a grid edit, a mark,
  a favourite or a config key. Those are session concepts or standing
  settings with an owning surface already, and the two that write to
  `config/` are named under "Storage goes through an async repository";
  generation is the exception because it is the one thing the CLI has always
  done from outside a browser, and the one a script or a phone would want.
- **OpenAPI docs.** `nicegui`'s `App.__init__` hardcodes
  `docs_url=None, redoc_url=None, openapi_url=None` regardless of what's
  passed to `ui.run()` — so `/api/docs`/`/api/openapi.json` don't exist
  today. If TypeScript types are ever wanted for a real front end, they
  should come from that OpenAPI schema (never a hand-maintained second copy
  of `Recipe`) — re-enabling it is a small, separate task, not done here.
- **Auth.** None added — the app is still localhost-only. If it's ever
  exposed beyond that, the place for it is a dependency on the router
  (`APIRouter(dependencies=[Depends(...)])`), which *gates access*, not
  *scopes data* — nothing in this app's storage is per-user today, so auth
  here is a lock on the door, not a multi-tenancy foundation.

#### Generation over HTTP: a job id, because the event rate says so

Phase 5 stopped here and said why: generation runs 30s–3min *per meal type*
and reports progress over NiceGUI's own socket, so turning it into an
HTTP-shaped operation was "a real design question (poll a job? SSE? WS?), not
a mechanical translation". The question is now answered, and **what answered
it was counting the events rather than weighing the three protocols**.

`progress_callback` fires once per *meal type* — at most four times a run,
three on the shipped config since `week_defaults.snack` is `skip` — plus a
handful of `note_callback` strings. That is on the order of a dozen events
across a quarter of an hour, which is not a streaming problem. A client
polling every few seconds sees every one of them, from `curl`, a shell script
or a phone shortcut.

**The deciding argument is that the other two designs need the job registry
anyway.** SSE and a WebSocket both lose a run's history when the connection
drops while the run keeps going, so both have to buffer events server-side
and support resuming from an offset — which is `generation_jobs.py` with a
stream in front of it. Polling is the substrate, not a third peer; a
streaming route added later reads the same records. The WebSocket loses twice
over: bidirectionality is its feature and this problem has no use for it
(cancel is one message), and NiceGUI already runs its own socket.io channel
in this process, so a second protocol is a second connection lifecycle to
debug.

Four things about the shape are decisions:

- **The finished week is deliberately not on the job.**
  `generate_and_store_week` saves it through the repository before the job
  completes, so `GET /api/weeks/…` already answers for it. A copy on the job
  would be a second answer to one question, free to disagree with the file the
  moment anything else writes a week — the same rule the read routes follow.
  The job carries what the *run* knew and the stored plan does not: which
  stages started, the portion-adjustment notes, and why it stopped.
- **The `POST` is `202` whatever happens next, including a grid that cannot be
  generated.** `validate_week` needs the config loaded and the grid built,
  which is the run's own first step, so a rejected week is reported on the job
  rather than as a `400`. It gets its own field — `WeekNotValidError` exists to
  keep `validation_errors` separate from `error`, so a client can tell "fix
  your config" from "the provider fell over" without parsing a message. A
  per-meal-type failure is a third thing again, and rides on a **succeeded**
  job, because a failed meal must not fail the week.
- **`stages_started` is stages *started*, not banked.** `progress_callback`
  fires before each call, the same off-by-one the UI's own stage checklist
  turns on: nothing marks the last stage complete but the run returning. So
  `len(stages_started) == len(stages)` is not completion; `status` is.
- **The registry is in memory, in one process, and that is its one real
  limit.** Exactly what NiceGUI serves today — `ui.run(..., reload=False)` on
  one Uvicorn worker, with this router mounted onto that same app — so it is
  correct now and stated rather than left to be discovered. Two workers, or
  the future backend `repository.py` was shaped for, and the claim below has
  to move to where the plan lives.

##### The guard, which is the half that touches the UI

`PlannerState.generating` is per-client, and its own comment says why that
stopped being enough: two tabs generating at once "would race to overwrite the
same `week_plan.json`". It cannot see an API run and an API run cannot see it,
so an API client was a third racer.

`GenerationJobs.claim()` is the one flag both consult —
`ui_app.GENERATION_JOBS`, threaded to the router as a constructor argument and
to the page through `UIContext.jobs`. It is **required** on
`build_api_router`, not defaulted, precisely because a router handed its own
fresh registry would be a guard that silently guards nothing.

Three things about it:

- **It is a plain field, not an `asyncio.Lock`.** Claiming must *fail* rather
  than queue: a second Generate is a mistake to report, not work to line up
  behind. No lock primitive is needed for correctness either — `claim` reaches
  its assignment with no `await` in between, and a single event loop cannot
  interleave anywhere else.
- **A UI run is recorded too, and reports nothing.** The registry is told who
  holds the claim and from when, so a `409` can say "a browser tab is already
  generating" — but `ui_generation.generate_week` reports its own outcome
  through the progress dialog and swallows its exception, so the job is
  released with the default rather than being stamped with an outcome nobody
  watched.
- **Module-level state, which is the documented exception to "state lives per
  client".** `PlannerState` is per-tab because it holds one browser's staged
  edits; this guards one file that every tab and the API share.

**`generate_and_store_week` is why the route cannot drift from the CLI.**
`run_cli` was doing the sequence inline; it now calls the same function, so
the three orderings that would otherwise drift silently — training
adjustments before the grid is built, `resolve_auto_choices` before
`validate_week`, `save_week_plan` before `record_week_history` — have one
home. The CLI's two peculiarities are seams on it rather than a second copy:
`spec_transform` for `--leftover-lunches`, `on_ready` for the "Generating
N-day plan…" line that needs the resolved spec and the model name. It is
deliberately **not** the UI's path — `ui_generation.generate_week` starts from
a *staged* spec and must clear the previous run's style/cuisine/pin/batch
state off it and apply the review dialog's batch toggles first, none of which
a grid built fresh by `default_week_spec` carries.

**Two findings recorded by phase 5, both since fixed** — kept because the
reasoning is still worth having:

- **`/api/recipes`'s filter duplicated `ui_catalog_browser._matches`**
  (favorites-only, meal-type equality, case-insensitive substring on name) —
  four lines, reimplemented rather than imported, because the original was
  private and lived in a UI widget module phase 5 wasn't touching. Both now
  call `repository.catalog_matches`, which sits beside `recipe_content_key`
  for the same reason `BIOMETRIC_SECTION_SOURCES` does: it is a fact about
  the shape of a stored record with two readers that have nothing else in
  common. `ui_catalog.py` was the other candidate home and lost on one
  point — `api.py` deliberately imports nothing from `ui_*`, and this needs
  no `PlannerState`, no `UIContext` and no NiceGUI.

  **The two had already drifted, in the way this class of duplication always
  does: silently.** `_matches` treated `"All"` as the no-filter meal type
  and the route treated `None` as it, so `/api/recipes?meal_type=All`
  returned nothing while the Library grid's own default returned everything.
  Neither side was wrong on its own and no error was ever raised —
  a differently-filtered list is a perfectly well-formed response.
  `CATALOG_MEAL_TYPE_ANY` now names the UI's spelling and the shared
  function accepts both.
- **`PlannerState.targets_for`'s live preview could disagree with
  `/api/targets`** — recorded here by phase 5 and **since fixed**, so this
  entry is kept only because the reasoning is still worth having. The UI read
  static `weekly_schedule` values plus staged overrides and never called
  `hydrate_config`, while `/api/targets` always reflected the dynamic figure.
  `planning_config()` now hydrates too (see "Who owns a number"), so both
  sides read one number. They still answer subtly different questions and are
  right to: `/api/targets` reports what disk says, the header reports what
  *this tab* would generate, which includes overrides no route can see. That
  is the same `PlannerState`-is-a-session-concept line the section above
  draws, not a gap.

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

#### The cascade's end effect, and the cap on it

Generation subtracts each stage's **actual** output from the day before
splitting the remainder across the meal types still pending. That is the right
rule — a later meal should aim at what is genuinely left — but it has an end
effect: by the final stage (`snack`, last in `MEAL_TYPE_PRIORITY`) exactly one
slot is pending, so it inherits the entire accumulated difference between what
the earlier meals were briefed and what they came back as.
`apply_protein_floor` cannot moderate it either — it returns early below two
slots, because there is nowhere left to move grams from.

Calories mostly self-correct, since layer 2 lands each recipe on its budget.
**Protein does not.** A single scale factor resizes a portion without changing
its macro *ratio*, so a dinner that hits its calories 20 g of protein light
passes every check and hands that 20 g down the cascade. Three meals of that
brief a snack for ~90 g of protein in a 200 kcal slot — the same "snack with no
protein source" failure `min_meal_protein_g` exists to prevent, reached from
the opposite direction, and one nothing downstream catches:
`reject_untrimmable_macro_miss` only ever checks calories.

`cap_to_weighted_share` bounds the briefed budget at
`planning_rules.max_meal_share_multiple` (1.75) x what that meal would have
been given had nothing drifted — `split_targets` run against the day's full
target, computed once per day before the stage loop. Every macro scales by the
same factor, not calories alone, because each slot's budget must stay
internally consistent (`calories ~= 4p + 4c + 9f`) for the response validator
to check against it. Pinned meals are exempt: an override never took a share
of the remainder, so there is no drift on it to cap.

**The capped surplus is deliberately dropped, not moved.** There is nowhere to
put it — every other meal that day is already cooked — so the day lands
visibly under target instead. That is this codebase's standing answer whenever
the numbers don't reconcile: an orphaned leftover contributes 0 and shows as a
shortfall, `apply_protein_floor` does nothing and logs when the floor is
unaffordable, an overspent `meal_overrides` floors the rest at 0 and warns.
Distorting one meal into a 900 kcal "snack" to hide a target problem is worse
than showing the gap. It logs and emits a note when it fires.

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
`cook_events`, so it turns green) — it shows up in the Plan destination's
failure list (`ui_plan.week_failures`) and the shopping drawer's "nothing for
those meals is on this list" note, which keep naming a meal that now exists.
The per-card regenerate button is offered *on* NOT GENERATED cards, so that
is the common path, not an edge case.

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

Its `models` table doubles as the Settings destination's selectable list (the
UI offers its keys) and as the home for per-model quirks; an entry with
nothing unusual about it is just `{}`.

`config["openrouter_model"]` is a third thing and is **not a file key**: it is
the per-run selection injected in memory by `--model` and the Settings
destination's model select, and no front end ever writes it to disk. It used
to exist as a config.json field too, where its only effect was to give the
standing choice a second place to hide.

There is no `openrouter_base_url` key any more — it was the same URL for every
model and a knob nobody turned, so it is a constant in `planner.py`.

Swapping the generation model has real gotchas (reasoning-token blowups,
free-tier churn, latency variance vs. the client timeout). They live in the
`openrouter-model-choice` skill — invoke it before changing
`meal_generation_model` or the `models` table.

### Shopping lists

`shopping.py` aggregates cook events (not days) and normalises ingredient
names before combining them. Every normalisation rule and the bad line it
fixes are in `.claude/rules/shopping.md`. **Nothing loads that file
automatically** — despite its `paths:` frontmatter, which was measured to do
nothing — so read it explicitly before changing `shopping.py`. Converting it
to a skill the way the front end's was is the obvious next step.

`collect_unique_plants` rides on the same normalisation: it counts distinct
ingredients in the `PLANT_DEPARTMENTS` (Produce, Herbs & Spices, Nuts/Seeds &
Spreads) across the week's cook events, stored on `WeekPlan.unique_plants` at
generation and shown as the telemetry header's 🌱 count. It reuses the
shopping key rather than raw names, so "Cucumber, diced" and "Cucumber,
sliced" count once — a plant-variety readout that double-counted prep
variants would flatter the week rather than describe it. Recomputed by both
narrow regenerations, since replacing one recipe changes the week's plant
set.

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

#### The order a list is walked, and the two lines that are not items

`DEPARTMENT_ORDER` is the walk — produce at the entrance, the dry middle
aisles, then the chilled perimeter, which also picks the perishables up last.
It replaces `sorted(shopping_list.categories)`, which appeared in **seven**
independent places (the drawer, all three formatters, `ShoppingList.items()`
and both export paths), so the order was a decision made seven times and only
ever agreed by accident — and alphabetical is a zigzag through a shop: Dairy,
Fish, Grains, Herbs, Meat, Nuts, Pantry, Produce. `ordered_departments` is the
one call all seven make now.

**It is deliberately a separate constant from `DEPARTMENT_KEYWORDS`' order**,
which looks like the same list and is not: that one is match precedence,
specific → general, and reusing it would pin Pantry first forever because
"Beef broth" has to be claimed before "beef". A department the order does not
name sorts after the named ones alphabetically, so adding a keyword group
without touching the walk degrades to the old behaviour for that group rather
than silently placing it first.

**Google Keep turns every pasted line into a checkbox, which is what the
`format_shopping_list_keep` changes are about.** A bare department name — the
only thing it emitted before — arrives in the shop looking exactly like
something to buy, so "Dairy & Eggs" gets ticked off next to the milk. The
format cannot use markdown or a blank line to separate them (both become junk
items), so the separation is typographic: `── DAIRY & EGGS ──` costs the one
checkbox the plain name already cost and is unmistakably not a purchase. The
`trip` argument is the second half — the toast said which window was copied
and the payload did not, so two trips pasted into Keep were indistinguishable
once the toast had gone. It gets a heavier rule (`═══`) than the departments
carry, so the two kinds of divider are not mistaken for each other.

`pantry_covered_line` is deliberately absent from that format and present in
every other one: a line you are *not* buying has no business being a checkbox,
and the text, Markdown, PDF and HTML renderings are read at home where naming
it is what lets a stale pantry be noticed.

### Batch cooking on purpose: the two prep toggles

The Plan destination's Generate button, and the staged-changes bar's, both
open `ui_review`'s dialog rather than running the week directly, and two of
its controls reshape the *grid* before generation rather than merely
briefing the model: **bulk prep** and **long cook**. Each calls
`week.spread_batch`, which picks one dinner as an anchor
and links enough forward slots to it — via ordinary `link_leftover` calls, so
portions stay derived and nothing new is invented — to approach
`planning_rules.batch_target_servings`.

**Each batch takes one meal type, straight across the front of the week**
(`ui_generation.apply_batch_selections`): bulk prep claims the lunches, long
cook claims the dinners, both starting at day 1 and running as far as the
fridge window allows. On the shipped config that is Monday-Wednesday lunches
from one prepped dish and Monday-Wednesday dinners from another — six meals,
`batch_target_servings` (6) portions each, exactly.

The pairing is not arbitrary: a soup/stew/curry (`BULK_PREP_RULE`'s
candidates) is the dish that reheats at a desk and travels in a container, and
an oven roast or braise (`BATCH_ROAST_ANCHOR_RULE`'s) is dinner food. It also
gives Monday two *different* dishes rather than the same one twice, which any
arrangement filling all six slots out of a single row would have forced.

**The anchor is bookkeeping, not a decision.** Every recipe has to live on
some slot — that is what a cook slot *is* — and prep day has no slot of its
own in the grid, so the first day a batch is eaten holds the recipe and the
rest point back at it. The anchor day is therefore always day 1, and nothing
searches for it.

That is worth stating plainly because earlier versions *did* search, and the
search was the entire source of the trouble. Both toggles anchored on
"dinner", so they competed for the same seven slots; the second was pushed
later and later; a weekend preference dragged the long cook to Saturday; and
because `spread_batch` only ever *adds* claims, whatever shape one run
happened to land on was frozen into every run after it. The symptom was
Sunday-prepped food scheduled for Thursday and Friday. None of that machinery
survives — no `prefer_days`, no cross-toggle `exclude_days`, no
lunch-versus-dinner preference — because two batches on two different rows,
both starting at day 1, cannot collide and cannot drift.

**Prep day is not the Sunday on the grid.** The batch-prep session runs the
day *before* `spec.days[0]` — that is what `ui_cards.prep_day_column` draws as
an eighth column left of day 0 — so on a Monday-start week `spec.days[-1]` is
a full **7 days** after it. The Sunday a batch is cooked on and the Sunday at
the end of the week are different Sundays, and nothing prepped ahead is still
food by the second one. `spread_batch`'s `exclude_target_days` is the rule:
`apply_batch_selections` passes `{spec.days[-1]}`, and no batch may link into
it. The anchor may still land there, and an ordinary "Link to next lunch" from
Saturday dinner into Sunday lunch is untouched — that one is cooked on
Saturday, not on prep day, so `validate_week` deliberately gets no matching
backstop.

Three consequences worth knowing:

- **An anchor that cannot grow is passed over, not picked and abandoned.**
  `spread_batch` filters its candidates to days that can still reach a slot
  they are actually allowed to claim — mirroring the walk's own conditions
  rather than just asking whether an unexcluded day exists, since a day can be
  eligible and have nothing claimable on it. Choosing such a day spends the
  pick and then returns `None`, which surfaces as "couldn't find a day with
  room" on a week that had room all along.

- **A batch may re-point a location link.** `location_rules` claims slots
  before either toggle runs — Thursday lunch eats Wednesday's dinner, Friday
  lunch eats Thursday's, Saturday dinner eats Friday's. `_claimable` accepts a
  `LINK_ORIGIN_LOCATION` leftover as a target and re-points it, which honours
  the rule rather than overriding it: the rule says an Office lunch *is* a
  leftover and never says whose — "the previous day's dinner" is how
  `apply_location_modes` resolved it, not an intent — and the slot stays a
  leftover throughout. A `LINK_ORIGIN_USER` link is never taken; that one
  names a specific dinner deliberately.

- **A location link that *blocks* a near slot is released, not walked past.**
  Re-pointing alone still sent batches to the wrong end of the week, and this
  is why. `leftover_link_error` refuses to convert a cook that already feeds
  something, so with Thursday's lunch pointing at Wednesday's dinner, that
  Wednesday dinner — an early-week slot the batch actually wanted — was
  unavailable, and the forward walk simply carried on into Thursday and Friday
  instead. `_releasable_dependants` frees a blocking dependant when *every* one
  of them is a `location` link, returning it to a cook; that is the same state
  `apply_location_modes` itself falls back to whenever a day's previous dinner
  isn't a cook, so nothing invalid is being invented. A `user` or `batch`
  dependant is never released — the batch skips the slot instead.

- **`max_day_index` bounds the batch from prep day, and covers the anchor.**
  `max_span_days` counts from the *anchor's* day, which cannot see that a
  prep-session batch is cooked the day before the week starts: a Tuesday
  anchor reaching Friday is 3 days by that bound and **5 days out of the
  fridge**. Day index `i` is `i + 1` days after prep, so
  `apply_batch_selections` passes the **default** storage window's day-gaps
  minus one. It applies to the anchor too, unlike every other bound here — an
  anchor outside the window is already unsafe before it spreads anywhere.

  **This paragraph used to end by pinning `fridge_safe_days` at 3 rather than
  4**, on the grounds that 4 "allowed a batch onto Thursday, which is 4 days
  out of the fridge and past what these batches should carry". That was the
  right instinct answering the wrong question, and the whole of "Storage
  windows belong to the dish" below is the answer to the right one: 4 days is
  fine for a stew and two days too many for a rice tray bake, so one global
  could only ever be wrong in one direction or the other. The default is now
  4 day-gaps and Thursday is reachable again — bounded by what the dish
  actually is rather than by a number picked to protect the worst case.

  Measured on `default_week_spec` with the shipped `config/`, both toggles on:
  bulk prep `Monday:lunch` → Tuesday and Wednesday lunches, long cook
  `Monday:dinner` → Tuesday and Wednesday dinners. Six meals, 6 portions each,
  `validate_week` clean and byte-identical across repeated runs. Note that
  Wednesday is `spread_batch`'s `target_claims` cap (3 claims) doing the work
  here, **not** the fridge bound — the lengthened window only shows when the
  walk has to step over a day it cannot claim, which is precisely the case the
  old bound silently shortened.

**The reporting side counts from prep day too, and for a while it didn't.**
`storage_note`'s `keeps_for_days` was measured from the anchor's own grid day,
which is the same blind spot `max_day_index` was added to fix on the planning
side — and since every anchor is day 0, it was short by exactly one on every
prep batch. That under-reported the span ("eaten across 2 day(s)" for food
three days out of the fridge) and, worse, flipped the advice at exactly the
wrong point: `storage_note` chooses between "refrigerate in airtight
containers" and "freeze the rest" on `keeps_for_days < the dish's window`, so
the maximum-span batch reported one day short, compared it against the window,
and told you to refrigerate the one batch in the week sitting at the fridge
limit — the whole reason the freeze branch exists. `apply_batch_selections`'
own window-minus-one bound guarantees that case on every week both toggles
run, and it can't be papered over downstream because
`generate_sunday_prep_session`'s prompt tells the model not to recompute the
note.

`week.PREP_DAY_INDEX` (-1) is the fix, and it is one idea used in four places
rather than four adjustments: `week.cook_day_index(spec, day, prepped_ahead)`
answers "which day was this actually cooked on", `span_days` takes the same
flag, and the callers each supply it from the handle they have —
`build_cook_event` from `week.prep_day_batch_slot_ids(config)` (the two
anchors, which are known before the first call — the function moved from
`planner` to `week` when `storage_safety_errors` needed it, and is still
re-exported under its old name), everything after generation from
`planner.is_prepped_ahead(event, week_plan)` (the stamped
`candidate_slot_ids`). The two lookups exist because of ordering, not
duplication: `generate_sunday_prep_session` runs *after* every cook event is
built, so the session doesn't exist yet when the first one needs the answer.

Three things about it are decisions:

- **`is_prepped_ahead` is `is_sunday_prepped` minus the shake.** The shake
  rides along in the same session but is only *portioned* ahead — each
  training morning blends it fresh — so its food is exactly as old as its own
  day says. The anchors are a lunch and a dinner and the shake is always a
  breakfast, which is what lets one `meal_type` test separate them, the same
  discriminator `slot_views` already uses for the reheat estimate.
- **`span_days` defaults to the anchor day, so `validate_week` is
  untouched.** That backstop bounds a hand-built chain of "Link to next lunch"
  clicks; the prep batches are bounded on the planning side by
  `max_day_index`, and making the backstop prep-aware would start rejecting
  the very weeks `apply_batch_selections` deliberately builds.
- **`ui_state.apply_spec` and `swap_slot_with_favorite` had to move with
  it.** `scale_to_servings` rewrites the storage note whenever a batch is
  rescaled, so fixing only generation would have let a single grid edit put
  the off-by-one straight back. The per-card fridge/freezer badge counts from
  the same origin for the same reason — a note saying "freeze the rest" over a
  row of cards all badged "fridge" is two surfaces disagreeing about one
  batch.

**Both anchors' prep time collapses too, and one of them did not for two
releases.** `slot_views`' collapse to `SUNDAY_PREP_REHEAT_MINUTES` used to
test `event.meal_type == "dinner"`, which was a faithful proxy for "cooked on
prep day" only while the long cook was the sole anchor. `apply_batch_
selections` anchors bulk prep on **lunch**, so that card showed its full
from-scratch cook time for a dish that came out of the pan the day before the
week started — while the fridge badge immediately beside it, counting from
`cook_day_index`, correctly said otherwise. Two surfaces on one card
disagreeing about one batch, which is the same failure the badge and the
storage note were reconciled to avoid.

It asks `planner.is_prepped_ahead` now — the function that already *names*
the rule, and the same one `days_since_cook` reads two lines above it. That
is the fix rather than a wider `in ("lunch", "dinner")` test: the shake still
has to be excluded (it is only *portioned* ahead, and blends fresh each
training morning), `is_prepped_ahead` is already exactly that distinction,
and a third batch axis added later cannot reopen the bug a third time.

`spread_batch` returns `None` for an anchor that never grew past what an
ordinary dinner already gets for free, which is the same "no batch happened"
signal as no valid anchor at all. Both are honest outcomes; neither is an
error. The chosen anchors ride on config as `long_cook_anchor` /
`bulk_prep_anchor`, which is how `generate_meal_type_week` knows to send the
per-slot anchor directive instead of the whole-week rule, and how
`generate_sunday_prep_session` knows there is something to prep.

`Recipe.long_oven_cook` and `Recipe.bulk_prep_friendly` are the model's own
answers about a dish, and they are separate fields on purpose — a dish can be
either, both or neither. **`is_sunday_prepped` used to test them directly, and
that was a bug in both directions**: a stray, unprompted flag on some other
dinner claimed the reheat badge it never earned, and — the one that actually
bit, on a real week — the anchor itself came back with both flags `False`
despite `LONG_COOK_ANCHOR_SLOT_DIRECTIVE`/`BULK_PREP_ANCHOR_SLOT_DIRECTIVE`
telling the model to set one, so a genuine Sunday-prepped batch (a "Korean
Beef Bulgogi Rice Tray Bake" anchor, in the wild) rendered as an ordinary
from-scratch cook eaten late in the week, with no "prepped on Sunday" badge on
the leftovers eating it. `generate_sunday_prep_session`'s own candidate
selection already matches by slot_id rather than by flag, for exactly this
reason, and already knew the exact slot_ids it folded into the session before
the model was ever called — that fact just wasn't being kept.
`SundayPrepSession.candidate_slot_ids` now carries it: stamped onto the
response after the call returns (Python-only, not something the model fills
in), and `is_sunday_prepped` matches an event against it by slot_id instead of
trusting the recipe's self-report. A session saved before this field existed
has an empty list and falls back to the old flag check — same pre-migration
tolerance `history_styles()` extends to old `meal_history.json` entries.

**A second, separate bug sat on top of that one: `ui_state.py` only ever
called `is_sunday_prepped` for `MODE_LEFTOVER` slots**, so even a correctly
recognised session never reached the batch's own anchor card (a MODE_COOK
slot on the day it first appears) or the shake candidate — both cook events
that are genuinely part of the session, not leftovers of it. The anchor's own
recipe was actually cooked in the Sunday session too; its grid day is only
where the leftover chain has to start, so it needs the "prepped ahead" badge
exactly as much as the leftovers eating it do, and its `prep_minutes` should
collapse to `SUNDAY_PREP_REHEAT_MINUTES` the same way — nothing is cooked
fresh on that calendar day either. The shake is different: `find_shake_
candidate` rides along in the same session, but it is never leftover-linked
because each training morning genuinely blends its own shake fresh — only the
shared base is portioned ahead. So it gets the badge (its base *was* prepped
Sunday) but keeps its own `prep_time_minutes`; `event.meal_type == "dinner"`
is what tells the two cases apart, since both are MODE_COOK and both test
`is_sunday_prepped` true.

### Buying what the shops actually stock

A generated week called for **mustard greens**, which no supermarket within
reach of `schedule.json`'s `regional` postcode carries, and reached for fresh
seafood a regional Victorian town can't reliably supply. Availability is now a
third axis beside cuisine and diet style, and it is answered in three places
because the failure had three separate sources.

`schedule.json`'s `sourcing` block is the config — beside `regional` rather
than in `dietary_rules`, because it is a fact about the **shops**, not about
the body: move house and every value here changes while not one dietary rule
does. It is also `regional`'s first real consumer, so "Coles, Woolworths or
Aldi" reaches the prompt qualified by "in VIC, AU" rather than left to mean
whatever the model assumes.

| key | means |
|---|---|
| `supermarkets` | the shops a week is bought from, named verbatim in the prompt — a shop name tells the model more about what is on the shelf than any adjective could |
| `specialty_grocers_available_days` | which weekday names an Asian grocer, deli, health-food shop, fishmonger or farmers' market is actually reachable on — `None` (absent) is every day, `[]` is none |
| `fresh_seafood_available_days` | same `None`/`[]`/list-of-weekdays shape, for a reliable fresh fish counter |
| `max_seafood_meals_per_week` | whole-week cap on meals whose *dominant* protein is seafood; `None` is uncapped |

**`build_sourcing_rule` is the soft half of a constraint whose hard half
already exists.** `dietary_rules.banned_ingredients` rejects a named
ingredient at validation, but a blocklist can only name what somebody already
thought of, and "not stocked within an hour's drive" has an unenumerable tail
— galangal, curry leaves, fresh yuzu, banana blossom, specialty butcher cuts.
So this shapes what the model reaches for and `banned_ingredients` still
polices what it must never return, the same division of labour `diet_styles`
has with the same list. It sits immediately after the banned-ingredient line
in `build_generation_rules` for that reason, and emits nothing at all when
`sourcing` is absent or every field is at its permissive default.

The wording is deliberately a **substitution instruction, not a prohibition**
("substitute the closest ingredient a mainstream supermarket stocks and put
the SUBSTITUTE in the recipe"). "Don't use it" invites the model to abandon
the cuisine over one ingredient, or to name the unavailable item anyway
because the dish genuinely needs one.

**Availability can be day-of-week, not just week-wide** — a specialty
grocer or fishmonger reachable only on a Saturday market run, say.
`build_sourcing_rule` takes `days`, the cook days its caller's prompt
actually covers, and `_sourcing_day_split` partitions them against each
day-list into `restricted`/`open`. `generate_meal_type_week` passes every
day the meal type is cooked (often all seven) and `generate_day` passes its
one day, so a week where dinner is cooked every night but the market only
opens Saturday/Sunday gets a day-scoped sentence naming exactly which nights
each rule binds, rather than either an unconditional block or silence. A call
whose days are wholly inside or wholly outside the available list still gets
the plain unconditional wording — the day-scoped sentence only appears when a
single call's days straddle both, which is the common case here since one
`generate_meal_type_week` call spans the whole week.

**The seafood cap is counted, not merely stated, and that is what makes it
week-wide.** No single generation call can see more than its own axis —
`generate_meal_type_week` sees one meal type's seven days — so a rule saying
"at most one per week" sent to all four axes permits four. `generate_week_plan`
instead counts what each stage actually returned (`is_seafood_meal`) and passes
the **remaining** allowance to the next one, spending the cap in
`MEAL_TYPE_PRIORITY` order — the same seed-then-extend pattern `avoid_proteins`
already uses across stages. Dinner is first in that order, so it gets first
claim, which is where a seafood meal is wanted if the week is only having one.
Once spent, later axes are told none of theirs may be seafood.

`is_seafood_meal` reads the recipe's **highest-protein ingredient** rather than
scanning every name, because a scan spends the whole week's allowance on a Thai
dinner's tablespoon of fish sauce or a bowl of dashi. Unlike
`extract_main_protein` it applies to every meal type — a smoked-salmon
breakfast is a trip to the same counter. Counting is per cook event, not per
slot that eats it: a bulk-cooked salmon feeding three lunches was bought once.

`regenerate_single_day`/`regenerate_single_meal` deliberately send the sourcing
rule but **not** the cap: a single replaced meal has no week in front of it to
count against, and a cap restated there would forbid seafood in the very slot
being fixed.

### Some slots are decided before the model is called

Three things now claim a slot ahead of generation, and the order they run in
is the order below. Everything they claim is one fewer recipe the model is
asked for, so a week with favourites in it is also a cheaper week to run.

**1. Where you are that day** (`schedule.json`'s `base_schedule` +
`location_rules`). These were config nothing read — declared on `AppConfig`
purely so `schedule.json` passed `extra="forbid"`. `week.apply_location_modes`
is the consumer, called by `default_week_spec`, and it reads
`<meal_type>_mode` off the day's location: an Office lunch inherits the
previous day's dinner, a Holiday block skips all four meals.

Two things about it are load-bearing. It applies to a **fresh grid only** —
once a week exists its slots carry structural edits the user made on purpose,
and re-imposing the schedule over those would silently undo them. And
`lunch_mode: "leftover"` has to *resolve* to a source, not just set a mode: a
leftover with no `source` fails `validate_week` outright, so it links to the
previous day's dinner and **falls back to cooking** when there's nothing to
inherit from (day one of the week). A grid that can't be generated is worse
than a grid that cooks one extra lunch.

`restrictions` reaches the prompt separately, via `build_location_note` in the
per-slot brief — per slot rather than per call because `generate_meal_type_week`
spans seven days and Monday at the office says nothing about Tuesday. The
tags are translated through `LOCATION_RESTRICTION_PHRASES` rather than sent
bare.

**A location only constrains the meals it declares a `<meal_type>_mode` for**,
which is what stops "must travel in a container" landing on a Monday
*breakfast* — eaten at home, before leaving. `Office` names `lunch_mode` and
nothing else, and that key is already the honest statement of which meals the
location has an opinion about, so it is reused as the scope rather than
duplicated into a parallel `restricted_meals` list that could disagree with
it. **There is deliberately no "no reheat" tag**: the office has a
microwave, which is also why `location_rules.Office` sets `lunch_mode:
leftover`. The two together could only be reconciled with a per-recipe
"edible cold" flag, and the product decision was that reheating is fine.

**2. A morning gym session's breakfast**, pinned to a shake — unchanged, see
"Targets come from the body" above.

**3. A saved favourite** (`planner.select_favorite_assignments`). Some slots
don't need inventing because there is already a dish you know you want.
`SlotSpec.recipe_id` carries the catalog id; the slot is **still a cook**, so
portions derive, shopping aggregates it and `span_days` works exactly as for
a generated recipe — a fourth mode would have meant revisiting every
`mode == MODE_COOK` test in the repo.

The rules, and why each is shaped that way:

- **Breakfast**: one favourite across `favorite_breakfast_slots` (2) mornings
  rather than a different one each day — the point of a standing breakfast is
  that it is the same one, and one shop covers both. A slot already pinned to
  `WORKOUT_BREAKFAST_STYLE` is skipped: the shake is a hard nutritional rule
  and a favourite is a preference.
- **Lunch**: one per eligible slot. Office lunches mostly never reach here —
  by this point they are leftovers, courtesy of step 1.
- **Dinner**: up to `favorite_dinner_slots` (2) **distinct** favourites — a
  count of dishes, not of days one dish covers the way breakfast works,
  because dinner is where repetition shows and `DINNER_VARIETY_RULE` says so
  outright. Capped rather than one-per-slot like lunch because dinner is the
  only meal type `pick_cuisine_blocks` lays contiguous blocks over, and
  `pin_recipe` blanks the cuisine of every slot it claims: left uncapped
  against a large catalog every dinner becomes a pin, no block survives, and
  the pantry overlap the blocks exist for goes with it. Two leaves five
  generated dinners, which a 4/3 `cuisine_block_pattern` can still do
  something with.

  **Which days is `cuisine_run_ends`, and that is the whole reason selection
  reads `slot.cuisine`.** Blocks are already resolved by the time this runs
  (`resolve_auto_choices` precedes it), so the runs are visible — and
  blanking a run's *last* day leaves the remainder contiguous, where blanking
  a middle day splits one block into two shorter ones with a hole between,
  which is then what `build_cuisine_continuity_rule` has to describe and what
  the shopping list pays for. One pin per run rather than two from the same
  run also spreads the favourites across the week instead of clustering them
  at the front, and damages each block equally rather than halving one and
  leaving the other whole. A week with no cuisines resolved degrades to
  earliest-first, exactly as lunch already does.
- **Snack**: nothing, deliberately. `week_defaults.snack` is `skip` in the
  shipped config, so there is usually no snack slot to claim — and a rule
  whose slots don't exist is one that can't be seen to be wrong.

**A `long_oven_cook` dish may only take a day with the hours in it**
(`day_allows_long_cook`), and that one function is read by both halves of the
rule — `favorite_fits_day` for a saved favourite, `reject_misplaced_long_cook`
for a generated one. It cuts across all three meal types on the favourite
side; breakfast, which covers two mornings from one record, has to suit both.

Nothing else in the app was going to stop a long cook landing on a Tuesday,
which is worth spelling out because all three plausible candidates look like
they should have: `build_batch_roast_rule` tells the *model* which days suit a
long cook and a favourite is never generated, so that rule never sees it; the
placement rule above is about protecting cuisine blocks and says nothing about
the day having the hours in it; and `prep_limit_for`'s 30-minute weeknight
ceiling counts **active** minutes, which a braise honestly reports as the 20
that are hands-on rather than the 8-10 hours it then sits in the oven. A "Slow
Cooked Beef Cheeks" imported from Keep was scheduled for a Thursday exactly
this way. Eight of the 36 dinner favourites in the shipped catalog are long
cooks, so this is a shape rather than one bad record.

Two consequences of *how* it declines are worth knowing. An ineligible run end
takes the next eligible favourite instead of being left empty, so a long cook
is deferred rather than dropped — it waits for a run end that lands on a day
with room. And the dinner cap counts **pins made, not run ends looked at**:
slicing the run ends first, which is what this used to do, spends both of them
on declined weeknights and pins nothing at all on a week whose Saturday was
free the whole time.

#### Which days those are: presence, not the calendar

`day_allows_long_cook` reads `location_rules.<location>.allows_long_cook` for
the day's `base_schedule` location, and **falls back to the weekend** when the
location declares nothing — so a config predating the key plans
byte-identically to before it existed. `week.location_rule` already collapses
"no `base_schedule`", "unknown location" and "no rule for it" into `{}`, which
is why one `.get` covers all three.

This used to be the weekend and nothing else, with the objection to that
recorded here and in `favorite_fits_day`'s own docstring: `base_schedule` does
know Tuesday is a WFH day and that a slow cooker started at 8am on one is
perfectly fine. What held it back was the worry that a second notion of "a day
with room to cook" would drift away from `prep_limit_for` and the batch-roast
rule. **It is not a second notion of the same thing — it is the other axis.**
Active minutes are a claim on your *attention* and stay weeknight-versus-
weekend; elapsed hours are a claim on your *presence*, which is what a braise
actually needs and what `base_schedule` already records. `prep_limit_for` is
deliberately untouched: widening the *active* ceiling on a WFH day would say
you have three hands-on hours on a working Tuesday, which is false.

**A location may rule a weekend day out, not only rule a weekday in**, and on
the shipped config it does — `Saturday: Outing` loses the long cook that the
calendar rule gave it, while Tuesday and Wednesday gain one. That direction is
the point rather than a side effect: the complaint against the old rule was
that the calendar is not where you are, and you cannot start a braise on a day
you are out. Declaring `Home` in `location_rules` (it had no entry before) is
part of the same move — relying on "Home happens to fall on a Sunday" is the
coupling being removed, and a Home day placed on a Wednesday should carry its
hours with it.

#### The generated side is enforced too, and needed a measured field to do it

This **used** to stop at the favourite path, with the generated one left soft:
`BATCH_ROAST_RULE` stated a preference and nothing rejected a model that put a
4-hour braise on a Tuesday while truthfully reporting 25 minutes of active
prep. So the two halves disagreed about a Thursday — a *saved* braise could
not take one and a *freshly generated* braise could, which is the worse of the
two failures because it is the one no catalog record makes visible.

`reject_misplaced_long_cook` is the hard half, run by a `model_validator` on
both response models (`DayRecipes` from its context's single `day`,
`MealTypeWeekRecipes` over its own day keys) — the same split
`enforce_prep_limit` already makes, over the same shared function, so the two
axes cannot disagree about a Tuesday.

**Two ways to fail, because one field alone catches only half of it.**
`long_oven_cook` is the model's self-report and a careless model simply omits
it. `Recipe.total_time_minutes` is the measured claim `ELAPSED_TIME_RULE` asks
for — whole wall-clock time including unattended hours — and is what catches
the braise that never flagged itself. It defaults to `None`, which means
*unknown* and never 0: every recipe saved before the field existed carries
None and falls through to the flag alone, the same pre-migration tolerance
`history_styles()` extends to old history entries. `WEEKNIGHT_ELAPSED_LIMIT_
MINUTES` (90) is deliberately a third number rather than a re-reading of the
two prep ceilings, for the attention-versus-presence reason above.

**A batch anchor is exempt, and the rule would break the long-cook toggle
without it.** `apply_batch_selections` anchors both batches on day 1 — Monday,
on the shipped config — but that food is cooked on **prep day**, the Sunday
before the week starts (`week.PREP_DAY_INDEX`). The anchor's grid day is only
where its leftover chain has to start, so judging it against Monday's schedule
would reject the one dish in the week most deliberately given the hours. Same
two slot ids `build_cook_event` already counts fridge days from
(`prep_day_batch_slot_ids`).

**The prompt names exactly the days the validator accepts**, which is why
`BATCH_ROAST_RULE` became `build_batch_roast_rule(config, days)` and why
`build_long_cook_day_rule` joined the shared rules block. A model rejected for
breaking a rule it was never given burns a 30s-3min retry to discover a
constraint one sentence would have stated — and `WEEKEND_PREP_LIMIT_MINUTES`
already learned the same lesson from the other direction, stated in the prompt
and enforced nowhere, so a 200-minute weekend recipe passed validation while
violating its own brief. `build_batch_roast_rule` **emits nothing at all when
no day in scope qualifies**: asking for a long cook the validator is certain
to reject is a guaranteed wasted call.

**A pinned dinner has to be visible to the model generating the same stage.**
`avoid_proteins` is extended from `stage_events` only *after* a stage
finishes, so pins built moments earlier in the same stage aren't in it yet.
That was harmless while only breakfast and lunch could pin — neither has a
consecutive-night protein rule — and is not harmless now: a pinned lamb
Thursday the model cannot see is exactly how a generated lamb Friday gets
through `DINNER_VARIETY_RULE`. `generate_week_plan` appends the stage's own
pinned proteins and recipe names to what it passes down, without folding them
into the running list, which stays the record of what *completed* stages
cooked.

Eligibility is strict LRU over `planning_rules.favorite_reuse_days`
(`{"breakfast": 7, "lunch": 21}`) — same rule and same reasoning as
`next_choice`. **`history_max_entries` had to move from 21 to 28 for this**:
it caps *entries* (one per cooked day), so at 21 a favourite that aged off the
window was indistinguishable from one never cooked, and the 21-day lunch rule
silently stopped binding at exactly the point it should have started.

Three details that were bugs first:

- **Pinning clears the slot's style and cuisine** (`week.pin_recipe`).
  `resolve_auto_choices` has already rolled a style by then, so a scramble
  pinned onto a `yoghurt_bowl` slot rendered as "YOGHURT BOWL" over a plate
  of eggs.
- **The model must only be handed the unpinned days.**
  `_generate_meal_type_events` derives which slots to ask for from
  `day_budgets`' own keys, so passing the full dict generated — and paid for —
  a second recipe for a slot already filled.
- **A favourite is normalised to one serving first** (`planner.single_serving`).
  It was bookmarked off a card already scaled to its portions, so a 2-serving
  dinner needs a 0.5 factor — outside `portion_trim_limits`, so the clamp
  fired at 0.6 and every pinned favourite silently served 20% over budget.
  `PlannerState.swap_slot_with_favorite` already normalised for this reason;
  the rule is now shared rather than copied.

`ui_generation.generate_week` calls `week.clear_recipe_pins` unconditionally
alongside `clear_styles`/`clear_cuisines`, for the identical reason: selection
only ever fills an *empty* slot, so without the clear, week one's favourites
would be re-served forever and the reuse window would never advance.

### A skipped meal that was actually eaten

`MODE_SKIP` contributed nothing anywhere, which is right for a meal genuinely
not eaten and wrong for the common case — dinner with friends, a working
lunch. Those calories are consumed, and a day that ignores them hands their
whole share to the meals it does plan, which come back oversized.

`SlotSpec.skip_estimate` (the four `MACRO_KEYS`, or None) makes such a slot
behave **exactly like a leftover**, in the same two places a leftover is
already handled:

- `generate_week_plan` subtracts `week.skip_estimate_totals` from each day
  into `plannable_targets` before the first split. `targets` itself is left
  whole — it becomes `WeekPlan.targets`, the telemetry denominator, and the
  day's goal doesn't shrink because part of it was met at a restaurant.
- `WeekPlan.day_slot_macros` adds the same estimate to the numerator, so the
  two agree and the header reads 100% rather than 60%.

`None` and an all-zero estimate are deliberately different: None is "not
eaten at all" (the original skip), zeros are "eaten, cost nothing measurable".
The card's "Eaten out?" button seeds from `PlannerState.default_skip_estimate`
— what the slot *would* have been briefed at, via `split_targets` with the
slot temporarily added back as a cook — because a restaurant dinner is
usually well above its weighted share and a missed meal is 0, so the default
is only ever a starting point. Calories/protein/carbs are typed and fat is
derived, the same division `ui_review.day_target_row` uses.

Fibre is deliberately **not** part of a skip estimate: the fibre in a meal
nobody cooked isn't estimable, and 0 is more honest than a guess.

### Fibre is targeted, and still has no term in the energy identity

**`MACRO_KEYS` names the keys with a term in `calories ~= 4p + 4c + 9f`, not
the keys with a target.** It answered both questions for four releases,
because fibre was the only nutrient the app reported and did not aim at. It
now has a daily target, a per-slot share and a denominator on the telemetry
header — and it is still not in `MACRO_KEYS`, which is the distinction the
whole feature turns on.

Every budget in `planner.py` is checked against that identity:
`split_targets` scales all four together, `apply_protein_floor` moves calories
with protein at 4 kcal/g to preserve it, `reject_untrimmable_macro_miss`
bounces a response whose calories don't reconcile. Fibre has no term in it —
it is already excluded from `net_carbs_g` by definition — so putting it in
`MACRO_KEYS` would drop a fifth number into arithmetic with nowhere to put it.
The split is therefore **identity operations walk `MACRO_KEYS`, proportional
ones may walk `NUTRIENT_KEYS`** (`MACRO_KEYS + ("fiber_g",)`): a portion trim,
a recipe total and a per-slot share are all linear in quantity and care
nothing for the identity, which is exactly what lets fibre take a briefed
share of a day without entering a single budget check.

#### The target: a floor, raised by a big day

`nutrition_engine.calculate_fiber_target_g(calories, floor_g)` is
`max(floor_g, calories / 1000 x FIBER_G_PER_1000_KCAL)`. Two numbers because
fibre is the one nutrient here whose requirement is genuinely part floor and
part scale: 14 g/1000 kcal is the dietary reference figure and sits in the
same class as `KCAL_PER_G_PROTEIN`, and `user_profile.fiber_floor_g` (30 g,
the Australian adequate intake for an adult man) is the preference half.

**The floor is the load-bearing one, for the same reason protein is locked to
the *target* weight rather than today's.** Scaled alone, an 800 kcal Fast 800
day asks for 11 g — cutting the fibre target exactly as the deficit that made
the day small starts to need the satiety and the gut microbiome fibre is
eaten for. A deficit does not shrink what the gut needs, so the energy term
may only ever raise the figure. On the shipped ~1722–1850 kcal days the floor
is what binds; above ~2143 kcal the scale takes over.

Four things about where it is computed are decisions:

- **It is not assembled inside `calculate_macro_targets`**, despite the
  queue's own sketch of the work saying so. That function returns `calories`
  *before* `hydrate_dynamic_targets` replays the training uplift onto it and
  takes it `min()` against a diet-style ceiling, so a fibre figure computed
  there would be wrong on precisely the days that move — every training day
  and every capped one. Same "after the uplift, not before it" argument that
  places the ceiling itself. It is a function of its own instead, called once
  each calorie figure is final.
- **Two callers, both final.** `hydrate_dynamic_targets` writes it onto every
  hydrated day, and `calculate_daily_targets` derives it from
  `weekly_schedule`'s post-hydration calories exactly as it derives `fat_g`.
  One function, so the engine path and the file path cannot disagree about a
  Thursday.
- **Every hydration path writes it, including the three that give up.**
  `with_fiber_targets` is the fallback: fibre needs the day's calories and a
  floor, and neither is a fact about the body, so an unfilled `user_profile`
  or a failed engine call does not cost it. Without that, `/api/targets`
  (which reads the hydrated `weekly_schedule` straight out) would report
  fibre on a machine with a weigh-in and omit it on one without, while the
  telemetry header printed a figure either way — the "one number, one call"
  rule at the end of this file, reached from the storage side.
- **There is deliberately no `fiber_g` on `DaySchedule`, and no
  `target_modes` entry.** A key the file may write and the app then ignores
  is a second place for a number to be wrong, which is the whole complaint
  this file makes about `weekly_schedule`'s inert calories; `fat_g` is stated
  and recomputed as a historical accident, tolerated rather than repeated. And
  `TARGET_MODE_MACROS` exists for the two macros with *two* possible sources —
  fibre, like fat, has one, so a toggle for it would be a control that changes
  nothing. Settings' Daily Targets panel says so in words instead, which is
  that section's whole point: every figure the week is planned against owes
  the reader a sentence about where it came from.

#### The per-slot share, and the two things it deliberately is not

`split_targets` gained a `fiber_target_g` keyword and a fourth pass
(`split_fibre_share`) after the protein floor. Omit it — as
`PlannerState.default_skip_estimate` and every pre-target caller does — and no
budget carries `fiber_g` at all, which is byte-identical to before the target
existed.

- **A `meal_overrides` pin does not pin fibre.** An override states a fixed
  *energy* budget, which is the thing `meal_overrides` exists for, and energy
  says nothing about fibre — so a pinned meal takes its weighted share like
  any other. A fifth optional key in an override would be a number with no
  identity to check it against.
- **There is no fibre counterpart to `apply_protein_floor`.** That floor
  exists because protein is dose-limited *per meal*; fibre is not, so a day
  that reaches its figure across three meals has reached it.
- **The share does not cascade, and that is the one non-obvious decision.**
  Every macro budget is a share of what is *left* of the day after each
  earlier stage's actual output, because the day's energy has to total. A
  fibre target is a goal rather than a sum to spend, and models come back
  fibre-light far more often than fibre-heavy — so cascading it would pile the
  week's whole shortfall onto whichever meal type runs last, which is exactly
  the failure `cap_to_weighted_share` bounds for calories and could not bound
  here (a portion trim scales fibre with everything else and can no more add
  fibre than it can add protein). `generate_week_plan` therefore reads each
  slot's share out of `apriori_budgets` — the day's full target, split once
  before any stage ran — so **a meal's fibre brief is the same number
  whichever stage generates it**, where its calorie brief legitimately is not.
  A meal that comes back short leaves the day short, visibly, in the header's
  `FIB 24/30g`. Same standing answer as an orphaned leftover and a capped
  surplus: show the gap, do not distort a meal to hide it.
- **A skip estimate still carries no fibre**, so a skipped-but-eaten slot
  leaves the day's fibre goal whole and simply is not in the denominator that
  divides it — the same direction `targets` itself takes for calories.

#### There is still no validator, and the prompt rule reversed

Nothing rejects a fibre-light response. A rejection costs a full 30s–3min
retry, a single scale factor cannot change a macro ratio, and nothing
downstream could act on the rejection — the same reasoning that keeps
`diet_styles` and `sourcing` soft while `banned_ingredients` stays hard.

`FIBER_REPORTING_RULE` **is now `FIBER_TARGET_RULE`**, renamed because its
second sentence used to say the opposite of what a target means: "tracked for
reporting only and has no target: never ... pick an ingredient to raise it".
That was right while there was nothing to aim at.

**The clause that had to survive the rewrite is the one about trading.** A
model told to hit a number will hit it out of whatever it has, and the four
macros are the budget that actually is checked — a recipe buying 6 g of fibre
with 200 kcal of extra lentils passes no validator this app has. So the rule
still forbids trading any of the four against fibre, and adds the mechanism:
fibre is bought by **substitution at constant macros** (wholegrain for
refined, legumes in place of some starch, skins left on, the vegetable already
in the dish chosen for its fibre), which is genuinely how the nutrient works.
Naming the mechanism is not decoration — "don't trade" alone leaves a model
with a target and no permitted way to reach it, which is the shape of rule it
drops entirely.

`fiber_g` still defaults to `0.0`, which is what keeps recipes saved before it
existed loadable — same pre-migration tolerance `history_styles()` extends to
old `meal_history.json` entries — and the rule still asks for it explicitly
rather than relying on the schema description, because that default means an
omitted field produces a silently fibre-free week rather than an error.

#### The one number that now has a measured counterpart

Cronometer's daily-summary export carries fibre and
`CRONOMETER_MACRO_COLUMNS` did not capture it, so of the five nutrients the
app reports, fibre was the only one holding a *planned* figure and no
measured one — while also, at the time, being the only one with no target.
The telemetry header's day row prints `FIB 32/38g` and, beside it,
`logged 24g` for a day Cronometer has a fibre figure for.

**Which pair takes a divider and which does not, and only half of that
changed.** `planned/target` is the `actual/target` shape every other figure
in the row already carries and is honest now the planner aims at a number.
`logged` is not that: it is the same quantity measured a second way, not a
goal, so it sits *beside* the pair rather than under it — `32/24` would still
read as a goal that was missed, and that is still not what the sync is
saying. `ui_state.fibre_view` is where both rules live, as a pure function
returning the formatted halves, so the widget prints what it is handed rather
than deciding it; `ui_telemetry.py` renders the logged half as a **second
label**, in slate, for the same reason. `delta` stays signed against the
*plan*, never against the target beside it — two different questions, and
only one is about the sync agreeing with the kitchen.

**Capture and readout had to land together, or neither.** Capture alone
reproduces the shape v0.29.0 closed for Garmin's sleep data: `daily_actuals`'
only consumers are `calculate_adaptive_tdee` (calories alone) and
`logged_intake_for` (`MACRO_KEYS` alone), so a stored `fiber_g` would be
written on every sync, pay its fetch cost and be read by nothing. That is
also the standing rule for the rest of that export — sodium, potassium and
the micronutrients are all one dict entry away and none of them has a
reader, so **an entry in `CRONOMETER_MACRO_COLUMNS` has to assert that
something reads it.**

Three things it deliberately does not do:

- **`MACRO_KEYS` is untouched.** `fiber_g` rides on `NUTRIENT_KEYS` on the
  measured side exactly as it does on the planned one, so
  `logged_intake_for`'s budget arithmetic and every `calories ~= 4p + 4c +
  9f` check are byte-identical to before. That stayed true when the daily
  fibre *target* landed a release later: the target rides on
  `NUTRIENT_KEYS` too, and not one identity operation walks it.
- **An absent column is omitted, never zeroed** — `_prune` and
  `has_measurements` are unchanged, so every row synced before this existed
  reads as "no log" and shows the planned figure alone. A stored `0.0` would
  claim a day with no fibre in it.
- **`PlannerState.logged_actuals_for` matches by *date*, not by weekday**,
  which is what makes this a per-day readout rather than a today-only one.
  `planner.logged_intake_for` refuses every day but today because a
  `SlotSpec` carries a weekday name and nothing else; a loaded `WeekPlan`
  carries `week_start_date`, so every column of the grid has a real calendar
  date to match against. A plan predating that field has none, and gets the
  planned figure alone — the same tolerance `day_date_iso` already draws.

### Leftovers can't outlive the fridge

How long a leftover may sit was config that only ever flavoured a storage
note. It is now enforced four times, and the split matters:

- **Prevention, from the anchor.** `week.spread_batch` takes `max_span_days`
  and stops its forward walk there, so neither batch toggle can plan food more
  than that many days past the day it was cooked.
- **Prevention, from prep day.** `max_day_index` bounds the same window for a
  batch that is *not* cooked on its anchor day — every prep-session batch, see
  "Batch cooking on purpose". `max_span_days` alone let Sunday-cooked food
  reach Friday, because from a Tuesday anchor that is only 3 days.
- **Prevention, at the brief.** `build_storage_rule` names the span each slot
  needs and `reject_short_storage_class` rejects a dish that cannot meet it —
  the pair below.
- **Backstop.** `week.storage_safety_errors`, read by `validate_week` before
  generation and again by `generate_week_plan` after it. A chain built by hand
  out of "Link to next lunch" clicks never goes through `spread_batch`, and
  neither does an imported or hand-edited `week_plan.json`.

Bounding the spread rather than only rejecting the result is the difference
between never creating the problem and refusing to generate a week the
planner itself just built.

#### Storage windows belong to the dish, not to the config

`inventory_rules.fridge_safe_days` was **3**, one global number read in six
places, and **it was wrong in both directions at once**. A beef stew keeps 4
days, so 3 threw away a day of good food and a batch that could have covered
Thursday. A rice or pasta dish keeps **2** — cooked rice and pasta carry
*Bacillus cereus* spores that survive cooking and produce toxin as the dish
sits, which is why the general 4-day rule has an exception carved out of it at
all — so 3 permitted a rice tray bake batched on prep day to be eaten a day
past its safe window, and `apply_batch_selections` built exactly that shape on
every week the long-cook toggle ran.

**Moving the number to 4 alone would have made the dangerous case worse**, so
the lengthening and the dish-level exception landed in one change. That is
also why the two are pinned together in `tests/test_food_safety.py`
(`TestTheDefaultLengthened` and `TestRiceIsBoundShorter`): a permissive change
riding on a safety one must be asserted, not discovered.

`inventory_rules.storage_windows` holds two tables and `Recipe.storage_class`
says which row a dish takes — reported by the model exactly as
`long_oven_cook` and `bulk_prep_friendly` are. **It is deliberately not a
preset key**: a preset over one global could only ever have picked a different
wrong global, and a tightening-only lever (the `DietStyle.calorie_ceiling`
shape) fixes the stew case never and the rice case by accident.

**The tables are hours and every consumer holds a date.** A `SlotSpec` carries
a weekday name, a `WeekPlan` carries `week_start_date`, a `CookEvent` resolves
to a grid day; nothing anywhere stores a *time*, so no consumer can establish
that a Sunday cook eaten Thursday was inside 96 hours. `week.storage_day_gaps`
is the one place hours become days (`// 24`), and **no surface prints hours** —
the app does not know them. The day figure is deliberately not written into
config beside the hour figure: they are different claims and a reader would
take them for one. (Adding cook and eating *times* is the alternative and is
not taken — it would cost a clock question at every cook, every slot and every
freeze, in an app whose grid is day-granular everywhere else.)

##### Every default here fails short, which inverts the house rule

Everywhere else in this codebase an absent value resolves to *the behaviour
before the feature existed* — `long_oven_cook` defaults False,
`total_time_minutes` defaults to None meaning unknown, an absent `sourcing`
block emits nothing — and all of those are safe because being wrong costs a
worse meal plan. Here it costs a food-poisoning risk, so
`week.storage_window_for` resolves anything unrecognised to the table's
**shortest** row.

The precedent is on the record: `is_sunday_prepped` broke because a batch
anchor came back with both flags False despite the per-slot directive telling
the model to set one — a self-report the model simply dropped, on a field the
prompt explicitly asked for. **If a dropped `storage_class` resolved long, the
failure mode of a model forgetting a field would be a rice dish scheduled four
days out.**

Four details of the resolution are decisions:

- **`None` and `"default"` are different answers.** `"default"` means somebody
  looked and said "an ordinary cooked dish" (4 day-gaps); `None` means nobody
  said (2). The same distinction `total_time_minutes` draws between None and
  0, and an un-marked adherence row against a marked one.
- **A recognised class the *fridge* table does not name takes the `default`
  row**, so a stew gets 4 rather than being treated as unclassified. Only rice
  is exceptional there; the freezer table is the one with a row per class.
- **The shortest is `min` of the table, not the literal rice figure.** A
  shorter class added later has to pull the unclassified case *down* with it.
- **A configured table merges over the shipped one.** Replacing meant a config
  naming only `fridge: {"default": 72}` had no `rice_or_pasta` row, so a rice
  dish resolved through `default` and its window *lengthened* — §3's rule
  broken by a config that never mentions rice.

##### The ordering problem: tell the model the span, then check the answer

**The grid is built before any recipe exists.** `spread_batch` decides how far
a batch reaches during `apply_batch_selections`, and generation happens
afterwards, so at the moment the span is chosen nothing knows whether the dish
will be a rice tray bake. Planning short always is safe and useless (no batch
could reach past two days, which removes bulk cooking); planning long and
validating afterwards throws a 30s–3min call away to discover a constraint one
sentence would have stated.

So the grid is planned against the **default** window, and the dish-level
answer is enforced at the brief — the same shape `build_batch_roast_rule` /
`reject_misplaced_long_cook` already use, for the same reason:

- `planner.storage_spans(spec, config)` computes slot id → day-gaps once, off
  the settled grid, and rides on `config["storage_spans"]` in memory only —
  the channel `nudge_foods`, `inventory_ledger` and `target_locks` already
  use, covered by the rule that `save_config_keys` merges *named* keys. **One
  dict, three readers**, so the brief, the rejection and the favourite gate
  cannot disagree about a Thursday.
- `build_storage_rule` emits **only** when a span exceeds the short window, so
  a week whose cooks are all eaten inside it produces a byte-identical prompt
  to before this existed. Slots are grouped by the figure they need rather
  than reduced to the largest, because the validator judges each against its
  own span.
- `reject_short_storage_class` is the hard half, over the two-axis split
  `reject_misplaced_long_cook` uses — `DayRecipes` from its context's single
  day, `MealTypeWeekRecipes` over its own keys, one shared function so the
  axes cannot disagree.

**The batch anchors are emphatically *not* exempt here**, unlike in
`reject_misplaced_long_cook`. That rule exempts them because the *day*
judgement is wrong for food cooked before the week started; this one is about
the *window*, and their span is measured from prep day and is therefore
**longer**. It is the case that most needs the rule, and the third time this
off-by-one has had to be closed (`max_day_index`, then `storage_note`) — which
is why `storage_safety_errors` reads `prep_day_batch_slot_ids` too. That
function moved from `planner` to `week` for it, and is re-exported under its
old name.

**A pinned favourite is a third route to a long span**, beside a batch spread
and a hand-built chain, and the one route the pair above structurally cannot
cover: a favourite is never generated, so nothing briefs it and nothing judges
its response. `favorite_keeps_long_enough` is the gate, deliberately a sibling
of `favorite_fits_day` rather than a widening of it — attention and presence
are one axis, how long the dish keeps is another, and a stew passes both where
a rice tray bake passes only one. Without it the two halves would disagree in
the mirror image of the bug `favorite_fits_day` exists for. Every recipe in
the shipped catalog predates `storage_class`, so in practice an unclassified
favourite takes any slot eaten within two days of its cook and the long spans
go to generation, where the model is asked what the dish actually is.

##### `Recipe.storage_class` is a plain `Optional[str]`, not a `Literal`

A `Literal` would turn a vocabulary typo into a hard retry, and a retry is
30s–3min on a free route — the same argument that keeps `diet_styles` and
`sourcing` soft while `banned_ingredients` stays hard. An unrecognised string
is a species of unclassified and resolves short; the span validator still
rejects anything genuinely too short for its slot, so nothing unsafe rides on
the softness. It is also never **derived** — not from `total_time_minutes`,
not from `long_oven_cook`, and not from the ingredient list ("contains rice"
is a substring match away from calling a rice-vinegar dressing a rice dish,
and it fails in the unsafe direction). Same rule as `total_time_minutes` never
being derived from `prep_time_minutes`.

##### The freezer half is about quality, and nothing is ever removed

`week.freezer_months` and `week.freezer_quality_note` are the resolver
`data/freezer.json` will import when the freezer ledger lands — written once
here rather than twice, because writing it twice is how the two come to
disagree about a tub. Three rules it encodes:

- **Frozen food does not become unsafe, it degrades.** The fridge figures are
  safety and the freezer figures are quality, and the wording says which:
  "past its best", never "unsafe". Conflating them teaches a reader to ignore
  both.
- **An undateable lot is flagged, never assumed fresh.** A missing freeze date
  degrades to "no idea how old this is", and the conservative reading of that
  is not a number the function may pick.
- **Nothing is auto-removed.** On a hand-declared list that is the app editing
  your own statement of what you own.

### Nudging generation toward whole foods

`reference/whfoods.json` is a 130-entry corpus of nutrient-dense whole foods.
`select_nudge_foods` samples 12 of them **once per run** and puts them on
`config["nudge_foods"]`; `build_slot_brief` names that same dozen in every
slot's brief. Sampled per run rather than per slot deliberately — a different
set per recipe would push the week's flavour profile in twelve directions at
once, where one consistent dozen reads as a theme.

It is a priority, not a constraint, and the wording says so ("where flavour
profiles permit"). An absent or empty `whfoods.json` resolves to `[]`, which
`build_slot_brief` treats as "say nothing" — the same tolerance
`inventory_instruction` extends to an empty pantry list, so an older checkout
generates exactly as it did before this existed.

**The sample is filtered through `banned_ingredients` first, and that matters
more than it looks.** whfoods.json is a shipped, location-blind reference: it
lists Mustard greens, Halibut, Scallops and Cod beside broccoli and eggs, and
`build_slot_brief` puts the sample in *every* slot's brief under "prioritize
incorporating these" — a stronger and far more specific signal than any rule
in `build_generation_rules`. Unfiltered it nominated foods the config already
banned, producing a prompt that asked for cod two lines after forbidding it
and burning a retry whenever the model obliged. Filtering in
`select_nudge_foods` rather than pruning the corpus keeps whfoods.json usable
in full by a config with different constraints, and makes `banned_ingredients`
a **single lever**: ban an item and it stops being suggested as well as being
rejected. Matching mirrors `Ingredient.reject_banned_ingredients` —
case-insensitive substring, same accepted false positives — so the two can't
disagree about whether something is banned.

### Using up what's already in the house

`config.inventory_to_clear` is a list of things to cook through, and it takes
**two shapes, both legal**: a bare string is an unquantified item ("half a bag
of spinach") and `{"item": "chicken thighs", "quantity_g": 600}` is one the
ledger below can reason about. Both stay legal on purpose — a quantity is
genuinely unknown for some things, and requiring one would make the honest
answer unexpressible. `inventory_entries()` is the single parser, and it drops
a malformed entry with a warning rather than raising, the same policy
`split_targets` applies to a malformed `meal_overrides`: a config typo must
not cost a week of generation.

Grams rather than counts, for the same reason every other quantity here is
grams: the ledger is spent against `Ingredient.quantity_g`, and a tin that had
to be converted somewhere would be converted twice and differently. A tin of
tuna is about 95 g drained.

`inventory_instruction()` turns the list into one system-prompt line per day;
an empty list emits nothing, so the prompt is byte-identical to before when
the feature is unused.

It is deliberately a **priority, not a constraint** — the wording tells the
model to prefer these items where they fit and forbids it from bending a
meal's style, cuisine or macro budget to use one up. A model told it *must*
use an item will wedge chicken thighs into a breakfast shake.

#### The ledger: a count each stage spends and passes on

One tin of tuna could be written into five recipes in the same week, because
every meal type was handed the whole pantry and nothing tracked that it had
been spent the first time. That is the identical shape `sourcing.max_seafood_
meals_per_week` already fixed, and it is fixed the identical way: **no single
generation call sees more than its own axis**, so a quantity stated to all
four permits four.

`seed_inventory_ledger(config)` builds `{item: grams}` from the quantified
entries only — there is no number to decrement for the others, so a ledger
holding them would have to invent one. `generate_week_plan` seeds it once,
publishes it to each stage as `config["inventory_ledger"]`, and calls
`spend_inventory` on what that stage actually returned. A dict where
`seafood_used` is an int, which is the only real difference: an item runs out
on its own without taking the others with it.

Four things about it are decisions:

- **It never reaches disk**, which was the item's own second open question.
  A count that survived the run would start disagreeing with the actual shelf
  the moment you cook something without telling the app — the same "state able
  to disagree with reality" problem the shopping list's unpersisted checkboxes
  were designed around. It rides on `config` in memory, the channel
  `select_nudge_foods` already uses for `nudge_foods`, and is covered by the
  standing rule that `save_config_keys` merges *named* keys rather than saving
  the config it is handed. It would also have made generation a third writer
  to `config/`, where both existing ones persist a *standing* setting.
- **Matching is `shopping.ingredient_draws_on`**, not a third notion of "same
  food". `normalize_name`'s equality is right for combining shopping lines and
  too strict here — a pantry entry is written the way you say it out loud
  ("chicken thighs") while a recipe names the cut ("Chicken thigh fillets,
  diced") — so this is *containment* of the pantry item's words, guarded by
  matching departments and by any state the pantry item states. The guards
  matter more than the widening: an ingredient wrongly matched tells later
  meal types an item is spent while it is still in the fridge, silently
  withdrawing a priority the user asked for. Failing to match is the safe
  direction, because an unmatched ingredient leaves the item unspent, which is
  exactly how the pantry behaved before it had quantities at all.
- **Counted per cook event, not per slot that eats it** — the same call
  `is_seafood_meal` makes for the seafood cap. A bulk-cooked tray of thighs
  feeding three lunches came out of the fridge once, and `CookEvent.recipe` is
  already scaled to its full batch by `build_cook_event`, so the grams here are
  the grams the shopping list would buy.
- **Overshoot floors at 0 and is not recorded.** A recipe legitimately calls
  for more than the house holds, and the honest reading is "the item is gone",
  not "you owe 200 g". An exhausted item then drops out of the prompt line
  entirely rather than being announced as gone: the sentence is a list of
  things to reach for, and a spent item is not one.

`regenerate_single_meal`/`regenerate_single_day` seed no ledger, so every
entry is simply named there — the same call `build_seafood_limit_rule` makes,
and for the same reason: a single replaced meal has no week in front of it to
count against.

These items are still ordinary ingredients in the recipe, so a recipe's own
ingredient list still names them in full. **The shopping list no longer does**,
and this paragraph used to say the opposite: "subtracting the pantry from the
list is a separate change and a harder one — it needs the ledger to survive
the run, which it deliberately does not." The second half of that sentence was
right and the first half did not follow from it.

`shopping.apply_pantry` subtracts at **render time, from
`inventory_to_clear` itself**, and never from a stored count — so the ledger
still dies with its run and every argument above for that is untouched. The
two are different questions asked of one hand-edited list: the ledger says how
much of the pantry a *generated week* reached for and is spent as the stages
run, while the list asks what is still worth buying and is derived fresh on
every repaint. Nothing new is stored, nothing can drift from the shelf, and an
item you have used up leaves the list the same way it got on — by being edited
out of the pantry. It would also have made generation a *third* writer to
`config/`, which is the other reason a persisted count was never the answer.

The two entry shapes stay two shapes here too, because they are two different
statements. A quantified entry is **subtracted** — 600 g of chicken thighs
against an 800 g line leaves 200 g to buy, and against a 400 g one covers it
outright, whereupon the line is lifted onto `ShoppingList.pantry_covered` and
named in a sentence rather than silently vanishing (a stale pantry is the
failure mode here, and a line that disappeared with no trace is the one you
could not notice was wrong). An unquantified one is **annotated only**: there
is no number to subtract, and inventing one is exactly what `inventory_entries`
keeps both shapes legal to avoid.

Three rules, and the second is the one that is not obvious:

- **One ingredient draws on at most one entry** — the rule `spend_inventory`
  already states, for the same reason: two entries both matching a line would
  each be charged for it.
- **An entry is a budget across the whole list, not a per-line discount.**
  600 g against a week with two chicken dinners covers the first and part of
  the second, in walk order, which is what the fridge will actually do.
- **A remainder is a remainder, however small.** 605 g needed against 600 g
  held leaves a 5 g line. A threshold below which the leftover was "close
  enough" would be a number nobody chose — the standing answer everywhere else
  the arithmetic fails to reconcile.

Matching is `ingredient_draws_on`, the same containment-plus-guards call the
ledger makes, so the list and the ledger cannot come to disagree about whether
"chicken thighs" covers "Chicken thigh fillets, diced" — or about "chicken
broth", which the department guard keeps out of both. `apply_pantry` is
applied by every caller that actually holds a pantry: `PlannerState.
shopping_view` (from the *staged* rows, not `config`, because a row typed into
the drawer moments ago is the honest statement of what is in the house), the
CLI's `window_shopping_list`, and both export builders. A caller with no
pantry passes nothing and gets the list the recipes need, byte-identically to
before this existed.

The drawer's Pantry clear section is now a row editor (item + grams + remove)
rather than the free-text chip box it was: a chip cannot hold two fields.
Same shape as the training editor beside it, which is that file's established
pattern for an editable list of records. `PlannerState.pantry` carries
`{"item", "quantity_g"}` rows accordingly, seeded through the same
`inventory_entries` parser generation reads, so the drawer can never show a
row the ledger would drop. Typing in a row refreshes nothing — nothing on
screen reads a row's contents, and repainting would rebuild the field the
cursor is in; adding or removing one refreshes `"pantry"`, its own topic,
because that changes the count the staged bar shows.

### Rejection capture

Hitting the regenerate icon on a meal card was, until phase 4 of
`ui-redesign.md`, a pure discard: the recipe vanished and an
identically-briefed call replaced it, with nothing learned from the fact
that a real suggestion had just been thrown away. Favourites already capture
the positive signal (`select_favorite_assignments`); this is the negative
one, and per that phase's own framing it is the most valuable thing in it —
everything else in phase 4 is UI, this is the app actually learning
something.

**What it stores, and why a new file.** `planner.RejectionEntry` (`date`,
`slot_id`, `recipe_name`, `reason` — one of `too_much_prep`/`dont_fancy_it`/
`had_it_recently`/`wrong_for_slot` — and `marked_at`) is appended to
`data/rejections.json` via `PlanRepository.save_rejection_entry`/
`load_rejections`, a plain event log rather than an upsert-by-date table:
regenerating the same slot twice must record twice, not overwrite, which is
exactly why `_append_rejection` (unlike `_upsert_dated_entry`) carries no
merge key at all. This is a genuinely different signal from adherence
(`AdherenceEntry`, whether a *served* plan was eaten, skipped or swapped —
see "Whether the plan actually happened") — a rejection happens *before* a
recipe ever becomes the plan — and the two must not share a file for the same
reason `weigh_ins` and `daily_actuals` don't: two different signals writing
the same key would silently overwrite each other with no way to tell which
won. Now that both exist, note also that they differ in whether a repeat is
an event or a correction: two rejections on one slot are two facts and are
appended, where re-marking a meal replaces the mark.

**It is soft guidance, exactly like `diet_styles` and `sourcing`, never a
validator.** `planner.build_rejection_rule(config)` reads
`config["rejected_preferences"]` (a list of `RejectionEntry` dicts, injected
into `config` the same way `select_nudge_foods` injects `nudge_foods` — see
below) and asks the model to avoid repeating the named dishes and to weigh a
reason that recurs across several entries (a run of "too much prep" answers
is a hint to lean simpler for that meal type generally, not merely to avoid
those specific dishes again). It sits in `build_generation_rules` right
after `build_diet_style_rule` — "beside banned_ingredients and diet-style
principles" — because a rejection is a preference the same way a diet style
is: a rejection that hard-failed a response would cost a full 30s-3min retry
for what is, at worst, a repeated dish, and `banned_ingredients` already
owns the "must never appear" case.

**Loaded at the top of all three generation entry points**
(`generate_week_plan`, `regenerate_single_day`, `regenerate_single_meal`),
not only the full-week path — a regenerated meal is exactly the moment the
signal was just captured, and it has to reach the very next call, not wait
for next week's run.

**Captured alongside the retry, never in front of it.** `ui_generation.py`'s
`regenerate_meal` already holds the discarded recipe's name at the exact
moment it's replaced; once the new one lands, a small `fixed`-positioned
prompt (four reason buttons, real `on_click` handlers — see below) offers to
record why, and an ignored prompt records nothing. It is deliberately not
`ui.notify`'s `actions` option: that only ever forwards to Quasar as
serialized JSON, so a Python click handler has nothing to bind to in this
NiceGUI version — checked directly rather than assumed. It is also
deliberately not `ui.dialog`, which is modal (a dimmed backdrop) and would
contradict "never in front of it" the same way a blocking confirmation
would. A plain `fixed` div is a real element tree with ordinary `on_click`
callbacks, and it floats regardless of where in the page it's built, the
same reason `ui.header`'s own fixed positioning doesn't care about DOM
nesting. Scoped to the per-card regenerate and the swap-with-favorite
dialog, not the day-level regenerate — a day regenerates four recipes at
once, which doesn't fit one four-option prompt naturally, and a card that
was previously NOT_GENERATED (a prior failure, not a real suggestion) offers
nothing to name as rejected.

**The swap dialog was the original gap, closed after the fact.** A favorite
swap is exactly as deliberate a "not this one" as the regenerate icon —
arguably more so, since the user picked what replaces it rather than trusting
another roll — but `offer_rejection_prompt` started life as a closure-local
helper inside `build_generation`'s `regenerate_meal`, unreachable from
`ui_cards.py`'s `confirm_swap`. It is now exposed on `GenerationHandles`
instead, and `confirm_swap` captures `state.swap_target.recipe` (the outgoing
recipe, read before the swap mutates state) and calls it the same way
`regenerate_meal` does. The prompt's own copy changed from "Why regenerate
…" to "Why replace …" to read correctly from both call sites.

#### The decay: two signals, two windows

Shipped unbounded on purpose — the phase that added rejection capture said
explicitly not to settle the decay question, only to raise it, because
capping to "most recent N" would have been that same policy picked silently.
It is now settled, and the answer is that **there were two questions, because
the rule was always carrying two signals**:

| | what it is | window |
|---|---|---|
| the **dish list** | a veto on one recipe | `planning_rules.rejection_decay_days`, per reason (21–180 days) |
| the **reason tally** | a standing statement about how you want to eat | `planning_rules.rejection_reason_window_days` (180) |

**Per reason rather than one number**, which is the same argument
`favorite_reuse_days` already makes for its own split — the four reasons are
not the same kind of statement. "Had it recently" is self-resolving (the dish
stops having been had recently whether or not anything honours the entry) and
expires soonest at 21 days; "wrong for that meal" is structural — a curry is
never breakfast — and barely decays at 180. A dislike honoured forever would
starve the rotation the same way an "unused in the last N" rule starves the
tail of a list (see `planner.next_choice` on why it is strict LRU instead),
which is the failure this bounds.

**The tally's window is deliberately the longest of the four, so it outlives
the names it was counted from.** A dish name should expire; a run of "too much
prep" answers is the more valuable half and should not. `active_rejections`
and `recurring_rejection_reasons` are the two halves, both pure, both reading
the same list.

**The tally is now counted in Python, and that is a consequence of the split
rather than a flourish.** The old rule handed the model the whole list and
asked it to notice a repeated reason itself. Once the halves have different
windows the model only ever sees the shorter one, so it cannot do that
counting — it would be weighing a subset while being told to weigh the whole.
`REJECTION_REASON_GUIDANCE` is what it is told instead: reason -> the standing
instruction a *run* of that answer implies, split from
`REJECTION_REASON_LABELS`, which says what one entry was about.
`REJECTION_REASON_SIGNAL_MIN` (3) is what counts as a run — two is a pair of
unrelated bad nights — and is a module constant rather than a third config
key, because the two windows are the policy and the threshold is arithmetic
in service of them.

Three things about it are decisions:

- **No storage change, and none was needed.** Every entry already carried its
  `date`, so the whole policy lands in `build_rejection_rule` and the two pure
  functions beside it. `data/rejections.json` is untouched and unmigrated.
- **An entry that cannot be dated, and a reason with no window, are both
  kept.** Neither is reachable from the app — `RejectionEntry.reason` is a
  `Literal` and the UI stamps `date` from the clock — so both mean a
  hand-edited file, and discarding a preference the user actually stated
  because the file knew a word the config did not is the worse failure. An
  unrecognised reason is still kept out of the *tally*, since
  `REJECTION_REASON_GUIDANCE` has no standing phrase it could be told to
  follow.
- **`build_rejection_rule` takes `today`**, defaulting to the clock — the same
  seam `select_favorite_assignments` uses, and the reason the tests can age an
  entry without touching the clock. The rejection tests previously carried
  fixed date literals against a live clock and would have started failing
  about six weeks later; see "Tests" for the standing rule that caught it.

Once nothing survives either window the rule emits `""` again, so a list that
has fully aged off produces a byte-identical prompt to before the feature
existed — the same convention every rule in `build_generation_rules` follows.

### Whether the plan actually happened

Nothing observed whether a planned meal was eaten, skipped or swapped. The
nearest thing was the swap-with-favourite flow, which changes the **plan**
rather than recording a deviation from it — so a week could be planned,
cooked around and half-ignored, and the app's own record of it stayed the
week it had generated.

`data/adherence.json` is where that is recorded now: two lists, written by
`PlanRepository.save_meal_adherence`/`save_workout_completion` and read by
`load_adherence`, marked from the Daily View (and the day inspector, which
shares its renderers).

| list | row | keyed on |
|---|---|---|
| `meals` | `planner.AdherenceEntry` — `date`, `slot_id`, `status` (`eaten`/`skipped`/`swapped`), `marked_at` | `date` + `slot_id` |
| `workouts` | `planner.WorkoutCompletion` — `date`, `session_id`, `session_type`, `completed`, `source`, `marked_at` | `date` + `session_id` |

**Three statuses rather than a boolean**, because the two failures are
different in kind and the difference is the reason for recording anything: a
*skipped* meal is a day that came short of a target it was planned to hit,
where a *swapped* one is a day fed by something else. An "eaten?" flag
collapses those into one "no", and the 7-day adherence readout this exists to
feed could not then tell a missed dinner from a dinner out.

**One file with two sections, not the two files `future-ideas.md`'s 5b
proposed.** The part that matters is that the two are separate *lists* — the
call this codebase has now made five times (`weigh_ins` vs. `daily_actuals`,
then `readiness_log`, `activity_log`, `rejections.json`), for the reason each
of those records: two signals sharing a key overwrite each other silently,
with nothing able to say which won. Neither folds into `daily_actuals` for
exactly that reason. What they *do* share is a question — did today's plan
happen — so they are always read together to answer it, and a second path, a
second loader and a second pair of save methods would be two of each for one
answer. `biometrics.json` is the precedent for the shape: four signals, four
lists, one file.

**The key is `date` plus one more field, unlike every biometric section.**
`ADHERENCE_SECTIONS` is the manifest naming that second field per section,
which is what lets one `_upsert_adherence` serve both. `date` alone would let
Thursday's lunch overwrite Thursday's dinner; `slot_id` alone repeats every
seven days, since it is a weekday name.

**A mark is an update, not an event** — the one thing separating this from
`save_rejection_entry`'s append. Two rejections on one slot are two facts;
re-marking a meal is a correction, and two rows disagreeing about Thursday's
dinner leave nothing able to say which is current.

**Un-marking deletes the row.** Absence and a status are genuinely different
answers — "nobody has said" versus "somebody said" — so there is no fourth
`unknown` status every reader would then have to treat as absent anyway.
Clicking the status a slot already carries clears it, which is what makes
three buttons a complete control rather than three one-way doors; and
clearing something never marked writes nothing at all, so an untouched
checkout stays distinguishable on disk from a marked-then-unmarked one.

**A day with no calendar date cannot be marked.** `slot_id` is a weekday
name, so without the loaded plan's `week_start_date` there is no key to file
a mark under and none to read one back — the same pre-migration tolerance
`logged_actuals_for` already draws for such a plan, reached from the other
direction: there it costs a readout, here it costs the affordance. Marks are
matched by **date**, never by weekday, for that same reason.

**A skipped slot is not markable**, and is excluded from the day's
denominator: nothing was planned to be eaten there, so "did you eat it" has
no answer, and counting it would make every week with a skipped snack read as
permanently 3-of-4 adhered. A leftover *is* markable — it is a meal the week
intends you to eat, whoever cooked it — and so is a failed one, which is
exactly the case where the answer is interesting.

**It is deliberately not an input to generation.** A rejection is a
preference about food, so `build_rejection_rule` sends it to the model; a
mark is a record of a day, and what to do with a run of skipped Thursdays is
a product question rather than something to answer by quietly adding a fourth
soft rule to every prompt. The Insights destination now *reports* the marks
(see "Insights: five readouts" below); what it deliberately still does not do
is feed them back into generation. Nothing in
`planner.py`'s generation path reads `adherence.json`.

#### The workout half is mostly derived, and only the gap is stored

`future-ideas.md` 5b proposed a `data/workout_log.json` carrying `scheduled`
and `completed` per session. **v0.33.0 made most of that derivable**:
`activity_log` records what Garmin actually did on each date, so "did the
declared session happen" is a question about two lists that already exist.

`nutrition_engine.match_recorded_sessions` is that read — the per-date
counterpart to `propose_training_schedule`'s four-week aggregate, pure over
the same two inputs and in the same module because both speak
`GARMIN_SESSION_TYPES`' vocabulary, and a second module knowing how an
activity row maps onto a declared one is a second chance to disagree about
it. It stores nothing.

Three things about the matching are decisions:

- **Type and date are the claim; the clock only breaks ties.** A 06:30
  session started at 07:10 is the same session, and a tolerance window would
  have to pick a number that is wrong for somebody. So a matching type on the
  matching date always counts.
- **Each declared session claims the nearest *unclaimed* recording**, so a
  day declaring two lifts against one recorded one leaves the second honestly
  unrecorded rather than silently confirmed by the first.
- **An unmapped activity answers nothing.** `GARMIN_SESSION_TYPES` has no
  catch-all, so a yoga class arrives with `session_type: None` — skipped here
  exactly as `propose_training_schedule` skips it, and for the same reason: a
  yoga class is not evidence that the declared lift happened.

**Only the gap is stored.** A `WorkoutCompletion` is written *only* for a
session the watch never recorded — a lift on a day the watch was flat, a
class with no device. `PlannerState.mark_workout` refuses one for a recorded
session rather than merely not offering the button, because a stored
`completed` row beside `activity_log` would be a second answer to one
question, free to disagree the moment a re-sync changed either. When both do
somehow say yes — reachable only when a later sync finds a session already
marked by hand — Garmin wins, since the watch's own record is the better
evidence and the stale manual row is the one that should stop being cited.

`ui_state.workout_marks_view` is where the stored half is laid over the
derived one — the engine measures, the view model decides what a reader is
told, the same layering `sync_status` and `adaptive_tdee_view` already use.
A day with no calendar date reports its sessions as neither recorded nor
markable rather than as "not done", which would state as fact something never
actually checked.

#### The affordance, and the trap it had to avoid

`ui_adherence.py` is the one concern — a `build_adherence(ctx)` factory
holding two click handlers — and it is its own module rather than more
handles on `CardHandles` because two unrelated surfaces raise these marks,
and only one of them is a card.

**The mark row is a *sibling* of the clickable body**, the pattern
`ui_cards.meal_card` established. `ui_today.today_card` used to put its click
handler on the whole card, which was correct while nothing on the card was
clickable in its own right; a mark button under that handler would open the
recipe dialog on top of the mark it had just recorded. The handler moved down
onto a body element, and the header row — meal type, marks, status badge —
now sits beside it.

**Glyph carries all of it; no hue does.** Every colour in the palette already
means at most two things (the `ui-work` skill's table), and emerald — the
obvious pick for a tick — is the cook status, so a green check on a card
would read as a fifth slot state. `check_circle` / `remove_circle` /
`swap_horiz` distinguish the three marks, `check_circle` versus `task_alt`
distinguishes a Garmin-recorded session from a hand-marked one, and set
versus unset is the fill-and-weight distinction `bookmark`/`bookmark_border`
already draws for a favourite.

**Marks persist on click and do not stage.** Every grid edit in this app
waits for Save because it is an input to the next generation; a mark is not,
so there is nothing for the staged bar to hold and a tick that vanished on
reload would be a control with no effect. That is the same test
`set_target_mode` and `accept_training_proposal` pass, arrived at from the
storage side rather than the config one — and unlike those two, this writes
to `data/`, so it does not touch the "two places write to `config/`" rule.

The repaint topic is `"adherence"`, its own: the only two sections drawing a
mark are the Daily View and the inspector, where `"plan"` would additionally
rebuild the 28-card canvas, the telemetry header and the shopping panel on
every click of a tick.

The day's line — "2 of 3 marked" — is silent until something is marked. The
whole week is unmarked until somebody starts, and a counter reading zero on
six days out of seven is a UI element announcing that a feature exists rather
than reporting anything.

### Insights: five readouts, each gated on its own precondition

The Insights destination was a 66-line honest empty state for five releases,
because CHANGE-QUEUE.md's trend-charts item is blocked on runtime data rather
than on engineering. **It still is, and that is the point of how this
shipped**: measured the day it landed, the file held 6 weigh-ins across a
5-day span against a floor of 7, and 5 logged days against the item's own
suggested 14. The item's trigger was not met and the charts were built
anyway, because waiting had a cost the stub itself demonstrated — it printed
the counts and named the rule without evaluating it, which is exactly the
failure v0.30.0 fixed for the adaptive estimate.

So **the page evaluates rather than describes**. Each of the five readouts is
an `InsightPanel` from `ui_state.py` carrying a `state`, a `headline` and a
`detail`, and `ui_insights.py` prints the verdict and draws the chart only
where `drawable` is true. The page fills itself as rows land instead of
waiting for another release to notice they have.

| readout | drawn from |
|---|---|
| weight against target | `biometrics.weigh_ins` + `user_profile.target_weight_kg` |
| the weigh-in table | the same windowed rows as the chart above it |
| planned against logged | `meal_history.json`'s per-day `targets` x `daily_actuals` |
| macro accuracy | the same pairing, as means, `MACRO_KEYS` only |
| adherence tiles | `adherence.json`'s `meals` + `workouts`, and `activity_log` |

**Four states, not a `ready` flag**, on `AdaptiveTDEEStatus`' reasoning:
`INSIGHT_EMPTY` (nothing recorded — sync something), `INSIGHT_SPARSE` (fewer
than `INSIGHT_MIN_POINTS`, so no line is drawn — keep going),
`INSIGHT_THIN` (drawn, below `INSIGHT_THIN_POINTS`) and `INSIGHT_READY`.
Empty and sparse spell identically as a missing chart and have different
fixes, which is the whole reason they are separate.

**Thin is drawn, because the item's worry is the axis and not the points.**
"A 14-day chart against 5 points is thin; a 30-day one is misleading" is a
statement about the frame: a window anchored on the data's own last row and
captioned `6 point(s) across 5 day(s)` cannot mislead the way a fixed 30-day
axis with six dots in one corner does. Anchoring on the data rather than on
today is the same rule `measure_adaptive_tdee` already applies.

**Two pairings, one join.** `paired_intake_days` is the only place planned
meets logged, and both charts that need it call it, so they cannot disagree
about which days count. It takes two rules from elsewhere rather than
inventing them: the *last* history entry for a date wins (a regenerated day
legitimately has two — the same rule `meal_adherence_view` applies to a
duplicated mark), and a zero-calorie logged row is not a pairing, which is
what `planner.logged_intake_for` already refuses to substitute for a plan. A
*partly* logged day is kept and reads as a shortfall — honest, and the same
reading `reconcile_adaptive_tdee` warns systematic under-logging produces.

Four decisions inside the charts are worth knowing, all of them the same
decision in different clothes — **a chart may not claim more than the data
supports**:

- **The target line is drawn only when it is in view.** The weight chart's
  y-axis is scaled to the weigh-ins, because a zero-based one renders a real
  0.6 kg week as a flat line 99 kg above the origin — which puts an 80 kg
  target outside the plot, where ECharts clips it, leaving a chart headed
  "weight against target" with no target on it. Widening the axis to include
  it flattens the trend instead. So `WeightTrendPanel.target_in_range` gates
  the line and the caption states the gap either way; the line appears on its
  own as the scale approaches it.
- **Macro accuracy is a percentage axis.** 2000 kcal and 79 g of protein
  cannot share a value axis, and four separate charts would say less than
  one. The dashed rule at 100% is the plan.
- **Fibre is on it, and only for the days that can carry it.** It was
  excluded while it had no planned figure to divide by. Now it has one, and
  what is still true is that a history entry written before the target states
  no `fiber_g` — a mean over those days would read as a 0 g plan massively
  overshot rather than as a plan nobody made. So the row appears only when
  **every** paired day in the window states one, the same all-or-nothing
  precondition `WeightTrendPanel.target_in_range` applies to its own line.
- **The adherence percentage's denominator is *marks*, and says so in
  words.** The plans those dates were generated against are gone from
  `week_plan.json` the moment a new week is generated over them, so "of meals
  planned" would be a divider under a number nobody counted. The tiles also
  have no `INSIGHT_MIN_POINTS` gate at all: a count of three marks is a true
  statement about three marks, where a line through three points is a claim
  about a direction.

**The adherence tile is the thinnest of the five and its empty state says
why.** Unlike a weigh-in or a Cronometer row, a mark exists only because
somebody clicked it — the series does not accumulate because the sync job
runs — so "have I been marking" is its own precondition, and reporting it as
late data would be wrong.

**No chart introduces a hue.** `CHART_MACRO_COLOURS` is `MACRO_TINTS` in the
units ECharts takes (categorical: which macro), a logged bar takes its
`BAND_COLOURS` fill (semantic: how that day went, the same `macro_band` call
the telemetry header makes about the same day), and everything structural is
slate. The planned series is the *dashed* one throughout — the fill-vs-outline
distinction `bookmark`/`bookmark_border` already draws, reached for because
both series are slate and a second hue would have to mean something.

**A legend swatch that disagrees with its mark is worse than no legend.**
ECharts takes the swatch from `itemStyle`, not `lineStyle`, so a line
coloured only through the latter is labelled with a chip from ECharts' own
default palette — measured, and it drew a white trend line with a blue chip.
The intake chart has no legend at all for the harder version of the same
problem: its bars are banded per day, and one chip cannot stand for five
different fills without being wrong about four of them, so the encoding is
in the caption instead.

`nutrition_engine.measure_weight_trend` is the engine half, and it exists so
the chart and the estimate cannot disagree about a slope. It returns the raw
weigh-ins, `smooth_series`' line through them **and** the least-squares rate,
which are three different things the module is emphatic about keeping apart:
smoothing is for the eye, and reading a rate off a smoothed series
understates a noise-free decline by 26%. Two details it does not share with
`measure_adaptive_tdee`: a 30-day default window (a chart wants points, the
estimate wants intake to pair against), and the raw slope's own sign —
**negative while losing**, where the estimate negates it into "kg lost per
day" because the energy formula is defined that way. Both are named for what
they carry, because inverting one for the other doubles the error rather than
cancelling it.

It is also the reader `smooth_series` never had: that function's docstring
has said "for display — the weight-trend line a UI draws" since it was
written, and nothing drew one. That is the same fetched-or-built-and-never-read
pattern this queue has now closed three times from the storage side, seen
from the other end.

The panel is `@ui.refreshable` and registered on `"plan"` **and**
`"adherence"` — generation appends the history entries the intake charts pair
against, and marking a meal is the only thing that fills the tiles. That
makes Insights the third member of the `"adherence"` topic, which was
documented as unable to grow one.

### Biometric sync — Garmin Connect and Cronometer

`src/integrations/sync_service.py` fills the four lists `biometrics.json`
holds, with no phone-side app in the loop:

    ./venv/bin/python src/integrations/sync_service.py --sync-garmin
    ./venv/bin/python src/integrations/sync_service.py --sync-cronometer --date 2026-08-16

`GarminSyncService` writes `weigh_ins`, `readiness_log` and `activity_log`,
`CronometerSyncService` writes `daily_actuals`. Three of the four go through
`LocalJSONRepository`'s existing upsert-by-date methods and the fourth
through `save_activity_entries`, which replaces a date rather than merging
it — see below. Neither service invents storage, and the CLI reports each
source independently: a Garmin outage must not cost a Cronometer sync that
would have worked, the same policy as "a failed meal must not fail the week".

Nine things here are decisions, not detail:

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
  later as an adaptive loop that never adapts. `fiber_g` is the fifth
  column captured and follows the same rule; **an entry in
  `CRONOMETER_MACRO_COLUMNS` has to assert that something reads it**, which
  is what keeps sodium, potassium and the micronutrients out. See "The one
  number that now has a measured counterpart".
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
  a sleep score, sleep hours, an HRV figure and a bucketed word, and stores
  them in `readiness_log`. A sleep score is a unitless 0–100 index and HRV is
  milliseconds, so no conversion of either to kcal could be legitimate — the
  separation is enforced by these being different methods writing a different
  list, not by a comment. Nothing in `nutrition_engine` or
  `apply_training_adjustments` reads that list; whether a readiness figure
  should *adjust* a target is a separate and much larger question
  (CHANGE-QUEUE.md's morning-readiness item, 5d's second decision), and this
  is deliberately only the storage-plus-one-read-surface half of it.

  **This was fetched on every sync and thrown away for months** — printed by
  the CLI, kept nowhere — which is what made "I would expect to see the sleep
  data downloaded previously" exactly right. It is a third list rather than a
  few more keys on the weigh-in row, for the reason the section below gives
  for keeping `weigh_ins` and `daily_actuals` apart: a scale and a watch can
  both report for one date, and one merged row would let a partial answer
  from either blank the other's.

  **HRV used to be withheld on purpose**, being the metric most likely to be
  mistaken for a recovery *cost* by a future caller looking for one.
  Withholding it protected nothing the list separation doesn't already
  protect, and cost the number a readiness read is actually about. It comes
  from `get_hrv_data`'s `hrvSummary.lastNightAvg` — the method name checked
  against the installed garminconnect (0.3.10) rather than copied from an
  example, per the standing rule about this dependency, and `lastNightAvg`
  rather than `weeklyAvg` because the row is keyed by date and a weekly figure
  would store one number under seven of them.

  **Sleep and HRV are two endpoints, caught separately.** Either fails on its
  own — a watch worn with HRV still baselining is a real state — and one
  `try` around both would discard a good HRV reading because the sleep call
  failed. `save_readiness_entry` merges by date, so the half that failed lands
  on a later re-sync without disturbing the half that didn't.

  **One checkpoint per source, not per list**, which makes
  `BIOMETRIC_SECTION_SOURCES` one-to-many and cost two readers their
  one-section-per-source assumption: `get_sync_date_range` now folds a
  source's lists together before taking its latest date (ranking them apart
  would put the emptier list into its `min` and re-walk days Garmin has
  already answered for — the same re-fetch-forever bug `sources` was added to
  fix, by a second route), and `ui_state.sync_status` names its cards by
  section, or two of three would read "Garmin". The one wrinkle, stated on the
  page rather than papered over: a date checked before `readiness_log` existed
  reads as "checked, nothing recorded" for readiness. `--date` re-syncs it,
  since Garmin keeps the history.

  **That wrinkle stopped being hypothetical, and it was `activity_log` it
  cost.** Measured on 2026-09-01: the list held **zero** rows against a
  Garmin checkpoint of 2026-08-31, while the account had 27 recorded
  activities in the five weeks behind it. Nothing was wrong with the
  mapping — all 27 `typeKey`s resolved through `GARMIN_SESSION_TYPES` and
  every row was `_storable`. Those days had simply never been asked for:
  `save_activity_entries` shipped in v0.33.0 at 20:46 on 2026-08-28, by
  which time the checkpoint had already marked every earlier date "asked",
  and `get_sync_date_range` never revisits a checkpointed date. A `--date`
  backfill across the 28 days `propose_training_schedule` reads restored 23
  rows over 16 dates and turned its answer from "nothing recorded" into
  four proposals.

  **Diagnose this by fetching before the filter, not by reading the
  filter.** A checkpoint that hides the history and a `_storable` that
  rejects everything are indistinguishable from the stored file, and only
  one of them is a code bug. The CLI already prints every activity with
  `(not stored: unmapped type or no start time)` beside the ones it drops,
  so one `--sync-garmin --date <a day you trained>` separates them. Guessing
  gives the wrong answer here: an empty `activity_log` looks exactly like a
  mapping gap and was not one, and "fixing" the mapping would have added
  entries for modalities that were already mapped.
- **Recorded activity is stored now, and only what can be read back.** It
  was fetched on every sync and printed for months, which is the same shape
  v0.29.0 closed for sleep; `activity_log` exists because
  `nutrition_engine.propose_training_schedule` reads it (see "Proposing the
  week you actually trained"), and its arrival is what finally gives
  `net_calories` a consumer. `fetch_activities` is unfiltered where
  `fetch_cardio_activities` is not — a `strength_training` session is the one
  a schedule proposal most needs, being what pins a breakfast shake — and
  `fetch_cardio_activities` is now a filter over it rather than a second
  fetch, so the two cannot disagree about a session's duration or its
  discount. It is also the one section holding several rows per date, so it
  is replaced per day rather than merged.
- **Absent metrics are omitted, never zeroed.** `save_biometric_entry` merges
  on `date`, so a scale that reported only weight must not send
  `body_fat_pct: 0.0` and overwrite a real reading. `_prune` drops the Nones
  and `has_measurements` decides whether a row is worth storing at all —
  count the *measured* keys, not `len(entry)`, which an earlier version did
  and which the `source` tag alone was enough to fool: a day the scale never
  saw was written as a weigh-in with no weight, and `get_latest_biometrics`
  handed that empty row back as the newest reading.
- **Cronometer is fetched a span at a time; Garmin a day at a time.** One
  Cronometer day is not one HTTP request. `CronometerClient.export_raw`
  calls `authenticate()` — which, even resuming a saved session,
  re-discovers the GWT build hashes and re-mints an auth token — and then
  mints a *second* token before the export GET: roughly five requests warm,
  seven cold. `CronometerSyncService` also built a fresh client per day, so
  nothing was reused across a range, and a six-day catchup spent about
  thirty requests to retrieve six CSV rows. Against an account that
  rate-limits, that walk was itself provoking the 429s `_is_rate_limited`
  exists to survive. The export endpoint takes a real `start`/`end` span
  and returns a row per day, so `fetch_range_summaries` asks once and
  `_daily_summary_row` folds the one CSV into each date —
  `sync_cronometer_range` no longer loops over `sync_cronometer` at all,
  and the two share `_persist_cronometer_day` so they can't disagree about
  when a row is worth keeping. Garmin keeps its per-day loop: it has no
  comparable limit, and the loop buys real per-day failure isolation.

  Two consequences. **One request has one outcome**, so a Cronometer
  failure is no longer isolable to a single date — it is reported against
  the first one, nothing is checkpointed, and `get_sync_date_range` finds
  the whole span still missing next run. That costs little, since every
  Cronometer failure seen in the wild was an account-level 429 that already
  short-circuited the walk. And **`_daily_summary_row`'s undated-row
  fallback is only sound for a single day** — "a range of one has nothing
  else it could be" stops being true the moment one CSV is folded into
  seven dates, where taking the row would copy the same figures onto every
  one of them. `single_day_request` is the guard, and `TestCronometer
  RequestCost` pins both halves.

- **`--date` means that day, not "catch up to that day".** Catchup defaults
  to on, which is right for the bare, scheduled-sync shape where a missed
  day must not be lost forever — but `--date` also defaulted to today, so
  nothing downstream could tell a named day from an unnamed one, and
  `--sync-cronometer --date 2026-08-26` announced "Catching up 6 missing
  day(s)" and fetched five other days as well. `--date` now defaults to
  `None` and `--catchup` to `None`, so the resolved default is "catch up
  unless a date was named"; an explicit `--catchup` still backfills up to
  the named day. Combined with the span fetch above, asking for one day is
  now one export request.

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

- **The saved Cronometer session expires, and the client cannot tell** — so
  `CronometerSyncService._fetch_rows` clears it and retries **once**. This is
  a bug in `cronometer_mcp` that no upgrade of ours can reach, and the failure
  is worth spelling out because it looks like exactly what it is not.
  `_restore_session` validates a resumed session by minting an auth token, but
  GWT-RPC answers an expired session with HTTP **200** and a *serialized
  exception* in the body — so `raise_for_status()` passes, and the token regex
  (`re.search(r'"([^"]+)"', ...)`) returns the first quoted string in that
  payload, which is the exception's own class name and type hash:
  `com.cronometer.shared.user.exceptions.NotLoggedInException/844385496`. It
  is non-empty, so the restore is judged a success, `authenticate()`
  short-circuits without ever logging in, and that string is sent as the
  export nonce. The 403 that comes back quotes the exception at you in the
  URL, which reads as a credentials problem and sends you to re-check a
  `.env` that was right all along. Seen in the wild on 2026-09-01 against a
  15-day-old session file.

  Three things about the recovery. **It can only be recognised after the
  fact** — nothing below `_fetch_rows` can tell that token from a real one and
  nothing above it can see the token at all, so there is no pre-flight check
  to add, only a 403 to interpret. **Exactly one retry, and only when there
  was a session file to clear**: a second attempt is another full login, about
  seven requests, and a run that had no cached session already logged in fresh
  — so its failure is a real one (wrong credentials, a lapsed subscription)
  and retrying would double the cost of every genuine auth failure against the
  endpoint whose 429s `_is_rate_limited` exists to survive. That is also why
  `_is_stale_session` is 403-and-the-marker and deliberately **not** any
  auth-shaped 4xx: a re-login is the worst possible response to a 429.
  And **the path is asked of the client, never spelled out here** —
  `_session_cache_path` reads `_cookie_path` off a constructed
  `CronometerClient` (construction issues no requests) rather than
  re-deriving `CRONOMETER_DATA_DIR` and its default, because a second copy of
  that rule would go stale the moment upstream moved either half, and a
  guessed path here does not fail to persist a token — it deletes somebody's
  file. `getattr` guarded, the same shape `GarminSyncService.client()` uses
  across garminconnect's two lines.

  `_stale_session_hint` is the `_rate_limit_wait_hint` of this failure and
  exists for the same reason: the raw message is unreadable, and the obvious
  reading of it is wrong.

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

#### Nothing syncs from the app — `scripts/sync.sh` does, on a schedule

`./scripts/sync.sh run` is the CLI above with both sources and no `--date`,
and `./scripts/sync.sh install` writes a launchd agent that runs it daily
(07:30 by default; `MEALS_SYNC_HOUR`/`MEALS_SYNC_MINUTE` at install time),
logging to `logs/sync.log`. `uninstall` and `status` do what they say —
`status` also prints each source's stored checkpoint, which is the same field
the Settings dialog reads.

**A bare run targets yesterday, not today**, and that is a correctness rule
rather than a scheduling preference. A day is only complete once it is over:
a 07:30 fetch asks about a half-empty day, and — the part that actually cost
data — its checkpoint then marks that day done, so everything logged or
recorded later that day is stranded forever, since `get_sync_date_range`
never re-requests a checkpointed date. An afternoon walk and an evening
Cronometer entry were both invisible to a same-day sync for exactly this
reason; see the `activity_log` incident above, whose second half this is.
`--date` still means the day it names.

**Neither the server nor any page ever triggers a sync, and that is the
decision rather than an omission.** The question behind it — should starting
the server sync? — had three real answers, and this is the one taken:

- **On server start**, as a fire-and-forget task. Simplest, and matches how
  the app is used, but it puts a Garmin outage and a rate-limited Cronometer
  inside the UI process and does nothing on days the server never starts.
- **A scheduled job.** Zero sync code in the app, failures stay in a log
  rather than a page, and it covers the days you never plan.
- **A button in Settings.** The integrations rows are deliberately read-only
  (phase 6e: "the row that owns a piece of state keeps owning it"), and a
  write action there reopens that call.

**The rate-limit worry that prompted the question was already handled**, and
is worth recording because it is the reason the schedule can be dumb: a
restart could not cause redundant fetches even if a sync *were* wired to one.
`sync_checkpoints` records each source's last-checked date and
`get_sync_date_range` anchors on whichever requested source is furthest
behind, so a second run the same day resolves to an empty range and issues no
requests at all. Cronometer's per-call cost was the real exposure and
`fetch_range_summaries` fixed it separately.

**What the app owes a job it doesn't run is saying when it stopped**, which
is `ui_state.sync_freshness` and the line it draws above the sync dialog's
per-list cards. Two questions, and they need separate answers:

- **Is anything running at all** — the *newest* checkpoint across every
  source, since that is the last time anything asked anyone anything.
  `SYNC_STALE_AFTER_DAYS` is 2, not 1: the job runs once at a fixed hour, so
  a checkpoint dated yesterday is the normal state all morning and a
  one-day threshold would cry wolf daily.
- **Is one source failing while the others advance** — a source whose own
  checkpoint sits that far behind the newest one, reported separately.
  A single date across the top cannot say it, and the two have entirely
  different fixes (reload the agent vs. re-auth an account).

It reads `sync_checkpoints` and **never the stored rows**, unlike
`sync_status` beside it, which folds the two together on purpose. A scale
nobody stood on for a week records nothing while the job runs perfectly, so
reading rows here would report a working sync as a broken one — the exact
confusion `sync_checkpoints` was added to end, arrived at from the other
direction. No colour carries any of it: amber already means five things in
this app, and "the scheduler stopped" would be the sixth
(the `ui-work` skill, "Known collisions"), so the icon and the wording do
the work.

Tests are `tests/test_sync_service.py`, `unittest` like the rest. Nothing there
touches the network: both clients are reached through one seam each, and the
fakes speak the real payload dialect (grams for Garmin mass, `Energy (kcal)`
headers for the CSV, `hrvSummary.lastNightAvg` beside the weekly average and
the five-minute peak a careless mapping would grab instead) because the unit
and key mapping *is* the module. The Garmin fake can fail sleep and HRV
independently, because that isolation is a decision being tested rather than
an incidental of the fake.

**That was not always true, and the way it failed is worth keeping.** Both
constructors used to read `username or os.environ.get("CRONOMETER_USERNAME")`.
`""` is falsy, so `CronometerSyncService(username="")` — written by the one
test whose whole purpose was proving the guard fires *before* any call —
silently received the developer's real `.env` credentials, passed
`_require_credentials`, and issued a genuine authenticated request to
cronometer.com on every run of the suite. It surfaced only as a `429` once
the account had been rate-limited enough to start refusing; until then the
test passed, for entirely the wrong reason.

`_from_env` now distinguishes `None` ("read the environment") from `""` ("no
credential"), and `TestCredentialGuards` runs with a *populated* fake
environment so the assertion means something. A guard test that constructs
its subject with empty credentials must be run against a filled environment,
or it only proves something about the machine it ran on.

### Bootstrapping the catalog from Google Keep

`src/integrations/keep_import.py` is a **once-off**: the recipes that have
been sitting in Google Keep under one note colour, pulled into
`data/recipes_master.json` so `select_favorite_assignments` has something to
claim slots with. Run `--colors` first, always:

    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --colors
    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --color CERULEAN --dry-run
    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --color CERULEAN

It parses through `import_external_recipe` and writes through
`repository.import_recipe`, so an imported note answers to the same NOVA and
`banned_ingredients` rules a generated recipe does and lands as an ordinary
catalog entry — no new storage, no second parse path.

- **It reads a Takeout export, not an API.** `keep.googleapis.com` is
  Workspace-only and needs domain-wide delegation through a service account —
  it cannot see a consumer `@gmail.com` account at all, which is where these
  notes are. The only live alternative is `gkeepapi`, an unofficial client
  driving the mobile protocol behind a `gpsoauth` master token: a real
  credential to store, an unpinned reverse-engineered dependency, and
  something that breaks whenever Google ships. For a job that happens once,
  "repeatable" is not worth paying for. Only `load_notes` is Takeout-shaped,
  so a future standing sync would swap that one function.
- **Keep's UI colour names are not the values Takeout writes**, and the two
  lists do not correspond in any guessable order — Storm is `CERULEAN`, not
  `DARKBLUE` or `STORM`. `--color` therefore takes the **raw enum**, and
  `--colors` prints every value actually present with sample titles so the
  right one is read off your own notes. `KEEP_COLOR_LABELS` is a display hint
  for that output and deliberately not what the filter matches on: a Keep
  release that renames a swatch must not silently retarget the import.
- **A note that fails to parse must not end the run**, same policy as "a
  failed meal must not fail the week" — handwritten notes are exactly the
  input a parser chokes on, and the failures are named at the end for a
  `--force` re-run.
- **Titles are checked against the catalog before any API call.**
  `import_recipe` already folds a duplicate by `recipe_content_key`, but that
  is decided *after* the parse has been paid for, so a re-run after a crash
  would re-parse everything. A title match needs no new storage and makes the
  command resumable.
- **Checklist notes carry their content in `listContent`**, not
  `textContent`. A recipe kept as tickable ingredients has an empty
  `textContent`, so reading only that field silently imports nothing from the
  notes most likely to be recipes. `note_text` reads both and drops the tick
  state — a ticked ingredient is one you have, not one the recipe omits.

Imports are sequential on purpose: `recipe_parser_model` is often a free
route, and a burst of concurrent calls is the reliable way to turn a working
bootstrap into a wall of 429s halfway through.

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
- `dietary_rules.active_diet_styles` (Mediterranean, Fast 800, DASH, the Total
  Wellbeing Diet, ...) is soft guidance, not a hard constraint like the two
  rules above — it shapes food selection via the generation prompt rather
  than rejecting a recipe. Its **one** hard effect is
  `DietStyle.calorie_ceiling`, which caps the day's computed calories inside
  `hydrate_dynamic_targets` and never reaches the prompt at all; Fast 800's
  800 is the only one declared, and nothing is active by default. See "Diet
  styles" under Architecture.
- **How long a dish keeps is a property of the dish**, not a config global:
  `inventory_rules.storage_windows` plus `Recipe.storage_class`. It is a hard
  constraint in the same class as `banned_ingredients` — the model is told the
  span each slot needs and a class too short for it is rejected and retried —
  and every default resolves to the *shortest* window rather than the usual
  "behaviour before the feature existed". See "Storage windows belong to the
  dish" under Architecture.
- **Fibre is targeted but never budgeted** — the day's figure comes from
  `nutrition_engine.calculate_fiber_target_g` (a floor of
  `user_profile.fiber_floor_g`, raised on a big day) and each meal is briefed
  a weighted share of it, while `fiber_g` stays deliberately absent from
  every *budget*: it has no term in `calories ~= 4p + 4c + 9f`, so no
  validator checks it and no identity operation walks it. The Cronometer
  sync's logged figure sits *beside* the planned/target pair rather than
  under it. See "Fibre is targeted, and still has no term in the energy
  identity" under Architecture.
- `schedule.json`'s `sourcing` block is soft in the same way, and constrains
  what can be *bought* rather than what may be eaten. An ingredient that must
  never appear belongs in `banned_ingredients`; `sourcing` covers the
  unenumerable tail. See "Buying what the shops actually stock".

## Tests

`python -m unittest discover -s tests` from the venv. `unittest` throughout —
this venv carries no pytest, so the suite runs with no extra dependency, and
the classes are plain `TestCase`s so they would run under pytest anyway. The
whole suite is under a tenth of a second because **nothing in it touches the
network, a model, or the clock**: every module reaches its outside world
through one seam, and the tests substitute at that seam.

**The clock half of that had two exceptions, and they were only found when
the date rolled over mid-session.** `test_ui_state.py`'s day-picker fixtures
legitimately call `date.today()` — the Today tab's whole question is whether
a week covers today, and a frozen date would test a different question — but
two assertions on top of them were quietly weekday-dependent, and the suite
went from 646 passing to one failure with no code touched.
`test_browsing_away_from_today_stops_being_today` stepped three days from
`days[0]`, which is Thursday, and
`test_covering_today_is_about_the_columns_not_the_span` asserted against its
own unrotated day list where `week_days` rotates the grid, so it disagreed on
Friday and Saturday. Both now measure from today and from the grid's first
column rather than from a fixed offset. **A fixture may read the clock; an
assertion may not depend on what it said** — and the check is cheap: re-run
the module under seven frozen weekdays before trusting it.

| file | covers |
|---|---|
| `test_week_composition.py` | style/cuisine resolution, cuisine blocks, workout breakfasts |
| `test_week_mechanics.py` | the deterministic week — derived portions, `validate_week`, shopping windows, `spread_batch`, the shopping aggregation and plant count, and the storage note reading the dish's own window rather than one global |
| `test_food_safety.py` | storage windows as a property of the dish — the hours-to-days resolver and that no surface prints hours, every default failing *short* (which inverts the house rule), the merge that stops a config deleting the rice exception, the two behaviour changes that had to land together (the default lengthening to Thursday, rice tightening to two day-gaps), the backstop's two modes, the prep-day anchor whose span is *longer*, the prompt rule and the byte-identical week that gets none of it, the two response axes rejecting identically, the pinned favourite that is a third route neither can see, and the freezer half's quality-not-safety wording |
| `test_portion_sizing.py` | the three portion layers, and the cap on the cascade's end effect |
| `test_planner_dynamic_targets.py` | target hydration, who owns a macro (`target_modes`/`target_locks`), the fibre target resolving on every hydration path including the three that give up on the engine, the diet-style calorie ceiling (idempotent across the two hydration passes, applied after the uplift, never over a stated target, and reported rather than corrected when it cannot pay for locked protein), the protein floor, logged-intake substitution, adaptive TDEE |
| `test_nutrition_engine.py` | BMR/TDEE/deficit arithmetic, the adaptive estimate and which precondition stopped it, the current-weight fallback, the fibre target (which of its two halves binds, and that it is deliberately not assembled into `calculate_macro_targets`, where the uplift and the ceiling have not been applied yet), the MET-based training-burn estimate, and the schedule proposal — its three states, the addition threshold, and the two guards on a proposed drop, plus the weight trend the Insights chart draws (a short span keeps its points and loses only its rate, and its sign is the raw slope's, not the estimate's negated one) |
| `test_model_resolution.py` | which model each role runs on, and the reasoning switch |
| `test_diet_styles.py` | the diet-style axis, its one numeric lever (which `calorie_ceiling` wins, and that the prompt never states the number), and `Ingredient`'s two hard rules |
| `test_ingredient_sourcing.py` | the sourcing rule, the week-wide seafood cap, the nudge-sample ban filter, the rejection-capture prompt rule and its two decay windows (per-reason dish expiry, and the longer reason tally that outlives the dishes it counted), and `rejections.json`'s storage round trip, and the pantry ledger — both entry shapes, the containment match and the two guards on it, and what a spent item stops being told to the model |
| `test_meal_selection.py` | location-shaped grids, favourite pre-assignment, skip estimates, fibre — both halves: the reported one that never enters a budget, and the target with its per-slot share, the calorie pin that does not pin it, and the share that deliberately does not cascade (asserted against a stubbed provider returning meals under brief, so the macro cascade visibly moves and the fibre share visibly does not) — the fridge cap, and which days have the hours for a long cook — the weekend fallback, a location widening a weekday *and* narrowing a weekend, the elapsed-time rejection that catches a braise the flag never declared, and the batch anchor exempt because it is cooked on prep day |
| `test_sync_service.py` | Garmin/Cronometer unit and key mapping (including fibre's capture under the repository's key and its absence from `MACRO_KEYS`), the sleep/HRV readiness row and its two independent endpoints, the activity mapping (Garmin type -> `training_schedule` type, local-not-GMT start times) and its replace-per-date storage, the expired-session recovery (cleared and retried once, never twice, never without a session to clear, and never on a 429), and the credential guards |
| `test_keep_import.py` | Takeout note loading, colour selection, and checklist-note text |
| `test_export_menu.py` | the Markdown export and the `_slot_entry` walk it shares with the PDF |
| `test_adherence.py` | adherence's three layers — `adherence.json`'s two-part key and its delete-don't-flag clear, the per-date match of `activity_log` against the declared week, and the view models both marking surfaces read (including the two spellings of `session_id` that have to stay equal across a module boundary) |
| `test_ui_state.py` | `PlannerState` — grid edits, batch rescaling, target overrides and the baseline they are diffed against, target modes, which days read the stored plan vs. a live preview, slot views, the Today tab's day picker — including the step across into the adjacent cached week, the outer clamp that still holds, the uncached neighbour that is never offered, and the unscanned state that behaves exactly as it did before it could cross — and location/training context, the derived training-burn estimate, the day inspector's open/closed state, the adaptive-TDEE state both diagnostic surfaces report, planned fibre against its target and beside what Cronometer logged for the same date (and which of those two pairs takes a divider), the schedule proposal's session half (what a dismissal and an accept each touch, and what an accept must not persist), the Settings destination's sync-status, sync-freshness and location read views, the generation dialog's per-stage checklist and the off-by-one it turns on (`progress_callback` counts stages *started*, so nothing banks the last one but the run returning), and the Insights destination's five series — the four-state gate each is drawn behind, the planned-against-logged join and the two rules it borrows, the denominators deliberately absent from macro accuracy and the adherence tiles, and the target line that is drawn only once it is in view |
| `test_config_layout.py` | a snapshot of the merged config, asserting nothing was lost or moved |
| `test_history.py` | history recording and rotation seeding |
| `test_api.py` | the FastAPI routes — week plans, recipe catalog filters, history, biometrics (including the mirrored `readiness_log` and `activity_log`), and derived targets/`tdee_source` (including the fibre figure reported with or without a weigh-in); plus `repository.catalog_matches`, the one filter the route and the Library grid share; and the generation route with its job — the single-flight claim from both sides (a second `POST`, and a browser tab holding it), the three outcomes a client has to read separately (a rejected grid, a failed run, and per-meal-type failures riding on a *succeeded* one), and the finished week arriving through the read route rather than on the job. `GenerationJobs.run` is awaited directly rather than reached through `start`, which is what that split exists for: asserting on a spawned task means asserting on a scheduler |

**Where the line is drawn on the UI.** `ui_state.py` is tested because it is
the view model — grid edits, derived portions, override precedence — and those
rules are exactly what a UI change can silently break. The other `ui_*`
modules (including `ui_inspector.py`, the day inspector, and
`ui_adherence.py`, whose two handlers are a `mark_*` call and a refresh) are
widget construction, and testing them would mean a NiceGUI
harness asserting on element trees, which pins the layout rather than the
behaviour. If logic worth testing appears in one of them, the move is to pull
it into `ui_state.py` (or a pure helper) rather than to grow a UI harness.

Two of these were written after a bug that the absence of the test allowed, and
both docstrings say so — `test_model_resolution.py` and the credential guards
in `test_sync_service.py`. That is the shape to follow: when a test is added
because something broke, record the failure in the test, not just the fix.

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
- **A model id named in `models.json` must appear in that file's `models`
  table.** `resolve_planner_model`/`resolve_recipe_parser_model` enforce it at
  load. Without that check the two drifted apart unnoticed:
  `recipe_parser_model` pointed at `google/gemini-3.6-flash` while only
  `google/gemini-3.7-flash` was described, so `model_metadata` returned `{}`,
  the `reasoning_required` flag never applied, and every recipe import died on
  the hard 400 in "Some providers reject the disable switch outright". A
  per-run `--model` is deliberately *not* checked — trying an unrecorded id is
  the flag's whole purpose.
- **A number the UI displays and a number a run plans against must come from
  one call, not two.** `weekly_schedule`'s calories and protein are inert
  while their `target_modes` entry is `auto` — `hydrate_dynamic_targets`
  replaces them — so anything reading the file directly is reading a value
  nothing plans from. The shipped config's 1000 kcal Thursday against a
  computed 1722 is the live example. Reach for `PlannerState.planned_targets`
  (or `baseline_targets`, for what a day would aim at unoverridden), never
  `config["weekly_schedule"][day]`.
- **`inventory_to_clear` holds two shapes and both are load-bearing.** A bare
  string is an unquantified item and a dict carries `quantity_g`; normalising
  the list to one shape would either invent a quantity nobody knows or discard
  one that is spent. `planner.inventory_entries` is the only parser — reach for
  it rather than iterating the list, or the drawer and the ledger will disagree
  about what the file said.
- **`Recipe.total_time_minutes` is `None` for unknown, never 0.** Every recipe
  saved before the field existed carries None, and a validator reading 0 as
  "instant" would pass exactly the dishes it exists to reject. It is also
  never derived from `prep_time_minutes`: the gap between them *is* the
  measurement (see "The generated side is enforced too").
- **`Recipe.storage_class` is `None` for unclassified, and `None` is the
  *shortest* window rather than the default one.** This is the one place the
  codebase's usual "absent means the behaviour before the feature existed"
  convention is deliberately inverted, because here being wrong makes somebody
  ill rather than producing a worse meal plan. Do not "helpfully" restore the
  convention, do not derive the class from `total_time_minutes`,
  `long_oven_cook` or the ingredient names, and do not put a day figure in
  config beside the hour figure. See "Storage windows belong to the dish".
- **Nothing prints storage hours at a user.** The tables are stated in hours
  and the app measures in whole day-gaps, because nothing anywhere stores a
  cook *time*. `week.storage_day_gaps` is the only conversion; every note,
  badge, warning and log line says days.
- **Testing a "fails before any call" guard requires a populated
  environment.** See the sync-credentials note under "Biometric sync": a guard
  test that constructs its subject with `""` and runs against an empty `.env`
  proves nothing about the guard and everything about the machine.
- `src/proposed-engine.py` (Kalman weight smoother, Holt trend) was deleted —
  unreferenced, unimportable by that filename, and depending on `numpy`, which
  is not in requirements.txt. The finished, tested version of what it was
  reaching for is `calculate_adaptive_tdee`, now wired in above.
