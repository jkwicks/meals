# Prompt 1: Async Generation Engine & Non-Blocking UI

```
Act as a Principal Software Engineer. We need to wire the inert "Generate Week" button in ui_app.py so it can generate plans without freezing the NiceGUI event loop.

Files to modify: ui_app.py, planner.py

1. In planner.py, wrap the synchronous generate_week_plan logic (or instructor client calls) so it can be safely dispatched from an async handler without blocking NiceGUI's main asyncio event loop.
2. In ui_app.py, enable the "Generate Week" button. 
3. When clicked, display a NiceGUI loading dialog or linear progress bar on the screen showing per-day progress status (e.g., "Generating Tuesday (2/7)..."). Use progress_callback to push real-time updates to the UI.
4. Upon successful completion, automatically save the generated WeekPlan via REPOSITORY.save_week_plan(), update PlannerState, and call the canvas/header refresh handlers so the grid renders immediately without requiring a full browser refresh.
5. Handle day generation failures gracefully by displaying a NiceGUI notify toast with the failure details.

Verify by running ./server.sh start and triggering a generation run from http://localhost:8080.
```

# Prompt 2: Target Customization & Shopping List Slide-Over Drawer
```
Act as a Principal Software Engineer and Product Designer. We need to restore missing functionality in ui_app.py: pre-generation target editing and shopping list viewing.

Files to modify: ui_app.py, shopping.py, config.json

1. Shopping List Drawer:
   - Add a "Shopping List" action button in the top header or left sidebar.
   - Clicking it opens a NiceGUI slide-over drawer (or wide ui.dialog) displaying the current week's staggered shopping windows (e.g., "Sunday Trip", "Mid-Week Top-Up").
   - Use shopping.py (format_shopping_list_markdown or format_shopping_list_text) to group items by grocery department.
   - Highlight perishable items flagged with buy_late ("← buy fresh closer to the day").
   - Add a "Copy for Google Keep" button that formats the list via format_shopping_list_keep() and copies it to the clipboard using ui.run_javascript().

2. Pre-Generation Macro & Pantry Customization:
   - Expand the Left Drawer in ui_app.py to include a collapsable "Daily Macro Targets & Overrides" section.
   - Allow the user to view and temporarily override Calorie/Protein/Carb targets for specific days before generation.
   - Add an "Inventory to Clear (Pantry)" multi-item input box that populates config['inventory_to_clear'] so the user can enter ingredients that need to be prioritized in the upcoming generation.
   - Ensure target changes persist in state and pass into generate_week_plan().

Verify that the shopping list renders cleanly with checkboxes and that pantry items pass to the planner brief.
```

# Prompt 3: UI Design Overhaul (Human Interface & Visual Density)
Act as an Expert Product Designer and Frontend Engineer specializing in high-density desktop dashboards. We are refining the visual design of ui_app.py to make it visually engaging, scannable, and modern.

```
Files to modify: ui_app.py

1. Visual Hierarchy & Card Scannability:
   - Redesign the meal card layout inside the 7-column canvas.
   - Meal Titles: Use clear typographic contrast (bold, clear sizing) and truncate gracefully with tooltips for long dish names.
   - Mode Badges: Differentiate COOK vs. LEFTOVER vs. SKIP using distinct visual chips:
     * COOK: Subtly tinted emerald background with a clean accent pill.
     * LEFTOVER: Distinct sky-blue border/chip with an explicit source indicator ("↩ from Mon dinner").
     * SKIP: Muted dark tone.
   - Macro Badges: Format calories, protein, carbs, and fat into clean, compact inline micro-pills (e.g., "450 kcal · 45g P · 30g C · 12g F") instead of raw text blocks.

2. Macro Telemetry Progress Bars (Header):
   - Upgrade the daily macro progress bars at the top of each day column.
   - Show target vs. actual calories and protein as dual-segmented progress bars or clear percentage rings/bars.
   - Color code bar state: Green (within ±5% of target), Amber (under/over budget by 10-15%), Red (significant macro imbalance).

3. Interaction Polish:
   - Make hover states explicit with sleek border-glow effects on meal cards.
   - Ensure "Link to next lunch" macro action feels like a primary button with immediate reactive state updates.
   - Add clear iconography (using Quasar/Material icons available in NiceGUI, e.g., icon='restaurant', icon='shopping_cart', icon='bolt') to sidebars, headers, and modal actions.

Run the server to confirm the visual overhaul maintains high information density while eliminating visual clutter.
```

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

# Prompt 5: Comprehensive User Manual & Testing Guide (README.md)

```
Act as a Technical Writer and Product Manager. Create a comprehensive, production-grade `README.md` for this AI Weekly Meal Planner application that serves as both a user manual and an end-to-end functionality verification guide.

Files to create/modify: README.md

Structure the document into the following clear sections:

1. Overview & Core Philosophy:
   - Explain the "Eating Slots vs. Cook Events" concept, derived portion counts, and staggered multi-trip shopping lists.

2. Quick Start & Setup:
   - Environment setup (`venv`, `.env` configuration, OpenRouter API keys).
   - How to start the server via `./server.sh start` and access the high-density NiceGUI desktop canvas on `http://localhost:8080`.

3. Feature Guides & Workflows:
   - Target Tuning & Training Plans: How to set daily macro budgets, meal overrides, and input workout schedules.
   - Pantry Clearing (`inventory_to_clear`): How to clear fridge leftovers by prioritizing specific ingredients.
   - Cook Once, Eat Twice Auto-Chaining: How to use "Link to next lunch" to link dinner leftovers to next-day lunches and automatically scale portion batches.
   - Non-Blocking Week Generation: How to trigger async week generation and monitor real-time day progress.
   - Staggered Shopping Lists: Viewing and exporting multi-trip grocery lists formatted for Google Keep or Markdown.

4. End-to-End Verification Checklist:
   - A step-by-step audit procedure for validating key app features:
     [ ] Macro Telemetry recalculation on schedule edits.
     [ ] Leftover chaining & visual outline highlighting.
     [ ] Async background week generation without UI freeze.
     [ ] Departmentalized shopping list generation and Google Keep clipboard export.
     [ ] Training day carb-shifting and digestion constraint application.

5. CLI vs. UI Commands:
   - Quick reference table of all `planner.py` CLI arguments vs. NiceGUI actions.
```