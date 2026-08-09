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