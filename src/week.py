"""Week-level planning primitives.

The unit of planning is no longer "a day of recipes" but a grid of **eating
slots** (one per day x meal_type) laid over a smaller set of **cook events**.
A slot either cooks something new, eats leftovers of an earlier cook slot, or
is skipped. That single distinction is what makes bulk cooking ("cook Sunday,
eat it Wednesday") and flexible shopping windows expressible at all:

- A cook slot's portions are derived from how many slots claim it, so the
  batch size can never silently disagree with the meals it has to cover.
- Shopping windows group recipes by **cook day, not eating day** — a Sunday
  batch eaten on Wednesday is bought on the Sunday trip, not split across two.

Everything in this module is deterministic and API-free: the whole week is
fully resolved (styles, cuisines, portions, windows) before a single token is
generated, so the UI can preview exactly what it is about to ask for.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

MODE_COOK = "cook"
MODE_LEFTOVER = "leftover"
MODE_SKIP = "skip"

# Who made a leftover link — `SlotSpec.link_origin`. The four differ in what
# is allowed to overwrite them, which is the whole reason the distinction is
# stored rather than inferred:
#
#   user      a deliberate "Link to next lunch" click. Never touched by
#             anything automatic. The conservative default, so a plan saved
#             before this field existed keeps every link it has.
#   location  `apply_location_modes` resolving `<meal_type>_mode: leftover`.
#             The rule says an Office lunch *is* a leftover, never whose —
#             "the previous day's dinner" is a resolution, not an intent — so
#             `spread_batch` may re-point one at a batch instead.
#   batch     `spread_batch`'s own. Dropped by `clear_batch_links` before
#             every run, so the toggles re-spread instead of freezing.
#   freezer   `resolve_freezer_draws`' own — design-04 §4. The one origin
#             whose `source` is not a slot in this week at all: it names a
#             `data/freezer.json` lot, food that predates the week by
#             definition. `validate_week`'s "source must be a slot in this
#             week" rule, and every other leftover rule that assumes grid
#             membership (meal-type compatibility, forward-only ordering),
#             is exempted for this origin alone — user/location/batch
#             leftovers still have to resolve to a real earlier cook slot.
LINK_ORIGIN_USER = "user"
LINK_ORIGIN_LOCATION = "location"
LINK_ORIGIN_BATCH = "batch"
LINK_ORIGIN_FREEZER = "freezer"

# Who pinned the recipe on a cook slot — `SlotSpec.recipe_pin_origin`, the
# same "what may overwrite this" question `link_origin` answers for a
# leftover. Two values, because there are two ways a `recipe_id` reaches a
# slot:
#
#   auto  `planner.select_favorite_assignments` claiming a slot by strict
#         LRU. Blanked by `clear_recipe_pins` before every full-week run, so
#         the rotation window advances instead of re-serving week one's picks
#         forever. This is the **default**, and deliberately the opposite of
#         `link_origin`'s "assume the human" default: before the review
#         dialog's hand pin existed, the automatic selector was the *only*
#         thing that set `recipe_id`, so an un-tagged pin on a plan saved
#         before this field is an automatic one. The default matches the only
#         case that could have produced it.
#   user  a hand pick in the review dialog. Survives `clear_recipe_pins` — a
#         recipe the user chose is a veto, not a pick due for a re-roll —
#         exactly as a user-made "Link to next lunch" survives
#         `clear_batch_links`.
PIN_ORIGIN_AUTO = "auto"
PIN_ORIGIN_USER = "user"
MODES = [MODE_COOK, MODE_LEFTOVER, MODE_SKIP]

# Sentinel used in the UI dropdowns for "let the planner decide". Stored as
# None on the model so callers never have to special-case the string.
AUTO = "auto"

DEFAULT_MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]
DEFAULT_SERVINGS_PER_MEAL = 2

# The four budgeted macros. Defined here rather than in `planner.py` (which
# imports it from this module) because `SlotSpec.skip_estimate` is validated
# against these keys and `week.py` cannot import `planner` — the dependency
# runs the other way. `planner.NUTRIENT_KEYS` extends this with the reported-
# only `fiber_g`; a skip estimate carries the budgeted four alone, since
# fibre you didn't cook is fibre nobody can estimate.
MACRO_KEYS = ("calories", "protein_g", "net_carbs_g", "fat_g")

# Departments where a long gap between shopping and cooking is a real problem.
# Used only to annotate the shopping list, never to change quantities.
PERISHABLE_DEPARTMENTS = {
    "Fish & Seafood",
    "Produce",
    "Meat & Poultry",
    "Dairy & Eggs",
}

# Fallbacks for config.json's "inventory_rules" object, used when a caller has
# no config (or an older config.json predates this section). The canonical
# values now live in config.json; these are what the app used before that
# section existed.
# How long a cooked dish keeps, by what the dish IS. There is deliberately no
# single number here any more: `inventory_rules.fridge_safe_days` was one
# global read in six places, and it was wrong in both directions at once — too
# short for a beef stew (which keeps 4 days, so a day of good food was thrown
# away and a batch that could have covered Thursday didn't) and, the direction
# that matters, too long for a rice tray bake. Cooked rice and pasta carry
# *Bacillus cereus* spores that survive cooking and produce toxin as the dish
# sits; 48 hours is the accepted window and is the reason the general 4-day
# rule has an exception carved out of it at all.
#
# Storage life is a property of the dish, so it is reported per recipe
# (`Recipe.storage_class`) and resolved against these tables. It is
# deliberately not a preset knob: a preset over one global could only ever
# have picked a different wrong global.
#
# The two tables answer two different questions and their wordings must not be
# conflated — the fridge figures are **safety**, the freezer figures are
# **quality**. Frozen food does not become unsafe at two months, it degrades.
DEFAULT_STORAGE_WINDOWS = {
    # Hours. See `storage_day_gaps` for why nothing downstream ever prints
    # them.
    "fridge": {
        "default": 96,
        "rice_or_pasta": 48,
    },
    # Whole months, which is the granularity the guidance itself is stated at.
    # Each figure is the LOWER end of its published range (2-3, 2-4, 1-3, 1-3,
    # 1) — see `storage_window_for` on why every default here fails short.
    "freezer_months": {
        "soup_stew_casserole": 2,
        "cooked_meat": 2,
        "cooked_poultry": 2,
        "poultry_pieces": 1,
        "fish_seafood": 1,
        "fried": 1,
        "default": 1,
    },
}

# The vocabulary `Recipe.storage_class` may use, and the row a hand-added
# freezer item picks from. `"default"` is a real member and is not the same
# answer as absence: it means "an ordinary cooked dish, none of the categories
# below" — somebody looked and said so — where `None` means nobody said. The
# two resolve differently on purpose (`storage_window_for`), the same
# distinction `total_time_minutes` draws between None and 0 and the same one
# an un-marked adherence row draws against a marked one.
STORAGE_CLASS_DEFAULT = "default"
STORAGE_CLASSES = (
    STORAGE_CLASS_DEFAULT,
    "rice_or_pasta",
    "soup_stew_casserole",
    "cooked_meat",
    "cooked_poultry",
    "poultry_pieces",
    "fish_seafood",
    "fried",
)

DEFAULT_INVENTORY_RULES = {
    "storage_windows": DEFAULT_STORAGE_WINDOWS,
    "perishable_day_gap": 3,
}

def _storage_window_table(config: Optional[dict], table: str) -> Dict[str, int]:
    """One of `inventory_rules.storage_windows`' two tables, merged over the
    shipped one.

    Per-table rather than per-block, so a config that overrides only `fridge`
    does not lose the freezer rows. A caller with no config in scope gets the
    shipped tables, the same tolerance every other `inventory_rules` read here
    extends.

    **Merged rather than replaced, and that is a safety decision rather than a
    convenience.** Replacing meant a config stating only
    `fridge: {"default": 72}` had no `rice_or_pasta` row at all, and
    `storage_window_for` would then resolve a rice dish through the `default`
    row — *lengthening* its window, which is the one direction nothing here is
    allowed to move by accident. Merging means a config can raise or lower any
    row and add new ones, but cannot delete the exception the whole feature
    exists for by leaving it out.
    """
    rules = (config or {}).get("inventory_rules") or DEFAULT_INVENTORY_RULES
    windows = rules.get("storage_windows") or {}
    return {**DEFAULT_STORAGE_WINDOWS[table], **(windows.get(table) or {})}


def storage_window_for(storage_class: Optional[str], table: Dict[str, int]) -> int:
    """Look one class up, **failing short** on anything unrecognised.

    This inverts the convention every other optional field in this codebase
    follows, and the inversion is the point. Everywhere else an absent value
    resolves to the behaviour before the feature existed — `long_oven_cook`
    defaults False, `total_time_minutes` defaults to None meaning unknown, an
    absent `sourcing` block emits nothing — and all of those are safe because
    being wrong costs a worse meal plan. Here being wrong costs a
    food-poisoning risk, so absence resolves to the SHORTEST row instead.

    That is not theoretical. `is_sunday_prepped` broke because an anchor came
    back with both its flags False despite the per-slot directive telling the
    model to set one: a self-report the model simply dropped, on a field the
    prompt explicitly asked for. `storage_class` is the same kind of field
    with the same failure mode, and if a dropped report resolved to the
    default row, **the failure mode of a model forgetting a field would be a
    rice dish scheduled four days out**. Failing short means a dropped report
    costs a shorter batch, which is the right direction to be wrong in.

    Three cases, and the middle one is why this is not a plain `dict.get`:

    - a class the table names -> that row (`rice_or_pasta` in the fridge).
    - a class in `STORAGE_CLASSES` the table does not name -> the `default`
      row. `soup_stew_casserole` has no fridge row because it keeps as long as
      an ordinary dish; only rice is exceptional there.
    - anything else, `None` included -> `min` of the table. Not the literal
      rice figure: if a shorter class is ever added, the unclassified case has
      to follow it *down*, never stay put above it.
    """
    if storage_class in table:
        return table[storage_class]
    if storage_class in STORAGE_CLASSES:
        return table[STORAGE_CLASS_DEFAULT]
    return min(table.values())


def storage_class_label(storage_class: Optional[str]) -> str:
    """How a class reads in a message aimed at a person.

    "an unclassified dish" rather than a blank or the word "None", because the
    backstop's message has to say *why* a stew was judged against two days —
    the honest answer is that nothing said what it was, not that the app
    thinks a stew keeps two days.
    """
    if storage_class is None or storage_class not in STORAGE_CLASSES:
        return "an unclassified dish"
    if storage_class == STORAGE_CLASS_DEFAULT:
        return "an ordinary cooked dish"
    return humanize(storage_class).replace(" or ", "/")


def storage_day_gaps(window_hours: int) -> int:
    """A stated window, in the whole day-gaps every consumer can actually measure.

    The tables are hours; everything that could measure against them holds a
    **date**. A `SlotSpec` carries a weekday name, `WeekPlan` carries
    `week_start_date`, a `CookEvent` resolves to a grid day, and a freezer lot
    would carry `cooked_on`/`frozen_on`. Nothing anywhere stores a *time*, so
    no consumer can establish that a Sunday cook eaten Thursday was inside 96
    hours — Sunday 09:00 to Thursday 20:00 is 107 and Sunday 18:00 to Thursday
    12:00 is 90, and the stored data cannot tell those apart.

    So the hours are the **guidance the figures were derived from** and the
    day-gap is what the app enforces. Adding cook and consumption times is the
    alternative and is deliberately not taken: it would buy a genuine 96-hour
    guarantee and cost a time on every cook event, a time on every eating slot
    and a clock question at every freeze, in an app whose grid is day-granular
    everywhere else.

    **No surface prints hours.** The app does not know them, and a note saying
    "96 hours" would be claiming a measurement nothing here took.

    `design-05` §2a and §5 disagreed about this conversion by exactly one day
    — §2a's prose derived 3 day-gaps from 96h by worst case ((N+1) x 24 <= 96),
    while §5's arithmetic and §2a's own formula line took the stated day-count
    (96h = 4 days, which is how the requirement was originally written). The
    day-count reading governs, settled 2026-09-01: the source figures are day
    counts and the hours are their gloss. The safety win here is the per-dish
    exception, not a silent extra day of tightening on top of it.
    """
    return max(0, int(window_hours) // 24)


def fridge_day_gaps(storage_class: Optional[str], config: Optional[dict] = None) -> int:
    """How many day-gaps a dish of this class may sit between cooking and eating.

    A day-gap of 0 is "eaten the day it was cooked". This is the number every
    fridge consumer compares a span against — `spread_batch`'s bound,
    `apply_batch_selections`' `max_day_index`, `validate_week`'s backstop,
    `storage_note`'s refrigerate-versus-freeze wording and the per-card badge
    — so they cannot come to disagree about a Thursday.
    """
    return storage_day_gaps(
        storage_window_for(storage_class, _storage_window_table(config, "fridge"))
    )


def freezer_months(storage_class: Optional[str], config: Optional[dict] = None) -> int:
    """How many whole months a frozen lot of this class stays worth eating.

    **Quality, not safety** — the distinction `fridge_day_gaps` is the other
    half of. Frozen food does not become unsafe at two months, it degrades,
    and "unsafe" and "past its best" prompt different behaviour: conflating
    them teaches a reader to ignore both. Nothing acting on this figure may
    ever remove anything (see `freezer_quality_note`).
    """
    return storage_window_for(
        storage_class, _storage_window_table(config, "freezer_months")
    )


# Roughly a month, for turning a date gap into the whole months the freezer
# table is stated in. Deliberately arithmetic rather than calendar months: the
# figure it is compared against is a one-significant-figure guideline, and a
# calendar walk would imply a precision the guidance does not have.
DAYS_PER_STORAGE_MONTH = 30


def freezer_quality_note(
    frozen_on: Optional[date],
    today: date,
    storage_class: Optional[str],
    config: Optional[dict] = None,
) -> str:
    """A quality warning for one frozen lot, or "" while it is still good.

    Pure, and storage-free on purpose: `data/freezer.json` does not exist yet
    (PROMPT-11 / `design-04`), and this is the window logic that work will
    import rather than write a second time — writing it twice is how the two
    come to disagree about a tub.

    Three things it will not do, each of them a decision:

    - **An undateable lot is flagged, never assumed fresh.** A missing
      `frozen_on` degrades to "no idea how old this is", and the conservative
      reading of that is not a number this function is entitled to pick. It is
      the one field whose absence cannot be defaulted safely.
    - **Nothing is removed.** On a hand-declared list, deleting an expired row
      is the app editing your own statement of what you own. It warns and the
      item stays.
    - **The wording says quality.** "Past its best" and never "unsafe" — see
      `freezer_months`. The fridge half is the sentence that is allowed to say
      unsafe.
    """
    if frozen_on is None:
        return (
            "No freeze date recorded — how old this is cannot be worked out, "
            "so check it yourself before using it."
        )
    months = freezer_months(storage_class, config)
    elapsed_days = (today - frozen_on).days
    if elapsed_days <= months * DAYS_PER_STORAGE_MONTH:
        return ""
    return (
        f"Frozen {elapsed_days} days ago, past the {months}-month mark for "
        "this kind of dish — still safe to eat, but likely past its best."
    )


# `shopping.py`'s `ShoppingItem.buy_late` still reads this module constant
# directly (it's a plain computed property with no config in scope at
# evaluation time) — dynamic wiring there would need `aggregate_cook_events`
# to thread a config through to `ShoppingItem` construction, which is outside
# this refactor. config.json's inventory_rules.perishable_day_gap is the
# value to edit; keep this constant in sync with it by hand until that's done.
PERISHABLE_DAY_GAP = DEFAULT_INVENTORY_RULES["perishable_day_gap"]


def humanize(value: Optional[str]) -> str:
    return value.replace("_", " ") if value else ""


def week_days(config: dict, week_start: Optional[str] = None) -> List[str]:
    """The week in cooking order, rotated so it begins on week_start.

    Generation walks this order, and leftovers may only point backwards
    along it, so "day 1" is whatever the user considers the start of their
    shopping week rather than a hardcoded Monday.
    """
    days = list(config["weekly_schedule"].keys())
    start = week_start or config["week_start_day"]
    if start in days:
        index = days.index(start)
        days = days[index:] + days[:index]
    return days


def week_date_range(days: List[str], generated_at: Optional[str] = None) -> Tuple[date, date]:
    """The calendar span `days` covers, anchored on `generated_at` (or today).

    Nothing in this codebase stores an actual calendar date — a week is a
    rotation of weekday *names* (see `week_days`) — so a banner that wants
    real dates has to derive them. The anchor's weekday tells us how far into
    the 7-day span it falls, which pins the whole week without needing a
    stored start date: a Wednesday generation still produces a Monday start
    if that's what `days[0]` is.
    """
    anchor = datetime.fromisoformat(generated_at).date() if generated_at else date.today()
    target_weekday = datetime.strptime(days[0], "%A").weekday()
    start = anchor - timedelta(days=(anchor.weekday() - target_weekday) % 7)
    return start, start + timedelta(days=6)


def today_in_week(
    week_start_date: Optional[str],
    days: List[str],
    generated_at: Optional[str],
    today: Optional[date] = None,
) -> Optional[str]:
    """Today's weekday name, if this week's actual calendar span covers it —
    else None.

    A loaded `WeekPlan` always has *some* slot for "Thursday", but that tells
    you nothing about whether it's *this* Thursday: `days` is a rotation of
    weekday names, not dates, and the same five-week-old cached plan looks
    identical to this week's at a glance (`week_date_range`'s own docstring).
    This is the check a "Today" view needs before trusting any of a plan's
    slots — reject a stale or not-yet-current week outright rather than
    confidently rendering the wrong Thursday.

    `week_start_date` is `WeekPlan.week_start_date`, set once at generation
    and preserved through later day/meal regenerations. Falls back to
    `week_date_range(days, generated_at)`'s own anchor for a plan generated
    before that field existed — the same pre-migration tolerance
    `history_styles()` already extends to old `meal_history.json` entries.
    """
    today = today or date.today()
    start = (
        datetime.fromisoformat(week_start_date).date()
        if week_start_date
        else week_date_range(days, generated_at)[0]
    )
    if not (start <= today <= start + timedelta(days=6)):
        return None
    return today.strftime("%A")


def day_date(week_start_date: str, days: List[str], day: str) -> str:
    """The ISO calendar date `day` fell on, given the week's real start date.

    Pure index arithmetic: `days[0]` fell on `week_start_date`, and each
    following entry in the rotation is one calendar day later. Requires a
    real `week_start_date` (WeekPlan.week_start_date) — unlike
    `today_in_week`, there is no `generated_at` fallback here, because
    dating a *past* history entry needs the date it actually happened on,
    not a plausible-looking anchor derived from when the plan was
    generated. Callers with no `week_start_date` (a plan from before that
    field existed) should record no date at all rather than guess one.
    """
    start = datetime.fromisoformat(week_start_date).date()
    return (start + timedelta(days=days.index(day))).isoformat()


def meal_types(config: dict) -> List[str]:
    return config["meal_types"]


def styles_for(config: dict, meal_type: str) -> Dict[str, str]:
    """style key -> prose description handed to the model."""
    return config["meal_styles"].get(meal_type, {})


def slot_id(day: str, meal_type: str) -> str:
    return f"{day}:{meal_type}"


def parse_slot_id(value: str) -> Tuple[str, str]:
    """Inverse of `slot_id`: 'Monday:dinner' -> ('Monday', 'dinner').

    The only place a slot id's `:` should get split apart — callers that need
    just the day (`span_days`, `generate_week_plan`) still go through this
    rather than a bare `.split(":")`, so a future change to the id format has
    one place to change.
    """
    day, _, meal_type = value.partition(":")
    return day, meal_type


def slot_label(value: str, short: bool = False) -> str:
    """A slot id as prose: 'Monday:dinner' -> 'Monday dinner' / 'Mon dinner'.

    `humanize` only swaps underscores, so it leaves the colon in a slot id
    sitting in the middle of a sentence. Anything that names a slot to the
    user goes through here instead.
    """
    day, meal_type = parse_slot_id(value)
    return f"{day[:3] if short else day} {meal_type}".strip()


class SlotSpec(BaseModel):
    """One eating slot: what the user wants at this day/meal, pre-generation."""

    day: str
    meal_type: str
    mode: str = MODE_COOK
    style: Optional[str] = None
    cuisine: Optional[str] = None
    source: Optional[str] = Field(
        default=None,
        description=(
            "slot_id of the cook slot this eats leftovers of (mode=leftover) — "
            "or, when link_origin is LINK_ORIGIN_FREEZER, a data/freezer.json "
            "lot id instead of a slot in this week."
        ),
    )
    extra_portions: int = Field(
        default=0,
        ge=0,
        description="Spare portions to freeze, on top of the slots claiming this cook",
    )
    skip_estimate: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Estimated macros for a skipped meal eaten elsewhere (mode=skip) — "
            "dinner with friends, a working lunch. Keys are planner.MACRO_KEYS."
        ),
    )
    recipe_id: Optional[str] = Field(
        default=None,
        description=(
            "Catalog entry id (data/recipes_master.json) to cook here instead "
            "of generating something new (mode=cook). Set by either a user "
            "pin or planner.select_favorite_assignments before a run; the slot is "
            "still a cook, so portions derive and shopping picks it up "
            "exactly as for a generated recipe."
        ),
    )
    recipe_pin_origin: str = Field(
        default=PIN_ORIGIN_AUTO,
        description=(
            "Who chose recipe_id — PIN_ORIGIN_AUTO or PIN_ORIGIN_USER. "
            "Meaningless when recipe_id is None; see the constants for why "
            "automatic pins are cleared before a run and user pins survive."
        ),
    )
    link_origin: str = Field(
        default=LINK_ORIGIN_USER,
        description=(
            "Who made this leftover link — one of LINK_ORIGIN_USER / "
            "_LOCATION / _BATCH / _FREEZER (see their definitions for what "
            "each permits). Meaningless on a slot that isn't MODE_LEFTOVER. Defaults to "
            "'user' so a plan saved before this field existed keeps every "
            "link it has: that is the conservative direction, preserving "
            "links rather than discarding or re-pointing ones whose origin "
            "cannot be proven."
        ),
    )

    @property
    def id(self) -> str:
        return slot_id(self.day, self.meal_type)


class WeekSpec(BaseModel):
    days: List[str]
    servings_per_meal: int = DEFAULT_SERVINGS_PER_MEAL
    slots: List[SlotSpec]

    def by_id(self) -> Dict[str, SlotSpec]:
        return {slot.id: slot for slot in self.slots}

    def cook_slots(self) -> List[SlotSpec]:
        return [slot for slot in self.slots if slot.mode == MODE_COOK]

    def cook_slots_on(self, day: str) -> List[SlotSpec]:
        return [slot for slot in self.cook_slots() if slot.day == day]

    def day_index(self, day: str) -> int:
        return self.days.index(day) if day in self.days else -1


def default_week_spec(
    config: dict,
    week_start: Optional[str] = None,
    servings_per_meal: Optional[int] = None,
) -> WeekSpec:
    """A fresh grid on config's per-meal-type default mode, reshaped by location.

    `week_defaults` is the baseline and `location_rules` overrides it per day
    (see `apply_location_modes`) — an Office lunch inherits last night's
    dinner, a Holiday block skips outright. A config with no `base_schedule`
    gets `week_defaults` untouched, which is every config that predates this.
    """
    days = week_days(config, week_start)
    defaults = config["week_defaults"]
    servings = servings_per_meal or config["serving_rules"]["servings_per_meal"]

    slots = [
        SlotSpec(
            day=day,
            meal_type=meal_type,
            mode=defaults.get(meal_type, MODE_COOK),
        )
        for day in days
        for meal_type in meal_types(config)
    ]
    return apply_location_modes(
        WeekSpec(days=days, servings_per_meal=servings, slots=slots), config
    )


def location_for(config: dict, day: str) -> Optional[str]:
    """Where `day` is spent, per `schedule.json`'s `base_schedule` — or None.

    None covers both a day the schedule doesn't name and a config with no
    `base_schedule` at all, which is what keeps every location feature opt-in:
    an empty mapping means the week is shaped by `week_defaults` exactly as it
    was before any of this existed.
    """
    return (config.get("base_schedule") or {}).get(day)


def location_rule(config: dict, day: str) -> Dict:
    """`day`'s entry out of `location_rules`, or `{}`.

    `{}` for an unnamed day, an unnamed location, or a location with no rule —
    all three mean the same thing to every caller ("no location constraint
    here"), so distinguishing them would only push the same `or {}` outward.
    """
    return (config.get("location_rules") or {}).get(location_for(config, day)) or {}


def location_mode(config: dict, day: str, meal_type: str) -> Optional[str]:
    """The mode `day`'s location forces on `meal_type`, if it forces one.

    Reads `<meal_type>_mode` — `lunch_mode`, `dinner_mode`, and so on — so a
    location constrains only the meals it has an opinion about. An Office day
    says `lunch_mode: leftover` and nothing about dinner, because being at the
    office all day says nothing about what you cook that evening; a Holiday
    block sets all four to `skip`.

    An unrecognised mode string resolves to None rather than raising: this is
    hand-edited config, and a typo should leave that meal on its default
    rather than take the app down at load.
    """
    mode = location_rule(config, day).get(f"{meal_type}_mode")
    return mode if mode in MODES else None


def apply_location_modes(spec: WeekSpec, config: dict) -> WeekSpec:
    """Reshape the grid to where the week is actually being spent.

    Applied by `default_week_spec` to a *fresh* grid only, never to a week
    that already exists: once a week has been generated its slots carry the
    user's own structural edits, and re-imposing the schedule over those would
    silently undo them. This is a better default, not a standing rule.

    The subtle case is `lunch_mode: "leftover"`, which is the Office rule and
    the whole reason this exists. A leftover slot needs a `source`, and a mode
    set without one fails `validate_week` outright — so a location-driven
    leftover is resolved to the previous day's dinner here (the one cross-type
    link `leftover_meal_type_error` permits), and **falls back to cooking**
    when there is nothing to inherit from: day one of the week, or a previous
    day whose dinner is itself skipped. A grid that can't be generated is a
    worse answer than a grid that cooks one extra lunch.
    """
    updated: List[SlotSpec] = []
    by_id = spec.by_id()
    for slot in spec.slots:
        mode = location_mode(config, slot.day, slot.meal_type)
        if mode is None or mode == slot.mode:
            updated.append(slot)
            continue

        if mode != MODE_LEFTOVER:
            # skip/cook need nothing resolving. `source` is cleared so a slot
            # moving off leftover can't keep pointing at a cook it no longer
            # eats — `validate_week` allows a stale source, but the shopping
            # list and portion arithmetic would both still count the claim.
            #
            # A location that skips a meal may say what is eaten instead —
            # `<meal_type>_skip_estimate` on the rule, the same shape as
            # `SlotSpec.skip_estimate`. The rule is the honest place for it:
            # `Outing` means dining out, and a skip with no estimate
            # contributes 0 to a day that was genuinely eaten on, which is
            # exactly what strands the rest of the day's budget (see H-1 in
            # the structural audit this fixed).
            estimate = (
                location_rule(config, slot.day).get(f"{slot.meal_type}_skip_estimate")
                if mode == MODE_SKIP
                else None
            )
            updated.append(
                slot.model_copy(
                    update={
                        "mode": mode,
                        "source": None,
                        "skip_estimate": (
                            {key: float(estimate[key]) for key in MACRO_KEYS}
                            if estimate
                            else None
                        ),
                    }
                )
            )
            continue

        index = spec.day_index(slot.day)
        candidates = (
            [slot_id(spec.days[index - 1], "dinner")] if index > 0 else []
        )
        source = next(
            (
                candidate
                for candidate in candidates
                if by_id.get(candidate) is not None
                and by_id[candidate].mode == MODE_COOK
                and not leftover_meal_type_error("dinner", slot.meal_type)
            ),
            None,
        )
        updated.append(
            slot.model_copy(
                update={
                    "mode": MODE_LEFTOVER,
                    "source": source,
                    # Tagged `location`, not `user`: the rule says this lunch
                    # *is* a leftover, and "the previous day's dinner" is only
                    # how that was resolved. `spread_batch` may therefore
                    # re-point it at a prep batch — still a leftover, still
                    # satisfying the location rule, just eating something
                    # cooked on purpose to be eaten here.
                    "link_origin": LINK_ORIGIN_LOCATION,
                }
            )
            if source
            else slot
        )
    return spec.model_copy(update={"slots": updated})


# Canonical order for weekday-name arithmetic in this module.
# `datetime.strptime(name, "%A")` does not decode the weekday token into a
# real date — with no year/month/day in the input it silently defaults to
# 1900-01-01 regardless of which day was named, so `.weekday()` on the
# result is always 0. `week.py` also cannot import `planner` (whose
# `WEEKDAY_NAMES` states the same order) — the dependency runs the other
# way — so `_shift_day` walks this tuple instead of either.
_WEEKDAY_ORDER = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def _shift_day(day: str, delta: int) -> str:
    """`day`'s name shifted by `delta` weekdays — negative walks backward."""
    return _WEEKDAY_ORDER[(_WEEKDAY_ORDER.index(day) + delta) % 7]


def _location_permits_prep_session(config: dict, day: str) -> bool:
    """Whether `day`'s location has the hours for a batch-prep session.

    There is no separate "permits a prep session" flag on a location rule,
    deliberately: a prep session *is* the long, mostly-unattended cook
    `allows_long_cook` already describes, and a second key asking the same
    question of the same day would be a second answer free to disagree with
    `planner.day_allows_long_cook` about a Tuesday. `week` cannot import
    `planner` (the dependency runs the other way), so this reads
    `location_rule` directly rather than calling that function — same key,
    same fallback shape, independently arrived at because both need it.

    A day whose location declares nothing at all — no `base_schedule` entry,
    an unknown location, or a `location_rules` entry with no
    `allows_long_cook` key — resolves True, the same answer the shipped
    `Home` rule gives: with no information to the contrary, the day is
    treated as one spent at home, free to host a session.
    """
    declared = location_rule(config, day).get("allows_long_cook")
    return True if declared is None else bool(declared)


@dataclass(frozen=True)
class PrepDayResolution:
    """Which real weekday a batch-prep session lands on, or why none does.

    `day` is None exactly when `reason` is set (prose a UI can show
    verbatim) — every consumer should read one or the other rather than
    assuming a day exists. Replaces treating "the day before the week
    starts" as a given: `PREP_DAY_INDEX` is still the right *position* for a
    prepped-ahead cook once a day has been chosen, but which literal weekday
    that position falls on is what this answers.
    """

    day: Optional[str]
    reason: Optional[str] = None


def resolve_prep_day(days: List[str], config: dict) -> PrepDayResolution:
    """Where a batch-prep session lands, derived from the location schedule.

    Whether a week *includes* prep at all stays a preference this function
    does not read (`enable_sunday_prep`, or an anchor being chosen) — this
    only answers *where*, given prep is wanted. It walks backward from the
    day before `days[0]` starts, over exactly the two days that precede it,
    and takes the first one whose location permits a prep session
    (`_location_permits_prep_session`). Never widens the search past those
    two candidates and never falls forward past a failing one — a prep day
    that silently drifted a week earlier than a hand-declared schedule
    expects would be a worse surprise than no prep day at all.

    `days` is the week's cooked order (`WeekSpec.days`, or `week_days`'
    output before a `WeekSpec` exists) — only `days[0]` matters, since that
    is the one fixed point "the day before" is measured from.
    """
    if not days:
        return PrepDayResolution(day=None, reason="No week to resolve a prep day against.")

    first, second = _shift_day(days[0], -1), _shift_day(days[0], -2)
    for candidate in (first, second):
        if _location_permits_prep_session(config, candidate):
            return PrepDayResolution(day=candidate)

    return PrepDayResolution(
        day=None,
        reason=(
            f"No prep day: neither {first} nor {second} has the hours for a "
            "batch-prep session."
        ),
    )


def autofill_leftovers(spec: WeekSpec, meal_type: str, source_meal_type: str) -> WeekSpec:
    """Point every slot of meal_type at the previous day's source_meal_type cook.

    The common "lunch is last night's dinner" pattern, as a one-click button
    rather than 7 manual dropdown selections. Day 1 is left alone — it has no
    previous day to inherit from.
    """
    by_id = spec.by_id()
    updated = []
    for slot in spec.slots:
        if slot.meal_type != meal_type:
            updated.append(slot)
            continue
        index = spec.day_index(slot.day)
        if index <= 0:
            updated.append(slot)
            continue
        candidate = slot_id(spec.days[index - 1], source_meal_type)
        source = by_id.get(candidate)
        if source is None or source.mode != MODE_COOK:
            updated.append(slot)
            continue
        updated.append(slot.model_copy(update={"mode": MODE_LEFTOVER, "source": candidate}))
    return spec.model_copy(update={"slots": updated})


def pin_style(spec: WeekSpec, meal_type: str, style: str, days: Iterable[str]) -> WeekSpec:
    """A copy of `spec` with `meal_type`'s cook slots on `days` set to `style`.

    Only slots still on `auto` (no style chosen) are touched. A style the user
    picked in the drawer is a decision, and a schedule-driven pin must not
    silently overwrite it — the same precedence `hydrate_dynamic_targets`
    gives a hand-written `meal_overrides` entry over a computed one. Leftover
    and skipped slots are skipped for the reason `resolve_auto_choices` skips
    them too: nothing is cooked there, so there is no style to pick.

    The spec edit lives here with the other spec edits; the rule about *which*
    days qualify is config interpretation and lives in
    `planner.morning_training_days`.
    """
    targets = set(days)
    updated = [
        slot.model_copy(update={"style": style})
        if (
            slot.meal_type == meal_type
            and slot.mode == MODE_COOK
            and not slot.style
            and slot.day in targets
        )
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def clear_cuisines(spec: WeekSpec) -> WeekSpec:
    """A copy of `spec` with every cook slot's cuisine reset to auto (None).

    Style counterpart is `clear_styles`, just below. Once a week has been
    generated, every slot carries the concrete cuisine that run resolved,
    and `resolve_auto_choices`/`pick_cuisine_blocks` only ever pick a fresh
    one when a slot is empty (planner.py) — otherwise every later run
    repeats the exact same per-day cuisine forever. `ui_generation.generate_week`
    calls this unconditionally, on every full-week generation, precisely to
    avoid that repeat: a slot carrying a concrete cuisine from a run before
    this one — including one picked from a wider `config["cuisines"]` list
    than the popup's cuisine picker has narrowed it to for this run — is
    reset so it can't disagree with what this run is about to ask for.
    """
    updated = [
        slot.model_copy(update={"cuisine": None}) if slot.mode == MODE_COOK else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def clear_styles(spec: WeekSpec) -> WeekSpec:
    """A copy of `spec` with every cook slot's style reset to auto (None).

    Cuisine counterpart is `clear_cuisines`, just above — same reason, same
    shape. `PlannerState.shuffle_styles` (the drawer's manual escape hatch)
    and `ui_generation.generate_week` (called unconditionally, so every
    full-week generation starts from a clean slate rather than repeating
    whatever a previous run on this same grid happened to resolve) are the
    two callers. Mode, leftover links and skips are untouched — those are
    structural edits the user made on purpose, not picks due for a re-roll.
    """
    updated = [
        slot.model_copy(update={"style": None}) if slot.mode == MODE_COOK else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def next_day_slot_id(spec: WeekSpec, day: str, meal_type: str) -> Optional[str]:
    """`meal_type`'s slot on the day after `day`, or None past the week's end.

    "The day after" is the next entry in `spec.days`, not the next calendar
    day: the week is rotated by `week_start_day`, so the last day has no
    following slot to link to even though a Sunday follows a Saturday.
    """
    index = spec.day_index(day)
    if index < 0 or index + 1 >= len(spec.days):
        return None
    return slot_id(spec.days[index + 1], meal_type)


def leftover_meal_type_error(source_meal_type: str, target_meal_type: str) -> Optional[str]:
    """Why a leftover from `source_meal_type` can't feed `target_meal_type`.

    Generation now runs one meal type at a time across the whole week (see
    planner.generate_week_plan), in priority order breakfast, dinner, lunch,
    snack — a leftover source has to have already been generated by the time
    its target's turn comes up, or the source recipe doesn't exist yet. Same
    meal type always works (that batch's cook and its leftover both wait for
    the same generation stage). The only cross-type link the priority order
    actually supports is dinner feeding lunch. Anything else — breakfast
    feeding lunch, lunch feeding dinner, either feeding snack, etc. — would
    ask the model to carry over a recipe from later in the run.
    """
    if source_meal_type == target_meal_type:
        return None
    if source_meal_type == "dinner" and target_meal_type == "lunch":
        return None
    return (
        f"a {humanize(target_meal_type)} can only eat leftovers of the same meal type "
        "or of a dinner — generation runs one meal type at a time, in an order that "
        "doesn't otherwise guarantee the source is cooked yet."
    )


def leftover_link_error(spec: WeekSpec, target_id: str, source_id: str) -> Optional[str]:
    """Why `target_id` can't eat `source_id`'s leftovers, or None if it can.

    Checked up front rather than by running `validate_week` over the mutated
    week: that returns every problem in the grid, and a one-click action needs
    a single sentence about the two meals the user just picked. The rules
    themselves are the same ones `validate_week` enforces, plus a repeat-click
    guard, so anything this accepts still passes validation afterwards.
    """
    by_id = spec.by_id()
    target = by_id.get(target_id)
    source = by_id.get(source_id)

    if source is None or target is None:
        return "That meal isn't part of this week."
    if source.id == target.id:
        return f"{slot_label(target_id)} can't be its own leftover."
    if source.mode != MODE_COOK:
        return f"{slot_label(source_id)} isn't cooked — leftovers need a cook to come from."
    meal_type_error = leftover_meal_type_error(source.meal_type, target.meal_type)
    if meal_type_error:
        return f"{slot_label(target_id)}: {meal_type_error}"
    if spec.day_index(source.day) > spec.day_index(target.day):
        return (
            f"{source.day} comes after {target.day} — leftovers only travel forwards "
            "through the week."
        )
    if target.mode == MODE_LEFTOVER and target.source == source_id:
        return f"{slot_label(target_id)} already eats this one."

    # Converting a cook into a leftover would strand anything already pointing
    # at it, which validate_week rejects as "source isn't a cooked meal". Say
    # so here instead of silently breaking the other end of that chain.
    dependants = [
        slot.id for slot in spec.slots if slot.mode == MODE_LEFTOVER and slot.source == target_id
    ]
    if dependants:
        return (
            f"{slot_label(target_id)} already feeds "
            f"{', '.join(slot_label(value) for value in dependants)} — repoint those first."
        )
    return None


def link_leftover(
    spec: WeekSpec,
    target_id: str,
    source_id: str,
    origin: str = LINK_ORIGIN_USER,
) -> WeekSpec:
    """A copy of `spec` with `target_id` set to eat `source_id`'s leftovers.

    Call `leftover_link_error` first — this applies the edit unconditionally.
    `extra_portions` is cleared because it only means anything on a cook slot.

    `origin` records who made the link — see `SlotSpec.link_origin` and the
    `LINK_ORIGIN_*` constants. It defaults to `user` so the UI's "Link to next
    lunch" needs no argument and nothing automatic can claim a link by
    omission; `apply_location_modes` and `spread_batch` pass their own.
    """
    updated = [
        slot.model_copy(
            update={
                "mode": MODE_LEFTOVER,
                "source": source_id,
                "extra_portions": 0,
                "link_origin": origin,
            }
        )
        if slot.id == target_id
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def unlink_leftover(spec: WeekSpec, target_id: str) -> WeekSpec:
    """A copy of `spec` with `target_id` turned back into a cook slot.

    The inverse of `link_leftover`, and the only way to undo one: clicking
    "Link to next lunch" a second time hits `leftover_link_error`'s
    repeat-click guard rather than toggling. Without this a grid could only
    ever accumulate links, which is what let a batch chain from one run
    survive into every later week (see `clear_batch_links`).

    Resetting `link_origin` alongside `source` matters: the slot is a cook
    again, and a stale `batch`/`location` origin would make the *next*
    `clear_batch_links` or batch re-point treat a link the user has since made
    by hand as automatic and free to discard.
    """
    updated = [
        slot.model_copy(
            update={
                "mode": MODE_COOK,
                "source": None,
                "link_origin": LINK_ORIGIN_USER,
            }
        )
        if slot.id == target_id
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def _parse_freezer_date(value: Optional[str]) -> Optional[date]:
    """`date.fromisoformat`, tolerant of a malformed or hand-edited row.

    `freezer.FreezerItem` requires a valid `frozen_on`, but `load_freezer`
    hands back plain dicts with no schema check behind them (`design-04` §2 —
    a hand-edited file has to stay trustworthy). A resolver that crashed the
    whole plan over one bad row would be a worse failure than treating that
    one lot as unusable and warning about the slot it would have fed.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class FreezerDraw:
    """One resolved freezer-origin leftover — `design-04` §4/§6.

    `item` is the drawn lot exactly as `resolve_freezer_draws` found it in
    the list it was given — already a freeze-time snapshot
    (`freezer.FreezerItem.storage_class` / `.per_serving`), so a caller that
    stores this record alongside the slot it fed needs nothing further from
    `data/freezer.json` to render that slot's macros and label later. That is
    what lets a cached plan survive the lot being edited or deleted
    afterwards — a historical plan's truth never depends on the current
    mutable ledger.
    """

    slot_id: str
    item: dict
    portions: int


def resolve_freezer_draws(
    spec: WeekSpec,
    slot_ids: Iterable[str],
    freezer_items: Iterable[dict],
    today: Optional[date] = None,
    config: Optional[dict] = None,
) -> Tuple[WeekSpec, List[FreezerDraw], List[str]]:
    """Turn each named slot into a freezer-origin leftover, oldest lot first.

    `slot_ids` names the slots to satisfy from the freezer — which slots
    those are is a `week_shape.freezer_draws` question this function does not
    ask; it only resolves declarations it is handed (`design-04` §6: "a draw
    does not name an item… which item is resolved at generation"). Each names
    exactly one meal's worth: `spec.servings_per_meal` portions, the same
    figure any other cook slot claims for one meal.

    **Resolution is oldest-suitable-first by `frozen_on`, stable `id` as the
    tie-break** — a plain ascending sort on `(frozen_on, id)` over every lot
    with at least `spec.servings_per_meal` portions left unreserved. A lot
    with an unparsable or missing `frozen_on` is skipped as unusable rather
    than guessed at (`_parse_freezer_date`).

    **Portions are reserved only in an in-memory dict scoped to this call.**
    Two draws in the same call cannot claim more of one lot than it has —
    the second sees the first's reservation and moves on to the next-oldest
    suitable lot, or fails — but nothing here writes back to
    `data/freezer.json`; the stored `portions` figure changes only when a
    person confirms it changed, exactly as `inventory_ledger`'s pantry
    spending never reaches disk from the planning side.

    **A missing or insufficient lot warns and changes nothing for that
    slot** — `spec` comes back with that slot exactly as it was handed in,
    so the caller's fallback (an ordinary cook, or whatever it already was)
    stands. Design-04 §8: "a plan should not fail to open because the
    freezer is empty."

    Returns the updated spec, one `FreezerDraw` per slot actually resolved
    (in call order, not necessarily `slot_ids` order for ties), and every
    warning — missing/insufficient lots and a lot past its freezer quality
    window (`freezer_quality_note`, "past its best" wording; still drawn and
    still present, never refused).
    """
    today = today or date.today()
    items = list(freezer_items)
    reserved: Dict[str, int] = {}
    draws: List[FreezerDraw] = []
    warnings: List[str] = []
    by_id = spec.by_id()
    needed = spec.servings_per_meal

    for target_id in slot_ids:
        if target_id not in by_id:
            warnings.append(f"{target_id}: not a slot in this week — freezer draw skipped.")
            continue

        candidates = []
        for item in items:
            item_id = item.get("id")
            frozen_on = _parse_freezer_date(item.get("frozen_on"))
            if not item_id or frozen_on is None:
                continue
            remaining = int(item.get("portions") or 0) - reserved.get(item_id, 0)
            if remaining < needed:
                continue
            candidates.append((frozen_on, item_id, item))
        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))

        if not candidates:
            reason = "no lot has enough portions left" if items else "nothing is declared"
            warnings.append(
                f"{slot_label(target_id)}: {reason} for a freezer draw — left as planned."
            )
            continue

        frozen_on, item_id, item = candidates[0]
        reserved[item_id] = reserved.get(item_id, 0) + needed
        spec = link_leftover(spec, target_id, item_id, origin=LINK_ORIGIN_FREEZER)
        draws.append(FreezerDraw(slot_id=target_id, item=item, portions=needed))

        note = freezer_quality_note(frozen_on, today, item.get("storage_class"), config)
        if note:
            label = item.get("label") or "a freezer lot"
            warnings.append(f"{slot_label(target_id)}: drawing {label} — {note}")

    return spec, draws, warnings


def clear_batch_links(spec: WeekSpec) -> WeekSpec:
    """Drop every link `spread_batch` made, so the next run re-spreads freely.

    Called unconditionally by `ui_generation.generate_week` alongside
    `clear_styles`/`clear_cuisines`/`clear_recipe_pins`, and for exactly the
    same reason those three are: a generated week's slots carry whatever the
    *last* run decided, and `spread_batch` only ever *adds* claims — it counts
    what an anchor already has (`existing_claims`) and tops up to
    `target_claims`. So once a week has been batched, every later run on that
    same grid finds the anchor already at target, links nothing, and returns
    the same anchor: the batch shape, and the anchor day itself, freeze
    permanently. That is the bug this exists to prevent, and it is invisible —
    the toggles still report success, and the week still has batches on it,
    just always the same ones in the same places.

    Only `LINK_ORIGIN_BATCH` slots are dropped. A user's own "Link to next
    lunch" is a structural edit they made on purpose (the same carve-out
    `clear_styles` documents for mode/links/skips) and survives untouched,
    which is also why `spread_batch` still counts *those* claims toward its
    target. A `location` link survives too — it is re-derived from config, not
    from a previous run, so there is nothing stale about it; `spread_batch`
    may still re-point or release one, which is a different operation from
    clearing it.
    """
    return spec.model_copy(
        update={
            "slots": [
                slot.model_copy(
                    update={
                        "mode": MODE_COOK,
                        "source": None,
                        "link_origin": LINK_ORIGIN_USER,
                    }
                )
                if slot.mode == MODE_LEFTOVER and slot.link_origin == LINK_ORIGIN_BATCH
                else slot
                for slot in spec.slots
            ]
        }
    )


def _releasable_dependants(spec: WeekSpec, target_id: str) -> Optional[List[str]]:
    """Slots that must be freed for `target_id` to become a leftover itself.

    `leftover_link_error` refuses to convert a cook that already feeds
    something, because that would strand the other end of the chain. On the
    shipped grid this is what puts **Wednesday's dinner out of reach**: an
    Office rule has already pointed Thursday's lunch at it, so the one Mon-Wed
    slot a second batch still wants is blocked by an auto-generated link
    nobody chose.

    Returns the dependants to release when every one of them is a
    `LINK_ORIGIN_LOCATION` link — those are re-derived from config, and
    `apply_location_modes` itself falls back to cooking whenever a day's
    previous dinner isn't a cook, so a released Office lunch lands in a state
    that rule already produces. Returns None when any dependant is a `user` or
    `batch` link, which must not be silently undone: the batch skips the slot
    instead. An empty list means nothing is in the way.
    """
    dependants = [
        slot for slot in spec.slots if slot.mode == MODE_LEFTOVER and slot.source == target_id
    ]
    if any(slot.link_origin != LINK_ORIGIN_LOCATION for slot in dependants):
        return None
    return [slot.id for slot in dependants]


def _claimable(target: SlotSpec, anchor_id: str) -> bool:
    """Whether `spread_batch` may point `target` at `anchor_id`.

    A cook slot, obviously. Also a leftover whose link came from
    `apply_location_modes` (`LINK_ORIGIN_LOCATION`): that rule says the slot
    *is* a leftover without saying whose, so re-pointing it at a batch honours
    it exactly as well as the previous-day's-dinner default it resolved to.
    Doing so is what lets a second batch exist at all on a grid whose Office
    lunches have already spent the week's slack — see `spread_batch`.

    A `user` link is never claimable: that one names a specific dinner on
    purpose. Nor is a `batch` link, which would mean the two toggles fighting
    over the same slot within a single run — `clear_batch_links` is how a
    *previous* run's batch links get out of the way, before any of this.
    """
    if target.mode == MODE_COOK:
        # A concrete recipe is a deliberate claimant ahead of automatic batch
        # spreading. It may still be an anchor, but never a target converted
        # into somebody else's leftover.
        return not target.recipe_id
    return (
        target.mode == MODE_LEFTOVER
        and target.link_origin == LINK_ORIGIN_LOCATION
        and target.source != anchor_id
    )


def spread_batch(
    spec: WeekSpec,
    anchor_meal_type: str,
    target_servings: int,
    exclude_days: Optional[Set[str]] = None,
    max_span_days: Optional[int] = None,
    exclude_target_days: Optional[Set[str]] = None,
    max_day_index: Optional[int] = None,
) -> Tuple[WeekSpec, Optional[str]]:
    """Pick one cook slot as a batch anchor and link enough forward slots to
    it to approximate `target_servings`, entirely via `link_leftover`.

    Anchor selection: any MODE_COOK slot of `anchor_meal_type`, excluding only
    `exclude_days` (so a second call for the week's other toggle doesn't reuse
    the same day). A slot that's already a leftover source for something else
    — most commonly a dinner already feeding the next day's lunch, the
    long-standing "Link to next lunch" pattern — is deliberately NOT excluded
    here: on a grid that already links every dinner forward (a well-used
    week), excluding those would leave nothing eligible but the last day of
    the week, which by definition has no day left to spread into and produces
    a "batch" of one. Reusing an already-linked day as the anchor and simply
    topping up its remaining claims is what makes this work on exactly the
    grid this feature is for. The earliest day in `spec.days` order wins —
    deterministic, and it leaves the most week left to spread across. It is
    also, for a batch cooked ahead on prep day, always the safest: the anchor
    slot is the first one to eat the batch, so the earliest anchor is the one
    whose whole chain sits closest to the day it was cooked.

    Spreading: starting the day after the anchor, walks the rest of
    `spec.days` in order, trying that day's `anchor_meal_type` slot then its
    "lunch" slot — the only two links `leftover_meal_type_error` allows out of
    a dinner anchor, and deduped to one attempt for a lunch anchor — at most
    one link per day. Links until the anchor's total claims (existing plus
    new, via `claim_counts`) reach `target_claims` or the week runs out,
    whichever first: a batch that can only reach fewer days still generates,
    just smaller, and an anchor that already had enough claims before this
    call adds none.

    `target_claims` = `max(2, min(3, ceil(target_servings /
    servings_per_meal)))` — at least 2 (a "batch" of one day isn't one) and
    at most 3, so a small household's arithmetic doesn't spread one dish
    across half the week.

    **Existing claims count toward that target, which is why every link this
    makes is tagged `LINK_ORIGIN_BATCH` and cleared before the next run** (see
    `clear_batch_links`, called unconditionally by
    `ui_generation.generate_week`). This function only ever *adds* claims, so
    left in place its own previous output satisfies `target_claims` on the
    next generation: it links nothing, returns the same anchor, and the
    week's batch shape — including which day anchors it — never changes
    again. Counting a *user's* links is the intended behaviour and is what
    the already-linked-grid reasoning above is written against; counting its
    own is the bug.

    `max_span_days` (the **default** storage window's day-gaps, threaded in
    by `apply_batch_selections`) stops the walk once it is that
    many days past the anchor. The default rather than a per-dish figure
    because no recipe exists yet: the grid is built before generation runs, so
    at the moment the span is chosen nothing knows whether the dish will turn
    out to be a rice tray bake. `build_storage_rule` then tells the model the
    span this slot needs and `reject_short_storage_class` enforces it, which
    is how a per-dish window is honoured despite the ordering — the same
    state-it-then-check-it shape `build_batch_roast_rule` and
    `reject_misplaced_long_cook` already use. **This is prevention, not validation**: cooked
    food keeps 3-4 days refrigerated, and the alternative — letting the walk
    reach Friday from a Sunday anchor and then refusing to generate the week
    — reports a problem the planner created itself. `validate_week` still
    checks the same bound as a backstop, because a hand-made chain of "Link
    to next lunch" clicks never comes through here. None means unbounded,
    which is what every caller that has no config in scope passes.

    **A target need not be a cook slot.** `_claimable` also accepts a leftover
    whose link came from `apply_location_modes`, re-pointing it at this batch
    — see its docstring. Without that, the shipped grid has room for exactly
    one batch: `location_rules` links Thursday and Friday lunches and Saturday
    dinner before either toggle runs, `leftover_link_error` then refuses every
    dinner that feeds one of them, and the week's second toggle strands with
    nowhere to go. A user's own link is still never taken.

    `max_day_index` is the last day index a batch may touch — **anchor
    included**, unlike every other bound here. It exists because
    `max_span_days` counts from the anchor's own day, and a batch folded into
    the Sunday prep session is not cooked on its anchor day at all: it is
    cooked on prep day, the day *before* `days[0]`. So a Tuesday anchor
    reaching Friday is 3 days by `max_span_days` and 5 days out of the fridge,
    which is how food cooked on Sunday ended up planned for Friday's lunch.
    Day index `i` is `i + 1` days after prep, so a default window of N
    day-gaps means `max_day_index = N - 1`;
    `apply_batch_selections` does that arithmetic. None leaves the anchor-relative bound as the only one,
    which is right for any caller whose batch really is cooked on its own day.

    `exclude_target_days` names days that may not *receive* a link; the
    anchor itself may still fall on one. `apply_batch_selections`
    passes the week's last day, because the batch-prep session happens the
    day *before* `spec.days[0]` (see `ui_cards.prep_day_column`, the eighth
    column left of day 0) — which makes `spec.days[-1]` a full **7 days**
    after prep. On a Monday-start week the Sunday a batch is prepped on and
    the Sunday at the end of the grid are different Sundays, and nothing
    cooked on the first is still food on the second. Deliberately *not* also
    a `validate_week` rule: an ordinary "Link to next lunch" from Saturday
    dinner into Sunday lunch is cooked on Saturday, not on prep day, and
    stays perfectly legal.

    Returns the (possibly updated) spec and the anchor's slot id, or the
    original spec and None if no valid anchor existed at all — callers treat
    that as "nothing to do this run", not an error.

    A second case also returns `None`, deliberately the same way: an anchor
    whose claims never grow past what an *ordinary* dinner already gets for
    free (itself plus, on an already-linked grid, the standard next-day-lunch
    claim) isn't a batch — it's a normal dinner the model was misleadingly
    told to mark `long_oven_cook`/`bulk_prep_friendly`. This bites hardest
    when a grid already has every dinner linked to the next day's lunch (via
    `autofill_leftovers` or repeated "Link to next lunch" clicks): the
    forward walk below refuses to convert a cook slot that's already feeding
    its own next-day lunch (see `leftover_link_error`'s dependants check) and
    refuses a lunch slot that's already fed *by* one, so on a fully-linked
    week there is at most one slot anywhere left free to claim — and a second
    `spread_batch` call for the week's other toggle finds nothing left at
    all. Reporting that honestly (as "no batch happened") beats silently
    keeping an anchor that never moved past its starting claim count.
    """
    exclude_days = exclude_days or set()
    exclude_target_days = exclude_target_days or set()

    candidates = [
        slot
        for slot in spec.cook_slots()
        if slot.meal_type == anchor_meal_type
        and slot.day not in exclude_days
        and (max_day_index is None or spec.day_index(slot.day) <= max_day_index)
    ]
    if not candidates:
        return spec, None

    # An anchor with no eligible day left in front of it can never grow, so it
    # would return None below having spent the pick. Filtering first means a
    # doomed day is passed over rather than chosen and then abandoned — the
    # difference between a toggle that batches and one that reports "couldn't
    # find a day with room".
    # It mirrors the walk's own conditions rather than just asking whether an
    # unexcluded day exists: a day can be perfectly eligible and still have
    # nothing on it this anchor may take (both its slots already claimed, or
    # a dinner that feeds something and so can't become a leftover itself).
    # Asking the cheap question instead would keep a doomed anchor in the pool
    # and strand the toggle anyway, which is the exact failure this prevents.
    def can_reach_a_target(anchor: SlotSpec) -> bool:
        by_id = spec.by_id()
        for offset, day in enumerate(spec.days[spec.day_index(anchor.day) + 1 :], start=1):
            if max_span_days is not None and offset > max_span_days:
                break
            if max_day_index is not None and spec.day_index(day) > max_day_index:
                break
            if day in exclude_target_days:
                continue
            for meal_type in (anchor_meal_type, "lunch"):
                target_id = slot_id(day, meal_type)
                target = by_id.get(target_id)
                if target is None or not _claimable(target, anchor.id):
                    continue
                if leftover_link_error(spec, target_id, anchor.id):
                    continue
                return True
        return False

    reachable = [slot for slot in candidates if can_reach_a_target(slot)]
    candidates = reachable or candidates

    anchor = min(candidates, key=lambda slot: spec.day_index(slot.day))

    target_claims = max(2, min(3, math.ceil(target_servings / spec.servings_per_meal)))
    existing_claims = claim_counts(spec).get(anchor.id, 1)
    additional_links_needed = max(0, target_claims - existing_claims)

    linked = 0
    anchor_index = spec.day_index(anchor.day)
    for offset, day in enumerate(spec.days[anchor_index + 1 :], start=1):
        if linked >= additional_links_needed:
            break
        # Past the fridge window there is nothing worth linking: the batch
        # would be planned into food that isn't safe to eat by then.
        if max_span_days is not None and offset > max_span_days:
            break
        # Past the food-safe window measured from *prep day* — see
        # `max_day_index`. Distinct from `max_span_days` above, which counts
        # from the anchor's own day.
        if max_day_index is not None and spec.day_index(day) > max_day_index:
            break
        # 7 days after prep day, not 0 — see `exclude_target_days` above.
        # `continue` rather than `break`: this rules out one day, not the
        # remainder of the walk.
        if day in exclude_target_days:
            continue
        by_id = spec.by_id()
        # dict.fromkeys dedupes while keeping order: a lunch-anchored batch
        # would otherwise try "lunch" twice and never anything else.
        for meal_type in dict.fromkeys((anchor_meal_type, "lunch")):
            target_id = slot_id(day, meal_type)
            target = by_id.get(target_id)
            if target is None or not _claimable(target, anchor.id):
                continue
            # Free any location link standing in the way first — see
            # `_releasable_dependants`. Applied to a trial copy so a slot is
            # only ever released when the link that needed it actually goes
            # through; `leftover_link_error` is then asked about the grid
            # that would result, not the one before the release.
            releasing = _releasable_dependants(spec, target_id)
            if releasing is None:
                continue
            trial = spec
            for dependant_id in releasing:
                trial = unlink_leftover(trial, dependant_id)
            if leftover_link_error(trial, target_id, anchor.id):
                continue
            spec = trial
            # Tagged `batch` so `clear_batch_links` can drop it again before
            # the next run — otherwise `existing_claims` below counts it on
            # every later generation, the anchor is permanently at target, and
            # both the shape and the anchor day freeze forever. Note this also
            # *overwrites* a location link's own origin when re-pointing one,
            # which is correct: the batch owns the link now, and next run's
            # clear returns the slot to a cook for `apply_location_modes` to
            # resolve again from config.
            spec = link_leftover(spec, target_id, anchor.id, origin=LINK_ORIGIN_BATCH)
            linked += 1
            break

    if linked == 0 and existing_claims < target_claims:
        return spec, None

    return spec, anchor.id


# planner.planning_rule's own fallback, restated: planner.py imports from
# week.py, never the reverse, so apply_batch_selections below cannot call that
# helper without a cycle. This mirrors PlanningRules.batch_target_servings and
# only matters for a config that never went through load_app_config — any
# config that has is guaranteed to carry the real value.
DEFAULT_BATCH_TARGET_SERVINGS = 6


def apply_batch_selections(spec: WeekSpec, config: dict) -> Tuple[WeekSpec, dict]:
    """Turn the popup's bulk-prep/long-cook toggles into leftover links.

    **Each batch takes one meal type, straight across the front of the week.**
    Bulk prep claims the lunches, long cook claims the dinners, both starting
    at day 1 and running as far as the fridge window allows — so with the
    shipped config, Monday-Thursday lunches are all one prepped dish and
    Monday-Thursday dinners are all another. Nothing is searched for and
    nothing competes: the two batches cannot collide because they are on
    different rows of the grid, and neither can drift late because both start
    at the earliest day there is.

    That pairing is not arbitrary. A soup/stew/curry (`BULK_PREP_RULE`'s own
    candidates) is exactly the dish that reheats at a desk and travels in a
    container, and an oven roast or braise (`BATCH_ROAST_ANCHOR_RULE`'s) is
    dinner food. It also means Monday eats two *different* dishes rather than
    the same one twice, which is what any arrangement filling all six slots
    from a single row would have forced.

    **The anchor is bookkeeping, not a choice.** Every recipe has to live on
    some slot — that is what a cook slot is — and prep day has no slot of its
    own in the grid, so the first day a batch is eaten holds the recipe and
    the rest point back at it. Earlier versions *searched* for that day
    (earliest dinner with room, weekends preferred, second toggle excluding
    the first's day), and the search was the entire source of both the
    late-week drift and the two toggles fighting over the same dinners. Day 1
    is always a valid anchor and always the safest one, so there is nothing
    left to search for.

    Returns the (possibly updated) spec and a dict of the two anchor slot ids
    actually chosen (None where a toggle was off, or where `spread_batch`
    could not grow that anchor past what an ordinary meal already gets — see
    its docstring) — merge straight into `config` so
    `generate_meal_type_week`/`generate_sunday_prep_session` can read
    `long_cook_anchor`/`bulk_prep_anchor` off it. A grid whose lunches or
    dinners are already claimed by the user leaves the corresponding batch
    with nothing to grow into; `ui_generation.generate_week` warns rather
    than generating a mislabeled meal silently — this function returns only
    the structured anchors, never a notification, so every caller (CLI, API,
    UI) decides for itself what a stranded toggle is worth saying.
    """
    target_servings = (config.get("planning_rules") or {}).get(
        "batch_target_servings", DEFAULT_BATCH_TARGET_SERVINGS
    )
    # The DEFAULT storage window, because no recipe exists yet — the grid is
    # built before generation runs, so nothing here knows whether the dish
    # will turn out to be a rice tray bake. `planner.build_storage_rule` tells
    # the model the span each slot needs and `reject_short_storage_class`
    # rejects a class too short for it, which is what makes planning against
    # the default safe rather than optimistic.
    safe_day_gaps = fridge_day_gaps(STORAGE_CLASS_DEFAULT, config)
    # These batches are cooked on prep day — the day *before* `spec.days[0]`,
    # the eighth column `ui_cards.prep_day_column` draws — not on the day
    # their anchor slot happens to sit. So the bound that matters is measured
    # from there: day index i is i+1 days out of the fridge, giving indices
    # 0..safe_day_gaps-1. `max_span_days` (anchor-relative) is deliberately
    # not passed as well; it can only ever be looser than this one here, since
    # every anchor is day 0.
    max_day_index = safe_day_gaps - 1
    anchors: Dict[str, Optional[str]] = {"long_cook_anchor": None, "bulk_prep_anchor": None}

    if config.get("bulk_prep_enabled"):
        spec, anchors["bulk_prep_anchor"] = spread_batch(
            spec, "lunch", target_servings, max_day_index=max_day_index
        )

    if config.get("long_cook_enabled"):
        spec, anchors["long_cook_anchor"] = spread_batch(
            spec, "dinner", target_servings, max_day_index=max_day_index
        )

    return spec, anchors


@dataclass(frozen=True)
class WeekShapeApplication:
    """What came of turning `config["week_shape"]` into grid edits —
    design-02 §4/§8, the return of `apply_week_shape` (Task 1.2c).

    `batch_anchors` maps each batch's own declared `name` to the slot id it
    anchored on — `None` for a batch that named an anchor this grid could not
    honour this run. Same "declared, but stranded" shape
    `apply_batch_selections` already returns for its two fixed names,
    generalised to however many `week_shape.batches` names.

    `selected_lots` is exactly `resolve_freezer_draws`' own `draws` return:
    every freezer item this run actually drew from, oldest-suitable-first,
    reserved only for the life of this call — design-04 §4/§6's "selected
    lots".

    `warnings` is every reason a declared batch link or freezer draw could
    not be honoured, in prose. Never raised: `week_shape_errors` is what
    refuses an *incoherent* shape, at load time, before this ever runs. What
    reaches here is a shape that is coherent in the abstract but has met a
    real grid it cannot quite have — a slot a hand edit has since claimed, a
    lot the freezer no longer holds enough of.
    """

    spec: WeekSpec
    batch_anchors: Dict[str, Optional[str]]
    selected_lots: List[FreezerDraw]
    warnings: List[str]


def apply_week_shape(
    spec: WeekSpec,
    week_shape: dict,
    config: dict,
    prep_day: PrepDayResolution,
    freezer_items: Iterable[dict],
    today: Optional[date] = None,
) -> WeekShapeApplication:
    """Turn a validated `config["week_shape"]` into grid edits — design-02's
    replacement for `apply_batch_selections`'s two hard-coded toggles.

    **Applied, never searched** (design-02 §2). Every fact this needs — which
    day a batch anchors on, which days it serves, which lot a draw prefers —
    is already named in `week_shape` or resolved by `resolve_freezer_draws`'s
    own oldest-first rule. There is no day preference, no candidate
    filtering and no re-anchoring: a batch that cannot be applied exactly as
    declared is skipped and warned about, never quietly moved to a day that
    would work instead.

    `week_shape` is trusted to already be **coherent in the abstract** —
    `week_shape_errors` (run at config load, and from Task 1.2d before every
    preset save) is what rejects an unknown meal type, a non-contiguous
    `serves`, a fridge window it can't reach, or two records claiming one
    slot, and this function does not repeat those checks. What it does still
    guard against is a *real* `WeekSpec` disagreeing with an abstractly
    coherent shape — a slot a hand edit has since claimed, or a `cook_on:
    "prep_day"` batch meeting a week whose `prep_day` argument (unlike the one
    `week_shape_errors` checked at load) has since resolved to none. Passed
    in rather than re-derived, exactly as `week_shape_errors` takes it,
    since every caller already has it.

    **The anchor is `serves[0]`, literally** — `slot_id(serves[0],
    meal_type)` — never chosen. It must already be an ordinary `MODE_COOK`
    slot (a pinned favourite is fine — `_claimable`'s docstring is why a pin
    only ever blocks a *target*, never an anchor) or the whole batch is
    skipped: reusing an already-cooking slot is applying the declaration,
    forcing a skipped or location-leftover slot into one is the
    re-anchoring design-02 §2 rules out — the same "location still wins on
    facts" precedence §8 there describes for a *target*, extended to the
    anchor itself.

    **Linking `serves[1:]` reuses `spread_batch`'s own linking half
    unchanged** (`_claimable`, `_releasable_dependants`, `leftover_link_error`,
    `link_leftover`, tagged `LINK_ORIGIN_BATCH`) — design-03 §6: "`spread_batch`
    supplies the linking." A day that can't be claimed (a user's own link, a
    pin, a dependant only a hand edit could free) is skipped with a warning
    and the walk moves to the next declared day — not the "candidate search"
    design-02 rules out, because no *other* day is ever substituted for the
    one that failed; the batch just ends up serving fewer of the days it
    named.

    **Surplus is stamped onto `extra_portions`, nothing more.**
    `freeze_portions` lands on the anchor slot exactly where a
    `spread_batch`-built batch already carries it, so `week.portions_for`,
    recipe scaling and shopping need nothing new to read it. Turning that
    surplus into an actual `data/freezer.json` row is a decision made *after
    the cook*, not here (design-04 §6a: "declared before the run and
    confirmed after" — Task 1.1d's `record_freezer_surplus`, dated via
    `is_prepped_ahead`/`freezer_cooked_on`). This function never touches the
    repository and never writes a lot.

    **A `cook_on: "prep_day"` batch needs no new bookkeeping to be found
    later.** `prep_day_batch_slot_ids` (below) reads `week_shape` directly, so
    a batch's anchor is discoverable from the same dict this function was
    handed the moment it is part of `config` — nothing here has to merge an
    anchor back into `config` for `storage_safety_errors`,
    `is_sunday_prepped` or `freezer_cooked_on` to find it.

    Freezer draws are one call to `resolve_freezer_draws` — oldest-suitable
    stock, reserved only for this call, a `LINK_ORIGIN_FREEZER` leftover per
    slot, an honest warning (never a raised error) for a slot the freezer
    can't cover. `selected_lots` on the result is exactly its `draws` return.

    **Idempotent on a fresh spec.** Every edit here is driven by what
    `week_shape` and `freezer_items` say, never by what the grid already has
    claimed — unlike `spread_batch`, there is no `existing_claims` count that
    would make a previous run's own output look partially satisfied, so
    nothing here needs `clear_batch_links` run first to stay honest: applying
    the same shape to the same fresh spec twice produces the same grid both
    times, and no run's chosen anchor can freeze the next one's.
    """
    batches = week_shape.get("batches") or []
    draw_declarations = week_shape.get("freezer_draws") or []

    batch_anchors: Dict[str, Optional[str]] = {
        raw_batch.get("name"): None for raw_batch in batches
    }
    warnings: List[str] = []

    for raw_batch in batches:
        name = raw_batch.get("name")
        meal_type = raw_batch.get("meal_type")
        serves = raw_batch.get("serves") or []
        cook_on = raw_batch.get("cook_on")
        freeze_portions = raw_batch.get("freeze_portions") or 0
        label = f"batch '{name}'"

        if cook_on == "prep_day" and prep_day.day is None:
            warnings.append(
                f"{label}: cook_on is 'prep_day', but this week has none — "
                f"{prep_day.reason or 'no prep day was resolved'} — skipped."
            )
            continue

        anchor_id = slot_id(serves[0], meal_type)
        anchor = spec.by_id().get(anchor_id)
        if anchor is None:
            warnings.append(f"{label}: {slot_label(anchor_id)} isn't a slot in this week — skipped.")
            continue
        if anchor.mode != MODE_COOK:
            warnings.append(
                f"{label}: {slot_label(anchor_id)} isn't a cook slot to anchor on "
                f"(it's {anchor.mode}) — no re-anchoring, so this batch is skipped."
            )
            continue

        batch_anchors[name] = anchor_id
        if freeze_portions:
            spec = spec.model_copy(
                update={
                    "slots": [
                        slot.model_copy(update={"extra_portions": freeze_portions})
                        if slot.id == anchor_id
                        else slot
                        for slot in spec.slots
                    ]
                }
            )

        for day in serves[1:]:
            target_id = slot_id(day, meal_type)
            target = spec.by_id().get(target_id)
            if target is None:
                warnings.append(f"{label}: {slot_label(target_id)} isn't a slot in this week — left as planned.")
                continue
            if not _claimable(target, anchor_id):
                reason = "a pinned recipe" if target.mode == MODE_COOK else f"a {target.link_origin}-made link"
                warnings.append(
                    f"{label}: {slot_label(target_id)} is already claimed ({reason}) — left as planned."
                )
                continue
            releasing = _releasable_dependants(spec, target_id)
            if releasing is None:
                warnings.append(
                    f"{label}: {slot_label(target_id)} feeds a link only a hand edit can free — "
                    "left as planned."
                )
                continue
            trial = spec
            for dependant_id in releasing:
                trial = unlink_leftover(trial, dependant_id)
            error = leftover_link_error(trial, target_id, anchor_id)
            if error:
                warnings.append(f"{label}: {error}")
                continue
            spec = link_leftover(trial, target_id, anchor_id, origin=LINK_ORIGIN_BATCH)

    draw_slot_ids = [
        slot_id(draw["day"], draw["meal_type"]) for draw in draw_declarations
    ]
    spec, selected_lots, draw_warnings = resolve_freezer_draws(
        spec, draw_slot_ids, freezer_items, today=today, config=config
    )
    warnings.extend(draw_warnings)

    return WeekShapeApplication(
        spec=spec,
        batch_anchors=batch_anchors,
        selected_lots=selected_lots,
        warnings=warnings,
    )


# What `apply_batch_selections` has always produced against the shipped
# config once both toggles were on (design-02 §9's worked example, pinned by
# `test_the_shipped_migration_shape_is_clean`) — literal, not derived, since
# the whole point of a declaration is that nothing here searches for it.
# `config/week.json` now states this explicitly, so `effective_week_shape`
# below only reaches for it when a raw config predates `week_shape` entirely.
#
# Documented removal point: once no `week.json` missing the key is expected
# to exist any more (the next time this file's schema changes is a natural
# trigger), delete this constant, `effective_week_shape`, and the "declared"
# branch in `generate_and_store_week`/`ui_generation.generate_week` that
# calls it — a raw config will then always carry `week_shape`, even if empty.
LEGACY_WEEK_SHAPE: Dict[str, list] = {
    "batches": [
        {
            "name": "bulk-prep",
            "meal_type": "lunch",
            "cook_on": "prep_day",
            "serves": ["Monday", "Tuesday", "Wednesday"],
            "freeze_portions": 0,
        },
        {
            "name": "long-cook",
            "meal_type": "dinner",
            "cook_on": "prep_day",
            "serves": ["Monday", "Tuesday", "Wednesday"],
            "freeze_portions": 0,
        },
    ],
    "freezer_draws": [],
}


def effective_week_shape(config: dict, week_shape_declared: bool) -> dict:
    """`config["week_shape"]` to actually apply — Task 1.2d.

    `AppConfig` always fills `week_shape` in, even when the raw `week.json`
    never mentioned the key at all (`WeekShape`'s own docstring: "every
    load... produces this same empty value") — so by the time a caller holds
    a *validated* `config`, absence and an explicit empty declaration already
    read alike. `week_shape_declared` restores the distinction a caller has
    to make **before** validation, off the raw dict `repository.load_config()`
    returns (`"week_shape" in raw`) — this function itself stays pure and
    takes the answer rather than re-deriving it.

    **Declared** (`week_shape_declared=True`), even as
    `{"batches": [], "freezer_draws": []}`, is a real, chosen answer and is
    applied exactly as given — that is what "explicitly empty" means: no
    automatic batching, on purpose.

    **Absent** is the migration fallback: a `week.json` that predates this
    key gets `LEGACY_WEEK_SHAPE` above, gated on `enable_sunday_prep` exactly
    as the retired `bulk_prep_enabled`/`long_cook_enabled` toggles always
    were (both seeded from it — see the deleted `PRESET_SEEDED_FIELDS`
    entries) — the shape a fresh, unmigrated checkout has always produced.
    """
    if week_shape_declared:
        return config.get("week_shape") or {"batches": [], "freezer_draws": []}
    if config.get("enable_sunday_prep"):
        return LEGACY_WEEK_SHAPE
    return {"batches": [], "freezer_draws": []}


def skip_estimate_totals(slots: Iterable[SlotSpec], day: str) -> Dict[str, float]:
    """`day`'s skipped-but-eaten macros, summed — zeros when there are none.

    A skipped meal used to contribute nothing anywhere, which is right for a
    meal genuinely not eaten and wrong for the common case: dinner with
    friends, a working lunch, a restaurant. Those calories are consumed, and
    a day that ignores them hands their whole share to the meals it *does*
    plan — the remaining two meals absorb a third meal's budget and come back
    oversized.

    An estimate makes such a slot behave exactly like a leftover: it reduces
    what generation briefs (`generate_week_plan` subtracts this from the
    day before splitting) and it counts toward the day's totals
    (`WeekPlan.day_slot_macros` adds it back). Those are the same two places
    `carried_macros` and `day_slot_macros` already handle a leftover, which
    is the parallel to keep in mind when changing either.

    Takes an iterable of slots rather than a `WeekSpec` so `WeekPlan`, whose
    `slots` are the same `SlotSpec` objects but which is not a `WeekSpec`,
    can call it too.
    """
    totals = {key: 0.0 for key in MACRO_KEYS}
    for slot in slots:
        if slot.day != day or slot.mode != MODE_SKIP or not slot.skip_estimate:
            continue
        for key in MACRO_KEYS:
            totals[key] += float(slot.skip_estimate.get(key) or 0.0)
    return totals


def set_skip_estimate(
    spec: WeekSpec, target_id: str, estimate: Optional[Dict[str, float]]
) -> WeekSpec:
    """A copy of `spec` with `target_id`'s skip estimate set (or cleared).

    Clearing is `estimate=None`, which is distinct from an all-zero estimate:
    None means "this meal is not eaten at all" (the original skip semantics,
    the doctor's-appointment case), zeros mean "eaten, and it cost nothing
    measurable". Both are legitimate and they brief the day differently, so
    the UI has to be able to express each.
    """
    updated = [
        slot.model_copy(update={"skip_estimate": estimate})
        if slot.id == target_id
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def pin_recipe(
    spec: WeekSpec,
    target_id: str,
    recipe_id: Optional[str],
    origin: str = PIN_ORIGIN_AUTO,
) -> WeekSpec:
    """A copy of `spec` with `target_id` set to cook a specific catalog recipe.

    The counterpart to `pin_style`: that one narrows *what kind* of meal the
    model invents, this one removes the invention entirely. `recipe_id=None`
    clears the pin and hands the slot back to generation.

    Only touches a slot already set to cook, for the reason `validate_week`
    rejects the combination outright — a leftover eats whatever its source
    cooked and a skip cooks nothing, so a recipe pinned to either is a
    statement with nowhere to land.

    **Pinning clears the slot's style and cuisine**, because a concrete dish
    is a more specific answer than either and the two would otherwise
    disagree on the card: `resolve_auto_choices` has already rolled a style
    for every cook slot by the time this runs, so a scramble pinned onto a
    slot that rolled `yoghurt_bowl` would render as "YOGHURT BOWL" above a
    plate of eggs. It also keeps the pinned day out of style rotation in
    `record_week_history`, which is right — nothing was rotated onto it.
    """
    updated = [
        slot.model_copy(
            update={
                "recipe_id": recipe_id,
                "recipe_pin_origin": origin,
                "style": None,
                "cuisine": None,
            }
            if recipe_id
            else {"recipe_id": None, "recipe_pin_origin": PIN_ORIGIN_AUTO}
        )
        if slot.id == target_id and slot.mode == MODE_COOK
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def clear_recipe_pins(spec: WeekSpec) -> WeekSpec:
    """Drop automatic recipe pins while preserving deliberate user pins.

    Called unconditionally by `ui_generation.generate_week` alongside
    `clear_styles`/`clear_cuisines`, and for exactly the same reason those two
    are: once a week has been generated its slots carry whatever the *last*
    run resolved, and `select_favorite_assignments` only ever fills an empty
    slot. Without this, week two would re-pin week one's favourites forever
    and the rotation window would never advance.
    """
    updated = [
        slot.model_copy(update={"recipe_id": None, "recipe_pin_origin": PIN_ORIGIN_AUTO})
        if slot.mode == MODE_COOK
        and slot.recipe_id
        and slot.recipe_pin_origin != PIN_ORIGIN_USER
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def claim_counts(spec: WeekSpec) -> Dict[str, int]:
    """How many eating slots each cook slot has to feed, itself included."""
    counts = {slot.id: 1 for slot in spec.cook_slots()}
    for slot in spec.slots:
        if slot.mode == MODE_LEFTOVER and slot.source in counts:
            counts[slot.source] += 1
    return counts


def portions_for(spec: WeekSpec) -> Dict[str, int]:
    """Total person-portions each cook slot must yield.

    Derived, never entered by hand: (meals it covers x household size) plus any
    deliberate extras to freeze. This is why the grid has no "batch multiplier"
    — the batch size *is* the number of slots pointing at it.
    """
    by_id = spec.by_id()
    return {
        cook_id: claims * spec.servings_per_meal + by_id[cook_id].extra_portions
        for cook_id, claims in claim_counts(spec).items()
    }


def eaten_on(spec: WeekSpec) -> Dict[str, List[str]]:
    """cook slot id -> every slot id that eats it, in week order."""
    order = {slot.id: index for index, slot in enumerate(spec.slots)}
    claims: Dict[str, List[str]] = {slot.id: [slot.id] for slot in spec.cook_slots()}
    for slot in spec.slots:
        if slot.mode == MODE_LEFTOVER and slot.source in claims:
            claims[slot.source].append(slot.id)
    for slot_ids in claims.values():
        slot_ids.sort(key=lambda value: order.get(value, 0))
    return claims


def storage_safety_errors(
    spec: WeekSpec,
    config: dict,
    storage_classes: Optional[Dict[str, Optional[str]]] = None,
) -> List[str]:
    """Cook slots planned to be eaten past what the dish keeps for.

    Read twice, by two callers that must not come to different answers about a
    Thursday: `validate_week` runs it as the grid gate before generation, and
    `planner.generate_week_plan` runs it again over the *generated* week —
    where the dishes are real and a dropped `storage_class` or a rice dish
    that slipped past the response validator is finally visible.

    **The two calls differ only in what they know, and the difference is
    exactly `storage_classes`.**

    - `None` — no plan yet. Every cook is judged against the **default**
      window, which is also what `spread_batch` planned the grid against, so
      the gate agrees with the planner rather than pre-emptively failing a
      grid the app itself just built. Per-dish tightening cannot happen here:
      the grid is built before any recipe exists.
    - a mapping — a generated week. A slot absent from it, or mapping to
      `None`, is genuinely unclassified and gets the shortest window. Absence
      of a *mapping* and absence of an *entry* are different questions and
      this is the only place both can be asked.

    It reports the slot and the days and never trims. A plan quietly rewritten
    is one nobody checks — the same reason `cap_to_weighted_share` drops its
    surplus visibly rather than moving it, and why `apply_protein_floor` does
    nothing and logs when the floor is unaffordable.
    """
    known = storage_classes is not None
    classes = storage_classes or {}
    # The third place this off-by-one has had to be closed — `max_day_index`
    # and `storage_note` were the first two. A batch folded into the prep
    # session is cooked the day *before* the week starts, so measuring its
    # span from the anchor's grid day is short by exactly one, and short in
    # the unsafe direction: with the shipped grid it reads a prep-day anchor
    # as 2 day-gaps, which is exactly the rice window it ought to be failing.
    prepped_ahead = prep_day_batch_slot_ids(config)
    errors: List[str] = []
    for cook in spec.cook_slots():
        span = span_days(spec, cook.id, cook.id in prepped_ahead)
        storage_class = classes.get(cook.id) if known else STORAGE_CLASS_DEFAULT
        allowed = fridge_day_gaps(storage_class, config)
        if span <= allowed:
            continue
        last = max(
            (value for value in eaten_on(spec).get(cook.id, [])),
            key=lambda value: spec.day_index(parse_slot_id(value)[0]),
        )
        errors.append(
            f"{cook.day} {cook.meal_type}: cooked {span} days before "
            f"{slot_label(last)} eats it, past the {allowed}-day fridge limit "
            f"for {storage_class_label(storage_class)} — re-point that meal "
            "to a later cook."
        )
    return errors


def validate_week(
    spec: WeekSpec,
    config: dict,
    storage_classes: Optional[Dict[str, Optional[str]]] = None,
) -> List[str]:
    """Everything that would make generation nonsensical, as plain messages.

    Returned rather than raised so the UI can show all problems at once and
    keep the Generate button disabled until the grid is coherent.

    `storage_classes` maps a cook slot's id to the `Recipe.storage_class` of
    the dish actually sitting on it, and is passed straight to
    `storage_safety_errors` — see there for what omitting it means.
    """
    errors: List[str] = []
    by_id = spec.by_id()
    cuisines = config["cuisines"]
    cuisine_meal_types = config["cuisine_meal_types"]

    for slot in spec.slots:
        label = f"{slot.day} {slot.meal_type}"

        if slot.mode not in MODES:
            errors.append(f"{label}: unknown mode '{slot.mode}'.")
            continue

        if slot.mode == MODE_LEFTOVER:
            if not slot.source:
                errors.append(f"{label}: set to leftover but no source meal chosen.")
            elif slot.link_origin == LINK_ORIGIN_FREEZER:
                # design-04 §4's one narrow exemption: a freezer draw's source
                # names a data/freezer.json lot, which predates this week by
                # definition and is never one of its slot ids. Every check
                # below — grid membership, same-meal-type-or-dinner, forward-
                # only ordering — assumes an ordinary leftover and would
                # reject a genuine freezer draw as malformed. Whether the
                # named lot actually exists and has enough left is
                # `resolve_freezer_draws`' question, not the grid's.
                pass
            else:
                source = by_id.get(slot.source)
                if source is None:
                    errors.append(f"{label}: source '{slot.source}' is not a slot in this week.")
                elif source.mode != MODE_COOK:
                    errors.append(
                        f"{label}: source '{slot_label(slot.source)}' isn't a cooked meal — "
                        "leftovers can only come from a slot set to cook."
                    )
                elif leftover_meal_type_error(source.meal_type, slot.meal_type):
                    errors.append(f"{label}: {leftover_meal_type_error(source.meal_type, slot.meal_type)}")
                elif spec.day_index(source.day) > spec.day_index(slot.day):
                    errors.append(
                        f"{label}: eats leftovers from {source.day}, which is later in the "
                        "week — leftovers can only come from an earlier or same day."
                    )
                elif source.id == slot.id:
                    errors.append(f"{label}: cannot be its own leftover source.")

        if slot.mode == MODE_COOK and slot.style:
            allowed = styles_for(config, slot.meal_type)
            if slot.style not in allowed:
                errors.append(
                    f"{label}: style '{humanize(slot.style)}' isn't a {slot.meal_type} style. "
                    f"Valid: {', '.join(humanize(key) for key in allowed) or 'none configured'}."
                )

        if slot.mode == MODE_COOK and slot.cuisine:
            if slot.cuisine not in cuisines:
                errors.append(f"{label}: cuisine '{humanize(slot.cuisine)}' is not in config cuisines.")
            elif slot.meal_type not in cuisine_meal_types:
                errors.append(
                    f"{label}: cuisine themes only apply to "
                    f"{', '.join(cuisine_meal_types)} — clear the cuisine here."
                )

        if slot.mode != MODE_COOK and slot.extra_portions:
            errors.append(f"{label}: extra portions only apply to a slot set to cook.")

        if slot.recipe_id and slot.mode != MODE_COOK:
            errors.append(
                f"{label}: a pinned recipe only applies to a slot set to cook — "
                "a leftover eats its source's recipe and a skip cooks nothing."
            )

        if slot.skip_estimate is not None:
            if slot.mode != MODE_SKIP:
                errors.append(
                    f"{label}: an estimate only applies to a skipped meal — "
                    "a cooked or leftover slot's macros come from its recipe."
                )
            else:
                missing = [key for key in MACRO_KEYS if key not in slot.skip_estimate]
                if missing:
                    # All four or none: a partial estimate would be subtracted
                    # from some macros and not others, leaving the day's
                    # budget internally inconsistent (calories ~= 4p + 4c + 9f)
                    # in exactly the way split_targets and the response
                    # validator both assume it never is.
                    errors.append(
                        f"{label}: estimate is missing {', '.join(missing)} — "
                        "give all four macros or none."
                    )
                negative = [
                    key
                    for key in MACRO_KEYS
                    if key in slot.skip_estimate and float(slot.skip_estimate[key]) < 0
                ]
                if negative:
                    errors.append(
                        f"{label}: estimate has negative {', '.join(negative)}."
                    )

    errors.extend(storage_safety_errors(spec, config, storage_classes))

    if not spec.cook_slots():
        errors.append("Nothing to cook: at least one slot must be set to cook.")

    return errors


# The batch-prep session cooks the day *before* `spec.days[0]` — the eighth
# column `ui_cards.prep_day_column` draws — so a batch it produces is already
# a day out of the fridge by the time day 0 eats it. Day index -1 is that day.
# It exists so "how old is this food" is counted from the pan rather than from
# the grid slot the recipe happens to be parked on: an anchor is always day 0
# (see `apply_batch_selections`), so measuring from its own slot
# is short by exactly one on every prep-session batch.
PREP_DAY_INDEX = -1


def cook_day_index(spec: WeekSpec, day: str, prepped_ahead: bool = False) -> int:
    """The day index a cook actually happened on, which is not always its slot's.

    `prepped_ahead` is the batch-prep case above; everything else is cooked on
    the day its slot sits on.
    """
    return PREP_DAY_INDEX if prepped_ahead else spec.day_index(day)


def prep_day_batch_slot_ids(config: Optional[dict]) -> Set[str]:
    """Slot ids this run cooks on prep day rather than on their own grid day.

    The generation-side answer to the question `is_prepped_ahead` answers
    afterwards, and it has to be a different lookup for a plain ordering
    reason: `generate_sunday_prep_session` runs *after* every cook event is
    built, so `candidate_slot_ids` does not exist yet when `build_cook_event`
    needs to know how old the food will be. The anchors do — they were chosen
    by `apply_batch_selections` and merged into `config` before
    the first call — and they are the same two slots the session goes on to
    stamp.

    Empty for a CLI run, whose legacy `enable_sunday_prep` path names no
    anchor in advance (`build_batch_roast_rule` lets the model pick its own
    day, from the ones that have the hours), so those weeks count spans
    exactly as they always have.

    It lives here rather than in `planner.py` (where it started, and from
    which it is still re-exported so every existing caller and every mention
    of it in CLAUDE.md still resolves) because `storage_safety_errors` needs
    it and `week` cannot import `planner`. Beside `PREP_DAY_INDEX` and
    `cook_day_index` is where it belonged anyway: it reads two config keys and
    answers a question about the grid, with no model, recipe or repository in
    sight.

    **A declarative `week_shape` batch (Task 1.2c) needs no third config key
    beside the legacy two.** `apply_week_shape` never merges its anchors back
    into `config` — a `cook_on: "prep_day"` batch is already answerable
    straight from `week_shape` itself: its anchor is `serves[0]`'s slot,
    exactly as `apply_week_shape` computes it. So this folds `config["week_shape"]`'s
    own `batches` in too, the same "anchor data" `week_shape_errors` already
    reads at load time to check the very same `cook_on` field — one config
    dict, one place that says what "prepped ahead" means, however many
    batches a shape declares (`long_cook_anchor`/`bulk_prep_anchor` cap the
    legacy toggle path at exactly two).
    """
    config = config or {}
    ids = {
        value
        for value in (config.get("long_cook_anchor"), config.get("bulk_prep_anchor"))
        if value
    }
    for raw_batch in (config.get("week_shape") or {}).get("batches") or []:
        if raw_batch.get("cook_on") != "prep_day":
            continue
        serves = raw_batch.get("serves") or []
        meal_type = raw_batch.get("meal_type")
        if serves and meal_type:
            ids.add(slot_id(serves[0], meal_type))
    return ids


def span_days(spec: WeekSpec, cook_id: str, prepped_ahead: bool = False) -> int:
    """Days between cooking and the last meal that eats it.

    Public because editing the week changes it: re-pointing a leftover moves
    the last meal a batch has to survive to, which is what decides whether its
    storage note says "refrigerate" or "freeze the rest".

    `prepped_ahead` says the food came out of the batch-prep session rather
    than out of its own grid day — see `PREP_DAY_INDEX`. It defaults to False
    so `validate_week`'s backstop keeps measuring what it always has: that
    check bounds a hand-built leftover chain, and the prep-session batches are
    bounded on the planning side by `apply_batch_selections`' own
    `max_day_index` instead.
    """
    claims = eaten_on(spec).get(cook_id, [])
    if not claims:
        return 0
    cook_index = cook_day_index(spec, parse_slot_id(cook_id)[0], prepped_ahead)
    last_index = max(spec.day_index(parse_slot_id(value)[0]) for value in claims)
    return last_index - cook_index


class ShoppingWindow(BaseModel):
    """One shopping trip: the day you shop and the cook days it has to cover."""

    shop_day: str
    days: List[str]
    shop_ahead: bool = Field(
        default=False,
        description="True when this window's food must be bought before the week starts",
    )

    @property
    def label(self) -> str:
        span = self.days[0] if len(self.days) == 1 else f"{self.days[0]}–{self.days[-1]}"
        if self.shop_ahead:
            return f"Before {self.days[0]} · covers {span}"
        return f"Shop {self.shop_day} · covers {span}"


def shopping_windows(days: List[str], shop_days: List[str]) -> List[ShoppingWindow]:
    """Partition the week at the days you actually shop.

    Day 1 is always an implicit boundary: if you don't shop on it, the days
    before your first real trip still need buying, and that leading window is
    flagged shop_ahead so the UI can say "buy this before the week starts"
    rather than silently attaching it to the wrong trip.
    """
    if not days:
        return []

    indices = sorted({days.index(day) for day in shop_days if day in days} | {0})
    windows = []
    for position, start in enumerate(indices):
        end = indices[position + 1] if position + 1 < len(indices) else len(days)
        span = days[start:end]
        windows.append(
            ShoppingWindow(
                shop_day=days[start],
                days=span,
                shop_ahead=days[start] not in shop_days,
            )
        )
    return windows
