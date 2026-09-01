# dev/

Approved design documents and the prompts that implement them.

| name | is |
|---|---|
| `design-NN-<topic>.md` | a design. `design-00-program.md` is the overview and everything else hangs off it |
| | `design-01` — the preset/block mechanism. `design-02` — `week_shape`, the largest thing a preset carries. `design-03` — what the interface permits, which constrains both |
| `PROMPT-<n>.md` | a briefing for one implementation session. **The number is identity, not rank** — see "Order of delivery" below |
| `DECISIONS-FOR-YOU.md` | **the plain-language decision list** — start here if you want the choices without the architecture |
| | `design-04` — the freezer and a movable prep day (Arm B, chosen next). `design-05` — storage windows per dish, split out of it, and **the first thing being built** (`PROMPT-10`) |
| | `design-06` — exercise planning: persistent personal constraints, selectable gym programs, structured workouts, and the first progression loop |
| `OUTSTANDING.md` | everything still open, ranked — blocked-on-you, undesigned arms, unplaced research, loose decisions, and what should graduate to CHANGE-QUEUE.md |

## These prompts are not queue-safe, structurally

`scripts/claude-queue.sh` runs `find .prompts -type f -name "prompt-*.md"` and
executes each match as `claude -p --dangerously-skip-permissions`. **The glob
is what decides.** These files are in `dev/` and named `PROMPT-*`, so the queue
cannot see them — and that is the point, not an accident. Every prompt here
either touches a live account, has visual acceptance, or has a diagnosis whose
outcome decides its own second half.

Do not move one into `.prompts/` or rename it to `prompt-N.md` to "run it
quickly". That is the single action this naming exists to prevent, and
`.prompts/README.md` says the same thing about its own convention.

## Reading order

1. `design-00-program.md` — the reframe, seven findings, five arms, the
   sequencing, the open decisions.
2. `design-01-presets-and-blocks.md` — Arm A, the first one chosen: the
   preset/block mechanism, the weekly pick, NOVA and the lazy-week axis.
3. `design-02-week-shape.md` — what a preset says about the *grid*. Reviewable
   on its own, and the one document that re-opens something the codebase
   deliberately closed, so it leads with that scar.
4. `design-03-interface-feasibility.md` — **read before finalising 01 §9.2 and
   02 §4.** What the front end can actually do, and the schema constraints that
   fall out of it. Where it disagrees with 01 or 02, it wins: it is the only
   one written capability-first.
5. `design-05-food-safety.md` — **read before running anything**, because
   `PROMPT-10` implemented it and `PROMPT-10` ran first (v0.42.0). It is the
   shortest design in the set and the only one describing a defect rather than
   a feature, and it is now the record of a shipped change rather than a
   pending one.
6. `design-06-exercise-planning.md` — the first designed slice of Arm E:
   personal limitations remain true across weekly presets, age activates
   nothing, and a selected gym program supplies the workout content.
7. The prompts, **in delivery order, not number order** — see below.
   `PROMPT-4` is the Hevy integration; its API-level question was closed from
   public type definitions, so it starts from a much smaller unknown than
   `design-00` D4 originally described.

`design-01` was corrected the same day on two points: **presets are picked
weekly, not set once** — so they are a catalog plus a per-week selection, and a
*block* is the pre-commitment device that suspends that pick — and the
NOVA/processing question was investigated and answered in its §7.

A **research audit** on 2026-09-01 (`design-00` §5a) traced every actionable
claim in `docs/rapid-weightloss.md`, `docs/fitness-model.md` and
`docs/periodization-engine.md` to where it lands. It found two real gaps — a
block had no *successor*, and `training_intent` was stored as an unread string
when the research already specifies its values — plus several ready-made preset
definitions. Both gaps are fixed; the still-open items are marked ❌ in that
table. The later `docs/exercise-protocols.md` audit is now in the same section;
its exercise-prescription and older-trainee outcome layer lands in `design-06`.

**Fourteen written prompts, with 13 reserved.** 1 — the empty `activity_log`
(**done**). 2 — day-scoped diet styles (**done**). 3 — pinning a recipe before
generation. 4 — Hevy. 5 — exporting recipes to Cronometer. 6 — location from
the calendar. 7 — the hard-coding audit (**done**). 8 — the preset container
and weekly pick (**done**). 9 — the preset editor and its validator
(**done**). 10 — per-dish storage windows (**done**). 11 — the freezer ledger.
12 — declarative `week_shape`. 13 — blocks (**reserved, unwritten**). 14 —
personal exercise constraints and gym programs. 15 — constraint-aware workout
plans and progression.

**Six of the fourteen are done**, and the count is stated here as well as in
the table below because this list is the one a reader meets first. `2` and `9`
both lost their `(**done**)` mark for a release after shipping together in
v0.44.0 — the same drift the Status section at the foot of this file records
against itself.

## Order of delivery

The numbers were originally priority and **stopped being so at 7**, which is
recorded here rather than fixed by renumbering — the same call OUTSTANDING.md
makes for its own entries, and for the same reason: every renumber so far has
left citations stale by one, four times in CHANGE-QUEUE.md's history.

**Revised 2026-09-01 after an external review of the whole set.** Three things
moved: `PROMPT-10` was written and went to the front, `PROMPT-1` was marked
done, and the preset pair picked up amendments that have to land inside them
rather than after them. The findings are in "What the review changed" below.

### The plan, in the order to run it

| # | Prompt | Model | Gate — what must be true before it starts |
|---|---|---|---|
| ~~1~~ | ~~**10** — per-dish storage windows~~ | — | **Complete** |
| ~~2~~ | ~~**7** — hard-coding audit~~ | — | **Complete** |
| ~~3~~ | ~~**8** — preset container + weekly pick~~ | — | **Complete** |
| ~~4~~ | ~~**9** — preset editor + validator~~ | — | **Complete** |
| ~~5~~ | ~~**2** — day-scoped diet styles~~ | — | **Complete** |
| **1** | **3** — recipe pin | **Sonnet 5** | 8, for the shared eligibility function |
| **2** | **11** — freezer ledger | **Opus 5** | 10. Windows before stock |
| **2** | **12** — `week_shape` | **Opus 5** | 10, 11. Shapes before reach |
| **3** | **5** — Cronometer export | **Sonnet 5** | A five-minute manual test. Independent |
| **3** | **6** — calendar location | **Sonnet 5** | A manual iCal fetch. Independent |
| **3** | **4** — Hevy | **Haiku 4.5** → **Sonnet 5** | Part 1 is a probe; part 2 waits on a key |
| **4** | **13** — blocks *(unwritten)* | — | 2, 8, 9. The larger half of Arm A |
| **5** | **14** — exercise constraints + gym-program catalog | **Sonnet 5** | 8, 9. Independent of Hevy |
| **6** | **15** — structured workout plans + first progression loop | **Opus 5** | 14. Hevy enriches it but is not a gate |
| **7** | training analytics / full fatigue controller *(partly designed)* | — | Read-only signals first; `design-06` §8 |

**Six prompts are complete**, struck through above rather than deleted so the
gate column still reads as the dependency record it is:

| | Closed | Verified against |
|---|---|---|
| `PROMPT-1` | the empty `activity_log` | every line of its acceptance, 2026-09-01 (`design-00` §5a) |
| `PROMPT-10` | per-dish storage windows | `tests/test_food_safety.py`; CLAUDE.md's "Storage windows belong to the dish" |
| `PROMPT-7` | the hard-coding audit | `design-01` §3.4a, which is the audit and the field list `PROMPT-9` consumed |
| `PROMPT-8` | the preset container, the layer and the weekly pick, v0.43.0 | `tests/test_presets.py` plus `test_config_layout.py`'s layered snapshot |
| `PROMPT-9` | the preset editor and its validator, v0.44.0 | `tests/test_presets.py` (editor classes), `tests/test_preset_validation.py`, and a Playwright drive of Settings → Presets |
| `PROMPT-2` | day-scoped diet styles — the schema blocks reuse, v0.44.0 | `tests/test_diet_styles.py` (all six parser cases and the three ways a call sits against a window), `tests/test_planner_dynamic_targets.py` (the four ceiling properties re-asserted against a four-day window), `tests/test_presets.py` (both shapes arriving from a preset) |

`PROMPT-1` ranked 4th here for a day after it was known to be done, which is
the ordinary staleness this table now carries a date against.

**Each of those six now carries a one-line status banner under its own H1**,
added in v0.44.1. Until then no prompt file recorded its own status anywhere,
and this table could not close the gap: the convention throughout `dev/` is
that a session is handed *the prompt*, cold, and `PROMPT-2`'s header actively
opens with "read this, its role changed" while saying nothing about having
shipped. That is a re-implementation hazard, and it is the one the banner
exists for.

**A banner states a verdict and never a rank**, which is what makes it safe to
duplicate the fact at all. CHANGE-QUEUE.md's own rule is that a closed row's
*reasoning* can go stale even though its verdict cannot — so "shipped in
v0.43.0" is immutable once true, where "immediately after PROMPT-7" is exactly
the sentence that rots. `PROMPT-8` and `PROMPT-9` still carry such a priority
line further down; it is left in place as the record of why they were ordered
that way, now read behind a banner that says the ordering is spent.

**`PROMPT-9` shipped in v0.44.0.** It imported `presets.resolve_config` rather
than writing a second validator, and `planner.resolve_preset_layer` composes
that with the `AppConfig` check the loader also runs — one function, two
presentations. Two things it decided rather than inherited: deleting the
**active** preset is *refused* (not "clear `active`" — deletion must never
silently change what the week plans against), and the field list is bounded to
the audit's `data` rows that already have a config home (`PRESET_EDITOR_FIELDS`
in `ui_state.py`), so items 7 and 8 in CHANGE-QUEUE.md became "the editor gains
a row when this lands" rather than blockers.

### Why that order, in one line each

- **10 first** (done) because it was the only prompt fixing something already wrong
  rather than enabling something new, and because everything in tier 6 makes it
  harder to land later: a freezer ledger and a `week_shape` both extend batch
  reach, and extending reach on one global number makes the rice case worse.
  It also has no dependency on anything else in the set.
- **7 before 8** (both done) — it produced the field list, and wrote no code.
- **8 before 9** held, and the reason proved out: 9 *imports* 8's resolver
  rather than writing a second validator, and that resolver was built to be
  imported — pure, storage-free, and returning displayable failures instead of
  raising, precisely so the loader and the editor cannot disagree about a file.
- **2 and 3 after 8**, which is a change. Both were "widens what a preset can
  say; compiles no logic in", and that is still true — but 3 needs the shared
  eligibility function that 8's preset filter defines, and 2's day-scoped
  schema is what a block reuses, so both are cheaper once the layer exists.

  **2 is done, and the reuse it was moved for is now a real function.**
  `planner.day_scoped_entries` is the parser — general in its subject
  (`subject_key`), so `PROMPT-13`'s blocks answer "which days does this bind
  on" through it rather than inventing a second spelling — and
  `_sourcing_day_split` turned out to need no change at all to carry the
  prompt half, which is the strongest evidence the shape was already there.
  What shipped is an **empty affordance**, per `design-00` F3:
  `active_diet_styles` still ships `[]`, so no generated week moved.
- **Tier 6 is written now** and its two prompts close `OUTSTANDING.md`'s top
  two missing-prompt rows. Both are gated on 10; 12 also waits on 11.
- **Tier 7 is genuinely independent** and can run whenever its manual probe
  passes. Nothing in it blocks anything.
- **Blocks last**, because they are the largest half of Arm A and the only one
  needing 2, 8 and 9 all present.
- **Exercise configuration before exercise generation.** `PROMPT-14` makes the
  persistent facts and selectable program representable without calling a
  model. `PROMPT-15` then has one validated input shape to generate against.
  Neither infers anything from age, and Hevy is optional for the static/manual
  first loop.
- **Training analytics after read-only signals are trusted.** RHR/VO2max
  display, then the HRV band, then Efficiency Factor, then Hevy e1RM, and only
  then anything that *proposes* a schedule change. Nothing in that arm may move
  a calorie target directly.

### What the review changed

An external review on 2026-09-01 checked the set against the code. Four
findings were real and blocking, and all four are now folded into the documents
rather than left as commentary:

| Finding | Where it landed |
|---|---|
| `save_config_keys` **cannot** write `presets.json` — it raises on every key outside `CONFIG_FILES`, so the weekly pick could not have run | `PROMPT-8` §1a — two new repository methods |
| Whole-key replacement made §2's own `comfort` preset **silently delete 17 `banned_ingredients` entries** — it validates cleanly, because `DietaryRules` has no required fields | `design-01` §3 — typed leaf-path overrides, with the refutation kept |
| `default` was the diff baseline **and** deletable | `PROMPT-8` §1 — the baseline is the base config; `default` is an ordinary row |
| A block could pin a `preset`, which one flat `AppConfig` cannot represent mid-week | `design-01` §4.1a — the field is gone; blocks set dated numbers |

Four more were real amendments: `FreezerItem` gained `frozen_on` and its own
snapshots (`design-04` §2.1), `freeze_portions` gained a confirmation step
(§2.2), `design-05` gained a days-not-hours policy (§2a), and `PROMPT-3` gained
the shared eligibility function (which `design-01` §7.1 needed anyway, for a
second claimant it had not anticipated).

Two findings were **already answered** in the documents and needed no change —
`PROMPT-1`'s stale rank (self-flagged in `design-00` §5a) and
`recipe_pin_origin` (already specified in `PROMPT-3`, following `link_origin`).

### Why those models, and the axis it turns on

**What makes a session expensive is not the size of the diff — it is how much
of the codebase you have to hold in your head to avoid breaking something
else.** That is the axis, and it sorts these nine cleanly:

- **Opus 5** — the change crosses module boundaries, or an invariant spans two
  passes and nothing local would catch a violation. `PROMPT-10` is the clearest
  case in the set: it touches five consumers, inverts the codebase's own
  defaulting convention on purpose, and its failure mode is somebody getting
  ill rather than a worse meal plan. `PROMPT-8` moves config
  validation to after the preset layer, which is the load path *every* feature
  sits on. `PROMPT-2` has to preserve four hydration properties across both
  passes, and this exact area has already shipped an idempotence bug — the
  uplift-unwinding pass that took a 2200 kcal override down to 1850. `PROMPT-7`
  writes no code at all and is still Opus, because the *judgement* is the
  deliverable and a row wrongly ruled `code` stays compiled in for another
  release.
- **Sonnet 5** — the work is well-specified and pattern-following inside one or
  two modules. `PROMPT-9` is a copy of `training_editor` plus a pure validator;
  `PROMPT-6` copies the sync-service shape; `PROMPT-3` has its three rules
  stated and an existing pin path to follow. Heavy specification is what lowers
  the model requirement, which is the argument for writing these prompts
  carefully in the first place.
- **Haiku 4.5** — mechanical and verifiable by running it. `PROMPT-4` Part 1
  makes an authenticated request and reports what fraction of working sets
  carry a non-null `RPE`. That answer decides Part 2; it does not need judgement
  to produce.

**Model choice is the smaller lever, and it should be said plainly.** What
actually holds these sessions down is that each prompt is a **cold session
naming the two or three CLAUDE.md sections to read** — CLAUDE.md is long, and a
session that reads all of it before writing a line has spent more than the model
  tier will ever save. Keep that habit when writing the next prompt.

Switch per session with `/model`. On Opus 5, `/fast` gives faster output at the
same model — it does not downgrade to a smaller one, so it costs nothing in
quality on the two rows above that need it.

**Prompts 7–9 are the answer to a gap found on 2026-09-01**: the entire preset
mechanism — three design documents — had **no prompt at all**, so under the
working assumption that `dev/PROMPT-*` *is* the outstanding work, Arm A was
unscheduled. `presets.json` appeared in no prompt in the set.

## Status

The designs remain **drafts for approval**, but the arm is now partly built:
`PROMPT-1`, `-10`, `-7`, `-8`, `-9` and `-2` have shipped (see "Order of
delivery"), so Arm A's enabling half — the preset container, the layer, the
weekly pick, the editor, and the day-scoped schema blocks reuse — is done
through v0.44.0. `data/biometrics.json` holds 23 `activity_log` rows over 16
dates and 28 `readiness_log` rows. What is left in the arm is **the recipe pin
(`PROMPT-3`, next up)** and blocks (the larger, dated half, still unwritten).

Arm E gained its first designed slice in the same pass: `design-06`,
`PROMPT-14` and `PROMPT-15` are **written and unstarted**, and none of the
three gates anything in Arm A.

**This paragraph was stale by one until v0.44.1**, naming five shipped prompts
and listing day-scoped diet styles as outstanding while the delivery table 140
lines above it already recorded that prompt against v0.44.0. That is the
ordinary drift of a summary kept beside the thing it summarises, and it is
worth a rule rather than a correction: **the delivery table is the authority
and this paragraph is a reading of it**, to be checked against it on every
release that closes a prompt.

**`design-01`, `design-04`, `design-05` and `PROMPT-2`, `-3`, `-8`, `-9` were
amended on 2026-09-01** after the review summarised above. Every amendment is
marked in place and keeps the superseded reasoning rather than overwriting it —
the same device `design-02` §5 uses for its own moved premise, and for the same
reason: a claim that was wrong is more useful with the refutation attached than
deleted.

**F1 and F5 were both revised under challenge on the day they were written,
and F5 twice.** F1's claim was too absolute and now states what a 7-day grid
genuinely gives up. F5 started as "derive, don't store", conceded that a
published nutrition figure is an observation worth keeping, and then conceded
the larger point — a **NOVA score** is a scale-invariant measurement and
belongs in storage; only the *verdict* against `allowed_nova_groups` has to be
derived. It now turns on one rule: **store a measurement iff it is
scale-invariant; never store a verdict.**

Every claim about the code was verified on **2026-09-01** against `main` at
`418c223` — per CHANGE-QUEUE.md's rule that a report is verified the day it is
*filed*, not the day it is picked up. Re-check `design-00`'s Findings section
before acting on any of it; F6 in particular is an observation about live data
that a single sync could change.
