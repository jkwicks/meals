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
MODES = [MODE_COOK, MODE_LEFTOVER, MODE_SKIP]

# Sentinel used in the UI dropdowns for "let the planner decide". Stored as
# None on the model so callers never have to special-case the string.
AUTO = "auto"

DEFAULT_MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"]
DEFAULT_SERVINGS_PER_MEAL = 2

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
    """A fresh grid with every slot on config's per-meal-type default mode."""
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
    return WeekSpec(days=days, servings_per_meal=servings, slots=slots)


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

    Same escape hatch as `PlannerState.shuffle_styles`, scoped to cuisine
    only: once a week has been generated, every slot carries the concrete
    cuisine that run resolved, and `resolve_auto_choices`/`pick_cuisine_blocks`
    only ever pick a fresh one when a slot is empty (planner.py) — otherwise
    every later run repeats the exact same per-day cuisine forever. Called
    only when the generate popup's cuisine picker narrows `config["cuisines"]`
    for this run (`ui_generation.generate_week`): without it, a slot carrying
    a concrete cuisine from a wider list a previous run picked from would fail
    `validate_week`'s "cuisine is not in config cuisines" check the moment the
    list no longer contains it, rather than simply being re-picked from the
    narrower one.
    """
    updated = [
        slot.model_copy(update={"cuisine": None}) if slot.mode == MODE_COOK else slot
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


def link_leftover(spec: WeekSpec, target_id: str, source_id: str) -> WeekSpec:
    """A copy of `spec` with `target_id` set to eat `source_id`'s leftovers.

    Call `leftover_link_error` first — this applies the edit unconditionally.
    `extra_portions` is cleared because it only means anything on a cook slot.
    """
    updated = [
        slot.model_copy(
            update={"mode": MODE_LEFTOVER, "source": source_id, "extra_portions": 0}
        )
        if slot.id == target_id
        else slot
        for slot in spec.slots
    ]
    return spec.model_copy(update={"slots": updated})


def spread_batch(
    spec: WeekSpec,
    anchor_meal_type: str,
    target_servings: int,
    exclude_days: Optional[Set[str]] = None,
    prefer_days: Optional[List[str]] = None,
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
    grid this feature is for. `prefer_days`, when non-empty, narrows the pool
    first — the long-cook caller passes the week's weekend days; bulk-prep
    passes None. Within the (possibly narrowed) pool, the earliest day in
    `spec.days` order wins — deterministic, and it leaves the most week left
    to spread across.

    Spreading: starting the day after the anchor, walks the rest of
    `spec.days` in order, trying that day's `anchor_meal_type` slot then its
    "lunch" slot — the only two links `leftover_meal_type_error` allows out of
    a dinner anchor — at most one link per day. Links until the anchor's total
    claims (existing plus new, via `claim_counts`) reach `target_claims` or
    the week runs out, whichever first: a batch that can only reach fewer
    days still generates, just smaller, and an anchor that already had enough
    claims before this call adds none.

    `target_claims` = `max(2, min(3, ceil(target_servings /
    servings_per_meal)))` — at least 2 (a "batch" of one day isn't one) and
    at most 3, so a small household's arithmetic doesn't spread one dish
    across half the week.

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

    candidates = [
        slot
        for slot in spec.cook_slots()
        if slot.meal_type == anchor_meal_type and slot.day not in exclude_days
    ]
    if not candidates:
        return spec, None

    pool = [slot for slot in candidates if slot.day in (prefer_days or [])] or candidates
    anchor = min(pool, key=lambda slot: spec.day_index(slot.day))

    target_claims = max(2, min(3, math.ceil(target_servings / spec.servings_per_meal)))
    existing_claims = claim_counts(spec).get(anchor.id, 1)
    additional_links_needed = max(0, target_claims - existing_claims)

    linked = 0
    for day in spec.days[spec.day_index(anchor.day) + 1 :]:
        if linked >= additional_links_needed:
            break
        by_id = spec.by_id()
        for meal_type in (anchor_meal_type, "lunch"):
            target_id = slot_id(day, meal_type)
            target = by_id.get(target_id)
            if target is None or target.mode != MODE_COOK:
                continue
            if leftover_link_error(spec, target_id, anchor.id):
                continue
            spec = link_leftover(spec, target_id, anchor.id)
            linked += 1
            break

    if linked == 0 and existing_claims < target_claims:
        return spec, None

    return spec, anchor.id


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
