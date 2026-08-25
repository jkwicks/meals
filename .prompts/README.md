# .prompts/

Staged work for `scripts/claude-queue.sh`, plus briefings for the phases that
must not go through it.

## The naming convention is a safety rail

`claude-queue.sh` runs `find .prompts -type f -name "prompt-*.md"` and
executes each match, in numeric order, as `claude -p ... --dangerously-skip-permissions`.
The glob is what decides. So:

| name | meaning |
|---|---|
| `prompt-N.md` | **queue-safe.** Mechanical, objectively checkable, safe to run unattended with permissions skipped. |
| anything else | a briefing for an **interactive** session. The queue cannot see it. |

`find` is recursive, so a subdirectory does not hide a file — only the name
does. Do not rename a briefing to `prompt-*.md` to "run it quickly"; that is
the one action this convention exists to prevent.

## Two things to know before running the queue

**Each file is a cold session.** Separate `claude -p` invocation, no memory of
the previous one. Every prompt must restate its own scope — never "continue
from before". CLAUDE.md auto-loads; so does `.claude/rules/ui.md` once a
`ui_*.py` file is touched.

**The queue runs every match back to back with no review gap.** A prompt that
fails loudly halts it, but one that succeeds *badly* exits 0 and the next
prompt builds on it. If you want a checkpoint between phases, keep only the
one you're running named `prompt-*.md` and rename the rest until you've
reviewed.

## Current contents

| file | phase | mode |
|---|---|---|
| `prompt-1.md` | 1 — typography and token pass | queue |
| `prompt-2.md` | 2a — overflow container, overlaying drawer | queue |
| `phase-2b-grid-rows.md` | 2b — real grid rows, meal-type gutter | interactive |
| `phase-3-rail.md` | 3 — the rail, five destinations, staged-changes bar | interactive, plan mode |
| `phase-4-inspector.md` | 4 — day inspector, target curve, rejection capture | interactive, plan mode |
| `phase-5-api.md` | 5 — API boundary extraction | interactive, plan mode |

`ui-redesign.md` in the project root is the plan these implement. It is the
source of truth; these files are how it gets executed.

## Working rules for every phase

- Branch first. `git checkout -b ui/phase-N-<name>`, one phase per branch, one
  PR per branch — the same shape the release flow already uses.
- Every phase finishes by updating CLAUDE.md. A cold session is only as
  competent as that file is true, and the next phase is always a cold session.
- A phase that discovers the plan is wrong should **say so and stop**, not
  improvise a different phase. `ui-redesign.md`'s phase 2 was split into 2a/2b
  exactly this way, after the canvas turned out to have no shared grid rows to
  hang a gutter on.
