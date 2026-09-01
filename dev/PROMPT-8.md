# PROMPT-8 — Presets: the container, the layer, and the weekly pick

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It
moves config validation, which is the load path every other feature sits on,
and its central acceptance test is a byte-identical comparison a human should
watch run.

**Priority: immediately after PROMPT-7, ahead of 4–6.** This is Arm A's
enabling half and nothing else in the arm can land without it.

Cold session. Read CLAUDE.md's **"config/ is seven files — five merged into one
dict, two loaded apart"** and **"Storage goes through an async repository"**
(`save_config_keys` is the one write path into `config/`). Then `design-01`
**§2** (storage), **§3** (the merge and its three rules), **§9.1** (the weekly
pick) and **§10** (acceptance). `design-03` **§8 step 1** is the scope line.

**Load the `ui-work` skill before editing any `ui_*.py`.**

## Scope, stated as an exclusion first

This prompt builds **presets only**. `config/blocks.json`, dated overrides,
block successors, the protein-floor basis and everything in `design-01` §4–§6
are **out** — a block is the dated mechanism and it is the larger half. What is
built here is what a block will later pin.

Also out: the **editor** (PROMPT-9). Presets are hand-edited JSON at the end of
this prompt, and that is deliberate — `design-03` §8 orders the selector before
the editor so the layer is proven against a file before a UI can write one.

## The requirement

`config/` holds one implicit profile smeared across five files. This names it,
makes it switchable, and makes the choice weekly.

## What to do

### 1. `config/presets.json`, a third supplemental file

Joins `models.json` and `integrations.json` — **not** `CONFIG_FILES`, no keys in
`AppConfig`. `design-01` §2 gives the shape; the two-tier rule decides it:
*a missing supplemental file resolves to `{}` because every value in it has an
in-code fallback.* A checkout without the file plans exactly as today.

```json
{ "active": "default",
  "presets": { "default": { "label": "Standard week", "overrides": {} } } }
```

`default` is **a row in the file, not a built-in.** A preset the code treats as
special is a hard-coded preset wearing a costume, and it is also what keeps the
acceptance test honest: `default` reproduces today's behaviour *because its
`overrides` are empty*, not because the loader falls back.

**But `default` is not the comparison baseline, and that distinction has to
hold from the first line of code.** The baseline is the **base config** — the
five merged core files — which cannot be deleted, because it is the thing
presets layer over. `default` is an ordinary preset that happens to override
nothing. Getting this backwards is what let the first draft require a diff
against `default` (§3) while `PROMPT-9` made `default` deletable: delete it and
both the baseline and `active: "default"` dangle. Diff against base config
throughout; `active` must be absent, null, or the name of a preset that exists.

### 1a. Two repository methods, because `save_config_keys` cannot write this file

```python
async def load_presets_config(self) -> dict          # {} when absent
async def save_presets_config(self, presets: dict) -> None
```

**The first draft of §3 said the pick was written through `save_config_keys`,
and that instruction could not have run.** `_save_config_keys` looks every key
up in `CONFIG_KEY_OWNER` — derived from `CONFIG_FILES` — and raises
`ValueError` on a miss (`repository.py`, "is not a known config key"). §1 puts
this file deliberately outside `CONFIG_FILES`, so *every* key in it misses.
The two requirements were incompatible and the weekly pick would have raised on
the first click.

Follow `load_models_config`/`load_integrations_config` exactly: absent file →
`{}`, blocking work in `asyncio.to_thread`, write via temp file + `os.replace`.
**Do not broaden `save_config_keys`** — it is narrow on purpose, and a method
that wrote both core merged keys and independent supplemental documents would
have to guess which it was being handed.

Writing is **read-modify-write on the whole file**, so a hand-added preset the
editor never saw survives a pick change — the same property `save_config_keys`
already gives per file.

### 2. The layer, and the ordering change it forces

```
1. merge the five core files           → base dict  (CONFIG_FILES manifest check)
2. resolve the active preset           → preset layer
3. validate                            → AppConfig (extra="forbid")
```

**Validation moves to after the layer, and that is the real change in this
prompt.** Today the merge validates and nothing touches the dict afterwards. A
preset overriding a key *after* validation could introduce a state `AppConfig`
would have rejected, so validating last is the only ordering where
`extra="forbid"` still means anything. Expect this to be the part that breaks
something; it is the reason this is not a small change.

Four rules on the layer, all from `design-01` §3 **as amended 2026-09-01**,
all load-time and loud:

- **A preset states typed leaf paths, not top-level keys.**
  `"dietary_rules.allowed_nova_groups": [1,2,3,4]` — a dotted path whose value
  replaces that leaf, whole.
- **The first path segment must be a key `CONFIG_FILES` knows.** Fail naming
  the preset *and* the path. A preset that appears applied and is not is
  strictly worse than one that refuses to load — the same argument
  `CONFIG_FILES` already makes about a key in the wrong file. Only the first
  segment is a `CONFIG_FILES` question, because only it is about file
  ownership.
- **Each leaf replaced whole; no recursive merge.** A merge cannot express
  deletion, and it makes "what does this preset actually plan against"
  unanswerable without replaying it. An override valued `[]` or `{}` is an
  explicit value, never an absence.
- **No chaining or inheritance.** One layer. Already filed as decided-against
  (`design-01` §12).

> **Read `design-01` §3's correction box before writing the resolver.** The
> first draft of this prompt said *whole-key replacement*, and the shipped
> `comfort` preset broke it silently: `DietaryRules` has no required fields, so
> replacing `dietary_rules` with `{"allowed_nova_groups": [1,2,3,4]}`
> **validates cleanly** and discards 17 `banned_ingredients` entries plus
> `active_diet_styles`. There is a test for exactly this in Acceptance; it is
> the one that matters most after the byte-identical pair.

### 2a. One resolver, and `PROMPT-9` calls it

Build the layer as a **pure function** over the base config and one preset,
returning either the resolved dict or structured, displayable failures. Not a
resolver here and a separate validator in `PROMPT-9` — the two would be two
interpretations of "valid", free to disagree about a file one accepted and the
other refused.

The loader raises on its failures (this app's fail-loudly-at-load policy, for
hand-edited files); `PROMPT-9`'s editor renders the same failures and declines
to write. **Same check, two presentations.** Put it where `api.py` could import
it — no NiceGUI, no `PlannerState`.

### 3. The weekly pick

`design-01` §9.1: **at the top of the review dialog**, above the batch toggles
it can override — not in Settings. The Generate button already opens that
dialog rather than running the week, so it is where the week's shape is settled.

- **The default is last week's pick.** An empty choice every Monday
  reintroduces the decision this arm removes.
- **Say what the pick changed** — a one-line diff against the **base config**,
  never against the preset named `default` ("NOVA 4 allowed · prep ceiling
  20 min"). A mode whose effect you cannot see is the stale-config problem in a
  new hat, and a diff computed against another *row* goes blank the moment that
  row is edited or deleted (§1).
- Writing `active` goes through **`save_presets_config`** (§1a), making this
  the **third writer to `config/`** after `set_target_mode` and
  `accept_training_proposal` — and it passes the same test both do: a standing
  choice, not an input to one run. It is a third *writer*, not a third caller
  of `save_config_keys`, which cannot write this file.

### 4. Record the pick on the week

`WeekPlan` and the history entry carry the preset name (`design-01` §4.6).
Without it the feedback arm can compare weeks and never explain them.

## Acceptance

The compatibility claim first, because everything else is negotiable:

- **No `presets.json` → byte-identical.** Same merged config, same prompts,
  same targets, same generated week as `main`. **Assert it; do not assume it.**
- **`presets.json` holding only an empty `default` → byte-identical too.** This
  is the stronger test and the one that proves `default` is data.
- **The sibling-destruction test, which is the one this prompt was rewritten
  for.** A preset overriding `dietary_rules.allowed_nova_groups` leaves all 17
  `banned_ingredients` entries and `active_diet_styles` **intact**. Assert on
  the values, not on the shape.
- A preset override whose **first path segment** `CONFIG_FILES` does not know
  **fails at load**, naming preset and path.
- A preset override replaces **its leaf** whole. Assert that an override on
  `weekly_schedule` itself, valued with three days, yields a three-day
  `weekly_schedule` — no seven-day merge — while an override on
  `weekly_schedule.Thursday` leaves the other six days untouched.
- An override valued `[]` is applied as an empty list, not skipped as absent.
- **The pick is written through `save_presets_config`.** Assert
  `save_config_keys` is never called with a presets key — it raises, and a test
  that only checks the file contents afterwards would pass on a code path that
  happened to write it another way.
- **Writing the pick preserves a preset the code never parsed.** Hand-add a
  preset carrying an unknown extra field, change `active`, assert it survives.
- `AppConfig` validation runs **after** the layer — assert a preset that would
  introduce an invalid state fails, rather than being silently accepted.
- **The resolver is one pure function** returning structured failures, and the
  loader raises on them. `PROMPT-9` imports it; assert it needs no NiceGUI and
  no `PlannerState`.
- `PlannerState.planning_config()` sees the preset, so the telemetry header
  previews what the run will aim at. CLAUDE.md's standing rule: *a number the
  UI displays and a number a run plans against must come from one call.*
- The pick survives a reload; the *rest* of the review dialog still does not.
- The week's preset is on `WeekPlan` and in the history entry.

Tests beside `test_config_layout.py`, which already snapshots the merged dict —
extend it rather than starting a second snapshot.

**Splitting `planning_rules` is no longer a prerequisite of this prompt.**
`OUTSTANDING.md` hardened that recommendation because whole-key replacement
trapped over half the key; leaf paths reach
`planning_rules.favorite_dinner_slots` without disturbing
`planning_rules.portion_trim_limits`. The split still has value on its own —
it separates preference from engine invariant — so it stays in CHANGE-QUEUE.md
as ordinary work rather than a blocker here.

## Do not

- Add `presets.json` to `CONFIG_FILES` or its keys to `AppConfig`.
- **Route the pick through `save_config_keys`.** It raises on every key in this
  file. Use §1a's methods.
- Treat `default` as special anywhere in code, or diff against it.
- Deep-merge, or replace whole top-level keys.
- Build the editor, blocks, or `week_shape`.
- Let a preset set `calories` or `protein_g`. **Inert** while `target_modes` is
  `auto` — `hydrate_dynamic_targets` replaces both — so the field would display
  a number nothing plans from (`design-03` §5). The level is the block's; the
  shape is the preset's.
- Persist anything else from the review dialog. Everything but the pick stays
  session-only.
