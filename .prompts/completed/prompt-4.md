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