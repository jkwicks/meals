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

`src/` holds the Python modules, `scripts/` the shell entry points, `tests/`
the tests. Four directories hold app files:

    config/     hand-edited. Change these to change what the app does.
    reference/  shipped corpora (whfoods.json). Read by `select_nudge_foods`;
                see "Nudging generation toward whole foods".
    data/       written by the app. Never hand-edit.
    logs/       written by the app. Disposable.

Flat inside each, with two deliberate exceptions: `src/integrations/` (the
sync service plus the one-off Keep and OneNote importers — see "Biometric
sync" for the `sys.path` insert that buys the subdirectory back) and
`tests/fixtures/`.

`dev/` holds the approved design documents and implementation prompts (see
"Which planning document to read"); `docs/` holds the research corpora those
designs draw on. `.claude/` holds what this file deliberately does not:
`skills/` (a skill is loaded only when its subject is being worked on — see
"NiceGUI front end" for the front end's, which is the largest), `rules/`
(legacy, and read only when named — see "Shopping lists") and
`settings.local.json`.

Root holds `README.md`, `CLAUDE.md`, `CHANGE-QUEUE.md`, `ISSUES.md`,
`user-manual.md`, `.env`, `.gitignore`, `requirements.txt` and a few transient
assistant notes. It also accumulates four **gitignored** AI-assistant bundles
— `python_codebase.md`, `project_context.md`, `data_schemas.md` (written by
`./scripts/prepare.sh`) and `test_suite.md` (written by `./scripts/upload.sh`).
They are generated, never edited: a change belongs in the source they
concatenate. `Keep/` is a transient directory when it exists at all — a Google
Takeout export dropped in for the once-off catalog bootstrap, gitignored and
deleted once the import is done (see "Bootstrapping the catalog from Google
Keep").

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

### Which planning document to read

**`CHANGE-QUEUE.md` is the only current one**, and the only one that answers
"what should I work on next". It ranks every unfinished item and known defect
in a single list — each with its type, size, what blocks it, and where it was
first recorded — plus a "Verified closed" table so a shipped item is not
re-filed as a new idea. It is verified against the *code*, not against any
document's account of itself.

Cite its entries **by name, never by number**. The numbers renumber on every
release that closes something, and its anchor links still carry a number, so
they are the one thing a renumber has to be checked against.

`dev/` holds the approved design documents (`design-00`…`design-06`, with
`design-00` the overview), the per-session implementation briefings
(`PROMPT-*`), and `dev/OUTSTANDING.md` ranking what is still open;
`dev/README.md` is the reading order. Every `design-NN §X` and `PROMPT-N`
citation below resolves here. Cite these by name too — the `PROMPT-*` numbers
stopped being priority order at 7.

`ISSUES.md` is the maintainer's own defect register, mostly now fixed and
stale by design. Read it for the original wording of a complaint, never for
what is still open.

The earlier planning documents — `ui-redesign.md` and `future-ideas.md`, and
their later `-deprecated` splits — were consolidated into the above and
deleted (v0.31.0, then v0.42.0). Git history has them.

### config/ is eight files — five merged into one dict, three loaded apart

`config.json` was one 196-line file of twenty unrelated top-level keys. It is
now eight, **two-tier**: five *core* files are merged by
`LocalJSONRepository.load_config()` into the same flat dict `AppConfig` has
always validated, so **nothing downstream of the repository knows the config
arrived in pieces** — `planner`, `week` and `ui_app` still read
`config["weekly_schedule"]` as before. Splitting the *files* without splitting
the *object* is the whole trick; namespacing the dict would have touched
hundreds of call sites for no gain.

The five core files, listed in `CONFIG_FILES`:

| file | holds |
|---|---|
| `profile.json` | the body and the numbers aimed at it — `user_profile`, `target_modes`, `weekly_schedule`, `meal_weights`, `dietary_rules`, `training_profile` (personal exercise constraints, its own root — see "Personal exercise constraints and the gym-program catalog") |
| `meals.json` | what a meal may be — `meal_types`, `meal_styles`, `cuisines`, `cuisine_affinities`, `cuisine_meal_types`, `diet_styles`, `week_defaults` |
| `week.json` | the shape of a week — `week_start_day`, `shopping`, `serving_rules`, `enable_sunday_prep`, `max_prep_active_mins`, `inventory_to_clear`, `inventory_rules` (including `storage_windows`, see "Storage windows belong to the dish") |
| `schedule.json` | where you are and what you're doing — `training_schedule` and `sourcing`, plus `base_schedule`, `location_rules` and `regional`, of which only `regional` is read (see below); also `gym_programs` and `active_gym_program`, the gym-program catalog and its standing pick (same section) |
| `engine.json` | tuning for the planner, not the food — `planning_rules`, `ui_settings` |

The three supplemental files, loaded by their own methods and **not** part of
the merge:

| file | holds | loader |
|---|---|---|
| `models.json` | model selection and LLM call params (see "Picking a model") | `load_models_config()` |
| `integrations.json` | sync tuning (see "Biometric sync") | `load_integrations_config()` |
| `presets.json` | the preset catalog and this week's pick (see "Presets") | `load_presets_config()` |

`presets.json` is the only one of the three that is also **written** —
`save_presets_config()`, because `save_config_keys` structurally cannot serve
it. See "Presets: naming the profile config/ already implies".

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
entire point. **It applies to the five core files only** — `models.json`,
`integrations.json` and `presets.json` keys are read directly by the code that
needs them and appear in neither `AppConfig` nor `CONFIG_FILES`.

`CONFIG_FILES` has a second reader now, and it is the one that makes a preset
honest: the **first segment** of every preset override path must be a key the
manifest knows, checked at load. Only the first, because only the first is a
question about file ownership — see "Presets" under Architecture.

`base_schedule` and `location_rules` stay typed loosely
(`Dict[str, Dict[str, Any]]`) because the *shape* is still open: a location
rule is a bag of `<meal_type>_mode` keys plus `restrictions`, and pinning that
down would make the next kind of rule a schema change. `allows_long_cook` is
that next kind, added without one — it says whether you are home with the
hours for something to sit in the oven, read by `day_allows_long_cook` (see
"Which days those are: presence, not the calendar"). A location that omits it
falls back to the weekend.

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
| `claude-queue.sh` | runs an ordered prompt queue (`.prompts/prompt-*.md` when that dir exists; the `dev/PROMPT-*` files are deliberately outside it — see `dev/README.md`) |
| `model-list.py` | dumps OpenRouter's top 50 models and prices to CSV |

**`prepare.sh` walks `src/` recursively.** It used to carry `-maxdepth 1`,
which silently excluded all of `src/integrations/` — so the bundle described
the biometric sync in CLAUDE.md and shipped none of its code. If you add
another subdirectory under `src/`, check it lands in the bundle.

### Web UI

NiceGUI (`ui_app.py`) is the only web UI. The Streamlit app (`app.py`) was
deleted after the migration; it is in git history at `git show e237872:app.py`.
A week can be generated from `python src/planner.py` or the NiceGUI rail's
"Generate" button — both go through `generate_week_plan`, write the same
`week_plan.json`, append the same history. The CLI still prints shopping lists.

### NiceGUI front end

**The app is called Larder.** `ui_theme.APP_NAME`/`APP_MARK_ICON`/
`APP_FAVICON`/`APP_TITLE` are the only places that name it — a literal at a
call site is a fourth thing to miss when it changes.

`ui_app.py` (`./scripts/server.sh start`, :8080) is a high-density desktop UI:
a header of 7 per-day macro bars, a persistent staged-changes bar, and a slim
vertical rail of six destinations — Plan, Today, Shopping, Library, Insights,
Settings. Shopping is the *same* panel the right-hand drawer draws (drawer:
read a trip against the grid; destination: work through one). `ui_app.py` is a
~300-line page shell; every other `ui_*.py` exposes one `build_*(ctx)`
factory, and `ui_state.py` holds `PlannerState`, the view model — the only UI
module with tests. Flat siblings, per the `sys.path[0]` note under Layout.

**The full front-end record lives in the `ui-work` skill**
(`.claude/skills/ui-work/`), not here — the type/spacing/radius scale, what
each colour may mean, the NiceGUI/Quasar traps, the refresh topics, which
module a change belongs in (`SKILL.md`), and the per-surface design record
(`architecture.md`). **Load it before editing any `ui_*.py`.** ~1,100 lines,
kept out of this file so a non-UI session doesn't carry it.

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
  key fails at startup), plus three supplemental files loaded apart from the
  merge. See "config/ is eight files" under Layout. The active preset is laid
  over the merged dict **before** that validation runs (`apply_preset_layer`),
  which is the one ordering where `extra="forbid"` still means anything.
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
      (`WORKOUT_BREAKFAST_TYPES = ("gym",)`, at or before
      `MORNING_TRAINING_CUTOFF`) picks the days, `week.pin_style()` applies
      `WORKOUT_BREAKFAST_STYLE`. Cardio and walks are excluded — a shake is
      what a hypertrophy session's fast refuel needs, and it is the only
      breakfast drinkable ten minutes before a session, so plain rotation
      gives a 06:30 gym slot eggs and smoked salmon about one week in five.
      An *evening* session is not covered: `apply_training_adjustments`
      already handles it as macros, and pinning a *style* only matters when
      the session lands before the meal can settle. Both are pins, not
      overrides — a style or cuisine chosen in the review dialog always
      survives.

      **The pin only fires on a slot still on auto**, which is why
      `ui_generation.generate_week` calls `week.clear_styles`/`clear_cuisines`
      unconditionally before `resolve_auto_choices` on every full-week
      generation. Without it a slot carrying a concrete style from a previous
      run blocks the pin from re-firing, so a `training_schedule` edit that
      newly qualifies a day would not reach the plan until "Shuffle styles"
      (`PlannerState.shuffle_styles`, same `week.clear_*` calls) was clicked.
      Mode, leftover links and skips survive the clear — those are structural
      edits made on purpose, not picks due for a re-roll.

    The prompt side lives in `generate_meal_type_week`, the only call that
    sees the whole week: `build_cuisine_continuity_rule` tells the model which
    days share a cuisine *on purpose* and to differ them by
    protein/vegetable/method and by how the aromatics are expressed (rub vs.
    marinade vs. finishing sauce), and swaps `WEEK_STYLE_RULE` for
    `WEEK_CUISINE_BLOCK_STYLE_RULE` — the standing rule that consecutive days
    differ in tradition is the exact opposite of a 4/3 split and invites the
    model to "fix" the repetition by substituting a cuisine. It emits nothing
    when no cuisine spans more than one day. `DINNER_VARIETY_RULE` gained
    "never the same primary protein on two consecutive nights" for the same
    reason: four Greek nights satisfy the "no protein more than twice" cap
    with lamb, lamb, chicken, chicken, which reads as the same meal twice.

    Everyday Western baselines (`homestyle`, `modern_australian`,
    `pub_classic`) exist in `cuisines` so `cuisine_affinities` can route a
    heavily-spiced block (`mexican`, `bbq`, `thai`, `indian`) toward a plain
    roast-and-veg block next rather than a second intense one. They carry no
    `principles` entry — a bare name plus `humanize()` is enough for the model.

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

### Presets: naming the profile `config/` already implies

`config/` holds one implicit profile smeared across five files. A **preset**
names it, makes it switchable, and makes the choice **weekly** — a label plus
a map of dotted leaf paths to the values that week wants instead
(`config/presets.json`, resolved by `src/presets.py`). A week generated under
one is stamped with its name (`WeekPlan.preset`, and the history entry), which
is the whole reason to record anything: "did that change work?" is
unanswerable without knowing which preset each week ran under.

**It is not a diet.** A diet strategy is `diet_styles`, which already exists
and which a preset may switch on; a preset is that *plus* everything about the
week that is not food — what gets cooked, what gets batched, how long you are
willing to spend. That is why it needed a word of its own, and why "meal
strategy" was wrong in both directions at once: too narrow, and already taken.

The load order, and the middle step is new:

    1. merge the five core files      -> base dict   (CONFIG_FILES manifest)
    2. resolve the active preset      -> preset layer  (presets.resolve_config)
    3. validate                       -> AppConfig (extra="forbid")

**Validation moved to after the layer, and that is the real change here.**
Before this, the merge validated and nothing touched the dict afterwards. A
preset overriding a key *after* validation could introduce a state `AppConfig`
would have rejected, so validating last is the only ordering where
`extra="forbid"` still means anything. `planner.apply_preset_layer` is where
the two meet, and both entry points — `load_config_with_models` and
`PlannerState.load` — go through it, so the CLI, the API and the UI cannot
disagree about which preset a week is planned under.

#### An override addresses a leaf, and the whole-key version is worth keeping

An override is a dotted path — `"dietary_rules.allowed_nova_groups"` — whose
value replaces **that leaf**, whole. Four rules, all load-time and loud:

- **The first segment must be a key `CONFIG_FILES` knows**, failing with the
  preset name *and* the path. Only the first, because only the first is a
  question about file ownership. A preset that appears applied and is not is
  strictly worse than one that refuses to load — the same argument
  `CONFIG_FILES` already makes about a key in the wrong file.
- **Every segment before the last must already exist and be an object.** A
  path describing a branch that is not there is structurally wrong, and
  creating it silently writes a value into a branch nothing reads. The *last*
  segment need not exist: that is how a preset states an optional key the base
  file leaves at its `AppConfig` default.
- **Each leaf is replaced whole; there is no recursive merge anywhere.** A
  merge cannot express deletion, and makes "what does this preset plan
  against" unanswerable without replaying it. An override valued `[]` or `{}`
  is an explicit value, never an absence.
- **No chaining or inheritance.** One layer, so you can read one object and
  know what the week will do.

**Whole-key replacement was rejected, and the failure was silent.** A
four-line `comfort` preset — `"dietary_rules": {"allowed_nova_groups":
[1,2,3,4]}` — reads as correct, but under whole-key replacement that object
*is* the week's `dietary_rules`, and `DietaryRules` has no required fields, so
it **validates cleanly** while discarding 17 `banned_ingredients` entries
(several allergen-shaped) and `active_diet_styles` — with nothing in the
pick's diff to show it. `test_presets.py` asserts that case on the 17 named
ingredients, not on shape, because the refuted design passed a shape
assertion.

#### `default` is a row in the file, and the baseline is the base config

Nothing in the code treats any preset name as special. `default` reproduces
today's behaviour **because its `overrides` are empty**, not because the
loader falls back to it — which is what keeps it editable and deletable, and
what makes the compatibility test honest.

**The baseline for every comparison is the base config — the five merged core
files — never the preset named `default`.** The two are easy to confuse and
the confusion is load-bearing: a diff computed against another *row* goes
blank the moment that row is edited, and dangles the moment it is deleted,
taking `active` with it. The base config cannot be deleted, because it is the
thing presets layer over.

#### One resolver, two presentations

`presets.resolve_config` is pure: it computes, returns structured
`PresetFailure`s, and never raises or touches disk. The **loader** raises on
those (fail-loudly-at-load); the preset editor renders the same ones and
declines to write. One resolver, so the two surfaces cannot disagree about a
file. It imports neither NiceGUI nor `PlannerState` nor `planner`, so `api.py`
and the editor can both reach it.

**`planner.resolve_preset_layer` is the composed check both surfaces run.**
`resolve_config` is only half of the loader's work — `apply_preset_layer` then
validates the resolved dict through `AppConfig`, and a preset that breaks
*that* passes `resolve_config` cleanly. `resolve_preset_layer` runs both
halves and returns `(config | None, [PresetFailure])`; `apply_preset_layer` is
a thin wrapper that raises on it. It lives in `planner.py` (not `presets.py`)
because step 2 needs `AppConfig`, and importing it back would be a cycle.

**Every preset is checked, not only the active one.** A preset you might pick
next Monday is worth knowing is broken now, and it costs the usual price of
the loud-at-load policy: a typo in a preset nobody is using stops the app,
exactly as a typo in `profile.json` does.

#### The weekly pick, and the nine fields it has to re-seed

The pick is at the **top of the review dialog** (where the week's shape is
settled, since Generate opens it rather than running the week), *above* the
"everything below is staged" line — because unlike everything under it, the
pick persists the moment it changes, through `save_presets_config`.

Three things about `PlannerState.set_preset`:

- **It re-layers from `base_config`; it never layers onto `self.config`.**
  Laying the incoming preset over the outgoing one's result would leave every
  leaf the new preset is silent about still carrying the old preset's opinion
  — a config nobody chose and no file describes. `PlannerState` therefore
  keeps the unlayered base beside the layered result. The swap-and-re-seed is
  `_relayer`, shared with the preset editor's `save_preset` (editing the
  active preset changes what it resolves to, exactly as a new pick does).
- **Nine `PlannerState` fields are copies of config values**, taken at load
  (`PRESET_SEEDED_FIELDS` — servings, shop days, the pantry rows, the training
  schedule, the batch toggles, and so on). A preset moving one of them would
  otherwise change the config and not the control that displays it, which is
  the "appears applied and is not" failure again. They are re-seeded **only
  where the config value behind them actually moved**, so a pantry row typed a
  moment ago survives a pick with no opinion about the pantry, while a preset
  that does have one wins. `_original_training_schedule` moves with the
  training schedule, or the staged bar reports a phantom edit for every
  session the preset just seeded.
- **The pick's diff is one line and says what changed**, generically
  (`path → value`), never through a phrase table mapping keys to prose — that
  would be exactly the hard-coded knowledge about presets this design rules
  out, and it goes stale the moment a preset states a key the table never
  heard of. A mode whose effect you cannot see is the stale-config problem
  wearing a new hat.

`config/presets.json` ships with one row — `default`, overriding nothing — so
a fresh checkout plans byte-identically to before presets existed, and so does
one with no file at all. Both are asserted rather than assumed
(`test_presets.py`, and `test_config_layout.py`'s layered snapshot).

#### The editor (`ui_presets.py`, Settings → Presets)

A copy of `ui_review.training_editor`'s list-of-records pattern — one bordered
row per preset with an Edit and a Delete, a "New preset" button, and a dialog
of fields — plus the save-time check. The logic is on `PlannerState`
(`preset_catalog_view`, `save_preset`, `delete_preset`, `preview_preset`) and
tested there and in `test_presets.py`; the module is widget construction only.
Six things are decisions:

- **The field list is bounded to preset keys with a config home and a clean
  widget shape.** `PRESET_EDITOR_FIELDS` in `ui_state.py` is the twelve today:
  NOVA groups, active diet styles, `week_defaults`, `meal_weights`, per-day
  `net_carbs_g`, and behind an *Advanced* fold `serving_rules.servings_per_meal`
  plus five `planning_rules` keys. The rest of `design-01 §9.2` (the prep
  ceilings, the long-cook threshold, numbers welded into
  `DINNER_VARIETY_RULE`/`PORTION_DENSITY_GUARD`, training constants,
  `meal_styles`, `meal_overrides`, `week_shape`) each need a code change first
  — CHANGE-QUEUE.md's editor-field items, each a later release.
  `calories`/`protein_g` are *not* offered — inert while `target_modes` is
  `auto`.
- **Every field renders unset and may be ignored.** Absent = today's
  behaviour, which keeps the empty preset the identity — everything blank
  produces `overrides: {}` and a byte-identical week, asserted.
- **The escape hatch is per preset.** `save_preset` starts from the preset's
  existing `overrides`, drops every path `PRESET_EDITOR_FIELDS` manages, then
  merges the user's choices back — so a hand-added `meal_styles.breakfast` or a
  hand-added non-`overrides` key survives untouched, and every *other* preset
  round-trips verbatim.
- **Validate before save, using the loader's check.** `save_preset` runs
  `planner.resolve_preset_layer` over a candidate document and writes nothing
  on failure. The one cross-field rule the editor prompted:
  `PlanningRules._reuse_windows_fit_history` rejects a `favorite_reuse_days`
  window past `history_max_entries` — on the shared model, so a hand-edited
  `engine.json` hits it too.
- **Deleting the active preset is refused** — deletion must never silently
  change what the week plans against. A preset's *name* is immutable after
  creation (it is what `active` and `WeekPlan.preset` store); label and
  overrides are editable.
- **Editing the *active* preset re-layers** through `PlannerState._relayer`,
  the re-seed half of `set_preset` factored out so a pick and an edit cannot
  diverge. Preview is on a button (`preview_preset`, pure), never live. The
  active row is a filled `bookmark` glyph and the word "Active" — no new
  colour.

### Diet styles: a standing philosophy, orthogonal to cuisine

`config/meals.json`'s `diet_styles` is a catalog of twelve named eating
patterns (Mediterranean, MIND, Nordic, Blue Zones, Fast 800, Total Wellbeing,
Anti-Inflammatory, Paleo, Pegan, DASH, Low-FODMAP, AIP), each a `label` and a
`principles` string. `dietary_rules.active_diet_styles` (in `profile.json`,
empty by default) says which are in effect. Same catalog/standing-choice split
as `cuisines`, but **not rotated per day or per block** — these are patterns
followed for a stretch, and a meal is meant to satisfy its cuisine *and* every
active style at once (a Korean dinner can still be Mediterranean-principled).

`AppConfig.diet_styles_are_known` cross-checks `active_diet_styles` against
the catalog at load — the check lives on `AppConfig` because only the parent
model sees both fields.

#### A style may be on for part of the week, and the list takes two shapes

`active_diet_styles` is a list mixing two legal shapes:

```json
"active_diet_styles": [
  "mediterranean_diet",
  { "style": "fast_800", "days": ["Monday", "Tuesday", "Wednesday", "Thursday"] }
]
```

A bare name is on **every day** (what every entry meant before this existed);
an object names its window in weekday **names**. Both stay legal for the same
reason `inventory_to_clear` keeps two shapes: normalising would make one
honest answer unexpressible.

`planner.day_scoped_entries` is the **one parser** — the prompt rule, the
calorie ceiling and the load-time catalog check all go through it. It is
general (`subject_key`, default `"style"`) so a mid-week block boundary asks
the same "which days does this bind on" question through it. Six cases, all
load-time:

| Case | Answer |
|---|---|
| unknown weekday name | **raises**, naming the style and the day |
| `"days": []` | **raises** — indistinguishable from a mistake; delete the entry |
| `days` absent from an object | **raises** — the bare string is how you say every day |
| an unknown key beside the two | **raises** — `extra="forbid"` |
| the same style twice | union the days, first-seen order; not an error |
| bare **and** day-scoped | the bare form wins and it **warns** — redundant, not wrong |
| a named day outside the planning week | inert, no error |

**A malformed entry raises, where `inventory_entries` drops one with a
warning** — a dropped pantry line costs a priority, a dropped `fast_800`
activation silently plans an 800 kcal day at ~1722. Do not give this parser
the drop-with-warning policy. The redundancy warning fires only at load
(`warn=True` from `AppConfig`'s validator); the per-day readers leave it off,
or a repaint would log one per day.

**A preset may set this key**; an override replaces the leaf whole and
`AppConfig` validates it, so the one parser is reached whichever file it came
from. The review dialog's "Diet styles this week" multi-select still REPLACES
the list with flat keys for that run.

`build_diet_style_rule(config, days)` turns the active `principles` into one
`Rules:` line, sent by both axes via `build_generation_rules`, empty when
nothing is active. It takes `days` because `generate_meal_type_week` spans the
week and `generate_day` covers one: a style active every day (or over every
day the call touches) is stated unconditionally; one whose window covers
*none* of the call's days is left out entirely; only a straddling call gets
`Fast 800 (on Monday, Tuesday only): ...` — via the same `_sourcing_day_split`
the fishmonger rule uses.

**One numeric lever: a ceiling, not an adjustment.** `DietStyle.calorie_ceiling`
is None on eleven of twelve; `fast_800` declares 800.
`planner.diet_style_calorie_ceiling(config, day)` reads the lowest any style
active *that day* declares, and `hydrate_dynamic_targets` takes the day's
computed calories `min()` against it — so a four-day window caps Mon–Thu and
leaves the rest at the engine's figure. Two windows with different ceilings
put two numbers on one week, so capped/unaffordable days are carried as
`(day, ceiling)` pairs and reported through `ceiling_summary`.

**A ceiling is admissible where an adjustment is not, because hydration runs
twice** (UI preview, then generation): anything that *shifts* a number shifts
it twice — the bug that took a 2200 kcal override to 1850 — where `min()` on
an already-capped day is stable. Four decisions:

- **After the training uplift, not before** — a workout does not buy an
  exemption from a bound its owner chose to eat inside.
- **A stated target (`target_is_stated`) is never capped** — that would be a
  second source of truth, and would make flipping calories to manual silently
  move the day.
- **Lowest wins** when two active styles declare one; averaging produces a
  figure neither asked for.
- **An unaffordable ceiling is reported, not corrected.** Locked protein
  (144 g) plus `weekly_schedule` carbs can exceed 800 kcal; `derive_fat_g`
  floors at 0, hydration warns and emits a note. Raise the ceiling or lower
  that day's `net_carbs_g` — the code does not pick a number nobody chose.

**The prompt never states the number** — `build_diet_style_rule` sends "simple,
lean, low-added-fat dishes inside the budget given". A model told a figure
starts optimising for it instead of the food.

**All twelve are soft guidance, including Paleo and AIP.** "Exclude all
grains, legumes and dairy" is `principles` text, not a validator — real hard
exclusions belong in `dietary_rules.banned_ingredients`.

### Personal exercise constraints and the gym-program catalog

`design-06`'s first slice (Task 4.1): configuration and UI only — **no
workout is generated from any of this yet** (that is Task 5.1). Three core
keys, all with benign absent meanings so an existing checkout is unaffected:

- `training_profile` (`profile.json`) — `planner.TrainingProfile`:
  `movement_constraints` (each a `planner.MovementConstraint` — `scope`
  `exercise`/`movement_pattern`, a `target` matched exactly, `action`
  `exclude`/`modify`/`prefer`), plus `available_equipment` and `notes`. Its
  own root, deliberately not nested under `user_profile` — different
  lifecycle, and the preset-protection rule below needs one unambiguous
  root. Empty means no personal restriction; **merely having a `birth_date`
  activates nothing** — no constraint or program is ever inferred from age.
- `gym_programs` (`schedule.json`) — a catalog of `planner.GymProgram`
  entries (rep ranges, working sets, target RIR, progression method,
  movement patterns covered), keyed by a stable catalog id exactly like
  `diet_styles`. Content for a gym session `training_schedule` already
  declares, never a second calendar.
- `active_gym_program` (`schedule.json`) — the standing pick from the
  catalog, or `None` (empty catalog + no pick = the detailed-workout
  feature is simply off).

**`presets.PROTECTED_CONFIG_ROOTS`** freezes `training_profile` and
`gym_programs` against any preset override, root or nested leaf — a
persistent personal fact, not a weekly opinion. `active_gym_program` is
deliberately the one presettable field of the three, and is
`PRESET_EDITOR_FIELDS`' `active_gym_program` entry (`ui_state.py`) — the
**only** exercise-planning field in the weekly preset editor. A violation is
an ordinary `PresetFailure` naming preset and path, exactly like a typo'd
override path.

**UI ownership:** the Settings destination's "Training" section
(`ui_settings.py`) is the *only* editor for the personal profile and the
catalog — list-of-record cards copying `ui_review.training_editor`'s
inline convention (add/remove repaints, a field edit is debounced and
persists immediately through `save_config_keys` once it validates through
the same typed config path, never a staged input). The review dialog
(`ui_review.py`) shows a **read-only** summary — the resolved active
program and the constraints that will bind — built by
`ui_state.training_review_view`, and states which layer won whenever the
active preset overrides the standing pick; it never edits anything.

### Targets come from the body, not the file — unless you say otherwise

`weekly_schedule`'s per-day calories and protein are, by default, not what
the week is planned against. `hydrate_dynamic_targets()` replaces them with
`nutrition_engine.calculate_macro_targets()`'s output — BMR from the latest
weigh-in, TDEE from the activity factor, and a deficit that slides with the
remaining gap to `target_weight_kg`. It is a **pure function**; `hydrate_config()`
is the thin `async` wrapper that fetches the biometrics for it.

#### Who owns a number: `target_modes` and `target_locks`

That replacement used to be **unconditional**, which made `weekly_schedule`'s
stated calories/protein dead weight the moment a weigh-in existed (shipped
config said 1000 kcal on a Thursday; every run planned 1722) and made **every
review-dialog override a silent no-op** — hydration overwrote the edited value
along with the stale file one. Only `net_carbs_g` survived, being the one
macro hydration reads back rather than replacing.

`planner.target_is_stated(config, day, macro)` is the fix — **one function,
two callers**, so `apply_training_adjustments` and `hydrate_dynamic_targets`
cannot disagree about whose number a day aims at. Two ways to say "somebody
stated this on purpose", different in lifetime:

| | `target_modes` | `target_locks` |
|---|---|---|
| scope | the whole week, one macro | one day, one macro |
| lives in | `config/profile.json` (`TargetModes`, on `AppConfig`) | injected at runtime by `planning_config()` |
| set by | the Settings destination's toggle | typing in the review dialog's target curve |
| persists | yes — the one thing besides generation that writes to `config/` | never reaches disk |

**Only two macros have a mode** (`TARGET_MODE_MACROS = ("calories",
"protein_g")`), because only two have two possible sources. `net_carbs_g` has
no computed form — the engine hands `weekly_schedule`'s figure straight back,
which makes carbs the week's cycling lever — and `fat_g` is always
`derive_fat_g`. Settings says so in words rather than a dead toggle.

Three consequences:

- **A stated target is the day's *final* number, so a workout does not grow
  it.** `apply_training_adjustments` skips a stated macro **and does not
  record the uplift it declined** (that record is replayed by hydration).
  Before this, switching protein to manual silently moved a training Monday
  from 144 g to 187.8 g — a toggle that changes *who decides* must not change
  the number.
- **The skip happens at the source, not by unwinding later.** An earlier
  version added the uplift and had hydration subtract it; the second
  hydration pass then subtracted an uplift the number no longer carried,
  taking a 2200 kcal override to 1850. Hydration is now idempotent for a
  stated macro because it takes it verbatim.
- **Every switchable macro manual means the engine is never called** —
  `needs_engine` false, no BMR, `dynamic_basis` absent; a checkout with no
  weigh-in plans off the file.

Switching a macro to `manual` **seeds `weekly_schedule` from what the engine
currently computes** (`PlannerState.set_target_mode`), not the stale file
figure — handing the stale one back would look like the toggle re-planned the
week. Defaults are `auto`, so a config predating `TargetModes` is unchanged.

#### The header previews what the run will actually aim at

`PlannerState.planning_config()` ends with `hydrate_dynamic_targets`, so
`planned_targets`, the target curve and the telemetry header read the engine's
numbers, not the file's. It can do this synchronously because hydration is
*pure*: `.load()` keeps the latest weigh-in and the full series on
`PlannerState.latest_biometrics`/`.biometrics`. `log=False`, or a repaint
would log a line per keystroke.

**`targets_for` branches on what *this session staged***
(`target_is_staged(day)` — a target override or a training edit), not on
`has_training(day)`, which is the config's standing state. Branching on the
latter put most days on the live preview and one on the stored plan — **one
row of figures computed two ways** — so a fresh weigh-in read as a plan that
had drifted on Monday and held on Thursday. Everything unstaged is measured
against `week_plan.targets`; re-generating reconciles the two. The telemetry
marker (amber `•` / emerald `⚡`) keys off the same predicate. `has_training`
also had to stop counting a `{"type": "rest", ...}` entry as training (it drew
a bolt on a scheduled rest day); it now mirrors `apply_training_adjustments`'
filter.

**An override is diffed against the day's *resolved* baseline**
(`PlannerState.baseline_targets` — `planning_config()` with that day's
overrides suppressed), not `weekly_schedule`, whose number is inert on an
`auto` macro (diffing against it reported "Thu +800 kcal" for a 78 kcal edit).
`set_target`'s clear-on-match, the staged bar's delta and the curve's ghost
line all measure from it.

#### TDEE is measured once there is enough data to measure it

The activity-factor TDEE above is a population regression, ~300 kcal off an
individual. `calculate_adaptive_tdee` measures instead:

    adaptive TDEE = mean logged calories + (kg lost per day x 7700)

This closes the loop the Cronometer sync feeds. Three things about the wiring:

- **It needs the series, not the latest row** — `hydrate_config` reads
  `load_biometrics()` *and* `get_latest_biometrics()`; "latest" is a question
  about dates, not list order.
- **The estimate is bounded, never blended.** `reconcile_adaptive_tdee` keeps
  the formula when the measured figure is more than `ADAPTIVE_TDEE_TOLERANCE`
  (25%) away. Under-logging is the common failure and always reads *low*, so
  an unbounded measurement would cut the target of whoever logs least
  carefully. One number, not an average of a good estimate and a bad one.
- **`basis["tdee_source"]` says which won** — `"formula"` (nothing to measure)
  distinct from `"formula_adaptive_rejected"` (measured and disbelieved, logs
  a warning with both figures).

`calculate_adaptive_tdee` returns `None` (keep the formula) for fewer than two
weigh-ins, a span under `MIN_TREND_SPAN_DAYS`, or no logs. Protein stays
locked to the target weight whichever TDEE wins — a measurement buys back
energy, not protein.

#### Which of those three `None`s it is

`measure_adaptive_tdee` returns an `AdaptiveTDEEStatus` — the estimate plus
the weigh-in count, **span**, logged-day count and floor, all measured inside
the window the estimate would have used; `calculate_adaptive_tdee` is a
wrapper returning `.estimate`. All three unmet preconditions are cold-start
states that read through to `tdee_source == "formula"`, indistinguishable from
an empty `biometrics.json`. **The span is the one worth naming loudest** — it
collapses while every count looks healthy (weigh-ins bunched into a few days),
and a fully caught-up Cronometer cannot fix it, which is why the status
reports days not counts.

`ui_state.adaptive_tdee_view(biometrics, basis)` is the one view model both
surfaces read, over six states: the engine's three unmet preconditions,
`rejected` and `adaptive` from `tdee_source`, and `measured` for a figure with
no basis beside it (every macro manual, or no body profile — reporting it as
`adaptive` would claim arithmetic that never ran). Settings' Daily Targets
prints it under the calories row; Insights prints the same verdict. Colour
carries none of it — the trend glyph does (`ui-work` skill).

It is called at all three generation entry points, not once in the CLI,
because NiceGUI builds its config in the synchronous `planning_config()` which
cannot await storage. `planning_config()` also calls `hydrate_dynamic_targets`
itself against the weigh-in `.load()` keeps, so the header previews what the
run will aim at; generation hydrates again (idempotent) in case the scale
reported between page load and Generate.

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

`apply_training_adjustments` reads `estimated_burn_kcal` straight off each
`training_schedule` session — it always has. What changed is where the number
starts: a flat 300 kcal default, now
`nutrition_engine.estimate_session_burn_kcal` (the MET formula
`MET * 3.5 * weight_kg / 200 * minutes` on the session's own type/duration and
`PlannerState.weight_kg`, falling back to `user_profile.current_weight_kg` via
the shared `resolve_current_weight_kg`).

**This is a *default*, not a second calorie source** — `apply_training_adjustments`
still reads one field and still records `training_uplift`; a derived number
and a hand-typed one are the same field to it. The training editor keeps
`estimated_burn_kcal` an editable `ui.number`, and the estimate is applied by
an explicit calculator-icon button (`estimate_burn`, tooltip, written on
click), not a live recompute — recomputing would rebuild the row the user is
mid-edit in, the focus-theft trap `training_field_handler` sidesteps by
refreshing `"targets"` not `"training"`.

Proposing the schedule itself from Garmin activity history follows the same
precedent — a derived default, into the same field, on an explicit click. See
"Proposing the week you actually trained" below.

#### Proposing the week you actually trained

`nutrition_engine.propose_training_schedule` reads `biometrics.json`'s
`activity_log`, diffs four weeks of it against the declared `training_schedule`,
and returns `ProposedSession`s — sessions to add and declared ones to drop.

**The detector is the easy half; the confirmation is the feature.** Nothing in
`nutrition_engine` writes; the review dialog renders one row per proposal with
accept/dismiss, copying `estimate_burn`'s explicit-click interaction. A
schedule written from a guess would move a day's calorie budget and pin its
post-workout meal off a pattern nobody agreed to.

`activity_log` is a fourth biometric section, written by
`GarminSyncService.fetch_activities`. Three decisions:

- **It holds several rows per date**, so `save_activity_entries` replaces a
  day wholesale where its siblings upsert — a re-sync's answer for a date *is*
  the answer.
- **Only mapped, timed activities are stored.** `GARMIN_SESSION_TYPES` has no
  catch-all; `startTimeLocal` (never `startTimeGMT`) supplies the clock. An
  unmapped modality guessed at ("Cardio Easy" for a yoga class) is a wrong
  answer that looks right — `MET_FALLBACK` can refine a declared session but
  not invent one.
- **`net_calories` is load-bearing.** A proposal's `estimated_burn_kcal` is
  the median of what Garmin reported (already `EXERCISE_RECOVERY_FACTOR`-
  discounted), MET formula as fallback. Medians throughout, so one long Sunday
  among short sessions doesn't drag the proposal down.

**What counts as observed is the whole difficulty.** Silence in `activity_log`
is ambiguous — rest day or unsynced day. The observed span runs from the first
recorded activity to the later of the last one and Garmin's checkpoint, capped
at today; a row past the checkpoint still counts (it is proof the day was
asked about). Under-claiming at the start is the safe direction.

**A declared session Garmin never sees is proposed for removal — and only
proposed**, behind two guards: the weekday must have come round
`MIN_PROPOSAL_OCCURRENCES` times in the span, and `MIN_ACTIVE_DAYS_FOR_DROP`
checks the watch is being worn at all (a watch in a drawer looks like a
fortnight of rest). A weekday that recorded *something* is never proposed for
a drop — a Sunday ride that became a Sunday walk arrives as an addition
instead.

**Additions are diffed against the staged schedule; drops against the file's.**
An accepted addition must stop being proposed on the next repaint; a drop is
an edit to `schedule.json` and may only name a session that file holds.

**Accepting persists — the second UI writer to `config/`** (after
`set_target_mode`), because `training_schedule` is the standing week, not an
input to one run. The change is applied to the file's list and the staged one
separately, not by persisting whatever the drawer holds;
`_original_training_schedule` moves with the file so the staged bar reports no
phantom change.

**Three states produce no proposals and mean different things**
(`ui_state.training_proposals_view`, same lesson as `adaptive_tdee_view`):
"nothing recorded", "not enough history", "your declared week already matches"
— the last most likely to be misread as broken. Dismissals are session-local
and unpersisted: a proposal waved away today comes back next week because the
evidence has grown.

**Protein is locked to the target weight, not today's and not the day's
activity.** 80 kg x 1.8 = 144 g every day. Tying it to current weight would
shrink the floor exactly as the diet begins to threaten the lean mass it
protects.

`planning_rules.min_meal_protein_g` (35 g) makes that per-day figure reach
each meal: `split_targets` ends with `apply_protein_floor`, which **moves
grams between meals rather than creating any** — under-floor slots raised,
donors give in proportion to surplus, calories travel at 4 kcal/g so totals
are conserved. Pinned and leftover slots are excluded. When the floor is
unaffordable it does **nothing** and logs — a day that can't carry `n x 35 g`
is a target problem, not a split problem.

#### The floor and the day have to be affordable together

Worth knowing before changing either number. `hydrate_dynamic_targets` locks
protein at 144 g whatever `weekly_schedule` says; four meals against a 35 g
floor need 140 of it, leaving 4 g of slack across the whole day — no meal can
be protein-forward. The fix is config, **two coupled changes, together or not
at all**:

- `week_defaults.snack` is `skip` — three meals, not four: 144 − 60 leaves
  84 g for lunch and dinner, 42 g each, clear of the floor.
- The two `gym_hypertrophy` mornings (Monday, Saturday) get `550 kcal / 60 g`;
  the other five keep smaller pins, because `eggs_salmon`/`beans_toast` have
  no protein-powder base for 60 g.

The shake style text says **"choose 3-6 items on top of the mandatory base, at
least one Protein Boost"** (was "2-4") — a template that cannot reach its own
budget is one the model abandons wholesale.

##### The shake's mandatory greens

The base is not just powder/creatine/water: **20-30 g raw leafy green, 50-80 g
raw frozen vegetable and one Fruit Fusion item are mandatory in every shake.**
They cost ~50-75 kcal for the best nutrient-per-calorie trade in the template
and there is no budget they don't fit.

**Making them mandatory took three coordinated edits, not one** — `meals.json`
base list, `SHAKE_ROTATION_RULE`'s never-drop list, and `SHAKE_SLOT_DIRECTIVE`.
`SHAKE_ROTATION_RULE`/`SHAKE_SLOT_DIRECTIVE` tell the model to keep the base
identical and *vary the secondary components*, and greens/veg/fruit sat in
that secondary pool — the cheapest things to drop when two drinks must differ.
Fruit is the subtlest: the rotation rule also names it as a thing to rotate,
so listing it as base turns "whether a fruit" into "which fruit", and the
matrix's "choose 3-6 **further** items" annotates its tier as already-spent so
it is not double-counted. Which green/veg/fruit may still vary; only their
presence is fixed.

This is soft guidance — no validator rejects a greens-less shake (a rejection
is a 30s-3min retry that cannot add an ingredient). A `model_validator` on the
style is the next step if they still turn up short now the budget is
satisfiable.

Everything degrades to the file's numbers, with a warning and a UI note, when
no weight is available — that is real targets somebody chose, not a fabricated
body. **`biometrics.json` ships empty, so this fallback is the normal path
until the first Garmin sync.**

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

`save_config_keys` is the **one write path into the five core files** (with
`save_presets_config` beside it, the only non-generation writes to `config/`).
Two callers only — `set_target_mode` and `accept_training_proposal` — both
persisting a *standing choice*, not a per-run input. It merges each key into
the file `CONFIG_FILES` says owns it, read-modify-write per file, so a
hand-added key survives. It is **not** a "save the config" call: the in-memory
config is a merged dict carrying runtime-injected keys (`training_uplift`,
`target_locks`, `nudge_foods`, `openrouter_model`) that must never reach disk.
Point the app at a backend via a different `PlanRepository` subclass —
`planner.main()` and `REPOSITORY` in `ui_app.py` are the only two that name one.

**`save_presets_config` is the second write path** because `_save_config_keys`
raises on every key outside `CONFIG_KEY_OWNER` and `presets.json` is
deliberately outside it. It is read-modify-write on the whole file, so writing
`{"active": name}` leaves a hand-added preset exactly as the file holds it.
**Four writers across two methods** — `set_target_mode`,
`accept_training_proposal`, `set_preset`, `save_preset`/`delete_preset` — all
persisting a standing choice; `save_preset` is the first to write *arbitrary
structure*, which is why `resolve_preset_layer` runs before it.

**Every method is `async`, including the local-file one**, so business logic
awaits its storage today rather than being rewritten around an `await`
boundary when the future backend lands. `LocalJSONRepository` runs its
blocking `open()`/`json` in `asyncio.to_thread`, so the `await` genuinely
yields.

Consequences:

- `generate_week_plan()` and `record_week_history()` are coroutines; sync
  callers bridge with `repository.run_sync()` — one `asyncio.run` per entry
  point, never per storage call. `run_sync` falls back to a scratch thread if
  a loop is already running; the CLI has none, NiceGUI must `await` directly.
- **The generation calls are synchronous, dispatched to threads.**
  `generate_meal_type_week()` and `generate_day()` block on instructor for
  30s–3min, so `generate_week_plan()` hands each meal type to
  `asyncio.to_thread` — awaiting inline froze every connected browser. Meal
  types stay strictly sequential (each stage's budget needs the last stage's
  *actual* output) — this is about not blocking the loop, not speed.
- **Callbacks come back to the loop.** `on_calling_loop()` re-schedules a
  worker thread's `note_callback` with `call_soon_threadsafe`, or the UI is
  mutated off-thread. `progress_callback` fires on the loop, between days.
- Writes go via temp file + `os.replace` — a crash mid-write could leave
  truncated JSON where `meal_history.json` was, and history can't be
  regenerated.
- `--use-cached-plan` exits with a clear message when there is no cached plan.

### The API boundary — five reads, and one write that answers with a job

`src/api.py`'s `build_api_router(repository) -> APIRouter` collects the bet the
previous section describes: `PlanRepository` was made fully `async` for a
future backend, and this is the first thing to actually reach it from outside
NiceGUI's own socket.

**It mounts onto NiceGUI's own FastAPI app, not a second server.** `nicegui.app`
*is* a `fastapi.FastAPI` instance, so `ui_app.py` does
`fastapi_app.include_router(build_api_router(REPOSITORY))` at module scope,
before `ui.run()`. No new port.

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

**Why `PlannerState` is not on it.** Every candidate method (`targets_for`,
`slot_views`, `day_context`, `planning_config`) reads per-client staged edits
that have no meaning outside one browser tab. A read route mirrors disk, not
what one tab has staged but not generated.

**What it deliberately does not expose, yet:** every write except generation
(session concepts, or standing settings with an owning surface); OpenAPI docs
(`nicegui` hardcodes them off — if TypeScript types are ever wanted they come
from that schema, not a hand-maintained `Recipe` copy); auth (localhost-only;
if ever exposed, a router dependency that *gates access*, not *scopes data*).

#### Generation over HTTP: a job id, because the event rate says so

Generation runs 30s–3min *per meal type* over NiceGUI's own socket, so an
HTTP shape was a real question (poll a job? SSE? WS?). **What answered it was
counting the events.** `progress_callback` fires once per meal type — a dozen
events across a quarter of an hour, not a streaming problem; a client polling
every few seconds sees every one.

**And the other two designs need the job registry anyway** — SSE and a
WebSocket both lose a run's history on a dropped connection, so both need
`generation_jobs.py` with a stream in front. Polling is the substrate; a
streaming route added later reads the same records. The WebSocket also has no
use for bidirectionality here (cancel is one message) and would be a second
connection lifecycle beside NiceGUI's own socket.io.

Four decisions about the shape:

- **The finished week is not on the job.** `generate_and_store_week` saves it
  through the repository first, so `GET /api/weeks/…` answers for it; a copy
  on the job would be a second answer free to disagree. The job carries what
  the *run* knew — stages started, portion notes, why it stopped.
- **The `POST` is `202` whatever happens next**, including a grid that cannot
  be generated (`validate_week` is the run's own first step). A rejected grid
  gets its own field (`WeekNotValidError` keeps `validation_errors` separate
  from `error`); a per-meal-type failure rides on a **succeeded** job.
- **`stages_started` is stages *started*, not banked** — `progress_callback`
  fires before each call, so `len(stages_started) == len(stages)` is not
  completion; `status` is.
- **The registry is in memory, one process** — exactly what NiceGUI serves on
  one Uvicorn worker. Two workers or the future backend, and the claim below
  moves to where the plan lives.

##### The guard

`PlannerState.generating` is per-client and cannot see an API run, so an API
client was a third racer for `week_plan.json`. `GenerationJobs.claim()` is the
one flag both consult (`ui_app.GENERATION_JOBS`, **required** on
`build_api_router`, not defaulted — a router with its own registry guards
nothing). Three things:

- **A plain field, not an `asyncio.Lock`** — claiming must *fail*, not queue;
  and `claim` reaches its assignment with no `await` between, so a single
  event loop cannot interleave.
- **A UI run is recorded but reports nothing** — `ui_generation.generate_week`
  reports through the progress dialog and swallows its exception, so the job
  releases with the default.
- **Module-level state — the documented exception to "state lives per
  client"**: this guards one file every tab and the API share.

**`generate_and_store_week` is why the route cannot drift from the CLI** —
`run_cli` calls the same function, so the three orderings that would drift
silently (training adjustments / grid, `resolve_auto_choices` / `validate_week`,
`save_week_plan` / `record_week_history`) have one home. The CLI's
peculiarities are seams (`spec_transform` for `--leftover-lunches`, `on_ready`
for the "Generating…" line). It is **not** the UI's path —
`ui_generation.generate_week` starts from a *staged* spec and must clear the
previous run's style/cuisine/pin/batch state and apply the batch toggles first.

**Two findings, both since fixed, kept for the lesson:**

- **`/api/recipes`'s filter had silently drifted from
  `ui_catalog_browser._matches`** — `_matches` treated `"All"` as the
  no-filter meal type and the route treated `None`, so `?meal_type=All`
  returned nothing while the Library grid returned everything, with no error
  either side (a differently-filtered list is a well-formed response). Both
  now call `repository.catalog_matches` (which imports nothing from `ui_*` and
  needs no `PlannerState`); `CATALOG_MEAL_TYPE_ANY` accepts both spellings.
- **`PlannerState.targets_for` could disagree with `/api/targets`** — the UI
  read static `weekly_schedule` and never hydrated. `planning_config()` now
  hydrates too (see "Who owns a number"). They still answer different
  questions on purpose: `/api/targets` reports what disk says, the header
  reports what *this tab* would generate, overrides no route can see included.

### Portion sizing — three layers, because models can't size meals

Measured on `google/gemma-4-26b-a4b-it:free`: asked for two meals totalling
1680 kcal, it returned 2564 — it composes plausible *dishes* but reaches for a
"full day" regardless of target. Three layers, in order:

1. **`split_targets()` gives each meal its own budget**, weighted by
   `config.meal_weights` over the slots being cooked. `meal_overrides` pins
   named meals verbatim (`fat_g` optional, else `derive_fat_g()`); what they
   consume comes off the day, the remainder splits by weight. An override that
   exceeds the day floors the rest at 0 and warns; a malformed one is dropped
   with a warning.
2. **`fit_recipe_to_budget()`** linearly rescales the response onto budget
   (every macro is linear in quantity), clamped to
   `planning_rules.portion_trim_limits` (0.6–1.6).
3. **`DayRecipes.reject_untrimmable_macro_miss()`** rejects only what layer 2
   *can't* rescue — a factor outside the clamp — so `instructor` retries.

**The threshold in 3 is derived from 2 — don't replace it with a standalone
tolerance.** A flat 25% killed a real 7-day run on day 7: two responses at
+62%/+43% (factors 0.62/0.70, well inside the clamp) were rejected, and the
third attempt hit a provider bug. A tolerance tighter than the trim's reach
rejects answers it could fix, at 30s–3min a rejection.

**What this does not fix:** wrong protein/carb *ratio* at the right calories —
a single scale factor can't change a ratio. It shows as a visible delta; if
protein is chronically low, change the model. Adjustments are surfaced via
`note_callback` under "Portion adjustments".

#### The cascade's end effect, and the cap on it

Generation subtracts each stage's **actual** output before splitting the
remainder across pending meal types. The end effect: the final stage (`snack`)
has one slot pending and inherits the whole accumulated brief-vs-actual
difference, and `apply_protein_floor` can't moderate it (it returns early
below two slots).

Calories self-correct (layer 2). **Protein does not** — a dinner 20 g light at
the right calories passes every check and hands 20 g down the cascade; three
of those brief a snack for ~90 g of protein in a 200 kcal slot, which
`reject_untrimmable_macro_miss` (calories only) does not catch.

`cap_to_weighted_share` bounds the briefed budget at
`planning_rules.max_meal_share_multiple` (1.75) × what the meal would have got
with no drift (`split_targets` on the day's full target, once per day). Every
macro scales by the same factor, to keep `calories ~= 4p + 4c + 9f`. Pinned
meals are exempt. **The capped surplus is dropped, not moved** — every other
meal is cooked — so the day lands visibly under target, the standing answer
whenever the numbers don't reconcile.

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

Every request sends `extra_body=reasoning_extra_body(model, config)`, which
sets `{"reasoning": {"enabled": False}}`, **except for models marked
`"reasoning_required": true` in `config/models.json`.** Do not change the
default to enabled. Measured on `anthropic/claude-sonnet-5`, same prompt:

| | reasoning on | reasoning off |
|---|---|---|
| latency | 303s | 16–19s |
| completion tokens | 32000 (hit the cap) | ~2200 |
| reasoning tokens | 6981 | 0 |
| finish_reason | `length`, **zero content** | `stop` (3/3 runs) |

It is intermittent (another attempt used 2149 reasoning tokens and
succeeded), which is what makes it nasty — two of seven days failed this way
on a real Sonnet run. Raising `max_tokens` does not fix it. The task needs no
deliberation, so the reasoning budget is pure cost. This is a *general*
problem, not free-tier — a paid frontier model hit it harder than gemma.

#### Some providers reject the disable switch outright

`google/gemini-3.6-flash` returns a hard `400` the moment `reasoning` is
present at all (`"Reasoning is mandatory for this endpoint"`), so
`instructor`'s `max_retries` burns three attempts and every slot fails within
a second. `"reasoning_required": true` on the model's `models.json` entry
makes `reasoning_extra_body()` omit the key entirely. If a newly picked model
fails every slot in under a second with that message, it needs the flag — on
its own entry, not a parallel id list that could name a model no longer
offered.

### Diagnosing a slow or failed call

`configure_logging()` (called from both `planner.main()` and `ui_app.py` at
import time) writes per-call generation timing to `logs/meals.log`: request start,
elapsed seconds, `finish_reason`, `completion_tokens`, and `reasoning_tokens`
for every `generate_day()` call, plus a line for any day that fails. This is
the same data the manual diagnostic below asks you to check by hand —
`reasoning_tokens` far above 0 or `finish_reason: length` in the log is the
signature of the reasoning-blowup failure mode, not a hung request.

### Picking a model

`config/models.json` names **two roles**: `meal_generation_model` (a week —
`generate_day`, `generate_meal_type_week`, `generate_sunday_prep_session`) and
`recipe_parser_model` (`import_external_recipe`, cheap and mechanical, does
**not** follow the generation model). Its `models` table doubles as the
Settings selectable list and the home for per-model quirks (`{}` if none).

`config["openrouter_model"]` is **not a file key** — the per-run selection
injected in memory by `--model` and the Settings model select, never written
to disk. There is no `openrouter_base_url` key; it is a constant in
`planner.py`.

Swapping the generation model has real gotchas (reasoning-token blowups,
free-tier churn, latency vs. the client timeout) — they live in the
`openrouter-model-choice` skill, invoke it before changing
`meal_generation_model` or the `models` table.

### Shopping lists

`shopping.py` aggregates cook events (not days) and normalises ingredient
names before combining them. Every normalisation rule and the bad line it
fixes are in `.claude/rules/shopping.md`. **Nothing loads that file
automatically** — despite its `paths:` frontmatter, which was measured to do
nothing — so read it explicitly before changing `shopping.py`. Converting it
to a skill the way the front end's was is the obvious next step.

`collect_unique_plants` rides on the same normalisation: distinct ingredients
in `PLANT_DEPARTMENTS` (Produce, Herbs & Spices, Nuts/Seeds & Spreads) across
cook events, stored on `WeekPlan.unique_plants` and shown as the header's 🌱
count. It reuses the shopping key so "Cucumber, diced" and "Cucumber, sliced"
count once. Recomputed by both narrow regenerations.

Duplicate *staples* are attacked from both ends. `PANTRY_CONSOLIDATION_RULE`
(in `build_generation_rules`) asks the model for one variant per staple and
says explicitly it is not the food-variety rule two lines above.
`shopping.CANONICAL_INGREDIENTS` catches what the model produces anyway
("Sardines (canned)" / "tinned sardines" / "sardines in water (tinned)" — one
purchase). It is deliberately narrow: an entry *asserts* two names are the
same thing, so a canonical name carrying a state only claims names whose own
state is absent or equivalent ("frozen sardines" stays its own line), and
exclusion lists keep "mustard seeds" out of mustard.

#### The order a list is walked, and the two lines that are not items

`DEPARTMENT_ORDER` is the walk — produce at the entrance, dry middle aisles,
chilled perimeter last. It replaces `sorted(shopping_list.categories)`, which
appeared in **seven** places agreeing only by accident;
`ordered_departments` is the one call all seven make now. It is deliberately
**separate from `DEPARTMENT_KEYWORDS`' order**, which is match precedence
(specific → general) and would pin Pantry first forever. An unnamed department
sorts after the named ones alphabetically.

**Google Keep turns every pasted line into a checkbox**, so
`format_shopping_list_keep` cannot emit a bare department name (it gets ticked
off next to the milk) and cannot use markdown or a blank line to separate
(both become junk items). The separation is typographic: `── DAIRY & EGGS ──`,
and a heavier `═══` rule for the `trip` name so two trips pasted into Keep are
distinguishable. `pantry_covered_line` is absent from this format and present
in every other — a line you are *not* buying has no business being a checkbox,
but naming it at home is what lets a stale pantry be noticed.

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

**The anchor is bookkeeping, not a decision.** Prep day has no slot of its own
in the grid, so the first day a batch is eaten holds the recipe and the rest
point back at it — the anchor is always day 1, and nothing searches for it.
Earlier versions *did* search: both toggles anchored on "dinner", competed for
the same slots, drifted (`spread_batch` only *adds* claims), and scheduled
Sunday-prepped food for Thursday. Two batches on two rows both starting at day
1 cannot collide.

**Prep day is not the Sunday on the grid.** The prep session runs the day
*before* `spec.days[0]` (`ui_cards.prep_day_column` draws it as an eighth
column), so `spec.days[-1]` is a full 7 days after it and nothing prepped
ahead is still food by then. `spread_batch`'s `exclude_target_days` =
`{spec.days[-1]}`; no batch may link into it (the anchor may still land there,
and a hand "Link to next lunch" from Saturday into Sunday is untouched — that
one is cooked Saturday).

Consequences:

- **An anchor that cannot grow is passed over, not picked and abandoned** —
  `spread_batch` filters candidates to days that can still reach a *claimable*
  slot, mirroring the walk's own conditions.
- **A batch may re-point a location link.** `_claimable` accepts a
  `LINK_ORIGIN_LOCATION` leftover as a target and re-points it (the rule says
  an Office lunch *is* a leftover, never whose); a `LINK_ORIGIN_USER` link is
  never taken.
- **A location link that *blocks* a near slot is released, not walked past.**
  `_releasable_dependants` frees a blocking dependant only when *every* one is
  a `location` link, returning it to a cook — the same fallback
  `apply_location_modes` already uses. A `user` or `batch` dependant is never
  released; the batch skips the slot.
- **`max_day_index` bounds the batch from prep day, covering the anchor.**
  `max_span_days` counts from the anchor's grid day and can't see that a
  prep-session batch is cooked before the week starts; day index `i` is `i+1`
  days after prep, so `apply_batch_selections` passes the **default** storage
  window's day-gaps minus one. It applies to the anchor too — an anchor
  outside the window is already unsafe.

`inventory_rules.fridge_safe_days` was **3**; it is now the dish-level
`storage_windows` (default 4 day-gaps) — see "Storage windows belong to the
dish". One global could only ever be wrong in one direction (fine for a stew,
two days too long for a rice tray bake).

**Everything downstream counts from prep day too**, and did not for a while —
`storage_note`'s `keeps_for_days`, the per-card fridge/freezer badge, and
`slot_views`' prep-time collapse all measured from the anchor's grid day (day
0), one short on every prep batch, which flipped `storage_note`'s
"refrigerate" / "freeze the rest" advice at exactly the fridge-limit batch the
freeze branch exists for.

`week.PREP_DAY_INDEX` (-1) is the fix, one idea in four places:
`week.cook_day_index(spec, day, prepped_ahead)` answers "which day was this
actually cooked on" and `span_days` takes the same flag. Callers supply it
from what they hold — `build_cook_event` from
`week.prep_day_batch_slot_ids(config)` (known before the first call;
re-exported under its old `planner` name), everything post-generation from
`planner.is_prepped_ahead(event, week_plan)`. Two lookups because
`generate_sunday_prep_session` runs after every cook event is built. Three
decisions:

- **`is_prepped_ahead` is `is_sunday_prepped` minus the shake** — the shake is
  only *portioned* ahead (blended fresh each morning), and `meal_type`
  separates it since the anchors are a lunch and a dinner.
- **`span_days` defaults to the anchor day, so `validate_week` is untouched** —
  that backstop bounds hand-built "Link to next lunch" chains; prep batches
  are bounded by `max_day_index`, and a prep-aware backstop would reject the
  very weeks `apply_batch_selections` builds.
- **`ui_state.apply_spec` and `swap_slot_with_favorite` had to move with it**
  because `scale_to_servings` rewrites the storage note on rescale.

`slot_views`' prep-time collapse asks `is_prepped_ahead` now, not
`event.meal_type == "dinner"` (a faithful proxy only while the long cook was
the sole anchor — bulk prep anchors on **lunch**).

`spread_batch` returns `None` for an anchor that never grew past a normal
dinner (an honest "no batch happened"). The chosen anchors ride on config as
`long_cook_anchor` / `bulk_prep_anchor`, telling `generate_meal_type_week` to
send the per-slot anchor directive and `generate_sunday_prep_session` there is
something to prep.

**`is_sunday_prepped` matches by `SundayPrepSession.candidate_slot_ids`, not
the model's `long_oven_cook`/`bulk_prep_friendly` self-report.** It used to
test those flags directly and broke both ways: a stray flag claimed the badge,
and — the one that bit — an anchor came back with both flags `False` despite
the directive to set one, so a real Sunday-prepped batch rendered as a
from-scratch cook. `candidate_slot_ids` is stamped onto the response after the
call (Python-only); an empty list (pre-migration session) falls back to the
flag check. A second bug on top: `ui_state.py` only called `is_sunday_prepped`
for `MODE_LEFTOVER` slots, so it never reached the anchor card or the shake
candidate — both genuinely part of the session. The anchor gets the badge and
its `prep_minutes` collapse; the shake gets the badge but keeps its own
`prep_time_minutes` (its base was prepped Sunday, but it blends fresh),
`event.meal_type == "dinner"` telling the two apart.

### Buying what the shops actually stock

A generated week called for **mustard greens** and fresh seafood a regional
Victorian town can't reliably supply. Availability is a third axis beside
cuisine and diet style. `schedule.json`'s `sourcing` block is the config
(beside `regional`, not `dietary_rules` — it is a fact about the shops), and
`regional`'s first real consumer, so "Coles, Woolworths or Aldi" reaches the
prompt qualified by "in VIC, AU".

| key | means |
|---|---|
| `supermarkets` | the shops a week is bought from, named verbatim in the prompt |
| `specialty_grocers_available_days` | weekdays an Asian grocer / deli / fishmonger / market is reachable — `None` (absent) is every day, `[]` is none |
| `fresh_seafood_available_days` | same `None`/`[]`/list shape, for a fresh fish counter |
| `max_seafood_meals_per_week` | whole-week cap on meals whose *dominant* protein is seafood; `None` is uncapped |

**`build_sourcing_rule` is the soft half of a hard/soft split** —
`banned_ingredients` polices what must never appear, this shapes what the
model reaches for over "not stocked within an hour's drive"'s unenumerable
tail. It sits after the banned-ingredient line in `build_generation_rules` and
emits nothing when `sourcing` is absent or permissive. The wording is a
**substitution instruction, not a prohibition** ("substitute the closest
supermarket ingredient and put the SUBSTITUTE in the recipe") — "don't use it"
invites the model to abandon the cuisine or name the item anyway.

**Availability can be day-of-week.** `build_sourcing_rule` takes `days` (the
cook days its caller's prompt covers) and `_sourcing_day_split` partitions
them into `restricted`/`open`; a call whose days straddle both gets a
day-scoped sentence, wholly-inside or wholly-outside gets the plain wording.

**The seafood cap is counted, not stated** — no single call sees more than its
axis, so "at most one per week" sent to all four permits four.
`generate_week_plan` counts each stage's returned `is_seafood_meal` and passes
the **remaining** allowance on in `MEAL_TYPE_PRIORITY` order (dinner first —
where the one seafood meal is wanted). `is_seafood_meal` reads the recipe's
**highest-protein ingredient**, not a name scan (which a Thai dinner's fish
sauce would trip), and applies to every meal type; counted per cook event.
`regenerate_single_*` send the sourcing rule but **not** the cap — a single
meal has no week to count against.

### Some slots are decided before the model is called

Four things now claim a slot ahead of generation, and the order they run in
is the order below. Everything they claim is one fewer recipe the model is
asked for, so a week with catalog recipes in it is also a cheaper week to run.

**1. Where you are that day** (`base_schedule` + `location_rules`).
`week.apply_location_modes` (called by `default_week_spec`) reads
`<meal_type>_mode` off the day's location: an Office lunch inherits the
previous day's dinner, a Holiday block skips all four. Two load-bearing rules:
it applies to a **fresh grid only** (a generated week's slots carry structural
edits), and `lunch_mode: "leftover"` must *resolve* to a source — a leftover
with no `source` fails `validate_week` — so it links to the previous dinner
and **falls back to cooking** on day one. `restrictions` reaches the prompt
per-slot via `build_location_note` (translated through
`LOCATION_RESTRICTION_PHRASES`). **A location only constrains the meals it
declares a `<meal_type>_mode` for** — `Office` names `lunch_mode` only, so
"must travel in a container" never lands on a Monday breakfast. There is
deliberately **no "no reheat" tag** — the office has a microwave.

**2. A morning gym session's breakfast**, pinned to a shake — see "Targets
come from the body".

**3. A recipe the user pinned for this week.** Outranks style/cuisine
rotation, the reuse window, batch targets and the dinner cap, but never
`banned_ingredients`, `allowed_nova_groups` or the storage window.
`SlotSpec.recipe_pin_origin` distinguishes it from an automatic favourite;
`clear_recipe_pins` removes automatic pins before a full-week run and
**preserves user pins**. Both claimants call `planner.recipe_eligibility_error`.
Session-only; writes to `week_plan.json` at generation, never config.

**4. A saved favourite** (`planner.select_favorite_assignments`).
`SlotSpec.recipe_id` carries the catalog id; the slot is **still a cook**, so
a fourth mode was avoided. Rules:

- **Breakfast**: one favourite across `favorite_breakfast_slots` (2) — a
  standing breakfast is the same one, one shop covers both. A
  `WORKOUT_BREAKFAST_STYLE` slot is skipped (shake is a hard rule).
- **Lunch**: one per eligible slot (Office lunches are already leftovers).
- **Dinner**: up to `favorite_dinner_slots` (2) **distinct** favourites,
  capped because `pin_recipe` blanks the slot's cuisine and uncapped against a
  large catalog every dinner becomes a pin and no `pick_cuisine_blocks` block
  survives. **Which days is `cuisine_run_ends`** (why selection reads
  `slot.cuisine`) — blanking a run's *last* day leaves the remainder
  contiguous where a middle day splits the block. No cuisines resolved →
  earliest-first, as lunch.
- **Snack**: nothing (`week_defaults.snack` is `skip`).

**A `long_oven_cook` dish may only take a day with the hours in it**
(`day_allows_long_cook`), read by both halves — `favorite_fits_day` (saved,
all three meal types) and `reject_misplaced_long_cook` (generated). Nothing
else stops a long cook landing on a Tuesday: `build_batch_roast_rule` never
sees a favourite, the placement rule is about cuisine blocks, and
`prep_limit_for`'s weeknight ceiling counts **active** minutes (a braise
honestly reports 20). Eight of 36 shipped dinner favourites are long cooks. An
ineligible run end takes the next eligible favourite (deferred, not dropped),
and the dinner cap counts **pins made, not run ends looked at**.

#### Which days those are: presence, not the calendar

`day_allows_long_cook` reads `location_rules.<location>.allows_long_cook` for
the day's `base_schedule` location, **falling back to the weekend** when the
location declares nothing (`week.location_rule` collapses all three "no rule"
cases to `{}`). It is **the other axis, not a second notion of the same
thing**: active minutes are a claim on your *attention* (weeknight vs weekend);
elapsed hours a claim on your *presence*, which `base_schedule` records.
`prep_limit_for` is untouched. **A location may rule a weekend day out** —
shipped, `Saturday: Outing` loses the long cook the calendar gave it while
Tuesday/Wednesday gain one; `Home` is now declared rather than relied on to
"happen to fall on a Sunday".

#### The generated side is enforced too, and needed a measured field

The generated path used to be soft (`BATCH_ROAST_RULE` a preference, nothing
rejecting a 4-hour braise reporting 25 minutes active prep), so a *saved*
braise could not take a Thursday and a *generated* one could — the worse
failure, invisible in any catalog record. `reject_misplaced_long_cook` is the
hard half, a `model_validator` on both response models (`DayRecipes` /
`MealTypeWeekRecipes`, the split `enforce_prep_limit` uses).

**Two ways to fail:** `long_oven_cook` (the model's self-report, often
omitted) and `Recipe.total_time_minutes` (the measured wall-clock claim
`ELAPSED_TIME_RULE` asks for, `None` = unknown never 0, catches the braise
that never flagged itself). `WEEKNIGHT_ELAPSED_LIMIT_MINUTES` (90) is a third
number, not a re-reading of the prep ceilings.

**A batch anchor is exempt** — `apply_batch_selections` anchors on day 1 but
the food is cooked on prep day, so judging it against Monday's schedule would
reject the dish most deliberately given the hours (same
`prep_day_batch_slot_ids`).

**The prompt names exactly the days the validator accepts**
(`build_batch_roast_rule(config, days)`, `build_long_cook_day_rule` in the
shared block) — a model rejected for a rule it was never given burns a
30s–3min retry. It **emits nothing when no day in scope qualifies**.

**A pinned dinner must be visible to the model generating the same stage.**
`avoid_proteins` is extended from `stage_events` only *after* a stage
finishes, so `generate_week_plan` appends the stage's own pinned proteins and
recipe names to what it passes down (without folding them into the running
list) — or a pinned lamb Thursday the model can't see lets a generated lamb
Friday through `DINNER_VARIETY_RULE`.

Eligibility is strict LRU over `planning_rules.favorite_reuse_days`
(`{"breakfast": 7, "lunch": 21}`). **`history_max_entries` moved 21 → 28 for
this** — it caps *entries*, so at 21 an aged-off favourite was
indistinguishable from one never cooked and the 21-day lunch rule stopped
binding just when it should start.

Three details that were bugs first: **pinning clears the slot's style and
cuisine** (`week.pin_recipe`, or a scramble renders as "YOGHURT BOWL"); **only
the unpinned days go to the model** (`_generate_meal_type_events` derives them
from `day_budgets`' keys, or a filled slot is paid for twice); **a favourite
is normalised to one serving first** (`planner.single_serving` — a 2-serving
dinner needs a 0.5 factor, outside `portion_trim_limits`).
`ui_generation.generate_week` calls `week.clear_recipe_pins` unconditionally
alongside `clear_styles`/`clear_cuisines` — selection only fills an *empty*
slot, so without the clear week one's favourites re-serve forever.

### A skipped meal that was actually eaten

`MODE_SKIP` contributed nothing anywhere, which is right for a meal genuinely
not eaten and wrong for the common case — dinner with friends, a working
lunch. Those calories are consumed, and a day that ignores them hands their
whole share to the meals it does plan, which come back oversized.

`SlotSpec.skip_estimate` (the four `MACRO_KEYS`, or None) makes such a slot
behave **exactly like a leftover**: `generate_week_plan` subtracts
`week.skip_estimate_totals` into `plannable_targets` before the split, while
`targets` (the telemetry denominator) is left whole; `WeekPlan.day_slot_macros`
adds the estimate to the numerator, so the header reads 100% not 60%.

`None` (not eaten) and an all-zero estimate (eaten, cost nothing measurable)
are deliberately different. The card's "Eaten out?" button seeds from
`PlannerState.default_skip_estimate` (what the slot would have been briefed at)
as a starting point. Calories/protein/carbs typed, fat derived. **Fibre is not
part of a skip estimate** — the fibre in a meal nobody cooked isn't estimable.

### Fibre is targeted, and still has no term in the energy identity

**`MACRO_KEYS` names the keys with a term in `calories ~= 4p + 4c + 9f`, not
the keys with a target.** Fibre now has a daily target, a per-slot share and a
header denominator — and is still not in `MACRO_KEYS`. The rule:
**identity operations walk `MACRO_KEYS`, proportional ones may walk
`NUTRIENT_KEYS`** (`MACRO_KEYS + ("fiber_g",)`). A portion trim, a recipe
total and a per-slot share are all linear in quantity and care nothing for the
identity — which lets fibre take a briefed share of a day without entering a
single budget check.

#### The target: a floor, raised by a big day

`nutrition_engine.calculate_fiber_target_g(calories, floor_g)` is
`max(floor_g, calories / 1000 x FIBER_G_PER_1000_KCAL)` — 14 g/1000 kcal is
the dietary reference, `user_profile.fiber_floor_g` (30 g) the preference.
**The floor is load-bearing**, same reasoning as protein locked to target
weight: scaled alone an 800 kcal day asks for 11 g, cutting the target exactly
as the deficit starts to need the satiety and gut fibre; the energy term may
only ever *raise* the figure.

Four decisions about where it is computed:

- **Not inside `calculate_macro_targets`** — that returns `calories` before
  `hydrate_dynamic_targets` replays the uplift and applies the diet-style
  ceiling, so a figure computed there is wrong on every day that moves. It is
  its own function, called once the calorie figure is final.
- **Two callers, both final** — `hydrate_dynamic_targets` and
  `calculate_daily_targets`, one function so the engine and file paths agree.
- **Every hydration path writes it, including the three that give up**
  (`with_fiber_targets` fallback — fibre needs only calories and a floor,
  neither a fact about the body), so `/api/targets` and the header cannot
  disagree.
- **No `fiber_g` on `DaySchedule`, no `target_modes` entry** — a key the file
  writes and the app ignores is a second place for a number to be wrong, and
  `TARGET_MODE_MACROS` is for macros with *two* sources; fibre has one.

#### The per-slot share, and the two things it deliberately is not

`split_targets` gained a `fiber_target_g` keyword and a fourth pass
(`split_fibre_share`) after the protein floor; omit it and no budget carries
`fiber_g`, byte-identical to before.

- **A `meal_overrides` pin does not pin fibre** — an override is a fixed
  *energy* budget; a pinned meal takes its weighted fibre share like any other.
- **No fibre counterpart to `apply_protein_floor`** — fibre is not
  dose-limited per meal, so three meals reaching the day's figure is enough.
- **The share does not cascade** — the one non-obvious decision. A calorie
  budget is a share of what is *left* after each stage; a fibre target is a
  goal, and models come back fibre-light far more than heavy, so cascading
  would pile the shortfall on the last meal type (the failure
  `cap_to_weighted_share` bounds for calories but a portion trim can't for
  fibre). `generate_week_plan` reads each slot's share from `apriori_budgets`
  (the day's full target, split once), so **a meal's fibre brief is the same
  number whichever stage generates it**. A short meal leaves the day visibly
  short (`FIB 24/30g`) — show the gap, don't distort a meal.
- **A skip estimate carries no fibre**, so a skipped-but-eaten slot leaves the
  goal whole and is not in the denominator.

#### There is still no validator, and the prompt rule reversed

Nothing rejects a fibre-light response — a rejection is a 30s–3min retry, a
scale factor can't change a ratio, and nothing downstream could act on it (the
`diet_styles`/`sourcing` reasoning). `FIBER_REPORTING_RULE` is now
`FIBER_TARGET_RULE`. **The clause that survived the rewrite is about trading**:
the four macros are the budget that *is* checked, so a recipe buying 6 g of
fibre with 200 kcal of lentils passes no validator — the rule forbids trading
any of the four against fibre and names the mechanism, **substitution at
constant macros** (wholegrain for refined, legumes for some starch, skins on).
"Don't trade" alone leaves a target with no permitted way to reach it, which
a model drops entirely. `fiber_g` defaults to `0.0` (keeps old recipes
loadable) and the rule asks for it explicitly, since that default otherwise
produces a silently fibre-free week.

#### The one number that now has a measured counterpart

`CRONOMETER_MACRO_COLUMNS` now captures fibre from the daily-summary export.
The header prints `FIB 32/38g` and, beside it, `logged 24g` for a day
Cronometer has a figure for. **`planned/target` takes a divider** (the
`actual/target` shape, honest now the planner aims at a number); **`logged`
sits beside it, not under** — it is the same quantity measured a second way,
and `32/24` would read as a missed goal. `ui_state.fibre_view` is the pure
function holding both rules; `delta` stays signed against the *plan*.

**Capture and readout had to land together** — capture alone reproduces the
sleep-data shape (fetched, stored, read by nothing), which is why **an entry
in `CRONOMETER_MACRO_COLUMNS` must assert something reads it** (keeping
sodium, potassium and the micros out). Three things it does not do:
`MACRO_KEYS` untouched (`fiber_g` rides `NUTRIENT_KEYS` on both sides); an
absent column is **omitted, never zeroed**; `PlannerState.logged_actuals_for`
matches by **date** (a loaded `WeekPlan` has `week_start_date`), where
`planner.logged_intake_for` refuses every day but today.

### Leftovers can't outlive the fridge

How long a leftover may sit is now enforced four times, and the split matters:

- **From the anchor** — `week.spread_batch` takes `max_span_days` and stops
  its forward walk there.
- **From prep day** — `max_day_index` bounds the same window for a batch not
  cooked on its anchor day (`max_span_days` alone let Sunday-cooked food reach
  Friday).
- **At the brief** — `build_storage_rule` names the span each slot needs,
  `reject_short_storage_class` rejects a dish that can't meet it.
- **Backstop** — `week.storage_safety_errors`, read by `validate_week` before
  generation and again by `generate_week_plan` after, for a hand-built or
  imported `week_plan.json` that never went through `spread_batch`.

Bounding the spread rather than only rejecting the result is the difference
between never creating the problem and refusing to generate a week the planner
just built.

#### Storage windows belong to the dish, not to the config

`inventory_rules.fridge_safe_days` was **3**, one global read in six places,
**wrong in both directions**: a beef stew keeps 4, a rice or pasta dish keeps
**2** (*Bacillus cereus* spores survive cooking), so 3 threw away good stew
and let a prep-day rice tray bake be eaten a day past safe — on every week the
long-cook toggle ran. The lengthening (3 → 4) and the dish-level exception
landed in **one change**, pinned together in `tests/test_food_safety.py`: a
permissive change riding on a safety one must be asserted, not discovered.

`inventory_rules.storage_windows` holds two tables and `Recipe.storage_class`
says which row a dish takes (a model self-report like `long_oven_cook`). **Not
a preset key** — a preset over one global could only pick a different wrong
global. **The tables are hours; every consumer holds a date**, so
`week.storage_day_gaps` (`// 24`) is the one conversion and **no surface
prints hours**. The day figure is not written into config beside the hours.

##### Every default fails short, which inverts the house rule

Everywhere else an absent value resolves to *behaviour before the feature*;
here it is a food-poisoning risk, so `week.storage_window_for` resolves
anything unrecognised to the table's **shortest** row. (`is_sunday_prepped`'s
dropped-self-report bug is the precedent — if a dropped `storage_class`
resolved long, a model forgetting a field would schedule a rice dish four days
out.) Four decisions:

- **`None` ≠ `"default"`** — `"default"` is "an ordinary cooked dish" (4);
  `None` is nobody said (2).
- **A recognised class the *fridge* table doesn't name takes `default`** (only
  rice is exceptional there; the freezer table has a row per class).
- **The shortest is `min` of the table**, so a shorter class added later pulls
  the unclassified case down with it.
- **A configured table *merges* over the shipped one** — replacing meant a
  config naming only `fridge.default` silently lost the rice exception.

##### The ordering problem: tell the model the span, then check the answer

**The grid is built before any recipe exists**, so it is planned against the
**default** window and the dish-level answer is enforced at the brief (the
`build_batch_roast_rule` / `reject_misplaced_long_cook` shape):

- `planner.storage_spans(spec, config)` computes slot id → day-gaps once and
  rides on `config["storage_spans"]` in memory (the `nudge_foods` channel).
  **One dict, three readers** (brief, rejection, favourite gate).
- `build_storage_rule` emits **only** when a span exceeds the short window,
  grouping slots by the figure they need (the validator judges each against
  its own span).
- `reject_short_storage_class` is the hard half, the two-axis
  `DayRecipes`/`MealTypeWeekRecipes` split, one shared function.

**The batch anchors are *not* exempt here**, unlike in
`reject_misplaced_long_cook` — that rule is about the *day* judgement (wrong
for prep-day food); this is about the *window*, and their span is measured
from prep day and is *longer*. `storage_safety_errors` reads
`prep_day_batch_slot_ids` too.

**A pinned favourite is a third route to a long span** that neither the brief
nor the response validator can cover (a favourite is never generated).
`favorite_keeps_long_enough` is the gate, a sibling of `favorite_fits_day` —
how long the dish keeps is a different axis from attention and presence.

`Recipe.storage_class` is a plain `Optional[str]`, **not a `Literal`** (a typo
would be a hard retry; an unrecognised string is unclassified and resolves
short, and the span validator still catches anything genuinely too short). It
is **never derived** — not from `total_time_minutes`, `long_oven_cook` or the
ingredient list ("contains rice" fails in the unsafe direction).

##### The freezer half is about quality

`week.freezer_months` / `week.freezer_quality_note` are the resolver
`data/freezer.json` will import when the freezer ledger lands — written once
here. Three rules: frozen food **degrades, not becomes unsafe** ("past its
best", never "unsafe"); an **undateable lot is flagged**, never assumed fresh;
**nothing is auto-removed** from a hand-declared list.

### Nudging generation toward whole foods

`reference/whfoods.json` is a 130-entry corpus of nutrient-dense whole foods.
`select_nudge_foods` samples 12 **once per run** onto `config["nudge_foods"]`,
and `build_slot_brief` names that dozen in every slot's brief. Per run, not
per slot — one consistent dozen reads as a theme. It is a priority ("where
flavour profiles permit"), not a constraint; an absent/empty `whfoods.json`
→ `[]` → say nothing.

**The sample is filtered through `banned_ingredients` first.** whfoods.json is
location-blind (Mustard greens, Halibut, Cod beside broccoli), and
`build_slot_brief` puts the sample under "prioritize incorporating these" — a
stronger signal than any `build_generation_rules` rule — so unfiltered it
asked for cod two lines after forbidding it. Filtering in `select_nudge_foods`
(not pruning the corpus) keeps whfoods.json reusable and makes
`banned_ingredients` a **single lever**. Matching mirrors
`Ingredient.reject_banned_ingredients`.

### Using up what's already in the house

`config.inventory_to_clear` is a list of things to cook through in **two legal
shapes**: a bare string (unquantified) and `{"item": "chicken thighs",
"quantity_g": 600}` (the ledger can reason about). Both stay legal — a
quantity is genuinely unknown for some things. `inventory_entries()` is the
single parser and drops a malformed entry with a warning (a config typo must
not cost a week). Grams not counts (the ledger spends against
`Ingredient.quantity_g`; a tin of tuna is ~95 g drained).
`inventory_instruction()` is one prompt line per day, empty list → nothing.
It is a **priority, not a constraint** — forbid the model from bending style,
cuisine or budget to use an item up, or it wedges chicken thighs into a shake.

#### The ledger: a count each stage spends and passes on

One tin of tuna could be written into five recipes because every meal type saw
the whole pantry — the `max_seafood_meals_per_week` shape, fixed the same way:
**no single call sees more than its own axis**. `seed_inventory_ledger(config)`
builds `{item: grams}` from the quantified entries only; `generate_week_plan`
seeds it once, publishes it as `config["inventory_ledger"]`, and calls
`spend_inventory` on each stage's actual output (a dict where `seafood_used` is
an int — an item runs out without taking the others with it). Four decisions:

- **It never reaches disk** — a surviving count disagrees with the shelf the
  moment you cook something untracked (the unpersisted-checkbox problem). It
  rides on `config` in memory (the `nudge_foods` channel).
- **Matching is `shopping.ingredient_draws_on`** — *containment* of the pantry
  item's words ("chicken thighs" ⊂ "Chicken thigh fillets, diced"), guarded by
  department and any state the pantry item names. Failing to match is the safe
  direction (the item stays unspent).
- **Counted per cook event** (`CookEvent.recipe` is already batch-scaled), the
  same call `is_seafood_meal` makes.
- **Overshoot floors at 0, not recorded** ("the item is gone", not "you owe
  200 g"); an exhausted item drops out of the prompt line entirely.

`regenerate_single_*` seed no ledger — every entry is simply named, like
`build_seafood_limit_rule`.

**The shopping list subtracts the pantry too.** `shopping.apply_pantry`
subtracts at **render time from `inventory_to_clear` itself**, never a stored
count — the ledger still dies with its run. The two are different questions of
one list: the ledger is how much a *generated week* reached for, the list is
what is still worth buying, derived fresh each repaint. A quantified entry is
**subtracted** (600 g against an 800 g line leaves 200 g; against a 400 g line
it covers it, and the line moves to `ShoppingList.pantry_covered` and is named
rather than silently vanishing); an unquantified one is **annotated only**.
Three rules: one ingredient draws on **at most one** entry; an entry is a
budget **across the whole list**, not a per-line discount; a remainder is a
remainder however small. `apply_pantry`'s callers: `PlannerState.shopping_view`
(from the *staged* rows), the CLI's `window_shopping_list`, both exports.

The drawer's Pantry section is a row editor (item + grams + remove) — a chip
cannot hold two fields — with `PlannerState.pantry` carrying
`{"item", "quantity_g"}` rows seeded through `inventory_entries`. Typing
refreshes nothing; add/remove refreshes `"pantry"` (the staged-bar count).

### Rejection capture

Hitting the regenerate icon used to be a pure discard. Favourites capture the
positive signal; this is the negative one.

**`planner.RejectionEntry`** (`date`, `slot_id`, `recipe_name`, `reason` ∈
`too_much_prep`/`dont_fancy_it`/`had_it_recently`/`wrong_for_slot`,
`marked_at`) is **appended** to `data/rejections.json` — an event log, not an
upsert (`_append_rejection` has no merge key): regenerating a slot twice
records twice. A separate file from adherence — a rejection happens *before* a
recipe becomes the plan, and two signals in one file with one key overwrite
each other silently (the `weigh_ins`/`daily_actuals` reasoning).

**Soft guidance, never a validator.** `build_rejection_rule(config)` reads
`config["rejected_preferences"]` (injected like `nudge_foods`) and asks the
model to avoid the named dishes and weigh a recurring reason. In
`build_generation_rules` after `build_diet_style_rule`. **Loaded at all three
generation entry points** — a regenerated meal must reach the very next call.

**Captured alongside the retry, never in front of it.**
`ui_generation.regenerate_meal` holds the discarded name; once the new recipe
lands, a `fixed`-positioned prompt (four reason buttons) offers to record why,
and an ignored prompt records nothing. Not `ui.notify`'s `actions` (forwards
to Quasar as JSON, no Python handler to bind); not `ui.dialog` (modal,
contradicts "never in front of it"). Scoped to the per-card regenerate and the
swap dialog, not day-level (four recipes) and not a NOT_GENERATED card.
`offer_rejection_prompt` is on `GenerationHandles` so `ui_cards.confirm_swap`
can reach it too, capturing the outgoing recipe before the swap mutates state.

#### The decay: two signals, two windows

The rule was always carrying two:

| | what it is | window |
|---|---|---|
| the **dish list** | a veto on one recipe | `planning_rules.rejection_decay_days`, **per reason** (21–180 days) |
| the **reason tally** | a standing statement about how you want to eat | `planning_rules.rejection_reason_window_days` (180) |

**Per reason**, like `favorite_reuse_days`: "had it recently" is self-resolving
and expires at 21 days; "wrong for that meal" is structural and barely decays
at 180. A dislike honoured forever starves the rotation the way an
"unused in the last N" rule starves a list's tail. The tally's window is the
longest of the four so it outlives the dish names it was counted from
(`active_rejections` and `recurring_rejection_reasons` are the two pure
halves).

**The tally is counted in Python** now — with different windows the model only
sees the shorter list, so it can't notice a repeated reason itself.
`REJECTION_REASON_GUIDANCE` (the standing instruction a *run* implies) is split
from `REJECTION_REASON_LABELS` (what one entry was about);
`REJECTION_REASON_SIGNAL_MIN` (3) is a run, a module constant not a config
key. Three decisions: **no storage change** (every entry already carried its
`date`); **an undateable entry and an unrecognised reason are both kept** (a
hand-edited file, and discarding a stated preference is the worse failure) but
kept out of the *tally*; **`build_rejection_rule` takes `today`** (the
`select_favorite_assignments` seam, so tests age entries without the clock).
Fully aged off → `""`, byte-identical to before.

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

**Three statuses, not a boolean** — a *skipped* meal came short of a target,
a *swapped* one is a day fed by something else; an "eaten?" flag collapses
those and the 7-day readout could not tell a missed dinner from a dinner out.

**One file, two separate *lists*** (the `weigh_ins`/`daily_actuals` reasoning
again — two signals in one keyed row overwrite silently). **The key is `date`
plus one more field** — `ADHERENCE_SECTIONS` names it per section, so one
`_upsert_adherence` serves both. **A mark is an update, not an event** (vs
`save_rejection_entry`'s append — re-marking is a correction).

**Un-marking deletes the row** — absence ("nobody said") and a status
("somebody said") are different answers, so no fourth `unknown` status.
Clicking the status a slot already carries clears it. **A day with no calendar
date cannot be marked** (`slot_id` is a weekday name; without `week_start_date`
there is no key), and marks are matched by **date**, never weekday. **A
skipped slot is not markable** and is out of the day's denominator (nothing
was planned there); a leftover and a failed slot *are* markable.

**Not an input to generation** — a mark is a record of a day, and what to do
with a run of skipped Thursdays is a product question. Nothing in
`planner.py`'s generation path reads `adherence.json`; Insights *reports* it.

#### The workout half is mostly derived, and only the gap is stored

`activity_log` already records what Garmin did each date, so "did the declared
session happen" is a two-list question. `nutrition_engine.match_recorded_sessions`
is that read — pure, per-date counterpart to `propose_training_schedule`, same
module because both speak `GARMIN_SESSION_TYPES`. Three matching decisions:
**type + date are the claim, the clock only breaks ties** (a 06:30 session
started 07:10 is the same session); **each declared session claims the nearest
*unclaimed* recording**; **an unmapped activity answers nothing** (a yoga
class is not evidence a lift happened).

**Only the gap is stored** — a `WorkoutCompletion` is written *only* for a
session the watch never recorded. `PlannerState.mark_workout` refuses one for
a recorded session (a stored row beside `activity_log` is a second answer);
Garmin wins if both somehow say yes. `ui_state.workout_marks_view` lays the
stored half over the derived one.

#### The affordance

`ui_adherence.py` is one module (`build_adherence(ctx)`, two click handlers),
its own because two surfaces raise these marks and only one is a card. **The
mark row is a *sibling* of the clickable body** (the `ui_cards.meal_card`
pattern) — a mark button under a whole-card click handler opens the recipe
dialog on top of the mark. **Glyph carries all of it, no hue** —
`check_circle` / `remove_circle` / `swap_horiz` for the three marks,
`check_circle` vs `task_alt` for Garmin-recorded vs hand-marked. **Marks
persist on click and do not stage** (not an input to generation; writes to
`data/`, so outside the "two places write to `config/`" rule). Repaint topic
`"adherence"` (not `"plan"`, which would rebuild the whole canvas per tick).
The day's "2 of 3 marked" line is silent until something is marked.

### Insights: five readouts, each gated on its own precondition

The Insights destination was an honest empty state for five releases
(CHANGE-QUEUE.md's trend-charts item is blocked on runtime data). The charts
were built anyway, because waiting had a cost the stub demonstrated — it
printed the counts and named the rule without evaluating it, the v0.30.0
adaptive-estimate failure. So **the page evaluates rather than describes**:
each readout is an `InsightPanel` (`state`, `headline`, `detail`) and
`ui_insights.py` draws the chart only where `drawable`.

| readout | drawn from |
|---|---|
| weight against target | `biometrics.weigh_ins` + `user_profile.target_weight_kg` |
| the weigh-in table | the same windowed rows as the chart above it |
| planned against logged | `meal_history.json`'s per-day `targets` × `daily_actuals` |
| macro accuracy | the same pairing, as means, `MACRO_KEYS` only |
| adherence tiles | `adherence.json`'s `meals` + `workouts`, and `activity_log` |

**Four states, not a `ready` flag** (`AdaptiveTDEEStatus`' reasoning):
`INSIGHT_EMPTY` (nothing recorded), `INSIGHT_SPARSE` (< `INSIGHT_MIN_POINTS`,
no line drawn), `INSIGHT_THIN` (drawn, < `INSIGHT_THIN_POINTS`),
`INSIGHT_READY`. Empty and sparse spell identically as a missing chart and
have different fixes. **Thin is drawn** — the worry is the axis, not the
points, and a window anchored on the data's last row and captioned
`6 point(s) across 5 day(s)` cannot mislead the way a fixed 30-day axis with
six dots in a corner does.

**`paired_intake_days` is the one join** where planned meets logged (both
charts call it): the *last* history entry for a date wins (a regenerated day
has two), a zero-calorie logged row is not a pairing, a *partly* logged day is
kept as a shortfall.

Four chart decisions, all **a chart may not claim more than the data
supports**:

- **The target line is drawn only when in view** — the weight y-axis is scaled
  to the weigh-ins (a zero-based axis flatlines a 0.6 kg week 99 kg up and
  clips the target); `WeightTrendPanel.target_in_range` gates the line, the
  caption states the gap either way.
- **Macro accuracy is a percentage axis** (2000 kcal and 79 g can't share a
  value axis); the dashed rule at 100% is the plan.
- **Fibre appears only when *every* paired day in the window states a
  `fiber_g`** — a mean over the pre-target days would read as a 0 g plan
  massively overshot.
- **The adherence denominator is *marks*, and says so** — the plans those
  dates ran against are gone from `week_plan.json`. The tiles have no
  `INSIGHT_MIN_POINTS` gate (three marks is a true statement about three
  marks). The adherence tile is the thinnest — a mark exists only from a
  click, so "have I been marking" is its own precondition.

**No chart introduces a hue** — `CHART_MACRO_COLOURS` is `MACRO_TINTS`
(categorical: which macro), a logged bar takes its `BAND_COLOURS` fill
(semantic: how the day went), everything structural is slate, and the planned
series is the *dashed* one throughout. **A legend swatch comes from
`itemStyle`, not `lineStyle`** (a line coloured only through the latter gets
ECharts' default-palette chip — measured); the intake chart has no legend
(bars banded per day, one chip can't stand for five fills).

`nutrition_engine.measure_weight_trend` is the engine half so the chart and
the estimate can't disagree about a slope. It returns the raw weigh-ins,
`smooth_series`' line, **and** the least-squares rate — kept apart because
reading a rate off a smoothed series understates a noise-free decline by 26%.
Two things it does not share with `measure_adaptive_tdee`: a 30-day window,
and the raw slope's sign — **negative while losing**, where the estimate
negates it into "kg lost per day". It is also the reader `smooth_series`
never had.

The panel is `@ui.refreshable` on `"plan"` **and** `"adherence"`, making
Insights the third member of the `"adherence"` topic.

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

Ten things here are decisions, not detail:

- **The only code in `src/` in a subdirectory**, so the `sys.path.insert` near
  the top of `sync_service.py` is load-bearing — `python
  src/integrations/sync_service.py` puts `src/integrations/` on `sys.path[0]`,
  not `src/`.
- **Macro keys are the repository's, not the upstream's** — `daily_actuals`
  rows are indexed `protein_g`/`net_carbs_g`/`fat_g` by
  `calculate_macro_targets`, so a row keyed the CSV's way (`protein`) stores
  and displays fine and feeds *nothing*. `fiber_g` follows the same rule:
  **an entry in `CRONOMETER_MACRO_COLUMNS` must assert something reads it**
  (keeping sodium, potassium and the micros out).
- **Exercise calories are discounted by `EXERCISE_RECOVERY_FACTOR` (0.50)** —
  Garmin's gross figure includes the BMR that hour, which every TDEE the app
  computes already contains. Both `gross_calories` and `net_calories` are kept
  (a silently adjusted number can't be reconciled). The knob is
  `config/integrations.json`'s `garmin.exercise_recovery_factor`, injected at
  construction — the *only* genuine knob the sync has, and the file's reason
  to exist.
- **Sleep and HRV never reach an energy equation** — `fetch_readiness` stores
  a sleep score, sleep hours, an HRV figure and a bucketed word in
  `readiness_log`, a separate list (a sleep score is unitless, HRV is ms — no
  kcal conversion is legitimate). Nothing reads it yet. HRV is
  `get_hrv_data`'s `hrvSummary.lastNightAvg` (not `weeklyAvg` — the row is
  date-keyed). Sleep and HRV are two endpoints **caught separately**
  (`save_readiness_entry` merges by date, so a failed half lands on a
  re-sync).
- **One checkpoint per source, not per list** (`BIOMETRIC_SECTION_SOURCES` is
  one-to-many) — `get_sync_date_range` folds a source's lists together before
  taking its latest date (ranking apart re-walks days Garmin already
  answered), and `ui_state.sync_status` names cards by section. One wrinkle: a
  date checked before a list existed reads as "checked, nothing recorded";
  `--date` re-syncs it. **That wrinkle cost `activity_log`** — measured
  2026-09-01, zero rows against 27 recorded activities, because
  `save_activity_entries` shipped after the checkpoint had already marked
  those dates asked. A `--date` backfill fixed it. **Diagnose by fetching
  before the filter**: a hiding checkpoint and a rejecting `_storable` look
  identical in the stored file, but the CLI prints every dropped activity with
  a reason — one `--sync-garmin --date <a day you trained>` separates them.
- **Recorded activity is stored** (`activity_log` for
  `propose_training_schedule`), giving `net_calories` its first consumer.
  `fetch_activities` is unfiltered; `fetch_cardio_activities` is now a filter
  over it, not a second fetch. Replaced per date, not merged.
- **Absent metrics are omitted, never zeroed** — `save_biometric_entry` merges
  on `date`, so a scale reporting only weight must not send
  `body_fat_pct: 0.0`. `has_measurements` counts the *measured* keys, not
  `len(entry)` (which the `source` tag alone fooled — an empty weigh-in was
  handed back as the latest reading).
- **Cronometer is fetched a span at a time; Garmin a day at a time.** One
  Cronometer day is ~5–7 HTTP requests (`export_raw` calls `authenticate()`,
  which re-mints tokens even resuming a session), and a per-day client was
  itself provoking 429s. The export endpoint takes a real `start`/`end` span,
  so `fetch_range_summaries` asks once and `_daily_summary_row` folds the CSV
  into each date. Two consequences: **one request has one outcome** (a
  Cronometer failure isn't isolable to a date any more — cheap, since every
  wild failure was an account-level 429), and **`_daily_summary_row`'s
  undated-row fallback is only sound for a single day** (`single_day_request`
  is the guard). Garmin keeps its per-day loop for real failure isolation.
- **`--date` means that day, not "catch up to that day"** — `--date` and
  `--catchup` both default to `None`, resolving to "catch up unless a date was
  named".
- **Cronometer is reverse-engineered** — no public API; `cronometer-mcp`
  drives GWT-RPC and re-discovers build hashes per login (survives a web
  release). Needs a paid tier and Python ≥ 3.11 (now satisfied in-process; the
  `venv-cronometer/` subprocess bridge is deleted).

**The saved Cronometer session expires and the client cannot tell**, so
`CronometerSyncService._fetch_rows` clears it and retries **once**. GWT-RPC
answers an expired session with HTTP **200** and a serialized exception in the
body, so `raise_for_status()` passes and the token regex grabs the exception's
class name (`...NotLoggedInException/844385496`) and sends it as the export
nonce; the 403 that comes back reads like a credentials problem against a
`.env` that was right. Three things: **recognised only after the fact** (403 +
marker, deliberately not any auth-shaped 4xx — never re-login on a 429);
**exactly one retry, only when a session file existed** (a run with no cache
already logged in fresh, so its failure is real); **the path is asked of the
client** (`_session_cache_path` reads `_cookie_path` off a constructed
`CronometerClient` — a guessed path deletes somebody's file).
`_stale_session_hint` translates the unreadable message.

**garminconnect's two lines disagree about token persistence** — 0.2.8 (Py
3.9) exposes `.garth` and leaves the token dump to the caller; 0.3.x removed
it and persists them itself. `GarminSyncService.client()` guards the dump with
`hasattr(client, "garth")`; calling it unconditionally is an `AttributeError`
on the *first* login under 0.3.x only.

Credentials come from `.env`. Garmin auth resumes cached tokens in
`~/.garminconnect` and falls back to the password only on failure — Garmin
rate-limits and MFA-challenges repeated password logins, so a timer-driven
sync logging in fresh every run would start failing after days.

#### Nothing syncs from the app — `scripts/sync.sh` does, on a schedule

`./scripts/sync.sh run` is the CLI above with both sources and no `--date`,
and `./scripts/sync.sh install` writes a launchd agent that runs it daily
(07:30 by default; `MEALS_SYNC_HOUR`/`MEALS_SYNC_MINUTE` at install time),
logging to `logs/sync.log`. `uninstall` and `status` do what they say —
`status` also prints each source's stored checkpoint, which is the same field
the Settings dialog reads.

**A bare run targets yesterday, not today** — a correctness rule, not a
scheduling preference. A day is complete only once it is over; a same-day
fetch checkpoints a half-empty day, stranding everything logged or recorded
later (`get_sync_date_range` never re-requests a checkpointed date). `--date`
still means the day it names.

**Neither the server nor any page ever triggers a sync** — the decision, not
an omission. Three options: on server start (a Garmin outage inside the UI
process, nothing on days the server never starts); a scheduled job (zero sync
code in the app, failures in a log); a Settings button (the integrations rows
are read-only). The scheduled job was chosen. **The rate-limit worry was
already handled** — `sync_checkpoints` + `get_sync_date_range` resolve a
second same-day run to an empty range, so the schedule can be dumb.

**`ui_state.sync_freshness` says when the job stopped**, above the sync
dialog's cards. Two questions: **is anything running** (the *newest*
checkpoint across all sources; `SYNC_STALE_AFTER_DAYS` is 2, since a
yesterday checkpoint is normal all morning) and **is one source failing while
others advance** (a source's own checkpoint that far behind the newest —
different fix: reload the agent vs. re-auth). It reads `sync_checkpoints` and
**never the stored rows** (unlike `sync_status`) — a scale nobody stood on
records nothing while the job runs fine. No colour.

Tests are `tests/test_sync_service.py`, no network — the fakes speak the real
payload dialect because the unit and key mapping *is* the module.
**The credential guard was written after a bug**: both constructors read
`username or os.environ.get(...)`, and `""` is falsy, so
`CronometerSyncService(username="")` — the test proving the guard fires
*before* any call — silently got the real `.env` credentials and hit
cronometer.com on every suite run. `_from_env` now distinguishes `None` from
`""`, and `TestCredentialGuards` runs against a populated fake environment.

### Bootstrapping the catalog from Google Keep

`src/integrations/keep_import.py` is a **once-off**: the recipes that have
been sitting in Google Keep under one note colour, pulled into
`data/recipes_master.json` so `select_favorite_assignments` has something to
claim slots with. Run `--colors` first, always:

    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --colors
    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --color CERULEAN --dry-run
    ./venv/bin/python src/integrations/keep_import.py --takeout ~/Downloads/Takeout --color CERULEAN

It parses through `import_external_recipe` and writes through
`repository.import_recipe`, so an imported note gets the same NOVA and
`banned_ingredients` rules and lands as an ordinary catalog entry.

- **It reads a Takeout export, not an API** — `keep.googleapis.com` is
  Workspace-only, and `gkeepapi` (the mobile protocol behind a `gpsoauth`
  token) is not worth its cost for a once-off. Only `load_notes` is
  Takeout-shaped.
- **Keep's UI colour names are not the Takeout values** (Storm is `CERULEAN`),
  and they don't correspond in any guessable order — `--color` takes the raw
  enum, `--colors` prints what's present. `KEEP_COLOR_LABELS` is a display
  hint, not what the filter matches on.
- **A note that fails to parse doesn't end the run** — failures are named for a
  `--force` re-run.
- **Titles are checked against the catalog before any API call** — `import_recipe`
  folds a duplicate only *after* the parse is paid for, so a crash re-run
  would re-parse everything.
- **Checklist notes carry content in `listContent`, not `textContent`** — a
  recipe kept as tickable ingredients has an empty `textContent`. `note_text`
  reads both and drops the tick state.

Imports are sequential — a burst of concurrent calls on a free
`recipe_parser_model` is the reliable way to hit a wall of 429s.

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
- `dietary_rules.active_diet_styles` is soft guidance, not a hard constraint —
  it shapes the prompt rather than rejecting a recipe. Its **one** hard effect
  is `DietStyle.calorie_ceiling` (caps the day's computed calories in
  `hydrate_dynamic_targets`, never reaches the prompt). A style may be on for
  part of the week: a bare name (every day) or a `{"style", "days"}` window,
  parsed by the one `day_scoped_entries`, which *raises* on a malformed entry.
  See "Diet styles" under Architecture.
- **How long a dish keeps is a property of the dish**, not a config global —
  `inventory_rules.storage_windows` + `Recipe.storage_class`. A hard
  constraint in the `banned_ingredients` class, and every default resolves to
  the *shortest* window (inverting the usual convention). See "Storage windows
  belong to the dish".
- **Fibre is targeted but never budgeted** — the day's figure comes from
  `nutrition_engine.calculate_fiber_target_g` and each meal is briefed a
  weighted share, but `fiber_g` has no term in `calories ~= 4p + 4c + 9f`, so
  no validator checks it. The Cronometer logged figure sits *beside* the
  planned/target pair. See "Fibre is targeted, and still has no term in the
  energy identity".
- `schedule.json`'s `sourcing` block is soft in the same way, and constrains
  what can be *bought* rather than what may be eaten. An ingredient that must
  never appear belongs in `banned_ingredients`; `sourcing` covers the
  unenumerable tail. See "Buying what the shops actually stock".

## Tests

`python -m unittest discover -s tests` from the venv. `unittest` throughout
(no pytest in the venv), plain `TestCase`s. The whole suite is under a tenth
of a second because **nothing touches the network, a model, or the clock** —
every module reaches its outside world through one seam.

**The clock half had two exceptions**, found only when the date rolled over
mid-session: `test_ui_state.py`'s day-picker fixtures legitimately call
`date.today()`, but two assertions on top of them were weekday-dependent (a
fixed offset from `days[0]`; an unrotated day list). **A fixture may read the
clock; an assertion may not depend on what it said** — re-run the module under
seven frozen weekdays before trusting it.

| file | covers |
|---|---|
| `test_week_composition.py` | style/cuisine resolution, cuisine blocks, workout breakfasts |
| `test_week_mechanics.py` | the deterministic week — derived portions, `validate_week`, shopping windows, `spread_batch`, shopping aggregation, plant count, the dish-window storage note |
| `test_food_safety.py` | storage windows as a property of the dish — the hours-to-days resolver, every default failing *short*, the config-merge that keeps the rice exception, the two coupled behaviour changes, the backstop's two modes, the prep-day anchor's longer span, both response axes, the pinned-favourite third route, the freezer half's wording |
| `test_portion_sizing.py` | the three portion layers, and the cap on the cascade's end effect |
| `test_planner_dynamic_targets.py` | target hydration, `target_modes`/`target_locks`, the fibre target on every hydration path, the diet-style calorie ceiling (idempotent, after the uplift, never over a stated target, reported not corrected) re-asserted against a four-day window, the protein floor, logged-intake substitution, adaptive TDEE |
| `test_nutrition_engine.py` | BMR/TDEE/deficit arithmetic, the adaptive estimate and which precondition stopped it, the current-weight fallback, the fibre target's two halves, the MET training-burn estimate, the schedule proposal (three states, addition threshold, two drop guards), the weight trend the Insights chart draws |
| `test_model_resolution.py` | which model each role runs on, and the reasoning switch |
| `test_diet_styles.py` | the diet-style axis, `calorie_ceiling`, the day-scoped shape (all six parser cases, the flat list pinned byte-identical, three ways a call sits against a window), `Ingredient`'s two hard rules |
| `test_ingredient_sourcing.py` | the sourcing rule, the seafood cap, the nudge-sample ban filter, the rejection-capture rule and its two decay windows, `rejections.json` round trip, the pantry ledger (both entry shapes, the containment match and its guards, the spent item) |
| `test_meal_selection.py` | location-shaped grids, favourite pre-assignment, skip estimates, fibre (reported vs targeted, the calorie pin that doesn't pin it, the share that doesn't cascade), the fridge cap, long-cook day eligibility (weekend fallback, a location widening a weekday and narrowing a weekend, the elapsed-time rejection, the exempt batch anchor) |
| `test_sync_service.py` | Garmin/Cronometer unit and key mapping (fibre included), the sleep/HRV row and its two endpoints, the activity mapping and replace-per-date storage, the expired-session recovery (once, only with a session, never on a 429), the credential guards |
| `test_keep_import.py` | Takeout note loading, colour selection, checklist-note text |
| `test_export_menu.py` | the Markdown export and the `_slot_entry` walk it shares with the PDF |
| `test_adherence.py` | adherence's three layers — the two-part key and delete-don't-flag clear, the per-date `activity_log` match, the view models both marking surfaces read |
| `test_ui_state.py` | `PlannerState` — grid edits, batch rescaling, target overrides and their baseline, target modes, stored-plan vs live-preview days, slot views, the Today day picker (including the step across into an adjacent cached week), location/training context, the derived training-burn estimate, the inspector's open/closed state, the adaptive-TDEE state, planned fibre vs target vs logged, the schedule proposal's session half, Settings' read views, the generation dialog's stage checklist and its off-by-one, Insights' five series and their gates |
| `test_config_layout.py` | a snapshot of the merged config, through the bare merge *and* the preset layer |
| `test_presets.py` | the preset layer — compatibility (no file, empty `default`), the sibling-destruction case (17 named ingredients), leaf-whole replacement both ways, an empty list as a value, the four load-time failures, the write path that can't be `save_config_keys`, a hand-added preset surviving a pick, a day-scoped `active_diet_styles` from a preset, `set_preset`'s re-layer; and **the editor** (the identity preset, unexposed paths surviving an edit, the invalid preset refused, the delete guard, the active-preset re-layer) |
| `test_preset_validation.py` | `resolve_preset_layer` returns both `resolve_config`'s structural failures and an `AppConfig` schema failure; `apply_preset_layer` raises on exactly those; the `favorite_reuse_days > history_max_entries` cross-field rule |
| `test_history.py` | history recording and rotation seeding |
| `test_api.py` | the FastAPI routes (week plans, catalog filters, history, biometrics, derived targets), `repository.catalog_matches`, and the generation route + job — the single-flight claim from both sides, the three outcomes read separately, the finished week via the read route. `GenerationJobs.run` is awaited directly, not through `start` |

**Where the line is drawn on the UI.** `ui_state.py` is tested (it is the view
model — grid edits, derived portions, override precedence). The other `ui_*`
modules are widget construction; testing them means a NiceGUI harness pinning
layout. Logic worth testing moves into `ui_state.py` or a pure helper instead.
When a test is added because something broke, record the failure in the test
(`test_model_resolution.py` and `test_sync_service.py`'s credential guards
both do).

## Notes for future sessions

- If `planner.py` fails with a Pydantic validation error after 3 retries, it's
  `instructor` surfacing the model's inability to satisfy the schema — check
  which field failed before assuming a code bug. A message about kcal totals
  is `DayRecipes.reject_untrimmable_macro_miss`: swap models rather than
  widening `planning_rules.portion_trim_limits`. Fails only that day now, not
  the run.
- `meal_history.json` entries from before the weekly rewrite have no `styles`
  key; `history_styles()` tolerates that, so old files don't need migrating.
- **A preset override addresses a *leaf*, and the leaf is replaced whole.**
  Do not "helpfully" make it a deep merge (a merge cannot express deletion),
  and do not widen it back to top-level keys — that version silently discarded
  17 `banned_ingredients` entries while validating cleanly. `presets.json` is
  supplemental: never add it to `CONFIG_FILES`, never add its keys to
  `AppConfig`, and never route the pick through `save_config_keys`, which
  raises on every key in it. See "Presets" under Architecture.
- **`default` is a preset like any other, and it is not the baseline.** Every
  diff and every compatibility claim measures against the *base config* — the
  five merged core files — because that is the thing presets layer over and
  the one thing that cannot be edited or deleted out from under a comparison.
- **The preset editor's field list is `PRESET_EDITOR_FIELDS` in `ui_state.py`,
  the single authority on what "the editor manages".** `save_preset` clears
  exactly those paths before merging the user's choices back, so a widget
  drawing a path not in the list leaves it preserved forever, and a list entry
  with no widget is dropped on every save. Adding a field means adding it
  there.
- **`dietary_rules.active_diet_styles` holds two shapes and `day_scoped_entries`
  is the only parser.** Reach for it rather than iterating the list. It
  **raises** on a malformed entry — do not "helpfully" give it
  `inventory_entries`' drop-with-warning policy: a dropped `fast_800`
  activation plans an 800 kcal day at ~1722 with nothing said.
- **A model id named in `models.json` must appear in that file's `models`
  table** — `resolve_planner_model`/`resolve_recipe_parser_model` enforce it at
  load. Without the check the two drifted (`recipe_parser_model` pointed at a
  model `model_metadata` didn't know, so `reasoning_required` never applied and
  every import died on the hard 400). A per-run `--model` is *not* checked —
  trying an unrecorded id is the flag's purpose.
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
