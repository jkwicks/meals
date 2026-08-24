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
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

MODE_COOK = "cook"
MODE_LEFTOVER = "leftover"
MODE_SKIP = "skip"

# Who made a leftover link — `SlotSpec.link_origin`. The three differ in what
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
LINK_ORIGIN_USER = "user"
LINK_ORIGIN_LOCATION = "location"
LINK_ORIGIN_BATCH = "batch"
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
DEFAULT_INVENTORY_RULES = {
    # Cooked food keeps ~3-4 days refrigerated, so a leftover eaten 4+ days
    # after its cook day is at the edge — flagged in the grid and reflected
    # in the recipe's storage note rather than silently planned.
    "fridge_safe_days": 4,
    "perishable_day_gap": 3,
}

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
        description="slot_id of the cook slot this eats leftovers of (mode=leftover)",
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
            "of generating something new (mode=cook). Set by "
            "planner.select_favorite_assignments before a run; the slot is "
            "still a cook, so portions derive and shopping picks it up "
            "exactly as for a generated recipe."
        ),
    )
    link_origin: str = Field(
        default=LINK_ORIGIN_USER,
        description=(
            "Who made this leftover link — one of LINK_ORIGIN_USER / "
            "_LOCATION / _BATCH (see their definitions for what each permits). "
            "Meaningless on a slot that isn't MODE_LEFTOVER. Defaults to "
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
        return True
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

    `max_span_days` (`inventory_rules.fridge_safe_days`, threaded in by
    `ui_generation.apply_batch_selections`) stops the walk once it is that
    many days past the anchor. **This is prevention, not validation**: cooked
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
    Day index `i` is `i + 1` days after prep, so a `fridge_safe_days` of N
    means `max_day_index = N - 1`; `ui_generation.apply_batch_selections` does
    that arithmetic. None leaves the anchor-relative bound as the only one,
    which is right for any caller whose batch really is cooked on its own day.

    `exclude_target_days` names days that may not *receive* a link; the
    anchor itself may still fall on one. `ui_generation.apply_batch_selections`
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


def pin_recipe(spec: WeekSpec, target_id: str, recipe_id: Optional[str]) -> WeekSpec:
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
            update={"recipe_id": recipe_id, "style": None, "cuisine": None}
            if recipe_id
            else {"recipe_id": None}
        )
        if slot.id == target_id and slot.mode == MODE_COOK
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def clear_recipe_pins(spec: WeekSpec) -> WeekSpec:
    """Drop every pinned recipe, so the next run re-picks from the catalog.

    Called unconditionally by `ui_generation.generate_week` alongside
    `clear_styles`/`clear_cuisines`, and for exactly the same reason those two
    are: once a week has been generated its slots carry whatever the *last*
    run resolved, and `select_favorite_assignments` only ever fills an empty
    slot. Without this, week two would re-pin week one's favourites forever
    and the rotation window would never advance.
    """
    updated = [
        slot.model_copy(update={"recipe_id": None}) if slot.mode == MODE_COOK else slot
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


def validate_week(spec: WeekSpec, config: dict) -> List[str]:
    """Everything that would make generation nonsensical, as plain messages.

    Returned rather than raised so the UI can show all problems at once and
    keep the Generate button disabled until the grid is coherent.
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

    # Food safety, as a backstop. `spread_batch` already refuses to plan a
    # batch past this bound (`max_span_days`), so the toggles can't produce a
    # breach — but a hand-built chain of "Link to next lunch" clicks never
    # goes through `spread_batch`, and neither does an imported or hand-edited
    # week_plan.json. Cooked food keeps 3-4 days refrigerated; a leftover
    # planned beyond that is a meal you would have to throw away.
    fridge_safe_days = (config.get("inventory_rules") or DEFAULT_INVENTORY_RULES).get(
        "fridge_safe_days", DEFAULT_INVENTORY_RULES["fridge_safe_days"]
    )
    for cook in spec.cook_slots():
        span = span_days(spec, cook.id)
        if span > fridge_safe_days:
            last = max(
                (value for value in eaten_on(spec).get(cook.id, [])),
                key=lambda value: spec.day_index(parse_slot_id(value)[0]),
            )
            errors.append(
                f"{cook.day} {cook.meal_type}: cooked {span} days before "
                f"{slot_label(last)} eats it, past the {fridge_safe_days}-day "
                "fridge limit — re-point that meal to a later cook."
            )

    if not spec.cook_slots():
        errors.append("Nothing to cook: at least one slot must be set to cook.")

    return errors


def span_days(spec: WeekSpec, cook_id: str) -> int:
    """Days between cooking and the last meal that eats it.

    Public because editing the week changes it: re-pointing a leftover moves
    the last meal a batch has to survive to, which is what decides whether its
    storage note says "refrigerate" or "freeze the rest".
    """
    claims = eaten_on(spec).get(cook_id, [])
    if not claims:
        return 0
    cook_index = spec.day_index(parse_slot_id(cook_id)[0])
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
