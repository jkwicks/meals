# Prompt 4: Ingesting Weekly Training Plans

```
Act as a Principal Backend and Systems Engineer. We need to implement a Training Schedule Ingestion Engine that dynamically adjusts daily and per-meal macronutrient targets based on a user's weekly workout schedule.

Files to modify: planner.py, week.py, ui_app.py, config.json

1. Schema & Data Updates (config.json & planner.py):
   - Update config.json schema to include an optional "training_schedule" array. Each item contains:
     {"day": "Monday", "time": "07:00", "type": "gym_hypertrophy" | "cardio_run" | "walk" | "rest", "duration_minutes": 60, "estimated_burn_kcal": 350}
   - In planner.py, create a function `apply_training_adjustments(config: dict) -> dict` that executes before target calculation:
     * Step A (Daily Budget Expansion): Adds `estimated_burn_kcal` directly to the day's total target calories. Derives additional Carbohydrates (4 kcal/g) and Protein based on activity intensity.
     * Step B (Meal Slot Pinning): Identifies the meal slot immediately preceding or following the workout time. Injects an explicit `meal_override` for that slot (e.g., allocating 50% of the day's carbs to post-gym dinner).
     * Step C (Digestion Rules): If a workout is scheduled within 2 hours after a meal, injects slot prompt constraints demanding "low-fiber, ultra-easily digestible, low-fat pre-workout fuel".

2. UI Controls (ui_app.py):
   - Add an expandable "Training & Activity Schedule" section in the Left Drawer.
   - Allow the user to view, add, or edit workout sessions for each day (Day, Time, Activity Type, Duration, Kcal Burn) directly from the UI.
   - Bind these schedule inputs to `PlannerState` so changes dynamically re-calculate and preview the top Macro Telemetry progress bars in real-time before clicking "Generate Week".

3. Prompt Brief Integration (planner.py):
   - Update `build_slot_brief()` to explicitly include training context in the system prompt for affected slots (e.g., "[POST-WORKOUT MEAL: High glycogen replenishment required]").

Verify by running `./server.sh start`, editing a Monday morning gym session, and confirming that Monday's macro telemetry and meal briefs scale appropriately.
```