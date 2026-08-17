"""Presentation constants and pure rendering helpers for `ui_app.py`.

Split out of `ui_app.py` so a widget module (cards, canvas, drawer) can pull
in "what colour is a cook card" without also pulling in `PlannerState` and
the repository. Nothing here reads or writes state — every function takes
its inputs as arguments and returns either a string or renders directly via
`ui.element`/`ui.label`, the same leaf-widget shape `link_line` and
`telemetry_bar` already had in the monolith.
"""

from nicegui import ui

from planner import TRAINING_INTENSITY_SPLIT
from week import humanize

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

# `SlotView.prep_badge` — set only on a leftover card eating a Sunday-prepped
# batch (see `planner.is_sunday_prepped`). "fridge" vs. "freezer" mirrors the
# same threshold `storage_note` used to write the cook's own storage note, so
# the badge never disagrees with the text a user would see on the recipe.
PREP_BADGE_STYLES = {
    "fridge": {
        "label": "⚡ Prepped on Sun",
        "classes": "bg-amber-400/15 text-amber-200 ring-1 ring-inset ring-amber-300/30",
    },
    "freezer": {
        "label": "❄️ From Freezer",
        "classes": "bg-cyan-400/15 text-cyan-200 ring-1 ring-inset ring-cyan-300/30",
    },
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
MACRO_TINTS = {
    "protein_g": "text-sky-300",
    "net_carbs_g": "text-amber-300",
    "fat_g": "text-violet-300",
}

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

# The context pipeline shown above the telemetry header: what's supposed to
# feed a day's plan, in dependency order. (key, label, icon, description,
# connected). `connected=False` stages have no data source wired up yet —
# they render as a permanently dashed/muted chip until something real lands
# in `pipeline_value()`. "Meal Plan" isn't a fifth stage here because
# `telemetry()` already renders it immediately below this row.
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
        "Health Connect Sync",
        "monitor_heart",
        "Garmin sleep/Body Battery — not built yet.",
        False,
    ),
    (
        "context",
        "Calendar/Location",
        "event",
        "WFH vs. in-office, meeting load — not built yet.",
        False,
    ),
    (
        "workout",
        "Adaptive Workout",
        "fitness_center",
        "Training session for the day, from the drawer's schedule.",
        True,
    ),
]


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
    with ui.element("div").classes("flex flex-row items-center gap-1 min-w-0"):
        ui.element("span").classes("shrink-0 w-1.5 h-1.5 rounded-full").style(
            f"background: {colour}"
        )
        ui.label(f"{marker} {text}").classes("text-[9px] truncate").style(f"color: {colour}")


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
        "relative w-full rounded-full bg-slate-800 overflow-hidden"
    ).style(f"height: {height}"):
        ui.element("div").classes(
            "absolute inset-y-0 left-0 rounded-full transition-all duration-300"
        ).style(f"width: {fill_pct:.1f}%; background: {colour};")
        ui.element("div").classes("absolute inset-y-0 w-px bg-slate-100/50").style(
            f"left: {target_pct:.1f}%;"
        )
