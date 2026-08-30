# gllm-suggest.md — Suggested Improvements

Every improvement raised across the end-to-end UI/product reviews (2026-08-30).
Grouped by theme, each item notes effort, impact, and where it lives in the code.
Nothing here has been implemented.

---

## 1. Brand Identity

- [ ] **Add a logo/wordmark.** No app name, mark, or favicon appears anywhere on
  screen — the page opens straight into a date pill and the grid. Place a small
  wordmark at the top of the rail (above the Plan tab); the rail is the app's
  spine and currently starts bare. *Effort: small · Impact: high*
- [ ] **Favicon** via `ui.add_head_html` with an inline SVG — the browser tab is
  the first thing anyone sees. *Effort: tiny · Impact: medium*
- [ ] **Consider a product-y name.** "AI Weekly Meal Planner" is a description;
  professional products have a name. *Effort: n/a (decision)*
- [ ] **One brand accent colour**, deliberately named as a token, used only for
  the primary CTA (Generate) and logo. Quasar's default `primary` currently does
  this invisibly. *Effort: small · Impact: medium*

## 2. Typography

- [ ] **Declare a deliberate font stack.** Nothing sets `font-family` — the app
  renders in default Roboto plus the OS default mono for ~60 `font-mono` data
  figures (calories, macros, times). Add one `add_head_html` block: Inter (or
  declared Roboto) for UI + JetBrains Mono / Geist Mono / IBM Plex Mono for
  figures, with system fallbacks (no CDN dependency). *Effort: small · Impact: high — biggest single visual upgrade*
- [ ] **Tabular numerals** (`font-feature-settings: "tnum"`) so columns of
  figures align — a detail every financial-grade product has. *Effort: tiny · Impact: medium*
- [ ] **Raise `TEXT_MICRO` 10px → 11px** (`ui_theme.py`, one line + skill table
  update). Below comfortable readability at the current contrast. *Effort: tiny · Impact: high*
- [ ] **Line-height tokens** (`leading-snug` / `leading-relaxed`) per scale step —
  multi-line captions in Insights/Settings prose are cramped. *Effort: small · Impact: low-medium*
- [ ] **Page-level hierarchy anchor.** The scale tops out at `TEXT_DISPLAY`
  (18px) — nothing reads as "the title". Preferred: promote the week date pill to
  `TEXT_DISPLAY` weight so *it* is the anchor (no fifth size). Alternative:
  one `TEXT_PAGE` (20–22px) used in exactly one place (header week banner).
  Note: the ui-work skill says "resist adding a fifth size" — option (a) is
  safer. *Effort: tiny · Impact: low*

## 3. Colour & Contrast

- [ ] **Contrast floor for muted text.** `text-slate-500`/`600` captions
  (`·` separators, Insights footer, muted labels) on `bg-slate-900/950` sit at
  or below WCAG AA for small text. Rule: never darker than `text-slate-500` at
  `TEXT_BODY`, `text-slate-400` at `TEXT_MICRO`. ~15 call sites.
  *Effort: small · Impact: medium*
- [ ] **Brighten the cook-card fill** from `emerald-400/[0.07]` to `/10`–`/15`
  — the "this costs you an evening" card is the only one meant to glow, and its
  fill is nearly invisible. *Effort: tiny · Impact: medium*
- [ ] **Replace the four emoji with Material icons** — `ui_telemetry.week_banner`
  (`📅`→`event`, `🌱`→`eco`) and `PREP_BADGE_STYLES` (`⚡`→`bolt`,
  `❄️`→`ac_unit`). Emoji render per-OS, don't inherit text colour, and violate
  the app's own glyph convention. *Effort: tiny · Impact: medium*
- [ ] **Restyle toasts** to match the app tokens (radius, typography) — stock
  Quasar notifications are the one off-brand component. *Effort: small · Impact: low-medium*
- [ ] **Dark-only is fine; document it.** `ui.dark_mode(True)` is hardcoded and
  the palette is dark-slate-based. A light theme is a large job (every
  `slate-800/900` literal remapped) — make dark-only an explicit decision in
  the ui-work skill rather than an accident. *Effort: docs only · Impact: clarity*

## 4. Layout, Spacing & Depth

- [ ] **Elevation system** — the whole app is flat `slate-900/950` surfaces with
  1px borders; at this density everything carries equal visual weight so
  nothing reads as important. Three surfaces, documented in `ui_theme.py`:
  page `slate-950`, panels/rail/dialogs `slate-900`, cards `slate-900` +
  `shadow-sm` + brighter border (`slate-700/60`). *Effort: 1–2 days · Impact: high*
- [ ] **Card interior breathing room** — bump card interiors from
  `SPACE_TIGHT`→`SPACE_BASE` padding; grid stays dense, interiors stop being
  cramped. *Effort: small · Impact: medium*
- [ ] **More separation in the header** between the stat cluster and telemetry
  bars; group the reporting strip visually. *Effort: small · Impact: medium*
- [ ] **Finish the legacy token sweep** — `ui_cards.py:275` uses literal `p-6`
  (24px) where `SPACE_PAGE` (16px) is the token; ~40 `mt-*`/`mb-*` margin
  literals across 9 files; stray `rounded-md`/`rounded-xl`. All documented as
  phase-2 leftovers in the skill; completing the sweep removes subtle
  cross-dialog inconsistency. *Effort: medium · Impact: low-medium*

## 5. Motion & Feedback

- [ ] **Generation progress theatre.** The flagship action (a 20-minute
  multi-call run) deserves staged per-meal-type progress ("Breakfast ✓ ·
  Dinner ⋯ · Lunch · Snack") with checkmarks appearing live. The worker-thread
  dispatch already keeps the loop free — the UI just needs to be more
  theatrical. *Effort: 2–3 days · Impact: high*
- [ ] **Staggered card entrance** on first load — 28 cards fade in over ~200ms
  via per-index `transition-delay`, pure CSS, one-time. *Effort: small · Impact: medium*
- [ ] **Cross-fade refreshes** — panels should swap with a `duration-150`
  opacity transition rather than blinking. *Effort: small · Impact: medium*
- [ ] **Dialog motion** — recipe detail (most-used modal) deserves a
  scale-0.98→1 / `duration-200` entrance. *Effort: small · Impact: low-medium*
- [ ] **Skeleton/shopping-drawer loading state** — panel refreshes during plan
  loads blink empty. *Effort: small · Impact: low*

## 6. Functionality & UX

- [ ] **Confirm dialog on "Discard pending changes"** (`ui_staged_bar.on_discard`)
  — runs immediately and discards grid edits *and* pending inputs with no
  confirmation. The #1 "hobby app" tell. *Effort: small · Impact: medium*
- [ ] **Keyboard accessibility on clickable divs** — catalog column headers and
  table rows use `div.on("click")` with no `tabindex`/`role`/Enter-Space
  handling. All real buttons are fine; these are the exception.
  *Effort: small · Impact: medium*
- [ ] **Empty-state hero for the un-generated week.** A grid of grey SKIP cards
  is the first thing a new user sees. Centered icon, one sentence of value,
  primary CTA "Generate your first week →". `state.week_plan is None` already
  gates the shopping list the same way. *Effort: small · Impact: high*
- [ ] **Hide the Insights tab** until `MIN_TREND_SPAN_DAYS` of data exists —
  a "charts still to come" tab is the worst-looking surface in the app; the
  empty state already tells the story in Settings' TDEE panel.
  *Effort: tiny · Impact: medium*
- [ ] **Customer-facing labels in Settings** — internal vocabulary (sync
  checkpoints, `readiness_log`, model IDs) reads like a debug panel.
  *Effort: small · Impact: medium*
- [ ] **Tooltip consistency pass** — placement/delay uniform across the ~29
  tooltip sites (many already `max-w-xs` — good). *Effort: small · Impact: low*
- [ ] **Mobile story** — the grid is deliberately desktop; mobile is delegated
  to the exported HTML page (a good pattern). Document it as a feature; expect
  pushback if this ever ships to customers. *Effort: docs · Impact: clarity*

## 7. Architecture Note (decided against, for the record)

**Moving to React was evaluated and declined for now.** A React stack
(shadcn/Radix, Framer Motion, TanStack, Vitest) raises the polish ceiling and
offers a real UI-testing story, but: (a) none of the current look/feel limits
are NiceGUI limits — the app already ships Tailwind, custom CSS, and JS
handlers, so every item above is implementable in the current stack; (b) it
would mean rewriting ~10k lines of UI and building a write-path API (`api.py`
is deliberately read-only). Revisit only on real walls: native mobile, PWA,
rich charts, or hiring frontend developers. Cheap seam kept open:
`ui_state.py` (testable logic) + `api.py` mean a future React UI could sit on
the unchanged Python core.

---

## Implementation phases (as proposed)

| Phase | Contents | Effort | Impact |
|---|---|---|---|
| 1. Identity & type | Font stack + tabular numerals, favicon, wordmark, `TEXT_MICRO`→11px, contrast floor, emoji→icons | 1–2 days | Transformative |
| 2. Depth & spacing | Elevation system, cook-card fill, card padding, header grouping, legacy token debt | 1–2 days | High |
| 3. Motion & feedback | Generation progress, staggered entrance, dialog transitions, toasts, skeletons | 2–3 days | High |
| 4. Moments | Empty-state hero, hide Insights, Settings labels, discard confirm, keyboard access | 1–2 days | Medium-high |
| 5. Craft audit | Remaining token sweep, line-height, microcopy | ongoing | Long-term |

All changes stay within the existing design system; any token change
(`TEXT_MICRO`, elevation, accent) must be mirrored into
`.claude/skills/ui-work/SKILL.md` so the contract stays canonical.
