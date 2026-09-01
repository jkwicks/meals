# PROMPT-5 — Export generated recipes for Cronometer import

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). Part 0
is a five-minute manual test that decides whether the rest is worth building.

Cold session. Read CLAUDE.md's **"Shopping lists"** (the ingredient
normalisation this reuses) and `design-00` **F5** (why a second set of macro
figures is a cross-check, never a second answer).

## The requirement

Generated recipes should be importable into Cronometer, so a planned meal can
be logged to the diary without re-entering every ingredient by hand.

## Route: schema.org/Recipe JSON-LD, imported by URL

**This document originally recommended the opposite and was wrong.** It treated
Cronometer's URL importer as risky because it "re-matches ingredient names
against its own database" — but that is not a cost, **it is the feature**. The
importer resolves an ingredient *string* itself, through text parsing and fuzzy
matching against its food database. So the exporter's whole job is to emit good
strings, which this app is unusually well placed to do.

The earlier recommendation — Cronometer's own JSON import — has the opposite
risk profile and it is the worse one: its format is **undocumented**, it is
built for round-tripping Cronometer's *own* exports, and if it references
internal food IDs then reconstructing them from outside needs a resolution step
with no public API behind it. **Documented format with a known resolver beats
undocumented format with an unknown one.** It stays as the fallback (§4).

## Why this app is a better source than a recipe blog

Two things it has that a food website usually does not, and they matter
directly to a fuzzy matcher:

- **Every quantity is already in grams.** A CLAUDE.md invariant: *"All
  ingredient quantities are in grams. No cups, oz, lbs, or imperial units
  anywhere."* A blog says "2 tbsp olive oil" and the importer has to guess a
  density; this app says `27 g`. **Mass is unambiguous**, which removes the
  single largest source of error in recipe import.
- **Ingredient names are already normalised for exactly this.**
  `shopping.display_name` strips `PREP_QUALIFIERS` and keeps state in
  parentheses — it turns `"onion, grated"` into `"onion"` and
  `"fresh parsley, chopped"` into `"fresh parsley"`. **That is precisely the
  string a food-database matcher wants**: the prep verb is noise to it, and the
  state ("raw", "cooked", "dry") is not.

  So the exporter emits `f"{quantity_g} g {display_name(name)}"` and reuses the
  module that already exists. **Do not emit the raw model name** — and do not
  write a second cleaner, or the shopping list and the export will drift about
  what an ingredient is called, which is the exact silent divergence CLAUDE.md
  records between `_matches` and the `/api/recipes` filter.

## Part 0 — validate before building. Five minutes, no code.

Do this first and stop if it fails.

1. Take one generated recipe. Hand-write a minimal HTML page with a
   `schema.org/Recipe` JSON-LD block — `name`, `recipeYield`, and
   `recipeIngredient` as an array of `"<grams> g <display_name>"` strings.
2. Publish it as a **public gist** and take the raw URL. A gist needs no
   hosting decision, and this is a throwaway.
3. Import that URL into Cronometer.

Report: **did it import, and how many ingredients resolved correctly?** Name
the ones that did not. That failure list is worth more than any amount of
design — it says whether the normalisation above is sufficient or whether some
ingredient phrasings need help.

**If it fails outright**, note whether the account has Gold (the URL importer
is a Gold feature) before concluding the route is wrong.

## Part 1 — the exporter

`src/export_menu.py` already produces Markdown and PDF from a `WeekPlan` and
shares a `_slot_entry` walk between them. **This is a third format in that
module**, not a new one — reuse the walk, or the three exports drift about
which slots they include.

- **Write to `data/`**, never to a tracked directory. `data/` is *written by
  the app, never hand-edited* and is gitignored wholesale; a tracked
  `recipes/` of generated files is the pattern CLAUDE.md warns about with the
  four generated bundles — *"they are generated, never edited."*
- **One file per cook event**, named from the recipe.
- **Export the recipe as cooked.** `CookEvent.recipe` is already scaled to its
  full batch by `build_cook_event` and `servings` says how many portions that
  is, so `recipeYield` is the batch. Cronometer divides by yield the same way
  `per_serving_macros` does; exporting a single serving would misreport a bulk
  cook.
- **A recipe that fails to export must not fail the week's export** — same
  policy as "a failed meal must not fail the week".

## Part 2 — hosting, decided by Part 0's result

Only if Part 0 succeeds. Three options, cheapest first — and **this is a
workflow decision, not an architecture one**:

| | |
|---|---|
| **A gist per recipe, pasted by hand** | Zero setup. Fine at a handful of recipes a week, which is the actual volume |
| **GitHub Pages from a public repo** | Simple and free; puts meal plans on the open web |
| **A route on the app's own server** | `ui_app.py` already *is* a FastAPI app and a `/recipes/{id}` page is trivial — but it is localhost-only, so it needs a tunnel. Same reachability problem as Hevy webhooks, and the same reason not to start here |

**Do not build hosting as part of this change.** Emit the files; let the
workflow settle before automating it.

## Part 3 — the finding this makes available

**Cronometer computes its own macros for an imported recipe**, from a real
nutrition database rather than the model's per-ingredient estimates. So every
recipe exported and then logged is a **free calibration point** on how accurate
this app's generated macros actually are.

Nothing today checks that. `design-00` F5 records `source_nutrition` as a
cross-check for *imported* recipes and there is no equivalent for *generated*
ones — this is that equivalent, arriving through the diary.

**Report the comparison; do not act on it here.** Systematically high or low
figures are a finding about the generation model, and CLAUDE.md's standing
advice is *"change the model, not the trim limits"*.

## 4. Fallback, if the URL route is unavailable

If the account has no Gold, the fallback is Cronometer's **Create New Recipe →
gear icon → Import from JSON file**. Get the format by exporting one
hand-made Cronometer recipe and reading it — it is undocumented, so the file
*is* the spec. The question that decides its cost: **do ingredients reference
Cronometer food IDs, or carry their own nutrient values?**

## Do not

- **Write to Cronometer through the reverse-engineered protocol.** This project
  already depends on `cronometer-mcp` *for reading*; a write path is a much
  larger surface against an undocumented protocol, and the failure mode is a
  corrupted food diary — the one dataset here with no backup and no way to
  regenerate it.
- Emit raw model ingredient names, or write a second name cleaner.
- Add a tracked `recipes/` directory.
- Let Cronometer's macro figures replace the app's (F5).
