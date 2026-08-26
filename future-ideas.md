# Future ideas

Scoped but deliberately not built yet — each needs either a product decision
only the maintainer can make, or real time/data neither engineering nor this
document can shortcut. Phase 5a (dated `meal_history.json` entries, each
day's planned targets archived alongside) shipped for real; see CLAUDE.md's
"The Today tab" section and `week.day_date`/`planner.record_week_history`.
5b and 5c below are what's left of that Phase 5 roadmap.

A mock-data visual preview of what 5c's Trends tab could look like exists as
a Claude artifact (not in this repo — built for review, not committed). Ask
for it to be republished if the link has gone stale.

## 5b — Adherence and workout-completion tracking

**The gap:** `training_schedule` (config/schedule.json) is a *declared*
plan; nothing observes whether a session actually happened. Nothing records
whether a planned meal was actually eaten, skipped, or swapped for something
else either — the closest thing today is `ui_cards`'s swap-with-favorite
flow, which changes the plan itself rather than logging a deviation from it.

**Why this isn't just plumbing.** Two decisions belong to the maintainer,
not to whoever builds this:

1. **Where it's stored.** Proposed: a new `data/adherence.json`
   (`AdherenceEntry`: `date`, `slot_id`, `status` — `eaten` / `skipped` /
   `swapped`, `marked_at`) and a new `data/workout_log.json`
   (`WorkoutCompletion`: `date`, `session_type`, `scheduled`, `completed`,
   `source`). Neither folds into `daily_actuals` — a manual mark and a
   Cronometer sync writing the same key would silently overwrite each other
   with no way to tell which won, the same reasoning CLAUDE.md's biometric
   sync section already applies to keeping `weigh_ins` and `daily_actuals`
   as separate upsert targets.
2. **What "click a card" means.** A Today-tab click currently opens recipe
   detail (shipped in v0.18.0). Adding "mark eaten" needs its own affordance
   — a checkbox, a swipe action, a second click target on the same card —
   and that changes the interaction model of a screen that just shipped.
   Worth a deliberate look, not an assumption.

**Proposed schemas** (Pydantic, matching this codebase's existing style —
`planner.py`'s `WeekPlan`/`CookEvent` pattern):

```python
class AdherenceEntry(BaseModel):
    date: str                                    # ISO date the slot was eaten on
    slot_id: str                                 # "Sunday:dinner" — same shape as WeekPlan.slots
    status: Literal["eaten", "skipped", "swapped"]
    marked_at: str                                # ISO datetime the mark was made

class WorkoutCompletion(BaseModel):
    date: str
    session_type: str                             # matches training_schedule's own `type` values
    scheduled: bool
    completed: bool
    source: Literal["manual", "garmin"] = "manual" # "garmin" reserved for a future
                                                    # activity-type match; every mark
                                                    # today would be "manual" — GarminSyncService
                                                    # has no completion signal yet, only
                                                    # gross/net calories and readiness
```

**Order, if built:** repository methods (`save_adherence_entry`,
`load_adherence`, same upsert-by-`date`+`slot_id` shape as
`_upsert_dated_entry`) before any UI — the write path is the real design
question, not the read path.

## 5c — Trend charts

**Blocked on time, not engineering.** Current real numbers, checked
2026-08-16: `biometrics.json` holds **one** weigh-in and **one**
`daily_actuals` row, both already stale relative to today; `meal_history.json`
has 21 entries, only the newest few dated (5a is forward-only — it doesn't
backfill). A 14/30-day chart built today would be near-empty or actively
misleading regardless of how well it's built. This is why 5a shipped alone
and 5c stayed a mockup: 5a (and 5b, once built) need real weeks of runtime
before a chart is worth shipping.

**Chart plan, once there's data to chart** (see the mockup artifact for the
visual treatment — reuses `ui_theme`'s existing `STATUS_STYLES`/
`BAND_COLOURS`/`MACRO_TINTS` rather than a new palette):

| Chart | Source | Form |
|---|---|---|
| Weight vs. target | `biometrics.json` weigh-ins + `user_profile.target_weight_kg` | line vs. dashed baseline |
| Calories: actual vs. planned | `daily_actuals` + `dated_history[].targets` (5a) | line vs. baseline, dot coloured by `macro_band()` — same on/near/off the Week tab's telemetry bars already use |
| Macro accuracy (14-day) | same, per macro | diverging bar, signed % deviation |
| 7-day adherence, gym completion | 5b's `AdherenceEntry`/`WorkoutCompletion` | stat tiles (KPI row) |
| Recent weigh-ins | `biometrics.json` | table — needs no new schema |

**Library:** `ui.echart`, bundled with NiceGUI 3.16 (already installed,
confirmed in this repo's venv) — no new dependency. `plotly`/`matplotlib`
are not installed and would be for three line charts.

**Not scoped at all yet:** food waste tracking. Flagged in the original
architecture review as having no data source whatsoever — would need a new
logging entry point of its own, a separate product decision from either 5b
or 5c above.

## Rejection-list decay

**The gap:** rejection capture (phase 4 of `ui-redesign.md`; see CLAUDE.md's
"Rejection capture") ships without any decay on the preference list —
`build_rejection_rule` sends every entry in `data/rejections.json`, forever,
to every generation call. That was a deliberate choice to raise the question
rather than settle it, not an oversight: the phase that added this said so
explicitly, and left picking a policy to the maintainer.

**Why this isn't just plumbing.** A dislike recorded once and honoured
forever will eventually starve the rotation, the same failure mode
`planner.next_choice`'s docstring already documents for why style/cuisine
rotation is strict LRU rather than "unused in the last N": with a handful of
dinner favourites and a growing rejection list, the model could end up with
nothing left it's allowed to suggest for a slot. A decay window (only count
rejections from the last N weeks, or discount older ones) is probably the
right shape, but:

- **What N should be** is a real product call — too short and a genuine,
  stable dislike stops being honoured after a month; too long and it's
  today's unbounded behaviour with extra steps.
- **Whether it's a hard cutoff or a soft discount** changes
  `build_rejection_rule`'s aggregation, not just its inputs — a soft decay
  might weight a recent "too much prep" more heavily than one from three
  months ago rather than dropping the old one outright.
- **Whether the reason matters to the decay rate.** "Had it recently" is
  arguably self-resolving (the dish stops having been had recently) in a way
  "don't fancy it" isn't — a single decay window applied uniformly across
  all four reasons may not be the right model at all.

**Order, if built:** the decision belongs in `planner.build_rejection_rule`
(currently a pure function of `config["rejected_preferences"]`, so the
filtering/weighting can happen either there or at the point that list is
assembled before injection) — no repository or storage change needed either
way, since `data/rejections.json` already keeps every entry's `date`.

## Pantry photo → an inventory ledger with real quantities

**The gap:** `config.inventory_to_clear` is a flat list of strings ("600g
chicken thighs", "half a bag of spinach") and `inventory_instruction()` sends
it as one priority line per day. There are no quantities the code can reason
about, which is why CLAUDE.md is explicit that the shopping list can't
subtract inventory from what it tells you to buy. It also means one tin of
tuna can be written into five recipes in the same week — nothing tracks that
it was spent the first time.

**Two decisions belong to the maintainer, not to whoever builds this:**

1. **Whether the photo path earns a third model role.** `models.json` names
   two today (`meal_generation_model`, `recipe_parser_model`), both text.
   Reading a pantry shelf needs a vision model, and it needs somewhere to put
   the image: `StoragePaths` handles JSON only, and nothing in `data/` is
   binary. The cheaper first version skips the camera entirely — a quantity
   column on the existing list, typed by hand — and that version delivers
   most of the value below, because the ledger is the hard part, not the OCR.
2. **Whether a decremented ledger writes back to disk.** A count that only
   lives for one run is honest and simple; a count that persists starts
   disagreeing with the actual shelf the moment you cook something without
   telling the app, which is the same "state able to disagree with reality"
   problem the shopping list's unpersisted checkboxes were designed around.

**The mechanism, once those are settled, already has a precedent in the
codebase.** A week-wide count that each generation stage spends and passes
on is exactly what `seafood_used` does for
`sourcing.max_seafood_meals_per_week`, and what `avoid_proteins`/
`avoid_recipe_names` do for variety: seed before the stage loop, subtract
each stage's actual output, hand the remainder to the next axis. An
inventory ledger is that pattern with a dict instead of an int —
`{"tinned tuna": 1}` seeded from config, decremented by what each meal type
actually used, and once an item hits zero the later axes are told it is gone.
Doing it any other way (handing every meal type the full pantry) permits four
meal types to each claim the same tin, which is the current behaviour.

**What it would then unlock, and only then:** subtracting inventory from the
shopping list, which is the thing people actually want from this feature and
which is impossible today for want of a number.
