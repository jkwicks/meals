import re
from typing import Dict, List, Optional, Sequence, Tuple

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


# One canonical purchase per pantry staple, matched against an ingredient's
# normalised words as `(words that must all appear, words that must not, the
# single line the shopper reads)`.
#
# `NAME_ALIASES` above rewrites one *whole* key to another and can only fix
# variants that already normalise to a single word each. These are the
# variants that don't: a model writes the same tin three ways in one week —
# "Sardines (canned)", "sardines in water (tinned)", "tinned sardines" — and
# the word-sorted key only merges the two that happen to share a word set, so
# the shopping list said to buy sardines twice. Same for "low fat cottage
# cheese" against "cottage cheese", and "extra virgin olive oil" against
# "olive oil". The prompt's PANTRY_CONSOLIDATION_RULE asks the model not to
# produce these; this is what catches the ones it produces anyway.
#
# Longest match wins (most matched words), ties fall to list order — the same
# specificity-beats-ordering rule `categorize_department` uses, and what keeps
# "Greek yoghurt" from collapsing into plain "Yoghurt".
#
# Deliberately narrow. An entry here *asserts* that every variant is the same
# purchase, which is exactly the merge `STATE_QUALIFIERS` exists to prevent
# when it isn't true — hence the exclusion lists ("mustard seeds" is not
# mustard, "oat milk" is not oats) and hence nothing here whose forms differ
# in what a gram means. When the canonical name carries a state of its own it
# wins, which is what merges "(tinned)" into "(canned)"; when it carries none
# the original's state is kept, so "Oats, cooked" still can't merge with dry.
CANONICAL_INGREDIENTS = [
    # (must contain, must not contain, canonical name)
    (("greek", "yoghurt"), (), "Greek yoghurt"),
    (("greek", "yogurt"), (), "Greek yoghurt"),
    (("cottage", "cheese"), (), "Cottage cheese"),
    (("protein", "powder"), (), "Protein powder"),
    (("olive", "oil"), (), "Olive oil"),
    (("flax", "seed"), (), "Flaxseed"),
    (("chia", "seed"), (), "Chia seeds"),
    (("hemp", "seed"), (), "Hemp seeds"),
    (("hemp", "heart"), (), "Hemp seeds"),
    # Single-word rules below, grouped for readability rather than for
    # precedence — longest match already wins, so "Greek yoghurt" beats
    # "yoghurt" wherever either sits in this list.
    (("sardine",), (), "Sardines (canned)"),
    (("mackerel",), (), "Mackerel (canned)"),
    (("tuna",), ("steak", "loin", "fillet"), "Tuna (canned)"),
    (("yoghurt",), (), "Yoghurt"),
    (("yogurt",), (), "Yoghurt"),
    (("flaxseed",), (), "Flaxseed"),
    # "mustard seeds" is a spice and "mustard powder" is a different jar again
    # — both are excluded rather than merged into the condiment.
    (("mustard",), ("seed", "powder"), "Mustard"),
    (("oat",), ("milk", "flour", "cake"), "Oats"),
    (("creatine",), (), "Creatine"),
]


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


# States that describe the same shelf. "Tinned" and "canned" are one word in
# two dialects, and a canonical name that says one has to accept the other or
# the merge it exists for never happens. Nothing else belongs here: "frozen"
# and "dried" really are different purchases of the same food.
EQUIVALENT_STATES = [{"canned", "tinned"}]


def key_words(name: str) -> List[str]:
    """The ingredient head's words, singularized, minus prep and state words.

    The shared first step of `normalize_name` and `canonical_ingredient`, so a
    canonical rule is matched against exactly the words the combining key is
    built from — a rule that matched some other tokenisation would fire on
    names the key then splits apart anyway.
    """
    head = ingredient_head(name).lower()
    return [
        singularize(word)
        for word in re.findall(r"[a-z]+", head)
        if word not in PREP_QUALIFIERS and word not in STATE_QUALIFIERS
    ]


def canonical_ingredient(name: str) -> Optional[str]:
    """The single canonical purchase `name` is a variant of, or None.

    Longest match wins, ties to list order (see `CANONICAL_INGREDIENTS`). A
    canonical name carrying a state of its own — "Sardines (canned)" — only
    claims names whose own state is absent or equivalent to it, so "tinned
    sardines" merges in while "frozen sardines" is left as its own line: the
    point of the state suffix is that a gram of one isn't a gram of the other,
    and canonicalisation must not be the thing that quietly overrides it.
    """
    words = set(key_words(name))
    if not words:
        return None

    match_length = 0
    matched: Optional[str] = None
    for required, excluded, canonical in CANONICAL_INGREDIENTS:
        if len(required) <= match_length:
            continue
        if not words.issuperset(required) or not words.isdisjoint(excluded):
            continue
        canonical_states = set(states_in(canonical))
        if canonical_states:
            allowed = set(canonical_states)
            for group in EQUIVALENT_STATES:
                if canonical_states & group:
                    allowed |= group
            if not set(states_in(name)).issubset(allowed):
                continue
        matched, match_length = canonical, len(required)
    return matched


def resolve_ingredient(name: str) -> Tuple[str, List[str]]:
    """`(the name to key and display on, the state qualifiers that apply)`.

    Where canonicalisation is applied, once, so the combining key and the
    displayed line can never disagree about which purchase this is — two
    variants merging into one total under a name that only describes one of
    them would be worse than not merging at all.

    A canonical name's own states win when it has any (that is what folds
    "(tinned)" into "(canned)"); otherwise the original's are kept, so
    canonicalising "Oats" out of "rolled oats" still leaves "Oats, cooked" on
    its own line.
    """
    canonical = canonical_ingredient(name)
    if canonical:
        return canonical, states_in(canonical) or states_in(name)
    return name, states_in(name)


def normalize_name(name: str) -> str:
    """Combining key: head minus cut words, word-sorted, plus any state words.

    Word-sorting is what collapses "Fresh lemon juice" and "Lemon juice,
    fresh" — models phrase the same purchase both ways within one week. The
    state suffix is what keeps "Quinoa, dry" and "Quinoa, cooked" apart.
    """
    base_name, states = resolve_ingredient(name)
    words = key_words(base_name)
    base = " ".join(sorted(words)) if words else ingredient_head(base_name).lower().strip()
    base = NAME_ALIASES.get(base, base)
    return f"{base} [{' '.join(states)}]" if states else base


def display_name(name: str) -> str:
    """What the shopper reads: head minus cut words, state kept in parentheses."""
    base_name, states = resolve_ingredient(name)
    head = ingredient_head(base_name)
    words = [
        word
        for word in head.split()
        if word.lower().strip(".") not in PREP_QUALIFIERS
        and word.lower().strip(".") not in STATE_QUALIFIERS
    ]
    cleaned = " ".join(words).strip(" -,")
    if not cleaned:
        cleaned = ingredient_head(base_name)
    cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else name
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


def round_ingredient_quantity(name: str, quantity_g: float, department: str) -> float:
    """Snap a scaled ingredient quantity to an amount a shopper can actually buy.

    A portion trim or batch multiply leaves quantities like 516g — precise but
    unbuyable. Meat/fish and anything already sizeable (>=100g) round to the
    nearest 50g, the way it's sold; mid-size amounts round to the nearest 10g;
    small spice/seasoning amounts (Herbs & Spices, <20g) round to the nearest
    1g, since 2g of turmeric and 5g are meaningfully different; everything
    else under 20g rounds to the nearest 5g.

    Floored at one increment rather than letting a rounded-down trace ingredient
    hit 0g: `Ingredient.quantity_g` requires `gt=0`, and a positive amount that
    rounds to nothing is still on the recipe, just too small to weigh precisely.
    """
    if department in ("Meat & Poultry", "Fish & Seafood") or quantity_g >= 100:
        increment = 50.0
    elif quantity_g >= 20:
        increment = 10.0
    elif department == "Herbs & Spices":
        increment = 1.0
    else:
        increment = 5.0
    rounded = round(quantity_g / increment) * increment
    return rounded if rounded > 0 else increment


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
            key = normalize_name(ingredient.name)
            if key in NON_SHOPPING_INGREDIENTS:
                continue
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


PLANT_DEPARTMENTS = {"Produce", "Herbs & Spices", "Nuts, Seeds & Spreads"}


def collect_unique_plants(cook_events: Sequence["CookEvent"]) -> List[str]:
    """Unique plant-based ingredients across a week's cook events.

    Diversity, not shopping quantity: an ingredient counts once no matter how
    many recipes use it, keyed by the same `normalize_name()` used to combine
    shopping-list lines so "Spinach" and "Baby spinach, washed" aren't counted
    twice.
    """
    plants: Dict[str, str] = {}
    for event in cook_events:
        for ingredient in event.recipe.ingredients:
            if categorize_department(ingredient.name) not in PLANT_DEPARTMENTS:
                continue
            key = normalize_name(ingredient.name)
            plants.setdefault(key, display_name(ingredient.name))
    return sorted(plants.values())


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
    note — bullets or blank lines would just become extra junk items.

    The perishable note rides along on the item's own line rather than getting
    a line of its own: this is the copy you read *in the shop*, which is the
    one place the warning can still change what you put in the basket, and a
    separate line would become a checkbox for a thing you can't buy.
    """
    lines = []
    for department in sorted(shopping_list.categories):
        lines.append(department)
        for item in shopping_list.categories[department]:
            note = " (buy fresh closer to the day)" if item.buy_late else ""
            lines.append(
                f"{item.name}: {format_quantity(item.name, item.total_amount_g)}{note}"
            )
    return "\n".join(lines)
