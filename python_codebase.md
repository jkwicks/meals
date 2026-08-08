=== File: ./planner.py ===
import argparse
import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import instructor
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

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
WEEK_PLAN_CACHE_FILE = "week_plan.json"
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
MEAL_HISTORY_FILE = "meal_history.json"
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


def load_config(config_path: str = "config.json") -> dict:
    with open(config_path, "r") as f:
        return json.load(f)


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

    protein_cal = protein_g * 4
    carb_cal = net_carbs_g * 4
    fat_g = max(0, (calories - (protein_cal + carb_cal)) / 9)

    return {
        "day_of_week": day_of_week,
        "calories": calories,
        "protein_g": protein_g,
        "net_carbs_g": net_carbs_g,
        "fat_g": round(fat_g, 1),
    }


def week_targets(spec: WeekSpec, config: dict) -> Dict[str, dict]:
    return {day: calculate_daily_targets(day, config) for day in spec.days}


# --------------------------------------------------------------------------
# History-aware rotation
# --------------------------------------------------------------------------


def load_history(path: str = MEAL_HISTORY_FILE) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


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
) -> Dict[str, dict]:
    """Divide the day's remaining macros into a per-meal budget.

    Handing the model one daily number and letting it apportion the meals
    itself is where free models drift worst — they reach for a familiar "full
    day" shape and blow past a reduced target. Splitting in Python is the same
    rule as calculate_daily_targets: Python does the arithmetic, the model
    only fills in food.

    A meal eaten more than once today contributes its macros that many times,
    so it takes a proportionally larger share of the day while its own recipe
    budget stays a single serving.
    """
    weights_config = config.get("meal_weights", DEFAULT_MEAL_WEIGHTS)
    base = {
        slot.id: weights_config.get(slot.meal_type, 0.25) or 0.25 for slot in cook_slots
    }
    total_weight = sum(base[slot.id] * multiplicity.get(slot.id, 1) for slot in cook_slots)
    if total_weight <= 0:
        return {slot.id: dict(remaining) for slot in cook_slots}

    return {
        slot.id: {
            key: remaining[key] * base[slot.id] / total_weight for key in MACRO_KEYS
        }
        for slot in cook_slots
    }


def build_slot_brief(
    slot: SlotSpec, config: dict, times_eaten_today: int, budget: dict
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

    budgets = split_targets(remaining, cook_slots, multiplicity, config)
    slot_briefs = "\n".join(
        build_slot_brief(slot, config, multiplicity.get(slot.id, 1), budgets[slot.id])
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


def generate_week_plan(
    spec: WeekSpec,
    config: dict,
    history: Optional[List[dict]] = None,
    progress_callback=None,
    note_callback=None,
) -> WeekPlan:
    """Generate the whole week, one API call per day that has cooking to do.

    Days are walked in week order so a leftover slot's source recipe always
    exists by the time its macros are needed. Cost scales with cook days, not
    calendar days: a week where lunches are all leftovers is 7 smaller calls,
    and a day with nothing to cook is free.
    """
    history = load_history() if history is None else history
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


def record_week_history(
    week_plan: WeekPlan,
    path: str = MEAL_HISTORY_FILE,
    max_entries: int = HISTORY_MAX_ENTRIES,
) -> None:
    """One history entry per cooked day, so rotation carries across weeks."""
    history = load_history(path)
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

    history = history[-max_entries:]
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


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


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description="AI Weekly Meal Planner CLI")
    parser.add_argument("--config", default="config.json", help="Path to config JSON file")
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
    args = parser.parse_args()

    config = load_config(args.config)
    if args.model:
        config["openrouter_model"] = args.model
    spec = default_week_spec(config, args.week_start, args.servings)

    if args.leftover_lunches:
        from week import autofill_leftovers

        spec = autofill_leftovers(spec, "lunch", "dinner")

    if args.use_cached_plan:
        print(f"Loading cached week plan from {WEEK_PLAN_CACHE_FILE}...", flush=True)
        with open(WEEK_PLAN_CACHE_FILE, "r") as f:
            week_plan = WeekPlan.model_validate(json.load(f))
    else:
        history = load_history()
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

        week_plan = generate_week_plan(
            spec,
            config,
            history,
            progress_callback=report,
            note_callback=lambda message: print(f"    {message}", flush=True),
        )

        with open(WEEK_PLAN_CACHE_FILE, "w") as f:
            json.dump(week_plan.model_dump(), f, indent=2)
        record_week_history(week_plan)

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


if __name__ == "__main__":
    main()
-e 

=== File: ./shopping.py ===
import re
from typing import Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from week import PERISHABLE_DAY_GAP, PERISHABLE_DEPARTMENTS

# planner.py imports this module, so importing Recipe/CookEvent back from
# planner would be circular; they're only needed for type hints.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planner import CookEvent, Recipe

# Ordered (specific -> general) keyword -> department lookup. Matched as whole
# words against the ingredient's *head* — the part before the first comma —
# because models write "Garlic, minced" and "Pork shoulder, lean, cubed", and
# matching the whole string put minced garlic in Meat & Poultry (the "mince"
# keyword) on a real run.
DEPARTMENT_KEYWORDS = [
    # Ambient/bottled goods, matched before the fresh departments they'd
    # otherwise be dragged into: "Beef broth" is not meat, "Apple cider
    # vinegar" is not produce, "Coconut milk" is not dairy, "Fish sauce" is
    # not seafood, "Tomato paste" is not produce. All observed on real runs.
    ("Pantry", [
        # The "<animal> broth" pairs are spelled out because longest-match
        # would otherwise give "chicken broth" to Meat & Poultry on "chicken".
        "chicken broth", "beef broth", "vegetable broth", "fish broth",
        "chicken stock", "beef stock", "vegetable stock", "bone broth",
        "broth", "stock", "vinegar", "wine", "soy sauce", "fish sauce",
        "coconut milk", "coconut cream", "tomato paste", "tomato passata",
        "passata", "honey", "molasses", "gochujang", "miso", "curry paste",
        "canned tomato", "chopped tomato", "olive oil", "coconut oil",
        "avocado oil", "sesame oil", "mct oil", "vegetable oil", "ghee",
        "tamarind paste", "palm sugar", "brown sugar", "protein powder",
        "protein isolate",
    ]),
    # Before Dairy, or "peanut butter" matches "butter". Before Produce, or
    # "pumpkin seeds" matches "pea".
    ("Nuts, Seeds & Spreads", [
        "peanut butter", "almond butter", "cashew butter", "nut butter",
        "tahini", "peanut", "walnut", "almond", "cashew", "pecan",
        "pistachio", "hazelnut", "macadamia", "pumpkin seed", "sunflower seed",
        "sesame seed", "chia seed", "flaxseed", "flax seed", "hemp heart",
        "hemp seed", "nut", "seed",
    ]),
    # "pepper" is deliberately NOT a keyword here — it put "Red bell pepper"
    # in Herbs & Spices. The pepper *spices* are listed individually instead.
    ("Herbs & Spices", [
        "salt", "black pepper", "white pepper", "cayenne", "cayenne pepper",
        "peppercorn", "red pepper flake", "chili flake", "chilli flake",
        "cumin", "paprika", "oregano", "basil", "thyme",
        "rosemary", "cinnamon", "turmeric", "chili powder", "chilli powder",
        "garlic powder", "onion powder", "bay leaf", "parsley", "cilantro",
        "coriander", "dill", "sage", "nutmeg", "clove", "cardamom",
        "chives", "mint", "vanilla extract", "spice", "seasoning",
    ]),
    ("Fish & Seafood", [
        "salmon", "tuna", "shrimp", "prawn", "cod", "tilapia", "halibut",
        "trout", "sardine", "anchovy", "crab", "lobster", "mussel", "clam",
        "scallop", "mackerel", "kipper", "fish",
    ]),
    ("Meat & Poultry", [
        "chicken", "beef", "pork", "turkey", "lamb", "bacon", "sausage",
        "ground beef", "steak", "ham", "duck", "veal", "mince",
    ]),
    ("Dairy & Eggs", [
        "milk", "cheese", "yogurt", "yoghurt", "butter", "cream", "egg",
        "mozzarella", "cheddar", "parmesan", "ricotta", "feta",
    ]),
    ("Grains & Bakery", [
        "rice", "pasta", "bread", "oats", "oatmeal", "flour", "quinoa",
        "tortilla", "noodle", "cereal", "bun", "bagel", "cracker",
    ]),
    ("Produce", [
        "apple", "banana", "spinach", "kale", "lettuce", "tomato", "onion",
        "garlic", "pepper bell", "bell pepper", "broccoli", "cauliflower",
        "carrot", "potato", "zucchini", "courgette", "cucumber", "avocado",
        "lemon", "lime", "berry", "berries", "mushroom", "celery", "cabbage",
        "squash", "sweet potato", "asparagus", "green bean", "pea",
        "blueberry", "raspberry", "strawberry", "blackberry", "cranberry",
        "cherry", "orange", "grape", "peach", "pear", "mango", "melon", "eggplant",
        "aubergine", "okra", "pumpkin", "artichoke", "brussels sprout",
        "aubergine", "scallion", "spring onion", "shallot", "leek", "ginger",
        "greens", "chili", "chilli", "radish", "beet", "fennel", "turnip",
    ]),
]

DEFAULT_DEPARTMENT = "Pantry"

# Never appears on a shopping list — you don't buy it, and a "Water: 300g"
# line is noise that makes the rest look untrustworthy.
NON_SHOPPING_INGREDIENTS = {"water", "ice", "cold water", "hot water", "tap water"}

# Matched as whole words against the ingredient head -> average grams per
# unit, for shopping-list display only. Ingredient.quantity_g and all macro
# math stay in grams — this just renders the total as "6 eggs" instead of
# "300g" for items a shopper actually buys by the piece.
#
# Whole-word matching on the head is what stops "Eggplant, cubed" rendering as
# "10 eggs" and "Butter, for frying eggs" as "1 egg" — both happened on a real
# run under plain substring matching.
COUNT_UNIT_INGREDIENTS = {
    "egg": 50,
    "garlic clove": 5,
}

# Words describing how an ingredient is cut or presented. Stripped before
# combining, so "Cucumber, diced" and "Cucumber, sliced" become one line
# instead of sending you to buy cucumber twice.
#
# STATE_QUALIFIERS are the opposite: they change what a gram *means*, so they
# are pulled out of the full name (not just the head) and folded back into the
# combining key. Without this, splitting on the first comma silently discarded
# them and merged "Quinoa, cooked" with "Quinoa, dry" — two very different
# weights of the same purchase, which would understate the shop.
#
# "raw" and "uncooked" are excluded on purpose: they describe the *default*
# state, so treating them as qualifiers split "Red bell pepper" from "Red bell
# pepper (raw)" into two lines for the same purchase. Their absence still
# separates correctly, because the non-default state ("cooked") is the one
# that carries a qualifier.
STATE_QUALIFIERS = {
    "cooked", "dry", "dried", "canned", "tinned", "frozen",
}

PREP_QUALIFIERS = {
    "raw", "uncooked",
    "baby", "chopped", "cubed", "crushed", "diced", "finely", "fresh",
    "freshly", "grated", "grilled", "halved", "julienned", "large", "lean",
    "medium", "minced", "peeled", "quartered", "roasted", "sauteed",
    "sautéed", "shredded", "sliced", "small", "thin", "thinly", "toasted",
    "torn", "trimmed", "washed", "whole",
}


class ShoppingItem(BaseModel):
    name: str = Field(..., description="Ingredient name")
    total_amount_g: float = Field(..., ge=0, description="Combined quantity in grams")
    nova_group: int = Field(..., ge=1, le=4)
    department: str = Field(..., description="Grocery department/category")
    latest_cook_offset: int = Field(
        default=0,
        ge=0,
        description="Days between this shopping trip and the last meal that uses the item",
    )

    @property
    def buy_late(self) -> bool:
        """A perishable that isn't cooked until several days into the window.

        Multi-day shopping windows are the point of this planner, but they
        mean fresh fish bought on day 1 for a day 5 cook. Flagged rather than
        rescheduled — buying it on a second trip is the shopper's call.
        """
        return (
            self.department in PERISHABLE_DEPARTMENTS
            and self.latest_cook_offset >= PERISHABLE_DAY_GAP
        )


class ShoppingList(BaseModel):
    categories: Dict[str, List[ShoppingItem]] = Field(default_factory=dict)

    def items(self) -> List[ShoppingItem]:
        return [item for department in sorted(self.categories) for item in self.categories[department]]


def strip_parentheticals(name: str) -> str:
    """Remove bracketed asides, including an unclosed trailing one.

    Must run before the comma split: models write "Egg yolks (large, from
    free-range eggs)", and splitting first left the dangling "Egg yolks (large"
    on the shopping list.
    """
    cleaned = re.sub(r"\([^()]*\)", " ", name)
    while re.search(r"\([^()]*\)", cleaned):
        cleaned = re.sub(r"\([^()]*\)", " ", cleaned)
    cleaned = re.sub(r"[(\[].*$", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def ingredient_head(name: str) -> str:
    """The part before the first comma — the thing itself, minus preparation.

    Models write "Pork shoulder, lean, cubed" and "Butter, for frying eggs".
    Everything after the first comma describes handling, not what you buy, and
    matching against it is what produced miscategorised and miscounted lines.
    """
    return strip_parentheticals(name).split(",")[0].strip()


def contains_word(haystack: str, phrase: str) -> bool:
    """Whole-word/phrase containment, so 'egg' misses 'eggplant'.

    Handles the plural forms English actually uses: a bare +s missed
    "potatoes" (from "potato") and "berries" (from "berry"), which dropped
    both into the default department on a real run.
    """
    stem = re.escape(phrase)
    forms = [stem, stem + "s", stem + "es"]
    if phrase.endswith("y"):
        forms.append(re.escape(phrase[:-1]) + "ies")
    return re.search(rf"\b(?:{'|'.join(forms)})\b", haystack) is not None


# Different names for the same purchase. Applied to the combining key after
# normalisation, so "Garlic cloves" and "Garlic" become one line rather than
# two entries in the same department.
NAME_ALIASES = {
    "clove garlic": "garlic",
    "onion spring": "scallion",
    "coriander fresh": "cilantro",
}


def singularize(word: str) -> str:
    """Crude plural stripper, used only to build combining keys.

    "Carrot"/"Carrots" and "Garlic clove"/"Garlic cloves" are the same
    purchase and must land on one line. Only ever applied to the key, never
    to what the shopper reads, so an odd stem does no visible harm.
    """
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    # "-es" is only the plural marker after a sibilant or -o ("potatoes",
    # "boxes"). Applying it everywhere turned "cloves" into "clov", which kept
    # "Garlic cloves" and "Garlic" on separate shopping lines.
    if word.endswith("es") and word[:-2].endswith(("o", "x", "z", "ch", "sh", "s")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def states_in(name: str) -> List[str]:
    """State qualifiers anywhere in the full name, deduped and sorted."""
    words = set(re.findall(r"[a-z]+", name.lower()))
    return sorted(words & STATE_QUALIFIERS)


def normalize_name(name: str) -> str:
    """Combining key: head minus cut words, word-sorted, plus any state words.

    Word-sorting is what collapses "Fresh lemon juice" and "Lemon juice,
    fresh" — models phrase the same purchase both ways within one week. The
    state suffix is what keeps "Quinoa, dry" and "Quinoa, cooked" apart.
    """
    head = ingredient_head(name).lower()
    words = [
        singularize(word)
        for word in re.findall(r"[a-z]+", head)
        if word not in PREP_QUALIFIERS and word not in STATE_QUALIFIERS
    ]
    base = " ".join(sorted(words)) if words else head.strip()
    base = NAME_ALIASES.get(base, base)
    states = states_in(name)
    return f"{base} [{' '.join(states)}]" if states else base


def display_name(name: str) -> str:
    """What the shopper reads: head minus cut words, state kept in parentheses."""
    head = ingredient_head(name)
    words = [
        word
        for word in head.split()
        if word.lower().strip(".") not in PREP_QUALIFIERS
        and word.lower().strip(".") not in STATE_QUALIFIERS
    ]
    cleaned = " ".join(words).strip(" -,")
    if not cleaned:
        cleaned = ingredient_head(name)
    cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else name
    states = states_in(name)
    return f"{cleaned} ({', '.join(states)})" if states else cleaned


def categorize_department(ingredient_name: str) -> str:
    """Department of the longest matching keyword, not the first one found.

    Specificity beats list order, which removes a whole class of fragile
    ordering bugs seen on real runs: "garlic cloves" matched the spice
    "clove" before the produce "garlic"; "cauliflower rice" matched "rice";
    "beef broth" matched "beef". The longer phrase is the more specific
    description in every one of those cases. Ties fall back to list order, so
    the specific -> general ordering still decides genuine ambiguity.
    """
    head = ingredient_head(ingredient_name).lower()
    best_department = DEFAULT_DEPARTMENT
    best_length = 0
    for department, keywords in DEPARTMENT_KEYWORDS:
        for keyword in keywords:
            if len(keyword) > best_length and contains_word(head, keyword):
                best_department = department
                best_length = len(keyword)
    return best_department


def aggregate_recipes(
    recipes: Sequence["Recipe"], offsets: Optional[Sequence[int]] = None
) -> ShoppingList:
    """Combine recipes into one departmentalised list.

    `offsets` is a parallel sequence giving each recipe's cook day as a day
    count from the start of the shopping window; an ingredient's offset is the
    latest cook that uses it, which is what decides the perishable warning.
    """
    if offsets is None:
        offsets = [0] * len(recipes)

    aggregated: Dict[str, dict] = {}

    for recipe, offset in zip(recipes, offsets):
        for ingredient in recipe.ingredients:
            if normalize_name(ingredient.name) in NON_SHOPPING_INGREDIENTS:
                continue
            key = normalize_name(ingredient.name)
            if key not in aggregated:
                aggregated[key] = {
                    "name": display_name(ingredient.name),
                    "total_amount_g": 0.0,
                    "nova_group": ingredient.nova_group,
                    "latest_cook_offset": offset,
                }
            aggregated[key]["total_amount_g"] += ingredient.quantity_g
            aggregated[key]["nova_group"] = max(
                aggregated[key]["nova_group"], ingredient.nova_group
            )
            aggregated[key]["latest_cook_offset"] = max(
                aggregated[key]["latest_cook_offset"], offset
            )

    categories: Dict[str, List[ShoppingItem]] = {}
    for item in aggregated.values():
        department = categorize_department(item["name"])
        shopping_item = ShoppingItem(
            name=item["name"],
            total_amount_g=round(item["total_amount_g"], 1),
            nova_group=item["nova_group"],
            department=department,
            latest_cook_offset=item["latest_cook_offset"],
        )
        categories.setdefault(department, []).append(shopping_item)

    for items in categories.values():
        items.sort(key=lambda i: i.name.lower())

    return ShoppingList(categories=categories)


def aggregate_cook_events(
    cook_events: Sequence["CookEvent"], window_days: Optional[Sequence[str]] = None
) -> ShoppingList:
    """Shopping list for a set of cook events, offsets derived from their days.

    Grouping is by **cook day**, never eating day: a Sunday batch eaten on
    Wednesday belongs entirely to the Sunday trip, so its ingredients are never
    split across two shopping lists.
    """
    days = list(window_days) if window_days else []
    offsets = [days.index(event.day) if event.day in days else 0 for event in cook_events]
    return aggregate_recipes([event.recipe for event in cook_events], offsets)


def format_grams(amount_g: float) -> str:
    if amount_g >= 1000:
        return f"{amount_g / 1000:.2f}kg"
    return f"{amount_g:g}g"


def format_quantity(name: str, amount_g: float) -> str:
    head = ingredient_head(name).lower()
    for keyword, grams_per_unit in COUNT_UNIT_INGREDIENTS.items():
        if contains_word(head, keyword):
            count = max(1, round(amount_g / grams_per_unit))
            unit = keyword if count == 1 else f"{keyword}s"
            return f"{count} {unit}"
    return format_grams(amount_g)


def cook_plan_lines(cook_events: Sequence["CookEvent"]) -> List[str]:
    """What this trip's shopping is actually for: each cook and the meals it
    covers. Ingredient totals below already include every portion."""
    lines = []
    for event in cook_events:
        meals = len(event.eaten_by)
        covers = (
            f"{meals} meals"
            if meals > 1
            else "1 meal"
        )
        lines.append(
            f"{event.day} {event.meal_type}: {event.recipe.name} — "
            f"{event.portions} portions, covers {covers}"
        )
    return lines


def _item_line(item: ShoppingItem) -> str:
    note = "  ← buy fresh closer to the day" if item.buy_late else ""
    return f"{item.name}: {format_quantity(item.name, item.total_amount_g)}{note}"


def format_shopping_list_text(
    shopping_list: ShoppingList, cook_events: Optional[Sequence["CookEvent"]] = None
) -> str:
    lines = []
    if cook_events:
        lines.append("Cooking this window (quantities below already include every portion):")
        for line in cook_plan_lines(cook_events):
            lines.append(f"  - {line}")
        lines.append("")
    for department in sorted(shopping_list.categories):
        lines.append(f"{department}:")
        for item in shopping_list.categories[department]:
            lines.append(f"  - {_item_line(item)}")
    return "\n".join(lines)


def format_shopping_list_markdown(
    shopping_list: ShoppingList,
    cook_events: Optional[Sequence["CookEvent"]] = None,
    title: str = "Shopping List",
) -> str:
    lines = [f"# {title}", ""]
    if cook_events:
        lines.append("## Cooking this window")
        lines.append("_Quantities below already include every portion._")
        lines.append("")
        for line in cook_plan_lines(cook_events):
            lines.append(f"- {line}")
        lines.append("")
    for department in sorted(shopping_list.categories):
        lines.append(f"## {department}")
        for item in shopping_list.categories[department]:
            note = " _(buy fresh closer to the day)_" if item.buy_late else ""
            lines.append(
                f"- [ ] {item.name} — {format_quantity(item.name, item.total_amount_g)}{note}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_shopping_list_keep(shopping_list: ShoppingList) -> str:
    """One item per line, no bullets/markdown/blank lines. Google Keep turns
    each line of pasted text into its own checkbox item inside a list-type
    note — bullets or blank lines would just become extra junk items."""
    lines = []
    for department in sorted(shopping_list.categories):
        lines.append(department)
        for item in shopping_list.categories[department]:
            lines.append(f"{item.name}: {format_quantity(item.name, item.total_amount_g)}")
    return "\n".join(lines)
-e 

=== File: ./app.py ===
import copy

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from planner import (
    DEFAULT_MODEL,
    calculate_daily_targets,
    configure_logging,
    day_slot_macros,
    generate_week_plan,
    load_config,
    load_history,
    per_serving_totals,
    record_week_history,
    resolve_auto_choices,
)
from shopping import (
    aggregate_cook_events,
    cook_plan_lines,
    format_quantity,
    format_shopping_list_keep,
    format_shopping_list_markdown,
)
from week import (
    AUTO,
    MODE_COOK,
    MODE_LEFTOVER,
    MODE_SKIP,
    MODES,
    SlotSpec,
    WeekSpec,
    all_style_keys,
    autofill_leftovers,
    default_week_spec,
    eaten_on,
    humanize,
    meal_types,
    portions_for,
    shopping_windows,
    slot_id,
    validate_week,
    week_days,
    week_warnings,
)

load_dotenv()

CONFIG_PATH = "config.json"
MODEL_OPTIONS = [
    "anthropic/claude-sonnet-5",
    "deepseek/deepseek-v4-flash",
    # Free OpenRouter models below. Spot-checked directly (bypassing
    # instructor) with a trivial prompt before adding: finish_reason "stop"
    # and near-zero reasoning_tokens, per the diagnostic method in
    # CLAUDE.md. Several other free models were tried and rejected because
    # they burned most/all of their completion budget on hidden reasoning
    # tokens instead of the actual reply (nvidia/nemotron-nano-9b-v2:free,
    # nvidia/nemotron-3-super-120b-a12b:free, poolside/laguna-xs-2.1:free)
    # — the same failure mode documented for gpt-oss-20b / nemotron-3-nano
    # / ling-3.0 / north-mini-code.
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "poolside/laguna-s-2.1:free",
]

# Grid column headers. Kept as constants because they're referenced by
# st.data_editor's column_config, the DataFrame, and the row<->spec mapping.
COL_DAY = "Day"
COL_MEAL = "Meal"
COL_MODE = "Mode"
COL_STYLE = "Style"
COL_CUISINE = "Cuisine"
COL_SOURCE = "Leftover of"
COL_EXTRA = "Extra portions"

NO_SOURCE = "—"

configure_logging()
st.set_page_config(page_title="AI Weekly Meal Planner", layout="wide")


# --------------------------------------------------------------------------
# Grid <-> WeekSpec mapping
# --------------------------------------------------------------------------


def source_label(value: str) -> str:
    day, meal_type = value.split(":")
    return f"{day} · {meal_type}"


def source_value(label: str) -> str:
    if not label or label == NO_SOURCE:
        return ""
    day, meal_type = label.split(" · ")
    return slot_id(day, meal_type)


def spec_to_rows(spec: WeekSpec) -> list:
    return [
        {
            COL_DAY: slot.day,
            COL_MEAL: slot.meal_type,
            COL_MODE: slot.mode,
            COL_STYLE: slot.style or AUTO,
            COL_CUISINE: slot.cuisine or AUTO,
            COL_SOURCE: source_label(slot.source) if slot.source else NO_SOURCE,
            COL_EXTRA: int(slot.extra_portions),
        }
        for slot in spec.slots
    ]


def rows_to_spec(rows, days: list, servings_per_meal: int) -> WeekSpec:
    """Read the edited grid back into a WeekSpec.

    `auto` and the em-dash placeholder become None so the rest of the codebase
    only ever sees a concrete choice or an explicit absence.
    """
    slots = []
    for row in rows:
        mode = row[COL_MODE]
        style = row[COL_STYLE]
        cuisine = row[COL_CUISINE]
        slots.append(
            SlotSpec(
                day=row[COL_DAY],
                meal_type=row[COL_MEAL],
                mode=mode,
                style=None if (style in (AUTO, None) or mode != MODE_COOK) else style,
                cuisine=None if (cuisine in (AUTO, None) or mode != MODE_COOK) else cuisine,
                source=source_value(row[COL_SOURCE]) if mode == MODE_LEFTOVER else None,
                extra_portions=int(row[COL_EXTRA] or 0) if mode == MODE_COOK else 0,
            )
        )
    return WeekSpec(days=days, servings_per_meal=servings_per_meal, slots=slots)


def set_grid_rows(rows: list) -> None:
    """Replace the grid's data programmatically.

    A keyed st.data_editor keeps its own dict of edited cells in
    st.session_state["grid_editor"] and replays it over whatever data it's
    given. Without dropping that, a button that rewrites the rows appears to
    do nothing for any cell the user had already touched by hand.
    """
    st.session_state["grid_rows"] = rows
    st.session_state.pop("grid_editor", None)


def ensure_grid(config: dict, week_start: str, servings: int) -> None:
    """(Re)build the grid rows in session state when the week shape changes.

    Rebuilding on every rerun would throw away the user's edits; rebuilding
    never would leave stale days after changing the week start. Keying on the
    week shape is the middle ground.
    """
    shape = (week_start, tuple(meal_types(config)))
    if st.session_state.get("grid_shape") != shape:
        st.session_state["grid_shape"] = shape
        set_grid_rows(spec_to_rows(default_week_spec(config, week_start, servings)))


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------


def render_sidebar(config: dict) -> dict:
    st.sidebar.header("Week Setup")

    all_days = list(config["weekly_schedule"].keys())
    default_start = config.get("week_start_day", all_days[0])
    week_start = st.sidebar.selectbox(
        "Week starts on",
        all_days,
        index=all_days.index(default_start) if default_start in all_days else 0,
        help="Sets day 1. Leftovers may only be eaten on or after their cook day in this order.",
    )
    days = week_days(config, week_start)

    servings = st.sidebar.slider(
        "People per meal",
        min_value=1,
        max_value=8,
        value=int(config.get("serving_rules", {}).get("servings_per_meal", 2)),
        help="Batch sizes are derived from this times the number of meals each cook covers.",
    )

    st.sidebar.subheader("Shopping trips")
    default_shop_days = [
        day for day in config.get("shopping", {}).get("shop_days", []) if day in days
    ]
    shop_days = st.sidebar.multiselect(
        "Days you shop",
        days,
        default=default_shop_days,
        help="Each trip covers from that day until the next one.",
    )

    st.sidebar.subheader("Model")
    model_default = config.get("openrouter_model", DEFAULT_MODEL)
    model = st.sidebar.selectbox(
        "OpenRouter Model",
        MODEL_OPTIONS,
        index=MODEL_OPTIONS.index(model_default) if model_default in MODEL_OPTIONS else 0,
    )

    return {"week_start": week_start, "days": days, "servings": servings,
            "shop_days": shop_days, "model": model}


# --------------------------------------------------------------------------
# Plan setup tab
# --------------------------------------------------------------------------


def render_targets_editor(config: dict, days: list) -> dict:
    """Per-day macro targets as an editable table, returning an overridden config.

    Fat is never entered — calculate_daily_targets derives it from the other
    three, exactly as the CLI does, so the displayed Fat column is always the
    number the model will actually be given.
    """
    st.markdown("#### Daily targets")
    base = config["weekly_schedule"]
    rows = []
    for day in days:
        targets = calculate_daily_targets(day, config)
        rows.append(
            {
                "Day": day,
                "Calories": int(base[day]["calories"]),
                "Protein (g)": int(base[day]["protein_g"]),
                "Net carbs (g)": int(base[day]["net_carbs_g"]),
                "Fat (g)": targets["fat_g"],
            }
        )

    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        disabled=["Day", "Fat (g)"],
        column_config={
            "Calories": st.column_config.NumberColumn(min_value=800, max_value=6000, step=50),
            "Protein (g)": st.column_config.NumberColumn(min_value=0, max_value=400, step=5),
            "Net carbs (g)": st.column_config.NumberColumn(min_value=0, max_value=500, step=5),
            "Fat (g)": st.column_config.NumberColumn(
                help="Computed in Python from the other three — not editable.", format="%.1f"
            ),
        },
        key="targets_editor",
    )

    day_config = copy.deepcopy(config)
    for row in edited.to_dict("records"):
        day_config["weekly_schedule"][row["Day"]].update(
            {
                "calories": int(row["Calories"]),
                "protein_g": int(row["Protein (g)"]),
                "net_carbs_g": int(row["Net carbs (g)"]),
            }
        )
    return day_config


def render_grid(config: dict, days: list, servings: int) -> WeekSpec:
    st.markdown("#### Meal grid")
    st.caption(
        "One row per meal. **Mode** decides whether it's cooked fresh, eaten as "
        "leftovers of an earlier cook, or skipped. Leave Style and Cuisine on "
        "`auto` to let the planner rotate them for you."
    )

    quick_cols = st.columns([1, 1, 1, 3])
    if quick_cols[0].button("Lunches = last night's dinner", use_container_width=True):
        spec = rows_to_spec(st.session_state["grid_rows"], days, servings)
        set_grid_rows(spec_to_rows(autofill_leftovers(spec, "lunch", "dinner")))
        st.rerun()
    if quick_cols[1].button("Reset grid", use_container_width=True):
        set_grid_rows(spec_to_rows(default_week_spec(config, days[0], servings)))
        st.rerun()

    current = rows_to_spec(st.session_state["grid_rows"], days, servings)
    # Only slots currently set to cook can be a leftover source — recomputed
    # each rerun so the dropdown tracks edits made on the previous pass. Any
    # source already in use is kept in the list even if it stopped being a
    # cook slot: a SelectboxColumn value outside its options is a render
    # error, and a stale pointer should surface as a validation message
    # instead.
    source_options = [NO_SOURCE] + [source_label(slot.id) for slot in current.cook_slots()]
    for row in st.session_state["grid_rows"]:
        if row[COL_SOURCE] not in source_options:
            source_options.append(row[COL_SOURCE])

    edited = st.data_editor(
        pd.DataFrame(st.session_state["grid_rows"]),
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        height=min(1000, 60 + 35 * len(st.session_state["grid_rows"])),
        disabled=[COL_DAY, COL_MEAL],
        column_config={
            COL_MODE: st.column_config.SelectboxColumn(options=MODES, required=True),
            COL_STYLE: st.column_config.SelectboxColumn(
                options=[AUTO] + all_style_keys(config),
                help="Styles are per meal type — a breakfast style on a dinner row is flagged below.",
            ),
            COL_CUISINE: st.column_config.SelectboxColumn(
                options=[AUTO] + config.get("cuisines", []),
                help="Applies to "
                + ", ".join(config.get("cuisine_meal_types") or meal_types(config)),
            ),
            COL_SOURCE: st.column_config.SelectboxColumn(
                options=source_options,
                help="Which cooked meal this eats the leftovers of.",
            ),
            COL_EXTRA: st.column_config.NumberColumn(
                min_value=0, max_value=12, step=1,
                help="Spare portions to freeze, on top of the meals this cook covers.",
            ),
        },
        key="grid_editor",
    )

    rows = edited.to_dict("records")
    st.session_state["grid_rows"] = rows
    return rows_to_spec(rows, days, servings)


def render_cook_summary(spec: WeekSpec) -> None:
    portions = portions_for(spec)
    claims = eaten_on(spec)
    if not portions:
        return

    rows = []
    for slot in spec.cook_slots():
        claim_ids = claims.get(slot.id, [slot.id])
        last_day = max(claim_ids, key=lambda value: spec.day_index(value.split(":")[0]))
        span = spec.day_index(last_day.split(":")[0]) - spec.day_index(slot.day)
        rows.append(
            {
                "Cook": f"{slot.day} · {slot.meal_type}",
                "Style": humanize(slot.style) or "auto",
                "Cuisine": humanize(slot.cuisine) or "—",
                "Meals covered": len(claim_ids),
                "Portions": portions[slot.id],
                "Eaten over": f"{span + 1} day(s)",
            }
        )

    st.markdown("#### Cooking sessions")
    st.caption(
        f"{len(rows)} cook(s) · {sum(row['Portions'] for row in rows)} portions total. "
        "Portions are derived: meals covered × people per meal, plus any extras."
    )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_window_preview(spec: WeekSpec, shop_days: list) -> list:
    windows = shopping_windows(spec.days, shop_days)
    st.markdown("#### Shopping windows")
    if not shop_days:
        st.caption("No shopping days selected — the whole week becomes one trip.")
    rows = []
    for window in windows:
        cooks = len(
            [slot for slot in spec.cook_slots() if slot.day in window.days]
        )
        rows.append(
            {
                "Trip": window.label,
                "Days": len(window.days),
                "Cooks bought for": cooks,
                "Buy ahead": "yes" if window.shop_ahead else "",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    return windows


# --------------------------------------------------------------------------
# Results tabs
# --------------------------------------------------------------------------


def render_week(week_plan) -> None:
    by_slot = week_plan.by_slot()
    slots_by_day = {}
    for slot in week_plan.slots:
        slots_by_day.setdefault(slot.day, []).append(slot)

    for day in week_plan.days:
        target = week_plan.targets[day]
        totals = day_slot_macros(week_plan, day)

        with st.expander(
            f"**{day}** — {totals['calories']:.0f} / {target['calories']:.0f} kcal",
            expanded=True,
        ):
            macro_cols = st.columns(4)
            for column, (label, key, unit) in zip(
                macro_cols,
                [
                    ("Calories", "calories", "kcal"),
                    ("Protein", "protein_g", "g"),
                    ("Net Carbs", "net_carbs_g", "g"),
                    ("Fat", "fat_g", "g"),
                ],
            ):
                column.metric(
                    f"{label} ({unit})",
                    f"{totals[key]:.0f}",
                    delta=f"{totals[key] - target[key]:+.0f} vs target",
                )

            for slot in slots_by_day.get(day, []):
                if slot.mode == MODE_SKIP:
                    st.markdown(f"**{slot.meal_type.title()}** — _skipped_")
                    continue

                source_id = slot.id if slot.mode == MODE_COOK else slot.source
                event = by_slot.get(source_id)
                if event is None:
                    st.markdown(f"**{slot.meal_type.title()}** — _not generated_")
                    continue

                render_meal(slot, event, is_leftover=slot.mode == MODE_LEFTOVER)


def render_meal(slot, event, is_leftover: bool) -> None:
    with st.container(border=True):
        origin = (
            f" · leftovers from {event.day} {event.meal_type}"
            if is_leftover
            else (f" · cooks {event.portions} portions" if event.portions > 1 else "")
        )
        st.markdown(f"**{slot.meal_type.title()}: {event.recipe.name}**{origin}")

        tags = [humanize(event.style), humanize(event.cuisine)]
        caption = " · ".join([tag for tag in tags if tag])
        st.caption(
            f"{caption + ' · ' if caption else ''}{event.recipe.prep_time_minutes} min prep"
        )

        serving = per_serving_totals(event.recipe)
        nutrition_cols = st.columns(4)
        nutrition_cols[0].metric("Calories", f"{serving['calories']:.0f} kcal")
        nutrition_cols[1].metric("Protein", f"{serving['protein_g']:.0f} g")
        nutrition_cols[2].metric("Net Carbs", f"{serving['net_carbs_g']:.0f} g")
        nutrition_cols[3].metric("Fat", f"{serving['fat_g']:.0f} g")

        if is_leftover:
            st.caption("Reheat only — no shopping or cooking needed today.")
            return

        with st.expander(f"Ingredients (for all {event.portions} portions)"):
            for ingredient in event.recipe.ingredients:
                st.write(
                    f"- {ingredient.name}: "
                    f"{format_quantity(ingredient.name, ingredient.quantity_g)} "
                    f"(NOVA {ingredient.nova_group})"
                )

        with st.expander("Instructions"):
            for step_number, step in enumerate(event.recipe.instructions, start=1):
                st.write(f"{step_number}. {step}")

        if event.recipe.prep_notes:
            st.info(event.recipe.prep_notes)


def render_shopping(week_plan, windows) -> None:
    if not windows:
        st.info("No shopping windows configured.")
        return

    tabs = st.tabs([window.label for window in windows])
    for tab, window in zip(tabs, windows):
        with tab:
            events = week_plan.events_on_days(window.days)
            if not events:
                st.info("Nothing is cooked in this window — no shopping needed.")
                continue

            if window.shop_ahead:
                st.warning(
                    f"You didn't mark {window.days[0]} as a shopping day, so this "
                    "food has to be bought before the week starts."
                )

            with st.expander(f"Cooking this window ({len(events)} sessions)", expanded=False):
                for line in cook_plan_lines(events):
                    st.write(f"- {line}")
                st.caption("Quantities below already include every portion.")

            shopping_list = aggregate_cook_events(events, window.days)

            late_items = [item for item in shopping_list.items() if item.buy_late]
            if late_items:
                st.caption(
                    "⚠️ marks perishables not cooked until later in the window — "
                    "worth a top-up trip closer to the day."
                )

            for department in sorted(shopping_list.categories):
                st.markdown(f"**{department}**")
                for item in shopping_list.categories[department]:
                    flag = " ⚠️" if item.buy_late else ""
                    st.checkbox(
                        f"{item.name} — {format_quantity(item.name, item.total_amount_g)}{flag}",
                        key=f"shop_{window.shop_day}_{department}_{item.name}",
                    )

            st.download_button(
                "Download this list",
                data=format_shopping_list_markdown(
                    shopping_list, cook_events=events, title=window.label
                ),
                file_name=f"shopping_{window.shop_day.lower()}.md",
                mime="text/markdown",
            )

            with st.expander("Copy to Google Keep"):
                st.caption(
                    "Click the copy icon, then paste into a new Google Keep list "
                    "note — each line becomes its own checkbox item."
                )
                st.code(format_shopping_list_keep(shopping_list), language=None)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run_generation(spec: WeekSpec, config: dict) -> None:
    history = load_history()
    resolved = resolve_auto_choices(spec, config, history)
    cook_days = sorted({slot.day for slot in resolved.cook_slots()}, key=resolved.days.index)

    progress = st.progress(0.0, text="Starting…")
    completed = {"count": 0}
    notes = []

    def report(day: str, cooks: int) -> None:
        if day in cook_days:
            completed["count"] += 1
        fraction = completed["count"] / max(1, len(cook_days))
        progress.progress(
            min(1.0, fraction),
            text=f"{day}: generating {cooks} recipe(s)…" if cooks else f"{day}: leftovers only",
        )

    try:
        week_plan = generate_week_plan(
            resolved, config, history, progress_callback=report, note_callback=notes.append
        )
    except Exception as exc:
        progress.empty()
        st.error(f"Failed to generate the week: {exc}")
        return

    progress.empty()
    record_week_history(week_plan)
    st.session_state["week_plan"] = week_plan
    st.session_state["generation_notes"] = notes


def main():
    config = load_config(CONFIG_PATH)
    sidebar = render_sidebar(config)
    days = sidebar["days"]

    st.title("AI Weekly Meal Planner")

    ensure_grid(config, sidebar["week_start"], sidebar["servings"])

    setup_tab, week_tab, shopping_tab = st.tabs(["Plan Setup", "The Week", "Shopping"])

    with setup_tab:
        day_config = render_targets_editor(config, days)
        day_config["openrouter_model"] = sidebar["model"]

        spec = render_grid(day_config, days, sidebar["servings"])
        render_cook_summary(spec)
        windows = render_window_preview(spec, sidebar["shop_days"])

        errors = validate_week(spec, day_config)
        for error in errors:
            st.error(error)
        for warning in week_warnings(spec):
            st.warning(warning)

        cook_count = len(spec.cook_slots())
        cook_days = len({slot.day for slot in spec.cook_slots()})
        st.caption(
            f"Generating will make {cook_days} API call(s) — one per day with "
            f"cooking to do — for {cook_count} recipes."
        )
        if st.button(
            "Generate Week",
            type="primary",
            use_container_width=True,
            disabled=bool(errors),
        ):
            run_generation(spec, day_config)

        st.session_state["windows"] = windows

    with week_tab:
        if "week_plan" not in st.session_state:
            st.info("Set up your week in **Plan Setup**, then click Generate Week.")
        else:
            failures = st.session_state["week_plan"].failures
            if failures:
                st.warning(
                    "These days failed to generate and are missing from the plan "
                    "and shopping lists. Click Generate Week again to retry — the "
                    "rest of the week is kept in the meantime.\n\n"
                    + "\n".join(f"- **{day}** — {error}" for day, error in failures.items())
                )
            notes = st.session_state.get("generation_notes") or []
            if notes:
                with st.expander(f"Portion adjustments ({len(notes)})"):
                    st.caption(
                        "The model sizes portions poorly, so Python rescaled these "
                        "recipes to land on their macro budget."
                    )
                    for note in notes:
                        st.write(f"- {note}")
            render_week(st.session_state["week_plan"])

    with shopping_tab:
        if "week_plan" not in st.session_state:
            st.info("Generate a week first — shopping lists are built from it.")
        else:
            render_shopping(st.session_state["week_plan"], st.session_state.get("windows", []))


if __name__ == "__main__":
    main()
-e 

=== File: ./week.py ===
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
-e 

