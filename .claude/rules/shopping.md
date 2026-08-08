---
paths: ["shopping.py"]
---

# Shopping lists

- `shopping.py` aggregates **cook events**, not days: `aggregate_cook_events()`
  takes the events in a window plus the window's day list, so ingredient
  totals include every portion of a batch on the day it is cooked.
- `ShoppingItem.latest_cook_offset` records how many days into the window an
  ingredient is finally needed; `buy_late` flags perishables
  (`week.PERISHABLE_DEPARTMENTS`) needed `PERISHABLE_DAY_GAP`+ days later. It
  only annotates — it never moves an item to another trip, since whether to
  make a top-up trip is the shopper's call.
- Multi-day windows are the *point* of this design, so the fresh-food tension
  is surfaced rather than solved.

## Ingredient name handling — every rule here came from a real bad line

Models write ingredient names for a cook, not for a shopper. `shopping.py`
normalises them before combining; each rule fixes something observed:

- **`strip_parentheticals()` runs before the comma split.** "Egg yolks (large,
  from free-range eggs)" split first leaves the dangling "Egg yolks (large".
- **`ingredient_head()`** keeps only the part before the first comma — what you
  buy, not how it's handled.
- **`contains_word()`** matches whole words with real plural forms. Substring
  matching rendered "Eggplant, cubed" as **"10 eggs"** and filed "Garlic,
  minced" under Meat & Poultry (the "mince" keyword). A bare `+s` plural
  missed "potatoes" and "berries".
- **`categorize_department()` picks the longest matching keyword**, not the
  first department in the list. Specificity beating list order is what fixes
  "garlic cloves" (spice "clove" vs produce "garlic"), "cauliflower rice"
  (vs "rice"), and "beef broth" (vs "beef") without fragile reordering.
  `<animal> broth` pairs are still spelled out, since "chicken" is longer
  than "broth".
- **`PREP_QUALIFIERS` vs `STATE_QUALIFIERS` is the important distinction.**
  Prep words (diced, sliced, grilled) are stripped so "Cucumber, diced" and
  "Cucumber, sliced" are one line. State words (cooked, dry, canned, frozen)
  are *preserved and folded into the key*, because they change what a gram
  means — merging "Quinoa, dry" with "Quinoa, cooked" would understate the
  shop. `raw`/`uncooked` are prep, not state: they describe the default, and
  treating them as state split "Red bell pepper" from "Red bell pepper (raw)".
- **`singularize()` is key-only.** Note the `-es` rule only fires after a
  sibilant or `-o`; applying it everywhere turned "cloves" into "clov".
- `NON_SHOPPING_INGREDIENTS` drops water — a "Water: 300g" line makes the rest
  of the list look untrustworthy.

When adding a keyword, prefer the most specific phrase; longest-match will do
the right thing without touching the ordering.
