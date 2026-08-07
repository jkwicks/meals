from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# planner.py imports this module, so importing MealPlan back from planner
# would be circular; the aggregator only needs it for type hints.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planner import MealPlan

# Ordered (specific -> general) keyword -> department lookup. Ingredient
# names are matched by substring against these keywords, first match wins.
DEPARTMENT_KEYWORDS = [
    ("Herbs & Spices", [
        "salt", "pepper", "cumin", "paprika", "oregano", "basil", "thyme",
        "rosemary", "cinnamon", "turmeric", "chili powder", "chilli powder",
        "garlic powder", "onion powder", "bay leaf", "parsley", "cilantro",
        "coriander", "dill", "sage", "nutmeg", "clove", "cardamom",
        "chives", "mint", "vanilla extract", "spice", "seasoning",
    ]),
    ("Fish & Seafood", [
        "salmon", "tuna", "shrimp", "prawn", "cod", "tilapia", "halibut",
        "trout", "sardine", "anchovy", "crab", "lobster", "mussel", "clam",
        "scallop", "fish",
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
        "carrot", "potato", "zucchini", "cucumber", "avocado", "lemon",
        "lime", "berry", "berries", "mushroom", "celery", "cabbage",
        "squash", "sweet potato", "asparagus", "green bean", "pea",
        "orange", "grape", "peach", "pear", "mango", "melon",
    ]),
]

DEFAULT_DEPARTMENT = "Dairy & Pantry"


class ShoppingItem(BaseModel):
    name: str = Field(..., description="Ingredient name")
    total_amount_g: float = Field(..., ge=0, description="Combined quantity in grams")
    nova_group: int = Field(..., ge=1, le=4)
    department: str = Field(..., description="Grocery department/category")


class ShoppingList(BaseModel):
    categories: Dict[str, List[ShoppingItem]] = Field(default_factory=dict)


def categorize_department(ingredient_name: str) -> str:
    name_lower = ingredient_name.lower()
    for department, keywords in DEPARTMENT_KEYWORDS:
        if any(keyword in name_lower for keyword in keywords):
            return department
    return DEFAULT_DEPARTMENT


def aggregate_meal_plan(meal_plan: "MealPlan") -> ShoppingList:
    aggregated: Dict[str, dict] = {}

    for recipe in meal_plan.recipes:
        for ingredient in recipe.ingredients:
            key = ingredient.name.strip().lower()
            if key not in aggregated:
                aggregated[key] = {
                    "name": ingredient.name.strip(),
                    "total_amount_g": 0.0,
                    "nova_group": ingredient.nova_group,
                }
            aggregated[key]["total_amount_g"] += ingredient.quantity_g
            aggregated[key]["nova_group"] = max(
                aggregated[key]["nova_group"], ingredient.nova_group
            )

    categories: Dict[str, List[ShoppingItem]] = {}
    for item in aggregated.values():
        department = categorize_department(item["name"])
        shopping_item = ShoppingItem(
            name=item["name"],
            total_amount_g=round(item["total_amount_g"], 1),
            nova_group=item["nova_group"],
            department=department,
        )
        categories.setdefault(department, []).append(shopping_item)

    for items in categories.values():
        items.sort(key=lambda i: i.name.lower())

    return ShoppingList(categories=categories)


def format_grams(amount_g: float) -> str:
    if amount_g >= 1000:
        return f"{amount_g / 1000:.2f}kg"
    return f"{amount_g:g}g"


def batch_prep_lines(meal_plan: "MealPlan") -> List[str]:
    """Recipe-level batch-prep summary. Ingredient totals in the shopping
    list already include these recipes' bulk quantities (Recipe.ingredients
    stores the scaled batch amounts) — this just calls out which recipes
    they came from and how many portions each yields."""
    return [
        f"{recipe.name}: {recipe.servings} total portions"
        for recipe in meal_plan.recipes
        if recipe.is_batch_prep
    ]


def format_shopping_list_text(
    shopping_list: ShoppingList, meal_plan: Optional["MealPlan"] = None
) -> str:
    lines = []
    if meal_plan is not None:
        batch_lines = batch_prep_lines(meal_plan)
        if batch_lines:
            lines.append("Batch Prep (quantities below already include these):")
            for line in batch_lines:
                lines.append(f"  - {line}")
            lines.append("")
    for department in sorted(shopping_list.categories):
        lines.append(f"{department}:")
        for item in shopping_list.categories[department]:
            lines.append(f"  - {item.name}: {format_grams(item.total_amount_g)}")
    return "\n".join(lines)


def format_shopping_list_markdown(
    shopping_list: ShoppingList, meal_plan: Optional["MealPlan"] = None
) -> str:
    lines = ["# Shopping List", ""]
    if meal_plan is not None:
        batch_lines = batch_prep_lines(meal_plan)
        if batch_lines:
            lines.append("## Batch Prep")
            lines.append("_Quantities below already include these bulk portions._")
            lines.append("")
            for line in batch_lines:
                lines.append(f"- {line}")
            lines.append("")
    for department in sorted(shopping_list.categories):
        lines.append(f"## {department}")
        for item in shopping_list.categories[department]:
            lines.append(f"- [ ] {item.name} — {format_grams(item.total_amount_g)}")
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
            lines.append(f"{item.name}: {format_grams(item.total_amount_g)}")
    return "\n".join(lines)
