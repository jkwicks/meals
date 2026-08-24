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

Root holds README.md, CLAUDE.md, future-ideas.md, .env, .gitignore and
requirements.txt. It also accumulates four **gitignored** AI-assistant bundles
— `python_codebase.md`, `project_context.md`, `data_schemas.md` (written by
`./scripts/prepare.sh`) and `test_suite.md` (written by `./scripts/upload.sh`).
They are generated, never edited: a change belongs in the source they
concatenate.

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
| `profile.json` | the body and the numbers aimed at it — `user_profile`, `weekly_schedule`, `meal_weights`, `dietary_rules` |
| `meals.json` | what a meal may be — `meal_types`, `meal_styles`, `cuisines`, `cuisine_affinities`, `cuisine_meal_types`, `diet_styles`, `week_defaults` |
| `week.json` | the shape of a week — `week_start_day`, `shopping`, `serving_rules`, `enable_sunday_prep`, `max_prep_active_mins`, `inventory_to_clear`, `inventory_rules` |
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
NiceGUI drawer's "Generate Current Week" — and both go through the same
`generate_week_plan`, write the same `week_plan.json` and append the same
history. The CLI is still the one that prints shopping lists.

### NiceGUI front end

`ui_app.py` (`./scripts/server.sh start`, serves on :8080) is the high-density desktop
UI: left drawer for global controls, a header of 7 per-day macro bars, and a
7-column x 4-card canvas sitting inside a "Week" tab alongside a "Today" tab
(see "Module layout" and "The Today tab" below). Both grids are `grid-cols-8`
— an indigo Sunday-prep column sits at index 0, ahead of the seven days — so
a day's telemetry stays directly above its meals. Cook/leftover/skip/not-generated
are four distinct card treatments (`STATUS_STYLES`).

#### Module layout

`ui_app.py` used to be the whole UI — every widget a closure inside one
~3,200-line page function. It is now an ~300-line **page shell**: it builds
a `UIContext` (`state`, the repository, a `Refreshables` registry), calls
each concern's `build_*(ctx)` factory, lays out the header (the one region
with no natural module of its own, since it's shared chrome above both
tabs), and registers every returned refreshable into one topic map. The
concerns:

| module | owns |
|---|---|
| `ui_theme.py` | presentation constants, CSS, pure render helpers (`STATUS_STYLES`, `telemetry_bar`, `chain_css`, ...) — no `PlannerState` dependency |
| `ui_state.py` | `PlannerState`, `SlotView` — the view model, unchanged in substance from before the split |
| `ui_context.py` | `Refreshables` (the topic registry, see below) and `UIContext` |
| `ui_catalog.py` | favorites helpers shared by `ui_cards` and `ui_drawer` (`is_favorited`, `toggle_favorite`, ...) |
| `ui_generation.py` | everything that writes `week_plan.json`: `run_generation`, `regenerate_day`, `regenerate_meal`, `reload_from_disk`, plus the progress dialog |
| `ui_cards.py` | the Week tab's canvas, meal cards, recipe detail and swap-with-favorite dialogs |
| `ui_telemetry.py` | the header's week banner, context-pipeline strip, and macro bars |
| `ui_shopping.py` | the shopping slide-over |
| `ui_drawer.py` | the left drawer's targets/training/pantry/catalog/import sections |
| `ui_prep_options.py` | the "Generate Current Week" options popup — cuisine picker, diet-style picker, bulk-prep and long-cook toggles, each a one-off for the *next* run only (see below) |
| `ui_today.py` | the Today tab — one day's cards, its location/training context strip, and the day picker that moves between days (see below) |

Each `build_*(ctx)` returns a small dataclass of the refreshable functions
(and, for `ui_shopping`, the drawer element) other modules or the shell
need — `ui_cards.build_cards`, for instance, needs `ui_generation`'s
handles passed in, because a card's regenerate icon calls into it. This is
why build order matters in `planner_page()`: `ui_generation` before
`ui_cards`, everything before the refresh-topic registration at the bottom.

**The `Refreshables` registry replaces a hand-maintained `refresh_all()`.**
A call site says *what changed* — `refreshables.refresh("plan")`,
`"targets"`, `"catalog"` — instead of naming every widget that currently
depends on it. Topics are registered once, in `planner_page()`, after every
module is built. `"plan"` is the broad one (a generation, a reload, a
leftover link, or a drawer control that reshapes the week all repaint the
same set); several narrower topics exist because rebuilding a section
mid-edit would steal an input's focus — see `ui_drawer.day_target_row`'s
`sync()`, which refreshes `"telemetry"` alone rather than `"targets"` for
exactly that reason.

No package structure: every module above is still a flat sibling, importable
via plain `python src/ui_app.py`, per this file's `sys.path[0]` note under
Layout. Nothing outside `src/ui_*.py` and `ui_app.py` changed shape — the
repository, planner and week modules are untouched by the split.

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

The main edit it offers is the **"Link to next lunch"** button on each dinner
card: one click sets the following day's lunch to `MODE_LEFTOVER` with
`source` pointing at that dinner. Because portions are derived, that single
change is also what grows the batch. Its inverse is the **Unlink** button on
every leftover card (`PlannerState.unlink_slot`), which is the *only* way to
undo one — clicking the link button a second time hits
`leftover_link_error`'s repeat-click guard rather than toggling, so before
this existed a grid could only ever accumulate links, and
`ui_generation.generate_week`'s stranding warning told users to "unlink one"
with no control anywhere that did it. Both go through
`PlannerState.apply_spec`, which is where every future grid edit should land
too:

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

#### The expanded recipe card

Clicking any card — Week tab or Today tab, they share the one dialog — opens
`ui_cards.recipe_detail`, which is laid out as a document you cook from rather
than as a roomier version of the card that opened it: a mono eyebrow
(`MEAL TYPE — STYLE`), the title, one ruled strip carrying
`KCAL/PRO/CHO/FAT/PREP`, ingredients as a two-column table with the quantities
right-aligned in one mono column, and the method as numbered rows. Type and
alignment do the work because the grid *behind* the dialog is already spending
every colour the app owns; the only hues inside it are the status chip, the
`MACRO_TINTS` on the three macro labels, and the amber prep note.

Four decisions in it are worth keeping:

- **Ingredients are for the batch, macros are for one serving**, and on a
  bulk-cooked dinner those differ by a factor of six. Each half is labelled
  next to itself — `ALL 6 PORTIONS` on the ingredients header, `PER SERVING`
  under the macro strip — rather than once at the top where it would be read
  as applying to both.
- **The reference design this came from had a 1x/2x/4x portion multiplier,
  and it is deliberately absent.** Portions are derived (`week.portions_for`),
  which is what makes a batch size unable to disagree with the meals it
  covers; a multiplier sitting on the recipe would be a second source of
  truth for the same number. The status chip took that corner of the eyebrow
  instead, so the dialog says what it opened from.
- **Ticking a step mutates that row's classes**, never `recipe_detail.refresh()`
  — repainting to strike one line out of nine would lose the scroll position
  in the recipe you are reading. It is not persisted, and resets whenever the
  dialog opens: scratch state for one cook, the same reasoning as the shopping
  list's unticked checkboxes. `add=`/`remove=` rather than `toggle=` because
  the pairs are conflicting Tailwind utilities (`text-slate-200` vs
  `text-slate-600`), and both present at once resolves by stylesheet order.
- **`flex-nowrap` on the step and prep-note rows is load-bearing.** Quasar's
  own `.flex` rule sets `flex-wrap: wrap` and Tailwind's `flex-row` does not
  undo it, so a step long enough to fill its row wrapped *below* its number
  and ran back underneath it. `min-w-0` on the label is the other half — a
  flex item's default `min-width: auto` won't shrink past its longest word.
  Worth knowing before adding any icon-plus-text row anywhere in this UI.

NOVA group moved to a per-ingredient tooltip: every group that reaches the
dialog is an allowed one (4 is rejected in validation), so it is worth being
able to check and not worth a column.

### The Today tab

`ui_today.py` is a read-only preview of just today's four cards, sitting
next to the Week tab. It is deliberately not built on `ui_cards.meal_card`
— that function's action-row buttons all need `ui_catalog`/`ui_generation`
wired in, none of which a card with no buttons needs, so a smaller card of
its own there is a real decoupling rather than a "fix later" shortcut. No
favorite/swap/regenerate buttons yet, but clicking a card *does* open the
recipe detail dialog — `build_today(ctx, cards)` takes `ui_cards`'s
`CardHandles` and calls `cards.open_detail(view)` on click, the same one
dialog every Week-tab card already shares, rather than a second copy of it
living here.

**Knowing "today" needed a real calendar date, which nothing in this
codebase stored.** `WeekPlan.days` is a rotation of weekday *names*
(`week.week_days` rotates names, not dates), and `week.week_date_range`
existed only to *derive* a plausible date range for display — it anchors on
`generated_at` or on "now", so the same cached plan looks equally plausible
whether it's five weeks old or generated ten minutes ago. That ambiguity is
fine for a banner ("Week of Aug 10 – Aug 16") but not for deciding whether
today's Thursday slot is actually *this* Thursday — confidently rendering
last week's Thursday would be worse than saying nothing.

`WeekPlan.week_start_date` fixes this: the ISO date `days[0]` fell on at
generation time, set once in `generate_week_plan` (anchored on the same
`generated_at` timestamp, not a second `date.today()` call, so the two can't
disagree) and preserved through `regenerate_single_day`/`regenerate_single_meal`,
which only `model_copy` the fields they actually change. `week.today_in_week`
is the check built on top: given a plan's `week_start_date` (or, for a plan
generated before this field existed, `week_date_range(days, generated_at)`'s
own anchor — the same pre-migration tolerance `history_styles()` already
extends to old `meal_history.json` entries), it returns today's weekday name
only if today's actual calendar date falls inside that week's span, else
`None`. `PlannerState.today_day()` wraps it against whichever week is
currently loaded. It used to be the whole story — a `None` replaced the
panel with "no cached week covers today" — but a tab with a day picker can
show the week anyway, so that case is now a note beside the heading and
`viewed_day()` falls back to day one (see below). Only "nothing generated at
all" still replaces the panel.

#### Where you are and what you trained

The Today tab also carries a **day-context strip** above the calorie bar:
where the day is spent, and the workouts scheduled for it. This is the one
thing the tab can show that the Week tab structurally cannot — seven columns
have room for an amber bolt saying *that* a day has a workout, and one day
has room to say which session, at what time, for how many calories, and what
the location does to lunch. So it lives here rather than in the shared header
above both tabs.

`ui_state.day_context` is the whole view model, built **once per repaint**
rather than once per card: the per-meal training notes are only reachable
through `planning_config()`, which runs `apply_training_adjustments` over the
entire week, and four cards each asking for their own would be four copies of
that work for one day's answer.

It reads the config **the next run would use**, not the file on disk, which
is what puts it under the same "live preview" contract `targets_for` already
honours — a session added in the drawer changes the day's budget *and* its
post-workout pin, so a strip still showing the file's schedule would
contradict the calorie bar directly above it. `today_view` is registered on
the `targets` and `training` refresh topics for that reason. It is
deliberately **not** on `telemetry`: that topic exists so a keystroke in a
focused target input can repaint the header without disturbing the drawer,
and rebuilding four cards plus a `planning_config()` per keystroke is exactly
the cost it was carved out to avoid.

Four things in it are decisions rather than detail:

- **A location badge appears on a card only if the location declares that
  meal's `<meal_type>_mode`.** `LocationView.constrains`/`brief` mirror
  `planner.build_location_note`'s scope rule rather than re-deciding it, so
  "must travel in a container" reaches an Office *lunch* and never the
  breakfast eaten at home before leaving. Getting this wrong renders as an
  ordinary-looking card carrying a constraint the prompt never sent — nothing
  else in the app would catch it, which is why `test_ui_state.py` pins it.
- **The restriction chips are tag-labelled and prose-tooltipped, and the two
  come from `LocationView.phrase_pairs` as pairs.** A tag with no
  `LOCATION_RESTRICTION_PHRASES` entry is dropped exactly as
  `build_location_note` drops it — so zipping `restrictions` against a
  filtered `phrases` would silently pair the surviving tags with the wrong
  sentences the moment one tag went unrecognised. Pairing at the source is
  what makes that unrepresentable.
- **The post/pre-workout badge classifies `training_notes` by
  `planner.TRAINING_NOTE_PREFIXES`**, a constant `apply_training_adjustments`
  now writes those notes *with*. Matching on the wording instead would mean a
  reworded prompt silently dropping the badge, and a note that fails to parse
  renders as no badge rather than as an error. The badge carries the kind and
  the tooltip carries the model's own sentence with the prefix stripped, so
  the two don't restate each other.
- **A rest day, or any zero-burn session, is muted rather than amber.**
  `apply_training_adjustments` skips both, so neither expands a budget or
  pins a meal, and an amber chip would promise calories it never bought.
  `TrainingView.is_rest` folds the two cases together for that reason — a
  typed `rest` and a session logged at 0 kcal are the same thing downstream.

Sessions are ordered by `planner._clock_minutes`, shared rather than
reimplemented so the strip orders a day by the same tolerant clock reading
that decides which meal gets the post-workout pin. Everything degrades to
saying nothing: a config with no `base_schedule` yields no location, an
untrained day yields no chips, and a day with neither renders no strip at
all rather than an empty panel announcing the absence of a feature.

#### Browsing to another day

The tab is no longer pinned to today: a row of seven day pills with a chevron
either side moves through the loaded week, and the **tab's own label becomes
the day being viewed** — "Today · Sun 23 Aug" on today, "Fri 21 Aug" once you
step away. Each pill carries an amber mark per workout that day, so the row
doubles as a week-at-a-glance of the training schedule.

**This was cheap because the panel was already day-parameterized.**
`today_view` had exactly one line deciding the day, and everything under it —
`targets_for`, `totals_for`, `day_context`, `slot_id` — already took a day
argument. Adding the picker changed that one line; no plumbing followed.

Four things in it are decisions:

- **`selected_day = None` means "follow today", and is a distinct state from
  storing today's name.** A tab left open overnight should be on the right day
  in the morning, and the resolved name would pin it to whichever day the page
  loaded on. The "Today" reset button clears the key rather than re-pointing
  it — the same reasoning as `set_target` dropping an override that matches
  the file.
- **Stepping clamps at both ends rather than wrapping or spilling into the
  next week.** The loaded `week_plan` holds exactly these seven days;
  continuing past the last one would mean an async load of the other cached
  plan (`current`/`next`) plus a second control free to disagree with the
  header's week selector. The chevrons disable at the ends instead. Crossing
  weeks is a real feature, and a bigger one than this.
- **`week_covers_today()` is stricter than `today_day() is not None`, and the
  gap is the point.** `today_in_week` answers "is today inside this week's
  seven-day *span*" — a question about dates — while the grid is drawn from
  `state.days`. A config whose `weekly_schedule` names fewer than seven days
  has a span wider than its columns, and it is the columns a picker can
  navigate to. Both the "doesn't cover today" note and the reset button's
  visibility key off the columns for that reason.
- **A plan with no `week_start_date` shows the bare weekday name.**
  `day_date_iso` returns None for it and `ui_theme.format_day_label` degrades
  accordingly, because `week.day_date` deliberately refuses a `generated_at`
  fallback — and a tab title is the most visible possible place to print a
  plausible-looking wrong date.
- **The workout marks differ by icon, never by colour, and "today" gave up
  its dot for them.** `ui_theme.TRAINING_TYPE_ICONS`/`training_icon` map a
  type to a glyph — dumbbell, bolt, bike, runner, heart, walker — and every
  one of them stays amber, because emerald, sky, slate, rose, indigo, amber,
  violet and cyan already each mean something specific here (slot status,
  prep column, training, location, freezer) and seven new hues would collide
  with one of those long before they read as a scale. Today used to be a `•`
  on the pill; it is now a ring, since two different dots on one pill would
  be two meanings competing for the same glyph. The same map drives the
  context strip's chips, so a day's mark and its chip can't disagree.

  Matching is exact first, then **longest prefix** — the same widening
  `WORKOUT_BREAKFAST_TYPES` uses — so a future `gym_strength` gets the
  dumbbell and a `cardio_swim` the heart with no edit, and an unrecognised
  type falls back to a generic workout rather than taking the picker down.
  Marks are deduped per day by icon (two gym sessions, one dumbbell) while
  Saturday's gym-plus-HIIT keeps two, and the pill reserves the mark row's
  height whether or not the day trains so the row keeps one baseline.

- **`PlannerState.training_for` exists so the picker can afford this.** It
  reads `training_schedule` alone, no config, because the pills call it for
  all seven days on every repaint — routing that through `day_context` (which
  needs `planning_config()` for its per-meal notes) would have been seven
  `apply_training_adjustments` passes over the week to draw one row of icons.
  `day_context` calls the same method, so the strip and the pills are reading
  one list.

The label is kept in step by `today_view` calling `sync_tab_label()` on every
repaint, rather than by a NiceGUI binding: the label depends on the plan and
on today as well as on the browsed day, so a binding keyed to any one of them
would go stale on the others. The shell injects its `ui.tab` through
`TodayHandles.bind_tab` because `build_today` runs well before the tabs exist
(see `planner_page`'s build order).

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

- `config/` — external configuration; five core files merged and validated
  once at load through `AppConfig` (`extra="forbid"`, so an unknown or typo'd
  key fails at startup), plus two supplemental files loaded apart from the
  merge. See "config/ is seven files" under Layout.
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
      drawer always survives, the same precedence a hand-written
      `meal_overrides` entry gets over a computed one.

      **The pin only fires on a slot still on auto**, which is what makes
      `ui_generation.generate_week` call `week.clear_styles`/`clear_cuisines`
      unconditionally, on every full-week generation, before
      `resolve_auto_choices` runs. Without that, a slot already carrying a
      concrete style from a previous run — the normal state once a week has
      been generated once — blocks the pin from ever re-firing, even after a
      `training_schedule` edit newly qualifies that day: a schedule change
      would otherwise silently fail to reach the plan until the drawer's
      "Shuffle styles" button (`PlannerState.shuffle_styles`, same two
      `week.clear_*` calls) was clicked by hand. Mode, leftover links and
      skips survive the clear — those are structural edits the user made on
      purpose, not picks due for a re-roll.

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

**Deliberately no numeric lever.** Fast 800's real-world hook is a low
calorie ceiling, but `weekly_schedule`/`hydrate_dynamic_targets` already own
every day's calorie number (see below), computed from the body and the
config's `deficit`/`target_weight_kg` gap — a second, diet-style-driven
calorie adjustment would be exactly the kind of double-count
`hydrate_dynamic_targets` already has to guard against for training uplift.
So Fast 800 is expressed as food-selection guidance instead — simple,
lean, low-added-fat dishes — inside whatever budget the day was already
given, not as a competing source of truth for the number itself. If Fast
800's actual calorie ceiling is ever wanted as a hard target, it belongs as
an adjustment inside `hydrate_dynamic_targets`, not as a second config knob
sitting beside it.

**All twelve are soft guidance, including the two — Paleo and AIP — that read
like hard elimination lists.** "Exclude all grains, legumes and dairy" is
`principles` text sent to the model, not a Pydantic validator: nothing stops
a slip-through the way `Ingredient.reject_banned_ingredients` stops a banned
ingredient outright. Real hard exclusions for one of these belong in
`dietary_rules.banned_ingredients`, same as any other must-never-appear
ingredient; `diet_styles` is for shaping what the model reaches for, not
policing what it must not.

### Targets come from the body, not the file

`weekly_schedule`'s per-day calories and protein are no longer what the week
is planned against. `hydrate_dynamic_targets()` replaces them with
`nutrition_engine.calculate_macro_targets()`'s output — BMR from the latest
weigh-in, TDEE from the activity factor, and a deficit that slides with the
remaining gap to `target_weight_kg`. It is a **pure function**; `hydrate_config()`
is the thin `async` wrapper that fetches the biometrics for it.

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

### Batch cooking on purpose: the two prep toggles

The drawer's Generate button opens `ui_prep_options`' popup rather than
running the week directly, and two of its controls reshape the *grid* before
generation rather than merely briefing the model: **bulk prep** and **long
cook**. Each calls `week.spread_batch`, which picks one dinner as an anchor
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
  `apply_batch_selections` passes `fridge_safe_days - 1`. It applies to the
  anchor too, unlike every other bound here — an anchor outside the window is
  already unsafe before it spreads anywhere. `inventory_rules.fridge_safe_days`
  is therefore now **3**, not 4: 4 allowed a batch onto Thursday, which is 4
  days out of the fridge and past what these batches should carry.

  Measured on `default_week_spec` with the shipped `config/`, both toggles on:
  bulk prep `Monday:lunch` → Tuesday and Wednesday lunches, long cook
  `Monday:dinner` → Tuesday and Wednesday dinners. Six meals, 6 portions each,
  nothing landing Thursday or later, `validate_week` clean and byte-identical
  across repeated runs.

One thing this deliberately does **not** fix, still measured from the anchor
day rather than prep day: `storage_note`'s `keeps_for_days` (so a prep-session
candidate's "eaten across N day(s)" line under-reports, and
`generate_sunday_prep_session`'s prompt then says "do not recompute it").

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

**A `long_oven_cook` favourite may only take a weekend slot**
(`favorite_fits_day`), and the rule cuts across all three meal types —
breakfast, which covers two mornings from one record, has to suit both.
Nothing else in the app was going to stop a long cook landing on a Tuesday,
which is worth spelling out because all three plausible candidates look like
they should have: `BATCH_ROAST_RULE` tells the *model* to put the week's long
cook on a weekend and a favourite is never generated, so that rule never sees
it; the placement rule above is about protecting cuisine blocks and says
nothing about the day having the hours in it; and `prep_limit_for`'s
30-minute weeknight ceiling counts **active** minutes, which a braise
honestly reports as the 20 that are hands-on rather than the 8-10 hours it
then sits in the oven. A "Slow Cooked Beef Cheeks" imported from Keep was
scheduled for a Thursday exactly this way. Eight of the 36 dinner favourites
in the shipped catalog are long cooks, so this is a shape rather than one bad
record.

Two consequences of *how* it declines are worth knowing. A weeknight run end
takes the next eligible favourite instead of being left empty, so a long cook
is deferred rather than dropped — it waits for a run end that lands on a
weekend. And the dinner cap counts **pins made, not run ends looked at**:
slicing the run ends first, which is what this used to do, spends both of
them on declined weeknights and pins nothing at all on a week whose Saturday
was free the whole time.

It keys on the weekend rather than on `base_schedule`, which does know that
Tuesday is a WFH day and that a slow cooker started at 8am on one is
perfectly fine. Weeknight-versus-weekend is the split `prep_limit_for` and
`BATCH_ROAST_RULE` already draw, and a second, subtler notion of "a day with
room to cook" is one more thing to keep in agreement with them. Widening it
to the days you are actually home is a real improvement and belongs in
`favorite_fits_day` when it happens.

**It deliberately does not touch the generated side**, where the day choice
is still soft — `BATCH_ROAST_RULE` states a preference for a weekend and
nothing rejects a model that puts a 4-hour braise on a Tuesday while
truthfully reporting 25 minutes of active prep. Making that hard needs an
elapsed-time field on `Recipe` that no saved recipe carries, which is a
schema change, a prompt change and a validator change; the favourite path is
where the failure actually happened and is fixed on its own terms.

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
derived, the same division `ui_drawer.day_target_row` uses.

Fibre is deliberately **not** part of a skip estimate: the fibre in a meal
nobody cooked isn't estimable, and 0 is more honest than a guess.

### Fibre is reported, never budgeted

`Ingredient.fiber_g` exists and is summed everywhere a recipe's macros are,
but it is **not** in `MACRO_KEYS`. That separation is the whole feature.

Every budget in `planner.py` is checked against `calories ~= 4p + 4c + 9f`:
`split_targets` scales all four together, `apply_protein_floor` moves calories
with protein at 4 kcal/g to preserve it, `reject_untrimmable_macro_miss`
bounces a response whose calories don't reconcile. Fibre has no term in that
identity — it is already excluded from `net_carbs_g` by definition — so
putting it in `MACRO_KEYS` would drop a fifth number into arithmetic with
nowhere to put it.

`NUTRIENT_KEYS` (`MACRO_KEYS + ("fiber_g",)`) is what it rides on instead:
everything linear in an ingredient's quantity. `Ingredient.scaled`,
`Recipe.total_macros` and `sum_serving_macros` all walk it, so the portion
trim halves fibre along with everything else; every budget-side consumer
indexes `MACRO_KEYS` out of the result and never sees it.

Surfaced in three places, never with a denominator: the recipe dialog's macro
strip (`MACRO_DETAIL_LABELS`), the telemetry header's day row as a bare `FIB
32g`, and the PDF/Markdown exports. Printing `32/xx` would invent a goal the
planner never aimed at. A daily fibre *target* is a real feature and a bigger
one — it needs a term in `nutrition_engine.calculate_macro_targets` and a
per-slot share in `split_targets`. This is deliberately not that.

`fiber_g` defaults to `0.0`, which is what keeps recipes saved before it
existed loadable — same pre-migration tolerance `history_styles()` extends to
old `meal_history.json` entries. `FIBER_REPORTING_RULE` asks the model for it
explicitly rather than relying on the schema description, because the default
means an omitted field produces a silently fibre-free week rather than an
error. The rule's second sentence is the important one: a model told to
"report fibre" starts *optimising* for it and pulls the recipe off the budget
that actually is checked.

### Leftovers can't outlive the fridge

`inventory_rules.fridge_safe_days` (3) was config that only ever flavoured a
storage note. It is now enforced three times, and the split matters:

- **Prevention, from the anchor.** `week.spread_batch` takes `max_span_days`
  and stops its forward walk there, so neither batch toggle can plan food more
  than that many days past the day it was cooked.
- **Prevention, from prep day.** `max_day_index` bounds the same window for a
  batch that is *not* cooked on its anchor day — every prep-session batch, see
  "Batch cooking on purpose". `max_span_days` alone let Sunday-cooked food
  reach Friday, because from a Tuesday anchor that is only 3 days.
- **Backstop.** `validate_week` checks `span_days` against the same number.
  A chain built by hand out of "Link to next lunch" clicks never goes through
  `spread_batch`, and neither does an imported or hand-edited `week_plan.json`.

Bounding the spread rather than only rejecting the result is the difference
between never creating the problem and refusing to generate a week the
planner itself just built.

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
  than rejecting a recipe. See "Diet styles" under Architecture.
- **Fibre is tracked but never targeted** — `Ingredient.fiber_g` is reported
  and displayed, and is deliberately absent from every macro budget. See
  "Fibre is reported, never budgeted" under Architecture.
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

| file | covers |
|---|---|
| `test_week_composition.py` | style/cuisine resolution, cuisine blocks, workout breakfasts |
| `test_week_mechanics.py` | the deterministic week — derived portions, `validate_week`, shopping windows, `spread_batch`, the shopping aggregation and plant count |
| `test_portion_sizing.py` | the three portion layers, and the cap on the cascade's end effect |
| `test_planner_dynamic_targets.py` | target hydration, the protein floor, logged-intake substitution, adaptive TDEE |
| `test_nutrition_engine.py` | BMR/TDEE/deficit arithmetic and the adaptive estimate |
| `test_model_resolution.py` | which model each role runs on, and the reasoning switch |
| `test_diet_styles.py` | the diet-style axis and `Ingredient`'s two hard rules |
| `test_ingredient_sourcing.py` | the sourcing rule, the week-wide seafood cap, and the nudge-sample ban filter |
| `test_meal_selection.py` | location-shaped grids, favourite pre-assignment, skip estimates, fibre, the fridge cap |
| `test_sync_service.py` | Garmin/Cronometer unit and key mapping, and the credential guards |
| `test_keep_import.py` | Takeout note loading, colour selection, and checklist-note text |
| `test_export_menu.py` | the Markdown export and the `_slot_entry` walk it shares with the PDF |
| `test_ui_state.py` | `PlannerState` — grid edits, batch rescaling, target overrides, slot views, and the Today tab's day picker and location/training context |
| `test_config_layout.py` | a snapshot of the merged config, asserting nothing was lost or moved |
| `test_history.py` | history recording and rotation seeding |

**Where the line is drawn on the UI.** `ui_state.py` is tested because it is
the view model — grid edits, derived portions, override precedence — and those
rules are exactly what a UI change can silently break. The other eleven `ui_*`
modules are widget construction, and testing them would mean a NiceGUI
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
- **Testing a "fails before any call" guard requires a populated
  environment.** See the sync-credentials note under "Biometric sync": a guard
  test that constructs its subject with `""` and runs against an empty `.env`
  proves nothing about the guard and everything about the machine.
- `src/proposed-engine.py` (Kalman weight smoother, Holt trend) was deleted —
  unreferenced, unimportable by that filename, and depending on `numpy`, which
  is not in requirements.txt. The finished, tested version of what it was
  reaching for is `calculate_adaptive_tdee`, now wired in above.
