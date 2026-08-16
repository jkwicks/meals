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
