import argparse
import json
import os
from datetime import datetime
from typing import List, Optional

import instructor
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from shopping import (
    aggregate_meal_plan,
    format_shopping_list_markdown,
    format_shopping_list_text,
)

load_dotenv()

DEFAULT_ALLOWED_NOVA_GROUPS = [1, 2, 3]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
MEAL_PLAN_CACHE_FILE = "meal_plan.json"
MEAL_HISTORY_FILE = "meal_history.json"
DEFAULT_SERVINGS_PER_MEAL = 2
DEFAULT_BATCH_MULTIPLIER = 2
HISTORY_MAX_ENTRIES = 14
CUISINE_LOOKBACK = 4
PROTEIN_LOOKBACK_ENTRIES = 3


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
    is_keto = day_targets["is_keto"]

    protein_cal = protein_g * 4
    carb_cal = net_carbs_g * 4
    fat_g = max(0, (calories - (protein_cal + carb_cal)) / 9)

    return {
        "day_of_week": day_of_week,
        "calories": calories,
        "protein_g": protein_g,
        "net_carbs_g": net_carbs_g,
        "fat_g": round(fat_g, 1),
        "is_keto": is_keto,
    }


def resolve_serving_rules(
    day_of_week: str,
    config: dict,
    servings_override: Optional[int] = None,
    bulk_override: bool = False,
    batch_multiplier_override: Optional[int] = None,
) -> dict:
    """Merge config.json's serving_rules with CLI overrides.

    servings_per_meal is the household size cooked for every day. A day
    counts as a batch-prep day if it's listed in batch_cook_days, if
    is_bulk_prep is set in config, or if the caller forces it via --bulk /
    --batch-multiplier.
    """
    serving_rules = config.get("serving_rules", {})

    servings_per_meal = (
        servings_override
        if servings_override is not None
        else serving_rules.get("servings_per_meal", DEFAULT_SERVINGS_PER_MEAL)
    )
    batch_multiplier = (
        batch_multiplier_override
        if batch_multiplier_override is not None
        else serving_rules.get("batch_multiplier", DEFAULT_BATCH_MULTIPLIER)
    )
    batch_cook_days = serving_rules.get("batch_cook_days", [])
    is_batch_day = (
        bulk_override
        or batch_multiplier_override is not None
        or serving_rules.get("is_bulk_prep", False)
        or day_of_week in batch_cook_days
    )

    return {
        "servings_per_meal": servings_per_meal,
        "batch_multiplier": batch_multiplier,
        "is_batch_day": is_batch_day,
    }


def load_history(path: str = MEAL_HISTORY_FILE) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def pick_cuisine(config: dict, history: List[dict], lookback: int = CUISINE_LOOKBACK) -> Optional[str]:
    """Deterministically rotate through config["cuisines"] so meal plans don't
    default to the same handful of dishes. Picks the first cuisine not used
    in the last `lookback` history entries; if every cuisine has been used
    that recently (a short cuisine list vs. a long lookback), falls back to
    whichever cuisine was used longest ago overall."""
    cuisines = config.get("cuisines", [])
    if not cuisines:
        return None

    recent = {entry.get("cuisine") for entry in history[-lookback:]}
    for cuisine in cuisines:
        if cuisine not in recent:
            return cuisine

    last_seen_index = {cuisine: -1 for cuisine in cuisines}
    for index, entry in enumerate(history):
        if entry.get("cuisine") in last_seen_index:
            last_seen_index[entry["cuisine"]] = index
    return min(cuisines, key=lambda cuisine: last_seen_index[cuisine])


def recent_main_proteins(history: List[dict], lookback_entries: int = PROTEIN_LOOKBACK_ENTRIES) -> List[str]:
    """Collect the main proteins used across the last few history entries so
    the model can be told to avoid repeating them, in order, de-duplicated."""
    seen = set()
    proteins = []
    for entry in history[-lookback_entries:]:
        for protein in entry.get("main_proteins", []):
            if protein not in seen:
                seen.add(protein)
                proteins.append(protein)
    return proteins


def extract_main_proteins(meal_plan: "MealPlan") -> List[str]:
    """Cheap proxy for "the protein source" of a recipe: the ingredient with
    the highest protein_g. Only considered for lunch/dinner, where a single
    dominant protein is the norm."""
    proteins = []
    for recipe in meal_plan.recipes:
        if recipe.meal_type.lower() not in ("lunch", "dinner") or not recipe.ingredients:
            continue
        main_ingredient = max(recipe.ingredients, key=lambda ingredient: ingredient.protein_g)
        proteins.append(main_ingredient.name)
    return proteins


def record_history_entry(
    meal_plan: "MealPlan",
    cuisine: Optional[str],
    path: str = MEAL_HISTORY_FILE,
    max_entries: int = HISTORY_MAX_ENTRIES,
) -> None:
    history = load_history(path)
    history.append(
        {
            "day_of_week": meal_plan.day_of_week,
            "generated_at": datetime.now().isoformat(),
            "cuisine": cuisine,
            "main_proteins": extract_main_proteins(meal_plan),
            "recipe_names": [recipe.name for recipe in meal_plan.recipes],
        }
    )
    history = history[-max_entries:]
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def scale_recipe(
    recipe: "Recipe", servings_per_meal: int, batch_multiplier: int, is_batch_day: bool
) -> "Recipe":
    """Scale a recipe's ingredients from a single serving up to the full
    household/batch yield.

    The model is instructed to report ingredient quantities and their
    calories/protein_g/net_carbs_g/fat_g for ONE serving, so the scaling
    here is a plain linear multiply — the same math the codebase already
    uses to keep target-macro arithmetic deterministic instead of trusting
    the model with it.
    """
    total_servings = servings_per_meal * (batch_multiplier if is_batch_day else 1)

    scaled_ingredients = [
        ingredient.model_copy(
            update={
                "quantity_g": round(ingredient.quantity_g * total_servings, 1),
                "calories": round(ingredient.calories * total_servings, 1),
                "protein_g": round(ingredient.protein_g * total_servings, 1),
                "net_carbs_g": round(ingredient.net_carbs_g * total_servings, 1),
                "fat_g": round(ingredient.fat_g * total_servings, 1),
            }
        )
        for ingredient in recipe.ingredients
    ]

    prep_notes = recipe.prep_notes
    if is_batch_day and not prep_notes:
        prep_notes = (
            f"Batch recipe: yields {total_servings} portions. Portion into "
            "airtight containers and refrigerate (up to 4 days) or freeze "
            "for future meals; reheat thoroughly before serving."
        )

    return recipe.model_copy(
        update={
            "ingredients": scaled_ingredients,
            "servings": total_servings,
            "is_batch_prep": is_batch_day,
            "prep_notes": prep_notes,
        }
    )


def compute_recipe_totals(recipe: "Recipe") -> dict:
    totals = {"calories": 0.0, "protein_g": 0.0, "net_carbs_g": 0.0, "fat_g": 0.0}
    for ingredient in recipe.ingredients:
        totals["calories"] += ingredient.calories
        totals["protein_g"] += ingredient.protein_g
        totals["net_carbs_g"] += ingredient.net_carbs_g
        totals["fat_g"] += ingredient.fat_g
    return totals


def compute_macro_totals(meal_plan: "MealPlan") -> dict:
    totals = {"calories": 0.0, "protein_g": 0.0, "net_carbs_g": 0.0, "fat_g": 0.0}
    for recipe in meal_plan.recipes:
        recipe_totals = compute_recipe_totals(recipe)
        for key in totals:
            totals[key] += recipe_totals[key]
    return totals


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
            "Total number of portions this recipe yields. Left at the default "
            "of 1 by the model — Python overwrites this with the real "
            "household/batch serving count after generation."
        ),
    )
    is_batch_prep: bool = Field(
        default=False,
        description=(
            "True if this recipe is bulk-cooked for future meals. Set by "
            "Python from the day's serving rules, not the model."
        ),
    )
    prep_notes: Optional[str] = Field(
        default=None,
        description="Storage/reheating notes for batch-prepped recipes.",
    )


class MealPlan(BaseModel):
    day_of_week: str
    target_calories: float
    target_protein_g: float
    target_net_carbs_g: float
    target_fat_g: float
    is_keto: bool
    recipes: List[Recipe]


def build_client() -> instructor.Instructor:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key or api_key == "your_openrouter_api_key_here":
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file."
        )
    openai_client = OpenAI(
        base_url=OPENROUTER_BASE_URL, api_key=api_key, timeout=120.0
    )
    return instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)


def generate_meal_plan(
    targets: dict,
    config: dict,
    serving_info: dict,
    cuisine: Optional[str] = None,
    avoid_proteins: Optional[List[str]] = None,
) -> MealPlan:
    client = build_client()

    dietary_rules = config["dietary_rules"]
    keto_instruction = (
        "This is a KETO day: recipes must be very low carb and high fat."
        if targets["is_keto"]
        else "This is not a keto day: balanced macros are fine."
    )

    cuisine_instruction = (
        f"- This meal plan's cuisine theme is **{cuisine.replace('_', ' ')}**: "
        f"lunch and dinner should authentically reflect {cuisine.replace('_', ' ')} "
        "flavors, spices, and cooking techniques — not a generic protein-plus-"
        "a-vegetable default.\n"
        if cuisine
        else ""
    )
    avoid_protein_instruction = (
        "- Avoid making any of these the primary protein again — they were "
        f"used in recent meal plans: {', '.join(avoid_proteins)}.\n"
        if avoid_proteins
        else ""
    )

    servings_per_meal = serving_info["servings_per_meal"]
    is_batch_day = serving_info["is_batch_day"]

    batch_instruction = (
        (
            "- This is a BATCH PREP day (is_batch_prep is True): design recipes "
            "suited to bulk cooking and storage — e.g. a big pot/tray that "
            "portions and reheats well — and write instructions that mention "
            "storing and reheating across multiple future meals. Output "
            "recipes formatted as batch meals designed to be stored and "
            "reheated; Python will scale your single-serving quantities up "
            "into the total bulk ingredient quantities, so you don't need to "
            "do that multiplication yourself.\n"
        )
        if is_batch_day
        else ""
    )

    system_prompt = (
        f"You are a precision meal-planning assistant. You are generating a "
        f"meal plan for {servings_per_meal} people. Generate a full day of "
        "varied, non-repetitive, realistic recipes (breakfast, lunch, dinner, "
        "and an optional snack) that adhere strictly to the provided macro "
        "targets and dietary constraints.\n\n"
        "Rules:\n"
        "- Use metric units only (grams) for all ingredient quantities.\n"
        "- Every ingredient's nova_group must be one of: "
        f"{dietary_rules['allowed_nova_groups']} (1=unprocessed/minimally "
        "processed, 2=processed culinary ingredients, 3=processed foods). "
        "Never use Group 4 ultra-processed ingredients.\n"
        "- Never use any of these banned ingredients: "
        f"{', '.join(dietary_rules['banned_ingredients'])}.\n"
        f"- {keto_instruction}\n"
        f"{cuisine_instruction}"
        f"{avoid_protein_instruction}"
        "- The target macros given below are PER SERVING (one portion, one "
        "person's day) — report every ingredient's quantity_g and its "
        "calories/protein_g/net_carbs_g/fat_g for a SINGLE serving too. Do "
        "not multiply by the number of people or by any batch size yourself; "
        "Python scales the recipe up for the full household/batch after you "
        "respond. The sum of each ingredient's macros across all recipes "
        "should approximate the day's per-serving target macros given below. "
        "Do not do the target math yourself — those numbers are already "
        "calculated; just hit them with real food.\n"
        f"{batch_instruction}"
        "- Leave servings, is_batch_prep, and prep_notes at their schema "
        "defaults — Python fills those in after generation.\n"
        "- Recipes must be varied and non-repetitive across meals.\n"
        "- Do not show your work, explain your reasoning, or narrate your "
        "process. Respond with the structured data only."
    )

    user_prompt = (
        f"Generate a meal plan for {targets['day_of_week']} with these exact "
        "PER-SERVING daily targets (already calculated, do not recompute):\n"
        f"- Calories: {targets['calories']} kcal\n"
        f"- Protein: {targets['protein_g']} g\n"
        f"- Net carbs: {targets['net_carbs_g']} g\n"
        f"- Fat: {targets['fat_g']} g\n"
    )

    model = config.get("openrouter_model", DEFAULT_MODEL)

    return client.chat.completions.create(
        model=model,
        response_model=MealPlan,
        max_retries=3,
        max_tokens=8000,
        context={"config": config},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="AI Meal Planner CLI")
    parser.add_argument(
        "--day",
        default=None,
        help="Day of the week (e.g. Wednesday). Defaults to today.",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON file (default: config.json)",
    )
    parser.add_argument(
        "--no-shopping-list",
        action="store_true",
        help="Skip generating/printing the consolidated shopping list.",
    )
    parser.add_argument(
        "--save-shopping-list",
        action="store_true",
        help="Save the shopping list to shopping_list.md alongside the meal plan.",
    )
    parser.add_argument(
        "--use-cached-plan",
        action="store_true",
        help=(
            f"Load the meal plan from {MEAL_PLAN_CACHE_FILE} instead of calling "
            "OpenRouter (useful for testing the shopping list without repeated "
            "API calls). Requires the cache file to already exist."
        ),
    )
    parser.add_argument(
        "--servings",
        type=int,
        default=None,
        help="Override serving_rules.servings_per_meal (people cooked for per meal).",
    )
    parser.add_argument(
        "--bulk",
        action="store_true",
        help="Force this day to be treated as a batch-prep day.",
    )
    parser.add_argument(
        "--batch-multiplier",
        type=int,
        default=None,
        help=(
            "Override serving_rules.batch_multiplier. Passing this also "
            "implies --bulk for this run."
        ),
    )
    parser.add_argument(
        "--cuisine",
        default=None,
        help=(
            "Force a specific cuisine theme instead of auto-rotating through "
            "config.json's cuisines list. Still recorded to meal_history.json "
            "so rotation stays aware of it."
        ),
    )
    args = parser.parse_args()

    config = load_config(args.config)
    day_of_week = args.day or datetime.now().strftime("%A")

    targets = calculate_daily_targets(day_of_week, config)
    serving_info = resolve_serving_rules(
        day_of_week, config, args.servings, args.bulk, args.batch_multiplier
    )

    if args.use_cached_plan:
        print(f"Loading cached meal plan from {MEAL_PLAN_CACHE_FILE}...", flush=True)
        with open(MEAL_PLAN_CACHE_FILE, "r") as f:
            meal_plan = MealPlan.model_validate(json.load(f))
    else:
        history = load_history()
        cuisine = args.cuisine or pick_cuisine(config, history)
        avoid_proteins = recent_main_proteins(history)

        model = config.get("openrouter_model", DEFAULT_MODEL)
        print(f"Generating meal plan for {day_of_week} using {model}...", flush=True)
        print(
            f"Targets (per serving) -> calories: {targets['calories']}, protein_g: "
            f"{targets['protein_g']}, net_carbs_g: {targets['net_carbs_g']}, "
            f"fat_g: {targets['fat_g']}, keto: {targets['is_keto']}",
            flush=True,
        )
        print(
            f"Serving rules -> servings_per_meal: {serving_info['servings_per_meal']}, "
            f"batch_multiplier: {serving_info['batch_multiplier']}, "
            f"is_batch_day: {serving_info['is_batch_day']}",
            flush=True,
        )
        print(
            f"Cuisine -> {cuisine or '(none configured)'}"
            + (f" | avoiding recent proteins: {', '.join(avoid_proteins)}" if avoid_proteins else ""),
            flush=True,
        )

        meal_plan = generate_meal_plan(targets, config, serving_info, cuisine, avoid_proteins)

        with open(MEAL_PLAN_CACHE_FILE, "w") as f:
            json.dump(meal_plan.model_dump(), f, indent=2)

        record_history_entry(meal_plan, cuisine)

    # meal_plan.recipes at this point hold single-serving quantities (either
    # freshly generated or loaded from cache) — capture per-serving totals
    # before scaling up to the household/batch yield.
    per_serving_totals = compute_macro_totals(meal_plan)

    meal_plan.recipes = [
        scale_recipe(
            recipe,
            serving_info["servings_per_meal"],
            serving_info["batch_multiplier"],
            serving_info["is_batch_day"],
        )
        for recipe in meal_plan.recipes
    ]

    batch_totals = compute_macro_totals(meal_plan)

    print(json.dumps(meal_plan.model_dump(), indent=2))

    print("\nPer Serving Macros (1 portion, vs. daily target)")
    print("==================================================")
    print(f"  Calories:  target {targets['calories']:.1f} | generated {per_serving_totals['calories']:.1f}")
    print(f"  Protein:   target {targets['protein_g']:.1f} | generated {per_serving_totals['protein_g']:.1f}")
    print(f"  Net carbs: target {targets['net_carbs_g']:.1f} | generated {per_serving_totals['net_carbs_g']:.1f}")
    print(f"  Fat:       target {targets['fat_g']:.1f} | generated {per_serving_totals['fat_g']:.1f}")

    print("\nRecipes")
    print("=======")
    for recipe in meal_plan.recipes:
        batch_tag = " [BATCH PREP]" if recipe.is_batch_prep else ""
        print(f"- {recipe.name} ({recipe.meal_type}): {recipe.servings} servings{batch_tag}")
        if recipe.prep_notes:
            print(f"    prep notes: {recipe.prep_notes}")

    print(
        f"\nTotal Batch Yield (scaled for {serving_info['servings_per_meal']} "
        f"servings/meal"
        + (f" x {serving_info['batch_multiplier']} batch multiplier)" if serving_info["is_batch_day"] else ")")
    )
    print("=" * 60)
    print(f"  Calories:  {batch_totals['calories']:.1f}")
    print(f"  Protein:   {batch_totals['protein_g']:.1f}")
    print(f"  Net carbs: {batch_totals['net_carbs_g']:.1f}")
    print(f"  Fat:       {batch_totals['fat_g']:.1f}")

    if not args.no_shopping_list:
        shopping_list = aggregate_meal_plan(meal_plan)
        print("\nShopping List")
        print("=============")
        print(format_shopping_list_text(shopping_list, meal_plan=meal_plan))

        if args.save_shopping_list:
            with open("shopping_list.md", "w") as f:
                f.write(format_shopping_list_markdown(shopping_list, meal_plan=meal_plan))
            print("\nSaved shopping list to shopping_list.md", flush=True)


if __name__ == "__main__":
    main()
