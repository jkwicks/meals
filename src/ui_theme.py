"""Presentation constants and pure rendering helpers for `ui_app.py`.

Split out of `ui_app.py` so a widget module (cards, canvas, drawer) can pull
in "what colour is a cook card" without also pulling in `PlannerState` and
the repository. Nothing here reads or writes state — every function takes
its inputs as arguments and returns either a string or renders directly via
`ui.element`/`ui.label`, the same leaf-widget shape `link_line` and
`telemetry_bar` already had in the monolith.
"""

import re
from datetime import datetime
from typing import Optional

from nicegui import ui

from planner import TRAINING_INTENSITY_SPLIT
from week import humanize

# The type scale — four sizes, replacing nine crammed into an 8-to-14-pixel
# band (56 uses of the smallest pixel value alone, 35 of the next) that no
# two of were distinguishable at a glance. That was noise, not hierarchy.
# Phase 1 of `ui-redesign.md`; the canonical statement of the scale is the
# `ui-work` skill, loaded before any `ui_*.py` edit. Weight and
# colour carry the rest of the hierarchy now, not a fifth size — resist
# adding one.
TEXT_MICRO = "text-[10px]"  # data figures, chip/badge labels, link lines, captions
TEXT_BODY = "text-xs"  # the default — labels, inputs, card titles, list rows
TEXT_HEAD = "text-sm"  # section headings, day names, dialog section titles
TEXT_DISPLAY = "text-lg"  # dialog titles, a recipe name in the detail view

# Spacing — five steps, each with a job. Space siblings with the parent's
# `gap`, not a per-element margin: margins collapse and double in ways a gap
# does not, and put the spacing decision on the child rather than on the
# layout that owns it. Write new code this way. The `mt-*`/`mb-*`/`mx-*`
# still scattered through `ui_*.py` is legacy and deliberately untouched by
# this scale — converting a margin to a parent `gap` moves layout, which is
# phase 2's job, not phase 1's.
SPACE_HAIR = "0.5"  # inside a chip or badge; icon to its own label
SPACE_TIGHT = "1"  # between rows inside one card
SPACE_BASE = "2"  # between cards, between form fields
SPACE_SECTION = "3"  # between sections in a panel or dialog
SPACE_PAGE = "4"  # dialog padding, page gutters
# `1.5` was never a step: it is 6px, exactly halfway between SPACE_TIGHT and
# SPACE_BASE, so "nearest" is undefined. Every one of its call sites resolves
# to SPACE_TIGHT — down, not up, because the dense card interiors where `1.5`
# appeared cannot absorb growth from a larger type scale and a wider gap at
# once.

# Radius — three values. The two in-between Tailwind radii this codebase used
# to reach for are retired; every call site that used either now names which
# of these three it actually meant.
RADIUS_CARD = "rounded"  # cards, boxes, inputs
RADIUS_PANEL = "rounded-lg"  # dialogs, panels
RADIUS_PILL = "rounded-full"  # bars, dots, pills

# Phase 2a of `ui-redesign.md`. The header's `WEEK_GRID_COLS` row
# (`ui_telemetry.telemetry`) and the canvas (`ui_cards.canvas`) have to
# scroll in lockstep, but they can't share one physical scroll parent: the
# header is `position: fixed` (a plain Quasar `QHeader` default) so it stays
# visible while the canvas scrolls vertically beneath it, and a fixed
# ancestor is never the same element as the page's own scrolling container.
# `week_grid_scroll()` below wraps each region's call site in its own
# `overflow-x: auto` div carrying this class, and one `scroll` listener —
# attached to both, running `WEEK_GRID_SCROLL_SYNC_JS` — mirrors
# `scrollLeft` between them entirely client-side. Same technique a
# frozen-header spreadsheet UI uses; no round trip per scroll tick, same
# reasoning `chain_css` already gives for staying out of Python on a hover
# effect.
WEEK_GRID_SCROLL_CLASS = "week-grid-scroll"

# How far the header's copy of the grid has to be pushed right to sit over the
# canvas's copy. Phase 2a could assume the two started at the same x — both
# were `px-3` in from the same full-width page — but phase 3 put a vertical
# rail to the left of the destination panels, and only the canvas is inside
# one. Measured before this constant existed: the header's grid started at
# x=12 with 159px day columns while the canvas's started at x=192 with 135px
# ones, which put every day's telemetry above its *neighbour's* meals. The
# inset makes both scroll viewports the same width as well as the same left
# edge, so the columns resolve to identical pixel widths and the scroll sync
# below mirrors like for like.
#
# `RAIL_WIDTH_CLASS` is what makes the arithmetic hold: `ui.tabs()` sizes a
# vertical rail to its widest tab, and the Daily View tab's label is the day
# being browsed ("Daily View · Sun 30 Aug", "Fri 28 Aug"), so an intrinsic
# width would shift the whole canvas sideways as you stepped through the
# week. 168px is what it measured at; pinning it changes nothing on screen
# and stops it moving.
RAIL_WIDTH_PX = 168
# `ui_plan.panel()`'s own `p-{SPACE_SECTION}` inside its tab panel — the one
# padding the header doesn't already have.
PANEL_PAD_PX = 12
RAIL_WIDTH_CLASS = f"w-[{RAIL_WIDTH_PX}px]"
# An explicit width, not `w-auto` plus margins. Quasar's `.flex` puts
# `flex-wrap: wrap` on the header, and a wrapping column flex container sizes
# its line — and therefore every stretched child — to the *widest* item's
# max-content, not to its own content box. The grid's max-content is all nine
# columns at their 110px floor, so a stretched wrapper stayed 1024px wide at a
# 1000px viewport: wider than the page, nothing left to overflow, and the
# canvas below scrolling on its own with the header frozen. A percentage
# resolves against the header's content box instead, which is the box that
# actually matches the destination panel's.
WEEK_GRID_HEADER_INSET_STYLE = (
    f"margin-left: {RAIL_WIDTH_PX + PANEL_PAD_PX}px; "
    f"width: calc(100% - {RAIL_WIDTH_PX + 2 * PANEL_PAD_PX}px)"
)

# `minmax(110px, 1fr)` per day/prep column, not a flat min-width on the
# wrapper — 110px is the day column's own natural width (see CLAUDE.md's
# phase-1 "cards visually overlapping" note). At any normal desktop width the
# `1fr` still splits the available space evenly across those eight columns
# exactly as `grid-cols-8` always did; only a viewport too narrow for eight
# 110px columns triggers the scroll. And because `ui_telemetry`'s two rows and
# `ui_cards.canvas` use this identical template inside wrappers made
# identically wide by `WEEK_GRID_HEADER_INSET_CLASS` above, their columns
# resolve to identical pixel widths, which is what keeps them aligned *while
# scrolled* and not just at rest. (That used to follow from both sitting
# `px-3` in from the same full-width page; the rail broke it, and the inset
# is what restores it.)
#
# The leading `minmax(80px,auto)` track is phase 2b of `ui-redesign.md`'s
# meal-type gutter — real content (BREAKFAST/LUNCH/DINNER/SNACK) only in
# `ui_cards.canvas()`, an empty spacer cell in `ui_telemetry.telemetry()` that
# exists purely so both grids keep nine tracks and stay aligned. 80px is
# comfortably past "BREAKFAST" (the longest meal-type name in the shipped
# config) at the gutter's tracking-wide 10px label; `auto` lets it grow for a
# longer name rather than truncating one, the same way the day columns never
# truncate below 110px.
WEEK_GRID_COLS = "grid-cols-[minmax(80px,auto)_repeat(8,minmax(110px,1fr))]"

# `e.currentTarget` (the listening element itself), not `e.target`: a native
# `scroll` event doesn't bubble, but `currentTarget` is the correct handle
# regardless. The equality check is what stops the mirrored write from
# re-triggering its source — setting `scrollLeft` to the value it already
# holds doesn't fire another `scroll` event in practice, but the check makes
# that explicit rather than relying on it.
WEEK_GRID_SCROLL_SYNC_JS = (
    "(e) => { document.querySelectorAll('." + WEEK_GRID_SCROLL_CLASS + "').forEach(el => {"
    " if (el !== e.currentTarget && el.scrollLeft !== e.currentTarget.scrollLeft)"
    " el.scrollLeft = e.currentTarget.scrollLeft; }); }"
)


def week_grid_scroll(inset: bool = False):
    """One `overflow-x: auto` region around a `WEEK_GRID_COLS` grid.

    Two call sites use this — the header's `telemetry()` row and the Plan
    destination's canvas — because they can't share one physical scroll
    parent (see `WEEK_GRID_SCROLL_CLASS`'s comment above). Both carry the
    same class and the same `WEEK_GRID_SCROLL_SYNC_JS` listener, which is
    what keeps a scroll on either one visually moving the other. Living here
    rather than duplicated at each call site is what stops the two from
    drifting apart.

    `inset=True` is the header's call: it is the one of the two that is *not*
    inside a destination panel, so it has to be pushed past the rail by hand
    to line up with the canvas (`WEEK_GRID_HEADER_INSET_STYLE`). The margin
    sits outside the scroll box and the width is the box itself, so the two
    regions end up the same number of pixels wide — without that, one can
    scroll while the other has nothing to scroll, and mirroring `scrollLeft`
    between them moves one grid out from under the other.
    """
    element = ui.element("div").classes(f"{WEEK_GRID_SCROLL_CLASS} w-full overflow-x-auto")
    if inset:
        element.style(WEEK_GRID_HEADER_INSET_STYLE)
    return element.on("scroll", js_handler=WEEK_GRID_SCROLL_SYNC_JS)


# The two cached weeks the app keeps on disk at once (see
# `repository.LocalJSONRepository._week_plan_path`). "current" is the
# original single-file layout (week_plan.json); "next" is stored alongside it
# as week_plan_next.json. Keys are what's passed to `load_week_plan`/
# `save_week_plan`, values are what the header select shows.
WEEK_SELECTION_LABELS = {"current": "Current Week", "next": "Next Week"}

# A slot's render status is its mode, except that a cook (or the cook a
# leftover points at) whose day failed to generate has no recipe to show. That
# is a fourth visual state, not an error: CLAUDE.md's "a failed meal must not
# fail the week" means the rest of the grid still renders around it.
STATUS_COOK = "cook"
STATUS_LEFTOVER = "leftover"
STATUS_SKIP = "skip"
STATUS_MISSING = "missing"

# Tailwind per status. Each status is a *tint plus a rule*, not just a border
# colour: cook is the only one that carries a lit background, because it is the
# only card that costs you an evening. Leftover is dashed and cooler — nothing
# is bought or cooked for it, so it should read as derived from the card it
# points at rather than as its own event. `glow` is the hover colour (see
# `card_css`), and `icon` is what makes the chip legible at a glance down a
# column of 28.
STATUS_STYLES = {
    STATUS_COOK: {
        "card": "border border-emerald-400/25 border-l-[3px] border-l-emerald-400 bg-emerald-400/[0.07]",
        "badge": "bg-emerald-400/20 text-emerald-200 ring-1 ring-inset ring-emerald-300/30",
        "label": "COOK",
        "icon": "local_fire_department",
        "glow": "#34d399",
    },
    STATUS_LEFTOVER: {
        "card": "border border-dashed border-sky-400/30 border-l-[3px] border-l-sky-400 bg-sky-400/[0.04]",
        "badge": "bg-sky-400/15 text-sky-200 ring-1 ring-inset ring-sky-300/30",
        "label": "LEFTOVER",
        "icon": "restore",
        "glow": "#38bdf8",
    },
    STATUS_SKIP: {
        "card": "border border-dashed border-slate-800 border-l-[3px] border-l-slate-700 bg-slate-900/40",
        "badge": "bg-slate-700/40 text-slate-400 ring-1 ring-inset ring-slate-600/30",
        "label": "SKIP",
        "icon": "remove",
        "glow": "#64748b",
    },
    STATUS_MISSING: {
        "card": "border border-rose-500/30 border-l-[3px] border-l-rose-500 bg-rose-500/[0.07]",
        "badge": "bg-rose-500/20 text-rose-200 ring-1 ring-inset ring-rose-400/30",
        "label": "NOT GENERATED",
        "icon": "error_outline",
        "glow": "#fb7185",
    },
}

# `SlotView.prep_badge` — set on any card (cook or leftover) folded into a
# Sunday prep session (see `planner.is_sunday_prepped`): the batch's own
# anchor slot as well as the leftovers eating it. "fridge" vs. "freezer"
# mirrors the same threshold `storage_note` used to write the cook's own
# storage note, so the badge never disagrees with the text a user would see
# on the recipe.
#
# **Both badges are neutral, and the glyph in the label is the distinction.**
# "fridge" used to be amber and "freezer" cyan; the amber was the fifth
# meaning that colour carried, and the cyan was a whole hue spent on a
# two-member set that ⚡ and ❄️ already tell apart. Retiring both freed
# cyan for `MACRO_TINTS["fiber_g"]`. CHANGE-QUEUE.md's amber/violet item.
PREP_BADGE_STYLES = {
    "fridge": {
        "label": "⚡ Prepped on Sun",
        "classes": "bg-slate-700/40 text-slate-300 ring-1 ring-inset ring-slate-600/30",
    },
    "freezer": {
        "label": "❄️ From Freezer",
        "classes": "bg-slate-700/40 text-slate-300 ring-1 ring-inset ring-slate-600/30",
    },
}

# `ui_state.SyncDay.state` — what one date in the Settings sync strip is, from
# one source's point of view. Here rather than in `ui_state.py` for the same
# reason `STATUS_COOK`..`STATUS_MISSING` are: the view model sets the value and
# a widget module renders it, so the two need one vocabulary, and the module
# with no `PlannerState` dependency is the one both can import.
#
# Three states, and the third is the whole reason `sync_checkpoints` exists
# (see `repository.save_sync_checkpoint`): a date with no row is either a day
# the sync looked at and found nothing — a forgotten weigh-in, an unlogged
# meal, a real answer — or a day nobody has asked about yet. Collapsing them
# into one grey cell would discard the only thing that file records.
SYNC_RECORDED = "recorded"
SYNC_CHECKED = "checked"
SYNC_UNCHECKED = "unchecked"

# Fill and outline carry the distinction, not three hues — the same call
# `TRAINING_TYPE_ICONS` makes just below, and for the same reason: every
# colour in this UI already means something specific, and a third one here
# would collide before it read as a scale. Emerald is "there is data", which
# is what it already means on the "Connected" pill this strip sits under; a
# filled slate cell is an answered day with nothing in it; an outline is a day
# nobody has asked about.
#
# Two labels per state, not one. `phrase` completes "Fri 14 Aug — ..." in a
# cell's tooltip and wants to be a clause; `count` completes "11 ..." in the
# strip's summary line and wants to be a noun. One string doing both produced
# "Fri 14 Aug — checked — nothing recorded", which reads as two dashes and one
# thought too many.
SYNC_DAY_STYLES = {
    SYNC_RECORDED: {
        "classes": "bg-slate-300",
        "phrase": "recorded",
        "count": "recorded",
    },
    SYNC_CHECKED: {
        "classes": "bg-slate-700",
        "phrase": "checked, nothing recorded",
        "count": "empty",
    },
    SYNC_UNCHECKED: {
        "classes": "ring-1 ring-inset ring-slate-700",
        "phrase": "not checked yet",
        "count": "unchecked",
    },
}


# `ui_state.SyncFreshness.state` — whether anything is syncing *at all*, which
# is a different question from what any one card below it reports. Same
# vocabulary-in-the-theme-module split as `SYNC_RECORDED` above.
#
# There is deliberately **no colour here**. Amber is now reserved for one
# thing — "this reading is staged, not what the week was generated for" —
# and "a scheduled job has stopped" is not that. The icon carries it
# instead, the same call `TRAINING_TYPE_ICONS` and `SYNC_DAY_STYLES` make.
# This comment survived the amber/violet pass unchanged in substance: it was
# right for the wrong reason before (five meanings) and right for the stated
# reason now.
SYNC_FRESH_NEVER = "never"
SYNC_FRESH_CURRENT = "current"
SYNC_FRESH_STALE = "stale"

SYNC_FRESHNESS_STYLES = {
    SYNC_FRESH_CURRENT: {"icon": "o_sync", "phrase": "The sync is keeping up."},
    SYNC_FRESH_STALE: {
        "icon": "o_sync_problem",
        "phrase": "Nothing has run recently — the scheduled job may be off.",
    },
    SYNC_FRESH_NEVER: {
        "icon": "o_sync_disabled",
        "phrase": "Nothing has ever synced.",
    },
}


# What each stored list is called on the sync page. Here rather than derived
# from the section key because `humanize("weigh_ins").title()` is "Weigh Ins",
# and because the card headings are the one thing that has to distinguish two
# sections filled by one source — `BIOMETRIC_SECTION_SOURCES` is one-to-many
# since `readiness_log` arrived, so labelling these by source would print
# "Garmin" twice and leave the reader to guess which card was which.
#
# The source is still named, first, because "last checked" on both Garmin
# cards moves together (one login, one checkpoint) and a reader who can't see
# they share a source can't see why.
SYNC_SECTION_LABELS = {
    "weigh_ins": "Garmin · weigh-ins",
    "daily_actuals": "Cronometer · logged intake",
    "readiness_log": "Garmin · sleep & HRV",
}


def format_day_label(day: str, iso: Optional[str], short: bool = False) -> str:
    """A day's name with its calendar date — "Thursday 28 August", "Thu 28 Aug".

    `iso` is `PlannerState.day_date_iso`, which is None for a plan generated
    before `week_start_date` existed. That case degrades to the bare weekday
    name rather than inventing a date: the whole reason `week.day_date`
    refuses a `generated_at` fallback is that a plausible-looking wrong date
    is worse than no date, and printing one in a tab title would be the most
    visible possible place to be wrong.

    Pure so it can be tested without a plan and used by both the tab label
    (short) and the panel heading (long) — the two must not drift into
    describing the same day differently.
    """
    if iso is None:
        return day[:3] if short else day
    stamp = datetime.fromisoformat(iso)
    # %-d rather than %d: "Thu 8 Aug", not "Thu 08 Aug". POSIX-only, which is
    # every platform this app runs on.
    return stamp.strftime("%a %-d %b") if short else stamp.strftime(f"{day} %-d %B")


# The Today tab's day-context strip: where the day is spent, and what is being
# trained. Two accents, deliberately outside the emerald/sky/slate/rose that
# STATUS_STYLES spends on eating slots and the indigo PREP_COLUMN_ACCENT owns
# — neither of these is a slot status, and a location chip that borrowed
# "cook" green would read as a fifth one.
#
# **Training carries no hue of its own, and that is the change.** It used to
# be amber everywhere — this chip, the telemetry bolt, the generation
# dialog, the Daily View pills, the review dialog's uplift segment — which
# was one of five things amber meant. `TRAINING_TYPE_ICONS` below already
# distinguishes the *kind* of session by glyph, so the hue was saying only
# "a session exists", which the glyph's presence says by itself.
#
# **The palette contract, after CHANGE-QUEUE.md's amber/violet pass.** Each
# hue means at most two things, and the two never appear in one place:
# - amber   — staged/overridden: a reading measured against a live preview
#             rather than the plan, and `BAND_COLOURS`' near-target band.
# - emerald — a cook slot (`STATUS_STYLES`), and on-target (`BAND_COLOURS`).
# - sky     — a leftover slot, and protein.
# - rose    — a failed slot, and off-target.
# - violet  — fat (`MACRO_TINTS`), and location (`LOCATION_ACCENT`, here).
# - orange  — carbs, and nothing else.
# - cyan    — fibre, and nothing else.
# - indigo  — the prep column, and nothing else.
# - slate   — the neutral ground, not a meaning. Anything subtracted from a
#             hue lands here and leans on a glyph or on weight instead.
#
# Adding a third meaning to any of the above is the specific thing not to do.
LOCATION_ACCENT = "bg-violet-400/10 text-violet-200 ring-1 ring-inset ring-violet-300/25"
TRAINING_ACCENT = "bg-slate-700/30 text-slate-300 ring-1 ring-inset ring-slate-600/30"
# A scheduled rest day. Explicitly muted rather than left in TRAINING_ACCENT:
# `apply_training_adjustments` skips a rest entry, so it expands no budget and
# pins no meal, and an amber chip would promise calories it never bought.
REST_ACCENT = "bg-slate-700/30 text-slate-400 ring-1 ring-inset ring-slate-600/30"

# Workout type -> the icon that stands for it. **Icon, not colour, is what
# distinguishes the types**: every hue in this module already means something
# specific (see the palette contract above), so seven new ones would collide
# with an existing meaning long before they read as a scale. Every session is
# now neutral — training gave up amber in the amber/violet pass, precisely
# because this map was already doing the distinguishing — and the glyph
# carries both the type and the fact that there is a session at all.
#
# Keys are matched by `training_icon` exactly first, then as a **prefix**, the
# same widening `WORKOUT_BREAKFAST_TYPES` uses: a future `gym_strength` gets
# the dumbbell and a `cardio_swim` the heart, with no edit here.
TRAINING_TYPE_ICONS = {
    "gym_hypertrophy": "fitness_center",
    "cardio_hiit": "bolt",
    "cardio_run": "directions_run",
    "cardio_ride": "directions_bike",
    "cardio_easy": "monitor_heart",
    "walk": "directions_walk",
    "rest": "bedtime",
    # Prefix fallbacks, longest-first at match time.
    "gym": "fitness_center",
    "cardio": "monitor_heart",
}
# What an unrecognised type gets. `fitness_center` rather than a question mark:
# a type this file hasn't heard of is still a workout, and the strip prints its
# humanized name beside the icon anyway.
TRAINING_ICON_FALLBACK = "fitness_center"


def training_icon(training_type: str) -> str:
    """The icon for a workout type — exact match, then longest prefix.

    Longest prefix rather than first match, because `cardio_ride` and
    `cardio` are both prefixes of the former and only the specific one is
    right. Never raises: an unknown type resolves to
    `TRAINING_ICON_FALLBACK`, since a config typo should render a generic
    workout rather than take the day picker down.
    """
    if training_type in TRAINING_TYPE_ICONS:
        return TRAINING_TYPE_ICONS[training_type]
    matches = [key for key in TRAINING_TYPE_ICONS if training_type.startswith(key)]
    return TRAINING_TYPE_ICONS[max(matches, key=len)] if matches else TRAINING_ICON_FALLBACK


# `ui_state.TrainingNote.kind` -> the badge that goes on the meal the note is
# about. Post- and pre-workout are opposite instructions (refuel vs. stay
# light), so they get their own labels rather than one shared "training" chip
# whose tooltip is the only thing distinguishing them.
TRAINING_NOTE_BADGES = {
    "post": {"label": "POST-WORKOUT", "icon": "bolt"},
    "pre": {"label": "PRE-WORKOUT", "icon": "schedule"},
}

# (key, short label, unit suffix). Calories carry no suffix because their
# short label already reads as one — "kcal: 2200kcal" otherwise.
MACRO_LABELS = [
    ("calories", "kcal", ""),
    ("protein_g", "P", "g"),
    ("net_carbs_g", "C", "g"),
    ("fat_g", "F", "g"),
]

# The three macros that ride behind the calorie figure on a card's micro-pill
# strip, each with the tint that identifies it everywhere in the UI. Colour is
# on the letter, not the number: the digits are what you compare between cards,
# so they stay one weight and one colour down the whole column.
#
# Carbs moved off amber (to orange) and fibre off emerald (to cyan, freed by
# `PREP_BADGE_STYLES`) in CHANGE-QUEUE.md's amber/violet pass: amber is now
# staged-vs-stored and emerald is the cook status, and a macro figure is
# neither. Violet keeps fat, its second meaning being location — the two
# never share a surface. See the palette contract beside `LOCATION_ACCENT`.
MACRO_TINTS = {
    "protein_g": "text-sky-300",
    "net_carbs_g": "text-orange-300",
    "fat_g": "text-violet-300",
    # Only read by the detail dialog and the day-totals row — fibre is not on
    # the card strip this dict was originally written for.
    "fiber_g": "text-cyan-300",
}

# The same four macros again, labelled for the expanded recipe card rather
# than for a 12-column grid cell. `MACRO_LABELS`' single letters exist because
# a card is one seventh of the screen wide; the detail dialog has room for the
# conventional three-letter forms, which is what a recipe card reads like
# everywhere outside this app. Calories carry no unit suffix for the same
# reason they don't there — "590kcal KCAL".
MACRO_DETAIL_LABELS = [
    ("calories", "KCAL", ""),
    ("protein_g", "PRO", "g"),
    ("net_carbs_g", "CHO", "g"),
    ("fat_g", "FAT", "g"),
    # Fibre is reported, never budgeted (see `planner.NUTRIENT_KEYS`), so it
    # appears here — where there is room for it — and deliberately not on
    # `MACRO_LABELS`' card strip, which is one seventh of the screen wide and
    # carries only figures being compared against a target. It is tinted
    # emerald rather than left grey so the strip still reads as a set, but it
    # sits last because it is the one number with nothing to divide by.
    ("fiber_g", "FIB", "g"),
]

# The mono, letter-spaced, uppercase section heading the expanded card is
# built out of ("INGREDIENTS (6 ITEMS)", "PREPARATION INSTRUCTIONS"). One
# constant because the look only reads as a system while every heading in the
# dialog shares it exactly — a second hand-typed copy drifting by a pixel of
# tracking is what makes this kind of layout look approximate.
MONO_SECTION_LABEL = (
    f"{TEXT_BODY} font-mono uppercase tracking-[0.18em] text-slate-500"
)

# Fallbacks for config.json's "ui_settings" object, used when a config.json
# predates that section.
#
# A title longer than title_tooltip_chars can't fit the card's two lines at
# this column width, so it gets a tooltip carrying the full name. Below it the
# tooltip would only repeat what is already on screen.
DEFAULT_UI_SETTINGS = {
    "bar_scale_limit": 1.6,
    "title_tooltip_chars": 38,
}

# bar_scale_limit (in DEFAULT_UI_SETTINGS above) is how far a telemetry bar
# can extend past its target before it stops growing. The bar's full width is
# `max(1, ratio)` capped there, so an overshoot renders as a real second
# segment rather than a bar pinned at 100% that looks identical to landing
# exactly on budget.

# Band -> hex, for the telemetry bars. Hex rather than Quasar colour names
# because these are painted onto plain divs (a two-segment bar is not something
# `ui.linear_progress` can draw) and the same value has to serve as the
# overshoot segment at reduced alpha.
#
# "near"'s amber is one of the two meanings amber is allowed to keep, the
# other being a staged/overridden reading. They never co-occur: this one
# only ever fills a telemetry bar, that one only ever marks a label or a
# chip. See the palette contract beside `LOCATION_ACCENT`.
BAND_COLOURS = {
    "on": "#34d399",  # within ±5% of target
    "near": "#fbbf24",  # ±5–15%: worth seeing, not worth fixing
    "off": "#fb7185",  # beyond ±15%
    "none": "#475569",  # nothing generated for this day yet
}

# The one-click leftover action: tonight's dinner feeds tomorrow's lunch. This
# is the overwhelmingly common bulk-cooking pattern (it is the same one
# `week.autofill_leftovers` automates for the whole week), offered per-card so
# it can be applied to one dinner without rewriting the grid.
LINK_SOURCE_MEAL = "dinner"
LINK_TARGET_MEAL = "lunch"
LINK_ACTION_LABEL = "Link to next lunch"

# Hues for the cook->leftover chains. Cycled, so two chains can share a colour
# on a busy week — the colour is a hint, the hover outline (keyed on a unique
# class per chain) is what disambiguates.
LINK_COLOURS = ["#38bdf8", "#fbbf24", "#a78bfa", "#34d399", "#fb7185", "#22d3ee"]

# The three targets that are editable in the drawer. Fat is deliberately not
# among them: `derive_fat_g` computes it from the other three, so an input for
# it could only ever disagree with the number the planner actually uses.
TARGET_FIELDS = [
    ("calories", "kcal"),
    ("protein_g", "protein g"),
    ("net_carbs_g", "carbs g"),
]

# How the Settings destination's Daily Targets section labels each macro, and
# what it is allowed to say about where the number comes from. The fourth
# element is the unit; the third is None for the two switchable macros (the
# toggle speaks for them) and a fixed sentence for the two that have no mode
# to switch — carbs because the engine has no carb model and hands
# `weekly_schedule`'s figure straight back, fat because `derive_fat_g` always
# computes it from the other three. Stating those two outright is the point
# of the section: "where does this number come from" has an answer for all
# four macros, not only the two with a control beside them.
TARGET_SOURCE_ROWS = [
    ("calories", "Calories", None, "kcal"),
    ("protein_g", "Protein", None, "g"),
    (
        "net_carbs_g",
        "Carbs",
        "Always yours — the engine has no carb model, so this is the week's "
        "cycling lever and fat absorbs the difference.",
        "g",
    ),
    (
        "fat_g",
        "Fat",
        "Always derived — whatever energy is left once protein and carbs are "
        "paid for.",
        "g",
    ),
]

# Indigo marks the Sunday prep column everywhere it appears (telemetry header,
# canvas, pipeline row) — deliberately outside the emerald/sky/slate/rose
# palette STATUS_STYLES and BAND_COLOURS already use for day statuses, since
# this column is prep work, not an eating slot, and must never read as a fifth
# status.
PREP_COLUMN_ACCENT = "border border-indigo-400/25 border-l-[3px] border-l-indigo-400 bg-indigo-400/[0.05]"

# Selectable workout types. "rest" is a legitimate entry (a day explicitly
# marked as no training) but carries no macro split — `apply_training_adjustments`
# skips it — so it isn't a key in TRAINING_INTENSITY_SPLIT and is appended here.
TRAINING_TYPES = list(TRAINING_INTENSITY_SPLIT) + ["rest"]
TRAINING_TYPE_LABELS = {value: humanize(value) for value in TRAINING_TYPES}

# What's supposed to feed a day's plan, in dependency order. (key, label,
# icon, description, connected). Shown on the Settings destination as an
# integrations list (`ui_settings.py`) — `connected=False` stages render "Not
# connected" until something real lands, and three of the four now open a
# read-only detail dialog over the data they actually carry (phase 6e of
# `ui-redesign.md`). Used to be a per-day chip row above the telemetry header
# (28 chips: 3 unconnected stages x 7 days, plus workout x 7 days); phase 3
# moved it here since workout, then the one connected stage, already has its
# per-day detail in the Today destination's day-context strip. "Meal Plan"
# isn't a fifth stage here because `telemetry()` already shows it directly.
#
# **Two of these said "not built yet" long after they were built**, which is
# what phase 6e's detail dialogs made impossible to leave standing: `sync` was
# written when Health Connect was the plan and describes a Garmin sleep/Body
# Battery feed that never landed, while the Garmin/Cronometer sync that *did*
# has been writing `biometrics.json` since (CLAUDE.md's "Biometric sync"), and
# `context` predates `week.apply_location_modes`, which reads `base_schedule`/
# `location_rules` on every generation. `connected` here means "something real
# reaches a plan from this", not "this is finished" — hence the descriptions
# below naming what each one still doesn't do rather than a flag with a third
# state to interpret.
PIPELINE_STAGES = [
    (
        "readiness",
        "Morning Readiness",
        "self_improvement",
        "Subjective readiness check-in — not built yet.",
        False,
    ),
    (
        "sync",
        "Biometric Sync",
        "monitor_heart",
        "Garmin weigh-ins and sleep/HRV, Cronometer intake, from the sync CLI.",
        True,
    ),
    (
        "context",
        "Calendar/Location",
        "event",
        "Where each day is spent, from schedule.json's defaults — no "
        "calendar integration.",
        True,
    ),
    (
        "workout",
        "Adaptive Workout",
        "fitness_center",
        "Training session for the day, from the review dialog's schedule.",
        True,
    ),
]


def split_quantity(text: str) -> tuple:
    """Split "200g" into ("200", "g") so the unit can be set smaller than the
    number it qualifies.

    The digits are what you read off an ingredient line; the unit is a
    constant "g" down the whole column and only needs to be legible, not
    prominent. Takes `shopping.format_quantity`'s output rather than raw
    grams, so it inherits the count-unit forms ("2 eggs") too and simply
    treats "eggs" as the unit.

    A string with no numeric head (nothing produces one today) comes back as
    ("", text), which renders as an unsplit label rather than as nothing.
    """
    match = re.match(r"^([\d.,]+)\s*(.*)$", text.strip())
    if not match:
        return "", text
    return match.group(1), match.group(2)


def pluralize(word: str) -> str:
    """Plural of a meal-type name, for the progress dialog's stage heading.

    Display only, and only for meal types — `meal_type_order` lets config
    define its own, so this can't assume the four built-ins. The sibilant rule
    is what "breakfasts" and "lunches" need between them; anything else takes a
    bare -s, which is right for every meal name in English worth the extra code.
    """
    if word.endswith(("ch", "sh", "s", "x", "z")):
        return word + "es"
    return word + "s"


def chain_css(chains: int) -> str:
    """CSS that lights up a whole cook->leftover chain when any card in it is hovered.

    `:has()` on the canvas is what makes this work without JavaScript: hovering
    any member matches the ancestor, which then outlines *every* card carrying
    that chain's class, wherever it sits in the 7-column grid. A round trip to
    Python per mouseenter would be visibly laggy for a pure hover effect.

    One rule per chain because a selector can't compare one element's class to
    another's; `chains` is the most a week of this shape can hold, since every
    chain needs a cook plus at least one leftover. Outline rather than border
    so nothing reflows, and browsers without `:has()` (pre-2023) simply lose
    the highlight — the dot and the "feeds"/"from" lines still name both ends.
    """
    rules = [
        ".meal-canvas .chain {"
        " outline: 1px solid transparent; outline-offset: 2px;"
        " border-radius: 0.25rem; transition: outline-color 120ms ease; }"
    ]
    for index in range(max(chains, 0)):
        colour = LINK_COLOURS[index % len(LINK_COLOURS)]
        rules.append(
            f".meal-canvas:has(.chain-{index}:hover) .chain-{index}"
            f" {{ outline-color: {colour}; }}"
        )
    return "\n".join(rules)


def card_hover_css() -> str:
    """Per-status glow, keyed off `STATUS_STYLES[...]['glow']`.

    A `box-shadow` rather than a border-width change: the latter reflows
    neighbouring cards by a pixel on hover, which reads as a jitter down a
    column of 28 cards rather than as polish.
    """
    rules = []
    for status, look in STATUS_STYLES.items():
        rules.append(
            f".meal-card.card-{status}:hover {{"
            f" border-color: {look['glow']};"
            f" box-shadow: 0 0 0 1px {look['glow']}66, 0 0 14px 0 {look['glow']}40; }}"
        )
    return "\n".join(rules)


def link_line(marker: str, text: str, colour: str) -> None:
    """The one-line "this card is tied to that one" note, in its chain's colour.

    Both ends get one — the cook says who eats it, the leftover says what it
    came from — so the pairing is readable without hovering anything.
    """
    with ui.element("div").classes(f"flex flex-row items-center gap-{SPACE_TIGHT} min-w-0"):
        ui.element("span").classes(f"shrink-0 w-1.5 h-1.5 {RADIUS_PILL}").style(
            f"background: {colour}"
        )
        ui.label(f"{marker} {text}").classes(f"{TEXT_MICRO} truncate").style(f"color: {colour}")


def macro_band(actual: float, target: float) -> str:
    """Which `BAND_COLOURS` key a day landed in against one macro's target.

    A single scale factor can't fix a bad macro *ratio* (CLAUDE.md), so this is
    a read on the day, not a promise the plan is right — on ±5%, near ±15%,
    off beyond that. Wide enough that only a genuinely off day goes red.
    """
    if target <= 0 or actual <= 0:
        return "none"
    delta = abs(actual / target - 1)
    if delta <= 0.05:
        return "on"
    if delta <= 0.15:
        return "near"
    return "off"


def telemetry_bar(
    actual: float,
    target: float,
    *,
    height: str = "8px",
    bar_scale_limit: float = DEFAULT_UI_SETTINGS["bar_scale_limit"],
) -> None:
    """A target-vs-actual bar that keeps growing past 100% instead of clipping.

    Plain nested divs, not `ui.linear_progress`: a bar pinned at 100% looks
    identical whether a day landed on target or blew past it, so the fill
    is scaled against `bar_scale_limit` (config.json's
    `ui_settings.bar_scale_limit`) and a genuine overshoot renders as a
    visibly longer bar. The thin marker line is where the target itself sits
    on that same scale, so "landed short" and "landed long" both read at a
    glance relative to it.
    """
    colour = BAND_COLOURS[macro_band(actual, target)]
    ratio = (actual / target) if target else 0.0
    fill_pct = min(max(ratio, 0.0), bar_scale_limit) / bar_scale_limit * 100
    target_pct = 100 / bar_scale_limit
    with ui.element("div").classes(
        f"relative w-full {RADIUS_PILL} bg-slate-800 overflow-hidden"
    ).style(f"height: {height}"):
        ui.element("div").classes(
            f"absolute inset-y-0 left-0 {RADIUS_PILL} transition-all duration-300"
        ).style(f"width: {fill_pct:.1f}%; background: {colour};")
        ui.element("div").classes("absolute inset-y-0 w-px bg-slate-100/50").style(
            f"left: {target_pct:.1f}%;"
        )
