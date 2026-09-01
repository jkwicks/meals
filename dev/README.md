# dev/

Approved design documents and the prompts that implement them.

| name | is |
|---|---|
| `design-NN-<topic>.md` | a design. `design-00-program.md` is the overview and everything else hangs off it |
| | `design-01` — the preset/block mechanism. `design-02` — `week_shape`, the largest thing a preset carries. `design-03` — what the interface permits, which constrains both |
| `PROMPT-<n>.md` | a briefing for one implementation session. **The number is identity, not rank** — see "Order of delivery" below |
| `DECISIONS-FOR-YOU.md` | **the plain-language decision list** — start here if you want the choices without the architecture |
| | `design-04` — the freezer and a movable prep day (Arm B, chosen next). `design-05` — storage windows per dish, split out of it, and **the first thing being built** (`PROMPT-10`) |
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
   `PROMPT-10` implements it and `PROMPT-10` runs first. It is the shortest
   design in the set and the only one describing a defect rather than a
   feature.
6. The prompts, **in delivery order, not number order** — see below.
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
table.

**Twelve prompts.** 1 — the empty `activity_log` (**done**). 2 — day-scoped diet
styles. 3 — pinning a recipe before generation. 4 — Hevy. 5 — exporting recipes
to Cronometer. 6 — location from the calendar. 7 — the hard-coding audit. 8 —
the preset container and weekly pick. 9 — the preset editor and its validator.
10 — per-dish storage windows. 11 — the freezer ledger. 12 — declarative
`week_shape`.

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
| **1** | **10** — per-dish storage windows | **Opus 5** | Nothing. Fixes a live defect |
| **2** | **7** — hard-coding audit | **Opus 5** | Nothing. No code; produces 9's field list |
| **3** | **8** — preset container + weekly pick | **Opus 5** | 7 |
| **4** | **9** — preset editor + validator | **Sonnet 5** | 8 (imports its resolver), 7 |
| **5** | **2** — day-scoped diet styles | **Opus 5** | 8. Its schema is the substrate blocks reuse |
| **5** | **3** — recipe pin | **Sonnet 5** | 8, for the shared eligibility function |
| **6** | **11** — freezer ledger | **Opus 5** | 10. Windows before stock |
| **6** | **12** — `week_shape` | **Opus 5** | 10, 11. Shapes before reach |
| **7** | **5** — Cronometer export | **Sonnet 5** | A five-minute manual test. Independent |
| **7** | **6** — calendar location | **Sonnet 5** | A manual iCal fetch. Independent |
| **7** | **4** — Hevy | **Haiku 4.5** → **Sonnet 5** | Part 1 is a probe; part 2 waits on a key |
| **8** | **13** — blocks *(unwritten)* | — | 2, 8, 9. The larger half of Arm A |
| **9** | training analytics *(undesigned)* | — | Read-only first; see below |

`PROMPT-1` is **complete** and off the list — verified against every line of its
acceptance on 2026-09-01 (`design-00` §5a). It ranked 4th here for a day after
that was known, which is the ordinary staleness this table now carries a date
against.

### Why that order, in one line each

- **10 first** because it is the only prompt fixing something already wrong
  rather than enabling something new, and because everything in tier 6 makes it
  harder to land later: a freezer ledger and a `week_shape` both extend batch
  reach, and extending reach on one global number makes the rice case worse.
  It also has no dependency on anything else in the set.
- **7 before 8** unchanged — it produces the field list, and it writes no code.
- **8 before 9** unchanged, and now stronger: 9 *imports* 8's resolver rather
  than writing a second validator.
- **2 and 3 after 8**, which is a change. Both were "widens what a preset can
  say; compiles no logic in", and that is still true — but 3 needs the shared
  eligibility function that 8's preset filter defines, and 2's day-scoped
  schema is what a block reuses, so both are cheaper once the layer exists.
- **Tier 6 is written now** and its two prompts close `OUTSTANDING.md`'s top
  two missing-prompt rows. Both are gated on 10; 12 also waits on 11.
- **Tier 7 is genuinely independent** and can run whenever its manual probe
  passes. Nothing in it blocks anything.
- **Blocks last**, because they are the largest half of Arm A and the only one
  needing 2, 8 and 9 all present.
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

Both designs are **drafts for approval**. Nothing here is built, with one
exception: `PROMPT-1` shipped, and `data/biometrics.json` now holds 23
`activity_log` rows over 16 dates and 28 `readiness_log` rows.

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
