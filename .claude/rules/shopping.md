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

## Canonical staples — the variants normalisation can't reach

`NAME_ALIASES` rewrites one whole key to another, so it only fixes variants
that already normalise to a single word each. `CANONICAL_INGREDIENTS` handles
the ones that don't: applied by `resolve_ingredient()` before the key *and*
the display name are built, so the two can never disagree about which purchase
a line is.

- Each rule is `(words that must all appear, words that must not, the
  canonical name)`, matched against `key_words()` — the same singularized,
  prep-stripped words the combining key is built from. Longest match wins,
  ties to list order, same as `categorize_department()`; that ordering is what
  keeps "Greek yoghurt" from collapsing into plain "Yoghurt".
- Observed duplicates it fixes: "Sardines (canned)" / "sardines in water
  (tinned)" / "tinned sardines"; "Low fat cottage cheese" / "cottage cheese";
  "Extra virgin olive oil" / "Olive oil"; "Rolled oats" / "Porridge oats";
  "Ground flaxseed" / "Flax seeds"; "yogurt" / "yoghurt".
- **Keep it narrow.** An entry asserts two names are the same purchase, which
  is exactly the merge `STATE_QUALIFIERS` prevents when they aren't. Two
  guards enforce that: exclusion words ("mustard seeds" is not mustard, "oat
  milk" is not oats), and a canonical name carrying a state of its own claims
  only names whose state is absent or in the same `EQUIVALENT_STATES` group,
  so "frozen sardines" stays on its own line while "tinned" merges into
  "canned". A canonical name with no state of its own preserves the original's
  ("Oats, cooked" is still separate from dry).
- The model is also asked not to create these in the first place, by
  `planner.PANTRY_CONSOLIDATION_RULE`. This is the backstop, not the plan.
