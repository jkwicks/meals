import argparse
import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import instructor
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from repository import (
    DEFAULT_CONFIG_FILE,
    DEFAULT_HISTORY_FILE,
    DEFAULT_WEEK_PLAN_FILE,
    LocalJSONRepository,
    PlanRepository,
    run_sync,
)
from shopping import (
    aggregate_cook_events,
    format_shopping_list_markdown,
    format_shopping_list_text,
)
from week import (
    FRIDGE_SAFE_DAYS,
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    ShoppingWindow,
    SlotSpec,
    WeekSpec,
    default_week_spec,
    eaten_on,
    humanize,
    meal_types,
    portions_for,
    shopping_windows,
    styles_for,
    validate_week,
)

load_dotenv()

DEFAULT_ALLOWED_NOVA_GROUPS = [1, 2, 3]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
# Where the local files live is repository.py's business now; these names are
# kept only for the CLI's help text and log messages.
WEEK_PLAN_CACHE_FILE = DEFAULT_WEEK_PLAN_FILE
LOG_FILE = "meals.log"

logger = logging.getLogger("meals")


def configure_logging(log_file: str = LOG_FILE) -> None:
    """Log per-day generation timing/token usage to a file for diagnosing slow days.

    Latency on a free OpenRouter route is highly variable (see CLAUDE.md) and
    the two known failure modes — a hung/throttled request and a reasoning
    model burning its token budget on hidden tokens — both show up in
    completion_tokens_details, not in anything the CLI prints today.
    """
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
MEAL_HISTORY_FILE = DEFAULT_HISTORY_FILE
HISTORY_MAX_ENTRIES = 21
PROTEIN_LOOKBACK_ENTRIES = 3
# How many recent main proteins to name in the prompt. Long enough to stop a
# week of chicken, short enough that a 7-day plan doesn't end up banning
# everything the model knows by Friday.
PROTEIN_AVOID_WINDOW = 6
FREE_MODEL_MAX_TOKENS = 8000
PAID_MODEL_MAX_TOKENS = 16000

MACRO_KEYS = ("calories", "protein_g", "net_carbs_g", "fat_g")

# Share of the day each meal type gets when splitting targets across slots.
# Only the ratios matter — they're normalised over whichever slots are
# actually being cooked, so a day with no snack redistributes its share.
DEFAULT_MEAL_WEIGHTS = {"breakfast": 0.25, "lunch": 0.30, "dinner": 0.35, "snack": 0.10}

# Models compose plausible meals but size them badly, so portions are corrected
# after the fact by scaling every quantity linearly. The clamp stops a trim
# producing an absurd portion (a 30g breakfast, a 900g steak).
PORTION_TRIM_LIMITS = (0.6, 1.6)
PORTION_TRIM_DEADBAND = 0.03


def is_free_model(model: str) -> bool:
    return model.endswith(":free")


def derive_fat_g(calories: float, protein_g: float, net_carbs_g: float) -> float:
    """Fat is whatever energy is left once protein and carbs are paid for.

    The one place this arithmetic lives, so a per-meal override and a whole-day
    target are derived by the identical rule.
    """
    return max(0, (calories - (protein_g * 4 + net_carbs_g * 4)) / 9)


def calculate_daily_targets(day_of_week: str, config: dict) -> dict:
    weekly_schedule = config["weekly_schedule"]
    if day_of_week not in weekly_schedule:
        raise ValueError(
            f"'{day_of_week}' not found in weekly_schedule. "
            f"Valid days: {list(weekly_schedule.keys())}"
        )

    day_targets = weekly_schedule[day_of_week]
    calories = day_targets["calories"]
    protein_g = day_targets["protein_g"]
    net_carbs_g = day_targets["net_carbs_g"]

    fat_g = derive_fat_g(calories, protein_g, net_carbs_g)

    return {
        "day_of_week": day_of_week,
        "calories": calories,
        "protein_g": protein_g,
        "net_carbs_g": net_carbs_g,
        "fat_g": round(fat_g, 1),
    }


def week_targets(spec: WeekSpec, config: dict) -> Dict[str, dict]:
    return {day: calculate_daily_targets(day, config) for day in spec.days}


def meal_overrides_for(day: str, config: dict) -> Dict[str, dict]:
    """Per-meal budgets pinned by config for `day`, keyed by meal_type.

    `weekly_schedule[day].meal_overrides` is how you say "Saturday dinner is
    900 kcal whatever else that day looks like" — a fixed budget for one meal
    that the weight-based split must work around rather than compute.

    Written the same way a daily target is (calories, protein_g, net_carbs_g);
    fat_g may be given explicitly but is otherwise derived by the same rule, so
    an override that names only calories puts every remaining calorie in fat.
    Malformed entries are dropped with a warning to meals.log rather than
    raised — a typo in one meal must not cost the whole day's generation.
    """
    raw = (config.get("weekly_schedule", {}).get(day) or {}).get("meal_overrides") or {}
    known = meal_types(config)

    resolved: Dict[str, dict] = {}
    for meal_type, override in raw.items():
        if meal_type not in known:
            logger.warning(
                "%s: ignoring meal_override for unknown meal type '%s' (known: %s)",
                day, meal_type, ", ".join(known),
            )
            continue
        if not isinstance(override, dict) or "calories" not in override:
            logger.warning(
                "%s %s: ignoring meal_override without a calories target", day, meal_type
            )
            continue
        calories = float(override["calories"])
        protein_g = float(override.get("protein_g", 0))
        net_carbs_g = float(override.get("net_carbs_g", 0))
        resolved[meal_type] = {
            "calories": calories,
            "protein_g": protein_g,
            "net_carbs_g": net_carbs_g,
            "fat_g": float(
                override.get("fat_g", derive_fat_g(calories, protein_g, net_carbs_g))
            ),
        }
    return resolved


# --------------------------------------------------------------------------
# History-aware rotation
# --------------------------------------------------------------------------


def next_choice(options: List[str], recent: List[str]) -> Optional[str]:
    """Strict least-recently-used pick from `options`.

    `recent` is oldest-to-newest usage. Never-used options rank before used
    ones, ties break on config order, so repeated calls walk the whole list
    before repeating anything. (A "not used in the last N" rule looks similar
    but starves the tail of the list: with 5 styles and N=3 it just cycles
    through the first 4 forever.)
    """
    if not options:
        return None
    last_seen = {option: -1 for option in options}
    for index, value in enumerate(recent):
        if value in last_seen:
            last_seen[value] = index
    return min(options, key=lambda option: (last_seen[option], options.index(option)))


def history_values(history: List[dict], key: str) -> List[str]:
    """Flat oldest-to-newest list of a scalar history field."""
    return [entry[key] for entry in history if entry.get(key)]


def history_styles(history: List[dict], meal_type: str) -> List[str]:
    values = []
    for entry in history:
        style = (entry.get("styles") or {}).get(meal_type)
        if style:
            values.append(style)
    return values


def recent_main_proteins(
    history: List[dict], lookback_entries: int = PROTEIN_LOOKBACK_ENTRIES
) -> List[str]:
    """Main proteins across the last few days, de-duplicated, so the model can
    be told not to repeat them."""
    seen = set()
    proteins = []
    for entry in history[-lookback_entries:]:
        for protein in entry.get("main_proteins", []):
            if protein not in seen:
                seen.add(protein)
                proteins.append(protein)
    return proteins


def resolve_auto_choices(spec: WeekSpec, config: dict, history: List[dict]) -> WeekSpec:
    """Fill in every `auto` style and cuisine with a concrete choice.

    Runs before any API call so the entire week is deterministic and
    previewable: rotation continues from meal_history.json and then keeps
    rotating *within* the week, so seven auto breakfasts don't all resolve to
    whatever happens to be first in the config list.
    """
    cuisines = config.get("cuisines", [])
    cuisine_meal_types = config.get("cuisine_meal_types") or meal_types(config)

    recent_cuisines = history_values(history, "cuisine")
    recent_styles = {
        meal_type: history_styles(history, meal_type) for meal_type in meal_types(config)
    }

    resolved: List[SlotSpec] = []
    for slot in spec.slots:
        if slot.mode != MODE_COOK:
            resolved.append(slot)
            continue

        style = slot.style
        if not style:
            options = list(styles_for(config, slot.meal_type).keys())
            style = next_choice(options, recent_styles.get(slot.meal_type, []))
        if style:
            recent_styles.setdefault(slot.meal_type, []).append(style)

        cuisine = slot.cuisine
        if not cuisine and slot.meal_type in cuisine_meal_types:
            cuisine = next_choice(cuisines, recent_cuisines)
        if cuisine:
            recent_cuisines.append(cuisine)

        resolved.append(slot.model_copy(update={"style": style, "cuisine": cuisine}))

    return spec.model_copy(update={"slots": resolved})


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


class Ingredient(BaseModel):
    name: str = Field(..., description="Ingredient name")
    quantity_g: float = Field(..., gt=0, description="Quantity in grams")
    nova_group: int = Field(
        ..., ge=1, le=4, description="NOVA food processing classification (1-4)"
    )
    calories: float = Field(..., ge=0)
    protein_g: float = Field(..., ge=0)
    net_carbs_g: float = Field(..., ge=0)
    fat_g: float = Field(..., ge=0)

    @field_validator("nova_group")
    @classmethod
    def enforce_allowed_nova_group(cls, v: int, info: ValidationInfo) -> int:
        allowed = DEFAULT_ALLOWED_NOVA_GROUPS
        if info.context and "config" in info.context:
            allowed = info.context["config"]["dietary_rules"]["allowed_nova_groups"]
        if v not in allowed:
            raise ValueError(
                f"nova_group {v} is not allowed (allowed groups: {allowed}); "
                "ultra-processed (Group 4) ingredients are rejected"
            )
        return v

    @field_validator("name")
    @classmethod
    def reject_banned_ingredients(cls, v: str, info: ValidationInfo) -> str:
        banned = []
        if info.context and "config" in info.context:
            banned = info.context["config"]["dietary_rules"]["banned_ingredients"]
        name_lower = v.lower()
        for banned_item in banned:
            if banned_item.lower() in name_lower:
                raise ValueError(
                    f"ingredient '{v}' contains banned ingredient '{banned_item}'"
                )
        return v


class Recipe(BaseModel):
    name: str = Field(..., description="Recipe name")
    meal_type: str = Field(..., description="breakfast, lunch, dinner, or snack")
    ingredients: List[Ingredient]
    instructions: List[str] = Field(..., description="Ordered preparation steps")
    prep_time_minutes: int = Field(..., ge=0)
    servings: int = Field(
        default=1,
        ge=1,
        description=(
            "Total portions this recipe yields. Left at the default of 1 by the "
            "model — Python overwrites it with the portion count derived from "
            "how many slots eat this cook."
        ),
    )
    prep_notes: Optional[str] = Field(
        default=None,
        description="Storage/reheating notes. Set by Python for multi-meal cooks.",
    )


class DayRecipes(BaseModel):
    """The model's response for a single day: one recipe per cook slot."""

    recipes: List[Recipe]

    @model_validator(mode="after")
    def reject_untrimmable_macro_miss(self, info: ValidationInfo) -> "DayRecipes":
        """Bounce a response too far off budget for the portion trim to rescue.

        The threshold is derived from PORTION_TRIM_LIMITS rather than picked:
        anything the trim can scale onto its budget is accepted and corrected
        silently, and only a response needing a factor outside the clamp is
        rejected so instructor can hand the model its own numbers back and
        retry. Coupling them this way keeps retries rare — which matters,
        because exhausting max_retries fails the whole week, and a free model
        that reliably missed by a fixed margin would do exactly that.

        The characteristic failure this catches: when some meals are already
        covered by leftovers, the model ignores the reduced target and writes
        a full day anyway.
        """
        budget = (info.context or {}).get("day_budget")
        if not budget or not self.recipes:
            return self

        target = budget.get("calories", 0)
        total = sum(
            ingredient.calories
            for recipe in self.recipes
            for ingredient in recipe.ingredients
        )
        if target <= 0 or total <= 0:
            return self

        factor = target / total
        low, high = PORTION_TRIM_LIMITS
        if not low <= factor <= high:
            raise ValueError(
                f"the recipes total {total:.0f} kcal per serving but the budget for "
                f"these meals is {target:.0f} kcal ({(total - target) / target:+.0%}). "
                "Resize the portions to match each meal's stated budget — do not "
                "add or remove meals, and remember any meals already listed as "
                "fixed leftovers are NOT yours to generate."
            )
        return self


class CookEvent(BaseModel):
    """One recipe, cooked once, eaten by one or more slots.

    `recipe.ingredients` hold the full scaled batch quantities (portions x the
    model's single-serving amounts), matching what you actually buy and cook.
    """

    slot_id: str
    day: str
    meal_type: str
    portions: int
    style: Optional[str] = None
    cuisine: Optional[str] = None
    eaten_by: List[str] = Field(default_factory=list)
    recipe: Recipe


class WeekPlan(BaseModel):
    days: List[str]
    servings_per_meal: int
    generated_at: str
    cook_events: List[CookEvent]
    slots: List[SlotSpec]
    targets: Dict[str, dict]
    failures: Dict[str, str] = Field(
        default_factory=dict,
        description="day -> error, for days whose generation failed outright",
    )

    def by_slot(self) -> Dict[str, CookEvent]:
        return {event.slot_id: event for event in self.cook_events}

    def events_on_days(self, days: List[str]) -> List[CookEvent]:
        day_set = set(days)
        return [event for event in self.cook_events if event.day in day_set]


# --------------------------------------------------------------------------
# Macro math (always Python, never the model)
# --------------------------------------------------------------------------


def compute_recipe_totals(recipe: Recipe) -> dict:
    totals = {key: 0.0 for key in MACRO_KEYS}
    for ingredient in recipe.ingredients:
        for key in MACRO_KEYS:
            totals[key] += getattr(ingredient, key)
    return totals


def per_serving_totals(recipe: Recipe) -> dict:
    servings = max(1, recipe.servings)
    return {key: value / servings for key, value in compute_recipe_totals(recipe).items()}


def round_quantity(grams: float) -> float:
    """Whole grams once there's enough of something to weigh out that way.

    Trimming portions by a fraction produces quantities like 393.8g, which is
    noise on a shopping list. Spices and oils keep a decimal because 2.4g of
    turmeric and 2g are meaningfully different amounts.
    """
    return max(0.1, round(grams) if grams >= 10 else round(grams, 1))


def resize_recipe(recipe: Recipe, factor: float) -> Recipe:
    """Multiply every ingredient quantity and its macros by `factor`."""
    return recipe.model_copy(
        update={
            "ingredients": [
                ingredient.model_copy(
                    update=dict(
                        {key: round(getattr(ingredient, key) * factor, 1) for key in MACRO_KEYS},
                        quantity_g=round_quantity(ingredient.quantity_g * factor),
                    )
                )
                for ingredient in recipe.ingredients
            ]
        }
    )


def fit_recipe_to_budget(recipe: Recipe, budget: dict) -> Tuple[Recipe, float]:
    """Resize one serving of a recipe so its calories land on its budget.

    Models pick sensible *ingredients* and implausible *amounts*, and every
    macro is linear in quantity, so a single scale factor fixes the portion
    without touching the dish. It cannot fix a bad macro ratio — a recipe with
    the right calories and the wrong protein split stays wrong, and shows up
    as a visible delta in the day summary rather than being papered over.
    """
    actual = compute_recipe_totals(recipe)["calories"]
    target = budget.get("calories", 0)
    if actual <= 0 or target <= 0:
        return recipe, 1.0

    factor = target / actual
    factor = min(max(factor, PORTION_TRIM_LIMITS[0]), PORTION_TRIM_LIMITS[1])
    if abs(factor - 1.0) < PORTION_TRIM_DEADBAND:
        return recipe, 1.0
    return resize_recipe(recipe, factor), factor


def scale_recipe(recipe: Recipe, portions: int, keeps_for_days: int) -> Recipe:
    """Scale a recipe from the model's single serving up to its full yield.

    The model reports one serving; the portion count comes from how many slots
    claim this cook (see week.portions_for), so this stays a plain linear
    multiply and the arithmetic never leaves Python.
    """
    scaled = resize_recipe(recipe, portions)

    prep_notes = recipe.prep_notes
    if portions > 1 and keeps_for_days > 0 and not prep_notes:
        storage = (
            "refrigerate in airtight containers"
            if keeps_for_days < FRIDGE_SAFE_DAYS
            else f"refrigerate what you'll eat within {FRIDGE_SAFE_DAYS} days and freeze the rest"
        )
        prep_notes = (
            f"Yields {portions} portions, eaten across {keeps_for_days} day(s). "
            f"Portion immediately, {storage}; reheat thoroughly before serving."
        )

    return scaled.model_copy(update={"servings": portions, "prep_notes": prep_notes})


def day_slot_macros(week_plan: WeekPlan, day: str) -> dict:
    """What one person actually eats on `day`, summed across their slots."""
    by_slot = week_plan.by_slot()
    totals = {key: 0.0 for key in MACRO_KEYS}
    for slot in week_plan.slots:
        if slot.day != day or slot.mode == MODE_SKIP:
            continue
        source_id = slot.id if slot.mode == MODE_COOK else slot.source
        event = by_slot.get(source_id)
        if event is None:
            continue
        serving = per_serving_totals(event.recipe)
        for key in MACRO_KEYS:
            totals[key] += serving[key]
    return totals


def day_multiplicity(spec: WeekSpec, day: str) -> Dict[str, int]:
    """How many times each of `day`'s own cooks is eaten on that same day.

    Almost always 1. It's >1 when a big lunch is also eaten at dinner, and the
    prompt has to say so or the model will aim at the wrong daily total.
    """
    counts = {slot.id: 1 for slot in spec.cook_slots_on(day)}
    for slot in spec.slots:
        if slot.day == day and slot.mode == MODE_LEFTOVER and slot.source in counts:
            counts[slot.source] += 1
    return counts


def carried_macros(
    spec: WeekSpec, day: str, events: Dict[str, CookEvent]
) -> Tuple[dict, List[str]]:
    """Macros already locked in for `day` by leftovers cooked on earlier days,
    plus human-readable descriptions of those meals for the prompt."""
    totals = {key: 0.0 for key in MACRO_KEYS}
    descriptions = []
    for slot in spec.slots:
        if slot.day != day or slot.mode != MODE_LEFTOVER or not slot.source:
            continue
        event = events.get(slot.source)
        if event is None:
            continue
        serving = per_serving_totals(event.recipe)
        for key in MACRO_KEYS:
            totals[key] += serving[key]
        descriptions.append(
            f"{slot.meal_type}: leftovers of \"{event.recipe.name}\" "
            f"(cooked {event.day}) — {serving['calories']:.0f} kcal, "
            f"{serving['protein_g']:.0f}g protein, {serving['net_carbs_g']:.0f}g net carbs, "
            f"{serving['fat_g']:.0f}g fat"
        )
    return totals, descriptions


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def build_client() -> instructor.Instructor:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or api_key == "your_openrouter_api_key_here":
        raise RuntimeError("OPENROUTER_API_KEY is not set. Add it to your .env file.")
    openai_client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key, timeout=120.0)
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


def split_targets(
    remaining: dict,
    cook_slots: List[SlotSpec],
    multiplicity: Dict[str, int],
    config: dict,
    overrides: Optional[Dict[str, dict]] = None,
) -> Dict[str, dict]:
    """Divide the day's remaining macros into a per-meal budget.

    Handing the model one daily number and letting it apportion the meals
    itself is where free models drift worst — they reach for a familiar "full
    day" shape and blow past a reduced target. Splitting in Python is the same
    rule as calculate_daily_targets: Python does the arithmetic, the model
    only fills in food.

    Two passes, in order:

    1. Any slot with an explicit override (see meal_overrides_for) is assigned
       that budget verbatim — it is a fixed number, not a starting point.
    2. What those pinned meals consume is subtracted from the day, and only the
       leftover is split across the remaining slots by normalised weight. So
       pinning breakfast at 600 kcal moves the other meals down, exactly as
       leftover macros already do; weights renormalise over the un-pinned slots
       alone, which is the same rule that already redistributes a skipped meal.

    A meal eaten more than once today contributes its macros that many times,
    so it consumes (or takes a share of) the day proportionally while its own
    recipe budget stays a single serving.
    """
    overrides = overrides or {}
    weights_config = config.get("meal_weights", DEFAULT_MEAL_WEIGHTS)

    budgets: Dict[str, dict] = {}
    pinned = {key: 0.0 for key in MACRO_KEYS}
    flexible: List[SlotSpec] = []
    for slot in cook_slots:
        override = overrides.get(slot.meal_type)
        if override is None:
            flexible.append(slot)
            continue
        budgets[slot.id] = {key: override[key] for key in MACRO_KEYS}
        eaten_today = multiplicity.get(slot.id, 1)
        for key in MACRO_KEYS:
            pinned[key] += override[key] * eaten_today

    if not flexible:
        return budgets

    left = {key: remaining[key] - pinned[key] for key in MACRO_KEYS}
    overspent = [key for key in MACRO_KEYS if left[key] < 0]
    if overspent:
        # Floored rather than raised: the day still generates, and a 0 budget
        # shows up as a visible shortfall in the day summary the same way an
        # orphaned leftover does.
        logger.warning(
            "meal_overrides for %s claim more %s than the day's target leaves — "
            "the un-overridden meals are floored at 0 for those macros",
            ", ".join(sorted(overrides)),
            ", ".join(overspent),
        )
        left = {key: max(0.0, left[key]) for key in MACRO_KEYS}

    base = {slot.id: weights_config.get(slot.meal_type, 0.25) or 0.25 for slot in flexible}
    total_weight = sum(base[slot.id] * multiplicity.get(slot.id, 1) for slot in flexible)
    if total_weight <= 0:
        budgets.update({slot.id: dict(left) for slot in flexible})
        return budgets

    budgets.update(
        {
            slot.id: {key: left[key] * base[slot.id] / total_weight for key in MACRO_KEYS}
            for slot in flexible
        }
    )
    return budgets


def inventory_instruction(config: dict) -> str:
    """Prompt line telling the model to build around food already in the house.

    `inventory_to_clear` is a plain list of things to use up ("400g chicken
    thighs", "half a bag of spinach"). It is a priority, never a constraint:
    the styles, cuisines and macro budgets still win, because a model told it
    *must* use an item will wedge it into a breakfast where it doesn't belong.

    Note these items are still costed as ordinary ingredients, so they appear
    on the shopping list — the list says what a recipe needs, not what you have
    yet to buy.
    """
    items = [
        str(item).strip()
        for item in config.get("inventory_to_clear", [])
        if str(item).strip()
    ]
    if not items:
        return ""
    return (
        "- We already have these at home and want them used up first — prefer "
        "them over buying more of the same kind of thing, and spread them "
        f"across the day's meals where they genuinely fit: {', '.join(items)}. "
        "Never force one into a meal it doesn't suit, and never break a meal's "
        "style, cuisine or macro budget to use one up.\n"
    )


def build_slot_brief(
    slot: SlotSpec, config: dict, times_eaten_today: int, budget: dict, pinned: bool = False
) -> str:
    """One line per meal the model has to invent: style, cuisine, macro budget."""
    parts = [f"- {slot.meal_type.upper()}"]
    style_description = styles_for(config, slot.meal_type).get(slot.style or "")
    if slot.style:
        parts.append(f"style: {humanize(slot.style)}")
        if style_description:
            parts.append(f"({style_description})")
    if slot.cuisine:
        parts.append(f"cuisine: {humanize(slot.cuisine)} — authentic flavours and technique")
    parts.append(
        f"budget (one serving): {budget['calories']:.0f} kcal, "
        f"{budget['protein_g']:.0f}g protein, {budget['net_carbs_g']:.0f}g net carbs, "
        f"{budget['fat_g']:.0f}g fat"
    )
    if pinned:
        parts.append("[fixed budget for this meal — the other meals absorb the rest of the day]")
    if times_eaten_today > 1:
        parts.append(f"[eaten {times_eaten_today}x today, budget already accounts for that]")
    return " | ".join(parts)


def generate_day(
    day: str,
    targets: dict,
    cook_slots: List[SlotSpec],
    config: dict,
    servings_per_meal: int,
    multiplicity: Dict[str, int],
    carried: dict,
    carried_descriptions: List[str],
    avoid_proteins: Optional[List[str]] = None,
    progress_note=None,
) -> Dict[str, Recipe]:
    """Generate one day's cooked recipes, returned keyed by meal_type.

    Only the slots set to cook are generated. Leftover slots' macros are
    subtracted from the day's target first, so the model is asked for the
    remaining gap rather than a full day it would then overshoot.
    """
    client = build_client()
    dietary_rules = config["dietary_rules"]

    remaining = {key: max(0.0, targets[key] - carried.get(key, 0.0)) for key in MACRO_KEYS}

    avoid_protein_instruction = (
        "- Avoid making any of these the primary protein again — they were used "
        f"recently: {', '.join(avoid_proteins)}.\n"
        if avoid_proteins
        else ""
    )
    leftovers_instruction = (
        "- The following meals on this day are ALREADY FIXED (leftovers of an "
        "earlier cook). Do NOT generate them; their macros are already "
        "subtracted from the targets below:\n"
        + "\n".join(f"  * {line}" for line in carried_descriptions)
        + "\n"
        if carried_descriptions
        else ""
    )
    batch_slots = [slot for slot in cook_slots if multiplicity.get(slot.id, 1) > 1]
    batch_instruction = (
        "- Some meals below are eaten more than once. Design those to portion "
        "and reheat well (a tray/pot dish rather than something that must be "
        "served immediately). Still give quantities for ONE serving; Python "
        "scales them to the full batch.\n"
        if batch_slots
        else ""
    )

    overrides = meal_overrides_for(day, config)
    budgets = split_targets(remaining, cook_slots, multiplicity, config, overrides)
    slot_briefs = "\n".join(
        build_slot_brief(
            slot,
            config,
            multiplicity.get(slot.id, 1),
            budgets[slot.id],
            pinned=slot.meal_type in overrides,
        )
        for slot in cook_slots
    )

    system_prompt = (
        f"You are a precision meal-planning assistant cooking for "
        f"{servings_per_meal} people. Generate exactly {len(cook_slots)} recipe(s) "
        f"for {day} — one for each meal listed by the user, matching its meal_type "
        "exactly. Recipes must be realistic, varied and non-repetitive.\n\n"
        "Rules:\n"
        "- Use metric units only (grams) for all ingredient quantities.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Never use Group 4 ultra-processed ingredients.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients'])}.\n"
        "- Respect each meal's requested style and cuisine exactly. Where a "
        "cuisine is given it applies to that meal only — the other meals must "
        "draw on different culinary traditions so the day isn't one cuisine "
        "end to end.\n"
        "- Prioritize nutrient-dense whole foods: vary the vegetables, herbs/"
        "spices and protein sources across the day and minimize ingredient "
        "overlap between meals, the way a registered dietitian would design a "
        "menu — not just whatever hits the numbers with the fewest ingredients.\n"
        f"{avoid_protein_instruction}"
        f"{inventory_instruction(config)}"
        f"{leftovers_instruction}"
        f"{batch_instruction}"
        "- Each meal below carries its OWN macro budget. Hit that meal's "
        "budget — not a typical portion size for that meal, and not a whole "
        "day's worth. The budgets are already calculated and already add up "
        "correctly; do not recompute or redistribute them.\n"
        "- All budgets are PER SERVING (one portion for one person). Report "
        "every ingredient's quantity_g and its calories/protein_g/net_carbs_g/"
        "fat_g for a SINGLE serving too. Do not multiply by the number of "
        "people or by any batch size — Python scales the recipe afterwards.\n"
        "- Leave servings and prep_notes at their schema defaults — Python "
        "fills those in.\n"
        "- Do not show your work, explain your reasoning, or narrate your "
        "process. Respond with the structured data only."
    )

    scope_note = (
        f"This is only PART of {day} — {len(carried_descriptions)} other meal(s) "
        "are already fixed and are NOT yours to generate. Do not try to make "
        "these recipes add up to a full day.\n\n"
        if carried_descriptions
        else ""
    )

    user_prompt = (
        f"{scope_note}"
        f"Generate exactly {len(cook_slots)} recipe(s) for {day}, one per line "
        f"below, each hitting its own budget:\n{slot_briefs}\n\n"
        "Together they must total approximately (per serving, already "
        "calculated — do not recompute):\n"
        f"- Calories: {remaining['calories']:.0f} kcal\n"
        f"- Protein: {remaining['protein_g']:.0f} g\n"
        f"- Net carbs: {remaining['net_carbs_g']:.0f} g\n"
        f"- Fat: {remaining['fat_g']:.0f} g\n"
    )

    model = config.get("openrouter_model", DEFAULT_MODEL)
    max_tokens = FREE_MODEL_MAX_TOKENS if is_free_model(model) else PAID_MODEL_MAX_TOKENS

    logger.info("%s: requesting %d recipe(s) from %s", day, len(cook_slots), model)
    started = time.monotonic()
    response, completion = client.chat.completions.create_with_completion(
        model=model,
        response_model=DayRecipes,
        max_retries=3,
        max_tokens=max_tokens,
        # OpenRouter's unified switch for turning a model's hidden reasoning
        # off. Measured on anthropic/claude-sonnet-5 with this exact prompt:
        # reasoning on gave 303s and a run that consumed all 32000 tokens on
        # 6981 reasoning tokens and returned *zero* content (finish_reason
        # "length"); reasoning off gave 16-19s, ~2200 completion tokens and
        # finish_reason "stop" on 3/3 attempts. This task needs no deliberation
        # — the macro arithmetic is already done in Python — so the reasoning
        # budget is pure cost and a pure failure mode. Harmless for models that
        # have no reasoning mode.
        extra_body={"reasoning": {"enabled": False}},
        # The validator compares against the sum of the per-recipe budgets, not
        # `remaining`: a meal eaten twice in one day contributes its macros
        # twice, so the recipes legitimately total less than the day does.
        context={
            "config": config,
            "day_budget": {
                key: sum(budget[key] for budget in budgets.values()) for key in MACRO_KEYS
            },
        },
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    elapsed = time.monotonic() - started
    usage = getattr(completion, "usage", None)
    reasoning_tokens = getattr(
        getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
    )
    logger.info(
        "%s: got response in %.1fs (finish_reason=%s, completion_tokens=%s, reasoning_tokens=%s)",
        day,
        elapsed,
        getattr(completion.choices[0], "finish_reason", None) if completion.choices else None,
        getattr(usage, "completion_tokens", None),
        reasoning_tokens,
    )

    # One slot per (day, meal_type) by construction, so meal_type is a safe key.
    by_meal_type = {recipe.meal_type.strip().lower(): recipe for recipe in response.recipes}
    missing = [slot.meal_type for slot in cook_slots if slot.meal_type not in by_meal_type]
    if missing:
        raise ValueError(
            f"{day}: model returned no recipe for {', '.join(missing)} "
            f"(got: {', '.join(sorted(by_meal_type)) or 'nothing'})"
        )

    fitted = {}
    for slot in cook_slots:
        recipe, factor = fit_recipe_to_budget(by_meal_type[slot.meal_type], budgets[slot.id])
        if factor != 1.0 and progress_note:
            progress_note(
                f"{day} {slot.meal_type}: portions resized x{factor:.2f} to hit "
                f"{budgets[slot.id]['calories']:.0f} kcal"
            )
        fitted[slot.meal_type] = recipe
    return fitted


async def generate_week_plan(
    spec: WeekSpec,
    config: dict,
    history: Optional[List[dict]] = None,
    progress_callback=None,
    note_callback=None,
    repository: Optional[PlanRepository] = None,
) -> WeekPlan:
    """Generate the whole week, one API call per day that has cooking to do.

    Days are walked in week order so a leftover slot's source recipe always
    exists by the time its macros are needed. Cost scales with cook days, not
    calendar days: a week where lunches are all leftovers is 7 smaller calls,
    and a day with nothing to cook is free.

    Async because it may have to read history through the repository. Note the
    generation calls themselves are still synchronous: `generate_day` blocks on
    instructor's sync client for 30s-3min per day, so this coroutine holds the
    loop for the length of a run. Fixing that means an async OpenAI client (or
    an `asyncio.to_thread` hop, which would move progress callbacks off the
    Streamlit script thread) — a separate change from the storage boundary.
    """
    if history is None:
        history = await (repository or LocalJSONRepository()).load_history()
    targets = week_targets(spec, config)
    portions = portions_for(spec)
    claims = eaten_on(spec)
    # Seeded from previous weeks, then extended as this week generates —
    # otherwise every day is told to avoid the same stale list and nothing
    # stops all seven dinners being chicken.
    avoid_proteins = recent_main_proteins(history)

    events: Dict[str, CookEvent] = {}
    failures: Dict[str, str] = {}

    for day in spec.days:
        cook_slots = spec.cook_slots_on(day)
        carried, descriptions = carried_macros(spec, day, events)

        if progress_callback:
            progress_callback(day, len(cook_slots))
        if not cook_slots:
            continue

        try:
            recipes = generate_day(
                day=day,
                targets=targets[day],
                cook_slots=cook_slots,
                config=config,
                servings_per_meal=spec.servings_per_meal,
                multiplicity=day_multiplicity(spec, day),
                carried=carried,
                carried_descriptions=descriptions,
                avoid_proteins=avoid_proteins[-PROTEIN_AVOID_WINDOW:],
                progress_note=note_callback,
            )
        except Exception as exc:
            # One bad day must not discard the six good ones. Free routes fail
            # in ways no amount of retrying fixes (a provider returning an
            # empty completion, a model that can't hit the budget), and a
            # week is ~7 chances to hit one. The day is recorded and skipped;
            # its slots simply render as "not generated" and its ingredients
            # never reach a shopping list.
            failures[day] = f"{type(exc).__name__}: {exc}".split("\n")[0][:300]
            logger.warning("%s: generation failed — %s", day, failures[day])
            if note_callback:
                note_callback(f"{day}: generation failed — {failures[day]}")
            continue

        for recipe in recipes.values():
            protein = extract_main_protein(recipe)
            if protein and protein not in avoid_proteins:
                avoid_proteins.append(protein)

        for slot in cook_slots:
            recipe = recipes[slot.meal_type]
            claim_ids = claims.get(slot.id, [slot.id])
            last_day_index = max(spec.day_index(value.split(":")[0]) for value in claim_ids)
            recipe = scale_recipe(
                recipe,
                portions=portions[slot.id],
                keeps_for_days=last_day_index - spec.day_index(slot.day),
            )
            events[slot.id] = CookEvent(
                slot_id=slot.id,
                day=day,
                meal_type=slot.meal_type,
                portions=portions[slot.id],
                style=slot.style,
                cuisine=slot.cuisine,
                eaten_by=claim_ids,
                recipe=recipe,
            )

    ordered_events = [events[slot.id] for slot in spec.cook_slots() if slot.id in events]
    return WeekPlan(
        days=spec.days,
        servings_per_meal=spec.servings_per_meal,
        generated_at=datetime.now().isoformat(),
        cook_events=ordered_events,
        slots=spec.slots,
        targets=targets,
        failures=failures,
    )


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def extract_main_protein(recipe: Recipe) -> Optional[str]:
    """Cheap proxy for a recipe's protein source: its highest-protein
    ingredient. Only meaningful for lunch/dinner, where one dominates."""
    if recipe.meal_type.lower() not in ("lunch", "dinner") or not recipe.ingredients:
        return None
    return max(recipe.ingredients, key=lambda ingredient: ingredient.protein_g).name


async def record_week_history(
    week_plan: WeekPlan,
    repository: Optional[PlanRepository] = None,
    max_entries: int = HISTORY_MAX_ENTRIES,
) -> None:
    """One history entry per cooked day, so rotation carries across weeks."""
    repository = repository or LocalJSONRepository()
    history = await repository.load_history()
    generated_at = week_plan.generated_at

    for day in week_plan.days:
        events = [event for event in week_plan.cook_events if event.day == day]
        if not events:
            continue
        proteins = [
            protein
            for protein in (extract_main_protein(event.recipe) for event in events)
            if protein
        ]
        history.append(
            {
                "day_of_week": day,
                "generated_at": generated_at,
                "cuisine": next((event.cuisine for event in events if event.cuisine), None),
                "styles": {event.meal_type: event.style for event in events if event.style},
                "main_proteins": proteins,
                "recipe_names": [event.recipe.name for event in events],
            }
        )

    await repository.save_history(history[-max_entries:])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def print_week_summary(week_plan: WeekPlan) -> None:
    print("\nWeek Plan")
    print("=========")
    if week_plan.failures:
        print("\n!! Some days failed to generate — re-run to retry them:")
        for day, error in week_plan.failures.items():
            print(f"   {day}: {error}")
    by_slot = week_plan.by_slot()
    slots_by_day: Dict[str, List[SlotSpec]] = {}
    for slot in week_plan.slots:
        slots_by_day.setdefault(slot.day, []).append(slot)

    for day in week_plan.days:
        totals = day_slot_macros(week_plan, day)
        target = week_plan.targets[day]
        print(f"\n{day}")
        for slot in slots_by_day.get(day, []):
            if slot.mode == MODE_SKIP:
                print(f"  {slot.meal_type:<10} —")
                continue
            source_id = slot.id if slot.mode == MODE_COOK else slot.source
            event = by_slot.get(source_id)
            if event is None:
                print(f"  {slot.meal_type:<10} (unresolved)")
                continue
            if slot.mode == MODE_COOK:
                tag = f" [cook {event.portions} portions]" if event.portions > week_plan.servings_per_meal else ""
                print(f"  {slot.meal_type:<10} {event.recipe.name}{tag}")
            else:
                print(f"  {slot.meal_type:<10} {event.recipe.name} (leftovers from {event.day})")
        print(
            f"  → {totals['calories']:.0f}/{target['calories']:.0f} kcal · "
            f"P {totals['protein_g']:.0f}/{target['protein_g']:.0f} · "
            f"C {totals['net_carbs_g']:.0f}/{target['net_carbs_g']:.0f} · "
            f"F {totals['fat_g']:.0f}/{target['fat_g']:.0f}"
        )


def print_shopping_windows(week_plan: WeekPlan, windows: List[ShoppingWindow]) -> None:
    for window in windows:
        events = week_plan.events_on_days(window.days)
        print(f"\n{window.label}")
        print("=" * len(window.label))
        if not events:
            print("  (nothing cooked in this window)")
            continue
        shopping_list = aggregate_cook_events(events, window.days)
        print(format_shopping_list_text(shopping_list, cook_events=events))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Weekly Meal Planner CLI")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_FILE, help="Path to config JSON file"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override config's openrouter_model for this run.",
    )
    parser.add_argument(
        "--week-start",
        default=None,
        help="Day the week starts on (default: config week_start_day).",
    )
    parser.add_argument(
        "--servings",
        type=int,
        default=None,
        help="People cooked for per meal (default: config serving_rules.servings_per_meal).",
    )
    parser.add_argument(
        "--shop-days",
        default=None,
        help=(
            "Comma-separated days you shop, e.g. 'Sunday,Wednesday'. Shopping "
            "windows run from each shop day to the next (default: config shopping.shop_days)."
        ),
    )
    parser.add_argument(
        "--leftover-lunches",
        action="store_true",
        help="Set every lunch to leftovers of the previous day's dinner.",
    )
    parser.add_argument(
        "--save-shopping-list",
        action="store_true",
        help="Write each window's shopping list to shopping_list.md.",
    )
    parser.add_argument(
        "--use-cached-plan",
        action="store_true",
        help=(
            f"Load the week from {WEEK_PLAN_CACHE_FILE} instead of calling "
            "OpenRouter (for iterating on the shopping list without API calls)."
        ),
    )
    return parser.parse_args(argv)


async def run_cli(args: argparse.Namespace, repository: PlanRepository) -> None:
    """The CLI's actual work, async so it can await the repository.

    Split from `main()` so there is exactly one `asyncio.run` in the process
    (in `main`) and everything below it is ordinary async code — the shape the
    future backend expects, and the reason storage calls are awaited here
    rather than bridged individually.
    """
    config = await repository.load_config()
    if args.model:
        config["openrouter_model"] = args.model
    spec = default_week_spec(config, args.week_start, args.servings)

    if args.leftover_lunches:
        from week import autofill_leftovers

        spec = autofill_leftovers(spec, "lunch", "dinner")

    if args.use_cached_plan:
        print(f"Loading cached week plan from {WEEK_PLAN_CACHE_FILE}...", flush=True)
        cached = await repository.load_week_plan()
        if cached is None:
            print(f"No cached week plan found ({WEEK_PLAN_CACHE_FILE}). Generate one first.")
            raise SystemExit(1)
        week_plan = WeekPlan.model_validate(cached)
    else:
        history = await repository.load_history()
        spec = resolve_auto_choices(spec, config, history)

        errors = validate_week(spec, config)
        if errors:
            print("Week plan is not valid:")
            for error in errors:
                print(f"  - {error}")
            raise SystemExit(1)

        model = config.get("openrouter_model", DEFAULT_MODEL)
        cook_days = len({slot.day for slot in spec.cook_slots()})
        print(
            f"Generating {len(spec.days)}-day plan ({len(spec.cook_slots())} cooks "
            f"across {cook_days} days) using {model}...",
            flush=True,
        )

        def report(day: str, cooks: int) -> None:
            print(f"  {day}: {cooks} recipe(s)..." if cooks else f"  {day}: leftovers only", flush=True)

        week_plan = await generate_week_plan(
            spec,
            config,
            history,
            progress_callback=report,
            note_callback=lambda message: print(f"    {message}", flush=True),
            repository=repository,
        )

        await repository.save_week_plan(week_plan.model_dump())
        await record_week_history(week_plan, repository)

    print_week_summary(week_plan)

    shop_days = (
        [day.strip() for day in args.shop_days.split(",") if day.strip()]
        if args.shop_days
        else config.get("shopping", {}).get("shop_days", [])
    )
    windows = shopping_windows(week_plan.days, shop_days)

    print("\n\nShopping Lists")
    print("==============")
    print_shopping_windows(week_plan, windows)

    if args.save_shopping_list:
        sections = []
        for window in windows:
            events = week_plan.events_on_days(window.days)
            if not events:
                continue
            shopping_list = aggregate_cook_events(events, window.days)
            sections.append(
                format_shopping_list_markdown(
                    shopping_list, cook_events=events, title=window.label
                )
            )
        with open("shopping_list.md", "w") as f:
            f.write("\n\n".join(sections))
        print("\nSaved shopping lists to shopping_list.md", flush=True)


def main() -> None:
    """Sync entry point: parse args, pick a repository, run the async CLI.

    `--config` still names a file because the only repository today is the
    local one; a backend implementation would be selected here instead and
    nothing below this line would change.
    """
    configure_logging()
    args = parse_args()
    repository = LocalJSONRepository(config_path=args.config)
    run_sync(run_cli(args, repository))


if __name__ == "__main__":
    main()
