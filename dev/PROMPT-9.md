# PROMPT-9 — The preset editor, and the validator it cannot ship without

**Shipped in v0.44.0.** It imported `presets.resolve_config` rather than
writing a second validator, which is the reason it was gated behind `PROMPT-8`.
Kept as the record of what was asked for and why; it is not work to pick up.
`dev/README.md`'s order of delivery is the authority on what is still
outstanding — this banner states a verdict, never a rank, because a verdict
cannot go stale.

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). It
touches `ui_*.py` and its acceptance is partly visual.

**Priority: immediately after PROMPT-8.** `design-03` §8 calls steps 1 and 2 *a
stated requirement, not a convenience* — the editor is what makes presets usable
at all, which is why it sits ahead of every block mechanism.

**Depends on PROMPT-8** (the container and layer must exist) **and PROMPT-7**
(which produces the field list this exposes).

Cold session. **Load the `ui-work` skill before editing any `ui_*.py`** — it is
the front-end contract and is not in CLAUDE.md. Read `design-03` **§2** (the six
widget shapes), **§4.1** (absent-meaning), **§4.2** (validate-before-save) and
**§4.3** (focus theft). Then `design-01` **§9.2**.

## The requirement

Asked directly: *"I would also need an interface to define different
profiles… they should be defined somewhere and be able to be edited via
interface."*

## Why this is mostly a copy, which is the useful fact

`design-03` §1: **UI cost tracks how many distinct widget shapes the schema
needs, not how expressive it is.** The front end has six, and `ui_review.py`'s
`training_editor` is a complete, shipped implementation of the only interesting
one — a list of records you add to, edit and delete:

- iterate `enumerate(state.<list>)`, one bordered `SURFACE_INSET` card per row;
- selects, numbers and text inside the row;
- a `delete` icon button with an index-bound closure;
- one field-handler factory for every field;
- **add and remove refresh; a field edit does not.**

Every preset dimension is that pattern, or a bare select/number/switch, or
`ui.select(multiple=True)` — present in nicegui 3.16 and unused in this app so
far, so new *here* but not new code.

## What to do

### 1. The validator — **already built; import it, do not write a second one**

**Amended 2026-09-01.** This section originally specified the validator as
this prompt's "one genuinely new piece of architecture". It is now `PROMPT-8`
§2a's job, and the reason is the failure that split would produce: a resolver
that decides validity at load and a validator that decides it at save are two
interpretations of one word, free to disagree about a file one accepts and the
other refuses. There is one function and it lands with the layer it validates.

So the requirement here is narrower and stricter:

- **Import `PROMPT-8`'s pure resolver.** It takes the base config and one
  preset and returns either the resolved dict or structured failures. Same
  shape and same reason as `AppConfig.diet_styles_are_known`: only something
  seeing both can check one against the other.
- **The editor calls it on save, renders the failures, and does not write.**
  The loader raises on the same failures. Same check, two presentations.
- Load-time validation stays **unchanged**, for hand-edited files.
- **If a check the editor needs is missing, add it to the shared function** —
  never to the editor. A check that lives only in `ui_*` is a check a
  hand-edited file walks past.
- `ui.select`/`ui.number` take a `validation` argument in 3.16 — use it for
  per-field bounds, as a *convenience over* the shared function, never instead
  of it. Cross-field checks need the function regardless.

Why the editor still cannot ship without it: today only `set_target_mode` and
`accept_training_proposal` write to `config/`, and neither can produce an
invalid file. An editor writes arbitrary structure, and this app's policy is
**fail loudly at load** — which for a UI-authored file means *the next start
raises* with the surface that caused it long gone.

### 2. The editor panel, bounded to the audit's *data* rows

**Not a general config editor over every `CONFIG_FILES` key** — most core keys
(`week_start_day`, `regional`, `meal_types`) are not things a mood varies, and
`design-01` §12 already files the general editor as decided-against. PROMPT-7's
verdicts are the field list. `design-01` §9.2's nine groups are the starting
point and PROMPT-7 may add to or subtract from them.

Every field renders as an **unset** control the user may ignore, because every
field has an absent-meaning that is exactly today's behaviour (§4.1). A field
that must be filled before a preset is valid is a field that forces every preset
to have an opinion about it — the rigidity this arm removes, one key at a time.

### 3. A preview, on a button

**Not live.** Showing the resulting grid as you type would repaint the canvas
per keystroke: both the focus-theft trap and a `"plan"` refresh, the most
expensive topic there is — 28 cards, the telemetry header and the shopping
panel. On demand, the same call `estimate_burn`'s calculator button already
makes, for the identical reason.

### 4. Preserve what the editor does not know

A preset naming an override path the editor does not expose **survives an edit
untouched**. Read-modify-write through `PROMPT-8` §1a's
`load_presets_config`/`save_presets_config`, the way `save_config_keys` already
merges named keys per file so a hand-added key survives the next settings
change. This is what stops the editor's field list becoming a ceiling on what a
preset can say — `presets.json` stays the escape hatch, the same division
`training_schedule` already has.

Leaf paths make this cheaper than the first draft assumed. Under whole-key
replacement, exposing one field of `planning_rules` obliged the editor to
materialise and store the *whole* object, freezing every value it did not
show; a path-valued override touches one leaf and leaves the rest inherited.

## Acceptance

- **Saving a preset the editor cannot fully represent preserves the keys it does
  not expose.** Assert on a preset carrying an unexposed key, edited and saved.
- **An invalid preset is refused at save, with the failure named, and the file
  is not written.** Assert the file is byte-identical after a refused save.
- Load-time validation is unchanged — a hand-edited invalid file still fails at
  load, naming preset and key.
- Creating a preset with every field left unset produces a preset whose
  `overrides` are empty, and a week byte-identical to the **base config**. This
  is §4.1 made testable.
- `default` is editable and deletable like any other row — **because nothing
  depends on it**. The diff baseline is the base config, not this row
  (`PROMPT-8` §1). Assert: delete `default`, and the editor still renders every
  remaining preset's diff.
- **Deleting the preset named by `active` is refused, or clears `active`** —
  pick one and state it, but a dangling `active` must not be writable. The
  loader's contract is that `active` is absent, null, or names a preset that
  exists.
- Editing a field does not repaint the field being edited. Add and remove do
  refresh. (Visual check; `ui-work`'s trap.)
- The preview is on a button and repaints nothing until pressed.
- **No new colour.** A preset is a label; glyph-and-wording is the route
  `sync_freshness` and the adherence marks both took, and amber already means
  five things.

Tests go in `test_ui_state.py` for anything on `PlannerState`, and the validator
gets its own module — it is a pure function, which is exactly the line CLAUDE.md
draws: *if logic worth testing appears in a `ui_*` module, pull it into
`ui_state.py` or a pure helper rather than growing a UI harness.*

## Do not

- Offer `calories` or `protein_g`. Inert while `target_modes` is `auto`
  (`design-03` §5), and flipping that mode is the block's business.
- Build a general config editor.
- Add drag-reordering. No precedent, and preset order carries no meaning.
- Make the grid clickable. `design-03` §3 prices it at an L and §9 files it as
  decided-against.
- Ship the editor without the validator, or write a second one. `PROMPT-8` §2a
  owns it; this prompt imports it. That is the whole point of §1 as amended.
- Treat `default` as the baseline. It is a row.
