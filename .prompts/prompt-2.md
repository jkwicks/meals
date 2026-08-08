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