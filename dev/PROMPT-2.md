# PROMPT-2 — Day-scoped diet styles ("Fast 800 for four days")

**Not queue-safe** (in `dev/`, so `claude-queue.sh` cannot see it). Its
acceptance includes a numeric check across two hydration passes, which is
worth a human eye.

Cold session. Read CLAUDE.md's **"Diet styles: a standing philosophy,
orthogonal to cuisine"** — especially the four bullets on the calorie ceiling —
before starting. Read `dev/design-00-program.md` finding **F3**.

## Its role changed after this prompt was written — read this

Filed as an independent Tier 1 win. `design-01` §5 then found it is a
**dependency of the block machinery, not a sibling of it**: a block boundary
can fall mid-week ("Fast 800 for four days" is exactly that), so blocks need
per-day scoping, and this builds it on the one field where the machinery is
already half-present. Build them together and the scoping gets invented twice.

Two consequences for the work below, neither of which changes its scope:

- **Build the day-scoping as something a block can reuse.** The parser and the
  `_sourcing_day_split`-shaped prompt handling are the reusable parts; do not
  bury them inside `diet_style_calorie_ceiling`.
- **A preset will also set `active_diet_styles`** (`design-01` §9.2,
  `design-03` §6 — an XS multi-select). That is a *layer over* this key, not a
  change to it, so the two shapes accepted here must both survive coming from
  a preset rather than from `profile.json` directly.

It stays an **S** and it still ships standing value on its own.

## The problem

`dietary_rules.active_diet_styles` is whole-config: a style is on for the
entire week or off. "I want to do a Fast 800 plan for 4 days" is not
expressible, and it is the brief's most concrete complaint about rigidity.

## Why this is small, which is the non-obvious part

`planner.diet_style_calorie_ceiling` reads the lowest ceiling any active style
declares, and `hydrate_dynamic_targets` **already applies it per day**, as a
`min()` against that day's final calorie figure. The hard problems are solved
and tested:

- idempotent across the two hydration passes (the UI previews, then generation
  hydrates the same config again — `min()` on an already-capped day returns
  the same figure);
- applied **after** the training uplift, so a workout does not buy an
  exemption from a bound its owner chose to eat inside;
- never applied over a **stated** target (`target_is_stated`);
- lowest wins when two styles declare one, never an average;
- unaffordable ceilings are **reported, not corrected**.

Every one of those properties must survive this change. None of them needs
re-deciding — the change is to *which days* the ceiling is looked up for.

## What to do

Let `active_diet_styles` accept a day-scoped form alongside the flat list it
takes today. Both stay legal, the same way `inventory_to_clear` keeps a bare
string and a `{"item", "quantity_g"}` dict legal — and for the same stated
reason: normalising to one shape would make the honest answer unexpressible.

A bare string keeps meaning "every day". The day-scoped form names the days.

**The spelling is settled here rather than left to the session**, amended
2026-09-01: `design-01` §5 makes this the substrate a *block* reuses, and a
schema three features depend on is not a detail to be picked once the first
one is being written.

```json
"active_diet_styles": [
  "mediterranean",
  { "style": "fast_800", "days": ["Monday", "Tuesday", "Wednesday", "Thursday"] }
]
```

Weekday **names**, matching `weekly_schedule`, `base_schedule` and
`training_schedule` — every other day-keyed structure in this config speaks
weekday names, and `SlotSpec` carries nothing else. Six cases the parser has to
answer, all of them load-time and all of them stated rather than discovered:

| Case | Answer |
|---|---|
| unknown weekday name | **raise**, naming the style and the day |
| `"days": []` | **raise** — "active on no days" is indistinguishable from a mistake, and the way to express it is to remove the entry |
| `days` absent on a dict form | **raise** — the bare string is how you say "every day" |
| same style twice, different days | union the days; not an error |
| style appears bare **and** day-scoped | the bare form wins (every day), and it **warns** — the entry is redundant, not wrong |
| a named day outside the planning week | inert, no error — the week rotates by `week_start_day` and a style may legitimately name days a short block does not reach |

Then:

- One parser, and it is the only thing that reads the raw list. **A malformed
  entry raises; it is not dropped with a warning** — amended 2026-09-01, and
  the amendment reverses this prompt's first draft.

  The draft cited `planner.inventory_entries` and CLAUDE.md's *"a config typo
  must not cost a week of generation"*, which is the right rule for the wrong
  field. Two things separate this case from that one. **The consequences differ
  in kind**: a dropped `meal_overrides` entry costs a pinned budget and the day
  still plans sensibly, where a dropped `fast_800` activation plans the day at
  ~1722 kcal instead of 800 — silently serving twice the intended energy on a
  day whose whole purpose was the restriction. And **this field already fails
  loudly**: `AppConfig.diet_styles_are_known` raises on an unknown style name
  today, so dropping a malformed *wrapper* around that same name would give one
  field two policies, with the quiet one reachable by a smaller typo.
- `diet_style_calorie_ceiling(config, day)` takes the day. Every existing
  caller passes one.
- `AppConfig.diet_styles_are_known` must still cross-check every named style
  against the catalog, in both shapes. That check is on `AppConfig` rather
  than `DietaryRules` because only the parent can see both fields — keep it
  there.
- `build_diet_style_rule` is **per call, and the two axes differ**.
  `generate_meal_type_week` spans the whole week and `generate_day` is one
  day, so a week-spanning call whose days straddle active and inactive must
  say which days each principle binds on — the same problem
  `_sourcing_day_split` already solves for specialty grocers and fresh
  seafood. Reuse that shape rather than inventing a second one. A call whose
  days are wholly inside or wholly outside gets the plain unconditional
  wording.
- **The prompt still never states the calorie number.** That rule is in
  CLAUDE.md and it does not relax here.

## Acceptance

- `tests/test_diet_styles.py` and `tests/test_planner_dynamic_targets.py`
  extended: a 4-day Fast 800 caps exactly four days and leaves three
  untouched; the capped days stay capped across two hydration passes; a
  training day inside the window is capped *after* its uplift; a stated
  target inside the window is not capped at all.
- A flat `active_diet_styles` list produces a **byte-identical** prompt and
  byte-identical targets to before. This is the compatibility claim that
  matters most — assert it, do not assume it.
- Empty `active_diet_styles` still produces a prompt byte-identical to before
  the feature existed.
- **Each of the six parser cases above has a test**, including the two that
  raise. A schema three features depend on earns assertions on its edges.
- CLAUDE.md's diet-styles section updated: the two shapes, why both stay
  legal, why this parser raises where `inventory_entries` drops, and how the
  day-scoped rule reaches the two generation axes.

## Do not

- Add a second config knob for a diet-style calorie *adjustment*. CLAUDE.md
  records why a ceiling is admissible where an adjustment is not, and the
  answer is idempotence. Do not reopen it.
- Move the ceiling out of `hydrate_dynamic_targets`.
- Make diet styles rotate per day or per block the way cuisines do. They are
  a standing choice; this change scopes *when* one is active, not how it is
  picked.
- Copy `inventory_entries`' drop-with-warning policy. It is the right rule for
  a pantry line and the wrong one here — see the amendment above.
