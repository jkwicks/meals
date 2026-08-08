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

from typing import Dict, List, Optional

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
PERISHABLE_DAY_GAP = 3

# Cooked food keeps ~3-4 days refrigerated, so a leftover eaten 4+ days after
# its cook day is at the edge — flagged in the grid and reflected in the
# recipe's storage note rather than silently planned.
FRIDGE_SAFE_DAYS = 4


def humanize(value: Optional[str]) -> str:
    return value.replace("_", " ") if value else ""


def week_days(config: dict, week_start: Optional[str] = None) -> List[str]:
    """The week in cooking order, rotated so it begins on week_start.

    Generation walks this order, and leftovers may only point backwards
    along it, so "day 1" is whatever the user considers the start of their
    shopping week rather than a hardcoded Monday.
    """
    days = list(config["weekly_schedule"].keys())
    start = week_start or config.get("week_start_day")
    if start in days:
        index = days.index(start)
        days = days[index:] + days[:index]
    return days


def meal_types(config: dict) -> List[str]:
    return config.get("meal_types", DEFAULT_MEAL_TYPES)


def styles_for(config: dict, meal_type: str) -> Dict[str, str]:
    """style key -> prose description handed to the model."""
    return config.get("meal_styles", {}).get(meal_type, {})


def all_style_keys(config: dict) -> List[str]:
    """Union of every meal type's styles, for the grid's single Style column.

    st.data_editor's dropdown options are per-column, not per-row, so the
    column offers every style and validate_week() rejects ones that don't
    belong to the row's meal type.
    """
    keys: List[str] = []
    for meal_type in meal_types(config):
        for key in styles_for(config, meal_type):
            if key not in keys:
                keys.append(key)
    return keys


def slot_id(day: str, meal_type: str) -> str:
    return f"{day}:{meal_type}"


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
    defaults = config.get("week_defaults", {})
    servings = servings_per_meal or config.get("serving_rules", {}).get(
        "servings_per_meal", DEFAULT_SERVINGS_PER_MEAL
    )

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
    cuisines = config.get("cuisines", [])
    cuisine_meal_types = config.get("cuisine_meal_types") or meal_types(config)

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
                        f"{label}: source '{humanize(slot.source)}' isn't a cooked meal — "
                        "leftovers can only come from a slot set to cook."
                    )
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


def week_warnings(spec: WeekSpec) -> List[str]:
    """Non-blocking notes — things that are legal but probably not intended."""
    warnings: List[str] = []
    counts = claim_counts(spec)
    by_id = spec.by_id()

    for cook_id, claims in counts.items():
        slot = by_id[cook_id]
        if claims >= 5:
            warnings.append(
                f"{slot.day} {slot.meal_type} feeds {claims} meals — that's a lot of "
                "repeats of one recipe, and it has to keep for "
                f"{_span_days(spec, cook_id)} days."
            )
        span = _span_days(spec, cook_id)
        if span >= FRIDGE_SAFE_DAYS:
            warnings.append(
                f"{slot.day} {slot.meal_type} is eaten up to {span} days after cooking — "
                "at or past safe fridge storage, so plan to freeze the later portions."
            )

    skipped = [slot for slot in spec.slots if slot.mode == MODE_SKIP]
    by_day: Dict[str, int] = {}
    for slot in skipped:
        by_day[slot.day] = by_day.get(slot.day, 0) + 1
    for day, count in by_day.items():
        if count >= 3:
            warnings.append(f"{day} has {count} skipped meals — its macro targets will be hard to hit.")

    return warnings


def _span_days(spec: WeekSpec, cook_id: str) -> int:
    """Days between cooking and the last meal that eats it."""
    claims = eaten_on(spec).get(cook_id, [])
    if not claims:
        return 0
    cook_index = spec.day_index(cook_id.split(":")[0])
    last_index = max(spec.day_index(value.split(":")[0]) for value in claims)
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


def window_for_day(windows: List[ShoppingWindow], day: str) -> Optional[ShoppingWindow]:
    for window in windows:
        if day in window.days:
            return window
    return None
