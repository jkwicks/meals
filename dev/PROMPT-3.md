# PROMPT-3 — Pin a specific recipe to a slot before generation ("steak on Wednesday")

**Shipped in v0.45.0.** The review dialog now stages a catalog recipe for a
specific cook slot, with one shared hard-rule eligibility gate used by both
user pins and automatic favourites. User pins survive full-week regeneration
and cost no model call. Kept as the record of what was asked for and why; it is
not work to pick up. `dev/README.md`'s order of delivery is the authority on what
is still outstanding — this banner states a verdict, never a rank, because a
verdict cannot go stale.

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It
touches `ui_*.py` and its acceptance is partly visual.

Cold session. **Load the `ui-work` skill before editing any `ui_*.py`** — it
is the front-end contract and is not in CLAUDE.md. Read
`dev/design-00-program.md` finding **F3**.

**Re-checked against `design-01`–`03` and unchanged in scope.** It is the
"still yours" half of `design-01` §8: a preset decides the numbers and the
week's shape, and the user keeps the vetoes — "steak on Wednesday" is the
loudest of them. `design-03` §2 confirms the widgets it needs (a select and a
dialog list) are shapes this front end already has, so it stays an **S** and
does not wait on the preset mechanism.

## The problem

There is no way to say "steak on Wednesday" *before* a week is generated.
Today:

- `planner.select_favorite_assignments` pins catalog recipes automatically, by
  strict LRU, and the user cannot choose which or where;
- `PlannerState.swap_slot_with_favorite` lets the user choose — but only
  **after** generation, so the model was already paid for a dinner that is
  then thrown away;
- the review dialog sets *style* and *cuisine* per slot, never a recipe.

## Why this is small

`SlotSpec.recipe_id` already exists and is honoured end to end. A pinned slot
**stays a cook**, so portions derive, shopping aggregates it, `span_days`
works, and no `mode == MODE_COOK` test anywhere needs revisiting — that was
the explicit reason a fourth mode was rejected when the field was added. What
is missing is only the affordance: nothing but the automatic selector ever
sets it.

## What to do

Give the review dialog a per-slot recipe pin, sourced from the catalog and
filtered to that slot's `meal_type`. Reuse `repository.catalog_matches` — it
is the one filter the Library grid and `/api/recipes` already share, and a
third implementation is exactly the silent drift CLAUDE.md records between
those two (`"All"` vs `None` for the no-filter meal type, which returned
different lists and raised no error).

**One eligibility function, called by both claimants** — added 2026-09-01, and
it is the only genuinely new thing in this prompt.

`design-01` §7.1 records that catalog *selection* has to filter on the active
preset's `allowed_nova_groups`, because the catalog outlives the preset that
admitted a recipe: a NOVA-4 dish imported during a `comfort` week sits in
`recipes_master.json` for ever and is an ordinary candidate during a `strict`
one. That section wrote the filter into `select_favorite_assignments`, which
was the only claimant when it was written. **This prompt adds the second**, and
a filter living inside the automatic selector is one a hand-picked pin walks
straight past.

So: one function — *may this recipe be served in this slot, under this
config* — called by `select_favorite_assignments` and by the pin path. It is
also the natural home for the `meal_type` match, which both need anyway.

What it decides, and what it must not:

- **A user pin overrides preference.** Style rotation, cuisine blocks, the LRU
  reuse window, the dinner cap — a pin outranks all of them. That is §8's
  "still yours" line and the whole point of the feature.
- **A user pin does not override a hard rule.** `banned_ingredients` and
  `allowed_nova_groups` are enforced by `Ingredient`'s validators at
  generation, and a pin is the one path that reaches a slot **without passing
  through them** — the recipe is already built. Refuse it, name the reason,
  and say which rule refused: "Slow Cooked Beef Cheeks — contains an
  ingredient on your banned list" is actionable where a greyed-out row is not.
- **Ship it filtering the *offered list*, not just the accepted pin.** A dialog
  that lets you choose a dish and then refuses it teaches you to distrust the
  dialog. Refusal on accept is the backstop for a config that changed between
  opening the dialog and pressing Generate.

Three rules the existing pin path already establishes, all of which apply:

- **Normalise to one serving** via `planner.single_serving` before pinning. A
  catalog record is stored at whatever portion count it was bookmarked at; a
  2-serving dinner needs a 0.5 factor, which is outside `portion_trim_limits`,
  so the clamp fires at 0.6 and the slot silently serves 20% over budget. This
  bug has already been fixed once for the automatic path — do not reintroduce
  it.
- **`week.pin_recipe` blanks the slot's style and cuisine.** Keep that. A
  scramble pinned onto a `yoghurt_bowl` slot otherwise renders as "YOGHURT
  BOWL" over a plate of eggs.
- **A user pin outranks an automatic one.** `select_favorite_assignments` only
  ever fills an *empty* slot, so a user-pinned slot must be invisible to it —
  the same precedence a hand-set style or cuisine already gets over a computed
  one.

**The clear on regeneration is the trap.** `ui_generation.generate_week` calls
`week.clear_recipe_pins` unconditionally on every full-week run, alongside
`clear_styles`/`clear_cuisines` — without it, week one's favourites are
re-served forever and the reuse window never advances. A **user** pin must
survive that clear, exactly as a user-chosen style and cuisine already
survive it. Distinguish the two the way `link_origin` already distinguishes
who made a leftover link (`LINK_ORIGIN_USER` / `_LOCATION` / `_BATCH`) — that
is this codebase's established pattern for "who decided this, and what may
overwrite it", and it should not get a second one.

Consider whether the pin should also be reachable from the day inspector; if
so it belongs in `ui_state.py` as a single method both surfaces call, not
duplicated.

## Acceptance

- `tests/test_ui_state.py`: pinning sets `recipe_id`, normalises to one
  serving, blanks style and cuisine, and **survives a full-week regeneration**
  while an automatic pin does not.
- `tests/test_meal_selection.py`: `select_favorite_assignments` skips a
  user-pinned slot and does not count it against `favorite_dinner_slots`.
- **A recipe carrying a banned ingredient cannot be pinned** — it is absent
  from the offered list, and refused with the rule named if pinned anyway.
  Same for a recipe outside `allowed_nova_groups`. Both claimants go through
  the one function; assert there is no second implementation.
- **A pin still outranks every preference**: a pinned dinner survives a
  cuisine block that would have assigned it another cuisine, and is served on
  a day the LRU window would have declined.
- A pinned slot costs no generation call — assert the model is not asked for
  that slot. `_generate_meal_type_events` derives which slots to ask for from
  `day_budgets`' keys; CLAUDE.md records that passing the full dict generated
  and paid for a second recipe for an already-filled slot.
- Staged-changes bar reports the pin, and it does not persist to `config/`:
  this is an input to the next run, not a standing setting. Only
  `target_modes` and an accepted `training_schedule` proposal write to
  `config/`, and this is neither.
- CLAUDE.md's "Some slots are decided before the model is called" section
  updated with the user pin as a fourth claimant and its precedence.

## Do not

- Add a fourth `SlotSpec.mode`.
- Persist the pin to `config/`.
- Change `select_favorite_assignments`' LRU rule, its dinner cap, or the
  `cuisine_run_ends` placement logic. This adds a claimant ahead of it; it
  does not alter how it picks.
- Write a second filter. If `design-01` §7.1's preset filter has not landed
  yet, build the shared function here with the `meal_type` and hard-rule
  checks, shaped so the NOVA filter drops in — do not leave a pin path with no
  eligibility check at all on the grounds that the automatic one has none yet.
