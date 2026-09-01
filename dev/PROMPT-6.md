# PROMPT-6 — Location from the calendar, without handing over the account

**Not queue-safe** (in `dev/`; the queue globs `.prompts/prompt-*.md`). Part 0
is account setup only the user can do, and its result decides the rest.

Cold session. Read CLAUDE.md's **"Some slots are decided before the model is
called"** (what `base_schedule` already drives) and **"Biometric sync"** (the
integration shape this copies). Then `design-04` §7 — prep-day placement is
this feature's most demanding consumer.

## The requirement

`schedule.json`'s `base_schedule` is hand-maintained: a standing map of weekday
→ location, edited by a human when life changes. **Four things already read
it**, and all four are wrong whenever the file and the calendar disagree:

| Reads it | Gets wrong |
|---|---|
| `week.apply_location_modes` | an Office lunch that should have been cooked, or vice versa |
| `day_allows_long_cook` | a braise scheduled for a day you are out |
| `location_rules.<loc>.restrictions` | "must travel in a container" on a meal eaten at home |
| **prep-day placement** (`design-04` §7) | **the whole prep session on a day you are in Ballarat** |

That last one is why this moved up: `design-04` derives prep day from
`base_schedule`, so a stale file silently plans a two-hour cooking session for
a day you are away.

## Part 0 — setup, and the security question answered

Asked directly: *"Is there a push method I can use from Google Calendar to
populate a lower security calendar? If it pushes, then no issue with the Google
account being hacked. The target calendar object would be throwaway."*

**The goal is right and a pull achieves it better than a push.**

**Use a dedicated calendar and its "Secret address in iCal format".** In Google
Calendar, create a new calendar (call it *Location*), put only location-relevant
entries in it, then Settings → that calendar → **Secret address in iCal
format**.

Why this satisfies the stated threat model:

| | |
|---|---|
| **Scoped to one calendar** | not the account. It cannot see, or reach, anything else |
| **Read-only and one-way** | the URL cannot write |
| **Revocable** | Reset the address and the old URL dies |
| **No credential at all** | no OAuth, no password, no refresh token. There is nothing to steal that grants access to anything but this one calendar |

If the URL leaks, someone learns you are in Ballarat on Sunday. **That is the
"throwaway target, low impact" property asked for** — reached by holding no
credential rather than by holding a weaker one.

**And push is worse here, not better.** An Apps Script pushing to a webhook
runs with *your account's* authority — more exposure, not less — and needs a
publicly reachable HTTPS endpoint, which makes CHANGE-QUEUE.md's "No auth on
`/api`" item **required** rather than "only if exposed". Google's own Calendar
API push channels additionally need a verified domain.

**Setup:** `CALENDAR_ICS_URL` in **`.env`**, beside `GARMIN_*`,
`CRONOMETER_*` and `HEVY_API_KEY`. Credentials and secret URLs live there;
`config/integrations.json` holds tuning. Treat the URL as a secret in logs and
error messages — it *is* the credential.

Report: does the URL fetch, and what do the events actually look like? Paste
two or three **redacted** `VEVENT` blocks — `SUMMARY`, `LOCATION`, `DTSTART`,
and whether they are all-day or timed. Everything in Part 2 depends on that
shape.

**Also report, because Part 2's parser choice turns on it** (added 2026-09-01
under review — the probe originally asked only about event *shape*, and the
answer that decides the dependency is about the *file*):

- Does the feed contain any `RRULE`, `RDATE` or `EXDATE`? Recurrence is the
  single line between "a hand parser is fine" and "a hand parser is a partial
  reimplementation of RFC 5545".
- Does it contain `STATUS:CANCELLED` events? They are present in the file and
  must not become locations.
- Are lines folded (continuations beginning with a space or tab)? Any
  real-world feed of any size has them.
- What does `DTSTART` carry — a `TZID=` parameter, a `Z` suffix, or a bare
  `VALUE=DATE`? All three appear in practice and mean different things.
- Roughly how many `VEVENT`s, and does the URL redirect before serving?

### The parser decision, which the probe's answer settles

**There is no ICS library in `requirements.txt` today**, so Part 2 either adds
one deliberately or hand-parses. Both are defensible and the wrong way to
choose is by accident:

- **`RRULE` present → add `icalendar` (plus `python-dateutil` for recurrence
  expansion).** Expanding a recurrence rule correctly is genuinely hard —
  `BYSETPOS`, `UNTIL` against a timezone, `EXDATE` — and a partial
  implementation is wrong in ways nobody notices until a fortnightly meeting
  silently claims every week.
- **No `RRULE` → a bounded hand parser is acceptable**, provided it handles
  line unfolding, `\,` / `\;` / `\n` escaping, and the three `DTSTART`
  forms. State that decision in the module docstring with the probe's evidence
  beside it, so a later feed that *does* recur is a known trigger to revisit
  rather than a silent misread.

Four semantics to settle whichever route is taken, because each has one right
answer and one plausible wrong one:

| | Rule |
|---|---|
| `DTEND` | **Exclusive.** An all-day event `DTSTART;VALUE=DATE:20260907` / `DTEND;VALUE=DATE:20260908` is **one** day, not two. Off by one here silently claims an extra day |
| multi-day all-day | Expands to every date in the half-open range, each an independent override |
| `STATUS:CANCELLED` | Skipped entirely — never a location |
| timezone | Resolve to the **project's** local date, the same rule `startTimeLocal`-not-`startTimeGMT` already establishes for Garmin activities. A date is what `base_schedule` is keyed by |

**Resolve the calendar once into `PlannerState` and use that one snapshot for
both the preview and the run.** Fetching separately for each is the *"a number
the UI displays and a number a run plans against must come from one call"*
rule, arrived at from the network side — and here the two calls can genuinely
disagree, because a calendar changes between them.

## Part 1 — a standing week with dated exceptions, not a replacement

**The central decision, and it has two precedents in this codebase.**

`base_schedule` is a *standing* week: every Monday is Office. A calendar
supplies *specific dates*. Replacing one with the other would be wrong — most
weeks **are** the standing pattern, and the calendar names the exceptions.

So: **`base_schedule` stays the standing week, and the calendar produces dated
overrides for the days being planned.** That is the same standing-versus-dated
split the app already makes twice — `target_modes` (standing) against
`target_locks` (per-run), and presets (standing) against blocks (dated). This
is the third instance and it should look like the other two.

Two consequences:

- **The sync window is one week, not a history.** Only the days being planned
  matter, so this fetches a 7–14 day span and stores nothing long-term. It is
  the cheapest of the four integrations by a wide margin.
- **An override never edits `schedule.json`.** It is an input to the run, like
  `target_locks` — not one of the two things that write to `config/`.

## Part 2 — the mapping, and the rule that keeps it honest

A calendar gives events; `location_rules` needs a location *name*. The mapping
belongs in `config/integrations.json` under a `calendar` key — tuning, not
credentials — as an explicit table from a match to a location name.

**An event that matches nothing changes nothing.** This is the load-bearing
rule. Most calendar entries are meetings, not location changes, and a system
that guessed would move a prep session off a day because of a dentist
appointment. `GARMIN_SESSION_TYPES` sets exactly this precedent — no catch-all,
and CLAUDE.md's reasoning applies verbatim: *"an unmapped modality guessed at
would be worse than absent — a wrong answer that looks like a right one."*

Three questions Part 0's output answers, to be decided from real events rather
than in advance:

- **Match on `SUMMARY`, `LOCATION`, or both?** A dedicated calendar makes the
  title reliable, which argues for `SUMMARY` and a simple keyword table.
- **All-day versus timed.** An all-day "Ballarat" plainly claims the day. A
  two-hour evening event probably does not claim lunch. **Suggest: only all-day
  events set a location** unless the events say otherwise — under-claiming is
  the safe direction, since it falls back to the standing week.
- **Two events on one day.** Refuse and report, naming both, rather than
  picking. Same policy as overlapping blocks (`design-01` §4.3).

## Part 3 — say what it changed

**A derived location must be visible, or this silently rewrites the week.**
Location already decides whether lunch is cooked, whether a braise is allowed,
and now where the prep session lands — so an override arriving from a calendar
has to be as legible as one typed by hand.

Two surfaces, both existing:

- **Settings' location read view** already prints the standing week. It gains
  the overrides, with which event produced each.
- **The week briefing** (`design-04` §7.3) is the natural home for the
  consequence rather than the cause: *"No prep day — you are in Ballarat
  Saturday and Sunday."* That is the briefing's best worked example, and it is
  unreachable without this feature.

## Acceptance

- **No `CALENDAR_ICS_URL` → byte-identical.** `base_schedule` behaves exactly
  as today. Same standing rule as every other optional integration.
- An unmatched event changes nothing.
- An all-day mapped event overrides that date's location; the standing week
  covers every other day.
- Two mapped events on one date **fail with both named**.
- An override never reaches `config/`.
- A failed calendar fetch does not fail a generation — it falls back to the
  standing week and says so. *A Garmin outage must not cost a Cronometer sync*,
  same policy.
- The ICS URL never appears in a log line or an error message.

- **`DTEND` is exclusive.** A one-day all-day event with
  `DTSTART:20260907`/`DTEND:20260908` claims **7 September only**. Assert it —
  this is the single likeliest off-by-one in the module.
- **A `STATUS:CANCELLED` event claims nothing**, even when it matches.
- **A folded line parses identically to its unfolded form.**
- If a hand parser was chosen, the module docstring names the probe evidence
  that justified it (no `RRULE` in the feed).

Tests beside `test_sync_service.py`, same shape: **no network**, one seam, and
the fake speaks real `VEVENT` text — including an all-day entry, a timed one,
an unmatched one, a cancelled one and a folded line, since those distinctions
*are* the module.

## Do not

- Ask for OAuth, a password, or full-account calendar access.
- Push. Part 0 explains why it is more exposure, not less.
- Let the calendar rewrite `schedule.json`.
- Add a catch-all to the mapping table.
- Infer a location from an event's own `LOCATION` free text without a table
  entry — "Ballarat" in a field is not a location this app has a rule for.
