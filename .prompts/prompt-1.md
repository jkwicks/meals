# Phase 1 — typography and token pass

Read `ui-redesign.md` (project root) for the rationale, and
`.claude/rules/ui.md` for the canonical scale. Do **only** phase 1.

## What to do

Introduce named presentation tokens in `src/ui_theme.py` and migrate every
`src/ui_*.py` call site onto them. This is a mechanical pass: no layout moves,
no element is added or removed, no behaviour changes.

### 1. Add the tokens to `ui_theme.py`

Four type sizes, five spacing steps, three radii — exactly the values in
`.claude/rules/ui.md`. Plain module-level string constants, so a call site
reads `.classes(f"{TEXT_MICRO} text-slate-500")`.

Each constant gets a comment saying what it is *for*, in this file's existing
voice — `ui_theme.py`'s current constants (`STATUS_STYLES`,
`PREP_COLUMN_ACCENT`, `TRAINING_TYPE_ICONS`) are the model: they explain the
decision, not the syntax.

### 2. Migrate every call site

Map old to new deterministically:

| current | becomes |
|---|---|
| `text-[8px]`, `text-[9px]`, `text-[10px]` | `TEXT_MICRO` |
| `text-[11px]`, `text-[12px]`, `text-xs` | `TEXT_BODY` |
| `text-[13px]`, `text-sm` | `TEXT_HEAD` |
| `text-base` | `TEXT_DISPLAY` |
| `gap-1.5`, `px-1.5`, `p-1.5` | the nearest step in the scale |
| `mt-*`, `mb-*` between siblings | delete; use the parent's `gap` |
| `rounded-md`, `rounded-xl` | `rounded` or `rounded-lg`, whichever the element is |

Files in scope: every `src/ui_*.py`. Note `props()` strings carry sizes too
(e.g. `header-class='text-xs px-0'`) — those must stay **quoted** inside the
prop or Quasar silently drops them; see `.claude/rules/ui.md`.

### 3. Record the colour collisions

`.claude/rules/ui.md` documents that amber currently carries five meanings and
violet two. Add a comment in `ui_theme.py` at the relevant constants naming
the collision. **Do not resolve them** — that is a phase 3 decision, made when
the surfaces using them are rebuilt. Recording it is the whole task here.

## Scope fence

- Touch `src/ui_*.py` only.
- Do **not** touch `src/ui_state.py`'s logic — only its class strings, if it
  has any.
- Do **not** touch `planner.py`, `week.py`, `repository.py`, or anything in
  `src/integrations/`.
- Do **not** move, add or remove any element. If a layout looks wrong, that is
  phase 2's job — note it, don't fix it.

## Acceptance

Run these and confirm each:

1. Size literals survive only in `ui_theme.py`'s constant definitions:
   ```
   grep -ohE "text-\[[0-9]+px\]|text-(xs|sm|base|lg|xl)" src/ui_theme.py | sort -u   # exactly 4
   grep -lE "text-\[[0-9]+px\]|text-(xs|sm|base|lg|xl)" src/ui_*.py                   # only ui_theme.py
   ```
2. `rounded-md` and `rounded-xl` appear nowhere in `src/`.
3. `python -m unittest discover -s tests` passes.
4. The app starts and serves: `./scripts/server.sh start`, then
   `./scripts/server.sh status`. Stop it again when done.

### The one visual risk, and how to handle it

Bumping 35 uses of 9px and 27 of 11px up a step makes text larger inside
`grid-cols-8` columns that are already tight. **If a card's micro row
overflows or wraps, the fix is to show less in that row — not to reintroduce
a fifth size.** A scale with an exception is not a scale. Report any card you
had to trim.

## Finish by

Updating CLAUDE.md's "NiceGUI front end" section with a short subsection
naming the scale and why each step exists — in that document's established
voice, explaining the decision rather than listing the values. State plainly
that nine sizes inside a 6-pixel band were noise rather than hierarchy, since
that is the reasoning a future reader will otherwise undo.

Report at the end: which files changed, how many call sites moved, any card
you trimmed, and anything you found that belongs in a later phase.
